# Stage 07 上下文管理 Plan

## 架构概览

上下文管理采用一个位于 `mycode.compact` 包内的统一门面 `ContextManager`。它不直接承担所有细节，而是协调五个职责单一的组件：

- `TokenEstimator`：维护最近有效 usage 锚点，计算完整请求及增量字符的估算 Token。
- `ArtifactStore`：管理工作区隔离的会话缓存、原子写入、回滚、清理和受限读取。
- `ToolResultCompactor`：执行单项 8K 与同批 12K 的轻量归档策略。
- `ConversationCompactor`：负责近期边界、用户原文优先、结构化摘要、递归强制压缩和确定性应急压缩。
- `ContextManager`：执行请求前顺序、三次重试、熔断、历史原子提交和压缩观测。

常规请求链路为：

```text
Agent 构造候选完整请求
  → ContextManager 轻量处理历史
  → Agent 用处理后历史重建完整请求
  → TokenEstimator 估算
  → 未达到触发线：直接返回安全请求
  → 达到触发线：重量压缩或熔断后的应急压缩
  → 重建并重新估算
  → 原子提交新历史
  → Agent 发送常规模型请求
  → DONE usage 回写 TokenEstimator
```

重量压缩始终在工作副本和归档事务中进行。正式摘要、格式校验和预算校验全部成功后才同时提交历史与归档；失败则回滚本次新建归档，原历史保持不变。轻量归档完成后可独立提交，因为它不依赖摘要结果。

`/compact` 通过 `TUI → ChatSession → AgentLoop → ContextManager` 进入同一套重量压缩流程，不写入用户历史，也不发送普通聊天请求。

受限读取采用常驻只读工具 `read_compact_artifact`。它只接受当前上下文管理器签发的归档路径，并支持分段读取与最大返回量限制，避免模型重读一个超大归档后再次把整个文件塞回上下文。

现有包外只做必要适配：

- 配置层解析上下文窗口和 8K/12K 阈值。
- Memory 增加原子替换历史的能力。
- 三个协议适配器把供应商 usage 统一放入现有 `UsageObservation`。
- Agent 接入请求前管理和 usage 回写。
- TUI 展示 `/compact` 和压缩状态。
- CLI 创建上下文管理器、注册只读归档工具，并在退出时清理会话缓存。

## 核心数据结构

### CompactConfig

```python
@dataclass(frozen=True)
class CompactConfig:
    context_window_tokens: int
    tool_result_threshold_tokens: int = 8_000
    tool_batch_threshold_tokens: int = 12_000
```

YAML 使用 `compact` 节点，`context_window_tokens` 必填。其余已确认策略作为内部固定策略集中定义。

### CompactPolicy

```python
@dataclass(frozen=True)
class CompactPolicy:
    preview_tokens: int = 2_000
    auto_reserve_tokens: int = 13_000
    manual_reserve_tokens: int = 3_000
    keep_recent_tokens: int = 10_000
    min_recent_messages: int = 5
    max_attempts: int = 3
    stale_after_seconds: int = 86_400
```

### RequestSnapshot 与 TokenEstimate

```python
@dataclass(frozen=True)
class RequestSnapshot:
    ascii_chars: int
    non_ascii_chars: int
    fingerprint: str


@dataclass(frozen=True)
class TokenEstimate:
    tokens: int
    source: Literal["full_chars", "usage_delta"]
    anchor_input_tokens: int | None
    delta_tokens: int
```

`RequestSnapshot` 由消息和工具定义的确定性 JSON 表示生成。增量估算公式为：

```text
最近 input_tokens
+ 当前请求字符估算
- 锚点请求字符估算
```

结果最小钳制为 0。

### 归档模型

```python
@dataclass(frozen=True)
class ArchivedArtifact:
    path: str
    kind: Literal["tool_result", "user_message", "history"]
    original_chars: int
    estimated_tokens: int
    sha256: str


@dataclass(frozen=True)
class ArtifactSlice:
    path: str
    text: str
    next_offset: int
    eof: bool
```

### 压缩状态与结果

