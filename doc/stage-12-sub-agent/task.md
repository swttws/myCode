# myCode Stage 12：子 Agent 委派与后台任务任务拆解

## 执行约束

- 严格测试先行：每个行为先写失败测试并确认失败原因正确，再写最小实现；禁止把实现和首次测试放在同一步完成。
- 子 Agent 领域集中在 `src/mycode/subagent/`；现有 Agent、工具、协议、Session、TUI、Slash 和 CLI 只增加计划明确的接入点。
- 前台和后台任务共用执行槽；超时和 `Ctrl+B` 只解除父 Agent 等待，不重启、复制或改变任务队列位置。
- 子 Agent 不进行交互式问答或审批；权限 `ASK` 必须转换为中文结构化拒绝并回填子 Agent。
- `Agent`、父项目记忆和父压缩归档工具不得在子 Agent 中执行；Fork 只保留其冻结 schema。
- Fork 首次请求的父 messages 和 tools 前缀必须逐项不变；任何新增指令、Hook block 和子历史只能追加在冻结前缀之后。
- 每个任务独立维护 memory、权限会话、文件缓存、MCP 发现、Skill 激活、Hook 可变状态、usage 和取消信号。
- 所有新增定义字段使用简洁中文注释；复杂状态竞争、缓存前缀和隔离逻辑只注释关键不变量。
- 自动化测试只使用临时 cwd/home、scripted LLM、fake 工具、fake MCP pool、fake prompt-toolkit input 和确定性同步原语，不访问真实网络、API、用户角色或权限文件。
- 本阶段不实现子 Agent 递归、团队编排、任务取消/重跑入口、跨会话持久化、文件冲突解决或角色热更新。

## 文件清单

### 新建实现与资源文件

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `src/mycode/subagent/__init__.py` | 子 Agent 公共导出与包级说明 |
| 新建 | `src/mycode/subagent/models.py` | 枚举、冻结数据类、限制常量和错误类型 |
| 新建 | `src/mycode/subagent/config.py` | 主配置中的 `sub_agent` 解析与工具名校验 |
| 新建 | `src/mycode/subagent/loader.py` | 角色候选扫描、Markdown/frontmatter 解析和诊断 |
| 新建 | `src/mycode/subagent/catalog.py` | 来源覆盖、无效候选回退和稳定目录快照 |
| 新建 | `src/mycode/subagent/context.py` | 父请求快照、定义式提示和 Fork 前缀构建 |
| 新建 | `src/mycode/subagent/tooling.py` | 任务工具注册表、工具策略和非交互权限拦截 |
| 新建 | `src/mycode/subagent/runtime.py` | 独立 AgentLoop 创建、事件消费、结果和 usage 聚合 |
| 新建 | `src/mycode/subagent/tasks.py` | FIFO 调度、统一执行槽、状态机和终态留存 |
| 新建 | `src/mycode/subagent/notifications.py` | 通知截断、预留、提交、释放和 framework block |
| 新建 | `src/mycode/subagent/service.py` | 运行、等待、脱离、查询和会话清理编排 |
| 新建 | `src/mycode/subagent/tool.py` | 固定 `Agent` 工具 schema、参数校验和响应格式化 |
| 新建 | `src/mycode/subagent/builtins/general.md` | 通用中文内置角色 |
| 新建 | `src/mycode/subagent/builtins/explore.md` | 只读探索中文内置角色 |
| 新建 | `src/mycode/subagent/builtins/review.md` | 只读审查中文内置角色 |

### 修改实现、配置与文档文件

| 操作 | 文件 | 职责 |
|---|---|---|
| 修改 | `src/mycode/config.py` | `LLMConfig` 接入严格子 Agent 配置 |
| 修改 | `src/mycode/tool/base.py` | 增加 `ToolRuntimeScope` 和定义级执行超时 |
| 修改 | `src/mycode/tool/__init__.py` | 导出工具运行时作用域 |
| 修改 | `src/mycode/tool/executor.py` | 应用定义级超时覆盖 |
| 修改 | `src/mycode/tool/defaults.py` | 默认工具任务级重建与作用域声明 |
| 修改 | `src/mycode/memory/tools.py` | 长期记忆读取工具标记为父运行时专用 |
| 修改 | `src/mycode/compact/archive.py` | 压缩归档读取工具标记为父运行时专用 |
| 修改 | `src/mycode/skill/load_tool.py` | Skill 加载工具标记为任务级 |
| 修改 | `src/mycode/mcp/tools.py` | 增加无 listener 的任务 MCP 快照装配 |
| 修改 | `src/mycode/protocols/anthropic.py` | 完整 Anthropic system/tools/tool_use/tool_result 支持 |
| 修改 | `src/mycode/agent/loop.py` | 更新父快照并在安全点注入通知 |
| 修改 | `src/mycode/session.py` | 接入前台脱离、clear 和 close |
| 修改 | `src/mycode/slash/controller.py` | 增加任务列表和详情控制接口 |
| 修改 | `src/mycode/slash/builtins.py` | 注册 `/tasks` 和 `/task <id>` |
| 修改 | `src/mycode/tui.py` | 流式输出期间捕获 `Ctrl+B` |
| 修改 | `src/mycode/cli.py` | 按无装配环顺序创建目录、服务和 AgentTool |
| 修改 | `pyproject.toml` | 打包三个内置角色 Markdown |
| 修改 | `examples/mycode.anthropic.yaml` | 增加完整模型档位映射 |
| 修改 | `examples/mycode.openai-responses.yaml` | 增加完整模型档位映射 |
| 修改 | `examples/mycode.openai-chat.yaml` | 增加完整模型档位映射并补非空示例模型 |
| 修改 | `README.md` | 说明子 Agent、任务命令、配置和 Anthropic 工具支持 |

### 新建测试文件

| 操作 | 文件 | 覆盖范围 |
|---|---|---|
| 新建 | `tests/test_subagent_models.py` | 数据不变量、截断和 usage 聚合 |
| 新建 | `tests/test_subagent_config.py` | 子 Agent 主配置严格校验 |
| 新建 | `tests/test_subagent_loader.py` | 角色文件扫描与解析 |
| 新建 | `tests/test_subagent_catalog.py` | 来源覆盖与回退 |
| 新建 | `tests/test_subagent_docs.py` | 内置角色和 package data |
| 新建 | `tests/test_subagent_context.py` | 定义式上下文和 Fork 前缀 |
| 新建 | `tests/test_subagent_tooling.py` | 工具过滤、权限收紧和任务工具隔离 |
| 新建 | `tests/test_subagent_notifications.py` | 通知预留、批次和溢出 |
| 新建 | `tests/test_subagent_tasks.py` | 调度、状态竞争和留存 |
| 新建 | `tests/test_subagent_runtime.py` | 独立 AgentLoop、结果和 usage |
| 新建 | `tests/test_subagent_service.py` | 前后台等待、查询和清理 |
| 新建 | `tests/test_subagent_tool.py` | 固定 Agent 工具 schema 和 action |
| 新建 | `tests/test_subagent_agent.py` | 父快照和通知安全点接入 |
| 新建 | `tests/test_subagent_session_tui.py` | Session 生命周期和 Ctrl+B |
| 新建 | `tests/test_subagent_slash_cli.py` | Slash、CLI 装配和启动错误 |
| 新建 | `tests/test_subagent_e2e.py` | 定义式、Fork、队列、通知和清理端到端 |

