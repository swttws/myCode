# myCode Stage 11：Hook 系统任务拆解

## 执行约束

- 严格按测试先行执行：每个任务先写能证明缺失行为的测试并确认它按预期失败，再写最小实现并重跑目标测试。
- Hook 实现集中在 `src/mycode/hook/`；Agent、Session、CLI 和 TUI 只做装配与生命周期触发。
- Hook 只能收紧工具执行，不能放宽权限、跳过审批、扩大路径边界或改变工具读写分类。
- 运行期 Hook 失败只记日志，不中断 Agent 主流程；配置错误在启动或显式加载阶段失败。
- 只在拦截回填、失败隔离、异步限制和权限规范化复用处添加简洁中文注释。
- 自动化测试使用临时目录、fake LLM、fake executor、fake HTTP transport 和安全命令；不得访问真实网络、真实用户配置或真实凭据。
- 本阶段不实现子 Agent 真实运行、`once` 持久化、显式优先级和热加载。

## 文件清单

### 新建实现文件

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `src/mycode/hook/__init__.py` | Hook 包公共导出与包级说明 |
| 新建 | `src/mycode/hook/models.py` | 事件、动作、条件、规则、上下文、结果和错误类型 |
| 新建 | `src/mycode/hook/config.py` | `mycode.hooks.yaml` 加载、字段白名单和集中校验 |
| 新建 | `src/mycode/hook/matcher.py` | 条件展平、精确、glob、正则和反向匹配 |
| 新建 | `src/mycode/hook/context.py` | 从消息、工具调用和权限规范化结果构造 Hook 上下文 |
| 新建 | `src/mycode/hook/actions.py` | command、prompt、http、sub_agent 动作执行与失败隔离 |
| 新建 | `src/mycode/hook/runtime.py` | 规则调度、once 状态、Prompt 注入、工具前拦截和空运行时 |

### 修改实现与文档文件

| 操作 | 文件 | 职责 |
|---|---|---|
| 修改 | `src/mycode/agent/loop.py` | 接入 Hook 生命周期事件、Prompt block 合并、工具前拦截和工具后事件 |
| 修改 | `src/mycode/session.py` | 接入会话开始、会话结束和清空事件 |
| 修改 | `src/mycode/cli.py` | 增加 `--hook-config`、加载配置、装配运行时和启动错误报告 |
| 修改 | `src/mycode/tui.py` | 正常退出路径调用 `session.close()` |
| 新建 | `examples/mycode.hooks.yaml` | 声明式 Hook 示例 |
| 修改 | `README.md` | 说明 Hook 文件、事件、条件、动作、拦截和不做范围 |

### 新建测试文件

| 操作 | 文件 | 覆盖范围 |
|---|---|---|
| 新建 | `tests/test_hook_config.py` | YAML 加载、字段校验、事件、动作、执行控制 |
| 新建 | `tests/test_hook_matcher.py` | 条件结构、展平上下文和匹配语法 |
| 新建 | `tests/test_hook_context.py` | 工具参数权限规范化、消息和工具结果上下文 |
| 新建 | `tests/test_hook_actions.py` | command、prompt、http、sub_agent 和后台失败隔离 |
| 新建 | `tests/test_hook_runtime.py` | once、顺序、Prompt 注入、拦截结果和运行期失败 |
| 新建 | `tests/test_hook_agent.py` | AgentLoop 生命周期触发、Prompt 注入、工具前拦截和权限兼容 |
| 新建 | `tests/test_hook_session_cli.py` | Session、CLI、TUI 装配和启动失败 |

### 按行为调整的现有测试

| 操作 | 文件 | 调整原因 |
|---|---|---|
| 修改 | `tests/test_agent_loop.py` | AgentLoop 构造可接收 Hook runtime，现有行为保持兼容 |
| 修改 | `tests/test_session.py` | Session 增加 start/close/clear Hook 触发但默认无 Hook 时行为不变 |
| 修改 | `tests/test_slash_cli.py` | CLI 装配参数增加 Hook 配置依赖，现有斜杠启动测试需补默认值 |

## 任务列表

## T1：编写 Hook 模型与配置加载失败测试

