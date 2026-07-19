# myCode Stage 05：纵深权限与安全检查验收清单

> 每一项都通过运行测试、检查结构化结果或观察用户可见行为验证。自动化验证统一使用 fake LLM、fake executor、临时工作区和临时用户目录，不执行真实危险命令、不访问真实网络、不读取真实 API key，也不修改真实用户权限配置。

## 实现完整性与包边界

- [ ] `I1` 权限领域只由 `src/mycode/permission/__init__.py`、`models.py`、`policy.py`、`command.py`、`pathing.py`、`config.py`、`service.py` 组成，模型、规则、审批、命令分析、路径守卫、配置存储和 Agent 拦截适配均在该包内。（验证：列出 `src/mycode/permission/`；检查职责与文件清单一致；运行 `python -m compileall src`，期望退出码为 0）
- [ ] `I2` Agent 只编排轮次和事件，工具包只定义并执行工具，TUI 只展示权限信息并收集选择；这些模块没有复制规则优先级、命令风险识别或路径边界算法。（验证：检查 `src/mycode/agent/`、`src/mycode/tool/`、`src/mycode/tui.py` 的依赖方向；运行 `rg -n "class PermissionPolicy|class CommandAnalyzer|class PathGuard" src/mycode/agent src/mycode/tool src/mycode/tui.py`，期望无重复实现）
- [ ] `I3` 旧的 `agent.approval`、`agent.interceptor` 和 `tool.pathing` 实现及引用已移除，Agent 的兼容导出指向权限领域，文件工具单向依赖 `permission.pathing`。（验证：运行 `rg -n "tool\.pathing|agent\.approval|agent\.interceptor" src tests`，期望无匹配；运行权限、Agent 和文件工具测试，期望导入成功）
- [ ] `I4` `ToolDefinition` 的 `kind` 和 `grant_arguments` 仅用于本地权限判断，OpenAI Chat、OpenAI Responses 和 Anthropic 请求中的工具定义仍只包含供应商所需字段。（验证：运行 `python -m pytest tests/test_tool_registry.py tests/test_openai_chat_protocol.py tests/test_openai_responses_protocol.py tests/test_anthropic_protocol.py tests/test_llm_base.py -q`，期望权限元数据不出站且旧构造方式兼容）
- [ ] `I5` CLI 启动时只装配一套共享的 `PermissionService` 和 `PathGuard`，并将必需的权限拦截器传入 AgentLoop、权限服务传入 ChatSession。（验证：运行 `python -m pytest tests/test_cli.py tests/test_agent_loop.py tests/test_session.py -q`，期望依赖身份和必需构造参数断言通过）

## 调用校验与统一决策

- [ ] `D1` 每个合法工具调用在进入 executor 前都调用统一权限入口，只有最终 `ALLOW` 或获批的 `ASK` 才进入 executor。（验证：运行 `python -m pytest tests/test_permission_service.py tests/test_agent_loop.py tests/test_permission_e2e.py -q`，期望 fake executor 的调用顺序和次数断言通过）
- [ ] `D2` 未知工具保持既有 `UNKNOWN_TOOL` 行为；非法 JSON、缺少必填参数或参数类型不符合契约时返回 `invalid_tool_arguments`，均不进入 fake executor。（验证：运行 `python -m pytest tests/test_permission_policy.py tests/test_agent_loop.py -q`，期望错误码、结构化结果和 executor 零调用断言通过）
- [ ] `D3` 相同工具调用、配置、会话状态和工作区上下文产生相同决定；调整 YAML 规则声明顺序不改变 effect、原因码或命中规则。（验证：运行 `python -m pytest tests/test_permission_policy.py tests/test_permission_config.py -q`，期望顺序置换测试通过）
- [ ] `D4` 命令分析、路径检查、规则匹配、配置加载或审批处理内部抛出异常时 fail-closed，返回安全的 `security_check_failed` 或对应错误，不调用 executor 且不包含异常堆栈。（验证：运行 `python -m pytest tests/test_permission_service.py tests/test_agent_loop.py -q`，期望故障注入测试通过）

## 配置、来源与规则

