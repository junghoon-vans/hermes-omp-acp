"""Register OMP through Hermes's external-process provider interface."""

from contextlib import closing

from providers import register_provider
from providers.base import ProviderProfile


class OmpACPProfile(ProviderProfile):
    def create_client(self, **client_kwargs):
        from .client import OmpACPClient
        from hermes_cli.auth import resolve_external_process_provider_credentials

        if not client_kwargs.get("command"):
            credentials = resolve_external_process_provider_credentials(self.name)
            client_kwargs["command"] = credentials["command"]
            client_kwargs["args"] = client_kwargs.get("args") or credentials["args"]
        client_kwargs.setdefault("api_key", self.name)
        client_kwargs.setdefault("base_url", self.base_url)
        return OmpACPClient(**client_kwargs)

    def fetch_models(self, *, api_key=None, base_url=None, timeout=15):
        try:
            with closing(self.create_client()) as client:
                return client.list_models(timeout_seconds=timeout) or None
        except (OSError, RuntimeError):
            # Hermes's catalog contract uses None when a CLI cannot be probed.
            return None


def register(*, scope=None):
    register_provider(OmpACPProfile(
        name="omp-acp",
        display_name="Oh My Pi (ACP)",
        description="Use the native OMP agent through ACP",
        auth_type="external_process",
        api_mode="chat_completions",
        base_url="acp://omp",
        env_vars=(),
        process_command="omp",
        process_args=("acp",),
        process_command_env_vars=("HERMES_OMP_ACP_COMMAND",),
        process_args_env_var="HERMES_OMP_ACP_ARGS",
    ))

    # Filesystem providers follow their profile's lifetime; an installed wheel's
    # stateless backend is global and reads the active profile on each request.
    from agent.delegation_registry import register_provider as register_delegation

    from .delegation import OmpWorktreeDelegationProvider

    register_delegation(OmpWorktreeDelegationProvider(), scope=scope)
