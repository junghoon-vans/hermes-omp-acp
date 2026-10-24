"""Local OpenAI-compatible peer exercising real OMP file tools."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import threading


class ModelEndpoint:
    def __init__(self, probe, replacement=None):
        self.requests = []
        self.errors = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.requests.append(request)
                tools = [message for message in request["messages"] if message["role"] == "tool"]
                if replacement is not None and len(tools) < 3:
                    step = len(tools)
                    arguments = {"path": str(probe)}
                    if step == 1:
                        arguments["content"] = replacement + "\n"
                    delta = {"tool_calls": [{
                        "index": 0, "id": f"workspace-step-{step}", "type": "function",
                        "function": {"name": "write" if step == 1 else "read",
                                     "arguments": json.dumps(arguments)},
                    }]}
                    finish = "tool_calls"
                elif tools:
                    match = re.search(r"NATIVE_READ_[a-f0-9]{16}", json.dumps(tools[-1]))
                    if match is None:
                        owner.errors.append("The model did not receive the native read tool's file contents")
                        self.send_error(400, owner.errors[-1])
                        return
                    delta = {"content": match.group()}
                    finish = "stop"
                else:
                    delta = {"tool_calls": [{
                        "index": 0, "id": "read-probe", "type": "function",
                        "function": {"name": "read", "arguments": json.dumps({"path": str(probe)})},
                    }]}
                    finish = "tool_calls"
                chunks = [
                    {"choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]},
                    {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                    {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                     "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25}},
                ]
                payload = "".join("data: " + json.dumps({
                    "id": "fixture-completion", "object": "chat.completion.chunk", "created": 0,
                    "model": request["model"], **chunk,
                }) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
                encoded = payload.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, format, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}/v1"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
