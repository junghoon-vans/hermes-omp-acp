"""Real JSON-RPC subprocess peer for ACP cancellation lifecycle regressions."""

import json
import os
from pathlib import Path
import signal
import sys
import time


def _write(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def _reply(request, result):
    _write({"jsonrpc": "2.0", "id": request["id"], "result": result})


def _notify_text(session_id, text):
    _write({
        "jsonrpc": "2.0",
        "method": "session/update",
        "params": {
            "sessionId": session_id,
            "update": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": text},
            },
        },
    })


def _wait_forever():
    while True:
        time.sleep(1)


def _next_prompt_count():
    count_path = Path.cwd() / "cancel-peer.prompt-count"
    previous = count_path.read_text(encoding="ascii").splitlines() if count_path.exists() else []
    with count_path.open("a", encoding="ascii") as counter:
        counter.write("prompt\n")
    return len(previous) + 1


def main():
    mode = sys.argv[1]
    cwd = Path.cwd()
    (cwd / "cancel-peer.pid").write_text(str(os.getpid()), encoding="ascii")
    signal.signal(signal.SIGTERM, signal.SIG_IGN)

    for line in sys.stdin:
        request = json.loads(line)
        method = request["method"]
        if method == "initialize":
            if mode == "startup":
                (cwd / "cancel-peer.startup-ready").touch()
                _wait_forever()
            _reply(request, {
                "protocolVersion": request["params"]["protocolVersion"],
                "agentCapabilities": {},
            })
        elif method == "session/new":
            _reply(request, {
                "sessionId": "cancel-peer-session",
                "configOptions": [{
                    "id": "model",
                    "name": "Model",
                    "category": "model",
                    "type": "select",
                    "currentValue": "fixture/model",
                    "options": [{"value": "fixture/model", "name": "Fixture"}],
                }],
            })
        elif method == "session/set_config_option":
            if mode == "model-selection":
                (cwd / "cancel-peer.model-ready").touch()
                _wait_forever()
            _reply(request, {"configOptions": []})
        elif method == "session/prompt":
            prompt_count = _next_prompt_count()
            if prompt_count == 1:
                (cwd / "cancel-peer.prompt-ready").touch()
                if mode == "noncooperative":
                    _wait_forever()
                for message_line in sys.stdin:
                    message = json.loads(message_line)
                    if message.get("method") == "session/cancel":
                        (cwd / "cancel-peer.cancel-received").touch()
                        _reply(request, {"stopReason": "cancelled"})
                        break
            else:
                _notify_text("cancel-peer-session", "completion after cancellation")
                _reply(request, {"stopReason": "end_turn"})
        else:
            break


if __name__ == "__main__":
    main()
