# myCode 阶段 02：工具系统验收清单

> 每一项通过运行测试、观察输出或检查可机读结果来验证，聚焦系统行为。

## 工具基础设施

- [ ] 统一工具接口已实现，六个核心工具都暴露名称、描述、参数 Schema 和执行入口。（验证：运行 `python -m pytest tests/test_tool_registry.py tests/test_tool_filesystem.py tests/test_tool_command.py -q`，期望全部通过）
- [ ] 工具相关类型和实现都位于 `src/mycode/tool` 包下，对外可从 `mycode.tool` 导入公开入口。（验证：运行 `python -m pytest tests/test_tool_registry.py -q`，期望导入测试通过）
- [ ] 工具注册中心能按名称查找工具，并拒绝重复名称。（验证：运行 `python -m pytest tests/test_tool_registry.py -q`，期望注册、查找和重复名称测试通过）
- [ ] 注册中心能生成 OpenAI Responses 和 OpenAI Chat 可复用的 function tool spec。（验证：运行 `python -m pytest tests/test_tool_registry.py -q`，期望 payload 结构断言通过）
- [ ] 工具执行器能把未知工具、非法 JSON 参数、工具异常和工具超时包装成结构化 `ToolResult`。（验证：运行 `python -m pytest tests/test_tool_executor.py -q`，期望全部通过）

## 文件与代码工具

- [ ] 读文件工具能读取工作目录内 UTF-8 文本，并拒绝工作目录外路径。（验证：运行 `python -m pytest tests/test_tool_filesystem.py::test_read_file_tool_reads_workspace_text tests/test_tool_filesystem.py::test_read_file_tool_rejects_path_outside_workspace -q`，期望通过）
- [ ] 写文件工具能写入工作目录内 UTF-8 文本并创建父目录，同时拒绝工作目录外路径。（验证：运行 `python -m pytest tests/test_tool_filesystem.py::test_write_file_tool_writes_text_and_creates_parent tests/test_tool_filesystem.py::test_write_file_tool_rejects_path_outside_workspace -q`，期望通过）
- [ ] 改文件工具只在原文出现一次时替换成功，零匹配和多匹配都返回明确错误且不改动文件。（验证：运行 `python -m pytest tests/test_tool_filesystem.py::test_edit_file_tool_replaces_unique_text tests/test_tool_filesystem.py::test_edit_file_tool_reports_zero_matches tests/test_tool_filesystem.py::test_edit_file_tool_reports_multiple_matches_without_writing -q`，期望通过）
- [ ] 读文件、写文件和改文件共用同一个带锁文本缓存；写入或改写后再次读取返回最新内容。（验证：运行 `python -m pytest tests/test_tool_cache.py -q`，期望缓存命中和缓存更新测试通过）
- [ ] 同一路径并发读写或改写时，缓存内容和磁盘内容保持一致，不使用模块级可变全局状态串扰请求。（验证：运行 `python -m pytest tests/test_tool_cache.py::test_file_text_cache_keeps_disk_and_cache_consistent_under_concurrent_access -q`，期望通过）
- [ ] 按模式找文件工具只返回工作目录内匹配文件。（验证：运行 `python -m pytest tests/test_tool_filesystem.py::test_find_files_tool_returns_matching_relative_paths tests/test_tool_filesystem.py::test_find_files_tool_rejects_root_outside_workspace -q`，期望通过）
- [ ] 搜代码内容工具返回文件路径、行号和行内容，并跳过非 UTF-8 文件而不崩溃。（验证：运行 `python -m pytest tests/test_tool_filesystem.py::test_search_code_tool_returns_matching_lines tests/test_tool_filesystem.py::test_search_code_tool_skips_non_utf8_files -q`，期望通过）

## 命令工具

- [ ] 执行命令工具在工作目录下运行命令，并返回退出码、stdout、stderr 和 timed_out 字段。（验证：运行 `python -m pytest tests/test_tool_command.py::test_run_command_tool_returns_stdout_and_exit_code tests/test_tool_command.py::test_run_command_tool_returns_stderr_and_nonzero_exit_code tests/test_tool_command.py::test_run_command_tool_runs_in_workspace_root -q`，期望通过）
- [ ] 执行命令工具超时时返回结构化超时结果，不让会话崩溃。（验证：运行 `python -m pytest tests/test_tool_command.py::test_run_command_tool_returns_timeout_result -q`，期望通过）

## OpenAI 协议接入

