from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any

from mycode.hook.models import (
    HookActionResult,
    HookActionType,
    HookContext,
    HookRule,
)


logger = logging.getLogger(__name__)

_DEFAULT_COMMAND_TIMEOUT = 10.0
_DEFAULT_HTTP_TIMEOUT = 10.0


class HookActionRunner:
    def __init__(
        self,
        *,
        workspace_root: Path,
        http_client_factory: Callable[[], AbstractAsyncContextManager[Any]] | None = None,
    ) -> None:
        self._workspace_root = workspace_root
        self._http_client_factory = http_client_factory
        self._background_tasks: set[asyncio.Task[HookActionResult]] = set()

    async def run(
        self,
        rule: HookRule,
        context: HookContext | None,
    ) -> HookActionResult:
        if rule.background:
            task = asyncio.create_task(self._run_foreground(rule, context))
            self._background_tasks.add(task)
            task.add_done_callback(lambda completed: self._consume_background(rule, completed))
            return HookActionResult(ok=True, output="background task scheduled")
        return await self._run_foreground(rule, context)

    async def _run_foreground(
        self,
        rule: HookRule,
        context: HookContext | None,
    ) -> HookActionResult:
        try:
            action_type = rule.action.type
            if action_type is HookActionType.COMMAND:
                return await self._run_command(rule)
            if action_type is HookActionType.PROMPT:
                return HookActionResult(ok=True, output=rule.action.content or "")
            if action_type is HookActionType.HTTP:
                return await self._run_http(rule)
            if action_type is HookActionType.SUB_AGENT:
                logger.info("Hook 子 Agent 占位：rule=%s，event=%s", rule.id, rule.event.value)
                return HookActionResult(
                    ok=False,
                    output=f"sub_agent placeholder: {rule.action.task or ''}",
                    error="sub_agent 动作本阶段不支持真实执行。",
                )
            return HookActionResult(ok=False, error=f"unsupported hook action: {action_type}")
        except Exception as exc:
            logger.warning(
                "Hook 动作执行失败：rule=%s，event=%s，reason=%s",
                rule.id,
                rule.event.value,
                _safe_error(exc),
            )
            return HookActionResult(ok=False, error=_safe_error(exc))

    async def _run_command(self, rule: HookRule) -> HookActionResult:
        command = rule.action.command or ""
        cwd = self._resolve_cwd(rule.action.cwd)
        env = os.environ.copy()
        env.update(rule.action.env)
        timeout = rule.timeout_seconds or _DEFAULT_COMMAND_TIMEOUT
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=str(cwd),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                process.kill()
                await process.communicate()
                logger.warning(
                    "Hook 命令超时：rule=%s，event=%s，timeout=%s",
                    rule.id,
                    rule.event.value,
                    timeout,
                )
                return HookActionResult(
                    ok=False,
                    output="",
                    error=f"command timeout after {timeout} seconds",
                )
        except Exception as exc:
            return HookActionResult(ok=False, error=_safe_error(exc))

        output = _decode(stdout) + _decode(stderr)
        if process.returncode == 0:
            return HookActionResult(ok=True, output=output)
        logger.warning(
            "Hook 命令失败：rule=%s，event=%s，exit_code=%s",
            rule.id,
            rule.event.value,
            process.returncode,
        )
        return HookActionResult(
            ok=False,
            output=output,
            error=f"command exited with exit code {process.returncode}",
        )

    async def _run_http(self, rule: HookRule) -> HookActionResult:
        factory = self._http_client_factory or _default_http_client_factory
        timeout = rule.timeout_seconds or _DEFAULT_HTTP_TIMEOUT
        try:
            async with factory() as client:
                response = await client.request(
                    rule.action.method or "POST",
                    rule.action.url or "",
                    headers=dict(rule.action.headers),
                    json=rule.action.json_body,
                    timeout=timeout,
                )
        except Exception as exc:
            logger.warning(
                "Hook HTTP 请求失败：rule=%s，event=%s，reason=%s",
                rule.id,
                rule.event.value,
                _safe_error(exc),
            )
            return HookActionResult(ok=False, error=_safe_error(exc))

        status_code = getattr(response, "status_code", None)
        if isinstance(status_code, int) and 200 <= status_code < 300:
            return HookActionResult(ok=True, output=f"HTTP {status_code}")
        logger.warning(
            "Hook HTTP 非成功响应：rule=%s，event=%s，status=%s",
            rule.id,
            rule.event.value,
            status_code,
        )
        return HookActionResult(ok=False, error=f"HTTP status {status_code}")

    def _consume_background(
        self,
        rule: HookRule,
        task: asyncio.Task[HookActionResult],
    ) -> None:
        self._background_tasks.discard(task)
        try:
            result = task.result()
        except asyncio.CancelledError:
            logger.warning(
                "Hook 后台任务取消：rule=%s，event=%s",
                rule.id,
                rule.event.value,
            )
            return
        except Exception as exc:
            # 后台任务异常必须被消费，否则事件循环会报告未观察异常。
            logger.warning(
                "Hook 后台任务异常：rule=%s，event=%s，reason=%s",
                rule.id,
                rule.event.value,
                _safe_error(exc),
            )
            return
        if not result.ok:
            logger.warning(
                "Hook 后台任务失败：rule=%s，event=%s，reason=%s",
                rule.id,
                rule.event.value,
                result.error or "unknown",
            )

    def _resolve_cwd(self, cwd: str | None) -> Path:
        if cwd is None:
            return self._workspace_root
        path = Path(cwd)
        if path.is_absolute():
            return path
        return self._workspace_root / path


def _default_http_client_factory():
    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError("httpx is required for HTTP hook actions") from exc
    return httpx.AsyncClient(timeout=None)


def _decode(value: bytes | None) -> str:
    return (value or b"").decode("utf-8", errors="replace")


def _safe_error(exc: BaseException) -> str:
    message = str(exc)
    return message or exc.__class__.__name__
