"""Translate OMP's MCP config into ACP session server definitions."""

import json
import os
import shutil
import warnings
from pathlib import Path


_SUPPORTED_SERVER_FIELDS = {
    "stdio": {"type", "enabled", "command", "args", "env"},
    "http": {"type", "enabled", "url", "headers"},
    "sse": {"type", "enabled", "url", "headers"},
}


def _config_path() -> Path:
    agent_dir = os.environ.get("PI_CODING_AGENT_DIR")
    if agent_dir:
        return Path(agent_dir).expanduser() / "mcp.json"
    return Path.home() / ".omp" / "agent" / "mcp.json"


def _read_config() -> dict | None:
    try:
        config = json.loads(_config_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError):
        raise RuntimeError("Unable to read the OMP MCP config.") from None
    except (json.JSONDecodeError, RecursionError):
        raise RuntimeError("The OMP MCP config is malformed JSON.") from None

    if not isinstance(config, dict):
        raise RuntimeError("The OMP MCP config must be a JSON object.")
    return config


def _resolve_command(command: str, env: list[dict[str, str]], name: str) -> str:
    if os.path.isabs(command) or command.startswith("~") or os.path.sep in command:
        try:
            resolved = shutil.which(os.path.abspath(os.path.expanduser(command)))
        except (OSError, ValueError):
            resolved = None
        if resolved:
            return os.path.abspath(resolved)
    else:
        configured_path = next((item["value"] for item in env if item["name"] == "PATH"), None)
        search_paths = []
        if configured_path and "$" not in configured_path:
            search_paths.append(configured_path)

        parent_path = os.environ.get("PATH", "")
        local_bin = os.path.expanduser("~/.local/bin")
        fallback_path = os.pathsep.join(path for path in (parent_path, local_bin) if path)
        if fallback_path:
            search_paths.append(fallback_path)

        for search_path in search_paths:
            try:
                resolved = shutil.which(command, path=search_path)
            except (OSError, ValueError):
                resolved = None
            if resolved:
                return os.path.abspath(resolved)

    raise RuntimeError(f"OMP MCP server {name!r}: command could not be resolved.")


def _server_names(config: dict, field: str) -> set[str]:
    names = config.get(field, [])
    if not isinstance(names, list) or any(
        not isinstance(name, str) or not name for name in names
    ):
        raise RuntimeError(f"OMP MCP {field} must be a list of non-empty server names.")
    return set(names)


def _invalid_server(name: str, field: str, expectation: str) -> RuntimeError:
    return RuntimeError(f"OMP MCP server {name!r}: {field} {expectation}.")


def _string_map(value: object, name: str, field: str) -> list[dict[str, str]]:
    if not isinstance(value, dict):
        raise _invalid_server(name, field, "must be an object of strings")
    if any(
        not isinstance(key, str) or not isinstance(item, str)
        for key, item in value.items()
    ):
        raise _invalid_server(name, field, "must contain only string names and values")
    return [{"name": key, "value": item} for key, item in value.items()]


def _string_list(value: object, name: str, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise _invalid_server(name, field, "must be a list of strings")
    return value


def _warn_dropped_fields(name: str, server: dict, server_type: str) -> None:
    dropped = sorted(set(server) - _SUPPORTED_SERVER_FIELDS[server_type])
    if dropped:
        warnings.warn(
            f"OMP MCP server {name!r}: ACP cannot represent fields; "
            f"dropping: {', '.join(dropped)}.",
            stacklevel=3,
        )


def _translate_server(name: str, server: object, enabled_by_name: bool) -> dict | None:
    if not isinstance(server, dict):
        raise _invalid_server(name, "definition", "must be an object")

    enabled = server.get("enabled", True)
    if not isinstance(enabled, bool):
        raise _invalid_server(name, "enabled", "must be a boolean")
    if not enabled and not enabled_by_name:
        return None

    server_type = server.get("type", "stdio")
    if not isinstance(server_type, str) or server_type not in _SUPPORTED_SERVER_FIELDS:
        raise _invalid_server(name, "type", "must be 'stdio', 'http', or 'sse'")

    _warn_dropped_fields(name, server, server_type)

    if server_type == "stdio":
        command = server.get("command")
        if not isinstance(command, str) or not command.strip():
            raise _invalid_server(name, "command", "must be a non-empty string")
        env = _string_map(server.get("env", {}), name, "env")
        return {
            "name": name,
            "command": _resolve_command(command, env, name),
            "args": _string_list(server.get("args", []), name, "args"),
            "env": env,
        }

    url = server.get("url")
    if not isinstance(url, str) or not url.strip():
        raise _invalid_server(name, "url", "must be a non-empty string")
    return {
        "name": name,
        "type": server_type,
        "url": url,
        "headers": _string_map(server.get("headers", {}), name, "headers"),
    }


def load_mcp_servers() -> list[dict]:
    """Load and translate the global OMP MCP config for ACP."""
    config = _read_config()
    if config is None:
        return []

    servers = config.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise RuntimeError("OMP MCP config mcpServers must be an object.")

    disabled = _server_names(config, "disabledServers")
    enabled = _server_names(config, "enabledServers")

    result = []
    for name, server in servers.items():
        if not name:
            raise RuntimeError("OMP MCP server names must be non-empty.")
        if name in disabled:
            continue
        translated = _translate_server(name, server, name in enabled)
        if translated is not None:
            result.append(translated)
    return result
