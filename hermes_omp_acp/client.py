"""OpenAI-compatible ACP completions with request-owned runtimes."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import threading
from types import SimpleNamespace
from typing import Any

from ._bridge import build_completion, completion_to_stream_chunks, format_messages_as_prompt
from ._session import ACPRuntime

_DEFAULT_TIMEOUT_SECONDS = 900.0


def _effective_timeout(timeout: Any) -> float:
    if isinstance(timeout, (int, float)):
        return float(timeout)
    candidates = [getattr(timeout, attr, None) for attr in ("read", "write", "connect", "pool", "timeout")]
    return max(
        (float(value) for value in candidates if isinstance(value, (int, float))),
        default=_DEFAULT_TIMEOUT_SECONDS,
    )


class _Request:
    """Keep cancellation scoped to one worker, including runtime startup."""

    def __init__(self):
        self._lock = threading.Lock()
        self._cancelled = False
        self._runtime = None
        self._task = None
        self._loop = None

    def check(self):
        with self._lock:
            if self._cancelled:
                raise RuntimeError("The OMP request was cancelled before startup.")

    def bind(self, runtime):
        with self._lock:
            if self._cancelled:
                raise RuntimeError("The OMP request was cancelled before startup.")
            self._runtime = runtime
            self._task = asyncio.current_task()
            self._loop = asyncio.get_running_loop()

    def cancel(self):
        with self._lock:
            self._cancelled = True

    def close(self):
        with self._lock:
            self._cancelled = True
            runtime, task, loop = self._runtime, self._task, self._loop
        if runtime is not None:
            runtime.close()
        if task is not None and not task.done():
            try:
                loop.call_soon_threadsafe(self._cancel_task, task)
            except RuntimeError:
                pass  # The worker already finished and closed its event loop.

    @staticmethod
    def _cancel_task(task):
        if not task.done() and task.cancelling() == 0:
            task.cancel()


class OmpACPClient:
    """Run each completion in an owned ACP runtime."""

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
        )
        self._request_lock = threading.Lock()
        self._requests = set()
        self._client_closed = False
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create_chat_completion))

    @property
    def is_closed(self) -> bool:
        with self._request_lock:
            return not self._requests and (self._client_closed or self._runtime.is_closed)

    def list_models(self, *, timeout_seconds: float = 15.0) -> list[str]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self._runtime.list_models(timeout_seconds))
        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(
                copy_context().run, asyncio.run, self._runtime.list_models(timeout_seconds),
            ).result()

    def close(self) -> None:
        with self._request_lock:
            self._client_closed = True
            requests = tuple(self._requests)
        for request in requests:
            request.close()
        self._runtime.close()

    def _complete(self, *, _request, **kwargs):
        _request.check()
        runtime = ACPRuntime(
            acp_command=self._runtime._acp_command,
            acp_args=self._runtime._acp_args,
            acp_cwd=self._runtime._acp_cwd,
            mcp_enabled=self._runtime._mcp_enabled,
        )
        model = kwargs.get("model")
        prompt = format_messages_as_prompt(
            kwargs.get("messages") or [], tools=kwargs.get("tools"), tool_choice=kwargs.get("tool_choice"),
        )

        async def run():
            _request.bind(runtime)
            return await runtime.run(prompt, model, _effective_timeout(kwargs.get("timeout")))

        try:
            text, reasoning = asyncio.run(run())
        finally:
            runtime.close()
        completion = build_completion(text, reasoning, model)
        return completion_to_stream_chunks(completion) if kwargs.get("stream") else completion

    def _new_request(self):
        request = _Request()
        with self._request_lock:
            if self._client_closed:
                raise RuntimeError("The OMP client was closed; no request was started.")
            self._requests.add(request)
        return request

    def _run_request(self, request, kwargs):
        try:
            return self._complete(_request=request, **kwargs)
        finally:
            request.close()
            with self._request_lock:
                self._requests.discard(request)

    def _create_chat_completion(self, **kwargs):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return self._run_request(self._new_request(), kwargs)
        return self._acreate_chat_completion(**kwargs)

    def _async_worker(self, request, kwargs):
        try:
            return self._run_request(request, kwargs), None
        except (Exception, asyncio.CancelledError) as error:
            return None, error

    async def _acreate_chat_completion(self, **kwargs):
        request = self._new_request()
        worker = asyncio.create_task(asyncio.to_thread(self._async_worker, request, kwargs))
        try:
            result, error = await asyncio.shield(worker)
        except asyncio.CancelledError:
            # Keep the request until its subprocess is reaped and its worker exits, including repeated cancellation.
            request.cancel()
            cancellation = asyncio.create_task(asyncio.to_thread(request.close))
            for pending in (cancellation, worker):
                while not pending.done():
                    try:
                        await asyncio.shield(pending)
                    except asyncio.CancelledError:
                        continue
                pending.result()
            raise
        if error is not None:
            raise error
        if not kwargs.get("stream"):
            return result

        async def chunks():
            for chunk in result:
                yield chunk

        return chunks()
