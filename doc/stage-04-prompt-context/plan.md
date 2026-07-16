# myCode Stage 04：Prompt Pipeline 与运行时上下文技术设计

## 架构概览

Stage 04 新增 `src/mycode/prompt/` 作为提示词构建边界。该包负责稳定模块注册与排序、环境快照采集、提醒周期和请求消息构建；它不依赖 `agent` 包或任意协议实现。`AgentLoop` 仅负责在用户 turn 开始时建立上下文，并在每个 model round 调用提示词构建器。`protocols` 继续负责将内部 `ChatMessage` 和工具定义映射为供应商请求，以及把供应商 usage 映射为统一观测。

依赖关系固定为：

```text
CLI / ChatSession
        |
        v
    AgentLoop
        |
        +------------------> prompt.PromptBuilder
        |                         |        |        |
        |                         |        |        +--> ReminderPolicy
        |                         |        +-----------> EnvironmentCollector
        |                         +--------------------> PromptRegistry
        |
        v
   llm.ChatMessage / llm.UsageObservation / tool.ToolDefinition
        |
        v
OpenAI Chat / OpenAI Responses / Anthropic 协议适配器
```

稳定 system 指令和工具定义以确定性顺序构造。原始用户消息仍写入 memory；`<system-reminder>` 和 `<environment-context>` 作为独立的临时 user-role 消息只进入当次 LLM 请求。这样既保持用户输入可审计，也使动态上下文不污染普通会话历史。

## 核心数据结构

### `src/mycode/llm/base.py`

```python
class MessageOrigin(str, Enum):
    CONVERSATION = "conversation"
    SYSTEM_INSTRUCTION = "system_instruction"
    SYSTEM_REMINDER = "system_reminder"
    ENVIRONMENT_CONTEXT = "environment_context"


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_arguments: str | None = None
    origin: MessageOrigin = MessageOrigin.CONVERSATION


@dataclass(frozen=True)
class UsageObservation:
    provider: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    request_id: str | None = None


@dataclass(frozen=True)
class StreamEvent:
    type: StreamEventType
    content: str = ""
    tool_call: ToolCall | None = None
    tool_result: ToolResult | None = None
    usage: UsageObservation | None = None
```

`origin` 放在 `ChatMessage` 的既有位置参数之后，保持已有位置参数调用的兼容性。协议层只序列化 role、content 和工具字段；`origin` 仅供内部区分普通对话、稳定 system、运行时提醒和环境上下文。

`UsageObservation` 的 token 字段使用 `None` 表示供应商未返回、格式错误或不支持，即用户可见的 `unknown`。`provider` 始终由适配器填充，`request_id` 来自供应商响应头或响应体中的已知字段。

### `src/mycode/prompt/models.py`

```python
@dataclass(frozen=True)
class PromptConfig:
    full_reminder_interval_rounds: int = 4
    environment_value_limit: int = 512
    git_timeout_seconds: float = 1.0


@dataclass(frozen=True)
class PromptModuleDefinition:
    id: str
    priority: int
    protected: bool = False


class PromptModule(Protocol):
    @property
    def definition(self) -> PromptModuleDefinition: ...

    def render(self, context: StablePromptContext) -> str: ...


@dataclass(frozen=True)
class StablePromptContext:
    tools: tuple[ToolDefinition, ...]


@dataclass(frozen=True)
class PromptDiagnostic:
    code: str
    source: str
    message: str


@dataclass(frozen=True)
class EnvironmentSnapshot:
    workspace: str | None
    operating_system: str
    current_time: str
    timezone: str
    git_branch: str | None
    git_status: str | None
    diagnostics: tuple[PromptDiagnostic, ...]


@dataclass(frozen=True)
class SystemReminder:
    id: str
    full_content: str
    concise_content: str


@dataclass(frozen=True)
class TurnPromptContext:
    turn_id: int
    environment: EnvironmentSnapshot
    plan_only: bool
    reminders: tuple[SystemReminder, ...]


@dataclass(frozen=True)
class PromptBuildMetadata:
    enabled_module_ids: tuple[str, ...]
    stable_prompt_sha256: str
    diagnostics: tuple[PromptDiagnostic, ...]


@dataclass(frozen=True)
class PromptBuildResult:
    messages: tuple[ChatMessage, ...]
    tools: tuple[ToolDefinition, ...]
    metadata: PromptBuildMetadata
```