```python
class CompactAction(str, Enum):
    NONE = "none"
    LIGHT = "light"
    HEAVY = "heavy"
    FORCE = "force"
    EMERGENCY = "emergency"


class CompactStatus(str, Enum):
    SAFE = "safe"
    COMPACTED = "compacted"
    NO_OP = "no_op"
    FAILED = "failed"


class CompactFailureCode(str, Enum):
    LLM_ERROR = "llm_error"
    TOOL_ATTEMPT = "tool_attempt"
    INVALID_FORMAT = "invalid_format"
    SUMMARY_TOO_LARGE = "summary_too_large"
    BUDGET_NOT_RECOVERED = "budget_not_recovered"
    ARCHIVE_ERROR = "archive_error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class CompactReport:
    status: CompactStatus
    actions: tuple[CompactAction, ...]
    before_tokens: int
    after_tokens: int
    archived_count: int
    attempts: int
    circuit_open: bool
    failure_code: CompactFailureCode | None = None
    message_zh: str = ""


@dataclass(frozen=True)
class LightCompactResult:
    history: tuple[ChatMessage, ...]
    artifacts: tuple[ArchivedArtifact, ...]
    changed: bool


@dataclass(frozen=True)
class HeavyCompactResult:
    history: tuple[ChatMessage, ...]
    artifacts: tuple[ArchivedArtifact, ...]
    actions: tuple[CompactAction, ...]
    summary: str | None


@dataclass(frozen=True)
class PreparedContext:
    request: PromptBuildResult
    snapshot: RequestSnapshot
    estimate: TokenEstimate
    report: CompactReport


class CompactError(RuntimeError):
    def __init__(self, report: CompactReport) -> None: ...
```

`ContextManager.prepare_auto()` 在归档失败且无法恢复安全预算时抛出 `CompactError`；异常必须携带失败报告，且该路径不返回任何可发送请求。

### RequestBuilder

```python
class RequestBuilder(Protocol):
    def __call__(
        self,
        history: Sequence[ChatMessage],
    ) -> PromptBuildResult: ...
```

Agent 每轮用当前 `TurnPromptContext`、工具定义和 round index 构造该闭包。压缩器只传入候选历史，不理解 PromptBuilder 内部规则。

## 核心接口

### TokenEstimator

```python
class TokenEstimator:
    def snapshot(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDefinition],
    ) -> RequestSnapshot: ...

    def estimate(self, snapshot: RequestSnapshot) -> TokenEstimate: ...

    def estimate_text(self, text: str) -> int: ...

    def record_usage(
        self,
        snapshot: RequestSnapshot,
        usage: UsageObservation,
    ) -> None: ...

    def reset(self) -> None: ...
```

### ArtifactStore

```python
class ArchiveTransaction:
    def archive_text(
        self,
        *,
        kind: Literal["tool_result", "user_message", "history"],
        text: str,
    ) -> ArchivedArtifact: ...

    def commit(self) -> None: ...
    def rollback(self) -> None: ...


class ArtifactStore:
    def begin(self) -> ArchiveTransaction: ...

    def read(
        self,
        path: str,
        *,
        offset: int = 0,
        max_tokens: int = 2_000,
    ) -> ArtifactSlice: ...

    def reset_session(self) -> None: ...
    def close(self) -> None: ...
```

读取接口只接受当前会话已登记路径，`max_tokens` 不得超过 2K，并使用同一字符估算器截取，避免重读结果再次过大。

### ToolResultCompactor

```python
class ToolResultCompactor:
    def compact(
        self,
        history: Sequence[ChatMessage],
        transaction: ArchiveTransaction,
    ) -> LightCompactResult: ...
```

已归档工具消息继续保留原 `role="tool"` 和 `tool_call_id`，仅替换正文并标记新的内部来源，协议层不会序列化该标记。

### ConversationCompactor

```python
class ConversationCompactor:
    async def compact(
        self,
        history: Sequence[ChatMessage],
        *,
        mode: Literal["auto", "manual"],
        build_request: RequestBuilder,
        transaction: ArchiveTransaction,
        run_deadline: float | None,
    ) -> HeavyCompactResult: ...

    def emergency(
        self,
        history: Sequence[ChatMessage],
        *,
        build_request: RequestBuilder,
        transaction: ArchiveTransaction,
    ) -> HeavyCompactResult: ...
```

