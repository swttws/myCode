# myCode Stage 06: MCP 远端工具接入 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `src/mycode/mcp/__init__.py` | MCP 包公开导出 |
| 新建 | `src/mycode/mcp/models.py` | MCP 配置、状态、诊断和远端工具数据结构 |
| 新建 | `src/mycode/mcp/config.py` | 独立 MCP 配置发现、解析和校验 |
| 新建 | `src/mycode/mcp/jsonrpc.py` | JSON-RPC 消息和协议错误处理 |
| 新建 | `src/mycode/mcp/transport.py` | 传输协议 |
| 新建 | `src/mycode/mcp/stdio.py` | stdio transport |
| 新建 | `src/mycode/mcp/streamable_http.py` | Streamable HTTP transport |
| 新建 | `src/mycode/mcp/connection.py` | 单 server MCP 生命周期与双向 JSON-RPC |
| 新建 | `src/mycode/mcp/pool.py` | 多 server 缓存、隔离、重连和关闭 |
| 新建 | `src/mycode/mcp/tools.py` | 远端工具 wrapper 与 ToolSearch |
| 修改 | `src/mycode/tool/base.py` | 异步与延迟工具类型契约 |
| 修改 | `src/mycode/tool/registry.py` | 可见工具、延迟摘要和发现状态 |
| 修改 | `src/mycode/tool/executor.py` | 异步工具执行分流 |
| 修改 | `src/mycode/tool/__init__.py` | 新类型导出 |
| 修改 | `src/mycode/agent/loop.py` | 延迟工具提醒和可见工具列表注入 |
| 修改 | `src/mycode/cli.py` | MCP CLI 参数、初始化和关闭 |
| 新建 | `tests/mcp_helpers.py` | 内存 transport、受控 stdio/HTTP MCP server 测试辅助 |
| 新建 | `tests/test_mcp_config.py` | MCP 配置测试 |
| 新建 | `tests/test_mcp_jsonrpc.py` | JSON-RPC 与连接分派测试 |
| 新建 | `tests/test_mcp_stdio.py` | stdio transport 测试 |
| 新建 | `tests/test_mcp_streamable_http.py` | Streamable HTTP transport 测试 |
| 新建 | `tests/test_mcp_pool.py` | server 池测试 |
| 新建 | `tests/test_mcp_tools.py` | 远端工具与 ToolSearch 测试 |
| 修改 | `tests/test_tool_registry.py` | 注册表延迟可见性测试 |
| 修改 | `tests/test_tool_executor.py` | 异步工具执行测试 |
| 修改 | `tests/test_agent_loop.py` | 延迟工具提醒与下一轮注入测试 |
| 修改 | `tests/test_cli.py` | MCP CLI 生命周期测试 |
| 新建 | `examples/mycode.mcp.yaml` | MCP 配置示例 |
| 修改 | `README.md` | MCP 使用和边界文档 |

## T1: 建立 MCP 领域类型与包边界

**文件：** `src/mycode/mcp/__init__.py`、`src/mycode/mcp/models.py`
**依赖：** 无
**步骤：**
1. 定义 `MCPTransportKind`、`MCPServerState`、`MCPServerConfig`、`MCPConfig`、`MCPDiagnostic`、`RemoteTool` 和 `DeferredToolSummary`，字段与 `plan.md` 的定义一致。
2. 为配置映射与工具 schema 使用只读类型标注，确保后续模块不修改解析后的配置。
3. 在包入口只导出供 CLI、工具适配和测试使用的公共类型，不导入 transport 或连接实现，避免循环依赖。
4. 对空名称、非法 transport 和非正超时的构造条件提供明确错误，供配置加载器转为诊断。

**验证：** `python -m compileall src/mycode/mcp` 成功。

## T2: 实现独立 MCP 配置发现和校验

