from __future__ import annotations

import hashlib
from collections.abc import Sequence

from mycode.llm import ChatMessage, MessageOrigin
from mycode.prompt.environment import EnvironmentCollector, format_environment_context
from mycode.prompt.models import (
    PromptContextBlock,
    PromptBuildMetadata,
    PromptBuildResult,
    PromptConfig,
    PromptDiagnostic,
    StablePromptContext,
    SystemReminder,
    TurnPromptContext,
)
from mycode.prompt.registry import PromptBuildError, PromptRegistry
from mycode.prompt.reminder import ReminderPolicy
from mycode.tool import ToolDefinition


class PromptBuilder:
    def __init__(
        self,
        *,
        registry: PromptRegistry,
        environment_collector: EnvironmentCollector,
        reminder_policy: ReminderPolicy,
        config: PromptConfig,
    ) -> None:
        self._registry = registry
        self._environment_collector = environment_collector
        self._reminder_policy = reminder_policy
        self._config = config

    def begin_turn(
        self,
        *,
        turn_id: int,
        plan_only: bool,
        reminders: Sequence[SystemReminder] = (),
        framework_blocks: Sequence[PromptContextBlock] = (),
    ) -> TurnPromptContext:
        mode_reminder = self._reminder_policy.mode_reminder(plan_only=plan_only)
        all_reminders = tuple(reminders) + ((mode_reminder,) if mode_reminder is not None else ())
        return TurnPromptContext(
            turn_id,
            self._environment_collector.collect(),
            plan_only,
            all_reminders,
            tuple(framework_blocks),
        )

    def build(
        self,
        *,
        history: Sequence[ChatMessage],
        tools: Sequence[ToolDefinition],
        turn: TurnPromptContext,
        round_index: int,
    ) -> PromptBuildResult:
        sorted_tools = tuple(sorted(tools, key=lambda tool: tool.name))
        context = StablePromptContext(sorted_tools)
        diagnostics = list(turn.environment.diagnostics)
        rendered_sections: list[str] = []
        module_ids: list[str] = []
        for module in self._registry.enabled_modules():
            try:
                rendered_sections.append(module.render(context))
                module_ids.append(module.definition.id)
            except Exception as exc:
                if module.definition.protected:
                    raise PromptBuildError(f"protected prompt module failed: {module.definition.id}") from exc
                diagnostics.append(
                    PromptDiagnostic("prompt_module_render_failed", module.definition.id, "Prompt module was omitted")
                )

        system_text = "\n\n".join(section for section in rendered_sections if section)
        messages: list[ChatMessage] = [
            ChatMessage(role="system", content=system_text, origin=MessageOrigin.SYSTEM_INSTRUCTION),
            *history,
        ]
        framework_context = _render_framework_context(turn.framework_blocks)
        if framework_context is not None:
            messages.append(
                ChatMessage(
                    role="user",
                    content=framework_context,
                    origin=MessageOrigin.FRAMEWORK_CONTEXT,
                )
            )
        reminder_content = self._reminder_policy.render(turn.reminders, round_index)
        if reminder_content is not None:
            # 运行时提醒只注入当前请求，绝不能回写到普通 conversation memory。
            messages.append(
                ChatMessage(
                    role="user",
                    content=f"<system-reminder>\n{reminder_content}\n</system-reminder>",
                    origin=MessageOrigin.SYSTEM_REMINDER,
                )
            )
        messages.append(
            ChatMessage(
                role="user",
                content=format_environment_context(turn.environment, self._config),
                origin=MessageOrigin.ENVIRONMENT_CONTEXT,
            )
        )
        metadata = PromptBuildMetadata(
            enabled_module_ids=tuple(module_ids),
            stable_prompt_sha256=hashlib.sha256(system_text.encode("utf-8")).hexdigest(),
            diagnostics=tuple(diagnostics),
        )
        return PromptBuildResult(tuple(messages), sorted_tools, metadata)


def _render_framework_context(blocks: Sequence[PromptContextBlock]) -> str | None:
    if not blocks:
        return None
    lines = ["<framework-context>"]
    for block in sorted(blocks, key=lambda item: (item.priority, item.id)):
        lines.extend(
            [
                f'<block id="{block.id}" kind="{block.kind}">',
                block.content,
                "</block>",
            ]
        )
    lines.append("</framework-context>")
    return "\n".join(lines)