`PromptConfig` 在 `__post_init__` 中拒绝小于 1 的提醒周期、非正环境字段长度和非正 Git 超时。`PromptDiagnostic.message` 只记录固定错误说明和字段名称，不记录 API key、完整用户消息、完整环境变量、完整 diff 或完整工具结果。

`PromptModule.render()` 只接收稳定上下文。内置模块不会读取时间、Git、memory 或 round；自定义模块也通过此接口与动态数据隔离。

### `src/mycode/prompt/registry.py`

```python
class PromptConfigurationError(ValueError):
    pass


class PromptBuildError(RuntimeError):
    pass


class PromptRegistry:
    def __init__(self, modules: Sequence[PromptModule] | None = None) -> None: ...

    def register(self, module: PromptModule, *, enabled: bool = True) -> None: ...

    def enable(self, module_id: str) -> None: ...

    def disable(self, module_id: str) -> None: ...

    def override(self, module: PromptModule) -> None: ...

    def enabled_modules(self) -> tuple[PromptModule, ...]: ...
```

注册表保存模块和启用状态。`register()` 遇到重复 ID 抛出 `PromptConfigurationError`；`enable()`、`disable()` 和 `override()` 遇到未知 ID 也抛出该错误。`disable()` 和 `override()` 作用于 `protected=True` 的模块时必须失败，以保证 `safety-boundaries` 始终生效。`enabled_modules()` 按 `(definition.priority, definition.id)` 返回模块，保证稳定排序。

### `src/mycode/prompt/builder.py`

```python
class PromptBuilder:
    def __init__(
        self,
        *,
        registry: PromptRegistry,
        environment_collector: EnvironmentCollector,
        reminder_policy: ReminderPolicy,
        config: PromptConfig,
    ) -> None: ...

    def begin_turn(
        self,
        *,
        turn_id: int,
        plan_only: bool,
        reminders: Sequence[SystemReminder] = (),
    ) -> TurnPromptContext: ...

    def build(
        self,
        *,
        history: Sequence[ChatMessage],
        tools: Sequence[ToolDefinition],
        turn: TurnPromptContext,
        round_index: int,
    ) -> PromptBuildResult: ...
```

`begin_turn()` 是环境采集的唯一入口。它采集一次 `EnvironmentSnapshot`，合并调用方传入的可信提醒，并在 `plan_only=True` 时追加由 `ReminderPolicy` 创建的模式提醒。环境采集本身不会因为 Git 缺失或命令失败抛出 `PromptBuildError`，而是返回带诊断和 `unknown` 字段的快照。

`build()` 的算法如下：

1. 按工具名排序 `tools`，构造 `StablePromptContext`。
2. 从注册表取得稳定排序的已启用模块并依次渲染。非受保护模块渲染失败时加入诊断并跳过；受保护模块失败时抛出 `PromptBuildError`。
3. 用换行分隔已渲染模块，生成唯一的 system `ChatMessage`，其 `origin=SYSTEM_INSTRUCTION`。
4. 复制 `history`，不修改其中的原始用户消息或工具历史。
5. 根据当前 round 从 `ReminderPolicy` 选择完整或精简文本。存在提醒时追加独立的 user `ChatMessage`，其 `origin=SYSTEM_REMINDER`，内容包裹在 `<system-reminder>` 中。
6. 将一次性环境快照格式化为独立的 user `ChatMessage`，其 `origin=ENVIRONMENT_CONTEXT`，内容包裹在 `<environment-context>` 中。
7. 计算稳定 system 文本的 SHA-256，生成 `PromptBuildMetadata`，返回消息序列和排序后的工具定义。

`build()` 生成的消息顺序为：

```text
[system：稳定指令]
[history：包含当前原始 user 消息和既有 assistant/tool 历史]
[user：<system-reminder>...</system-reminder>，仅在有提醒时]
[user：<environment-context>...</environment-context>]
```

