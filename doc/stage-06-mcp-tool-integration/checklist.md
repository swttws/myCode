# myCode Stage 06：MCP 远端工具接入验收清单

> 每一项都通过运行测试、检查结构化结果或观察用户可见行为验证。自动化验证统一使用内存 transport、受控本地 stdio 子进程、HTTP 模拟服务、fake LLM 和脚本化审批 provider；不访问外网、真实 MCP server、真实 API key 或真实凭据。

## 验收证据（2026-07-20）

- 实现分支：`feature/stage-06-mcp-tool-integration`；独立 worktree：`D:\java\project\myCode\myCode\.worktrees\stage-06-mcp-tool-integration`。
- 功能提交：`73b192d`；审查修复提交：`9654c0c`。
- Stage 06 聚焦回归：`169 passed`；运行时将 `PytestUnraisableExceptionWarning` 提升为错误，退出码为 0。
- 全项目回归：`444 passed, 2 skipped`；运行时将 `PytestUnraisableExceptionWarning` 提升为错误，退出码为 0。
- Python `3.10.18` 对 `src` 和 `tests` 执行 `compileall`，退出码为 0；`mycode.mcp` 独立导入成功。
- `git diff --check` 退出码为 0；通用 Tool 层反向导入 `mycode.mcp` 的匹配数为 0。
- 常见高风险凭据字面量扫描命中文件数为 0；feature worktree 提交后无未提交变更。
- 独立审查列出的 Streamable HTTP 时序/流式消费、JSON-RPC error、取消清理、目录刷新、结果校验、保留请求头、分页及 `error.data` 兼容问题均有回归测试覆盖。

## 实现完整性与包边界

- [x] `I1` `mycode.mcp` 包完整提供配置、JSON-RPC、stdio、Streamable HTTP、连接、server 池和远端工具适配边界，且包可独立导入、无循环依赖。（验证：运行 `python -m compileall src/mycode/mcp`，期望退出码为 0；运行 `python -c "import sys; sys.path.insert(0, 'src'); import mycode.mcp"`，期望导入成功）
- [x] `I2` 通用 Tool 层只依赖异步工具和延迟工具契约，不反向导入 MCP 协议实现；MCP 层不复制 Agent、TUI 或权限策略。（验证：运行 `rg -n "mycode\.mcp" src/mycode/tool`，期望无匹配；检查 `src/mycode/mcp/` 不包含权限档位、审批 UI 或 Agent 轮次实现）
- [x] `I3` CLI 在 TUI 所在的同一事件循环内初始化、使用并关闭 MCP 资源，不跨 `asyncio.run()` 共享 HTTP client、子进程或连接。（验证：运行 `python -m pytest tests/test_cli.py tests/test_mcp_pool.py -q`，期望事件循环身份、正常退出和异常退出测试通过）
- [x] `I4` 未配置 MCP 时不创建 MCP 网络连接或子进程，既有本地工具、权限、Agent Loop、TUI 和 LLM 协议行为保持可用。（验证：运行 `python -m pytest tests/test_cli.py tests/test_agent_loop.py tests/test_tool_registry.py tests/test_e2e_chat.py -q`，期望无 MCP 配置回归用例通过且 MCP transport 创建计数为 0）

## 配置发现与校验

