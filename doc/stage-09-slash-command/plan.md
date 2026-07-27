# Stage 09 斜杠命令注册与分发 Plan

## 架构概览

本阶段采用“命令核心、内置命令、状态快照、界面控制、终端适配、应用组装”六个协作边界。

1. **命令核心层**  
   `slash` 包定义不可变命令元数据、三种命令类型、解析结果和分发结果。注册中心在构造时一次性规范化并校验全部名称与别名，之后只提供查找、公开命令枚举和补全候选。

2. **内置命令层**  
   十个公开命令和隐藏退出命令集中登记。处理函数只接收参数、注册中心和界面控制接口，不导入 TUI、Rich、Agent Loop 或底层存储实现。

3. **状态快照层**  
   Token、会话、记忆、Git 和 MCP 状态先转换成不可变快照，再由命令格式化为纯文本。Token、会话和记忆在各自领域补充公开只读接口；Git 使用稳定 porcelain 输出；MCP 只读取连接池已有公开状态。

4. **界面控制层**  
   `slash` 包定义控制协议，覆盖显示消息、发送用户消息、手动压缩、清空、模式切换、权限查询与设置、状态查询。`ChatTUI` 实现该协议，负责把纯文本消息和 Agent 事件渲染到终端。

5. **终端适配层**  
   独立补全器把注册中心候选转换为 `prompt_toolkit` 补全项。增强终端配置补全菜单和动态底部状态栏；普通输入降级路径只使用带模式标记的提示符。

6. **应用组装层**  
   CLI 在进入交互循环前创建默认注册中心和分发器。注册冲突在此阶段成为启动错误；TUI 输入循环只保留“调用分发器或发送普通消息”两条路径。

依赖方向保持单向：

```text
CLI
 ├─> Slash Registry / Dispatcher
 ├─> Git/MCP status collectors
 └─> ChatTUI implements Slash Controller
       ├─> ChatSession
       └─> Slash Dispatcher
             └─> Built-in Handler -> Slash Controller
```

这样新增命令只增加登记项和处理函数，不修改解析器或 TUI 输入条件分支。

## 核心数据结构

### 命令模型

```python
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
```

名称和别名内部不带 `/`，使用 `casefold()` 建立索引。登记时拒绝空值、前导斜杠、空白字符和重复标识；`?` 是合法别名。

### 状态快照

```python
@dataclass(frozen=True)
class ContextTokenStatus:
    estimated_tokens: int  # 当前完整请求上下文的估算 Token
    context_window_tokens: int  # 当前模型配置的上下文窗口上限
    usage_ratio: float  # 估算值占窗口上限的比例，范围为 0 到 1
    source: str  # 估算来源，例如 full_chars 或 usage_delta


class SessionSource(str, Enum):
    NEW = "new"
    RESTORED = "restored"


@dataclass(frozen=True)
class SessionStatusSnapshot:
    session_id: str  # 当前正在追加写入的会话 ID
    message_count: int  # 当前内存消息数，包含已恢复历史
    source: SessionSource  # 当前内存来自新会话还是恢复会话
    restored_from_session_id: str | None  # 被恢复的旧会话 ID
    updated_at: str | None  # 最近会话活动时间


@dataclass(frozen=True)
class MemoryScopeStatus:
    scope: MemoryScope  # 用户级或项目级记忆
    path: str  # 对应记忆目录的绝对路径
    note_count: int  # 当前有效笔记文件数量
    index_line_count: int  # 索引实际载入行数
    index_byte_count: int  # 索引实际载入字节数
    diagnostic_codes: tuple[str, ...]  # 已脱敏的最近诊断代码


@dataclass(frozen=True)
class MemoryStatusSnapshot:
    user: MemoryScopeStatus  # 用户级记忆摘要
    project: MemoryScopeStatus  # 当前项目记忆摘要
    diagnostic_codes: tuple[str, ...]  # 跨作用域或后台诊断代码


@dataclass(frozen=True)
class PermissionStatusSnapshot:
    mode: PermissionMode  # 当前生效的权限档位
    source: RuleSource | None  # 产生当前档位的规则来源


@dataclass(frozen=True)
class GitStatusSnapshot:
    is_repository: bool  # 工作区是否位于 Git 仓库中
    repository_root: str | None  # Git 仓库根目录
    branch: str | None  # 当前分支或 detached HEAD 标记
    upstream: str | None  # 当前分支的上游引用
    ahead: int  # 本地领先上游的提交数
    behind: int  # 本地落后上游的提交数
    staged: int  # 已暂存文件数量
    unstaged: int  # 未暂存文件数量
    untracked: int  # 未跟踪文件数量


@dataclass(frozen=True)
class MCPServerStatus:
    name: str  # MCP 服务配置名称
    state: MCPServerState  # 连接池当前缓存状态
    available: bool  # 当前是否可以直接调用
    tool_count: int  # 当前已发现的远端工具数量
    diagnostic_categories: tuple[str, ...]  # 最近诊断类别，不含敏感配置


@dataclass(frozen=True)
class MCPStatusSnapshot:
    servers: tuple[MCPServerStatus, ...]  # 按配置顺序排列的服务状态


StatusValue = TypeVar("StatusValue")


@dataclass(frozen=True)
class StatusSection(Generic[StatusValue]):
    value: StatusValue | None  # 成功取得的状态快照
    error: str | None = None  # 已脱敏的单项失败原因


@dataclass(frozen=True)
class ApplicationStatusSnapshot:
    workspace_root: str  # 当前应用工作区绝对路径
    mode: SlashMode  # 当前 DEFAULT 或 PLAN 模式
    permission: StatusSection[PermissionStatusSnapshot]  # 权限状态
    token: StatusSection[ContextTokenStatus]  # Token 状态
    session: StatusSection[SessionStatusSnapshot]  # 当前会话状态
    memory: StatusSection[MemoryStatusSnapshot]  # 长期记忆状态
    git: StatusSection[GitStatusSnapshot]  # 本地 Git 状态
    mcp: StatusSection[MCPStatusSnapshot]  # MCP 连接池状态
```