递归强制压缩是 `compact()` 的内部路径，不暴露第二套公共入口。

### ContextManager

```python
class ContextManager:
    @property
    def artifact_tool(self) -> Tool: ...

    async def prepare_auto(
        self,
        *,
        build_request: RequestBuilder,
        run_deadline: float | None,
    ) -> PreparedContext: ...

    async def compact_manual(
        self,
        *,
        build_request: RequestBuilder,
        run_deadline: float | None,
    ) -> CompactReport: ...

    def record_usage(
        self,
        snapshot: RequestSnapshot,
        usage: UsageObservation,
    ) -> None: ...

    def clear(self) -> None: ...
    def close(self) -> None: ...
```

`ContextManager` 持有现有 `ConversationMemory`。`clear()` 清空 Memory、usage 锚点、失败计数和当前归档会话，然后创建新的空归档会话；`close()` 删除当前归档会话，但不处理权限或 MCP 资源。

创建入口为：

```python
def create_context_manager(
    *,
    workspace_root: Path,
    home: Path,
    llm: BaseLLM,
    memory: ConversationMemory,
    config: CompactConfig,
    model_timeout_seconds: float | None,
) -> ContextManager: ...
```

### ConversationMemory

```python
def replace(self, messages: Sequence[ChatMessage]) -> None: ...
```

压缩成功时由管理器一次性替换历史；失败时不调用 `replace()`。

### 外部接口调整

- `AgentLoop.compact(mode)`：异步执行手动压缩并产生压缩事件。
- `ChatSession.compact()`：转发手动压缩，不创建用户消息。
- `AgentEventType.COMPACTION`：携带 `CompactReport`，供 TUI 和测试观察；`AgentEvent` 增加 `compaction: CompactReport | None = None` 字段。
- `AgentErrorCode.COMPACTION_ERROR`：表示请求前上下文管理无法安全完成，常规模型请求不得发送。
- `read_compact_artifact(path, offset=0, max_tokens=2000)`：只读当前会话归档。
- `MessageOrigin.COMPACT_PREVIEW`：标记已归档工具结果或用户消息预览。
- `MessageOrigin.COMPACT_SUMMARY`：标记正式摘要。
- `MessageOrigin.COMPACT_BOUNDARY`：标记持久化边界提醒。

## 模块设计

### `compact.models`

**职责：** 定义配置、固定策略、估算结果、归档信息、压缩报告和错误码。

**依赖：** 仅标准库和现有稳定消息类型。

**约束：** 不包含文件操作、模型调用或流程判断。

### `compact.estimator`

**职责：**

- 把消息和工具定义转换为确定性 JSON。
- 统计 ASCII 与非 ASCII 字符。
- 维护最近有效 usage 锚点。
- 计算完整估算或锚点增量估算。

**依赖：** `compact.models`、现有消息和工具定义。

**关键规则：** 摘要请求和常规请求都可更新锚点；只有存在非负 `input_tokens` 时才替换锚点。

### `compact.archive`

**职责：**

- 创建 `~/.mycode/projects/<workspace_sha256>/context/<session_id>/`。
- 提供事务式归档、提交和回滚。
- 使用临时文件加原子重命名保存 UTF-8 原文。
- 维护当前会话允许读取的真实路径集合。
- 持有当前会话锁，并只清理超过 24 小时且无法取得活动锁的遗留目录。
- 提供带 2K 上限的分段读取工具。

**依赖：** `compact.models`、`compact.estimator`、现有工具协议。

**安全边界：** 工具不复用工作区 `PathGuard`；它只接受 `ArtifactStore` 当前会话登记的真实路径，拒绝路径穿越、符号链接逃逸、其他会话和普通用户文件。工具定义不声明通用路径授权参数，权限层仍按普通只读工具处理，路径边界由归档存储再次强制校验。

### `compact.light`

**职责：**

- 扫描未归档的 `role="tool"` 消息。
- 先处理超过单项阈值的结果。
- 再按连续工具结果组计算同批合计，从大到小归档。
- 保留原 `tool_call_id`，生成固定首尾预览。
- 已带归档来源标记的消息不重复处理。