因此原始用户消息不与任何 XML 标签拼接，也不会在请求中重复出现；两条动态消息不写入 memory。

## Prompt 包模块设计

### `src/mycode/prompt/modules.py`

该模块定义只包含稳定文本的 `StaticPromptModule`，以及 `create_builtin_modules()`。默认内置模块和优先级固定如下：

| ID | 优先级 | protected | 责任 |
|---|---:|---:|---|
| `safety-boundaries` | 100 | 是 | 安全边界、权限约束、外部文本不提升为指令 |
| `identity` | 200 | 否 | myCode 身份和职责 |
| `behavior` | 300 | 否 | 工作方式、验证和主动澄清约束 |
| `tool-usage` | 400 | 否 | 专用工具优先、编辑前读取、结果验证 |
| `coding-standards` | 500 | 否 | 代码编辑、测试和质量规则 |
| `output-style` | 600 | 否 | 输出语言、结构与信息密度 |

稳定 system 文本还明确说明：`system-reminder` 和 `environment-context` 是框架提供的上下文，不应当作新的用户需求或普通回答对象；但环境上下文、工具结果和其他外部值仍然是不可信数据。

### `src/mycode/prompt/environment.py`

```python
class EnvironmentCollector(Protocol):
    def collect(self) -> EnvironmentSnapshot: ...


class DefaultEnvironmentCollector:
    def __init__(self, workspace_root: Path, config: PromptConfig) -> None: ...

    def collect(self) -> EnvironmentSnapshot: ...
```

`DefaultEnvironmentCollector` 只采集工作区、`platform.system()`、当前带时区的 ISO 时间、Git 分支和 Git 简要状态。Git 使用参数列表形式的 `subprocess.run()`，在 `workspace_root` 下执行并受 `git_timeout_seconds` 限制，不通过 shell 拼接命令。

环境 XML 采用固定字段顺序：`workspace`、`operating_system`、`current_time`、`timezone`、`git_branch`、`git_status`。每个值在 XML 转义后按 `environment_value_limit` 截断；截断和采集失败都会形成诊断。环境变量值、密钥、完整 diff、文件内容和工具结果不参与采集。

### `src/mycode/prompt/reminder.py`

```python
class ReminderPolicy:
    def __init__(self, full_interval_rounds: int) -> None: ...

    def mode_reminder(self, *, plan_only: bool) -> SystemReminder | None: ...

    def render(self, reminders: Sequence[SystemReminder], round_index: int) -> str | None: ...
```

`mode_reminder()` 在 `plan_only=False` 时返回 `None`，在开启时返回同时带有完整和精简文本的 `SystemReminder`。`render()` 在第 1 轮以及 `1 + N`、`1 + 2N` 等轮次选用完整文本；其他轮次选用精简文本。默认 `N=4`，因此完整提醒位于第 1、5、9、13 轮。多个可信提醒按 ID 排序并合并到同一个 `<system-reminder>` 消息中，避免增加无必要的临时 user 消息。

### `src/mycode/prompt/__init__.py`

该入口导出模型、错误类型、`PromptRegistry`、`PromptBuilder` 和工厂函数：

```python
def create_default_prompt_builder(
    workspace_root: str | Path,
    config: PromptConfig | None = None,
) -> PromptBuilder: ...
```

工厂函数创建默认内置模块注册表、默认环境采集器和默认提醒策略。它是 CLI 和默认 `AgentLoop` 构造路径的唯一默认装配入口。

## Agent、工具与会话集成

### `src/mycode/agent/config.py`

`AgentConfig` 移除 `minimal_system_prompt`，新增：

```python
prompt: PromptConfig = field(default_factory=PromptConfig)
```

现有 `max_rounds`、模型超时和整次运行超时字段保持不变。`PromptConfig` 从 `prompt` 包导入，`agent` 不复制提示词配置或默认文本。

### `src/mycode/agent/history.py`

