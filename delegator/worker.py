"""One retained OMP session/worktree per Hermes conversation and repository."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid

from .transport import Cancelled, OMPClient, run_omp_session


class Worker:
    def __init__(self, config):
        self.config = config
        self.root = Path(config["state_dir"]).expanduser().resolve()
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.git = config["git"]

    def _git(self, cwd, *args):
        result = subprocess.run(
            [self.git, "-C", str(cwd), "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", *args],
            text=True,
            capture_output=True,
            timeout=30,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        if result.returncode:
            raise RuntimeError(f"Git {' '.join(args[:2])} failed: {result.stderr.strip()}")
        return result.stdout.strip()

    def _repository(self, value):
        repository = Path(value).expanduser().resolve(strict=True)
        roots = [Path(root).expanduser().resolve() for root in self.config["allowed_roots"]]
        if not any(repository.is_relative_to(root) for root in roots):
            raise ValueError("Repository is outside the configured repository roots.")
        top = Path(self._git(repository, "rev-parse", "--show-toplevel")).resolve()
        if top != repository:
            raise ValueError("Specify the repository root, not a subdirectory.")
        return repository

    def _state_path(self, conversation, repository):
        key = hashlib.sha256(f"{conversation}\0{repository}".encode()).hexdigest()
        return self.root / f"{key}.json"

    @contextmanager
    def _task(self, conversation, repository):
        if not conversation:
            raise ValueError("A trusted Hermes session identity is required.")
        state_path = self._state_path(conversation, repository)
        fd = os.open(state_path.with_suffix(".lock"), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("This conversation already has an OMP task running in this repository.") from None
            state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else None
            if state and (state.get("conversation") != conversation or state.get("repository") != str(repository)):
                raise RuntimeError("Retained task ownership does not match the caller.")
            yield state_path, state
        finally:
            os.close(fd)

    def _save(self, path, state):
        temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(state, stream, ensure_ascii=False)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def _create(self, conversation, repository):
        task_id = uuid.uuid4().hex
        worktree = self.root / "worktrees" / task_id
        worktree.parent.mkdir(mode=0o700, exist_ok=True)
        base = self._git(repository, "rev-parse", "HEAD")
        branch = f"hermes/{task_id}"
        self._git(repository, "worktree", "add", "-b", branch, str(worktree), base)
        return {
            "task_id": task_id,
            "conversation": conversation,
            "repository": str(repository),
            "worktree": str(worktree),
            "base_commit": base,
            "branch": branch,
            "session_id": None,
            "status": "created",
            "response": "",
            "tool_calls": [],
            "permission_requests": [],
        }

    def status(self, conversation, repository):
        repository = self._repository(repository)
        if not conversation:
            raise ValueError("A trusted Hermes session identity is required.")
        state_path = self._state_path(conversation, repository)
        if not state_path.exists():
            return {"status": "not_started", "repository": str(repository)}
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("conversation") != conversation or state.get("repository") != str(repository):
            raise RuntimeError("Retained task ownership does not match the caller.")
        return self._result(state)

    @staticmethod
    def _result(state):
        return {key: value for key, value in state.items() if key != "conversation"}

    def run(self, conversation, repository, prompt, *, cancelled, resume_interrupted=False):
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("A non-empty task or follow-up instruction is required.")
        repository = self._repository(repository)
        with self._task(conversation, repository) as (path, state):
            if state is None:
                state = self._create(conversation, repository)
                self._save(path, state)
            elif state["status"] in ("running", "failed", "cancelled", "permission_required", "completed_with_errors") and not resume_interrupted:
                raise RuntimeError(
                    f"Previous run is {state['status']}; inspect omp_status and obtain an explicit "
                    "user continuation before setting resume_interrupted. Never blindly retry."
                )

            worktree = Path(state["worktree"])
            if self._git(worktree, "rev-parse", "--show-toplevel") != str(worktree):
                raise RuntimeError("The retained worktree is missing or no longer valid.")

            state.update(
                status="running",
                response="",
                tool_calls=[],
                permission_requests=[],
                tool_calls_truncated=False,
                permission_requests_truncated=False,
            )
            state.pop("error", None)
            state.pop("stop_reason", None)
            self._save(path, state)

            client = OMPClient()
            try:
                stop_reason = asyncio.run(
                    run_omp_session(
                        list(self.config["command"]),
                        cwd=worktree,
                        log_path=self.root / f"{state['task_id']}.stderr.log",
                        session_id=state.get("session_id"),
                        prompt=prompt,
                        client=client,
                        cancelled=cancelled,
                        timeout=self.config.get("timeout", 1800),
                        on_session=lambda value: self._save_session(path, state, value),
                    )
                )
                state["stop_reason"] = stop_reason
                if client.permission_requests:
                    state["status"] = "permission_required"
                elif stop_reason != "end_turn":
                    raise RuntimeError(f"OMP did not finish normally: {stop_reason}")
                elif client.has_tool_errors:
                    state["status"] = "completed_with_errors"
                    if client.tool_calls_truncated:
                        state["error"] = "OMP emitted more than 200 tool calls; some tool-call details were omitted."
                    else:
                        state["error"] = "OMP returned failed or incomplete tool calls; inspect tool_calls."
                else:
                    state["status"] = "completed"
            except Exception as error:
                state["status"] = "cancelled" if isinstance(error, Cancelled) else "failed"
                state["error"] = str(error)

            state["response"] = client.response
            state["tool_calls"] = client.tool_calls
            state["permission_requests"] = client.permission_requests
            state["tool_calls_truncated"] = client.tool_calls_truncated
            state["permission_requests_truncated"] = client.permission_requests_truncated

            try:
                state["changes"] = self._git(worktree, "status", "--short")
                state["diff_stat"] = self._git(
                    worktree,
                    "diff",
                    "--no-ext-diff",
                    "--no-textconv",
                    "--stat",
                    state["base_commit"],
                )
                state["head"] = self._git(worktree, "rev-parse", "HEAD")
            except Exception as error:
                state["status"] = "failed"
                state["error"] = f"{state.get('error', '')} Finalization: {error}".strip()
            self._save(path, state)
            return self._result(state)

    def _save_session(self, path, state, session_id):
        state["session_id"] = session_id
        self._save(path, state)