### 按行为调整的现有测试

| 操作 | 文件 | 调整原因 |
|---|---|---|
| 修改 | `tests/test_config.py` | 现有配置 fixture 补有效模型档位映射 |
| 修改 | `tests/test_anthropic_protocol.py` | 旧的忽略 tools 断言改为完整工具往返 |
| 修改 | `tests/test_tool_executor.py` | 增加定义级超时覆盖 |
| 修改 | `tests/test_tool_registry.py` | 新增 ToolDefinition 本地字段保持协议 schema 不变 |
| 修改 | `tests/test_agent_loop.py` | 父快照和通知依赖缺省时保持兼容 |
| 修改 | `tests/test_session.py` | 子 Agent service 缺省时保持兼容 |
| 修改 | `tests/test_slash_builtins.py` | 新增任务命令注册、帮助和用法 |
| 修改 | `tests/test_slash_cli.py` | CLI 装配参数补有效子 Agent 配置 |
| 修改 | `tests/test_slash_tui.py` | TUI 控制接口增加任务方法 |
| 修改 | `tests/test_docs.py` | README 和三份示例配置的新契约 |

## 任务列表

## T1：编写子 Agent 模型和配置失败测试

**文件：** `tests/test_subagent_models.py`、`tests/test_subagent_config.py`、`tests/test_config.py`  
**依赖：** 无

**步骤：**
1. 测试角色、任务、结果、通知和 usage 数据类按 `plan.md` 字段构造，非法枚举、空错误码组合和非终态执行报告被拒绝。
2. 测试 UTF-8 截断不切断多字节字符，超限结果追加 `...[结果已截断]`。
3. 测试 usage 只有每轮都提供同一字段时才累加；任一轮缺失后该字段保持 `None`。
4. 测试 `sub_agent.models` 缺失任一 `haiku/sonnet/opus`、空模型 ID、未知键时报 `ConfigError`。
5. 测试超时、并发、任务/结果/通知上限拒绝布尔、非有限和非正数；省略可选字段使用设计默认值。
6. 给现有通用配置 fixture 加合法三档映射，保留专门测试缺失映射的红灯用例。

**验证：** `python -m pytest tests/test_subagent_models.py tests/test_subagent_config.py tests/test_config.py -q`；预期非零退出，失败集中在 `mycode.subagent` 模型和配置接口缺失。

## T2：实现领域模型和严格主配置

**文件：** `src/mycode/subagent/__init__.py`、`src/mycode/subagent/models.py`、`src/mycode/subagent/config.py`、`src/mycode/config.py`  
**依赖：** T1

**步骤：**
1. 定义 `SubAgentKind`、角色来源/模型/权限枚举、任务状态和全部冻结数据类，落实状态与字段不变量。
2. 实现 UTF-8 字节边界截断和 `SubAgentUsage` 逐轮精确聚合辅助器。
3. 实现 `parse_subagent_config()`，完整校验模型映射、默认后台工具和所有资源上限。
4. `LLMConfig` 增加 `sub_agent: SubAgentConfig | None = None`，直接构造保持兼容；`load_config()` 必须解析出非空配置。
5. 导出后续模块需要的公共类型；所有新增定义字段添加简洁中文注释。

**验证：** `python -m pytest tests/test_subagent_models.py tests/test_subagent_config.py tests/test_config.py -q`；预期全部通过。

## T3：编写角色加载和目录覆盖失败测试

**文件：** `tests/test_subagent_loader.py`、`tests/test_subagent_catalog.py`  
**依赖：** T2

**步骤：**
1. 在临时项目、home、builtin 和 fake plugin 来源创建同名与不同名角色文件。
2. 测试合法 frontmatter 的七个字段、中文正文、revision 和 entry path。
3. 测试文件超过 128 KiB、frontmatter 超过 16 KiB、文件名与 name 不同、正文为空、非法模型/权限/轮次和重复工具产生可定位诊断。
4. 测试 `allowed_tools: ["*"]`、空列表、具体列表合法，`"*"` 混用或出现在 denied 中非法。
5. 测试项目 > 用户 > 内置 > 插件；高优先级候选无效时回退下一有效候选并保留诊断。
6. 测试 definitions 按名称排序、diagnostics 按来源/路径/错误码排序，初始化后禁止 refresh。

**验证：** `python -m pytest tests/test_subagent_loader.py tests/test_subagent_catalog.py -q`；预期非零退出，失败指向 loader/catalog 尚未实现。

## T4：实现角色 loader 和只初始化一次的目录

**文件：** `src/mycode/subagent/loader.py`、`src/mycode/subagent/catalog.py`、`src/mycode/subagent/__init__.py`  
**依赖：** T3

**步骤：**
1. 实现项目 `.mycode/agents`、用户 `~/.mycode/agents`、内置目录和空 plugin provider 候选扫描。
2. 在读取完整正文前检查文件大小，在 YAML 解析前检查 frontmatter 大小。
3. 用 `yaml.safe_load` 严格校验字段白名单、文件名、名称模式、工具列表、模型、权限和正整数轮次。
4. 实现 revision、来源优先级、无效高优先级回退和稳定排序。
5. `AgentCatalog.initialize()` 只允许调用一次，`snapshot()` 和 `get()` 不触发文件重扫。

**验证：** `python -m pytest tests/test_subagent_loader.py tests/test_subagent_catalog.py -q`；预期全部通过。

## T5：编写内置角色和打包失败测试

**文件：** `tests/test_subagent_docs.py`  
**依赖：** T4

**步骤：**
1. 通过 `importlib.resources.files("mycode.subagent")` 定位 builtins 并使用真实 loader 加载。
2. 断言角色集合严格等于 `general/explore/review`，正文均含中文且无模板占位符。
3. 断言 general 为 `allowed_tools: ["*"]`、拒绝 `Agent`、inherit 模型/权限、8 轮。
4. 断言 explore/review 只含三个默认只读文件工具、strict 权限、inherit 模型、8 轮。
5. 断言 `pyproject.toml` 声明 `"mycode.subagent" = ["builtins/*.md"]`。

**验证：** `python -m pytest tests/test_subagent_docs.py -q`；预期非零退出，失败原因是内置资源和 package-data 尚不存在。

## T6：实现并打包三个中文内置角色

**文件：** `src/mycode/subagent/builtins/general.md`、`src/mycode/subagent/builtins/explore.md`、`src/mycode/subagent/builtins/review.md`、`pyproject.toml`  
**依赖：** T5

**步骤：**
1. 创建 general 角色，正文要求通用执行、非交互跑到底和有界结论。
2. 创建 explore 角色，正文要求只读定位、引用事实并避免修改建议冒充结果。
3. 创建 review 角色，正文要求按严重度输出缺陷、风险和测试缺口。
4. 三个 frontmatter 使用批准的工具、模型、轮次和权限默认值。
5. 更新 setuptools package-data，使 wheel 安装后仍能发现角色文件。

**验证：** `python -m pytest tests/test_subagent_docs.py tests/test_subagent_loader.py tests/test_subagent_catalog.py -q`；预期全部通过。

## T7：编写工具运行时作用域和定义级超时失败测试

**文件：** `tests/test_tool_executor.py`、`tests/test_tool_registry.py`、`tests/test_subagent_tooling.py`  
**依赖：** T2

