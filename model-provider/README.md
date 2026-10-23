# OMP ACP model provider

Use [Hermes Agent](https://github.com/NousResearch/hermes-agent) with Oh My Pi (OMP) as its model backend through native ACP.

Hermes sends model requests to `omp acp`, and OMP handles inference with its configured models and credentials.

## Requirements

- **Hermes Agent 0.21.4 or later** ([release `v2026.9.21`](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.9.21)).
- **OMP 15.0.0 or later**, with `omp` on the Hermes process's `PATH` and a configured model.
- **Python matching Hermes:** 3.11–3.13 for Hermes 0.21.4; 3.14 for Hermes 0.22.x.

## Quick start

### 1. Install and enable the plugin

Install and enable the plugin with Hermes:

```sh
hermes plugins install junghoon-vans/hermes-omp-acp/model-provider
hermes plugins enable omp-acp-provider
```

Run both commands for the Hermes profile that should load the plugin. For a named profile, prefix the commands with `hermes -p <profile>`.

`omp-acp-provider` is the plugin ID used for installation and activation. `omp-acp` is the model provider ID used in model settings and `--provider`; do not replace it with the plugin ID.

### 2. Select a model

In that profile's `config.yaml`, merge the following settings into your existing configuration:

```yaml
model:
  provider: omp-acp
  default: openai-codex/gpt-6.1-sol
  base_url: acp://omp
  api_mode: chat_completions
```

Replace the example model with a `provider/model` ID available to your OMP account. `acp://omp` identifies the subprocess backend; it is not a network endpoint.

### 3. Send a request

```sh
hermes chat --provider omp-acp --model openai-codex/gpt-6.1-sol \
  --oneshot -q 'Reply with exactly OMP_OK. Do not use tools.'
```

For a running Hermes gateway, restart it after updating the configuration.

### Python package alternative

From the repository root, enter the provider directory and install into the **same Python environment that runs Hermes**:

```sh
cd model-provider
python -m pip install .
```

The package exposes the `hermes_agent.plugins` entry point `omp-acp-provider`. Use the same activation command from step 1 after installing it. This package route is an alternative to the Hermes installation command.

Install Hermes and OMP separately. The Python package has not been published to PyPI yet.

## Configuration

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `HERMES_OMP_ACP_COMMAND` | `omp` | Override the executable, for example with an absolute path. |
| `HERMES_OMP_ACP_ARGS` | `acp` | Replace the complete argument list; parsed with shell-style quoting. Include `acp` when overriding. |

These variables must be available to the Hermes process, including a gateway launched as a service.

## Documentation

- [How it works](../docs/model-provider/implementation.md)
- [Known limitations](../docs/model-provider/limitations.md)
- [Troubleshooting](../docs/model-provider/troubleshooting.md)
- [Verification](../docs/model-provider/verification.md)

## Contributing

See [CONTRIBUTING.md](../CONTRIBUTING.md) for development setup, tests, builds, and pull request guidance.

## License

[MIT](https://github.com/junghoon-vans/hermes-omp-acp/blob/main/LICENSE), maintained at the repository root. Hermes and OMP remain subject to their own licenses.
