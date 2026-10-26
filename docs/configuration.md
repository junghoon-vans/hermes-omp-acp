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

Install `hermes-omp-acp` in Hermes's Python environment (from PyPI or the standalone `plugins/omp-task` directory wrapper), then enable the general plugin with `hermes plugins enable omp-task`. It is an optional tool in the same distribution as `omp-acp-provider`; it does not change provider routing and has no Advanced Delegation dependency. The settings live under `plugins.entries.omp-task.settings`:

For this independent-tool architecture, enable `omp-task` with the general plugin commands; do not select a delegation backend. Earlier PR revisions described `delegation.backend: omp-worktree`, `delegation.backends.omp-worktree`, and `model_map` composition. Those are not configuration interfaces in the current implementation. Existing Advanced Delegation configuration for unrelated tasks is independent of this plugin.

```yaml
plugins:
  enabled:
    - omp-task
  entries:
    omp-task:
      settings:
        model_id: "<model ID advertised by OMP>"
        allowed_roots:
          - /path/to/allowed/repositories
        command: omp
        args: [acp]
        mcp_enabled: true
        timeout_seconds: 900
```

Replace the model placeholder with a model ID offered by OMP's ACP session. `allowed_roots` must be a non-empty list of existing absolute directories. The `repository` argument must be the absolute path to an existing Git repository root within one of those directories; passing a subdirectory of a repository is rejected. The tool reads its settings from this entry when it handles each call.

| Setting | Default | Validation and effect |
| --- | --- | --- |
| `model_id` | required | Non-empty string; selected for each task and must be offered by the OMP ACP session. No model fallback is used. |
| `allowed_roots` | required | Non-empty list of absolute paths to existing directories. Bounds the Git repository roots the caller may select. |
| `command` | `omp` | Non-empty executable string, either a command on Hermes's `PATH` or an absolute path. |
| `args` | `[acp]` | List of strings passed as the complete launch arguments. Use `[]` only when the configured executable needs no arguments. |
| `mcp_enabled` | `true` | Boolean controlling MCP forwarding to this task's ACP session. |
| `timeout_seconds` | `900.0` | Positive finite number (booleans are rejected). It limits ACP session startup/initialization, model selection, and prompting after workspace acquisition. It is not a hard total wall-clock deadline: repository validation and Git setup are outside it, and child/worker shutdown and lock drain can extend total time. |

The `HERMES_OMP_ACP_MCP_ENABLED` environment override also applies to `omp_task`: `0`/`false`/`no`/`off` disable, `1`/`true`/`yes`/`on` enable (case-insensitive, surrounding whitespace ignored), and any other non-empty value makes the task return an error. `HERMES_OMP_ACP_COMMAND` and `HERMES_OMP_ACP_ARGS` are provider launch overrides; `omp_task` uses its configured `command` and `args` directly. `PI_CODING_AGENT_DIR` still chooses the OMP agent directory used for MCP configuration, not task workspace storage. These variables must be available to Hermes, including a gateway service.

### Tool arguments and results

`omp_task` accepts exactly these arguments (`additionalProperties` is false):

| Argument | Required | Contract |
| --- | --- | --- |
| `repository` | yes | String containing an absolute path to an existing Git repository root under a configured `allowed_roots` directory. |
| `goal` | yes | Non-empty string of instructions for this invocation. |
| `task_id` | no | String matching `^[0-9a-f]{32}$`. If omitted, the tool generates a 32-character lowercase hexadecimal UUID. |

Example call:

```json
{
  "repository": "/absolute/path/to/allowed/repository",
  "goal": "Inspect the project and implement the requested change.",
  "task_id": "0123456789abcdef0123456789abcdef"
}
```

The tool returns a JSON string containing an object with these fields:

| Field | Meaning |
| --- | --- |
| `task_id` | Generated only after argument-object, extra-key, and non-empty-goal checks pass. A supplied string is echoed even if malformed; outside the schema, a non-string ID or an earlier argument/goal validation failure leaves it `null`. If omitted after those checks, the tool generates a 32-character lowercase hexadecimal UUID. |
| `path` | Retained worktree path, or `null` until workspace acquisition succeeds. |
| `branch` | Retained branch name, or `null` until workspace acquisition succeeds. |
| `base_commit` | Source repository commit recorded when the worktree was first created, or `null` until acquisition succeeds. |
| `response` | OMP's text response on success; an empty string on error or cancellation. |
| `status` | Exactly `success`, `error`, or `cancelled`. |
| `error` | Present only for `error` and `cancelled`, containing the failure/interruption message. |

Success example (angle-bracketed values stand for runtime values):

```json
{
  "task_id": "0123456789abcdef0123456789abcdef",
  "path": "<active Hermes home>/plugin-data/omp-task/workspaces/0123456789abcdef0123456789abcdef/tree",
  "branch": "hermes/omp-0123456789abcdef0123456789abcdef",
  "base_commit": "<source repository commit>",
  "response": "<OMP response text>",
  "status": "success"
}
```

Error example for a repository outside the configured roots:

```json
{
  "task_id": "0123456789abcdef0123456789abcdef",
  "path": null,
  "branch": null,
  "base_commit": null,
  "response": "",
  "status": "error",
  "error": "Repository is outside the configured repository roots."
}
```

Cancellation example after workspace acquisition:

```json
{
  "task_id": "0123456789abcdef0123456789abcdef",
  "path": "<retained worktree path>",
  "branch": "hermes/omp-0123456789abcdef0123456789abcdef",
  "base_commit": "<source repository commit>",
  "response": "",
  "status": "cancelled",
  "error": "OMP task interrupted."
}
```

Each task ID owns one workspace under `<active Hermes home>/plugin-data/omp-task/workspaces/<task_id>/tree`. A fresh workspace is based on the repository's current `HEAD` and uses branch `hermes/omp-<task_id>`. Reusing the ID with the same repository reuses that branch and its files; using it with a different repository is rejected. Every invocation still starts a fresh OMP process and ACP session, so `task_id` is file/worktree reuse, not ACP session or conversation resume. Created worktrees are retained after success, failure, and cancellation; there is no automatic pruning. Different IDs have separate worktrees.

Only one invocation can hold a given task ID's workspace lock at a time. A concurrent call with the same ID fails with `This OMP task already has a workspace in use.` On interruption during Git setup, the tool cannot cancel the already-running Git command: it waits for setup to return, then avoids launching ACP and drains its worker before releasing the lock. During ACP work it closes only that invocation's runtime and joins the worker before returning. Hermes's executor has its own interrupt grace period and may stop awaiting the handler before that drain completes; it cannot force the task worker to release the lock while that worker is still running.

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

