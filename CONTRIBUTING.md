# Contributing

## Development setup

Use the minimum versions in [README.md](README.md#requirements) and a Python version supported by your Hermes release. Install Hermes's `acp` extra for the test peer's `agent-client-protocol` dependency. See the [compatibility workflow](.github/workflows/ci.yml) for exact upstream revisions and dependency setup.

From a checkout, install the plugin into that environment:

```sh
python -m pip install -e .
```

## Run the tests

```sh
python -m unittest discover -s tests -v
```

The tests launch a deterministic ACP peer subprocess and require no model credentials. They exercise async event-loop responsiveness, async streaming completion boundaries, and an unavailable executable during model discovery.

### Native OMP integration

With `omp` on `PATH`, run:

```sh
python -m unittest discover -s tests/integration -v
```

Set `OMP_INTEGRATION_COMMAND` to an absolute executable path to test a specific OMP installation.

These tests run real Hermes and OMP against a local OpenAI-compatible model endpoint. The endpoint requests a native file read and returns the value delivered by that tool result. The tests check model-picker visibility, model selection, async stream boundaries, and CLI output. Each scenario uses temporary Hermes and OMP homes and needs no external credentials.

CI uses the latest stable Hermes and OMP releases with Python 3.14. See [Verification](docs/verification.md) for the CI scope and minimum-version boundaries. Recheck the documented minimum combination locally when changing compatibility-sensitive code.

## Build distributions

Install `uv`, then run:

```sh
uv build
```

CI installs the built wheel into each upstream Hermes runtime and runs both test suites against the installed package.

## Verify runtime changes

For changes affecting the provider's runtime behavior, also exercise the changed path with real OMP. Use a temporary `HERMES_HOME` and test files so verification stays separate from an existing Hermes profile. Record the Hermes and OMP versions, scenario, and observed result in the pull request.

The native integration suite exercises real OMP with a local model fixture. Paid-provider authentication, live model behavior, and permission-dependent operations need separate checks.

## Submit a change

- Keep changes focused on the provider integration and reuse existing Hermes behavior where possible.
- Add a regression test for a consumer-visible compatibility bug.
- Update usage documentation when configuration or behavior changes.
- Include the problem, implementation, and verification results in the pull request.
- Keep credentials, personal configuration, and session data out of commits and logs.

The integration follows the [official Hermes model-provider contract](https://github.com/NousResearch/hermes-agent/blob/e06df9c2eae6901f4c88f464e0ae6e753c0428b8/website/docs/developer-guide/model-provider-plugin.md).
