# myCode Stage 04：Prompt Pipeline 与运行时上下文验收清单

> 每一项通过运行测试、检查结构化消息或观察可机读输出验证，聚焦系统行为。除标注为人工观察的项目外，所有自动化验证均不得依赖真实网络、真实 API key 或真实终端输入。

## 实现完整性

- [ ] 默认提示词注册表包含 `safety-boundaries`、`identity`、`behavior`、`tool-usage`、`coding-standards`、`output-style` 六个模块，按优先级 100 至 600 确定性排序。（验证：运行 `python -m pytest tests/test_prompt_registry.py -q`，期望内置模块、优先级和排序断言通过）
- [ ] 自定义模块可注册、启用、禁用和显式 override；重复 ID、未知 ID 和非法提示词配置返回明确错误。（验证：运行 `python -m pytest tests/test_prompt_registry.py -q`，期望注册表异常与状态测试通过）
- [ ] `safety-boundaries` 受保护，不能被禁用或 override。（验证：运行 `python -m pytest tests/test_prompt_registry.py -q`，期望受保护模块测试通过）
- [ ] 相同模块配置和相同工具集合生成逐字节一致的稳定 system 文本、稳定 SHA-256 和按名称排序的工具定义。（验证：运行 `python -m pytest tests/test_prompt_builder.py tests/test_tool_registry.py -q`，期望 metadata 和工具排序断言通过）
- [ ] `ChatMessage` 的消息来源能区分普通对话、稳定 system、system reminder 和环境上下文，且追加字段不破坏既有工具调用位置参数。（验证：运行 `python -m pytest tests/test_llm_base.py -q`，期望来源和向后兼容断言通过）
- [ ] `UsageObservation` 能表达 provider、请求 ID、token 与缓存 token；缺失、无效或不支持的字段均以 `None` 表示 `unknown`，不被解释为缓存未命中。（验证：运行 `python -m pytest tests/test_llm_base.py tests/test_openai_chat_protocol.py tests/test_openai_responses_protocol.py tests/test_anthropic_protocol.py -q`，期望 unknown 映射断言通过）
- [ ] `PromptConfig` 默认完整提醒间隔为 4，并拒绝非正的提醒周期、环境字段长度和 Git 超时。（验证：运行 `python -m pytest tests/test_prompt_registry.py -q`，期望配置校验通过）
- [ ] 复杂实现逻辑均有简洁中文注释，覆盖受保护模块、XML 转义/截断、turn 快照复用、提醒周期、memory 边界、工具排序、usage/cache 映射和 Anthropic 顶层 system 转换。（验证：检查 `src/mycode/prompt/`、`src/mycode/agent/loop.py`、`src/mycode/tool/registry.py` 和三个协议文件；运行 `rg -n "#.*[一-龥]" src/mycode/prompt src/mycode/agent/loop.py src/mycode/tool/registry.py src/mycode/protocols`，并人工确认注释解释原因而不重复显而易见代码）

## Prompt 构建与环境上下文

- [ ] 每个用户 turn 只采集一次环境快照；同一 turn 的多个 model round 复用同一快照，新的 turn 重新采集。（验证：运行 `python -m pytest tests/test_prompt_builder.py tests/test_agent_loop.py -q`，期望采集次数和多轮消息内容断言通过）
- [ ] 请求中的原始用户消息、`<system-reminder>` 和 `<environment-context>` 是三条独立 user-role 消息；原始用户文本不被改写、拼接或重复发送。（验证：运行 `python -m pytest tests/test_prompt_builder.py tests/test_agent_loop.py -q`，期望消息顺序、角色和内容断言通过）
- [ ] 临时 reminder 和 environment 消息不写入 conversation memory；assistant 文本、工具调用和工具结果历史仍按既有规则持久化。（验证：运行 `python -m pytest tests/test_prompt_builder.py tests/test_agent_loop.py tests/test_e2e_chat.py -q`，期望 memory 边界和工具历史断言通过）
- [ ] `plan-only` 关闭时不发送模式提醒；开启时第 1、5、9 轮为完整提醒，中间轮为精简提醒；自定义正整数周期按相同规则工作。（验证：运行 `python -m pytest tests/test_prompt_reminder.py -q`，期望默认和自定义周期断言通过）
- [ ] 多个可信提醒以稳定 ID 顺序合并为一条 `<system-reminder>`，提醒内容经过 XML 转义；外部 Git、文件名和工具数据不会被提升为可信提醒。（验证：运行 `python -m pytest tests/test_prompt_reminder.py tests/test_prompt_builder.py -q`，期望标签、转义和来源边界断言通过）
- [ ] 环境消息固定包含工作区、操作系统、时间、时区、Git 分支和 Git 简要状态；不包含环境变量值、API key、完整 diff、文件内容或工具结果。（验证：运行 `python -m pytest tests/test_prompt_environment.py -q`，期望字段白名单和敏感数据排除断言通过）
- [ ] 环境值在 XML 转义后受长度限制；被截断的字段带诊断，包含标签样式字符的值不能逃逸 `<environment-context>`。（验证：运行 `python -m pytest tests/test_prompt_environment.py -q`，期望截断、诊断和 XML 转义断言通过）
- [ ] Git 不可用、不是 Git 仓库或 Git 命令超时时，环境字段为 unknown 并保留诊断，提示词构建和 Agent 请求继续执行。（验证：运行 `python -m pytest tests/test_prompt_environment.py tests/test_prompt_builder.py tests/test_agent_loop.py -q`，期望降级路径测试通过）
- [ ] 非受保护模块渲染失败时仅跳过该模块并记录诊断；受保护模块渲染失败时不发送 LLM 请求并产生 `PROMPT_ERROR`。（验证：运行 `python -m pytest tests/test_prompt_builder.py tests/test_agent_loop.py tests/test_agent_events.py -q`，期望失败策略和错误事件断言通过）

