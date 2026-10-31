"""Process-backed ACP runtime, including session negotiation and safety policy."""

from __future__ import annotations

import asyncio
import asyncio.subprocess as aio_subprocess
import os
import shlex
import tempfile
import threading
from collections.abc import AsyncGenerator, Coroutine, Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from acp import PROTOCOL_VERSION, connect_to_agent
from acp.connection import StreamDirection, StreamEvent
from acp.exceptions import RequestError
from acp.schema import (
    ClientCapabilities,
    DeniedOutcome,
    FileSystemCapabilities,
    Implementation,
    ReadTextFileResponse,
    RequestPermissionResponse,
    TextContentBlock,
)
from acp.transports import spawn_stdio_transport
from agent.file_safety import (
    get_nt_namespace_error,
    get_read_block_error,
    get_write_denied_error,
    is_write_approval_required,
)
from agent.redact import redact_sensitive_text
from agent.runtime_cwd import resolve_agent_cwd

from .errors import ACPCancelledError, ACPError, ACPTimeoutError, _safe_cause, _safe_detail
from .mcp import load_mcp_servers


# Command catalogs and tool results can exceed asyncio's default 64 KiB line limit.
DEFAULT_STREAM_LIMIT_BYTES = 16 * 1024 * 1024
_STDERR_LIMIT_BYTES = 8192
# SDK 0.9 closes stdin before its process wait; its drain/wait_closed steps are
# unbounded, so cap the whole manager exit and retain time for its normal grace.
_SHUTDOWN_TIMEOUT = 2.0
_STDIN_CLOSE_TIMEOUT = 0.5
_TERMINATE_TIMEOUT = 0.5
_PROCESS_REAP_TIMEOUT = 1.0
_MANAGER_SETTLE_TIMEOUT = 0.5
_CANCEL_SEND_TIMEOUT = 0.25
_CANCEL_CONFIRM_TIMEOUT = 0.5


async def _terminate_and_reap(process: asyncio.subprocess.Process) -> None:
    wait_task = asyncio.create_task(process.wait())
    if process.returncode is None:
        with suppress(ProcessLookupError, OSError):
            process.terminate()
    try:
        await asyncio.wait_for(asyncio.shield(wait_task), timeout=_TERMINATE_TIMEOUT)
    except TimeoutError:
        ACPRuntime._kill_process(process)
        try:
            await asyncio.wait_for(asyncio.shield(wait_task), timeout=_PROCESS_REAP_TIMEOUT)
        except TimeoutError as exc:
            raise RuntimeError("OMP ACP process did not exit after kill.") from exc
    finally:
        if not wait_task.done():
            wait_task.cancel()
            await asyncio.gather(wait_task, return_exceptions=True)


async def _exit_agent_process_manager(
    manager: Any,
    process: asyncio.subprocess.Process,
    exc_info: tuple[type[BaseException] | None, BaseException | None, Any],
    shutdown_timeout: float,
) -> BaseException | None:
    exit_task = asyncio.create_task(manager.__aexit__(*exc_info))
    cleanup_error: BaseException | None = None
    done, _ = await asyncio.wait(
        (exit_task,),
        timeout=shutdown_timeout + _STDIN_CLOSE_TIMEOUT,
    )
    if not done:
        try:
            await _terminate_and_reap(process)
        except BaseException as exc:
            cleanup_error = exc
        done, _ = await asyncio.wait((exit_task,), timeout=_MANAGER_SETTLE_TIMEOUT)
    if not done:
        exit_task.cancel()
        done, _ = await asyncio.wait((exit_task,), timeout=_MANAGER_SETTLE_TIMEOUT)
        if not done and cleanup_error is None:
            cleanup_error = RuntimeError("OMP ACP SDK process cleanup did not settle.")
    if done:
        if exit_task.cancelled():
            if cleanup_error is None:
                cleanup_error = RuntimeError("OMP ACP SDK process cleanup was cancelled.")
        else:
            try:
                exit_task.result()
            except BaseException as exc:
                if cleanup_error is None:
                    cleanup_error = exc
    if cleanup_error is not None and process.returncode is None:
        try:
            await _terminate_and_reap(process)
        except BaseException as exc:
            if hasattr(cleanup_error, "add_note"):
                cleanup_error.add_note(
                    f"Process escalation also failed ({type(exc).__name__}); cleanup error preserved."
                )
    return cleanup_error


async def _await_agent_process_exit(
    manager: Any,
    process: asyncio.subprocess.Process,
    exc_info: tuple[type[BaseException] | None, BaseException | None, Any],
    shutdown_timeout: float,
) -> tuple[BaseException | None, bool]:
    async def finish() -> BaseException | None:
        try:
            return await _exit_agent_process_manager(manager, process, exc_info, shutdown_timeout)
        except BaseException as exc:
            return exc

    finish_task = asyncio.create_task(finish())
    owner = asyncio.current_task()
    interrupted = False
    while not finish_task.done():
        try:
            await asyncio.shield(finish_task)
        except asyncio.CancelledError:
            interrupted = True
            if owner is not None:
                owner.uncancel()
        except BaseException:
            break
    return finish_task.result(), interrupted


