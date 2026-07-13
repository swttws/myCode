# myCode 阶段 02：工具系统任务拆解

## 阶段标识

- 阶段编号：Stage 02
- 阶段名称：工具系统
- 阶段目标：按可测试的小步任务完成 OpenAI 系列工具调用接入，并预留 Anthropic 扩展口。

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `src/mycode/tool/__init__.py` | 导出工具系统公开类型和默认注册入口 |
| 新建 | `src/mycode/tool/base.py` | 定义 `ToolDefinition`、`ToolCall`、`ToolResult`、`Tool` |
| 新建 | `src/mycode/tool/pathing.py` | 工作目录路径解析与越界保护 |
| 新建 | `src/mycode/tool/cache.py` | 带锁 UTF-8 文本缓存，供读文件、写文件和改文件共享 |
| 新建 | `src/mycode/tool/filesystem.py` | 读文件、写文件、改文件、找文件、搜代码工具 |
| 新建 | `src/mycode/tool/command.py` | 执行命令工具 |
| 新建 | `src/mycode/tool/registry.py` | 工具注册中心和 OpenAI tool spec 转换 |
| 新建 | `src/mycode/tool/executor.py` | 工具查找、执行、超时和异常包装 |
| 新建 | `src/mycode/tool/defaults.py` | 创建默认六工具注册中心 |
| 修改 | `src/mycode/llm/base.py` | 增加工具调用/结果事件和工具历史字段 |
| 修改 | `src/mycode/protocols/openai_responses.py` | 注入工具定义、解析 Responses 流式工具调用、转换工具历史 |
| 修改 | `src/mycode/protocols/openai_chat.py` | 注入工具定义、解析 Chat 流式工具调用、转换工具历史 |
| 修改 | `src/mycode/session.py` | 执行一次工具调用并写回 memory |
| 修改 | `src/mycode/tui.py` | 渲染工具执行成功或失败状态 |
| 修改 | `src/mycode/cli.py` | 创建默认工具注册中心和工具执行器 |
| 修改 | `README.md` | 更新当前阶段能力和限制 |
| 新建 | `tests/test_tool_registry.py` | 注册中心和 OpenAI tool spec 测试 |
| 新建 | `tests/test_tool_cache.py` | 文件文本缓存命中、失效、写入更新和并发锁测试 |
| 新建 | `tests/test_tool_filesystem.py` | 文件、编辑、查找、搜索工具测试 |
| 新建 | `tests/test_tool_command.py` | 命令执行工具测试 |
| 新建 | `tests/test_tool_executor.py` | 工具执行器错误和超时测试 |
| 修改 | `tests/test_llm_base.py` | LLM 抽象工具事件和消息字段测试 |
| 修改 | `tests/test_openai_responses_protocol.py` | Responses 工具请求、历史转换、流式解析测试 |
| 修改 | `tests/test_openai_chat_protocol.py` | Chat 工具请求、历史转换、流式解析测试 |
| 修改 | `tests/test_session.py` | 工具调用会话集成测试 |
| 修改 | `tests/test_tui.py` | 工具状态渲染测试 |
| 修改 | `tests/test_e2e_chat.py` | mocked 端到端工具调用流程测试 |

## T1: 建立工具基础类型和注册中心

**文件：** `src/mycode/tool/__init__.py`、`src/mycode/tool/base.py`、`src/mycode/tool/registry.py`、`tests/test_tool_registry.py`

**依赖：** 无

**步骤：**

1. 在 `tests/test_tool_registry.py` 写失败测试：定义一个 `FakeTool`，验证 `ToolRegistry([tool]).get("fake")` 返回该工具。
2. 增加失败测试：验证重复注册同名工具抛出 `ValueError`。
3. 增加失败测试：验证 `definitions()` 返回 `ToolDefinition` 列表。
4. 增加失败测试：验证 `openai_tool_specs()` 返回 `{"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}`。
5. 运行 `python -m pytest tests/test_tool_registry.py -q`，预期因为 `mycode.tool` 不存在而失败。
6. 新建 `base.py`，定义 `JSONSchema`、`ToolArguments`、`ToolDefinition`、`ToolCall`、`ToolResult` 和 `Tool` 协议。
7. 新建 `registry.py`，实现注册、重复名称检查、按名查找、定义列表和 OpenAI tool spec 转换。
8. 新建 `__init__.py`，导出基础类型和 `ToolRegistry`。
9. 运行 `python -m pytest tests/test_tool_registry.py -q`，预期通过。

