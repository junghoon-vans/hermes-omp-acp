# Contributing

Run development commands from the repository root. This repository builds one Python distribution from the root package.

## Package boundary and Python environment

The root package builds `hermes-omp-acp`, requires Python 3.11 or later, and declares `agent-client-protocol>=0.9.0,<0.10.0` as its transport dependency. It runs inside a Hermes environment and reuses Hermes's shared provider, completion bridge, environment, and safety interfaces, not its Copilot ACP client. Use a Python version supported by the Hermes release being checked.

For supported user-facing minimums, see the [requirements](README.md#requirements).

## Provider unit tests

Run provider tests with the Python environment for a supported Hermes checkout. The commands below install Hermes's `acp` extra, which supplies the SDK used by both the provider and the real ACP test peer; the subsequent `--no-deps` installation therefore has its transport dependency available. From the repository root, set `HERMES_SOURCE` to a Hermes source checkout and use its `uv` environment:

```sh
uv sync --project "$HERMES_SOURCE" --extra acp --locked --no-install-project
uv pip install --python "$HERMES_SOURCE/.venv/bin/python" --no-deps -e .
export PYTHONPATH="$HERMES_SOURCE"
uv run --project "$HERMES_SOURCE" --no-sync python -P -m unittest discover -s tests -v
```

These deterministic tests exercise model discovery and strict selection, synchronous and asynchronous callers, ordered buffered streaming, readiness-gated concurrent sessions, timeout/cancellation/close with child-process reaping, permission cancellation, safe file write/read-back, filesystem restrictions/redaction, reasoning/tool-call conversion, and missing or exited executables. They need no model credentials and use the public provider/client interface rather than private module imports. Provider minimums remain Hermes 0.21.4 and OMP 15.0.0, with Python matching Hermes.

## Native OMP integration

With a real OMP installation on `PATH`, run the native integration suite in the same Hermes environment:

```sh
uv run --project "$HERMES_SOURCE" --no-sync python -P -m unittest discover -s tests/integration -v
```

Set `OMP_INTEGRATION_COMMAND` to an absolute executable path to test a specific OMP installation. Each scenario uses temporary Hermes and OMP homes, a local OpenAI-compatible model endpoint, and a test file. It exercises provider discovery and model selection, async responses, native file reading, and Hermes CLI output without external credentials. The model response is based on the value returned by OMP's actual file-read tool.

The suite creates isolated homes. Keep any manual Hermes or OMP checks isolated too: point `HERMES_HOME`, `PI_CODING_AGENT_DIR`, and any other profile/data paths at temporary directories, and do not run tests against or modify a production or personal profile.

CI runs both the provider and native integration suites against the installed wheel in two combinations: the latest stable Hermes and latest OMP on Python 3.14, and Hermes 0.21.4 (`v2026.9.21`) with OMP 15.0.0 on Python 3.11. Recheck compatibility-sensitive changes locally as well. See [verification](docs/model-provider/verification.md) for the exercised scenarios and minimum-version boundaries.

## Build the distribution

Install `uv`, then build the distribution from the repository root:

```sh
uv build --out-dir dist
```

CI builds the root distribution.

## Publish the provider distribution

Publish `hermes-omp-acp` from tags named `vX.Y.Z`. Tags may use an optional `aN`, `bN`, or `rcN` suffix, and the version must exactly match root `pyproject.toml`.

### One-time trusted-publishing setup

An administrator needs access to the GitHub repository settings and accounts on both package indexes:

1. In GitHub, open **Settings → Environments** and create the shared environments named exactly `pypi` and `testpypi`. Publishing uses OIDC, not API-token secrets; `id-token: write` is granted only to the publish job. Configure required maintainer approval for both environments (a solo maintainer must allow self-approval). Under **Deployment branches and tags**, allow tags `v*` for `pypi`, and only branch `main` for `testpypi`. Run the manual TestPyPI workflow from `main`; its `tag` input selects the commit independently.
2. In the account-level **Publishing** settings on each index, add one pending publisher for the provider using this mapping:

   | Index | Project name | GitHub environment |
   | --- | --- | --- |
   | PyPI | `hermes-omp-acp` | `pypi` |
   | TestPyPI | `hermes-omp-acp` | `testpypi` |

   For both entries, set the GitHub owner to `junghoon-vans`, repository to `hermes-omp-acp`, and workflow to `publish.yml` (the file is `.github/workflows/publish.yml`). Set the environment field to the value shown in the table.

Pending publishers do not reserve project names; confirm the project name is available on each index before publishing. See the official [PyPI Trusted Publishers guide](https://docs.pypi.org/trusted-publishers/using-a-publisher/) and [pending publisher setup guide](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/). PyPI and TestPyPI make uploaded wheels, sdists, and metadata publicly downloadable; review them for secrets or source you do not intend to disclose. Publishing a package does not change the repository's visibility.

### TestPyPI dry run and installed-package validation

Set the version in root `pyproject.toml`, commit the release candidate, then create and push its matching tag. Do not create a GitHub Release yet. For example, for version `0.1.0`:

```sh
git tag v0.1.0
git push origin v0.1.0
```

In GitHub, open **Actions → `publish.yml` → Run workflow**, enter the existing tag in the `tag` input, and start the run. The workflow runs its reusable CI prerequisite and uploads the tested wheel and sdist artifacts to TestPyPI unchanged. `workflow_dispatch` publishes **only** to TestPyPI; it does not create a GitHub Release or upload to production PyPI. This is a real TestPyPI publication, not a no-side-effect simulation.

Validate the package from TestPyPI in a disposable Hermes environment/profile. Set `HERMES_PYTHON` to the interpreter that runs Hermes and `VERSION` to the exact version in the tag:

```sh
HERMES_PYTHON=/absolute/path/to/hermes/python
VERSION=0.1.0
"$HERMES_PYTHON" -m pip install 'agent-client-protocol>=0.9.0,<0.10.0'
"$HERMES_PYTHON" -m pip install --no-deps \
  --index-url https://test.pypi.org/simple \
  "hermes-omp-acp==$VERSION"
```

Install the SDK from the normal package index first. The provider installation uses `--no-deps` so TestPyPI is used only for the provider release under validation.

Run Hermes from that same environment, enable the `omp-acp-provider` plugin, and exercise the quick start in [the root README](README.md#quick-start). Check that Hermes discovers the installed entry point and that its documented behavior works; `pip install` alone is not validation.

### Production release

After the TestPyPI install check succeeds, create and publish a GitHub Release for the same tag in the repository's **Releases** page. Publishing the GitHub Release starts the production path; a pushed tag alone does not publish to PyPI, and `workflow_dispatch` remains TestPyPI-only. The release workflow requires the full reusable CI suite and promotes the exact tested wheel and sdist artifacts without rebuilding them. Verify the production release contains the expected version and both artifacts.

### Failure and version handling

- A tag/version mismatch or failed CI gate prevents upload. Inspect the workflow logs, fix the provider, and create a correctly versioned tag for the corrected commit. Do not edit or replace the tested artifacts.
- If a TestPyPI upload has succeeded, use a new version and matching tag for another test upload rather than relying on overwriting it.
- Production PyPI versions are immutable: an uploaded file cannot be replaced or re-uploaded for that version. If any production artifact was uploaded before a failure, treat that version as consumed; bump the version in root `pyproject.toml` and publish a new matching tag and GitHub Release.

## Runtime changes and pull requests

For runtime changes, exercise the changed path with the applicable real Hermes/OMP installation and temporary profiles and test files. Include the Hermes and OMP versions, scenario, and observed result in the pull request. Paid-provider authentication, live model behavior, and permission-dependent operations need separate checks against the relevant configured provider.

- Keep changes focused on the provider and reuse existing Hermes behavior where possible.
- Add regression coverage for consumer-visible compatibility changes.
- Include the problem, implementation, and verification results in the pull request.
- Keep credentials, personal configuration, and session data out of commits and logs.

The provider follows the [official Hermes model-provider contract](https://github.com/NousResearch/hermes-agent/blob/e06df9c2eae6901f4c88f464e0ae6e753c0428b8/website/docs/developer-guide/model-provider-plugin.md).
