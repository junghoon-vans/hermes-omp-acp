"""JSON-RPC peer with a separate control channel for stdio EOF ordering."""

import json
import os
import signal
import socket
import sys


def main():
    mode, port = sys.argv[1:]
    for line in sys.stdin:
        request = json.loads(line)
        method = request["method"]
        if method == "initialize":
            result = {"protocolVersion": request["params"]["protocolVersion"], "agentCapabilities": {}}
        elif method == "session/new":
            result = {"sessionId": "eof-diagnostics", "configOptions": []}
        elif method == "session/prompt":
            with socket.create_connection(("127.0.0.1", int(port)), timeout=5) as control:
                if mode.startswith("exit-"):
                    # Cleanup must not turn the intended exit 17 into SIGTERM
                    # before the controller releases this explicit EOF window.
                    signal.signal(signal.SIGTERM, signal.SIG_IGN)
                payload = {
                    "exit-safe": b"safe diagnostic after prompt\n",
                    "exit-secrets": b"safe diagnostic after prompt\nAPI_KEY=fixture-eof-secret-123456789\n",
                    "exit-huge-newline": b"API_KEY=fixture-eof-secret-123456789\n" + b"x" * (1024 * 1024) + b"\nEOF-OVERSIZED-SECRET\n",
                    "exit-huge-no-newline": b"API_KEY=fixture-eof-secret-123456789" + b"x" * (1024 * 1024) + b"EOF-OVERSIZED-SECRET",
                }.get(mode, b"")
                view = memoryview(payload)
                while view:
                    view = view[os.write(2, view):]
                os.close(1)
                if mode != "stdout-only":
                    os.close(2)
                # Receipt proves all writes completed and selected pipes closed;
                # no inference from a sleep or a marker written before EOF.
                control.sendall(b"E")
                if control.recv(1) == b"X":
                    control.sendall(b"X")
                    os._exit(17)
                return
        else:
            continue
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
