# myCode Stage 12：子 Agent 委派与后台任务技术设计

## 架构概览

Stage 12 新增独立的 `mycode.subagent` 领域包，负责角色发现、统一工具入口、运行时创建、工具约束、任务调度、结果留存和安全点通知。每个子 Agent 复用现有 `AgentLoop` 类型，但使用独立实例和独立可变运行状态；现有主 `AgentLoop`、`ChatSession`、CLI、TUI 和 Slash 模块只增加必要接入点，不复制第二套 Agent 循环。

固定 `Agent` 工具必须在 `openai_chat`、`openai_responses` 和 `anthropic` 三个现有协议中都可见并可完成工具结果往返。Stage 12 因此同步补齐 Anthropic 工具 schema、流式工具调用和工具历史序列化，不建立子 Agent 专用协议分支。

整体调用关系固定为：

```text
Agent 工具 / /tasks / /task
            |
            v
    SubAgentService
      |-- AgentCatalog
      |-- SubAgentTaskManager
      |-- SubAgentRuntimeFactory
      |-- SubAgentToolPolicy
      `-- SubAgentNotificationInbox
            |
            v
      独立 AgentLoop 实例
```

### 角色与统一入口

`AgentCatalog` 在应用启动时加载、校验并覆盖项目级、用户级、内置级和插件接口角色。`AgentTool` 始终以固定名称和固定 schema 注册，只把运行、列表和详情查询转发给 `SubAgentService`；角色目录和任务状态变化不修改工具注册表。

### 运行时隔离

`SubAgentRuntimeFactory` 为每个任务创建独立的 `AgentLoop`、消息内存、上下文管理器、权限会话、文件文本缓存、Hook 可变状态、usage 累加器和取消信号。模型工厂、LLM 协议配置、Hook 规则配置、工作区文件系统、路径安全规则、MCP 连接池和无会话状态的工具能力可以共享。

定义式任务重新构造核心系统规则、工作区环境、项目指令、固定角色正文和本次任务，不接入父消息、父项目记忆、父 Skill 状态或父临时提醒。

Fork 任务在父模型请求构建完成后冻结消息、工具 schema、模型和最大轮次。Fork 专用提示构建器始终把该快照原样放在请求前缀，只在其后追加中文 Fork 任务指令和子 Agent 后续消息。父 Agent 后续新增消息不会修改已创建的快照。

### 工具与权限边界

`SubAgentToolPolicy` 统一计算定义式任务的可见工具和所有子 Agent 的实际可执行工具。定义式任务只把过滤后的 schema 交给模型；Fork 为保持父请求前缀而继续使用冻结的完整 schema，但在每次执行前应用全局禁止集、后台白名单、权限和路径检查。`Agent` 工具在所有子 Agent 中始终拒绝执行。

子 Agent 共享持久权限规则和系统级安全底线，但每个任务重新创建会话授权状态。角色权限档位先与父 Agent 有效档位取更严格值，再交给任务自己的权限服务判断；需要人工审批的结果在非交互执行中直接转成结构化拒绝。

### 调度与前后台切换

`SubAgentTaskManager` 管理任务状态机、FIFO 队列、统一执行槽、结果留存和通知入队。前台和后台子 Agent 共用 `max_concurrency` 个执行槽，默认 4 个；第 5 个任务从创建起进入 `queued`，最早排队的任务在槽位释放后启动。

前台、后台只表示父 Agent 是否等待，不是两套执行路径。显式后台和 Fork 创建后立即解除等待；前台等待超时或用户按 `Ctrl+B` 时，只把任务标记为已脱离并向父 Agent 返回任务 ID，不取消、重启、复制或改变任务的队列位置。等待超时从任务提交时开始计算，因此排队中的前台任务也能按时脱离。

### 结果与安全点通知

任务结束后，`SubAgentTaskManager` 只结算一次终态和 usage，并保存经过大小规范化的详细结果。已脱离任务的完成事件进入 `SubAgentNotificationInbox`；主 Agent 运行中只在下一次模型调用前的安全点取出，主 Agent 已结束时由下一次用户请求取出。通知不会自行启动模型请求。

`/tasks` 和 `/task <id>` 直接读取任务管理器快照。自然语言查询由模型通过 `Agent` 工具的只读动作获取实时状态，不依赖聊天历史猜测。

### 生命周期

`ChatSession.clear_async()` 和 `ChatSession.close()` 调用 `SubAgentService` 的会话清理入口。服务取消排队及运行任务，等待运行任务安全收尾，然后清空任务记录、详细结果和待处理通知。任务及结果不跨 `/clear`、会话或进程保存。

## 核心数据结构

### 枚举

```python
class SubAgentKind(str, Enum):
    DEFINED = "defined"
    FORK = "fork"


class AgentRoleSource(str, Enum):
    PLUGIN = "plugin"
    BUILTIN = "builtin"
    USER = "user"
    PROJECT = "project"


class AgentModelTier(str, Enum):
    INHERIT = "inherit"
    HAIKU = "haiku"
    SONNET = "sonnet"
    OPUS = "opus"


class AgentPermissionMode(str, Enum):
    INHERIT = "inherit"
    STRICT = "strict"
    DEFAULT = "default"
    PERMISSIVE = "permissive"


class SubAgentTaskState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
```

### 角色定义

```python
@dataclass(frozen=True)
class AgentRoleMetadata:
    name: str
    description: str
    allowed_tools: tuple[str, ...]
    denied_tools: tuple[str, ...]
    model: AgentModelTier
    max_rounds: int
    permission_mode: AgentPermissionMode


@dataclass(frozen=True)
class AgentRoleDefinition:
    metadata: AgentRoleMetadata
    instruction: str
    source: AgentRoleSource
    entry_path: Path
    revision: str


