# Hermes OMP ACP

[![PyPI](https://img.shields.io/pypi/v/hermes-omp-acp)](https://pypi.org/project/hermes-omp-acp/)
[![Python: 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://pypi.org/project/hermes-omp-acp/)
[![Hermes: 0.21.4+](https://img.shields.io/badge/Hermes-0.21.4%2B-blue)](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.9.21)
[![OMP: 15.7.6+](https://img.shields.io/badge/OMP-15.7.6%2B-blue)](https://github.com/can1357/oh-my-pi/releases/tag/v15.7.6)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

**Bring OMP model routing and isolated task workspaces to Hermes Agent.**

Use [Oh My Pi (OMP)](https://github.com/can1357/oh-my-pi) with [Hermes Agent](https://github.com/NousResearch/hermes-agent) through native ACP. The `omp-acp` model provider reuses OMP's models, provider configuration, and credentials. The separate `omp_task` tool runs repository tasks in retained Git worktrees.

Hermes sends provider requests and tool tasks to `omp acp`; OMP handles model inference. Provider requests can select an OMP `provider/model` ID without configuring that model's credentials again in Hermes.

## Requirements

- **Hermes Agent 0.21.4 or later** ([release `v2026.9.21`](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.9.21)).
- **OMP 15.7.6 or later**, with `omp` on the Hermes process's `PATH` and a configured model. This version fixes activation of client-provided MCP tools in ACP sessions.
- **Python matching Hermes:** 3.11–3.13 for Hermes 0.21.4; 3.14 for Hermes 0.22.x.

The independent task tool has the same minimums; it does not raise the Hermes requirement to 0.22.5. The reviewed combinations are Hermes 0.21.4 / OMP 15.7.6 and Hermes 0.22.5 / OMP 18.12.6; see [verification and limits](docs/verification.md#minimum-version-boundaries).

## Quick start

### 1. Install and enable the plugins you need

Install from [PyPI](https://pypi.org/project/hermes-omp-acp/) into the **same Python environment that runs Hermes**, then enable the provider and, optionally, the task tool:

```sh
pip install hermes-omp-acp
hermes plugins enable omp-acp-provider
# Optional: enable the independent omp_task tool
hermes plugins enable omp-task
```

If `pip` points to another environment, use the Hermes environment's Python: `python -m pip install hermes-omp-acp`.

For a named Hermes profile, enable the provider with `hermes -p <profile> plugins enable omp-acp-provider`; enable the task tool independently with `hermes -p <profile> plugins enable omp-task`. Install Hermes and OMP separately; this package does not install either.

`omp-acp-provider` is the model-provider plugin ID and `omp-acp` is the model-provider ID used in model settings and `--provider`. The separate general-plugin ID is `omp-task`, which registers the `omp_task` tool.

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

An explicitly requested model must be selected successfully in the ACP session before the completion runs. An unavailable model, failed selection, or returned model mismatch raises an error instead of running on OMP's default model.

### 3. Send a request

```sh
hermes chat --provider omp-acp --model openai-codex/gpt-6.1-sol \
  --oneshot -q 'Reply with exactly OMP_OK. Do not use tools.'
```

For a running Hermes gateway, restart it after updating the configuration.

### Run an isolated OMP task

Install the Python distribution in Hermes's environment and enable `omp-task` to make `omp_task(repository, goal, task_id?)` available. The existing model provider is unchanged and need not be enabled or selected to use this tool. Configure its required `model_id` and allowed repository roots as described in [OMP task tool settings](docs/configuration.md#omp-task-tool), including the [arguments and result contract](docs/configuration.md#tool-arguments-and-results).

Each tool call starts a fresh OMP ACP session. Reusing `task_id` reuses the task's files and worktree, not its ACP conversation. Once created, the worktree remains after successful, failed, or stopped OMP invocations. Stop reaps only that task's OMP process and waits for its worker before releasing the workspace; an in-flight Git setup may delay stop. The tool is an ordinary Hermes plugin and does not require Advanced Delegation or a `model_map` backend.

The tool returns a JSON string containing the task identity, worktree path, branch, base commit, response, and status. Review and merge retained changes yourself: the plugin does not merge or clean up worktrees automatically, and it adds no status/cancel tools. An allowed repository root constrains workspace selection, not the OMP process's operating-system permissions.

### Alternative: install directly with Hermes

The direct GitHub directory route preserves the root model-provider manifest and supports either plugin independently:

```sh
# Existing model provider
hermes plugins install junghoon-vans/hermes-omp-acp
hermes plugins enable omp-acp-provider

# Optional standalone task tool
hermes plugins install junghoon-vans/hermes-omp-acp/plugins/omp-task
hermes plugins enable omp-task
```

The task directory is a wrapper for the same `hermes-omp-acp` Python distribution, declared as its install dependency; it is not a second package. For a named profile, prefix every command with `hermes -p <profile>`. The root manifest remains `kind: model-provider`.

These package-index and GitHub commands require a published distribution/default branch containing this implementation. To evaluate an unmerged PR, build and install its wheel from the checked-out source as described in [Installed-wheel plugin discovery](CONTRIBUTING.md#installed-wheel-plugin-discovery); the directory wrapper's dependency does not install an unmerged PR's Python code.

Installing the Python distribution with `pip` instead discovers both entry points from that one package; use the PyPI instructions above when you prefer that route.

For local source installation and development, see [CONTRIBUTING.md](CONTRIBUTING.md).

## Configuration

See [Configuration](docs/configuration.md) for provider settings, environment overrides, MCP forwarding, and `omp_task` settings. Launch options default to `omp acp`; MCP forwarding is enabled by default.

## Documentation

- [How it works](docs/implementation.md)
- [Known limitations](docs/limitations.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Verification](docs/verification.md)

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, tests, builds, and publishing guidance.

The Python package is `hermes_omp_acp`, its distribution is `hermes-omp-acp`, the provider plugin ID is `omp-acp-provider`, the model-provider ID is `omp-acp`, and the separate tool plugin ID is `omp-task`.

## License

[MIT](LICENSE). Hermes and OMP remain subject to their own licenses.