**验证：** `python -m pytest tests/test_tool_registry.py -q` 通过。

## T2: 实现工作目录路径保护

**文件：** `src/mycode/tool/pathing.py`、`tests/test_tool_filesystem.py`

**依赖：** T1

**步骤：**

1. 在 `tests/test_tool_filesystem.py` 写失败测试：`PathGuard(tmp_path).resolve("a/b.txt")` 返回 `tmp_path / "a" / "b.txt"` 的绝对路径。
2. 增加失败测试：`PathGuard(tmp_path).resolve("../outside.txt")` 抛出工具层路径错误。
3. 增加失败测试：传入绝对路径且该路径不在 workspace root 内时抛出路径错误。
4. 运行 `python -m pytest tests/test_tool_filesystem.py -q`，预期因为 `PathGuard` 不存在而失败。
5. 新建 `pathing.py`，定义 `ToolPathError` 和 `PathGuard`。
6. `PathGuard.resolve()` 使用 `Path.resolve()` 规范化路径，并用 `relative_to()` 或等价逻辑确认结果位于 workspace root 内。
7. 运行 `python -m pytest tests/test_tool_filesystem.py -q`，预期通过当前路径保护测试。

**验证：** `python -m pytest tests/test_tool_filesystem.py -q` 通过当前已有测试。

## T3A: 实现带锁文本文件缓存

**文件：** `src/mycode/tool/cache.py`、`tests/test_tool_cache.py`

**依赖：** T2

**步骤：**

1. 在 `tests/test_tool_cache.py` 写失败测试：`FileTextCache.read_text(path)` 第一次读取磁盘 UTF-8 文本，第二次在文件未变化时返回缓存文本。
2. 增加失败测试：`FileTextCache.write_text(path, text)` 写入磁盘后同步更新缓存，后续 `read_text(path)` 返回新文本。
3. 增加失败测试：文件被外部写入且 `mtime_ns` 或 `size` 变化后，`read_text(path)` 重新读取磁盘，不返回旧缓存。
4. 增加失败测试：`edit_text(path, old_text, new_text)` 在唯一匹配时返回 `(1, new_text)`，写入磁盘并更新缓存。
5. 增加失败测试：`edit_text()` 在零匹配或多匹配时返回匹配次数和 `None`，磁盘和缓存保持原内容。
6. 增加失败测试：使用 `asyncio.to_thread()` 或 `concurrent.futures.ThreadPoolExecutor` 并发调用同一路径的 `write_text()` 和 `read_text()`，最终缓存内容和磁盘内容一致。
7. 运行 `python -m pytest tests/test_tool_cache.py -q`，预期因为 `FileTextCache` 不存在而失败。
8. 新建 `cache.py`，定义 `CachedText` 和 `FileTextCache`。
9. `FileTextCache` 内部使用实例字段保存缓存字典和锁，不使用模块级可变全局变量。
10. 使用 `threading.RLock` 保护缓存字典、路径锁表、同一路径的磁盘写入和缓存更新。
11. `read_text()` 使用 `path.stat().st_mtime_ns` 和 `st_size` 判断缓存是否仍有效。
12. `write_text()` 创建必要父目录，写入 UTF-8 文本，并在同一锁保护下更新缓存。
13. `edit_text()` 在同一锁保护下读取、统计、替换、写入并更新缓存；失败时不修改磁盘和缓存。
14. 运行 `python -m pytest tests/test_tool_cache.py -q`，预期通过。

**验证：** `python -m pytest tests/test_tool_cache.py -q` 通过。

## T3: 实现读文件和写文件工具

**文件：** `src/mycode/tool/filesystem.py`、`tests/test_tool_filesystem.py`

**依赖：** T2、T3A

**步骤：**

