# myCode 阶段 03：Agent Loop 与事件流任务拆解

## 阶段标识

- 阶段编号：Stage 03
- 阶段名称：Agent Loop 与事件流
- 阶段目标：按可测试的小步任务完成独立 Agent Loop、稳定事件流、工具分批、`plan-only` 审批、取消与超时边界。

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `src/mycode/agent/__init__.py` | 导出 Agent 层公开入口 |
| 新建 | `src/mycode/agent/approval.py` | 审批请求、审批决定和审批 provider 类型 |
| 新建 | `src/mycode/agent/config.py` | Agent 运行配置和最小 system prompt |
| 新建 | `src/mycode/agent/events.py` | 稳定 Agent 事件契约 |
| 新建 | `src/mycode/agent/history.py` | `ChatMessage` 构造和工具结果序列化 |
| 新建 | `src/mycode/agent/interceptor.py` | 工具执行前后拦截协议和 `plan-only` 默认拦截器 |
| 新建 | `src/mycode/agent/loop.py` | Agent Loop 主循环 |
| 新建 | `src/mycode/agent/scheduler.py` | 工具调用读写分批 |
| 新建 | `src/mycode/agent/state.py` | 会话级 Agent 模式状态 |
| 修改 | `src/mycode/tool/base.py` | 增加 `ToolKind`，让 `ToolDefinition` 声明读写分类 |
| 修改 | `src/mycode/tool/filesystem.py` | 默认文件工具补齐读写分类 |
| 修改 | `src/mycode/tool/command.py` | 命令工具补齐写类分类 |
| 修改 | `src/mycode/tool/registry.py` | 注册时校验工具分类，OpenAI payload 忽略分类 |
| 修改 | `src/mycode/tool/__init__.py` | 导出 `ToolKind` |
| 修改 | `src/mycode/session.py` | 改为 AgentLoop 薄门面，保存 `plan-only` 状态 |
| 修改 | `src/mycode/tui.py` | 消费 Agent 事件，增加 `/plan-only` 和审批输入 |
| 修改 | `src/mycode/cli.py` | 组装 AgentLoop 与 ChatSession |
| 修改 | `README.md` | 更新 Stage 03 能力与边界说明 |
| 新建 | `tests/test_agent_events.py` | Agent 事件契约测试 |
| 新建 | `tests/test_agent_scheduler.py` | 工具读写分批测试 |
| 新建 | `tests/test_agent_interceptor.py` | `plan-only` 拦截测试 |
| 新建 | `tests/test_agent_loop.py` | Agent Loop 文本、工具循环、错误、取消、超时测试 |
| 新建 | `tests/test_agent_plan_only.py` | 审批批准、拒绝、取消测试 |
| 修改 | `tests/test_tool_registry.py` | 工具分类注册和默认工具分类测试 |
| 修改 | `tests/test_tool_executor.py` | fake 工具定义补齐分类 |
| 修改 | `tests/test_llm_base.py` | fake 工具定义补齐分类 |
| 修改 | `tests/test_openai_responses_protocol.py` | fake 工具定义补齐分类，确认 payload 不含分类 |
| 修改 | `tests/test_openai_chat_protocol.py` | fake 工具定义补齐分类，确认 payload 不含分类 |
| 修改 | `tests/test_anthropic_protocol.py` | fake 工具定义补齐分类 |
| 修改 | `tests/test_session.py` | 迁移为 AgentEvent 转发和模式状态测试 |
| 修改 | `tests/test_tui.py` | 迁移为 AgentEvent 渲染、`/plan-only` 和审批测试 |
| 修改 | `tests/test_cli.py` | 验证 CLI 组装 AgentLoop |
| 修改 | `tests/test_e2e_chat.py` | mocked 端到端 Agent Loop 测试 |
| 修改 | `tests/test_docs.py` | README Stage 03 文档断言 |

## T1: 增加工具读写分类基础类型

**文件：** `src/mycode/tool/base.py`、`src/mycode/tool/__init__.py`、`tests/test_tool_registry.py`、`tests/test_tool_executor.py`、`tests/test_llm_base.py`、`tests/test_openai_responses_protocol.py`、`tests/test_openai_chat_protocol.py`、`tests/test_anthropic_protocol.py`

**依赖：** 无

