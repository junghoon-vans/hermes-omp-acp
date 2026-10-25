"""Register OMP through Hermes's external-process provider interface."""

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

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


def _delegation_registration():
    from importlib import import_module

    try:
        registry = import_module("agent.delegation_registry")
        delegation_api = import_module("agent.delegation_provider")
    except ModuleNotFoundError as error:
        if error.name in {"agent.delegation_provider", "agent.delegation_registry"}:
            return None
        raise

    register_delegation = getattr(registry, "register_provider", None)
    if not callable(register_delegation) or not all(
        hasattr(delegation_api, name)
        for name in ("DelegationProvider", "DelegationRouteError")
    ):
        return None

    from .delegation import OmpWorktreeDelegationProvider

    return register_delegation, OmpWorktreeDelegationProvider


def _omp_worktree_selected(scope):
    if scope is None:
        from hermes_constants import get_hermes_home

        scope = get_hermes_home()

    config_path = Path(scope) / "config.yaml"
    try:
        config_text = config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False

    from utils import fast_safe_load

    config = fast_safe_load(config_text)
    delegation = config.get("delegation") if isinstance(config, dict) else None
    return isinstance(delegation, dict) and delegation.get("backend") == "omp-worktree"


def _require_delegation_support():
    raise RuntimeError(
        "The configured delegation backend 'omp-worktree' requires Hermes delegation APIs "
        "(agent.delegation_provider and agent.delegation_registry), which are unavailable. "
        "Upgrade Hermes to a release with these APIs or select a different delegation backend."
    )


def register(*, scope=None):
    delegation = _delegation_registration()
    if delegation is None and _omp_worktree_selected(scope):
        _require_delegation_support()

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

    if delegation is None:
        return

    # Filesystem providers follow their profile's lifetime; an installed wheel's
    # stateless backend is global and reads the active profile on each request.
    register_delegation, provider_type = delegation
    register_delegation(provider_type(), scope=scope)

