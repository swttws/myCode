from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import AsyncIterator
from typing import Mapping

from mycode.mcp.models import MCPServerConfig, MCPTransportKind
from mycode.mcp.transport import MCPTransportError


logger = logging.getLogger(__name__)


class StdioTransport:
    def __init__(
        self,
        config: MCPServerConfig,
        *,
        shutdown_timeout_seconds: float = 1.0,
    ) -> None:
        if config.transport is not MCPTransportKind.STDIO:
            raise ValueError("StdioTransport requires a stdio server config")
        self._config = config
        self._shutdown_timeout_seconds = shutdown_timeout_seconds
        self._process: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._close_lock = asyncio.Lock()

    @property
    def process(self) -> asyncio.subprocess.Process | None:
        return self._process

    @property
    def is_open(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def open(self) -> None:
        if self.is_open:
            raise MCPTransportError("already_open", "MCP stdio transport is already open")
        if not self._config.command:
            raise MCPTransportError("open_failed", "MCP stdio command is missing")

        environment = os.environ.copy()
        environment.update(self._config.env)
        try:
            process = await asyncio.create_subprocess_exec(
                self._config.command,
                *self._config.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=environment,
            )
        except (OSError, ValueError) as exc:
            raise MCPTransportError(
                "open_failed",
                f"unable to start MCP stdio server: {self._config.name}",
            ) from exc

        self._process = process
        self._stderr_task = asyncio.create_task(
            self._drain_stderr(process),
            name=f"mcp-stderr-{self._config.name}",
        )

    async def send(self, message: Mapping[str, object]) -> None:
        process = self._process
        if process is None or process.returncode is not None or process.stdin is None:
            raise MCPTransportError("not_open", "MCP stdio transport is not open")
        try:
            payload = json.dumps(dict(message), ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise MCPTransportError("invalid_message", "MCP message is not JSON serializable") from exc

        try:
            process.stdin.write(payload.encode("utf-8") + b"\n")
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionError, RuntimeError) as exc:
            raise MCPTransportError(
                "disconnected",
                f"MCP stdio server disconnected: {self._config.name}",
            ) from exc

    async def receive(self) -> AsyncIterator[dict[str, object]]:
        process = self._process
        if process is None or process.stdout is None:
            raise MCPTransportError("not_open", "MCP stdio transport is not open")

        while True:
            line = await process.stdout.readline()
            if not line:
                raise MCPTransportError(
                    "disconnected",
                    f"MCP stdio server disconnected: {self._config.name}",
                )
            try:
                message = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise MCPTransportError(
                    "invalid_json",
                    f"MCP stdio server returned invalid JSON: {self._config.name}",
                ) from exc
            if not isinstance(message, dict):
                raise MCPTransportError(
                    "invalid_json",
                    f"MCP stdio server returned a non-object message: {self._config.name}",
                )
            yield message

    async def close(self) -> None:
        async with self._close_lock:
            process = self._process
            if process is None:
                return

            if process.stdin is not None and not process.stdin.is_closing():
                process.stdin.close()
                with contextlib.suppress(BrokenPipeError, ConnectionError, RuntimeError):
                    await process.stdin.wait_closed()

            if process.returncode is None:
                try:
                    await asyncio.wait_for(
                        process.wait(), timeout=self._shutdown_timeout_seconds
                    )
                except asyncio.TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        process.terminate()
                    try:
                        await asyncio.wait_for(
                            process.wait(), timeout=self._shutdown_timeout_seconds
                        )
                    except asyncio.TimeoutError:
                        with contextlib.suppress(ProcessLookupError):
                            process.kill()
                        await process.wait()

            stderr_task = self._stderr_task
            if stderr_task is not None and not stderr_task.done():
                stderr_task.cancel()
            if stderr_task is not None:
                await asyncio.gather(stderr_task, return_exceptions=True)
            self._stderr_task = None

    async def _drain_stderr(self, process: asyncio.subprocess.Process) -> None:
        if process.stderr is None:
            return
        while True:
            line = await process.stderr.readline()
            if not line:
                return
            logger.debug("MCP stdio server wrote to stderr: %s", self._config.name)
