# myCode Stage 04：Prompt Pipeline 与运行时上下文任务拆解

## 前置约束

- 实现开始前必须重新阅读 `doc/stage-04-prompt-context/spec.md` 和 `doc/stage-04-prompt-context/plan.md`。
- 当前工作区中的 `examples/mycode.openai-chat.yaml` 和 `examples/mycode.openai-responses.yaml` 已有用户改动。Stage 04 任务不得修改、暂存、还原或提交这两个文件。
- 每次提交使用 `git commit --only -- <本任务路径>`，防止把用户已有改动带入提交。
- 所有新增或修改行为先写失败测试，再实现最小代码，再运行对应测试。测试仅使用 fake LLM、mock HTTP 和 fixture，不访问真实网络或 API key。
- 所有新增或修改的复杂实现逻辑必须配套简洁的**中文注释**；注释说明设计意图、协议差异、安全边界或生命周期原因，不重复描述显而易见的赋值和分支。
- 本阶段至少为以下位置添加中文注释：稳定模块的保护规则、XML 转义与截断、每 turn 环境快照复用、完整/精简提醒周期、临时消息不写入 memory、工具定义稳定排序、三种协议的 usage/cache 字段映射，以及 Anthropic 顶层 system 转换。

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `src/mycode/prompt/__init__.py` | Prompt 包公共导出和默认构建器工厂 |
| 新建 | `src/mycode/prompt/models.py` | 提示词配置、模块、上下文、快照、构建结果和诊断类型 |
| 新建 | `src/mycode/prompt/registry.py` | 模块注册、启用/禁用、覆盖和错误类型 |
| 新建 | `src/mycode/prompt/modules.py` | 六个稳定内置模块和静态模块实现 |
| 新建 | `src/mycode/prompt/environment.py` | 环境/Git 快照、截断和 XML 格式化 |
| 新建 | `src/mycode/prompt/reminder.py` | 完整/精简提醒与周期策略 |
| 新建 | `src/mycode/prompt/builder.py` | turn 上下文和完整请求消息构建 |
| 修改 | `src/mycode/llm/base.py` | 消息来源和统一 usage 观测契约 |
| 修改 | `src/mycode/llm/__init__.py` | 导出新的 LLM 公共类型 |
| 修改 | `src/mycode/agent/config.py` | `AgentConfig` 接入 `PromptConfig` |
| 修改 | `src/mycode/agent/events.py` | `USAGE` 事件和 `PROMPT_ERROR` |
| 修改 | `src/mycode/agent/history.py` | system 和普通历史的消息来源标记 |
| 修改 | `src/mycode/agent/loop.py` | PromptBuilder 调用、turn 快照复用和 usage 转发 |
| 修改 | `src/mycode/agent/__init__.py` | 导出新增 Agent 公共类型 |
| 修改 | `src/mycode/tool/registry.py` | 工具定义按名称稳定排序 |
| 修改 | `src/mycode/config.py` | `UsageConfig` 及 YAML 配置解析 |
| 修改 | `src/mycode/cli.py` | 默认 PromptBuilder 装配 |
| 修改 | `src/mycode/protocols/openai_chat.py` | 可选 stream usage、请求 ID 和 usage 映射 |
| 修改 | `src/mycode/protocols/openai_responses.py` | Responses usage、请求 ID 和缓存字段映射 |
| 修改 | `src/mycode/protocols/anthropic.py` | 顶层 system、usage、请求 ID 和独立消息映射 |
| 新建 | `tests/test_prompt_registry.py` | 模块与注册表行为 |
| 新建 | `tests/test_prompt_environment.py` | 环境快照、降级、截断和 XML 转义 |
| 新建 | `tests/test_prompt_reminder.py` | 提醒周期和提醒内容 |
| 新建 | `tests/test_prompt_builder.py` | 消息顺序、metadata、失败策略和工具排序 |
| 修改 | `tests/test_llm_base.py` | 新增 LLM 契约 |
| 修改 | `tests/test_agent_events.py` | 新增事件和错误码契约 |
| 修改 | `tests/test_agent_loop.py` | PromptBuilder 集成、memory 边界和 usage 转发 |
| 修改 | `tests/test_tool_registry.py` | 工具定义稳定排序 |
| 修改 | `tests/test_config.py` | usage 配置加载与校验 |
| 修改 | `tests/test_openai_chat_protocol.py` | Chat usage、请求 ID 和独立消息 |
| 修改 | `tests/test_openai_responses_protocol.py` | Responses usage、请求 ID 和独立消息 |
| 修改 | `tests/test_anthropic_protocol.py` | 顶层 system、usage 和独立消息 |
| 修改 | `tests/test_e2e_chat.py` | 文本、工具、plan-only、环境变化和 `/clear` 回归 |
| 修改 | `README.md` | Stage 04 功能、usage 配置和非目标说明 |
| 修改 | `tests/test_docs.py` | README 的 Stage 04 文档断言 |

## T1：扩展 LLM 消息和 usage 契约

