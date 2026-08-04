from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from mycode.hook.actions import HookActionRunner
from mycode.hook.models import (
    HookAction,
    HookActionType,
    HookContext,
    HookEvent,
    HookRule,
)


def python_command(code: str) -> str:
    escaped = code.replace('"', '\\"')
    return f'"{sys.executable}" -c "{escaped}"'


def rule(
    action: HookAction,
    *,
    background: bool = False,
    timeout_seconds: float | None = None,
    rule_id: str = "rule-1",
) -> HookRule:
    return HookRule(
        id=rule_id,
        event=HookEvent.MODEL_ROUND_START,
        condition=None,
        action=action,
        once=False,
        background=background,
        timeout_seconds=timeout_seconds,
        index=0,
    )


class FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class FakeHTTPClient:
    def __init__(self, request_log: list[dict[str, object]], *, status_code: int = 200, error: Exception | None = None):
        self._request_log = request_log
        self._status_code = status_code
        self._error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def request(self, method, url, *, headers=None, json=None, timeout=None):
        self._request_log.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers or {}),
                "json": json,
                "timeout": timeout,
            }
        )
        if self._error is not None:
            raise self._error
        return FakeResponse(self._status_code)


def test_command_action_returns_stdout_from_workspace(tmp_path: Path) -> None:
    runner = HookActionRunner(workspace_root=tmp_path)
    action = HookAction(
        type=HookActionType.COMMAND,
        command=python_command("from pathlib import Path; print(Path.cwd())"),
    )

    result = asyncio.run(runner.run(rule(action), None))

    assert result.ok is True
    assert result.output.strip() == str(tmp_path)
    assert result.error is None


def test_command_action_uses_context_workspace_for_cwd(tmp_path: Path) -> None:
    configured_root = tmp_path / "configured"
    context_root = tmp_path / "context"
    configured_root.mkdir()
    context_root.mkdir()
    runner = HookActionRunner(workspace_root=configured_root)
    action = HookAction(
        type=HookActionType.COMMAND,
        command=python_command("from pathlib import Path; print(Path.cwd())"),
    )
    hook_context = HookContext(event=HookEvent.MODEL_ROUND_START, workspace_root=context_root)

    result = asyncio.run(runner.run(rule(action), hook_context))

    assert result.ok is True
    assert result.output.strip() == str(context_root)


def test_command_action_rejects_context_cwd_outside_workspace(tmp_path: Path) -> None:
    runner = HookActionRunner(workspace_root=tmp_path)
    action = HookAction(
        type=HookActionType.COMMAND,
        command=python_command("print('nope')"),
        cwd="../outside",
    )
    hook_context = HookContext(event=HookEvent.MODEL_ROUND_START, workspace_root=tmp_path)

    result = asyncio.run(runner.run(rule(action), hook_context))

    assert result.ok is False
    assert "工作区" in (result.error or "")


def test_command_action_reports_nonzero_exit_without_throwing(tmp_path: Path) -> None:
    runner = HookActionRunner(workspace_root=tmp_path)
    action = HookAction(
        type=HookActionType.COMMAND,
        command=python_command("import sys; print('bad', file=sys.stderr); sys.exit(3)"),
    )

    result = asyncio.run(runner.run(rule(action), None))

    assert result.ok is False
    assert "exit code 3" in (result.error or "")
    assert "bad" in result.output


def test_command_action_timeout_is_reported_without_throwing(tmp_path: Path) -> None:
    runner = HookActionRunner(workspace_root=tmp_path)
    action = HookAction(
        type=HookActionType.COMMAND,
        command=python_command("import time; time.sleep(2)"),
    )

    result = asyncio.run(runner.run(rule(action, timeout_seconds=0.1), None))

    assert result.ok is False
    assert "timeout" in (result.error or "")


def test_background_command_returns_immediately_and_consumes_failure(
    tmp_path: Path,
    caplog,
) -> None:
    async def run_background() -> None:
        runner = HookActionRunner(workspace_root=tmp_path)
        action = HookAction(
            type=HookActionType.COMMAND,
            command=python_command("import sys; sys.exit(7)"),
        )
        with caplog.at_level(logging.WARNING):
            result = await runner.run(rule(action, background=True), None)
            await asyncio.sleep(0.2)
        assert result.ok is True
        assert "background" in result.output

    asyncio.run(run_background())

    assert "rule-bg" not in caplog.text
    assert "rule-1" in caplog.text


def test_prompt_action_returns_content(tmp_path: Path) -> None:
    runner = HookActionRunner(workspace_root=tmp_path)
    action = HookAction(type=HookActionType.PROMPT, content="remember tests")

    result = asyncio.run(runner.run(rule(action), None))

    assert result.ok is True
    assert result.output == "remember tests"


def test_http_action_sends_method_url_headers_and_json(tmp_path: Path) -> None:
    request_log: list[dict[str, object]] = []
    runner = HookActionRunner(
        workspace_root=tmp_path,
        http_client_factory=lambda: FakeHTTPClient(request_log),
    )
    action = HookAction(
        type=HookActionType.HTTP,
        method="PUT",
        url="http://127.0.0.1:8765/hook",
        headers={"X-Test": "yes"},
        json_body={"source": "test"},
    )

    result = asyncio.run(runner.run(rule(action, timeout_seconds=1.5), None))

    assert result.ok is True
    assert request_log == [
        {
            "method": "PUT",
            "url": "http://127.0.0.1:8765/hook",
            "headers": {"X-Test": "yes"},
            "json": {"source": "test"},
            "timeout": 1.5,
        }
    ]


def test_http_action_failures_are_results_not_exceptions(tmp_path: Path) -> None:
    failing_status_runner = HookActionRunner(
        workspace_root=tmp_path,
        http_client_factory=lambda: FakeHTTPClient([], status_code=500),
    )
    raising_runner = HookActionRunner(
        workspace_root=tmp_path,
        http_client_factory=lambda: FakeHTTPClient([], error=TimeoutError("slow")),
    )
    action = HookAction(
        type=HookActionType.HTTP,
        method="POST",
        url="http://127.0.0.1:8765/hook",
    )

    failing_status = asyncio.run(failing_status_runner.run(rule(action), None))
    raising = asyncio.run(raising_runner.run(rule(action), None))

    assert failing_status.ok is False
    assert "500" in (failing_status.error or "")
    assert raising.ok is False
    assert "slow" in (raising.error or "")


def test_sub_agent_action_is_placeholder_only(tmp_path: Path) -> None:
    runner = HookActionRunner(workspace_root=tmp_path)
    action = HookAction(
        type=HookActionType.SUB_AGENT,
        task="review changes",
        input={"scope": "diff"},
        output="summary",
    )

    result = asyncio.run(runner.run(rule(action), None))

    assert result.ok is False
    assert "sub_agent" in result.output
    assert "不支持" in (result.error or "")
