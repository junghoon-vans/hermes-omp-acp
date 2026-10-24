# Contributing

Run development commands from the repository root unless a command says otherwise. The provider and delegator are independent Python distributions: keep their metadata, versions, dependencies, and test environments separate.

## Package boundaries and Python environments

- `model-provider/` builds `hermes-omp-acp-provider`, requires Python 3.11 or later, and declares no package dependencies. It runs inside a Hermes environment; its tests need Hermes's `acp` extra, which provides the official `agent-client-protocol` test peer dependency. Use a Python version supported by the Hermes release being checked.
- `delegator/` builds `hermes-omp-acp-delegator`, supports Python 3.11–3.14, and pins `agent-client-protocol==0.9.0`.

Do not merge these requirements into one package or assume their version numbers move together. For the supported user-facing minimums, see the [provider requirements](model-provider/README.md#requirements) and the [delegator compatibility table](delegator/README.md#hermes-compatibility).

For an isolated local delegator environment, use a temporary location instead of the Hermes/provider environment:

```sh
DELEGATOR_ENV="$(mktemp -d)/venv"
uv venv "$DELEGATOR_ENV"
uv pip install --python "$DELEGATOR_ENV/bin/python" -r delegator/pyproject.toml
"$DELEGATOR_ENV/bin/python" -m unittest discover -s delegator/tests -v
```

The test suite uses its ACP fixture and temporary Git repositories; it does not require model credentials or messaging accounts. The 16 behavior tests cover configuration validation, task/worktree behavior, session isolation, errors, permission denial, live status, timeout, and cancellation.

## Provider unit tests

Run provider tests with the Python environment for a supported Hermes checkout, with Hermes's `acp` extra installed and this provider installed into that environment. For example, from the repository root, set `HERMES_SOURCE` to a Hermes source checkout and use its `uv` environment:

```sh
uv sync --project "$HERMES_SOURCE" --extra acp --locked --no-install-project
uv pip install --python "$HERMES_SOURCE/.venv/bin/python" --no-deps -e model-provider
export PYTHONPATH="$HERMES_SOURCE"
uv run --project "$HERMES_SOURCE" --no-sync python -P -m unittest discover -s model-provider/tests -v
```

These deterministic tests exercise provider registration, asynchronous event-loop responsiveness, streaming completion boundaries, and missing-executable handling. They need no model credentials. Use a Hermes release and Python combination supported by that release; provider minimums are Hermes 0.21.4 and OMP 15.0.0, with Python matching Hermes.

## Native OMP integration

With a real OMP installation on `PATH`, run the native integration suite in the same Hermes environment:

```sh
uv run --project "$HERMES_SOURCE" --no-sync python -P -m unittest discover -s model-provider/tests/integration -v
```

Set `OMP_INTEGRATION_COMMAND` to an absolute executable path to test a specific OMP installation. Each scenario uses temporary Hermes and OMP homes, a local OpenAI-compatible model endpoint, and a test file. It exercises provider discovery and model selection, async responses, native file reading, and Hermes CLI output without external credentials. The model response is based on the value returned by OMP's actual file-read tool.

The suite creates isolated homes itself. Keep any manual Hermes or OMP checks isolated too: point `HERMES_HOME`, `PI_CODING_AGENT_DIR`, and any other profile/data paths at temporary directories, and do not run tests against or modify a production or personal profile. The delegator Hermes smoke test described below also creates its own temporary Hermes home and repository.

CI checks the provider wheel with the latest stable Hermes and latest OMP, using Python 3.14, and runs both the unit and native integration suites. The provider compatibility-sensitive minimum combination should also be rechecked locally when relevant code changes. See [model-provider verification](docs/model-provider/verification.md) for the CI scope and minimum-version boundaries.

## Delegator compatibility smoke

Install the intended Hermes release and the delegator dependencies in a disposable environment, then run the official-Hermes fixture smoke from the repository root:

```sh
python delegator/tests/smoke_hermes.py
```

To exercise the wheel instead of the source directory, install the built package into that disposable Hermes environment and run:

```sh
python -m pip install --no-deps dist/delegator/*.whl
python delegator/tests/smoke_hermes.py --installed
```

The script exercises the plugin through Hermes's native plugin manager and tool registry with an ACP fixture; it makes no model calls and does not require external credentials. Its temporary profile and repository are removed when the script exits.

On Hermes 0.22.3 or later, validate the delegator source and check its supported hooks from the repository root:

```sh
hermes plugins validate delegator --install-deps
hermes plugins doctor delegator --ci
```

Hermes 0.21.0 does not provide `plugins validate`, and its Plugin Doctor cannot accept the optional `post_compaction` hook. CI therefore omits diagnostics for the minimum release; this does not prevent basic delegation.

CI covers three Hermes compatibility points for the delegator:

| Coverage point | Hermes release | Python | Diagnostics |
| --- | --- | --- | --- |
| Minimum tested | `v2026.8.31` (0.21.0) | 3.13 | Plugin validation/Doctor unavailable on this host |
| Recommended | `v0.22.3` | 3.14 | Validation and Plugin Doctor |
| Latest stable | Dynamically resolved latest stable release | 3.14 | Validation and Plugin Doctor |

At each point CI runs the official-Hermes fixture smoke for both the source plugin and the installed wheel. It also negotiates ACP with official OMP 18.12.1 without model inference. The minimum test uses the official calendar-versioned Hermes tag and does not run diagnostics that host does not provide. For provider and delegator support details, refer to their module READMEs; CI is compatibility coverage, not a claim that every optional feature exists on every Hermes release.

## Build distributions and check licenses

Install `uv`, then build into separate output directories so the two distributions cannot be confused:

```sh
uv build model-provider --out-dir dist/model-provider
uv build delegator --out-dir dist/delegator
```

Each module has its own distribution version and `pyproject.toml`. Keep each module's `LICENSE` identical to the root license:

```sh
cmp LICENSE model-provider/LICENSE
cmp LICENSE delegator/LICENSE
```

CI builds both distributions and checks both license copies.

## Publish the independent distributions

Publish each distribution separately. The provider is `hermes-omp-acp-provider` (currently `0.1.0`), selected by `model-provider/v0.1.0`; the delegator is `hermes-omp-acp-delegator` (currently `0.1.0`), selected by `delegator/v0.1.0`. Future tags use the same directory prefixes with the distribution's version. Tags accept `X.Y.Z` with an optional `aN`, `bN`, or `rcN` suffix, and the version must exactly match that module's `pyproject.toml`.

### One-time trusted-publishing setup

An administrator needs access to the GitHub repository settings and accounts on both package indexes:

1. In GitHub, open **Settings → Environments** and create `pypi-provider`, `pypi-delegator`, `testpypi-provider`, and `testpypi-delegator`. Publishing uses OIDC, not API-token secrets; `id-token: write` is granted only to the publish job.
   Configure required maintainer approval for all four environments (a solo maintainer must allow self-approval). Under **Deployment branches and tags**, allow only tags `model-provider/v*` for `pypi-provider` and only tags `delegator/v*` for `pypi-delegator`. Allow only branch `main` for both TestPyPI environments. Run the manual TestPyPI workflow from `main`; its `tag` input selects the package commit independently.
2. In the account-level **Publishing** settings on each index, choose GitHub Actions and add a pending publisher for each package using this mapping:

   | Index | Project name | GitHub environment |
   | --- | --- | --- |
   | PyPI | `hermes-omp-acp-provider` | `pypi-provider` |
   | PyPI | `hermes-omp-acp-delegator` | `pypi-delegator` |
   | TestPyPI | `hermes-omp-acp-provider` | `testpypi-provider` |
   | TestPyPI | `hermes-omp-acp-delegator` | `testpypi-delegator` |

   For all four entries, set the GitHub owner to `junghoon-vans`, repository to `hermes-omp-acp`, and workflow to `publish.yml` (the file is `.github/workflows/publish.yml`). The environment field must match the table.

   Each package uses a distinct environment because PyPI rejects pending publishers with identical owner/repository/workflow/environment settings for different project names. If a pending publisher already uses the old shared `pypi` or `testpypi` environment, replace that pending registration with the package-specific configuration above before publishing. Registering a publisher does not upload a release.

Pending publishers do not reserve project names; confirm each name is available on the intended index before publishing. See the official [PyPI Trusted Publishers guide](https://docs.pypi.org/trusted-publishers/using-a-publisher/) and [pending publisher setup guide](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/). PyPI and TestPyPI make uploaded wheels, sdists, and metadata publicly downloadable; review them for secrets or source you do not intend to disclose. Publishing a package does not change the repository's visibility.

### TestPyPI dry run and installed-package validation

Set the selected module's `pyproject.toml` version, commit the release candidate, then create and push its matching tag. Do not create a GitHub Release yet. For the current initial version `0.1.0`, the provider tag is `model-provider/v0.1.0` and the delegator tag is `delegator/v0.1.0`; create only the tag for the selected distribution:

```sh
git tag model-provider/v0.1.0
git push origin model-provider/v0.1.0
```

For the delegator, replace `model-provider` with `delegator` on both commands. Future tags use the exact version from that module's `pyproject.toml`.

In GitHub, open **Actions → `publish.yml` → Run workflow**, enter the existing tag in the `tag` input, and start the run. The workflow runs its reusable CI prerequisite and uploads the tested wheel and sdist artifacts to TestPyPI unchanged. `workflow_dispatch` publishes **only** to TestPyPI; it does not create a GitHub Release or upload to production PyPI. This is a real TestPyPI publication, not a no-side-effect simulation.

Validate the package from TestPyPI in a disposable Hermes environment/profile. Set `HERMES_PYTHON` to the interpreter that runs Hermes and set `PACKAGE` and `VERSION` to the selected distribution and exact tag version:

```sh
HERMES_PYTHON=/absolute/path/to/hermes/python
PACKAGE=hermes-omp-acp-provider # or hermes-omp-acp-delegator
VERSION=0.1.0
"$HERMES_PYTHON" -m pip install --no-deps \
  --index-url https://test.pypi.org/simple \
  "$PACKAGE==$VERSION"
```

For the delegator, install its declared dependency separately from production PyPI:

```sh
"$HERMES_PYTHON" -m pip install --index-url https://pypi.org/simple \
  "agent-client-protocol==0.9.0"
```

Do not use `--extra-index-url`: install the named package with dependencies disabled from TestPyPI, then obtain its dependencies from PyPI. Run Hermes from that same environment, enable the package's plugin ID (`omp-acp-provider` or `omp-acp-delegator`), and exercise the corresponding quick start in the [provider guide](model-provider/README.md) or [delegator guide](delegator/README.md). Check that Hermes discovers the installed entry point and that its documented behavior works; `pip install` alone is not validation.

### Production release

After the TestPyPI install check succeeds, create and publish a GitHub Release for the same tag in the repository's **Releases** page. Publishing the GitHub Release starts the production path for only the selected package; a pushed tag alone does not publish to PyPI, and `workflow_dispatch` remains TestPyPI-only. The release workflow requires the full reusable CI suite and promotes the exact tested wheel and sdist artifacts without rebuilding them. Verify the production release contains the expected version and both artifacts.

### Failure and version handling

- A tag/version mismatch or failed CI gate prevents upload. Inspect the workflow logs, fix the affected package, and create a correctly versioned tag for the corrected commit. Do not edit or replace the tested artifacts.
- If a TestPyPI upload has succeeded, use a new version and matching tag for another test upload rather than relying on overwriting it.
- Production PyPI versions are immutable: an uploaded file cannot be replaced or re-uploaded for that version. If any production artifact was uploaded before a failure, treat that version as consumed; fix the package, bump only its version in its `pyproject.toml`, and publish a new matching tag and GitHub Release. The other distribution's version is independent.

## Runtime changes and pull requests

For runtime changes, exercise the changed path with the applicable real Hermes/OMP installation and temporary profiles and test files. Include the Hermes and OMP versions, scenario, and observed result in the pull request. Paid-provider authentication, live model behavior, and permission-dependent operations need separate checks against the relevant configured provider.

- Keep changes focused on the affected plugin and reuse existing Hermes behavior where possible.
- Add regression coverage for consumer-visible compatibility changes.
- Include the problem, implementation, and verification results in the pull request.
- Keep credentials, personal configuration, and session data out of commits and logs.

The provider follows the [official Hermes model-provider contract](https://github.com/NousResearch/hermes-agent/blob/e06df9c2eae6901f4c88f464e0ae6e753c0428b8/website/docs/developer-guide/model-provider-plugin.md).