**文件：** `src/mycode/llm/base.py`、`src/mycode/llm/__init__.py`、`tests/test_llm_base.py`

**依赖：** 无

**步骤：**

1. 在 `tests/test_llm_base.py` 增加断言：`MessageOrigin` 包含 `conversation`、`system_instruction`、`system_reminder`、`environment_context`；`ChatMessage` 未指定来源时仍为 `CONVERSATION`。
2. 增加断言：以既有位置参数创建 `ChatMessage` 时，工具调用 ID、工具名和原始参数仍处于原字段位置，来源字段不改变旧调用语义。
3. 增加断言：`UsageObservation` 可保存 provider、请求 ID、输入/输出/总 token、缓存读/写 token；任意 token 字段为 `None` 时表示未知。
4. 增加断言：`StreamEvent(StreamEventType.DONE, usage=observation)` 保留 usage，既有文本和工具事件仍能按原方式构造。
5. 运行 `python -m pytest tests/test_llm_base.py -q`，预期因公共类型和字段尚不存在而失败。
6. 在 `src/mycode/llm/base.py` 新增 `MessageOrigin` 和 `UsageObservation`；在 `ChatMessage` 的既有位置参数字段之后追加默认 `origin`；在 `StreamEvent` 的既有字段之后追加可选 `usage`。
7. 在 `src/mycode/llm/__init__.py` 导出 `MessageOrigin` 和 `UsageObservation`，保持已导出的公共对象不变。
8. 运行 `python -m pytest tests/test_llm_base.py -q`，预期所有 LLM 基础契约测试通过。

**验证：** `python -m pytest tests/test_llm_base.py -q` 通过。

**提交：** `git commit --only -m "feat: add message origin and usage contract" -- src/mycode/llm/base.py src/mycode/llm/__init__.py tests/test_llm_base.py`

## T2：定义 Prompt 基础模型和模块注册表

**文件：** `src/mycode/prompt/models.py`、`src/mycode/prompt/registry.py`、`tests/test_prompt_registry.py`

**依赖：** T1

**步骤：**

1. 新建 `tests/test_prompt_registry.py`，定义可控的 fake `PromptModule`，测试 `PromptConfig()` 默认提醒周期为 4、环境值限制为 512、Git 超时为 1 秒。
2. 增加参数化测试：提醒周期、环境值限制或 Git 超时为零或负数时，`PromptConfig` 抛出明确的 `ValueError`。
3. 增加注册表测试：同一 ID 二次注册失败；未知 ID 的启用、禁用和 override 失败；默认启用状态和显式禁用状态正确。
4. 增加排序测试：已启用模块按 `(priority, id)` 排序，而非注册顺序。
5. 增加保护测试：`protected=True` 模块不能 disable 或 override；普通模块可显式 override，替换后保持该模块 ID。
6. 运行 `python -m pytest tests/test_prompt_registry.py -q`，预期因 prompt 包不存在而失败。
7. 新建 `models.py`，实现 `PromptConfig`、`PromptModuleDefinition`、`PromptModule`、`StablePromptContext`、`PromptDiagnostic`、`EnvironmentSnapshot`、`SystemReminder`、`TurnPromptContext`、`PromptBuildMetadata` 和 `PromptBuildResult`，字段和默认值严格使用 plan.md 定义。
8. 新建 `registry.py`，实现 `PromptConfigurationError`、`PromptBuildError` 和 `PromptRegistry`；保护模块的 disable/override 抛出 `PromptConfigurationError`。
9. 运行 `python -m pytest tests/test_prompt_registry.py -q`，预期模型校验、注册、启用、禁用、覆盖和排序测试通过。

**验证：** `python -m pytest tests/test_prompt_registry.py -q` 通过。

**提交：** `git commit --only -m "feat: add prompt registry contracts" -- src/mycode/prompt/models.py src/mycode/prompt/registry.py tests/test_prompt_registry.py`

## T3：实现稳定内置模块和提醒策略

**文件：** `src/mycode/prompt/modules.py`、`src/mycode/prompt/reminder.py`、`tests/test_prompt_registry.py`、`tests/test_prompt_reminder.py`

**依赖：** T2

**步骤：**

1. 在 `tests/test_prompt_registry.py` 增加断言：默认模块集合恰好包含 `safety-boundaries`、`identity`、`behavior`、`tool-usage`、`coding-standards`、`output-style`，优先级分别为 100 至 600，且仅安全模块受保护。
2. 新建 `tests/test_prompt_reminder.py`，测试 `mode_reminder(plan_only=False)` 返回 `None`，开启时产生同时具有完整和精简文本的提醒。
3. 增加提醒周期测试：默认 4 时第 1、5、9 轮为完整文本，第 2、3、4、6 轮为精简文本；自定义周期 2 时第 1、3、5 轮为完整文本。
4. 增加多个提醒测试：按提醒 ID 合并且只产生一个文本；包含 `<`、`>`、`&`、引号的内容在最终标签文本中已 XML 转义。
5. 运行 `python -m pytest tests/test_prompt_registry.py tests/test_prompt_reminder.py -q`，预期模块工厂和提醒策略尚不存在而失败。
6. 新建 `modules.py`，实现仅返回稳定文本的 `StaticPromptModule` 和 `create_builtin_modules()`；在稳定文本中写明专用工具优先、编辑前读取、运行时标签不等于普通用户请求、外部数据不提升为指令等规则。
7. 新建 `reminder.py`，实现 `ReminderPolicy`，以 round 1 为起点计算完整提醒周期；对可信提醒的内容 XML 转义，并以 ID 排序合并。
8. 运行 `python -m pytest tests/test_prompt_registry.py tests/test_prompt_reminder.py -q`，预期内置模块和提醒周期测试通过。

