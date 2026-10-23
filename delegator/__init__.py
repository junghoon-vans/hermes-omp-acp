"""Hermes tools delegating work to the official OMP ACP server."""

import hashlib
import json
from pathlib import Path
import threading

from .config import load_config
from .worker import Worker


def _session_key(session_id):
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("A trusted Hermes session ID is required.")
    return "session:" + hashlib.sha256(session_id.encode()).hexdigest()


def _data_dir(plugin_id):
    from plugins.plugin_storage import plugin_data_dir

    return plugin_data_dir(plugin_id)


class OMPPlugin:
    def __init__(self, ctx):
        self.ctx = ctx
        self._active = {}
        self._lock = threading.Lock()
        self._closed = False

    def _conversation(self, session_id):
        key = _session_key(session_id)
        conversation = self.ctx.state.get(key, default=session_id)
        if not isinstance(conversation, str) or not conversation.strip():
            raise ValueError("Invalid retained Hermes conversation identity.")
        return conversation

    def compacted(self, *, session_id="", old_session_id="", **_):
        if old_session_id and session_id and old_session_id != session_id:
            conversation = self._conversation(old_session_id)
            self.ctx.state.set(_session_key(session_id), conversation)

    def _run_key(self, conversation, repository):
        if not isinstance(repository, str) or not repository.strip():
            raise ValueError("An absolute repository root is required.")
        path = Path(repository).expanduser()
        if not path.is_absolute():
            raise ValueError("An absolute repository root is required.")
        return (
            str(_data_dir(self.ctx.plugin_id).resolve()),
            conversation,
            str(path.resolve(strict=True)),
        )

    def handle(self, args, *, session_id=None, status=False, **_):
        try:
            conversation = self._conversation(session_id)
            config = load_config(self.ctx)
            repository = args["repository"]
            worker = Worker(config)
            if status:
                return json.dumps(worker.status(conversation, repository), ensure_ascii=False)
            key = self._run_key(conversation, repository)
            cancelled = threading.Event()
            with self._lock:
                if self._closed:
                    raise RuntimeError("The OMP plugin is unloading; no task was started.")
                if key in self._active:
                    raise RuntimeError("This conversation already has an OMP task running in this repository.")
                self._active[key] = cancelled
            try:
                result = worker.run(
                    conversation,
                    repository,
                    args["prompt"],
                    cancelled=cancelled.is_set,
                    resume_interrupted=args.get("resume_interrupted") is True,
                )
            finally:
                with self._lock:
                    self._active.pop(key, None)
            return json.dumps(result, ensure_ascii=False)
        except (OSError, ValueError, KeyError, RuntimeError) as error:
            return json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False)

    def cancel(self, args, *, session_id=None, **_):
        try:
            conversation = self._conversation(session_id)
            key = self._run_key(conversation, args["repository"])
            with self._lock:
                event = self._active.get(key)
                if event is None:
                    result = {"status": "not_running", "repository": key[2]}
                else:
                    event.set()
                    result = {"status": "cancellation_requested", "repository": key[2]}
            return json.dumps(result, ensure_ascii=False)
        except (OSError, ValueError, KeyError, RuntimeError) as error:
            return json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False)

    def cancel_command(self, raw_args, *, session_id=None, **_):
        return self.cancel({"repository": raw_args.strip()}, session_id=session_id)

    def session_ended(self, *, session_id="", interrupted=False, **_):
        if interrupted:
            self.session_closed(session_id=session_id)

    def session_closed(self, *, session_id="", old_session_id="", **_):
        session_id = old_session_id or session_id
        if not session_id:
            return
        conversation = self._conversation(session_id)
        profile = str(_data_dir(self.ctx.plugin_id).resolve())
        with self._lock:
            for (run_profile, run_conversation, _), event in self._active.items():
                if run_profile == profile and run_conversation == conversation:
                    event.set()

    def unload(self):
        with self._lock:
            self._closed = True
            for event in self._active.values():
                event.set()


def register(ctx):
    plugin = OMPPlugin(ctx)
    repository = {"type": "string", "description": "Absolute path to the allowed Git repository root."}
    for name, description, properties, required, handler in (
        (
            "omp_task",
            "Run or continue a coding task through the installed OMP ACP server in a retained Git worktree. "
            "OMP uses its own permissions; extra permission requests are denied, not relayed to the user. "
            "Inspect omp_status after failure, interruption or permission_required; do not blindly retry.",
            {
                "repository": repository,
                "prompt": {"type": "string", "description": "Task or follow-up instruction for OMP."},
                "resume_interrupted": {
                    "type": "boolean",
                    "description": "Only after inspecting retained state and explicit user continuation.",
                },
            },
            ["repository", "prompt"],
            plugin.handle,
        ),
        (
            "omp_status",
            "Inspect this conversation's retained OMP task, worktree and result without another model turn.",
            {"repository": repository},
            ["repository"],
            lambda args, **kwargs: plugin.handle(args, status=True, **kwargs),
        ),
        (
            "omp_cancel",
            "Request cancellation of this conversation's running OMP task and its child processes.",
            {"repository": repository},
            ["repository"],
            plugin.cancel,
        ),
    ):
        ctx.register_tool(
            name=name,
            toolset="omp-acp-delegator",
            schema={
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
            },
            handler=handler,
        )
    ctx.register_command(
        "omp-cancel",
        handler=plugin.cancel_command,
        description="Cancel this conversation's OMP task: /omp-cancel /absolute/repository",
    )
    ctx.register_hook("post_compaction", plugin.compacted)
    ctx.register_hook("on_session_end", plugin.session_ended)
    ctx.register_hook("on_session_reset", plugin.session_closed)
    ctx.register_hook("on_session_finalize", plugin.session_closed)
    ctx.on_unload(plugin.unload)