**步骤：**
1. 在 `tests/test_tool_registry.py` 中导入 `ToolKind`，把 `FakeTool` 的 `ToolDefinition` 增加 `kind=ToolKind.READ`。
2. 在 `tests/test_tool_registry.py` 增加断言：`FakeTool().definition.kind == ToolKind.READ`。
3. 在 `tests/test_tool_registry.py` 增加断言：`ToolRegistry` 注册 `kind` 不是 `ToolKind.READ` 或 `ToolKind.WRITE` 的 fake definition 时抛出 `ValueError`，错误信息包含 `invalid tool kind`。
4. 在所有测试 fake `ToolDefinition(...)` 调用点补齐 `kind=ToolKind.READ`，包括 `test_tool_executor.py`、`test_llm_base.py`、两个 OpenAI 协议测试和 Anthropic 协议测试。
5. 运行 `python -m pytest tests/test_tool_registry.py -q`，预期因 `ToolKind` 未定义或 `ToolDefinition.kind` 未实现而失败。
6. 在 `src/mycode/tool/base.py` 中新增 `ToolKind(str, Enum)`，包含 `READ` 和 `WRITE`。
7. 在 `ToolDefinition` 中新增必填字段 `kind: ToolKind`。
8. 在 `src/mycode/tool/__init__.py` 导出 `ToolKind`。
9. 在 `src/mycode/tool/registry.py` 的 `register()` 中校验 `tool.definition.kind` 是 `ToolKind.READ` 或 `ToolKind.WRITE`，否则抛出 `ValueError("invalid tool kind: ...")`。
10. 确认 `openai_chat_tool_specs()` 和 `openai_responses_tool_specs()` 不输出 `kind` 字段。

**验证：** `python -m pytest tests/test_tool_registry.py tests/test_tool_executor.py tests/test_llm_base.py tests/test_openai_responses_protocol.py tests/test_openai_chat_protocol.py tests/test_anthropic_protocol.py -q` 通过。

## T2: 为默认核心工具补齐分类

**文件：** `src/mycode/tool/filesystem.py`、`src/mycode/tool/command.py`、`tests/test_tool_registry.py`

**依赖：** T1

**步骤：**
1. 在 `tests/test_tool_registry.py` 增加 `test_default_tool_registry_declares_tool_kinds`。
2. 断言默认注册中心中 `read_file`、`find_files`、`search_code` 的 `definition.kind` 是 `ToolKind.READ`。
3. 断言默认注册中心中 `write_file`、`edit_file`、`run_command` 的 `definition.kind` 是 `ToolKind.WRITE`。
4. 运行 `python -m pytest tests/test_tool_registry.py::test_default_tool_registry_declares_tool_kinds -q`，预期因默认工具未声明分类而失败。
5. 在 `ReadFileTool.definition`、`FindFilesTool.definition`、`SearchCodeTool.definition` 中加入 `kind=ToolKind.READ`。
6. 在 `WriteFileTool.definition`、`EditFileTool.definition`、`RunCommandTool.definition` 中加入 `kind=ToolKind.WRITE`。
7. 确认文件和命令工具只新增分类字段，不改动现有工具参数 schema、描述和执行逻辑。

**验证：** `python -m pytest tests/test_tool_registry.py tests/test_tool_filesystem.py tests/test_tool_command.py -q` 通过。

## T3: 建立 Agent 事件、配置、状态和审批基础类型

**文件：** `src/mycode/agent/__init__.py`、`src/mycode/agent/approval.py`、`src/mycode/agent/config.py`、`src/mycode/agent/events.py`、`src/mycode/agent/state.py`、`tests/test_agent_events.py`

**依赖：** T1

**步骤：**
1. 新建 `tests/test_agent_events.py`，覆盖 `AgentEventType` 包含 `USER_MESSAGE`、`THINKING_DELTA`、`TEXT_DELTA`、`TOOL_CALL_STARTED`、`TOOL_RESULT`、`FINAL_RESPONSE`、`ERROR`、`CANCELLED`、`APPROVAL_REQUIRED`。
2. 在同一测试文件中覆盖 `AgentErrorCode.MAX_ROUNDS_EXCEEDED.value == "max_rounds_exceeded"`。
3. 在同一测试文件中构造 `ToolCall`、`ApprovalRequest`、`AgentEvent`，断言事件能携带 `round_index`、`tool_call`、`approval_request` 和 `error_code`。
4. 在同一测试文件中覆盖 `AgentMode(plan_only=True).reset()` 会把 `plan_only` 改为 `False`。
5. 在同一测试文件中覆盖 `AgentConfig().max_rounds == 8`，且 `minimal_system_prompt` 包含 `plan-only`。
6. 运行 `python -m pytest tests/test_agent_events.py -q`，预期因 `mycode.agent` 不存在而失败。
7. 新建 `src/mycode/agent/approval.py`，定义 `ApprovalRequest`、`ApprovalDecisionType`、`ApprovalDecision`、`ApprovalProvider`。
8. 新建 `src/mycode/agent/config.py`，定义 `AgentConfig` 和默认最小 system prompt。
9. 新建 `src/mycode/agent/state.py`，定义 `AgentMode` 和 `reset()`。
10. 新建 `src/mycode/agent/events.py`，定义 `AgentEventType`、`AgentErrorCode`、`AgentEvent`。
11. 新建 `src/mycode/agent/__init__.py`，导出本任务新增的公开类型。