## Agent、工具与会话集成

- [ ] `AgentConfig` 使用 `PromptConfig`，不再依赖 `minimal_system_prompt`；默认 Agent 和 CLI 通过 PromptBuilder 工厂装配提示词依赖。（验证：运行 `python -m pytest tests/test_agent_events.py tests/test_cli.py -q`，期望配置和装配测试通过）
- [ ] 每个 model round 的完成事件携带 usage 时，Agent 先发出带正确 round index 的 `USAGE` 事件，再继续既有工具调度或最终回复路径。（验证：运行 `python -m pytest tests/test_agent_loop.py tests/test_agent_events.py -q`，期望 usage 事件顺序和内容断言通过）
- [ ] Prompt 配置或受保护模块构建失败时，Agent 产生机器可读的 `prompt_error`，不调用 LLM，也不新增临时 memory 历史。（验证：运行 `python -m pytest tests/test_agent_loop.py tests/test_agent_events.py -q`，期望错误路径断言通过）
- [ ] 工具定义在注册表层按工具名稳定排序；工具实例查找、读写分批、审批和执行顺序维持 Stage 03 行为。（验证：运行 `python -m pytest tests/test_tool_registry.py tests/test_agent_scheduler.py tests/test_agent_loop.py tests/test_agent_plan_only.py -q`，期望工具排序和既有调度测试通过）
- [ ] `/clear` 清空普通历史、工具历史和 `plan-only` 状态，但不重置模块注册表或 Builder 配置；下一 turn 生成新环境上下文。（验证：运行 `python -m pytest tests/test_session.py tests/test_e2e_chat.py -q`，期望 clear 和新 turn 快照断言通过）
- [ ] TUI 和 ChatSession 继续只消费 AgentEvent；新增 `USAGE` 不改变文本输出、`/plan-only`、审批或输入循环。（验证：运行 `python -m pytest tests/test_session.py tests/test_tui.py tests/test_e2e_chat.py -q`，期望会话与 TUI 回归通过）

## 协议与 usage 观测

- [ ] OpenAI Chat 在 `usage.request_stream_usage=false` 时不发送 `stream_options`，开启时发送 `{"include_usage": true}`；独立 user 消息保持独立，内部 `origin` 不进入 payload。（验证：运行 `python -m pytest tests/test_openai_chat_protocol.py -q`，期望请求 payload 断言通过）
- [ ] OpenAI Chat 将 `prompt_tokens`、`completion_tokens`、`total_tokens` 和 `prompt_tokens_details.cached_tokens` 映射为统一观测，并从 `x-request-id` 或 `request-id` 读取请求 ID。（验证：运行 `python -m pytest tests/test_openai_chat_protocol.py -q`，期望 usage 和请求 ID fixture 通过）
- [ ] OpenAI Responses 保持独立 input 消息和工具调用历史语义，将 `input_tokens`、`output_tokens`、`total_tokens` 和 `input_tokens_details.cached_tokens` 映射为统一观测。（验证：运行 `python -m pytest tests/test_openai_responses_protocol.py -q`，期望独立消息、工具历史和 usage fixture 通过）
- [ ] Anthropic 将内部 system 消息按顺序合并到请求顶层 `system`，不保留在 `messages`；原始 user、reminder 和环境消息仍保持独立。（验证：运行 `python -m pytest tests/test_anthropic_protocol.py -q`，期望顶层 system 和消息顺序断言通过）
- [ ] Anthropic 将 `input_tokens`、`output_tokens`、`cache_read_input_tokens` 和 `cache_creation_input_tokens` 映射为统一观测，并保持现有 thinking 流式行为。（验证：运行 `python -m pytest tests/test_anthropic_protocol.py -q`，期望 usage、请求 ID 和 thinking 回归通过）
- [ ] 三个协议面对缺失、负数、布尔值、字符串或嵌套字段缺失的 usage/cache 数据时，均正常完成流并将受影响字段记为 unknown。（验证：运行 `python -m pytest tests/test_openai_chat_protocol.py tests/test_openai_responses_protocol.py tests/test_anthropic_protocol.py -q`，期望异常 usage fixture 通过）

## 测试与文档

