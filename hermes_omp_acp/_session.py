"""Process-backed ACP runtime, including session negotiation and safety policy."""

from __future__ import annotations

import asyncio
import asyncio.subprocess as aio_subprocess
import os
import shlex
import tempfile
import threading
from collections import deque
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from acp import PROTOCOL_VERSION, spawn_agent_process
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
from agent.file_safety import (
    get_nt_namespace_error,
    get_read_block_error,
    get_write_denied_error,
    is_write_approval_required,
)
from agent.redact import redact_sensitive_text
from agent.runtime_cwd import resolve_agent_cwd

from .mcp import load_mcp_servers


# Command catalogs and tool results can exceed asyncio's default 64 KiB line limit.
DEFAULT_STREAM_LIMIT_BYTES = 16 * 1024 * 1024
# SDK 0.9 closes stdin before its process wait; its drain/wait_closed steps are
# unbounded, so cap the whole manager exit and retain time for its normal grace.
_SHUTDOWN_TIMEOUT = 2.0
_STDIN_CLOSE_TIMEOUT = 0.5
_TERMINATE_TIMEOUT = 0.5
_PROCESS_REAP_TIMEOUT = 1.0
_MANAGER_SETTLE_TIMEOUT = 0.5


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


@asynccontextmanager
async def _bounded_spawn_agent_process(*args: Any, **kwargs: Any) -> AsyncGenerator[Any, None]:
    """Keep the public SDK connection manager while bounding its subprocess teardown."""
    transport_kwargs = dict(kwargs.get("transport_kwargs") or {})
    shutdown_timeout = float(transport_kwargs.get("shutdown_timeout", _SHUTDOWN_TIMEOUT))
    manager = spawn_agent_process(*args, **kwargs)
    connection, process = await manager.__aenter__()
    process = cast(asyncio.subprocess.Process, process)
    try:
        yield connection, process
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


async def _wait_for_stderr(task: asyncio.Task[None] | None, timeout: float = 0.5) -> None:
    if task is None:
        return
    with suppress(asyncio.CancelledError, Exception):
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)


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
        raise RuntimeError(
            f"OMP ACP agent does not support configured MCP transport(s): {transports}. "
            "The agent must advertise support during initialize."
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
    try:
        content = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        content = ""
    line, limit = params.get("line"), params.get("limit")
    if isinstance(line, int) and line > 1:
        end = line - 1 + limit if isinstance(limit, int) and limit > 0 else None
        content = "".join(content.splitlines(keepends=True)[line - 1 : end])
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


async def _drain_stderr(stream: asyncio.StreamReader, tail: deque[str]) -> None:
    pending = ""
    while chunk := await stream.read(4096):
        lines = (pending + chunk.decode("utf-8", errors="replace")).splitlines(keepends=True)
        pending = ""
        if lines and not lines[-1].endswith(("\n", "\r")):
            pending = lines.pop()
        tail.extend(line.rstrip("\r\n") for line in lines)
        if len(pending) > 8192:
            tail.append(pending[-8192:])
            pending = ""
    if pending:
        tail.append(pending)


async def _wait_for_stderr(task: asyncio.Task[None] | None, timeout: float = 0.5) -> None:
    if task is None:
        return
    with suppress(asyncio.CancelledError, Exception):
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)


def _process_exit_error(process: asyncio.subprocess.Process, tail: deque[str]) -> RuntimeError:
    stderr_text = "\n".join(tail).strip()
    return RuntimeError(f"OMP ACP process exited early: {stderr_text or f'exit code {process.returncode}'}")