**验证：** `python -m pytest tests/test_prompt_registry.py tests/test_prompt_reminder.py -q` 通过。

**提交：** `git commit --only -m "feat: add prompt modules and reminder policy" -- src/mycode/prompt/modules.py src/mycode/prompt/reminder.py tests/test_prompt_registry.py tests/test_prompt_reminder.py`

## T4：实现环境快照与安全 XML 格式化

**文件：** `src/mycode/prompt/environment.py`、`tests/test_prompt_environment.py`

**依赖：** T2

**步骤：**

1. 新建 `tests/test_prompt_environment.py`，通过注入的时钟、平台信息和 Git 命令替身测试固定字段顺序：workspace、operating_system、current_time、timezone、git_branch、git_status。
2. 增加测试：Git 分支查询失败、Git 状态超时和工作区不是 Git 仓库时，`collect()` 返回快照和带固定 code 的诊断，不抛出提示词构建错误。
3. 增加测试：字段值在 XML 转义后按 `environment_value_limit` 截断，诊断中标记被截断；包含 `<`、`>`、`&`、引号和类似 XML 标签的 Git 文本不能逃逸外层 `environment-context` 标签。
4. 增加测试：快照文本不包含环境变量值、API key、完整 diff、文件内容和工具结果；仅包含设计指定的六个环境字段及诊断状态。
5. 运行 `python -m pytest tests/test_prompt_environment.py -q`，预期环境采集器不存在而失败。
6. 新建 `environment.py`，定义 `EnvironmentCollector` 协议和 `DefaultEnvironmentCollector`；使用 `subprocess.run()` 的参数列表、固定工作目录、`capture_output=True` 和配置的超时采集 Git 信息，不通过 shell 拼接命令。
7. 实现固定字段顺序、XML 转义、截断和诊断构造；采集失败返回 `None` 或 `unknown` 对应字段，不中断调用方。
8. 运行 `python -m pytest tests/test_prompt_environment.py -q`，预期快照、降级、截断和敏感信息边界测试通过。

**验证：** `python -m pytest tests/test_prompt_environment.py -q` 通过。

**提交：** `git commit --only -m "feat: add prompt environment snapshots" -- src/mycode/prompt/environment.py tests/test_prompt_environment.py`

## T5：实现 PromptBuilder 和默认工厂

**文件：** `src/mycode/prompt/builder.py`、`src/mycode/prompt/__init__.py`、`tests/test_prompt_builder.py`

**依赖：** T2、T3、T4

**步骤：**

1. 新建 `tests/test_prompt_builder.py`，使用 fake 环境采集器和 fake 模块测试：`begin_turn()` 对同一 turn 只调用一次采集器，后续多次 `build()` 使用完全相同的 `EnvironmentSnapshot`。
2. 增加消息顺序测试：输出恰为稳定 system、原始 history、独立 `<system-reminder>` user 消息、独立 `<environment-context>` user 消息；原始用户内容不包含任何运行时 XML，也不重复出现。
3. 增加 metadata 测试：已启用模块 ID 排序正确，稳定文本 SHA-256 对相同模块和工具输入稳定；工具定义按名称返回。
4. 增加失败策略测试：普通模块 render 抛异常时构建成功并产生诊断；受保护模块 render 抛异常时抛出 `PromptBuildError`；环境采集诊断不阻断构建。
5. 增加工厂测试：`create_default_prompt_builder(tmp_path)` 使用六个内置模块、默认环境采集器和默认周期。
6. 运行 `python -m pytest tests/test_prompt_builder.py -q`，预期构建器和工厂尚不存在而失败。
7. 新建 `builder.py`，按 plan.md 的 `begin_turn()` 和 `build()` 签名实现上下文创建、模块渲染、工具排序、独立运行时消息、metadata 与失败策略。
8. 新建 `prompt/__init__.py`，导出模型、错误类型、注册表、构建器和 `create_default_prompt_builder()`；工厂负责装配内置模块、默认环境采集器和提醒策略。
9. 运行 `python -m pytest tests/test_prompt_builder.py tests/test_prompt_registry.py tests/test_prompt_environment.py tests/test_prompt_reminder.py -q`，预期 Prompt 包单元测试通过。

