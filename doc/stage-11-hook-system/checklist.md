# myCode Stage 11：Hook 系统验收清单

> 所有条目都在实现完成后执行。先运行验证命令、记录实际输出，再勾选结果；不得用代码阅读或主观判断代替可观察证据。

## 验收环境

- [ ] Hook 自动化测试只使用临时工作区、临时 Hook 配置、fake LLM、fake 工具执行器和 fake HTTP transport，不读取真实用户配置。（验证：清空模型供应商 API key 后运行 `python -m pytest tests/test_hook_config.py tests/test_hook_actions.py tests/test_hook_agent.py tests/test_hook_session_cli.py -q`，期望全部通过，测试夹具记录的配置路径均位于 `tmp_path`。）
- [ ] Hook 测试不访问真实网络、不执行危险命令、不读取真实凭据。（验证：运行 `python -m pytest tests/test_hook_actions.py tests/test_hook_agent.py -q -k "http or command or isolation"`，期望 HTTP 请求均由 `httpx.MockTransport` 捕获，命令均在临时目录执行固定安全命令。）
- [ ] 本阶段开始前已有工作区变更未被恢复或覆盖。（验证：运行 `git status --short`，期望只出现 Stage 11 文档、实现、测试、示例和本阶段明确修改的现有文件。）

## 配置与匹配

- [ ] **AC1-a：** 合法 `mycode.hooks.yaml` 能加载为版本 `1` 的规则集合，规则包含 `event`、可省略 `if`、`action` 和执行控制字段。（验证：运行 `python -m pytest tests/test_hook_config.py -q -k "valid or minimal"`，期望解析出的规则 ID、事件、动作类型、once、background 和 timeout 与 YAML 一致。）
- [ ] **AC1-b：** 缺少 `event` 或 `action`、非法版本、未知顶层字段、未知规则字段、重复规则 ID、未知事件和未知动作均在加载阶段报 `HookConfigError`。（验证：运行 `python -m pytest tests/test_hook_config.py -q -k "invalid or missing or unknown or duplicate"`，期望每个坏配置都有稳定错误信息。）
- [ ] **AC1-c：** 各动作的必填字段按类型校验，`prompt.content`、`command.command`、`http.url`、`sub_agent.task` 缺失时加载失败。（验证：运行 `python -m pytest tests/test_hook_config.py -q -k "action_fields"`，期望错误指向对应规则 ID 和字段名。）
- [ ] **AC2-a：** 省略 `if` 的规则在事件触发时无条件执行。（验证：运行 `python -m pytest tests/test_hook_matcher.py tests/test_hook_runtime.py -q -k "unconditional"`，期望无条件规则命中并产生动作记录。）
- [ ] **AC2-b：** `all` 必须全部谓词满足，`any` 任一谓词满足即可。（验证：运行 `python -m pytest tests/test_hook_matcher.py -q -k "all or any"`，期望匹配矩阵与断言完全一致。）
- [ ] **AC2-c：** 同一规则同时声明 `all` 和 `any`、空 mapping 或嵌套逻辑时加载失败。（验证：运行 `python -m pytest tests/test_hook_config.py tests/test_hook_matcher.py -q -k "mixed_logic or empty_condition or nested"`，期望全部报配置错误。）
- [ ] **AC3-a：** 条件匹配覆盖精确字符串、数字、布尔、隐式 glob、显式 `glob:`、`re:` 正则、映射 `regex` 和反向匹配。（验证：运行 `python -m pytest tests/test_hook_matcher.py -q -k "exact or glob or regex or negate"`，期望每种语法都有通过和不通过样例。）
- [ ] **AC3-b：** 非法正则在配置加载阶段失败，不等到事件触发时才报错。（验证：运行 `python -m pytest tests/test_hook_config.py tests/test_hook_matcher.py -q -k "invalid_regex"`，期望抛出 `HookConfigError`。）
- [ ] **AC3-c：** 工具参数中的 `path`、`root` 和 `command` 使用权限规则相同的规范化结果参与匹配。（验证：运行 `python -m pytest tests/test_hook_context.py tests/test_hook_matcher.py tests/test_permission_pathing.py tests/test_permission_command.py -q -k "normalize or path or root or command"`，期望 Hook 匹配值与权限主体中的规范化值一致。）
- [ ] **AC4-a：** 会话级、轮次级、消息级、工具级和系统级事件枚举完整且命名稳定。（验证：运行 `python -m pytest tests/test_hook_config.py tests/test_hook_runtime.py -q -k "events or enum"`，期望 15 个事件均可被加载并触发。）