**步骤：**
1. 测试 `ToolDefinition` 缺省 `runtime_scope=SHARED`、`execution_timeout_seconds=None`，协议工具 schema 不出现两个本地字段。
2. 测试定义级超时覆盖 executor 默认值；未设置覆盖的工具仍使用默认超时。
3. 测试非正、布尔和非有限定义级超时在注册或执行前被拒绝。
4. 测试默认文件工具和 `load_skill` 为 TASK_LOCAL，`Agent/read_memory_note/read_compact_artifact` 为 PARENT_ONLY。
5. 测试 PARENT_ONLY 拒绝适配器保留原 definition，但 execute 永远返回稳定拒绝。

**验证：** `python -m pytest tests/test_tool_executor.py tests/test_tool_registry.py tests/test_subagent_tooling.py -q`；预期非零退出，失败指向工具元数据和适配器缺失。

## T8：实现工具作用域、超时覆盖和父工具标记

**文件：** `src/mycode/tool/base.py`、`src/mycode/tool/__init__.py`、`src/mycode/tool/executor.py`、`src/mycode/tool/defaults.py`、`src/mycode/memory/tools.py`、`src/mycode/compact/archive.py`、`src/mycode/skill/load_tool.py`、`src/mycode/subagent/tooling.py`  
**依赖：** T7

**步骤：**
1. 增加 `ToolRuntimeScope`、`runtime_scope` 和 `execution_timeout_seconds`，保持现有构造调用兼容。
2. ToolExecutor 优先使用合法定义级超时，否则使用原默认超时。
3. 给默认文件、Skill、记忆和压缩工具标记批准的作用域。
4. 实现 definition-preserving PARENT_ONLY 拒绝适配器，错误码使用 `parent_runtime_tool_forbidden`。
5. 确保 OpenAI/Anthropic 序列化只读取 name/description/parameters，不输出本地字段。

**验证：** `python -m pytest tests/test_tool_executor.py tests/test_tool_registry.py tests/test_subagent_tooling.py -q`；预期全部通过。

## T9：编写 Anthropic 请求和历史序列化失败测试

**文件：** `tests/test_anthropic_protocol.py`  
**依赖：** T2

**步骤：**
1. 把“接受 tools 但不发送”的旧测试改为断言 `{name, description, input_schema}` 且顺序稳定。
2. 测试 system 消息从 messages 取出并按顺序进入顶层 `system`。
3. 测试普通 user/assistant 文本转换为 text content block。
4. 测试单个和多个连续 assistant tool call 合并为一个 assistant `tool_use` content 列表。
5. 测试连续 role=tool 消息合并为一个 user `tool_result` content 列表并保持 call ID。
6. 测试历史 tool arguments 非法 JSON 时以空对象重放，对应失败 tool_result 原文仍存在。

**验证：** `python -m pytest tests/test_anthropic_protocol.py -q -k "system or tools or history or tool_result"`；预期非零退出，失败指向 Anthropic 请求转换尚未支持工具。

## T10：实现 Anthropic system、tools 和工具历史请求转换

**文件：** `src/mycode/protocols/anthropic.py`  
**依赖：** T9

**步骤：**
1. 提取 system 消息到请求顶层，普通消息转换为 Anthropic content block。
2. 实现 ToolDefinition 到 `input_schema` 的稳定序列化。
3. 实现 assistant tool_use 和 user tool_result 历史转换，按连续角色合并 content block。
4. tool arguments 只接受 JSON object；历史非法参数使用空对象，不抛出序列化异常。
5. 保持 thinking、usage、URL、header 和纯文本请求现有测试兼容。

**验证：** `python -m pytest tests/test_anthropic_protocol.py -q -k "system or tools or history or tool_result"`；预期全部通过。

## T11：编写 Anthropic 流式 tool_use 失败测试

**文件：** `tests/test_anthropic_protocol.py`  
**依赖：** T10

**步骤：**
1. 构造 `content_block_start` 的 tool_use ID/name/input 和若干 `input_json_delta.partial_json`。
2. 测试对应 `content_block_stop` 产生一个 `StreamEventType.TOOL_CALL`，arguments 与 raw_arguments 正确。
3. 测试两个不同 index 的并行工具参数交错到达时分别累积和输出。
4. 测试文本、thinking、tool_use 和最终 usage 在同一流中保持事件顺序。
5. 测试非法参数 JSON 产生 `arguments=None` 而非协议异常。
6. 测试 tool_use 缺 ID/name、重复 block start 和未知 stop index 产生稳定 `LLMError` 或 ERROR 事件，不输出不完整调用。

**验证：** `python -m pytest tests/test_anthropic_protocol.py -q -k "tool_use or input_json or parallel_tool"`；预期非零退出，失败指向流式工具状态机尚未实现。

## T12：实现 Anthropic 流式工具调用状态机

**文件：** `src/mycode/protocols/anthropic.py`  
**依赖：** T11

**步骤：**
1. 按 content block index 保存待处理 tool_use 的 ID、名称、初始 input 和参数片段。
2. 在 `input_json_delta` 追加 partial JSON，不把不同 index 混合。
3. 在对应 block stop 解析参数并生成统一 ToolCall；非法 JSON 保留 raw text 且 arguments 为 None。
4. 对缺字段、重复 index 和未知 stop 执行稳定 fail-closed，不产生残缺调用。
5. 保持 text/thinking/usage 现有事件映射和 stream 关闭语义。

**验证：** `python -m pytest tests/test_anthropic_protocol.py -q`；预期全部通过。

## T13：编写定义式上下文和 Fork 前缀失败测试

**文件：** `tests/test_subagent_context.py`  
**依赖：** T6、T8

**步骤：**
1. 构造包含父历史、项目记忆、Skill block、提醒、环境和 tools 的 `PromptBuildResult`。
2. 测试 `ParentAgentSnapshotStore` 在 ContextVar 中按异步任务隔离，并深复制 messages 和 tool parameters。
3. 测试父消息、父 tool dict 和注册表后续变化不改变已创建快照。
4. 测试定义式提示只包含核心系统模块、重新加载的项目指令、当前环境、角色正文和任务。
5. 测试定义式不包含父历史、项目记忆、父 Skill 激活或父临时提醒。
6. 测试 Fork 每轮以冻结父 messages/tools 开头，中文 Fork 指令和子历史只追加在后。

**验证：** `python -m pytest tests/test_subagent_context.py -q`；预期非零退出，失败指向快照和提示构建器缺失。

## T14：实现父快照、定义式提示和 Fork prompt builder

**文件：** `src/mycode/subagent/context.py`、`src/mycode/subagent/__init__.py`  
**依赖：** T13

**步骤：**
1. 实现基于 ContextVar 的 `ParentAgentSnapshotStore.update/current`，缺少当前快照返回稳定错误。
2. 通过 JSON round-trip 深复制 tool parameters，协议前再构造普通 dict，避免父引用和只读代理问题。
3. 使用现有核心 prompt modules 和项目 instruction loader 创建定义式 builder，把角色正文作为受保护系统模块。
4. 实现 Fork builder，固定父 messages/tools 并只追加任务 memory、Hook blocks 和子工具结果。
5. 为缓存前缀与深复制不变量添加简洁中文注释。

**验证：** `python -m pytest tests/test_subagent_context.py tests/test_prompt_builder.py -q`；预期全部通过。

## T15：编写多层工具策略和权限单调性失败测试

