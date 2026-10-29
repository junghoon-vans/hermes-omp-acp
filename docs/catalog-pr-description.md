# Draft description — do not submit

Proposed title: `feat(catalog): add Hermes OMP ACP provider`

**Blocked on credential routing and public integration surfaces.** This is preparation text only. The user authorized personal-repository pushes, not PR creation, upstream pushes, or merges. The personal catalog branch is `chore/draft-omp-acp-catalog`.

## Summary

- Prepare `plugin-catalog/omp-acp-provider.yaml` for the community model-provider plugin at `https://github.com/junghoon-vans/hermes-omp-acp`.
- Use the full published SHA and pinned README URL recorded in that YAML. Version remains `"0.2.3"`, with `requires_hermes: ">=0.21.4"`.
- No Hermes runtime files change. The provider registers `omp-acp` through `providers.register_provider(ProviderProfile)`; it registers no tools, hooks, or middleware and declares no required environment secrets.
- No catalog issue is linked. Awesome issue #554 is not this submission. No issue or PR is created by this preparation.

## Risk disclosures

- **Processes/network:** Every completion starts separately installed `omp acp` in the session directory and sends the conversation to it. Model discovery also starts OMP. OMP contacts configured model services and may run native shell/file/web tools, MCP processes, or remote MCP services. The plugin does not install OMP.
- **Outside-plugin reads:** Default-on MCP forwarding reads `$PI_CODING_AGENT_DIR/mcp.json`, otherwise `~/.omp/agent/mcp.json`. Enabled commands, arguments, environment values, URLs, and headers are forwarded. Values may contain secrets; OMP interprets command-backed expressions such as `!command`. This is trusted executable configuration, not sandboxed data.
- **Credentials/rule 11 blocker:** The child receives Hermes's subprocess environment with credential inheritance enabled. OMP uses its own configured authentication/storage; a Hermes profile is not an OMP profile. The plugin does not directly read OMP's login store, persist tokens, or refresh OAuth, but does forward file-sourced MCP environment/header values rather than Hermes-owned secret routes. Documentation alone does not make this compliant. An approved credential integration is required before admission.
- **Approvals:** ACP permission requests are cancelled, not auto-approved or shown as Hermes approval prompts. ACP filesystem callbacks enforce session-directory confinement, Hermes file safety, read redaction, and rejection of approval-required writes. OMP operations that do not request permission follow OMP's policy; these callbacks do not sandbox all native tools. No YOLO argument is added by default. Rule 12's boundary still requires review.
- **Lifetime/updates:** Completion children are closed and reaped on completion, cancellation, or timeout. The plugin is not a persistent daemon and adds no telemetry or self-updater. External OMP/MCP service policies remain their own.
- **Private imports/execution model:** Nine undocumented bridge/safety/redaction/environment imports were reported by prior admission. They are not runtime core overrides, but public-surface eligibility is unresolved. The SDK client runs in-process; only OMP is a subprocess.

## Verification

The exact new published pin must pass the commands in [catalog-submission.md](catalog-submission.md): catalog schema, `hermes plugins validate --install-deps`, executable-payload scan, actual catalog loader/search smoke, and upstream blocking checks. Read back both personal branches independently after pushing.

Historical checks at `b66c4d5c63964b29f4cb5b5e63169f9c47250666` passed automated validation with a `context_exfil (implementation.md:62)` caution, nine private-import warnings, and an in-process warning. The caution documents intentional prompt transfer to the external provider; it is not suppressed. Historical results are not new-pin evidence or admission approval.

No paid model call, personal OMP account, Work integration, browser session, cross-platform runtime, full upstream test suite, or Linux catalog CI run is claimed. No UI or screenshots.

## Admission gates still open

- Resolve rule 11's file-sourced MCP credentials and external OMP authentication path through approved Hermes routes.
- Establish public bridge/safety/redaction/environment interfaces without dropping safeguards or duplicating core internals.
- Review scanner cautions and the native OMP/Hermes approval boundary.
- Obtain separate explicit authorization before opening any PR. Keep this branch draft until the architectural questions are resolved; automated validation alone is insufficient.
