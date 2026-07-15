# myCode 阶段 03：Agent Loop 与事件流技术设计

## 架构概览

Stage 03 采用“新增 Agent 层、保留既有协议与工具边界”的方案。新增 `src/mycode/agent` 包作为 Agent Loop 的唯一主边界，负责 ReAct 多轮循环、状态判断、过程事件契约、工具分批调度、`plan-only` 审批暂停、取消与超时处理。现有 `protocols` 仍只负责把供应商 SSE 映射成内部 LLM 事件；现有 `tool` 包仍只负责工具定义、注册和执行；现有 `memory` 继续保存 `ChatMessage` 历史。

系统分为五层：

- `llm/protocols` 层：继续输出现有 `StreamEvent`，包括文本、thinking、工具调用、done 和 error。Stage 03 不把 provider 事件直接暴露给 TUI / CLI，而是由 Agent 层转换为稳定 `AgentEvent`。
- `tool` 层：在 `ToolDefinition` 中增加显式工具分类，区分读类和写类。默认六个核心工具补齐分类；OpenAI tool spec 转换忽略该本地分类字段，避免影响 API payload。
- `agent` 层：新增 Agent 事件、配置、模式状态、拦截器、工具调度器和主循环。主循环消费 LLM 事件，收集同一模型轮次里的工具调用，按“连续读并发、写单独串行”的规则执行，再把工具结果写入 memory 并进入下一轮。
- `session` 层：`ChatSession` 改为薄封装，持有 memory、Agent 实例和会话模式状态，对外仍提供 `send()`、`clear()` 等入口。它不再直接执行工具或解释 LLM 事件。
- `tui/cli` 层：CLI 负责组装 LLM、memory、tool registry、tool executor、Agent 配置和 session；TUI 只消费 `AgentEvent`，支持 `/plan-only` 会话内切换，并在收到写工具审批事件时向用户询问批准、拒绝或取消。

核心运行路径是：

1. TUI 把用户输入交给 `ChatSession.send()`。
2. `ChatSession` 委托 `AgentLoop.run()`，并传入当前 `plan-only` 状态、审批回调和可选取消 token。
3. Agent 写入 user 历史并产出 `USER_MESSAGE` 事件。
4. Agent 用最小 system prompt 加当前 memory 调用 LLM。
5. Agent 将 LLM 文本、thinking 映射为稳定 `AgentEvent` 吐给上层，同时收集本轮工具调用。
6. 如果本轮无工具调用，Agent 写入 assistant 文本历史，产出 `FINAL_RESPONSE` 并结束。
7. 如果本轮有工具调用，Agent 写入 assistant 工具调用历史，按批次执行工具，逐个产出工具开始与结果事件，把结果写回 memory，再进入下一轮。
8. 任一轮触发最大轮数、取消、超时或不可恢复错误时，Agent 产出对应事件并停止。

## 核心数据结构

### `ToolKind`

位置：`src/mycode/tool/base.py`

```python
class ToolKind(str, Enum):
    READ = "read"
    WRITE = "write"
```

用于显式声明工具分类。读类工具可以在相邻批次中并发执行；写类工具必须单独串行执行。

### `ToolDefinition`

位置：`src/mycode/tool/base.py`

```python
@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: JSONSchema
    kind: ToolKind
```

`kind` 是本地 Agent 调度元信息，不进入 OpenAI tool payload。默认工具分类：

- `read_file`: `ToolKind.READ`
- `find_files`: `ToolKind.READ`
- `search_code`: `ToolKind.READ`
- `write_file`: `ToolKind.WRITE`
- `edit_file`: `ToolKind.WRITE`
- `run_command`: `ToolKind.WRITE`

### `AgentEventType`

位置：`src/mycode/agent/events.py`

```python
class AgentEventType(str, Enum):
    USER_MESSAGE = "user_message"
    THINKING_DELTA = "thinking_delta"
    TEXT_DELTA = "text_delta"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_RESULT = "tool_result"
    FINAL_RESPONSE = "final_response"
    ERROR = "error"
    CANCELLED = "cancelled"
    APPROVAL_REQUIRED = "approval_required"
```

