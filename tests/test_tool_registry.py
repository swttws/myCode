import pytest

from mycode.tool import (
    FileTextCache,
    ToolCall,
    ToolExecutor,
    ToolRegistry,
    ToolResult,
    create_default_tool_registry,
    ToolDefinition,
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
        )

    @property
    def definition(self):
        return self._definition

    def execute(self, arguments):
        raise AssertionError("registry tests should not execute tools")


def test_tool_registry_gets_registered_tool_by_name():
    tool = FakeTool()
    registry = ToolRegistry([tool])

    assert registry.get("fake") is tool


def test_tool_registry_rejects_duplicate_tool_names():
    registry = ToolRegistry([FakeTool("fake")])

    with pytest.raises(ValueError, match="duplicate tool name"):
        registry.register(FakeTool("fake"))


def test_tool_registry_returns_tool_definitions():
    definition = FakeTool().definition
    registry = ToolRegistry([FakeTool()])

    assert registry.definitions() == [definition]


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
        "read_file",
        "write_file",
        "edit_file",
        "run_command",
        "find_files",
        "search_code",
    ]


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
