from __future__ import annotations

import json
from contextvars import ContextVar
from dataclasses import dataclass

from mycode.llm import ChatMessage
from mycode.permission.models import PermissionMode
from mycode.prompt.models import PromptBuildResult
from mycode.subagent.models import AgentRoleDefinition, ParentAgentSnapshot
from mycode.tool import ToolDefinition


@dataclass(frozen=True)
class ForkPrompt:
    messages: tuple[ChatMessage, ...]
    tools: tuple[ToolDefinition, ...]


class ParentAgentSnapshotStore:
    def __init__(self) -> None:
        self._current: ContextVar[ParentAgentSnapshot | None] = ContextVar(
            "subagent_parent_snapshot",
            default=None,
        )

    def update(
        self,
        request: PromptBuildResult,
        *,
        model_id: str,
        max_rounds: int,
        permission_mode: PermissionMode,
    ) -> None:
        snapshot = ParentAgentSnapshot(
            messages=tuple(request.messages),
            tools=tuple(_freeze_tool_definition(tool) for tool in request.tools),
            model_id=model_id,
            max_rounds=max_rounds,
            permission_mode=permission_mode,
        )
        self._current.set(snapshot)

    def current(self) -> ParentAgentSnapshot:
        snapshot = self._current.get()
        if snapshot is None:
            raise RuntimeError("parent snapshot is not available.")
        return snapshot


def build_defined_agent_messages(
    *,
    role: AgentRoleDefinition,
    task: str,
    workspace_environment: str,
    project_instructions: tuple[str, ...] = (),
) -> tuple[ChatMessage, ...]:
    instructions = "\n\n".join(project_instructions) if project_instructions else "无项目指令。"
    return (
        ChatMessage(
            role="system",
            content=(
                "核心系统规则：你是 myCode 启动的非交互子 Agent。"
                "你必须独立运行到底，不能向用户提问或等待审批。"
            ),
        ),
        ChatMessage(
            role="system",
            content=f"工作区环境：\n{workspace_environment}",
        ),
        ChatMessage(
            role="system",
            content=f"项目指令：\n{instructions}",
        ),
        ChatMessage(
            role="system",
            content=f"固定角色：\n{role.instruction}",
        ),
        ChatMessage(
            role="user",
            content=f"本次子 Agent 任务：\n{task}",
        ),
    )


def build_fork_prompt(
    parent: ParentAgentSnapshot,
    *,
    task: str,
    child_messages: tuple[ChatMessage, ...] = (),
) -> ForkPrompt:
    fork_instruction = ChatMessage(
        role="system",
        content=(
            "Fork 子 Agent 任务：以下任务在冻结的父对话前缀之后执行。"
            "只能使用已经继承的上下文，不要假设父 Agent 后续消息会进入本任务。\n"
            f"{task}"
        ),
    )
    return ForkPrompt(
        messages=(*parent.messages, fork_instruction, *child_messages),
        tools=parent.tools,
    )


def _freeze_tool_definition(definition: ToolDefinition) -> ToolDefinition:
    parameters = json.loads(json.dumps(definition.parameters))
    return ToolDefinition(
        name=definition.name,
        description=definition.description,
        parameters=parameters,
        kind=definition.kind,
        grant_arguments=tuple(definition.grant_arguments),
        parallel_safe=definition.parallel_safe,
        runtime_scope=definition.runtime_scope,
        execution_timeout_seconds=definition.execution_timeout_seconds,
    )
