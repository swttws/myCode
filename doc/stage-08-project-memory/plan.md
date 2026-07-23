# Stage 08 项目知识与长期记忆 Plan

## 架构概览

Stage 08 在现有 `mycode.memory` 包内新增长期记忆与会话恢复边界。该边界统一负责项目指令加载、JSONL 会话存档与恢复、自动笔记和记忆索引；`agent`、`prompt`、`session` 和 `cli` 只通过门面接入，不直接拼路径、扫描文件或更新笔记。

新增门面命名为 `ProjectMemoryManager`，位于 `src/mycode/memory/manager.py`。它在每次普通用户请求前刷新长期上下文：清理过期会话、恢复最近会话、加载三层指令和用户/项目记忆索引，并返回可注入提示词的框架上下文块。它在 Agent 自然停止后追加 JSONL 会话记录，并异步调用 LLM 更新自动笔记。

短期上下文压缩继续由 Stage 07 的 `ContextManager` 负责。恢复后的历史如果超过预算，`ProjectMemoryManager` 不实现第二套压缩策略，而是把恢复历史交给现有 `ContextManager` 进行安全压缩。`PromptBuilder` 继续只负责请求消息组装；它接收框架上下文块，但不读取磁盘。

请求前链路为：

```text
ChatSession.send(user_text)
  → AgentLoop.run(user_text)
  → ProjectMemoryManager.before_user_request()
       → 清理过期 JSONL 会话
       → 恢复当前项目最近未过期会话（仅首次需要）
       → 读取项目指令和记忆索引
       → 返回 FrameworkContext
  → AgentLoop 追加当前 user message
  → PromptBuilder.begin_turn(framework_context=...)
  → PromptBuilder.build(...)
  → ContextManager.prepare_auto(...)
  → LLM stream_chat(...)
```

自然停止后链路为：

```text
LLM 输出 final response 且没有继续 tool calls
  → AgentLoop 追加 assistant final message
  → yield FINAL_RESPONSE
  → ProjectMemoryManager.after_final_response(...)
       → 追加 user/assistant JSONL 记录
       → 创建后台任务更新自动笔记和索引
```

## 核心数据结构

### MemoryScope

```python
class MemoryScope(str, Enum):
    USER = "user"
    PROJECT = "project"
```

`USER` 表示 `~/.mycode/memory/`，`PROJECT` 表示 `~/.mycode/projects/<workspace_sha256>/memory/`。

### MemoryKind

```python
class MemoryKind(str, Enum):
    USER_PREFERENCE = "user_preference"
    CORRECTION = "correction"
    PROJECT_KNOWLEDGE = "project_knowledge"
    REFERENCE = "reference"
```

默认归属规则为：`USER_PREFERENCE` 和 `CORRECTION` 进入用户级，`PROJECT_KNOWLEDGE` 和 `REFERENCE` 进入项目级。LLM 可以返回合法的归属调整。

### MemoryDiagnostic

```python
@dataclass(frozen=True)
class MemoryDiagnostic:
    code: str
    message: str
    scope: MemoryScope | None = None
    path: str | None = None
    line: int | None = None
```

诊断只记录稳定原因、路径和行号，不保存完整敏感正文。

### InstructionBlock 与 InstructionLoadResult

```python
class InstructionLayer(str, Enum):
    PROJECT_ROOT = "project_root"
    PROJECT_DIRECTORY = "project_directory"
    USER = "user"


@dataclass(frozen=True)
class InstructionBlock:
    layer: InstructionLayer
    path: str
    priority: int
    text: str
    sha256: str


@dataclass(frozen=True)
class InstructionLoadResult:
    blocks: tuple[InstructionBlock, ...]
    rendered_text: str
    diagnostics: tuple[MemoryDiagnostic, ...]
```

优先级固定为 `PROJECT_ROOT=100`、`PROJECT_DIRECTORY=200`、`USER=300`，数字越小越靠前。

### SessionRecord