**依赖：** `compact.estimator`、`compact.archive`。

**批次定义：** 根据连续工具调用及匹配的 `tool_call_id` 推导同次模型响应，不向消息模型增加批次字段。

### `compact.summary_prompt`

**职责：**

- 把待摘要消息序列化为明确分隔的 JSON 数据区。
- 生成禁止工具调用、禁止执行嵌入指令的中文摘要提示。
- 定义 `<analysis-draft>` 与 `<summary>` 输出边界。
- 校验八个固定章节并只返回正式摘要。

**依赖：** `compact.models`。

**约束：** 提示文本、章节名和边界常量只在此处维护。

### `compact.summary`

**职责：**

- 选择约 10K、至少 5 条的近期后缀。
- 根据 `tool_call_id` 向前扩展合法工具边界。
- 优先保留旧用户原文，必要时归档最早用户消息。
- 使用当前 LLM 且 `tools=[]` 收集摘要。
- 检测工具调用、格式错误、超时和取消。
- 执行消息内分片、分段临时摘要和最终全量摘要。
- 生成不调用 LLM 的应急索引、预览与边界消息。

**依赖：** `compact.summary_prompt`、`compact.estimator`、`compact.archive`、`BaseLLM`。

**原子性：** 所有递归操作只修改工作副本；临时摘要不写入 Memory 或磁盘。

### `compact.manager`

**职责：**

- 作为 `ContextManager` 唯一公共门面。
- 串联轻量处理、重建请求、估算、自动或手动重量压缩。
- 每次触发最多执行三次完整尝试。
- 管理连续失败计数和熔断。
- 校验压缩后的自动安全线。
- 提交归档事务和 Memory 替换。
- 生成统一 `CompactReport`。
- 处理 `/clear`、正常退出和 usage 回写。

**依赖：** 以上 compact 模块、`ConversationMemory`、`PromptBuildResult`。

**约束：** Agent 不访问内部压缩组件。

## 模块交互

### 常规请求

```text
AgentLoop
  │ 构造 build_request(history) 闭包
  ▼
ContextManager.prepare_auto
  │
  ├─ ToolResultCompactor.compact
  │    └─ ArchiveTransaction
  │
  ├─ build_request → TokenEstimator
  │
  ├─ 安全 → 返回 PreparedContext
  │
  └─ 超线
       ├─ circuit closed → ConversationCompactor.compact，最多三次
       └─ circuit open   → ConversationCompactor.emergency
              │
              ├─ 事务提交
              ├─ ConversationMemory.replace
              └─ 重建请求并返回 PreparedContext
```

Agent 发送 `PreparedContext.request`。收到带 usage 的 `DONE` 后，用同一个 `RequestSnapshot` 更新锚点，再继续对外发送原有 usage 事件。

### 手动压缩

```text
/compact
  → ChatSession.compact
  → AgentLoop.compact
  → ContextManager.compact_manual
  → 三次摘要尝试或应急降级
  → COMPACTION 事件
  → TUI 显示中文结果
```

该流程不追加用户消息，也不产生普通聊天响应。

### 归档重读

```text
模型调用 read_compact_artifact
  → 现有权限与调度流程
  → ArtifactStore 精确校验当前会话路径
  → 按 offset 和 2K Token 上限返回分片
  → 下一次请求进入正常轻量检查
```

### 重试、熔断与应急路径

```text
完整压缩尝试失败
  → 回滚本次归档事务
  → 失败计数 +1
  → 未到 3 次：从原历史重新执行完整流程
  → 达到 3 次：打开熔断
       → 新建应急事务
       → 归档旧历史确定性 JSON
       → 生成本地索引、预览、边界
       → 重建请求并验证低于自动安全线
       → 提交事务与历史
```

手动 `/compact` 在熔断期间仍可发起最多三次完整摘要尝试。成功后关闭熔断并清零计数；失败时保持熔断，历史继续使用应急压缩结果。

## 文件组织

