# Verification

## Continuous integration

[CI](https://github.com/junghoon-vans/hermes-omp-acp/actions) checks the latest stable Hermes release with OMP's npm `latest` version on Python 3.14, and the minimum supported Hermes 0.21.4 (`v2026.9.21`) with OMP 15.0.0 on Python 3.11. Both jobs use the latest Bun runtime. CI resolves each Hermes release to a commit SHA and records the source revision and executable versions in the logs.

CI builds and installs the wheel, then runs the ACP regression suite and native OMP integration suite. The native tests use a local OpenAI-compatible model endpoint to exercise provider discovery, model-picker availability, model selection, async responses, native file reading, and Hermes CLI output without external credentials.

Each integration scenario uses temporary Hermes and OMP homes. The model endpoint requests a file read, then produces its response from the value returned by the actual OMP tool execution.

The standalone client's ACP peer regressions also exercise permission cancellation, safe reads/writes with read-back, secret redaction, rejected out-of-workspace and symlink access, protected writes, reasoning/tool-call conversion, model discovery from an async caller, and peer-exit diagnostics. These use actual subprocess connections through the SDK rather than mocked transport.

Lifecycle regressions use peer readiness gates to establish overlapping sessions before releasing or cancelling them. They verify that timeout or caller cancellation reaps only the affected child while a sibling still completes, and that `close()` stops and reaps all active children. Additional cases cover one timeout budget across initialization/session creation/model selection, and ordered text/reasoning bursts followed immediately by the prompt response. The tests use the public provider/client interface, not private module names or transport mocks.

## Minimum-version boundaries

The minimum versions are listed in [Requirements](../../README.md#requirements).

- Hermes 0.21.4 adds generic external-process provider selection and setup. Earlier direct-provider runtime support alone does not cover the model picker. Its release tag is [`v2026.9.21`](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.9.21).
- OMP 15.0.0 adds the [`omp acp` subcommand](https://github.com/can1357/oh-my-pi/commit/275108974aa96f97931fca8353d4f517fd018b00). OMP 14.9.9 exposes the older `--mode acp` route.
- Hermes 0.21.4 declares Python `>=3.11,<3.14`; Hermes 0.22.x uses Python 3.14. The local minimum-combination checks ran with Python 3.11.15.

CI runs both suites against the minimum combination. Local standalone-client verification also exercised Hermes 0.21.4 / Python 3.11.15 / OMP 15.0.0 and Hermes 0.22.5 / Python 3.14.6 / OMP 18.12.6. See [Contributing](../../CONTRIBUTING.md#native-omp-integration) for the integration command and executable override.

## Additional checks

Paid-provider authentication, live model behavior, and permission-dependent operations need separate checks against the relevant configured provider.

For the standalone-client refactor, additional local smoke checks confirmed that timed-out model selection does not execute a prompt and reaps its process, `close()` stops and reaps an active request, and a session-scoped gateway working directory takes precedence over process cwd. These are local smoke results, not claims of Slack approval or end-to-end write support.
