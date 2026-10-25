# Troubleshooting

## Executable unavailable

Check `command -v omp` in the environment that launches Hermes. For a gateway service, set `HERMES_OMP_ACP_COMMAND` to the executable's absolute path.

## Provider missing

Confirm the plugin is installed under the active profile's `HERMES_HOME`. For a Python package installation, confirm it is installed in Hermes's Python environment and enabled in `plugins.enabled`.

If loading the plugin reports a missing `acp` module, install the provider with dependencies in that same Python environment: `python -m pip install hermes-omp-acp`. The standalone transport requires `agent-client-protocol>=0.9.0,<0.10.0`; installing with `--no-deps` assumes that dependency is already present.

## Model discovery or inference fails

Confirm OMP authentication works and the selected model is available to that account.

## MCP tools missing

A successful model picker does not check MCP connectivity: model discovery deliberately sends no MCP servers. Check the environment of the process that launches Hermes, not just an interactive terminal:

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

Malformed JSON, a non-object configuration, or an invalid active server definition stops the completion before OMP is spawned. Fix the selected file's structure and field types. Model discovery can still succeed because it does not read this file.

If the error says the agent does not support a configured HTTP or SSE transport, check the OMP version and the capabilities it advertises during ACP initialization. Do not infer transport support from the model list.

## OMP-specific option ignored

A warning such as `ACP cannot represent fields; dropping: instructions.` means the field was omitted, not applied. Review [MCP configuration compatibility](limitations.md#mcp-configuration-compatibility), especially if you rely on `instructions: false`, a server working directory, or explicit auth/OAuth settings. Diagnose OMP connection errors separately from these translation warnings.

See [Configuration](../../README.md#configuration) for the supported environment variables.