**验证：** `python -m pytest tests/test_agent_events.py -q` 通过。

## T4: 实现工具分批调度器

**文件：** `src/mycode/agent/scheduler.py`、`src/mycode/agent/__init__.py`、`tests/test_agent_scheduler.py`

**依赖：** T1、T3

**步骤：**
1. 新建 `tests/test_agent_scheduler.py`，定义 `FakeTool(name, kind)`，返回带 `ToolKind` 的 `ToolDefinition`。
2. 写测试：给定调用顺序 `read_a, read_b, write_a, read_c`，`build_tool_batches()` 返回三个 batch，分别是两个读调用、一个写调用、一个读调用。
3. 写测试：连续两个写调用必须返回两个独立 batch。
4. 写测试：未知工具调用抛出 `ToolScheduleError`，`code == "unknown_tool"`。
5. 写测试：工具定义分类非法时抛出 `ToolScheduleError`，`code == "invalid_tool_kind"`。
6. 运行 `python -m pytest tests/test_agent_scheduler.py -q`，预期因 `scheduler.py` 不存在而失败。
7. 新建 `src/mycode/agent/scheduler.py`，定义 `ToolBatch`、`ToolScheduleError`、`build_tool_batches(calls, registry)`。
8. 实现按 registry 查找工具定义；未知工具和非法分类抛出 `ToolScheduleError`。
9. 实现连续读合并、写调用单独成批的顺序逻辑。
10. 在 `src/mycode/agent/__init__.py` 导出 `ToolBatch`、`ToolScheduleError`、`build_tool_batches`。

**验证：** `python -m pytest tests/test_agent_scheduler.py -q` 通过。

## T5: 实现 plan-only 默认拦截器

**文件：** `src/mycode/agent/interceptor.py`、`src/mycode/agent/__init__.py`、`tests/test_agent_interceptor.py`

**依赖：** T1、T3

**步骤：**
1. 新建 `tests/test_agent_interceptor.py`，构造读类和写类 `ToolDefinition`。
2. 写测试：`PlanOnlyInterceptor.before_tool()` 在 `AgentMode(plan_only=False)` 下对读写工具都返回 `ALLOW`。
3. 写测试：`plan_only=True` 且工具为 `ToolKind.READ` 时返回 `ALLOW`。
4. 写测试：`plan_only=True` 且工具为 `ToolKind.WRITE` 时返回 `REQUIRE_APPROVAL`，`reason` 包含 `plan-only`。
5. 写测试：`after_tool()` 原样返回传入的 `ToolResult`。
6. 运行 `python -m pytest tests/test_agent_interceptor.py -q`，预期因 `interceptor.py` 不存在而失败。
7. 新建 `src/mycode/agent/interceptor.py`，定义 `InterceptDecisionType`、`InterceptDecision`、`ToolInterceptor`、`PlanOnlyInterceptor`。
8. 实现默认拦截规则。
9. 在 `src/mycode/agent/__init__.py` 导出拦截相关类型。

**验证：** `python -m pytest tests/test_agent_interceptor.py -q` 通过。

## T6: 实现 Agent 历史消息 helper

**文件：** `src/mycode/agent/history.py`、`src/mycode/agent/__init__.py`、`tests/test_agent_loop.py`

**依赖：** T3

**步骤：**
1. 在 `tests/test_agent_loop.py` 中先创建历史 helper 测试区。
2. 写测试：`make_system_message("prompt")` 返回 `ChatMessage(role="system", content="prompt")`。
3. 写测试：`make_user_message("hi")` 返回 user 消息。
4. 写测试：`make_assistant_text_message("ok")` 返回 assistant 文本消息。
5. 写测试：`make_assistant_tool_call_message(call)` 正确填充 `tool_call_id`、`tool_name` 和 `tool_arguments`。
6. 写测试：`make_tool_result_message(call, result)` 返回 `role="tool"`，且 content 可被 `json.loads()` 解析为 `ok/tool_name/content/error`。
7. 运行 `python -m pytest tests/test_agent_loop.py -q`，预期因 `history.py` 不存在而失败。
8. 新建 `src/mycode/agent/history.py` 并实现上述 helper。
9. 在 `src/mycode/agent/__init__.py` 导出 `serialize_tool_result`。

