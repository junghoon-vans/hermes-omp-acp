"""Small real JSON-RPC subprocess peer for structured-error regressions."""

import json
import os
from pathlib import Path
import signal
import sys
import time


def _reply(request, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}) + "\n")
    sys.stdout.flush()


def _error(request, code, message):
    sys.stdout.write(json.dumps({
        "jsonrpc": "2.0",
        "id": request["id"],
        "error": {"code": code, "message": message},
    }) + "\n")
    sys.stdout.flush()


def _wait_forever():
    while True:
        time.sleep(1)


def main():
    mode = sys.argv[1]
    (Path.cwd() / "error-peer.pid").write_text(str(os.getpid()), encoding="ascii")
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    for line in sys.stdin:
        request = json.loads(line)
        method = request["method"]
        if method == "initialize":
            if mode == "slow-initialize":
                time.sleep(10)
            _reply(request, {
                "protocolVersion": request["params"]["protocolVersion"],
                "agentCapabilities": {},
            })
        elif method == "session/new":
            _reply(request, {"sessionId": "error-peer", "configOptions": []})
        elif method == "session/set_model":
            _reply(request, {})
        elif method == "session/prompt":
            receipt = Path.cwd() / "error-peer.prompt-count"
            with receipt.open("a", encoding="ascii") as marker:
                marker.write("1\n")
            if mode == "rpc-error":
                _error(request, -32017, "fixture prompt rejection")
            _wait_forever()
        else:
            break


if __name__ == "__main__":
    main()