- [x] `C1` `--mcp-config PATH` 可与 `--config PATH` 同时使用并优先加载显式 MCP 文件；显式文件不存在、不可读或 YAML 无法解析时，启动前显示清晰配置错误并返回失败。（验证：运行 `python -m pytest tests/test_mcp_config.py tests/test_cli.py -q`，期望显式路径成功及三类致命错误用例通过）
- [x] `C2` 未提供 `--mcp-config` 时先查找工作目录 `mycode.mcp.yaml`，再查找 `~/.mycode/mcp.yaml`；两处均不存在时返回禁用 MCP 的空配置而非错误。（验证：运行 `python -m pytest tests/test_mcp_config.py tests/test_cli.py -q`，期望发现顺序、优先级和无文件启动测试通过）
- [x] `C3` 一个配置文件可声明多个稳定且唯一的 server 名称；stdio 项只接受命令、参数和环境变量字段，Streamable HTTP 项只接受 URL 和请求头字段，所有 server 均使用正数超时和合法传输类型。（验证：运行 `python -m pytest tests/test_mcp_config.py -q`，期望两类合法配置及缺字段、错字段、重复名称、非法名称、非法传输和非正超时测试通过）
- [x] `C4` `${NAME}` 环境变量引用可用于 stdio 环境变量和 HTTP 请求头；变量缺失时只报告变量名和配置位置，不输出已解析值、完整请求头或其他敏感配置。（验证：运行 `python -m pytest tests/test_mcp_config.py -q`，使用固定 secret fixture，期望解析成功且错误、诊断和日志中均无 secret）
- [x] `C5` 单个 server 配置项无效时，该项产生带 server 名称和原因的非致命诊断并被跳过，其他有效 server 仍被加载；文件级错误与逐 server 错误不会混淆。（验证：运行 `python -m pytest tests/test_mcp_config.py tests/test_cli.py -q`，期望混合有效/无效配置只初始化有效项）
- [x] `C6` `read_tools` 只接受目标 server 内精确的原始工具名；未列出的工具不会因通配符、前缀或相似名称被降为读工具。（验证：运行 `python -m pytest tests/test_mcp_config.py tests/test_mcp_pool.py tests/test_mcp_tools.py -q`，期望精确匹配和近似名称反例通过）

## 传输与 MCP 生命周期

- [x] `L1` stdio transport 按配置的命令、参数和环境启动受控子进程，以一行一个 JSON 对象发送和接收消息，持续消费 stderr 且不把 stderr 当作协议响应。（验证：运行 `python -m pytest tests/test_mcp_stdio.py -q`，期望启动、消息边界、环境覆盖和 stderr 用例通过）
- [x] `L2` stdio 正常关闭时先关闭输入流并等待子进程退出；超时后依次终止和必要时强制终止，测试结束后不遗留受控子进程。（验证：运行 `python -m pytest tests/test_mcp_stdio.py -q`，期望正常、占用、终止和重复关闭用例通过，并断言子进程均已退出）
- [x] `L3` Streamable HTTP 的 POST 请求携带 JSON-RPC body、协商协议版本、会话标识及配置请求头；`application/json` 响应进入统一消息流。（验证：运行 `python -m pytest tests/test_mcp_streamable_http.py -q`，期望请求体、协议头、会话头和 JSON 响应断言通过）
- [x] `L4` Streamable HTTP 正确解析 POST 返回的 SSE 多事件及可选 GET 事件流，忽略注释和空行；HTTP 错误、无效 Content-Type、无效 SSE 数据和断连形成稳定传输失败。（验证：运行 `python -m pytest tests/test_mcp_streamable_http.py -q`，期望 JSON/SSE/GET 和各失败用例通过）
- [x] `L5` stdio 与 Streamable HTTP 均严格按 `initialize` 请求、保存协商版本和能力、发送 `notifications/initialized`、调用 `tools/list` 的顺序完成初始化，初始化成功前不允许 `tools/call`。（验证：运行 `python -m pytest tests/test_mcp_jsonrpc.py tests/test_mcp_stdio.py tests/test_mcp_streamable_http.py -q`，期望生命周期消息顺序和提前调用拒绝测试通过）
- [x] `L6` server 返回的协议版本、能力、HTTP 会话标识和工具清单均被当前连接保存和使用；版本不兼容或工具清单无效时只使所属 server 初始化失败。（验证：运行 `python -m pytest tests/test_mcp_jsonrpc.py tests/test_mcp_streamable_http.py tests/test_mcp_pool.py -q`，期望协商、会话更新和无效初始化结果用例通过）

## 双向 JSON-RPC、并发与超时

