"""Register OMP through Hermes's external-process provider interface."""

from contextlib import closing
from dataclasses import dataclass

from providers import register_provider
from providers.base import ProviderProfile


@dataclass
class OmpACPProfile(ProviderProfile):
    mcp_enabled: bool = True

    def create_client(self, **client_kwargs):
        from .client import OmpACPClient
        from hermes_cli.auth import resolve_external_process_provider_credentials

        if not client_kwargs.get("command"):
            credentials = resolve_external_process_provider_credentials(self.name)
            client_kwargs["command"] = credentials["command"]
            client_kwargs["args"] = client_kwargs.get("args") or credentials["args"]
        client_kwargs.setdefault("api_key", self.name)
        client_kwargs.setdefault("base_url", self.base_url)
        client_kwargs.setdefault("mcp_enabled", self.mcp_enabled)
        return OmpACPClient(**client_kwargs)

    def fetch_models(self, *, api_key=None, base_url=None, timeout=15):
        try:
            with closing(self.create_client()) as client:
                return client.list_models(timeout_seconds=timeout) or None
        except (OSError, RuntimeError):
            # Hermes's catalog contract uses None when a CLI cannot be probed.
            return None


def register():
    from hermes_cli.config import load_config_readonly

    settings = (load_config_readonly().get("plugins", {})
                .get("entries", {}).get("omp-acp-provider", {}).get("settings", {}))
    command = settings.get("command", "omp")
    args = settings.get("args", ["acp"])
    mcp_enabled = settings.get("mcp_enabled", True)
    if not isinstance(command, str) or not command.strip():
        raise ValueError("omp-acp-provider.settings.command must be a non-empty executable string")
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        raise ValueError("omp-acp-provider.settings.args must be a list of strings")
    if not isinstance(mcp_enabled, bool):
        raise ValueError("omp-acp-provider.settings.mcp_enabled must be a boolean")

    register_provider(OmpACPProfile(
        name="omp-acp",
        display_name="Oh My Pi (ACP)",
        description="Use the native OMP agent through ACP",
        auth_type="external_process",
        api_mode="chat_completions",
        base_url="acp://omp",
        env_vars=(),
        process_command=command,
        process_args=tuple(args),
        mcp_enabled=mcp_enabled,
        process_command_env_vars=("HERMES_OMP_ACP_COMMAND",),
        process_args_env_var="HERMES_OMP_ACP_ARGS",
    ))