- [ ] `C1` 用户全局、本地项目和仓库项目三类 `version: 1` YAML 能从规定路径加载；会话规则只驻留内存，本地项目目录使用规范化工作区完整 SHA-256 隔离。（验证：运行 `python -m pytest tests/test_permission_config.py -q`，期望路径、哈希、workspace 校验和移动工作区测试通过）
- [ ] `C2` 仓库 `mycode.permissions.yaml` 只接受 `DENY/ASK`，包含 `ALLOW`、`mode` 或 `workspace` 时 CLI 在执行工具前用中文报错并返回 1；合法仓库规则只能收紧权限。（验证：运行 `python -m pytest tests/test_permission_config.py tests/test_cli.py tests/test_permission_e2e.py -q`，期望恶意仓库配置启动失败、合法限制生效且 executor 未被误放行）
- [ ] `C3` 非法版本、档位、effect、参数模式、未知字段、重复规则 ID、`FORBIDDEN` 声明和完全相同条件的冲突 effect 均在启动时失败，并安全定位配置来源和规则位置。（验证：运行 `python -m pytest tests/test_permission_config.py tests/test_cli.py -q`，期望各非法 fixture 都产生中文配置错误且不会泄露完整规则集）
- [ ] `C4` 规则匹配支持精确工具、`*` 工具、字符串 glob、数字/布尔精确值；路径先规范化为 `/` 分隔的工作区相对值，命令只折叠首尾及连续空白而不改变引号和转义。（验证：运行 `python -m pytest tests/test_permission_policy.py -q`，期望各匹配和规范化参数化测试通过）
- [ ] `C5` 来源优先级固定为 `SESSION > LOCAL_PROJECT > REPOSITORY_PROJECT > USER_GLOBAL`，只使用首个有匹配项的来源；本地项目普通规则可覆盖仓库规则，会话普通规则可覆盖更低来源，但任何普通来源都不能覆盖 `FORBIDDEN`。（验证：运行 `python -m pytest tests/test_permission_policy.py tests/test_permission_e2e.py -q`，期望分层组合测试通过）
- [ ] `C6` 同一来源内按精确工具、约束参数数量、精确参数数量依次排序；同具体度冲突使用 `DENY > ASK > ALLOW`，同 effect 以规则 ID 稳定选取诊断结果。（验证：运行 `python -m pytest tests/test_permission_policy.py -q`，期望具体度、冲突和声明顺序置换测试通过）
- [ ] `C7` HITL 新增的会话规则和本地项目规则无需重启立即生效，外部手工编辑权限文件在当前进程中不热加载。（验证：运行 `python -m pytest tests/test_permission_config.py tests/test_permission_service.py -q`，期望当前 store 立即命中新增规则而磁盘外部改动不改变已加载规则）
- [ ] `C8` 本地项目授权以同目录临时文件、`flush/fsync` 和 `os.replace` 原子写入；模拟任一步骤失败时旧文件逐字节保留、临时文件被清理且当前调用不执行。（验证：运行 `python -m pytest tests/test_permission_config.py tests/test_permission_service.py -q`，期望成功、幂等和各失败注入测试通过）

## 权限档位与策略叠加

- [ ] `M1` 未命中规则和内置风险时，`strict` 对读写均 `ASK`，`default` 允许普通读而写/命令 `ASK`，`permissive` 允许普通读写。（验证：运行 `python -m pytest tests/test_permission_policy.py -q`，期望三档矩阵测试通过）
- [ ] `M2` 有效档位按会话、本地项目、用户全局、内置 `default` 依次选择；仓库策略不参与档位选择。（验证：运行 `python -m pytest tests/test_permission_config.py tests/test_session.py -q`，期望档位及来源断言通过）
- [ ] `M3` `permissive` 不能覆盖普通 `DENY`、内置高风险 `ASK` 或 `FORBIDDEN`；`strict` 下精确普通 `ALLOW` 仍可直接允许非 plan-only 调用。（验证：运行 `python -m pytest tests/test_permission_policy.py tests/test_permission_command.py -q`，期望覆盖优先级测试通过）
- [ ] `M4` `/permission` 用中文显示当前有效档位和来源，三种设置命令立即设置会话档位；非法参数显示中文用法且不向 LLM 发送请求。（验证：运行 `python -m pytest tests/test_tui.py tests/test_session.py -q`，期望命令输出、状态变更和 LLM 零调用断言通过）
- [ ] `M5` `/clear` 清空会话规则和会话档位并复位 `plan-only`，恢复本地项目、用户全局或内置档位；持久化规则、普通工具/memory 清理语义保持规定行为。（验证：运行 `python -m pytest tests/test_session.py tests/test_tui.py tests/test_permission_e2e.py -q`，期望清理前后决定和状态断言通过）
- [ ] `M6` `plan-only` 开启时，写工具的普通 `ALLOW` 提升为 `ASK`，已有 `ASK` 保持询问，`DENY/FORBIDDEN` 保持更严格结果；关闭后原普通规则继续生效。（验证：运行 `python -m pytest tests/test_permission_policy.py tests/test_agent_plan_only.py tests/test_permission_e2e.py -q`，期望叠加矩阵通过）