```python
class SessionRecordType(str, Enum):
    MESSAGE = "message"


@dataclass(frozen=True)
class SessionRecord:
    type: SessionRecordType
    timestamp: str
    role: str
    content: str
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_arguments: str | None = None
    origin: str = "conversation"
```

JSONL 单行只保存可恢复字段，不保存派生 meta。`origin` 使用字符串保存，恢复时只接受已知 `MessageOrigin`，未知值降级为普通 conversation。

### SessionSummary 与 SessionRestoreResult

```python
@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    path: str
    title: str
    message_count: int
    updated_at: str | None
    recoverable: bool


@dataclass(frozen=True)
class SessionRestoreResult:
    summary: SessionSummary | None
    history: tuple[ChatMessage, ...]
    skipped_lines: int
    truncated_at_boundary: bool
    time_gap_seconds: int | None
    time_gap_block: "FrameworkContextBlock | None"
    diagnostics: tuple[MemoryDiagnostic, ...]
```

`title` 从第一条 user 消息派生，截断为适合展示的单行文本。`recoverable=False` 表示扫描时没有可恢复消息或工具边界无法形成安全历史。

### MemoryNote 与 MemoryIndexBundle

```python
@dataclass(frozen=True)
class MemoryNote:
    note_id: str
    scope: MemoryScope
    kind: MemoryKind
    path: str
    frontmatter: dict[str, str]
    body: str
    updated_at: str
    source_session_id: str | None
    sha256: str


@dataclass(frozen=True)
class MemoryIndexBundle:
    scope: MemoryScope
    entries: tuple[str, ...]
    rendered_text: str
    line_count: int
    byte_count: int
    truncated: bool
    diagnostics: tuple[MemoryDiagnostic, ...]
```

索引文件名固定为 `index.md`。注入时合并用户级和项目级索引，总量受 200 行和 25KB 限制。

### FrameworkContextBlock 与 FrameworkContext

```python
class FrameworkContextKind(str, Enum):
    INSTRUCTIONS = "instructions"
    MEMORY_INDEX = "memory_index"
    RESTORE_NOTICE = "restore_notice"


@dataclass(frozen=True)
class FrameworkContextBlock:
    id: str
    kind: FrameworkContextKind
    priority: int
    content: str


@dataclass(frozen=True)
class FrameworkContext:
    blocks: tuple[FrameworkContextBlock, ...]
    restored_history: tuple[ChatMessage, ...]
    session_summary: SessionSummary | None
    diagnostics: tuple[MemoryDiagnostic, ...]
```

`PromptBuilder` 只理解 `FrameworkContextBlock`，不依赖指令、会话和笔记的内部类型。

### NoteUpdateDecision 与 NoteUpdateResult

```python
class NoteUpdateAction(str, Enum):
    CREATE = "create"
    MERGE = "merge"
    UPDATE = "update"
    IGNORE = "ignore"


@dataclass(frozen=True)
class NoteUpdateDecision:
    action: NoteUpdateAction
    scope: MemoryScope | None
    kind: MemoryKind | None
    target_note_id: str | None
    title: str | None
    body: str | None
    reason: str


@dataclass(frozen=True)
class NoteUpdateResult:
    created: int
    merged: int
    updated: int
    ignored: int
    diagnostics: tuple[MemoryDiagnostic, ...]
```

解析器必须拒绝缺少必要字段的写入动作。`IGNORE` 可以没有 `scope`、`kind`、`target_note_id` 和 `body`。

## 核心接口

### MemoryPaths

```python
class MemoryPaths:
    def __init__(self, *, workspace_root: Path, home: Path) -> None: ...

    @property
    def project_digest(self) -> str: ...

    @property
    def project_store_root(self) -> Path: ...

    @property
    def sessions_dir(self) -> Path: ...

    @property
    def user_memory_dir(self) -> Path: ...

    @property
    def project_memory_dir(self) -> Path: ...

    def ensure_directories(self) -> None: ...

    def validate_project_path(self, path: Path) -> Path: ...

    def validate_user_mycode_path(self, path: Path) -> Path: ...
```

