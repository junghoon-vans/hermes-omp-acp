#!/usr/bin/env python3
"""Deterministic official-ACP agent for retained worker behavior tests."""

import asyncio
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
import uuid

from acp import Agent, run_agent
from acp.schema import (
    AgentCapabilities,
    AgentMessageChunk,
    InitializeResponse,
    LoadSessionResponse,
    NewSessionResponse,
    PermissionOption,
    PromptResponse,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
    ToolCallUpdate,
)


class FixtureAgent(Agent):
    def __init__(self):
        self.client = None

    def on_connect(self, client):
        self.client = client

    @staticmethod
    def _session_path(cwd, session_id):
        return Path(cwd).parent.parent / "peer-sessions" / Path(cwd).name / session_id

    async def initialize(self, protocol_version, **_):
        return InitializeResponse(
            protocol_version=protocol_version,
            agent_capabilities=AgentCapabilities(load_session=True),
        )

    async def new_session(self, cwd, **_):
        session_id = uuid.uuid4().hex
        state = self._session_path(cwd, session_id)
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text("0", encoding="utf-8")
        return NewSessionResponse(session_id=session_id)

    async def load_session(self, cwd, session_id, **_):
        if not self._session_path(cwd, session_id).is_file():
            raise RuntimeError(f"Unknown retained fixture session: {session_id}")
        return LoadSessionResponse()

    async def _message(self, session_id, text):
        await self.client.session_update(
            session_id,
            update=AgentMessageChunk(
                session_update="agent_message_chunk",
                content=TextContentBlock(type="text", text=text),
            ),
        )

    async def _start_tool(self, session_id, call_id, title, raw_input):
        await self.client.session_update(
            session_id,
            update=ToolCallStart(
                session_update="tool_call",
                tool_call_id=call_id,
                kind="execute",
                title=title,
                status="in_progress",
                raw_input=raw_input,
            ),
        )

    async def _finish_tool(self, session_id, call_id, status, raw_output):
        await self.client.session_update(
            session_id,
            update=ToolCallProgress(
                session_update="tool_call_update",
                tool_call_id=call_id,
                status=status,
                raw_output=raw_output,
            ),
        )

    async def _write_change(self, cwd, session_id, instruction):
        state = self._session_path(cwd, session_id)
        count = int(state.read_text(encoding="utf-8")) + 1
        state.write_text(str(count), encoding="utf-8")
        expected = f"{count}:{instruction}"
        result = Path(cwd) / "result.txt"
        with result.open("a", encoding="utf-8") as output:
            output.write(expected + "\n")

        verify = (
            "from pathlib import Path; import sys; "
            "path = Path(sys.argv[1]); expected = sys.argv[2]; "
            "assert path.read_text(encoding='utf-8').splitlines()[-1] == expected"
        )
        check = subprocess.run(
            [sys.executable, "-c", verify, str(result), expected],
            cwd=cwd,
            check=False,
        )
        status = "completed" if check.returncode == 0 else "failed"
        await self._finish_tool(
            session_id,
            "mutation",
            status,
            {"exit_code": check.returncode},
        )
        await self._message(
            session_id,
            f"Updated result.txt; verification exit code {check.returncode}.",
        )

    async def _sleep_with_shell_child(self, cwd):
        grandchild_script = (
            'trap "" TERM; echo $$ > grandchild.pid; '
            "while :; do sleep 1; done"
        )
        shell_script = (
            'trap "" TERM; echo $$ > shell.pid; '
            f"/bin/sh -c {shlex.quote(grandchild_script)} & child=$!; "
            'while [ ! -f grandchild.pid ]; do sleep 0.01; done; '
            "touch sleep.started; wait \"$child\""
        )
        subprocess.Popen(["/bin/sh", "-c", shell_script], cwd=cwd)

        marker = Path(cwd) / "sleep.started"
        deadline = time.monotonic() + 5
        while not marker.exists():
            if time.monotonic() >= deadline:
                raise RuntimeError("The fixture shell child did not start.")
            await asyncio.sleep(0.01)
        while True:
            await asyncio.sleep(1)

    async def prompt(self, prompt, session_id, **_):
        text = "\n".join(block.text for block in prompt if getattr(block, "type", None) == "text")
        instruction = text.splitlines()[0]
        cwd = Path.cwd()

        if instruction == "crash":
            os._exit(17)
        if instruction == "max_tokens":
            await self._message(session_id, "Stopped after reaching the token limit.")
            return PromptResponse(stop_reason="max_tokens")
        if instruction == "sleep":
            await self._start_tool(session_id, "sleep", "Run shell child", {"command": "sleep"})
            await self._sleep_with_shell_child(cwd)

        if instruction == "permission-needed":
            tool_call = ToolCallUpdate(
                tool_call_id="permission-mutation",
                kind="edit",
                title="Write result.txt",
                status="pending",
                raw_input={"path": "result.txt", "content": instruction},
            )
            response = await self.client.request_permission(
                session_id=session_id,
                tool_call=tool_call,
                options=[
                    PermissionOption(
                        option_id="allow-once",
                        kind="allow_once",
                        name="Allow once",
                    )
                ],
            )
            if response.outcome.outcome != "selected":
                await self._message(session_id, "Permission required; no mutation performed.")
                return PromptResponse(stop_reason="end_turn")
            await self._write_change(cwd, session_id, instruction)
            return PromptResponse(stop_reason="end_turn")

        if instruction == "tool-failure":
            await self._start_tool(session_id, "failing-tool", "Run failing verification", {"command": "exit 23"})
            check = subprocess.run(
                [sys.executable, "-c", "raise SystemExit(23)"],
                cwd=cwd,
                check=False,
            )
            await self._finish_tool(
                session_id,
                "failing-tool",
                "failed",
                {"exit_code": check.returncode},
            )
            await self._message(session_id, f"Tool failed with exit code {check.returncode}.")
            return PromptResponse(stop_reason="end_turn")

        await self._start_tool(
            session_id,
            "mutation",
            "Write and verify result.txt",
            {"content": instruction},
        )
        await self._write_change(cwd, session_id, instruction)
        return PromptResponse(stop_reason="end_turn")


if __name__ == "__main__":
    asyncio.run(run_agent(FixtureAgent()))