`make_system_message()` 保留公共兼容入口，但将其生成的消息标记为 `MessageOrigin.SYSTEM_INSTRUCTION`。`make_user_message()`、assistant 消息和 tool 消息维持 `MessageOrigin.CONVERSATION`。PromptBuilder 自己构造运行时提醒和环境消息，不使用 memory helper，因此不会误写入 memory。

### `src/mycode/agent/events.py`

新增：

```python
class AgentEventType(str, Enum):
    # 既有成员保持不变
    USAGE = "usage"


class AgentErrorCode(str, Enum):
    # 既有成员保持不变
    PROMPT_ERROR = "prompt_error"


@dataclass(frozen=True)
class AgentEvent:
    # 既有字段保持顺序
    usage: UsageObservation | None = None
```

`USAGE` 在每个 model round 的完成事件携带 `UsageObservation` 时产生。它不改变文本、工具、审批、取消和最终回复事件的顺序语义。

### `src/mycode/agent/loop.py`

`AgentLoop.__init__()` 增加可选 `prompt_builder: PromptBuilder | None`。未注入时，使用当前工作目录和 `config.prompt` 调用 `create_default_prompt_builder()`；CLI 会显式注入同样的默认构建器，避免工作区来源不明确。

`AgentLoop` 增加单调递增的 `_next_turn_id`。每次 `run()`：

1. 先把原始 user 文本写入 memory，并发出既有 `USER_MESSAGE` 事件。
2. 调用 `prompt_builder.begin_turn(turn_id=..., plan_only=mode.plan_only)`，得到本 turn 唯一的 `TurnPromptContext`。
3. 每个 model round 调用 `prompt_builder.build(history=self._memory.messages(), tools=self._tool_executor.definitions(), turn=..., round_index=...)`。
4. 把 `PromptBuildResult.messages` 和 `PromptBuildResult.tools` 传入 `BaseLLM.stream_chat()`。
5. 收到带 `usage` 的 `DONE` 事件时，先发出 `AgentEventType.USAGE`，再执行既有的工具调度或最终回复路径。
6. 捕获 `PromptConfigurationError` 和 `PromptBuildError`，发出 `PROMPT_ERROR`，不调用 LLM。

`clear_memory()` 不重置全局模块注册表或构建器配置；`ChatSession.clear()` 继续清 memory 和复位 `plan_only`。下一次 `run()` 一定重新创建环境快照。

### `src/mycode/tool/registry.py`

`ToolRegistry.definitions()` 改为按 `ToolDefinition.name` 升序返回定义。注册顺序仍用于工具实例查找和调用，不改变工具执行、读写批次或审批行为。将排序放在注册表层，能使 PromptBuilder、协议直接调用和测试获得同一工具顺序。

### `src/mycode/cli.py`

CLI 在创建默认工具注册表后，以 `Path.cwd()` 和 `AgentConfig().prompt` 创建默认 PromptBuilder，并把它注入 `AgentLoop`。CLI 不拼接 prompt 文本，也不采集环境；它只负责依赖装配。

## LLM 配置、协议与观测

### `src/mycode/config.py`

新增：

```python
@dataclass(frozen=True)
class UsageConfig:
    request_stream_usage: bool = False


@dataclass(frozen=True)
class LLMConfig:
    # 既有字段保持不变
    usage: UsageConfig = field(default_factory=UsageConfig)
```

`load_config()` 读取可选 YAML 映射：

```yaml
usage:
  request_stream_usage: true
```

缺失时使用 `False`，从而保持现有 OpenAI 兼容网关请求不变。字段不是映射或 `request_stream_usage` 不是布尔值时抛出 `ConfigError`。

### `src/mycode/protocols/openai_chat.py`

请求继续保留内部消息顺序，忽略 `ChatMessage.origin`。仅当 `config.usage.request_stream_usage=True` 时，追加：

```python
"stream_options": {"include_usage": True}
```

流处理器从最终 usage chunk 读取：

- `prompt_tokens` -> `input_tokens`；
- `completion_tokens` -> `output_tokens`；
- `total_tokens` -> `total_tokens`；
- `prompt_tokens_details.cached_tokens` -> `cache_read_tokens`。

