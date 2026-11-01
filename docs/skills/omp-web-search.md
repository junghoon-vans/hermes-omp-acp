# OMP web search skill

The source skill is [`skills/omp-web-search/SKILL.md`](../../skills/omp-web-search/SKILL.md). The
`hermes-omp-acp` wheel includes the same shared resource under
the Python environment's `share/hermes-omp-acp/skills/omp-web-search`. Hermes excludes
`site-packages` ancestors from discovery, so the wheel uses shared data outside
the import package. It is not a standalone Hermes plugin.

## Install and expose the shared skill

First [install the provider distribution](../configuration.md#installation) in
the same environment as Hermes, respecting PM/Nix dependency ownership. Then
print the absolute shared skill directory in that environment:

```sh
hermes-omp-acp-skills
```

`hermes-omp-acp-skills` prints one absolute directory containing
`omp-web-search/SKILL.md`. It does not inspect or modify Hermes profiles, install the
skill into a profile, or change configuration. Merge the printed directory into
`skills.external_dirs` in the `config.yaml` of each intended Hermes home,
preserving any existing entries:

```yaml
skills:
  external_dirs:
    - /absolute/path/printed/by/hermes-omp-acp-skills
```

The directory is shared; do not copy the skill into profile directories. Select
the intended Hermes home explicitly as the caller before using Hermes commands:

```sh
export HERMES_HOME=/absolute/path/to/intended/hermes-home
hermes skills list
hermes chat -s omp-web-search
```

For a source checkout instead of the wheel, use the absolute path to that
checkout's `skills` directory as the `external_dirs` entry. From the repository
root, obtain it with:

```sh
printf '%s/skills\n' "$(pwd -P)"
```

Keep the checkout available while using the skill. The returned path from the
installed command is preferred when using the wheel.

Hermes also excludes any `.venv` or `venv` ancestor, even for an explicit
external root. Use an environment named differently (for example `runtime-env`)
or the source checkout/Nix-store skill root outside those directories. The helper
does not copy resources out of excluded environments. On older Hermes releases,
a local skill with the same name produces an ambiguity error instead of taking
precedence; remove the collision or upgrade to tiered-precedence Hermes.

## Separate skill discovery

Enabling `omp-acp-provider` alone does not discover this skill. Expose its shared
directory through `skills.external_dirs` as above. The optional
[`omp-acp-skills` companion](../configuration.md#optional-bundled-omp-acp-usage-skill)
registers only the bundled `omp-acp` usage skill, not `omp-web-search`.

For the provider/general-plugin distinction, see
[skill discovery internals](../implementation.md#skill-discovery).

## Prerequisites and route check

- A working [OMP ACP provider](../../README.md#quick-start), installed in the
  same Hermes environment.
- An existing delegation model route whose provider is `omp-acp`. For the
  official Advanced Delegation setup, see
  [delegation model routes](../configuration.md#official-delegation-model-routes);
  that plugin requires Hermes 0.22.5 or later. Use a route already configured
  for the selected Hermes home—do not assume or invent a route name.
- An OMP inference model and working OMP-native `web_search` and public URL
  `read` tools. Search availability, backend, billing, and limits depend on the
  OMP setup.

Before using the skill, confirm `omp-web-search` appears in `hermes skills list` and
verify that the intended delegation route is configured with provider
`omp-acp`. The skill's workflow uses `delegate_task` and asks the delegated OMP
worker to use native `web_search` and `read`; route configuration or preloading
the skill alone does not prove those calls succeeded. Claim successful research
only when the actual delegated result and tool evidence support it.

## Workflow boundaries

The skill defines a bounded, public-web research procedure: at most 3 queries,
5 results per query, 5 explicit allowed hostnames, 8 research tool calls total,
and 180 seconds from delegation dispatch. These are agent-followed workflow
limits, not transport or sandbox enforcement; redirects and internal search
backend requests are not independently bounded by the skill. It does not
promise free or unlimited search, exclude any OMP backend, enforce web-only
access, or change approval/provider defaults. The workflow is not a security
boundary: OMP may still have shell, local-file, MCP, and other capabilities.

For route configuration details, see [Configuration](../configuration.md#official-delegation-model-routes).

## Framework contracts

- [Model provider discovery](https://hermes-agent.nousresearch.com/docs/developer-guide/model-provider-plugin#how-discovery-works)
- [General-plugin bundled skills](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins#bundle-skills)
- [External skill directories](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills#external-skill-directories)