**文件：** `tests/test_hook_config.py`  
**依赖：** 无

**步骤：**
1. 测试缺失默认配置等价于 `version=1` 且规则为空。
2. 测试合法 YAML 能解析 `event`、省略 `if`、`action`、`once`、`background` 和 `timeout_seconds`。
3. 测试缺少 `event`、缺少 `action`、非法版本、未知顶层字段、未知规则字段、重复规则 ID 都报 `HookConfigError`。
4. 测试未知事件、未知动作、动作必填字段缺失、非法超时时间和非法布尔执行控制都报错。
5. 测试 `tool_before` 配置 `background: true` 报错。
6. 运行目标测试，确认失败原因是 `mycode.hook` 尚未实现。

**验证：** `python -m pytest tests/test_hook_config.py -q`；预期非零退出，失败集中在缺失 Hook 配置接口。

## T2：实现 Hook 领域模型与配置加载

**文件：** `src/mycode/hook/__init__.py`、`src/mycode/hook/models.py`、`src/mycode/hook/config.py`  
**依赖：** T1

**步骤：**
1. 定义 `HookEvent`、`HookActionType`、`MatchKind`、`ValueMatcher`、`HookPredicate`、`HookCondition`、`HookAction`、`HookRule`、`HookConfig`、`HookContext`、`HookActionResult`、`HookTriggerResult`、`HookPromptInjection` 和错误类型。
2. 用 `yaml.safe_load` 实现 `load_hook_file()` 和 `load_hook_config()`。
3. 实现默认路径 `<workspace>/mycode.hooks.yaml`，缺失默认文件返回空配置，显式文件缺失由上层校验报错。
4. 按 `plan.md` 字段表执行严格白名单和动作字段校验。
5. 在 `tool_before` 禁止后台异步的分支添加简洁中文注释。

**验证：** `python -m pytest tests/test_hook_config.py -q`；预期全部通过。

## T3：编写条件匹配失败测试

**文件：** `tests/test_hook_matcher.py`  
**依赖：** T2

**步骤：**
1. 测试省略条件时无条件匹配。
2. 测试 `all` 必须全部满足，`any` 任一满足即可，空 mapping 和同时声明 `all/any` 报错。
3. 测试精确字符串、数字、布尔、隐式 glob、显式 `glob:`、正则 `re:` 和映射形式 `regex`。
4. 测试 `!` 前缀和映射 `not: true` 的反向匹配。
5. 测试缺失字段匹配失败，非法正则在配置加载阶段报错。
6. 测试展平上下文包含 `event`、`round_index`、`tool`、`arguments.*`、`raw_arguments.*`、`result.ok`、`message.content` 和 `session.plan_only`。

**验证：** `python -m pytest tests/test_hook_matcher.py -q`；预期非零退出，失败指向 matcher 尚未实现。

## T4：实现条件解析、展平和匹配

**文件：** `src/mycode/hook/matcher.py`、`src/mycode/hook/config.py`  
**依赖：** T3

**步骤：**
1. 实现 `parse_matcher()`，支持标量简写和映射形式。
2. 在配置解析阶段调用正则编译校验，保证非法正则启动前失败。
3. 实现 `flatten_context()`，只放入安全、稳定的事件字段。
4. 实现 `match_condition()`，按 `all` 或 `any` 执行谓词组合。
5. 保持 glob 使用 `fnmatchcase`，与权限规则的大小写规范化输入配合使用。

**验证：** `python -m pytest tests/test_hook_matcher.py tests/test_hook_config.py -q`；预期全部通过。

## T5：编写 Hook 上下文规范化失败测试

**文件：** `tests/test_hook_context.py`  
**依赖：** T4

**步骤：**
1. 构造文件工具定义和 `PathGuard`，验证 `arguments.path` 使用权限规范化后的工作区相对匹配值。
2. 构造查找工具定义，验证缺省 `root` 与权限规则一致规范化为工作区根。
3. 构造命令工具定义，验证 `arguments.command` 折叠首尾和连续空白，但保留引号语义。
4. 验证 `raw_arguments.*` 保留模型原始参数快照。
5. 验证消息事件和工具结果事件能构造包含 `message.*`、`result.*` 的上下文。
6. 验证规范化失败不会抛出破坏主流程的异常，而是生成可匹配失败的安全上下文。