**文件：** `src/mycode/mcp/config.py`、`tests/test_mcp_config.py`
**依赖：** T1
**步骤：**
1. 编写配置发现测试，覆盖显式 `--mcp-config` 路径优先、工作目录 `mycode.mcp.yaml` 优先于用户目录 `~/.mycode/mcp.yaml`，以及未找到文件时返回禁用 MCP 的空配置。
2. 编写 YAML schema 测试，覆盖 stdio 的 `command`/`args`/`env`、Streamable HTTP 的 `url`/`headers`、必填 `name`/`transport`/`timeout_seconds`、重复 server 名称、非法名称、非正超时和逐 server 失败诊断。
3. 编写环境变量测试，验证 `${NAME}` 在 `env` 和 `headers` 中解析，缺失变量只报告变量名，异常、诊断和序列化输出不包含实际敏感值。
4. 实现配置文件定位、YAML 读取、字段校验和环境变量解析；显式不存在或无法解析的文件抛出 `MCPConfigError`，可解析文件中的单个无效 server 作为 `MCPDiagnostic` 被跳过。
5. 保持主 `mycode.yaml` 加载器不变，避免 MCP 配置与 LLM 配置耦合。

**验证：** `python -m pytest tests/test_mcp_config.py -q` 通过。

## T3: 实现 JSON-RPC 消息构建和校验

**文件：** `src/mycode/mcp/jsonrpc.py`、`tests/test_mcp_jsonrpc.py`
**依赖：** T1
**步骤：**
1. 编写 JSON-RPC 2.0 请求、通知、成功响应和错误响应的构建测试，断言固定 `jsonrpc: "2.0"`、id 保留和错误结构。
2. 编写入站消息校验测试，覆盖合法响应、合法通知、合法 server 请求、无效 JSON-RPC 版本、同时含 `result` 与 `error`、缺少 method、缺少 id 的错误响应和非对象消息。
3. 编写取消通知测试，断言超时请求的取消通知引用原始请求 id，且可选原因不暴露敏感配置。
4. 实现消息构建器、分类器和 `MCPProtocolError`；将协议错误转换为稳定类别，而不是把原始 payload 暴露给上层日志。

**验证：** `python -m pytest tests/test_mcp_jsonrpc.py -q` 中 JSON-RPC 构造与校验用例通过。

## T4: 实现 stdio transport 与子进程清理

**文件：** `src/mycode/mcp/transport.py`、`src/mycode/mcp/stdio.py`、`tests/mcp_helpers.py`、`tests/test_mcp_stdio.py`
**依赖：** T1
**步骤：**
1. 在测试辅助中提供一个受控 Python 子进程 server：从 stdin 读取一行 JSON、向 stdout 写一行 JSON，并可按测试指令延迟、关闭或持续占用。
2. 编写 transport 测试，覆盖命令与参数启动、每条 JSON-RPC 消息一行写入、stdout JSON 行作为入站消息、子进程环境变量覆盖和 stderr 被消费。
3. 编写关闭测试，覆盖关闭 stdin、等待退出、超时后终止、必要时强制终止，以及关闭后不遗留子进程。
4. 定义 `MCPTransport` 协议，实现在写入时等待 drain、在接收时验证每行 JSON 对象、在读取结束时通知连接已断开。
5. 实现 `StdioTransport` 的资源关闭路径，确保 Windows 环境可用，且日志只包含 server 名称和非敏感错误类别。

**验证：** `python -m pytest tests/test_mcp_stdio.py -q` 通过。

## T5: 实现 Streamable HTTP transport

**文件：** `src/mycode/mcp/streamable_http.py`、`tests/mcp_helpers.py`、`tests/test_mcp_streamable_http.py`
**依赖：** T3、T4
**步骤：**
1. 在测试辅助中提供本地 HTTP server，能够返回 `application/json` 与 `text/event-stream`，记录收到的请求头和 JSON body，并可模拟断连和慢响应。
2. 编写 JSON 响应测试，断言每个 POST 带 MCP 协议版本、协商后会话标识、配置请求头和 JSON-RPC body；响应消息进入统一接收流。
3. 编写 SSE 测试，断言多个 event data JSON 按顺序进入接收流，并忽略 SSE 注释和空行。
4. 编写可选 GET 事件流、会话标识更新、HTTP 错误、无效 Content-Type、断连和 `AsyncClient` 关闭测试。
5. 实现 `StreamableHTTPTransport`，复用现有 `httpx` 依赖，不引入 OAuth、已废弃 HTTP+SSE 或真实网络依赖。

