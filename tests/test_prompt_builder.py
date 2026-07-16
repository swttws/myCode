from mycode.llm import ChatMessage, MessageOrigin
from mycode.prompt.builder import PromptBuilder
from mycode.prompt.models import EnvironmentSnapshot, PromptConfig, PromptModuleDefinition
from mycode.prompt.registry import PromptBuildError, PromptRegistry
from mycode.prompt.reminder import ReminderPolicy
from mycode.tool import ToolDefinition, ToolKind


class FakeEnvironmentCollector:
    def __init__(self) -> None:
        self.calls = 0

    def collect(self) -> EnvironmentSnapshot:
        self.calls += 1
        return EnvironmentSnapshot("workspace", "TestOS", "time", "UTC", "main", "M file", ())


class FakeModule:
    def __init__(self, module_id: str, priority: int, text: str, *, protected: bool = False, fail: bool = False) -> None:
        self._definition = PromptModuleDefinition(module_id, priority, protected)
        self._text = text
        self._fail = fail

    @property
    def definition(self) -> PromptModuleDefinition:
        return self._definition

    def render(self, context) -> str:
        if self._fail:
            raise RuntimeError("render failed")
        return self._text


def make_tool(name: str) -> ToolDefinition:
    return ToolDefinition(name, name, {"type": "object", "properties": {}, "required": []}, ToolKind.READ)


def test_builder_reuses_turn_snapshot_and_keeps_runtime_messages_out_of_history():
    collector = FakeEnvironmentCollector()
    builder = PromptBuilder(
        registry=PromptRegistry([FakeModule("stable", 100, "stable instruction")]),
        environment_collector=collector,
        reminder_policy=ReminderPolicy(4),
        config=PromptConfig(),
    )
    history = (ChatMessage(role="user", content="original request"),)

    turn = builder.begin_turn(turn_id=1, plan_only=True)
    first = builder.build(history=history, tools=(make_tool("zeta"), make_tool("alpha")), turn=turn, round_index=1)
    second = builder.build(history=history, tools=(make_tool("zeta"), make_tool("alpha")), turn=turn, round_index=2)

    assert collector.calls == 1
    assert first.messages[0] == ChatMessage(
        role="system", content="stable instruction", origin=MessageOrigin.SYSTEM_INSTRUCTION
    )
    assert first.messages[1] == history[0]
    assert first.messages[2].origin is MessageOrigin.SYSTEM_REMINDER
    assert first.messages[2].content.startswith("<system-reminder>")
    assert first.messages[3].origin is MessageOrigin.ENVIRONMENT_CONTEXT
    assert first.messages[3].content.startswith("<environment-context>")
    assert "original request" not in first.messages[2].content
    assert first.messages[3].content == second.messages[3].content
    assert [tool.name for tool in first.tools] == ["alpha", "zeta"]
    assert first.metadata.enabled_module_ids == ("stable",)
    assert first.metadata.stable_prompt_sha256 == second.metadata.stable_prompt_sha256


def test_builder_skips_regular_module_failure_and_rejects_protected_module_failure():
    collector = FakeEnvironmentCollector()
    history = (ChatMessage(role="user", content="request"),)
    builder = PromptBuilder(
        registry=PromptRegistry([FakeModule("optional", 100, "", fail=True)]),
        environment_collector=collector,
        reminder_policy=ReminderPolicy(4),
        config=PromptConfig(),
    )

    result = builder.build(history=history, tools=(), turn=builder.begin_turn(turn_id=1, plan_only=False), round_index=1)

    assert result.metadata.diagnostics[0].code == "prompt_module_render_failed"

    protected_builder = PromptBuilder(
        registry=PromptRegistry([FakeModule("safety", 100, "", protected=True, fail=True)]),
        environment_collector=collector,
        reminder_policy=ReminderPolicy(4),
        config=PromptConfig(),
    )

    import pytest

    with pytest.raises(PromptBuildError, match="safety"):
        protected_builder.build(
            history=history,
            tools=(),
            turn=protected_builder.begin_turn(turn_id=2, plan_only=False),
            round_index=1,
        )