**验证：** `python -m pytest tests/test_prompt_builder.py tests/test_prompt_registry.py tests/test_prompt_environment.py tests/test_prompt_reminder.py -q` 通过。

**提交：** `git commit --only -m "feat: build prompt request context" -- src/mycode/prompt/__init__.py src/mycode/prompt/builder.py tests/test_prompt_builder.py`

## T6：接入 Agent 配置、历史和事件契约

**文件：** `src/mycode/agent/config.py`、`src/mycode/agent/history.py`、`src/mycode/agent/events.py`、`src/mycode/agent/__init__.py`、`tests/test_agent_events.py`

**依赖：** T1、T5

**步骤：**

1. 在 `tests/test_agent_events.py` 更新公开事件序列断言，使其包含 `usage`；新增断言：`AgentErrorCode.PROMPT_ERROR.value == "prompt_error"`。
2. 增加测试：`AgentEvent(type=AgentEventType.USAGE, usage=UsageObservation(...))` 保留观测，既有审批、工具和错误字段不受影响。
3. 增加测试：`AgentConfig()` 默认包含 `PromptConfig()`，不再暴露或断言 `minimal_system_prompt`；`make_system_message()` 的来源为 `SYSTEM_INSTRUCTION`，普通 user、assistant、tool 历史来源为 `CONVERSATION`。
4. 运行 `python -m pytest tests/test_agent_events.py tests/test_agent_loop.py::test_agent_history_helpers_create_expected_messages -q`，预期新增事件、配置和来源字段断言失败。
5. 修改 `agent/config.py`，以 `field(default_factory=PromptConfig)` 接入 prompt 配置并移除最小提示词文本。
6. 修改 `agent/history.py`，为 system helper 显式设置 `MessageOrigin.SYSTEM_INSTRUCTION`，其余既有 helper 保留普通对话来源。
7. 修改 `agent/events.py`，增加 `USAGE`、`PROMPT_ERROR` 和 `usage` 字段；修改 `agent/__init__.py`，导出新增公共类型。
8. 运行 `python -m pytest tests/test_agent_events.py tests/test_agent_loop.py::test_agent_history_helpers_create_expected_messages -q`，预期事件和历史契约通过。

**验证：** `python -m pytest tests/test_agent_events.py tests/test_agent_loop.py::test_agent_history_helpers_create_expected_messages -q` 通过。

**提交：** `git commit --only -m "feat: add prompt agent contracts" -- src/mycode/agent/config.py src/mycode/agent/history.py src/mycode/agent/events.py src/mycode/agent/__init__.py tests/test_agent_events.py tests/test_agent_loop.py`

## T7：在 AgentLoop 中接入 PromptBuilder 并稳定工具定义顺序

**文件：** `src/mycode/agent/loop.py`、`src/mycode/tool/registry.py`、`tests/test_agent_loop.py`、`tests/test_tool_registry.py`

**依赖：** T5、T6

**步骤：**

1. 在 `tests/test_tool_registry.py` 增加测试：以乱序注册的工具从 `definitions()` 返回时按工具名升序排列，但 `get(name)` 仍返回原工具实例。
2. 在 `tests/test_agent_loop.py` 注入 fake PromptBuilder，增加普通文本测试：LLM 请求中系统消息、原始 user、reminder 和环境消息各出现一次；后两条消息不在 memory 中。
3. 增加多轮工具测试：同一 `run()` 的两次 LLM 请求使用同一个环境快照；第二次请求保留 assistant tool-call 和 tool result 历史，并使用下一 round 的 reminder 文本。
4. 增加 usage 测试：fake LLM 在每个 `DONE` 事件携带 `UsageObservation` 时，Agent 先产生 round 对应的 `USAGE`，随后继续工具或最终回复流程。
5. 增加错误测试：fake PromptBuilder 抛出 `PromptBuildError` 或 `PromptConfigurationError` 时，Agent 产生 `PROMPT_ERROR`、不调用 LLM，且不额外写入 memory。
6. 运行 `python -m pytest tests/test_tool_registry.py tests/test_agent_loop.py -q`，预期工具排序、提示词构建、usage 和错误路径测试失败。
7. 修改 `ToolRegistry.definitions()`，按 `ToolDefinition.name` 返回定义；不修改注册、查找和执行逻辑。
8. 修改 `AgentLoop.__init__()` 接受可选 PromptBuilder，未注入时通过当前工作目录和 `config.prompt` 创建默认构建器；增加单调递增 turn ID。
9. 修改 `AgentLoop.run()`：原始 user 写入 memory 后调用 `begin_turn()`；每个 round 用 `build()` 结果调用 LLM；处理带 usage 的完成事件并发出 `USAGE`；捕获 Prompt 错误并转换为 `PROMPT_ERROR`。
10. 运行 `python -m pytest tests/test_tool_registry.py tests/test_agent_loop.py tests/test_agent_events.py -q`，预期 Prompt 集成和既有 Agent 回归通过。

