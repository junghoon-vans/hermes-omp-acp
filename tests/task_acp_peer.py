"""ACP subprocess with explicit prompt barriers and a detectable delayed write."""

import asyncio
import json
import os
from pathlib import Path
import sys

from acp import Agent, PROTOCOL_VERSION, run_agent, update_agent_message_text
from acp.schema import InitializeResponse, NewSessionResponse, PromptResponse, SetSessionConfigOptionResponse

OPTIONS = [{"id": "model", "name": "Model", "category": "model", "type": "select",
            "currentValue": "fixture/model", "options": [{"value": "fixture/model", "name": "Fixture"}]}]


def json_value(value):
    if isinstance(value, list):
        return [json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if callable(getattr(value, "model_dump", None)):
        return json_value(value.model_dump(mode="json", by_alias=True))
    return value


class TaskPeer(Agent):
    def on_connect(self, client):
        self.client = client
        self.mode = sys.argv[1]
        self.signals = Path(sys.argv[2])

    def record(self, method, **details):
        with (self.signals / "protocol.jsonl").open("a", encoding="utf-8") as output:
            output.write(json.dumps({"method": method, "pid": os.getpid(), **details}) + "\n")

    async def initialize(self, protocol_version, **kwargs):
        return InitializeResponse(protocol_version=PROTOCOL_VERSION, agent_capabilities={})

    async def new_session(self, cwd, mcp_servers, **kwargs):
        self.cwd = Path(cwd)
        self.record("session/new", requested_cwd=cwd, actual_cwd=os.getcwd(),
                    command=sys.executable, argv=sys.argv, mcp_servers=json_value(mcp_servers))
        return NewSessionResponse.model_validate({"sessionId": "task-fixture",
            "configOptions": [] if self.mode == "no-model-exposure" else OPTIONS})

    async def set_config_option(self, config_id, session_id, value, **kwargs):
        self.record("session/set_config_option", config_id=config_id, value=value)
        if self.mode == "reject":
            raise ValueError("Model selection rejected")
        if self.mode == "timeout":
            await asyncio.sleep(30)
        if config_id != "model" or value != "fixture/model":
            raise ValueError("The requested model is unavailable")
        options = [{**OPTIONS[0], "currentValue": "fixture/default"}] if self.mode == "mismatch" else OPTIONS
        return SetSessionConfigOptionResponse.model_validate({"configOptions": options})

    async def prompt(self, prompt, session_id, **kwargs):
        text = "".join(block.get("text", "") if isinstance(block, dict) else getattr(block, "text", "") for block in prompt)
        self.record("session/prompt", text=text, requested_cwd=str(self.cwd), actual_cwd=os.getcwd())
        while not (self.signals / f"continue-{os.getpid()}").exists():
            await asyncio.sleep(0.01)
        (self.cwd / "after-prompt.txt").write_text("delayed write", encoding="utf-8")
        await self.client.session_update(session_id=session_id, update=update_agent_message_text(text))
        return PromptResponse(stop_reason="end_turn")


if __name__ == "__main__":
    asyncio.run(run_agent(TaskPeer(), use_unstable_protocol=True))