@dataclass(frozen=True)
class AgentRoleDiagnostic:
    code: str
    source: AgentRoleSource
    path: str
    message: str
    role_name: str | None = None


@dataclass(frozen=True)
class AgentCatalogSnapshot:
    definitions: tuple[AgentRoleDefinition, ...]
    diagnostics: tuple[AgentRoleDiagnostic, ...]
    generation: int
```

角色名使用 `^[a-z][a-z0-9_-]{0,63}$`。`allowed_tools` 可以为空、列出具体工具名或只包含 `"*"`；`"*"` 不允许与具体名称混用，也不允许出现在 `denied_tools`。两个列表内部均禁止重复名称。

`revision` 是角色文件规范化内容的 SHA-256，用于测试和诊断，不用于热更新。目录快照中的定义按角色名排序，诊断按来源优先级、路径和错误码排序。

### 主配置

```python
@dataclass(frozen=True)
class SubAgentConfig:
    model_map: Mapping[AgentModelTier, str]
    foreground_timeout_seconds: float = 120.0
    max_concurrency: int = 4
    background_allowed_tools: tuple[str, ...] = (
        "read_file",
        "find_files",
        "search_code",
    )
    max_task_bytes: int = 64 * 1024
    max_result_bytes: int = 128 * 1024
    max_notification_bytes: int = 4 * 1024
    max_queued_tasks: int = 64
    max_retained_tasks: int = 256
```

YAML 中 `sub_agent.models` 必须完整声明 `haiku`、`sonnet`、`opus` 三个非空具体模型 ID；缺少整个映射或任一键都属于启动错误。解析后把映射复制为不可变值。`foreground_timeout_seconds`、`max_concurrency`、后台工具列表和资源上限可以省略并使用默认值。所有数值拒绝布尔值、非有限值和非正数；所有工具名在完整工具注册表构造后统一校验。

`LLMConfig` 增加 `sub_agent: SubAgentConfig | None = None`，保留协议单元测试和低层工厂直接构造配置的兼容性；`load_config()` 成功返回时该字段必须为非空，CLI 对异常的手工配置对象再次执行防御性检查。严格模型映射要求因此只在应用配置边界生效，不侵入无关协议测试的构造契约。

角色文件上限为 128 KiB，frontmatter 上限为 16 KiB。任务输入、详细结果、通知摘要、排队任务和终态任务分别使用 `SubAgentConfig` 中的有界配置。

### 父请求与启动请求

```python
@dataclass(frozen=True)
class ParentAgentSnapshot:
    messages: tuple[ChatMessage, ...]
    tools: tuple[ToolDefinition, ...]
    model_id: str
    max_rounds: int
    permission_mode: PermissionMode


@dataclass(frozen=True)
class SubAgentLaunchRequest:
    kind: SubAgentKind
    task: str
    role_name: str | None
    requested_background: bool
    parent: ParentAgentSnapshot
```

`ParentAgentSnapshot` 在当前父模型请求构建完成后创建。消息和工具定义均深复制为快照私有值，工具参数 schema 不能继续引用注册表中的可变字典；协议调用前再从私有快照构造普通 JSON 字典，避免只读代理与 JSON 编码不兼容。定义式请求必须包含角色名；Fork 请求必须不含角色名，并把 `requested_background` 规范化为 `True`。

### 用量、结果与任务快照

```python
@dataclass(frozen=True)
class SubAgentUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None


@dataclass(frozen=True)
class SubAgentResult:
    detail: str
    summary: str
    detail_truncated: bool = False
    summary_truncated: bool = False


@dataclass(frozen=True)
class SubAgentTaskSummary:
    id: str
    sequence: int
    kind: SubAgentKind
    role_name: str | None
    state: SubAgentTaskState
    detached: bool
    rounds: int
    error_code: str | None
    usage: SubAgentUsage


@dataclass(frozen=True)
class SubAgentTaskSnapshot:
    id: str
    sequence: int
    kind: SubAgentKind
    role_name: str | None
    state: SubAgentTaskState
    detached: bool
    rounds: int
    result: SubAgentResult | None
    error_code: str | None
    error_message: str | None
    usage: SubAgentUsage


@dataclass(frozen=True)
class SubAgentNotification:
    task_id: str
    state: SubAgentTaskState
    summary: str
    summary_truncated: bool
    usage: SubAgentUsage


@dataclass(frozen=True)
class SubAgentExecutionReport:
    state: SubAgentTaskState
    rounds: int
    result: SubAgentResult | None
    error_code: str | None
    error_message: str | None
    usage: SubAgentUsage


@dataclass(frozen=True)
class ToolPolicyDecision:
    allowed: bool
    reason_code: str | None = None
    message_zh: str | None = None


@dataclass(frozen=True)
class NotificationReservation:
    id: str
    notifications: tuple[SubAgentNotification, ...]
    dropped_count: int
    block: PromptContextBlock
