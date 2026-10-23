# Verification

## Continuous integration

[CI](https://github.com/junghoon-vans/hermes-omp-acp/actions) runs the latest stable Hermes release and OMP's npm `latest` version, using Python 3.14 and the latest Bun runtime. It resolves the Hermes release to a commit SHA and records the source revision and executable versions in the logs.

CI builds and installs the wheel, then runs the ACP regression suite and native OMP integration suite. The native tests use a local OpenAI-compatible model endpoint to exercise provider discovery, model-picker availability, model selection, async responses, native file reading, and Hermes CLI output without external credentials.

Each integration scenario uses temporary Hermes and OMP homes. The model endpoint requests a file read, then produces its response from the value returned by the actual OMP tool execution.

## Minimum-version boundaries

The minimum versions are listed in [Requirements](../../model-provider/README.md#requirements).

- Hermes 0.21.4 adds generic external-process provider selection and setup. Earlier direct-provider runtime support alone does not cover the model picker. Its release tag is [`v2026.9.21`](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.9.21).
- OMP 15.0.0 adds the [`omp acp` subcommand](https://github.com/can1357/oh-my-pi/commit/275108974aa96f97931fca8353d4f517fd018b00). OMP 14.9.9 exposes the older `--mode acp` route.
- Hermes 0.21.4 declares Python `>=3.11,<3.14`; Hermes 0.22.x uses Python 3.14. The local minimum-combination checks ran with Python 3.11.15.

Minimum-version checks are run locally when changing compatibility-sensitive code. See [Contributing](../../CONTRIBUTING.md#native-omp-integration) for the integration command and executable override.

## Additional checks

Paid-provider authentication, live model behavior, and permission-dependent operations need separate checks against the relevant configured provider.
