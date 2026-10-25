# How it works

- The `omp-acp-provider` plugin registers the `omp-acp` model provider through Hermes's external-process model-provider interface.
- Launches `omp acp` and discovers its available models through ACP.
- Implements `OmpACPClient` independently using the official `agent-client-protocol` Python SDK for subprocess transport and JSON-RPC.
- Reuses Hermes's generic OpenAI completion/tool-call bridge, subprocess environment handling, filesystem safety checks, secret redaction, and logical workspace resolution. It does not import or subclass the Copilot ACP client.

## Module responsibilities

- [`client.py`](../../hermes_omp_acp/client.py) exposes the Hermes-compatible client and adapts synchronous and asynchronous callers.
- [`_bridge.py`](../../hermes_omp_acp/_bridge.py) converts messages and tool schemas into prompts, then converts text and reasoning into completion objects and buffered stream chunks.
- [`_session.py`](../../hermes_omp_acp/_session.py) owns ACP negotiation, callbacks and filesystem policy, subprocesses, stderr, deadlines, and cancellation.

The client calls the runtime's `list_models`, `run`, and `close` interfaces. SDK connections, child processes, callback buffers, and stderr tasks stay inside the runtime rather than crossing the interface as a tuple. A single runtime owns the active-session tracking for each client.

## Runtime behavior

Each completion opens a new OMP process and ACP session, sending the conversation history with the request. Responses are buffered before being returned as completion objects or stream chunks.

When a model is explicitly requested, the client selects it in that same session before sending the prompt, using `session/set_config_option` or the legacy `session/set_model` method. Unavailable models, selection errors, and returned model mismatches abort the completion instead of falling back to OMP's default. When no current model is returned, the successful selection RPC is the acknowledgement. Omitting the model allows the session default.

Synchronous completions run the SDK on a local event loop; asynchronous completions use the caller's event loop. Model discovery retains Hermes's synchronous interface and uses a worker thread when called from an existing event loop. That worker preserves the caller's context.

Incoming message and reasoning chunks are collected in wire order before the prompt response is returned, independently of the SDK's background callback scheduling. Streaming remains buffered rather than live.

One timeout budget covers initialization, session creation, model selection, and prompting. Requests also monitor child-process exit so a dead peer reports its failure instead of waiting for the response deadline. Child shutdown can take additional time. Each session tracks its own process and response buffers; `close()` stops active requests and their processes.

The client uses an explicit session workspace when supplied, otherwise Hermes's logical working-directory resolver. Model discovery disables filesystem callbacks. Sessions send an empty `mcpServers` list; this implementation does not forward OMP's on-disk MCP configuration.

Native ACP permission requests are cancelled by the plugin's own callback, preserving the previous behavior. See [Known limitations](limitations.md#native-acp-approval-requests) for the permission and filesystem policies.

Compatibility still depends on Hermes's shared provider, bridge, environment, and safety interfaces, plus the SDK version range declared in [package metadata](../../pyproject.toml). It no longer depends on the internal `CopilotACPClient` interface. See [Verification](verification.md) for the exercised combinations and scenarios.