**验证：** `python -m pytest tests/test_tool_registry.py tests/test_agent_loop.py tests/test_agent_events.py -q` 通过。

**提交：** `git commit --only -m "feat: integrate prompt builder with agent loop" -- src/mycode/agent/loop.py src/mycode/tool/registry.py tests/test_agent_loop.py tests/test_tool_registry.py`

## T8：加载 usage 配置并在 CLI 装配 PromptBuilder

**文件：** `src/mycode/config.py`、`src/mycode/cli.py`、`tests/test_config.py`、`tests/test_cli.py`

**依赖：** T5、T7

**步骤：**

1. 在 `tests/test_config.py` 增加测试：缺少 `usage` 映射时 `request_stream_usage` 默认为 `False`；`usage.request_stream_usage: true` 能读取为 `True`。
2. 增加配置失败测试：`usage` 不是 YAML 映射，或 `request_stream_usage` 不是布尔值时抛出 `ConfigError`。
3. 在 `tests/test_cli.py` 增加装配测试：CLI 使用当前工作目录创建默认 PromptBuilder，并将其传入 AgentLoop；测试使用 monkeypatch 替换构建器工厂，不访问真实环境或网络。
4. 运行 `python -m pytest tests/test_config.py tests/test_cli.py -q`，预期 usage 配置和 PromptBuilder 装配测试失败。
5. 修改 `config.py`，定义 `UsageConfig`，将其作为 `LLMConfig` 默认字段，并实现 YAML 读取和严格类型校验。
6. 修改 `cli.py`，创建默认 PromptBuilder 后注入 `AgentLoop`；保持 CLI 的配置、工具注册表、memory、TUI 和错误处理装配顺序不变。
7. 运行 `python -m pytest tests/test_config.py tests/test_cli.py -q`，预期配置与 CLI 装配测试通过。

**验证：** `python -m pytest tests/test_config.py tests/test_cli.py -q` 通过。

**提交：** `git commit --only -m "feat: configure prompt usage observation" -- src/mycode/config.py src/mycode/cli.py tests/test_config.py tests/test_cli.py`

## T9：实现 OpenAI Chat 的 usage 和请求 ID 映射

**文件：** `src/mycode/protocols/openai_chat.py`、`tests/test_openai_chat_protocol.py`

**依赖：** T1、T8

**步骤：**

1. 在 `tests/test_openai_chat_protocol.py` 增加请求测试：`request_stream_usage=False` 时 payload 不含 `stream_options`；为 `True` 时 payload 含 `{"stream_options": {"include_usage": true}}`。
2. 增加流 fixture：最终 chunk 含 `prompt_tokens`、`completion_tokens`、`total_tokens` 和 `prompt_tokens_details.cached_tokens`，HTTP 响应头含 `x-request-id`；断言 `[DONE]` 前的最终 `DONE` 事件携带正确 `UsageObservation(provider="openai_chat", ...)`。
3. 增加未知值 fixture：缺少 usage、负数、布尔值或字符串 token 字段时，完成事件仍产生，受影响的观测字段为 `None`。
4. 增加独立消息 fixture：传入带不同 `origin` 的原始 user、reminder、environment 三条消息时，payload 保留三条独立 user 消息，且不发送 `origin`。
5. 运行 `python -m pytest tests/test_openai_chat_protocol.py -q`，预期 stream usage、请求 ID 和观测断言失败。
6. 修改 `openai_chat.py`：仅在 usage 配置开启时增加 `stream_options`；在流期间保存最后一个有效 usage 快照和响应头请求 ID；收到 `[DONE]` 时以 `StreamEvent(DONE, usage=...)` 输出。
7. 实现安全整数转换，排除布尔值和负数；缺失或无效字段保持 `None`，不得影响既有文本和工具调用解析。
8. 运行 `python -m pytest tests/test_openai_chat_protocol.py -q`，预期 OpenAI Chat 原有和新增协议测试通过。

**验证：** `python -m pytest tests/test_openai_chat_protocol.py -q` 通过。

**提交：** `git commit --only -m "feat: observe openai chat usage" -- src/mycode/protocols/openai_chat.py tests/test_openai_chat_protocol.py`

## T10：实现 OpenAI Responses 的 usage 和请求 ID 映射

**文件：** `src/mycode/protocols/openai_responses.py`、`tests/test_openai_responses_protocol.py`

**依赖：** T1、T8

**步骤：**

