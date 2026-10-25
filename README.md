# hermes-omp-acp

Use [Hermes Agent](https://github.com/NousResearch/hermes-agent) with [Oh My Pi (OMP)](https://github.com/can1357/oh-my-pi) as its model backend through native ACP.

Hermes sends model requests to `omp acp`, and OMP handles inference with its configured models and credentials.

## Requirements

- **Hermes Agent 0.21.4 or later** ([release `v2026.9.21`](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.9.21)).
- **OMP 15.0.0 or later**, with `omp` on the Hermes process's `PATH` and a configured model.
- **Python matching Hermes:** 3.11–3.13 for Hermes 0.21.4; 3.14 for Hermes 0.22.x.

## Quick start

### 1. Install and enable the plugin

Install and enable the plugin with Hermes:

```sh
hermes plugins install junghoon-vans/hermes-omp-acp
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

An explicitly requested model must be selected successfully in the ACP session before the task runs. An unavailable model, failed selection, or returned model mismatch raises an error instead of running on OMP's default model.

### 3. Send a request

```sh
hermes chat --provider omp-acp --model openai-codex/gpt-6.1-sol \
  --oneshot -q 'Reply with exactly OMP_OK. Do not use tools.'
```

For a running Hermes gateway, restart it after updating the configuration.

### Python package installation

From the repository root, install the package into the **same Python environment that runs Hermes**:

```sh
python -m pip install .
```

The distribution is named `hermes-omp-acp` and exposes the `hermes_agent.plugins` entry point `omp-acp-provider`. Use the same activation command from step 1 after installing it. This package route is an alternative to Hermes's source installation command.

When a release is available on PyPI, install it into that same environment with:

```sh
python -m pip install hermes-omp-acp
```

Install Hermes and OMP separately; this package does not install either.

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
