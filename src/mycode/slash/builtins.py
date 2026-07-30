from __future__ import annotations

import inspect
import logging
from collections.abc import Sequence

from mycode.permission.models import PermissionMode, RuleSource
from mycode.slash.controller import SlashCommandController
from mycode.slash.models import (
    SlashCommand,
    SlashCommandContext,
    SlashCommandType,
    SlashHandlerSignal,
    SlashMode,
)
from mycode.slash.registry import SlashCommandRegistry
from mycode.slash.status import format_application_status


logger = logging.getLogger(__name__)

_COMMAND_TYPE_LABELS = {
    SlashCommandType.LOCAL: "本地",
    SlashCommandType.UI_STATE: "界面状态",
    SlashCommandType.PROMPT: "预设提示词",
}

_PUBLIC_COMMAND_ORDER: tuple[str, ...] = (
    "help",
    "compact",
    "clear",
    "plan",
    "do",
    "session",
    "memory",
    "tasks",
    "task",
    "permission",
    "status",
)


async def handle_help(context: SlashCommandContext, arguments: str) -> SlashHandlerSignal:
    query = arguments.strip()
    if not query:
        context.controller.show_message(_format_help_overview(context.registry))
        return SlashHandlerSignal.CONTINUE

    if len(query.split()) != 1:
        context.controller.show_message(
            f"用法：{_command_usage(context.registry, 'help')}",
            error=True,
        )
        return SlashHandlerSignal.CONTINUE

    command = context.registry.resolve(_normalize_identifier(query), include_hidden=False)
    if command is None:
        context.controller.show_message(
            f"未找到命令 /{_normalize_identifier(query)}。请使用 /help 查看可用命令。",
            error=True,
        )
        return SlashHandlerSignal.CONTINUE

    context.controller.show_message(_format_help_detail(command))
    return SlashHandlerSignal.CONTINUE


async def handle_compact(context: SlashCommandContext, arguments: str) -> SlashHandlerSignal:
    if _has_arguments(arguments):
        _show_usage(context.controller, "compact")
        return SlashHandlerSignal.CONTINUE
    await context.controller.compact_context()
    return SlashHandlerSignal.CONTINUE


async def handle_clear(context: SlashCommandContext, arguments: str) -> SlashHandlerSignal:
    if _has_arguments(arguments):
        _show_usage(context.controller, "clear")
        return SlashHandlerSignal.CONTINUE
    result = context.controller.clear_session()
    if inspect.isawaitable(result):
        await result
    context.controller.show_message("上下文已清空。")
    return SlashHandlerSignal.CONTINUE


async def handle_plan(context: SlashCommandContext, arguments: str) -> SlashHandlerSignal:
    if _has_arguments(arguments):
        _show_usage(context.controller, "plan")
        return SlashHandlerSignal.CONTINUE
    _set_mode_if_needed(context.controller, SlashMode.PLAN)
    context.controller.show_message(_mode_message(SlashMode.PLAN))
    return SlashHandlerSignal.CONTINUE


async def handle_do(context: SlashCommandContext, arguments: str) -> SlashHandlerSignal:
    if _has_arguments(arguments):
        _show_usage(context.controller, "do")
        return SlashHandlerSignal.CONTINUE
    _set_mode_if_needed(context.controller, SlashMode.DEFAULT)
    context.controller.show_message(_mode_message(SlashMode.DEFAULT))
    return SlashHandlerSignal.CONTINUE


async def handle_session(context: SlashCommandContext, arguments: str) -> SlashHandlerSignal:
    if _has_arguments(arguments):
        _show_usage(context.controller, "session")
        return SlashHandlerSignal.CONTINUE
    snapshot = await context.controller.session_status()
    context.controller.show_message(_format_session_status(snapshot))
    return SlashHandlerSignal.CONTINUE


async def handle_memory(context: SlashCommandContext, arguments: str) -> SlashHandlerSignal:
    if _has_arguments(arguments):
        _show_usage(context.controller, "memory")
        return SlashHandlerSignal.CONTINUE
    snapshot = await context.controller.memory_status()
    context.controller.show_message(_format_memory_status(snapshot))
    return SlashHandlerSignal.CONTINUE