这是 TUI / CLI 的稳定事件契约。LLM 的 `StreamEventType` 保留为内部协议事件，不直接作为上层 UI 契约。

### `AgentErrorCode`

位置：`src/mycode/agent/events.py`

```python
class AgentErrorCode(str, Enum):
    LLM_ERROR = "llm_error"
    TOOL_ERROR = "tool_error"
    UNKNOWN_TOOL = "unknown_tool"
    INVALID_TOOL_KIND = "invalid_tool_kind"
    MAX_ROUNDS_EXCEEDED = "max_rounds_exceeded"
    MODEL_TIMEOUT = "model_timeout"
    TOOL_TIMEOUT = "tool_timeout"
    RUN_TIMEOUT = "run_timeout"
    CANCELLED = "cancelled"
    APPROVAL_CANCELLED = "approval_cancelled"
```

错误事件必须包含机器可判断的 `code` 和人类可读 `message`。

### `AgentEvent`

位置：`src/mycode/agent/events.py`

```python
@dataclass(frozen=True)
class AgentEvent:
    type: AgentEventType
    content: str = ""
    round_index: int = 0
    tool_call: ToolCall | None = None
    tool_result: ToolResult | None = None
    approval_request: ApprovalRequest | None = None
    error_code: AgentErrorCode | None = None
```

字段规则：

- `content`: 文本增量、最终回复、错误说明或状态说明。
- `round_index`: 从 1 开始，标记当前 Agent 轮次。
- `tool_call`: 工具开始或审批事件中携带待处理工具调用。
- `tool_result`: 工具执行结果事件中携带结果。
- `approval_request`: 写工具审批事件中携带审批上下文。
- `error_code`: 错误或取消类事件中携带机器可读类别。

### `AgentConfig`

位置：`src/mycode/agent/config.py`

```python
@dataclass(frozen=True)
class AgentConfig:
    max_rounds: int = 8
    model_timeout_seconds: float | None = None
    run_timeout_seconds: float | None = None
    minimal_system_prompt: str = (
        "You are myCode, a terminal coding assistant. "
        "Use tools when needed. In plan-only mode, produce a plan for user approval "
        "and do not assume write tools are allowed unless approved."
    )
```

Stage 03 先不把这些字段全部暴露到 YAML；CLI 使用默认值。后续如需配置，再接入 `config.py`。

### `AgentMode`

位置：`src/mycode/agent/state.py`

```python
@dataclass
class AgentMode:
    plan_only: bool = False

    def reset(self) -> None:
        self.plan_only = False
```

会话级状态，由 `ChatSession` 持有并在 `/clear` 时复位。`/plan-only` 命令切换该状态。

### `ApprovalRequest` / `ApprovalDecision`

位置：`src/mycode/agent/approval.py`

```python
@dataclass(frozen=True)
class ApprovalRequest:
    id: str
    tool_call: ToolCall
    reason: str
    plan_only: bool
    round_index: int
```

```python
class ApprovalDecisionType(str, Enum):
    APPROVE_ONCE = "approve_once"
    REJECT = "reject"
    CANCEL = "cancel"
```

```python
@dataclass(frozen=True)
class ApprovalDecision:
    type: ApprovalDecisionType
```

审批只对当前 `ApprovalRequest.tool_call` 生效；不改变 `AgentMode.plan_only`。

### `ApprovalProvider`

位置：`src/mycode/agent/approval.py`

```python
ApprovalProvider = Callable[[ApprovalRequest], Awaitable[ApprovalDecision]]
```

TUI 提供真实审批交互；测试提供 fake provider。CLI 本身不直接审批。

### `ToolInterceptor`

位置：`src/mycode/agent/interceptor.py`

```python
class InterceptDecisionType(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"
```

```python
@dataclass(frozen=True)
class InterceptDecision:
    type: InterceptDecisionType
    reason: str = ""
    result: ToolResult | None = None
```

```python
class ToolInterceptor(Protocol):
    async def before_tool(
        self,
        call: ToolCall,
        definition: ToolDefinition,
        mode: AgentMode,
        round_index: int,
    ) -> InterceptDecision:
        ...

    async def after_tool(
        self,
        call: ToolCall,
        result: ToolResult,
        mode: AgentMode,
        round_index: int,
    ) -> ToolResult:
        ...
```