```

任务 ID 使用当前会话内单调递增的 `task-000001` 格式，`sequence` 是不依赖时间的稳定排序键。公开快照不暴露 `asyncio.Task`、`Event`、`Future` 或异常对象；任务管理器在私有控制块中保存这些并发对象以及 `finalized`、`notification_enqueued` 标记。

usage 按模型轮次累加。某字段只有在所有已完成模型轮次都报告非负整数时才输出精确总和；任一轮缺失该字段时最终值为 `None`，不从 input/output 推导 total，也不把部分和显示为完整用量。

任务状态只允许：

```text
queued -> running -> completed | failed | cancelled
queued -> cancelled
```

`detached` 只允许从 `False` 变为 `True`。完成、超时脱离和取消发生竞争时，任务管理器在同一临界区内决定结果：任务只能写入一个终态，已脱离任务最多入队一次通知，仍附着且被前台调用直接取得结果的任务不再发送完成通知。

## 核心接口

### 统一 Agent 工具

`AgentTool` 使用固定名称 `Agent` 和固定 `ToolKind.WRITE`。标记为写工具是为了让现有调度器串行处理同一父轮中的委派；`list` 和 `get` 动作本身仍为只读。

工具参数使用稳定的 `oneOf` schema：

| `action` | 必填参数 | 约束 |
|---|---|---|
| `run` | `type`、`task` | `defined` 还需 `role`；`fork` 禁止 `role` 并强制后台 |
| `list` | 无 | 禁止运行参数，返回按 `sequence` 排序的任务摘要 |
| `get` | `task_id` | 禁止运行参数，返回详细结果、错误和 usage |

三个分支均拒绝未知字段。`background` 只允许用于定义式 `run`，缺省为 `False`。

### 角色目录

```python
class AgentCatalog:
    def initialize(self) -> AgentCatalogSnapshot: ...
    def snapshot(self) -> AgentCatalogSnapshot: ...
    def get(self, name: str) -> AgentRoleDefinition: ...
```

`initialize()` 只允许在启动装配期间调用一次。高优先级候选无效时保留诊断并继续选择下一有效候选；本阶段不提供 refresh 或文件监听入口。

### 统一服务

```python
@dataclass(frozen=True)
class SubAgentRunResponse:
    task: SubAgentTaskSnapshot
    inline: bool


class SubAgentService:
    async def run(self, request: SubAgentLaunchRequest) -> SubAgentRunResponse: ...
    def list_tasks(self) -> tuple[SubAgentTaskSummary, ...]: ...
    def get_task(self, task_id: str) -> SubAgentTaskSnapshot: ...
    async def detach_active(self) -> SubAgentTaskSnapshot | None: ...
    async def clear(self) -> None: ...
    async def close(self) -> None: ...
```

`inline=True` 只表示前台调用直接取得终态结果。`detach_active()` 供 `Ctrl+B` 使用；没有附着任务时返回 `None`。服务只允许一个当前附着任务，因为 `Agent` 工具按写工具串行执行。

### 任务管理器

```python
class SubAgentTaskManager:
    async def submit(self, request, runner) -> SubAgentTaskSnapshot: ...
    async def wait(self, task_id: str, timeout_seconds: float) -> SubAgentRunResponse: ...
    async def detach(self, task_id: str) -> SubAgentTaskSnapshot: ...
    def list(self) -> tuple[SubAgentTaskSummary, ...]: ...
    def get(self, task_id: str) -> SubAgentTaskSnapshot: ...
    async def cancel_all_and_clear(self) -> None: ...
```

管理器只调用每个 runner 一次。槽位申请、FIFO 出队、终态结算、终态留存淘汰和通知入队都通过同一锁保护的状态转换完成。单任务取消、重跑和恢复没有公共接口。

### 运行时

```python
class SubAgentRuntime:
    async def run(self, cancel_event: asyncio.Event) -> SubAgentExecutionReport: ...


class SubAgentRuntimeFactory:
    def create(self, request: SubAgentLaunchRequest) -> SubAgentRuntime: ...
```

工厂负责角色解析、模型选择、权限收紧、独立 memory/cache/Hook 状态、任务工具注册表、定义式提示构建和 Fork 前缀构建。运行时只消费 `AgentLoop` 事件并生成规范化报告，不直接修改任务记录。

### 工具与权限策略

```python
class SubAgentToolPolicy:
    def visible_names(
        self,
        *,
        request: SubAgentLaunchRequest,
        role: AgentRoleDefinition | None,
        detached: bool,
    ) -> frozenset[str]: ...

    def evaluate(
        self,
        *,
        request: SubAgentLaunchRequest,
        role: AgentRoleDefinition | None,
        detached: bool,
        tool_name: str,
    ) -> ToolPolicyDecision: ...

    def effective_permission_mode(
        self,
        parent: PermissionMode,
        role: AgentRoleDefinition | None,
    ) -> PermissionMode: ...
```

定义式任务每轮动态计算可见 schema；转后台后的下一轮立即收紧。Fork 始终使用冻结 schema，但执行前仍调用 `evaluate()`。`SubAgentPermissionInterceptor` 先执行该策略，再调用任务独立的权限服务，并把所有 `ASK` 转换成 `approval_required_non_interactive` 结构化拒绝，不向外产生审批事件。

```python
class SubAgentPermissionInterceptor:
    async def before_tool(
        self,
        call: ToolCall,
        definition: ToolDefinition,
        *,
        plan_only: bool,
        round_index: int,
    ) -> PermissionDecision: ...

    def denied_result(self, call: ToolCall, decision: PermissionDecision) -> ToolResult: ...
    async def after_tool(self, call: ToolCall, result: ToolResult) -> ToolResult: ...
```

该拦截器向 `AgentLoop` 暴露与现有权限拦截器相同的已用表面，但 `before_tool()` 永不返回 `ASK`，因此子 Agent 路径不会调用审批创建或解析接口。

### 父请求快照

```python
class ParentAgentSnapshotStore:
    def update(
        self,
        request: PromptBuildResult,
        *,
        model_id: str,
        max_rounds: int,
        permission_mode: PermissionMode,
    ) -> None: ...

    def current(self) -> ParentAgentSnapshot: ...
```

快照存储使用 `ContextVar` 隔离并发 AgentLoop。父 loop 在每轮模型请求构建完成后更新当前任务上下文，`AgentTool` 在同一异步上下文中读取；创建子任务时立即深复制，后续更新不会影响已提交任务。

### 通知安全点

```python
class SubAgentNotificationInbox:
    def reserve(self) -> NotificationReservation | None: ...
    def commit(self, reservation_id: str) -> None: ...
    def release(self, reservation_id: str) -> None: ...
    def clear(self) -> None: ...