1. 在 `tests/test_openai_responses_protocol.py` 增加 `response.completed` fixture：其中 response usage 包含 `input_tokens`、`output_tokens`、`total_tokens` 和 `input_tokens_details.cached_tokens`，HTTP 响应头含 `request-id`。
2. 断言完成事件携带 `UsageObservation(provider="openai_responses")`，缓存读取字段正确，缓存写入字段为 `None`。
3. 增加未知值 fixture：usage 缺失、嵌套详情缺失、负数、布尔值和字符串字段都不会终止流，字段按 `None` 返回。
4. 增加独立消息 fixture：原始 user、reminder 和环境消息在 `input` 中保持独立且按输入顺序排列，`origin` 不出现在 payload。
5. 运行 `python -m pytest tests/test_openai_responses_protocol.py -q`，预期 usage 和请求 ID 断言失败。
6. 修改 `openai_responses.py`：从 `response.completed` 读取 usage 与请求 ID，构造带 usage 的 DONE 事件；保留 `response.failed`、文本增量和工具调用的既有语义。
7. 使用与 T9 一致的安全整数转换规则处理嵌套 cached token 字段。
8. 运行 `python -m pytest tests/test_openai_responses_protocol.py -q`，预期 Responses 原有和新增协议测试通过。

**验证：** `python -m pytest tests/test_openai_responses_protocol.py -q` 通过。

**提交：** `git commit --only -m "feat: observe openai responses usage" -- src/mycode/protocols/openai_responses.py tests/test_openai_responses_protocol.py`

## T11：实现 Anthropic 顶层 system 和 usage 映射

**文件：** `src/mycode/protocols/anthropic.py`、`tests/test_anthropic_protocol.py`

**依赖：** T1、T8

**步骤：**

1. 在 `tests/test_anthropic_protocol.py` 增加请求测试：一条 system 消息映射为 payload 顶层 `system`，不出现在 `messages`；多个 system 消息按输入顺序以双换行合并。
2. 增加独立消息测试：原始 user、`<system-reminder>` user 和 `<environment-context>` user 在 Anthropic `messages` 中仍是三个独立消息，且 payload 不包含 `origin`。
3. 增加 `message_delta` 与 `message_stop` fixture：usage 包含 `input_tokens`、`output_tokens`、`cache_read_input_tokens`、`cache_creation_input_tokens`，响应头含 `x-request-id`；断言 DONE 事件完成时携带正确观测。
4. 增加未知值测试：usage 缺失或字段无效时，文本/thinking 流继续，完成事件中的对应字段为 `None`。
5. 运行 `python -m pytest tests/test_anthropic_protocol.py -q`，预期顶层 system 和 usage 断言失败。
6. 修改 `anthropic.py`：分离并合并 system 消息到顶层字段，其余消息保持原顺序；在流内保存最后一个有效 usage 快照和请求 ID，在 `message_stop` 时附加到 DONE 事件。
7. 使用与 T9/T10 一致的安全整数规则，不改变现有 thinking 映射。
8. 运行 `python -m pytest tests/test_anthropic_protocol.py -q`，预期 Anthropic 原有和新增协议测试通过。

**验证：** `python -m pytest tests/test_anthropic_protocol.py -q` 通过。

**提交：** `git commit --only -m "feat: map anthropic prompt context usage" -- src/mycode/protocols/anthropic.py tests/test_anthropic_protocol.py`

## T12：补齐端到端 Prompt 行为与会话回归

**文件：** `tests/test_e2e_chat.py`、`tests/test_session.py`、`tests/test_tui.py`

**依赖：** T7、T8、T9、T10、T11

**步骤：**

1. 在 `tests/test_e2e_chat.py` 更新既有请求断言：不再断言 `minimal_system_prompt`，改为断言稳定 system、原始 user、独立 reminder 和独立环境消息的角色、来源和顺序。
2. 增加普通文本端到端场景：用户输入一次后，环境消息不写入 memory；下一 turn 生成新的环境快照。
3. 增加多轮工具场景：同一 turn 的多个 LLM 请求复用环境内容，第二轮保留工具历史，提醒在默认周期内由完整文本切换为精简文本。
4. 增加 plan-only 场景：开启后发送完整模式提醒，读工具流程和写工具审批语义保持既有行为。
5. 增加 `/clear` 场景：清空后下一请求不含旧普通历史、工具历史或临时消息；新 turn 仍构造新的环境上下文。
6. 增加 usage 场景：脚本 LLM 的 DONE 事件带观测时，端到端 Agent 事件流包含每个 round 对应的 `USAGE`，且最终回复和工具事件顺序仍正确。
7. 在 `tests/test_session.py` 和 `tests/test_tui.py` 更新事件/会话断言，使新的 `USAGE` 事件不影响 `/plan-only`、`/clear`、输出和输入循环行为。
8. 运行 `python -m pytest tests/test_e2e_chat.py tests/test_session.py tests/test_tui.py -q`，预期旧最小提示词断言或新场景在实现未完整前失败。
9. 仅根据失败信息修正 Stage 04 集成实现和测试预期；不得恢复 `minimal_system_prompt` 或把临时消息写入 memory。
10. 运行 `python -m pytest tests/test_e2e_chat.py tests/test_session.py tests/test_tui.py -q`，预期端到端和会话回归通过。

**验证：** `python -m pytest tests/test_e2e_chat.py tests/test_session.py tests/test_tui.py -q` 通过。

**提交：** `git commit --only -m "test: cover prompt pipeline end to end" -- tests/test_e2e_chat.py tests/test_session.py tests/test_tui.py`

