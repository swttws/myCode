# myCode Stage 12：子 Agent 委派与后台任务验收清单

> 所有条目都在实现完成后执行。先运行验证命令、记录实际输出，再勾选结果；不得用代码阅读或主观判断代替可观察证据。

## 验收环境

- [ ] Stage 12 自动化测试只使用临时 cwd、临时 home、scripted/fake LLM、fake 工具、fake MCP pool、fake prompt-toolkit input 和确定性同步原语，不访问真实网络、真实 API、真实用户角色或工作区外文件。（验证：清空模型供应商 API key 后运行 `python -m pytest tests -q -k subagent`，期望全部通过，测试夹具记录的路径均位于 `tmp_path` 或包资源目录。）
- [ ] 本阶段开始前已有工作区变更未被恢复或覆盖。（验证：运行 `git status --short`，期望只出现 Stage 12 文档、实现、测试、示例和本阶段明确修改的现有文件。）
- [ ] 所有子 Agent 相关测试不依赖真实时间长等待。（验证：运行 `python -m pytest tests/test_subagent_tasks.py tests/test_subagent_service.py tests/test_subagent_session_tui.py -q`，期望通过，测试使用可控 Event/Future 推进超时、脱离和队列竞争。）

## 统一 Agent 工具与查询

- [ ] **AC1：** 模型只看到一个固定 `Agent` 工具，并能通过 `action=run` 的固定参数分别启动 `defined` 与 `fork` 任务。（验证：运行 `python -m pytest tests/test_subagent_tool.py tests/test_subagent_e2e.py -q -k "schema or defined or fork"`，期望 fake LLM 捕获的工具名集合只含一个 `Agent` 委派入口。）
- [ ] **AC2-a：** 角色新增、删除、覆盖和任务状态变化前后，`Agent` 工具 schema 完全一致。（验证：运行 `python -m pytest tests/test_subagent_tool.py tests/test_subagent_docs.py -q -k "schema_snapshot or stable"`，期望 schema 快照逐字相等。）
- [ ] **AC2-b：** 任务列表和详情查询均通过 `Agent(action=list|get)` 的只读能力读取实时任务管理器状态，不依赖聊天历史。（验证：运行 `python -m pytest tests/test_subagent_tool.py tests/test_subagent_service.py -q -k "list or get or realtime"`，期望列表按 sequence 排序，详情包含规范化 result/error/usage。）
- [ ] **AC25：** `/tasks`、`/task <id>` 和自然语言“当前会话任务怎么样”均读取实时状态；不存在或已淘汰任务 ID 返回中文稳定错误。（验证：运行 `python -m pytest tests/test_subagent_slash_cli.py tests/test_slash_builtins.py tests/test_slash_tui.py tests/test_subagent_e2e.py -q -k "tasks or task_detail or task_not_found or natural_language"`，期望无 `None` 泄露，缺失 usage 显示“未知”。）

## 角色、配置与内置资源

- [ ] **AC3：** 合法单文件角色可加载；文件名不匹配、缺少字段、正文为空、非法模型档位、非正数最大轮次或非法权限模式均产生可定位诊断。（验证：运行 `python -m pytest tests/test_subagent_loader.py -q -k "valid or invalid or required or diagnostic"`，期望每个坏候选都有来源、路径和错误码。）
- [ ] **AC4：** `allowed_tools: ["*"]` 表示全部候选工具，空列表不开放普通工具；白名单与黑名单冲突时工具不可用。（验证：运行 `python -m pytest tests/test_subagent_loader.py tests/test_subagent_tooling.py -q -k "allowed_tools or denied_tools or wildcard or blacklist"`，期望可见工具与执行前拒绝结果一致。）
- [ ] **AC5：** 项目、用户和内置来源存在同名角色时项目版本生效；高优先级版本无效时回退到下一有效版本并产生诊断。（验证：运行 `python -m pytest tests/test_subagent_catalog.py -q -k "precedence or fallback"`，期望 definition 来源与诊断顺序稳定。）
- [ ] **AC6：** 插件来源接口和最低优先级存在，但未配置插件系统时不扫描或加载任何插件角色；运行中修改角色文件不会隐式热更新。（验证：运行 `python -m pytest tests/test_subagent_catalog.py tests/test_subagent_loader.py -q -k "plugin or no_refresh or initialize_once"`，期望 plugin provider 默认为空且 snapshot 不随文件修改变化。）
- [ ] **AC7：** 默认安装可发现 `general`、`explore`、`review` 三个中文内置角色，用户级或项目级同名文件可覆盖它们。（验证：运行 `python -m pytest tests/test_subagent_docs.py tests/test_subagent_catalog.py -q -k "builtins or override"`，期望三份 Markdown metadata 与设计一致且 package data 可加载。）
- [ ] **AC8：** 省略可选子 Agent 配置时使用 120 秒前台超时、4 个后台并发和三个默认只读工具；非法数值、未知工具或缺失模型映射使启动失败。（验证：运行 `python -m pytest tests/test_subagent_config.py tests/test_config.py tests/test_subagent_slash_cli.py -q -k "sub_agent or models or defaults or invalid"`，期望 `load_config()` 边界严格失败，直接构造低层 `LLMConfig` 仍兼容。）