**文件：** `tests/test_subagent_tooling.py`  
**依赖：** T6、T8、T14

**步骤：**
1. 测试定义式可见工具依次应用 PARENT_ONLY/Agent 全局禁止、角色白名单、角色黑名单和 detached 后台白名单。
2. 测试空 allowed_tools 不显示普通工具，`["*"]` 从候选集合开始，黑名单冲突时最终不可见。
3. 测试 Fork visible tools 始终等于冻结父 tools，但 evaluate 仍拒绝 Agent、PARENT_ONLY 和后台禁用工具。
4. 覆盖父 strict/default/permissive 与角色 inherit/strict/default/permissive 的全部权限取严组合。
5. 测试独立权限服务重新加载持久规则但不含父 session mode、session grant 或 pending approval。
6. 测试底层权限返回 ASK 时拦截器返回 `approval_required_non_interactive` DENY，审批 provider 和 executor 调用数均为零。

**验证：** `python -m pytest tests/test_subagent_tooling.py -q -k "policy or permission or visible or ask"`；预期非零退出，失败指向策略和权限包装尚未实现。

## T16：实现工具策略和非交互权限拦截器

**文件：** `src/mycode/subagent/tooling.py`  
**依赖：** T15

**步骤：**
1. 实现 `SubAgentToolPolicy.visible_names()` 和 `evaluate()` 的固定过滤顺序与中文拒绝码。
2. 实现权限档位 rank 和 `effective_permission_mode()`，角色不能放宽父档位。
3. 创建任务独立 PermissionService，复用持久配置路径和 PathGuard 规则，不复制父 session state。
4. 实现 `SubAgentPermissionInterceptor.before_tool/denied_result/after_tool`，保证永不向 AgentLoop 返回 ASK。
5. 对策略拒绝直接构造 PermissionDecision，使 Hook、审批和 executor 都不运行。

**验证：** `python -m pytest tests/test_subagent_tooling.py -q -k "policy or permission or visible or ask"`；预期全部通过。

## T17：编写任务工具注册表和状态隔离失败测试

**文件：** `tests/test_subagent_tooling.py`  
**依赖：** T16

**步骤：**
1. 创建两个任务注册表，断言 ToolRegistry、ToolExecutor 和 FileTextCache 对象身份不同。
2. 读取同一文件后修改一方缓存，断言另一任务和父缓存不被写入。
3. 用 fake MCP pool 构造两个任务快照，断言 wrapper/ToolSearch/发现集合独立且 pool 身份共享。
4. 断言任务 MCP 装配不增加 pool tools listener，主注册表 listener 数保持不变。
5. 创建两个 Skill runtime，激活一方后另一方 scope 为空；PARENT_ONLY 工具使用拒绝适配器。
6. 缺少 TASK_LOCAL 重建工厂时，注册表创建返回稳定 `task_local_tool_factory_missing`。

**验证：** `python -m pytest tests/test_subagent_tooling.py -q -k "registry or cache or mcp or skill or parent_only"`；预期非零退出，失败指向任务级工具工厂和 MCP 快照缺失。

## T18：实现任务工具注册表、MCP 快照和独立 Skill 状态

**文件：** `src/mycode/subagent/tooling.py`、`src/mycode/tool/defaults.py`、`src/mycode/mcp/tools.py`  
**依赖：** T17

**步骤：**
1. 创建任务级 ToolRegistry/ToolExecutor 和新的默认文件工具缓存。
2. 实现 MCP snapshot 注册函数，只读取当前 `pool.tools`，创建任务自己的 wrapper、ToolSearch 和 discovery set，不注册 listener。
3. 为 load_skill 创建任务自己的 SkillRuntime/SkillExecutor，复用定义来源但不继承激活 scope。
4. SHARED 工具仅在明确无任务状态且线程安全时复用；PARENT_ONLY 一律替换为拒绝适配器。
5. 工厂完成后校验所有 TASK_LOCAL 工具均有任务实例，失败时关闭已创建的临时资源。

**验证：** `python -m pytest tests/test_subagent_tooling.py tests/test_mcp_tools.py tests/test_skill_runtime.py -q -k "registry or cache or mcp or skill or parent_only"`；预期全部通过。

## T19：编写通知截断、预留和溢出失败测试

**文件：** `tests/test_subagent_notifications.py`  
**依赖：** T2

**步骤：**
1. 入队完成、失败和取消通知，断言按 task sequence 稳定排序并使用中文状态摘要。
2. 测试单通知摘要按 4 KiB UTF-8 边界截断并带标记。
3. 测试 reserve 每批最多 16 条、渲染 block 最多 32 KiB，其余通知继续 pending。
4. 测试 reservation 未 commit 前不会被另一次 reserve 重复领取。
5. 测试 commit 只移除当前批，release 恢复原顺序，未知 reservation ID 返回稳定错误。
6. 入队超过 256 条时淘汰最旧通知并累计 dropped_count，下批 block 明确报告丢弃数量。

**验证：** `python -m pytest tests/test_subagent_notifications.py -q`；预期非零退出，失败指向 Inbox 尚未实现。

## T20：实现通知 Inbox 和安全 framework block

**文件：** `src/mycode/subagent/notifications.py`、`src/mycode/subagent/__init__.py`  
**依赖：** T19

**步骤：**
1. 实现有界 pending/reserved 存储和单调 reservation ID。
2. 实现 UTF-8 摘要截断、16 条/32 KiB 批次限制和 dropped_count。
3. reserve 生成单个稳定 `PromptContextBlock`，内容只含任务 ID、终态、摘要和 usage。
4. commit/release/clear 保持幂等或返回稳定错误，不启动模型调用。
5. 在预留恢复和最多一次投递不变量处添加中文注释。

**验证：** `python -m pytest tests/test_subagent_notifications.py -q`；预期全部通过。

## T21：编写任务管理器基本调度失败测试

**文件：** `tests/test_subagent_tasks.py`  
**依赖：** T2、T20

**步骤：**
1. 使用 Event 控制的 fake runner 提交 5 个任务，断言前 4 个 running、第 5 个 queued。
2. 释放一个运行任务，断言第 5 个自动启动且只调用 runner 一次。
3. 提交多个 queued 任务，按 sequence 逐一释放槽位，断言严格 FIFO。
4. 测试 foreground/background 共享同一 max_concurrency，不存在后台专用旁路。
5. 测试队列已有 64 个等待项时下一提交返回 `task_queue_full` 且不分配 ID。
6. 测试 ID 为 `task-000001` 单调增长，list 返回轻量 summary 且不含 detail result。

**验证：** `python -m pytest tests/test_subagent_tasks.py -q -k "slots or fifo or queue or id or summary"`；预期非零退出，失败指向任务管理器缺失。

## T22：实现统一执行槽、FIFO 和轻量查询

**文件：** `src/mycode/subagent/tasks.py`  
**依赖：** T21

**步骤：**
1. 实现私有任务控制块、单调 ID/sequence、records、running 集合和 FIFO deque。
2. submit 在锁内判断队列上限，创建控制块并决定立即运行或 queued。
3. runner 完成释放槽位后只启动队首任务，确保每个 runner 只包装成一个 asyncio.Task。
4. list 构造不含详细结果的 `SubAgentTaskSummary`，get 返回完整不可变快照。
5. 对未知 ID、关闭后提交和非法状态转换返回稳定中文错误。