1. 在 `tests/test_tool_filesystem.py` 增加失败测试：`ReadFileTool` 能读取 workspace 内 UTF-8 文件，结果 `ok=True`，content 含 `path` 和 `text`。
2. 增加失败测试：`ReadFileTool` 读取 workspace 外路径返回 `ok=False`，error 说明路径越界。
3. 增加失败测试：`WriteFileTool` 能写入 workspace 内新文件，并自动创建父目录。
4. 增加失败测试：`WriteFileTool` 写 workspace 外路径返回 `ok=False`。
5. 增加失败测试：两个工具的 `definition` 都包含名称、描述和 JSON Schema，参数对象列出必需字段。
6. 运行 `python -m pytest tests/test_tool_filesystem.py -q`，预期因为工具不存在而失败。
7. 新建 `filesystem.py`，实现 `ReadFileTool` 和 `WriteFileTool`。
8. 两个工具使用 `PathGuard` 解析路径，并通过注入的 `FileTextCache` 读取或写入 UTF-8 文本。
9. 捕获 `ToolPathError` 和文件缓存层异常并返回结构化失败结果。
10. 运行 `python -m pytest tests/test_tool_filesystem.py -q`，预期通过当前文件读写测试。

**验证：** `python -m pytest tests/test_tool_filesystem.py -q` 通过。

## T4: 实现原文唯一匹配改文件工具

**文件：** `src/mycode/tool/filesystem.py`、`tests/test_tool_filesystem.py`

**依赖：** T3

**步骤：**

1. 在 `tests/test_tool_filesystem.py` 增加失败测试：`EditFileTool` 在 `old_text` 出现一次时替换为 `new_text`，返回 `ok=True`。
2. 增加失败测试：`old_text` 出现零次时返回 `ok=False`，content 含 `match_count: 0`。
3. 增加失败测试：`old_text` 出现多次时返回 `ok=False`，content 含实际 `match_count`，文件内容保持不变。
4. 增加失败测试：路径越界时返回结构化失败结果。
5. 增加失败测试：`definition.parameters` 要求 `path`、`old_text`、`new_text`。
6. 运行 `python -m pytest tests/test_tool_filesystem.py -q`，预期因为 `EditFileTool` 不存在而失败。
7. 在 `filesystem.py` 实现 `EditFileTool`。
8. 通过注入的 `FileTextCache.edit_text()` 完成读取、匹配计数、替换、写回和缓存更新；只有次数为 1 时写回替换后的文本。
9. 运行 `python -m pytest tests/test_tool_filesystem.py -q`，预期通过。

**验证：** `python -m pytest tests/test_tool_filesystem.py -q` 通过。

## T5: 实现按模式找文件和搜代码内容工具

**文件：** `src/mycode/tool/filesystem.py`、`tests/test_tool_filesystem.py`

**依赖：** T4

**步骤：**

1. 在 `tests/test_tool_filesystem.py` 增加失败测试：`FindFilesTool` 用模式 `*.py` 返回 workspace 内匹配文件的相对路径列表。
2. 增加失败测试：`FindFilesTool` 支持可选 `root`，且 root 越界时返回 `ok=False`。
3. 增加失败测试：`SearchCodeTool` 用 query 搜索 UTF-8 文本文件，返回包含 `path`、`line_number`、`line` 的结果。
4. 增加失败测试：`SearchCodeTool` 跳过无法按 UTF-8 解码的文件，不让搜索崩溃。
5. 增加失败测试：两个工具的参数 Schema 分别要求 `pattern` 和 `query`。
6. 运行 `python -m pytest tests/test_tool_filesystem.py -q`，预期因为工具不存在而失败。
7. 在 `filesystem.py` 实现 `FindFilesTool`，使用 `Path.rglob()` 和 `fnmatch` 或等价标准库逻辑匹配文件。
8. 在 `filesystem.py` 实现 `SearchCodeTool`，逐行读取文本，返回匹配行。
9. 运行 `python -m pytest tests/test_tool_filesystem.py -q`，预期通过。

**验证：** `python -m pytest tests/test_tool_filesystem.py -q` 通过。

## T6: 实现执行命令工具

**文件：** `src/mycode/tool/command.py`、`tests/test_tool_command.py`

**依赖：** T1

**步骤：**

1. 在 `tests/test_tool_command.py` 写失败测试：`RunCommandTool` 执行 `python -c "print('ok')"` 返回 `ok=True`、`exit_code=0`、stdout 含 `ok`。
2. 增加失败测试：执行 `python -c "import sys; print('bad', file=sys.stderr); sys.exit(3)"` 返回 `ok=False`、`exit_code=3`、stderr 含 `bad`。
3. 增加失败测试：执行超时命令返回 `ok=False`、`timed_out=True`，error 说明 timeout。
4. 增加失败测试：命令在 workspace root 下运行。
5. 增加失败测试：参数 Schema 要求 `command`，并允许可选 `timeout_seconds`。
6. 运行 `python -m pytest tests/test_tool_command.py -q`，预期因为 `RunCommandTool` 不存在而失败。
7. 新建 `command.py`，实现 `RunCommandTool`。
8. 使用 `asyncio.create_subprocess_shell()` 执行命令；工具的同步 `execute()` 可用 `asyncio.run()` 包装内部异步 helper，或先实现同步 subprocess 并在执行器中统一线程化。
9. 超时时终止进程并返回结构化失败结果。
10. 运行 `python -m pytest tests/test_tool_command.py -q`，预期通过。