async def handle_tasks(context: SlashCommandContext, arguments: str) -> SlashHandlerSignal:
    if _has_arguments(arguments):
        _show_usage(context.controller, "tasks")
        return SlashHandlerSignal.CONTINUE
    context.controller.show_message(_format_subagent_task_list(context.controller.list_subagent_tasks()))
    return SlashHandlerSignal.CONTINUE


async def handle_task(context: SlashCommandContext, arguments: str) -> SlashHandlerSignal:
    parts = arguments.split()
    if len(parts) != 1:
        _show_usage(context.controller, "task")
        return SlashHandlerSignal.CONTINUE
    task_id = parts[0]
    try:
        snapshot = context.controller.get_subagent_task(task_id)
    except KeyError:
        context.controller.show_message(
            f"task_not_found：未找到子 Agent 任务 {task_id}",
            error=True,
        )
        return SlashHandlerSignal.CONTINUE
    context.controller.show_message(_format_subagent_task_detail(snapshot))
    return SlashHandlerSignal.CONTINUE


async def handle_permission(context: SlashCommandContext, arguments: str) -> SlashHandlerSignal:
    query = arguments.strip()
    if not query:
        snapshot = context.controller.permission_status()
        context.controller.show_message(_format_permission_status(snapshot))
        return SlashHandlerSignal.CONTINUE

    if len(query.split()) != 1:
        _show_usage(context.controller, "permission")
        return SlashHandlerSignal.CONTINUE

    try:
        mode = PermissionMode(query.casefold())
    except ValueError:
        _show_usage(context.controller, "permission")
        return SlashHandlerSignal.CONTINUE

    context.controller.set_permission_mode(mode)
    context.controller.show_message(
        f"会话权限档位已设为 {_permission_mode_label(mode)}。"
    )
    return SlashHandlerSignal.CONTINUE


async def handle_status(context: SlashCommandContext, arguments: str) -> SlashHandlerSignal:
    if _has_arguments(arguments):
        _show_usage(context.controller, "status")
        return SlashHandlerSignal.CONTINUE
    try:
        snapshot = await context.controller.application_status()
        text = format_application_status(snapshot)
    except Exception:
        logger.exception("slash status rendering failed")
        context.controller.show_message("slash_status_failed", error=True)
        return SlashHandlerSignal.CONTINUE
    context.controller.show_message(text)
    return SlashHandlerSignal.CONTINUE


async def handle_exit(context: SlashCommandContext, arguments: str) -> SlashHandlerSignal:
    del context, arguments
    return SlashHandlerSignal.EXIT


def create_default_slash_registry() -> SlashCommandRegistry:
    return SlashCommandRegistry(_default_commands())


def _default_commands() -> tuple[SlashCommand, ...]:
    return (
        SlashCommand(
            name="help",
            aliases=("h", "?"),
            description="显示帮助和命令详情",
            usage="/help [command]",
            command_type=SlashCommandType.LOCAL,
            handler=handle_help,
            argument_hint="[command]",
        ),
        SlashCommand(
            name="compact",
            aliases=("comp",),
            description="触发手动上下文压缩",
            usage="/compact",
            command_type=SlashCommandType.LOCAL,
            handler=handle_compact,
        ),
        SlashCommand(
            name="clear",
            aliases=("cls",),
            description="清空当前会话上下文",
            usage="/clear",
            command_type=SlashCommandType.UI_STATE,
            handler=handle_clear,
        ),
        SlashCommand(
            name="plan",
            aliases=("p",),
            description="开启计划模式",
            usage="/plan",
            command_type=SlashCommandType.UI_STATE,
            handler=handle_plan,
        ),
        SlashCommand(
            name="do",
            aliases=("d",),
            description="退出计划模式",
            usage="/do",
            command_type=SlashCommandType.UI_STATE,
            handler=handle_do,
        ),
        SlashCommand(
            name="session",
            aliases=("sess",),
            description="显示当前会话摘要",
            usage="/session",
            command_type=SlashCommandType.LOCAL,
            handler=handle_session,
        ),
        SlashCommand(
            name="memory",
            aliases=("mem",),
            description="显示记忆摘要",
            usage="/memory",
            command_type=SlashCommandType.LOCAL,
            handler=handle_memory,
        ),
        SlashCommand(
            name="tasks",
            aliases=(),
            description="列出当前会话的子 Agent 任务",
            usage="/tasks",
            command_type=SlashCommandType.LOCAL,
            handler=handle_tasks,
        ),
        SlashCommand(
            name="task",
            aliases=(),
            description="显示指定子 Agent 任务详情",
            usage="/task <id>",
            command_type=SlashCommandType.LOCAL,
            handler=handle_task,
            argument_hint="<id>",
        ),
        SlashCommand(
            name="permission",
            aliases=("perm",),
            description="查询或设置会话权限档位",
            usage="/permission [strict|default|permissive]",
            command_type=SlashCommandType.UI_STATE,
            handler=handle_permission,
            argument_hint="[strict|default|permissive]",
        ),
        SlashCommand(
            name="status",
            aliases=("stat",),
            description="显示当前综合状态",
            usage="/status",
            command_type=SlashCommandType.LOCAL,
            handler=handle_status,
        ),
        SlashCommand(
            name="exit",
            aliases=("quit",),
            description="退出应用",
            usage="/exit",
            command_type=SlashCommandType.UI_STATE,
            handler=handle_exit,
            hidden=True,
        ),
    )