- [x] `R1` 请求、通知、成功响应、错误响应和取消通知均固定使用 JSON-RPC 2.0 形状；非对象消息、非法版本、响应同时包含 `result` 与 `error`、缺少必要字段等输入被拒绝为协议错误。（验证：运行 `python -m pytest tests/test_mcp_jsonrpc.py -q`，期望消息构造和参数化校验测试通过）
- [x] `R2` 同一 server 同时存在多个 pending 请求且成功或错误响应乱序到达时，每个 Future 只由相同请求 id 的响应完成，不发生错配、重复完成或永久挂起。（验证：运行 `python -m pytest tests/test_mcp_jsonrpc.py -q`，期望乱序成功/错误混合和重复响应用例通过）
- [x] `R3` 收到 server 通知时记录方法和非敏感诊断，不完成、取消或阻塞任何无关 pending 请求。（验证：运行 `python -m pytest tests/test_mcp_jsonrpc.py -q`，期望通知与并发请求交错测试通过）
- [x] `R4` 收到 server 的 `ping` 请求时以相同 id 返回成功响应；收到 `roots/list`、sampling、elicitation 或任意其他本期不支持的 server 请求时返回 `-32601`，且其他请求继续完成。（验证：运行 `python -m pytest tests/test_mcp_jsonrpc.py -q`，期望 `ping`、已知排除方法和未知方法测试通过）
- [x] `R5` 连接、初始化、发现和工具调用均受 server 超时约束；已发送请求超时后从 pending 映射移除、尽力发送引用原 id 的取消通知并返回结构化超时，迟到响应不会改变结果。（验证：运行 `python -m pytest tests/test_mcp_jsonrpc.py tests/test_mcp_pool.py tests/test_mcp_stdio.py tests/test_mcp_streamable_http.py -q`，期望各阶段超时、取消和迟到响应测试通过）
- [x] `R6` transport 断连、解析失败或协议错误会一次性结束所属连接的全部 pending 请求并关闭资源，不留下悬挂任务；其他 server 的 pending 请求不受影响。（验证：运行 `python -m pytest tests/test_mcp_jsonrpc.py tests/test_mcp_pool.py -q`，期望多 pending 失败收敛和跨 server 隔离测试通过）

## Server 池、工具映射与连接复用

- [x] `P1` 多个 server 可并行初始化；单个 server 连接或发现失败只进入该 server 的诊断和 `FAILED` 状态，其他 server 仍达到 `READY` 并提供工具。（验证：运行 `python -m pytest tests/test_mcp_pool.py -q`，期望并行时序、部分失败和可用工具集合断言通过）
- [x] `P2` 远端工具公开名称稳定使用 `server_name__remote_name`；两个 server 提供相同原始工具名时可同时注册且无冲突。（验证：运行 `python -m pytest tests/test_mcp_pool.py tests/test_mcp_tools.py tests/test_tool_registry.py -q`，期望同名工具得到不同公开名称）
- [x] `P3` 调用任一公开远端工具时，参数被原样发送到公开名称所对应的 server 和原始工具名，不会把前缀名发送给远端或路由到同名的其他 server。（验证：运行 `python -m pytest tests/test_mcp_tools.py tests/test_mcp_pool.py -q`，期望双 server 调用记录中的目标和参数准确）
- [x] `P4` 同一 `READY` server 的连续工具调用只初始化一次、发现一次并复用同一会话；不会为每次调用重复握手或 `tools/list`。（验证：运行 `python -m pytest tests/test_mcp_pool.py -q`，期望两次调用后的 initialize 和 `tools/list` 计数均为 1）
- [x] `P5` server 断连、超时或协议错误后转为 `FAILED`；下次搜索或调用该 server 时在每 server 锁内重连并重新发现，多个并发等待者只触发一次重连。（验证：运行 `python -m pytest tests/test_mcp_pool.py tests/test_mcp_tools.py -q`，期望成功重连、重连失败和并发单次握手测试通过）
- [x] `P6` 池关闭会关闭所有 HTTP 和 stdio 连接并将状态置为 `CLOSED`；关闭操作幂等，且不会关闭或注销本地工具。（验证：运行 `python -m pytest tests/test_mcp_pool.py tests/test_cli.py tests/test_tool_registry.py -q`，期望正常/异常退出、重复关闭和本地工具可用性测试通过）

## 延迟发现、注册表与异步执行

