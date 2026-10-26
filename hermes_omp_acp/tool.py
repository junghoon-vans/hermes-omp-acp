"""Synchronous Hermes tool with scoped interruption and retained Git workspaces."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from contextvars import copy_context
import json
import math
from pathlib import Path
import threading
from typing import Callable
import uuid

from ._session import ACPRuntime
from .workspaces import WorkspaceStore


class _Stopped(Exception):
    pass


class _Invocation:
    """Cancel only this runtime; the dispatcher joins its worker before returning."""

    def __init__(self):
        self.stopped = threading.Event()
        self._lock = threading.Lock()
        self._runtime = None
        self._loop = None

    def check(self):
        if self.stopped.is_set():
            raise _Stopped("OMP task interrupted.")

    async def run(self, runtime, goal, model, timeout):
        # Binding and entering runtime.run happen in one event-loop turn. A stop
        # during spawn invalidates the runtime generation; it does not cancel a
        # subprocess-creation await before the child can be registered and reaped.
        with self._lock:
            self._runtime = runtime
            self._loop = asyncio.get_running_loop()
        self.check()
        return await runtime.run(goal, model, timeout)

    def stop(self):
        with self._lock:
            if self.stopped.is_set():
                return
            self.stopped.set()
            runtime, loop = self._runtime, self._loop
        if runtime is not None:
            try:
                loop.call_soon_threadsafe(runtime.close)
            except RuntimeError:
                pass  # Worker has already drained and closed its loop.


def _settings(settings):
    if not isinstance(settings, dict):
        raise ValueError("omp-task.settings must be an object.")
    model = settings.get("model_id")
    command = settings.get("command", "omp")
    args = settings.get("args", ["acp"])
    mcp_enabled = settings.get("mcp_enabled", True)
    timeout = settings.get("timeout_seconds", 900.0)
    if not isinstance(model, str) or not model.strip():
        raise ValueError("omp-task.settings.model_id must be a non-empty model ID.")
    if not isinstance(command, str) or not command.strip():
        raise ValueError("omp-task.settings.command must be a non-empty executable string.")
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        raise ValueError("omp-task.settings.args must be a list of strings.")
    if not isinstance(mcp_enabled, bool):
        raise ValueError("omp-task.settings.mcp_enabled must be a boolean.")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("omp-task.settings.timeout_seconds must be positive and finite.")
    return model.strip(), command, list(args), mcp_enabled, float(timeout)


def run_task(arguments: dict, *, settings: dict, store_root: Path, interrupted: Callable[[], bool]) -> str:
    """Run one goal. Reusing task_id reuses files, never an ACP conversation.

    This handler deliberately stays synchronous: Hermes's async registry bridge
    may move coroutines to another thread. Poll the interrupt on the actual tool
    dispatch thread instead, including its acting_for_tid context when present.
    """
    result = {"task_id": None, "path": None, "branch": None, "base_commit": None,
              "response": "", "status": "error"}
    invocation = _Invocation()
    try:
        if not isinstance(arguments, dict) or set(arguments) - {"repository", "goal", "task_id"}:
            raise ValueError("omp_task accepts only repository, goal, and optional task_id.")
        goal = arguments.get("goal")
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("goal must be a non-empty string.")
        task_id = arguments.get("task_id", uuid.uuid4().hex)
        result["task_id"] = task_id if isinstance(task_id, str) else None
        model, command, args, mcp_enabled, timeout = _settings(settings)
        if interrupted():
            raise _Stopped("OMP task interrupted before workspace acquisition.")
        store = WorkspaceStore(store_root, settings.get("allowed_roots"))

        def execute():
            try:
                invocation.check()
                with store.acquire({"repository": arguments.get("repository"), "task_id": task_id}) as workspace:
                    result.update(path=str(workspace.path), branch=workspace.branch, base_commit=workspace.base_commit)
                    invocation.check()
                    runtime = ACPRuntime(command=command, args=args, acp_cwd=str(workspace.path), mcp_enabled=mcp_enabled)
                    try:
                        text, _reasoning = asyncio.run(invocation.run(runtime, goal, model, timeout))
                        invocation.check()
                        result.update(response=text, status="success")
                    finally:
                        runtime.close()
            except (_Stopped, asyncio.CancelledError) as error:
                result.update(status="cancelled", error=str(error) or "OMP task interrupted.")
            except Exception as error:
                result.update(status="cancelled" if invocation.stopped.is_set() else "error", error=str(error))

        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="omp-task") as executor:
            worker = executor.submit(copy_context().run, execute)
            try:
                while True:
                    if interrupted():
                        invocation.stop()
                    try:
                        worker.result(timeout=0.05)
                        break
                    except FutureTimeout:
                        continue
            except BaseException:
                invocation.stop()
                # Repeated caller cancellation must not release a workspace lock
                # or return while the owned child/worker can still write files.
                while not worker.done():
                    try:
                        worker.result(timeout=0.05)
                    except (FutureTimeout, KeyboardInterrupt):
                        continue
                worker.result()
                raise
    except _Stopped as error:
        result.update(status="cancelled", error=str(error))
    except Exception as error:
        result.update(status="error", error=str(error))
    return json.dumps(result, ensure_ascii=False)