**验证：** `python -m pytest tests/test_tool_command.py -q` 通过。

## T7: 实现工具执行器

**文件：** `src/mycode/tool/executor.py`、`tests/test_tool_executor.py`

**依赖：** T1、T6

**步骤：**

1. 在 `tests/test_tool_executor.py` 写失败测试：执行已注册 fake tool 时返回该工具的 `ToolResult`。
2. 增加失败测试：未知工具返回 `ok=False`，error 含 unknown tool。
3. 增加失败测试：`ToolCall.arguments is None` 时返回 `ok=False`，content 含 `raw_arguments`。
4. 增加失败测试：工具 `execute()` 抛异常时返回 `ok=False`，不向外抛出。
5. 增加失败测试：工具执行超过 `timeout_seconds` 时返回 `ok=False`，content 含 `timed_out=True`。
6. 增加失败测试：`ToolExecutor.definitions()` 返回注册中心的工具定义，供 session 传给 LLM。
7. 运行 `python -m pytest tests/test_tool_executor.py -q`，预期因为 `ToolExecutor` 不存在而失败。
8. 新建 `executor.py`，实现 `ToolExecutor`。
9. 使用 `asyncio.wait_for()` 和 `asyncio.to_thread()` 包装同步工具执行。
10. 所有失败路径都返回 `ToolResult`，不让异常穿透到 session。
11. 运行 `python -m pytest tests/test_tool_executor.py -q`，预期通过。

**验证：** `python -m pytest tests/test_tool_executor.py -q` 通过。

## T8: 注册默认六个核心工具并导出 tool 包入口

**文件：** `src/mycode/tool/__init__.py`、`src/mycode/tool/defaults.py`、`tests/test_tool_registry.py`

**依赖：** T3A、T3、T4、T5、T6、T7

**步骤：**

1. 在 `tests/test_tool_registry.py` 增加失败测试：`create_default_tool_registry(tmp_path)` 注册六个工具。
2. 增加失败测试：默认工具名称包含 `read_file`、`write_file`、`edit_file`、`run_command`、`find_files`、`search_code`。
3. 增加失败测试：可以从 `mycode.tool` 直接导入 `ToolCall`、`ToolResult`、`ToolRegistry`、`ToolExecutor`、`create_default_tool_registry`。
4. 运行 `python -m pytest tests/test_tool_registry.py -q`，预期因为默认注册入口不存在而失败。
5. 新建 `defaults.py`，实现 `create_default_tool_registry(workspace_root)`。
6. 在默认注册函数中创建一个 `PathGuard` 和一个 `FileTextCache`，并把同一个缓存实例注入 `ReadFileTool`、`WriteFileTool` 和 `EditFileTool`。
7. 更新 `__init__.py` 导出公开类型、工具类、注册中心、执行器、`FileTextCache` 和默认注册入口。
8. 运行 `python -m pytest tests/test_tool_registry.py -q`，预期通过。

**验证：** `python -m pytest tests/test_tool_registry.py -q` 通过。

## T9: 扩展 LLM 抽象以承载工具事件和工具历史

**文件：** `src/mycode/llm/base.py`、`tests/test_llm_base.py`、`tests/test_session.py`、`tests/test_e2e_chat.py`

**依赖：** T1

**步骤：**

