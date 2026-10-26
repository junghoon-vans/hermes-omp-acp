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
```

| Setting | Default | Purpose |
| --- | --- | --- |
| `command` | `omp` | Executable name on the Hermes process's `PATH`, or an absolute executable path. |
| `args` | `[acp]` | Complete argument list as a YAML list of strings. Include `acp` when launching OMP directly. Use `[]` for a wrapper that needs no arguments. |
| `mcp_enabled` | `true` | Forward active definitions from OMP's global MCP file into completion sessions. Use a YAML boolean, not a quoted string. |

Plugin settings are read when the provider is registered. Start a new Hermes process after changing them; restart a running gateway. These settings apply to model discovery, CLI requests, gateway requests, and auxiliary clients. Model discovery always skips MCP configuration and server startup.

## Environment overrides

For each option, precedence is **non-empty environment variable → plugin setting → built-in default**. Existing environment-variable setups continue to work. Empty or whitespace-only override values fall back to the plugin setting.

| Environment variable | Overrides | Format |
| --- | --- | --- |
| `HERMES_OMP_ACP_COMMAND` | `command` | Executable name or absolute path. |
| `HERMES_OMP_ACP_ARGS` | `args` | Complete argument list parsed with shell-style quoting, for example `acp --no-title`. |
| `HERMES_OMP_ACP_MCP_ENABLED` | `mcp_enabled` | `0`/`false`/`no`/`off` disable; `1`/`true`/`yes`/`on` enable. Case-insensitive; surrounding whitespace is ignored. Other values reject completions. |
| `PI_CODING_AGENT_DIR` | OMP agent directory | Defaults to `~/.omp/agent`. The provider reads `mcp.json` there; OMP also uses this directory for its own agent data. |

Variables must be available to the Hermes process, including a gateway launched as a service. Restart a running gateway after changing its environment. The MCP environment override is checked for each completion, against the plugin setting captured at registration.

## OMP task tool

After installing `hermes-omp-acp` in Hermes's Python environment—through PyPI or the standalone `plugins/omp-task` directory wrapper—enable `omp-task` independently with `hermes plugins enable omp-task`. It is a general Hermes plugin in the same Python distribution as the provider; these settings are read from `plugins.entries.omp-task.settings`:

```yaml
plugins:
  enabled:
    - omp-task
  entries:
    omp-task:
      settings:
        model_id: openai-codex/gpt-6.1-sol
        allowed_roots:
          - /path/to/allowed/repositories
        command: omp
        args: [acp]
        mcp_enabled: true
        timeout_seconds: 900
```

Replace `model_id` with a model ID available to OMP and `allowed_roots` with one or more absolute directory paths containing Git repositories you explicitly permit. The requested `repository` argument must be an existing Git repository root under one of these paths. Use `model_id`: Hermes reserves `model` in its plugin-settings API.

| Setting | Default | Purpose |
| --- | --- | --- |
| `model_id` | required | OMP model ID selected for each task. |
| `allowed_roots` | required | Non-empty list of absolute directory paths that bound eligible repository roots. |
| `command` | `omp` | Executable name on Hermes's `PATH`, or an absolute executable path. |
| `args` | `[acp]` | Complete ACP launch argument list as a YAML list of strings. |
| `mcp_enabled` | `true` | Forward MCP definitions to the task's ACP session. Use a YAML boolean. |
| `timeout_seconds` | `900` | Positive finite timeout for one task invocation, in seconds. |

The shared `HERMES_OMP_ACP_MCP_ENABLED` environment override also takes precedence over this tool's `mcp_enabled` setting. Task `command`, `args`, and `model_id` come from trusted tool configuration, not caller arguments or provider launch overrides.

`task_id` is optional; when omitted, the tool generates a new ID and workspace. Reuse the same 32-character lowercase hexadecimal ID with the same repository to reuse the retained Git worktree and files. Every invocation still creates a fresh OMP process and ACP session, so prior ACP conversation history is not retained.

Once created, a task worktree is retained after successful, failed, or stopped OMP invocations. Stop closes and reaps only that invocation's OMP process, then waits for its worker to release the workspace lock; it does not delete the worktree. Stop does not interrupt a Git command already setting up the worktree: the handler waits for setup to finish, prevents ACP launch, and drains its worker before returning. A later invocation using the same task ID can continue from its files, while other task IDs use independent worktrees.

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

