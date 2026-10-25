# Hermes OMP ACP

[![PyPI](https://img.shields.io/pypi/v/hermes-omp-acp)](https://pypi.org/project/hermes-omp-acp/)
[![Python: 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://pypi.org/project/hermes-omp-acp/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

**Bring OMP's model routing to Hermes Agent.**

Use [Oh My Pi (OMP)](https://github.com/can1357/oh-my-pi) as [Hermes Agent](https://github.com/NousResearch/hermes-agent)'s model backend through native ACP, reusing the models, provider configuration, and credentials you already use in OMP.

Hermes sends requests to `omp acp`; OMP handles model inference. Select an OMP `provider/model` ID in Hermes without configuring that model's credentials again in Hermes.

## Requirements

- **Hermes Agent 0.21.4 or later** ([release `v2026.9.21`](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.9.21)).
- **OMP 15.0.0 or later**, with `omp` on the Hermes process's `PATH` and a configured model.
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

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `HERMES_OMP_ACP_COMMAND` | `omp` | Override the executable, for example with an absolute path. |
| `HERMES_OMP_ACP_ARGS` | `acp` | Replace the complete argument list; parsed with shell-style quoting. Include `acp` when overriding. |

These variables must be available to the Hermes process, including a gateway launched as a service.

## Official delegation model routes

To choose an OMP model per subagent task, use the official [Advanced Delegation plugin](https://github.com/NousResearch/hermes-official-plugins/tree/main/advanced-delegation), which requires **Hermes 0.22.5 or later**. Install and enable both plugins in the profile that delegates:

```sh
hermes plugins install advanced-delegation
hermes plugins enable advanced-delegation
```

The `omp-acp-provider` installation and activation steps above are also required. Merge this separate delegation configuration into that profile's `config.yaml`:

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

## Documentation

- [How it works](docs/model-provider/implementation.md)
- [Known limitations](docs/model-provider/limitations.md)
- [Troubleshooting](docs/model-provider/troubleshooting.md)
- [Verification](docs/model-provider/verification.md)

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, tests, builds, and publishing guidance.

The Python package is `hermes_omp_acp`, its distribution is `hermes-omp-acp`, the Hermes plugin ID is `omp-acp-provider`, and the model-provider ID is `omp-acp`.

## License

[MIT](LICENSE). Hermes and OMP remain subject to their own licenses.
