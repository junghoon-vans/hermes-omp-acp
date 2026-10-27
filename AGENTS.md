# Agent instructions

## Scope and source of truth

These instructions apply to the entire repository. `AGENTS.md` is the canonical
agent guide; `HERMES.md` and `CLAUDE.md` are relative symlinks to it. Edit this file
rather than replacing either symlink with a separate copy.

Read [README.md](README.md) for installation, configuration, supported versions,
and user-facing behavior. Read [CONTRIBUTING.md](CONTRIBUTING.md) for development,
verification, and publishing procedures. Keep this guide focused on agent work;
do not duplicate those documents here.

## Repository map

- `hermes_omp_acp/provider.py`: Hermes provider registration, external-process
  configuration, and model discovery.
- `hermes_omp_acp/client.py`: Hermes ACP client adaptation, explicit model
  selection, and async completion handling.
- Root `__init__.py` and `plugin.yaml`: source-plugin entry point and metadata.
- `pyproject.toml`: the single root Python distribution and installed plugin
  entry point. The package is `hermes_omp_acp`, the distribution is
  `hermes-omp-acp`, the plugin ID is `omp-acp-provider`, and the provider ID is
  `omp-acp`; these names are not interchangeable.
- `tests/test_provider.py` and `tests/acp_peer.py`: deterministic provider tests
  and their ACP peer.
- `tests/integration/`: native OMP integration scenarios.
- `docs/model-provider/`: implementation details, limitations, troubleshooting,
  and verification scope.
- `.github/workflows/`: compatibility CI and distribution publishing.

## Implementation boundaries

- Keep this a focused Hermes model-provider plugin. Reuse Hermes's provider
  contract, ACP transport, and completion bridge rather than introducing a
  parallel transport or agent runtime.
- Preserve explicit model selection: an unavailable model, failed selection, or
  returned model mismatch must fail instead of silently using OMP's default.
- Preserve event-loop responsiveness for asynchronous callers and the existing
  buffered streaming behavior.
- Each completion starts a new ACP session. Do not imply retained sessions or
  worktree management without implementing and verifying that behavior.
- The distribution declares `agent-client-protocol>=0.9.0,<0.10.0`; Hermes and OMP
  are installed separately. Preserve the official Hermes SDK pin when resolving
  plugin dependencies, and use the SDK's public transport/connection APIs.

## Verification

Run development commands from the repository root. Use a supported Hermes
checkout with its `acp` extra and install this provider into the same environment;
standalone Python is not sufficient for the provider tests. Follow the setup in
[CONTRIBUTING.md](CONTRIBUTING.md), with `HERMES_SOURCE` set to that checkout and
`PYTHONPATH` set to `"$HERMES_SOURCE"`:

```sh
uv run --project "$HERMES_SOURCE" --no-sync python -P -m unittest discover -s tests -v
uv run --project "$HERMES_SOURCE" --no-sync python -P -m unittest discover -s tests/integration -v
uv build --out-dir dist
```

The integration suite needs a real OMP executable; `OMP_INTEGRATION_COMMAND` can
select an absolute executable path. Keep manual runtime checks isolated with
temporary `HERMES_HOME`, `PI_CODING_AGENT_DIR`, and test files. Never modify a
personal or production profile or commit credentials, configuration, or session
data.

For runtime changes, add regression coverage for consumer-visible compatibility
changes and exercise the changed path through real Hermes/OMP. Report the
versions, scenario, and observed result in the PR. State checks not run and their
missing prerequisites rather than implying success. Documentation-only changes
do not require model calls; validate affected paths and symlinks instead.

## Change delivery

Keep changes focused. Update relevant documentation when changing documented
behavior. PR descriptions should include the problem, implementation, and
verification results. Follow [CONTRIBUTING.md](CONTRIBUTING.md) for releases; do
not publish packages or create release tags as a side effect of ordinary work.

### Branch names

- Create branches as `<type>/<short-description>`, using lowercase ASCII and
  hyphen-separated words. Use the commit types below as branch prefixes.
- Keep names descriptive and focused on one change, for example
  `feat/model-discovery`, `fix/async-completion`, or `docs/agent-instructions`.
- When tied to an issue, optionally include its number before the description:
  `fix/42-model-selection`.
- Do not rename an existing PR branch merely to adopt this convention; use it
  for newly created branches.

### Commit, PR, and issue titles

- Use Conventional Commits: `<type>[optional scope][!]: <description>`.
- Allowed types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`,
  `build`, `ci`, `chore`, and `revert`. Choose the type by the actual change;
  `style` is formatting only, not behavior.
- Use a short, imperative English description without a trailing period. Keep
  the subject under 72 characters. A scope is optional; when useful, name the
  affected area, such as `provider`, `client`, or `release`.
- Mark breaking changes with `!` before the colon or a `BREAKING CHANGE:` footer,
  and explain the incompatible behavior and required migration in the body.
- Keep each commit focused on one logical change. Reference related issues in
  the body or footer when applicable.
- Use the same format, types, optional scope, and description rules for both PR
  and issue titles. PR titles also serve as conventional squash-merge subjects.
  Examples: `docs: add shared agent instructions`,
  `fix(client): reject mismatched model selection`, and
  `refactor!: change provider configuration`.