## 危险命令与安全底线

- [ ] `S1` 代表性的工作区根、用户主目录、文件系统根、Windows 磁盘根和系统关键目录递归删除均分类为不可审批的 `FORBIDDEN`。（验证：运行 `python -m pytest tests/test_permission_command.py -q`，期望 POSIX、cmd 和 PowerShell 参数化用例通过且不执行命令）
- [ ] `S2` 格式化磁盘、清理磁盘和覆盖原始块设备的代表性命令分类为 `FORBIDDEN`。（验证：运行 `python -m pytest tests/test_permission_command.py -q`，期望 `mkfs`、`format`、`diskpart`、PowerShell 磁盘命令和原始设备写入 fixture 通过）
- [ ] `S3` 管道下载到 shell/解释器/eval，以及同一控制链下载文件后立即执行的代表性命令分类为 `FORBIDDEN`；命令、规则、会话授权和三种档位都不提供审批选项或覆盖结果。（验证：运行 `python -m pytest tests/test_permission_command.py tests/test_permission_policy.py tests/test_permission_service.py -q`，期望下载即执行和不可覆盖断言通过）
- [ ] `S4` 工作区子路径删除、`git clean`、`git reset --hard`、软件包安装、网络传输、提权、权限修改、服务管理和进程终止分类为内置 `ASK`。（验证：运行 `python -m pytest tests/test_permission_command.py -q`，期望各风险类别及稳定原因码测试通过）
- [ ] `S5` 当前平台默认 shell及显式 `cmd /c`、PowerShell、`pwsh`、`sh -c`、`bash -c` 嵌套命令均被递归分析，引号外连接符与平台转义不会被引号内字符混淆。（验证：运行 `python -m pytest tests/test_permission_command.py -q`，期望嵌套、管道、连接符、引号和转义测试通过）
- [ ] `S6` 引号不闭合、编码命令、命令替换、空字符、超长命令、超过递归深度和未知 shell 启动器降级为 `ASK(command_ambiguous)`，不会返回普通安全结果。（验证：运行 `python -m pytest tests/test_permission_command.py -q`，期望所有保守降级边界通过）
- [ ] `S7` 内置高风险 `ASK` 可被之前创建的精确普通授权满足，但不精确或无关授权不能放宽调用，`plan-only` 仍可要求再次审批。（验证：运行 `python -m pytest tests/test_permission_policy.py tests/test_permission_service.py -q`，期望精确授权、作用域和 plan-only 组合测试通过）

## 文件路径沙箱

- [ ] `P1` 文件读取、写入、编辑、查找和搜索接受工作区内合法相对路径及工作区内绝对路径，并在规则匹配前生成稳定的工作区相对匹配值。（验证：运行 `python -m pytest tests/test_permission_pathing.py tests/test_tool_filesystem.py -q`，期望合法路径和规范化测试通过）
- [ ] `P2` `..` 逃逸、工作区外绝对路径、文件系统根切换、Windows 盘符根和 UNC 越界均返回结构化路径拒绝，不读取或写入目标。（验证：运行 `python -m pytest tests/test_permission_pathing.py tests/test_tool_filesystem.py -q`，期望平台路径和 executor/文件零变更断言通过）
- [ ] `P3` 已存在目标、符号链接和不存在目标的最近已存在父目录都会解析真实边界；链接检查失败或边界无法确认时 fail-closed。（验证：运行 `python -m pytest tests/test_permission_pathing.py tests/test_tool_filesystem.py -q`，期望符号链接逃逸、链接异常和不存在目标测试通过）
- [ ] `P4` 文件工具在实际操作前复检路径；查找和搜索同时检查起始目录及每个待读候选，候选在判定后被替换为越界符号链接时也不会被读取。（验证：运行 `python -m pytest tests/test_tool_filesystem.py tests/test_permission_e2e.py -q`，期望 TOCTOU 模拟、越界根和越界候选测试通过）
- [ ] `P5` 任何模式、规则或 HITL 授权都不能把内置文件工具的路径边界扩展到工作区外；shell 工具不被错误描述为操作系统级路径沙箱。（验证：运行 `python -m pytest tests/test_permission_policy.py tests/test_permission_e2e.py tests/test_docs.py -q`，期望越界授权仍拒绝且 README 边界断言通过）