1. 在 `tests/test_llm_base.py` 增加失败测试：`StreamEvent` 能承载 `ToolCall` 和 `ToolResult`。
2. 增加失败测试：`ChatMessage` 默认工具字段为 `None`，已有 `ChatMessage(role, content)` 调用保持可用。
3. 增加失败测试：测试用 `BaseLLM` 子类实现 `stream_chat(self, messages, tools=None)` 后可实例化。
4. 运行 `python -m pytest tests/test_llm_base.py -q`，预期因为新事件或签名不存在而失败。
5. 修改 `llm/base.py`：新增 `TOOL_CALL`、`TOOL_RESULT`，扩展 `StreamEvent` 和 `ChatMessage`，并把 `BaseLLM.stream_chat()` 签名改为接收可选 `tools`。
6. 更新测试内 `ScriptedLLM` 等 fake LLM 签名，让它们接受 `tools=None` 并记录传入工具定义。
7. 运行 `python -m pytest tests/test_llm_base.py tests/test_session.py tests/test_e2e_chat.py -q`，预期通过。

**验证：** `python -m pytest tests/test_llm_base.py tests/test_session.py tests/test_e2e_chat.py -q` 通过。

## T10: OpenAI Responses 请求注入工具定义并转换工具历史

**文件：** `src/mycode/protocols/openai_responses.py`、`tests/test_openai_responses_protocol.py`

**依赖：** T8、T9

**步骤：**

1. 在 `tests/test_openai_responses_protocol.py` 增加失败测试：调用 `stream_chat(messages, tools=[ToolDefinition(...)])` 时，请求 payload 包含 `tools` 和 `parallel_tool_calls: False`。
2. 增加失败测试：普通纯文本请求在 `tools=None` 时 payload 与 Stage 01 兼容，不出现空工具列表。
3. 增加失败测试：assistant 工具调用历史转换为 `type="function_call"`，包含 `call_id`、`name`、`arguments`。
4. 增加失败测试：工具结果历史转换为 `type="function_call_output"`，包含 `call_id` 和 `output`。
5. 运行 `python -m pytest tests/test_openai_responses_protocol.py -q`，预期因为 payload 未支持工具而失败。
6. 修改 `openai_responses.py` 的 `stream_chat()` 签名接收 `tools=None`。
7. 增加内部 helper，把 `ToolDefinition` 转为 OpenAI function tool spec。
8. 增加内部 helper，把 `ChatMessage` 转换为 Responses input item，覆盖普通文本、assistant 工具调用、工具结果三类。
9. 有工具定义时设置 `payload["tools"]` 和 `payload["parallel_tool_calls"] = False`。
10. 运行 `python -m pytest tests/test_openai_responses_protocol.py -q`，预期通过当前新增测试和旧测试。

**验证：** `python -m pytest tests/test_openai_responses_protocol.py -q` 通过。

## T11: OpenAI Responses 流式解析工具调用参数碎片

**文件：** `src/mycode/protocols/openai_responses.py`、`tests/test_openai_responses_protocol.py`

**依赖：** T10

**步骤：**

1. 在 `tests/test_openai_responses_protocol.py` 增加失败测试：模拟 `response.output_item.added` 的 function call item，记录 `call_id` 和 `name`。
2. 增加失败测试：模拟多个 `response.function_call_arguments.delta`，把 `{"path":"README.md"}` 拆成至少两段。
3. 增加失败测试：在 `response.function_call_arguments.done` 或 `response.completed` 后产出 `StreamEventType.TOOL_CALL`，其中 `ToolCall.arguments == {"path": "README.md"}`。
4. 增加失败测试：arguments 拼接后不是合法 JSON 时产出 `ToolCall(arguments=None, raw_arguments=...)`。
5. 运行 `python -m pytest tests/test_openai_responses_protocol.py -q`，预期因为工具调用事件未解析而失败。
6. 在 `openai_responses.py` 增加流式工具调用累积状态，按 item id 或 output index 记录 call id、name 和 arguments fragments。
7. 在 arguments done 或响应完成时拼接 fragments，解析 JSON 对象，产出 `StreamEvent(StreamEventType.TOOL_CALL, tool_call=...)`。
8. 非工具事件继续保持 Stage 01 文本 delta 和 done 行为。
9. 运行 `python -m pytest tests/test_openai_responses_protocol.py -q`，预期通过。

**验证：** `python -m pytest tests/test_openai_responses_protocol.py -q` 通过。

## T12: OpenAI Chat 请求注入工具定义并转换工具历史

**文件：** `src/mycode/protocols/openai_chat.py`、`tests/test_openai_chat_protocol.py`

**依赖：** T8、T9

**步骤：**

