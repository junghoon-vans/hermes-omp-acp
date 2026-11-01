# Hermes OMP ACP

[![PyPI](https://img.shields.io/pypi/v/hermes-omp-acp)](https://pypi.org/project/hermes-omp-acp/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

Use [Oh My Pi (OMP)](https://github.com/can1357/oh-my-pi) as
[Hermes Agent](https://github.com/NousResearch/hermes-agent)'s model backend through
native ACP. Reuse OMP's models, provider configuration, and credentials without
configuring the same model credentials again in Hermes.

Hermes sends requests to `omp acp`; OMP handles model inference.

## Requirements

- **Hermes 0.21.4+** and its compatible Python: 3.11–3.13 for Hermes 0.21.4;
  3.14 for Hermes 0.22.x.
- **OMP 15.7.6+**, authenticated with an available model, and `omp` on the
  Hermes process's `PATH`.
- **ACP SDK compatibility:** `agent-client-protocol>=0.9.0,<0.10.0`.
  Preserve Hermes's official SDK pin; do not bypass dependency conflicts.

Hermes and OMP are installed separately. See [installation options and
compatibility](docs/configuration.md#installation) for PM/Nix-managed environments.

## Quick start

For a **user-managed Python environment only**, use the Python environment that
runs Hermes. Select the intended profile with `HERMES_HOME` before running Hermes
commands; preserve existing configuration.

```sh
python -m pip install hermes-omp-acp
hermes plugins enable omp-acp-provider
```

For **PM-managed Hermes**, use the [Hermes plugin installer](docs/configuration.md#pm-managed-or-source-plugin-installation),
not pip. For **Nix**, use the [flake and Python overlay](docs/nix.md) and rebuild.

Merge this into that profile's `config.yaml`, replacing the example model with an
OMP `provider/model` ID available to your account:

```yaml
model:
  provider: omp-acp
  default: openai-codex/gpt-6.1-sol
  base_url: acp://omp
  api_mode: chat_completions
```

`omp-acp-provider` is the plugin ID; `omp-acp` is the model-provider ID.
`acp://omp` identifies a subprocess backend, not a network endpoint. Restart
Hermes or its gateway after installation or configuration changes, then try:

```sh
hermes chat --provider omp-acp --model openai-codex/gpt-6.1-sol \
  --oneshot -q 'Reply with exactly OMP_OK. Do not use tools.'
```

## Before using tools

- Explicit model selection fails on an unavailable or mismatched model; it does
  not silently use OMP's default.
- Each completion starts a new ACP session; streaming is buffered, not live.
- Global OMP MCP forwarding is enabled by default. Review [configuration and
  version limits](docs/configuration.md#mcp-servers) before connecting servers.
- Native ACP permission requests are cancelled, but this does **not** block all
  OMP writes or tools. Review [approval and filesystem limits](docs/limitations.md).
- A failed or cancelled request may have partial effects. [Inspect before
  retrying](docs/troubleshooting.md#failed-or-timed-out-completion).

## Documentation

**Using the provider**

- [Configuration](docs/configuration.md): installation options, main-model and
  delegation routes, MCP settings, and the optional bundled OMP ACP usage skill.
- [OMP web search skill](docs/skills/omp-web-search.md): optional public-web
  research workflow with separate discovery and route requirements.
- [Troubleshooting](docs/troubleshooting.md) and [known limitations](docs/limitations.md).

**Contributing and validation**

- [Contributing](CONTRIBUTING.md): development, tests, builds, and publishing.
- [How it works](docs/implementation.md): module boundaries and runtime internals.
- [Verification](docs/verification.md): tested combinations and evidence limits.

## License

[MIT](LICENSE). Hermes and OMP remain subject to their own licenses.
