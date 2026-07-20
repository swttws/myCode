from __future__ import annotations

import sys
import json
import queue
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import asyncio


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


class ControlledMCPHTTPServer(ThreadingHTTPServer):
    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), ControlledMCPHTTPHandler)
        self.requests: queue.Queue[dict[str, object]] = queue.Queue()

    @property
    def url(self) -> str:
        host, port = self.server_address
        return f"http://{host}:{port}/mcp"


class ControlledMCPHTTPHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        message = json.loads(self.rfile.read(length))
        self.server.requests.put(
            {"method": "POST", "headers": dict(self.headers), "message": message}
        )
        method = message.get("method")

        if method == "test/http_error":
            self._write_response(500, "text/plain", b"sensitive-response-body")
            return
        if method == "test/invalid_content_type":
            self._write_response(200, "text/plain", b"not-supported")
            return
        if "id" not in message:
            self._write_response(202, None, b"")
            return

        response = {"jsonrpc": "2.0", "id": message["id"], "result": {"ok": True}}
        if method == "initialize":
            response["result"] = {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "controlled", "version": "1"},
            }
            self._write_response(
                200,
                "application/json",
                json.dumps(response).encode("utf-8"),
                extra_headers={"MCP-Session-Id": "session-123"},
            )
            return
        if method == "test/sse":
            notification = {
                "jsonrpc": "2.0",
                "method": "notifications/tools/list_changed",
                "params": {},
            }
            body = (
                ": comment\n\n"
                f"data: {json.dumps(response)}\n\n"
                f"data: {json.dumps(notification)}\n\n"
            ).encode("utf-8")
            self._write_response(200, "text/event-stream", body)
            return

        self._write_response(200, "application/json", json.dumps(response).encode("utf-8"))

    def do_GET(self) -> None:
        self.server.requests.put({"method": "GET", "headers": dict(self.headers)})
        notification = {
            "jsonrpc": "2.0",
            "method": "notifications/progress",
            "params": {"progress": 1},
        }
        body = f"data: {json.dumps(notification)}\n\n".encode("utf-8")
        self._write_response(200, "text/event-stream", body)

    def log_message(self, format: str, *args) -> None:
        return

    def _write_response(
        self,
        status: int,
        content_type: str | None,
        body: bytes,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        if content_type is not None:
            self.send_header("Content-Type", content_type)
        if extra_headers:
            for name, value in extra_headers.items():
                self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)


@contextmanager
def run_http_server():
    server = ControlledMCPHTTPServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class MemoryMCPTransport:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[object] = asyncio.Queue()
        self.sent: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        self.open_count = 0
        self.close_count = 0
        self.is_open = False
        self.protocol_version: str | None = None

    async def open(self) -> None:
        self.open_count += 1
        self.is_open = True

    async def send(self, message) -> None:
        if not self.is_open:
            raise RuntimeError("memory transport is closed")
        await self.sent.put(dict(message))

    async def receive(self):
        while self.is_open:
            item = await self.incoming.get()
            if isinstance(item, BaseException):
                raise item
            if item is None:
                return
            yield item

    async def close(self) -> None:
        self.close_count += 1
        self.is_open = False
        await self.incoming.put(None)

    def set_protocol_version(self, version: str) -> None:
        self.protocol_version = version

    async def next_sent(self) -> dict[str, object]:
        return await asyncio.wait_for(self.sent.get(), timeout=1)

    async def push(self, message: object) -> None:
        await self.incoming.put(message)
