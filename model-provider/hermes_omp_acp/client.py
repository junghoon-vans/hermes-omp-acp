"""Keep Hermes's ACP transport and bridge usable from async auxiliary calls."""

import asyncio

from agent.copilot_acp_client import (
    CopilotACPClient,
    _model_config_option,
    _model_selection_request,
)


class OmpACPClient(CopilotACPClient):
    HERMES_SKIP_TRANSPORT_WRAP = True
    HERMES_SKIP_ASYNC_WRAP = True

    def _run_prompt(self, prompt_text: str, *, timeout_seconds: float, model: str | None = None) -> tuple[str, str]:
        requested_model = str(model or "").strip()
        with self._session(timeout_seconds) as (session, request):
            if requested_model:
                selection = _model_selection_request(session, requested_model)
                if selection is None:
                    raise RuntimeError(f"OMP ACP model {requested_model!r} is not offered by this session.")
                result = request(*selection) or {}
                option = _model_config_option(result)
                selected_model = (
                    option.get("currentValue") if option
                    else (result.get("models") or {}).get("currentModelId")
                )
                if selected_model is not None and selected_model != requested_model:
                    raise RuntimeError(
                        f"OMP ACP selected {selected_model!r} instead of requested model {requested_model!r}."
                    )

            text_parts: list[str] = []
            reasoning_parts: list[str] = []
            request(
                "session/prompt",
                {"sessionId": session["sessionId"], "prompt": [{"type": "text", "text": prompt_text}]},
                text_parts=text_parts,
                reasoning_parts=reasoning_parts,
            )
            return "".join(text_parts), "".join(reasoning_parts)

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