## 动作与运行时

- [ ] **AC7-a：** 前台 shell 动作能在临时目录执行固定安全命令，并记录标准输出。（验证：运行 `python -m pytest tests/test_hook_actions.py -q -k "command and success"`，期望 stdout 与测试命令输出一致。）
- [ ] **AC7-b：** shell 命令退出码非 `0` 或超时时只记录 Hook 失败，调用方收到失败结果但不抛异常。（验证：运行 `python -m pytest tests/test_hook_actions.py tests/test_hook_runtime.py -q -k "command and failure or timeout"`，期望 Agent/运行时后续规则继续执行。）
- [ ] **AC8：** prompt 动作进入 Hook framework block，不写入普通对话历史，并带有 `hook:` 来源标识。（验证：运行 `python -m pytest tests/test_hook_runtime.py tests/test_hook_agent.py -q -k "prompt_injection"`，期望 fake LLM 请求含 Hook block，memory 中不含该注入文本。）
- [ ] **AC9-a：** HTTP 动作通过 fake transport 发送方法、URL、请求头和 JSON 请求体。（验证：运行 `python -m pytest tests/test_hook_actions.py -q -k "http and success"`，期望 fake transport 捕获的请求与配置一致。）
- [ ] **AC9-b：** HTTP 连接异常、超时或非 2xx 响应只记录失败日志，不中断 Agent。（验证：运行 `python -m pytest tests/test_hook_actions.py tests/test_hook_agent.py -q -k "http and failure or timeout"`，期望当前用户请求仍完成。）
- [ ] **AC10：** `sub_agent` 动作配置可通过校验，命中后只返回占位结果和日志，不启动模型调用、后台任务或多 Agent 编排。（验证：运行 `python -m pytest tests/test_hook_config.py tests/test_hook_actions.py -q -k "sub_agent"`，期望 fake LLM 调用次数为 `0`。）
- [ ] **AC11：** `once` 规则在同一 `HookRuntime` 内只执行一次，重建运行时后可重新执行，且不产生状态文件。（验证：运行 `python -m pytest tests/test_hook_runtime.py -q -k "once"` 后运行 `git status --short`，期望测试通过且未出现 once 状态文件。）
- [ ] **AC12-a：** 非拦截事件允许 `background: true`，后台任务立即返回并消费异常。（验证：运行 `python -m pytest tests/test_hook_actions.py tests/test_hook_runtime.py -q -k "background"`，期望后台异常进入日志且没有未观察任务异常。）
- [ ] **AC12-b：** `tool_before` 规则配置 `background: true` 时加载失败。（验证：运行 `python -m pytest tests/test_hook_config.py -q -k "tool_before and background"`，期望 `HookConfigError`。）
- [ ] **AC13-a：** 多条命中规则按 YAML 声明顺序执行。（验证：运行 `python -m pytest tests/test_hook_runtime.py -q -k "order"`，期望动作记录顺序与规则 index 一致。）
- [ ] **AC13-b：** 工具前第一条产生拦截的规则生效后，不再执行该工具调用后续拦截动作。（验证：运行 `python -m pytest tests/test_hook_runtime.py -q -k "first_block"`，期望只记录第一条拦截规则 ID。）
- [ ] **AC14-a：** command、HTTP、prompt 和 sub_agent 动作内部异常均被隔离，普通事件后续规则继续执行。（验证：运行 `python -m pytest tests/test_hook_runtime.py tests/test_hook_actions.py -q -k "failure_isolation"`，期望失败规则之后的记录型规则仍执行。）
- [ ] **AC14-b：** 运行期失败日志包含规则 ID 和事件名，不把凭据、完整工具参数或内部堆栈暴露给模型。（验证：运行 `python -m pytest tests/test_hook_runtime.py tests/test_hook_agent.py -q -k "redact or diagnostic"`，期望敏感标记不在 fake LLM 请求和工具结果中出现。）