默认拦截器规则：非 `plan-only` 全部放行；`plan-only` 下读工具放行，写工具要求审批。

### `ToolBatch`

位置：`src/mycode/agent/scheduler.py`

```python
@dataclass(frozen=True)
class ToolBatch:
    kind: ToolKind
    calls: tuple[ToolCall, ...]
```

调度规则：

- 连续 `READ` 调用合并为一个 batch。
- 每个 `WRITE` 调用单独成为一个 batch。
- 工具分类缺失或非法时抛出调度错误，由 Agent 转换为 `ERROR` 事件。

### `AgentLoop`

位置：`src/mycode/agent/loop.py`

```python
class AgentLoop:
    def __init__(
        self,
        *,
        llm: BaseLLM,
        memory: ConversationMemory,
        tool_executor: ToolExecutor,
        tool_registry: ToolRegistry,
        config: AgentConfig | None = None,
        interceptor: ToolInterceptor | None = None,
    ) -> None:
        ...

    async def run(
        self,
        user_text: str,
        *,
        mode: AgentMode,
        approval_provider: ApprovalProvider | None = None,
    ) -> AsyncIterable[AgentEvent]:
        ...
```

职责：

- 写入 user history。
- 调用 LLM 并消费内部流事件。
- 产出稳定 Agent 事件。
- 收集每轮工具调用。
- 按批次执行工具并回填结果。
- 管理继续、终止、错误、取消、超时。

### `ChatSession`

位置：`src/mycode/session.py`

```python
class ChatSession:
    def __init__(
        self,
        *,
        agent: AgentLoop,
        mode: AgentMode | None = None,
    ) -> None:
        ...

    async def send(
        self,
        user_text: str,
        *,
        approval_provider: ApprovalProvider | None = None,
    ) -> AsyncIterable[AgentEvent]:
        ...

    def set_plan_only(self, enabled: bool) -> None:
        ...

    def is_plan_only(self) -> bool:
        ...

    def clear(self) -> None:
        ...
```

`ChatSession.clear()` 调用 Agent 或 memory 清理历史，并复位 `AgentMode.plan_only=False`。为保持测试和迁移可控，旧的 `ChatSession(llm=..., memory=..., tool_executor=...)` 初始化方式不再作为主要入口；相关测试随 Stage 03 一起迁移到 Agent 事件。

## 模块设计

### `src/mycode/agent/events.py`

**职责：** 定义 TUI / CLI 可依赖的稳定 Agent 事件契约。

**对外接口：** `AgentEventType`、`AgentErrorCode`、`AgentEvent`。

**依赖：** `dataclasses`、`enum`、`mycode.tool.ToolCall`、`mycode.tool.ToolResult`、`ApprovalRequest`。为避免循环导入，`ApprovalRequest` 使用 `from __future__ import annotations` 延迟解析。

### `src/mycode/agent/config.py`

**职责：** 定义 Agent 运行参数和最小 system prompt。

**对外接口：** `AgentConfig`。

**依赖：** 仅标准库 `dataclasses`。

### `src/mycode/agent/state.py`

**职责：** 保存会话级 Agent 状态，当前只包含 `plan_only`。

**对外接口：** `AgentMode`。

**依赖：** 仅标准库 `dataclasses`。

### `src/mycode/agent/approval.py`

**职责：** 定义写工具审批请求、审批结果和审批 provider 类型。

**对外接口：** `ApprovalRequest`、`ApprovalDecisionType`、`ApprovalDecision`、`ApprovalProvider`。

**依赖：** `dataclasses`、`enum`、`typing`、`mycode.tool.ToolCall`。

### `src/mycode/agent/interceptor.py`

**职责：** 提供工具执行前后拦截协议和默认 `plan-only` 拦截器。

**对外接口：** `InterceptDecisionType`、`InterceptDecision`、`ToolInterceptor`、`PlanOnlyInterceptor`。

**依赖：** `mycode.agent.state.AgentMode`、`mycode.tool.ToolCall`、`ToolDefinition`、`ToolKind`、`ToolResult`。

