"""Hermes ACP transport with request-local, task-owned Git workspaces."""

import asyncio
import threading

from agent.copilot_acp_client import CopilotACPClient

from .workspaces import WorkspaceStore


class _WorkspaceACPClient(CopilotACPClient):
    """Make closing a request atomic with its subprocess startup."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._spawn_lock = threading.Lock()
        self._cancelled = False

    def _spawn(self):
        with self._spawn_lock:
            if self._cancelled:
                raise RuntimeError("The OMP workspace request was closed before startup.")
            return super()._spawn()

    def close(self):
        with self._spawn_lock:
            self._cancelled = True
            super().close()


class OmpACPClient(CopilotACPClient):
    HERMES_SKIP_TRANSPORT_WRAP = True
    HERMES_SKIP_ASYNC_WRAP = True

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._workspace_clients = set()
        self._workspace_lock = threading.Lock()
        self._workspace_closed = False

    def close(self):
        with self._workspace_lock:
            self._workspace_closed = True
            clients = tuple(self._workspace_clients)
        for client in clients:
            client.close()
        super().close()

    def _complete(self, **kwargs):
        from hermes_cli.config import load_config_readonly
        from plugins.plugin_storage import plugin_data_dir

        config = load_config_readonly() or {}
        delegation = config.get("delegation") or {}
        extra_body = kwargs.get("extra_body") or {}
        if not isinstance(extra_body, dict):
            raise ValueError("OMP extra_body must be an object.")
        metadata = extra_body.get("hermes_omp_acp")
        if metadata is None:
            if delegation.get("backend") == "omp-worktree":
                raise ValueError("OMP worktree requests require a structured repository and task identity.")
            return super()._create_chat_completion(**kwargs)

        with self._workspace_lock:
            if self._workspace_closed:
                raise RuntimeError("The OMP client was closed; no workspace request was started.")

        policy = (delegation.get("backends") or {}).get("omp-worktree") or {}
        store = WorkspaceStore(
            plugin_data_dir("omp-acp-provider") / "workspaces",
            policy.get("allowed_roots"),
        )
        with store.acquire(metadata) as workspace:
            client = _WorkspaceACPClient(
                api_key=self.api_key,
                base_url=self.base_url,
                default_headers=self._default_headers,
                acp_command=self._acp_command,
                acp_args=self._acp_args,
                acp_cwd=str(workspace.path),
            )
            with self._workspace_lock:
                if self._workspace_closed:
                    client.close()
                    raise RuntimeError("The OMP client was closed; no workspace request was started.")
                self._workspace_clients.add(client)
            try:
                request = {**kwargs, "extra_body": {
                    key: value for key, value in extra_body.items() if key != "hermes_omp_acp"
                }}
                return client._create_chat_completion(**request)
            finally:
                client.close()
                with self._workspace_lock:
                    self._workspace_clients.discard(client)

    def _create_chat_completion(self, **kwargs):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return self._complete(**kwargs)
        return self._acreate_chat_completion(**kwargs)

    async def _acreate_chat_completion(self, **kwargs):
        result = await asyncio.to_thread(self._complete, **kwargs)
        if not kwargs.get("stream"):
            return result

        # The upstream bridge has already buffered these chunks; iteration is local.
        async def chunks():
            for chunk in result:
                yield chunk

        return chunks()
