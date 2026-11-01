---
name: omp-acp
description: Use when using OMP ACP as a Hermes model backend or delegated route.
metadata:
  hermes:
    tags: [omp, acp, delegation]
    category: development
---

# OMP ACP usage

Before using the OMP integration, call `skill_view(name='omp-acp-skills:omp-acp')` to load these instructions.

## Choose the route

- `omp-acp-provider` is the Hermes plugin ID to install and enable; `omp-acp` is the model-provider ID to select in Hermes. `omp-acp-skills:omp-acp` is this companion plugin's qualified skill name. Enabling the companion does not enable the provider.
- For a main-model request, select provider `omp-acp` and pass an exact OMP `provider/model` ID available to the account. The ACP backend is `acp://omp`; it is not a network URL. The provider requests the selected model explicitly and fails if it is unavailable or OMP selects a different model rather than silently using OMP's default.
- For subagent work, use Hermes's existing `delegate_task` route only when the active profile has a configured delegation option whose provider is `omp-acp`. With Advanced Delegation, select an already exposed option; do not invent a route name, tool, or model. See the repository's delegation configuration for setup. If no such route exists, report the missing setup instead of silently switching providers.

## Prepare and run the task

1. Send only the context needed for the task. Remove credentials, tokens, private or personal data, and unrelated conversation history. The provider forwards prompt text to OMP; its sensitive-text redaction applies to ACP file reads, not arbitrary prompt content.
2. Keep the request within the configured Hermes/OMP access and approval policies. This workflow cannot grant permission or bypass an approval. The ACP permission callback denies permission requests, and writes that require Hermes interactive approval are rejected by the ACP file bridge; stop and use the normal approved workflow if an operation is blocked.
3. Do not assume ACP session history or worktree management. Each completion starts a new ACP session; the provider does not retain OMP sessions or create/manage worktrees for delegated tasks.
4. For async callers, the client uses an asynchronous ACP runtime. Its streaming interface is buffered: response events are collected for the completion and then exposed as completion chunks, not guaranteed live token-by-token output.

## Handle failures carefully

If a request fails after its prompt may have been dispatched, it may already have changed files or had other partial effects. Inspect the relevant state before retrying, and avoid blindly submitting the same task again. Hermes may automatically retry only transport failures the provider confirms occurred before prompt dispatch; a possibly dispatched request is not automatically retried or switched to another model.

These are workflow instructions, not security enforcement. They do not restrict OMP's tools or replace Hermes, OMP, filesystem, or approval controls. The provider's file bridge applies its own filesystem checks and sensitive-text redaction, but do not treat that as a substitute for minimizing the prompt context you send.