**验证：** `python -m pytest tests/test_subagent_tasks.py -q -k "slots or fifo or queue or id or summary"`；预期全部通过。

## T23：编写终态竞争、脱离、留存和清理失败测试

**文件：** `tests/test_subagent_tasks.py`  
**依赖：** T22

**步骤：**
1. 用屏障同时触发 complete 和 detach，重复运行确定性分支，断言只能内联或通知其一。
2. 测试 detached 只从 False 变 True，重复 detach 幂等；queued detach 不改变队列位置。
3. 测试 completed/failed/cancelled 每个任务只写一次终态、只释放一次槽、最多入队一次通知。
4. 完成超过 256 个终态，断言只淘汰最旧终态，running/queued 不被淘汰，旧 ID 查询为 task_not_found。
5. cancel_all_and_clear 立即取消 queued，向 running 发取消信号并等待 runner；重复 clear 幂等。
6. 模拟 runner 15 秒宽限期后仍未结束，断言 asyncio task 被取消、异常被消费且 records/inbox 清空。

**验证：** `python -m pytest tests/test_subagent_tasks.py -q -k "race or detach or final or retain or clear or grace"`；预期非零退出，失败指向高级状态转换尚未实现。

## T24：实现一次终态结算、脱离、留存和全量清理

**文件：** `src/mycode/subagent/tasks.py`  
**依赖：** T23

**步骤：**
1. 在单一 asyncio.Lock 临界区实现 detach、finalize、槽位释放和通知入队。
2. finalize 规范化 result/error/usage，依据 detached 决定通知或前台 completion Future。
3. 实现按 sequence 的终态淘汰，不操作活动任务。
4. 实现 cancel_all_and_clear 的停止接收、queued 取消、running 信号、15 秒宽限和强制 task cancel。
5. 清空 records、队列、通知、当前计数并让 manager 可被 clear 后的新会话复用；close 后永久拒绝提交。

**验证：** `python -m pytest tests/test_subagent_tasks.py tests/test_subagent_notifications.py -q`；预期全部通过。

## T25：编写独立运行时和 usage 失败测试

**文件：** `tests/test_subagent_runtime.py`  
**依赖：** T6、T12、T14、T18

**步骤：**
1. scripted LLM 无工具直接返回文本，断言 completed、rounds=1、detail/summary 和 usage。
2. scripted LLM 先调用 fake 工具再返回文本，断言真实 AgentLoop 循环、memory 和 Hook 被使用。
3. 测试定义式按角色 tier 选择映射模型，inherit 使用父模型；Fork 总是父模型和父 max_rounds。
4. 测试达到 max rounds、LLM ERROR、Prompt 错误、无最终文本分别产生稳定 failed report。
5. 测试 cancel_event 取消运行中的模型或工具等待，报告 cancelled 且执行器异常被消费。
6. 两个并发 runtime 修改 memory、permission、cache、Skill、Hook once 和 usage，断言互不影响且父状态不变。
7. 测试任一模型轮次缺 usage 字段后对应任务总字段为 None，不从 input/output 推导 total。

**验证：** `python -m pytest tests/test_subagent_runtime.py -q`；预期非零退出，失败指向 runtime/factory 尚未实现。

## T26：实现独立 AgentLoop 运行时和工厂

**文件：** `src/mycode/subagent/runtime.py`、`src/mycode/subagent/__init__.py`  
**依赖：** T25

**步骤：**
1. Factory 校验请求，解析定义式角色或 Fork，选择 LLM、max rounds、prompt builder 和权限档位。
2. 为每个任务创建独立 memory、临时上下文管理器、工具注册表、权限、Skill、Hook runtime 和 usage accumulator。
3. Runtime 消费 AgentEvent，记录见过的 round、每轮 usage、最终文本、错误和取消。
4. 对结果与错误执行有界规范化，生成 terminal `SubAgentExecutionReport`，不直接修改 TaskManager。
5. cancel_event 触发时取消当前 AgentLoop 消费 task，并在 finally 关闭任务临时资源。

**验证：** `python -m pytest tests/test_subagent_runtime.py tests/test_agent_loop.py -q`；预期全部通过。

## T27：编写 Service 前后台等待和实时查询失败测试

**文件：** `tests/test_subagent_service.py`  
**依赖：** T24、T26

**步骤：**
1. 前台任务在阈值前完成，断言 inline=True、详细结果返回且无通知。
2. 显式后台定义式和 Fork 提交后立即 detached，返回 task ID 和 queued/running 状态。
3. 用注入式 waiter 触发从提交时计算的 120 秒超时，断言任务继续且响应 inline=False。
4. detach_active 在有活动前台任务时解除等待；无活动任务返回 None；同一时刻只登记一个 attached ID。
5. list_tasks 返回 sequence 排序 summary，get_task 返回 detail/usage；未知 ID 为 task_not_found。
6. clear 允许新会话重新提交，close 后永久拒绝；两者都清理 active ID 并幂等。

**验证：** `python -m pytest tests/test_subagent_service.py -q`；预期非零退出，失败指向 Service 尚未实现。

## T28：实现统一 Service 的等待、脱离、查询和生命周期

**文件：** `src/mycode/subagent/service.py`  
**依赖：** T27

**步骤：**
1. run 在创建 runtime 后提交 TaskManager；Fork 和 requested_background 立即 detach。
2. 前台 run 设置 active ID，同时等待 terminal、detach 和注入式 timeout，finally 只清自己的 active ID。
3. 竞争结果完全采用 TaskManager 锁内决定，不在 Service 二次结算。
4. 实现 list/get 只读转发、detach_active、clear 和 close。
5. 对任务输入 64 KiB 上限、未知角色、快照缺失和 runtime 创建失败返回设计错误码。

**验证：** `python -m pytest tests/test_subagent_service.py tests/test_subagent_tasks.py -q`；预期全部通过。

## T29：编写固定 Agent 工具 schema 和 action 失败测试

**文件：** `tests/test_subagent_tool.py`  
**依赖：** T28

**步骤：**
1. 快照断言工具名 `Agent`、ToolKind.WRITE、PARENT_ONLY、定义级超时=foreground timeout+5，parameters 在目录和任务变化后完全相同。
2. 测试 run/defined 必须有 type/task/role，background 可选；禁止 task_id 和未知字段。
3. 测试 run/fork 必须有 task、禁止 role/background 并规范化为后台。
4. 测试 list 禁止其他参数，get 只接受 task_id。
5. 测试 run 从 ParentAgentSnapshotStore 读取当前快照；list/get 不要求父快照。
6. 测试 Service 成功、queued、failed、未知任务和参数错误都转换为稳定中文 ToolResult。

**验证：** `python -m pytest tests/test_subagent_tool.py -q`；预期非零退出，失败指向 AgentTool 尚未实现。

## T30：实现固定 AgentTool 和结构化响应

**文件：** `src/mycode/subagent/tool.py`、`src/mycode/subagent/__init__.py`  
**依赖：** T29

**步骤：**
1. 定义固定 oneOf JSON schema，三个 action 均设置 additionalProperties=false。
2. 实现严格 action 级参数校验，不根据目录动态生成 enum。
3. run 构造 `SubAgentLaunchRequest` 并调用 Service；list/get 调用实时只读接口。
4. 响应只包含设计字段，列表不包含详细结果，详情使用规范化 result 和五类 usage。
5. ToolDefinition 使用 WRITE、PARENT_ONLY 和 `foreground_timeout_seconds + 5` 的本地超时。