**验证：** `python -m pytest tests/test_mcp_streamable_http.py -q` 通过。

## T6: 实现单 server 连接生命周期与并发响应匹配

**文件：** `src/mycode/mcp/connection.py`、`tests/mcp_helpers.py`、`tests/test_mcp_jsonrpc.py`
**依赖：** T3、T4
**步骤：**
1. 在测试辅助中实现内存 transport，允许测试按任意顺序推送入站 JSON-RPC 消息并检查发送消息。
2. 编写初始化测试，断言 `initialize` 成功后保存协议版本和能力，随后发送 `notifications/initialized`，再发送 `tools/list` 并转换为原始远端工具信息。
3. 编写并发请求测试，同时发出两个请求并以反序响应，断言各自等待到原请求 id 的成功或错误结果。
4. 实现接收循环、递增 id、pending Future 映射、`request()`、`notify()`、`initialize()` 和 `close()`；只允许初始化成功后调用工具。
5. 将远端 JSON-RPC error、无效工具清单、初始化版本不匹配和 transport 断连转换为连接层稳定异常。

**验证：** `python -m pytest tests/test_mcp_jsonrpc.py -q` 通过所有连接初始化和乱序响应测试。

## T7: 实现入站消息、超时和连接失败收敛

**文件：** `src/mycode/mcp/connection.py`、`tests/test_mcp_jsonrpc.py`
**依赖：** T6
**步骤：**
1. 编写通知测试，断言入站通知被记录且不完成或取消无关 pending 请求。
2. 编写 `ping` server 请求测试，断言以相同 id 返回 JSON-RPC 成功响应；为 `roots/list` 和任意未知 server 请求断言返回 `-32601`。
3. 编写超时测试，断言超时请求被移出 pending 映射、发送取消通知、返回超时失败，并忽略后续迟到响应。
4. 编写断连和无效入站消息测试，断言同一连接的全部 pending 请求都以协议或断连失败结束，资源关闭只执行一次。
5. 实现上述分派、取消和失败收敛逻辑，保证接收循环异常不会悬挂调用方。

**验证：** `python -m pytest tests/test_mcp_jsonrpc.py -q` 通过通知、server 请求、超时和断连用例。

## T8: 实现 server 池初始化、缓存与诊断

**文件：** `src/mycode/mcp/pool.py`、`tests/test_mcp_pool.py`
**依赖：** T1、T2、T6、T7
**步骤：**
1. 编写多 server 初始化测试，断言每个有效配置项各自建立连接、完成发现并保留 `RemoteTool`，且两个同名远端工具有不同公开名称。
2. 编写部分失败测试，断言一个 server 初始化失败只产生带 server 名称的非敏感诊断，其他 server 仍为 `READY`。
3. 编写分类测试，断言 `read_tools` 中的精确远端名称映射为 `ToolKind.READ`，其他远端工具一律为 `ToolKind.WRITE`，且远端定义没有持久化授权参数。
4. 实现每 server 状态、锁、连接和工具缓存；`initialize_all()` 并行初始化独立 server，但保证同一 server 不重复握手和发现。
5. 为远端工具名和公开名称添加下游工具协议兼容性校验；不兼容的单个工具只进入该 server 诊断，不破坏其他已发现工具。

**验证：** `python -m pytest tests/test_mcp_pool.py -q` 通过初始化、隔离、名称前缀与读写分类用例。

## T9: 实现池重连、调用与统一关闭

**文件：** `src/mycode/mcp/pool.py`、`tests/test_mcp_pool.py`
**依赖：** T8
**步骤：**
1. 编写连续调用测试，断言同一 `READY` server 的两次工具调用只使用一次初始化和工具发现。
2. 编写失效 server 重连测试，断言首次断连使该 server 失败，下一次调用在该 server 锁内重新连接、重新发现并成功调用；重连失败返回结构化失败。
3. 编写并发重连测试，断言多个等待同一失效 server 的调用只触发一次重连过程。
4. 编写 `close()` 测试，断言所有 server 连接关闭、状态变为 `CLOSED`，关闭可重复调用且不会影响本地工具。
5. 实现 `call_tool()`、按需重连、失败状态转换和统一关闭，将远端 `tools/call` 内容、错误、超时和断连转换为既有 `ToolResult`。