```

`AgentLoop` 在下一轮构建前预留一批通知并转换成单个 framework block。模型请求成功构建后提交预留；上下文压缩或 Prompt 构建失败时释放预留，使通知能在下一安全点再次注入。提交只表示通知已经进入一次模型请求，不等待该请求成功完成。

## 模块设计

### `subagent.models`

**职责：** 保存枚举、冻结数据类、限制常量和稳定错误类型。  
**依赖：** 标准库、`mycode.llm`、`mycode.tool`、`mycode.permission.models`。  
**限制：** 不读取文件、不创建异步任务、不导入 Agent、Session 或 TUI。

### `subagent.config`

**职责：** 从主 YAML 的 `sub_agent` 映射解析超时、并发、模型档位、后台工具和资源上限；提供在完整工具注册表建立后的工具名校验。  
**依赖：** `subagent.models`、`mycode.config.ConfigError`。  
**限制：** 不扫描角色目录，不创建模型实例。

### `subagent.loader`

**职责：** 扫描项目、用户、内置和插件候选，按大小上限解析单文件 Markdown 与 YAML frontmatter，生成定义或可定位诊断。  
**依赖：** `subagent.models`、PyYAML、标准库路径和哈希。  
**限制：** 插件 provider 本阶段返回空候选，不发现插件目录。

### `subagent.catalog`

**职责：** 对 loader 候选应用 `项目 > 用户 > 内置 > 插件` 优先级，执行无效候选回退并输出稳定快照。  
**依赖：** `subagent.loader`、`subagent.models`。  
**限制：** 只在启动时初始化，不监听或刷新文件。

### `subagent.context`

**职责：** 深冻结父请求、维护基于 `ContextVar` 的当前父快照、构造定义式角色 block、实现 Fork 前缀提示构建器。  
**依赖：** `subagent.models`、`mycode.prompt`、`mycode.llm`。  
**关键约束：** Fork 每轮都使用冻结的父消息和工具前缀；新增内容只能追加在前缀之后。

### `subagent.tooling`

**职责：** 创建任务级工具注册表和执行器，计算可见工具，执行全局/角色/后台约束，包装独立权限服务并把 `ASK` 转成拒绝。  
**依赖：** `subagent.models`、`mycode.tool`、`mycode.permission`、`mycode.mcp`、`mycode.skill`。  
**关键约束：** 不直接复用持有主文件缓存、主 MCP 发现集合或主 Skill 激活状态的对象。

### `subagent.runtime`

**职责：** 选择角色和模型，创建独立 `AgentLoop`，消费 Agent 事件，累计轮次和 usage，生成规范化执行报告。  
**依赖：** `subagent.catalog`、`subagent.context`、`subagent.tooling`、现有 Agent、Prompt、Memory、Hook 和协议工厂。  
**限制：** 不修改任务状态，不决定 FIFO 顺序，不直接向父会话注入结果。

### `subagent.tasks`

**职责：** 保存私有任务控制块，分配 ID，实施统一执行槽、FIFO、等待、脱离、取消、一次终态结算和终态留存淘汰。  
**依赖：** `subagent.models`、`subagent.notifications`、`asyncio`。  
**限制：** 不解析角色，不调用 TUI，不格式化工具结果。

### `subagent.notifications`

**职责：** 截断通知摘要，维护待处理和已预留通知，渲染单个中文 framework block。  
**依赖：** `subagent.models`、`mycode.prompt.models`。  
**限制：** 不启动模型、不读取任务管理器内部对象。

### `subagent.service`

**职责：** 编排启动请求、前台等待、后台脱离、实时查询和会话清理；维护唯一当前附着任务 ID。  
**依赖：** `subagent.runtime`、`subagent.tasks`、`subagent.catalog`。  
**限制：** 不依赖 CLI、TUI 或 Slash。

### `subagent.tool`

**职责：** 暴露固定 `Agent` 定义，执行严格的 action 分支参数校验，把结构化响应转换为 `ToolResult`。  
**依赖：** `subagent.service`、`subagent.context`、`mycode.tool.base`。  
**限制：** 不动态改变 schema，不直接创建异步任务。

### 内置角色

内置 Markdown 位于 `src/mycode/subagent/builtins/`：

| 角色 | 工具 | 模型 | 最大轮次 | 权限 |
|---|---|---|---|---|
| `general` | `allowed_tools: ["*"]`，`denied_tools: ["Agent"]` | `inherit` | 8 | `inherit` |
| `explore` | `read_file`、`find_files`、`search_code` | `inherit` | 8 | `strict` |
| `review` | `read_file`、`find_files`、`search_code` | `inherit` | 8 | `strict` |

三个正文均使用中文。`explore` 聚焦只读定位与事实摘要，`review` 聚焦缺陷、风险和测试缺口，`general` 处理不适合两个只读角色的通用子任务。

## 任务级基础设施

每个任务新建：

- `InMemoryConversationMemory` 和临时上下文管理器
- `ToolRegistry`、`ToolExecutor` 和 `FileTextCache`
- MCP wrapper、`ToolSearch` 和发现集合
- `PermissionService`、`SubAgentPermissionInterceptor`
- Skill runtime 的激活状态
- `HookRuntime`、提示注入和 `once` 状态
- usage accumulator 和取消事件

任务可以共享：

- LLM 工厂、主协议配置和模型 ID 映射
- 工作区文件系统和相同路径安全规则
- MCP 连接池
- Hook 配置和无任务状态的动作依赖
- 已初始化角色目录的不可变快照

MCP 任务注册使用新增的快照装配函数，只读取任务创建时的 `pool.tools`，不为每个任务注册重连 listener。远端调用仍通过共享连接池执行。父项目记忆管理器不注入子 Agent；Fork 冻结 schema 中存在但无法安全隔离的父状态工具，由工具策略在权限判断前返回结构化拒绝。

## 文件组织

```text
src/mycode/
|-- subagent/
|   |-- __init__.py
|   |-- models.py
|   |-- config.py
|   |-- loader.py
|   |-- catalog.py
|   |-- context.py
|   |-- tooling.py
|   |-- runtime.py
|   |-- tasks.py
|   |-- notifications.py
|   |-- service.py
|   |-- tool.py
|   `-- builtins/
|       |-- general.md
|       |-- explore.md
|       `-- review.md
|-- agent/loop.py
|-- mcp/tools.py
|-- slash/controller.py
|-- slash/builtins.py
|-- config.py
|-- session.py
|-- tui.py
`-- cli.py
```