- [x] `D1` 注册表区分完整注册工具、当前模型可见定义和未发现延迟摘要；本地工具与 ToolSearch 始终可见，未发现 MCP wrapper 只出现在摘要中。（验证：运行 `python -m pytest tests/test_tool_registry.py tests/test_mcp_tools.py -q`，期望三个视图及稳定排序断言通过）
- [x] `D2` 未发现 MCP 工具的运行时系统提醒只包含稳定公开名称及其对应描述，不包含参数 schema、环境变量、请求头、URL、会话标识或其他配置细节，也不写入正常 conversation memory。（验证：运行 `python -m pytest tests/test_agent_loop.py tests/test_prompt_reminder.py -q`，使用固定敏感 fixture，期望首轮请求和 memory 字段白名单断言通过）
- [x] `D3` ToolSearch 是始终可用的普通读工具，只接受完整公开名称；成功搜索返回该工具的名称、描述和完整参数 schema，并仅在成功后标记为已发现。（验证：运行 `python -m pytest tests/test_mcp_tools.py tests/test_tool_registry.py -q`，期望成功结果和发现状态转换测试通过）
- [x] `D4` 搜索不存在、歧义、非 MCP、未注册、不可用或所属 server 已失效的名称时返回可供模型理解的结构化失败，不改变任何工具的发现状态。（验证：运行 `python -m pytest tests/test_mcp_tools.py tests/test_agent_loop.py -q`，期望各失败类别、错误字段和发现集合不变断言通过）
- [x] `D5` ToolSearch 成功的当前轮仍不把目标工具 schema 加入已经发出的模型请求；从下一轮起仅成功发现的工具作为正常定义出现，其他延迟工具继续只显示名称和描述。（验证：运行 `python -m pytest tests/test_agent_loop.py tests/test_tool_registry.py -q`，期望搜索前、搜索轮和下一轮三阶段工具列表断言通过）
- [x] `D6` `MCPToolWrapper` 始终声明延迟，暴露远端描述和参数 schema，并通过异步执行路径返回现有 `ToolResult`；同步本地工具仍经线程执行，二者的超时、异常和取消语义不回归。（验证：运行 `python -m pytest tests/test_mcp_tools.py tests/test_tool_executor.py -q`，期望事件循环身份、线程分流和结果契约测试通过）

## 权限、审批与 Agent 结果

- [x] `A1` 所有未列入 `read_tools` 的远端工具均声明为写工具；在默认权限档位下先触发现有中文审批，未获批准时不发送远端 `tools/call`。（验证：运行 `python -m pytest tests/test_mcp_pool.py tests/test_mcp_tools.py tests/test_agent_loop.py tests/test_permission_e2e.py -q`，期望默认写分类、审批和远端调用计数断言通过）
- [x] `A2` 只有 `read_tools` 精确列出的指定远端工具按既有读工具规则调度；同 server 的其他工具及其他 server 的同名工具仍为写工具。（验证：运行 `python -m pytest tests/test_mcp_pool.py tests/test_agent_loop.py -q`，期望读写分类和审批矩阵通过）
- [x] `A3` `plan-only` 开启时，远端写工具的普通允许仍提升为审批，拒绝和取消沿用现有 Agent 行为；关闭后恢复原规则，读工具不被错误升级。（验证：运行 `python -m pytest tests/test_agent_plan_only.py tests/test_agent_loop.py tests/test_mcp_tools.py -q`，期望 plan-only 叠加矩阵通过）
- [x] `A4` 远端工具不声明可持久化授权参数，不会把远端参数、token 或任意业务字段保存为会话/项目授权规则。（验证：运行 `python -m pytest tests/test_mcp_pool.py tests/test_mcp_tools.py tests/test_permission_service.py -q`，期望远端定义的授权参数为空且审批选项不产生持久规则）
- [x] `A5` 远端成功内容被转换为既有成功 `ToolResult` 并按原调用顺序写入 Agent 工具历史；结构化内容不会被错误丢弃或改成协议对象。（验证：运行 `python -m pytest tests/test_mcp_tools.py tests/test_agent_loop.py -q`，期望文本/结构化内容和 history 断言通过）
- [x] `A6` 远端 JSON-RPC error、无效响应、超时和断连均转换为稳定的结构化失败 `ToolResult`，Agent 可把结果回填给模型并继续当前会话，TUI 不因异常退出。（验证：运行 `python -m pytest tests/test_mcp_jsonrpc.py tests/test_mcp_pool.py tests/test_mcp_tools.py tests/test_agent_loop.py -q`，期望四类失败后的后续 fake LLM 轮次继续执行）