Python 3.10 使用 `TypeVar` 和 `Generic` 定义 `StatusSection`，不采用 Python 3.12 类型参数语法。

会话快照语义固定为：

- `session_id` 是当前正在追加写入的 JSONL 会话。
- `message_count` 是当前内存中的完整消息数，包含已恢复历史。
- `source` 表示当前内存是否恢复过旧会话。
- `updated_at` 优先取当前写入会话最后记录时间；尚未写入时回退到被恢复会话更新时间。

Token 快照估算当前历史、系统提示、当前工具定义和最近一次框架上下文，不虚构下一条用户消息，也不触发上下文刷新或模型调用。

## 核心接口

### 注册与分发

```python
class SlashCommandRegistrationError(RuntimeError):
    """命令主名称或别名在启动注册阶段发生冲突。"""


class SlashCommandRegistry:
    def __init__(self, commands: Sequence[SlashCommand]) -> None: ...

    def resolve(
        self,
        name: str,
        *,
        include_hidden: bool = True,
    ) -> SlashCommand | None: ...

    def public_commands(self) -> tuple[SlashCommand, ...]: ...

    def completion_candidates(
        self,
        prefix: str,
    ) -> tuple[SlashCompletionCandidate, ...]: ...


def parse_slash_input(text: str) -> ParsedSlashInput: ...


class SlashCommandDispatcher:
    def __init__(self, registry: SlashCommandRegistry) -> None: ...

    async def dispatch(
        self,
        text: str,
        controller: "SlashCommandController",
    ) -> SlashDispatchResult: ...
```

注册中心在发布字段前使用临时列表和索引完成全部校验，避免冲突时留下部分注册结果。公开命令和补全候选保持登记顺序。帮助详情通过 `include_hidden=False` 查找，确保隐藏命令不能被显式查询。

### 界面控制

```python
class SlashCommandController(Protocol):
    def show_message(self, text: str, *, error: bool = False) -> None: ...

    async def send_user_message(self, text: str) -> None: ...
    async def compact_context(self) -> None: ...

    def clear_session(self) -> None: ...
    def current_mode(self) -> SlashMode: ...
    def set_mode(self, mode: SlashMode) -> None: ...

    def permission_status(self) -> PermissionStatusSnapshot: ...
    def set_permission_mode(self, mode: PermissionMode) -> None: ...

    async def token_status(self) -> ContextTokenStatus: ...
    async def session_status(self) -> SessionStatusSnapshot: ...
    async def memory_status(self) -> MemoryStatusSnapshot: ...
    async def application_status(self) -> ApplicationStatusSnapshot: ...
```