**验证：** `python -m pytest tests/test_subagent_tool.py tests/test_subagent_service.py -q`；预期全部通过。

## T31：编写父快照和通知安全点接入失败测试

**文件：** `tests/test_subagent_agent.py`、`tests/test_agent_loop.py`  
**依赖：** T14、T20

**步骤：**
1. 用 fake `ParentAgentSnapshotStore` 运行两轮父 `AgentLoop`，断言每次 `prepare_auto()` 成功后都以该轮最终 `PromptBuildResult`、主模型 ID、`AgentConfig.max_rounds` 和实时权限档位更新快照。
2. 让首轮工具调用改变下一轮可见 schema，断言第二轮快照使用新 schema，首轮已创建的深冻结快照保持不变。
3. 给 Inbox 预置通知，断言每轮只调用一次 `reserve()`，预留的单个 framework block 参与当轮 Prompt 构建且不会写入父 conversation memory。
4. 断言 `prepare_auto()` 成功返回后调用 `commit(reservation_id)`；压缩、Prompt 构建或父快照更新失败时调用 `release(reservation_id)`，同一 reservation 不会同时 commit 和 release。
5. 断言无通知时不改变 framework blocks；未注入 snapshot store/Inbox 的现有 `AgentLoop` 构造和全部旧行为保持兼容。
6. 断言通知提交不会等待模型成功：后续 LLM 错误不重新释放或重复投递已进入该请求的通知。

**验证：** `python -m pytest tests/test_subagent_agent.py tests/test_agent_loop.py -q -k "snapshot or notification or compatibility"`；预期非零退出，失败指向主 AgentLoop 尚未接入快照和通知安全点。

## T32：实现父 AgentLoop 快照更新和通知安全点

**文件：** `src/mycode/agent/loop.py`  
**依赖：** T31

**步骤：**
1. 给 `AgentLoop` 增加可选的 `parent_snapshot_store`、`notification_inbox`、主模型 ID 和权限档位 provider 依赖，缺省值保持低层测试及子 Agent runtime 的现有构造行为。
2. 在 `_prepare_round_request()` 开始时从 Inbox 预留一批通知，把 reservation 的单个 block 追加到该轮 framework blocks，不修改 `state.base_framework_blocks` 或 conversation memory。
3. `prepare_auto()` 成功后用最终 request 更新 `ParentAgentSnapshotStore`，再提交 reservation；主模型 ID、最大轮次和权限档位均取当前轮真实值。
4. 对压缩、Prompt、快照更新和其他构建异常释放 reservation 后沿用现有错误转换路径；提交后发生的模型错误不得释放已提交批次。
5. 把 reserve/commit/release 放进每轮独立局部状态，并在“构建失败可重试、成功请求最多投递一次”不变量处添加简洁中文注释。

**验证：** `python -m pytest tests/test_subagent_agent.py tests/test_agent_loop.py -q`；预期全部通过。

## T33：编写 Session 生命周期和 TUI Ctrl+B 失败测试

**文件：** `tests/test_subagent_session_tui.py`、`tests/test_session.py`、`tests/test_slash_tui.py`  
**依赖：** T28、T32

**步骤：**
1. 给 `ChatSession` 注入 fake `SubAgentService`，测试 `detach_active_subagent()` 返回当前任务快照；无附着任务时返回 `None` 且不改变主 Agent 状态。
2. 测试 `clear_async()` 在清主 memory、Skill、权限和模式前等待 `service.clear()`；`close()` 在会话结束前等待 `service.close()`，重复 clear/close 不重复结算任务。
3. 用 fake prompt-toolkit input 在 `_render_stream()` 期间发送 `Keys.ControlB`，断言只调用一次 `detach_active_subagent()`，显示任务 ID，事件流继续消费到父 Agent 的最终响应。
4. 测试 Ctrl+B 没有活动任务时不报错、不取消流、不输出虚假任务 ID；普通按键不触发 detach。
5. 测试流结束或异常后按键监听器必定解除，下一次输入 prompt 不残留 handler；两次流式请求分别安装独立监听周期。
6. 测试 `_approval_provider()` 读取审批输入时暂停后台按键监听，审批字符和 Ctrl+B 不互相吞掉；无 prompt-toolkit 控制台时保留现有 plain input 回退。

**验证：** `python -m pytest tests/test_subagent_session_tui.py tests/test_session.py tests/test_slash_tui.py -q`；预期非零退出，失败指向 Session service 生命周期和流式 Ctrl+B 监听尚未接入。

## T34：实现 Session 脱离/清理和流式 Ctrl+B

**文件：** `src/mycode/session.py`、`src/mycode/tui.py`  
**依赖：** T33

**步骤：**
1. `ChatSession` 增加可选 `subagent_service`，实现 `detach_active_subagent()`，并在 `clear_async()`/`close()` 中分别等待 service 的 clear/close 后再完成现有会话状态与 Hook 生命周期。
2. 在 TUI 中封装仅覆盖 `_render_stream()` 生命周期的 prompt-toolkit input 监听器，识别 `Keys.ControlB` 后在当前 event loop 调度一次异步 detach，不把按键写入下一条用户输入。
3. detach 成功时输出稳定中文任务 ID 和后台状态；返回 `None` 时静默继续，detach 异常只记录安全日志且不终止父事件流。
4. 用 `try/finally` 解除 input attach/raw-mode 上下文并消费监听任务异常，保证正常完成、Agent 错误、取消和 TUI 退出均不泄漏 handler。
5. 审批输入开始前暂停流式监听、审批结束后仅在原流仍活动时恢复，保持现有审批选项、Slash prompt 和 plain input 行为。

**验证：** `python -m pytest tests/test_subagent_session_tui.py tests/test_session.py tests/test_slash_tui.py -q`；预期全部通过。

## T35：编写 `/tasks` 和 `/task <id>` 失败测试

**文件：** `tests/test_subagent_slash_cli.py`、`tests/test_slash_builtins.py`、`tests/test_slash_tui.py`  
**依赖：** T28、T34

**步骤：**
1. 断言默认 Slash 注册表新增公开 `/tasks` 和 `/task`，帮助顺序稳定，详情用法严格为 `/task <id>`，既有命令、别名和隐藏 `/exit` 保持不变。
2. 测试 `/tasks` 拒绝额外参数并调用 controller 的实时列表接口；空列表显示“当前会话没有子 Agent 任务”。
3. 用乱序 fake summary 测试列表按 `sequence` 显示 ID、类型、角色、状态、轮次、detached、错误码和五类 usage，不包含详细 result。
4. 测试 `/task <id>` 必须恰有一个参数并调用实时详情接口，输出类型、角色、状态、轮次、完整规范化结果、错误码/摘要和五类 usage。
5. 对所有缺失 usage 字段显示“未知”，定义式无角色或无错误字段显示稳定中文占位，不把 `None` 渲染给用户。
6. 测试未知或已淘汰任务将 `task_not_found` 转成稳定中文错误；TUI controller 只转发 Session/Service，不从聊天历史构造状态。

**验证：** `python -m pytest tests/test_subagent_slash_cli.py tests/test_slash_builtins.py tests/test_slash_tui.py -q -k "tasks or task_detail or task_not_found"`；预期非零退出，失败指向任务 Slash 接口和格式化器尚未实现。