**验证：** `python -m pytest tests/test_hook_context.py -q`；预期非零退出，失败指向上下文构造尚未实现。

## T6：实现 Hook 上下文构造与权限规范化复用

**文件：** `src/mycode/hook/context.py`  
**依赖：** T5

**步骤：**
1. 实现 `build_tool_hook_context()`，内部复用 `permission.policy.build_subject()` 获取规范化参数。
2. 在权限复用处添加中文注释，说明 Hook 不重新决定权限，只借用规范化结果。
3. 实现消息、工具结果、错误和普通事件上下文辅助构造函数。
4. 对规范化异常执行 fail-soft：记录安全诊断，保留原始参数，主流程继续。

**验证：** `python -m pytest tests/test_hook_context.py tests/test_hook_matcher.py -q`；预期全部通过。

## T7：编写动作执行失败测试

**文件：** `tests/test_hook_actions.py`  
**依赖：** T6

**步骤：**
1. 用临时目录执行固定安全命令，验证前台 command 成功时记录 stdout。
2. 验证 command 退出码非 0 和超时返回失败结果且不抛出到调用方。
3. 验证 `background: true` 的 command 立即返回，后台异常被消费并记录日志。
4. 验证 prompt 动作返回输出内容，供运行时转换为注入块。
5. 用 `httpx.MockTransport` 验证 HTTP 方法、URL、头和 JSON 请求体；非 2xx、连接异常和超时只返回失败结果。
6. 验证 sub_agent 动作只返回占位结果，不调用 LLM 或 Agent。

**验证：** `python -m pytest tests/test_hook_actions.py -q`；预期非零退出，失败指向动作执行器尚未实现。

## T8：实现动作执行器和失败隔离

**文件：** `src/mycode/hook/actions.py`  
**依赖：** T7

**步骤：**
1. 实现 `HookActionRunner.run()`，按动作类型分发。
2. command 使用 `asyncio.create_subprocess_shell()`，支持 cwd、env 和超时。
3. HTTP 使用可注入的 `http_client_factory`，测试环境使用 fake transport。
4. 后台动作通过 `create_task()` 保存引用，并在完成回调中读取异常写日志。
5. 对命令、HTTP 和内部异常统一返回 `HookActionResult(ok=False)`，不向上抛出。
6. 在后台异常消费和失败隔离处添加简洁中文注释。

**验证：** `python -m pytest tests/test_hook_actions.py -q`；预期全部通过。

## T9：编写 HookRuntime 调度、once 和 Prompt 注入失败测试

**文件：** `tests/test_hook_runtime.py`  
**依赖：** T8

**步骤：**
1. 验证普通事件按 YAML 声明顺序执行所有命中规则。
2. 验证不匹配条件的规则不执行。
3. 验证 `once` 规则在同一运行时内只执行一次，重建运行时后可再次执行。
4. 验证 prompt 动作转为 `PromptContextBlock`，block ID、kind、priority 和 content 稳定。
5. 验证 `clear_request_state()` 清空当前用户请求内的 Prompt 注入，不清 once 状态。
6. 验证动作失败只记录日志，后续规则继续执行。

**验证：** `python -m pytest tests/test_hook_runtime.py -q -k "trigger or once or prompt or failure"`；预期非零退出，失败指向运行时尚未实现。

## T10：实现运行时普通事件调度和 Prompt 注入

**文件：** `src/mycode/hook/runtime.py`  
**依赖：** T9

**步骤：**
1. 实现 `HookRuntime.trigger()` 的事件筛选、条件匹配、once 检查和动作顺序执行。
2. 实现 Prompt 动作结果收集，并转换为优先级 `-150` 的 `PromptContextBlock`。
3. 实现 `clear_request_state()`，只清理本次请求注入和已完成后台任务引用。
4. 实现 `NullHookRuntime`，无规则时返回空结果并保持接口兼容。
5. 在运行期失败隔离处添加中文注释。

**验证：** `python -m pytest tests/test_hook_runtime.py -q -k "trigger or once or prompt or failure"`；预期全部通过。

