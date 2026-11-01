# Configuration

Use the active Hermes profile's `config.yaml`. Select the intended home with
`HERMES_HOME` before running Hermes commands. Merge examples into existing
configuration; preserve other plugins, routes, and settings.

## Installation

The [quick start](../README.md#quick-start) covers a user-managed Python
environment. Install the provider in the same environment as Hermes; the package
does not install Hermes or OMP. Choose one installation route, not both.

The distribution is `hermes-omp-acp`, the import package is `hermes_omp_acp`,
the provider plugin ID is `omp-acp-provider`, and the model-provider ID is
`omp-acp`. These names are not interchangeable.

### PM-managed or source-plugin installation

Use Hermes's source-plugin installer:

```sh
hermes plugins install junghoon-vans/hermes-omp-acp
hermes plugins enable omp-acp-provider
```

For PM-managed Hermes, let PM own dependency admission; do not modify its
environment with pip. Review the installer's scan findings and dependency
transaction rather than bypassing them. The [PM verification
scope](verification.md#pm-managed-git-installation) covers a pinned official
baseline with an isolated local Git remote, not GitHub transport or unattended
installation without scan consent.

For Nix-managed installations, declare the provider and SDK through Nix and
rebuild; do not run pip inside the managed environment. For editable source
development, use [Contributing](../CONTRIBUTING.md#provider-unit-tests).

Restart Hermes or its gateway after installing/enabling plugins or changing
dependencies. Enabling the source `omp-acp-provider` plugin alone does not
install the optional companion's Python entry point.

### Compatibility

The minimum is Hermes 0.21.4 ([`v2026.9.21`](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.9.21))
with OMP 15.7.6. Use Python 3.11–3.13 for Hermes 0.21.4 and Python 3.14 for
Hermes 0.22.x. OMP must have a configured model and be available to the process
launching Hermes, including a gateway service.

The provider declares `agent-client-protocol>=0.9.0,<0.10.0`; official Hermes's
source `acp` extra pins `agent-client-protocol==0.9.0`. Preserve that pin and
resolve dependencies together. Never force an SDK version, patch upstream pins,
or use resolver bypasses to make an incompatible Hermes release install.
See [Verification](verification.md#continuous-integration) for the tested matrix.

## Main model

Use the [quick-start model configuration](../README.md#quick-start) for the main
Hermes agent. Replace the example with an OMP `provider/model` ID available to
your account. An explicitly requested model must be selected in the same ACP
session before prompting; unavailable models, selection failures, and returned
model mismatches fail rather than falling back to OMP's default.

Each completion opens a new OMP process and ACP session. The provider does not
retain sessions or manage worktrees, and streaming is buffered. Delegation is a
separate route configuration described [below](#official-delegation-model-routes).

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

The stream limit applies to each incoming ACP message, not the total conversation or model context. ACP's [stdio specification](https://agentclientprotocol.com/protocol/v1/transports) defines newline-delimited JSON-RPC messages without a fixed size ceiling. Without an explicit limit, the Python SDK inherits asyncio's 64 KiB reader default; command catalogs and tool results can exceed it. Increase `stream_limit_bytes` if a legitimate message exceeds the default. Larger limits permit more memory to be buffered per active session; there is no unlimited setting.

## Environment overrides

For options with an environment override below, precedence is **non-empty environment variable → plugin setting → built-in default**. Existing environment-variable setups continue to work. Empty or whitespace-only override values fall back to the plugin setting.

| Environment variable | Overrides | Format |
| --- | --- | --- |
| `HERMES_OMP_ACP_COMMAND` | `command` | Executable name or absolute path. |
| `HERMES_OMP_ACP_ARGS` | `args` | Complete argument list parsed with shell-style quoting, for example `acp --no-title`. |
| `HERMES_OMP_ACP_MCP_ENABLED` | `mcp_enabled` | `0`/`false`/`no`/`off` disable; `1`/`true`/`yes`/`on` enable. Case-insensitive; surrounding whitespace is ignored. Other values reject completions. |
| `HERMES_OMP_ACP_STREAM_LIMIT_BYTES` | `stream_limit_bytes` | Positive base-10 integer in bytes, for example `16777216`. Surrounding whitespace is ignored; invalid or non-positive values reject client creation. |
| `PI_CODING_AGENT_DIR` | OMP agent directory | Defaults to `~/.omp/agent`. The provider reads `mcp.json` there; OMP also uses this directory for its own agent data. |

Variables must be available to the Hermes process, including a gateway launched as a service. Restart a running gateway after changing its environment. The MCP environment override is checked for each completion, against the plugin setting captured at registration.

The stream-limit environment override is read when a client is created and applies to all of that client's ACP sessions, including model discovery. An empty or whitespace-only value uses the configured limit, or the built-in 16 MiB default when the setting is omitted. Plugin settings must remain valid even when an environment override is present.

```sh
export HERMES_OMP_ACP_STREAM_LIMIT_BYTES=16777216
```

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

## Optional bundled OMP ACP usage skill

The distribution includes an optional general-plugin companion for the usage
skill `omp-acp-skills:omp-acp`. It is distinct from the external
[`omp-web-search` research skill](skills/omp-web-search.md).

The companion's Python entry point is installed with `hermes-omp-acp`, either
from the wheel in Hermes's Python environment or through Hermes PM's
dependency-install transaction. Installing only the source provider plugin does
not install or discover that entry point.

After installing the distribution and enabling `omp-acp-provider`, enable the
companion separately:

```sh
hermes plugins enable omp-acp-skills
```

In an agent session, load it with
`skill_view(name='omp-acp-skills:omp-acp')`. Enabling the companion registers the
namespaced skill but does not automatically load its body. No copying or
`skills.external_dirs` entry is needed.

The companion does not enable/configure the provider, create a delegation route,
or register `omp-web-search`. For subagent use, the configured
[delegation route](#official-delegation-model-routes) remains required.

If Hermes discovers the companion but its plugin context has no callable
`register_skill`, it warns that the skill cannot be registered; the provider
continues to work. Use a Hermes version with the bundled-skill API. The provider's
minimum Hermes version alone does not guarantee this optional API.

The [packaged skill instructions](../hermes_omp_acp/skills/omp-acp/SKILL.md)
cover route selection, prompt-context minimization, approval boundaries, and
safe handling of failures; they are workflow guidance, not security enforcement.
