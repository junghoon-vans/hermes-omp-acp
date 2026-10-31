# Search skill

The source skill is [`skills/search/SKILL.md`](../../skills/search/SKILL.md). The
`hermes-omp-acp` wheel includes the same shared resource under
the Python environment's `share/hermes-omp-acp/skills/search`. Hermes excludes
`site-packages` ancestors from discovery, so the wheel uses shared data outside
the import package. It is not a standalone Hermes plugin.

## Install and expose the shared skill

Install the wheel into the same Python environment as Hermes, then print the
absolute shared skill directory:

```sh
python -m pip install hermes-omp-acp
hermes-omp-acp-skills
```

`hermes-omp-acp-skills` prints one absolute directory containing
`search/SKILL.md`. It does not inspect or modify Hermes profiles, install the
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
hermes chat -s search
```

For a source checkout instead of the wheel, use the absolute path to that
checkout's `skills` directory as the `external_dirs` entry. From the repository
root, obtain it with:

```sh
printf '%s/skills\n' "$(pwd -P)"
```

Keep the checkout available while using the skill. The returned path from the
installed command is preferred when using the wheel.

## Why provider activation does not discover skills

Hermes 0.22.5's provider entry point in installed `providers/__init__.py` invokes
the provider's no-argument `register()` hook (around lines 525–540 and
596–610). General plugin discovery in `hermes_cli/plugins_discovery.py` (around
lines 286–290) skips `kind: model-provider` plugins. Skill loading and the
supported `skills.external_dirs` configuration live on the separate
`agent/skill_utils.py` path (around lines 337–363). The current provider
contract therefore does not provide a skill-registration hook, and enabling
`omp-acp-provider` alone cannot make this skill appear in Hermes.

The minimal supported choice today is explicit `skills.external_dirs` as above.
Automatic provider-driven registration would require either a separately
implemented general-plugin adapter (a boundary change that must be authorized)
or an upstream model-provider skill hook. Neither is added by this package.

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

Before using the skill, confirm `search` appears in `hermes skills list` and
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
