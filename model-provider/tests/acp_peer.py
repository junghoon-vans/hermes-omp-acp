"""Deterministic ACP peer for testing the provider's client interface."""

import asyncio
from pathlib import Path
import sys

from acp import Agent, PROTOCOL_VERSION, run_agent, update_agent_message_text
from acp.schema import InitializeResponse, NewSessionResponse, PromptResponse, SetSessionConfigOptionResponse


OPTIONS = [{
    "id": "model", "name": "Model", "category": "model", "type": "select",
    "currentValue": "fixture/model", "options": [{"value": "fixture/model", "name": "Fixture"}],
}]


class Peer(Agent):
    def on_connect(self, client):
        self.client = client
        self.mode = sys.argv[1] if len(sys.argv) > 1 else "config"

    async def initialize(self, protocol_version, **kwargs):
        return InitializeResponse(protocol_version=PROTOCOL_VERSION, agent_capabilities={})

    async def new_session(self, cwd, mcp_servers, **kwargs):
        self.cwd = Path(cwd)
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
        await asyncio.sleep(0.15)
        chunks = ("first", " second") if self.model == "fixture/model" else ("wrong model",)
        for text in chunks:
            await self.client.session_update(session_id=session_id, update=update_agent_message_text(text))
        return PromptResponse(stop_reason="end_turn")


if __name__ == "__main__":
    asyncio.run(run_agent(Peer(), use_unstable_protocol=True))