## 上下文、Fork 与运行时隔离

- [ ] **AC9：** 定义式子 Agent 的首个请求包含核心规则、工作区环境、项目指令、角色正文和任务，但不包含父消息、父记忆、父 Skill 或父临时提醒。（验证：运行 `python -m pytest tests/test_subagent_context.py tests/test_subagent_runtime.py -q -k "defined or prompt or no_parent"`，期望 fake LLM 捕获请求只含批准的上下文块。）
- [ ] **AC10：** 定义式角色选择模型档位后，请求只替换具体模型 ID 并复用当前协议配置；达到角色最大轮次后以稳定状态结束。（验证：运行 `python -m pytest tests/test_subagent_runtime.py tests/test_subagent_service.py -q -k "model_tier or max_rounds"`，期望模型工厂配置除 model ID 外保持一致，终态为稳定 `max_rounds_exceeded`。）
- [ ] **AC11：** Fork 首次请求的父 system 前缀、历史前缀和工具 schema 与父请求逐项相同；fake usage 可观察 cache-read，未提供缓存用量时显示未知。（验证：运行 `python -m pytest tests/test_subagent_context.py tests/test_subagent_e2e.py -q -k "fork or cache"`，期望冻结前缀逐项相等且 usage 未伪造。）
- [ ] **AC12：** Fork 始终返回后台任务 ID，使用父模型和父最大轮次；创建后的父消息不会进入 Fork 快照。（验证：运行 `python -m pytest tests/test_subagent_service.py tests/test_subagent_e2e.py -q -k "fork or background or snapshot"`，期望 `inline=False`，后续父消息不在子请求中。）
- [ ] **AC13：** 两个并发子 Agent 使用不同消息、权限授权、文件缓存和用量对象；任一方修改状态后另一方及父 Agent 保持不变。（验证：运行 `python -m pytest tests/test_subagent_tooling.py tests/test_subagent_runtime.py tests/test_subagent_e2e.py -q -k "isolation or concurrent"`，期望对象身份、状态和 usage 断言均隔离。）
- [ ] **AC14：** 子 Agent 可访问同一工作区并触发现有 Hook；Hook 或文件系统实例共享不会导致子 Agent 状态串扰。（验证：运行 `python -m pytest tests/test_subagent_tooling.py tests/test_subagent_runtime.py tests/test_hook_agent.py -q -k "workspace or hook or filesystem"`，期望共享基础设施可用且每任务 Hook runtime 状态独立。）
- [ ] **AC15：** fake LLM 在若干工具轮后停止调用工具时任务完成；最大轮次、取消和不可恢复错误分别进入确定终态。（验证：运行 `python -m pytest tests/test_subagent_runtime.py tests/test_subagent_tasks.py -q -k "complete or max_rounds or cancel or error"`，期望 runner 只结算一次且错误摘要脱敏。）

## 权限与工具防线