`project_digest` 使用工作区真实路径的 SHA-256，与现有权限和 Stage 07 缓存隔离策略保持一致。

### InstructionLoader

```python
class InstructionLoader:
    def __init__(
        self,
        *,
        paths: MemoryPaths,
        max_include_depth: int = 5,
    ) -> None: ...

    def load(self) -> InstructionLoadResult: ...
```

`load()` 按 `mycode.md`、`.mycode/instructions.md`、`~/.mycode/instructions.md` 顺序扫描。include 只允许相对当前指令文件的路径，项目级 include 不能跳出工作区，用户级 include 不能跳出 `~/.mycode`。

### SessionArchiveStore

```python
class SessionArchiveStore:
    def __init__(
        self,
        *,
        paths: MemoryPaths,
        now: Callable[[], datetime],
        max_age_days: int = 30,
    ) -> None: ...

    @property
    def current_session_id(self) -> str: ...

    def start_new_session(self) -> None: ...

    def append_message(self, message: ChatMessage) -> None: ...

    def append_messages(self, messages: Sequence[ChatMessage]) -> None: ...

    def list_sessions(self) -> tuple[SessionSummary, ...]: ...

    def latest_recoverable_session(self) -> SessionSummary | None: ...

    def restore_latest(self) -> SessionRestoreResult: ...

    def cleanup_expired(self) -> tuple[MemoryDiagnostic, ...]: ...

    def close(self) -> None: ...
```

追加使用 UTF-8 JSON，一行一个对象。恢复时逐行解析，坏行跳过；末尾工具调用不完整时截断到完整边界之前。

### MemoryNoteStore

```python
class MemoryNoteStore:
    def __init__(
        self,
        *,
        paths: MemoryPaths,
        now: Callable[[], datetime],
    ) -> None: ...

    def load_index_bundle(self, scope: MemoryScope) -> MemoryIndexBundle: ...

    def load_note_summaries(self, scope: MemoryScope) -> tuple[str, ...]: ...

    def apply_decisions(
        self,
        decisions: Sequence[NoteUpdateDecision],
        *,
        source_session_id: str | None,
    ) -> NoteUpdateResult: ...
```

笔记文件名使用稳定 slug 加短哈希，避免标题重名覆盖。索引更新使用临时文件加原子替换。

### NoteUpdatePrompt

```python
class NoteUpdatePrompt:
    def build(
        self,
        *,
        user_message: ChatMessage,
        assistant_message: ChatMessage,
        user_index: MemoryIndexBundle,
        project_index: MemoryIndexBundle,
    ) -> ChatMessage: ...

    def parse(self, text: str) -> tuple[NoteUpdateDecision, ...]: ...
```

提示要求模型输出 JSON 对象，包含 `decisions` 数组。摘要调用必须使用 `tools=[]`，模型返回工具调用时本次笔记更新失败。

### ProjectMemoryManager

```python
class ProjectMemoryManager:
    def __init__(
        self,
        *,
        paths: MemoryPaths,
        instructions: InstructionLoader,
        sessions: SessionArchiveStore,
        notes: MemoryNoteStore,
        note_prompt: NoteUpdatePrompt,
        llm: BaseLLM,
        memory: ConversationMemory,
        time_gap_notice_seconds: int = 86_400,
    ) -> None: ...

    async def before_user_request(
        self,
        *,
        compact_prepare: Callable[[Sequence[ChatMessage]], Awaitable[tuple[ChatMessage, ...]]] | None,
    ) -> FrameworkContext: ...

    def record_user_message(self, message: ChatMessage) -> None: ...

    def record_assistant_message(self, message: ChatMessage) -> None: ...

    def record_tool_history(
        self,
        *,
        assistant_tool_call: ChatMessage | None = None,
        tool_result: ChatMessage | None = None,
    ) -> None: ...

    def after_final_response(
        self,
        *,
        user_message: ChatMessage,
        assistant_message: ChatMessage,
        framework_context: FrameworkContext,
    ) -> None: ...

    def clear_session_state(self) -> None: ...

    async def close(self) -> None: ...
```