现有模块调整：

| 文件 | 调整 |
|---|---|
| `src/mycode/config.py` | `LLMConfig` 增加 `sub_agent`，调用领域解析器 |
| `src/mycode/agent/loop.py` | 更新当前父快照，预留、提交或释放通知 |
| `src/mycode/mcp/tools.py` | 增加不注册 listener 的任务 MCP 快照装配函数 |
| `src/mycode/protocols/anthropic.py` | 增加 system、tools、tool_use、tool_result 和流式参数增量支持 |
| `src/mycode/tool/base.py` | 增加 `ToolRuntimeScope` 和单工具超时元数据 |
| `src/mycode/tool/executor.py` | 使用定义级超时覆盖并保持现有默认值 |
| `src/mycode/tool/defaults.py` | 给默认工具声明任务级作用域并支持任务重建 |
| `src/mycode/memory/tools.py` | `read_memory_note` 标记为父运行时专用 |
| `src/mycode/compact/archive.py` | `read_compact_artifact` 标记为父运行时专用 |
| `src/mycode/skill/load_tool.py` | `load_skill` 标记为任务级并绑定独立 runtime |
| `src/mycode/session.py` | 接入 detach、clear 和 close 生命周期 |
| `src/mycode/slash/controller.py` | 增加任务列表和详情控制接口 |
| `src/mycode/slash/builtins.py` | 注册 `/tasks` 与 `/task <id>` |
| `src/mycode/tui.py` | 流式输出时捕获 `Ctrl+B` 并请求脱离当前前台任务 |
| `src/mycode/cli.py` | 装配配置、目录、通知、运行时、任务管理器、服务和 `AgentTool` |
| `pyproject.toml` | 打包 `subagent/builtins/*.md` |
| `examples/mycode.*.yaml` | 增加严格且完整的模型档位映射 |
| `README.md` | 增加子 Agent 配置、角色文件、后台行为和查询命令说明 |

## 模块交互

### 启动装配

```text
解析主配置并校验模型映射
  -> 初始化权限、Hook、MCP 和不含 Agent 的基础工具注册表
  -> 以基础工具名 + 保留名 Agent 校验后台工具和角色候选
  -> 初始化 AgentCatalog
  -> 创建 ParentAgentSnapshotStore / SubAgentNotificationInbox
  -> 创建 SubAgentRuntimeFactory / SubAgentTaskManager / SubAgentService
  -> 创建并注册固定 AgentTool
  -> 注入主 AgentLoop、ChatSession 和 TUI
```

未知后台工具属于主配置错误，导致启动失败。角色中的未知工具使当前候选无效并产生诊断，目录继续按来源优先级选择下一有效候选。

### 定义式前台执行

```text
父 AgentLoop 构建模型请求并冻结快照
  -> 模型调用 Agent(action=run, type=defined)
  -> AgentTool 校验参数并创建 SubAgentLaunchRequest
  -> SubAgentService 提交任务并登记当前附着 ID
  -> SubAgentTaskManager 获取统一执行槽
  -> SubAgentRuntimeFactory 创建独立 AgentLoop
  -> 子 Agent 非交互运行到底
```

任务在前台等待期限内完成时，规范化详细结果和 usage 直接成为 `Agent` 工具结果，父 Agent 写入工具结果消息并继续下一轮。任务没有被脱离，因此不再生成完成通知。

### Fork 与显式后台

Fork 使用产生本次 `Agent` 调用的父请求快照：

```text
冻结的父 messages + 冻结的父完整 tools
  -> 追加中文 Fork 任务指令
  -> 追加子 Agent 后续消息和工具结果
```

父模型随后生成的 `Agent` 工具调用消息不进入 Fork 前缀。Fork 和显式后台定义式任务提交后立即设置 `detached=True` 并返回任务 ID；任务尚无槽位时继续保持原 FIFO 位置。

### 超时与 `Ctrl+B`

`SubAgentService` 同时等待任务终态、detach 事件和从提交时开始计算的前台超时。`Ctrl+B` 调用链固定为：

```text
ChatTUI key handler
  -> ChatSession.detach_active_subagent()
  -> SubAgentService.detach_active()
  -> SubAgentTaskManager.detach(task_id)
```

终态先获得管理器锁时返回内联结果；detach 先获得锁时返回任务 ID，之后任务完成时发送一次通知。已经进入真实 executor 的工具调用不中断，后台白名单从下一次工具判断开始生效。排队任务也可以脱离，但不会改变状态或位置。

### 工具执行顺序

```text
全局禁止集
  -> 角色白名单
  -> 角色黑名单
  -> detached 时的后台白名单
  -> 任务独立权限策略
  -> ASK 转结构化拒绝
  -> Hook tool_before
  -> ToolExecutor
  -> 权限 after_tool
  -> Hook tool_after
```