## T36：实现实时任务 Slash 查询

**文件：** `src/mycode/session.py`、`src/mycode/tui.py`、`src/mycode/slash/controller.py`、`src/mycode/slash/builtins.py`  
**依赖：** T35

**步骤：**
1. 给 Session 增加 `list_subagent_tasks()` 和 `get_subagent_task(task_id)` 只读转发；未装配 service 时返回空列表或稳定 unavailable 错误以保持低层测试兼容。
2. 扩展 `SlashCommandController` 和 `ChatTUI` 的异步任务列表/详情方法，直接调用 Session，不缓存查询结果。
3. 注册 `/tasks` 与 `/task <id>`，执行严格参数数量检查并保持公开帮助顺序和补全信息稳定。
4. 实现中文 summary/detail 格式化，按 sequence 排序，统一显示 kind、role、state、detached、rounds、result/error 和 input/output/total/cache-read/cache-write；缺失值显示“未知”。
5. 只捕获设计的任务查询错误并显示 `task_not_found` 中文消息，其他异常记录日志并显示稳定 `subagent_task_query_failed`，不影响后续 Slash 命令。

**验证：** `python -m pytest tests/test_subagent_slash_cli.py tests/test_slash_builtins.py tests/test_slash_tui.py -q`；预期全部通过。

## T37：编写 CLI 装配、严格示例和文档失败测试

**文件：** `tests/test_subagent_slash_cli.py`、`tests/test_slash_cli.py`、`tests/test_subagent_docs.py`、`tests/test_docs.py`、`tests/test_config.py`  
**依赖：** T6、T8、T12、T18、T30、T32、T34、T36

**步骤：**
1. 用 fake factory 记录 CLI 构造顺序，断言先建立不含 `Agent` 的基础工具，再校验后台工具和角色候选，然后创建 catalog/snapshot store/Inbox/runtime/task manager/service，最后注册唯一 `AgentTool`。
2. 断言主 `AgentLoop` 收到 snapshot store、Inbox、主模型 ID 和实时权限 provider；`ChatSession` 收到同一个 service；TUI、Slash 和 AgentTool 读取的也是该服务实例。
3. 测试 `config.sub_agent is None`、未知后台工具、缺失三档映射和 Agent 名称冲突均在启动阶段返回非零中文配置错误，不启动 TUI 或任何子任务。
4. 测试无效项目角色产生诊断并回退内置角色，诊断按稳定顺序输出且不阻止其他有效角色启动。
5. 断言应用正常退出和 TUI 异常时都先关闭 Session/Service，再关闭项目记忆、上下文管理器和 MCP pool；重复关闭保持幂等。
6. 断言三份 `examples/mycode.*.yaml` 都有非空主 model 和完整 `sub_agent.models.haiku/sonnet/opus`，并能通过真实 `load_config()`。
7. 断言 README 删除 Anthropic 不支持工具的旧边界，记录固定 Agent actions、角色来源、配置默认值、后台/`Ctrl+B`、`/tasks`、`/task <id>`、通知安全点和会话清理。
8. 通过构建 wheel 或检查 setuptools 配置，断言安装产物包含三个 `subagent/builtins/*.md`，不依赖源码目录偶然存在。

**验证：** `python -m pytest tests/test_subagent_slash_cli.py tests/test_slash_cli.py tests/test_subagent_docs.py tests/test_docs.py tests/test_config.py -q`；预期非零退出，失败集中在 CLI 尚未装配领域服务以及示例/README 尚未更新。

## T38：实现 CLI 总装配并更新配置示例和 README

**文件：** `src/mycode/cli.py`、`examples/mycode.anthropic.yaml`、`examples/mycode.openai-responses.yaml`、`examples/mycode.openai-chat.yaml`、`README.md`、`pyproject.toml`  
**依赖：** T37

**步骤：**
1. CLI 防御性校验非空 `sub_agent`，在基础工具、MCP、Skill 和父专用工具注册完成后校验后台工具名，并把保留名 `Agent` 纳入角色字段校验候选。
2. 初始化 `AgentCatalog` 并稳定报告诊断，创建 `ParentAgentSnapshotStore`、`SubAgentNotificationInbox`、`SubAgentRuntimeFactory`、`SubAgentTaskManager` 和唯一 `SubAgentService`。
3. 用该 service 和 snapshot store 创建固定 `AgentTool` 并只注册一次；父 `ToolExecutor` 继续引用步骤 1 已建立的同一可扩展 registry，随后创建主 AgentLoop，避免 runtime factory、父 registry 与 AgentTool 的装配环。
4. 把 snapshot store、Inbox、主模型 ID 和 permissions.effective_mode provider 注入主 AgentLoop，把 service 注入 ChatSession；保持 Skill Slash 动态刷新和现有 TUI 参数不变。
5. 在 CLI `finally` 中优先等待 Session/Service 关闭，再按项目记忆、上下文管理器、MCP pool 的既有资源顺序清理；部分初始化失败只关闭已创建资源。
6. 给三份主协议示例增加完整模型档位映射，三个档位显式复用各示例的非空主模型字符串；修复 openai-chat 示例中的空 model/base_url。
7. 更新 README 的阶段说明、配置片段、角色文件格式、统一 Agent 工具、定义式/Fork、前后台切换、实时查询、通知、生命周期和 Anthropic 工具往返边界。
8. 保持并验证 `pyproject.toml` 的内置角色 package-data 声明，源码运行和 wheel 安装使用同一资源发现路径。

**验证：** `python -m pytest tests/test_subagent_slash_cli.py tests/test_slash_cli.py tests/test_subagent_docs.py tests/test_docs.py tests/test_config.py -q`；预期全部通过。

## T39：编写并通过 Stage 12 端到端测试

**文件：** `tests/test_subagent_e2e.py`  
**依赖：** T38

**步骤：**
1. 以临时 cwd/home、真实领域装配和 scripted 父/子 LLM 运行定义式前台任务，断言模型通过固定 `Agent` schema 启动任务、子 Agent 使用真实 AgentLoop 多轮工具并把终态详情内联给父 Agent。
2. 运行 Fork，逐项比较首次子请求与父快照的 messages/tools 前缀，断言立即返回后台 ID、后续父消息未进入快照且 fake cache-read usage 可在详情观察。
3. 用可控 Event 同时提交 5 个前后台任务，断言 4 个 running、第 5 个 queued、释放槽位后严格 FIFO，所有 runner 各执行一次。
4. 让已脱离任务完成，断言不会自行触发父 LLM；下一轮父安全点只注入一次有界通知，`Agent(list|get)` 随后读取实时状态和详细结果。
5. 经真实 Slash dispatcher 执行 `/tasks` 和 `/task <id>`，断言与 `Agent(list|get)` 的同一快照字段一致且未知 ID 为稳定中文错误。
6. 在运行和排队任务并存时执行 `/clear`，断言全部取消、安全收尾、结果/通知/ID 计数清空，新会话重新从 `task-000001` 开始且旧任务不可查询。
7. 注入一个角色解析、Hook、工具或 LLM 失败，断言错误只结算当前任务，另一个任务和普通父聊天继续完成。

**验证：** `python -m pytest tests/test_subagent_e2e.py -q`；预期全部通过，且不访问网络、真实 API、真实用户角色或工作区外文件。端到端场景只验证 T1-T38 已测试先行实现的行为，不新增未先覆盖的生产行为。

