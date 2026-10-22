"""Deterministic ACP peer for testing the provider's client interface."""

import asyncio

from acp import Agent, PROTOCOL_VERSION, run_agent, update_agent_message_text
from acp.schema import InitializeResponse, NewSessionResponse, PromptResponse, SetSessionConfigOptionResponse


OPTIONS = [{
    "id": "model", "name": "Model", "category": "model", "type": "select",
    "currentValue": "fixture/model", "options": [{"value": "fixture/model", "name": "Fixture"}],
}]


class Peer(Agent):
    def on_connect(self, client):
        self.client = client

    async def initialize(self, protocol_version, **kwargs):
        return InitializeResponse(protocol_version=PROTOCOL_VERSION, agent_capabilities={})

    async def new_session(self, cwd, mcp_servers, **kwargs):
        return NewSessionResponse.model_validate({"sessionId": "fixture", "configOptions": OPTIONS})

    async def set_config_option(self, config_id, session_id, value, **kwargs):
        if config_id != "model" or value != "fixture/model":
            raise ValueError("The requested model is unavailable")
        return SetSessionConfigOptionResponse.model_validate({"configOptions": OPTIONS})

    async def prompt(self, prompt, session_id, **kwargs):
        await asyncio.sleep(0.15)
        for text in ("first", " second"):
            await self.client.session_update(session_id=session_id, update=update_agent_message_text(text))
        return PromptResponse(stop_reason="end_turn")


if __name__ == "__main__":
    asyncio.run(run_agent(Peer()))
