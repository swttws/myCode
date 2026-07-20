import time
import asyncio
import threading

from mycode.tool import ToolCall, ToolDefinition, ToolExecutor, ToolKind, ToolRegistry, ToolResult


class EchoTool:
    @property
    def definition(self):
        return ToolDefinition(
            name="echo",
            description="Echo arguments.",
            parameters={"type": "object", "properties": {}, "required": []},
            kind=ToolKind.READ,
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
            kind=ToolKind.READ,
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
            kind=ToolKind.READ,
        )

    def execute(self, arguments):
        time.sleep(1)
        return ToolResult(ok=True, tool_name="slow", content={})


class AsyncEchoTool(EchoTool):
    def __init__(self):
        self.loop = None

    @property
    def definition(self):
        return ToolDefinition(
            name="async_echo",
            description="Async echo.",
            parameters={"type": "object", "properties": {}, "required": []},
            kind=ToolKind.READ,
        )

    def execute(self, arguments):
        raise AssertionError("async tools must not use the sync path")

    async def execute_async(self, arguments):
        self.loop = asyncio.get_running_loop()
        return ToolResult(ok=True, tool_name="async_echo", content={"arguments": arguments})


class AsyncSlowTool(AsyncEchoTool):
    @property
    def definition(self):
        return ToolDefinition(
            name="async_slow",
            description="Slow async tool.",
            parameters={"type": "object", "properties": {}, "required": []},
            kind=ToolKind.READ,
        )

    async def execute_async(self, arguments):
        await asyncio.sleep(1)
        return ToolResult(ok=True, tool_name="async_slow", content={})


class ThreadRecordingTool(EchoTool):
    def __init__(self):
        self.thread_id = None

    def execute(self, arguments):
        self.thread_id = threading.get_ident()
        return super().execute(arguments)


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


def test_tool_executor_awaits_async_tool_in_current_event_loop():
    async def scenario():
        tool = AsyncEchoTool()
        executor = ToolExecutor(ToolRegistry([tool]))
        current_loop = asyncio.get_running_loop()

        result = await executor.execute(
            ToolCall(id="call-async", name="async_echo", arguments={"value": 1})
        )

        assert result.ok is True
        assert result.content == {"arguments": {"value": 1}}
        assert tool.loop is current_loop

    asyncio.run(scenario())


def test_tool_executor_keeps_sync_tool_on_worker_thread():
    async def scenario():
        tool = ThreadRecordingTool()
        executor = ToolExecutor(ToolRegistry([tool]))
        event_loop_thread = threading.get_ident()

        result = await executor.execute(ToolCall(id="call-sync", name="echo", arguments={}))

        assert result.ok is True
        assert tool.thread_id != event_loop_thread

    asyncio.run(scenario())


def test_tool_executor_times_out_async_tool_with_existing_result_contract():
    executor = ToolExecutor(ToolRegistry([AsyncSlowTool()]), timeout_seconds=0.01)

    result = asyncio.run(
        executor.execute(ToolCall(id="call-slow", name="async_slow", arguments={}))
    )

    assert result.ok is False
    assert result.tool_name == "async_slow"
    assert result.content["timed_out"] is True