**验证：** `python -m pytest tests/test_agent_loop.py -q` 通过当前历史 helper 测试。

## T7: 实现 AgentLoop 文本路径

**文件：** `src/mycode/agent/loop.py`、`src/mycode/agent/__init__.py`、`tests/test_agent_loop.py`

**依赖：** T3、T5、T6

**步骤：**
1. 在 `tests/test_agent_loop.py` 中定义 `ScriptedLLM`，记录每次 `stream_chat(messages, tools)` 入参，并按脚本 yield `StreamEvent`。
2. 定义 `NoopTool` 和 `ToolExecutor(ToolRegistry([NoopTool]))`，用于满足 `AgentLoop` 构造。
3. 写测试：普通文本响应 `TEXT_DELTA("hi")`、`TEXT_DELTA(" there")`、`DONE` 时，Agent yield `USER_MESSAGE`、两个 `TEXT_DELTA`、`FINAL_RESPONSE("hi there")`。
4. 断言 memory 中保存 user 消息和 assistant 文本消息，不保存 system prompt。
5. 断言 LLM 请求第一条是 system message，第二条是 user message。
6. 写测试：`THINKING_DELTA` 会转成 Agent `THINKING_DELTA`，但不写入 assistant 历史。
7. 运行 `python -m pytest tests/test_agent_loop.py::test_agent_loop_streams_text_and_final_response -q`，预期因 `AgentLoop` 不存在而失败。
8. 新建 `src/mycode/agent/loop.py`，定义 `AgentLoop.__init__()` 保存 llm、memory、tool_executor、tool_registry、config、interceptor。
9. 实现 `AgentLoop.run()` 的文本路径：写 user memory，yield `USER_MESSAGE`，调用 LLM，转发文本和 thinking，遇到无工具调用时写 assistant 文本并 yield `FINAL_RESPONSE`。
10. 在 `src/mycode/agent/__init__.py` 导出 `AgentLoop`。

**验证：** `python -m pytest tests/test_agent_loop.py -q` 通过当前文本路径和历史 helper 测试。

## T8: 实现 AgentLoop 的 LLM 错误和显式结束

**文件：** `src/mycode/agent/loop.py`、`tests/test_agent_loop.py`

**依赖：** T7

**步骤：**
1. 在 `tests/test_agent_loop.py` 写测试：`ScriptedLLM` 抛出 `LLMError("network failed")` 时，Agent yield `ERROR`，`error_code == AgentErrorCode.LLM_ERROR`，content 包含 `network failed`。
2. 断言 LLM 错误时 memory 只保留本轮 user 消息，不写 assistant 文本。
3. 写测试：LLM 只 yield `DONE` 且无文本时，Agent yield `FINAL_RESPONSE`，content 为空字符串，并不写空 assistant 消息。
4. 运行 `python -m pytest tests/test_agent_loop.py::test_agent_loop_converts_llm_error_to_agent_error -q`，预期失败。
5. 在 `AgentLoop.run()` 捕获 `LLMError`，转成 `AgentEvent(type=ERROR, error_code=LLM_ERROR, content=str(exc))`。
6. 调整无文本无工具的结束逻辑，yield 空 final response，不写空 assistant history。

**验证：** `python -m pytest tests/test_agent_loop.py -q` 通过。

## T9: 实现单工具 ReAct 循环

**文件：** `src/mycode/agent/loop.py`、`tests/test_agent_loop.py`

**依赖：** T7、T8