## 故障隔离、诊断与敏感信息

- [x] `S1` 单个 server 的配置、连接、发现、调用或重连失败只影响该 server；本地工具和其他 MCP server 在同一会话中仍可搜索和调用。（验证：运行 `python -m pytest tests/test_mcp_config.py tests/test_mcp_pool.py tests/test_agent_loop.py -q`，期望各阶段故障注入后无关工具调用成功）
- [x] `S2` 用户可见错误和日志包含 server 名称、传输类型及非敏感错误类别，但不包含环境变量解析值、HTTP 鉴权头、URL userinfo/查询凭据、完整原始 payload 或异常堆栈。（验证：运行 MCP 专项测试并使用固定 secret fixture 捕获输出和日志，期望敏感值无匹配且诊断字段完整）
- [x] `S3` 连接失效、请求超时、CLI 正常退出和异常退出均释放 HTTP client、GET 事件流、stdio 输入流、接收任务和子进程；不存在 pending Future、未回收任务或进程泄漏警告。（验证：运行 `python -m pytest tests/test_mcp_stdio.py tests/test_mcp_streamable_http.py tests/test_mcp_jsonrpc.py tests/test_mcp_pool.py tests/test_cli.py -q`，期望资源计数归零且无 asyncio 资源警告）
- [x] `S4` resources、prompts、completions、tasks、sampling、elicitation、OAuth、旧 HTTP+SSE、热重载和跨进程缓存均未被暴露为已支持能力；除 `ping` 外的 server 请求统一走方法不支持分支。（验证：运行 `python -m pytest tests/test_mcp_jsonrpc.py tests/test_docs.py -q`，并检查 README 支持边界，期望排除项与协议行为一致）
- [x] `S5` 自动化测试只访问测试进程启动的本地 stdio/HTTP 服务和临时目录，不读取真实用户 MCP 配置，不依赖外网、真实凭据、真实 LLM 或真实 API key。（验证：在清除 MCP/LLM 凭据环境变量并禁止外网的测试环境运行 `python -m pytest tests/test_mcp_config.py tests/test_mcp_jsonrpc.py tests/test_mcp_stdio.py tests/test_mcp_streamable_http.py tests/test_mcp_pool.py tests/test_mcp_tools.py tests/test_cli.py tests/test_agent_loop.py -q`，期望全部通过）

## 文档与示例

- [x] `O1` `examples/mycode.mcp.yaml` 同时展示无真实凭据的 stdio 和 Streamable HTTP 配置、环境变量引用、正数超时及精确 `read_tools` 示例。（验证：运行 `python -m pytest tests/test_docs.py -q`，并解析示例 YAML，期望两类 server 均通过配置校验且固定 secret 扫描无匹配）
- [x] `O2` README 说明 `--mcp-config`、两级自动发现顺序、`server_name__remote_name`、默认写权限、延迟发现流程、连接复用、单 server 故障隔离、两种传输和明确不支持范围。（验证：运行 `python -m pytest tests/test_docs.py -q`，期望 MCP 文档断言全部通过）

## 编译、测试与回归

