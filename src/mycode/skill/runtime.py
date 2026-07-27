from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from mycode.permission.models import ApprovalProvider
from mycode.prompt.models import PromptContextBlock
from mycode.skill.catalog import SkillCatalog
from mycode.skill.models import (
    SkillActivation,
    SkillCatalogSnapshot,
    SkillDefinition,
    SkillExecutionScope,
    SkillRunContext,
)


_current_scope: ContextVar[SkillExecutionScope | None] = ContextVar("skill_current_scope", default=None)
_current_run_context: ContextVar[SkillRunContext | None] = ContextVar("skill_current_run_context", default=None)


class SkillRuntime:
    LOAD_TOOL_NAME = "load_skill"

    def __init__(self, catalog: SkillCatalog) -> None:
        self._catalog = catalog
        self._activations: dict[str, SkillActivation] = {}

    def refresh(self) -> SkillCatalogSnapshot:
        snapshot = (
            self._catalog.initialize()
            if self._catalog.snapshot().generation == 0
            else self._catalog.refresh()
        )
        self._rerender_activations()
        return snapshot

    def activate(self, name: str, arguments: str) -> SkillActivation:
        definition = self._definition(name)
        activation = _activation(definition, arguments)
        self._activations[name] = activation
        return activation

    def definition(self, name: str) -> SkillDefinition:
        return self._definition(name)

    def is_active(self, name: str) -> bool:
        return name in self._activations

    def read_resource(self, name: str, relative_path: str) -> str:
        return self._catalog.read_resource(name, relative_path)

    def prompt_blocks(self) -> tuple[PromptContextBlock, ...]:
        blocks: list[PromptContextBlock] = []
        if self._activations:
            blocks.append(
                PromptContextBlock(
                    id="active-skills",
                    kind="skill",
                    priority=-200,
                    content=_render_active_block(tuple(self._activations[name] for name in sorted(self._activations))),
                )
            )
        definitions = self._catalog.snapshot().definitions
        if definitions:
            blocks.append(
                PromptContextBlock(
                    id="skill-catalog",
                    kind="skill",
                    priority=-100,
                    content=_render_catalog_block(definitions),
                )
            )
        return tuple(blocks)

    @contextmanager
    def execution_scope(
        self,
        scope: SkillExecutionScope | None,
        *,
        history,
        framework_blocks: tuple[PromptContextBlock, ...],
        approval_provider: ApprovalProvider | None,
        isolated_depth: int = 0,
    ) -> Iterator[None]:
        run_context = SkillRunContext(
            history=tuple(history),
            framework_blocks=tuple(framework_blocks),
            approval_provider=approval_provider,
            scope=scope,
            isolated_depth=isolated_depth,
        )
        scope_token = _current_scope.set(scope)
        context_token = _current_run_context.set(run_context)
        try:
            yield
        finally:
            # 执行范围用 ContextVar 恢复父任务状态，避免共享和独立 Skill 互相串线。
            _current_run_context.reset(context_token)
            _current_scope.reset(scope_token)

    def set_current_scope(self, name: str) -> SkillExecutionScope:
        definition = self._definition(name)
        scope = SkillExecutionScope(name=name, allowed_tools=frozenset(definition.metadata.allowed_tools))
        _current_scope.set(scope)
        return scope

    def set_current_scope_object(self, scope: SkillExecutionScope) -> SkillExecutionScope:
        _current_scope.set(scope)
        return scope

    def set_current_run_context(
        self,
        *,
        history,
        framework_blocks: tuple[PromptContextBlock, ...],
        approval_provider: ApprovalProvider | None,
        isolated_depth: int = 0,
    ) -> None:
        _current_run_context.set(
            SkillRunContext(
                history=tuple(history),
                framework_blocks=tuple(framework_blocks),
                approval_provider=approval_provider,
                scope=self.current_scope(),
                isolated_depth=isolated_depth,
            )
        )

    def clear_current_scope(self) -> None:
        _current_scope.set(None)
        _current_run_context.set(None)

    def current_scope(self) -> SkillExecutionScope | None:
        return _current_scope.get()

    def current_run_context(self) -> SkillRunContext | None:
        return _current_run_context.get()

    def visible_tool_names(self) -> frozenset[str] | None:
        scope = self.current_scope()
        if scope is None:
            return None
        return frozenset(scope.allowed_tools | {self.LOAD_TOOL_NAME})

    def allows_tool(self, name: str) -> bool:
        visible = self.visible_tool_names()
        return True if visible is None else name in visible

    def clear(self) -> None:
        self._activations.clear()
        _current_scope.set(None)
        _current_run_context.set(None)

    def _definition(self, name: str) -> SkillDefinition:
        definition = self._catalog.get(name)
        if definition is None:
            raise KeyError(name)
        return definition

    def _rerender_activations(self) -> None:
        rerendered: dict[str, SkillActivation] = {}
        for name, activation in self._activations.items():
            definition = self._catalog.get(name)
            if definition is None:
                continue
            rerendered[name] = _activation(definition, activation.arguments)
        self._activations = rerendered


def _activation(definition: SkillDefinition, arguments: str) -> SkillActivation:
    return SkillActivation(
        name=definition.metadata.name,
        arguments=arguments,
        rendered_instruction=definition.instruction.replace("{{arguments}}", arguments),
        revision=definition.revision,
    )


def _render_catalog_block(definitions: tuple[SkillDefinition, ...]) -> str:
    lines = ["可用 Skill（按需调用 load_skill 加载详细指令）："]
    lines.extend(f"- {definition.metadata.name}: {definition.metadata.description}" for definition in definitions)
    return "\n".join(lines)


def _render_active_block(activations: tuple[SkillActivation, ...]) -> str:
    lines = ["已激活 Skill SOP："]
    for activation in activations:
        lines.append(f"\n## {activation.name}\n{activation.rendered_instruction}")
    return "\n".join(lines)
