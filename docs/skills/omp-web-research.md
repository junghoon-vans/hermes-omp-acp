# OMP web research skill

The source package is [`skills/omp-web-research/SKILL.md`](../../skills/omp-web-research/SKILL.md).
It uses existing Hermes → OMP ACP delegation and OMP's native `web_search` and
URL `read`. It adds no search transport and changes no approval/provider defaults.
Installing `omp-acp-provider` alone does **not** install or discover this skill.
Install the skill separately with Hermes's built-in installer; no extra installer
plugin or wheel resource path is needed.

## Prerequisites

- A working [OMP ACP provider](../../README.md#quick-start).
- An existing OMP-backed delegation route in the selected Hermes profile. The
  [Advanced Delegation model routes](../../README.md#official-delegation-model-routes)
  setup requires Hermes 0.22.5 or later. The examples use `option: omp-deep`;
  substitute an existing route name whose provider is `omp-acp`.
- A configured OMP inference model and working native web search and URL reader.
  Actual search availability, provider, billing, and limits depend on OMP setup.

## Install for one profile

Choose **one** installation method.
Identify the selected profile's home with `hermes profile show research` (replace
`research` with your profile). Do not assume the currently active profile is the
one you intended. Default installations use `~/.hermes`; named profiles normally
use `~/.hermes/profiles/<name>`. Custom Hermes homes can change these paths.

### Recommended: Hermes skill installer

Once this skill is published to the repository's default branch:

```sh
hermes -p research skills install \
  junghoon-vans/hermes-omp-acp/skills/omp-web-research
```

This uses Hermes's normal review/security-scan and profile-scoped installation
flow. Do not bypass a blocked scan with `--force`. Check installed hub skills for
updates with `hermes -p research skills check`; review updates before applying
`hermes -p research skills update`. Installing the skill does not configure the
OMP provider, inference credentials, or delegation route.

A separate installer plugin would duplicate this lifecycle and would still need
the same provider/route prerequisites. Keep the model-provider plugin focused on
model inference instead of adding automatic skill registration.

For an unpublished checkout, use one of the local methods below.

### Copy into the selected profile

From the repository root, set `PROFILE_HOME` to the home reported for that profile:

```sh
PROFILE_HOME=/absolute/path/to/selected/profile
mkdir -p "$PROFILE_HOME/skills"
test ! -e "$PROFILE_HOME/skills/omp-web-research" &&
  cp -R skills/omp-web-research "$PROFILE_HOME/skills/"
```

The guard avoids overwriting an existing skill. Review an existing copy before
updating it. A copied skill does not follow subsequent repository edits.

### Or configure an external directory

Merge into **that profile's** `config.yaml`, preserving existing skill settings:

```yaml
skills:
  external_dirs:
    - /absolute/path/to/hermes-omp-acp/skills
```

Point at `skills`, not at the individual `SKILL.md`. Keep the checkout available.
External directories are not write-protected: agent skill-management tools can
modify writable external files. Prefer a profile-local copy when repository
isolation matters. A same-named local/project skill may shadow the external copy.
See [Hermes external skill directories](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills#external-skill-directories).

## Discover and invoke

Start a new session after configuring the skill or delegation route:

```sh
hermes -p research skills list
hermes -p research chat -s omp-web-research
```

Check that `omp-web-research` appears in the list. In the interactive chat:

```text
/omp-web-research Find whether Python 3.14 has been released. Use only www.python.org and docs.python.org, at most 2 queries, 5 research tool calls, and 120 seconds. Use the existing omp-deep route. Read primary pages, cross-check the claim, and report sources, UTC retrieval time, uncertainty, failures, and usage. Do not change files or settings.
```

`-s` explicitly preloads the skill; `/omp-web-research` invokes it in chat.
Natural-language selection is also possible but is not the discovery smoke test.
If the route or skill is missing, fix installation/configuration before retrying;
do not treat an answer from another search tool as verification of this path.

## Real smoke test

Use disposable Hermes and OMP homes, not a personal/production profile. Configure
only the required provider/route and authorized model credentials using your
normal secure credential setup. Do not copy credentials into the repository,
query, transcript, or report. Run from a temporary working directory to avoid
loading project rules or exposing repository context.

1. Record `hermes --version` and `omp --version`.
2. Follow either installation method above for the disposable profile, then run
   `skills list` for that home/profile. Confirm the exact name is present.
3. Start `chat -s omp-web-research` there and send the public Python prompt above.
   Keep ordinary approvals enabled; do not add `--yolo` or change approval defaults.
4. Inspect the delegation result and OMP tool transcript. Require a real native
   `web_search` call and a successful URL `read` of an allowed primary page. Check
   the returned claim against that page. The final answer must link its sources,
   include UTC retrieval time (or an explicit clock limitation), and distinguish
   evidence from uncertainty. Record query/call counts and elapsed time.
5. In a fresh session, repeat with a **one-call total budget** and request both
   search and primary-page verification. The answer must report incomplete
   evidence/budget exhaustion, not claim that a snippet was a page read.
6. In another fresh session, ask it to read
   `https://www.python.org/omp-web-research-missing-page-smoke` with only
   `www.python.org` allowed and a one-call budget. Require an honest read failure
   or an explicit report of the actual returned page, not an invented citation.
7. Inspect the disposable working directory for unintended research output files.
   Normal Hermes/OMP state in their temporary homes is separate from intended
   repository mutations. Remove only the disposable homes you created afterward.

Workflow budgets and the domain allowlist are instructions to the agent, not a
hard sandbox. In-flight calls, redirects, and internal search fallback requests
are not independently prevented by this skill. Enforced web-only access and
backend exclusions (including Firecrawl) are outside its scope.

## Verification status

Checked with Hermes 0.22.5's installed Python runtime and OMP 18.12.6:

- A disposable profile using `skills.external_dirs` listed the skill as enabled;
  `skill_view` successfully loaded it. A separate profile-local copy also loaded.
- Real `omp search` returned Python.org sources using the Parallel backend.
  Real `omp read` retrieved the Python 3.14.0 release page, which states
  **October 7, 2025**. Retrieval was observed at **2026-10-26 13:55 UTC**.
- These are component checks, **not** a successful end-to-end delegated answer.
  A subsequent `hermes -p omp-worker chat` run recognized the existing
  `omp-acp-provider` 0.2.1 and Advanced Delegation setup. The repository skill was
  supplied in the request, without installing it or changing profile settings.
  Its configured OMP route is `coder`, not the illustrative `omp-deep`.
- That named-profile CLI run exited successfully and returned an honest incomplete
  report: its worker reported `turn_rate_limit_exceeded` ("Codex is at capacity"),
  zero searches/reads, and no invented findings or citations. Successful research
  through delegation and budget-exhaustion behavior remain unverified.
- Earlier `Unknown provider` output came from a disposable overlay setup, not the
  working named profile; it does not establish a provider-discovery defect there.
- The GitHub install command requires publication of these files; it has not
  been exercised against the unpublished skill.

## References

- [Hermes skills](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills)
- [OMP native web search, v18.12.6](https://github.com/can1357/oh-my-pi/blob/v18.12.6/docs/tools/web_search.md)
- [OMP URL reader, v18.12.6](https://github.com/can1357/oh-my-pi/blob/v18.12.6/docs/tools/read.md)
