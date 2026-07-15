# myCode 阶段 03：Agent Loop 与事件流验收清单

> 每一项通过运行测试、观察事件序列或检查可机读结果来验证，聚焦系统行为。

## 实现完整性

- [ ] Agent Loop 主入口已实现，用户请求能进入独立 Agent 层而不是由 TUI / CLI 直接驱动 LLM 或工具。（验证：运行 `python -m pytest tests/test_agent_loop.py::test_agent_loop_streams_text_and_final_response -q`，期望通过）
- [ ] Agent 相关实现集中在 `src/mycode/agent` 包下，并由包入口导出公开类型。（验证：运行 `python -m pytest tests/test_agent_events.py -q`，期望导入和类型契约测试通过）
- [ ] Agent 事件契约覆盖用户消息、thinking、模型文本、工具开始、工具结果、最终回复、错误、取消和等待审批。（验证：运行 `python -m pytest tests/test_agent_events.py -q`，期望事件类型断言通过）
- [ ] 错误事件包含机器可读错误类别和人类可读说明。（验证：运行 `python -m pytest tests/test_agent_loop.py::test_agent_loop_converts_llm_error_to_agent_error tests/test_agent_loop.py::test_agent_loop_errors_when_max_rounds_exceeded -q`，期望通过）
- [ ] 默认六个核心工具都显式声明读/写分类，且 OpenAI tool payload 不包含本地分类字段。（验证：运行 `python -m pytest tests/test_tool_registry.py tests/test_openai_responses_protocol.py tests/test_openai_chat_protocol.py -q`，期望通过）
- [ ] 工具分类缺失或非法时系统不猜测分类，而是产生明确失败。（验证：运行 `python -m pytest tests/test_agent_scheduler.py::test_build_tool_batches_rejects_invalid_tool_kind -q`，期望通过）
- [ ] 工具执行前后拦截点可替换，默认拦截器在 `plan-only` 下允许读工具并要求审批写工具。（验证：运行 `python -m pytest tests/test_agent_interceptor.py -q`，期望通过）
- [ ] Agent 历史写入 helper 能生成 user、assistant 文本、assistant 工具调用和 tool result 历史消息。（验证：运行 `python -m pytest tests/test_agent_loop.py::test_agent_history_helpers_create_expected_messages -q`，期望通过）

## Agent 行为

- [ ] 普通文本响应会流式产出文本事件、最终回复事件，并把 user / assistant 历史写入 memory。（验证：运行 `python -m pytest tests/test_agent_loop.py::test_agent_loop_streams_text_and_final_response -q`，期望通过）
- [ ] thinking 事件可被上层观察，但不会写入普通 assistant 历史。（验证：运行 `python -m pytest tests/test_agent_loop.py::test_agent_loop_streams_thinking_without_storing_it -q`，期望通过）
- [ ] 模型显式结束且没有工具调用时，Agent 正常终止，不额外请求 LLM。（验证：运行 `python -m pytest tests/test_agent_loop.py::test_agent_loop_finishes_when_model_done_without_tool_calls -q`，期望 LLM 调用次数为 1）
- [ ] 模型请求工具后，Agent 执行工具、回填工具结果，并继续下一轮 LLM 调用直到最终回复。（验证：运行 `python -m pytest tests/test_agent_loop.py::test_agent_loop_executes_tool_and_continues_to_final_response -q`，期望通过）
- [ ] 模型持续请求工具超过最大轮数时，Agent 产出 `max_rounds_exceeded` 错误事件并结束。（验证：运行 `python -m pytest tests/test_agent_loop.py::test_agent_loop_errors_when_max_rounds_exceeded -q`，期望通过）
- [ ] 一轮响应中出现多个工具调用时，连续读工具并发执行，写工具单独串行执行，写后的读工具进入后续批次。（验证：运行 `python -m pytest tests/test_agent_loop.py::test_agent_loop_batches_read_tools_and_serializes_writes -q`，期望通过）
- [ ] 并发读批中单个工具失败不会丢失其他已完成工具结果。（验证：运行 `python -m pytest tests/test_agent_loop.py::test_agent_loop_preserves_completed_read_results_when_one_read_fails -q`，期望每个完成结果都有事件和历史）
- [ ] 写工具失败不会触发自动回滚，失败结果会回填给模型供后续轮次处理。（验证：运行 `python -m pytest tests/test_agent_loop.py::test_agent_loop_records_failed_write_tool_result_and_continues -q`，期望通过）

