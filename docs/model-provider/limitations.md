# Known limitations

## Native ACP approval requests

`OmpACPClient` owns its ACP callbacks and uses the official Python SDK for transport. It preserves the previous client's permission-cancellation behavior for both synchronous and asynchronous completion calls.

When OMP sends a `session/request_permission` request, the plugin returns:

```json
{"outcome": {"outcome": "cancelled"}}
```

This native ACP request is not connected to a Slack approval card. The plugin does not wait for a person's decision or select an Allow once / Always allow option for OMP. An operation waiting for that permission receives cancellation instead.

This is an explicit policy of the standalone client, not an inherited Copilot handler. The plugin cannot obtain interactive approval through this callback, so it cancels the request. See the [session callbacks](../../hermes_omp_acp/_session.py).

Hermes-owned tool calls are a separate path: when a model response returns a tool call for Hermes to execute, Hermes applies its own tool and approval mechanisms. That does not establish approval support for OMP's native ACP requests.

## What this means for writes

The limitation does not mean every write is blocked. An operation that does not require a native permission request may execute under OMP's configured policy. The plugin does not change that policy.

The plugin's ACP filesystem callbacks also enforce these checks:

- File paths must be absolute and resolve within the session's working directory.
- Reads and writes are subject to Hermes's file safety checks.
- File content returned by reads is passed through Hermes's secret redaction.
- A `fs/write_text_file` request that requires interactive write approval is rejected by the bridge.

These checks describe the ACP filesystem callbacks, not every possible OMP tool execution path. The [session implementation](../../hermes_omp_acp/_session.py) applies Hermes's shared safety and redaction helpers.

## Verification boundary

The standalone client's automated local checks cover permission cancellation, safe filesystem reads and writes with read-back, secret redaction, rejection of out-of-workspace and symlink access, protected writes, and completion conversion. Native OMP integration checks cover file reading through actual OMP tool execution. They do not establish Slack end-to-end behavior.

The earlier implementation was exercised for read-only Slack responses, thread context, actual file reading, cancellation of an active turn, and a subsequent recovery request. Those Slack checks have not been repeated with the standalone transport.

Regular file creation, editing, deletion, and write/read-back checks have not been exercised in the Slack end-to-end checks. They need separate verification before claiming write support for that environment.

Slack approval for native ACP requests is unavailable with the current cancellation policy, not merely an untested feature. Allow once, denial without mutation, and Always allow reuse therefore cannot be claimed as supported by this plugin.