**验证：** `python -m pytest tests/test_mcp_pool.py -q` 通过连接复用、重连、并发锁和关闭用例。

## T10: 扩展 Tool 契约、注册表和执行器

**文件：** `src/mycode/tool/base.py`、`src/mycode/tool/registry.py`、`src/mycode/tool/executor.py`、`src/mycode/tool/__init__.py`、`tests/test_tool_registry.py`、`tests/test_tool_executor.py`
**依赖：** T1
**步骤：**
1. 编写注册表测试，断言普通本地工具始终出现在完整和模型可见定义中；延迟工具在发现前不出现在模型定义，而是出现在稳定排序的名称加描述摘要中。
2. 编写发现状态测试，断言仅注册的延迟工具可被标记为已发现，标记成功后才进入模型定义；未知或非延迟工具的标记请求返回明确失败。
3. 编写执行器测试，断言实现 `execute_async()` 的工具在当前事件循环 await，未实现该方法的既有同步工具继续经 `asyncio.to_thread()` 执行，二者的超时和异常都保持 `ToolResult` 语义。
4. 定义异步工具与延迟工具的类型契约，扩展 `ToolRegistry` 的完整、模型可见和延迟摘要视图，并保留既有 `definitions()` 的本地调用兼容性。
5. 更新 `ToolExecutor` 与包导出，不让通用 tool 包导入 `mycode.mcp`。

**验证：** `python -m pytest tests/test_tool_registry.py tests/test_tool_executor.py -q` 通过。

## T11: 实现 MCP wrapper 与 ToolSearch

**文件：** `src/mycode/mcp/tools.py`、`src/mycode/mcp/__init__.py`、`tests/test_mcp_tools.py`
**依赖：** T8、T9、T10
**步骤：**
1. 编写 wrapper 测试，断言 `MCPToolWrapper.should_defer()` 始终返回真，定义使用公开前缀名称、远端描述、远端参数 schema 和池确定的读写分类。
2. 编写 wrapper 调用测试，断言 `execute_async()` 将公开工具调用转为正确 server 与原始远端工具名的池调用，并保留成功、超时、断连和远端错误的结构化 `ToolResult`。
3. 编写 ToolSearch 成功测试，断言其默认是普通读工具，按完整公开名称返回工具名称、描述和完整 schema，并只在成功后调用注册表发现标记。
4. 编写 ToolSearch 失败测试，覆盖未知名称、非 MCP 工具、未注册工具、失效 server 和歧义条目；断言失败不改变任何发现状态。
5. 实现 wrapper、ToolSearch 和将池中 `RemoteTool` 批量注册到现有注册表的辅助函数。

**验证：** `python -m pytest tests/test_mcp_tools.py -q` 通过。

## T12: 接入 Agent 的延迟工具提醒与下一轮 schema 注入

**文件：** `src/mycode/agent/loop.py`、`tests/test_agent_loop.py`
**依赖：** T10、T11
**步骤：**
1. 编写首轮提示测试，注册一个未发现 MCP wrapper 后断言 LLM 工具列表不含其完整 schema，而运行时 `<system-reminder>` 同时含公开名称和描述。
2. 编写 ToolSearch 两轮测试，第一轮模型调用 ToolSearch，第二轮模型请求仅新增成功发现工具的完整 schema；断言提醒不写入正常 conversation memory。
3. 编写失败搜索测试，断言第二轮仍不包含失败工具 schema，且工具失败照常写回既有工具历史。
4. 编写权限链路测试，断言已发现的默认写远端工具仍进入现有审批和 plan-only 分支，读白名单工具保持既有读工具调度。
5. 修改 Agent 每轮从注册表获得模型可见定义和未发现摘要，并在 `PromptBuilder.begin_turn()` 中传入不含敏感字段的系统提醒。

**验证：** `python -m pytest tests/test_agent_loop.py tests/test_prompt_reminder.py -q` 通过。

## T13: 在 CLI 生命周期中初始化并关闭 MCP