- [ ] **AC16：** 角色声明更宽松权限时有效权限不超过父 Agent；子 Agent 不复用父临时授权，遇到 `ASK` 时工具不执行并收到结构化拒绝。（验证：运行 `python -m pytest tests/test_subagent_tooling.py tests/test_permission_service.py -q -k "permission or grant or ask"`，期望 fake executor 调用数为 `0` 且返回中文拒绝。）
- [ ] **AC17：** 定义式子 Agent 请求中只出现多层过滤后的工具；转后台后，后续调用进一步受后台白名单限制。（验证：运行 `python -m pytest tests/test_subagent_tooling.py tests/test_subagent_service.py -q -k "visible or background_allowed or detached"`，期望可见 schema 与执行前拒绝一致。）
- [ ] **AC18：** Fork 持续看到父工具 schema，但后台禁用工具和 `Agent` 工具在执行前被拒绝，executor 调用数保持为零。（验证：运行 `python -m pytest tests/test_subagent_tooling.py tests/test_subagent_tool.py -q -k "fork or parent_only or Agent or forbidden"`，期望拒绝适配器保留 definition 且真实执行未发生。）
- [ ] 工具运行时作用域和定义级超时不污染供应商 tool schema。（验证：运行 `python -m pytest tests/test_tool_executor.py tests/test_tool_registry.py tests/test_subagent_tooling.py -q`，期望 `SHARED/TASK_LOCAL/PARENT_ONLY`、`execution_timeout_seconds` 和 AgentTool 本地超时均符合设计。）

## 调度、后台与通知

- [ ] **AC19：** 显式后台、等待 120 秒自动后台和运行中按 `Ctrl+B` 均使父 Agent 获得任务 ID，子 Agent 继续且只执行一次。（验证：运行 `python -m pytest tests/test_subagent_service.py tests/test_subagent_session_tui.py -q -k "background or timeout or ctrl_b or detach"`，期望 runner 调用次数为 `1`。）
- [ ] **AC20：** 前台任务在阈值前完成时直接返回最终结果；切换后台时已开始的调用不中断，后续调用应用后台限制。（验证：运行 `python -m pytest tests/test_subagent_service.py tests/test_subagent_tasks.py tests/test_subagent_tooling.py -q -k "inline or threshold or in_flight"`，期望内联和脱离竞争状态确定。）
- [ ] **AC21：** 并发上限为 4 时，第 5 个任务进入 `queued`；任一运行任务结束后，最早排队任务首先启动。（验证：运行 `python -m pytest tests/test_subagent_tasks.py tests/test_subagent_e2e.py -q -k "concurrency or fifo or queued"`，期望 sequence 决定启动顺序。）
- [ ] **AC22：** 任务详情展示类型、角色、状态、轮次、结果、错误及五类 token/cache 用量；缺失字段明确显示未知。（验证：运行 `python -m pytest tests/test_subagent_service.py tests/test_subagent_slash_cli.py tests/test_subagent_tool.py -q -k "usage or detail or unknown"`，期望 input/output/total/cache-read/cache-write 均有显示路径。）
- [ ] **AC23：** 后台完成不会自动调用模型；通知只在下一模型安全点或下一用户请求注入，并且每个任务最多注入一次。（验证：运行 `python -m pytest tests/test_subagent_notifications.py tests/test_subagent_agent.py tests/test_subagent_e2e.py -q -k "safe_point or reserve or once"`，期望 fake 父 LLM 调用数只因正常轮次增加。）
- [ ] **AC24：** 通知只包含有界摘要。超长最终输出按上限截断并带明确标记，任务详情返回任务管理器保存的规范化详细结果。（验证：运行 `python -m pytest tests/test_subagent_models.py tests/test_subagent_notifications.py tests/test_subagent_tasks.py -q -k "truncate or summary or retained"`，期望 UTF-8 边界正确且标记为 `...[结果已截断]`。）
- [ ] **AC27：** 并发完成、超时切换与取消竞争时，每个任务只有一个终态和一次通知；单任务失败不影响普通聊天或其他任务。（验证：运行 `python -m pytest tests/test_subagent_tasks.py tests/test_subagent_service.py tests/test_subagent_e2e.py -q -k "race or final or notification or fault_isolation"`，期望终态和通知计数均为 1 或 0 的设计值。）

## 会话生命周期、CLI 与文档