## T11：编写工具前拦截运行时失败测试

**文件：** `tests/test_hook_runtime.py`  
**依赖：** T10

**步骤：**
1. 构造两个命中的 `tool_before` 规则，第一条 `block: true`，验证只产生第一条拦截结果。
2. 验证拦截结果为 `ToolResult(ok=False)`，包含 `tool_call_id`、`reason_code=hook_blocked` 和 `hook_rule_id`。
3. 验证拦截 reason 优先取 `reason`，其次取 prompt `content`，最后使用固定中文兜底。
4. 验证未声明 `block` 的 tool_before 规则只执行动作，不阻止工具。
5. 验证 `tool_after` 不能产生 `blocked_tool_result`。

**验证：** `python -m pytest tests/test_hook_runtime.py -q -k "before_tool or blocked or tool_after"`；预期非零退出，失败指向拦截逻辑尚未实现。

## T12：实现工具前拦截和工具后事件

**文件：** `src/mycode/hook/runtime.py`  
**依赖：** T11

**步骤：**
1. 实现 `before_tool()`，构造工具上下文并按声明顺序执行 `tool_before` 规则。
2. 第一条 `HookActionResult.blocked` 或动作配置 `block` 命中后构造结构化工具结果并停止后续规则。
3. 实现 `after_tool()`，只触发动作，不允许产生拦截。
4. 在拦截回填处添加中文注释，说明必须模拟工具结果而不是 Agent 错误。

**验证：** `python -m pytest tests/test_hook_runtime.py tests/test_hook_context.py -q`；预期全部通过。

## T13：编写 AgentLoop Prompt 注入和生命周期失败测试

**文件：** `tests/test_hook_agent.py`、`tests/test_agent_loop.py`  
**依赖：** T12

**步骤：**
1. 用记录型 HookRuntime 验证用户请求开始、用户消息、模型轮次开始、模型轮次结束、助手消息和请求结束事件按预期触发。
2. 验证 prompt 注入进入下一次模型请求的 framework context，且不写入普通 memory。
3. 验证请求正常结束、模型错误和取消时都会清理本次请求的 Hook prompt blocks。
4. 验证未传 Hook runtime 时现有 AgentLoop 测试继续通过。

**验证：** `python -m pytest tests/test_hook_agent.py tests/test_agent_loop.py -q -k "lifecycle or prompt_injection or compatibility"`；预期非零退出，失败指向 AgentLoop 尚未接入 Hook。

## T14：接入 AgentLoop 生命周期和 Prompt blocks

**文件：** `src/mycode/agent/loop.py`  
**依赖：** T13

**步骤：**
1. 为 `AgentLoop` 构造函数增加可选 `hook_runtime`，默认使用空运行时语义。
2. 在用户消息写入前后、每轮模型请求前后、助手最终消息和错误捕获处触发对应事件。
3. 在 `_framework_blocks()` 中追加 `hook_runtime.prompt_blocks()`。
4. 在 `run()` 的结束路径清理本次请求 Hook 注入。
5. 保持现有项目记忆、Skill、权限、压缩和事件顺序兼容。

**验证：** `python -m pytest tests/test_hook_agent.py tests/test_agent_loop.py -q -k "lifecycle or prompt_injection or compatibility"`；预期全部通过。

## T15：编写 AgentLoop 工具前拦截和权限兼容失败测试

**文件：** `tests/test_hook_agent.py`  
**依赖：** T14

**步骤：**
1. fake LLM 发出工具调用，权限允许，Hook 拦截，断言 fake 工具未执行。
2. 断言拦截工具结果写入 memory，并出现在下一轮模型请求中。
3. 验证权限拒绝和审批未获准时 Hook 不会放行，也不会调用真实工具。
4. 验证真实工具执行成功后触发 `tool_after` 和 `tool_result_message`。
5. 验证读工具并发、写工具串行、超时和现有工具结果语义不因 Hook 变化而回归。

**验证：** `python -m pytest tests/test_hook_agent.py tests/test_agent_loop.py -q -k "tool_before or hook_blocked or permission or tool_after"`；预期非零退出，失败指向工具执行路径尚未接入 Hook。