所有命令输出均为纯文本；终端样式只由控制实现决定。退出由处理函数返回 `EXIT`，不通过控制接口修改循环私有状态。

### 领域只读接口

```python
class ContextManager:
    def estimate_current(self, *, build_request) -> ContextTokenStatus: ...


class SessionArchiveStore:
    def current_summary(self) -> SessionSummary: ...


class ProjectMemoryManager:
    def session_status(self) -> SessionStatusSnapshot: ...
    def memory_status(self) -> MemoryStatusSnapshot: ...


class AgentLoop:
    def context_token_status(self, *, mode: AgentMode) -> ContextTokenStatus: ...
    def session_status(self) -> SessionStatusSnapshot: ...
    def memory_status(self) -> MemoryStatusSnapshot: ...


class ChatSession:
    def context_token_status(self) -> ContextTokenStatus: ...
    def session_status(self) -> SessionStatusSnapshot: ...
    def memory_status(self) -> MemoryStatusSnapshot: ...
```

`ProjectMemoryManager` 保存 `_restored_from_session_id` 和最近一次请求诊断；`/clear` 将恢复来源清空。状态输出仅保留最近十个去重后的诊断代码，不返回诊断正文。

## 模块设计

### `slash.models`

**职责：** 定义命令、解析结果、分发结果、模式和应用状态快照等不可变模型。

**依赖：** 标准库，以及现有权限、记忆、压缩和 MCP 枚举。

### `slash.registry`

**职责：**

- 接收完整命令序列并原子构建索引。
- 使用 `casefold()` 检测主名称、别名和大小写变体冲突。
- 提供公开命令列表、包含隐藏命令的运行时查找和公开补全候选。
- 保持注册顺序，不允许运行时修改。

**失败：** 抛出 `SlashCommandRegistrationError`，包含冲突标识和两条命令的主名称。

### `slash.parser`

**职责：**

- 统一处理空输入、普通输入和斜杠输入。
- 去除整体首尾空白。
- 使用第一次空白分隔命令名与参数。
- 命令名去掉 `/` 后执行 `casefold()`；参数去除分隔处多余空白，但保留内部空白。

### `slash.dispatcher`

**职责：**

- 调用解析器并返回四种分发结果。
- 未知命令显示原名称与 `/help` 引导。
- 创建命令上下文并调用处理函数。
- 捕获处理函数异常，向用户显示稳定错误码，把详细异常写入开发日志。

### `slash.controller`

**职责：** 保存 `SlashCommandController` 协议，不包含终端实现。该模块是内置处理函数与 TUI 之间唯一允许的依赖边界。

### `slash.builtins`

**职责：**

- 定义固定审查提示词。
- 实现十个公开处理函数和隐藏退出处理函数。
- 创建默认注册中心。
- 校验无参数命令的参数并统一显示用法。
- 把单项状态快照格式化为纯文本。

内置登记固定为：

| 命令 | 别名 | 类型 | 参数 |
|---|---|---|---|
| `/help` | `/h`, `/?` | 本地 | `[command]` |
| `/compact` | `/comp` | 本地 | 无 |
| `/clear` | `/cls` | 界面状态 | 无 |
| `/plan` | `/p` | 界面状态 | 无 |
| `/do` | `/d` | 界面状态 | 无 |
| `/session` | `/sess` | 本地 | 无 |
| `/memory` | `/mem` | 本地 | 无 |
| `/permission` | `/perm` | 界面状态 | `[strict\|default\|permissive]` |
| `/status` | `/stat` | 本地 | 无 |
| `/review` | `/rev` | 预设提示词 | 无 |
| `/exit` | `/quit` | 界面状态、隐藏 | 无 |

`/compact` 的类型表示它绕过普通 Agent 对话分支；压缩器内部沿用摘要模型不改变路由分类。

固定审查提示词为：

