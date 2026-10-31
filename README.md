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

Select the intended Hermes home with `HERMES_HOME` before running Hermes commands. Install Hermes and OMP separately; this package does not install either.

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

To use another Hermes home, set `HERMES_HOME` to that home before running either command. Choose either this route or the PyPI installation above; you do not need both.

For local source installation and development, see [CONTRIBUTING.md](CONTRIBUTING.md).

## Configuration

See [Configuration](docs/configuration.md) for per-profile plugin settings, environment overrides, MCP forwarding, and delegation model routes. Launch options default to `omp acp`; MCP forwarding is enabled by default.

## Optional search skill

The repository includes a bounded public-web research skill at
[`skills/search/SKILL.md`](skills/search/SKILL.md). The `hermes-omp-acp` wheel
ships the shared skill resource under `hermes_omp_acp/bundled_skills/search`.
After installing the wheel into the same Python environment as Hermes, run:

```sh
hermes-omp-acp-skills
```

The command prints the absolute shared skill directory containing
`search/SKILL.md`; merge that directory into `skills.external_dirs` in the
`config.yaml` of each intended Hermes home, preserving existing entries:

```yaml
skills:
  external_dirs:
    - /absolute/path/printed/by/hermes-omp-acp-skills
```

For a source checkout, configure the absolute path to its `skills` directory
instead. No copying is needed. Select the intended Hermes home with
`HERMES_HOME` before using generic Hermes commands:

```sh
export HERMES_HOME=/absolute/path/to/intended/hermes-home
hermes skills list
hermes chat -s search
```

Installing or enabling `omp-acp-provider` alone does **not** register or
discover the skill. In Hermes 0.22.5, the provider entry point in installed
`providers/__init__.py` calls the no-argument `register()` hook (around lines
525–540 and 596–610); general plugin discovery in
`hermes_cli/plugins_discovery.py` (around lines 286–290) skips
`kind: model-provider`. Skill loading and `skills.external_dirs` are handled on
the separate `agent/skill_utils.py` path (around lines 337–363). Explicit
`skills.external_dirs` is the minimal supported choice without changing the
provider boundary. Automatic provider registration would require an authorized
general-plugin adapter or an upstream provider skill hook; neither is included.

The skill requires an existing delegation model route using provider
`omp-acp`, plus working OMP-native `web_search` and public URL `read` tools.
See [delegation model routes](docs/configuration.md#official-delegation-model-routes)
for the Advanced Delegation setup and the
[search skill guide](docs/skills/search.md) for distribution, prerequisites,
and limitations. This describes the research workflow; it does not claim that
any particular delegated research task has succeeded.

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