## T16：接入工具前拦截、工具后和工具结果消息事件

**文件：** `src/mycode/agent/loop.py`  
**依赖：** T15

**步骤：**
1. 在权限允许后、加入 `executable_calls` 前调用 `hook_runtime.before_tool()`。
2. 若返回 `blocked_tool_result`，按权限拒绝结果相同路径写入 tool message、项目记忆和 AgentEvent。
3. 真实工具执行并通过权限 `after_tool()` 后触发 `hook_runtime.after_tool()`。
4. 工具结果写入普通 memory 前触发 `tool_result_message`。
5. 确保 Hook 拦截只收紧已经允许的调用，不影响权限拒绝、审批、取消和 run timeout 分支。

**验证：** `python -m pytest tests/test_hook_agent.py tests/test_agent_loop.py -q -k "tool_before or hook_blocked or permission or tool_after"`；预期全部通过。

## T17：编写 Session、CLI 和 TUI 装配失败测试

**文件：** `tests/test_hook_session_cli.py`、`tests/test_session.py`、`tests/test_slash_cli.py`  
**依赖：** T16

**步骤：**
1. 验证 `ChatSession.send()` 和 `send_skill()` 第一次请求前惰性触发 `session_start`，多次发送只触发一次。
2. 验证 `clear()` 触发 `session_clear`，再执行现有 memory、Skill、mode 和权限清理。
3. 验证 `close()` 触发 `session_end`，TUI 正常退出路径会调用它。
4. 验证 CLI 默认 Hook 文件缺失可启动，显式缺失文件或非法 Hook 文件返回退出码 `1`。
5. 验证合法 Hook 配置被加载并传入 AgentLoop 与 ChatSession，同一个 runtime 实例被共享。

**验证：** `python -m pytest tests/test_hook_session_cli.py tests/test_session.py tests/test_slash_cli.py -q`；预期非零退出，失败指向装配尚未实现。

## T18：实现 Session、CLI 和 TUI Hook 装配

**文件：** `src/mycode/session.py`、`src/mycode/cli.py`、`src/mycode/tui.py`  
**依赖：** T17

**步骤：**
1. 为 `ChatSession` 增加可选 Hook runtime，维护会话是否已开始的进程内状态。
2. 实现 `start()`、`close()` 和 `clear()` 的 Hook 触发，默认空运行时保持现有行为。
3. CLI 增加 `--hook-config`，加载 Hook 配置错误时输出中文错误并返回 `1`。
4. 创建 `HookRuntime` 时复用权限服务的 `PathGuard`，并传入 AgentLoop 与 ChatSession。
5. TUI 正常退出前调用 `session.close()`，异常退出不保证后台 Hook 完成。

**验证：** `python -m pytest tests/test_hook_session_cli.py tests/test_session.py tests/test_slash_cli.py -q`；预期全部通过。

## T19：编写 README 与示例契约失败测试

**文件：** `tests/test_hook_config.py`、`tests/test_hook_session_cli.py`  
**依赖：** T18

**步骤：**
1. 验证 `examples/mycode.hooks.yaml` 是合法 Hook 配置，并覆盖 prompt、tool_before block、http 和 sub_agent 示例。
2. 验证 README 包含默认文件名、事件层级、`all/any` 条件、精确/glob/regex/反向匹配、四种动作、once/background/timeout 和不做范围说明。
3. 运行测试确认示例和 README 尚未补齐导致失败。

**验证：** `python -m pytest tests/test_hook_config.py tests/test_hook_session_cli.py -q -k "example or readme"`；预期非零退出。

## T20：补充示例配置与 README

**文件：** `examples/mycode.hooks.yaml`、`README.md`  
**依赖：** T19

**步骤：**
1. 新增 `examples/mycode.hooks.yaml`，使用安全命令和本地 HTTP URL 示例，不包含真实凭据。
2. README 增加 Hook 配置位置、三要素、生命周期事件、条件语法、动作字段、执行控制和工具前拦截说明。
3. README 明确本阶段不做子 Agent 真实运行、once 持久化、显式优先级和热加载。

