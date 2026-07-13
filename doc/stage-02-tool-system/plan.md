# myCode 阶段 02：工具系统技术设计

## 架构概览

Stage 02 采用工具系统协议无关、OpenAI 协议先接入的方案。工具定义、注册、执行、结果和错误结构统一放在 `src/mycode/tool` 包下；OpenAI Responses 和 OpenAI Chat Completions 只负责把各自 API 的流式工具调用事件映射成内部事件，并把内部历史消息转换为各自 API 需要的格式。

系统拆成四层：

- `tool` 包：定义统一工具接口、工具调用、工具结果、注册中心、执行器、工作目录路径保护、带锁文本缓存，以及六个核心工具实现。
- `llm` 抽象层：扩展统一流式事件，新增工具调用和工具结果事件；`BaseLLM.stream_chat()` 接收可选工具定义。
- `protocols` 层：OpenAI Responses 和 OpenAI Chat Completions 注入工具定义、解析流式参数碎片、生成内部工具调用事件，并负责 provider-specific 历史转换。Anthropic 暂不接入工具调用。
- `session` 层：收到工具调用后执行一次工具，把结果写回 memory，向 TUI 产出工具结果事件，然后结束本轮，不自动再次请求 LLM。

OpenAI 工具调用形状参考官方 Function Calling 文档：https://developers.openai.com/api/docs/guides/function-calling

## 核心数据结构

### `src/mycode/tool/base.py`

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


JSONSchema = dict[str, Any]
ToolArguments = dict[str, Any]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: JSONSchema


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: ToolArguments | None
    raw_arguments: str = ""


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    tool_name: str
    content: dict[str, Any]
    error: str | None = None


class Tool(Protocol):
    @property
    def definition(self) -> ToolDefinition:
        ...

    def execute(self, arguments: ToolArguments) -> ToolResult:
        ...
```

`ToolCall.arguments` 为 `None` 时表示协议层已经拼出完整参数字符串，但 JSON 解析失败。执行器会把它包装成结构化失败结果，而不是让协议层或会话层崩溃。

### `src/mycode/tool/registry.py`

```python
class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None) -> None: ...

    def register(self, tool: Tool) -> None: ...

    def get(self, name: str) -> Tool | None: ...

    def definitions(self) -> list[ToolDefinition]: ...

    def openai_tool_specs(self) -> list[dict[str, object]]: ...
```

`openai_tool_specs()` 输出 OpenAI 可识别的 function tool 结构：

```python
{
    "type": "function",
    "function": {
        "name": definition.name,
        "description": definition.description,
        "parameters": definition.parameters,
    },
}
```

### `src/mycode/tool/executor.py`

```python
class ToolExecutor:
    def __init__(self, registry: ToolRegistry, timeout_seconds: float = 10.0) -> None: ...

    async def execute(self, call: ToolCall) -> ToolResult: ...
```

执行器负责未知工具、非法参数、工具异常和工具超时的统一包装。

### `src/mycode/tool/cache.py`

```python
@dataclass(frozen=True)
class CachedText:
    text: str
    mtime_ns: int
    size: int


class FileTextCache:
    def read_text(self, path: Path) -> str: ...

    def write_text(self, path: Path, text: str) -> None: ...

    def edit_text(self, path: Path, old_text: str, new_text: str) -> tuple[int, str | None]: ...

    def invalidate(self, path: Path) -> None: ...
```

`FileTextCache` 是进程内、实例级缓存，由 `create_default_tool_registry()` 创建并注入读文件、写文件和改文件工具。它不使用模块级可变全局变量。内部用 `threading.RLock` 保护缓存字典、路径锁表和同一路径的读改写流程；写入或改写成功后同步更新缓存。缓存命中需要对比文件的 `mtime_ns` 和 `size`，避免外部进程修改文件后返回旧内容。

### `src/mycode/llm/base.py`

```python
from mycode.tool import ToolCall, ToolDefinition, ToolResult


class StreamEventType(str, Enum):
    TEXT_DELTA = "text_delta"
    THINKING_DELTA = "thinking_delta"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    DONE = "done"
    ERROR = "error"


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_arguments: str | None = None


@dataclass(frozen=True)
class StreamEvent:
    type: StreamEventType
    content: str = ""
    tool_call: ToolCall | None = None
    tool_result: ToolResult | None = None