## HITL、执行顺序与并发

- [ ] `H1` 普通 `ASK` 的中文审批展示工具名、脱敏安全参数、风险原因、规则来源和当前档位，并提供本次允许、本会话允许、当前项目永久允许、拒绝、取消五种有效选择。（验证：运行 `python -m pytest tests/test_permission_service.py tests/test_tui.py -q`，期望请求结构和 `o/y/s/p/n/c` 输入测试通过）
- [ ] `H2` 本次允许只执行当前调用且不创建规则；本会话允许立即创建精确 `SESSION ALLOW`；项目永久允许只在用户目录工作区哈希目录写入精确 `LOCAL_PROJECT ALLOW`，成功持久化后才执行。（验证：运行 `python -m pytest tests/test_permission_service.py tests/test_permission_config.py tests/test_permission_e2e.py -q`，期望各授权范围、执行时序和落盘位置断言通过）
- [ ] `H3` 自动候选规则只保存工具声明的安全相关授权参数：文件工具保存规范化精确路径，命令工具保存规范化精确命令；正文、替换内容、超时和凭据等无关参数不进入规则。（验证：运行 `python -m pytest tests/test_permission_policy.py tests/test_permission_service.py tests/test_tool_command.py -q`，期望 `grant_arguments` 白名单和生成规则断言通过）
- [ ] `H4` 未声明授权参数或处于 `plan-only` 的调用只展示本次允许、拒绝和取消，输入会话/项目授权不会生成规则或执行工具。（验证：运行 `python -m pytest tests/test_permission_service.py tests/test_tui.py tests/test_agent_plan_only.py -q`，期望动态选项和无效输入测试通过）
- [ ] `H5` 项目永久授权绝不修改工作区内的仓库策略，也不能创建用户全局规则；模拟持久化失败时显示中文错误、保留原文件且不执行当前调用。（验证：运行 `python -m pytest tests/test_permission_config.py tests/test_permission_service.py tests/test_permission_e2e.py -q`，期望临时 workspace 逐字节不变和 fake executor 零调用）
- [ ] `H6` 没有审批 provider 时 `ASK` 按拒绝处理；用户拒绝返回结构化工具结果并继续当前 turn，用户取消发布 `CANCELLED` 并终止当前 turn。（验证：运行 `python -m pytest tests/test_permission_service.py tests/test_agent_loop.py tests/test_permission_e2e.py -q`，期望后续 fake LLM 轮次和取消终止断言通过）
- [ ] `H7` 多个并发读调用需要审批时按模型顺序串行展示；只有获准的读调用进入并发执行，被拒绝调用不进入 executor。（验证：运行 `python -m pytest tests/test_agent_loop.py tests/test_permission_e2e.py -q`，期望审批序列和并发执行时序断言通过）
- [ ] `H8` 工具通过权限检查后的执行失败、超时和取消继续使用原 `ToolResult`/Agent 事件与 memory 契约，不伪装为权限拒绝。（验证：运行 `python -m pytest tests/test_permission_service.py tests/test_agent_loop.py tests/test_tool_executor.py -q`，期望 after_tool、超时、取消和错误结果回归通过）

## 诊断、中文提示与安全注释