def _format_help_overview(registry: SlashCommandRegistry) -> str:
    lines = ["可用命令："]
    for command in registry.public_commands():
        lines.append(f"/{command.name} - {command.description}")
        lines.append(f"  用法：{command.usage}")
    return "\n".join(lines)


def _format_help_detail(command: SlashCommand) -> str:
    aliases = ", ".join(f"/{alias}" for alias in command.aliases) if command.aliases else "无"
    return "\n".join(
        [
            f"命令：/{command.name}",
            f"别名：{aliases}",
            f"描述：{command.description}",
            f"用法：{command.usage}",
            f"类型：{_command_type_label(command.command_type)}",
            f"参数提示：{command.argument_hint or '无'}",
        ]
    )


def _format_permission_status(snapshot) -> str:
    mode = _permission_mode_label(getattr(snapshot, "mode", PermissionMode.DEFAULT))
    source = getattr(snapshot, "source", None)
    source_label = _source_label(source)
    return f"当前权限档位：{mode}（{source_label}）"


def _format_session_status(snapshot) -> str:
    session_id = _text(getattr(snapshot, "session_id", None), default="未知")
    message_count = int(getattr(snapshot, "message_count", 0) or 0)
    source = _text(getattr(snapshot, "source", None), default="unknown")
    restored_from = _text(getattr(snapshot, "restored_from_session_id", None), default="")
    updated_at = _text(getattr(snapshot, "updated_at", None), default="未知")
    source_text = source if not restored_from else f"{source} ({restored_from})"
    return "\n".join(
        [
            f"会话 ID：{session_id}",
            f"消息数：{message_count}",
            f"来源：{source_text}",
            f"最近更新时间：{updated_at}",
        ]
    )


def _format_memory_status(snapshot) -> str:
    user = getattr(snapshot, "user", None)
    project = getattr(snapshot, "project", None)
    return "\n".join(
        [
            _format_memory_scope("用户记忆", user),
            _format_memory_scope("项目记忆", project),
        ]
    )


def _format_memory_scope(label: str, scope) -> str:
    if scope is None:
        return f"{label}：未知"
    path = _text(getattr(scope, "path", None), default="未知")
    note_count = int(getattr(scope, "note_count", 0) or 0)
    line_count = int(getattr(scope, "index_line_count", getattr(scope, "line_count", 0)) or 0)
    byte_count = int(getattr(scope, "index_byte_count", getattr(scope, "byte_count", 0)) or 0)
    diagnostic_source = getattr(scope, "diagnostic_codes", getattr(scope, "diagnostics", ()))
    diagnostics = tuple(
        _text(item) for item in diagnostic_source if _text(item)
    )
    diagnostics_text = ", ".join(diagnostics) if diagnostics else "无"
    return (
        f"{label}：{path}\n"
        f"  笔记数：{note_count}\n"
        f"  索引：{line_count} 行 / {byte_count} 字节\n"
        f"  最近诊断：{diagnostics_text}"
    )