**步骤：**
1. 在 `tests/test_agent_loop.py` 定义 `EchoTool`，分类为 `ToolKind.READ`，返回 `ToolResult(ok=True, tool_name="echo", content={"text": arguments["text"]})`。
2. 写测试：第一轮 LLM yield `TOOL_CALL(echo)`，第二轮 LLM yield `TEXT_DELTA("done")` 和 `DONE`。
3. 断言 Agent 事件顺序包含 `USER_MESSAGE`、`TOOL_CALL_STARTED`、`TOOL_RESULT`、`TEXT_DELTA`、`FINAL_RESPONSE`。
4. 断言 LLM 被调用两次。
5. 断言第二次 LLM 请求中包含 user、assistant tool-call、tool result 三条历史。
6. 运行 `python -m pytest tests/test_agent_loop.py::test_agent_loop_executes_tool_and_continues_to_final_response -q`，预期失败。
7. 在 `AgentLoop.run()` 中收集本轮 `TOOL_CALL`。
8. 本轮有工具调用时，写入 assistant tool-call history。
9. 调用 `build_tool_batches()`，执行工具，yield `TOOL_CALL_STARTED` 和 `TOOL_RESULT`。
10. 写入 tool result history 后进入下一轮。

**验证：** `python -m pytest tests/test_agent_loop.py -q` 通过。

## T10: 实现最大轮数上限

**文件：** `src/mycode/agent/loop.py`、`tests/test_agent_loop.py`

**依赖：** T9

**步骤：**
1. 在 `tests/test_agent_loop.py` 写测试：`AgentConfig(max_rounds=2)`，LLM 连续两轮都请求同一个读工具。
2. 断言 Agent 在第二轮工具结果回填后不发起第三轮 LLM 调用。
3. 断言最后一个事件是 `ERROR`，`error_code == AgentErrorCode.MAX_ROUNDS_EXCEEDED`，content 包含 `max rounds`。
4. 运行 `python -m pytest tests/test_agent_loop.py::test_agent_loop_errors_when_max_rounds_exceeded -q`，预期失败。
5. 在 `AgentLoop.run()` 的 round 循环结束后 yield `ERROR(max_rounds_exceeded)`。
6. 确保该错误不写入 assistant 历史。

**验证：** `python -m pytest tests/test_agent_loop.py -q` 通过。

## T11: 实现多工具分批执行

**文件：** `src/mycode/agent/loop.py`、`tests/test_agent_loop.py`

**依赖：** T4、T9

**步骤：**
1. 在 `tests/test_agent_loop.py` 定义受控延迟读工具和记录型写工具。
2. 写测试：一轮 LLM 返回工具调用顺序 `read_a, read_b, write_a, read_c`。
3. 断言 `read_a` 和 `read_b` 的执行时间有重叠，证明同一读批并发执行。
4. 断言 `write_a` 的开始时间晚于前两个读工具完成时间。
5. 断言 `read_c` 的开始时间晚于 `write_a` 完成时间。
6. 断言 Agent 为四个工具都 yield `TOOL_CALL_STARTED` 和 `TOOL_RESULT`。
7. 运行 `python -m pytest tests/test_agent_loop.py::test_agent_loop_batches_read_tools_and_serializes_writes -q`，预期失败。
8. 在 AgentLoop 中实现读批使用 `asyncio.gather()` 执行，写批按单个工具执行。
9. 保持每个工具调用结果单独 yield，并按批次完成顺序写入 memory。

**验证：** `python -m pytest tests/test_agent_loop.py tests/test_agent_scheduler.py -q` 通过。

## T12: 处理工具调度错误和工具失败结果

**文件：** `src/mycode/agent/loop.py`、`tests/test_agent_loop.py`

**依赖：** T9、T11

**步骤：**
1. 写测试：LLM 请求未知工具时，Agent yield `ERROR`，`error_code == AgentErrorCode.UNKNOWN_TOOL`。
2. 写测试：工具执行返回 `ok=False` 时，Agent 仍 yield `TOOL_RESULT`，写入 tool result history，并进入下一轮 LLM。
3. 写测试：工具执行返回 `content={"timed_out": True}` 时，Agent 事件仍是 `TOOL_RESULT`，内容保留给 TUI 展示。
4. 运行 `python -m pytest tests/test_agent_loop.py::test_agent_loop_reports_unknown_tool_as_error -q`，预期失败。
5. 在 `AgentLoop` 捕获 `ToolScheduleError`，按 code 映射 `AgentErrorCode.UNKNOWN_TOOL` 或 `AgentErrorCode.INVALID_TOOL_KIND`。
6. 确保失败的 `ToolResult` 和成功结果走同一回填路径。

**验证：** `python -m pytest tests/test_agent_loop.py -q` 通过。

## T13: 实现 plan-only 审批批准路径

**文件：** `src/mycode/agent/loop.py`、`tests/test_agent_plan_only.py`

**依赖：** T5、T9