## Plan-only 与审批

- [ ] `plan-only` 模式可以在会话内开启和关闭，开启后读工具仍可执行。（验证：运行 `python -m pytest tests/test_session.py::test_chat_session_toggles_plan_only tests/test_agent_plan_only.py::test_plan_only_allows_read_tools -q`，期望通过）
- [ ] `plan-only` 模式下写工具在审批前不会执行，也不会写入成功工具结果。（验证：运行 `python -m pytest tests/test_agent_plan_only.py::test_plan_only_write_tool_requires_approval_before_execution -q`，期望通过）
- [ ] 用户批准待审批写工具时，只放行当前工具一次，`plan-only` 状态保持开启。（验证：运行 `python -m pytest tests/test_agent_plan_only.py::test_plan_only_approval_approves_one_write_tool -q`，期望通过）
- [ ] 用户拒绝待审批写工具时，工具不执行，系统回填结构化拒绝结果并允许模型继续输出计划。（验证：运行 `python -m pytest tests/test_agent_plan_only.py::test_plan_only_rejects_write_tool_and_continues_with_rejection_result -q`，期望通过）
- [ ] 用户取消待审批写工具时，Agent 产出取消事件并结束本轮。（验证：运行 `python -m pytest tests/test_agent_plan_only.py::test_plan_only_cancel_stops_current_turn -q`，期望通过）
- [ ] TUI 能展示等待审批状态，并把 `y`、`n`、`c` 输入转换为批准一次、拒绝、取消。（验证：运行 `python -m pytest tests/test_tui.py::test_tui_approval_provider_accepts_yes tests/test_tui.py::test_tui_approval_provider_accepts_no tests/test_tui.py::test_tui_approval_provider_accepts_cancel -q`，期望通过）

## 取消与超时

- [ ] 外部取消到达时，Agent 产出取消事件并停止推进后续循环。（验证：运行 `python -m pytest tests/test_agent_loop.py::test_agent_loop_yields_cancelled_when_cancelled -q`，期望通过）
- [ ] 取消发生时，未完成或未审批的工具结果不会写入 memory。（验证：运行 `python -m pytest tests/test_agent_loop.py::test_agent_loop_does_not_store_unfinished_tool_result_after_cancel -q`，期望通过）
- [ ] 单次模型调用超时时，Agent 产出 `model_timeout` 错误事件。（验证：运行 `python -m pytest tests/test_agent_loop.py::test_agent_loop_reports_model_timeout -q`，期望通过）
- [ ] 整次 Agent 请求超时时，Agent 产出 `run_timeout` 错误事件。（验证：运行 `python -m pytest tests/test_agent_loop.py::test_agent_loop_reports_run_timeout -q`，期望通过）
- [ ] 工具执行超时仍作为结构化工具结果暴露，不导致会话崩溃。（验证：运行 `python -m pytest tests/test_agent_loop.py::test_agent_loop_surfaces_tool_timeout_result -q`，期望通过）

## 集成

- [ ] `ChatSession` 只作为 Agent 门面转发 `AgentEvent`，并向 Agent 传入当前模式和审批 provider。（验证：运行 `python -m pytest tests/test_session.py -q`，期望通过）
- [ ] `/clear` 清空普通历史、工具历史、暂停审批状态，并复位 `plan-only` 状态。（验证：运行 `python -m pytest tests/test_session.py::test_chat_session_clear_resets_memory_and_plan_only tests/test_e2e_chat.py::test_e2e_clear_removes_tool_history_before_next_request -q`，期望通过）
- [ ] TUI 只消费 Agent 事件展示过程，不直接依赖 LLM `StreamEventType`。（验证：运行 `python -m pytest tests/test_tui.py -q`，期望 AgentEvent 渲染测试通过）
- [ ] CLI 正确组装 LLM、memory、工具注册中心、工具执行器、AgentLoop 和 ChatSession。（验证：运行 `python -m pytest tests/test_cli.py -q`，期望通过）
- [ ] OpenAI Responses 工具调用历史和工具结果历史仍可转换为 provider 可理解的 input item。（验证：运行 `python -m pytest tests/test_openai_responses_protocol.py -q`，期望通过）
- [ ] OpenAI Chat 工具调用历史和工具结果历史仍可转换为 Chat Completions message。（验证：运行 `python -m pytest tests/test_openai_chat_protocol.py -q`，期望通过）
- [ ] Anthropic 协议保持纯对话行为，不接入工具调用。（验证：运行 `python -m pytest tests/test_anthropic_protocol.py tests/test_protocol_factory.py -q`，期望通过）

