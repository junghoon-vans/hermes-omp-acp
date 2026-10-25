# Troubleshooting

## Executable unavailable

Check `command -v omp` in the environment that launches Hermes. For a gateway service, set `HERMES_OMP_ACP_COMMAND` to the executable's absolute path.

## Provider missing

Confirm the plugin is installed under the active profile's `HERMES_HOME`. For a Python package installation, confirm it is installed in Hermes's Python environment and enabled in `plugins.enabled`.

If loading the plugin reports a missing `acp` module, install the provider with dependencies in that same Python environment: `python -m pip install hermes-omp-acp`. The standalone transport requires `agent-client-protocol>=0.9.0,<0.10.0`; installing with `--no-deps` assumes that dependency is already present.

## Model discovery or inference fails

Confirm OMP authentication works and the selected model is available to that account.

See [Configuration](../../README.md#configuration) for the supported environment variables.