## Agent、Session、CLI 集成

- [ ] **AC4-b：** `AgentLoop` 触发用户请求开始、用户消息、模型轮次开始、模型轮次结束、助手消息、工具结果消息和用户请求结束事件，并携带对应上下文。（验证：运行 `python -m pytest tests/test_hook_agent.py tests/test_agent_loop.py -q -k "lifecycle"`，期望记录型 HookRuntime 捕获的事件序列与设计一致。）
- [ ] **AC4-c：** `ChatSession` 触发 `session_start`、`session_clear` 和 `session_end`，CLI 触发 `app_started` 与 `hooks_loaded`。（验证：运行 `python -m pytest tests/test_hook_session_cli.py tests/test_session.py -q -k "session or app_started or hooks_loaded"`，期望事件仅在对应生命周期节点出现。）
- [ ] **AC5：** 工具执行前 Hook 命中拦截后，fake 工具没有被调用，Agent 写入结构化 `ToolResult`，下一轮模型请求可见拒绝原因。（验证：运行 `python -m pytest tests/test_hook_agent.py tests/test_hook_runtime.py -q -k "hook_blocked or before_tool"`，期望 `reason_code=hook_blocked`、`hook_rule_id` 和面向模型的 reason 均存在。）
- [ ] **AC6-a：** 权限系统拒绝的工具调用不会被 Hook 放行，也不会进入真实工具执行器。（验证：运行 `python -m pytest tests/test_hook_agent.py tests/test_permission_service.py -q -k "permission and hook"`，期望权限拒绝结果保持原样，Hook 拦截结果不替代权限拒绝。）
- [ ] **AC6-b：** 需要审批但未获准的工具调用不会因 Hook 存在而执行。（验证：运行 `python -m pytest tests/test_hook_agent.py tests/test_permission_e2e.py -q -k "approval or pending"`，期望 fake 工具执行次数为 `0`。）
- [ ] **AC15-a：** CLI 默认缺失 `<workspace>/mycode.hooks.yaml` 时等价于空 Hook 配置并可正常启动。（验证：运行 `python -m pytest tests/test_hook_session_cli.py tests/test_slash_cli.py -q -k "default_missing"`，期望退出码 `0` 或测试中的启动流程完成。）
- [ ] **AC15-b：** CLI 显式 `--hook-config` 指向缺失或非法文件时启动失败，中文错误输出到 stderr 且退出码为 `1`。（验证：运行 `python -m pytest tests/test_hook_session_cli.py -q -k "explicit_missing or invalid_config"`，期望错误包含配置路径和规则定位。）
- [ ] **AC15-c：** 合法 Hook 配置加载后，同一个 `HookRuntime` 实例被传入 AgentLoop 与 ChatSession。（验证：运行 `python -m pytest tests/test_hook_session_cli.py -q -k "shared_runtime"`，期望记录到的对象身份一致。）
- [ ] TUI 正常退出路径会调用 `ChatSession.close()`，触发 `session_end`。（验证：运行 `python -m pytest tests/test_hook_session_cli.py tests/test_slash_tui.py -q -k "close or session_end"`，期望正常退出路径产生一次会话结束事件。）

## 非功能与故障隔离

