# Verification

## Continuous integration

[CI](https://github.com/junghoon-vans/hermes-omp-acp/actions) checks three installation configurations: minimum Hermes 0.21.4 (`v2026.9.21`) with OMP 15.7.6 on Python 3.11; dynamically resolved latest Hermes with OMP's npm `latest` on Python 3.14 using source extra `acp`; and Hermes 0.22.5 at pinned official commit [`bf0203a`](https://github.com/NousResearch/hermes-agent/commit/bf0203a794a07aac3ff764972ab9087baa4e4f4e) with OMP `latest` on Python 3.14 using source extra `all` (which includes `acp`). Latest currently resolves to 0.22.5, but those rows exercise different extras. CI resolves and installs the provider wheel together with Hermes dependencies read from the official source `pyproject.toml`. Hermes source itself is checked out and imported through `PYTHONPATH`, not installed as a Hermes wheel: its regular installer metadata does not declare the full source dependency set.

CI rejects modifications to the upstream `pyproject.toml` and `uv.lock`, and checks the installed SDK against the original Hermes `acp` declaration as well as the installed provider's metadata. Before inference scenarios, `.github/scripts/smoke_discovery.py` verifies the installed wheel import, enabled entry-point discovery, and native OMP ACP model catalog in temporary homes without a completion/model call. This smoke uses a local fixture model with no credentials and no inference endpoint.

The provider wheel's native integration suite starts with `omp-acp-provider` already enabled in a temporary Hermes configuration. A separate PM Git-install job exercises the official installation lifecycle rather than treating that wheel setup as admission proof.

### PM-managed Git installation

The dedicated job targets unmodified Hermes 0.22.5 at `bf0203a794a07aac3ff764972ab9087baa4e4f4e`, bootstrapped with Python 3.14 and the `acp` extra. Hermes 0.21.4 has no `pm` package or transactional plugin-admission module; its existing wheel/runtime lane is retained, not silently skipped by the PM job. The dynamic latest and pinned `all` wheel lanes remain independent compatibility checks; PM admission is claimed only for the pinned `acp` baseline.

The command shape follows the [official plugin installation guide](https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins); supported flags and admission behavior are checked against the [pinned install implementation](https://github.com/NousResearch/hermes-agent/blob/bf0203a794a07aac3ff764972ab9087baa4e4f4e/hermes_cli/plugins_cmd_install.py) and [PM CLI](https://github.com/NousResearch/hermes-agent/blob/bf0203a794a07aac3ff764972ab9087baa4e4f4e/pm/cli.py), not assumed from mutable current documentation.

`.github/scripts/smoke_pm_install.py` clones the official baseline and creates a bare Git remote containing the current checked-out PR/reusable-workflow revision. In fresh `HOME`, `HERMES_HOME`, and XDG directories, it runs:

```sh
python hermes pm install --tools-only --without agent-browser --without cua-driver
python hermes pm install --extra acp
python hermes plugins install file:///scratch/hermes-omp-acp.git \
  --ref <full-current-commit> --enable --yes-deps --force
python hermes plugins enable omp-acp-provider
python hermes plugins list
```

The first command provisions PM's required pinned tool closure; `--extra acp` alone assumes those tools are already available. Only the optional browser/computer-use payloads are declined through official CLI flags, not dependency checks.

The official security scanner labels this custom source `CAUTION` (including subprocess calls in tests/CI and explanatory documentation). `--force` is its explicit non-interactive confirmation for this trusted local revision, not an SDK/resolver override. Scanning remains enabled, and Hermes still refuses `DANGEROUS` verdicts. Without that confirmation, the official installer rejects the source before dependency admission. CI does not claim unattended installation with the default scan-consent policy.

The script first verifies that neither the provider distribution nor provider registration exists. After installation it checks the manager's immutable pin and actual Git HEAD, committed enablement, installed SDK satisfaction of both original declarations, and provider discovery in a fresh process using PM's selected dependency environment. It rejects changes to upstream `pyproject.toml` and `uv.lock`. A zero CLI exit alone is not success: Hermes can leave a refused plugin installed but disabled.

The local `file://` remote is a supported custom Git source (Hermes warns that this scheme is unsuitable for production); it exercises real clone, exact-ref checkout, scanning, publication, and dependency admission without requiring GitHub credentials or fetching the old plugin from `main`. It does not cover GitHub transport/authentication, catalog admission, a running gateway's hot reload, or a live service restart. Fresh-process discovery is the restart boundary tested here. No model, paid API, or OMP installation is needed in this job; native OMP behavior remains covered by the wheel lanes.

This is profile-isolated, not network-offline: official PM acquires its pinned manager/tool/interpreter artifacts and locked Python dependencies from upstream download hosts/package indexes. Its lock validation, dependency policy, and any network failures remain hard failures; the script does not force an SDK, preinstall the provider, patch upstream, or bypass dependency resolution. Run locally from the repository root with a clean bootstrap interpreter:

```sh
uv run --no-project --python 3.14 python .github/scripts/smoke_pm_install.py \
  --hermes-source /path/to/unmodified/hermes-0.22.5
```

CI builds and installs the wheel, then runs the ACP regression suite and native OMP integration suite. The native tests use a local OpenAI-compatible model endpoint to exercise provider discovery, model-picker availability, model selection, async responses, native file reading, MCP tool execution, and Hermes CLI output without external credentials.

Each integration scenario uses temporary Hermes and OMP homes. The model endpoint requests a file read or a stdio MCP tool call, then produces its response from the fresh token returned by actual OMP tool execution. The token is not supplied in the initial prompt.

MCP regression coverage includes:

- Reading configuration through `PI_CODING_AGENT_DIR` and the default `~/.omp/agent` path.
- Launching a real stdio MCP server from `~/.local/bin` when that directory is absent from the process PATH.
- Allowlist overrides for `enabled: false`, with the denylist still preventing server startup.
- Rejecting malformed configuration before task execution without exposing configuration values, while keeping model discovery available.
- Rejecting an HTTP server when the ACP peer does not advertise HTTP support.
- Disabling forwarding through the `mcp_enabled` plugin setting or `HERMES_OMP_ACP_MCP_ENABLED`, bypassing malformed configuration, then restoring validation when overridden on the same client.
- Rejecting an invalid toggle before task execution while keeping model discovery available.
- Preventing configured MCP server startup when disabled while native OMP file reading still completes; existing stdio scenarios exercise forwarding enabled by default.

The automated native MCP tool-call scenarios use stdio. They do not establish live HTTP/SSE server compatibility, OAuth refresh behavior, or preservation of OMP-only options.

The standalone client's ACP peer regressions also exercise permission cancellation, safe reads/writes with read-back, secret redaction, rejected out-of-workspace and symlink access, protected writes, reasoning/tool-call conversion, model discovery from an async caller, and peer-exit diagnostics. These use actual subprocess connections through the SDK rather than mocked transport.

Lifecycle regressions use peer readiness gates to establish overlapping sessions before releasing or cancelling them. They verify that timeout or caller cancellation reaps only the affected child while a sibling still completes, and that `close()` stops and reaps all active children. Additional cases cover one timeout budget across initialization/session creation/model selection, and ordered text/reasoning bursts followed immediately by the prompt response. A focused post-EOF guard case uses the internal `ACPRuntime._session`/`_request` boundary to wait until the real stdout reader observes EOF, then verifies that a subsequent RPC sends no bytes. The remaining lifecycle tests use the public provider/client interface; these checks use real SDK subprocess connections rather than mocked transport.

## Package metadata and release checks

The version and description come from [`plugin.yaml`](../plugin.yaml), not static fields in `pyproject.toml`. See [Build the distribution](../CONTRIBUTING.md#build-the-distribution) for the metadata hook and local build/check commands.

Local packaging checks for release 0.2.0 built an sdist and then a wheel from that sdist. Both artifacts passed `twine check --strict`. A disposable environment installation confirmed that the wheel's version and Summary match the manifest and that PyYAML is not a runtime requirement.

The release preparation script accepted `v0.2.0` and rejected the mismatched tags `v0.1.0` and `v0.2.0rc1`, as well as the malformed tag `0.2.0`. Running it with a temporary GitHub output file also succeeded. These checks did not publish to TestPyPI or PyPI and do not replace installed-package validation in Hermes.

## Minimum-version boundaries

The minimum versions are listed in [Requirements](../README.md#requirements).

- Hermes 0.21.4 adds generic external-process provider selection and setup. Earlier direct-provider runtime support alone does not cover the model picker. Its release tag is [`v2026.9.21`](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.9.21).
- OMP 15.7.6 fixes activation of client-provided MCP tools in ACP sessions ([upstream PR #1598](https://github.com/can1357/oh-my-pi/pull/1598)). Earlier versions can register those tools without making them available to the model. The `omp acp` subcommand itself was introduced in OMP 15.0.0, but that is not sufficient for MCP support.
- Hermes 0.21.4 declares Python `>=3.11,<3.14`; Hermes 0.22.x uses Python 3.14.
- Stored URL-keyed MCP OAuth reuse through ACP is an optional feature requiring OMP 18.12.6 or later. It is not guaranteed by the package's 15.7.6 minimum and is not covered by the automated native stdio scenarios.

CI runs both test suites against the minimum combination, including real stdio MCP tool execution and disabled-server precedence. See [Contributing](../CONTRIBUTING.md#native-omp-integration) for the integration command and executable override.

## Source-reviewed MCP behavior

The following findings come from upstream source and release-tag comparison, not live server/authentication checks:

- **Forwarded fields and extensions:** OMP 18.12.6's [ACP mapper](https://github.com/can1357/oh-my-pi/blob/v18.12.6/packages/coding-agent/src/modes/acp/acp-agent.ts#L3135-L3165) reconstructs configs from standard transport fields. Server-specific `cwd`, `timeout`, `instructions`, `requestIdFormat`, `auth`, `oauth`, and `_meta` are not copied.
- **OAuth release boundary:** [Commit `d022a0d`](https://github.com/can1357/oh-my-pi/commit/d022a0d37ac8ccfcc8ae7ca90acccedbf3c09eca) connects the ACP manager to auth storage. The connection is absent in tags 18.12.0–18.12.5 and present in [18.12.6](https://github.com/can1357/oh-my-pi/blob/v18.12.6/packages/coding-agent/src/modes/acp/acp-agent.ts#L3071-L3076). The minimum 15.7.6 ACP manager also lacks it.
- **OAuth conditions:** OMP 18.12.6's [credential lookup](https://github.com/can1357/oh-my-pi/blob/v18.12.6/packages/coding-agent/src/mcp/oauth-credentials.ts#L24-L79) uses profile/URL-keyed storage and disables URL fallback for an explicit Authorization header. Credential accessibility and refresh material still matter.
- **Timeout alternative:** the process-wide resolver exists in both [15.7.6](https://github.com/can1357/oh-my-pi/blob/v15.7.6/packages/coding-agent/src/mcp/timeout.ts#L1-L18) and [18.12.6](https://github.com/can1357/oh-my-pi/blob/v18.12.6/packages/coding-agent/src/mcp/timeout.ts#L1-L33). The latter also provides a separate startup-window setting.
- **Instructions path:** the [ACP session factory](https://github.com/can1357/oh-my-pi/blob/v18.12.6/packages/coding-agent/src/main.ts#L536-L582) disables the SDK MCP manager; the [prompt instructions provider](https://github.com/can1357/oh-my-pi/blob/v18.12.6/packages/coding-agent/src/sdk.ts#L4855-L4870) therefore has no manager. ACP refreshes tools from a separate manager, but [tool refresh](https://github.com/can1357/oh-my-pi/blob/v18.12.6/packages/coding-agent/src/session/session-tools.ts#L2372-L2385) does not install its instructions provider. Server instructions from that ACP manager are not connected to the model prompt.

The targeted field-mapping, auth-storage, and instructions paths were also checked against upstream `main` during the review. That is not a promise about future upstream changes. The review did not verify live OAuth login/renewal, HTTP/SSE connections, or model-prompt capture at runtime. Keep those claims separate from the automated tests above and follow the [MCP compatibility limits](limitations.md#mcp-configuration-compatibility) when validating a configured server.

## Additional checks

Paid-provider authentication, live model behavior, and permission-dependent operations need separate checks against the relevant configured provider.

For the standalone-client refactor, additional local smoke checks confirmed that timed-out model selection does not execute a prompt and reaps its process, `close()` stops and reaps an active request, and a session-scoped gateway working directory takes precedence over process cwd. These are local smoke results, not claims of Slack approval or end-to-end write support.

For MCP-specific manual checks, keep the model/profile environment isolated and explicitly request a read-only tool operation. Verify both the returned tool result and server-side behavior, including command-backed headers where applicable. HTTP/SSE connections, authentication renewal, and real browser integration need checks against the relevant server; a successful model listing or configuration conversion alone is insufficient. Review [MCP compatibility limits](limitations.md#mcp-configuration-compatibility) before interpreting those results.
