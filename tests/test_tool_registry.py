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


def test_tool_package_exports_public_tool_system_entrypoints():
    assert ToolCall(id="call-1", name="fake", arguments={}).name == "fake"
    assert ToolResult(ok=True, tool_name="fake", content={}).ok is True
    assert FileTextCache is not None
    assert ToolExecutor is not None
    assert create_default_tool_registry is not None