- [ ] **AC16-a：** Hook 模型、配置、匹配、上下文、动作执行和运行时集中在 `src/mycode/hook/`，AgentLoop、Session、CLI、TUI 只做装配和触发。（验证：审查最终差异并运行 `python -m pytest tests/test_hook_agent.py tests/test_hook_session_cli.py -q`，期望跨包调用只经过 Hook runtime 接口。）
- [ ] **AC16-b：** 拦截回填、失败隔离、异步限制和权限规范化复用处有简洁中文注释，其他自解释代码没有叙述性注释。（验证：审查最终差异并运行 `git diff --check`，记录关键注释位置且无格式错误。）
- [ ] **AC16-c：** 实现中没有子 Agent 真实运行、once 持久化、显式优先级、Hook 热加载、远程规则加载、脚本化条件或图形编辑器入口。（验证：运行 `python -m pytest tests/test_hook_config.py tests/test_hook_actions.py -q -k "out_of_scope"`，并审查 CLI 帮助、README 和新增文件列表。）
- [ ] **AC17-a：** 现有 AgentLoop、工具执行器、工具注册、权限审批和路径保护测试继续通过。（验证：运行 `python -m pytest tests/test_agent_loop.py tests/test_tool_executor.py tests/test_tool_registry.py tests/test_tool_filesystem.py tests/test_tool_command.py tests/test_permission_config.py tests/test_permission_policy.py tests/test_permission_service.py tests/test_permission_e2e.py -q`，期望零失败。）
- [ ] **AC17-b：** 现有 Skill、斜杠命令、上下文压缩、项目记忆和配置测试继续通过。（验证：运行 `python -m pytest tests/test_skill_agent.py tests/test_skill_cli.py tests/test_skill_e2e.py tests/test_slash_cli.py tests/test_slash_e2e.py tests/test_compact_manager.py tests/test_context_compaction_e2e.py tests/test_memory_manager.py tests/test_config.py -q`，期望零失败。）
- [ ] **AC18：** Hook 自动化测试均通过 fake 依赖和临时文件验证，不访问真实网络、真实用户 Hook 配置或真实凭据。（验证：运行 `python -m pytest tests/test_hook_config.py tests/test_hook_matcher.py tests/test_hook_context.py tests/test_hook_actions.py tests/test_hook_runtime.py tests/test_hook_agent.py tests/test_hook_session_cli.py -q`，期望全部通过且测试日志显示 fake 组件被使用。）
- [ ] README 与示例配置覆盖默认文件、三要素、事件层级、条件语法、四种动作、执行控制、工具前拦截和不做范围。（验证：运行 `python -m pytest tests/test_hook_config.py tests/test_hook_session_cli.py -q -k "example or readme"`，期望示例 YAML 可加载，README 关键段落均可被测试定位。）

## 编译与测试

- [ ] Python 源码可完整编译，无语法错误。（验证：运行 `python -m compileall -q src`，期望退出码 `0`。）
- [ ] 新增 Hook 单元与集成测试全部通过。（验证：运行 `python -m pytest tests/test_hook_config.py tests/test_hook_matcher.py tests/test_hook_context.py tests/test_hook_actions.py tests/test_hook_runtime.py tests/test_hook_agent.py tests/test_hook_session_cli.py -q`，期望零失败。）
- [ ] Hook 相关接入回归测试全部通过。（验证：运行 `python -m pytest tests/test_agent_loop.py tests/test_session.py tests/test_slash_cli.py tests/test_slash_tui.py tests/test_permission_service.py tests/test_tool_executor.py -q`，期望零失败。）
- [ ] 仓库当前全部自动化测试通过。（验证：运行 `python -m pytest -q`，记录测试总数、通过数和退出码 `0`。）
- [ ] 最终差异无空白错误、冲突标记或意外修改用户已有文件。（验证：运行 `git diff --check` 和 `git status --short`，期望前者退出码 `0`，后者只列 Stage 11 相关文件及进入本阶段前已存在的用户变更。）

## 端到端场景

