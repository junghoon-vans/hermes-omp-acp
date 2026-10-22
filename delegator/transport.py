"""OMP ACP transport using the official Python ACP client connection."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import signal
import time
from typing import Callable

from acp import PROTOCOL_VERSION, text_block
from acp.client import ClientSideConnection
from acp.schema import (
    ClientCapabilities,
    DeniedOutcome,
    FileSystemCapabilities,
    Implementation,
    RequestPermissionResponse,
)


_MAX_RESPONSE_CHARS = 64_000
_MAX_TOOL_CALLS = 200
_MAX_PERMISSION_REQUESTS = 200
_CANCEL_SEND_TIMEOUT = 0.25


class Cancelled(RuntimeError):
    """The caller requested cancellation of the current OMP task."""


class OMPClient:
    """Collect ACP task updates and deny every permission request."""

    def __init__(self) -> None:
        self.session_id: str | None = None
        self._response = ""
        self._tool_calls: dict[str, dict] = {}
        self._permission_requests: list[dict] = []
        self.tool_calls_truncated = False
        self.permission_requests_truncated = False

    def bind_session(self, session_id: str) -> None:
        self.session_id = session_id

    def clear_events(self) -> None:
        """Discard events replayed by session/load before the new prompt."""
        self._response = ""
        self._tool_calls.clear()
        self._permission_requests.clear()
        self.tool_calls_truncated = False
        self.permission_requests_truncated = False

    @property
    def response(self) -> str:
        return self._response

    @property
    def tool_calls(self) -> list[dict]:
        return list(self._tool_calls.values())

    @property
    def permission_requests(self) -> list[dict]:
        return list(self._permission_requests)

    @property
    def has_tool_errors(self) -> bool:
        return self.tool_calls_truncated or any(
            call.get("status") != "completed"
            for call in self._tool_calls.values()
        )

    @staticmethod
    def _dump(value) -> dict:
        return value.model_dump(mode="json", by_alias=True, exclude_none=True, exclude_unset=True)

    async def session_update(self, session_id: str, update, **kwargs) -> None:
        if self.session_id is not None and session_id != self.session_id:
            return
        value = self._dump(update)
        kind = value.get("sessionUpdate")
        if kind == "agent_message_chunk":
            content = value.get("content") or {}
            if content.get("type") == "text":
                text = content.get("text", "")
                if len(text) >= _MAX_RESPONSE_CHARS:
                    self._response = text[-_MAX_RESPONSE_CHARS:]
                elif text:
                    self._response = (self._response + text)[-_MAX_RESPONSE_CHARS:]
        elif kind in ("tool_call", "tool_call_update"):
            call_id = value.get("toolCallId")
            if not call_id:
                return
            current = self._tool_calls.get(call_id)
            if current is None:
                if len(self._tool_calls) >= _MAX_TOOL_CALLS:
                    evict = next(
                        (
                            key
                            for key, call in self._tool_calls.items()
                            if call.get("status") == "completed"
                        ),
                        None,
                    )
                    self.tool_calls_truncated = True
                    if evict is None:
                        return
                    del self._tool_calls[evict]
                current = {}
                self._tool_calls[call_id] = current
            current.update(value)

    async def request_permission(self, options: list, session_id: str, tool_call, **kwargs) -> RequestPermissionResponse:
        request = {
            "sessionId": session_id,
            "toolCall": self._dump(tool_call),
            "options": [self._dump(option) for option in options],
        }
        if len(self._permission_requests) == _MAX_PERMISSION_REQUESTS:
            del self._permission_requests[0]
            self.permission_requests_truncated = True
        self._permission_requests.append(request)
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))


async def run_omp_session(
    command: list[str],
    *,
    cwd: Path,
    log_path: Path,
    session_id: str | None,
    prompt: str,
    client: OMPClient,
    cancelled: Callable[[], bool],
    timeout: float,
    on_session: Callable[[str], None],
) -> str:
    """Run one ACP turn, preserving the supplied OMP session across calls."""
    if not command:
        raise ValueError("The OMP ACP command is empty.")
    deadline = time.monotonic() + timeout
    if cancelled():
        raise Cancelled("OMP task cancelled; partial changes remain in its worktree.")
    if deadline <= time.monotonic():
        raise TimeoutError("OMP task timed out; inspect its retained worktree before continuing.")

    log_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    with log_path.open("ab", buffering=0) as stderr:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=stderr,
            env=os.environ.copy(),
            start_new_session=True,
        )
        if process.stdin is None or process.stdout is None:
            await _stop_process_group(process)
            raise RuntimeError("OMP ACP subprocess did not provide stdin and stdout pipes.")

        if session_id:
            client.bind_session(session_id)
        connection = ClientSideConnection(client, process.stdin, process.stdout)
        wait_task = asyncio.create_task(process.wait())
        prompt_active = False
        try:
            capabilities = ClientCapabilities(
                fs=FileSystemCapabilities(read_text_file=False, write_text_file=False),
                terminal=False,
            )
            await _controlled(
                connection.initialize(
                    PROTOCOL_VERSION,
                    client_capabilities=capabilities,
                    client_info=Implementation(name="hermes-omp-acp-delegator", version="2.0.0"),
                ),
                "initialize",
                connection,
                wait_task,
                deadline,
                cancelled,
                lambda: client.session_id,
            )
            if session_id:

                try:
                    await _controlled(
                        connection.load_session(str(cwd), session_id, mcp_servers=[]),
                        "session/load",
                        connection,
                        wait_task,
                        deadline,
                        cancelled,
                        lambda: client.session_id,
                    )
                except Exception:
                    client.clear_events()
                    raise
            else:
                session = await _controlled(
                    connection.new_session(str(cwd), mcp_servers=[]),
                    "session/new",
                    connection,
                    wait_task,
                    deadline,
                    cancelled,
                    lambda: client.session_id,
                )
                session_id = session.session_id
                if not session_id:
                    raise RuntimeError("OMP did not return an ACP session ID.")
                client.bind_session(session_id)
                on_session(session_id)

            client.clear_events()
            prompt_active = True
            try:
                response = await _controlled(
                    connection.prompt(
                        [
                            text_block(
                                prompt
                                + "\n\nWork only in the current worktree. Do not push, deploy or change permissions without a separate explicit human approval. Report actual changes and verification commands with exit codes; never invent evidence."
                            )
                        ],
                        session_id,
                    ),
                    "session/prompt",
                    connection,
                    wait_task,
                    deadline,
                    cancelled,
                    lambda: client.session_id,
                )
            finally:
                prompt_active = False
            return response.stop_reason
        finally:
            if prompt_active and client.session_id:
                await _send_cancel(connection, client.session_id)
            await _close_connection(connection)
            await _stop_process_group(process, wait_task)


async def _controlled(
    awaitable,
    operation: str,
    connection: ClientSideConnection,
    process_wait: asyncio.Task,
    deadline: float,
    cancelled: Callable[[], bool],
    session_id: Callable[[], str | None],
):
    if cancelled():
        awaitable.close()
        known_session = session_id()
        if known_session:
            await _send_cancel(connection, known_session)
        raise Cancelled("OMP task cancelled; partial changes remain in its worktree.")
    if time.monotonic() >= deadline:
        awaitable.close()
        known_session = session_id()
        if known_session:
            await _send_cancel(connection, known_session)
        raise TimeoutError("OMP task timed out; inspect its retained worktree before continuing.")
    if process_wait.done():
        awaitable.close()
        raise _early_exit(operation, process_wait)

    request = asyncio.create_task(awaitable)
    watcher = asyncio.create_task(
        _watch_controls(connection, deadline, cancelled, session_id)
    )
    try:
        done, _ = await asyncio.wait(
            (request, watcher, process_wait),
            return_when=asyncio.FIRST_COMPLETED,
        )
        if request in done:
            return request.result()
        if watcher in done:
            return watcher.result()
        raise _early_exit(operation, process_wait)
    finally:
        if not watcher.done():
            watcher.cancel()
        if not request.done():
            request.cancel()
        await asyncio.gather(watcher, request, return_exceptions=True)


async def _watch_controls(
    connection: ClientSideConnection,
    deadline: float,
    cancelled: Callable[[], bool],
    session_id: Callable[[], str | None],
) -> None:
    while True:
        if cancelled():
            known_session = session_id()
            if known_session:
                await _send_cancel(connection, known_session)
            raise Cancelled("OMP task cancelled; partial changes remain in its worktree.")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            known_session = session_id()
            if known_session:
                await _send_cancel(connection, known_session)
            raise TimeoutError("OMP task timed out; inspect its retained worktree before continuing.")
        await asyncio.sleep(min(0.1, remaining))


async def _send_cancel(connection: ClientSideConnection, session_id: str) -> None:
    try:
        await asyncio.wait_for(connection.cancel(session_id), timeout=_CANCEL_SEND_TIMEOUT)
    except (Exception, asyncio.CancelledError):
        pass


def _early_exit(operation: str, process_wait: asyncio.Task) -> RuntimeError:
    code = process_wait.result() if process_wait.done() else "unknown"
    return RuntimeError(f"OMP ACP subprocess exited during {operation} (exit code {code}).")


async def _close_connection(connection: ClientSideConnection) -> None:
    try:
        await asyncio.wait_for(connection.close(), timeout=0.5)
    except (Exception, asyncio.CancelledError):
        pass


async def _stop_process_group(process: asyncio.subprocess.Process, wait_task: asyncio.Task | None = None) -> None:
    if process.stdin is not None and not process.stdin.is_closing():
        process.stdin.close()
        try:
            await asyncio.wait_for(process.stdin.wait_closed(), timeout=0.25)
        except (Exception, asyncio.CancelledError):
            pass

    if process.returncode is None:
        try:
            if wait_task is None:
                await asyncio.wait_for(process.wait(), timeout=0.5)
            else:
                await asyncio.wait_for(asyncio.shield(wait_task), timeout=0.5)
        except asyncio.TimeoutError:
            pass

    _signal_group(process, signal.SIGTERM)
    if process.returncode is None:
        try:
            if wait_task is None:
                await asyncio.wait_for(process.wait(), timeout=0.5)
            else:
                await asyncio.wait_for(asyncio.shield(wait_task), timeout=0.5)
        except asyncio.TimeoutError:
            pass

    # The parent may exit before descendants that inherited its process group.
    _signal_group(process, signal.SIGKILL)
    if process.returncode is None:
        try:
            if wait_task is None:
                await asyncio.wait_for(process.wait(), timeout=2.0)
            else:
                await asyncio.wait_for(asyncio.shield(wait_task), timeout=2.0)
        except asyncio.TimeoutError:
            process.kill()
            if wait_task is None:
                await process.wait()
            else:
                await asyncio.shield(wait_task)
    # communicate() drains EOF and closes pipe transports through the public
    # asyncio API; wait() alone can leave stdout open when the loop exits.
    await asyncio.wait_for(process.communicate(), timeout=2.0)
    if wait_task is not None and wait_task.done():
        await asyncio.gather(wait_task, return_exceptions=True)


def _signal_group(process: asyncio.subprocess.Process, sig: signal.Signals) -> None:
    if os.name == "nt":
        if process.returncode is None:
            try:
                process.send_signal(sig)
            except ProcessLookupError:
                pass
        return
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        pass
