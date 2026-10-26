# Troubleshooting

## Executable unavailable

Check `command -v omp` in the environment that launches Hermes. For a gateway service, set the plugin's `command` to the executable's absolute path, or override it with `HERMES_OMP_ACP_COMMAND`. Restart Hermes after changing plugin settings.

## Provider missing

Confirm the plugin is installed under the active profile's `HERMES_HOME`. For a Python package installation, confirm it is installed in Hermes's Python environment and enabled in `plugins.enabled`.

If loading the plugin reports a missing or incompatible `acp` module, install the provider with dependencies in that same Python environment: `python -m pip install --upgrade hermes-omp-acp`. The standalone transport requires `agent-client-protocol>=0.10.1,<0.11`; Hermes's locked `acp` extra alone may install an older SDK. Installing with `--no-deps` requires installing the supported SDK separately first.

## Model discovery or inference fails

Confirm OMP authentication works and the selected model is available to that account.

## Failed or timed-out completion

The provider reports structured failure context, including the request phase, RPC method/code when available, process exit code, original cause, and whether prompt dispatch is known. Invalid configuration, unsupported protocol/capabilities, unavailable models, and RPC rejections are terminal rather than invitations to retry or switch providers.

Only a classified transient transport failure known to precede prompt dispatch is retryable. A timeout or lost connection after dispatch means the outcome is unknown: the provider cannot prove that OMP performed no work and does not authorize automatic replay or fallback. This policy also applies when Hermes's delegated watchdog wraps the provider error. Inspect the task's actual effects before manually resubmitting it; cancellation and process exit do not roll back completed tool actions.

Timeouts use Hermes's `timeout` classification; a disconnected agent with an unknown outcome is not a malformed request. Only invalid ACP request/parameter errors use `format_error`. Terminal failures remain non-retryable with fallback disabled, independently of the displayed cause.

The provider supplies actionable guidance through `error_context.display_message`. Showing it requires a Hermes build that honors this field for terminal provider errors. Stock Hermes 0.21.4 and 0.22.5 remain runtime-compatible but use their generic rejection copy until that renderer change is applied. Starting a new session or switching models does not establish that an earlier task performed no work.

## MCP tools missing

A successful model picker does not check MCP connectivity: model discovery deliberately sends no MCP servers. Check the environment of the process that launches Hermes, not just an interactive terminal:

First check the plugin's `mcp_enabled` setting and `HERMES_OMP_ACP_MCP_ENABLED` override. An explicit `false`, `0`, `no`, or `off` environment value disables forwarding; unset or empty uses the plugin setting, which defaults to `true`. Set the environment override to `true` to force forwarding on. Restart a running gateway after changing settings or its environment. The provider does not change the original OMP file.

1. Determine whether `PI_CODING_AGENT_DIR` is set. The provider reads `<directory>/mcp.json`, or `~/.omp/agent/mcp.json` when unset or empty.
2. Confirm that file exists. A missing file results in no MCP servers; a missing custom-path file does not fall back to the default path.
3. Check the server's entry in `mcpServers` and the `enabled`, `enabledServers`, and `disabledServers` settings. The denylist wins.
4. Confirm the definition is in the global file. Project `.mcp.json` / `.omp/mcp.json` files and other applications' configuration are not imported.
5. Run an explicitly requested read-only MCP operation and inspect OMP's connection/authentication errors. A configured server list is not proof of a successful connection.

Do not duplicate the list in Hermes's `mcp_servers` to fix this provider path. The source of forwarded definitions is the OMP file.

## MCP executable cannot be resolved

`OMP MCP server '<name>': command could not be resolved.` means an active stdio command could not be located before OMP was spawned. Check its spelling, existence, and executable permission. Use an absolute `command` path if the service's environment differs from your shell.

The provider checks a literal server `env.PATH`, then the process PATH and `~/.local/bin`. It does not install the executable or modify the service's PATH. Setting `HERMES_OMP_ACP_COMMAND` fixes discovery of **OMP itself**, not an MCP server command.

## MCP configuration rejected

When forwarding is enabled, malformed JSON, a non-object configuration, or an invalid active server definition stops the completion before OMP is spawned. Fix the selected file's structure and field types. Model discovery can still succeed because it does not read this file. If MCP is intentionally unnecessary for this provider, `HERMES_OMP_ACP_MCP_ENABLED=false` skips the file entirely.

An error naming `HERMES_OMP_ACP_MCP_ENABLED` means the override itself is invalid. Use `true`/`false`, `1`/`0`, `yes`/`no`, or `on`/`off` (case-insensitive); unset or empty falls back to the plugin setting. Model discovery skips this check too.

If the error says the agent does not support a configured HTTP or SSE transport, check the OMP version and the capabilities it advertises during ACP initialization. Do not infer transport support from the model list.

## OMP-specific option ignored

A warning such as `ACP cannot represent fields; dropping: instructions.` means the field was omitted, not applied. It does not by itself prove a connection failed or that an instruction was injected. Review [MCP configuration compatibility](limitations.md#mcp-configuration-compatibility) for field-specific consequences; sending the values as raw keys or `_meta` is not a workaround in the source-reviewed OMP 18.12.6 mapper.

## MCP OAuth works in OMP but not through Hermes

1. Check the OMP executable/version used by the Hermes process, not just your shell. Stored URL-keyed OAuth reuse through ACP requires **OMP 18.12.6 or later**; the package minimum of 15.7.6 covers MCP tool forwarding, not this optional feature.
2. Confirm the OMP profile and agent/auth storage visible to that process match the one you authorized. A named Hermes profile does not automatically select the same-named OMP profile.
3. Confirm the forwarded server URL matches the stored credential's URL. Authorize or reauthorize through OMP's normal workflow under that OMP profile, for example with `/mcp reauth <server>` in OMP.
4. Check whether the server definition includes an `Authorization` header, regardless of case. An explicit header disables URL-keyed OAuth fallback; if it uses `!command`, diagnose that command's result and service environment rather than expecting stored OAuth to replace it.
5. Review warnings for dropped `auth`/`oauth` fields. Explicit credential IDs, client/callback settings, and legacy config-supplied refresh metadata are not preserved. An expired credential may need reauthorization even when initial reuse worked.

Do not paste credentials into logs or duplicate them in Hermes configuration. The provider forwards definitions; OMP owns authentication. See [Stored MCP OAuth reuse](limitations.md#stored-mcp-oauth-reuse) for the exact boundaries. A source-reviewed reuse path is not a successful connection or refresh test against your server.

## MCP timeout differs from native OMP

A server's `timeout` field is omitted. To apply one MCP timeout across servers, set `OMP_MCP_TIMEOUT_MS` in the environment that launches Hermes and restart a running gateway. Do not put it in a server's `env` map: that configures the MCP child, not OMP.

The value is in milliseconds; `0` disables OMP's MCP timeout. The provider's overall completion deadline still applies. `OMP_MCP_STARTUP_TIMEOUT_MS` on OMP 18.12.6 is a separate discovery-window setting, not a request timeout. See [Global timeout alternative](limitations.md#global-timeout-alternative).

## MCP instructions differ from native OMP

In the source-reviewed OMP 18.12.6 ACP implementation, MCP server instructions are not wired into the model prompt. Dropping `instructions: false` does not enable them, and setting `instructions: true` does not add them. Tool descriptions and tool results remain separate sources of model context. See [MCP server instructions](limitations.md#mcp-server-instructions); do not diagnose this from the dropped-field warning alone.

See [Configuration](configuration.md) for plugin settings and supported environment variables.