**行为：**

- `before_tool()`：
  - 非 `plan-only`：返回 `ALLOW`。
  - `plan-only` + `READ`：返回 `ALLOW`。
  - `plan-only` + `WRITE`：返回 `REQUIRE_APPROVAL`，reason 说明写工具在 plan-only 下需要用户审批。
- `after_tool()`：默认原样返回 `ToolResult`，作为后续权限策略或审计扩展点。

### `src/mycode/agent/scheduler.py`

**职责：** 将同一模型轮次里的多个 `ToolCall` 按工具分类切成执行批次。

**对外接口：** `ToolBatch`、`ToolScheduleError`、`build_tool_batches(calls, registry)`。

**依赖：** `mycode.tool.ToolCall`、`ToolRegistry`、`ToolKind`。

**行为：**

- 对每个 `ToolCall` 通过 registry 查定义。
- 找不到工具时不在调度器里转为 `ToolResult`，而是抛出 `ToolScheduleError(code="unknown_tool")`，由 Agent 转成错误事件或结构化工具结果。
- 读类连续合并；写类单独成批。
- 分类缺失或不是 `ToolKind.READ/WRITE` 时抛出 `ToolScheduleError(code="invalid_tool_kind")`。

### `src/mycode/agent/history.py`

**职责：** 集中处理 Agent 对 memory 的写入，避免 loop 文件里到处手写 `ChatMessage`。

**对外接口：**

```python
def make_system_message(prompt: str) -> ChatMessage: ...
def make_user_message(text: str) -> ChatMessage: ...
def make_assistant_text_message(text: str) -> ChatMessage: ...
def make_assistant_tool_call_message(call: ToolCall) -> ChatMessage: ...
def make_tool_result_message(call: ToolCall, result: ToolResult) -> ChatMessage: ...
def serialize_tool_result(result: ToolResult) -> str: ...
```

**依赖：** `json`、`mycode.llm.ChatMessage`、`mycode.tool.ToolCall`、`ToolResult`。

**说明：** Stage 02 的 `_serialize_tool_result()` 从 `session.py` 迁移到这里，OpenAI history 格式继续由 protocols 层根据 `ChatMessage` 字段转换。

### `src/mycode/agent/loop.py`

**职责：** Agent Loop 主循环。它是 Stage 03 的行为中心。

**对外接口：** `AgentLoop`。

**依赖：** `asyncio`、`mycode.llm.BaseLLM`、`StreamEventType`、`LLMError`、`ConversationMemory`、`ToolRegistry`、`ToolExecutor`、agent 子模块。

**核心流程：**

1. `run()` 先写入 user memory，并 yield `USER_MESSAGE`。
2. 对 `round_index` 从 1 到 `max_rounds`：
   - 读取 memory。
   - 将最小 system prompt 作为请求上下文的一部分传给 LLM。
   - 消费 LLM stream：
     - `TEXT_DELTA`：累积 assistant 文本，yield `TEXT_DELTA`。
     - `THINKING_DELTA`：yield `THINKING_DELTA`，不写入 assistant 普通历史。
     - `TOOL_CALL`：加入本轮工具调用列表。
     - `DONE`：结束本轮 LLM 消费。
     - `ERROR`：yield `ERROR` 并结束。
   - 若没有工具调用：
     - 若 assistant 文本非空，写入 assistant text memory。
     - yield `FINAL_RESPONSE`，content 为本轮完整 assistant 文本。
     - return。
   - 若有工具调用：
     - 将每个工具调用写入 assistant tool-call history。
     - 调用 scheduler 生成批次。
     - 逐批执行工具，执行前 yield `TOOL_CALL_STARTED`，执行后 yield `TOOL_RESULT` 并写入 tool result history。
     - 当前批次全部处理完成后进入下一轮。
3. 循环耗尽后 yield `ERROR(max_rounds_exceeded)` 并 return。

**审批处理：**