class _FrameLimitError(ValueError):
    """Payload-free marker for asyncio's incoming JSONL size rejection."""


class _EOFReader(asyncio.StreamReader):
    """Observe terminal stdout reads without a second consumer or SDK patches.

    The SDK requires a StreamReader instance. Delegate only its readline API;
    the SDK remains the sole stdout consumer and keeps its normal frame ordering.
    """

    def __init__(self, source: asyncio.StreamReader) -> None:
        super().__init__()
        self._source = source
        self.failure: Exception | None = None
        self.failure_ready = asyncio.Event()

    async def readline(self) -> bytes:
        try:
            line = await self._source.readline()
            if not line:
                self.failure = ConnectionError("OMP ACP stdout closed.")
                self.failure_ready.set()
            return line
        except Exception as exc:
            # readline wraps LimitOverrunError in ValueError. Replace it before
            # the SDK logs a traceback; never include the rejected frame.
            if isinstance(exc, asyncio.LimitOverrunError) or isinstance(
                exc.__context__, asyncio.LimitOverrunError
            ):
                failure = _FrameLimitError("OMP ACP incoming frame exceeds the stream limit.")
            else:
                failure = _safe_cause(exc)
            self.failure = failure
            self.failure_ready.set()
            raise failure from None


@asynccontextmanager
async def _spawn_agent_connection(
    callbacks: Any, command: str, *args: str,
    transport_kwargs: Mapping[str, Any] | None = None, **kwargs: Any,
) -> AsyncGenerator[Any, None]:
    env = kwargs.pop("env", None)
    cwd = kwargs.pop("cwd", None)
    async with spawn_stdio_transport(
        command, *args, env=env, cwd=cwd, **dict(transport_kwargs or {}),
    ) as (reader, writer, process):
        stdout = _EOFReader(reader)
        connection = connect_to_agent(callbacks, writer, stdout, **kwargs)
        try:
            yield connection, process, stdout
        finally:
            await connection.close()


@asynccontextmanager
async def _bounded_spawn_agent_process(*args: Any, **kwargs: Any) -> AsyncGenerator[Any, None]:
    """Keep public SDK transport cleanup while bounding the complete teardown."""
    transport_kwargs = dict(kwargs.get("transport_kwargs") or {})
    shutdown_timeout = float(transport_kwargs.get("shutdown_timeout", _SHUTDOWN_TIMEOUT))
    manager = _spawn_agent_connection(*args, **kwargs)
    connection, process, stdout = await manager.__aenter__()
    process = cast(asyncio.subprocess.Process, process)
    try:
        yield connection, process, stdout
    except BaseException as primary:
        cleanup_error, _ = await _await_agent_process_exit(
            manager, process, (type(primary), primary, primary.__traceback__), shutdown_timeout,
        )
        if cleanup_error is not None and hasattr(primary, "add_note"):
            primary.add_note(
                f"OMP ACP cleanup also failed ({type(cleanup_error).__name__}); original failure preserved."
            )
        raise
    else:
        cleanup_error, interrupted = await _await_agent_process_exit(
            manager, process, (None, None, None), shutdown_timeout,
        )
        if cleanup_error is not None:
            raise cleanup_error
        if interrupted:
            raise asyncio.CancelledError




