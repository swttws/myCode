from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable


class SlashCommandType(str, Enum):
    LOCAL = "local"
    UI_STATE = "ui_state"
    PROMPT = "prompt"


class SlashInputKind(str, Enum):
    EMPTY = "empty"
    NORMAL = "normal"
    COMMAND = "command"


class SlashHandlerSignal(str, Enum):
    CONTINUE = "continue"
    EXIT = "exit"


class SlashDispatchKind(str, Enum):
    EMPTY = "empty"
    NOT_COMMAND = "not_command"
    HANDLED = "handled"
    EXIT = "exit"


class SlashMode(str, Enum):
    DEFAULT = "default"
    PLAN = "plan"


@dataclass(frozen=True)
class ParsedSlashInput:
    kind: SlashInputKind
    text: str  # 去除首尾空白后的完整输入
    command_name: str | None = None  # 不含斜杠、已转小写的命令名
    arguments: str = ""  # 命令名之后的参数文本


@dataclass(frozen=True)
class SlashDispatchResult:
    kind: SlashDispatchKind  # 空输入、普通输入、已处理或退出
    normal_text: str = ""  # 仅普通输入携带规范化后的消息正文


@dataclass(frozen=True)
class SlashCompletionCandidate:
    text: str  # 插入输入框的完整命令或别名，包含斜杠
    description: str  # 补全菜单中展示的简短说明


SlashCommandHandler = Callable[
    ["SlashCommandContext", str],
    Awaitable[SlashHandlerSignal],
]


@dataclass(frozen=True)
class SlashCommand:
    name: str  # 不含斜杠的主名称
    aliases: tuple[str, ...]  # 不含斜杠的固定别名
    description: str  # 帮助列表使用的简短描述
    usage: str  # 可直接展示给用户的用法示例
    command_type: SlashCommandType  # 本地、界面状态或提示词命令
    handler: SlashCommandHandler  # 异步处理函数
    argument_hint: str | None = None  # 可选参数说明，不作为补全候选
    hidden: bool = False  # 是否从帮助和补全中隐藏


@dataclass(frozen=True)
class SlashCommandContext:
    controller: "SlashCommandController"  # 与具体终端框架解耦的控制接口
    registry: "SlashCommandRegistry"  # 供帮助命令读取公开元数据
