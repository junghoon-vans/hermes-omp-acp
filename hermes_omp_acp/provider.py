"""Register OMP through Hermes's external-process provider interface."""

from contextlib import closing
from dataclasses import dataclass, field
from typing import Any

from providers import register_provider
from providers.base import ProviderProfile

from ._session import DEFAULT_STREAM_LIMIT_BYTES, validate_stream_limit_bytes
from .errors import ACPError, ACPCancelledError, ACPTimeoutError


def _classify_acp_error(
    error: BaseException,
    *,
    status_code: int | None = None,
    error_code: str | None = None,
    message: str | None = None,
    body: Any = None,
    model: str | None = None,
) -> dict[str, Any] | None:
    if isinstance(error, ACPCancelledError) or not isinstance(error, (ACPError, ACPTimeoutError)):
        return None

    context: dict[str, Any] = {
        "phase": error.phase,
        "failure_kind": error.failure_kind,
    }
    if error.method is not None:
        context["method"] = error.method
    if error.rpc_code is not None:
        context["rpc_code"] = error.rpc_code
    if error.exit_code is not None:
        context["exit_code"] = error.exit_code
    if error.prompt_dispatched is None:
        context["prompt_dispatch_uncertain"] = True
    else:
        context["prompt_dispatched"] = error.prompt_dispatched

    if error.failure_kind == "pre_dispatch_transport" and error.prompt_dispatched is False:
        return {
            "reason": "timeout",
            "retryable": True,
            "should_fallback": False,
            "error_context": context,
        }
    return {
        "reason": "format_error",
        "retryable": False,
        "should_fallback": False,
        "error_context": context,
    }


@dataclass
class OmpACPProfile(ProviderProfile):
    mcp_enabled: bool = True
    stream_limit_bytes: int = DEFAULT_STREAM_LIMIT_BYTES
    classify_api_error: Any = field(init=False, repr=False)

    def __post_init__(self):
        self.classify_api_error = _classify_acp_error

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
        client_kwargs.setdefault("stream_limit_bytes", self.stream_limit_bytes)
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
    stream_limit_bytes = validate_stream_limit_bytes(
        settings.get("stream_limit_bytes", DEFAULT_STREAM_LIMIT_BYTES)
    )
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
        stream_limit_bytes=stream_limit_bytes,
        process_command_env_vars=("HERMES_OMP_ACP_COMMAND",),
        process_args_env_var="HERMES_OMP_ACP_ARGS",
    ))
