# Verification

## Continuous integration

[CI](https://github.com/junghoon-vans/hermes-omp-acp/actions) checks both the latest stable Hermes release with OMP's npm `latest` version on Python 3.14, and the minimum supported Hermes 0.21.4 (`v2026.9.21`) with OMP 15.7.6 on Python 3.11. Both jobs use the latest Bun runtime. CI resolves each Hermes release to a commit SHA and records the source revision and executable versions in the logs.

CI builds and installs the wheel, then runs the ACP regression suite and native OMP integration suite. The native tests use a local OpenAI-compatible model endpoint to exercise provider discovery, model-picker availability, model selection, async responses, native file reading, MCP tool execution, and Hermes CLI output without external credentials.

Each integration scenario uses temporary Hermes and OMP homes. The model endpoint requests a file read or a stdio MCP tool call, then produces its response from the fresh token returned by actual OMP tool execution. The token is not supplied in the initial prompt.

MCP regression coverage includes:

- Reading configuration through `PI_CODING_AGENT_DIR` and the default `~/.omp/agent` path.
- Launching a real stdio MCP server from `~/.local/bin` when that directory is absent from the process PATH.
- Allowlist overrides for `enabled: false`, with the denylist still preventing server startup.
- Rejecting malformed configuration before task execution without exposing configuration values, while keeping model discovery available.
- Rejecting an HTTP server when the ACP peer does not advertise HTTP support.

The automated native MCP tool-call scenarios use stdio. They do not establish live HTTP/SSE server compatibility, OAuth refresh behavior, or preservation of OMP-only options.

The standalone client's ACP peer regressions also exercise permission cancellation, safe reads/writes with read-back, secret redaction, rejected out-of-workspace and symlink access, protected writes, reasoning/tool-call conversion, model discovery from an async caller, and peer-exit diagnostics. These use actual subprocess connections through the SDK rather than mocked transport.

Lifecycle regressions use peer readiness gates to establish overlapping sessions before releasing or cancelling them. They verify that timeout or caller cancellation reaps only the affected child while a sibling still completes, and that `close()` stops and reaps all active children. Additional cases cover one timeout budget across initialization/session creation/model selection, and ordered text/reasoning bursts followed immediately by the prompt response. The tests use the public provider/client interface, not private module names or transport mocks.

## Minimum-version boundaries

The minimum versions are listed in [Requirements](../../README.md#requirements).

- Hermes 0.21.4 adds generic external-process provider selection and setup. Earlier direct-provider runtime support alone does not cover the model picker. Its release tag is [`v2026.9.21`](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.9.21).
- OMP 15.7.6 fixes activation of client-provided MCP tools in ACP sessions ([upstream PR #1598](https://github.com/can1357/oh-my-pi/pull/1598)). Earlier versions can register those tools without making them available to the model. The `omp acp` subcommand itself was introduced in OMP 15.0.0, but that is not sufficient for MCP support.
- Hermes 0.21.4 declares Python `>=3.11,<3.14`; Hermes 0.22.x uses Python 3.14.

CI runs both test suites against the minimum combination, including real stdio MCP tool execution and disabled-server precedence. See [Contributing](../../CONTRIBUTING.md#native-omp-integration) for the integration command and executable override.

## Additional checks

Paid-provider authentication, live model behavior, and permission-dependent operations need separate checks against the relevant configured provider.

For the standalone-client refactor, additional local smoke checks confirmed that timed-out model selection does not execute a prompt and reaps its process, `close()` stops and reaps an active request, and a session-scoped gateway working directory takes precedence over process cwd. These are local smoke results, not claims of Slack approval or end-to-end write support.

For MCP-specific manual checks, keep the model/profile environment isolated and explicitly request a read-only tool operation. Verify both the returned tool result and server-side behavior, including command-backed headers where applicable. HTTP/SSE connections, authentication renewal, and real browser integration need checks against the relevant server; a successful model listing or configuration conversion alone is insufficient. Review [MCP compatibility limits](limitations.md#mcp-configuration-compatibility) before interpreting those results.