def validate_stream_limit_bytes(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("omp-acp-provider.settings.stream_limit_bytes must be a positive integer")
    return value


def _resolve_stream_limit_bytes(configured_limit: int) -> int:
    configured_limit = validate_stream_limit_bytes(configured_limit)
    override = os.getenv("HERMES_OMP_ACP_STREAM_LIMIT_BYTES", "").strip()
    if not override:
        return configured_limit
    try:
        limit = int(override)
        if limit <= 0:
            raise ValueError
    except ValueError:
        raise ValueError("HERMES_OMP_ACP_STREAM_LIMIT_BYTES must be a positive integer") from None
    return limit


def _resolve_command() -> str:
    return os.getenv("HERMES_OMP_ACP_COMMAND", "").strip() or "omp"


def _resolve_args() -> list[str]:
    return shlex.split(os.getenv("HERMES_OMP_ACP_ARGS", "").strip()) or ["acp"]


def _resolve_home_dir() -> str:
    """Return a stable HOME for a child ACP process."""
    if home := os.environ.get("HOME", "").strip():
        return home
    if (expanded := os.path.expanduser("~")) and expanded != "~":
        return expanded
    try:
        import pwd

        return pwd.getpwuid(os.getuid()).pw_dir.strip() or tempfile.gettempdir()
    except (ImportError, OSError, AttributeError, KeyError, TypeError):
        return tempfile.gettempdir()


def _build_subprocess_env() -> dict[str, str]:
    from hermes_constants import apply_subprocess_home_env
    from tools.environments.local import hermes_subprocess_env

    env = hermes_subprocess_env(inherit_credentials=True)
    env["HOME"] = _resolve_home_dir()
    apply_subprocess_home_env(env)
    return env


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(by_alias=True)
        return dumped if isinstance(dumped, Mapping) else {}
    return {}


def _validate_protocol_version(initialize_result: Any) -> None:
    version = _as_mapping(initialize_result).get("protocolVersion")
    if type(version) is not int or version != PROTOCOL_VERSION:
        raise ACPError(
            f"Unsupported OMP ACP protocol version {version!r}; supported version is {PROTOCOL_VERSION}.",
            phase="initialize",
            method="initialize",
            prompt_dispatched=False,
        )


def _validate_mcp_transports(mcp_servers: list[dict], initialize_result: Any) -> None:
    agent_capabilities = _as_mapping(_as_mapping(initialize_result).get("agentCapabilities"))
    mcp_capabilities = _as_mapping(agent_capabilities.get("mcpCapabilities"))
    unsupported = {
        server["type"]
        for server in mcp_servers
        if server.get("type") in {"http", "sse"}
        and mcp_capabilities.get(server["type"]) is not True
    }
    if unsupported:
        transports = ", ".join(sorted(unsupported))
        raise ACPError(
            f"OMP ACP agent does not support configured MCP transport(s): {transports}. "
            "The agent must advertise support during initialize.",
            phase="initialize",
            method="initialize",
            prompt_dispatched=False,
            failure_kind="deterministic",
        )


def _enabled_id_list(entries: Any, key: str) -> list[str]:
    """Return ordered, unique model ids, excluding ACP options explicitly disabled by Hermes."""
    seen: set[str] = set()
    result: list[str] = []
    for entry in entries or []:
        if not isinstance(entry, Mapping):
            continue
        value = str(entry.get(key) or "").strip()
        metadata = entry.get("_meta")
        disabled = (
            isinstance(metadata, Mapping)
            and str(metadata.get("copilotEnablement") or "").strip().lower() == "disabled"
        )
        if not value or value in seen or disabled:
            continue
        seen.add(value)
        result.append(value)
    return result


def _model_config_option(session: Mapping[str, Any]) -> Mapping[str, Any] | None:
    return next(
        (
            option
            for option in session.get("configOptions") or []
            if isinstance(option, Mapping)
            and "model" in (option.get("category"), option.get("id"))
        ),
        None,
    )


def _legacy_session_model_ids(session: Mapping[str, Any]) -> list[str]:
    models = session.get("models") or {}
    return _enabled_id_list(_as_mapping(models).get("availableModels"), "modelId")


def _session_model_ids(session: Mapping[str, Any]) -> list[str]:
    if option := _model_config_option(session):
        return _enabled_id_list(option.get("options"), "value")
    return _legacy_session_model_ids(session)


def _model_selection_request(
    session: Mapping[str, Any], requested_model: str
) -> tuple[str, dict[str, str]] | None:
    session_id = str(session.get("sessionId") or "").strip()
    requested_model = str(requested_model or "").strip()
    if not session_id or not requested_model:
        return None
    if option := _model_config_option(session):
        if requested_model not in _enabled_id_list(option.get("options"), "value"):
            return None
        return "session/set_config_option", {
            "sessionId": session_id,
            "configId": str(option.get("id") or "model"),
            "value": requested_model,
        }
    available = _legacy_session_model_ids(session)
    if available and requested_model not in available:
        return None
    return "session/set_model", {"sessionId": session_id, "modelId": requested_model}


def _ensure_path_within_cwd(path_text: str, cwd: str, *, verb: str) -> Path:
    if nt_error := get_nt_namespace_error(path_text, verb=verb):
        raise PermissionError(nt_error)
    if not Path(path_text).is_absolute():
        raise PermissionError("ACP file-system paths must be absolute.")
    resolved, root = Path(path_text).resolve(), Path(cwd).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise PermissionError(f"Path '{resolved}' is outside the session cwd '{root}'.") from exc
    return resolved


def _fs_read_text_file(params: Mapping[str, Any], cwd: str) -> dict[str, str]:
    path = _ensure_path_within_cwd(str(params.get("path") or ""), cwd, verb="Read")
    if block_error := get_read_block_error(str(path)):
        raise PermissionError(block_error)
    line, limit = params.get("line"), params.get("limit")
    start = line - 1 if isinstance(line, int) and line > 1 else 0
    count = limit if isinstance(limit, int) and limit > 0 else None
    try:
        if start or count is not None:
            with path.open(encoding="utf-8-sig") as stream:
                # Keep splitlines semantics (including Unicode separators) without
                # reading the rest of the file for a bounded request.
                lines = (part for physical in stream for part in physical.splitlines(keepends=True))
                selected = []
                for index, part in enumerate(lines):
                    if index < start:
                        continue
                    selected.append(part)
                    if count is not None and len(selected) >= count:
                        break
                content = "".join(selected)
        else:
            content = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        content = ""
    if content:
        content = redact_sensitive_text(content, force=True)
    return {"content": content}


def _fs_write_text_file(params: Mapping[str, Any], cwd: str) -> None:
    path = _ensure_path_within_cwd(str(params.get("path") or ""), cwd, verb="Write")
    if denied := get_write_denied_error(str(path)):
        raise PermissionError(denied)
    if is_write_approval_required(str(path)):
        raise PermissionError(
            f"Write denied: '{path}' requires interactive approval and cannot be written through the ACP file bridge."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(params.get("content") or ""), encoding="utf-8")


class _StderrDiagnostics:
    """Bound the entire block; never publish a context-free secret suffix.

    Chunk and newline boundaries are not redaction boundaries. If the whole
    block cannot be retained, discard it permanently while continuing to drain.
    """

    def __init__(self) -> None:
        self._data = bytearray()
        self._overflow = False
        self._finished = False

    @property
    def retained_bytes(self) -> int:
        return len(self._data)

    def feed(self, chunk: bytes) -> None:
        if self._overflow:
            return
        if len(self._data) + len(chunk) > _STDERR_LIMIT_BYTES:
            self._data.clear()
            self._overflow = True
        else:
            self._data.extend(chunk)

    def finish(self) -> None:
        self._finished = True

    def text(self) -> str:
        if self._overflow:
            return "[stderr omitted: diagnostic byte limit exceeded]"
        if not self._finished:
            return "[stderr omitted: incomplete diagnostic block]"
        return _safe_detail(self._data.decode("utf-8", errors="replace")).strip()


async def _drain_stderr(stream: asyncio.StreamReader, tail: _StderrDiagnostics) -> None:
    while chunk := await stream.read(4096):
        tail.feed(chunk)
    tail.finish()


async def _wait_for_stderr(task: asyncio.Task[None] | None, timeout: float = 0.5) -> None:
    if task is None:
        return
    with suppress(asyncio.CancelledError, Exception):
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)