class BaseLLM(ABC):
    @abstractmethod
    def stream_chat(
        self,
        messages: list[ChatMessage],
        tools: list[ToolDefinition] | None = None,
    ) -> AsyncIterable[StreamEvent]:
        raise NotImplementedError
```

`ChatMessage` 保持一个简单内部结构。普通 user/assistant 文本只使用 `role` 和 `content`；工具调用历史使用 `tool_call_id`、`tool_name`、`tool_arguments`；工具结果历史使用 `role="tool"`、`content` 和 `tool_call_id`。

## 模块设计

### `src/mycode/tool/base.py`

职责：定义协议无关的工具基础类型和 `Tool` 协议。

对外接口：`ToolDefinition`、`ToolCall`、`ToolResult`、`Tool`。

依赖：Python 标准库 typing/dataclasses。

### `src/mycode/tool/pathing.py`

职责：集中处理工作目录路径解析和越界保护。所有文件类工具必须通过该模块把模型输入的路径解析到 workspace root 内。

对外接口：

```python
class PathGuard:
    def __init__(self, workspace_root: Path) -> None: ...
    def resolve(self, path: str) -> Path: ...
```

越界时抛出工具层异常，由工具实现或执行器包装成结构化错误。

### `src/mycode/tool/cache.py`

职责：提供读文件、写文件、改文件共享的 UTF-8 文本缓存。缓存对象由默认注册中心创建并注入文件工具，不放在模块级全局变量里。

对外接口：

```python
class FileTextCache:
    def read_text(self, path: Path) -> str: ...
    def write_text(self, path: Path, text: str) -> None: ...
    def edit_text(self, path: Path, old_text: str, new_text: str) -> tuple[int, str | None]: ...
    def invalidate(self, path: Path) -> None: ...
```

并发约束：缓存内部使用 `threading.RLock`。`read_text()` 读取缓存字典时加锁；`write_text()` 和 `edit_text()` 在同一路径的磁盘写入与缓存更新期间持有锁，确保并发工具执行时不会出现缓存内容和磁盘内容不一致。

### `src/mycode/tool/filesystem.py`

职责：实现五个文件和代码工具：

- `ReadFileTool`
- `WriteFileTool`
- `EditFileTool`
- `FindFilesTool`
- `SearchCodeTool`

所有工具共享 `PathGuard`。`ReadFileTool`、`WriteFileTool` 和 `EditFileTool` 还共享同一个 `FileTextCache`，统一使用 UTF-8 文本。`EditFileTool` 使用缓存层的原文唯一匹配替换，零匹配或多匹配都返回 `ok=False`，且失败时不更新磁盘和缓存。

### `src/mycode/tool/command.py`

职责：实现 `RunCommandTool`。命令在 workspace root 下运行，返回退出码、stdout、stderr、是否超时。

对外接口：`RunCommandTool(workspace_root: Path, default_timeout_seconds: float = 10.0)`。

### `src/mycode/tool/registry.py`

职责：集中登记工具、按名称查找工具、输出内部定义和 OpenAI function tool spec。

约束：重复注册同名工具时抛出错误，避免模型调用时出现歧义。

### `src/mycode/tool/executor.py`

职责：接收内部 `ToolCall` 并执行。它查找工具、检查参数是否为对象、执行工具、捕获异常和超时，最终总是返回 `ToolResult`。

### `src/mycode/tool/defaults.py`

职责：提供主流程默认工具集合。

对外接口：

```python
def create_default_tool_registry(workspace_root: Path) -> ToolRegistry:
    ...