- [ ] OpenAI Responses 请求在传入工具定义时包含 `tools` 和 `parallel_tool_calls: False`，未传工具时保持纯文本请求兼容。（验证：运行 `python -m pytest tests/test_openai_responses_protocol.py::test_openai_responses_includes_tools_when_provided tests/test_openai_responses_protocol.py::test_openai_responses_omits_tools_when_none -q`，期望通过）
- [ ] OpenAI Responses 能把工具调用历史和工具结果历史转换为 Responses API 可理解的 input item。（验证：运行 `python -m pytest tests/test_openai_responses_protocol.py::test_openai_responses_serializes_tool_call_history tests/test_openai_responses_protocol.py::test_openai_responses_serializes_tool_result_history -q`，期望通过）
- [ ] OpenAI Responses 流式解析能拼接拆碎的 function call arguments 并产出内部 `TOOL_CALL` 事件。（验证：运行 `python -m pytest tests/test_openai_responses_protocol.py::test_openai_responses_streams_function_call_arguments_as_tool_call -q`，期望通过）
- [ ] OpenAI Responses 遇到非法 JSON arguments 时产出带 raw arguments 的工具调用事件，后续由执行器包装失败结果。（验证：运行 `python -m pytest tests/test_openai_responses_protocol.py::test_openai_responses_preserves_invalid_function_arguments -q`，期望通过）
- [ ] OpenAI Chat 请求在传入工具定义时包含 `tools` 和 `parallel_tool_calls: False`，未传工具时保持纯文本请求兼容。（验证：运行 `python -m pytest tests/test_openai_chat_protocol.py::test_openai_chat_includes_tools_when_provided tests/test_openai_chat_protocol.py::test_openai_chat_omits_tools_when_none -q`，期望通过）
- [ ] OpenAI Chat 能把工具调用历史和工具结果历史转换为 Chat Completions 可理解的 message。（验证：运行 `python -m pytest tests/test_openai_chat_protocol.py::test_openai_chat_serializes_tool_call_history tests/test_openai_chat_protocol.py::test_openai_chat_serializes_tool_result_history -q`，期望通过）
- [ ] OpenAI Chat 流式解析能拼接 `tool_calls[].function.arguments` 碎片并产出内部 `TOOL_CALL` 事件。（验证：运行 `python -m pytest tests/test_openai_chat_protocol.py::test_openai_chat_streams_tool_call_arguments_as_tool_call -q`，期望通过）
- [ ] OpenAI Chat 遇到非法 JSON arguments 时产出带 raw arguments 的工具调用事件，后续由执行器包装失败结果。（验证：运行 `python -m pytest tests/test_openai_chat_protocol.py::test_openai_chat_preserves_invalid_tool_call_arguments -q`，期望通过）

## 会话与 TUI 集成

- [ ] 模型不调用工具时，纯文本流式聊天、多轮记忆和 thinking 不进入普通历史的 Stage 01 行为保持可用。（验证：运行 `python -m pytest tests/test_session.py::test_chat_session_appends_user_and_assistant_after_success tests/test_session.py::test_chat_session_sends_previous_turns_in_next_request tests/test_session.py::test_chat_session_does_not_store_thinking_as_assistant_text -q`，期望通过）
- [ ] 会话层收到工具调用后只执行一次工具，写入工具调用历史和工具结果历史，并且不自动第二次请求 LLM。（验证：运行 `python -m pytest tests/test_session.py::test_chat_session_executes_tool_call_once_and_stores_tool_history -q`，期望通过）
- [ ] 工具执行成功时 TUI 输出简短可读状态。（验证：运行 `python -m pytest tests/test_tui.py::test_tui_prints_successful_tool_result -q`，期望输出断言通过）
- [ ] 工具执行失败时 TUI 输出工具名和错误信息。（验证：运行 `python -m pytest tests/test_tui.py::test_tui_prints_failed_tool_result -q`，期望输出断言通过）
- [ ] CLI 主流程默认创建工具注册中心和执行器，mocked 端到端流程能执行工具并让下一轮请求看到工具结果历史。（验证：运行 `python -m pytest tests/test_e2e_chat.py::test_e2e_tool_call_result_is_stored_for_next_request -q`，期望通过）
- [ ] `/clear` 能清空普通历史和工具历史。（验证：运行 `python -m pytest tests/test_e2e_chat.py::test_e2e_clear_removes_tool_history_before_next_request -q`，期望通过）

## 文档与限制

- [ ] README 说明 Stage 02 支持 OpenAI 系列单轮工具调用和六个核心工具。（验证：运行 `python -m pytest tests/test_docs.py -q`，期望通过）
- [ ] README 明确本阶段不做 Agent Loop、多工具连环调用和 Anthropic 工具调用。（验证：运行 `python -m pytest tests/test_docs.py -q`，期望通过）
- [ ] Anthropic 协议仍保持纯对话行为，不接入工具调用。（验证：运行 `python -m pytest tests/test_anthropic_protocol.py tests/test_protocol_factory.py -q`，期望通过）

## 全量验收

- [ ] 全部自动化测试通过。（验证：运行 `python -m pytest`，期望全部通过）
- [ ] 测试不依赖真实 API key 或真实外部网络。（验证：运行 `python -m pytest` 时不设置 OpenAI/Anthropic 环境变量，期望全部通过）
- [ ] 变更集中在 Stage 02 相关代码、测试和文档，没有引入持久化索引、多 agent 或自动循环能力。（验证：运行 `git status --short` 并检查变更文件列表）

## 端到端场景

- [ ] 场景 1：用户询问需要读文件的问题，mocked OpenAI 模型发起 `read_file` 工具调用，myCode 执行工具、TUI 显示工具已执行、memory 写入工具结果，本轮结束。（验证：运行 `python -m pytest tests/test_e2e_chat.py::test_e2e_tool_call_result_is_stored_for_next_request -q`，期望通过）
- [ ] 场景 2：用户要求修改文件，mocked OpenAI 模型发起 `edit_file` 工具调用；当原文匹配多次时，工具返回结构化失败结果，TUI 显示失败，程序回到可继续输入状态。（验证：运行 `python -m pytest tests/test_e2e_chat.py::test_e2e_failed_edit_tool_call_returns_structured_error_and_continues -q`，期望通过）
- [ ] 场景 3：用户连续对话，第一轮执行工具后不自动循环，第二轮用户继续输入时，OpenAI 请求包含上一轮工具调用和工具结果历史。（验证：运行 `python -m pytest tests/test_e2e_chat.py::test_e2e_next_turn_sends_previous_tool_history_to_llm -q`，期望通过）