**步骤：**
1. 新建 `tests/test_agent_plan_only.py`，复用 fake LLM、fake write tool 和 `collect_async`。
2. 写测试：`AgentMode(plan_only=True)` 下，LLM 请求写工具时，Agent 先 yield `APPROVAL_REQUIRED`。
3. 提供 fake `approval_provider` 返回 `ApprovalDecision(APPROVE_ONCE)`。
4. 断言写工具实际执行一次，随后 yield `TOOL_RESULT`。
5. 断言 `mode.plan_only` 仍为 `True`。
6. 写测试：同一轮或后续轮再次请求写工具时再次触发审批。
7. 运行 `python -m pytest tests/test_agent_plan_only.py::test_plan_only_approval_approves_one_write_tool -q`，预期失败。
8. 在 AgentLoop 工具执行前调用 `interceptor.before_tool()`。
9. `REQUIRE_APPROVAL` 时 yield `APPROVAL_REQUIRED`，调用 `approval_provider`，批准后只执行当前工具。

**验证：** `python -m pytest tests/test_agent_plan_only.py -q` 通过当前批准路径测试。

## T14: 实现 plan-only 审批拒绝和取消路径

**文件：** `src/mycode/agent/loop.py`、`tests/test_agent_plan_only.py`

**依赖：** T13

**步骤：**
1. 写测试：approval provider 返回 `REJECT` 时，写工具不执行。
2. 断言 Agent yield `TOOL_RESULT`，`ok=False`，`error` 包含 `rejected`。
3. 断言拒绝结果写入 memory，下一轮 LLM 请求能看到该 tool result。
4. 写测试：approval provider 返回 `CANCEL` 时，Agent yield `CANCELLED` 并结束，不执行写工具。
5. 写测试：需要审批但没有 approval provider 时，Agent yield `ERROR`，`error_code == AgentErrorCode.APPROVAL_CANCELLED`。
6. 运行 `python -m pytest tests/test_agent_plan_only.py -q`，预期失败。
7. 在 AgentLoop 中实现 `REJECT` 构造结构化拒绝 `ToolResult` 并回填。
8. 在 AgentLoop 中实现 `CANCEL` 和缺少 provider 的结束逻辑。

**验证：** `python -m pytest tests/test_agent_plan_only.py tests/test_agent_interceptor.py -q` 通过。

## T15: 实现取消与 Agent 级超时

**文件：** `src/mycode/agent/loop.py`、`tests/test_agent_loop.py`

**依赖：** T9

**步骤：**
1. 写测试：消费 `AgentLoop.run()` 的任务被取消时，Agent 能产出 `CANCELLED` 事件，且未产生的工具结果不写入 memory。
2. 写测试：`AgentConfig(model_timeout_seconds=0.01)` 且 LLM 长时间不 yield 时，Agent yield `ERROR`，`error_code == AgentErrorCode.MODEL_TIMEOUT`。
3. 写测试：`AgentConfig(run_timeout_seconds=0.01)` 且多轮执行超过限制时，Agent yield `ERROR`，`error_code == AgentErrorCode.RUN_TIMEOUT`。
4. 运行 `python -m pytest tests/test_agent_loop.py::test_agent_loop_reports_model_timeout -q`，预期失败。
5. 在 AgentLoop 中给单次模型 stream 消费加超时控制。
6. 在 AgentLoop 中给整次 run 加截止时间检查。
7. 捕获 `asyncio.CancelledError` 并 yield `CANCELLED`。
8. 确保取消和超时路径不写入 tool result history。

**验证：** `python -m pytest tests/test_agent_loop.py -q` 通过。

## T16: 将 ChatSession 改为 Agent 门面

**文件：** `src/mycode/session.py`、`tests/test_session.py`

**依赖：** T7、T13

**步骤：**
1. 重写 `tests/test_session.py`，使用 `FakeAgent` 产生 `AgentEvent` 列表。
2. 写测试：`ChatSession.send("hello")` 转发 `AgentLoop.run()` 的所有事件。
3. 写测试：`ChatSession.send()` 传入当前 `AgentMode` 和 approval provider。
4. 写测试：`set_plan_only(True)` 后 `is_plan_only()` 返回 `True`。
5. 写测试：`clear()` 调用 Agent 的 memory 清理入口，并把 `plan_only` 复位为 `False`。
6. 运行 `python -m pytest tests/test_session.py -q`，预期因旧 session 接口而失败。
7. 修改 `ChatSession.__init__()`，接收 `agent: AgentLoop` 和可选 `AgentMode`。
8. 实现 `send()`、`set_plan_only()`、`is_plan_only()`、`clear()`。
9. 在 `AgentLoop` 中提供 `clear_memory()` 方法，内部调用 `ConversationMemory.clear()`。