- [x] `T1` 源码可编译且新增 MCP 包不存在导入错误。（验证：运行 `python -m compileall src`，期望退出码为 0）
- [x] `T2` MCP 配置、JSON-RPC、stdio、Streamable HTTP、server 池和工具适配专项测试全部通过。（验证：运行 `python -m pytest tests/test_mcp_config.py tests/test_mcp_jsonrpc.py tests/test_mcp_stdio.py tests/test_mcp_streamable_http.py tests/test_mcp_pool.py tests/test_mcp_tools.py -q`，期望全部通过）
- [x] `T3` 注册表、异步执行器、延迟提醒、Agent 权限链路和 CLI 生命周期集成测试全部通过。（验证：运行 `python -m pytest tests/test_tool_registry.py tests/test_tool_executor.py tests/test_prompt_reminder.py tests/test_agent_loop.py tests/test_agent_plan_only.py tests/test_cli.py -q`，期望全部通过）
- [x] `T4` 既有本地工具、权限、Session、TUI、memory、Agent 调度和三种 LLM 协议行为没有回归。（验证：运行 `python -m pytest tests/test_permission_e2e.py tests/test_session.py tests/test_tui.py tests/test_memory.py tests/test_agent_scheduler.py tests/test_e2e_chat.py tests/test_openai_chat_protocol.py tests/test_openai_responses_protocol.py tests/test_anthropic_protocol.py -q`，期望全部通过）
- [x] `T5` MCP 用户文档和配置示例测试通过。（验证：运行 `python -m pytest tests/test_docs.py -q`，期望全部通过）
- [x] `T6` 全项目自动化测试在无外网、无真实 MCP/LLM 凭据条件下通过。（验证：运行 `python -m pytest -q`，期望全部通过）
- [x] `T7` 变更不存在空白错误、测试生成物、遗留子进程或意外的用户配置修改。（验证：运行 `git diff --check`，期望无输出；运行 `git status --short`，只出现预期源代码、测试、文档和示例变更；检查测试辅助记录的子进程均已退出）

## 端到端场景

- [x] `E1` 无 MCP 配置场景：用户按既有配置启动 myCode，fake LLM 调用本地工具并获得结果，期间不创建 MCP transport、ToolSearch 或远端 wrapper。（验证：运行 `python -m pytest tests/test_cli.py tests/test_e2e_chat.py -q` 中无 MCP 配置集成场景，期望本地工具完整流程成功）
- [x] `E2` 主流程场景：用户以独立配置启动 myCode，受控 stdio 与 HTTP server 均连接成功；fake LLM 首轮只看到两个 server 的“公开名称 + 描述”，先调用 ToolSearch 获取目标工具完整定义，下一轮看到并调用该工具，按默认写权限完成审批后获得远端结果。（验证：运行 `python -m pytest tests/test_cli.py tests/test_agent_loop.py tests/test_mcp_pool.py tests/test_mcp_tools.py -q` 中双 server 延迟发现集成场景，期望握手、搜索、下一轮 schema、审批、路由和结果断言全部通过）
- [x] `E3` 同名路由场景：两个 server 都提供相同原始工具名，fake LLM 分别发现并调用两个公开名称，两次调用到达各自 server 且返回值按调用顺序回填。（验证：运行 `python -m pytest tests/test_mcp_pool.py tests/test_mcp_tools.py tests/test_agent_loop.py -q` 中同名工具集成场景，期望目标、参数和结果均不串线）
- [x] `E4` 运行中失效场景：一个 server 在调用期间断连时返回结构化失败，fake LLM 随后仍成功调用本地工具或另一 server；再次需要失效 server 时只发生一次重连和重新发现，成功则恢复调用，失败则会话仍继续。（验证：运行 `python -m pytest tests/test_mcp_pool.py tests/test_agent_loop.py tests/test_cli.py -q` 中断连、成功重连和重连失败集成场景，期望无关调用成功且 TUI/Agent 不退出）

## 验收标准映射

| Spec | 对应清单条目 |
|---|---|
| AC1 | C1、C2、C5、I4、E1 |
| AC2 | L1、L3、L4、L5、L6 |
| AC3 | R2、R5、R6 |
| AC4 | R3、R4 |
| AC5 | P2、P3、E3 |
| AC6 | C6、A1、A2、A3、A4 |
| AC7 | P1、P4、P5、S1、E4 |
| AC8 | D1、D2、D3、D5、E2 |
| AC9 | D4、D5 |
| AC10 | A5、A6 |
| AC11 | C1、C5、P1、S1、S2 |
| AC12 | L2、R5、P6、S3 |
| AC13 | C4、D2、S2、S5、T6 |
| AC14 | E2 |
| AC15 | E4 |