- 执行工具前调用 interceptor。
- `ALLOW`：正常执行。
- `DENY`：使用拦截器提供的 `ToolResult` 或默认拒绝结果，yield `TOOL_RESULT` 并回填。
- `REQUIRE_APPROVAL`：
  - 没有 `approval_provider`：yield `ERROR(approval_cancelled)` 并结束。
  - 有 provider：yield `APPROVAL_REQUIRED`，调用 provider。
  - `APPROVE_ONCE`：执行当前工具一次。
  - `REJECT`：构造 `ok=False` 的结构化拒绝 `ToolResult`，yield 并回填，让模型下一轮继续输出计划。
  - `CANCEL`：yield `CANCELLED` 并结束。

**取消与超时：**

- `asyncio.CancelledError` 被捕获后 yield `CANCELLED`，并在未写入工具结果时停止。
- `model_timeout_seconds` 包裹单次 LLM stream 消费。
- `run_timeout_seconds` 包裹整次 `run()` 的内部实现。
- 工具执行超时仍由现有 `ToolExecutor` 负责，Agent 根据 `ToolResult.content["timed_out"]` 或 error 产出普通 `TOOL_RESULT`；如果是 Agent 层等待工具超时，则产出 `ERROR(tool_timeout)`。

### `src/mycode/session.py`

**职责：** 成为 Agent 的会话门面，保存会话模式，并保留 TUI/CLI 的简单调用入口。

**对外接口：** `ChatSession.send()`、`set_plan_only()`、`is_plan_only()`、`clear()`。

**依赖：** `mycode.agent.AgentLoop`、`AgentMode`、`ApprovalProvider`。

**行为：**

- `send()` 直接 `async for` 转发 `AgentLoop.run()` 的 `AgentEvent`。
- `/clear` 时调用 Agent 关联 memory 的 clear 能力，并 `AgentMode.reset()`。
- 不再直接解释 `StreamEventType` 或执行工具。

### `src/mycode/tui.py`

**职责：** 消费 Agent 事件并提供基本交互命令。

**对外接口：** `ChatTUI`。

**依赖：** `mycode.agent.AgentEventType`、`ApprovalDecision`、`ApprovalDecisionType`。

**新增行为：**

- 启动文案更新为 Stage 03 Agent 模式。
- 增加 `/plan-only` 命令切换：
  - `/plan-only on`
  - `/plan-only off`
  - `/plan-only` 显示当前状态
- `_render_stream()` 消费 `AgentEvent`：
  - `TEXT_DELTA` 输出正文。
  - `THINKING_DELTA` 仅在 `show_thinking=True` 时输出。
  - `TOOL_CALL_STARTED` 输出工具开始。
  - `TOOL_RESULT` 输出工具成功/失败。
  - `APPROVAL_REQUIRED` 调用内部审批提示，返回 `ApprovalDecision`。
  - `FINAL_RESPONSE` 不重复打印已流式输出的正文，只作为本轮完成标记。
  - `ERROR` / `CANCELLED` 输出可读状态。
- 审批提示使用现有输入能力，接受：
  - `y`：批准当前工具一次。
  - `n`：拒绝当前工具。
  - `c`：取消本轮。

### `src/mycode/cli.py`

**职责：** 组装 Stage 03 依赖。

**变化：**

- 创建 `ToolRegistry`、`ToolExecutor` 后，再创建 `AgentLoop`。
- 创建 `ChatSession(agent=agent)`。
- 不直接把 `llm/memory/tool_executor` 传给 session。

### `src/mycode/tool/base.py` 与默认工具

**职责：** 增加工具分类元信息。

**变化：**

- 新增 `ToolKind`。
- `ToolDefinition` 增加 `kind` 字段。
- 默认工具的 `definition` 补齐 `kind`。
- `ToolRegistry.register()` 校验 `definition.kind` 必须是合法 `ToolKind`。
- OpenAI tool spec 转换继续只输出 name、description、parameters，不输出 kind。

## 模块交互

### 普通文本路径