1. 在 `tests/test_openai_chat_protocol.py` 增加失败测试：调用 `stream_chat(messages, tools=[ToolDefinition(...)])` 时，请求 payload 包含 `tools` 和 `parallel_tool_calls: False`。
2. 增加失败测试：普通纯文本请求在 `tools=None` 时 payload 与 Stage 01 兼容，不出现空工具列表。
3. 增加失败测试：assistant 工具调用历史转换为 `role="assistant"` 且包含 `tool_calls`。
4. 增加失败测试：工具结果历史转换为 `role="tool"` 且包含 `tool_call_id`。
5. 运行 `python -m pytest tests/test_openai_chat_protocol.py -q`，预期因为 payload 未支持工具而失败。
6. 修改 `openai_chat.py` 的 `stream_chat()` 签名接收 `tools=None`。
7. 增加内部 helper，把 `ToolDefinition` 转为 OpenAI function tool spec。
8. 增加内部 helper，把 `ChatMessage` 转换为 Chat Completions message，覆盖普通文本、assistant 工具调用、工具结果三类。
9. 有工具定义时设置 `payload["tools"]` 和 `payload["parallel_tool_calls"] = False`。
10. 运行 `python -m pytest tests/test_openai_chat_protocol.py -q`，预期通过当前新增测试和旧测试。

**验证：** `python -m pytest tests/test_openai_chat_protocol.py -q` 通过。

## T13: OpenAI Chat 流式解析 `tool_calls` 参数碎片

**文件：** `src/mycode/protocols/openai_chat.py`、`tests/test_openai_chat_protocol.py`

**依赖：** T12

**步骤：**

1. 在 `tests/test_openai_chat_protocol.py` 增加失败测试：模拟 `choices[0].delta.tool_calls[0]` 带 `id`、`function.name` 和第一段 `function.arguments`。
2. 增加失败测试：模拟后续 chunk 只带同一 `index` 的 `function.arguments` 追加片段。
3. 增加失败测试：收到 `[DONE]` 时产出 `StreamEventType.TOOL_CALL`，其中 `ToolCall.arguments == {"path": "README.md"}`。
4. 增加失败测试：arguments 拼接后不是合法 JSON 时产出 `ToolCall(arguments=None, raw_arguments=...)`。
5. 运行 `python -m pytest tests/test_openai_chat_protocol.py -q`，预期因为工具调用事件未解析而失败。
6. 在 `openai_chat.py` 增加按 `tool_calls[].index` 累积 id、name 和 arguments fragments 的逻辑。
7. 在 `[DONE]` 到达前先产出累积出的 `TOOL_CALL` 事件，再产出 `DONE`。
8. 文本 delta 和 existing streaming-first 行为保持不变。
9. 运行 `python -m pytest tests/test_openai_chat_protocol.py -q`，预期通过。

**验证：** `python -m pytest tests/test_openai_chat_protocol.py -q` 通过。

## T14: 会话层执行一次工具调用并写回 memory

**文件：** `src/mycode/session.py`、`tests/test_session.py`

**依赖：** T7、T8、T9、T11、T13

**步骤：**

1. 在 `tests/test_session.py` 增加失败测试：无工具调用时旧行为不变，assistant 正文仍写回 memory。
2. 增加失败测试：`ChatSession` 初始化带 `ToolExecutor` 时，会把工具定义传给 LLM 的 `stream_chat(messages, tools=...)`。
3. 增加失败测试：LLM 产出 `TOOL_CALL` 后，session 执行工具并 yield `TOOL_RESULT`。
4. 增加失败测试：工具调用历史和工具结果历史都写入 memory。
5. 增加失败测试：工具执行后本轮结束，不对 LLM 发起第二次请求。
6. 增加失败测试：没有 `ToolExecutor` 却收到工具调用时，yield `ERROR` 或失败工具结果，且不崩溃。
7. 运行 `python -m pytest tests/test_session.py -q`，预期因为 session 未处理工具事件而失败。
8. 修改 `ChatSession.__init__()`，增加可选 `tool_executor`。
9. 在 `send()` 中调用 LLM 时传入 `tool_executor.definitions()`；没有执行器时传 `None`。
10. 收到 `TOOL_CALL` 时写入 assistant 工具调用历史，执行工具，yield `TOOL_RESULT`，写入 `role="tool"` 工具结果历史，然后 return。
11. 文本 delta 路径保持原有 assistant_parts 聚合和成功后写回行为。
12. 运行 `python -m pytest tests/test_session.py -q`，预期通过。

