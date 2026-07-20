from __future__ import annotations

import sys
from pathlib import Path


STDIO_SERVER_SOURCE = r'''
import json
import os
import sys
import time

for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    if method == "echo":
        response = {
            "jsonrpc": "2.0",
            "id": message["id"],
            "result": {
                "params": message.get("params", {}),
                "token": os.environ.get("MCP_TEST_TOKEN"),
            },
        }
        print(json.dumps(response), flush=True)
    elif method == "stderr":
        print("sensitive-stderr-value", file=sys.stderr, flush=True)
        print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": {}}), flush=True)
    elif method == "invalid":
        print("not-json", flush=True)
    elif method == "exit":
        sys.exit(0)
    elif method == "hang":
        time.sleep(60)
'''


def create_stdio_server(tmp_path: Path) -> tuple[str, tuple[str, ...]]:
    script = tmp_path / "controlled_mcp_stdio_server.py"
    script.write_text(STDIO_SERVER_SOURCE, encoding="utf-8")
    return sys.executable, (str(script),)
