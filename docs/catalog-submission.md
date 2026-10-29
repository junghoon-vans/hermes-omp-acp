# Plugin catalog preparation — draft

**Not admission-ready.** Publishing preparation to the author's repositories is authorized; opening any PR, pushing upstream, merging, releasing, tagging, or changing credentials is not. Awesome issue #554 is unrelated and must remain unchanged.

## Publication scope

- Plugin repository: `https://github.com/junghoon-vans/hermes-omp-acp`, branch `chore/catalog-submission-prep`.
- Personal Hermes fork: `https://github.com/junghoon-vans/hermes-agent`, branch `chore/draft-omp-acp-catalog`.
- Upstream baseline: `NousResearch/hermes-agent` at `83bde48a4d9760d84220a9b29094a1ad86410f6e`.
- Catalog payload: only `plugin-catalog/omp-acp-provider.yaml`. Its `sha` and `docs_url` must identify the published plugin preparation commit, not the old release or an unpublished local revision.
- The plugin version remains `0.2.3`, with `requires_hermes: ">=0.21.4"`; these changes disclose existing behavior rather than changing runtime code or making a release.
- The original plugin checkout and meta-repository submodule pointer are outside this change. Separate preparation and detached pin worktrees are used.

The plugin's contribution instructions define no Kanban assignment workflow. No task board or issue is created solely for publication.

## Policy assessment

### Rule 11: actual blocker, not just a scanner warning

`hermes_omp_acp/mcp.py` reads OMP's external `mcp.json` and forwards environment/header strings from it. The file can contain secrets or command-backed values. These do not pass through `agent.secret_scope.get_secret` or Hermes-owned OAuth request routing. This is incompatible with the rule's required credential path when those fields carry secrets; a README disclosure cannot waive it.

`_session.py:_build_subprocess_env` calls `hermes_subprocess_env(inherit_credentials=True)`. OMP owns its model credentials and may reuse its own MCP OAuth storage. The plugin does not directly read a vendor login store, but that distinction alone does not establish that delegating authentication to OMP satisfies the catalog's Hermes-owned credential requirement. An approved external-process credential integration is needed before claiming eligibility.

Do not silently disable MCP forwarding, strip required authentication, remove safety checks, or replace the working provider architecture just to pass admission. Resolving these paths requires a scoped integration design, not a data-only listing change. No upstream issue or PR is opened by this preparation.

### Rule 9: undocumented imports, no observed runtime override

The provider registers through `providers.register_provider(ProviderProfile)` with `auth_type="external_process"`. Source inspection found no runtime replacement of Hermes core. The previous admission probe reported nine undocumented import warnings:

- `_bridge.py:9`: three completion/tool-bridge helpers from `agent.acp_openai_bridge`.
- `_session.py:31`: four safety helpers from `agent.file_safety`.
- `_session.py:37`: `agent.redact.redact_sensitive_text`.
- `_session.py:266`: `tools.environments.local.hermes_subprocess_env`.

These warnings are not the rule 9 runtime-override failure, but the public-surface-only requirement remains unresolved. Do not duplicate or drop safety code to hide them. Public bridge, filesystem safety, redaction, and subprocess-environment seams are the needed integration surfaces.

### Scanner caution and execution model

The prior `context_exfil (implementation.md:62)` caution points to documentation of sending the conversation prompt to OMP. That is intentional provider behavior, now disclosed in the README, not evidence of a separate hidden exfiltration endpoint. Keep the text and scanner enabled; a caution is not an admission approval.

The live SDK client runs in the Hermes process; OMP runs in a child process. This is not an isolated plugin host. The validator's in-process warning is a trust disclosure, not by itself a ban in the catalog rules. Native OMP tools are not universally sandboxed by ACP filesystem callbacks. Permission cancellation is not equivalent to applying Hermes approval policy to every native OMP operation; rule 12 needs review of that boundary before admission.

## Validation procedure

Use the official upstream source and an already prepared disposable PM environment. Do not mutate a personal PM/Nix installation, use resolver overrides, or inherit personal credentials. Let `CATALOG` be the catalog checkout, `PIN` a pristine checkout of the exact published plugin SHA, `PY` the disposable Hermes Python, and `SCRATCH` a disposable scratch directory.

From `CATALOG`:

```sh
"$PY" scripts/validate_plugin_catalog.py plugin-catalog/
"$PY" scripts/validate_plugin_catalog.py --json plugin-catalog/omp-acp-provider.yaml
"$PY" scripts/scan_plugin_payloads.py "$PIN"
```

Run admission with an allowlisted environment, using the Hermes executable beside `PY`:

```sh
env -i HOME="$SCRATCH/home" PATH=/opt/homebrew/bin:/usr/bin:/bin \
  HERMES_HOME="$SCRATCH/home/.hermes" \
  HERMES_RUNTIME_DIR="$SCRATCH/runtime" \
  PI_CODING_AGENT_DIR="$SCRATCH/omp" TMPDIR="$SCRATCH" \
  PYTHONDONTWRITEBYTECODE=1 \
  "$(dirname "$PY")/hermes" plugins validate "$PIN" --install-deps
```

Also exercise the actual catalog loader/search against the entry and confirm its SHA, install URL, version, floor, and pinned README link. Run `scripts/check` under the same isolation with the existing uv executable directory on PATH. Review warnings rather than suppressing them.

## Historical evidence and current-pin gate

Before these disclosure changes, the public release pin was `b66c4d5c63964b29f4cb5b5e63169f9c47250666`. Earlier local preparation against that tree reported: 863 structurally valid catalog files, passing dependency admission/capability probe, a clean executable-payload scan, passing catalog-loader smoke, and `scripts/check: 13 checks, ok`. The security caution, nine private imports, and in-process warning remained. Those results are historical, not proof for a new SHA.

After publishing, independently read back each exact personal repository/branch SHA, check out the plugin commit in the clean pin worktree, update both catalog pin fields, and repeat the procedure above. Report the actual new-pin outputs separately; this document deliberately does not embed its own future commit SHA.

No paid model inference, personal OMP login, Work integration, browser session, cross-platform run, Linux catalog CI job, or full upstream test suite is claimed. Documentation-only preparation does not require a model call. Automated passes cannot clear the architectural blockers above.

## References

- [Official admission rules](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins/catalog-submission)
- Upstream `plugin-catalog/README.md`, `.github/workflows/plugin-catalog-ci.yml`, `scripts/validate_plugin_catalog.py`, and `.github/PULL_REQUEST_TEMPLATE.md` at the baseline above.
- [Draft description](catalog-pr-description.md); text only, not authorization to create a PR.
