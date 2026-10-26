# Configuration

Use the active Hermes profile's `config.yaml`. Merge these entries into the existing `plugins` section; do not replace other enabled plugins or settings.

## Plugin settings

```yaml
plugins:
  enabled:
    - omp-acp-provider
  entries:
    omp-acp-provider:
      settings:
        command: omp
        args: [acp]
        mcp_enabled: true
        stream_limit_bytes: 16777216
```

| Setting | Default | Purpose |
| --- | --- | --- |
| `command` | `omp` | Executable name on the Hermes process's `PATH`, or an absolute executable path. |
| `args` | `[acp]` | Complete argument list as a YAML list of strings. Include `acp` when launching OMP directly. Use `[]` for a wrapper that needs no arguments. |
| `mcp_enabled` | `true` | Forward active definitions from OMP's global MCP file into completion sessions. Use a YAML boolean, not a quoted string. |
| `stream_limit_bytes` | `16777216` (16 MiB) | Maximum incoming ACP JSONL message size in bytes, excluding the terminating newline. Must be a positive YAML integer; zero, negative values, booleans, strings, and null are rejected. |

Plugin settings are read when the provider is registered. Start a new Hermes process after changing them; restart a running gateway. These settings apply to model discovery, CLI requests, gateway requests, and auxiliary clients. Model discovery always skips MCP configuration and server startup.

The stream limit applies to each incoming ACP message, not the total conversation or model context. ACP's [stdio specification](https://agentclientprotocol.com/protocol/v1/transports) defines newline-delimited JSON-RPC messages without a fixed size ceiling. Without an explicit limit, the Python SDK inherits asyncio's 64 KiB reader default; command catalogs and tool results can exceed it. Increase `stream_limit_bytes` if a legitimate message exceeds the default. Larger limits permit more memory to be buffered per active session; there is no unlimited setting. This option has no environment override.

## Environment overrides

For options with an environment override below, precedence is **non-empty environment variable → plugin setting → built-in default**. Existing environment-variable setups continue to work. Empty or whitespace-only override values fall back to the plugin setting.

| Environment variable | Overrides | Format |
| --- | --- | --- |
| `HERMES_OMP_ACP_COMMAND` | `command` | Executable name or absolute path. |
| `HERMES_OMP_ACP_ARGS` | `args` | Complete argument list parsed with shell-style quoting, for example `acp --no-title`. |
| `HERMES_OMP_ACP_MCP_ENABLED` | `mcp_enabled` | `0`/`false`/`no`/`off` disable; `1`/`true`/`yes`/`on` enable. Case-insensitive; surrounding whitespace is ignored. Other values reject completions. |
| `PI_CODING_AGENT_DIR` | OMP agent directory | Defaults to `~/.omp/agent`. The provider reads `mcp.json` there; OMP also uses this directory for its own agent data. |

Variables must be available to the Hermes process, including a gateway launched as a service. Restart a running gateway after changing its environment. The MCP environment override is checked for each completion, against the plugin setting captured at registration.

## MCP servers

Keep MCP server definitions in OMP's configuration, not in a second Hermes `mcp_servers` list. Forwarding is **enabled by default**: for each completion, the provider reads the OMP file and supplies the active server definitions to `session/new.mcpServers`. OMP connects to the servers and executes their tools.

To disable forwarding for this Hermes profile:

```yaml
plugins:
  entries:
    omp-acp-provider:
      settings:
        mcp_enabled: false
```

Alternatively, override it in the environment that launches Hermes:

```sh
export HERMES_OMP_ACP_MCP_ENABLED=false
```

Set the override to `true` to force forwarding on. Unset it to return to the plugin setting, which defaults to `true` when omitted.

When disabled, the provider does not read or validate `mcp.json`, resolve MCP commands, or forward any server definitions. This leaves OMP's original file, ordinary OMP sessions, native OMP tools, and Hermes-managed tools unchanged.

### MCP file location

| Environment | MCP file read by the provider |
| --- | --- |
| `PI_CODING_AGENT_DIR` unset or empty | `~/.omp/agent/mcp.json` |
| `PI_CODING_AGENT_DIR` set | `$PI_CODING_AGENT_DIR/mcp.json` |

The directory supports `~` expansion; the filename is always `mcp.json`. This selects OMP's agent directory, not just an alternative MCP file.

For example, an existing OMP file can define Aside as:

```json
{
  "mcpServers": {
    "aside": {
      "command": "aside",
      "args": ["mcp"]
    }
  }
}
```

The provider resolves stdio executables to absolute paths, checking a literal server `env.PATH`, then the Hermes process's `PATH` with `~/.local/bin` as a fallback. This does not change the gateway's PATH or install missing commands.

The MCP file is reread for each completion only when forwarding is enabled. A missing file supplies no MCP servers; a missing file in a custom directory does not trigger a retry at the default location. Model discovery does not read the toggle or MCP file or start MCP servers, so a working model picker does not prove MCP connectivity.

Only this global file is read: project files and other tools' MCP configurations are not discovered. Stdio, HTTP, and SSE definitions can be forwarded; HTTP and SSE require support advertised by the OMP ACP agent.

### Compatibility

**MCP tool forwarding requires OMP 15.7.6; reuse of stored MCP OAuth credentials requires OMP 18.12.6 or later.** The package minimum remains 15.7.6. OAuth reuse also requires a matching OMP profile and server URL, accessible stored credentials, and no explicit `Authorization` header. It does not forward custom `auth`/`oauth` objects or perform a new login.

This is not a full-fidelity export of OMP settings: server-specific timeouts, working directories, and other OMP-only fields are omitted with warnings. In the source-reviewed OMP 18.12.6 ACP path, server-provided instructions are not wired into the model prompt; omitting `instructions: false` does **not** imply they are enabled. See [MCP compatibility limits](limitations.md#mcp-configuration-compatibility) for the field/version matrix, OAuth conditions, and global timeout alternative. For missing tools or authentication failures, see [MCP troubleshooting](troubleshooting.md#mcp-tools-missing).

## Official delegation model routes

To choose an OMP model per subagent task, use the official [Advanced Delegation plugin](https://github.com/NousResearch/hermes-official-plugins/tree/main/advanced-delegation), which requires **Hermes 0.22.5 or later**. Install and enable both plugins in the profile that delegates:

```sh
hermes plugins install advanced-delegation
hermes plugins enable advanced-delegation
```

The `omp-acp-provider` installation and activation steps in the [quick start](../README.md#quick-start) are also required. Merge this separate delegation configuration into that profile's `config.yaml`:

```yaml
delegation:
  backend: model_map
  backends:
    model_map:
      options:
        - name: omp-deep
          description: Complex code analysis and implementation.
          provider: omp-acp
          model: openai-codex/gpt-6.1-sol
      default: omp-deep
```

Replace `model` with an OMP model ID available to your account. Start a new Hermes session after changing routes.

**Do not copy `base_url: acp://omp` from the main-model example into a delegation route.** Hermes's delegation resolver treats an explicit `base_url` as a custom endpoint; use `provider: omp-acp` and let the provider resolve its executable and arguments.

This routes Hermes subagents to OMP models. The provider does not manage retained OMP sessions or worktrees; each completion starts a new ACP session. Route-level `reasoning_effort` and `service_tier` are not mapped to OMP settings by this provider.
