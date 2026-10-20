# How it works

- Registers the `omp-acp` provider through Hermes's external-process model-provider interface.
- Launches `omp acp` and discovers its available models through ACP.
- Reuses Hermes's `CopilotACPClient` for transport, message conversion, and tool-call bridging.
- Adapts the synchronous client for Hermes's async callers by running requests in a worker thread.

## Runtime behavior

Each completion opens a new OMP process and ACP session, sending the conversation history with the request. Responses are buffered before being returned as completion objects or stream chunks.

Native ACP permission requests receive a `cancelled` response from the inherited Hermes client.

The plugin depends on Hermes's internal `CopilotACPClient` API. Upstream compatibility is checked through the scenarios described in [Verification](verification.md).