```text
请审查当前 Git 工作区的所有未提交改动，包括已暂存、未暂存和未跟踪文件，并忽略 Git 已忽略文件。优先查找会导致错误行为的缺陷、行为回归、安全风险和缺失测试。请先按严重程度列出发现，并给出对应文件与位置；如果没有发现，明确说明，并指出剩余测试风险。
```

### `slash.status`

**职责：**

- 通过 `git status --porcelain=v2 --branch --untracked-files=all` 获取机器格式状态。
- 通过 `git rev-parse --show-toplevel` 确认仓库根目录。
- 解析分支、上游、ahead/behind 和三类文件计数。
- 从 MCP 连接池公开接口读取服务状态、可用性、工具数量和诊断类别。
- 提供逐项异常封装，确保综合状态的一个数据源失败不影响其他项。
- 按固定顺序把综合状态格式化为纯文本。

同一文件同时处于 staged 和 unstaged 时分别计入两个计数。重命名和冲突记录按 porcelain v2 的 XY 状态计算，不依赖面向用户的本地化文本。

### `slash.completion`

**职责：**

- 实现 `prompt_toolkit.completion.Completer`。
- 只在光标前文本以 `/` 开头且尚未出现参数空白时返回候选。
- 把注册中心候选转换为 `Completion`，描述写入 `display_meta`。
- 用 `start_position` 覆盖当前完整命令片段，使大小写输入也被规范候选替换。
- 隐藏命令和参数位置返回空候选。

本机安装版本为 `prompt_toolkit 3.0.52`。已通过实际签名和源码确认 `PromptSession`、`prompt_async()`、`Completion`、动态 `bottom_toolbar`、`complete_while_typing` 与 `CompleteStyle.COLUMN` 的用法。当前任务未暴露 Context7 的 `resolve-library-id` 和 `query-docs`，因此没有远端 Context7 查询结果。

### 现有领域扩展

- **上下文管理：** 增加当前请求 Token 的只读估算，不压缩、不刷新长期上下文、不调用模型。
- **项目记忆：** 增加会话和双作用域记忆快照，并记录本进程是否恢复过旧会话。
- **Agent Loop：** 使用当前历史、工具定义、提示构造器和最近框架上下文生成 Token 快照。
- **ChatSession：** 转发 Token、会话和记忆快照，继续作为 TUI 的会话边界。
- **ChatTUI：** 实现控制协议，持有分发器和 MCP 连接池；输入循环不再包含具体命令判断。
- **CLI：** 在 TUI 启动前创建默认注册中心和分发器；注册错误作为启动错误返回退出码 `1`。

## 模块交互

### 启动流程

```text
CLI
  -> 创建完整内置命令序列
  -> SlashCommandRegistry 原子校验并构建
     -> 冲突：输出注册错误，返回 1，不启动 TUI
     -> 成功：创建 Dispatcher 和 Completer
  -> 注入 ChatTUI
  -> 进入输入循环
```

### 输入分流

```text
读取原始输入
   |
   v
dispatcher.dispatch()
   |
   +-- EMPTY ----------> 继续读取
   |
   +-- NOT_COMMAND ----> TUI 发送 normal_text 给 ChatSession
   |
   +-- HANDLED --------> 命令已完成，继续读取
   |
   +-- EXIT -----------> 正常退出 TUI
```

普通消息只经过一次解析。未知斜杠输入由分发器显示错误后返回 `HANDLED`，不会落入普通对话。

### 本地和界面命令

1. 分发器通过注册中心解析主名称或别名。
2. 处理函数校验参数。
3. 处理函数通过控制接口查询状态或执行动作。
4. 输出以纯文本交回 TUI。
5. 分发器捕获未处理异常并输出命令错误。
6. 除 `/compact` 的既有摘要流程外，不进入模型调用链。

`/clear` 调用顺序保持为：清上下文和归档状态、复位计划模式、清会话级权限、将会话来源标记为新会话。

### `/review` 提示词命令

```text
Dispatcher
  -> /review handler
  -> controller.send_user_message(固定提示词)
  -> ChatTUI._render_stream(固定提示词)
  -> ChatSession.send()
  -> AgentLoop.run()
  -> 标准 AgentEvent 流
  -> 正常会话与长期会话存档
```

长期会话看到的是展开后的固定提示词，不是 `/review` 字面量。

### 状态采集

`/session`、`/memory` 和 Token 查询分别读取单项快照，不执行 Git 或 MCP 检查。