**验证：** `python -m pytest tests/test_session.py -q` 通过。

## T15: TUI 渲染工具执行状态

**文件：** `src/mycode/tui.py`、`tests/test_tui.py`

**依赖：** T9、T14

**步骤：**

1. 在 `tests/test_tui.py` 增加失败测试：收到成功 `TOOL_RESULT` 时输出包含工具名和“已执行”含义的文本。
2. 增加失败测试：收到失败 `TOOL_RESULT` 时输出包含工具名和 error。
3. 运行 `python -m pytest tests/test_tui.py -q`，预期因为 TUI 不处理工具结果事件而失败。
4. 修改 `_render_stream()`，为 `StreamEventType.TOOL_RESULT` 增加分支。
5. 成功时输出简短状态，失败时输出红色错误状态。
6. 运行 `python -m pytest tests/test_tui.py -q`，预期通过。

**验证：** `python -m pytest tests/test_tui.py -q` 通过。

## T16: CLI 主流程接入默认工具系统

**文件：** `src/mycode/cli.py`、`tests/test_e2e_chat.py`

**依赖：** T8、T14、T15

**步骤：**

1. 在 `tests/test_e2e_chat.py` 增加失败测试：CLI 创建的 session 会把默认工具定义传给 LLM。
2. 增加失败测试：mocked LLM 发起工具调用后，工具结果进入 memory；下一轮请求能看到 `role="tool"` 历史。
3. 增加失败测试：`/clear` 会清掉普通历史和工具历史。
4. 运行 `python -m pytest tests/test_e2e_chat.py -q`，预期因为 CLI 未创建工具执行器而失败。
5. 修改 `cli.py`，在加载配置和创建 LLM 后，使用当前工作目录创建默认工具注册中心。
6. 创建 `ToolExecutor` 并传给 `ChatSession`。
7. 保持配置错误处理和 TUI 启动路径不变。
8. 运行 `python -m pytest tests/test_e2e_chat.py -q`，预期通过。

**验证：** `python -m pytest tests/test_e2e_chat.py -q` 通过。

## T17: 更新 README 中的阶段说明和工具限制

**文件：** `README.md`

**依赖：** T16

**步骤：**

1. 修改 README 的当前阶段说明，从纯对话 TUI 更新为已支持 OpenAI 系列单轮工具调用。
2. 增加六个核心工具能力说明。
3. 增加本阶段限制说明：不做 Agent Loop、不做多工具连环调用、Anthropic 工具调用暂未接入。
4. 保留配置格式说明，不新增配置开关。
5. 运行 `python -m pytest tests/test_docs.py -q`，预期通过。

**验证：** `python -m pytest tests/test_docs.py -q` 通过。

## T18: 全量回归与验收

**文件：** 全项目

**依赖：** T1 到 T17，包含 T3A

**步骤：**

1. 运行 `python -m pytest -q`。
2. 如果失败，按失败测试定位到对应任务文件修复，再重跑失败测试。
3. 全部单测通过后，运行 `python -m pytest` 查看完整输出。
4. 确认测试不依赖真实 API key，不访问真实外部网络。
5. 检查 `git status --short`，确认变更集中在 Stage 02 相关代码、测试和文档。

**验证：** `python -m pytest` 全部通过。

## 执行顺序

```text
T1 -> T2 -> T3A -> T3 -> T4 -> T5
T1 -> T6
T1 + T6 -> T7
T3A + T3 + T4 + T5 + T6 + T7 -> T8
T1 -> T9
T8 + T9 -> T10 -> T11
T8 + T9 -> T12 -> T13
T7 + T8 + T9 + T11 + T13 -> T14 -> T15 -> T16 -> T17 -> T18
```

## 自查

- `plan.md` 中的每个组件都有任务覆盖：tool 基础、路径、文件缓存、文件工具、命令工具、注册中心、执行器、默认注册、LLM 抽象、OpenAI Responses、OpenAI Chat、session、TUI、CLI、文档。
- 每个任务都有明确依赖和验证命令。
- 任务顺序先建立协议无关工具系统，再接 OpenAI 协议，最后接会话和 TUI。
- 工具相关类和工具实现都放在 `src/mycode/tool` 包下。
- 读文件、写文件和改文件共用 `FileTextCache`，缓存实例加锁且由默认注册中心注入。
- 没有任务引入自动 Agent Loop、多工具连环调用或 Anthropic 工具调用。
