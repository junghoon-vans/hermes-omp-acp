# hermes-omp-acp

Use [Hermes Agent](https://github.com/NousResearch/hermes-agent) with Oh My Pi (OMP) as its model backend through native ACP.

Hermes sends model requests to `omp acp`, and OMP handles inference with its configured models and credentials.

## Requirements

- Hermes with model-provider plugin support and `agent.copilot_acp_client` available.
- `omp` on the Hermes process's `PATH`, with native `omp acp` support and working model authentication.
- Access to this repository. It is currently private; the clone command below uses GitHub SSH authentication.

Verified locally with Hermes **v0.22.0**, OMP **18.12.1**, and Python **3.14**. Compatibility with other versions has not been verified.

## Quick start

### 1. Install the plugin

Clone the plugin into your Hermes profile's model-provider directory:

```sh
plugin_dir="${HERMES_HOME:-$HOME/.hermes}/plugins/model-providers"
mkdir -p "$plugin_dir"
git clone git@github.com:junghoon-vans/hermes-omp-acp.git "$plugin_dir/omp-acp"
```

Use the appropriate `HERMES_HOME` for each Hermes profile that should load the plugin.

### 2. Select a model

In that profile's `config.yaml`, merge the following settings into your existing configuration:

```yaml
model:
  provider: omp-acp
  default: openai-codex/gpt-6.1-sol
  base_url: acp://omp
  api_mode: chat_completions

plugins:
  enabled:
    - omp-acp
```

Replace the example model with a `provider/model` ID available to your OMP account. Preserve any other entries in `plugins.enabled`. `acp://omp` identifies the subprocess backend; it is not a network endpoint.

### 3. Send a request

```sh
hermes chat --provider omp-acp --model openai-codex/gpt-6.1-sol \
  --oneshot -q 'Reply with exactly OMP_OK. Do not use tools.'
```

For a running Hermes gateway, restart it after updating the configuration.

### Python package alternative

From a checkout, install into the **same Python environment that runs Hermes**:

```sh
python -m pip install .
```

The package exposes the `hermes_agent.plugins` entry point `omp-acp`. Enable it through the `plugins.enabled` configuration shown above. Choose either the Python package or filesystem installation.

Install Hermes and OMP separately. Install this plugin from a checkout until a PyPI release is available.

## Configuration

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `HERMES_OMP_ACP_COMMAND` | `omp` | Override the executable, for example with an absolute path. |
| `HERMES_OMP_ACP_ARGS` | `acp` | Replace the complete argument list; parsed with shell-style quoting. Include `acp` when overriding. |

These variables must be available to the Hermes process, including a gateway launched as a service.

## How it works

- Registers the `omp-acp` provider through Hermes's external-process model-provider interface.
- Launches `omp acp` and discovers its available models through ACP.
- Reuses Hermes's `CopilotACPClient` for transport, message conversion, and tool-call bridging.
- Adapts the synchronous client for Hermes's async callers by running requests in a worker thread.

### Runtime behavior

The verified Hermes v0.22.0 client opens a new OMP process and ACP session for each completion, sending the conversation history with the request. Responses are buffered before being returned as completion objects or stream chunks.

Native ACP permission requests receive a `cancelled` response from the inherited Hermes client. The local integration checks cover model responses and file reading.

The plugin depends on Hermes's internal `CopilotACPClient` API. CI pins the tested Hermes revision; compatibility with newer versions should be checked before upgrading.

## Troubleshooting

- **Executable unavailable:** check `command -v omp` in the environment that launches Hermes. For a gateway service, set `HERMES_OMP_ACP_COMMAND` to the executable's absolute path.
- **Provider missing:** confirm the plugin is installed under the active profile's `HERMES_HOME`. For a Python package installation, confirm it is installed in Hermes's Python environment and enabled in `plugins.enabled`.
- **Model discovery or inference fails:** confirm OMP authentication works and the selected model is available to that account.

## Verification

[CI](https://github.com/junghoon-vans/hermes-omp-acp/actions) checks the installed wheel against a pinned official Hermes runtime using credential-free ACP regression tests.

Local integration checks with real OMP exercised model discovery, an async model response, and a Hermes CLI request that read a temporary file, using an isolated `HERMES_HOME`.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, tests, builds, and pull request guidance.

## License

[MIT](LICENSE). Hermes and OMP remain subject to their own licenses.
