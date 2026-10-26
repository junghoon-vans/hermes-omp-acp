# Known limitations

## Native ACP approval requests

`OmpACPClient` owns its ACP callbacks and uses the official Python SDK for transport. It preserves the previous client's permission-cancellation behavior for both synchronous and asynchronous completion calls.

When OMP sends a `session/request_permission` request, the plugin returns:

```json
{"outcome": {"outcome": "cancelled"}}
```

This native ACP request is not connected to a Slack approval card. The plugin does not wait for a person's decision or select an Allow once / Always allow option for OMP. An operation waiting for that permission receives cancellation instead.

This is an explicit policy of the standalone client, not an inherited Copilot handler. The plugin cannot obtain interactive approval through this callback, so it cancels the request. See the [session callbacks](../hermes_omp_acp/_session.py).

Hermes-owned tool calls are a separate path: when a model response returns a tool call for Hermes to execute, Hermes applies its own tool and approval mechanisms. That does not establish approval support for OMP's native ACP requests.

## What this means for writes

The limitation does not mean every write is blocked. An operation that does not require a native permission request may execute under OMP's configured policy. The plugin does not change that policy.

The plugin's ACP filesystem callbacks also enforce these checks:

- File paths must be absolute and resolve within the session's working directory.
- Reads and writes are subject to Hermes's file safety checks.
- File content returned by reads is passed through Hermes's secret redaction.
- A `fs/write_text_file` request that requires interactive write approval is rejected by the bridge.

These checks describe the ACP filesystem callbacks, not every possible OMP tool execution path. The [session implementation](../hermes_omp_acp/_session.py) applies Hermes's shared safety and redaction helpers.

## MCP configuration compatibility