class _SessionCallbacks:
    def __init__(self, cwd: str, *, allow_file_requests: bool):
        self.cwd = cwd
        self.allow_file_requests = allow_file_requests
        self.text_parts: list[str] = []
        self.reasoning_parts: list[str] = []

    def observe(self, event: StreamEvent) -> None:
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
    stderr_tail: deque[str]


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
        self._active_tasks: dict[asyncio.Task[Any], tuple[asyncio.AbstractEventLoop, int]] = {}
        self._session_processes: dict[asyncio.Task[Any], asyncio.subprocess.Process] = {}
        self._active_lock = threading.Lock()
        self._generation = 0

    @property
    def is_closed(self) -> bool:
        return self._is_closed

    @staticmethod
    def _kill_process(process: asyncio.subprocess.Process) -> None:
        with suppress(ProcessLookupError, OSError):
            process.kill()

    def _begin_session(self) -> tuple[asyncio.Task[Any], int]:
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("OMP ACP sessions require an asyncio task.")
        loop = asyncio.get_running_loop()
        with self._active_lock:
            generation = self._generation
            self._active_tasks[task] = (loop, generation)
        return task, generation

    def _register_process(self, process: asyncio.subprocess.Process, generation: int) -> None:
        task = asyncio.current_task()
        if task is None:
            self._kill_process(process)
            raise RuntimeError("OMP ACP sessions require an asyncio task.")
        with self._active_lock:
            stale = generation != self._generation
            if not stale:
                self._active_processes.add(process)
                self._session_processes[task] = process
                self._is_closed = False
        if stale:
            self._kill_process(process)
            raise asyncio.CancelledError

    def _finish_session(self, task: asyncio.Task[Any], process: asyncio.subprocess.Process | None) -> None:
        with self._active_lock:
            if process is not None:
                self._active_processes.discard(process)
            self._active_tasks.pop(task, None)
            self._session_processes.pop(task, None)
            if not self._active_processes:
                self._is_closed = True

    async def _request(self, state: _SessionState, method: str, awaitable: Any) -> Any:
        response_task = asyncio.ensure_future(awaitable)
        exit_task = asyncio.create_task(state.process.wait())
        try:
            done, _ = await asyncio.wait((response_task, exit_task), return_when=asyncio.FIRST_COMPLETED)
            if response_task in done:
                return response_task.result()
            await _wait_for_stderr(state.stderr_task)
            raise _process_exit_error(state.process, state.stderr_tail)
        except Exception as exc:
            if state.process.returncode is not None:
                await _wait_for_stderr(state.stderr_task)
                raise _process_exit_error(state.process, state.stderr_tail) from exc
            raise RuntimeError(f"OMP ACP {method} failed: {exc}") from exc
        finally:
            for pending in (response_task, exit_task):
                if not pending.done():
                    pending.cancel()
            await asyncio.gather(response_task, exit_task, return_exceptions=True)

    @asynccontextmanager
    async def _session(self, *, allow_file_requests: bool = True) -> AsyncGenerator[_SessionState, None]:
        mcp_servers = load_mcp_servers(enabled=self._mcp_enabled) if allow_file_requests else []
        task, generation = self._begin_session()
        process: asyncio.subprocess.Process | None = None
        stderr_task: asyncio.Task[None] | None = None
        stderr_tail: deque[str] = deque(maxlen=40)
        callbacks = _SessionCallbacks(self._acp_cwd, allow_file_requests=allow_file_requests)
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
                ) as (connection, spawned_process):
                    process = cast(asyncio.subprocess.Process, spawned_process)
                    self._register_process(process, generation)
                    if process.stderr is not None:
                        stderr_task = asyncio.create_task(_drain_stderr(process.stderr, stderr_tail))
                    state = _SessionState(
                        connection=connection,
                        session={},
                        callbacks=callbacks,
                        process=process,
                        stderr_task=stderr_task,
                        stderr_tail=stderr_tail,
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
                    _validate_mcp_transports(mcp_servers, initialize_result)
                    session = await self._request(
                        state,
                        "session/new",
                        connection.new_session(cwd=self._acp_cwd, mcp_servers=mcp_servers),
                    )
                    session_data = _as_mapping(session)
                    if not str(session_data.get("sessionId") or "").strip():
                        raise RuntimeError("OMP ACP did not return a sessionId.")
                    state.session = session_data
                    yield state
            except FileNotFoundError as exc:
                raise RuntimeError(
                    f"Could not start OMP ACP command '{self._acp_command}'. Install OMP or configure its command."
                ) from exc
        finally:
            self._finish_session(task, process)
            await _wait_for_stderr(stderr_task)
            if stderr_task is not None and not stderr_task.done():
                stderr_task.cancel()
                with suppress(asyncio.CancelledError):
                    await stderr_task

    async def run(self, prompt: str, model: str | None, timeout_seconds: float) -> tuple[str, str]:
        try:
            async with asyncio.timeout(timeout_seconds):
                async with self._session() as state:
                    requested_model = str(model or "").strip()
                    if requested_model:
                        selection = _model_selection_request(state.session, requested_model)
                        if selection is None:
                            raise RuntimeError(f"OMP ACP model {requested_model!r} is not offered by this session.")
                        method, params = selection
                        try:
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
                        except RuntimeError:
                            raise
                        except Exception as exc:
                            raise RuntimeError(f"OMP ACP {method} failed: {exc}") from exc
                        result_data = _as_mapping(result)
                        option = _model_config_option(result_data)
                        models = _as_mapping(result_data.get("models"))
                        selected_model = option.get("currentValue") if option else models.get("currentModelId")
                        if selected_model is not None and selected_model != requested_model:
                            raise RuntimeError(
                                f"OMP ACP selected {selected_model!r} instead of requested model {requested_model!r}."
                            )
                    await self._request(
                        state,
                        "session/prompt",
                        state.connection.prompt(
                            session_id=str(state.session["sessionId"]),
                            prompt=[TextContentBlock(type="text", text=prompt)],
                        ),
                    )
                    return "".join(state.callbacks.text_parts), "".join(state.callbacks.reasoning_parts)
        except TimeoutError as exc:
            raise TimeoutError("Timed out waiting for OMP ACP response.") from exc

    async def list_models(self, timeout_seconds: float) -> list[str]:
        try:
            async with asyncio.timeout(timeout_seconds):
                async with self._session(allow_file_requests=False) as state:
                    return _session_model_ids(state.session)
        except TimeoutError as exc:
            raise TimeoutError("Timed out waiting for OMP ACP response.") from exc

    def close(self) -> None:
        with self._active_lock:
            self._generation += 1
            processes = tuple(self._active_processes)
            tasks = tuple(
                (task, loop)
                for task, (loop, _) in self._active_tasks.items()
                if task in self._session_processes
            )
            self._is_closed = True
        for process in processes:
            self._kill_process(process)
        for task, loop in tasks:
            if not task.done() and task.cancelling() == 0:
                with suppress(RuntimeError):
                    loop.call_soon_threadsafe(task.cancel)
