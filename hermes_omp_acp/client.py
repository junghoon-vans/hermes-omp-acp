"""Keep Hermes's ACP transport and bridge usable from async auxiliary calls."""

import asyncio

from agent.copilot_acp_client import CopilotACPClient


class OmpACPClient(CopilotACPClient):
    HERMES_SKIP_TRANSPORT_WRAP = True
    HERMES_SKIP_ASYNC_WRAP = True

    def _create_chat_completion(self, **kwargs):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return super()._create_chat_completion(**kwargs)
        return self._acreate_chat_completion(**kwargs)

    async def _acreate_chat_completion(self, **kwargs):
        result = await asyncio.to_thread(super()._create_chat_completion, **kwargs)
        if not kwargs.get("stream"):
            return result

        # The upstream bridge has already buffered these chunks; iteration is local.
        async def chunks():
            for chunk in result:
                yield chunk

        return chunks()