## T40：执行 Stage 12 完整回归

**文件：** 无预期文件修改；仅执行端到端、定向回归、完整测试和编译检查  
**依赖：** T39

**步骤：**
1. 单独重跑 `tests/test_subagent_e2e.py`，确认 defined、Fork、FIFO、通知、查询、清理和故障隔离端到端场景稳定通过。
2. 运行全部名称包含 subagent 的测试，确认领域模块及新接入测试没有遗漏。
3. 运行 Anthropic、工具超时、AgentLoop、Session、Slash/TUI、CLI 和文档定向回归，确认 Stage 12 的窄接入点没有破坏既有行为。
4. 运行完整 pytest；若失败，停止验收并回到拥有该行为的最早任务修复和重跑，不在本任务引入未计划功能。
5. 运行 `compileall`，确认 `src` 和 `tests` 均无语法或导入编译错误。
6. 检查测试输出没有真实网络/API 访问、真实用户角色读取、未消费异常或遗留 asyncio task 警告。

**验证：** 依次运行：

```powershell
python -m pytest tests/test_subagent_e2e.py -q
python -m pytest tests -q -k subagent
python -m pytest tests/test_anthropic_protocol.py tests/test_tool_executor.py tests/test_agent_loop.py tests/test_session.py tests/test_slash_builtins.py tests/test_slash_cli.py tests/test_slash_tui.py tests/test_docs.py -q
python -m pytest -q
python -m compileall -q src tests
```

预期所有 pytest 命令通过，`compileall` 退出码为 0，测试期间没有真实网络/API 请求、真实用户角色读取或未消费 asyncio task 警告。

## 执行顺序

主要依赖分支：

```text
T1 -> T2
      |-> T3 -> T4 -> T5 -> T6
      |-> T7 -> T8
      |-> T9 -> T10 -> T11 -> T12
      `-> T19 -> T20 -> T21 -> T22 -> T23 -> T24

T6 + T8 -> T13 -> T14 -> T15 -> T16 -> T17 -> T18
T6 + T12 + T14 + T18 -> T25 -> T26
T24 + T26 -> T27 -> T28 -> T29 -> T30
T14 + T20 -> T31 -> T32
T28 + T32 -> T33 -> T34
T28 + T34 -> T35 -> T36
T6 + T8 + T12 + T18 + T30 + T32 + T34 + T36 -> T37 -> T38 -> T39 -> T40
```

合法拓扑批次如下；同一批次中的任务可并行，下一批次必须等待上一批次中自己的依赖完成：

```text
01: T1
02: T2
03: T3, T7, T9, T19
04: T4, T8, T10, T20
05: T5, T11, T21
06: T6, T12, T22
07: T13, T23
08: T14, T24
09: T15, T31
10: T16, T32
11: T17
12: T18
13: T25
14: T26
15: T27
16: T28
17: T29, T33
18: T30, T34
19: T35
20: T36
21: T37
22: T38
23: T39
24: T40
```

可并行执行的主分支：

- T3-T6（角色）、T7-T8（工具元数据）、T9-T12（Anthropic）和 T19-T24（通知/调度）在 T2 后可并行推进。
- T31-T32（父 Agent 安全点）在 T14、T20 完成后可与 T25-T30（运行时/服务/工具）并行推进。
- T33-T36 必须等待 Service 和父 Agent 安全点均可用；T37 起进入单线总装配和端到端阶段。

## 设计与需求覆盖

| `plan.md` 组件/接入点 | 实施任务 |
|---|---|
| `subagent.models`、`subagent.config` | T1-T2 |
| `subagent.loader`、`subagent.catalog`、内置角色/package data | T3-T6 |
| 工具作用域、定义级超时、Anthropic 工具往返 | T7-T12 |
| `subagent.context`、父请求冻结与 Fork prompt builder | T13-T14、T31-T32 |
| `subagent.tooling`、权限取严、任务级工具/MCP/Skill 隔离 | T15-T18 |
| `subagent.notifications` | T19-T20、T31-T32 |
| `subagent.tasks` | T21-T24 |
| `subagent.runtime` | T25-T26 |
| `subagent.service` | T27-T28、T33-T36 |
| 固定 `AgentTool` | T29-T30 |
| 主 `AgentLoop`、Session、TUI、Slash 接入 | T31-T36 |
| CLI 装配、示例配置、README | T37-T38 |
| 端到端与完整回归 | T39-T40 |

| Spec 需求 | 覆盖任务 |
|---|---|
| F1 统一 Agent 工具 | T7-T12、T29-T30、T37-T39 |
| F2 角色定义格式 | T1-T4 |
| F3 角色来源与覆盖 | T3-T4、T37-T39 |
| F4 内置角色 | T5-T6、T37-T38 |
| F5 主配置 | T1-T2、T37-T38 |
| F6 定义式执行 | T13-T18、T25-T30、T39 |
| F7 Fork 式执行 | T13-T18、T25-T30、T39 |
| F8 运行时隔离 | T7-T8、T17-T18、T25-T26、T39 |
| F9 非交互跑到底 | T15-T18、T25-T26、T39 |
| F10 权限约束 | T15-T18、T25-T26、T39 |
| F11 多层工具防线 | T7-T8、T15-T18、T25-T26、T39 |
| F12 后台切换 | T21-T24、T27-T28、T33-T34、T39 |
| F13 调度与状态 | T21-T28、T35-T36、T39 |
| F14 结果与通知 | T19-T24、T31-T32、T39 |
| F15 任务查询 | T27-T30、T35-T36、T39 |
| F16 会话清理 | T23-T24、T27-T28、T33-T34、T37-T39 |
| N1 领域边界 | T1-T30、T31-T38 |
| N2 状态隔离 | T17-T18、T25-T26、T39-T40 |
| N3 缓存兼容 | T13-T14、T25-T26、T39-T40 |
| N4 权限单调性 | T15-T18、T25-T26、T39-T40 |
| N5 资源有界 | T1-T4、T19-T24、T27-T30、T39-T40 |
| N6 并发安全 | T21-T28、T31-T34、T39-T40 |
| N7 确定性 | T3-T4、T13-T24、T29-T30、T35-T40 |
| N8 故障隔离 | T3-T4、T15-T18、T23-T28、T31-T40 |
| N9 可观测性 | T1-T2、T19-T30、T35-T40 |
| N10 中文规范 | T1-T6、T13-T20、T29-T40 |
| N11 兼容性 | T7-T12、T31-T40 |
| N12 测试隔离 | T1、T3、T5、T7、T9、T11、T13、T15、T17、T19、T21、T23、T25、T27、T29、T31、T33、T35、T37、T39-T40 |

## 自检结论

- T1-T40 均包含具体文件、显式依赖、可执行步骤和带预期结果的验证命令。
- 所有任务引用均指向已定义任务，依赖图无环，拓扑批次覆盖全部 40 个任务。
- `plan.md` 中的领域组件、协议改造、主循环/Session/TUI/Slash/CLI 接入、资源与文档文件均有任务归属。
- F1-F16 和 N1-N12 均有实现任务、测试任务或完整回归入口，没有把团队编排、任务取消/重跑、跨会话持久化和文件冲突处理带入本阶段。
- 文档不存在未决占位标记、模糊跨任务类比或未定义接口；类型名、方法名、限制值和 T1-T30 已定义内容保持一致。