OpenAI Chat 不提供统一的缓存写入字段，因此 `cache_write_tokens=None`。适配器从响应头优先读取 `x-request-id`，其次读取 `request-id`。收到 `[DONE]` 时输出带观测的 `StreamEvent(DONE, usage=...)`；未收到 usage 时仍输出 provider 和请求 ID 已知、其余字段为 `None` 的观测。

### `src/mycode/protocols/openai_responses.py`

请求保留独立 user 消息顺序，忽略 `origin`。在 `response.completed` 事件中读取 response usage：

- `input_tokens` -> `input_tokens`；
- `output_tokens` -> `output_tokens`；
- `total_tokens` -> `total_tokens`；
- `input_tokens_details.cached_tokens` -> `cache_read_tokens`。

缓存写入字段没有已知统一来源时保持 `None`。完成事件携带 `UsageObservation(provider="openai_responses", ...)`；响应失败事件行为不变。

### `src/mycode/protocols/anthropic.py`

协议层从内部消息中分离所有 `role="system"` 消息，并按原始顺序以双换行拼接到 Anthropic 请求的顶层 `system` 字段。PromptBuilder 只产生一条稳定 system 消息，因此正常路径只有一段稳定指令；若调用方提供额外 system 消息，仍以确定性顺序合并。

其余消息，包括原始用户消息、独立 `system-reminder`、独立 `environment-context`、assistant 历史和 tool 历史，按顺序保留在 `messages` 数组中。`origin` 不会发送给供应商。

流事件中的 `message_delta.usage` 映射为：

- `input_tokens` -> `input_tokens`；
- `output_tokens` -> `output_tokens`；
- `cache_read_input_tokens` -> `cache_read_tokens`；
- `cache_creation_input_tokens` -> `cache_write_tokens`。

在 `message_stop` 时输出带观测的完成事件。请求 ID 使用与其他协议相同的响应头读取规则。

### 观测字段解析规则

各协议只接受整数类型且排除布尔值。负数、字符串、列表、对象和缺失字段都转换为 `None`，不得导致流失败。协议实现保留收到的最后一个有效 usage 快照；如果同一流出现多个 usage 片段，使用后出现的非空字段覆盖前一快照对应字段。

## 模块交互与数据流

### 普通文本 round

```text
user 输入
  -> AgentLoop 写入原始 user 消息到 memory
  -> PromptBuilder.begin_turn() 采集一次环境
  -> PromptBuilder.build() 组装 system + memory + reminder + environment
  -> 协议适配器发送请求
  -> 协议适配器返回文本增量与 DONE(usage)
  -> AgentLoop 发出 USAGE，再发出 FINAL_RESPONSE
```

### 工具调用后的下一 round

```text
第一轮工具调用
  -> AgentLoop 将 assistant tool call 和 tool result 写入 memory
  -> 使用同一个 TurnPromptContext 再次调用 PromptBuilder.build()
  -> 原始 user、tool 历史仍在 memory；环境快照不重新采集
  -> reminder 按新的 round_index 选择完整或精简文本
  -> 协议适配器处理下一次请求
```

### 提示词构建失败

```text
受保护模块渲染失败 / 非法模块配置
  -> PromptBuildError 或 PromptConfigurationError
  -> AgentLoop 产生 AgentEvent(ERROR, error_code=PROMPT_ERROR)
  -> 本轮不调用 LLM，不修改既有 memory
```

环境采集失败不进入该失败路径。它只让对应环境字段为 `unknown` 并写入构建诊断，随后继续发送模型请求。

## 文件组织

