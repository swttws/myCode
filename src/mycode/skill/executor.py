from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Callable

from mycode.agent import AgentConfig, AgentEventType, AgentLoop, AgentMode
from mycode.config import LLMConfig
from mycode.llm import BaseLLM, ChatMessage, StreamEventType
from mycode.memory import InMemoryConversationMemory
from mycode.permission.service import PermissionInterceptor
from mycode.prompt.models import PromptContextBlock
from mycode.skill.context import (
    EphemeralContextManager,
    SkillContextTooLarge,
    build_summary_prompt,
    select_completed_turns,
)
from mycode.skill.models import (
    SkillContextStrategy,
    SkillDefinition,
    SkillExecutionResult,
    SkillExecutionScope,
    SkillRunContext,
)
from mycode.skill.runtime import SkillRuntime
from mycode.tool import ToolExecutor, ToolRegistry
from mycode.workspace import WorkspaceContext, WorkspaceKind


class SkillExecutor:
    def __init__(
        self,
        *,
        runtime: SkillRuntime,
        main_llm: BaseLLM,
        llm_config: LLMConfig,
        llm_factory: Callable[[LLMConfig], BaseLLM],
        tool_registry: ToolRegistry,
        tool_executor: ToolExecutor,
        permission: PermissionInterceptor,
        agent_config: AgentConfig,
        workspace_root: Path | None = None,
        workspace: WorkspaceContext | None = None,
    ) -> None:
        self._runtime = runtime
        self._main_llm = main_llm
        self._llm_config = llm_config
        self._llm_factory = llm_factory
        self._tool_registry = tool_registry
        self._tool_executor = tool_executor
        self._permission = permission
        self._agent_config = agent_config
        self._workspace = workspace or _shared_workspace_from_root(workspace_root)
        if workspace_root is not None and Path(workspace_root).resolve() != self._workspace.root:
            raise ValueError("workspace_root must match workspace.root")

    async def execute_isolated(
        self,
        definition: SkillDefinition,
        arguments: str,
        *,
        run_context: SkillRunContext,
        mode: AgentMode,
    ) -> SkillExecutionResult:
        try:
            selected_llm = self._select_llm(definition)
            history, framework_blocks = await self._prepare_context(definition, selected_llm, run_context)
            memory = InMemoryConversationMemory()
            for message in history:
                memory.append(message)
            context_manager = EphemeralContextManager(memory)
            loop = AgentLoop(
                llm=selected_llm,
                memory=memory,
                tool_executor=self._tool_executor,
                tool_registry=self._tool_registry,
                permission=self._permission,
                context_manager=context_manager,
                config=self._agent_config,
                skill_runtime=self._runtime,
                workspace=self._workspace,
            )
            summary = ""
            scope = SkillExecutionScope(
                name=definition.metadata.name,
                allowed_tools=frozenset(definition.metadata.allowed_tools),
            )
            # 独立历史只存在于临时 memory 和临时上下文管理器，最终只回收安全摘要。
            async for event in loop.run(
                definition.instruction.replace("{{arguments}}", arguments),
                mode=mode,
                approval_provider=run_context.approval_provider,
                initial_skill_scope=scope,
                initial_framework_blocks=framework_blocks,
                isolated_depth=run_context.isolated_depth + 1,
            ):
                if event.type is AgentEventType.FINAL_RESPONSE:
                    summary = event.content
                elif event.type is AgentEventType.ERROR:
                    return SkillExecutionResult(ok=False, summary=event.content, error_code=str(event.error_code))
            if not summary:
                return SkillExecutionResult(ok=False, summary="独立 Skill 未产生最终回复。", error_code="no_final_response")
            return SkillExecutionResult(ok=True, summary=summary)
        except SkillContextTooLarge:
            return SkillExecutionResult(
                ok=False,
                summary="独立 Skill 上下文过大。",
                error_code="independent_context_too_large",
            )
        except Exception:
            return SkillExecutionResult(
                ok=False,
                summary="独立 Skill 执行失败。",
                error_code="execution_error",
            )

    async def execute_loaded(self, definition: SkillDefinition, arguments: str) -> SkillExecutionResult:
        run_context = self._runtime.current_run_context()
        if run_context is None:
            return SkillExecutionResult(
                ok=False,
                summary="缺少父任务运行上下文。",
                error_code="missing_run_context",
            )
        return await self.execute_isolated(
            definition,
            arguments,
            run_context=run_context,
            mode=AgentMode(),
        )

    async def _prepare_context(
        self,
        definition: SkillDefinition,
        selected_llm: BaseLLM,
        run_context: SkillRunContext,
    ) -> tuple[tuple[ChatMessage, ...], tuple[PromptContextBlock, ...]]:
        policy = definition.metadata.context
        if policy is None or policy.strategy is SkillContextStrategy.NONE:
            return (), run_context.framework_blocks
        if policy.strategy is SkillContextStrategy.RECENT:
            return select_completed_turns(run_context.history, policy.turns), run_context.framework_blocks
        summary = await _summarize(selected_llm, tuple(run_context.history))
        block = PromptContextBlock(
            id="skill-summary",
            kind="skill",
            priority=-150,
            content=summary,
        )
        return (), run_context.framework_blocks + (block,)

    def _select_llm(self, definition: SkillDefinition) -> BaseLLM:
        if definition.metadata.model is None:
            return self._main_llm
        return self._llm_factory(replace(self._llm_config, model=definition.metadata.model))


async def _summarize(llm: BaseLLM, history: tuple[ChatMessage, ...]) -> str:
    parts: list[str] = []
    stream = llm.stream_chat([ChatMessage(role="user", content=build_summary_prompt(history))], tools=[]).__aiter__()
    async for event in stream:
        if event.type is StreamEventType.TEXT_DELTA:
            parts.append(event.content)
        elif event.type is StreamEventType.ERROR:
            break
    return "".join(parts)


def _shared_workspace_from_root(workspace_root: Path | None) -> WorkspaceContext:
    if workspace_root is None:
        raise ValueError("workspace or workspace_root is required")
    root = Path(workspace_root).resolve()
    return WorkspaceContext(
        kind=WorkspaceKind.SHARED,
        root=root,
        repository_root=root,
        repository_id="skill-workspace",
        task_identity=None,
        branch_name=None,
        hooks_path=None,
    )