When forwarding is enabled (the default), the provider reads only the global OMP `mcp.json`. It does not reproduce OMP's project-file, plugin, or third-party configuration discovery. Set the plugin's `mcp_enabled: false` or override it with `HERMES_OMP_ACP_MCP_ENABLED=false` to skip file loading and send an empty `mcpServers` list without changing OMP's configuration. This controls this provider's MCP forwarding, not native OMP tools or Hermes-managed tools. See [MCP servers](configuration.md#mcp-servers) for toggle precedence, path selection, and missing-file behavior.

The supported transport fields are:

| Transport | Fields forwarded to ACP |
| --- | --- |
| Stdio (`type` omitted or `stdio`) | `command`, `args`, `env` |
| HTTP (`type: "http"`) | `url`, `headers` |
| SSE (`type: "sse"`) | `url`, `headers` |

The entry's map key becomes the ACP server name. Omitted argument lists and environment/header maps become empty lists. `enabled`, `enabledServers`, and `disabledServers` are applied before forwarding; they are not sent to OMP as server options. An entry in `disabledServers` stays disabled even if also listed in `enabledServers`.

**Other server fields are warned about and omitted, not preserved.** A warning names the affected server and omitted fields without printing their values. This is also an OMP ACP boundary, not just a provider allowlist: OMP 18.12.6 reconstructs internal MCP configuration from the standard transport fields. Sending the omitted values as raw extra keys or through `_meta` does not restore them.

### Feature and version boundaries

The package minimum remains **OMP 15.7.6**. An optional feature can require a newer OMP without changing that baseline:

| Feature | OMP 15.7.6 | Source-reviewed OMP 18.12.6 |
| --- | --- | --- |
| Client-provided MCP tools | Supported | Supported |
| Stdio `env`, HTTP/SSE `headers`, including OMP's `!command` resolution | Forwarded; interpreted by OMP | Forwarded; interpreted by OMP |
| Process-wide `OMP_MCP_TIMEOUT_MS` | Supported | Supported |
| Reuse of stored, URL-keyed MCP OAuth credentials in ACP | Not supported by the ACP manager | Supported conditionally; see below |
| Per-server `cwd`, `timeout`, `auth`, `oauth` through ACP | Not forwarded | Not forwarded |
| Per-server `instructions`, `requestIdFormat` through ACP | These native config switches are not present in this release | Native switches exist, but ACP does not forward them |

The table separates source-reviewed behavior from [runtime verification](verification.md#source-reviewed-mcp-behavior). It does not establish live HTTP/SSE connectivity or OAuth renewal.

### Omitted server options

| Option | Consequence and alternative |
| --- | --- |
| `timeout` | The server-specific value does not carry over. Set `OMP_MCP_TIMEOUT_MS` in the OMP process environment for one timeout shared by its MCP servers. |
| `cwd` | No per-server working-directory override. ACP has a session `cwd`, but it is not a translation of this option; the stdio transport can fall back to OMP's process project directory. |
| `requestIdFormat` | No ACP selector. OMP 18.12.6 uses its default numeric MCP request IDs; OMP 15.7.6 uses string IDs without this selector. |
| `auth` | Explicit auth strategy, credential ID, and config-supplied refresh metadata do not carry over. Standard `env`/`headers` and conditional stored OAuth reuse are separate paths. |
| `oauth` | Client ID/secret, scope, callback, and authorization prompt settings do not carry over. Stored OAuth reuse does not create a new authorization flow with these settings. |
| `instructions` | The switch is not forwarded. In OMP 18.12.6 ACP, server instructions are not connected to the model prompt, regardless of the native default. See below. |
| `envPolicy`, `envLiteralKeys`, `headerPolicy` | OMP's internal/plugin literal-value and origin-locking controls are not forwarded. Do not assume those policies are preserved. |

Other unrecognized server fields are also omitted. Do not assume native and ACP sessions have identical behavior when an omitted option matters.

### MCP server instructions

In the source-reviewed OMP 18.12.6 ACP path, the SDK session is created with `enableMCP: false`. ACP creates a separate MCP manager and refreshes the session's tools from it, but does not connect that manager's server instructions to the system-prompt instructions provider.

As a result, omitting `instructions: false` does **not** turn server instructions on in this path. A native setup using `false` still has no automatic server-instruction injection through ACP; a native setup using `true` or the default does not gain that injection either. Tool descriptions, input schemas, tool results, and explicit/custom prompt text are separate from this instructions block.

This is a source-level finding for OMP 18.12.6, also checked against upstream `main` during the compatibility review, not a provider guarantee that future OMP versions will keep this wiring.

### Stored MCP OAuth reuse

**OMP 18.12.6 is the first tagged release that connects the ACP MCP manager to the session's auth storage.** OMP 18.12.0 through 18.12.5 do not have that connection. The minimum supported OMP 15.7.6 therefore does not guarantee this optional feature.

Reuse requires all of the following:

- The OMP process launched by Hermes uses the intended OMP profile and can access its auth storage. A Hermes profile is not itself an OMP profile.
- That OMP profile already has an OAuth credential bound to the server URL. Use OMP's normal authorization workflow to create or renew it; the provider does not log in on your behalf.
- The forwarded HTTP/SSE definition has no explicit `Authorization` header, matched case-insensitively. Such a header disables URL-keyed credential fallback.
- Credential refresh material is available when renewal is needed. Older credentials that require a dropped `auth` block for refresh metadata are not equivalent to self-contained stored credentials.

OMP handles credential lookup, Bearer header injection, and supported renewal/retry behavior. Forwarding a URL alone does not prove the credential exists, is accessible, or can be refreshed. Static authentication through standard `env`/`headers` does not require this managed OAuth reuse path.

Environment/header strings are passed through for OMP to interpret, including `!command` expressions. Treat the global MCP file as trusted executable configuration. The provider does not copy, refresh, or persist MCP credentials itself.

### Global timeout alternative

Set the variable in the environment that launches Hermes, or in the gateway service environment:

```sh
export OMP_MCP_TIMEOUT_MS=120000
```

OMP applies this to MCP requests across its servers, ahead of per-server values; `0` disables OMP's MCP timeout and the default is 30,000 ms. Putting the variable in a server's `env` config only changes that MCP child process's environment, not the OMP client's timeout.

The provider's overall completion deadline still applies. Disabling or increasing OMP's MCP timeout does not extend that deadline. OMP 18.12.6 also has `OMP_MCP_STARTUP_TIMEOUT_MS`, which controls the initial discovery window rather than the request timeout; it is not available in the minimum 15.7.6 release. Restart a running gateway after changing its environment.

### Launch and transport checks

An active stdio command must resolve before the session starts. Bare names use a literal server `env.PATH` when available, then the launching process's PATH with `~/.local/bin` appended as a fallback. Dynamic PATH expressions are not evaluated by the provider; use an absolute command if resolution depends on them. This fallback does not alter PATH for other OMP subprocesses.

HTTP and SSE require the ACP agent to advertise the corresponding capability. An unsupported transport aborts before `session/new` and before the prompt is sent. Passing configuration validation does not prove a server is reachable or authenticated; connection failures are handled by OMP.

## Verification boundary

The standalone client's automated local checks cover permission cancellation, safe filesystem reads and writes with read-back, secret redaction, rejection of out-of-workspace and symlink access, protected writes, and completion conversion. Native OMP integration checks cover file reading through actual OMP tool execution. They do not establish Slack end-to-end behavior.

The earlier implementation was exercised for read-only Slack responses, thread context, actual file reading, cancellation of an active turn, and a subsequent recovery request. Those Slack checks have not been repeated with the standalone transport.

Regular file creation, editing, deletion, and write/read-back checks have not been exercised in the Slack end-to-end checks. They need separate verification before claiming write support for that environment.

Slack approval for native ACP requests is unavailable with the current cancellation policy, not merely an untested feature. Allow once, denial without mutation, and Always allow reuse therefore cannot be claimed as supported by this plugin.