**文件：** `src/mycode/cli.py`、`tests/test_cli.py`
**依赖：** T2、T8、T9、T11、T12
**步骤：**
1. 编写参数解析测试，断言 `--mcp-config PATH` 与已有 `--config` 可同时传入，且省略 MCP 参数时维持现有 CLI 参数行为。
2. 编写无 MCP 配置测试，断言 CLI 启动、Agent 创建和 TUI 运行继续使用本地工具，不创建网络连接或子进程。
3. 编写有效 MCP 配置测试，断言在 TUI 所在单一事件循环内创建池、初始化 server、注册 ToolSearch 与远端 wrapper，并将诊断以非敏感方式报告。
4. 编写显式无效 MCP 配置测试，断言 TUI 前返回清晰配置错误；编写正常退出和 TUI 异常退出测试，断言池均被关闭。
5. 重构 CLI 的异步应用边界，使 MCP 池不跨越 `asyncio.run()`；保持现有 LLM、权限、会话与本地工具构造顺序。

**验证：** `python -m pytest tests/test_cli.py -q` 通过。

## T14: 编写用户配置示例与使用文档

**文件：** `examples/mycode.mcp.yaml`、`README.md`、`tests/test_docs.py`
**依赖：** T13
**步骤：**
1. 新建无真实凭据的 YAML 示例，分别展示 stdio server 的命令、参数、环境变量、超时和读工具白名单，以及 Streamable HTTP server 的 URL、静态请求头环境变量引用和超时。
2. 在 README 说明 `--mcp-config` 与两级自动发现顺序、server 名称前缀、默认写权限、连接缓存与单 server 故障隔离。
3. 在 README 说明延迟注入的“名称加描述 → ToolSearch → 下一轮完整 schema”流程。
4. 在 README 明确只支持 stdio 与 Streamable HTTP、只响应 `ping` server 请求，并列出 resources、prompts、sampling、elicitation、OAuth、旧 HTTP+SSE、热重载和持久化缓存等不支持范围。
5. 扩展文档测试，断言 README 包含 MCP 配置入口、两种传输、延迟发现和关键边界说明。

**验证：** `python -m pytest tests/test_docs.py -q` 通过。

## T15: 执行 Stage 06 集成回归与验收准备

**文件：** 所有 Stage 06 新增与修改文件
**依赖：** T1-T14
**步骤：**
1. 运行所有 MCP 专项测试，确认配置、双传输、双向 JSON-RPC、超时、重连、延迟发现、权限和 CLI 生命周期共同通过。
2. 运行既有 Tool、Prompt、Agent、Permission、Session、TUI、Protocol 与文档测试，确认没有改变本地工具和无 MCP 配置时的行为。
3. 运行完整测试套件，不设置真实 MCP、HTTP、LLM 或鉴权环境变量。
4. 检查 `git diff --check`、`git status --short` 和测试日志，确认没有敏感值、测试生成的子进程或未预期的文件变更。
5. 将实际命令结果记录到 `checklist.md` 的验收证据中；只有全部通过后才宣告实现完成。

**验证：** `python -m pytest tests/test_mcp_config.py tests/test_mcp_jsonrpc.py tests/test_mcp_stdio.py tests/test_mcp_streamable_http.py tests/test_mcp_pool.py tests/test_mcp_tools.py tests/test_tool_registry.py tests/test_tool_executor.py tests/test_agent_loop.py tests/test_cli.py tests/test_docs.py -q` 通过；随后 `python -m pytest` 通过。

## 执行顺序

```text
T1
├─ T2 ───────────────────────────────────────────────┐
├─ T3 ─┬─ T5 ─────────────────────────────────────────┤
├─ T4 ─┴─ T6 ─ T7 ─ T8 ─ T9 ─ T11 ─ T12 ─ T13 ─ T14 ─ T15
└─ T10 ───────────────────────────────────────────────┘
```

T2、T3、T4 和 T10 在完成 T1 后可并行。T5 与 T6 在 T3 和 T4 完成后可并行；T6 使用内存 transport 覆盖协议分派，T4 和 T5 分别覆盖真实 stdio 与 HTTP 传输。T13 前必须完成池、远端工具和 Agent 集成；T15 只在所有前置任务验证通过后执行。
