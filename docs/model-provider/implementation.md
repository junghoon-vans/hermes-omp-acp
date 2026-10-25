# How it works

- The `omp-acp-provider` plugin registers the `omp-acp` model provider through Hermes's external-process model-provider interface.
- Launches `omp acp` and discovers its available models through ACP.
- Implements `OmpACPClient` independently using the official `agent-client-protocol` Python SDK for subprocess transport and JSON-RPC.
- Reuses Hermes's generic OpenAI completion/tool-call bridge, subprocess environment handling, filesystem safety checks, secret redaction, and logical workspace resolution. It does not import or subclass the Copilot ACP client.

## Module responsibilities

- [`client.py`](../../hermes_omp_acp/client.py) exposes the Hermes-compatible client and adapts synchronous and asynchronous callers.
- [`_bridge.py`](../../hermes_omp_acp/_bridge.py) converts messages and tool schemas into prompts, then converts text and reasoning into completion objects and buffered stream chunks.
- [`_session.py`](../../hermes_omp_acp/_session.py) owns ACP negotiation, callbacks and filesystem policy, subprocesses, stderr, deadlines, and cancellation.
- [`mcp.py`](../../hermes_omp_acp/mcp.py) translates the global OMP MCP configuration into ACP server definitions; `_session.py` sends them when creating a completion session.

The client calls the runtime's `list_models`, `run`, and `close` interfaces. SDK connections, child processes, callback buffers, and stderr tasks stay inside the runtime rather than crossing the interface as a tuple. A single runtime owns the active-session tracking for each client.

## Runtime behavior

Each completion opens a new OMP process and ACP session, sending the conversation history with the request. Responses are buffered before being returned as completion objects or stream chunks.

When a model is explicitly requested, the client selects it in that same session before sending the prompt, using `session/set_config_option` or the legacy `session/set_model` method. Unavailable models, selection errors, and returned model mismatches abort the completion instead of falling back to OMP's default. When no current model is returned, the successful selection RPC is the acknowledgement. Omitting the model allows the session default.

Synchronous completions run the SDK on a local event loop; asynchronous completions use the caller's event loop. Model discovery retains Hermes's synchronous interface and uses a worker thread when called from an existing event loop. That worker preserves the caller's context.

Incoming message and reasoning chunks are collected in wire order before the prompt response is returned, independently of the SDK's background callback scheduling. Streaming remains buffered rather than live.

One timeout budget covers initialization, session creation, model selection, and prompting. Requests also monitor child-process exit so a dead peer reports its failure instead of waiting for the response deadline. Child shutdown can take additional time. Each session tracks its own process and response buffers; `close()` stops active requests and their processes.

The client uses an explicit session workspace when supplied, otherwise Hermes's logical working-directory resolver. Model discovery disables filesystem callbacks, skips MCP configuration loading, and sends an empty `mcpServers` list. Completion sessions forward the supported global OMP MCP definitions as described below.

Native ACP permission requests are cancelled by the plugin's own callback, preserving the previous behavior. See [Known limitations](limitations.md#native-acp-approval-requests) for the permission and filesystem policies.

Compatibility still depends on Hermes's shared provider, bridge, environment, and safety interfaces, plus the SDK version range declared in [package metadata](../../pyproject.toml). It no longer depends on the internal `CopilotACPClient` interface. See [Verification](verification.md) for the exercised combinations and scenarios.

## MCP configuration flow

1. Before spawning OMP for a completion, read `mcp.json` from `PI_CODING_AGENT_DIR`, or `~/.omp/agent` when the variable is unset or empty. Resolve `~` in the directory. No project or third-party discovery is performed.
2. Select active entries from `mcpServers`: `disabledServers` wins, then `enabledServers` can override an entry's `enabled: false`. Other entries are enabled by default.
3. Convert the selected definitions to ACP transport records and resolve stdio commands to absolute paths. A missing file produces an empty list. Invalid configuration or an unresolved active command aborts before OMP is spawned.
4. Initialize the ACP connection and check the agent's HTTP/SSE capabilities before sending definitions that use those transports.
5. Send the list in `session/new.mcpServers`. OMP owns MCP connections, tool execution, and runtime resolution of command-backed environment/header values. The provider does not execute `!command` expressions or copy credentials into Hermes configuration.
6. Select the requested model, then send the conversation prompt. The standalone runtime's existing cancellation policy still applies to native ACP permission requests.

OMP's ACP mode does not automatically discover its on-disk MCP servers. Forwarding the definitions supplies them without creating a separate provider-side MCP connection manager or a second maintained server list.

Model discovery uses an empty MCP list and skips configuration loading. Each completion rereads the file; the provider does not watch it or retain MCP connections across completions. Missing files and an empty active list are allowed, while a malformed existing file fails the completion even if model discovery succeeds.

The native file has more options than the ACP transport records and OMP's mapper preserve. In OMP 18.12.6, the ACP handler constructs fresh internal server configs from standard fields, so raw extra keys and `_meta` do not restore server-specific options. See [MCP configuration compatibility](limitations.md#mcp-configuration-compatibility) for the field/version matrix and warning behavior; this is not full-fidelity export of OMP's configuration.

OMP 18.12.6 additionally connects its ACP MCP manager to the session's auth storage, enabling conditional reuse of existing URL-keyed OAuth credentials without the provider forwarding `auth`/`oauth` objects. This feature is not available at the package's OMP 15.7.6 minimum. Process-wide `OMP_MCP_TIMEOUT_MS` works through OMP's own environment in both versions, independently of the omitted per-server `timeout`.

The ACP manager is separate from the SDK session's MCP manager, which is disabled to avoid on-disk discovery. In the source-reviewed OMP 18.12.6 wiring, refreshing tools does not connect the ACP manager's server instructions to the SDK's system-prompt provider. Therefore, omitted `instructions: false` must not be described as enabling server instructions. These upstream source findings and their runtime-verification limits are recorded in [Verification](verification.md#source-reviewed-mcp-behavior).
