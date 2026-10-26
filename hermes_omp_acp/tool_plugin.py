"""Hermes general-plugin entry point; separate from the model provider loader."""

from .tool import run_task


SCHEMA = {
    "name": "omp_task",
    "description": (
        "Run a goal with OMP in a retained isolated Git worktree. Requires an explicit "
        "allowed repository root. Reuse task_id to reuse files, not the ACP conversation; "
        "every call starts a fresh session. Worktrees remain after success, failure, or stop."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "repository": {"type": "string", "description": "Absolute Git repository root allowed by operator configuration."},
            "goal": {"type": "string", "description": "Instructions for this fresh OMP session."},
            "task_id": {"type": "string", "pattern": "^[0-9a-f]{32}$", "description": "Retained task identity; omitted generates a UUID (32 lowercase hex characters)."},
        },
        "required": ["repository", "goal"],
        "additionalProperties": False,
    },
}


def register(ctx):
    def handler(arguments, **_kwargs):
        from plugins.plugin_storage import plugin_data_dir
        from tools.interrupt import is_interrupted

        settings = {key: ctx.get_config(key, default) for key, default in (
            ("model_id", None), ("allowed_roots", None), ("command", "omp"),
            ("args", ["acp"]), ("mcp_enabled", True), ("timeout_seconds", 900.0),
        )}
        return run_task(arguments, settings=settings,
                        store_root=plugin_data_dir("omp-task") / "workspaces",
                        interrupted=is_interrupted)

    ctx.register_tool(name="omp_task", toolset="omp_task", schema=SCHEMA,
                      handler=handler, is_async=False)