`before_user_request()` 只在当前进程第一次请求或 `/clear` 后恢复最近会话；后续请求不重复覆盖正在进行的 memory。项目指令和索引每次请求都重新扫描。

## 模块设计

### `memory.models`

**职责：** 定义 Stage 08 的枚举、诊断、指令、会话、笔记、框架上下文和更新结果类型。

**依赖：** 标准库 dataclass/enum、`mycode.llm.ChatMessage`。

**约束：** 不做 IO、不调 LLM、不依赖 `agent`、`prompt`、`compact`。

### `memory.paths`

**职责：**

- 计算 `~/.mycode/projects/<workspace_sha256>/sessions/`。
- 计算 `~/.mycode/projects/<workspace_sha256>/memory/`。
- 计算 `~/.mycode/memory/`。
- 提供项目级和用户级真实路径校验。

**依赖：** 标准库 `hashlib`、`pathlib`。

**关键边界：** 路径校验必须使用 `Path.resolve()` 后比较父路径；符号链接逃逸必须拒绝。

### `memory.instructions`

**职责：**

- 扫描三层指令文件。
- 展开 `@include`。
- 限制 include 最大嵌套深度。
- 使用 visited 集合防环路。
- 将合法内容按优先级渲染为一个框架上下文文本。

**依赖：** `memory.models`、`memory.paths`。

**关键边界：** include 失败必须形成诊断，同时保留可加载的其余指令内容。

### `memory.sessions`

**职责：**

- 生成 `YYYYMMDD-HHMMSS-xxxx` 会话 ID。
- 追加 JSONL 会话记录。
- 扫描 JSONL 派生 `SessionSummary`。
- 选择最近未过期可恢复会话。
- 恢复 `ChatMessage` 历史。
- 跳过坏行。
- 截断悬空工具调用边界。
- 清理超过 30 天的过期会话。

**依赖：** `memory.models`、`memory.paths`、`mycode.llm.ChatMessage`。

**关键边界：** 不维护 meta sidecar；JSONL 半行或坏行只能影响对应行。

### `memory.notes`

**职责：**

- 读取和写入带 frontmatter 的 Markdown 笔记。
- 按用户级和项目级隔离目录。
- 加载 `index.md`。
- 对索引注入执行 200 行/25KB 限制。
- 执行 LLM 决策返回的新增、合并、修改、忽略动作。
- 原子重建索引。

**依赖：** `memory.models`、`memory.paths`。

**关键边界：** 不做语义去重；只执行已解析且合法的 `NoteUpdateDecision`。

### `memory.note_prompt`

**职责：**

- 构造自动笔记更新提示。
- 说明四类笔记和默认归属规则。
- 注入用户级和项目级索引。
- 要求 JSON 输出。
- 解析 `decisions` 数组。

**依赖：** `memory.models`、`mycode.llm.ChatMessage`。

**关键边界：** 提示禁止工具调用，解析失败不修改任何笔记。

### `memory.manager`

**职责：**

- 作为 Stage 08 唯一公共门面。
- 请求前刷新指令、恢复会话和加载记忆索引。
- 管理当前 session ID 和是否已恢复的进程内状态。
- 将恢复历史写入 `ConversationMemory`。
- 通过传入回调复用 Stage 07 压缩能力。
- 会话过程中追加 JSONL。
- 自然停止后调度自动笔记后台任务。
- `/clear` 后开启新 JSONL 会话并避免恢复旧短期历史。
- 关闭时收尾后台任务。

**依赖：** `memory.*`、`ConversationMemory`、`BaseLLM`。