def _process_exit_error(
    process: asyncio.subprocess.Process, tail: _StderrDiagnostics,
) -> RuntimeError:
    message = f"OMP ACP process exited early (exit code {process.returncode})"
    stderr_text = tail.text()
    return RuntimeError(f"{message}: {stderr_text}" if stderr_text else message)


def _request_phase(method: str) -> str:
    if method == "initialize":
        return "initialize"
    if method == "session/new":
        return "session"
    if method == "session/prompt":
        return "prompt"
    if method.startswith("session/set_"):
        return "model_selection"
    return "rpc"


def _failure_kind(method: str, cause: BaseException, prompt_dispatched: bool | None) -> str:
    if isinstance(cause, RequestError):
        return "deterministic"
    if method == "session/prompt" and prompt_dispatched is not False:
        return "outcome_unknown"
    current: BaseException | None = cause
    while current is not None:
        if isinstance(current, RequestError) or isinstance(current, FileNotFoundError):
            return "deterministic"
        if isinstance(current, (TimeoutError, ConnectionError, OSError)):
            return "pre_dispatch_transport"
        current = current.__cause__ or current.__context__
    return "deterministic"


@dataclass
class _RequestContext:
    phase: str = "startup"
    method: str | None = None
    prompt_dispatched: bool | None = False
    loop: asyncio.AbstractEventLoop | None = None
    task: asyncio.Task[Any] | None = None
    process: asyncio.subprocess.Process | None = None
    connection: Any = None
    session_id: str | None = None
    prompt_task: asyncio.Task[Any] | None = None
    cancel_task: asyncio.Task[Any] | None = None
    cancel_requested: bool = False


class _SessionCallbacks:
    def __init__(
        self,
        cwd: str,
        *,
        allow_file_requests: bool,
        request_context: _RequestContext,
    ):
        self.cwd = cwd
        self.allow_file_requests = allow_file_requests
        self.request_context = request_context
        self.text_parts: list[str] = []
        self.reasoning_parts: list[str] = []
        self.initializing = True
        self.initialize_request_id: int | str | None = None
        self.initialize_response: Mapping[str, Any] | None = None

    def observe(self, event: StreamEvent) -> None:
        if self.initializing:
            if (
                event.direction == StreamDirection.OUTGOING
                and event.message.get("method") == "initialize"
            ):
                self.initialize_request_id = event.message.get("id")
            elif (
                event.direction == StreamDirection.INCOMING
                and "method" not in event.message
                and "id" in event.message
                and "result" in event.message
            ):
                # Only initialization is outstanding. Retain at most one frame,
                # including a response observed before the outgoing drain ends.
                if (
                    self.initialize_response is None
                    or event.message["id"] == self.initialize_request_id
                ):
                    self.initialize_response = event.message
        # ACP's observer runs after the JSON-RPC frame has been drained to stdin.
        if (
            event.direction == StreamDirection.OUTGOING
            and event.message.get("method") == "session/prompt"
        ):
            self.request_context.prompt_dispatched = True
            return
        # The SDK dispatches callbacks in background tasks. Ingest updates on
        # receipt so a prompt response cannot overtake its final text chunks.
        if event.direction != StreamDirection.INCOMING or event.message.get("method") != "session/update":
            return
        params = _as_mapping(event.message.get("params"))
        update_data = _as_mapping(params.get("update"))
        content = _as_mapping(update_data.get("content"))
        text = str(content.get("text") or "")
        if not text:
            return
        kind = str(update_data.get("sessionUpdate") or update_data.get("session_update") or "").strip()
        if kind == "agent_message_chunk":
            self.text_parts.append(text)
        elif kind == "agent_thought_chunk":
            self.reasoning_parts.append(text)

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        # The synchronous stream observer has already consumed this update.
        return None

    async def request_permission(self, **kwargs: Any) -> RequestPermissionResponse:
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

    def _ensure_file_access(self, method: str) -> None:
        if not self.allow_file_requests:
            raise RequestError.method_not_found(method)

    async def read_text_file(
        self,
        path: str,
        session_id: str,
        limit: int | None = None,
        line: int | None = None,
        **kwargs: Any,
    ) -> ReadTextFileResponse:
        self._ensure_file_access("fs/read_text_file")
        try:
            result = _fs_read_text_file({"path": path, "line": line, "limit": limit}, self.cwd)
        except Exception as exc:
            raise RequestError(-32602, str(exc)) from exc
        return ReadTextFileResponse(content=result["content"])

    async def write_text_file(
        self,
        content: str,
        path: str,
        session_id: str,
        **kwargs: Any,
    ) -> None:
        self._ensure_file_access("fs/write_text_file")
        try:
            _fs_write_text_file({"path": path, "content": content}, self.cwd)
        except Exception as exc:
            raise RequestError(-32602, str(exc)) from exc

    async def create_terminal(self, **kwargs: Any) -> None:
        raise RequestError.method_not_found("terminal/create")

    async def terminal_output(self, **kwargs: Any) -> None:
        raise RequestError.method_not_found("terminal/output")

    async def release_terminal(self, **kwargs: Any) -> None:
        raise RequestError.method_not_found("terminal/release")

    async def wait_for_terminal_exit(self, **kwargs: Any) -> None:
        raise RequestError.method_not_found("terminal/wait_for_exit")

    async def kill_terminal(self, **kwargs: Any) -> None:
        raise RequestError.method_not_found("terminal/kill")