1. TUI 读取用户输入。
2. `ChatSession.send(user_text)` 调用 `AgentLoop.run(user_text, mode=...)`。
3. Agent 写入 user memory，yield `USER_MESSAGE`。
4. Agent 构造 `[system] + memory.messages()` 调用 `BaseLLM.stream_chat(messages, tools=definitions)`。
5. LLM 输出 `TEXT_DELTA` / `THINKING_DELTA` / `DONE`。
6. Agent 将 LLM 事件映射为 `AgentEvent`。
7. 没有工具调用时，Agent 将完整 assistant 文本写入 memory。
8. Agent yield `FINAL_RESPONSE` 并结束。
9. TUI 已经流式输出正文，收到 `FINAL_RESPONSE` 只换行或标记完成。

### ReAct 工具循环路径

1. 模型在第 N 轮输出一个或多个 `TOOL_CALL`。
2. Agent 收集本轮所有工具调用，直到收到 `DONE` 或 LLM stream 结束。
3. Agent 将每个工具调用写入 assistant tool-call history。
4. `build_tool_batches()` 按工具定义分类切批。
5. 对每个 batch：
   - 读批：为每个工具调用创建并发任务。
   - 写批：单个工具调用串行执行。
6. 每个工具执行前，Agent yield `TOOL_CALL_STARTED`。
7. Agent 调用 interceptor：
   - 放行：执行工具。
   - 拒绝：生成拒绝结果。
   - 需要审批：yield `APPROVAL_REQUIRED` 并等待审批 provider。
8. Agent yield `TOOL_RESULT`，并把结果写入 tool result history。
9. 所有 batch 完成后，Agent 进入下一轮 LLM 调用。
10. 模型最终输出文本且无工具调用时，Agent 写入 assistant 文本，yield `FINAL_RESPONSE`。

### `plan-only` 审批路径

1. 用户输入 `/plan-only on`，TUI 调用 `ChatSession.set_plan_only(True)`。
2. 后续用户请求进入 Agent 时，`AgentMode.plan_only=True`。
3. 读工具被 `PlanOnlyInterceptor` 放行。
4. 写工具触发 `REQUIRE_APPROVAL`。
5. Agent yield `APPROVAL_REQUIRED`，TUI 显示工具名、参数和原因，并询问 `y/n/c`。
6. 用户输入 `y`：Agent 执行当前工具一次，`plan_only` 仍保持开启。
7. 用户输入 `n`：Agent 不执行工具，回填结构化拒绝结果，下一轮让模型继续产出计划。
8. 用户输入 `c`：Agent yield `CANCELLED` 并结束本轮。

### 取消与超时路径

- 用户中断 TUI 或外部任务取消时，`asyncio.CancelledError` 传入 Agent。
- Agent 捕获取消后 yield `CANCELLED`。
- 如果工具结果尚未完整产生，不写入 tool result history。
- 单次模型调用超时产出 `ERROR(model_timeout)`。
- 整次请求超时产出 `ERROR(run_timeout)`。
- 现有 `ToolExecutor` 的工具超时保留为结构化 `ToolResult`；Agent 额外保证不会在取消后继续回填结果。

## 文件组织

```text
src/mycode/
├── agent/
│   ├── __init__.py        — 导出 AgentLoop、AgentEvent、AgentConfig、AgentMode、审批类型
│   ├── approval.py        — ApprovalRequest、ApprovalDecision、ApprovalProvider
│   ├── config.py          — AgentConfig 和最小 system prompt
│   ├── events.py          — AgentEventType、AgentErrorCode、AgentEvent
│   ├── history.py         — ChatMessage 构造和 ToolResult 序列化
│   ├── interceptor.py     — ToolInterceptor、PlanOnlyInterceptor、InterceptDecision
│   ├── loop.py            — AgentLoop 主循环
│   ├── scheduler.py       — ToolBatch 和工具分批逻辑
│   └── state.py           — AgentMode
├── tool/
│   ├── base.py            — 新增 ToolKind，ToolDefinition 增加 kind
│   ├── filesystem.py      — 默认文件工具补齐 kind
│   ├── command.py         — run_command 补齐 kind
│   ├── registry.py        — 注册时校验 kind
│   └── __init__.py        — 导出 ToolKind
├── session.py             — 改为 AgentLoop 的薄门面
├── tui.py                 — 消费 AgentEvent，增加 /plan-only 和审批输入
├── cli.py                 — 组装 AgentLoop 与 ChatSession
└── llm/
    └── base.py            — 保持 LLM 内部事件结构，必要时只做兼容性调整
```

