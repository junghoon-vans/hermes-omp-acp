"""ACP peer returning terminal outcomes and deliberately incomplete tool output."""

import asyncio

from acp import run_agent, update_agent_message_text
from acp.schema import PromptResponse

from acp_peer import Peer


TOOL = '<tool_call>{"id":"fixture-call","type":"function","function":{"name":"lookup","arguments":"{\\"query\\":\\"value\\"}"}}</tool_call>'


class OutcomesPeer(Peer):
    async def prompt(self, prompt, session_id, **kwargs):
        with (self.cwd / "prompt-receipts").open("a", encoding="ascii") as receipts:
            receipts.write("received\n")
        if self.mode == "end_turn-tools":
            stop_reason, text = "end_turn", TOOL
        elif self.mode == "cancelled-tools":
            stop_reason, text = "cancelled", TOOL
        elif self.mode == "max_tokens-incomplete":
            stop_reason, text = "max_tokens", TOOL.removesuffix("</tool_call>")
        else:
            stop_reason = self.mode
            text = "I cannot proceed." if stop_reason == "refusal" else "Partial answer."
        await self.client.session_update(session_id=session_id, update=update_agent_message_text(text))
        return PromptResponse(stop_reason=stop_reason)


if __name__ == "__main__":
    asyncio.run(run_agent(OutcomesPeer(), use_unstable_protocol=True))