**验证：** `python -m pytest tests/test_hook_config.py tests/test_hook_session_cli.py -q -k "example or readme"`；预期全部通过。

## T21：端到端和回归验证

**文件：** `tests/test_hook_agent.py`、`tests/test_hook_runtime.py`、现有回归测试  
**依赖：** T20

**步骤：**
1. 完成端到端场景：fake LLM 请求被 prompt Hook 注入提示词；工具调用被 tool_before Hook 拦截；模型下一轮根据拒绝结果调整并返回最终响应。
2. 完成失败隔离场景：command、HTTP 和 prompt 动作分别失败，Agent 仍完成当前请求。
3. 完成 once 场景：同一运行时多轮请求只执行一次，重建运行时后重新执行。
4. 运行新增 Hook 测试、Agent/Session/CLI 相关测试、全量测试和源码编译检查。
5. 任何失败先修复对应范围，再重跑失败命令和最终回归命令。

**验证：** 依次运行 `python -m pytest tests/test_hook_config.py tests/test_hook_matcher.py tests/test_hook_context.py tests/test_hook_actions.py tests/test_hook_runtime.py tests/test_hook_agent.py tests/test_hook_session_cli.py -q`、`python -m pytest -q`、`python -m compileall -q src`；预期三个命令退出码均为 `0`。

## 执行顺序

```text
T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8 → T9 → T10 → T11 → T12
                                                        │
                                                        ▼
T13 → T14 → T15 → T16 → T17 → T18 → T19 → T20 → T21
```

T3、T5 和 T7 在 T2 后可按文件边界并行准备测试，但实现仍按编号推进。T13 之后开始修改 `AgentLoop`，不得与其他任务并行修改同一文件。

## 建议提交分组

| 提交 | 任务 | 内容 | 提交前验证 |
|---|---|---|---|
| C1 | T1-T6 | Hook 模型、配置、匹配和上下文规范化 | `python -m pytest tests/test_hook_config.py tests/test_hook_matcher.py tests/test_hook_context.py -q` |
| C2 | T7-T12 | 动作执行、运行时、Prompt 注入和工具前拦截 | `python -m pytest tests/test_hook_actions.py tests/test_hook_runtime.py -q` |
| C3 | T13-T16 | AgentLoop 生命周期、Prompt 合并、工具前后接入 | `python -m pytest tests/test_hook_agent.py tests/test_agent_loop.py -q` |
| C4 | T17-T20 | Session、CLI、TUI、示例和 README | `python -m pytest tests/test_hook_session_cli.py tests/test_session.py tests/test_slash_cli.py tests/test_hook_config.py -q` |
| C5 | T21 | 端到端、全量回归和编译 | 依次运行 `python -m pytest -q`、`python -m compileall -q src` |

提交前先运行 `git status --short`，只暂存 Stage 11 相关文件和本任务明确调整的现有测试。

## 覆盖自检

| Plan 组件或需求 | 任务归属 |
|---|---|
| `hook.models`、事件、动作和规则模型 | T1-T2 |
| `hook.config`、YAML 严格校验和默认路径 | T1-T2、T17-T18 |
| 条件表达式、精确、glob、regex、反向和 `all/any` | T3-T4 |
| 权限规则参数规范化复用 | T5-T6 |
| command、prompt、http、sub_agent 动作 | T7-T8 |
| once、声明顺序、Prompt 注入和失败隔离 | T9-T10 |
| 工具前拦截和工具后事件 | T11-T12、T15-T16 |
| AgentLoop 生命周期触发和 framework block 合并 | T13-T14 |
| 权限兼容、工具结果回填和模型调整 | T15-T16、T21 |
| Session start/end/clear | T17-T18 |
| CLI `--hook-config`、启动错误和共享 runtime | T17-T18 |
| TUI 关闭路径 | T17-T18 |
| 示例配置和 README | T19-T20 |
| 端到端、全量回归和编译 | T21 |

依赖链无环；`plan.md` 中每个新增组件、所有现有接入点和 Spec 的 AC1-AC18 均至少由一个任务覆盖。每个任务都有可执行验证命令，未引入子 Agent 真实运行、once 持久化、显式优先级或 Hook 热加载。
