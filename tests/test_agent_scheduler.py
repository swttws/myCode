import pytest

from mycode.agent import ToolScheduleError, build_tool_batches
from mycode.tool import ToolCall, ToolDefinition, ToolKind, ToolRegistry, ToolResult


class FakeTool:
    def __init__(self, name: str, kind) -> None:
        self._definition = ToolDefinition(
            name=name,
            description=f"{name} test tool.",
            parameters={"type": "object", "properties": {}, "required": []},
            kind=kind,
        )

    @property
    def definition(self):
        return self._definition

    def execute(self, arguments):
        return ToolResult(ok=True, tool_name=self.definition.name, content={})


class BrokenRegistry:
    def get(self, name):
        return FakeTool(name, "mutating")


def call(name: str) -> ToolCall:
    return ToolCall(id=f"call-{name}", name=name, arguments={})


def test_build_tool_batches_groups_adjacent_reads_and_serializes_writes():
    registry = ToolRegistry(
        [
            FakeTool("read_a", ToolKind.READ),
            FakeTool("read_b", ToolKind.READ),
            FakeTool("write_a", ToolKind.WRITE),
            FakeTool("read_c", ToolKind.READ),
        ]
    )

    batches = build_tool_batches(
        [call("read_a"), call("read_b"), call("write_a"), call("read_c")],
        registry,
    )

    assert [[tool_call.name for tool_call in batch.calls] for batch in batches] == [
        ["read_a", "read_b"],
        ["write_a"],
        ["read_c"],
    ]
    assert [batch.kind for batch in batches] == [ToolKind.READ, ToolKind.WRITE, ToolKind.READ]


def test_build_tool_batches_keeps_consecutive_writes_separate():
    registry = ToolRegistry(
        [
            FakeTool("write_a", ToolKind.WRITE),
            FakeTool("write_b", ToolKind.WRITE),
        ]
    )

    batches = build_tool_batches([call("write_a"), call("write_b")], registry)

    assert [[tool_call.name for tool_call in batch.calls] for batch in batches] == [["write_a"], ["write_b"]]


def test_build_tool_batches_reports_unknown_tool():
    registry = ToolRegistry()

    with pytest.raises(ToolScheduleError) as exc_info:
        build_tool_batches([call("missing")], registry)

    assert exc_info.value.code == "unknown_tool"


def test_build_tool_batches_rejects_invalid_tool_kind():
    with pytest.raises(ToolScheduleError) as exc_info:
        build_tool_batches([call("strange")], BrokenRegistry())

    assert exc_info.value.code == "invalid_tool_kind"