```text
src/mycode/
├── prompt/
│   ├── __init__.py           # 公共导出和默认 PromptBuilder 工厂
│   ├── models.py             # 配置、模块、上下文、快照、构建结果、诊断
│   ├── registry.py           # PromptRegistry 与提示词错误类型
│   ├── modules.py            # 六个内置稳定模块和静态模块实现
│   ├── environment.py        # 环境/Git 采集、截断、XML 格式化
│   ├── reminder.py           # 可信提醒和完整/精简周期
│   └── builder.py            # PromptBuilder 与请求消息组装
├── llm/
│   ├── __init__.py           # 导出 MessageOrigin 和 UsageObservation
│   └── base.py               # ChatMessage、StreamEvent 与 usage 契约
├── agent/
│   ├── __init__.py           # 导出新增配置与事件类型
│   ├── config.py             # AgentConfig 接入 PromptConfig
│   ├── events.py             # USAGE 和 PROMPT_ERROR
│   ├── history.py            # 稳定 system 的消息来源标记
│   └── loop.py               # turn 上下文、构建调用、usage 转发
├── tool/registry.py          # 工具定义确定性排序
├── config.py                 # UsageConfig 与 YAML 解析
├── cli.py                    # 默认 PromptBuilder 依赖装配
└── protocols/
    ├── anthropic.py          # 顶层 system、usage 映射和请求 ID
    ├── openai_chat.py        # 可选 stream usage、usage 映射和请求 ID
    └── openai_responses.py   # usage 映射和请求 ID

tests/
├── test_prompt_registry.py       # 注册、排序、启用、禁用、override、保护规则
├── test_prompt_builder.py        # 稳定文本、消息顺序、metadata、工具排序、失败策略
├── test_prompt_environment.py    # 单次快照、Git 降级、截断、XML 转义和敏感值排除
├── test_prompt_reminder.py       # 默认 4 轮和自定义周期、完整/精简提醒
├── test_llm_base.py              # MessageOrigin、UsageObservation 和 StreamEvent
├── test_agent_events.py          # USAGE、PROMPT_ERROR 事件契约
├── test_agent_loop.py            # 多 round 快照复用、临时消息不入 memory、usage 转发
├── test_config.py                # usage YAML 配置和类型校验
├── test_tool_registry.py         # definitions() 名称排序
├── test_openai_chat_protocol.py  # 独立消息、可选 stream usage、请求 ID、未知字段
├── test_openai_responses_protocol.py # usage/cache 映射和未知字段
├── test_anthropic_protocol.py    # 顶层 system、独立消息、usage/cache 映射
├── test_e2e_chat.py              # 文本、工具、plan-only、/clear、环境变化和 usage 场景
└── test_docs.py                  # README 和 Stage 04 文档说明

README.md                          # 记录 Stage 04 能力和明确非目标
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 提示词代码边界 | 所有构建逻辑放在 `prompt` 包 | 让 Agent 只编排流程，协议只序列化请求 |
| 稳定模块扩展 | `PromptModule.render()` + `PromptRegistry` | 调用方可扩展，同时统一排序和保护约束 |
| 保护模块 | 不允许 disable 或 override | 不能通过配置移除安全边界 |
| 动态消息 | 两条独立 user-role 消息 | 原始用户文本不被改写，临时上下文不写 memory |
| 环境生命周期 | 每个 turn 一次不可变快照 | 同一工具循环内一致，下一 turn 可反映变化 |
| XML | 只允许框架生成两个固定外层标签，值统一转义 | 降低标签注入和提示词注入风险 |
| 工具顺序 | `ToolRegistry.definitions()` 按名称排序 | 所有请求路径共享确定性顺序 |
| 消息来源 | `ChatMessage.origin` 仅作内部元数据 | 协议载荷保持兼容，同时满足内部可区分性 |
| usage 未知状态 | `None` 表示 `unknown` | 缺失字段不等于缓存未命中 |
| OpenAI Chat usage 请求 | YAML 显式启用，默认关闭 | 不改变已有 OpenAI 兼容网关的请求负载 |
| Anthropic system | 映射到顶层 `system` | 符合 Anthropic Messages 的请求模型 |
| usage 上报 | 每个 model round 产生 `AgentEvent.USAGE` | 多轮工具流程可分别观察缓存与 token 数据 |
| 环境和 usage 错误 | 环境/usage 降级，受保护模块 fail-closed | 保持对话可用性，同时不放松安全约束 |

## Spec 覆盖关系

| Spec 需求 | 设计归属 |
|---|---|
| F1-F2 | `PromptModule`、`PromptRegistry`、`modules.py` |
| F3-F4 | `TurnPromptContext`、`PromptBuilder`、`AgentLoop` |
| F5 | `SystemReminder`、`ReminderPolicy`、稳定模块规则 |
| F6 | `EnvironmentCollector`、`EnvironmentSnapshot`、XML 格式化 |
| F7 | `PromptConfig.full_reminder_interval_rounds` 与 `ReminderPolicy` |
| F8 | `tool-usage` 模块与 `ToolRegistry.definitions()` |
| F9 | `UsageObservation`、`StreamEvent`、协议 usage 解析、`AgentEvent.USAGE` |
| F10 | 提示词错误类型、Agent `PROMPT_ERROR`、环境和 usage 降级 |
| N1-N5 | `prompt`、`llm`、`agent`、`protocols` 的单向依赖和职责划分 |
| N6-N8 | 测试文件组织、fake/fixture 策略、保持现有 Agent 和工具契约 |
| AC1-AC13 | 新增 prompt 单元测试、协议 fixture、Agent/e2e 回归和 README 文档测试 |

## 中文提示词与工具并发调用补充设计

### 提示词文本边界

`src/mycode/prompt/modules.py` 保持六个模块的 ID、优先级和保护属性不变，只将稳定文本替换为中文。`src/mycode/prompt/reminder.py` 将 `plan-only` 的完整与精简提醒改为中文。`src/mycode/prompt/environment.py` 保持 `<environment-context>` 标签、字段顺序、XML 转义和截断行为不变，只将字段显示名改为中文。`<system-reminder>` 和 `<environment-context>` 是结构化协议标签，不翻译；原始用户输入、工具定义、异常信息和日志不改动。

### 配置模型与 YAML

`src/mycode/config.py` 新增 `ToolConfig(parallel_calls: bool = True)`，并在 `LLMConfig` 中以 `field(default_factory=ToolConfig)` 提供 `tools` 字段。`load_config()` 调用 `_parse_tools(raw.get("tools"))`；缺失的 `tools` 段返回默认值，非映射或 `parallel_calls` 非布尔值保持现有 `ConfigError` 失败模式。YAML 形状为：

```yaml
tools:
  parallel_calls: false
