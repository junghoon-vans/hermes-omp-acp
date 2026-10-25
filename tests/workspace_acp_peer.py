"""Deterministic ACP subprocess peer for workspace-client regression tests."""

import asyncio
import json
import os
from pathlib import Path
import sys

from acp import Agent, PROTOCOL_VERSION, run_agent, update_agent_message_text
from acp.schema import (
    InitializeResponse,
    NewSessionResponse,
    PromptResponse,
    SetSessionConfigOptionResponse,
)


OPTIONS = [{
    "id": "model",
    "name": "Model",
    "category": "model",
    "type": "select",
    "currentValue": "fixture/model",
    "options": [{"value": "fixture/model", "name": "Fixture"}],
}]


class WorkspacePeer(Agent):
    def on_connect(self, client):
        self.client = client
        self.mode = sys.argv[1]
        self.signal_dir = Path(sys.argv[2])

    def record(self, method, **details):
        entry = {"method": method, "pid": os.getpid(), **details}
        with (self.signal_dir / "protocol.jsonl").open("a", encoding="utf-8") as output:
            output.write(json.dumps(entry) + "\n")

    async def initialize(self, protocol_version, **kwargs):
        return InitializeResponse(protocol_version=PROTOCOL_VERSION, agent_capabilities={})

    async def new_session(self, cwd, mcp_servers, **kwargs):
        self.cwd = Path(cwd)
        self.record("session/new", requested_cwd=str(self.cwd), actual_cwd=os.getcwd())
        if self.mode == "no-model-exposure":
            return NewSessionResponse.model_validate({
                "sessionId": "workspace-fixture",
                "configOptions": [],
            })
        return NewSessionResponse.model_validate({
            "sessionId": "workspace-fixture",
            "configOptions": OPTIONS,
        })

    async def set_config_option(self, config_id, session_id, value, **kwargs):
        self.record("session/set_config_option", config_id=config_id, value=value)
        if self.mode == "reject":
            raise ValueError("Model selection rejected")
        if self.mode == "timeout":
            await asyncio.sleep(30)
        if config_id != "model" or value != "fixture/model":
            raise ValueError("The requested model is unavailable")
        if self.mode == "mismatch":
            return SetSessionConfigOptionResponse.model_validate({
                "configOptions": [{**OPTIONS[0], "currentValue": "fixture/default"}],
            })
        return SetSessionConfigOptionResponse.model_validate({"configOptions": OPTIONS})

    async def prompt(self, prompt, session_id, **kwargs):
        text = "".join(
            block.get("text", "") if isinstance(block, dict) else getattr(block, "text", "")
            for block in prompt
        )
        self.record("session/prompt", text=text, requested_cwd=str(self.cwd), actual_cwd=os.getcwd())
        gate = self.signal_dir / f"continue-{os.getpid()}"
        while not gate.exists():
            await asyncio.sleep(0.01)
        (self.cwd / "after-prompt.txt").write_text("delayed write", encoding="utf-8")
        await self.client.session_update(
            session_id=session_id,
            update=update_agent_message_text(text),
        )
        return PromptResponse(stop_reason="end_turn")


if __name__ == "__main__":
    asyncio.run(run_agent(WorkspacePeer(), use_unstable_protocol=True))
