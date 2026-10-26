# Hermes OMP ACP

[![PyPI](https://img.shields.io/pypi/v/hermes-omp-acp)](https://pypi.org/project/hermes-omp-acp/)
[![Python: 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://pypi.org/project/hermes-omp-acp/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

**Bring OMP's model routing to Hermes Agent.**

Use [Oh My Pi (OMP)](https://github.com/can1357/oh-my-pi) as [Hermes Agent](https://github.com/NousResearch/hermes-agent)'s model backend through native ACP, reusing the models, provider configuration, and credentials you already use in OMP.

Hermes sends requests to `omp acp`; OMP handles model inference. Select an OMP `provider/model` ID in Hermes without configuring that model's credentials again in Hermes.

## Requirements

- **Hermes Agent 0.21.4 or later** ([release `v2026.9.21`](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.9.21)).
- **OMP 15.7.6 or later**, with `omp` on the Hermes process's `PATH` and a configured model. This version fixes activation of client-provided MCP tools in ACP sessions.
- **Python matching Hermes:** 3.11–3.13 for Hermes 0.21.4; 3.14 for Hermes 0.22.x.

## Quick start

### 1. Install and enable the plugin

Install from [PyPI](https://pypi.org/project/hermes-omp-acp/) into the **same Python environment that runs Hermes**, then enable the plugin:

```sh
pip install hermes-omp-acp
hermes plugins enable omp-acp-provider
```

If `pip` points to another environment, use the Hermes environment's Python: `python -m pip install hermes-omp-acp`.

For a named Hermes profile, enable it with `hermes -p <profile> plugins enable omp-acp-provider`. Install Hermes and OMP separately; this package does not install either.

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

An explicitly requested model must be selected successfully in the ACP session before the task runs. An unavailable model, failed selection, or returned model mismatch raises an error instead of running on OMP's default model.

### 3. Send a request

```sh
hermes chat --provider omp-acp --model openai-codex/gpt-6.1-sol \
  --oneshot -q 'Reply with exactly OMP_OK. Do not use tools.'
```

For a running Hermes gateway, restart it after updating the configuration.

### Alternative: install directly with Hermes

To install from GitHub instead of PyPI:

```sh
hermes plugins install junghoon-vans/hermes-omp-acp
hermes plugins enable omp-acp-provider
```

For a named profile, prefix both commands with `hermes -p <profile>`. Choose either this route or the PyPI installation above; you do not need both.

For local source installation and development, see [CONTRIBUTING.md](CONTRIBUTING.md).

## Configuration

See [Configuration](docs/configuration.md) for per-profile plugin settings, environment overrides, MCP forwarding, and delegation model routes. Launch options default to `omp acp`; MCP forwarding is enabled by default.

## Documentation

- [How it works](docs/implementation.md)
- [Known limitations](docs/limitations.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Verification](docs/verification.md)

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, tests, builds, and publishing guidance.

The Python package is `hermes_omp_acp`, its distribution is `hermes-omp-acp`, the Hermes plugin ID is `omp-acp-provider`, and the model-provider ID is `omp-acp`.

## License

[MIT](LICENSE). Hermes and OMP remain subject to their own licenses.
