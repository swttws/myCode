from datetime import datetime, timezone
from subprocess import CompletedProcess, TimeoutExpired

from mycode.prompt.environment import DefaultEnvironmentCollector, format_environment_context
from mycode.prompt.models import PromptConfig


def test_environment_collector_captures_bounded_git_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "mycode.prompt.environment.datetime",
        type("Clock", (), {"now": staticmethod(lambda: datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc))}),
    )
    monkeypatch.setattr("mycode.prompt.environment.platform.system", lambda: "TestOS")

    def fake_run(args, **kwargs):
        if args[-2:] == ("branch", "--show-current"):
            return CompletedProcess(args, 0, "main\n", "")
        return CompletedProcess(args, 0, " M file.txt\n", "")

    monkeypatch.setattr("mycode.prompt.environment.subprocess.run", fake_run)
    config = PromptConfig(environment_value_limit=64)

    snapshot = DefaultEnvironmentCollector(tmp_path, config).collect()

    assert snapshot.workspace == str(tmp_path)
    assert snapshot.operating_system == "TestOS"
    expected_now = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc).astimezone()
    assert snapshot.current_time == expected_now.isoformat()
    assert snapshot.timezone == expected_now.tzname()
    assert snapshot.git_branch == "main"
    assert snapshot.git_status == "M file.txt"
    assert snapshot.diagnostics == ()


def test_environment_collector_degrades_when_git_is_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr("mycode.prompt.environment.subprocess.run", lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutExpired("git", 1)))
    monkeypatch.setattr("mycode.prompt.environment.platform.system", lambda: "TestOS")

    snapshot = DefaultEnvironmentCollector(tmp_path, PromptConfig()).collect()

    assert snapshot.git_branch is None
    assert snapshot.git_status is None
    assert {diagnostic.code for diagnostic in snapshot.diagnostics} == {
        "git_branch_unavailable",
        "git_status_unavailable",
    }


def test_environment_context_escapes_and_truncates_values_without_exposing_extra_data(tmp_path):
    config = PromptConfig(environment_value_limit=24)
    collector = DefaultEnvironmentCollector(tmp_path, config)
    snapshot = collector.collect()
    snapshot = snapshot.__class__(
        workspace="<workspace>&secret",
        operating_system="TestOS",
        current_time="2026-07-16T12:00:00+00:00",
        timezone="UTC",
        git_branch="<branch>&name",
        git_status="a" * 40,
        diagnostics=(),
    )

    content = format_environment_context(snapshot, config)

    assert content.startswith("<environment-context>\n")
    assert content.endswith("\n</environment-context>")
    assert [line.split(":", 1)[0] for line in content.splitlines()[1:-1]] == [
        "工作区",
        "操作系统",
        "当前时间",
        "时区",
        "Git 分支",
        "Git 状态",
    ]
    assert "&lt;workspace&gt;&amp;" in content
    assert "&lt;branch&gt;&amp;" in content
    assert "Git 状态: " + "a" * 24 + "..." in content
    assert "API_KEY" not in content
    assert "diff --git" not in content
