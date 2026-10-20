# Contributing

## Development setup

Use a Python environment containing Hermes and its `acp` extra. The test peer uses `agent-client-protocol`, provided by that extra. See the [compatibility workflow](.github/workflows/ci.yml) for the pinned Hermes revision and dependency setup.

From a checkout, install the plugin into that environment:

```sh
python -m pip install -e .
```

## Run the tests

```sh
python -m unittest discover -s tests -v
```

The tests launch a deterministic ACP peer subprocess and require no model credentials. They exercise async event-loop responsiveness, async streaming completion boundaries, and an unavailable executable during model discovery.

## Build distributions

Install `uv`, then run:

```sh
uv build
```

This builds the wheel and source distribution under `dist/`. CI installs the wheel into a runtime prepared from the pinned official Hermes source and runs the regression tests against the installed package.

## Verify runtime changes

For changes affecting the provider's runtime behavior, also exercise the changed path with real OMP. Use a temporary `HERMES_HOME` and test files so verification stays separate from an existing Hermes profile. Record the Hermes and OMP versions, scenario, and observed result in the pull request.

The credential-free test suite covers the Python client boundary. Live model responses, model authentication, and permission-dependent operations need their own integration checks.

## Submit a change

- Keep changes focused on the provider integration and reuse existing Hermes behavior where possible.
- Add a regression test for a consumer-visible compatibility bug.
- Update usage documentation when configuration or behavior changes.
- Include the problem, implementation, and verification results in the pull request.
- Keep credentials, personal configuration, and session data out of commits and logs.

The integration follows the [official Hermes model-provider contract](https://github.com/NousResearch/hermes-agent/blob/e06df9c2eae6901f4c88f464e0ae6e753c0428b8/website/docs/developer-guide/model-provider-plugin.md).