@dataclass
class _SessionState:
    connection: Any
    session: Mapping[str, Any]
    callbacks: _SessionCallbacks
    process: asyncio.subprocess.Process
    stderr_task: asyncio.Task[None] | None
    stderr_tail: _StderrDiagnostics
    request_context: _RequestContext
    stdout: _EOFReader


class ACPRuntime:
    """Owns all ACP process, session, and concurrency state for one client."""

    def __init__(
        self,
        *,
        acp_command: str | None = None,
        acp_args: list[str] | None = None,
        acp_cwd: str | None = None,
        command: str | None = None,
        args: list[str] | None = None,
        mcp_enabled: bool = True,
        stream_limit_bytes: int = DEFAULT_STREAM_LIMIT_BYTES,
    ):
        self._stream_limit_bytes = _resolve_stream_limit_bytes(stream_limit_bytes)
        self._acp_command = acp_command or command or _resolve_command()
        self._acp_args = list(
            acp_args if acp_args is not None else args if args is not None else _resolve_args()
        )
        self._acp_cwd = str(Path(acp_cwd or resolve_agent_cwd()).resolve())
        self._mcp_enabled = mcp_enabled
        self._is_closed = False
        self._active_processes: set[asyncio.subprocess.Process] = set()
        self._active_tasks: dict[
            asyncio.Task[Any],
            tuple[asyncio.AbstractEventLoop, int, _RequestContext],
        ] = {}
        self._active_lock = threading.Lock()
        self._generation = 0

    @property
    def is_closed(self) -> bool:
        return self._is_closed

    @staticmethod
    def _kill_process(process: asyncio.subprocess.Process) -> None:
        with suppress(ProcessLookupError, OSError):
            process.kill()

    def _begin_session(self, request_context: _RequestContext) -> tuple[asyncio.Task[Any], int]:
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("OMP ACP sessions require an asyncio task.")
        loop = asyncio.get_running_loop()
        request_context.loop = loop
        request_context.task = task
        with self._active_lock:
            generation = self._generation
            self._active_tasks[task] = (loop, generation, request_context)
        return task, generation

    def _register_process(
        self,
        process: asyncio.subprocess.Process,
        generation: int,
        request_context: _RequestContext,
    ) -> None:
        task = asyncio.current_task()
        if task is None:
            self._kill_process(process)
            raise RuntimeError("OMP ACP sessions require an asyncio task.")
        with self._active_lock:
            stale = generation != self._generation
            self._active_processes.add(process)
            self._active_tasks[task] = (
                request_context.loop or asyncio.get_running_loop(),
                generation,
                request_context,
            )
            request_context.process = process
            if stale:
                request_context.cancel_requested = True
            else:
                self._is_closed = False
        if stale or request_context.cancel_requested:
            raise asyncio.CancelledError

    def _finish_session(self, task: asyncio.Task[Any], process: asyncio.subprocess.Process | None) -> None:
        with self._active_lock:
            if process is not None:
                self._active_processes.discard(process)
            self._active_tasks.pop(task, None)
            if not self._active_processes:
                self._is_closed = True

    def _request_cancel(self, context: _RequestContext) -> None:
        if context.cancel_requested:
            return
        context.cancel_requested = True
        prompt_task = context.prompt_task
        if (
            context.phase == "prompt"
            and context.session_id
            and context.connection is not None
            and prompt_task is not None
            and not prompt_task.done()
        ):
            context.cancel_task = asyncio.create_task(self._cancel_prompt(context))
            return
        if context.process is None:
            # Do not cancel SDK __aenter__ before it returns its process handle;
            # _register_process observes this flag and exits through bounded cleanup.
            return
        self._cancel_owner_task(context)

    async def _notify_prompt_cancel(self, context: _RequestContext) -> bool:
        try:
            await asyncio.wait_for(
                context.connection.cancel(context.session_id),
                timeout=_CANCEL_SEND_TIMEOUT,
            )
        except (Exception, asyncio.CancelledError):
            return False

        prompt_task = context.prompt_task
        if prompt_task is None:
            return False
        try:
            await asyncio.wait_for(asyncio.shield(prompt_task), timeout=_CANCEL_CONFIRM_TIMEOUT)
        except (TimeoutError, asyncio.CancelledError):
            return False
        except Exception:
            # A terminal RPC error also confirms that the prompt has settled.
            return True
        return True

    async def _cancel_prompt(self, context: _RequestContext) -> None:
        if not await self._notify_prompt_cancel(context):
            self._cancel_owner_task(context)

    @staticmethod
    def _cancel_owner_task(context: _RequestContext) -> None:
        task = context.task
        if task is not None and not task.done():
            task.cancel()

    def _schedule_cancellation(
        self,
        requests: tuple[tuple[asyncio.AbstractEventLoop, _RequestContext], ...],
    ) -> None:
        for loop, context in requests:
            if loop.is_closed():
                continue
            with suppress(RuntimeError):
                loop.call_soon_threadsafe(self._request_cancel, context)

    def cancel(self) -> None:
        """Cancel all active requests owned by this client; request timeouts stay local."""
        with self._active_lock:
            requests = tuple((loop, request) for loop, _, request in self._active_tasks.values())
        self._schedule_cancellation(requests)

    async def _request(
        self, state: _SessionState, method: str, awaitable: Coroutine[Any, Any, Any],
    ) -> Any:
        context = state.request_context
        context.phase = _request_phase(method)
        context.method = method
        if method == "session/prompt":
            context.prompt_dispatched = None

        async def guarded_request() -> Any:
            # Check inside the task, immediately before SDK registration: EOF
            # can arrive after _request schedules it but before it starts.
            if state.stdout.failure is not None:
                if method == "session/prompt":
                    context.prompt_dispatched = False
                raise state.stdout.failure
            return await awaitable

        response_task = asyncio.create_task(guarded_request())
        if method == "session/prompt":
            context.prompt_task = response_task
        exit_task = asyncio.create_task(state.process.wait())
        read_failure_task = asyncio.create_task(state.stdout.failure_ready.wait())
        try:
            await asyncio.wait(
                (response_task, exit_task, read_failure_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            # The SDK resolves a preceding wire response before it reads EOF.
            # Prefer that result even when the EOF/exit waiter settles together.
            if response_task.done():
                result = response_task.result()
                if context.cancel_requested and method == "session/prompt":
                    raise ACPCancelledError(
                        "OMP ACP prompt was interrupted.",
                        phase=context.phase,
                        method=method,
                        prompt_dispatched=context.prompt_dispatched,
                    )
                return result
            if state.stdout.failure is not None:
                raise state.stdout.failure
            await _wait_for_stderr(state.stderr_task)
            raise _process_exit_error(state.process, state.stderr_tail)
        except asyncio.CancelledError as exc:
            if method == "session/prompt" and not context.cancel_requested and not response_task.done():
                # Native task cancellation and the request deadline are local
                # to this session. Notify its peer before abandoning the turn,
                # without changing the caller's cancellation/timeout exception.
                await self._notify_prompt_cancel(context)
            if context.cancel_requested:
                raise ACPCancelledError(
                    "OMP ACP request was interrupted.",
                    phase=context.phase,
                    method=method,
                    prompt_dispatched=context.prompt_dispatched,
                    cause=exc,
                ) from _safe_cause(exc)
            raise
        except Exception as exc:
            if context.cancel_requested and method == "session/prompt":
                raise ACPCancelledError(
                    "OMP ACP prompt was interrupted.",
                    phase=context.phase,
                    method=method,
                    prompt_dispatched=context.prompt_dispatched,
                    cause=exc,
                ) from _safe_cause(exc)
            if isinstance(exc, ACPCancelledError):
                raise
            rpc_code = getattr(exc, "code", None)
            if type(rpc_code) is not int:
                rpc_code = None
            if state.stdout.failure is not None and not isinstance(exc, _FrameLimitError):
                await _wait_for_stderr(state.stderr_task)
            message = f"OMP ACP {context.phase} ({method}) failed"
            if isinstance(exc, _FrameLimitError):
                message += (
                    f": incoming frame exceeds stream_limit_bytes={self._stream_limit_bytes}. "
                    "Increase plugins.entries.omp-acp-provider.settings.stream_limit_bytes "
                    "or HERMES_OMP_ACP_STREAM_LIMIT_BYTES for legitimate large messages, "
                    "then restart Hermes or its gateway. Request timeout is a separate control."
                )
            elif state.process.returncode is None:
                message += f": {_safe_cause(exc)}"
            if rpc_code is not None:
                message += f" (RPC code {rpc_code})"
            if state.process.returncode is not None:
                await _wait_for_stderr(state.stderr_task)
                message += f": {_process_exit_error(state.process, state.stderr_tail)}"
            raise ACPError(
                message,
                phase=context.phase,
                method=method,
                rpc_code=rpc_code,
                exit_code=state.process.returncode,
                prompt_dispatched=context.prompt_dispatched,
                failure_kind=_failure_kind(method, exc, context.prompt_dispatched),
                cause=exc,
            ) from _safe_cause(exc)
        finally:
            if context.prompt_task is response_task:
                context.prompt_task = None
            for pending in (response_task, exit_task, read_failure_task):
                if not pending.done():
                    pending.cancel()
            await asyncio.gather(response_task, exit_task, read_failure_task, return_exceptions=True)
            # Also close an SDK coroutine whose guarded task never started.
            awaitable.close()

    @asynccontextmanager
    async def _session(
        self,
        *,
        allow_file_requests: bool = True,
        request_context: _RequestContext | None = None,
    ) -> AsyncGenerator[_SessionState, None]:
        request_context = request_context or _RequestContext()
        try:
            mcp_servers = load_mcp_servers(enabled=self._mcp_enabled) if allow_file_requests else []
        except Exception as exc:
            detail = _safe_detail(str(exc))
            raise ACPError(
                f"Invalid OMP ACP configuration: {detail}",
                phase="configuration",
                prompt_dispatched=False,
                failure_kind="deterministic",
                cause=exc,
            ) from _safe_cause(exc)
        task, generation = self._begin_session(request_context)
        process: asyncio.subprocess.Process | None = None
        stderr_task: asyncio.Task[None] | None = None
        stderr_tail = _StderrDiagnostics()
        callbacks = _SessionCallbacks(
            self._acp_cwd,
            allow_file_requests=allow_file_requests,
            request_context=request_context,
        )
        try:
            try:
                async with _bounded_spawn_agent_process(
                    callbacks,
                    self._acp_command,
                    *self._acp_args,
                    env=_build_subprocess_env(),
                    cwd=self._acp_cwd,
                    transport_kwargs={
                        "stderr": aio_subprocess.PIPE,
                        "shutdown_timeout": _SHUTDOWN_TIMEOUT,
                        "limit": self._stream_limit_bytes,
                    },
                    use_unstable_protocol=True,
                    observers=[callbacks.observe],
                ) as (connection, spawned_process, stdout):
                    process = cast(asyncio.subprocess.Process, spawned_process)
                    request_context.connection = connection
                    self._register_process(process, generation, request_context)
                    if process.stderr is not None:
                        stderr_task = asyncio.create_task(_drain_stderr(process.stderr, stderr_tail))
                    state = _SessionState(
                        connection=connection,
                        session={},
                        callbacks=callbacks,
                        process=process,
                        stderr_task=stderr_task,
                        stderr_tail=stderr_tail,
                        request_context=request_context,
                        stdout=stdout,
                    )
                    initialize_result = await self._request(
                        state,
                        "initialize",
                        connection.initialize(
                            protocol_version=PROTOCOL_VERSION,
                            client_capabilities=ClientCapabilities(
                                fs=FileSystemCapabilities(read_text_file=True, write_text_file=True)
                            ),
                            client_info=Implementation(
                                name="hermes-agent",
                                title="Hermes Agent",
                                version="0.0.0",
                            ),
                        ),
                    )
                    callbacks.initializing = False
                    initialize_frame = callbacks.initialize_response
                    callbacks.initialize_response = None
                    raw_initialize_result = (
                        initialize_frame.get("result")
                        if initialize_frame is not None
                        and initialize_frame.get("id") == callbacks.initialize_request_id
                        else None
                    )
                    # SDK model validation coerces strings/bools/floats to int.
                    # Check the correlated wire result instead, outside observe:
                    # the SDK logs and suppresses observer exceptions.
                    _validate_protocol_version(raw_initialize_result)
                    _validate_mcp_transports(mcp_servers, initialize_result)
                    session = await self._request(
                        state,
                        "session/new",
                        connection.new_session(cwd=self._acp_cwd, mcp_servers=mcp_servers),
                    )
                    session_data = _as_mapping(session)
                    if not str(session_data.get("sessionId") or "").strip():
                        raise ACPError(
                            "OMP ACP did not return a sessionId.",
                            phase="session",
                            method="session/new",
                            prompt_dispatched=False,
                            failure_kind="deterministic",
                        )
                    state.session = session_data
                    request_context.session_id = str(session_data["sessionId"])
                    yield state
            except FileNotFoundError as exc:
                raise ACPError(
                    "Could not start the configured OMP ACP executable.",
                    phase="launch",
                    prompt_dispatched=False,
                    failure_kind="deterministic",
                    cause=exc,
                ) from _safe_cause(exc)
            except ACPError:
                raise
            except (RuntimeError, ValueError) as exc:
                detail = str(_safe_cause(exc))
                raise ACPError(
                    f"OMP ACP setup failed: {detail}",
                    phase=request_context.phase,
                    method=request_context.method,
                    prompt_dispatched=request_context.prompt_dispatched,
                    failure_kind="deterministic",
                    cause=exc,
                ) from _safe_cause(exc)
        finally:
            cancel_task = request_context.cancel_task
            if cancel_task is not None and not cancel_task.done():
                cancel_task.cancel()
                await asyncio.gather(cancel_task, return_exceptions=True)
            self._finish_session(task, process)
            await _wait_for_stderr(stderr_task)
            if stderr_task is not None and not stderr_task.done():
                stderr_task.cancel()
                with suppress(asyncio.CancelledError):
                    await stderr_task

    async def run(self, prompt: str, model: str | None, timeout_seconds: float) -> tuple[str, str, str]:
        request_context = _RequestContext()
        try:
            async with asyncio.timeout(timeout_seconds):
                async with self._session(request_context=request_context) as state:
                    requested_model = str(model or "").strip()
                    if requested_model:
                        request_context.phase = "model_selection"
                        request_context.method = None
                        request_context.prompt_dispatched = False
                        selection = _model_selection_request(state.session, requested_model)
                        if selection is None:
                            raise ACPError(
                                f"OMP ACP model {requested_model!r} is not offered by this session.",
                                phase="model_selection",
                                prompt_dispatched=False,
                                failure_kind="deterministic",
                            )
                        method, params = selection
                        if method == "session/set_config_option":
                            result = await self._request(
                                state,
                                method,
                                state.connection.set_config_option(
                                    config_id=params["configId"],
                                    session_id=params["sessionId"],
                                    value=params["value"],
                                ),
                            )
                        else:
                            result = await self._request(
                                state,
                                method,
                                state.connection.set_session_model(
                                    model_id=params["modelId"],
                                    session_id=params["sessionId"],
                                ),
                            )
                        result_data = _as_mapping(result)
                        option = _model_config_option(result_data)
                        models = _as_mapping(result_data.get("models"))
                        selected_model = option.get("currentValue") if option else models.get("currentModelId")
                        if selected_model is not None and selected_model != requested_model:
                            raise ACPError(
                                f"OMP ACP selected {selected_model!r} instead of requested model "
                                f"{requested_model!r}.",
                                phase="model_selection",
                                method=method,
                                prompt_dispatched=False,
                                failure_kind="deterministic",
                            )
                    prompt_response = await self._request(
                        state,
                        "session/prompt",
                        state.connection.prompt(
                            session_id=str(state.session["sessionId"]),
                            prompt=[TextContentBlock(type="text", text=prompt)],
                        ),
                    )
                    return (
                        "".join(state.callbacks.text_parts),
                        "".join(state.callbacks.reasoning_parts),
                        str(_as_mapping(prompt_response).get("stopReason") or ""),
                    )
        except (ACPError, ACPCancelledError) as exc:
            # Explicit safe causes do not stop Python from retaining the raw
            # implicit context. Remove it at the public runtime boundary.
            exc.__context__ = None
            raise
        except asyncio.CancelledError as exc:
            if request_context.cancel_requested:
                failure = ACPCancelledError(
                    "OMP ACP request was interrupted.",
                    phase=request_context.phase,
                    method=request_context.method,
                    prompt_dispatched=request_context.prompt_dispatched,
                    cause=exc,
                )
            else:
                raise
        except TimeoutError as exc:
            if request_context.cancel_requested:
                failure = ACPCancelledError(
                    "OMP ACP request was interrupted.",
                    phase=request_context.phase,
                    method=request_context.method,
                    prompt_dispatched=request_context.prompt_dispatched,
                    cause=exc,
                )
            else:
                failure_kind = (
                    "outcome_unknown"
                    if request_context.prompt_dispatched is not False
                    else "pre_dispatch_transport"
                )
                failure = ACPTimeoutError(
                    "Timed out waiting for OMP ACP response.",
                    phase=request_context.phase,
                    method=request_context.method,
                    prompt_dispatched=request_context.prompt_dispatched,
                    failure_kind=failure_kind,
                    cause=exc,
                )
        # Raise after leaving the handler so __context__ cannot retain a raw
        # transport/deadline exception behind the sanitized cause snapshot.
        raise failure

    async def list_models(self, timeout_seconds: float) -> list[str]:
        try:
            async with asyncio.timeout(timeout_seconds):
                async with self._session(allow_file_requests=False) as state:
                    return _session_model_ids(state.session)
        except (ACPError, ACPCancelledError) as exc:
            exc.__context__ = None
            raise
        except TimeoutError as exc:
            failure = TimeoutError("Timed out waiting for OMP ACP response.")
            failure.__cause__ = _safe_cause(exc)
        raise failure

    def close(self) -> None:
        with self._active_lock:
            self._generation += 1
            requests = tuple((loop, request) for loop, _, request in self._active_tasks.values())
            self._is_closed = True
        self._schedule_cancellation(requests)