- [ ] `O1` 每次权限决定可观察到调用 ID、工具名、决定、稳定原因码、有效档位、命中规则 ID/作用域、风险分类以及审批结果/授权范围；不适用字段明确为空而非伪造值。（验证：运行 `python -m pytest tests/test_permission_policy.py tests/test_permission_service.py -q`，期望诊断对象完整性测试通过）
- [ ] `O2` `DENY`、`FORBIDDEN`、审批拒绝、无 provider、路径越界和权限内部失败均回填安全结构化工具结果，只包含调用标识、原因码和中文摘要，不包含规则全集、黑名单表达式或内部异常堆栈。（验证：运行 `python -m pytest tests/test_permission_service.py tests/test_agent_loop.py -q`，期望结果字段白名单测试通过）
- [ ] `O3` 审批摘要和诊断对 API key、token、密码、secret、URL userinfo/查询凭据、常见命令行凭据参数及敏感环境变量赋值脱敏，同时保留命令结构并限制值长度。（验证：运行 `python -m pytest tests/test_permission_policy.py tests/test_permission_service.py -q`，期望固定敏感 fixture 不出现在输出且截断标记为中文）
- [ ] `O4` `/permission`、审批选项、拒绝原因、配置错误和持久化错误等所有面向用户的权限提示均为中文，英文稳定原因码只作为机器字段出现。（验证：运行 `python -m pytest tests/test_tui.py tests/test_cli.py tests/test_permission_service.py tests/test_docs.py -q`，期望中文文案和原因码分层断言通过）
- [ ] `O5` 权限优先级与冲突、`FORBIDDEN` 不可覆盖、危险命令不确定时降级、路径边界与符号链接、持久化失败不执行、`plan-only` 叠加等关键路径具有简洁中文注释，注释解释安全理由而非逐行复述代码。（验证：运行 `rg -n "#.*[一-龥]" src/mycode/permission src/mycode/agent/loop.py src/mycode/tool/filesystem.py`，再人工检查上述六类分支均有实质注释）
- [ ] `O6` README 和安全示例说明三档模式、规则来源、仓库只允许 `DENY/ASK`、HITL 范围、`FORBIDDEN`、路径沙箱及 shell 非 OS 沙箱边界；仓库示例不含凭据和 `ALLOW`。（验证：运行 `python -m pytest tests/test_docs.py -q`，期望文档断言通过）

## 编译、测试与回归

- [ ] `T1` 权限包可编译且不存在循环导入。（验证：运行 `python -m compileall src`，期望退出码为 0）
- [ ] `T2` 权限策略、命令、路径、配置和服务单元测试全部通过。（验证：运行 `python -m pytest tests/test_permission_policy.py tests/test_permission_command.py tests/test_permission_pathing.py tests/test_permission_config.py tests/test_permission_service.py -q`，期望全部通过）
- [ ] `T3` Agent、plan-only、Session、TUI、CLI、工具和协议集成测试全部通过。（验证：运行 `python -m pytest tests/test_agent_events.py tests/test_agent_loop.py tests/test_agent_plan_only.py tests/test_session.py tests/test_tui.py tests/test_cli.py tests/test_tool_registry.py tests/test_tool_executor.py tests/test_tool_filesystem.py tests/test_tool_command.py tests/test_openai_chat_protocol.py tests/test_openai_responses_protocol.py tests/test_anthropic_protocol.py -q`，期望全部通过）
- [ ] `T4` 现有 Agent Loop、多轮工具调用、读并发/写串行、memory、工具历史、session、TUI、协议、取消和超时行为没有回归。（验证：运行 `python -m pytest tests/test_agent_scheduler.py tests/test_agent_loop.py tests/test_agent_plan_only.py tests/test_e2e_chat.py tests/test_session.py tests/test_tui.py tests/test_protocol_factory.py tests/test_sse.py -q`，期望全部通过）
- [ ] `T5` 权限端到端测试独立通过，且测试只使用 fake LLM、记录型 fake executor、临时 home/workspace 和脚本化审批 provider。（验证：运行 `python -m pytest tests/test_permission_e2e.py -q`，期望全部通过且无真实命令、网络或用户目录访问）
- [ ] `T6` 全项目自动化测试通过。（验证：运行 `python -m pytest -q`，期望全部通过）
- [ ] `T7` 代码与文档没有空白错误，旧权限模块引用已经清除。（验证：运行 `git diff --check`，期望无输出；运行 `rg -n "tool\.pathing|agent\.approval|agent\.interceptor" src tests`，期望无匹配）

