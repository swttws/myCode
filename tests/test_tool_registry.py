import pytest

from mycode.permission.pathing import PathGuard
from mycode.tool import (
    DeferredToolSummary,
    FileTextCache,
    ToolCall,
    ToolExecutor,
    ToolRegistry,
    ToolResult,
    create_default_tool_registry,
    ToolDefinition,
    ToolKind,
)


class FakeTool:
    def __init__(self, name: str = "fake") -> None:
        self._definition = ToolDefinition(
            name=name,
            description="Fake test tool.",
            parameters={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
            kind=ToolKind.READ,
        )

    @property
    def definition(self):
        return self._definition

    def execute(self, arguments):
        raise AssertionError("registry tests should not execute tools")


class DeferredFakeTool(FakeTool):
    def should_defer(self) -> bool:
        return True


def test_tool_registry_gets_registered_tool_by_name():
    tool = FakeTool()
    registry = ToolRegistry([tool])

    assert registry.get("fake") is tool


def test_tool_definition_declares_tool_kind():
    assert FakeTool().definition.kind == ToolKind.READ


def test_tool_definition_defaults_to_no_persistable_grant_arguments():
    assert FakeTool().definition.grant_arguments == ()


def test_tool_registry_rejects_duplicate_tool_names():
    registry = ToolRegistry([FakeTool("fake")])

    with pytest.raises(ValueError, match="duplicate tool name"):
        registry.register(FakeTool("fake"))


def test_tool_registry_rejects_invalid_tool_kind():
    class InvalidKindTool(FakeTool):
        @property
        def definition(self):
            return ToolDefinition(
                name="invalid",
                description="Invalid kind test tool.",
                parameters={"type": "object", "properties": {}, "required": []},
                kind="mutating",
            )

    with pytest.raises(ValueError, match="invalid tool kind"):
        ToolRegistry([InvalidKindTool()])


def test_tool_registry_returns_tool_definitions():
    definition = FakeTool().definition
    registry = ToolRegistry([FakeTool()])

    assert registry.definitions() == [definition]


def test_tool_registry_returns_definitions_in_name_order_without_affecting_lookup():
    first = FakeTool("zeta")
    second = FakeTool("alpha")
    registry = ToolRegistry([first, second])

    assert [definition.name for definition in registry.definitions()] == ["alpha", "zeta"]
    assert registry.get("zeta") is first


def test_tool_registry_separates_full_model_and_deferred_views():
    local = FakeTool("local")
    deferred_zeta = DeferredFakeTool("zeta_remote")
    deferred_alpha = DeferredFakeTool("alpha_remote")
    registry = ToolRegistry([deferred_zeta, local, deferred_alpha])

    assert [definition.name for definition in registry.definitions()] == [
        "alpha_remote",
        "local",
        "zeta_remote",
    ]
    assert [definition.name for definition in registry.model_definitions()] == ["local"]
    assert registry.deferred_summaries() == [
        DeferredToolSummary("alpha_remote", "Fake test tool."),
        DeferredToolSummary("zeta_remote", "Fake test tool."),
    ]


def test_tool_registry_marks_only_registered_deferred_tool_as_discovered():
    registry = ToolRegistry([FakeTool("local"), DeferredFakeTool("remote")])

    assert registry.mark_discovered("missing") is False
    assert registry.mark_discovered("local") is False
    assert registry.mark_discovered("remote") is True
    assert registry.mark_discovered("remote") is True
    assert [definition.name for definition in registry.model_definitions()] == ["local", "remote"]
    assert registry.deferred_summaries() == []


def test_tool_registry_converts_definitions_to_openai_chat_tool_specs():
    registry = ToolRegistry([FakeTool()])

    expected = [
        {
            "type": "function",
            "function": {
                "name": "fake",
                "description": "Fake test tool.",
                "parameters": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                },
            },
        }
    ]
    assert registry.openai_chat_tool_specs() == expected
    assert registry.openai_tool_specs() == expected


def test_tool_registry_converts_definitions_to_openai_responses_tool_specs():
    registry = ToolRegistry([FakeTool()])

    assert registry.openai_responses_tool_specs() == [
        {
            "type": "function",
            "name": "fake",
            "description": "Fake test tool.",
            "parameters": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
            "strict": False,
        }
    ]


