"""OpenAI-compatible client for short-lived native OMP ACP sessions."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from types import SimpleNamespace
from typing import Any

from ._bridge import (
    build_completion,
    completion_to_stream_chunks,
    format_messages_as_prompt,
)
from ._session import ACPRuntime, DEFAULT_STREAM_LIMIT_BYTES

_DEFAULT_TIMEOUT_SECONDS = 900.0


def _effective_timeout(timeout: Any) -> float:
    if isinstance(timeout, (int, float)):
        return float(timeout)
    candidates = [getattr(timeout, attr, None) for attr in ("read", "write", "connect", "pool", "timeout")]
    return max(
        (float(value) for value in candidates if isinstance(value, (int, float))),
        default=_DEFAULT_TIMEOUT_SECONDS,
    )


class OmpACPClient:
    """OpenAI-compatible facade that runs an isolated OMP ACP process per completion."""

    HERMES_SKIP_TRANSPORT_WRAP = True
    HERMES_SKIP_ASYNC_WRAP = True

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        default_headers: dict[str, str] | None = None,
        acp_command: str | None = None,
        acp_args: list[str] | None = None,
        acp_cwd: str | None = None,
        command: str | None = None,
        args: list[str] | None = None,
        mcp_enabled: bool = True,
        stream_limit_bytes: int = DEFAULT_STREAM_LIMIT_BYTES,
        **_: Any,
    ):
        self.api_key = api_key or "omp-acp"
        self.base_url = base_url or "acp://omp"
        self._default_headers = dict(default_headers or {})
        self._runtime = ACPRuntime(
            acp_command=acp_command,
            acp_args=acp_args,
            acp_cwd=acp_cwd,
            command=command,
            args=args,
            mcp_enabled=mcp_enabled,
            stream_limit_bytes=stream_limit_bytes,
        )
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create_chat_completion))

    @property
    def is_closed(self) -> bool:
        return self._runtime.is_closed

    def list_models(self, *, timeout_seconds: float = 15.0) -> list[str]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self._runtime.list_models(timeout_seconds))
        # Model discovery is a synchronous Hermes interface, including when its
        # caller already has an event loop. Run the SDK on a separate loop.
        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(
                copy_context().run,
                asyncio.run,
                self._runtime.list_models(timeout_seconds),
            ).result()

    def cancel(self) -> None:
        self._runtime.cancel()

    def close(self) -> None:
        self._runtime.close()

    def _completion(self, **kwargs: Any) -> Any:
        model = kwargs.get("model")
        prompt_text = format_messages_as_prompt(
            kwargs.get("messages") or [],
            tools=kwargs.get("tools"),
            tool_choice=kwargs.get("tool_choice"),
        )
        response_text, reasoning = asyncio.run(
            self._runtime.run(prompt_text, model, _effective_timeout(kwargs.get("timeout")))
        )
        return build_completion(response_text, reasoning, model)

    def _create_chat_completion(self, **kwargs: Any) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            completion = self._completion(**kwargs)
            return completion_to_stream_chunks(completion) if kwargs.get("stream") else completion
        return self._acreate_chat_completion(**kwargs)

    async def _acreate_chat_completion(self, **kwargs: Any) -> Any:
        model = kwargs.get("model")
        prompt_text = format_messages_as_prompt(
            kwargs.get("messages") or [],
            tools=kwargs.get("tools"),
            tool_choice=kwargs.get("tool_choice"),
        )
        response_text, reasoning = await self._runtime.run(
            prompt_text,
            model,
            _effective_timeout(kwargs.get("timeout")),
        )
        completion = build_completion(response_text, reasoning, model)
        if not kwargs.get("stream"):
            return completion

        async def chunks():
            for chunk in completion_to_stream_chunks(completion):
                yield chunk

        return chunks()
