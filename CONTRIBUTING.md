# Contributing

Run development commands from the repository root. This repository builds one Python distribution from the root package.

## Package boundary and Python environment

The root package builds `hermes-omp-acp`, requires Python 3.11 or later, and declares `agent-client-protocol>=0.9.0,<0.10.0` as its transport dependency. Official Hermes source pins `agent-client-protocol==0.9.0` in its `acp` extra, which satisfies the provider's range. Install the provider in the same Python environment as Hermes; do not override Hermes's SDK pin. The provider reuses Hermes's shared provider, completion bridge, environment, and safety interfaces, not its Copilot ACP client. Use a Python version supported by the Hermes release being checked.

For supported user-facing minimums, see the [requirements](README.md#requirements).

## Provider unit tests

Run provider tests in a disposable Hermes source checkout and Python environment, not a PM- or Nix-managed installation. The Hermes source declares its exact SDK pin in `pyproject.toml`; resolve its dependencies and the editable provider source together. From the repository root, set `HERMES_SOURCE` to that checkout and choose a Python version supported by it:

```sh
REPO_ROOT=$PWD
HERMES_PYTHON_VERSION=3.14
uv venv --python "$HERMES_PYTHON_VERSION" "$HERMES_SOURCE/.venv"
(
  cd "$HERMES_SOURCE"
  uv pip install --python .venv/bin/python \
    -r pyproject.toml --extra acp -e "$REPO_ROOT"
)
export PYTHONPATH="$HERMES_SOURCE"
uv run --project "$HERMES_SOURCE" --no-sync python -P -m unittest discover -s tests -v
```

This local workflow tests the provider source in editable mode and imports Hermes from its source checkout. It jointly resolves the Hermes project dependencies and editable provider requirements, but it is not a full PM-managed source-plugin admission test. CI separately installs the built provider wheel with Hermes source dependencies.

These deterministic tests exercise model discovery and strict selection, synchronous and asynchronous callers, ordered buffered streaming, readiness-gated concurrent sessions, timeout/cancellation/close with child-process reaping, permission cancellation, safe file write/read-back, filesystem restrictions/redaction, reasoning/tool-call conversion, and missing or exited executables. They need no model credentials and use the public provider/client interface rather than private module imports. Provider minimums are Hermes 0.21.4 and OMP 15.7.6, with Python matching Hermes.

## Native OMP integration

With a real OMP installation on `PATH`, run the native integration suite in the same Hermes environment:

```sh
uv run --project "$HERMES_SOURCE" --no-sync python -P -m unittest discover -s tests/integration -v
```

Set `OMP_INTEGRATION_COMMAND` to an absolute executable path to test a specific OMP installation. Each scenario uses temporary Hermes and OMP homes, a local OpenAI-compatible model endpoint, and a test file. It exercises provider discovery and model selection, async responses, native file reading, and Hermes CLI output without external credentials. The model response is based on the value returned by OMP's actual file-read tool.

The suite creates isolated homes. Keep any manual Hermes or OMP checks isolated too: point `HERMES_HOME`, `PI_CODING_AGENT_DIR`, and any other profile/data paths at temporary directories, and do not run tests against or modify a production or personal profile.

CI runs both suites against the provider wheel in three Hermes/OMP environments: Hermes 0.21.4 (`v2026.9.21`) with OMP 15.7.6 on Python 3.11; the dynamically resolved latest Hermes release with latest OMP on Python 3.14 using the `acp` extra; and Hermes 0.22.5 at pinned commit [`bf0203a`](https://github.com/NousResearch/hermes-agent/commit/bf0203a794a07aac3ff764972ab9087baa4e4f4e) with latest OMP on Python 3.14 using the `all` extra. Latest currently resolves to 0.22.5, but exercises `acp` while the fixed commit exercises `all`. Each job jointly resolves the provider wheel and unmodified Hermes source `pyproject.toml`, checks the installed SDK against both the original Hermes `acp` requirement and the provider requirement, and rejects changes to upstream `pyproject.toml` or `uv.lock`. CI runs `uv pip check`; for a nonzero report it accepts only incompatibilities exactly accounted for by the official Hermes lock's declared overrides using Hermes's `pm.pip_check` helper. Unrecognized reports fail, and a failing check without that helper fails raw. There is no separate SDK-version matrix because the official compatibility points pin 0.9.0. A separate installed-wheel discovery/import and native ACP catalog smoke makes no model call. See [verification](docs/verification.md) for test scope and installation limits.

## Build the distribution

Root [`plugin.yaml`](plugin.yaml) is the single source of the package/plugin `version` and `description`. Edit those fields there, not in `pyproject.toml`. The project declares both fields as dynamic; [`hatch_build.py`](hatch_build.py) reads the manifest through Hatchling's custom metadata hook and supplies them to the wheel and sdist metadata.

Hatchling and PyYAML are isolated build dependencies declared in `pyproject.toml`; neither is a provider runtime dependency. The sdist includes the hook and manifest so a wheel built from the sdist uses the same metadata.

Install `uv`, then build the distribution from the repository root:

```sh
uv build --out-dir dist
```

CI builds the root distribution and checks both artifacts with Twine. To run the same metadata check locally after building:

```sh
uvx --from twine twine check --strict dist/*
```

## Publish the provider distribution

Publish `hermes-omp-acp` from tags named `vX.Y.Z`. Tags may use an optional `aN`, `bN`, or `rcN` suffix, and the version after `v` must exactly match `version` in root `plugin.yaml`. The release preparation job checks the package name in `pyproject.toml` and the version in `plugin.yaml` before compatibility checks and publication.

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

Set `version` in root `plugin.yaml`, build and check the distribution as described above, and commit the release preparation. After the release PR is merged, create and push the matching tag at that merged commit. Do not create a GitHub Release yet. For example, for version `0.2.0`:

```sh
git tag v0.2.0
git push origin v0.2.0
```

In GitHub, open **Actions → `publish.yml` → Run workflow**, enter the existing tag in the `tag` input, and start the run. The workflow runs its reusable CI prerequisite and uploads the tested wheel and sdist artifacts to TestPyPI unchanged. `workflow_dispatch` publishes **only** to TestPyPI; it does not create a GitHub Release or upload to production PyPI. This is a real TestPyPI publication, not a no-side-effect simulation.

Validate the package from TestPyPI in a disposable Hermes environment/profile. Set `HERMES_PYTHON` to the interpreter that runs Hermes and `VERSION` to the exact version in the tag:

```sh
HERMES_PYTHON=/absolute/path/to/hermes/python
VERSION=0.2.0
"$HERMES_PYTHON" -m pip install 'agent-client-protocol==0.9.0'
"$HERMES_PYTHON" -m pip install --no-deps \
  --index-url https://test.pypi.org/simple \
  "hermes-omp-acp==$VERSION"
```

Install Hermes's pinned SDK from the normal package index first. For the published TestPyPI wheel check, `--no-deps` ensures TestPyPI is used only for the provider artifact; CI separately verifies dependency resolution from the Hermes source project and provider wheel together. If Hermes's runtime SDK is not 0.9.0, resolve the conflict rather than forcing a different version.

Run Hermes from that same environment, enable the `omp-acp-provider` plugin, and exercise the quick start in [the root README](README.md#quick-start). Check that Hermes discovers the installed entry point and that its documented behavior works; `pip install` alone is not validation.

### Production release

After the TestPyPI install check succeeds, create and publish a GitHub Release for the same tag in the repository's **Releases** page. Publishing the GitHub Release starts the production path; a pushed tag alone does not publish to PyPI, and `workflow_dispatch` remains TestPyPI-only. The release workflow requires the full reusable CI suite and promotes the exact tested wheel and sdist artifacts without rebuilding them. Verify the production release contains the expected version and both artifacts.

### Failure and version handling

- A tag/version mismatch or failed CI gate prevents upload. Inspect the workflow logs, fix the provider, and create a correctly versioned tag for the corrected commit. Do not edit or replace the tested artifacts.
- If a TestPyPI upload has succeeded, use a new version and matching tag for another test upload rather than relying on overwriting it.
- Production PyPI versions are immutable: an uploaded file cannot be replaced or re-uploaded for that version. If any production artifact was uploaded before a failure, treat that version as consumed; bump `version` in root `plugin.yaml` and publish a new matching tag and GitHub Release.

## Runtime changes and pull requests

For runtime changes, exercise the changed path with the applicable real Hermes/OMP installation and temporary profiles and test files. Include the Hermes and OMP versions, scenario, and observed result in the pull request. Paid-provider authentication, live model behavior, and permission-dependent operations need separate checks against the relevant configured provider.

- Keep changes focused on the provider and reuse existing Hermes behavior where possible.
- Add regression coverage for consumer-visible compatibility changes.
- Include the problem, implementation, and verification results in the pull request.
- Keep credentials, personal configuration, and session data out of commits and logs.

The provider follows the [official Hermes model-provider contract](https://github.com/NousResearch/hermes-agent/blob/e06df9c2eae6901f4c88f464e0ae6e753c0428b8/website/docs/developer-guide/model-provider-plugin.md).