任一子 Agent 工具策略拒绝都直接形成 `ToolResult`，不调用后续权限审批、Hook 或真实 executor。`Agent` 始终在全局禁止层拒绝。单次拒绝和普通工具失败回填子 Agent memory，由模型决定是否调整后继续。

### 通知与查询

已脱离任务结束后，详细结果保留在任务管理器，Inbox 只保存有界完成摘要。每个安全点按任务 `sequence` 最多预留 16 条通知，渲染后的整个 framework block 不超过 32 KiB；未进入本批的通知保留到后续模型轮次。

```text
Inbox.reserve()
  -> Prompt/上下文构建成功：commit()
  -> 压缩或 Prompt 构建失败：release()
```

主 Agent 尚在运行时，下一轮模型请求执行该流程；主 Agent 已结束时，下一次用户请求的第一轮执行同一流程。`/tasks` 和 `/task <id>` 直接调用 `SubAgentService`，自然语言查询通过 `Agent(list|get)` 获取同一实时快照。

### `/clear` 与退出

服务首先停止接收新任务。排队任务直接进入 `cancelled`，运行任务收到取消信号，runtime 取消当前 AgentLoop 并等待其 `finally` 和 Hook 清理路径结束。所有 runner 结束后清空任务、结果、通知、当前附着 ID 和任务 ID 序列。应用退出流程随后再关闭项目记忆、上下文管理器和 MCP 连接池。

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| Agent 循环 | 每任务独立 `AgentLoop` 实例 | 复用现有循环并隔离实例状态 |
| 定义式提示 | 核心模块 + 环境 + 项目指令 + 角色系统模块 + 任务 | 满足空白对话和固定角色约束 |
| Fork 提示 | 冻结 `PromptBuildResult` 前缀，子内容只追加 | 保证缓存兼容和父快照不变 |
| 上下文管理 | 任务级内存上下文，不写父压缩归档 | 避免结果和归档状态串扰 |
| 并发控制 | 前后台任务共用统一执行槽 | 使转后台与并发硬上限同时成立 |
| 权限档位 | `strict < default < permissive`，取父档位和角色档位中更严格者 | 权限只能保持或收紧 |
| 权限规则 | 重新加载持久规则，不继承父会话状态 | 保留基础策略并隔离临时授权 |
| Hook | 共享配置，每任务创建 runtime | 规则一致，`once` 和 prompt 状态隔离 |
| 结果摘要 | 从最终文本确定性截断 | 不增加模型请求、成本和不确定性 |
| 任务排序 | 会话内单调 `sequence` | 不依赖时间和调度速度 |
| 终态淘汰 | 按 `sequence` 淘汰最旧终态 | 保持内存有界且顺序确定 |
| Agent 工具分类 | 固定 `ToolKind.WRITE` | 避免同一父轮并行委派造成附着状态冲突 |
| 中文规范 | 新增定义字段使用简洁中文注释，复杂状态、缓存和隔离逻辑只注释关键不变量 | 满足可维护性而不重复代码叙述 |

### 工具运行时作用域

`ToolDefinition` 增加只在本地运行时使用的作用域和单工具超时字段：

```python
class ToolRuntimeScope(str, Enum):
    SHARED = "shared"
    TASK_LOCAL = "task_local"
    PARENT_ONLY = "parent_only"


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: JSONSchema
    kind: ToolKind
    grant_arguments: tuple[str, ...] = ()
    parallel_safe: bool = True
    runtime_scope: ToolRuntimeScope = ToolRuntimeScope.SHARED
    execution_timeout_seconds: float | None = None
```

两个字段都不序列化到供应商 tool schema。`Agent`、`read_memory_note` 和 `read_compact_artifact` 标记为 `PARENT_ONLY`；文件工具、MCP wrapper、`tool_search` 和 `load_skill` 标记为 `TASK_LOCAL`。其余无任务状态且线程安全的工具可保持 `SHARED`。

`ToolExecutor` 在 `execution_timeout_seconds` 为 `None` 时继续使用执行器默认超时，否则使用定义上的正数覆盖值。`AgentTool` 设置为 `foreground_timeout_seconds + 5` 秒，默认 125 秒，使 Service 能先在 120 秒完成内联返回或自动脱离；该本地字段变化不改变稳定 tool schema。Agent 整次 run 的更短截止时间仍可优先取消等待。

Fork 为保持 schema 冻结而保留 `PARENT_ONLY` 定义，但任务注册表只放入具有同定义的拒绝适配器，真实父实例永不进入子运行时。`TASK_LOCAL` 工具缺少重建工厂时，runtime 创建失败，不得退化为复用父实例。

### Anthropic 工具调用

Anthropic 请求转换遵循现有统一消息模型：

- `role="system"` 的消息从普通 messages 中取出，按原顺序拼接到请求顶层 `system`。
- `ToolDefinition` 转为 `{name, description, input_schema}`，工具顺序保持调用方传入顺序。
- 普通 user/assistant 文本转为 text content block。
- 带 `tool_call_id` 和 `tool_name` 的 assistant 消息转为 `tool_use` block，参数从 `tool_arguments` 解析；历史中的非法 JSON 使用空对象重放，原文已由对应的统一失败 `tool_result` 告知模型。
- role 为 `tool` 的消息转为 user `tool_result` block，以 `tool_call_id` 关联原调用。
- 连续的 assistant 工具调用合并在同一 assistant content 列表，连续工具结果合并在同一 user content 列表，保持并行工具批次语义。