`/status` 按以下方式采集：

- 模式与权限直接读取当前会话。
- Token 通过 Agent 的只读请求构造和现有估算器计算。
- 会话和记忆磁盘扫描通过 `asyncio.to_thread()` 执行。
- Git 命令通过 `asyncio.to_thread()` 执行。
- MCP 状态直接读取连接池内存快照。
- 每项独立包装为 `StatusSection`，最后按固定顺序渲染。

会话恢复仍遵守 Stage 08：只有普通消息或 `/review` 进入 Agent 时才触发首次恢复。启动后直接执行 `/session` 不恢复旧会话，此时来源显示 `new`。完成首次恢复后，快照记录被恢复的旧会话 ID。

### Token 估算

1. Agent 保存最近一次成功准备的框架上下文。
2. 状态查询使用当前模式创建只读 turn context。
3. 请求包含当前历史、系统提示、当前工具定义、延迟工具提醒和最近框架上下文。
4. 上下文管理器只调用估算器，不压缩历史、不更新 usage 锚点、不递增真实 turn ID。
5. 尚无框架上下文时使用空框架块，结果仍标注估算来源。

### 补全与状态栏

增强终端首次使用时创建一个长期复用的 `PromptSession`：

```python
PromptSession(
    completer=SlashCommandCompleter(registry),
    complete_while_typing=False,
    complete_style=CompleteStyle.COLUMN,
    bottom_toolbar=mode_toolbar_callable,
)
```

- Tab 触发补全；单候选直接替换，多候选由内置菜单选择。
- 状态栏 callable 每次渲染读取当前模式。
- `/plan` 或 `/do` 完成后，下一次输入提示显示新标记。
- 普通输入降级路径使用 `"[PLAN] you> "` 或 `"[DEFAULT] you> "`。

## 文件组织

```text
src/mycode/
├── slash/
│   ├── __init__.py       — 稳定导出、默认注册中心创建入口
│   ├── models.py         — 命令、解析、分发、模式和状态快照模型
│   ├── controller.py     — SlashCommandController 协议
│   ├── parser.py         — 空输入、普通输入和斜杠输入解析
│   ├── registry.py       — 原子注册、冲突检测、查找和候选生成
│   ├── dispatcher.py     — 命令路由、未知命令和异常隔离
│   ├── builtins.py       — 十个公开命令、隐藏退出和固定审查提示词
│   ├── status.py         — Git/MCP 采集、状态隔离和纯文本格式化
│   └── completion.py     — prompt_toolkit 补全适配器
├── compact/
│   ├── __init__.py       — 导出 ContextTokenStatus
│   ├── models.py         — 增加 Token 状态快照
│   └── manager.py        — 增加无副作用的当前请求估算
├── memory/
│   ├── __init__.py       — 导出会话和记忆状态快照
│   ├── models.py         — 增加 SessionStatusSnapshot、MemoryStatusSnapshot
│   ├── sessions.py       — 增加当前写入会话摘要查询
│   └── manager.py        — 跟踪恢复来源并生成只读状态快照
├── agent/
│   └── loop.py           — 构造当前 Token 快照并保存最近框架上下文
├── session.py            — 转发模式、权限、Token、会话和记忆状态
├── tui.py                — 实现控制协议、分流输入、补全和模式状态栏
└── cli.py                — 启动期创建注册中心并注入 TUI

tests/
├── test_slash_registry.py     — 元数据校验、冲突检测、别名和公开顺序
├── test_slash_parser.py       — 空输入、普通输入、大小写和参数切分
├── test_slash_dispatcher.py   — 四种分发结果、未知命令和异常隔离
├── test_slash_builtins.py     — 十个公开命令及隐藏退出的处理行为
├── test_slash_status.py       — Git porcelain、MCP 快照和单项失败
├── test_slash_completion.py   — 单候选、多候选、隐藏及参数位置
├── test_slash_snapshots.py    — Token、会话和记忆领域快照
├── test_slash_tui.py          — 输入分流、状态栏和普通终端降级
├── test_slash_cli.py          — 注册成功注入与冲突启动失败
└── test_slash_e2e.py          — 完整命令链和审查提示词场景

doc/stage-09-slash-command/
├── spec.md
├── plan.md
├── task.md
└── checklist.md

README.md                     — 更新公开交互命令和模式说明
```

