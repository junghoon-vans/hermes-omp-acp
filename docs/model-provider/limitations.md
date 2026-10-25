# Known limitations

## Native ACP approval requests

`OmpACPClient` owns its ACP callbacks and uses the official Python SDK for transport. It preserves the previous client's permission-cancellation behavior for both synchronous and asynchronous completion calls.

When OMP sends a `session/request_permission` request, the plugin returns:

```json
{"outcome": {"outcome": "cancelled"}}
```

This native ACP request is not connected to a Slack approval card. The plugin does not wait for a person's decision or select an Allow once / Always allow option for OMP. An operation waiting for that permission receives cancellation instead.

This is an explicit policy of the standalone client, not an inherited Copilot handler. The plugin cannot obtain interactive approval through this callback, so it cancels the request. See the [session callbacks](../../hermes_omp_acp/_session.py).

Hermes-owned tool calls are a separate path: when a model response returns a tool call for Hermes to execute, Hermes applies its own tool and approval mechanisms. That does not establish approval support for OMP's native ACP requests.

## What this means for writes

The limitation does not mean every write is blocked. An operation that does not require a native permission request may execute under OMP's configured policy. The plugin does not change that policy.

The plugin's ACP filesystem callbacks also enforce these checks:

- File paths must be absolute and resolve within the session's working directory.
- Reads and writes are subject to Hermes's file safety checks.
- File content returned by reads is passed through Hermes's secret redaction.
- A `fs/write_text_file` request that requires interactive write approval is rejected by the bridge.

These checks describe the ACP filesystem callbacks, not every possible OMP tool execution path. The [session implementation](../../hermes_omp_acp/_session.py) applies Hermes's shared safety and redaction helpers.

## MCP configuration compatibility

The provider reads only the global OMP `mcp.json`. It does not reproduce OMP's project-file, plugin, or third-party configuration discovery. See [MCP servers](../../README.md#mcp-servers) for path selection and missing-file behavior.

The supported transport fields are:

| Transport | Fields forwarded to ACP |
| --- | --- |
| Stdio (`type` omitted or `stdio`) | `command`, `args`, `env` |
| HTTP (`type: "http"`) | `url`, `headers` |
| SSE (`type: "sse"`) | `url`, `headers` |

The entry's map key becomes the ACP server name. Omitted argument lists and environment/header maps become empty lists. `enabled`, `enabledServers`, and `disabledServers` are applied before forwarding; they are not sent to OMP as server options. An entry in `disabledServers` stays disabled even if also listed in `enabledServers`.

**Other server fields are warned about and omitted, not preserved.** Examples include `instructions`, `timeout`, `requestIdFormat`, `cwd`, `auth`, and `oauth`. In particular:

- `instructions: false` does not suppress server instructions in the ACP session.
- A server-specific working directory or timeout does not carry over.
- Explicit auth strategies, credential IDs, and OAuth client options do not carry over. OMP remains responsible for authentication, but forwarding a URL alone does not guarantee equivalence with a native setup that relies on those options.

Do not assume native and ACP sessions have identical behavior when these options matter. A warning names the affected server and omitted fields without printing their values.

Environment/header strings are passed through for OMP to interpret, including `!command` expressions. Treat the global MCP file as trusted executable configuration. The provider does not copy, refresh, or persist MCP credentials itself.

An active stdio command must resolve before the session starts. Bare names use a literal server `env.PATH` when available, then the launching process's PATH with `~/.local/bin` appended as a fallback. Dynamic PATH expressions are not evaluated by the provider; use an absolute command if resolution depends on them. This fallback does not alter PATH for other OMP subprocesses.

HTTP and SSE require the ACP agent to advertise the corresponding capability. An unsupported transport aborts before `session/new` and before the prompt is sent. Passing configuration validation does not prove a server is reachable or authenticated; connection failures are handled by OMP.

## Verification boundary

The standalone client's automated local checks cover permission cancellation, safe filesystem reads and writes with read-back, secret redaction, rejection of out-of-workspace and symlink access, protected writes, and completion conversion. Native OMP integration checks cover file reading through actual OMP tool execution. They do not establish Slack end-to-end behavior.

The earlier implementation was exercised for read-only Slack responses, thread context, actual file reading, cancellation of an active turn, and a subsequent recovery request. Those Slack checks have not been repeated with the standalone transport.

Regular file creation, editing, deletion, and write/read-back checks have not been exercised in the Slack end-to-end checks. They need separate verification before claiming write support for that environment.

Slack approval for native ACP requests is unavailable with the current cancellation policy, not merely an untested feature. Allow once, denial without mutation, and Always allow reuse therefore cannot be claimed as supported by this plugin.
