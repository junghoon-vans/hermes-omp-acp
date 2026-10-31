"""Raw JSON-RPC subprocess peer for stderr and framing diagnostics tests."""

import json
import os
from pathlib import Path
import sys
import time


def _reply(request, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}) + "\n")
    sys.stdout.flush()


def _error(request, code, message, data=None):
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request["id"], "error": error}) + "\n")
    sys.stdout.flush()


def _write_stderr(parts):
    for part in parts:
        view = memoryview(part)
        while view:
            written = os.write(2, view)
            view = view[written:]


def _emit_oversized_stderr(mode):
    header = b"API_KEY=fixture-overflow-prefix-123456789"
    filler = b"x" * (1024 * 1024)
    tail = b"OVERSIZED-DIAGNOSTIC-SENTINEL-OVERFLOW-CONTEXT-TAIL-SECRET-99887766"
    if mode == "oversized-newline":
        payload = header + b"\n" + filler + b"\n" + tail + b"\n"
    else:
        payload = header + filler + tail
    cut = len(payload) // 2
    _write_stderr((payload[:cut], payload[cut:]))
    # This receipt is deliberately written only after every diagnostic byte.
    (Path.cwd() / "diagnostic-peer-complete").write_text("complete", encoding="ascii")
    os._exit(17)


def _emit_bounded_stderr():
    _write_stderr((b"safe bounded diagnostic: child detail\n",))
    (Path.cwd() / "diagnostic-peer-complete").write_text("complete", encoding="ascii")
    os._exit(17)


def _emit_split_secrets():
    _write_stderr((
        # Force the key/value across the drainer's 4096-byte read boundary,
        # even if the OS coalesces these writes before the parent is scheduled.
        b"safe diagnostic line\n"
        + b" " * (4096 - len(b"safe diagnostic line\nAPI_"))
        + b"API_",
        b"KEY=fixture-secret-value-",
        b"123456789\n sk-abc\n",
        b"mnopqrstuvwxyz123456\n",
    ))
    (Path.cwd() / "diagnostic-peer-complete").write_text("complete", encoding="ascii")
    os._exit(17)


def _emit_utf8_stderr():
    for part in (
        b"safe UTF-8 diagnostic: caf\xc3",
        b"\xa9 and snowman \xe2\x98",
        b"\x83\n",
    ):
        _write_stderr((part,))
        time.sleep(0.05)
    (Path.cwd() / "diagnostic-peer-complete").write_text("complete", encoding="ascii")
    os._exit(17)


def _emit_overlong_frame(request, mode):
    frame = json.dumps({
        "jsonrpc": "2.0",
        "id": request["id"],
        "result": {"padding": "FRAME-PAYLOAD-SENTINEL" + "x" * 4096},
    }).encode("utf-8")
    if mode == "overlong-newline":
        frame += b"\n"
    sys.stdout.buffer.write(frame)
    sys.stdout.buffer.flush()
    (Path.cwd() / "diagnostic-frame-complete").write_text("complete", encoding="ascii")
    time.sleep(10)


def _emit_secret_rpc_error(request):
    api_secret = "fixture-secret-value-123456789"
    token = "sk-abc\nmnopqrstuvwxyz123456"
    _error(
        request,
        -32023,
        f"API_KEY={api_secret}\n{token}",
        {
            "api_key": api_secret,
            "access_token": token,
            "payload": "payload-only-secret-99887766",
        },
    )


def _emit_prompt_timeout():
    time.sleep(10)


def main():
    mode = sys.argv[1]
    for line in sys.stdin:
        request = json.loads(line)
        method = request["method"]
        if method == "initialize":
            _reply(request, {
                "protocolVersion": request["params"]["protocolVersion"],
                "agentCapabilities": {},
            })
        elif method == "session/new" and mode == "catalog-rpc-secret":
            _emit_secret_rpc_error(request)
        elif method == "session/new":
            _reply(request, {"sessionId": "diagnostic-peer", "configOptions": []})
        elif method == "session/prompt" and mode.startswith("oversized-"):
            _emit_oversized_stderr(mode)
        elif method == "session/prompt" and mode == "bounded-stderr":
            _emit_bounded_stderr()
        elif method == "session/prompt" and mode == "empty-stderr":
            os._exit(17)
        elif method == "session/prompt" and mode == "split-secrets":
            _emit_split_secrets()
        elif method == "session/prompt" and mode == "utf8-stderr":
            _emit_utf8_stderr()
        elif method == "session/prompt" and mode.startswith("overlong-"):
            _emit_overlong_frame(request, mode)
        elif method == "session/prompt" and mode == "wait-for-timeout":
            _emit_prompt_timeout()
        elif method == "session/prompt" and mode == "rpc-secret":
            _emit_secret_rpc_error(request)
        else:
            break


if __name__ == "__main__":
    main()
