# Hermes OMP ACP

[![PyPI](https://img.shields.io/pypi/v/hermes-omp-acp)](https://pypi.org/project/hermes-omp-acp/)
[![Python: 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://pypi.org/project/hermes-omp-acp/)
[![Hermes: 0.21.4+](https://img.shields.io/badge/Hermes-0.21.4%2B-blue)](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.9.21)
[![OMP: 15.7.6+](https://img.shields.io/badge/OMP-15.7.6%2B-blue)](https://github.com/can1357/oh-my-pi/releases/tag/v15.7.6)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

**Bring OMP's model routing to Hermes Agent.**

Use [Oh My Pi (OMP)](https://github.com/can1357/oh-my-pi) as [Hermes Agent](https://github.com/NousResearch/hermes-agent)'s model backend through native ACP, reusing the models, provider configuration, and credentials you already use in OMP.

Hermes sends requests to `omp acp`; OMP handles model inference. Select an OMP `provider/model` ID in Hermes without configuring that model's credentials again in Hermes.

## Requirements

- **Hermes Agent 0.21.4 or later** ([release `v2026.9.21`](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.9.21)).
- **ACP SDK:** The provider requires `agent-client-protocol>=0.9.0,<0.10.0`; the official Hermes source `acp` extra pins `agent-client-protocol==0.9.0`.
- **OMP 15.7.6 or later**, with `omp` on the Hermes process's `PATH` and a configured model. This version fixes activation of client-provided MCP tools in ACP sessions.
- **Python matching Hermes:** 3.11–3.13 for Hermes 0.21.4; 3.14 for Hermes 0.22.x.

## Quick start

### 1. Install and enable the plugin

For a **user-managed Python environment only**, install from [PyPI](https://pypi.org/project/hermes-omp-acp/) into the same Python environment that runs Hermes, then enable the plugin:

```sh
pip install hermes-omp-acp
hermes plugins enable omp-acp-provider
```

If `pip` points to another user-managed environment, use Hermes's Python: `python -m pip install hermes-omp-acp`. This package does not install Hermes or OMP.

Do not use `pip` to modify a Hermes PM-managed or Nix-managed environment. For PM-managed installations, use Hermes's plugin installer and let PM perform the dependency transaction. For Nix-managed installations, declare the provider and SDK through Nix and rebuild the environment. Restart Hermes (or its gateway) after installing/enabling the plugin or changing dependencies.

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

Restart Hermes or its gateway after updating configuration.

### Alternative: install directly with Hermes

The following uses Hermes's source-plugin install route rather than installing the provider wheel from PyPI:

```sh
hermes plugins install junghoon-vans/hermes-omp-acp
hermes plugins enable omp-acp-provider
```

For a named profile, prefix both commands with `hermes -p <profile>`. Choose either this route or the PyPI installation above; you do not need both.

For PM-managed Hermes, use this plugin-installer route and let PM own dependency admission. CI tests the wheel with the provider already enabled in temporary configuration; it does not run `hermes plugins enable` or cover the full PM-managed source-Git installation and dependency transaction. See [verification](docs/verification.md#continuous-integration) for the tested admission limits.

Nix-managed installations must keep dependency ownership declarative; do not run the pip commands above inside a Nix-managed environment.

For local source installation and development, see [CONTRIBUTING.md](CONTRIBUTING.md).

## Configuration

See [Configuration](docs/configuration.md) for per-profile plugin settings, environment overrides, MCP forwarding, and delegation model routes. Launch options default to `omp acp`; MCP forwarding is enabled by default.

## Security and external-process disclosure

**Catalog status: draft, not admitted.** File-sourced MCP secrets do not use Hermes's required secret routes (rule 11). External OMP authentication and undocumented Hermes imports also need an approved public integration path. Passing validators does not make this architecture eligible for the official catalog. See [catalog preparation](docs/catalog-submission.md).

- Each completion launches the separately installed `omp acp` executable in the session working directory and sends it the conversation. Model discovery also starts OMP. OMP, not this plugin, owns model-provider authentication and network requests; OMP uses its own configured account and credential storage.
- The child receives Hermes's subprocess environment with credential inheritance enabled. A Hermes profile is not an OMP profile. Review the child environment, executable, arguments, and OMP configuration before using this backend in a shared gateway or unattended job.
- OMP may execute its own shell, file, web, and MCP tools under its configured policy. Native ACP permission requests are cancelled, not auto-approved or displayed as Hermes approval prompts. Operations that do not request permission may still execute. ACP filesystem callbacks enforce the session directory, Hermes file-safety checks, read redaction, and rejection of approval-required writes; these checks do not sandbox every OMP tool.
- MCP forwarding is enabled by default. The plugin reads `$PI_CODING_AGENT_DIR/mcp.json`, or `~/.omp/agent/mcp.json` when unset, outside its own data directory. It forwards enabled server commands, arguments, environment values, URLs, and headers to OMP; these can contain secrets. OMP may start configured MCP processes or contact remote servers. Values including `!command` expressions are passed through for OMP to interpret. Disable forwarding with `mcp_enabled: false` in plugin settings or `HERMES_OMP_ACP_MCP_ENABLED=false`.
- The plugin does not directly read OMP's login store or refresh or persist OAuth tokens. OMP may use its own stored credentials, including supported MCP OAuth credentials. This external authentication path and file-sourced MCP environment/header forwarding require catalog rule 11 review; a disclosure alone does not establish admission eligibility.
- Completion children are closed and reaped on completion, cancellation, or timeout. This is not a background daemon. The plugin adds no telemetry or self-updater; external OMP and MCP services retain their own policies.

See [Known limitations](docs/limitations.md) for the permission boundary and exact MCP transport behavior.

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