**关键边界：** 不直接依赖 `AgentLoop`，避免循环依赖；Agent 通过方法调用把消息和生命周期事件通知进来。

### `prompt.models`

**职责：** 增加协议无关的框架上下文块类型，或从 `memory.models.FrameworkContextBlock` 接收只读序列。

**建议：** 为避免 `prompt` 反向依赖 `memory`，在 `prompt.models` 定义同形的轻量类型 `PromptContextBlock`，由 `ProjectMemoryManager` 输出后在 Agent 层转换。

### `prompt.builder`

**职责调整：**

- `begin_turn()` 接收 `framework_blocks: Sequence[PromptContextBlock] = ()`。
- `build()` 在 system instruction 后、conversation history 后追加框架上下文消息。
- 框架上下文消息使用 `role="user"` 和新的 `MessageOrigin.FRAMEWORK_CONTEXT`。

框架上下文必须是临时消息，不写入 conversation memory 或 JSONL。

### `llm.base`

**职责调整：** `MessageOrigin` 增加：

```python
FRAMEWORK_CONTEXT = "framework_context"
```

协议层继续忽略 `origin`，不把内部来源序列化给供应商。

### `agent.loop`

**职责调整：**

- 构造时接收 `project_memory: ProjectMemoryManager | None`。
- 在追加当前 user 前调用 `before_user_request()`。
- 将返回的框架上下文传给 PromptBuilder。
- 追加 user/assistant/tool 历史时同步通知 `ProjectMemoryManager` 写 JSONL。
- final response 事件 yield 后触发 `after_final_response()`。

### `session`

**职责调整：** `/clear` 时调用 `AgentLoop.clear_memory()`；`AgentLoop.clear_memory()` 内部同步清 Stage 07 context 和 Stage 08 project memory session state。

### `cli`

**职责调整：**

- 创建 `MemoryPaths`、`InstructionLoader`、`SessionArchiveStore`、`MemoryNoteStore`、`NoteUpdatePrompt` 和 `ProjectMemoryManager`。
- 将 `ProjectMemoryManager` 注入 `AgentLoop`。
- `finally` 中先关闭 `ProjectMemoryManager`，再关闭 Stage 07 context 和 MCP pool。

## 模块交互

### 请求前刷新

```text
AgentLoop.run(user_text)
  ├─ ProjectMemoryManager.before_user_request(compact_prepare=...)
  │    ├─ SessionArchiveStore.cleanup_expired()
  │    ├─ SessionArchiveStore.restore_latest()（仅首次或 clear 后）
  │    ├─ ConversationMemory.replace(restored_history)
  │    ├─ InstructionLoader.load()
  │    ├─ MemoryNoteStore.load_index_bundle(USER)
  │    ├─ MemoryNoteStore.load_index_bundle(PROJECT)
  │    └─ FrameworkContext
  ├─ ConversationMemory.append(current_user)
  ├─ ProjectMemoryManager.record_user_message(current_user)
  └─ PromptBuilder.begin_turn(framework_blocks=...)
```

### 恢复预算保护

```text
恢复历史
  → 写入 memory 工作副本
  → 调用 compact_prepare(restored_history)
  → 成功：ConversationMemory.replace(compacted_history)
  → 失败：FrameworkContext 带不可安全诊断
  → AgentLoop 遇到不可安全诊断时返回 ERROR，不调用常规模型
```

`compact_prepare` 由 Agent 层包装现有 `ContextManager`。这样 `memory` 包不直接依赖 `compact` 包。

### 工具历史写入

```text
assistant tool call message
  → memory.append(...)
  → ProjectMemoryManager.record_tool_history(assistant_tool_call=...)

tool result message
  → memory.append(...)
  → ProjectMemoryManager.record_tool_history(tool_result=...)
```

JSONL 中保留 `tool_call_id`、`tool_name` 和 `tool_arguments`，保证恢复时能重建协议需要的工具历史。

### 自动笔记更新

