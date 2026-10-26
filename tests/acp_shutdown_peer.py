"""Subprocess peer for bounded-shutdown regressions."""

import json
import os
from pathlib import Path
import signal
import sys
import time


def _reply(request, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}) + "\n")
    sys.stdout.flush()


def main():
    mode = sys.argv[1]
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    for line in sys.stdin:
        request = json.loads(line)
        if request["method"] == "initialize":
            _reply(request, {
                "protocolVersion": request["params"]["protocolVersion"],
                "agentCapabilities": {},
            })
        elif request["method"] == "session/new":
            _reply(request, {"sessionId": "shutdown-peer", "configOptions": []})
            (Path.cwd() / "shutdown-peer.pid").write_text(str(os.getpid()), encoding="ascii")
            if mode == "closed-stdin":
                os.close(sys.stdin.fileno())
            while True:
                time.sleep(1)
        else:
            break


if __name__ == "__main__":
    main()