def _format_subagent_task_list(summaries) -> str:
    summaries = tuple(sorted(summaries, key=lambda item: getattr(item, "sequence", 0)))
    if not summaries:
        return "当前会话没有子 Agent 任务。"
    lines = ["子 Agent 任务："]
    for summary in summaries:
        lines.append(
            " - "
            f"{summary.id} "
            f"{_enum_text(summary.kind)} "
            f"role={_optional(summary.role_name)} "
            f"state={_enum_text(summary.state)} "
            f"rounds={summary.rounds} "
            f"detached={str(summary.detached).lower()} "
            f"error={_optional(summary.error_code)} "
            f"usage={_format_usage(summary.usage)}"
        )
    return "\n".join(lines)


def _format_subagent_task_detail(snapshot) -> str:
    lines = [
        f"任务 ID：{snapshot.id}",
        f"类型：{_enum_text(snapshot.kind)}",
        f"角色：{_optional(snapshot.role_name)}",
        f"状态：{_enum_text(snapshot.state)}",
        f"轮次：{snapshot.rounds}",
        f"后台：{str(snapshot.detached).lower()}",
        f"错误码：{_optional(snapshot.error_code)}",
        f"错误信息：{_optional(snapshot.error_message)}",
        f"usage：{_format_usage(snapshot.usage)}",
    ]
    if snapshot.result is not None:
        lines.extend(
            [
                "结果摘要：",
                snapshot.result.summary,
                "完整结果：",
                snapshot.result.detail,
            ]
        )
    return "\n".join(lines)


def _format_usage(usage) -> str:
    return (
        f"input={_value_or_unknown(getattr(usage, 'input_tokens', None))}, "
        f"output={_value_or_unknown(getattr(usage, 'output_tokens', None))}, "
        f"total={_value_or_unknown(getattr(usage, 'total_tokens', None))}, "
        f"cache-read={_value_or_unknown(getattr(usage, 'cache_read_tokens', None))}, "
        f"cache-write={_value_or_unknown(getattr(usage, 'cache_write_tokens', None))}"
    )


def _value_or_unknown(value) -> str:
    return "未知" if value is None else str(value)


def _optional(value) -> str:
    return "无" if value is None else str(value)


def _enum_text(value) -> str:
    enum_value = getattr(value, "value", None)
    return enum_value if isinstance(enum_value, str) else str(value)


def _show_usage(controller: SlashCommandController, command_name: str) -> None:
    command = _COMMAND_BY_NAME[command_name]
    controller.show_message(f"用法：{command.usage}", error=True)


def _command_usage(registry: SlashCommandRegistry, name: str) -> str:
    command = registry.resolve(name, include_hidden=True)
    if command is None:
        return f"/{name}"
    return command.usage


def _set_mode_if_needed(controller: SlashCommandController, mode: SlashMode) -> None:
    current_mode = controller.current_mode()
    if current_mode is not mode:
        controller.set_mode(mode)


def _mode_message(mode: SlashMode) -> str:
    if mode is SlashMode.PLAN:
        return "[PLAN] 计划模式已开启。"
    return "[DEFAULT] 计划模式已关闭。"


def _permission_mode_label(mode: PermissionMode | object) -> str:
    if isinstance(mode, PermissionMode):
        return mode.value
    return _text(mode, default="unknown")


def _command_type_label(command_type: SlashCommandType) -> str:
    return _COMMAND_TYPE_LABELS.get(command_type, command_type.value)


def _source_label(source: RuleSource | object | None) -> str:
    if source is None:
        return "unknown"
    if isinstance(source, RuleSource):
        return source.value
    return _text(source, default="unknown")


def _normalize_identifier(identifier: str) -> str:
    return identifier.removeprefix("/")


def _has_arguments(arguments: str) -> bool:
    return bool(arguments.strip())


def _text(value, *, default: str = "") -> str:
    if value is None:
        return default
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str):
        return enum_value
    if isinstance(value, str):
        return value
    return str(value)


_COMMAND_BY_NAME = {
    command.name: command
    for command in _default_commands()
}


__all__ = [
    "create_default_slash_registry",
    "handle_clear",
    "handle_compact",
    "handle_do",
    "handle_exit",
    "handle_help",
    "handle_memory",
    "handle_permission",
    "handle_plan",
    "handle_session",
    "handle_status",
    "handle_task",
    "handle_tasks",
]