**验证：** `python -m pytest tests/test_session.py tests/test_agent_loop.py -q` 通过。

## T17: 让 TUI 消费 AgentEvent

**文件：** `src/mycode/tui.py`、`tests/test_tui.py`

**依赖：** T3、T16

**步骤：**
1. 修改 `tests/test_tui.py` 的 `FakeSession.send()`，yield `AgentEvent` 而不是 LLM `StreamEvent`。
2. 写测试：`TEXT_DELTA` 会流式输出 assistant 文本。
3. 写测试：默认隐藏 `THINKING_DELTA`，`show_thinking=True` 时显示 thinking。
4. 写测试：`TOOL_CALL_STARTED` 输出工具名和开始状态。
5. 写测试：`TOOL_RESULT` 成功时输出工具已执行，失败时输出工具失败和 error。
6. 写测试：`ERROR` 输出错误说明，`CANCELLED` 输出取消说明。
7. 写测试：启动文案包含 `Stage 03` 和 `Agent`。
8. 运行 `python -m pytest tests/test_tui.py -q`，预期因 TUI 仍消费 `StreamEventType` 而失败。
9. 修改 `tui.py` 导入 `AgentEventType`。
10. 更新 `_render_stream()` 的事件分支，消费 AgentEvent 字段。
11. 保持 `/exit`、`/clear` 和空输入行为不变。

**验证：** `python -m pytest tests/test_tui.py -q` 通过当前事件渲染测试。

## T18: 实现 TUI 的 /plan-only 命令和审批输入

**文件：** `src/mycode/tui.py`、`tests/test_tui.py`

**依赖：** T14、T17

**步骤：**
1. 在 `tests/test_tui.py` 写测试：输入 `/plan-only` 时显示当前 plan-only 状态，不调用 session.send。
2. 写测试：输入 `/plan-only on` 调用 `session.set_plan_only(True)` 并输出已开启。
3. 写测试：输入 `/plan-only off` 调用 `session.set_plan_only(False)` 并输出已关闭。
4. 写测试：收到 `APPROVAL_REQUIRED` 时，TUI 调用审批输入；用户输入 `y` 返回 `ApprovalDecision(APPROVE_ONCE)`。
5. 写测试：审批输入 `n` 返回 `ApprovalDecision(REJECT)`。
6. 写测试：审批输入 `c` 返回 `ApprovalDecision(CANCEL)`。
7. 运行 `python -m pytest tests/test_tui.py::test_tui_plan_only_on_command_enables_mode -q`，预期失败。
8. 在 `ChatTUI.run()` 中解析 `/plan-only`、`/plan-only on`、`/plan-only off`。
9. 在 `_render_stream()` 调用 `self._session.send(user_text, approval_provider=self._approval_provider)`。
10. 实现 `_approval_provider()`，使用现有 `_read_input()` 获取 `y/n/c`，返回对应 `ApprovalDecision`。

**验证：** `python -m pytest tests/test_tui.py tests/test_agent_plan_only.py -q` 通过。

## T19: CLI 组装 AgentLoop

**文件：** `src/mycode/cli.py`、`tests/test_cli.py`

**依赖：** T16、T18

**步骤：**
1. 在 `tests/test_cli.py` 写测试：monkeypatch `AgentLoop`，断言 CLI 创建 AgentLoop 时传入 llm、memory、tool_executor、tool_registry。
2. 写测试：CLI 创建 `ChatSession(agent=agent)`，不再用旧的 `ChatSession(llm=..., memory=..., tool_executor=...)`。
3. 运行 `python -m pytest tests/test_cli.py -q`，预期因 CLI 仍旧组装 session 而失败。
4. 修改 `cli.py`，导入 `AgentLoop`。
5. 在创建 `tool_registry` 和 `tool_executor` 后创建 `AgentLoop(llm=llm, memory=memory, tool_executor=tool_executor, tool_registry=tool_registry)`。
6. 创建 `ChatSession(agent=agent)` 并传给 `ChatTUI`。
7. 保持配置错误处理和 TUI 启动流程不变。

**验证：** `python -m pytest tests/test_cli.py -q` 通过。

## T20: 更新端到端 mocked Agent Loop 测试

**文件：** `tests/test_e2e_chat.py`

**依赖：** T9、T16、T19