## 编译与测试

- [ ] Agent 相关单元测试全部通过。（验证：运行 `python -m pytest tests/test_agent_events.py tests/test_agent_scheduler.py tests/test_agent_interceptor.py tests/test_agent_loop.py tests/test_agent_plan_only.py -q`，期望通过）
- [ ] 工具系统回归测试全部通过。（验证：运行 `python -m pytest tests/test_tool_registry.py tests/test_tool_executor.py tests/test_tool_filesystem.py tests/test_tool_command.py tests/test_tool_cache.py -q`，期望通过）
- [ ] 协议层回归测试全部通过。（验证：运行 `python -m pytest tests/test_openai_responses_protocol.py tests/test_openai_chat_protocol.py tests/test_anthropic_protocol.py tests/test_protocol_factory.py tests/test_sse.py -q`，期望通过）
- [ ] 会话、TUI、CLI 和端到端测试全部通过。（验证：运行 `python -m pytest tests/test_session.py tests/test_tui.py tests/test_cli.py tests/test_e2e_chat.py -q`，期望通过）
- [ ] 全项目自动化测试通过，且不需要真实 API key、真实外部网络或真实终端输入。（验证：运行 `python -m pytest`，期望全部通过）

## 文档与边界

- [ ] README 说明 Stage 03 支持 Agent Loop、事件流、工具分批执行、`plan-only` 审批、取消与超时边界。（验证：运行 `python -m pytest tests/test_docs.py -q`，期望文档断言通过）
- [ ] README 明确本阶段不做复杂 system prompt、完整权限策略、Agent 递归调用、索引/RAG、复杂 TUI 面板和 Anthropic 工具调用。（验证：运行 `python -m pytest tests/test_docs.py -q`，期望文档断言通过）
- [ ] 变更集中在 Stage 03 相关代码、测试和文档，没有引入无关重构。（验证：运行 `git status --short` 并检查变更文件列表）

## 端到端场景

- [ ] 场景 1：用户发送普通问题，mocked LLM 返回文本流；TUI 显示文本，Agent 产出最终回复，下一轮请求携带上一轮 user / assistant 历史。（验证：运行 `python -m pytest tests/test_e2e_chat.py::test_e2e_cli_tui_agent_memory_streams_and_sends_previous_context -q`，期望通过）
- [ ] 场景 2：用户要求读取文件，mocked LLM 第一轮请求 `read_file`，Agent 执行工具并自动进入第二轮，mocked LLM 基于工具结果输出最终文本。（验证：运行 `python -m pytest tests/test_e2e_chat.py::test_e2e_agent_loop_reads_file_and_returns_final_response -q`，期望通过）
- [ ] 场景 3：用户开启 `plan-only` 后请求修改文件，mocked LLM 请求 `edit_file`；TUI 展示审批，用户拒绝后文件不变，模型下一轮输出计划。（验证：运行 `python -m pytest tests/test_e2e_chat.py::test_e2e_plan_only_rejects_write_and_returns_plan -q`，期望通过）
- [ ] 场景 4：用户开启 `plan-only` 后批准当前写工具一次；该写工具执行成功，后续写工具再次触发审批。（验证：运行 `python -m pytest tests/test_e2e_chat.py::test_e2e_plan_only_approves_one_write_only -q`，期望通过）
- [ ] 场景 5：模型按顺序请求两个读工具、一个写工具、一个读工具；事件流显示前两个读工具同批完成，写工具串行执行，最后一个读工具在写工具后执行。（验证：运行 `python -m pytest tests/test_e2e_chat.py::test_e2e_tool_batches_preserve_model_order -q`，期望通过）
- [ ] 场景 6：用户执行 `/clear` 后继续提问；下一轮 LLM 请求不包含清空前的普通历史、工具历史或 `plan-only` 状态。（验证：运行 `python -m pytest tests/test_e2e_chat.py::test_e2e_clear_resets_history_and_plan_only -q`，期望通过）