```

该名称描述模型能力，不携带 OpenAI 专用字段名，也不进入 `prompt` 包。

### 协议映射

`OpenAIChatLLM.stream_chat()` 与 `OpenAIResponsesLLM.stream_chat()` 在 `tools` 非空时读取 `self.config.tools.parallel_calls`，将其写入各自 payload 的 `parallel_tool_calls`。没有工具定义时继续省略该字段。Anthropic 不读取此字段，也不增加供应商特有参数。

### 测试策略

`tests/test_prompt_registry.py` 断言所有稳定模块文本为中文；`tests/test_prompt_reminder.py` 断言完整与精简提醒为中文；`tests/test_prompt_environment.py` 断言环境字段名为中文且 XML 标签不变。`tests/test_config.py` 覆盖工具并发默认开启、YAML 显式关闭和非法值拒绝。两个 OpenAI 协议测试分别覆盖默认 payload 为 `parallel_tool_calls: true`、显式关闭为 `false`，以及无工具时继续省略该字段。

| 决策点 | 选择 | 理由 |
|---|---|---|
| 模型可见提示词语言 | 仅替换 `prompt` 包发送给模型的自然语言文本 | 满足中文交互要求且不影响程序错误与外部数据 |
| XML 标签 | 保留既有英文标签 | 标签是稳定的结构边界，避免破坏协议映射与已有测试 |
| 工具并发配置 | `ToolConfig.parallel_calls`，默认 `True` | 使用协议无关的能力名称，避免向配置泄漏供应商字段 |
| 协议支持 | 两个 OpenAI 适配器映射开关，Anthropic 保持不变 | 只向支持该请求参数的供应商发送字段 |

| F11 | `prompt/modules.py`、`prompt/reminder.py`、`prompt/environment.py` 的模型可见中文文本 |
| F12 | `ToolConfig`、YAML 解析与 OpenAI 工具请求映射 |
| N9 | `ToolConfig` 位于配置边界，`prompt` 包不依赖协议配置 |
| AC14-AC15 | 中文提示词断言、并发默认值和显式关闭的协议 fixture |
