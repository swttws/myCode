from __future__ import annotations

from pathlib import Path

import pytest

from mycode.hook.config import load_hook_config, load_hook_file
from mycode.hook.models import HookActionType, HookConfigError, HookEvent


def write_yaml(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_missing_default_hook_file_loads_empty_config(tmp_path: Path) -> None:
    config = load_hook_config(workspace_root=tmp_path)

    assert config.version == 1
    assert config.rules == ()
    assert config.path is None


def test_valid_hook_file_loads_rules_and_execution_controls(tmp_path: Path) -> None:
    path = write_yaml(
        tmp_path / "mycode.hooks.yaml",
        """
version: 1
hooks:
  - id: remind-tests
    event: model_round_start
    once: true
    timeout_seconds: 2.5
    action:
      type: prompt
      content: "本轮请先确认测试影响。"
  - event: tool_after
    background: true
    if:
      any:
        result.ok: false
    action:
      type: http
      method: POST
      url: "http://127.0.0.1:8765/hooks"
      headers:
        X-Test: "yes"
      json:
        source: hook-test
""",
    )

    config = load_hook_file(path)

    assert config.version == 1
    assert config.path == path
    assert len(config.rules) == 2
    first, second = config.rules
    assert first.id == "remind-tests"
    assert first.event is HookEvent.MODEL_ROUND_START
    assert first.condition is None
    assert first.action.type is HookActionType.PROMPT
    assert first.action.content == "本轮请先确认测试影响。"
    assert first.once is True
    assert first.background is False
    assert first.timeout_seconds == 2.5
    assert first.index == 0
    assert second.id == "hook-2"
    assert second.event is HookEvent.TOOL_AFTER
    assert second.background is True
    assert second.action.type is HookActionType.HTTP
    assert second.action.method == "POST"
    assert second.action.url == "http://127.0.0.1:8765/hooks"
    assert second.action.headers == {"X-Test": "yes"}
    assert second.action.json_body == {"source": "hook-test"}


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("version: 2\nhooks: []\n", "version"),
        ("version: 1\nunknown: true\nhooks: []\n", "unknown"),
        ("version: 1\nhooks:\n  - action: {type: prompt, content: hi}\n", "event"),
        ("version: 1\nhooks:\n  - event: model_round_start\n", "action"),
        (
            "version: 1\nhooks:\n  - event: no_such_event\n    action: {type: prompt, content: hi}\n",
            "no_such_event",
        ),
        (
            "version: 1\nhooks:\n  - event: model_round_start\n    action: {type: unknown}\n",
            "unknown",
        ),
        (
            "version: 1\nhooks:\n  - event: model_round_start\n    extra: true\n    action: {type: prompt, content: hi}\n",
            "extra",
        ),
        (
            "version: 1\nhooks:\n  - id: dup\n    event: model_round_start\n    action: {type: prompt, content: hi}\n  - id: dup\n    event: model_round_end\n    action: {type: prompt, content: bye}\n",
            "dup",
        ),
    ],
)
def test_invalid_hook_files_fail_at_load_time(
    tmp_path: Path,
    body: str,
    message: str,
) -> None:
    path = write_yaml(tmp_path / "bad.hooks.yaml", body)

    with pytest.raises(HookConfigError, match=message):
        load_hook_file(path)


@pytest.mark.parametrize(
    ("action", "message"),
    [
        ("{type: prompt}", "content"),
        ("{type: command}", "command"),
        ("{type: http}", "url"),
        ("{type: sub_agent}", "task"),
    ],
)
def test_action_required_fields_are_validated(
    tmp_path: Path,
    action: str,
    message: str,
) -> None:
    path = write_yaml(
        tmp_path / "bad-action.hooks.yaml",
        f"""
version: 1
hooks:
  - event: model_round_start
    action: {action}
""",
    )

    with pytest.raises(HookConfigError, match=message):
        load_hook_file(path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("once", "\"yes\""),
        ("background", "\"no\""),
        ("timeout_seconds", "0"),
        ("timeout_seconds", "-1"),
    ],
)
def test_execution_controls_are_validated(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    path = write_yaml(
        tmp_path / "bad-control.hooks.yaml",
        f"""
version: 1
hooks:
  - event: model_round_start
    {field}: {value}
    action:
      type: prompt
      content: hi
""",
    )

    with pytest.raises(HookConfigError, match=field):
        load_hook_file(path)


def test_tool_before_cannot_run_in_background(tmp_path: Path) -> None:
    path = write_yaml(
        tmp_path / "bad-background.hooks.yaml",
        """
version: 1
hooks:
  - event: tool_before
    background: true
    action:
      type: prompt
      content: hi
""",
    )

    with pytest.raises(HookConfigError, match="tool_before"):
        load_hook_file(path)


def test_example_hook_config_loads_and_covers_action_contract() -> None:
    example_path = Path(__file__).resolve().parents[1] / "examples" / "mycode.hooks.yaml"

    config = load_hook_file(example_path)

    assert config.path == example_path
    assert len(config.rules) >= 5
    action_types = {rule.action.type for rule in config.rules}
    assert action_types >= {
        HookActionType.COMMAND,
        HookActionType.PROMPT,
        HookActionType.HTTP,
        HookActionType.SUB_AGENT,
    }
    assert any(
        rule.event is HookEvent.TOOL_BEFORE and rule.action.block and rule.action.reason
        for rule in config.rules
    )
    assert any(
        rule.action.type is HookActionType.HTTP
        and str(rule.action.url).startswith("http://127.0.0.1")
        for rule in config.rules
    )
