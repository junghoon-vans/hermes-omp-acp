# Troubleshooting

## Executable unavailable

Check `command -v omp` in the environment that launches Hermes. For a gateway service, set `HERMES_OMP_ACP_COMMAND` to the executable's absolute path.

## Provider missing

Confirm the plugin is installed under the active profile's `HERMES_HOME`. For a Python package installation, confirm it is installed in Hermes's Python environment and enabled in `plugins.enabled`.

## Model discovery or inference fails

Confirm OMP authentication works and the selected model is available to that account.

See [Configuration](../README.md#configuration) for the supported environment variables.