## 端到端场景

- [ ] `E1` 默认档位下，fake LLM 请求普通读工具时直接执行并将结果回填；请求普通写工具时先出现中文审批，选择本次允许后只执行该次调用。（验证：运行 `python -m pytest tests/test_permission_e2e.py -q`，期望普通允许与风险本次批准场景通过）
- [ ] `E2` 同一风险调用选择本会话允许后再次请求可复用精确授权；执行 `/clear` 后会话授权和档位消失，再次请求恢复审批，而持久化规则不被清除。（验证：运行 `python -m pytest tests/test_permission_e2e.py tests/test_session.py -q`，期望会话复用与 clear 生命周期场景通过）
- [ ] `E3` 选择当前项目永久允许后，授权只写入临时 home 的工作区 SHA-256 目录；重建权限服务后精确调用可复用授权，仓库策略文件逐字节不变。（验证：运行 `python -m pytest tests/test_permission_e2e.py -q`，期望持久授权、重启加载和仓库不变场景通过）
- [ ] `E4` 恶意仓库策略声明 `ALLOW` 或 `mode` 时应用启动失败；改为合法 `DENY/ASK` 后只能阻断或追加审批，无法扩大默认权限。（验证：运行 `python -m pytest tests/test_permission_e2e.py tests/test_cli.py -q`，期望仓库信任边界场景通过）
- [ ] `E5` fake LLM 请求删除工作区根或下载即执行时直接收到不可审批的 `FORBIDDEN` 结果；切换 permissive、加入会话/本地项目允许规则后仍不执行。（验证：运行 `python -m pytest tests/test_permission_e2e.py tests/test_permission_command.py -q`，期望安全底线场景中 fake executor 调用数为 0）
- [ ] `E6` fake LLM 请求读取 `..`、工作区外绝对路径或越界符号链接时，策略前或执行期路径检查拒绝访问；加入精确允许规则也不能突破边界。（验证：运行 `python -m pytest tests/test_permission_e2e.py tests/test_permission_pathing.py tests/test_tool_filesystem.py -q`，期望所有越界场景文件内容未被读取）
- [ ] `E7` `plan-only` 下已有会话或项目写授权仍要求审批且只允许本次/拒绝/取消；关闭后原授权恢复，普通 `DENY/FORBIDDEN` 始终保持更严格决定。（验证：运行 `python -m pytest tests/test_permission_e2e.py tests/test_agent_plan_only.py -q`，期望模式叠加完整场景通过）
- [ ] `E8` 同一批多个读调用依次审批，批准项并发进入 fake executor，拒绝项回填模型且不执行；后续 fake LLM 仍能基于完整、按序的工具结果继续 turn。（验证：运行 `python -m pytest tests/test_permission_e2e.py tests/test_agent_loop.py -q`，期望串行审批、选择性并发和 memory 顺序场景通过）
- [ ] `E9` 用户拒绝、无 provider、项目授权写入失败分别回填安全中文结果并继续 turn；用户取消只终止当前 turn，所有被阻断调用的 fake executor 计数均为 0。（验证：运行 `python -m pytest tests/test_permission_e2e.py tests/test_permission_service.py -q`，期望四种失败恢复场景通过）

## 验收标准映射

| Spec | 对应清单条目 |
|------|--------------|
| AC1 | D1、D2 |
| AC2 | I1、I2、I3 |
| AC3 | S1、S2、S3、E5 |
| AC4 | S4、S5、S6、S7 |
| AC5 | C5、C6 |
| AC6 | C2、C3、E4 |
| AC7 | C4、C6、D3 |
| AC8 | M1、M3 |
| AC9 | M2、M4、M5、E2 |
| AC10 | H1、H2、H3、H4 |
| AC11 | C8、H2、H5、E3 |
| AC12 | H6、E9 |
| AC13 | M6、H4、E7 |
| AC14 | P1、P2、P3、P4、P5、E6 |
| AC15 | S5、S6 |
| AC16 | D4、H6、O2、E5、E9 |
| AC17 | H7、H8、E8 |
| AC18 | C3、C7、C8 |
| AC19 | O1、O2、O3、O4 |
| AC20 | O5 |
| AC21 | I4、H8、T3、T4、T6 |
| AC22 | T5、E1-E9 |