```text
FINAL_RESPONSE 已 yield
  → ProjectMemoryManager.after_final_response(...)
       → create_task(_update_notes_async)
       → NoteUpdatePrompt.build(...)
       → llm.stream_chat([prompt], tools=[])
       → NoteUpdatePrompt.parse(...)
       → MemoryNoteStore.apply_decisions(...)
```

后台任务失败只形成诊断和日志，不影响已经返回给用户的 final response。

### `/clear`

```text
ChatSession.clear()
  → AgentLoop.clear_memory()
       → ContextManager.clear()
       → ProjectMemoryManager.clear_session_state()
  → AgentMode.reset()
  → PermissionService.clear_session()
```

`clear_session_state()` 关闭当前 JSONL 会话并生成新 session ID。它不删除用户级或项目级笔记。

## 文件组织

```text
src/mycode/
├── memory/
│   ├── __init__.py          # 导出现有 memory 和 Stage 08 门面
│   ├── base.py              # 现有 ConversationMemory
│   ├── in_memory.py         # 现有进程内 memory
│   ├── models.py            # Stage 08 数据结构与诊断
│   ├── paths.py             # mycode 持久化路径与边界校验
│   ├── instructions.py      # 三层指令加载与 @include
│   ├── sessions.py          # JSONL 会话追加、扫描、恢复、清理
│   ├── notes.py             # Markdown 笔记、索引和决策应用
│   ├── note_prompt.py       # 自动笔记 LLM 提示与输出解析
│   └── manager.py           # ProjectMemoryManager 门面
├── llm/
│   └── base.py              # 增加 FRAMEWORK_CONTEXT origin
├── prompt/
│   ├── models.py            # 增加 PromptContextBlock
│   └── builder.py           # 注入框架上下文临时消息
├── agent/
│   └── loop.py              # 请求前刷新、JSONL 记录、自动笔记触发
├── session.py               # /clear 生命周期保持薄转发
├── cli.py                   # 装配 ProjectMemoryManager
└── tui.py                   # 如需显示诊断，消费现有或新增事件

tests/
├── test_memory_paths.py
├── test_memory_instructions.py
├── test_memory_sessions.py
├── test_memory_notes.py
├── test_memory_note_prompt.py
├── test_memory_manager.py
├── test_prompt_builder.py
├── test_agent_loop.py
├── test_session.py
├── test_cli.py
├── test_docs.py
└── test_project_memory_e2e.py

doc/
└── stage-08-project-memory/
    ├── spec.md
    ├── plan.md
    ├── task.md
    └── checklist.md

README.md
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 包边界 | 长期记忆全部放在 `src/mycode/memory/` | 符合项目语义，也避免新建与 memory 重叠的 `knowledge` 包 |
| 公共门面 | `ProjectMemoryManager` | Agent 只接生命周期事件，不理解指令、JSONL 和笔记细节 |
| 路径根 | `~/.mycode/projects/<workspace_sha256>/` 和 `~/.mycode/memory/` | 沿用现有 `mycode` 命名和项目隔离方式 |
| 项目指令顺序 | `mycode.md` → `.mycode/instructions.md` → `~/.mycode/instructions.md` | 项目级高于用户级，高优先级靠前 |
| include 语法 | 行级 `@include relative/path.md` | 简单可测试，避免实现 Markdown AST |
| include 深度 | 默认最大 5 层 | 足够组合手写指令，同时防止递归膨胀 |
| include 防环 | 真实路径 visited 集合 | 同一文件经不同相对路径引用时仍能识别环路 |
| 会话格式 | 一行一个 JSON object | 追加快，坏行可跳过，崩溃最多影响最后一行 |
| 会话元数据 | 扫描 JSONL 派生 | 避免 meta sidecar 同步问题 |
| 会话 ID | `YYYYMMDD-HHMMSS-xxxx` | 便于按文件名排序，同时用随机后缀防同秒冲突 |
| 自动恢复 | 进程首次请求前恢复最近未过期会话 | 满足自动接续，同时避免每轮覆盖当前 memory |
| 恢复预算 | 通过回调复用 `ContextManager` | 不在 memory 包中引入 compact 依赖和第二套压缩 |
| 时间跨度提醒 | 超过 24 小时注入恢复提醒 | 与用户“隔太久”需求匹配，阈值可在实现中集中配置 |
| 过期清理 | 30 天以上 JSONL 会话 | 区分 Stage 07 24 小时短期归档缓存 |
| 笔记格式 | Markdown + YAML-like frontmatter | 人可读、易编辑，符合用户指定格式 |
| frontmatter 解析 | 手写窄范围 key/value 解析 | 本阶段不引入新依赖，字段固定可控 |
| 笔记去重 | LLM 输出决策，系统只校验和落盘 | 符合“去重交给 LLM”，避免硬编码语义规则 |
| 索引限制 | 注入前总量限制 200 行/25KB | 满足约 2-3K tokens 预算目标 |
| 索引截断 | 用户索引优先，项目索引其次；各自保持文件顺序 | 用户偏好跨项目更重要，规则确定可复现 |
| 异步笔记 | final response yield 后 `create_task` | 不阻塞用户看到最终回复 |
| 关闭策略 | close 时取消未完成后台任务 | 避免退出等待不确定网络耗时 |
| 框架上下文注入 | 独立临时 user-role 消息，`origin=FRAMEWORK_CONTEXT` | 不污染普通 conversation memory，也保持协议兼容 |
| 观测方式 | `FrameworkContext.diagnostics` 加必要 Agent 事件/日志 | 测试可见，不泄露完整敏感正文 |
| 注释 | 只在路径校验、JSONL 恢复、工具边界、后台写入、索引限制写中文原因注释 | 满足用户要求并保持代码不过度注释 |

## Spec 覆盖

| Spec | 设计归属 |
|---|---|
| F1 | `ProjectMemoryManager.before_user_request` |
| F2 | `InstructionLoader` |
| F3 | `InstructionLoader` + `MemoryPaths` |
| F4 | `SessionArchiveStore.append_message` |
| F5 | `SessionArchiveStore.list_sessions` |
| F6 | `SessionArchiveStore.latest_recoverable_session` + `ProjectMemoryManager` |
| F7 | `SessionArchiveStore.restore_latest` |
| F8 | `SessionArchiveStore` 工具边界截断 |
| F9 | `ProjectMemoryManager.before_user_request` 的 compact 回调 |
| F10 | `SessionRestoreResult.time_gap_block` |
| F11 | `SessionArchiveStore.cleanup_expired` |
| F12 | `MemoryKind` 默认归属 + `NoteUpdateDecision` |
| F13 | `ProjectMemoryManager.after_final_response` |
| F14 | `NoteUpdatePrompt` + `MemoryNoteStore.apply_decisions` |
| F15 | `MemoryScope` + 分离目录 |
| F16 | `MemoryIndexBundle` + `FrameworkContextBlock` |
| F17 | `MemoryPaths` |
| F18 | `MemoryNoteStore` 文件索引设计 |
| N1 | `MemoryPaths`、include 和笔记路径校验 |
| N2 | 固定排序、扫描派生和确定性截断 |
| N3 | JSONL 坏行跳过和工具边界截断 |
| N4 | 请求前流程只做本地 IO，笔记更新异步 LLM |
| N5 | sessions、memory、Stage 07 context 路径分离 |
| N6 | Agent/Prompt/Protocol 薄接线 |
| N7 | `MemoryDiagnostic` 和框架上下文诊断 |
| N8 | JSONL append、笔记写入和索引原子替换 |
| N9 | `FRAMEWORK_CONTEXT` 临时消息 |
| N10 | 文件组织与命名 |
| N11 | 技术决策和任务阶段约束 |
| N12 | 测试文件使用 fake LLM、临时目录、固定时间 |
| N13 | 仅标准库文件型实现 |