```

该函数创建一个 `PathGuard` 和一个 `FileTextCache`，并把同一个缓存实例传给读文件、写文件和改文件工具。

### `src/mycode/protocols/openai_responses.py`

职责：请求时注入工具定义；设置非并行工具调用约束；解析 Responses API 的流式 function call item 和 arguments delta/done；产出内部 `TOOL_CALL` 事件。

历史转换：

- 普通 user/assistant 消息转换为现有 Responses input item。
- assistant 工具调用历史转换为 `type="function_call"`，包含 `call_id`、`name`、`arguments`。
- 工具结果历史转换为 `type="function_call_output"`，包含 `call_id` 和 `output`。

### `src/mycode/protocols/openai_chat.py`

职责：请求时注入工具定义；设置非并行工具调用约束；解析 Chat Completions 的 `delta.tool_calls[]`；拼接 `function.arguments` 碎片；产出内部 `TOOL_CALL` 事件。

历史转换：

- 普通 user/assistant 消息保持 Chat Completions 消息格式。
- assistant 工具调用历史转换为 `role="assistant"` 且带 `tool_calls`。
- 工具结果历史转换为 `role="tool"` 且带 `tool_call_id`。

### `src/mycode/session.py`

职责：在现有多轮对话协调基础上加入一次工具执行。`ChatSession` 增加可选 `ToolExecutor`。没有工具执行器时，工具调用事件会转换为错误事件；有执行器时，执行一次并写回 memory。

行为：

- 无工具调用：沿用 Stage 01 文本流和 assistant 历史写入。
- 有工具调用：写入 assistant 工具调用历史，执行工具，yield `TOOL_RESULT`，写入工具结果历史，本轮结束。
- 工具结果写入后不自动再次请求 LLM。

### `src/mycode/tui.py`

职责：渲染工具执行状态。成功显示简短工具名，失败显示工具名和 error。

### `src/mycode/cli.py`

职责：创建默认工具注册中心和执行器，把执行器传入 `ChatSession`。

## 模块交互

### 无工具调用

1. TUI 读取用户输入。
2. `ChatSession` 写入 user 消息。
3. `ChatSession` 读取 memory 中的完整消息。
4. LLM 协议客户端发起流式请求。
5. 协议客户端产出 `TEXT_DELTA` 和 `DONE`。
6. TUI 流式渲染文本。
7. `ChatSession` 把 assistant 正文写回 memory。

### 有工具调用

1. TUI 读取用户输入。
2. `ChatSession` 写入 user 消息。
3. `ChatSession` 读取 memory 中的完整消息。
4. `ChatSession` 把 `tool_registry.definitions()` 传给 `BaseLLM.stream_chat()`。
5. OpenAI 协议客户端把工具定义转换成 OpenAI function tool spec 并放入请求，同时限制非并行工具调用。
6. OpenAI 返回流式 function call。
7. 协议客户端拼接 JSON 参数碎片，产出内部 `TOOL_CALL` 事件。
8. `ChatSession` 收到 `TOOL_CALL` 后写入 assistant 工具调用历史。
9. `ChatSession` 调用 `ToolExecutor.execute(call)`。
10. `ToolExecutor` 返回 `ToolResult`。
11. `ChatSession` yield `TOOL_RESULT` 给 TUI。
12. `ChatSession` 把工具结果写入 memory。
13. 本轮结束，不再次请求 LLM。

### 失败路径

- JSON 参数非法：协议层保留 raw arguments，`ToolCall.arguments=None`，执行器返回 `ok=False`。
- 工具不存在：执行器返回 `ok=False`，error 为 unknown tool。
- 路径越界：文件类工具返回 `ok=False`，error 说明路径不在工作目录内。
- 改文件零匹配或多匹配：`EditFileTool` 返回 `ok=False`，content 中包含 match count，缓存和磁盘都保持原内容。
- 命令超时：`RunCommandTool` 返回 `ok=False` 或 `ok=True` 但 `timed_out=True`，error 说明 timeout；测试中固定预期为失败结果。
- 工具异常：执行器捕获异常，返回 `ok=False`，不让 TUI 主循环崩溃。

## 文件组织

```text
src/mycode/
├── tool/
│   ├── __init__.py
│   ├── base.py
│   ├── pathing.py
│   ├── cache.py
│   ├── filesystem.py
│   ├── command.py
│   ├── registry.py
│   ├── executor.py
│   └── defaults.py
├── llm/
│   └── base.py
├── protocols/
│   ├── openai_responses.py
│   └── openai_chat.py
├── session.py
├── cli.py
└── tui.py
```

测试文件：

```text
tests/
├── test_tool_registry.py
├── test_tool_cache.py
├── test_tool_filesystem.py
├── test_tool_command.py
├── test_tool_executor.py
├── test_openai_responses_protocol.py
├── test_openai_chat_protocol.py
├── test_session.py
├── test_tui.py
└── test_e2e_chat.py
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 工具类位置 | 全部放 `src/mycode/tool` | 满足包边界要求，后续 Anthropic 可复用 |
| 协议接入范围 | 只接 OpenAI Responses 和 OpenAI Chat | 符合本阶段范围，Anthropic 留扩展口 |
| `ToolCall` 归属 | 放在 `tool/base.py` | 工具相关数据结构不散落到 LLM 包 |
| LLM 工具入口 | `stream_chat(messages, tools=None)` | 保持工具定义可选，纯对话路径不受影响 |
| 工具执行模型 | 工具同步接口，执行器异步包装 | 文件工具简单，执行器统一超时和异常包装 |
| 文件文本缓存 | `FileTextCache` 实例注入文件工具并用锁保护 | 避免重复读取，也避免并发工具执行时共享变量串扰 |
| 命令执行 | `asyncio.create_subprocess_shell()` | 易于捕获 stdout/stderr 和超时 |
| 路径权限 | 路径解析后必须位于 workspace root 内 | 简单可测，限制风险 |
| 改文件策略 | 原文唯一匹配替换 | 失败原因清楚，模型可重试 |
| 搜索实现 | Python 标准库优先 | 不依赖本机是否安装外部命令 |
| OpenAI 工具定义 | 注册中心输出 `type=function` 结构 | 两个 OpenAI 协议可共享 |
| Responses 历史 | `function_call` + `function_call_output` | 按 call id 配对工具调用和结果 |
| Chat 历史 | assistant `tool_calls` + `role=tool` | 按 tool call id 配对工具调用和结果 |
| 单轮停止 | 工具结果写回后结束本轮 | 严格不做 Agent Loop |