- [ ] **场景 1：Prompt 注入影响下一轮模型请求。** 配置 `model_round_start` 的 prompt Hook，发送一次用户请求，fake LLM 首轮请求中出现 Hook framework block，普通 memory 不含该注入。（验证：运行 `python -m pytest tests/test_hook_agent.py -q -k "e2e_prompt_injection"`，期望请求快照和 memory 断言均通过。）
- [ ] **场景 2：工具前安全拦截并让模型调整。** fake LLM 先请求危险命令，`tool_before` Hook 拦截，下一轮 fake LLM 看到拒绝原因后改用安全方案并完成回答。（验证：运行 `python -m pytest tests/test_hook_agent.py -q -k "e2e_tool_block_and_adjust"`，期望危险 fake 工具未执行、安全替代路径执行或最终响应完成。）
- [ ] **场景 3：权限先于 Hook 生效。** 同一 fake 工具调用在权限拒绝时不触发真实执行，也不会被 Hook 放行；权限允许后 Hook 可进一步拒绝。（验证：运行 `python -m pytest tests/test_hook_agent.py tests/test_permission_e2e.py -q -k "permission_before_hook"`，期望两个分支结果分别符合权限和 Hook 约束。）
- [ ] **场景 4：后台通知与失败隔离。** `tool_after` 配置后台 HTTP 通知，fake transport 成功时捕获请求；失败时只记录日志，当前 Agent 请求仍完成。（验证：运行 `python -m pytest tests/test_hook_agent.py tests/test_hook_actions.py -q -k "e2e_background_http"`，期望成功和失败分支都可观察。）
- [ ] **场景 5：once、timeout 与进程内状态。** 同一运行时多次触发 once 规则只执行一次；命令超时不阻断后续规则；重建运行时后 once 规则重新执行。（验证：运行 `python -m pytest tests/test_hook_runtime.py tests/test_hook_actions.py -q -k "e2e_once_timeout"`，期望执行计数和日志符合设计。）
- [ ] **场景 6：会话生命周期。** 第一次 `send()` 前触发 `session_start`，`clear()` 触发 `session_clear` 并清理现有状态，正常退出触发 `session_end`。（验证：运行 `python -m pytest tests/test_hook_session_cli.py tests/test_session.py -q -k "e2e_session_lifecycle"`，期望事件序列稳定。）
- [ ] **场景 7：示例配置可执行。** 使用 `examples/mycode.hooks.yaml` 运行隔离端到端测试，prompt、tool_before block、http 和 sub_agent 示例均被加载到对应分支。（验证：运行 `python -m pytest tests/test_hook_session_cli.py tests/test_hook_config.py -q -k "example_config"`，期望示例不含真实凭据且全部规则可定位。）

## 验收覆盖矩阵

| Spec 验收标准 | 对应检查区域 |
|---|---|
| AC1 | 配置与匹配 AC1-a 至 AC1-c；编译与测试 Hook 套件 |
| AC2 | 配置与匹配 AC2-a 至 AC2-c |
| AC3 | 配置与匹配 AC3-a 至 AC3-c |
| AC4 | 配置与匹配 AC4-a；Agent、Session、CLI 集成 AC4-b 至 AC4-c；端到端场景 6 |
| AC5 | Agent、Session、CLI 集成 AC5；端到端场景 2 |
| AC6 | Agent、Session、CLI 集成 AC6-a 至 AC6-b；端到端场景 3 |
| AC7 | 动作与运行时 AC7-a 至 AC7-b；端到端场景 5 |
| AC8 | 动作与运行时 AC8；端到端场景 1 |
| AC9 | 动作与运行时 AC9-a 至 AC9-b；端到端场景 4 |
| AC10 | 动作与运行时 AC10；端到端场景 7 |
| AC11 | 动作与运行时 AC11；端到端场景 5 |
| AC12 | 动作与运行时 AC12-a 至 AC12-b；端到端场景 4 |
| AC13 | 动作与运行时 AC13-a 至 AC13-b；端到端场景 2 |
| AC14 | 动作与运行时 AC14-a 至 AC14-b；非功能与故障隔离 |
| AC15 | Agent、Session、CLI 集成 AC15-a 至 AC15-c |
| AC16 | 非功能与故障隔离 AC16-a 至 AC16-c |
| AC17 | 非功能与故障隔离 AC17-a 至 AC17-b；编译与测试全量回归 |
| AC18 | 验收环境；非功能与故障隔离 AC18；编译与测试 Hook 套件 |

18 条 Spec 验收标准均至少对应一个可执行检查项；配置校验、条件匹配、四种动作、执行控制、工具前拦截、权限兼容、CLI 装配、故障隔离和端到端调整流程均有独立证据覆盖。
