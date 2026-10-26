import json
import os
from pathlib import Path
import sys
import time

from acp import PROTOCOL_VERSION


def send(message):
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def respond(request_id, result):
    send({"jsonrpc": "2.0", "id": request_id, "result": result})


def main():
    mode = sys.argv[1]
    for line in sys.stdin:
        request = json.loads(line)
        method = request.get("method")
        request_id = request.get("id")

        if method == "initialize":
            respond(request_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "agentCapabilities": {},
            })
        elif method == "session/new":
            respond(request_id, {
                "sessionId": "fixture",
                "models": {
                    "currentModelId": "fixture/model",
                    "availableModels": [{"modelId": "fixture/model", "name": "Fixture"}],
                },
            })
        elif method == "session/set_model":
            respond(request_id, {})
        elif method == "session/prompt":
            if mode == "eof-live":
                (Path.cwd() / "sdk-compat-eof.ready").write_text(str(os.getpid()), encoding="ascii")
                os.close(sys.stdout.fileno())
                while True:
                    time.sleep(1)
            if mode == "exit-after-response":
                (Path.cwd() / "sdk-compat-exit.ready").write_text(str(os.getpid()), encoding="ascii")
                send({
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": "fixture",
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {"type": "text", "text": "completed response"},
                        },
                    },
                })
                respond(request_id, {"stopReason": "end_turn"})
                os._exit(0)


if __name__ == "__main__":
    main()