```text
src/mycode/
├── compact/
│   ├── __init__.py          # 稳定导出和创建入口
│   ├── models.py            # 配置、策略、结果、错误码
│   ├── estimator.py         # 字符估算与 usage 锚点
│   ├── archive.py           # 缓存事务、生命周期、受限读取工具
│   ├── light.py             # 工具结果轻量归档
│   ├── summary_prompt.py    # 摘要 Prompt、输出解析与章节校验
│   ├── summary.py           # 重量、递归强制和应急压缩
│   └── manager.py           # ContextManager 编排、重试与熔断
├── agent/
│   ├── events.py            # 增加 COMPACTION 事件
│   └── loop.py              # 请求前接入、usage 回写、手动入口
├── memory/
│   ├── base.py              # 增加 replace 抽象方法
│   └── in_memory.py         # 原子替换列表
├── protocols/
│   ├── anthropic.py         # 归一化 Anthropic usage
│   ├── openai_chat.py       # 归一化流式 Chat usage
│   └── openai_responses.py  # 归一化 Responses usage
├── llm/base.py              # 增加压缩消息 origin
├── config.py                # 解析 compact 配置
├── session.py               # 转发 compact、clear
├── tui.py                   # /compact 与压缩状态
└── cli.py                   # 创建、注册、退出清理

tests/
├── test_compact_estimator.py
├── test_compact_archive.py
├── test_compact_light.py
├── test_compact_summary_prompt.py
├── test_compact_summary.py
├── test_compact_manager.py
├── test_context_compaction_e2e.py
└── 现有 config/memory/protocol/agent/session/tui 测试文件

examples/
├── mycode.anthropic.yaml
├── mycode.openai-chat.yaml
└── mycode.openai-responses.yaml

README.md
```

## Spec 覆盖

