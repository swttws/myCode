from __future__ import annotations

import asyncio
from pathlib import Path

from mycode.skill.catalog import SkillCatalog
from mycode.skill.load_tool import SkillLoadTool
from mycode.skill.loader import SkillLoader
from mycode.skill.runtime import SkillRuntime
from mycode.skill import SkillExecutionResult
from mycode.tool import ToolKind
from tests.skill_test_support import write_skill


class FakeIsolatedExecutor:
    def __init__(self, result: SkillExecutionResult | None = None) -> None:
        self.result = result or SkillExecutionResult(ok=True, summary="isolated done")
        self.calls = []

    async def execute_loaded(self, definition, arguments):
        self.calls.append((definition.metadata.name, arguments))
        return self.result


def make_tool(tmp_path: Path, *, executor=None) -> tuple[SkillLoadTool, SkillRuntime, Path]:
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    builtin = tmp_path / "builtins"
    project_root = workspace / ".mycode" / "skills"
    for root in (project_root, home / ".mycode" / "skills", builtin):
        root.mkdir(parents=True)
    catalog = SkillCatalog(
        loader=SkillLoader(workspace_root=workspace, home=home, builtin_root=builtin),
        tool_names=lambda: frozenset({"read_file", "search_code", "run_command", "load_skill"}),
        reserved_slash_names=frozenset({"help", "clear"}),
    )
    runtime = SkillRuntime(catalog)
    tool = SkillLoadTool(runtime=runtime, executor=executor)
    return tool, runtime, project_root


def test_load_skill_definition_is_read_but_not_parallel_safe(tmp_path):
    tool, _, _ = make_tool(tmp_path)

    assert tool.definition.name == "load_skill"
    assert tool.definition.kind is ToolKind.READ
    assert tool.definition.parallel_safe is False
    assert set(tool.definition.parameters["properties"]) == {"name", "arguments", "resource"}
    assert tool.definition.parameters["required"] == ["name"]


def test_load_skill_activates_shared_skill_without_returning_sop(tmp_path):
    tool, runtime, project_root = make_tool(tmp_path)
    write_skill(
        project_root,
        "review",
        body="秘密 SOP {{arguments}}",
        resources={"notes/info.md": "resource text"},
    )

    result = asyncio.run(tool.execute_async({"name": "review", "arguments": "main"}))

    assert result.ok is True
    assert result.content["action"] == "activated"
    assert result.content["name"] == "review"
    assert result.content["mode"] == "shared"
    assert result.content["resources"] == ["notes/info.md"]
    assert result.content["set_scope"] is True
    assert "秘密 SOP" not in str(result.content)
    assert "秘密 SOP main" in runtime.prompt_blocks()[0].content


def test_load_skill_reads_resource_for_active_skill(tmp_path):
    tool, _, project_root = make_tool(tmp_path)
    write_skill(project_root, "review", resources={"notes/info.md": "resource text"})
    asyncio.run(tool.execute_async({"name": "review"}))

    result = asyncio.run(tool.execute_async({"name": "review", "resource": "notes/info.md"}))

    assert result.ok is True
    assert result.content == {
        "action": "resource",
        "name": "review",
        "path": "notes/info.md",
        "text": "resource text",
    }


def test_load_skill_returns_stable_errors_for_unknown_or_invalid_resource_request(tmp_path):
    tool, _, project_root = make_tool(tmp_path)
    write_skill(project_root, "review")

    unknown = asyncio.run(tool.execute_async({"name": "missing"}))
    with_arguments = asyncio.run(
        tool.execute_async({"name": "review", "arguments": "x", "resource": "notes/info.md"})
    )
    inactive = asyncio.run(tool.execute_async({"name": "review", "resource": "notes/info.md"}))

    assert unknown.ok is False
    assert unknown.content["category"] == "unknown_skill"
    assert with_arguments.ok is False
    assert with_arguments.content["category"] == "invalid_arguments"
    assert inactive.ok is False
    assert inactive.content["category"] == "resource_error"


def test_load_skill_executes_isolated_skill_with_parent_run_context(tmp_path):
    fake_executor = FakeIsolatedExecutor()
    tool, runtime, project_root = make_tool(tmp_path, executor=fake_executor)
    write_skill(
        project_root,
        "test",
        mode="isolated",
        context={"strategy": "none"},
        allowed_tools=("read_file",),
    )
    runtime.refresh()

    with runtime.execution_scope(None, history=(), framework_blocks=(), approval_provider=None):
        result = asyncio.run(tool.execute_async({"name": "test", "arguments": "target"}))

    assert result.ok is True
    assert result.content == {
        "action": "completed",
        "name": "test",
        "mode": "isolated",
        "summary": "isolated done",
    }
    assert fake_executor.calls == [("test", "target")]


def test_load_skill_rejects_recursive_isolated_skill_without_starting_executor(tmp_path):
    fake_executor = FakeIsolatedExecutor()
    tool, runtime, project_root = make_tool(tmp_path, executor=fake_executor)
    write_skill(
        project_root,
        "test",
        mode="isolated",
        context={"strategy": "none"},
        allowed_tools=("read_file",),
    )
    runtime.refresh()

    with runtime.execution_scope(None, history=(), framework_blocks=(), approval_provider=None, isolated_depth=1):
        result = asyncio.run(tool.execute_async({"name": "test"}))

    assert result.ok is False
    assert result.content["category"] == "recursive_isolated_skill"
    assert fake_executor.calls == []