- [ ] **AC26：** 执行 `/clear` 或退出时，排队及运行任务全部取消并安全收尾，结果和通知被清空，重启后不可恢复。（验证：运行 `python -m pytest tests/test_subagent_service.py tests/test_subagent_session_tui.py tests/test_subagent_e2e.py -q -k "clear or close or restart"`，期望新会话 ID 从 `task-000001` 重新开始。）
- [ ] **AC28-a：** 系统子 Agent 提示、内置角色、用户提示、权限拒绝、任务状态和错误提示均为中文；外部角色正文保持原文。（验证：运行 `python -m pytest tests/test_subagent_docs.py tests/test_subagent_context.py tests/test_subagent_tooling.py tests/test_subagent_tool.py -q -k "chinese or prompt or error"`，期望新增系统文本为中文且外部正文逐字保留。）
- [ ] **AC28-b：** 新增定义类字段具有简洁中文注释，复杂隔离、缓存和状态转换逻辑只注释关键不变量。（验证：审查最终差异并运行 `git diff --check`，记录关键注释位置且无空白错误。）
- [ ] **AC28-c：** 完整测试不访问真实网络、API、用户角色或工作区外文件，现有回归测试通过。（验证：运行 `python -m pytest -q`，期望全部通过且 fake 组件捕获所有模型、MCP、网络和文件边界。）
- [ ] CLI 装配顺序无环：先建立不含 `Agent` 的基础工具，再校验后台工具和角色候选，最后注册唯一 `AgentTool` 并注入 AgentLoop、Session、TUI 与 Slash。（验证：运行 `python -m pytest tests/test_subagent_slash_cli.py tests/test_slash_cli.py -q -k "startup_order or assembly"`，期望构造顺序与设计一致。）
- [ ] 三份示例配置均包含非空主模型和完整 `sub_agent.models.haiku/sonnet/opus` 映射，并能被真实 `load_config()` 加载。（验证：运行 `python -m pytest tests/test_docs.py tests/test_config.py -q -k "example or sub_agent"`，期望三份示例配置全部通过。）
- [ ] README 记录固定 Agent actions、角色来源、配置默认值、定义式/Fork、前后台/`Ctrl+B`、`/tasks`、`/task <id>`、通知安全点、会话清理和 Anthropic 工具支持范围。（验证：运行 `python -m pytest tests/test_docs.py tests/test_subagent_docs.py -q -k "readme or anthropic or package_data"`，期望关键段落和 package data 声明均可定位。）

## Anthropic 协议与兼容接入

- [ ] Anthropic 请求支持顶层 system、工具 schema、assistant `tool_use` 历史、user `tool_result` 历史和并行工具批次。（验证：运行 `python -m pytest tests/test_anthropic_protocol.py -q -k "system or tools or history or tool_result"`，期望请求 JSON 与统一消息模型一致。）
- [ ] Anthropic 流式响应支持 `tool_use`、`input_json_delta.partial_json`、并行 index、非法参数 JSON 和 usage 共存。（验证：运行 `python -m pytest tests/test_anthropic_protocol.py -q -k "tool_use or input_json or parallel_tool or usage"`，期望输出统一 `TOOL_CALL` 事件或稳定错误。）
- [ ] Stage 12 对现有 AgentLoop、Session、Slash、TUI、Skill、Hook、MCP、权限、记忆和上下文压缩路径保持兼容。（验证：运行 `python -m pytest tests/test_agent_loop.py tests/test_session.py tests/test_slash_builtins.py tests/test_slash_cli.py tests/test_slash_tui.py tests/test_skill_agent.py tests/test_hook_agent.py tests/test_mcp_tools.py tests/test_permission_service.py tests/test_memory_tools.py tests/test_context_compaction_e2e.py -q`，期望零失败。）

## 编译与测试

- [ ] 子 Agent 端到端测试通过。（验证：运行 `python -m pytest tests/test_subagent_e2e.py -q`，期望 defined、Fork、FIFO、通知、查询、清理和故障隔离场景全部通过。）
- [ ] 所有 subagent 相关测试通过。（验证：运行 `python -m pytest tests -q -k subagent`，期望零失败。）
- [ ] Stage 12 窄接入回归通过。（验证：运行 `python -m pytest tests/test_anthropic_protocol.py tests/test_tool_executor.py tests/test_agent_loop.py tests/test_session.py tests/test_slash_builtins.py tests/test_slash_cli.py tests/test_slash_tui.py tests/test_docs.py -q`，期望零失败。）
- [ ] 仓库当前全部自动化测试通过。（验证：运行 `python -m pytest -q`，记录测试总数、通过数和退出码 `0`。）
- [ ] Python 源码和测试可完整编译，无语法错误。（验证：运行 `python -m compileall -q src tests`，期望退出码 `0`。）
- [ ] 最终差异无空白错误、冲突标记或意外修改用户已有文件。（验证：运行 `git diff --check` 和 `git status --short`，期望前者退出码 `0`，后者只列 Stage 12 相关文件及进入本阶段前已存在的用户变更。）