| Spec | 设计归属 |
|---|---|
| F1 | `CompactConfig`、根配置解析、启动校验 |
| F2 | `ContextManager.prepare_auto`、Agent 请求前接线 |
| F3 | `ToolResultCompactor`、`ArtifactStore` |
| F4 | `ToolResultCompactor` 的批次选择和逐次重估 |
| F5 | `ArtifactStore`、ContextManager clear/close、CLI 生命周期 |
| F6 | `TokenEstimator`、三个协议 usage 归一化、Agent usage 回写 |
| F7 | `ContextManager` 自动安全线 |
| F8 | ContextManager/Agent/Session/TUI 手动入口 |
| F9 | `ConversationCompactor` 近期选择和工具边界闭包 |
| F10 | `ConversationCompactor` 用户原文保留与归档 |
| F11 | `compact.summary_prompt` 八章节校验 |
| F12 | `compact.summary_prompt`、无工具摘要收集器 |
| F13 | `ConversationCompactor` 分片和递归收缩 |
| F14 | ContextManager 重试、归档事务、Memory replace |
| F15 | ContextManager 熔断、ConversationCompactor emergency |
| F16 | `compact.summary` 构造并持久化边界消息 |
| F17 | `CompactFailureCode`、`CompactReport`、Agent/TUI 反馈 |

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 接入位置 | 在 `PromptBuilder` 生成候选完整请求后、协议发送前执行 | 能同时计算系统提示、运行时提醒、环境上下文、历史和工具定义 |
| 包边界 | 压缩实现全部位于 `mycode.compact` | 外部文件只保留适配与接线，避免继续扩大 `AgentLoop` |
| 请求估算表示 | 对消息与工具定义生成排序稳定、UTF-8 JSON 快照 | 不绑定供应商 wire payload，同时保证相同输入产生相同增量 |
| 字符估算 | ASCII `ceil(chars / 4)`，非 ASCII `ceil(chars / 1.5)`，两者相加 | 对中文与代码混合内容比统一比例更稳妥 |
| usage 锚点 | 只接受非负 `input_tokens`；锚点保存实际值及对应请求快照 | `total_tokens` 混有输出，不能作为下一次输入预算基准 |
| Chat usage | 保留现有 `usage.request_stream_usage` 开关；开启时解析最终 usage chunk | 不强制兼容网关支持 `stream_options`，关闭或缺失时自动回退字符估算 |
| Responses usage | 从完成事件携带的 response usage 归一化 | 不改变现有流式文本和工具事件顺序 |
| Anthropic usage | 累积开始事件的输入/cache usage 与增量事件的输出 usage，在停止事件附加 | 保持上层只在 `DONE` 处理统一 usage |
| 轻量批次识别 | 根据连续工具调用及匹配的 `tool_call_id` 推导，不给消息增加 batch 字段 | 当前 Agent 已保证同轮调用和结果有序，避免扩张公共消息模型 |
| 压缩消息来源 | 为预览、正式摘要和边界增加内部 `MessageOrigin` 值 | 协议层不序列化 origin，但压缩器可避免重复归档 |
| 正式历史布局 | `保留的旧用户原文 → assistant 摘要 → user 边界消息 → 近期原文` | 用户原文不改写，摘要承担旧回复状态，边界在进入近期历史前生效 |
| 工具对完整性 | 切点根据 `tool_call_id` 做闭包扩展 | 比仅依赖相邻位置更能处理同轮多个工具调用 |
| 摘要输入 | 使用确定性 JSON 数据区，不直接拼接未转义对话文本 | 降低消息中伪造标签或提示注入破坏摘要协议的风险 |
| 摘要调用 | 同一 `BaseLLM`，只传一个摘要 user 消息，明确 `tools=[]` | 复用现有模型配置，并从能力层和提示层同时禁止工具 |
| 摘要输出 | `<analysis-draft>` 后跟 `<summary>`；正式区必须包含八个 Markdown 标题 | 可稳定丢弃草稿并验证结构，缺失即计为失败 |
| 摘要大小 | 正式摘要最多 3K 估算 Token；超过即使格式正确也失败 | 给近期约 10K 原文与自动 13K 余量留下明确边界 |
| 递归强制压缩 | 优先压缩最早可容纳块；单消息超限时先归档再按字符预算分片 | 所有原文可恢复，同时确保最终仍执行一次全量摘要 |
| 用户原文降级 | 从最早用户消息开始归档，保留固定首尾预览和路径 | 最大限度保留近期用户要求，并使选择确定 |
| 历史提交 | 每次尝试使用工作副本和归档事务；摘要、格式、预算全部通过后提交 | 任一步失败都不会留下半替换历史或无效引用 |
| 重试 | 同一次触发立即重跑完整流程，最多三次 | 符合已批准语义；不复用可能不完整的临时摘要 |
| 熔断 | 第三次失败后打开会话级熔断并立即执行本地应急压缩 | 不继续消耗摘要调用，也不发送可能溢出的请求 |
| 应急归档 | 保存待压缩旧历史的确定性 JSON，生成本地索引和预览 | 不依赖模型，仍能恢复逐条消息及工具字段 |
| 归档格式 | UTF-8 JSON；正文和消息元数据分字段保存，并记录 SHA-256 | 可验证完整性，也避免依赖 Markdown 转义还原原文 |
| 归档读取 | 精确登记路径、字符 offset、最多 2K 估算 Token 的分片 | 不开放任意用户目录读取，也避免一次重载全部大文件 |
| 缓存清理 | 会话 UUID 和活动文件锁隔离；`/clear` 轮换会话目录，退出删除，启动时只清理超过 24 小时且未持有活动锁的目录 | 不误删并行运行的长会话，并限制异常遗留 |
| 配置校验 | `0 < 2K < 单项阈值 <= 批次阈值 < 窗口上限 - 13K` | 启动时排除无法形成有效安全区的组合 |
| 超时与取消 | 摘要子调用同时受现有 model timeout 和整次 run deadline 约束 | 压缩不能绕过 Agent 已有终止语义 |
| 观测 | 一个结构化 `COMPACTION` 事件加不含正文的日志 | TUI、测试和后续调用方共享同一结果模型 |
| 依赖 | 只使用 Python 标准库和现有项目依赖 | 本阶段不为估算、缓存或摘要引入新包 |
| 注释规范 | 仅在预算切点、原子提交、递归收缩、路径验证和熔断转换处写中文注释 | 解释关键“为什么”，保持其余代码简洁可读 |
