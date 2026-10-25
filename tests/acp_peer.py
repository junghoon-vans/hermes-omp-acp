"""Deterministic ACP peer for testing the provider's client interface."""

import asyncio
import os
import sys
from pathlib import Path

from acp import (
    Agent,
    PROTOCOL_VERSION,
    run_agent,
    update_agent_message_text,
    update_agent_thought_text,
)
from acp.schema import (
    InitializeResponse,
    NewSessionResponse,
    PermissionOption,
    PromptResponse,
    SetSessionConfigOptionResponse,
    ToolCallUpdate,
)


OPTIONS = [{
    "id": "model", "name": "Model", "category": "model", "type": "select",
    "currentValue": "fixture/model", "options": [{"value": "fixture/model", "name": "Fixture"}],
}]


class Peer(Agent):
    def _mark(self, name):
        (self.cwd / f"{name}.ready").write_text(str(os.getpid()), encoding="ascii")

    async def _wait_for_parent_release(self, name):
        self._mark(name)
        release = self.cwd / f"{name}.release"
        while not release.exists():
            await asyncio.sleep(0.01)

    def on_connect(self, client):
        self.client = client
        self.mode = sys.argv[1] if len(sys.argv) > 1 else "config"

    async def initialize(self, protocol_version, **kwargs):
        if self.mode == "exit":
            print("fixture peer failed during initialization", file=sys.stderr, flush=True)
            os._exit(17)
        if self.mode == "budget":
            self.cwd = Path.cwd()
            self._mark("budget_initialize")
            await asyncio.sleep(1.2)
        return InitializeResponse(protocol_version=PROTOCOL_VERSION, agent_capabilities={})

    async def new_session(self, cwd, mcp_servers, **kwargs):
        self.cwd = Path(cwd)
        if self.mode == "budget":
            self._mark("budget_new_session")
            await asyncio.sleep(1.2)
        self.model = "fixture/default" if self.mode.startswith("legacy") else "fixture/model"
        if self.mode.startswith("legacy"):
            return NewSessionResponse.model_validate({
                "sessionId": "fixture",
                "models": {
                    "currentModelId": "fixture/default",
                    "availableModels": [{"modelId": "fixture/model", "name": "Fixture"}],
                },
            })
        return NewSessionResponse.model_validate({"sessionId": "fixture", "configOptions": OPTIONS})

    async def set_config_option(self, config_id, session_id, value, **kwargs):
        if config_id != "model" or value != "fixture/model":
            raise ValueError("The requested model is unavailable")
        if self.mode == "budget":
            self._mark("budget_selection")
            await asyncio.sleep(1.2)
        if self.mode == "reject":
            raise ValueError("Model selection rejected")
        if self.mode == "timeout":
            await asyncio.sleep(10)
        if self.mode == "mismatch":
            return SetSessionConfigOptionResponse.model_validate({
                "configOptions": [{**OPTIONS[0], "currentValue": "fixture/default"}],
            })
        return SetSessionConfigOptionResponse.model_validate({"configOptions": OPTIONS})

    async def set_session_model(self, model_id, session_id, **kwargs):
        if self.mode == "legacy-reject":
            raise ValueError("Model selection rejected")
        if model_id != "fixture/model":
            raise ValueError("The requested model is unavailable")
        self.model = model_id
        return {}

    async def prompt(self, prompt, session_id, **kwargs):
        (self.cwd / "prompt-executed").write_text("executed")

        if self.mode == "permission":
            decision = await self.client.request_permission(
                session_id=session_id,
                options=[PermissionOption(option_id="allow-once", name="Allow once", kind="allow_once")],
                tool_call=ToolCallUpdate(tool_call_id="fixture-operation", title="Run a protected operation"),
            )
            text = f"permission outcome: {decision.outcome.outcome}"
        elif self.mode == "filesystem":
            allowed = await self.client.read_text_file(path=str(self.cwd / "allowed.txt"), session_id=session_id)

            allowed_write_path = self.cwd / "nested" / "allowed-write.txt"
            await self.client.write_text_file(
                path=str(allowed_write_path), content="allowed-write\n", session_id=session_id,
            )
            allowed_write = await self.client.read_text_file(
                path=str(allowed_write_path), session_id=session_id,
            )

            outside_read_path = self.cwd.parent / f"{self.cwd.name}-outside-read.txt"
            try:
                outside_read = await self.client.read_text_file(path=str(outside_read_path), session_id=session_id)
                outside_read_denied = False
                outside_read_content = outside_read.content
            except Exception:
                outside_read_denied = True
                outside_read_content = ""

            outside_read_link = self.cwd / "outside-read-link.txt"
            try:
                outside_link_read = await self.client.read_text_file(path=str(outside_read_link), session_id=session_id)
                outside_link_read_denied = False
                outside_link_read_content = outside_link_read.content
            except Exception:
                outside_link_read_denied = True
                outside_link_read_content = ""

            protected_write_path = self.cwd / ".env"
            try:
                await self.client.write_text_file(
                    path=str(protected_write_path), content="must-not-write", session_id=session_id,
                )
                protected_write_denied = False
            except Exception:
                protected_write_denied = True

            outside_write_path = self.cwd.parent / f"{self.cwd.name}-outside-write.txt"
            try:
                await self.client.write_text_file(
                    path=str(outside_write_path), content="must-not-write", session_id=session_id,
                )
                outside_write_denied = False
            except Exception:
                outside_write_denied = True

            outside_write_link = self.cwd / "outside-write-link.txt"
            try:
                await self.client.write_text_file(
                    path=str(outside_write_link), content="must-not-overwrite", session_id=session_id,
                )
                outside_link_write_denied = False
            except Exception:
                outside_link_write_denied = True

            text = (
                f"allowed={allowed.content!r}; allowed_write={allowed_write.content!r}; "
                f"outside_read_denied={outside_read_denied}; outside_read={outside_read_content!r}; "
                f"outside_link_read_denied={outside_link_read_denied}; "
                f"outside_link_read={outside_link_read_content!r}; "
                f"protected_write_denied={protected_write_denied}; outside_write_denied={outside_write_denied}; "
                f"outside_link_write_denied={outside_link_write_denied}"
            )
        elif self.mode == "conversion":
            await self.client.session_update(
                session_id=session_id,
                update=update_agent_thought_text("The agent's private reasoning."),
            )
            text = (
                'Before the call. <tool_call>{"id":"fixture-call","type":"function",'
                '"function":{"name":"lookup","arguments":"{\\"query\\":\\"hermes\\"}"}}</tool_call> '
                "After the call."
            )
        elif self.mode == "burst":
            for index in range(40):
                await self.client.session_update(
                    session_id=session_id,
                    update=update_agent_message_text(f"text-{index:02d}|"),
                )
                await self.client.session_update(
                    session_id=session_id,
                    update=update_agent_thought_text(f"reason-{index:02d}|"),
                )
            return PromptResponse(stop_reason="end_turn")
        elif self.mode in ("isolation", "lifecycle"):
            prompt_text = " ".join(block.text for block in prompt if hasattr(block, "text")).lower()
            marker = next(
                (
                    candidate for candidate in (
                        "iso_alpha", "iso_beta", "timeout_primary", "timeout_sibling",
                        "cancel_primary", "cancel_sibling", "close_left", "close_right",
                    )
                    if candidate in prompt_text
                ),
                None,
            )
            if marker is None:
                text = "missing request marker"
            else:
                await self._wait_for_parent_release(marker)
                reply_to = marker.removeprefix("iso_")
                text = f"reply to {reply_to}"
        else:
            await asyncio.sleep(0.15)
            chunks = ("first", " second") if self.model == "fixture/model" else ("wrong model",)
            for chunk in chunks:
                await self.client.session_update(session_id=session_id, update=update_agent_message_text(chunk))
            return PromptResponse(stop_reason="end_turn")

        await self.client.session_update(session_id=session_id, update=update_agent_message_text(text))
        return PromptResponse(stop_reason="end_turn")

if __name__ == "__main__":
    asyncio.run(run_agent(Peer(), use_unstable_protocol=True))