## 端到端场景

- [ ] **场景 1：定义式前台任务内联返回。** 父模型通过固定 `Agent` schema 启动 `defined/general`，子 Agent 从空白对话使用真实 AgentLoop 多轮工具运行，阈值前完成后终态详情作为工具结果内联给父 Agent。（验证：运行 `python -m pytest tests/test_subagent_e2e.py -q -k "defined_frontground"`，期望父历史只包含有界结果，不含子过程噪音。）
- [ ] **场景 2：Fork 后台与缓存前缀。** Fork 立即返回任务 ID，首次子请求与父快照 messages/tools 前缀逐项一致，后续父消息不进入快照，fake cache-read usage 可在详情观察。（验证：运行 `python -m pytest tests/test_subagent_e2e.py tests/test_subagent_context.py -q -k "fork"`，期望前缀相等和 usage 显示均通过。）
- [ ] **场景 3：5 个任务的统一 FIFO 调度。** 同时提交 5 个前后台任务，4 个进入 running，第 5 个 queued；释放任一槽位后最早 queued 任务启动，每个 runner 只执行一次。（验证：运行 `python -m pytest tests/test_subagent_e2e.py tests/test_subagent_tasks.py -q -k "five or fifo"`，期望状态序列确定。）
- [ ] **场景 4：后台通知安全点与实时查询。** 已脱离任务完成后不会自行触发父 LLM；下一轮安全点注入一次有界通知，随后 `Agent(list|get)`、`/tasks` 和 `/task <id>` 读取同一快照。（验证：运行 `python -m pytest tests/test_subagent_e2e.py tests/test_subagent_agent.py tests/test_subagent_slash_cli.py -q -k "notification or realtime"`，期望通知计数和查询字段一致。）
- [ ] **场景 5：Ctrl+B 手动脱离。** 前台流式输出期间按 `Ctrl+B`，父 Agent 获得任务 ID，子 Agent 继续运行且已开始的工具不被取消；无活动任务时按键静默无害。（验证：运行 `python -m pytest tests/test_subagent_session_tui.py tests/test_subagent_service.py -q -k "ctrl_b"`，期望监听器按流生命周期安装和解除。）
- [ ] **场景 6：`/clear` 与退出清理。** 运行和排队任务并存时执行 `/clear`，全部取消并安全收尾，结果/通知/ID 计数清空；退出路径同样关闭 service 且幂等。（验证：运行 `python -m pytest tests/test_subagent_e2e.py tests/test_subagent_service.py tests/test_subagent_session_tui.py -q -k "clear or close"`，期望旧任务不可查询。）
- [ ] **场景 7：故障隔离。** 一个角色解析、Hook、工具或 LLM 失败只结算当前任务，另一个任务和普通父聊天继续完成；错误不包含凭据、完整父历史或无关工具输出。（验证：运行 `python -m pytest tests/test_subagent_e2e.py tests/test_subagent_runtime.py -q -k "fault_isolation or redacted"`，期望错误码稳定且相邻流程成功。）

## 验收覆盖矩阵

| Spec 验收标准 | 对应检查区域 |
|---|---|
| AC1-AC2 | 统一 Agent 工具与查询 |
| AC3-AC8 | 角色、配置与内置资源 |
| AC9-AC15 | 上下文、Fork 与运行时隔离 |
| AC16-AC18 | 权限与工具防线 |
| AC19-AC24、AC27 | 调度、后台与通知 |
| AC25 | 统一 Agent 工具与查询；端到端场景 4 |
| AC26 | 会话生命周期、CLI 与文档；端到端场景 6 |
| AC28 | 会话生命周期、CLI 与文档；编译与测试 |

28 条 Spec 验收标准均至少对应一个可执行检查项；定义式、Fork、权限收紧、统一调度、通知安全点、实时查询、清理、Anthropic 工具往返和完整回归均有独立证据入口。