## T13：更新文档并验证 Stage 04 回归范围

**文件：** `README.md`、`tests/test_docs.py`

**依赖：** T12

**步骤：**

1. 在 `tests/test_docs.py` 更新阶段断言为 Stage 04，并增加对 `Prompt Pipeline`、`system-reminder`、`environment-context`、`usage`、`prompt cache`、`unknown`、`plan-only` 和 Anthropic 顶层 system 行为的关键词断言。
2. 保留示例配置安全断言；不要修改 `examples/` 文件。若现有用户改动使该断言失败，记录为工作区前置问题，等待其所有者处理，不得在本任务中覆盖该改动。
3. 运行 `python -m pytest tests/test_docs.py -q`，预期 README 尚未覆盖 Stage 04 术语时失败；如果示例配置仍含用户改动，单独记录该失败原因。
4. 修改 `README.md`：将当前阶段改为 Stage 04，说明稳定模块、独立运行时消息、每 turn 环境快照、usage 配置示例、`unknown` 语义和不保证缓存命中等边界；保留 Stage 03 的工具、审批、取消和超时说明。
5. 运行 `python -m pytest tests/test_docs.py -q`。当示例配置恢复为环境变量引用后，预期文档测试通过；若仍被用户已有配置改动阻断，报告实际失败而不改动示例文件。

**验证：** `python -m pytest tests/test_docs.py -q`；预期 README 断言通过，示例配置断言取决于用户已有的两个示例配置改动。

**提交：** `git commit --only -m "docs: document stage 04 prompt pipeline" -- README.md tests/test_docs.py`

## T14：执行完整回归和人工缓存观测场景

**文件：** 不新增实现文件；需要读取 `doc/stage-04-prompt-context/spec.md`、`doc/stage-04-prompt-context/plan.md`、`doc/stage-04-prompt-context/task.md`

**依赖：** T1-T13

**步骤：**

1. 运行 Prompt、LLM、Agent、工具、配置、协议、TUI 和端到端测试：
   `python -m pytest tests/test_prompt_registry.py tests/test_prompt_environment.py tests/test_prompt_reminder.py tests/test_prompt_builder.py tests/test_llm_base.py tests/test_agent_events.py tests/test_agent_loop.py tests/test_tool_registry.py tests/test_config.py tests/test_cli.py tests/test_openai_chat_protocol.py tests/test_openai_responses_protocol.py tests/test_anthropic_protocol.py tests/test_session.py tests/test_tui.py tests/test_e2e_chat.py -q`。
2. 记录通过数、失败数和失败文件；任何失败先回到对应任务修正，不跳过或以预期代替实际结果。
3. 在示例配置由其所有者恢复为安全环境变量引用后，运行 `python -m pytest tests/test_docs.py -q`，确认 README 与示例配置安全断言通过。
4. 使用 mocked 协议 fixture 完成人工对照：两次相同稳定模块和工具集合的请求具有相同的稳定 system 文本 SHA-256 和工具顺序；不同 turn 的环境消息可不同；同一 turn 的多 round 环境消息相同。
5. 使用支持 usage 的真实供应商配置进行可选人工观测时，开启 `usage.request_stream_usage: true`，记录每个 `USAGE` 事件的 provider、请求 ID、输入 token 和缓存读取 token；没有缓存字段时记录 `unknown`，不得判定为未命中。
6. 确认人工观测不把 API key、完整用户输入、完整环境变量、完整 diff 或工具结果写入日志或文档。
7. 仅在所有自动化测试和可执行的人工检查均有实际证据后，生成验收报告；不在本任务中修改实现或示例配置以掩盖失败。

**验证：** 完整命令退出码为 0；`tests/test_docs.py` 需要示例配置前置状态满足安全断言；人工观察满足 spec 的稳定前缀、turn 快照和 unknown 语义。

**提交：** 不单独提交；该任务只汇总前序任务的验证证据。

## T15：将模型可见提示词改为中文

**文件：** `src/mycode/prompt/modules.py`、`src/mycode/prompt/reminder.py`、`src/mycode/prompt/environment.py`、`tests/test_prompt_registry.py`、`tests/test_prompt_reminder.py`、`tests/test_prompt_environment.py`

**依赖：** T3、T4、T5

**步骤：**

1. 在 `tests/test_prompt_registry.py` 增加断言：六个默认稳定模块的渲染文本均为中文，模块 ID、优先级和 `protected` 属性保持现有值。
2. 在 `tests/test_prompt_reminder.py` 增加断言：`plan-only` 的完整提醒和精简提醒均为中文；提醒周期与 XML 转义行为保持原有断言。
3. 在 `tests/test_prompt_environment.py` 增加断言：环境上下文的六个字段显示名为中文，同时 `<environment-context>` 标签、字段顺序和 XML 转义结果保持不变。
4. 运行 `python -m pytest tests/test_prompt_registry.py tests/test_prompt_reminder.py tests/test_prompt_environment.py -q`，预期新增中文文本断言在实现修改前失败。
5. 修改 `modules.py`、`reminder.py` 和 `environment.py` 中发送给模型的自然语言文本为中文；保留 `<system-reminder>`、`<environment-context>`、模块 ID 和所有异常信息不变。
6. 为解释“标签不翻译但字段显示名翻译”的边界保留或新增简洁中文注释；不要为直观字符串替换添加重复注释。
7. 再次运行 `python -m pytest tests/test_prompt_registry.py tests/test_prompt_reminder.py tests/test_prompt_environment.py -q`，预期所有提示词与环境回归通过。

