import time
import asyncio

from mycode.tool import ToolCall, ToolDefinition, ToolExecutor, ToolRegistry, ToolResult


class EchoTool:
    @property
    def definition(self):
        return ToolDefinition(
            name="echo",
            description="Echo arguments.",
            parameters={"type": "object", "properties": {}, "required": []},
        )

    def execute(self, arguments):
        return ToolResult(ok=True, tool_name="echo", content={"arguments": arguments})


class ExplodingTool(EchoTool):
    @property
    def definition(self):
        return ToolDefinition(
            name="explode",
            description="Raise an error.",
            parameters={"type": "object", "properties": {}, "required": []},
        )

    def execute(self, arguments):
        raise RuntimeError("boom")


class SlowTool(EchoTool):
    @property
    def definition(self):
        return ToolDefinition(
            name="slow",
            description="Sleep too long.",
            parameters={"type": "object", "properties": {}, "required": []},
        )

    def execute(self, arguments):
        time.sleep(1)
        return ToolResult(ok=True, tool_name="slow", content={})


def test_tool_executor_executes_registered_tool():
    executor = ToolExecutor(ToolRegistry([EchoTool()]))

    result = asyncio.run(executor.execute(ToolCall(id="call-1", name="echo", arguments={"x": 1})))

    assert result == ToolResult(ok=True, tool_name="echo", content={"arguments": {"x": 1}})


def test_tool_executor_returns_structured_error_for_unknown_tool():
    executor = ToolExecutor(ToolRegistry())

    result = asyncio.run(executor.execute(ToolCall(id="call-1", name="missing", arguments={})))

    assert result.ok is False
    assert result.tool_name == "missing"
    assert "unknown tool" in result.error


def test_tool_executor_returns_structured_error_for_invalid_arguments_json():
    executor = ToolExecutor(ToolRegistry([EchoTool()]))

    result = asyncio.run(
        executor.execute(ToolCall(id="call-1", name="echo", arguments=None, raw_arguments="{bad"))
    )

    assert result.ok is False
    assert result.tool_name == "echo"
    assert result.content["raw_arguments"] == "{bad"
    assert "invalid JSON arguments" in result.error


def test_tool_executor_catches_tool_exceptions():
    executor = ToolExecutor(ToolRegistry([ExplodingTool()]))

    result = asyncio.run(executor.execute(ToolCall(id="call-1", name="explode", arguments={})))

    assert result.ok is False
    assert result.tool_name == "explode"
    assert "boom" in result.error


def test_tool_executor_returns_timeout_result():
    executor = ToolExecutor(ToolRegistry([SlowTool()]), timeout_seconds=0.01)

    result = asyncio.run(executor.execute(ToolCall(id="call-1", name="slow", arguments={})))

    assert result.ok is False
    assert result.tool_name == "slow"
    assert result.content["timed_out"] is True
    assert "timeout" in result.error


def test_tool_executor_returns_registered_definitions():
    executor = ToolExecutor(ToolRegistry([EchoTool()]))

    assert executor.definitions() == [EchoTool().definition]