流式响应按 `content_block_start.index` 建立待处理工具调用，记录 `tool_use.id/name/input`；后续 `input_json_delta.partial_json` 按 index 追加，在对应 `content_block_stop` 生成一个统一 `StreamEventType.TOOL_CALL`。参数 JSON 非法时生成 `arguments=None` 并保留 `raw_arguments`，交给现有统一工具参数校验形成失败结果。文本、thinking 和 usage 继续沿用现有事件映射。未知 block 类型忽略，缺少 ID/name 的 tool block 转成稳定 `LLMError`，不能产生不完整调用。

该协议能力同时服务主 Agent 和所有子 Agent。README 中“暂不实现 Anthropic 工具调用”的旧边界同步移除，并记录 Stage 12 支持范围。

### 结果规范化

详细结果、通知摘要和错误消息都按 UTF-8 字节边界截断，超限时追加固定标记：

```text
...[结果已截断]
```

摘要直接取规范化最终文本的有界前缀，不发起额外模型请求。错误只保留稳定错误码和脱敏中文摘要，不保留异常对象、堆栈、完整父历史或无关工具输出。

### 留存溢出

任务管理器最多保留 `max_retained_tasks` 条终态记录。新增终态超限时淘汰最旧终态，排队和运行任务永不淘汰。通知 Inbox 最多保留 256 条待处理通知；溢出时淘汰最旧通知并累计 `dropped_count`，下一次安全点明确注入：

```text
另有 N 条较早通知因留存上限被丢弃。
```

查询已淘汰任务返回 `task_not_found`。留存淘汰不改变任务曾经完成的终态，也不重复生成通知。

## 失败处理

| 失败点 | 行为 |
|---|---|
| 模型映射缺失 | CLI 启动失败 |
| 配置数值非法或后台工具未知 | CLI 启动失败 |
| 角色文件过大、frontmatter 或字段非法 | 记录候选诊断并回退下一来源 |
| `Agent` 参数非法 | 返回结构化工具错误，不创建任务 |
| 父快照缺失或角色不存在 | 返回稳定错误，不创建任务 |
| 任务输入超限或排队已满 | 返回稳定错误，不分配任务 ID |
| 模型实例或任务工具注册表创建失败 | 已创建任务进入 `failed` |
| LLM、Prompt 或上下文构建失败 | 任务进入 `failed`，错误脱敏 |
| 达到最大轮次 | `failed / max_rounds_exceeded` |
| 子 Agent 没有最终回复 | `failed / no_final_response` |
| 单次工具拒绝或普通工具失败 | 结构化回填子 Agent，任务继续 |
| Hook 运行期失败 | 记录安全诊断，任务继续 |
| 用户 `/clear` 或应用退出 | 任务进入 `cancelled`，不重复通知 |
| 通知渲染、压缩或 Prompt 构建失败 | 释放 reservation，下个安全点重试 |
| 查询不存在或已淘汰 ID | 返回中文 `task_not_found` |

`clear()` 和 `close()` 先发送取消信号并等待 runtime 正常收尾。超过 15 秒宽限期后取消剩余 asyncio runner、消费其异常并继续关闭应用资源。清理接口幂等；重复调用不会重新结算任务或发送通知。

## 测试设计

### 模型、配置与角色

- `tests/test_subagent_models.py`：枚举转换、字段不变量、UTF-8 字节截断、usage 精确累加与未知传播。
- `tests/test_subagent_config.py`：完整模型映射、可选字段默认值、布尔值冒充整数、非有限超时、非法上限、重复及未知后台工具。
- 现有 `tests/test_config.py`、CLI 测试和共享配置 fixture 除“缺失映射”专用用例外统一加入有效三档映射，避免新的必填错误遮蔽原测试目标。
- `tests/test_subagent_loader.py`：四个来源候选、文件和 frontmatter 大小、YAML 类型、必填字段、正文、角色名、工具列表、权限和模型档位。
- `tests/test_subagent_catalog.py`：项目/用户/内置/插件优先级、无效高优先级回退、无有效候选、定义和诊断稳定排序。
- `tests/test_subagent_docs.py`：三个中文内置角色可从 package data 加载，metadata 与本设计一致，`pyproject.toml` 包含角色文件声明。

### 上下文与工具边界

- `tests/test_subagent_context.py`：定义式请求包含核心规则、环境、项目指令、角色和任务，不含父历史、父记忆、父 Skill 或临时提醒。
- 同一文件逐项比较 Fork 的父 system、history、framework/environment messages 和 tools；断言 Fork 指令只追加在冻结前缀后，父对象后续变化不影响快照。
- `tests/test_subagent_tooling.py`：`"*"`、空白名单、黑名单优先、全局禁止、后台动态收紧、三档权限取更严格值、父会话 grant 隔离、`ASK` 非交互拒绝。
- 同一文件验证 `SHARED`、`TASK_LOCAL`、`PARENT_ONLY`：父状态工具 executor 调用数为零，文件缓存、MCP 发现和 Skill 激活状态互不共享。

### 调度、运行时与服务

- `tests/test_subagent_tasks.py`：四个运行槽、第 5 个排队、FIFO 启动、队列上限、前台和后台共用槽、一次 runner、一次终态和一次通知。
- 同一文件覆盖完成/超时/detach/cancel 竞争、排队任务脱离、终态淘汰、通知溢出计数和 `/clear` 全量取消。
- `tests/test_subagent_runtime.py`：scripted LLM 多轮工具、无工具完成、普通工具失败后调整、最大轮次、LLM/Prompt 错误、取消、无最终回复、结果截断和五类 usage。
- `tests/test_subagent_service.py`：前台内联、显式后台、Fork 强制后台、从提交计时的自动脱离、活动任务登记、实时列表/详情和幂等清理。
- 所有并发测试使用可控 `Future`、`Event` 和注入式等待器推进，不使用依赖机器速度的长 sleep。

