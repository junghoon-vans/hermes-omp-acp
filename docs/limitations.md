# Known limitations

## Native ACP approval requests

`OmpACPClient` subclasses Hermes's `CopilotACPClient`. The plugin adapts completion calls for synchronous and asynchronous callers; it inherits the upstream permission handler unchanged.

When OMP sends a `session/request_permission` request, that handler returns:

```json
{"outcome": {"outcome": "cancelled"}}
```

This native ACP request is not connected to a Slack approval card. The plugin does not wait for a person's decision or select an Allow once / Always allow option for OMP. An operation waiting for that permission receives cancellation instead.

This is a limitation inherited from the reused client, rather than an additional approval policy implemented by this plugin. See the [upstream handler](https://github.com/NousResearch/hermes-agent/blob/e06df9c2eae6901f4c88f464e0ae6e753c0428b8/agent/copilot_acp_client.py#L488-L505).

Hermes-owned tool calls are a separate path: when a model response returns a tool call for Hermes to execute, Hermes applies its own tool and approval mechanisms. That does not establish approval support for OMP's native ACP requests.

## What this means for writes

The limitation does not mean every write is blocked. An operation that does not require a native permission request may execute under OMP's configured policy. The plugin does not change that policy.

The inherited ACP filesystem bridge also has its own checks:

- File paths must be absolute and resolve within the session's working directory.
- Reads and writes are subject to Hermes's file safety checks.
- A `fs/write_text_file` request that requires interactive write approval is rejected by the bridge.

These checks describe the ACP filesystem bridge, not every possible OMP tool execution path. See the [upstream filesystem handlers](https://github.com/NousResearch/hermes-agent/blob/e06df9c2eae6901f4c88f464e0ae6e753c0428b8/agent/copilot_acp_client.py#L228-L273).

## Verification boundary

Read-only Slack responses, thread context, actual file reading, cancellation of an active turn, and a subsequent recovery request have been exercised.

Regular file creation, editing, deletion, and write/read-back checks have not been exercised in the Slack end-to-end checks. They need separate verification before claiming write support for that environment.

Slack approval for native ACP requests is unavailable with the current inherited handler, not merely an untested feature. Allow once, denial without mutation, and Always allow reuse therefore cannot be claimed as supported by this plugin.