**验证：** `python -m pytest tests/test_prompt_registry.py tests/test_prompt_reminder.py tests/test_prompt_environment.py -q` 通过。

## T16：配置模型工具并发调用能力并映射到 OpenAI 请求

**文件：** `src/mycode/config.py`、`src/mycode/protocols/openai_chat.py`、`src/mycode/protocols/openai_responses.py`、`tests/test_config.py`、`tests/test_openai_chat_protocol.py`、`tests/test_openai_responses_protocol.py`

**依赖：** T8

**步骤：**

1. 在 `tests/test_config.py` 增加失败测试：缺失 `tools` 配置时 `config.tools.parallel_calls is True`；`tools.parallel_calls: false` 解析为 `False`；非映射 `tools` 或非布尔 `parallel_calls` 抛出 `ConfigError`。
2. 在两个 OpenAI 协议测试中增加失败断言：携带工具定义且未显式配置时 payload 包含 `parallel_tool_calls: true`；构造 `LLMConfig(..., tools=ToolConfig(parallel_calls=False))` 时 payload 包含 `false`；没有工具定义时继续不包含该字段。
3. 运行 `python -m pytest tests/test_config.py tests/test_openai_chat_protocol.py tests/test_openai_responses_protocol.py -q`，预期默认并发和显式关闭断言失败。
4. 在 `config.py` 定义冻结的 `ToolConfig`，字段 `parallel_calls: bool = True`；将其作为 `LLMConfig.tools` 默认字段，新增 `_parse_tools()`，并在 `load_config()` 中装配。配置解析的中文注释只解释默认允许并发的原因。
5. 在 `openai_chat.py` 和 `openai_responses.py` 的工具定义分支中，将 `self.config.tools.parallel_calls` 写入 `parallel_tool_calls`；不改变无工具、消息序列化、流解析或 Anthropic 行为。
6. 运行 `python -m pytest tests/test_config.py tests/test_openai_chat_protocol.py tests/test_openai_responses_protocol.py -q`，预期配置和两个协议测试通过。

**验证：** `python -m pytest tests/test_config.py tests/test_openai_chat_protocol.py tests/test_openai_responses_protocol.py -q` 通过。

## T17：执行本次变更的集成回归

**文件：** 不新增实现文件；读取 `doc/stage-04-prompt-context/spec.md`、`doc/stage-04-prompt-context/plan.md`、`doc/stage-04-prompt-context/task.md` 和 `doc/stage-04-prompt-context/checklist.md`

**依赖：** T15、T16

**步骤：**

1. 运行 `python -m pytest tests/test_prompt_registry.py tests/test_prompt_reminder.py tests/test_prompt_environment.py tests/test_config.py tests/test_openai_chat_protocol.py tests/test_openai_responses_protocol.py -q`。
2. 使用项目 `.venv` 运行 `python -m compileall -q src`，确认修改后的 Python 源码可编译。
3. 执行 `git diff --check -- src/mycode/prompt src/mycode/config.py src/mycode/protocols/openai_chat.py src/mycode/protocols/openai_responses.py tests/test_prompt_registry.py tests/test_prompt_reminder.py tests/test_prompt_environment.py tests/test_config.py tests/test_openai_chat_protocol.py tests/test_openai_responses_protocol.py`。
4. 运行全量 `python -m pytest -q`；若被用户已有配置或现有测试阻断，记录失败文件和根因，不修改不在本任务范围内的文件。
5. 检查 `git status --short`，确认只新增本任务指定的代码、测试和 Stage 04 文档改动，且不修改 `examples/` 中的用户已有配置。

**验证：** 定向回归、编译和本任务路径的差异检查通过；全量回归结果如实记录。

## 执行顺序

```text
T1 -> T2
      ├-> T3 --┐
      └-> T4 --┴-> T5 -> T6 -> T7 -> T8
                                      ├-> T9  --┐
                                      ├-> T10 --┼-> T12 -> T13 -> T14
                                      └-> T11 --┘
```

T3 和 T4 都依赖 T2，可并行完成。T9、T10、T11 共享 T8 的 LLM 配置契约，其中 T10 与 T11 可以并行；T12 依赖三种协议适配全部完成。

本次补充任务在既有 Stage 04 工作完成后执行：`T15` 与 `T16` 可并行，二者完成后执行 `T17`。每个任务均先运行新增失败测试，再写最小实现；代码注释使用中文。