`MCPServerPool` 当前已经公开服务名、服务状态、可用性、工具列表和诊断，因此不修改 MCP 包。新增测试使用独立文件，不恢复或覆盖工作区中用户已删除的旧测试文件。

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 注册生命周期 | 构造期一次性校验并冻结 | 满足启动即失败，避免部分注册状态 |
| 名称规范化 | `casefold()`，内部名称不带 `/` | 主名称与别名统一进行大小写不敏感比较 |
| 命令处理形式 | 异步函数和明确返回信号 | 同时支持本地状态、异步压缩和 Agent 流 |
| 界面抽象 | `Protocol` 结构化接口 | 不要求继承，便于 TUI 实现和 fake 测试 |
| 命令输出 | 纯文本，TUI 使用 `markup=False` | 防止状态内容被误解释为 Rich 标记 |
| 退出控制 | 处理函数返回 `EXIT` | 不修改 TUI 私有循环变量 |
| 未知命令 | 显示 `/help` 后视为已处理 | 斜杠输入永不误发给模型 |
| 运行时异常 | 稳定错误码给用户，详细异常写开发日志 | 可观测且避免敏感信息泄漏 |
| 注册冲突 | CLI 输出冲突标识并返回 `1` | 启动失败清晰可定位 |
| Token 状态 | 复用请求构造器和估算器 | 覆盖系统提示、工具和历史，不维护第二套逻辑 |
| 框架上下文 | 缓存最近一次已准备的框架块 | 状态查询不触发长期记忆刷新 |
| 会话来源 | 当前写入 ID 与被恢复 ID 分开 | 符合 Stage 08 恢复后写新 JSONL 的语义 |
| 记忆状态 | 领域层生成脱敏快照 | 命令不读取私有字段或笔记正文 |
| Git 查询 | porcelain v2 和 branch headers | 稳定、非本地化、便于解析 |
| Git 文件计数 | 依据 XY 两列分别累计 | 同一文件可同时 staged 和 unstaged |
| 非 Git 目录 | 返回正常的不适用状态 | 不把正常环境误报为命令失败 |
| MCP 健康 | 只读连接池内存状态 | 不产生网络请求或重连 |
| 综合状态失败 | 每个数据源独立 `StatusSection` | 单项失败不影响其他结果 |
| 补全触发 | `complete_while_typing=False` | 只在 Tab 时显示候选 |
| 多候选菜单 | `CompleteStyle.COLUMN` | 复用现有终端库，不自建菜单状态机 |
| 模式状态栏 | 动态 `bottom_toolbar` callable | 每次渲染读取最新模式 |
| 普通终端降级 | 模式标记进入提示符 | 无增强终端时仍可观察模式 |
| `/review` | 固定普通用户提示词并持久化 | 后续对话可引用，不引入动态提示生成 |
| `/plan-only` | 完全移除命令入口 | 统一为 `/plan` 和 `/do` |
| `/exit` | 隐藏保留并支持 `/quit` | 保持兼容及十个公开命令 |
| 第三方依赖 | 不新增依赖 | 标准库、Rich、prompt_toolkit 已足够 |
| 测试文件 | 新建 Stage 09 专用测试 | 不恢复用户已删除的旧测试文件 |

## Spec 覆盖

| Spec | 设计归属 |
|---|---|
| F1-F2 | `slash.models`、`slash.registry`、CLI 启动错误 |
| F3 | `slash.parser` |
| F4-F5 | `slash.dispatcher`、`SlashDispatchResult` |
| F6 | `slash.controller`、`ChatTUI` 控制实现 |
| F7-F16 | `slash.builtins`、状态快照与格式化 |
| F17 | `slash.completion`、PromptSession 配置 |
| F18 | ChatTUI 动态底部状态栏和普通提示符 |
| F19 | 隐藏 `/exit` 登记、`EXIT` 分发结果 |
| N1-N6 | 只读快照、原子注册、逐项故障隔离 |
| N7-N10 | 脱敏模型、稳定错误输出、fake 状态源 |
| N11-N12 | 独立 `slash` 包、中文关键注释、登记式扩展 |
