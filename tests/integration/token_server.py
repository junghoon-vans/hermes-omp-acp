"""Read a fresh token through a real stdio MCP connection."""

import json
from pathlib import Path
import sys


for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    method = request["method"]
    if method == "initialize":
        result = {
            "protocolVersion": request["params"]["protocolVersion"],
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "token-probe", "version": "1"},
        }
    elif method == "tools/list":
        result = {"tools": [{
            "name": "token", "description": "Read the private integration token.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "annotations": {"readOnlyHint": True},
        }]}
    elif method == "tools/call":
        if request["params"]["name"] != "token":
            raise ValueError("Unknown tool")
        result = {"content": [{"type": "text", "text": Path(sys.argv[1]).read_text().strip()}]}
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": request["id"],
                          "error": {"code": -32601, "message": "Unknown method"}}), flush=True)
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