测试文件：

```text
tests/
├── test_agent_events.py       — AgentEvent 类型和字段契约
├── test_agent_scheduler.py    — 读写分类分批、未知工具、非法分类
├── test_agent_interceptor.py  — plan-only 拦截规则
├── test_agent_loop.py         — 多轮 ReAct、最终回复、max rounds、取消、超时
├── test_agent_plan_only.py    — 写工具审批 approve/reject/cancel
├── test_tool_registry.py      — ToolKind 注册和默认工具分类
├── test_session.py            — session 转发 AgentEvent、plan-only 状态、clear 复位
├── test_tui.py                — AgentEvent 渲染、/plan-only 命令、审批输入
├── test_cli.py                — CLI 组装 AgentLoop
├── test_e2e_chat.py           — mocked 端到端 Agent Loop
└── test_docs.py               — README 或阶段文档说明
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| Agent 主边界 | 新增 `src/mycode/agent` 包 | 满足本章“相关代码落在 agent 目录下”，也避免 `session.py` 继续膨胀 |
| 对外事件 | 新增 `AgentEvent`，不复用 LLM `StreamEvent` | LLM 事件是协议内部抽象；Agent 事件需要表达审批、取消、最终回复、工具开始等更高层语义 |
| 工具分类 | `ToolDefinition.kind: ToolKind` | 调度器只看显式元信息，不猜工具名或参数 |
| `run_command` 分类 | `WRITE` | 命令可能产生副作用，默认按写类保守处理 |
| 多工具调度 | 连续读并发，写单独串行，保持原顺序 | 兼顾并发能力和模型表达的顺序依赖 |
| 工具调用收集时机 | 等本轮 LLM stream 完成后统一调度 | 简化批次判断，避免边收边跑破坏模型给出的完整顺序 |
| plan-only 审批 | 通过 `ApprovalProvider` 回调 | Agent 不依赖 TUI，测试可以提供 fake provider |
| 审批批准语义 | 只放行当前工具一次 | 符合用户要求，避免一次审批扩大权限 |
| 审批拒绝语义 | 回填结构化拒绝结果并继续 | 让模型能转而输出计划，而不是直接崩掉本轮 |
| 取消语义 | 捕获取消事件，停止推进，未完成结果不回填 | 优先保证 memory 状态一致 |
| 超时配置 | `AgentConfig` 默认值，Stage 03 暂不扩展 YAML | 保持现有配置稳定，后续再把运行参数暴露给用户 |
| system prompt | AgentConfig 中最小 prompt，作为 system message 注入 | 满足本章最小可用约束，不引入复杂 prompt builder |
| `ChatSession` 职责 | 变成 Agent 门面和模式状态持有者 | TUI/CLI 保持简单入口，循环逻辑集中在 agent |
| OpenAI tool payload | 不包含 `ToolKind` | `kind` 是本地调度元信息，不污染 provider API |
| 测试策略 | fake LLM + fake tool + mock TUI 输入 | 不依赖真实网络、真实 API key 或真实终端 |

## Spec 覆盖自查

- F1/F5/F6/F14/F15：由 `AgentLoop`、`AgentConfig`、取消/超时流程覆盖。
- F2/N3：由 `src/mycode/agent` 文件组织覆盖。
- F3/F4/F17：由 `AgentEvent` 和 TUI 消费逻辑覆盖。
- F7/F8/N4/N5/N6/N7：由 `ToolKind`、`ToolDefinition.kind`、`scheduler.py` 和工具批处理覆盖。
- F9/F10/F11/F12/F13/N8/N9：由 `interceptor.py`、`approval.py`、`AgentMode` 和 TUI 审批流程覆盖。
- F16：由 `history.py` 与现有 OpenAI protocol history 转换保持兼容覆盖。
- F18：由 `ChatSession.clear()` 复位 memory 和 `AgentMode` 覆盖。
- F19/N14：由测试文件规划覆盖。
- 不做项：没有引入复杂权限、Agent 递归、索引/RAG、Anthropic 工具调用或复杂 TUI 面板。