### 工具、Agent、TUI 与 CLI

- `tests/test_subagent_tool.py`：固定 `Agent` schema 快照、`run/list/get` 分支、未知字段、互斥字段、缺失父快照、输入超限和稳定 `ToolResult`。
- 同一文件验证 `AgentTool` 的定义级超时长于前台等待阈值，现有普通工具仍使用执行器默认超时。
- `tests/test_subagent_agent.py`：父 loop 每轮更新快照；通知 reserve 后在 Prompt 成功时 commit、失败时 release；拒绝工具不触发 Hook、审批或 executor。
- `tests/test_subagent_session_tui.py`：fake prompt-toolkit input 触发 `Ctrl+B`，无活动任务、clear、close、流式输出和审批输入互不冲突。
- `tests/test_subagent_slash_cli.py`：`/tasks`、`/task <id>` 用法、排序、未知 ID、配置启动错误、角色诊断和装配顺序。
- `tests/test_subagent_e2e.py`：定义式前台、Fork 后台、5 任务队列、下一安全点通知、自然语言实时查询和 `/clear` 清理。
- `tests/test_anthropic_protocol.py`：替换“忽略 tools”的旧断言，覆盖顶层 system、工具 schema、assistant `tool_use` 历史、user `tool_result` 历史、多个并行工具、参数增量、非法参数和 usage 共存。
- `tests/test_tool_executor.py`：增加定义级超时覆盖测试，并确认未声明覆盖的普通工具仍使用执行器默认超时。

### 测试隔离与回归

全部新测试使用临时 cwd、临时 home、scripted/fake LLM、fake 工具、fake MCP pool 和确定性调度，不读取真实用户角色、真实权限文件，不访问网络或真实 API。

严格模型映射会同步更新三份 `examples/mycode.*.yaml` 和 README 中四段可加载主配置。示例的三个档位都显式映射到该示例已有的主模型字符串，避免猜测供应商型号；用户可按实际账户自行区分。

验证命令：

```powershell
python -m pytest -q tests -k subagent
python -m pytest -q tests/test_anthropic_protocol.py tests/test_tool_executor.py tests/test_agent_loop.py tests/test_session.py
python -m pytest -q
python -m compileall -q src tests
```

项目当前未配置独立 lint 工具，因此验收不声明不存在的 lint 命令；编译检查和完整 pytest 回归作为统一静态与行为验证。

## Spec 覆盖

| 需求 | 设计归属 | 主要测试 |
|---|---|---|
| F1 统一 Agent 工具 | `subagent.tool`、固定 action schema、三个协议工具序列化 | `test_subagent_tool.py`、`test_anthropic_protocol.py` |
| F2 角色格式 | `subagent.models`、`subagent.loader` | `test_subagent_loader.py` |
| F3 来源与覆盖 | `subagent.catalog` | `test_subagent_catalog.py` |
| F4 内置角色 | `subagent.builtins` | `test_subagent_docs.py` |
| F5 主配置 | `subagent.config` | `test_subagent_config.py` |
| F6 定义式执行 | `subagent.context`、`subagent.runtime` | `test_subagent_context.py`、`test_subagent_runtime.py` |
| F7 Fork 执行 | Fork prompt builder、父快照 | `test_subagent_context.py`、`test_subagent_e2e.py` |
| F8 状态隔离 | 任务级基础设施、runtime scope | `test_subagent_tooling.py`、`test_subagent_runtime.py` |
| F9 非交互跑到底 | runtime 事件消费、失败处理 | `test_subagent_runtime.py` |
| F10 权限约束 | `SubAgentPermissionInterceptor` | `test_subagent_tooling.py` |
| F11 多层工具防线 | `SubAgentToolPolicy` | `test_subagent_tooling.py`、`test_subagent_agent.py` |
| F12 后台切换 | Service、TUI detach | `test_subagent_service.py`、`test_subagent_session_tui.py` |
| F13 调度与状态 | `SubAgentTaskManager` | `test_subagent_tasks.py` |
| F14 结果与通知 | 结果规范化、Inbox 安全点 | `test_subagent_tasks.py`、`test_subagent_agent.py` |
| F15 任务查询 | Agent list/get、Slash | `test_subagent_tool.py`、`test_subagent_slash_cli.py` |
| F16 会话清理 | Service、Session、CLI close | `test_subagent_service.py`、`test_subagent_e2e.py` |
| N1-N2 边界与隔离 | 独立包、任务级实例 | 模块测试 + 双任务隔离测试 |
| N3-N4 缓存与权限 | Fork 冻结前缀、权限取严 | context/tooling 测试 |
| N5-N7 有界、并发、确定性 | 配置上限、状态锁、sequence/FIFO | config/tasks 测试 |
| N8-N9 故障与可观测性 | 失败表、稳定 ID、usage | runtime/service 测试 |
| N10 中文规范 | 内置角色、提示和错误 | docs/tool/runtime 测试 |
| N11-N12 兼容与隔离测试 | 窄接入点、fake 基础设施 | 完整 pytest 回归 |

## 自检结论

- F1-F16 均有明确模块归属和至少一个主要测试入口。
- N1-N12 均有架构约束、失败边界或测试策略覆盖。
- 文档不存在占位符、模糊的跨项引用或未定义后续步骤。
- `SubAgentExecutionReport`、`ToolPolicyDecision`、`NotificationReservation` 等接口引用均已有一致定义。
- 定义式、Fork、前后台切换、通知、查询、清理和 Anthropic 工具往返形成完整调用链。
- 文件清单包含运行时作用域、定义级工具超时、协议、打包、示例和 README 等兼容修改。
- 当前范围仍为单层子 Agent；团队编排、持久化、任务取消入口和文件冲突处理没有被计划内容隐式引入。