- [ ] Prompt 包单元测试全部通过。（验证：运行 `python -m pytest tests/test_prompt_registry.py tests/test_prompt_environment.py tests/test_prompt_reminder.py tests/test_prompt_builder.py -q`，期望全部通过）
- [ ] LLM、Agent、工具、配置、协议、会话、TUI、CLI 和端到端回归测试全部通过，且不访问真实网络。（验证：运行 `python -m pytest tests/test_llm_base.py tests/test_agent_events.py tests/test_agent_scheduler.py tests/test_agent_interceptor.py tests/test_agent_loop.py tests/test_agent_plan_only.py tests/test_tool_registry.py tests/test_tool_executor.py tests/test_tool_filesystem.py tests/test_tool_command.py tests/test_tool_cache.py tests/test_config.py tests/test_cli.py tests/test_openai_chat_protocol.py tests/test_openai_responses_protocol.py tests/test_anthropic_protocol.py tests/test_protocol_factory.py tests/test_sse.py tests/test_session.py tests/test_tui.py tests/test_e2e_chat.py -q`，期望全部通过）
- [ ] README 说明 Stage 04 的稳定模块、独立运行时消息、每 turn 环境快照、usage 配置、unknown 语义和不保证缓存命中的边界，同时保留 Stage 03 的工具、审批、取消和超时说明。（验证：运行 `python -m pytest tests/test_docs.py -q`，期望 README 关键词断言通过）
- [ ] 三份示例配置均使用环境变量引用，不含 `sk-` 前缀的字面 API key。（验证：运行 `python -m pytest tests/test_docs.py::test_example_configs_exist_and_use_environment_variables -q`，期望通过；若当前用户已有示例配置改动导致失败，记录为工作区前置问题，不在 Stage 04 内覆盖该改动）
- [ ] 全项目自动化测试通过。（验证：在示例配置安全断言满足后运行 `python -m pytest -q`，期望全部通过）

## 端到端场景

- [ ] 场景 1：用户发送普通问题，mocked LLM 返回文本和 usage；请求包含稳定 system、原始 user、环境消息，Agent 先产出 `USAGE` 再产出最终回复，memory 只保留普通 user/assistant 历史。（验证：运行 `python -m pytest tests/test_e2e_chat.py -q`，期望普通文本和 usage 场景通过）
- [ ] 场景 2：用户请求读取文件，mocked LLM 第一轮调用 `read_file`，第二轮基于工具结果返回文本；两轮环境消息完全一致、工具历史存在于第二轮请求、临时消息不进入 memory。（验证：运行 `python -m pytest tests/test_e2e_chat.py -q`，期望多轮工具快照复用场景通过）
- [ ] 场景 3：用户开启 `plan-only` 后请求修改文件；完整模式提醒作为独立消息发送，读工具仍执行，写工具等待审批；拒绝后文件不变且模型继续输出计划。（验证：运行 `python -m pytest tests/test_e2e_chat.py tests/test_agent_plan_only.py -q`，期望 plan-only 回归和提醒场景通过）
- [ ] 场景 4：同一 turn 进入多轮后，默认提醒从第 1 轮完整文本切换为第 2 至第 4 轮精简文本；第 5 轮再次完整；新的 user turn 创建新的环境快照。（验证：运行 `python -m pytest tests/test_prompt_reminder.py tests/test_agent_loop.py tests/test_e2e_chat.py -q`，期望周期和 turn 生命周期场景通过）
- [ ] 场景 5：用户执行 `/clear` 后继续提问；新请求不包含清空前的普通历史、工具历史、模式状态或临时消息，且产生新的环境上下文。（验证：运行 `python -m pytest tests/test_e2e_chat.py tests/test_session.py -q`，期望 clear 场景通过）
- [ ] 场景 6：协议 fixture 返回有缓存字段、无缓存字段和格式错误缓存字段；Agent 事件中可分别观察到数值或 unknown，任何一种情况都不把请求中断。（验证：运行 `python -m pytest tests/test_openai_chat_protocol.py tests/test_openai_responses_protocol.py tests/test_anthropic_protocol.py tests/test_agent_loop.py -q`，期望三协议和 Agent 观测场景通过）

## 人工缓存观测

- [ ] 对两次拥有相同模块配置和工具集合的请求，记录稳定 system 文本 SHA-256 与工具定义顺序；两次结果必须相同。（验证：使用 PromptBuilder 的测试输出或调试日志对比，期望稳定前缀元数据一致）
- [ ] 对同一 turn 的多 round 请求和连续两个不同 turn 请求，比较环境消息；同一 turn 内相同，跨 turn 可以变化。（验证：查看 fake LLM 捕获请求或 debug 日志，期望满足快照生命周期）
- [ ] 在支持 usage 的供应商配置中显式开启 `usage.request_stream_usage: true`，记录每个 `USAGE` 的 provider、请求 ID、输入 token 和缓存读取 token。（验证：运行一次受控真实请求，期望字段有值或显示 unknown；unknown 不得被记录为缓存未命中）
- [ ] 手工检查日志、测试 fixture 和用户可见输出，不含 API key、完整用户文本、完整环境变量、完整 diff 或完整工具结果。（验证：搜索运行日志和测试输出，期望不存在敏感值）