def test_default_tool_registry_registers_core_tools(tmp_path):
    registry = create_default_tool_registry(tmp_path)

    assert [definition.name for definition in registry.definitions()] == [
        "edit_file",
        "find_files",
        "read_file",
        "run_command",
        "search_code",
        "write_file",
    ]


def test_default_tool_registry_reuses_injected_path_guard(tmp_path):
    guard = PathGuard(tmp_path)

    registry = create_default_tool_registry(tmp_path, path_guard=guard)

    assert registry.get("read_file")._path_guard is guard
    assert registry.get("write_file")._path_guard is guard
    assert registry.get("find_files")._path_guard is guard


def test_default_tool_registry_declares_tool_kinds(tmp_path):
    registry = create_default_tool_registry(tmp_path)
    definitions = {definition.name: definition for definition in registry.definitions()}

    assert definitions["read_file"].kind == ToolKind.READ
    assert definitions["find_files"].kind == ToolKind.READ
    assert definitions["search_code"].kind == ToolKind.READ
    assert definitions["write_file"].kind == ToolKind.WRITE
    assert definitions["edit_file"].kind == ToolKind.WRITE
    assert definitions["run_command"].kind == ToolKind.WRITE


def test_default_tool_registry_declares_exact_grant_arguments(tmp_path):
    registry = create_default_tool_registry(tmp_path)
    definitions = {definition.name: definition for definition in registry.definitions()}

    assert definitions["read_file"].grant_arguments == ("path",)
    assert definitions["write_file"].grant_arguments == ("path",)
    assert definitions["edit_file"].grant_arguments == ("path",)
    assert definitions["find_files"].grant_arguments == ("root",)
    assert definitions["search_code"].grant_arguments == ("root",)
    assert definitions["run_command"].grant_arguments == ("command",)


def test_default_tool_registry_uses_chinese_tool_definitions(tmp_path):
    registry = create_default_tool_registry(tmp_path)
    definitions = {definition.name: definition for definition in registry.definitions()}

    assert definitions["read_file"].description == "读取工作区内的 UTF-8 文本文件。"
    assert definitions["read_file"].parameters["properties"]["path"]["description"] == "要读取的工作区内相对路径。"

    assert definitions["write_file"].description == "向工作区内写入 UTF-8 文本文件，并自动创建父目录。"
    assert definitions["write_file"].parameters["properties"]["path"]["description"] == "要写入的工作区内相对路径。"
    assert definitions["write_file"].parameters["properties"]["text"]["description"] == "要写入文件的文本内容。"

    assert definitions["edit_file"].description == "仅当原文在文件中唯一出现时，替换对应文本。"
    assert definitions["edit_file"].parameters["properties"]["old_text"]["description"] == "要替换的原始文本。"
    assert definitions["edit_file"].parameters["properties"]["new_text"]["description"] == "替换后的新文本。"

    assert definitions["run_command"].description == "在当前工作区内执行 shell 命令，并返回退出码、标准输出、标准错误和超时状态。"
    assert definitions["run_command"].parameters["properties"]["command"]["description"] == "要执行的 shell 命令。"
    assert definitions["run_command"].parameters["properties"]["timeout_seconds"]["description"] == "命令超时时间（秒）。"

    assert definitions["find_files"].description == "按 glob 模式在工作区内查找文件。"
    assert definitions["find_files"].parameters["properties"]["pattern"]["description"] == "用于匹配文件名的 glob 模式。"
    assert definitions["find_files"].parameters["properties"]["root"]["description"] == "查找起始目录，相对于工作区根目录。"

    assert definitions["search_code"].description == "在工作区内的 UTF-8 文本文件中搜索字面量内容。"
    assert definitions["search_code"].parameters["properties"]["query"]["description"] == "要搜索的字面量内容。"
    assert definitions["search_code"].parameters["properties"]["root"]["description"] == "搜索起始目录，相对于工作区根目录。"


def test_tool_package_exports_public_tool_system_entrypoints():
    assert ToolCall(id="call-1", name="fake", arguments={}).name == "fake"
    assert ToolResult(ok=True, tool_name="fake", content={}).ok is True
    assert FileTextCache is not None
    assert ToolExecutor is not None
    assert create_default_tool_registry is not None