## 测试设计

工具层：

- `test_tool_registry.py` 验证注册、重复名称报错、按名查找、OpenAI tool spec 输出结构。
- `test_tool_cache.py` 验证读缓存命中、写入后缓存更新、外部修改后缓存失效、同一路径并发读写不会让缓存和磁盘内容不一致。
- `test_tool_filesystem.py` 验证读文件、写文件、改文件唯一匹配、零匹配、多匹配、路径越界、文件查找、代码搜索。
- `test_tool_command.py` 验证命令成功、命令失败、stdout/stderr 捕获、超时结果。
- `test_tool_executor.py` 验证未知工具、参数非法、工具异常、工具超时都返回结构化 `ToolResult`。

协议层：

- `test_openai_responses_protocol.py` 保留纯文本流式测试；新增工具定义进入请求 payload；新增 `response.function_call_arguments.delta` 多碎片拼接测试；新增工具调用事件测试。
- `test_openai_chat_protocol.py` 保留纯文本流式测试；新增工具定义进入请求 payload；新增 `delta.tool_calls[].function.arguments` 多碎片拼接测试；新增工具调用事件测试。

会话与 TUI：

- `test_session.py` 验证无工具调用时旧行为不变；有工具调用时执行一次工具、写回工具历史、产出 `TOOL_RESULT`，并且不继续二次请求 LLM。
- `test_tui.py` 验证工具成功和失败状态能被渲染为简短可读输出。
- `test_e2e_chat.py` 用 mocked LLM 或 mocked HTTP stream 模拟完整流程：用户提问、模型发起工具调用、myCode 执行工具、结果进入 memory、下一轮请求携带工具结果历史。

验收命令：

```powershell
python -m pytest
```

预期：全部测试通过，不需要真实 API key，不访问真实网络。

## Spec 覆盖自查

- F1/F2/F3：由 `tool/base.py`、`tool/filesystem.py`、`tool/command.py` 覆盖。
- F4：由 `tool/registry.py` 覆盖。
- F5/F12：由 `tool/executor.py` 和各工具失败结果覆盖。
- F6：由 `EditFileTool` 覆盖。
- F7：由 `tool/cache.py` 以及读文件、写文件、改文件工具覆盖。
- F8：由 `openai_responses.py` 覆盖。
- F9：由 `openai_chat.py` 覆盖。
- F10/F11：由 `session.py` 覆盖。
- F13：由 `tui.py` 覆盖。

本设计没有引入 Agent Loop、多工具连环调用、Anthropic 工具接入、持久化索引或复杂权限系统。