**步骤：**
1. 修改 `ScriptedLLM` 测试 helper，让断言适配请求开头的 system message。
2. 更新纯文本 e2e：第一轮输出 `hi`，第二轮请求能看到 system、user、assistant、user 的顺序。
3. 更新 `/clear` e2e：clear 后下一轮请求只包含 system 和新的 user，不包含旧工具历史。
4. 更新工具 e2e：第一轮模型请求 `read_file`，Agent 自动进入第二轮并输出最终文本。
5. 断言工具调用结果写入 memory，第二轮 LLM 请求包含 assistant tool-call 和 tool result。
6. 更新失败 edit e2e：工具失败结果回填后，模型第二轮能输出文本，程序继续运行。
7. 运行 `python -m pytest tests/test_e2e_chat.py -q`，预期在更新实现前失败。
8. 根据 AgentLoop 和 CLI 新行为修正 e2e 断言。

**验证：** `python -m pytest tests/test_e2e_chat.py -q` 通过。

## T21: 保持协议层和工具层回归

**文件：** `tests/test_openai_responses_protocol.py`、`tests/test_openai_chat_protocol.py`、`tests/test_anthropic_protocol.py`、`tests/test_protocol_factory.py`

**依赖：** T1、T2、T20

**步骤：**
1. 在 OpenAI Responses payload 测试中断言 tool spec 不包含 `kind`。
2. 在 OpenAI Chat payload 测试中断言 tool spec 不包含 `kind`。
3. 确认 Responses 和 Chat 的工具历史转换仍使用 `tool_call_id`、`tool_name`、`tool_arguments`、tool result content。
4. 确认 Anthropic 协议仍保持纯对话行为，不接入工具调用。
5. 运行协议相关测试。

**验证：** `python -m pytest tests/test_openai_responses_protocol.py tests/test_openai_chat_protocol.py tests/test_anthropic_protocol.py tests/test_protocol_factory.py -q` 通过。

## T22: 更新 README 和文档测试

**文件：** `README.md`、`tests/test_docs.py`

**依赖：** T18、T20

**步骤：**
1. 修改 `tests/test_docs.py`，断言 README 包含 `Stage 03`、`Agent Loop`、`事件流`、`plan-only`、`工具分批`。
2. 修改 `tests/test_docs.py`，断言 README 说明本阶段不做复杂权限策略、Agent 递归调用、复杂 system prompt。
3. 运行 `python -m pytest tests/test_docs.py -q`，预期因 README 未更新而失败。
4. 更新 README 当前阶段说明为 Stage 03 Agent 模式。
5. 增加事件流、ReAct 循环、工具分批、`/plan-only` 命令和审批行为说明。
6. 增加当前阶段不做的能力清单。

**验证：** `python -m pytest tests/test_docs.py -q` 通过。

## T23: 全量回归与变更检查

**文件：** 全项目

**依赖：** T1 到 T22

**步骤：**
1. 运行 `python -m pytest -q`。
2. 对失败测试按对应任务文件定位并修复，然后重跑失败测试。
3. 全部通过后运行 `python -m pytest` 查看完整输出。
4. 运行 `git status --short`，确认 Stage 03 相关变更集中在 `src/mycode/agent`、工具分类适配、session/TUI/CLI、测试和 README。
5. 确认没有引入真实 API key、真实外部网络或真实终端输入依赖。

**验证：** `python -m pytest` 全部通过，`git status --short` 仅包含预期变更和用户已有未提交项。

## 执行顺序

```text
T1 -> T2
T1 -> T3 -> T4 -> T5 -> T6
T3 + T5 + T6 -> T7 -> T8 -> T9 -> T10 -> T11 -> T12 -> T13 -> T14 -> T15
T7 + T13 -> T16 -> T17 -> T18 -> T19 -> T20
T1 + T2 + T20 -> T21
T18 + T20 -> T22
T1..T22 -> T23
```

## 自查

- `plan.md` 中的每个组件都有任务覆盖：`agent` 事件、配置、状态、审批、拦截器、调度器、历史 helper、主循环、工具分类、session、TUI、CLI、测试和 README。
- 每个任务都有明确文件、依赖、步骤和验证命令。
- 任务顺序先建立底层类型，再实现 Agent Loop，再接入 session/TUI/CLI，最后做端到端和文档回归。
- `plan-only` 的批准、拒绝、取消三条路径都有独立任务和验证。
- 工具读写分类、分批执行、最大轮数、取消、超时、错误事件都有测试任务覆盖。
- 没有任务引入复杂权限策略、Agent 递归调用、索引/RAG、Anthropic 工具调用或复杂 TUI 面板。
