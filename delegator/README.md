# OMP ACP delegator

A standalone [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin for delegating coding tasks to the installed [OMP](https://github.com/can1357/oh-my-pi) agent. Its plugin ID and Hermes toolset are `omp-acp-delegator`.

The plugin starts the official `omp acp` command and uses the official [ACP Python SDK](https://github.com/agentclientprotocol/python-sdk) for session and message handling. OMP keeps its own models, authentication, tools, and permission settings. This is task delegation, not a Hermes model provider.

## Permission policy

**Use OMP's existing permissions. Do not escalate them.**

- The plugin does not add `--yolo`, approval-bypass flags, or a forced approval-policy overlay.
- Tools already permitted by OMP can execute. This is **not** an automatically read-only mode.
- Additional ACP permission requests are rejected, not relayed to a user or silently approved. The result records them and reports `permission_required`, even if OMP finishes its turn normally.
- No client-side filesystem, terminal, or elicitation capabilities are advertised. Unsupported requests are rejected by the SDK. OMP operations needing an interactive UI cannot acquire that permission through this plugin.
- There is no plugin-specific approval UI, permanent-grant store, or channel SDK. A user wanting unattended writes must explicitly configure OMP's own official permissions outside this plugin.
- A worktree protects the original checkout from routine edits; it is **not an OS sandbox**. Already permitted commands can access anything the gateway's OS user can access. Use a dedicated OS account or container when that is required.

Hermes's own tool restrictions and approval configuration can still gate the outer delegation. Approving that outer call does not make the plugin approve OMP's later permission requests.

## Requirements

- macOS or Linux; Windows is not supported because process-group cleanup and Git task locking use POSIX facilities.
- Hermes Agent **0.21.0 or later; 0.22.3 or later is recommended**. See the compatibility table below for optional features and diagnostic limitations.
- Python 3.11–3.14 and `agent-client-protocol==0.9.0` in the Hermes Python environment. Use a Python version supported by your Hermes release: Hermes 0.21.0–0.21.5 declare Python 3.11–3.13. The SDK pin matches Hermes's optional ACP dependency.
- Git and a working OMP installation on the gateway's PATH. OMP 18.12.1 is the compatibility version used by CI; the plugin does not install or update OMP.
- OMP provider authentication and model configuration prepared through OMP's normal setup. No credentials belong in this repository or the plugin settings.

There is no Node.js/npm/Bun requirement imposed by the plugin; any runtime requirements of your OMP installation remain OMP's responsibility.

### Hermes compatibility

|Hermes release|Basic delegation, follow-up sessions and conversation isolation|`/omp-cancel` slash command|Continuity after compaction changes the Hermes session ID|
|---|---|---|---|
|0.21.0–0.21.7|Supported|Unavailable|Unavailable|
|0.22.0–0.22.2|Supported|Unavailable|Unavailable|
|0.22.3–0.22.4|Supported|Supported|Supported|

The minimum is a tested support baseline, not a claim that every earlier Hermes release is incompatible. All eight 0.21.x releases passed native-Hermes delegation, follow-up and conversation-isolation smoke checks without changes to the plugin implementation. Versions 0.21.0–0.21.5 use official calendar-versioned tags, from `v2026.8.31` through `v2026.9.24`; CI uses `v2026.8.31` for the minimum, `v0.22.3` for the recommended baseline, and the dynamically resolved latest stable release.

Hermes 0.22.3 adds the official [plugin command context dispatcher](https://github.com/NousResearch/hermes-agent/blob/v0.22.3/hermes_cli/plugin_command_dispatch.py), which passes the trusted `session_id` to `/omp-cancel`. Earlier hosts call slash handlers with raw arguments only; this plugin rejects cancellation without a trusted session ID. The `omp_cancel` **tool** uses the separate session-aware tool dispatch path and remains available on older supported releases.

The missing `post_compaction` hook does not disable Hermes's own context compression. It prevents this plugin from mapping a rotated Hermes session ID back to the existing OMP session. Unknown-hook warnings and Plugin Doctor's rejection of that hook on older hosts are expected; they do not prevent basic delegation.

## Install and configure

Install the standalone plugin subdirectory into the active Hermes profile and enable its plugin ID:

```sh
hermes plugins install junghoon-vans/hermes-omp-acp/delegator
hermes plugins enable omp-acp-delegator
```

The repository is private, so installation requires GitHub access. Run both commands for the Hermes profile that should load the plugin. For a named profile, prefix the commands with `hermes -p <profile>`.

On Hermes **0.22.3 or later**, prepare dependencies and check the installed plugin using the official commands:

```sh
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PLUGIN_DIR="$HERMES_HOME/plugins/omp-acp-delegator"
hermes plugins validate "$PLUGIN_DIR" --install-deps
hermes plugins doctor "$PLUGIN_DIR" --ci
```

Older supported hosts need the same SDK dependency, but may not provide `plugins validate` (Hermes 0.21.0 does not). Install the declared dependencies into the Python environment that runs Hermes. For example, with `uv`, replace the interpreter path with your Hermes interpreter:

```sh
uv pip install --python /absolute/path/to/hermes/python \
  -r "$HERMES_HOME/plugins/omp-acp-delegator/pyproject.toml"
```

Then enable the plugin if you have not already:

```sh
hermes plugins enable omp-acp-delegator
```

Dependency preparation does not install OMP or change your OMP permissions.

On Hermes 0.21.0–0.22.2, loading logs an unknown `post_compaction` hook warning. Plugin Doctor also reports that unavailable hook as an error; basic delegation can still run. Use Hermes 0.22.3+ for a clean Doctor check, `/omp-cancel`, and compaction continuity.

Merge the following into Hermes's `config.yaml`, replacing the repository root with an existing absolute directory:

```yaml
plugins:
  enabled:
    - omp-acp-delegator
  entries:
    omp-acp-delegator:
      settings:
        allowed_roots:
          - /absolute/path/to/projects
        timeout: 1800
        omp_command: omp
```

These are official plugin-relative settings read through `ctx.get_config()`. `allowed_roots` is required and has no permissive default. `timeout` is the total duration of one run in seconds. `omp_command` names the installed executable; use an absolute path when a service's PATH differs from your shell. The plugin appends only the `acp` subcommand.

Enable the `omp-acp-delegator` toolset on the Hermes surfaces you use. For example, merge this into existing toolsets rather than discarding other tools:

```yaml
platform_toolsets:
  cli: [omp-acp-delegator]
  slack: [omp-acp-delegator]
  telegram: [omp-acp-delegator]
```

The implementation is channel-independent and uses trusted session IDs injected by Hermes. It has no Slack-specific token, SDK, or message-delivery requirement. A surface that does not provide a Hermes session identity cannot start a task. Restart the gateway using your normal procedure after changing plugin configuration; this plugin does not manage services.

### Python package alternative

The delegator can also be installed as a Python package from this directory, into the **same Python environment that runs Hermes**:

```sh
cd delegator
python -m pip install .
```

The package registers the `hermes_agent.plugins` entry point `omp-acp-delegator`. Use the same activation command above after installing it. This package route is an alternative to Hermes's source-plugin installation; it does not install Hermes or OMP. The Python package has not been published to PyPI.

## Tools

### `omp_task`

```json
{
  "repository": "/absolute/path/to/projects/my-repo",
  "prompt": "Fix the failing behavior, run targeted verification, and report the results."
}
```

Pass the canonical Git repository root, not a subdirectory. A repository needs a committed `HEAD`. A new task starts in an isolated worktree from that commit; uncommitted changes in the original checkout are not copied.

Follow-up calls in the same Hermes conversation and repository reload the same OMP session and worktree. On Hermes 0.22.3+, public `post_compaction` hooks preserve this identity when Hermes rotates transcript IDs. On 0.21.0–0.22.2, a rotated ID can instead start a new OMP session and worktree; the previous task data is retained but is not automatically reattached. New/reset/forked sessions do not inherit an unrelated task. No Hermes internal database is read.

After failure, cancellation, timeout, or `permission_required`, inspect `omp_status` and obtain explicit user continuation before adding `"resume_interrupted": true`. The plugin never retries a mutation automatically.

### `omp_status`

```json
{"repository": "/absolute/path/to/projects/my-repo"}
```

Reads the retained task state, including while a task is running, without another model turn. Results include the worktree, branch, OMP session ID, response, tool outcomes, permission requests, and Git change summary when available.

A normal ACP `end_turn` is not proof that the requested coding task or its tests succeeded. Read the reported tool outcomes and actual verification. Permission denial and failed final tool outcomes are not reported as an unqualified `completed` result.

### `omp_cancel` and `/omp-cancel`

The `omp_cancel` tool takes the same `repository` argument as `omp_status`. It requests cancellation only for the calling conversation's active task and is available on every supported Hermes version.

**Hermes 0.22.3 or later is required for the `/omp-cancel` slash command**, because Hermes must pass the calling conversation's trusted session ID:

```text
/omp-cancel /absolute/path/to/projects/my-repo
```

Cancellation sends ACP `session/cancel`, terminates the OMP process group, and escalates cleanup for children that ignore termination. The total run timeout uses the same cleanup path. The task state and worktree are retained for inspection. A cancellation request is not proof that cleanup has completed; inspect `omp_status` for the final `cancelled` result. Session reset/finalization, interrupted session-end hooks, and plugin unload also request cancellation. `/stop` behavior depends on the host's lifecycle dispatch. On older hosts, `/omp-cancel` fails closed; use the `omp_cancel` tool when the host can dispatch another tool call, or the host's supported session-reset/termination controls.

## Storage and lifecycle

Task state, worktrees, and stderr logs live under the documented `plugin_data_dir("omp-acp-delegator")` location, normally `$HERMES_HOME/plugin-data/omp-acp-delegator/tasks`. Conversation aliases use the profile-scoped public `ctx.state` API. Plugin updates do not delete this data. There is no automatic worktree cleanup or pruning.

OMP owns its own persistent sessions and provider credentials. Prompts and repository content may be sent to the provider selected in OMP. The plugin has no separate telemetry or credential-copying mechanism.

Python owns subprocess creation so it can put each run in a separate POSIX process group; the official ACP SDK owns the connection, types, session methods, callbacks, and protocol framing. No custom ACP server or JSON-RPC parser is shipped.

## Development

See [repository CONTRIBUTING.md](../CONTRIBUTING.md) for the behavior suite, Hermes compatibility smoke, temporary-profile safety, and build instructions.

## License

MIT.
