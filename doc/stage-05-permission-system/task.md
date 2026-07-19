# myCode Stage 05：纵深权限与安全检查任务拆解

## 阶段标识

- 阶段：Stage 05
- 输入：已批准的 `spec.md` 与 `plan.md`
- 输出：按 TDD 顺序执行的文件级任务列表
- 实现原则：每个任务先写失败测试，再写最小实现，验证通过后提交

> 当前工作区可能存在用户已经暂存的无关改动。所有任务提交都必须使用明确 pathspec，只提交该任务列出的文件；禁止裸 `git commit`、`git commit -a` 和无范围的 `git add -A`。

## 文件清单

### 新建

| 文件 | 职责 |
|------|------|
| `src/mycode/permission/__init__.py` | 权限包文档，不做急切导出 |
| `src/mycode/permission/models.py` | 权限枚举、数据类、审批类型和异常 |
| `src/mycode/permission/policy.py` | 调用规范化、脱敏、规则匹配和判定链 |
| `src/mycode/permission/command.py` | shell 扫描与危险命令分类 |
| `src/mycode/permission/pathing.py` | PathGuard 与路径边界检查 |
| `src/mycode/permission/config.py` | 权限 YAML、多来源、会话状态和持久化 |
| `src/mycode/permission/service.py` | 服务门面、审批、拦截器和默认装配 |
| `tests/test_permission_policy.py` | 规范化、规则、档位与 plan-only 测试 |
| `tests/test_permission_command.py` | POSIX、cmd、PowerShell 风险分类测试 |
| `tests/test_permission_pathing.py` | 工作区、符号链接与平台路径测试 |
| `tests/test_permission_config.py` | 配置来源、会话状态与原子写入测试 |
| `tests/test_permission_service.py` | 权限决定、审批和拦截器测试 |
| `tests/test_permission_e2e.py` | fake LLM/fake executor 端到端权限测试 |
| `examples/mycode.permissions.yaml` | 只含 `DENY/ASK` 的仓库策略示例 |

### 修改

| 文件 | 改动 |
|------|------|
| `src/mycode/tool/base.py` | `ToolDefinition` 增加 `grant_arguments` |
| `src/mycode/tool/defaults.py` | 注入共享 `PathGuard` |
| `src/mycode/tool/filesystem.py` | 迁移路径导入并复检候选文件 |
| `src/mycode/tool/command.py` | 声明 `command` 授权参数 |
| `src/mycode/tool/__init__.py` | 移除工具领域路径守卫导出 |
| `src/mycode/agent/__init__.py` | 从权限领域兼容导出审批类型 |
| `src/mycode/agent/events.py` | 使用新的 `ApprovalRequest` |
| `src/mycode/agent/loop.py` | 接入必需权限拦截器和审批结果 |
| `src/mycode/session.py` | 权限档位查询、设置与 clear 委托 |
| `src/mycode/tui.py` | `/permission` 和中文审批交互 |
| `src/mycode/cli.py` | 权限服务装配和配置错误处理 |
| `src/mycode/protocols/openai_chat.py` | 确保权限本地元数据不进入 Chat payload |
| `src/mycode/protocols/openai_responses.py` | 确保权限本地元数据不进入 Responses payload |
| `tests/test_tool_filesystem.py` | 路径守卫迁移与执行期复检回归 |
| `tests/test_tool_command.py` | `grant_arguments` 元数据 |
| `tests/test_tool_registry.py` | 新字段默认值和注册兼容 |
| `tests/test_agent_loop.py` | 注入权限拦截器和判定分支 |
| `tests/test_agent_plan_only.py` | 统一审批类型与 plan-only 叠加 |
| `tests/test_agent_events.py` | 新审批请求契约 |
| `tests/test_session.py` | 会话权限状态与 clear |
| `tests/test_tui.py` | 权限命令和五种审批选择 |
| `tests/test_cli.py` | 权限装配与启动失败 |
| `tests/test_openai_chat_protocol.py` | 本地权限元数据不出站 |
| `tests/test_openai_responses_protocol.py` | 本地权限元数据不出站 |
| `tests/test_docs.py` | README 权限说明 |
| `README.md` | 模式、规则来源、HITL 与安全边界 |

### 删除

| 文件 | 原因 |
|------|------|
| `src/mycode/agent/approval.py` | 审批类型迁入 `permission/models.py` |
| `src/mycode/agent/interceptor.py` | 拦截实现迁入 `permission/service.py` |
| `src/mycode/tool/pathing.py` | 路径守卫迁入 `permission/pathing.py` |
| `tests/test_agent_interceptor.py` | 用例迁入 `test_permission_service.py` |

## T1：建立权限模型与包边界

**文件：** `src/mycode/permission/__init__.py`、`src/mycode/permission/models.py`、`tests/test_permission_service.py`
**依赖：** 无

**步骤：**

1. 在测试中导入并断言 `PermissionEffect`、`PermissionMode`、`RuleSource`、`ApprovalDecisionType` 和 `ApprovalOutcome` 的全部稳定值。
2. 运行测试，确认因 `mycode.permission` 不存在而失败。
3. 创建只含包文档的 `__init__.py`；在 `models.py` 实现 plan.md 定义的枚举、规则/决定/审批/配置/会话数据类及四类权限异常。
4. 使用 tuple 保存条件和规则，保持数据对象 frozen；为 `FORBIDDEN`、审批范围和 fail-closed 异常写中文安全注释。

**验证：** `pytest -q tests/test_permission_service.py`，期望模型契约测试通过。

**提交：** `git add src/mycode/permission/__init__.py src/mycode/permission/models.py tests/test_permission_service.py; git commit -m "feat: add permission domain models" -- src/mycode/permission/__init__.py src/mycode/permission/models.py tests/test_permission_service.py`

## T2：实现工作区路径守卫

**文件：** `src/mycode/permission/pathing.py`、`tests/test_permission_pathing.py`
**依赖：** T1

**步骤：**

1. 编写合法相对路径、工作区内绝对路径、`..` 越界、工作区外绝对路径和展示相对路径测试。
2. 运行测试，确认 `PathGuard` 未定义而失败。
3. 实现独立的 `ToolPathError`、`GuardedPath`、`PathGuard.inspect()`、`resolve()` 和 `workspace_root`。
4. 使用规范化真实路径做边界判断，并在拒绝不确定边界处写中文注释说明 fail-closed 原因。

**验证：** `pytest -q tests/test_permission_pathing.py`，期望基础路径场景全部通过。

**提交：** `git add src/mycode/permission/pathing.py tests/test_permission_pathing.py; git commit -m "feat: guard permission paths" -- src/mycode/permission/pathing.py tests/test_permission_pathing.py`

## T3：补齐符号链接与平台路径边界

**文件：** `src/mycode/permission/pathing.py`、`tests/test_permission_pathing.py`
**依赖：** T2

**步骤：**

1. 增加工作区内链接、链接到工作区外、不存在目标的最近已存在父目录、Windows 大小写/分隔符和 UNC fixture 测试；平台不支持创建链接时只跳过链接用例。
2. 运行新增测试，确认现有实现至少有一项边界失败。
3. 扩展 `inspect()`：解析已存在链接；对不存在目标验证最近已存在父目录；分别生成 `relative` 和平台 `match_value`。
4. 确认路径显示保持 `/` 分隔符，Windows 匹配值使用 `normcase`，边界判断不依赖字符串前缀。

**验证：** `pytest -q tests/test_permission_pathing.py`，期望全部通过且无真实工作区外写入。

**提交：** `git add src/mycode/permission/pathing.py tests/test_permission_pathing.py; git commit -m "feat: harden path boundary checks" -- src/mycode/permission/pathing.py tests/test_permission_pathing.py`

## T4：增加工具授权参数元数据

**文件：** `src/mycode/tool/base.py`、`src/mycode/tool/filesystem.py`、`src/mycode/tool/command.py`、`tests/test_tool_registry.py`、`tests/test_tool_filesystem.py`、`tests/test_tool_command.py`
**依赖：** T1

**步骤：**

1. 增加 `ToolDefinition.grant_arguments` 默认空 tuple，以及六个内置工具授权字段的测试。
2. 运行三份测试，确认新字段断言失败。
3. 在 dataclass 末尾增加 `grant_arguments: tuple[str, ...] = ()`；文件工具声明 `path/root`，命令工具声明 `command`。
4. 保持 `kind` 和 JSON Schema 不变，确保未修改的测试工具依靠默认值继续构造成功。

**验证：** `pytest -q tests/test_tool_registry.py tests/test_tool_filesystem.py tests/test_tool_command.py`，期望全部通过。

**提交：** `git add src/mycode/tool/base.py src/mycode/tool/filesystem.py src/mycode/tool/command.py tests/test_tool_registry.py tests/test_tool_filesystem.py tests/test_tool_command.py; git commit -m "feat: declare tool grant arguments" -- src/mycode/tool/base.py src/mycode/tool/filesystem.py src/mycode/tool/command.py tests/test_tool_registry.py tests/test_tool_filesystem.py tests/test_tool_command.py`

## T5：解析版本化权限 YAML

**文件：** `src/mycode/permission/config.py`、`tests/test_permission_config.py`
**依赖：** T1

**步骤：**

1. 编写 `version: 1`、可选 mode、空 rules、完整规则、glob 字符串和标量条件解析测试。
2. 运行测试，确认配置解析入口不存在而失败。
3. 实现单文件 YAML 读取、根 mapping 校验、`PermissionFileConfig` 和 `PermissionRule` 构造。
4. 拒绝 null/列表/嵌套参数值、未知版本、未知字段、未知 effect、`forbidden` 和空 ID/工具名。

**验证：** `pytest -q tests/test_permission_config.py`，期望合法 fixture 解析、非法 fixture 抛出 `PermissionConfigError`。

**提交：** `git add src/mycode/permission/config.py tests/test_permission_config.py; git commit -m "feat: parse permission yaml" -- src/mycode/permission/config.py tests/test_permission_config.py`

## T6：实施配置来源信任约束

**文件：** `src/mycode/permission/config.py`、`tests/test_permission_config.py`
**依赖：** T5

**步骤：**

1. 增加用户全局/本地项目允许 mode 与 ALLOW、仓库项目拒绝 mode/ALLOW/workspace 的测试。
2. 增加单来源重复 ID、相同条件冲突和不同来源相同 ID 可共存的测试。
3. 运行新增测试，确认来源限制尚未生效。
4. 让解析入口接收 `RuleSource`，按来源校验允许字段与 effect，并为仓库不能扩大权限写中文注释。

**验证：** `pytest -q tests/test_permission_config.py`，期望恶意仓库配置被拒绝，可信来源正常加载。

**提交：** `git add src/mycode/permission/config.py tests/test_permission_config.py; git commit -m "feat: enforce permission source trust" -- src/mycode/permission/config.py tests/test_permission_config.py`

## T7：实现权限路径和多来源加载

**文件：** `src/mycode/permission/config.py`、`tests/test_permission_config.py`
**依赖：** T6

**步骤：**

1. 编写用户全局、仓库项目和本地项目路径测试，断言本地目录使用规范化工作区完整 SHA-256。
2. 增加缺失文件视为空配置、本地 workspace 字段匹配/不匹配和工作区移动产生新目录测试。
3. 运行新增测试，确认 `PermissionStore.load()` 不存在而失败。
4. 实现 `PermissionPaths` 构造、三来源加载和本地 workspace 校验；允许注入临时 home 以隔离测试。

**验证：** `pytest -q tests/test_permission_config.py`，期望所有路径与加载生命周期测试通过。

**提交：** `git add src/mycode/permission/config.py tests/test_permission_config.py; git commit -m "feat: load layered permission sources" -- src/mycode/permission/config.py tests/test_permission_config.py`

## T8：实现会话规则和有效档位

**文件：** `src/mycode/permission/config.py`、`tests/test_permission_config.py`
**依赖：** T7

**步骤：**

1. 编写 `会话 > 本地项目 > 用户全局 > DEFAULT` 档位测试，以及会话规则添加、同 ID 幂等替换、条件冲突和 reset 测试。
2. 运行新增测试，确认 `effective_mode()`、`add_session_rule()` 和 `clear_session()` 尚未实现。
3. 实现 `PermissionSessionState` 与 store 的会话 API，仓库来源不参与 mode 计算。
4. `clear_session()` 只清会话档位与规则，不修改三类持久配置。

**验证：** `pytest -q tests/test_permission_config.py`，期望会话生命周期和档位测试通过。

**提交：** `git add src/mycode/permission/config.py tests/test_permission_config.py; git commit -m "feat: manage permission session state" -- src/mycode/permission/config.py tests/test_permission_config.py`

## T9：实现本地项目授权原子持久化

**文件：** `src/mycode/permission/config.py`、`tests/test_permission_config.py`
**依赖：** T8

**步骤：**

1. 编写首次创建、幂等更新、保留 workspace/mode/其他规则、`os.replace` 失败保留旧文件和不修改仓库策略测试。
2. 运行新增测试，确认 `persist_local_project_rule()` 不存在而失败。
3. 使用同目录 `NamedTemporaryFile`、UTF-8 `safe_dump`、`flush/fsync` 和 `os.replace` 实现同步原子写入，并通过 `asyncio.to_thread` 暴露异步方法。
4. 替换成功后才更新内存；异常清理临时文件并抛出 `PermissionPersistenceError`，关键顺序写中文注释。

**验证：** `pytest -q tests/test_permission_config.py`，期望写入成功与失败恢复测试通过。

**提交：** `git add src/mycode/permission/config.py tests/test_permission_config.py; git commit -m "feat: persist local project grants" -- src/mycode/permission/config.py tests/test_permission_config.py`

## T10：实现 shell 保守扫描器

**文件：** `src/mycode/permission/command.py`、`tests/test_permission_command.py`
**依赖：** T1

**步骤：**

1. 编写 POSIX、cmd、PowerShell 在引号外切分 `|`、`&&`、`||`、`;` 和换行的测试，并验证引号内操作符不切分。
2. 运行测试，确认 `CommandAnalyzer` 不存在而失败。
3. 实现平台选择、引号/转义状态机和控制链片段结构，POSIX、cmd、PowerShell 分别处理 `\`、`^` 和反引号。
4. 分析器只返回分类数据，不调用 subprocess 或访问网络。

**验证：** `pytest -q tests/test_permission_command.py`，期望基础扫描用例通过。

**提交：** `git add src/mycode/permission/command.py tests/test_permission_command.py; git commit -m "feat: scan shell command chains" -- src/mycode/permission/command.py tests/test_permission_command.py`

## T11：处理嵌套 shell 与不确定结构

**文件：** `src/mycode/permission/command.py`、`tests/test_permission_command.py`
**依赖：** T10

**步骤：**

1. 增加 `cmd /c`、`powershell/pwsh -command`、`sh/bash -c` 递归测试。
2. 增加引号不闭合、编码命令、命令替换、未知启动器、32768 字符上限和深度超过 3 返回 `ASK(command_ambiguous)` 的测试。
3. 运行新增测试，确认至少一个不确定结构被错误当作普通命令。
4. 实现嵌套入口识别和所有保守降级；在不能证明安全的分支写中文注释。

**验证：** `pytest -q tests/test_permission_command.py`，期望嵌套与降级测试通过。

**提交：** `git add src/mycode/permission/command.py tests/test_permission_command.py; git commit -m "feat: analyze nested shell commands" -- src/mycode/permission/command.py tests/test_permission_command.py`

## T12：识别破坏性删除与磁盘命令

**文件：** `src/mycode/permission/command.py`、`tests/test_permission_command.py`
**依赖：** T2、T11

**步骤：**

1. 为 POSIX `rm/rmdir`、cmd `del/rmdir`、PowerShell `Remove-Item` 编写工作区根、用户主目录、文件系统/磁盘根和系统目录 `FORBIDDEN` 测试。
2. 为 `mkfs*`、`format`、`diskpart`、`dd of=/dev/*`、`Format-Volume`、`Clear-Disk` 编写 `FORBIDDEN` 测试。
3. 运行新增测试，确认当前分类未达到预期。
4. 使用构造时注入的 workspace/home/platform 规范化保护目标，实现高置信度分类；不确定目标降级为 `ASK`。

**验证：** `pytest -q tests/test_permission_command.py`，期望受保护根和磁盘破坏全部不可审批。

**提交：** `git add src/mycode/permission/command.py tests/test_permission_command.py; git commit -m "feat: forbid destructive system commands" -- src/mycode/permission/command.py tests/test_permission_command.py`

## T13：识别下载即执行

**文件：** `src/mycode/permission/command.py`、`tests/test_permission_command.py`
**依赖：** T11

**步骤：**

1. 编写 `curl/wget | sh/bash/python`、PowerShell `iwr/irm | iex` 和 `Invoke-Expression` 的 `FORBIDDEN` 测试。
2. 编写同一链中 `curl -o x && sh x`、`wget -O x; python x` 和 PowerShell 下载文件后执行的测试。
3. 运行新增测试，确认远程生产者与执行消费者尚未关联。
4. 在扫描片段间跟踪管道生产者和明确下载目标，只对同一命令链的直接执行返回 `FORBIDDEN`，单纯下载保留给 ASK 分类。

**验证：** `pytest -q tests/test_permission_command.py`，期望远程脚本直接执行全部被硬拒绝。

**提交：** `git add src/mycode/permission/command.py tests/test_permission_command.py; git commit -m "feat: forbid download and execute chains" -- src/mycode/permission/command.py tests/test_permission_command.py`

## T14：补齐可审批高风险命令

**文件：** `src/mycode/permission/command.py`、`tests/test_permission_command.py`
**依赖：** T11

**步骤：**

1. 增加工作区内部删除、`git clean/reset --hard`、包安装、网络上传下载、远程 Git 写入测试。
2. 增加 `sudo/runas`、权限/所有权修改、服务/计划任务管理和进程终止测试。
3. 运行新增测试，确认分类结果不是 `ASK` 或原因码不稳定。
4. 实现有限的内置 ASK 分类表，返回稳定英文 category/reason code 和中文说明；普通安全命令返回无内置风险。

**验证：** `pytest -q tests/test_permission_command.py`，期望 ASK、FORBIDDEN 和普通命令三类边界清晰。

**提交：** `git add src/mycode/permission/command.py tests/test_permission_command.py; git commit -m "feat: classify risky shell commands" -- src/mycode/permission/command.py tests/test_permission_command.py`

## T15：构建并规范化 PermissionSubject

**文件：** `src/mycode/permission/policy.py`、`tests/test_permission_policy.py`
**依赖：** T3、T4、T5

**步骤：**

1. 编写非法 JSON、缺失 required、基础类型错误、普通标量复制、路径规范化和命令空白规范化测试。
2. 增加 `find_files/search_code` 缺失 root 时规范化为 `.` 的测试。
3. 运行测试，确认 `build_subject()` 不存在而失败。
4. 实现 JSON Schema 当前使用子集的契约校验、路径 `PathGuard.inspect()`、引号外连续空白规范化，并用 `MappingProxyType` 冻结映射。

**验证：** `pytest -q tests/test_permission_policy.py`，期望主体规范化与非法参数 fail-closed 测试通过。

**提交：** `git add src/mycode/permission/policy.py tests/test_permission_policy.py; git commit -m "feat: normalize permission subjects" -- src/mycode/permission/policy.py tests/test_permission_policy.py`

## T16：实现授权参数提取与脱敏

**文件：** `src/mycode/permission/policy.py`、`tests/test_permission_policy.py`
**依赖：** T15

**步骤：**

1. 编写文件正文不进入授权参数、六个内置工具只提取声明字段、自定义工具空授权参数测试。
2. 编写敏感字段名、URL userinfo/query、命令行凭据、敏感环境赋值和 512 字符截断测试。
3. 运行新增测试，确认授权映射或展示映射泄露 fixture 值。
4. 实现 `grant_arguments` 白名单提取和脱敏函数；保持命令结构，只替换敏感值并追加中文截断标记。

**验证：** `pytest -q tests/test_permission_policy.py`，期望 fixture 凭据不出现在展示字符串或授权规则中。

**提交：** `git add src/mycode/permission/policy.py tests/test_permission_policy.py; git commit -m "feat: redact permission arguments" -- src/mycode/permission/policy.py tests/test_permission_policy.py`

## T17：实现规则匹配与具体度

**文件：** `src/mycode/permission/policy.py`、`tests/test_permission_policy.py`
**依赖：** T16

**步骤：**

1. 编写精确工具/`*`、精确字符串/glob、数字/布尔精确匹配和 Windows 路径 match value 测试。
2. 编写 `精确工具 > 参数更多 > 精确参数 > DENY > ASK > ALLOW > 规则 ID` 的确定性测试，并交换 YAML 顺序验证结果不变。
3. 运行新增测试，确认规则选择入口不存在或顺序不稳定。
4. 实现 `match_rule()`、`RuleSpecificity` 计算和 `select_rule()`；优先级分支写中文注释解释拒绝优先原因。

**验证：** `pytest -q tests/test_permission_policy.py`，期望全部匹配与排序测试通过。

**提交：** `git add src/mycode/permission/policy.py tests/test_permission_policy.py; git commit -m "feat: evaluate permission rules" -- src/mycode/permission/policy.py tests/test_permission_policy.py`

## T18：实现来源、档位与 plan-only 策略链

**文件：** `src/mycode/permission/policy.py`、`tests/test_permission_policy.py`
**依赖：** T8、T17

**步骤：**

1. 编写 `SESSION > LOCAL_PROJECT > REPOSITORY_PROJECT > USER_GLOBAL` 首个命中来源测试，验证较低来源不再参与。
2. 编写 strict/default/permissive 未命中行为、显式 DENY 不被 permissive 覆盖和仓库 mode 不参与测试。
3. 编写 plan-only 对写工具 `ALLOW→ASK`、ASK 保持、DENY/FORBIDDEN 不降级和读工具不变测试。
4. 实现纯规则/档位/plan-only 判定链，并在来源截断和 plan-only 叠加处添加中文安全注释。

**验证：** `pytest -q tests/test_permission_policy.py`，期望来源、档位和模式矩阵全部通过。

**提交：** `git add src/mycode/permission/policy.py tests/test_permission_policy.py; git commit -m "feat: compose permission policy layers" -- src/mycode/permission/policy.py tests/test_permission_policy.py`

## T19：接入命令风险与不可覆盖安全底线

**文件：** `src/mycode/permission/policy.py`、`tests/test_permission_policy.py`
**依赖：** T12、T13、T14、T18

**步骤：**

1. 编写 FORBIDDEN 在所有普通规则与档位前终止的测试。
2. 编写精确普通 ALLOW 可以满足内置 ASK、无规则时 ASK 不被 permissive 兜底覆盖的测试。
3. 编写命令分析器异常转换为 `DENY(security_check_failed)` 的测试。
4. 在 `PermissionPolicy.evaluate()` 中先终止 FORBIDDEN，再选择普通规则；仅在无规则时应用 ASK 风险，最后使用档位与 plan-only。

**验证：** `pytest -q tests/test_permission_policy.py tests/test_permission_command.py`，期望安全底线不可覆盖且风险授权语义正确。

**提交：** `git add src/mycode/permission/policy.py tests/test_permission_policy.py; git commit -m "feat: enforce command safety policy" -- src/mycode/permission/policy.py tests/test_permission_policy.py`

## T20：实现 PermissionService 判定门面

**文件：** `src/mycode/permission/service.py`、`tests/test_permission_service.py`
**依赖：** T9、T19

**步骤：**

1. 编写 `PermissionService.create()` 装配同一 store/policy/analyzer/path guard、`evaluate()` 返回决定和只缓存 ASK 主体测试。
2. 编写 invalid arguments、DENY、FORBIDDEN 和内部异常生成安全中文 `ToolResult` 的测试。
3. 运行测试，确认服务入口不存在而失败。
4. 实现服务工厂、ASK 缓存、`denied_result()`、档位委托和 `clear_session()`；拒绝结果不包含规则全集或堆栈。

**验证：** `pytest -q tests/test_permission_service.py`，期望判定与结果转换测试通过。

**提交：** `git add src/mycode/permission/service.py tests/test_permission_service.py; git commit -m "feat: add permission service facade" -- src/mycode/permission/service.py tests/test_permission_service.py`

## T21：生成审批请求与精确候选授权

**文件：** `src/mycode/permission/service.py`、`tests/test_permission_service.py`
**依赖：** T20

**步骤：**

1. 编写普通 ASK 提供五个选项、plan-only 和空授权参数只提供本次/拒绝/取消的测试。
2. 编写候选 `PermissionGrant` 只含规范化授权参数、fingerprint 稳定、规则 ID 为 `hitl-<tool>-<12位摘要>` 的测试。
3. 编写非 ASK、缺失缓存和调用 ID 不匹配时 fail-closed 的测试。
4. 实现 `create_approval_request()`，请求创建后移除缓存，TUI 不参与候选授权构造。

**验证：** `pytest -q tests/test_permission_service.py`，期望审批请求和授权摘要测试通过。

**提交：** `git add src/mycode/permission/service.py tests/test_permission_service.py; git commit -m "feat: build scoped approval requests" -- src/mycode/permission/service.py tests/test_permission_service.py`

## T22：处理五种审批结果

**文件：** `src/mycode/permission/service.py`、`tests/test_permission_service.py`
**依赖：** T21

**步骤：**

1. 编写本次允许不写规则、会话允许生成 SESSION 规则、项目允许生成 LOCAL_PROJECT 规则并等待持久化测试。
2. 编写拒绝、取消、非法选项和持久化失败返回对应 `ApprovalOutcome`/中文 ToolResult 的测试。
3. 运行新增测试，确认审批处理未实现。
4. 实现 `resolve_approval()`；持久化成功前不返回 EXECUTE，并在该关键顺序写中文注释。

**验证：** `pytest -q tests/test_permission_service.py tests/test_permission_config.py`，期望五种审批与失败恢复测试通过。

**提交：** `git add src/mycode/permission/service.py tests/test_permission_service.py; git commit -m "feat: resolve permission approvals" -- src/mycode/permission/service.py tests/test_permission_service.py`

## T23：实现拦截器并迁移旧权限模块

**文件：** `src/mycode/permission/service.py`、`src/mycode/agent/__init__.py`、`src/mycode/agent/events.py`、`src/mycode/agent/approval.py`、`src/mycode/agent/interceptor.py`、`tests/test_permission_service.py`、`tests/test_agent_events.py`、`tests/test_agent_interceptor.py`
**依赖：** T22

**步骤：**

1. 把旧 PlanOnlyInterceptor 的 allow/ask/after_tool 契约用例迁入 `test_permission_service.py`，并更新 AgentEvent 使用新 ApprovalRequest 的测试。
2. 实现 `PermissionInterceptor.before_tool()`、`resolve_approval()` 和不修改结果的 `after_tool()`。
3. 更新 `agent/__init__.py` 与 `events.py` 的直接导入，删除旧 `approval.py`、`interceptor.py` 和 `test_agent_interceptor.py`。
4. 运行测试，确认 Agent 公共兼容导出仍可用，权限实现只存在于 permission 包。

**验证：** `pytest -q tests/test_permission_service.py tests/test_agent_events.py`，期望迁移后的契约通过；`rg "class PlanOnlyInterceptor|class ToolInterceptor" src/mycode/agent` 无匹配。

**提交：** `git add -A -- src/mycode/permission/service.py src/mycode/agent/__init__.py src/mycode/agent/events.py src/mycode/agent/approval.py src/mycode/agent/interceptor.py tests/test_permission_service.py tests/test_agent_events.py tests/test_agent_interceptor.py; git commit -m "refactor: move approvals into permission domain" -- src/mycode/permission/service.py src/mycode/agent/__init__.py src/mycode/agent/events.py src/mycode/agent/approval.py src/mycode/agent/interceptor.py tests/test_permission_service.py tests/test_agent_events.py tests/test_agent_interceptor.py`

## T24：迁移文件工具到新 PathGuard

**文件：** `src/mycode/tool/defaults.py`、`src/mycode/tool/filesystem.py`、`src/mycode/tool/__init__.py`、`src/mycode/tool/pathing.py`、`tests/test_tool_filesystem.py`
**依赖：** T3、T4

**步骤：**

1. 更新测试从 `permission.pathing` 导入 PathGuard，并增加 find/search 对越界符号链接候选不读取的测试。
2. 修改 `create_default_tool_registry()` 接收可选共享 PathGuard，文件工具改用新模块。
3. 在 find/search 遍历到每个候选文件时调用 `inspect()`；任何候选越界都让本次工具返回结构化路径失败，不读取或静默跳过该候选。
4. 移除工具包 PathGuard 导出并删除 `tool/pathing.py`。

**验证：** `pytest -q tests/test_permission_pathing.py tests/test_tool_filesystem.py`，期望路径迁移和执行期复检通过；`rg "tool\.pathing" src tests` 无匹配。

**提交：** `git add -A -- src/mycode/tool/defaults.py src/mycode/tool/filesystem.py src/mycode/tool/__init__.py src/mycode/tool/pathing.py tests/test_tool_filesystem.py; git commit -m "refactor: move path guard to permission" -- src/mycode/tool/defaults.py src/mycode/tool/filesystem.py src/mycode/tool/__init__.py src/mycode/tool/pathing.py tests/test_tool_filesystem.py`

## T25：接入 AgentLoop 非审批决定

**文件：** `src/mycode/agent/loop.py`、`tests/test_agent_loop.py`
**依赖：** T23、T24

**步骤：**

1. 更新测试 helper 为每个 AgentLoop 注入真实或 fake `PermissionInterceptor`，增加 ALLOW、DENY、FORBIDDEN 不执行 fake tool 的测试。
2. 运行目标测试，确认构造签名或决定分支失败。
3. 将 AgentLoop 的可选旧 interceptor 改为必需 `permission`；调用 `before_tool(plan_only=..., round_index=...)`。
4. ALLOW 加入执行列表；DENY/FORBIDDEN yield TOOL_RESULT、写入 memory 并继续；after_tool 保持现有结果语义。

**验证：** `pytest -q tests/test_agent_loop.py`，期望普通工具循环、调度和非审批决定测试通过。

**提交：** `git add src/mycode/agent/loop.py tests/test_agent_loop.py; git commit -m "feat: gate agent tools with permissions" -- src/mycode/agent/loop.py tests/test_agent_loop.py`

## T26：接入 Agent 审批、plan-only 与并发

**文件：** `src/mycode/agent/loop.py`、`tests/test_agent_loop.py`、`tests/test_agent_plan_only.py`
**依赖：** T25

**步骤：**

1. 迁移 approve once/reject/cancel 测试，新增会话/项目批准、无 provider 拒绝后继续 turn 和持久化错误继续 turn 测试。
2. 新增多个连续读调用依次审批、只对获准调用执行且获准读仍并发的测试。
3. 运行目标测试，确认旧 APPROVE_ONCE-only 分支无法满足新结果。
4. 用 `create_approval_request()` 与 `resolve_approval()` 处理四类 outcome；取消终止，其他拒绝/错误回填后继续。

**验证：** `pytest -q tests/test_agent_loop.py tests/test_agent_plan_only.py`，期望审批、plan-only、并发和 memory 顺序通过。

**提交：** `git add src/mycode/agent/loop.py tests/test_agent_loop.py tests/test_agent_plan_only.py; git commit -m "feat: integrate scoped agent approvals" -- src/mycode/agent/loop.py tests/test_agent_loop.py tests/test_agent_plan_only.py`

## T27：扩展 ChatSession 权限状态

**文件：** `src/mycode/session.py`、`tests/test_session.py`
**依赖：** T23、T26

**步骤：**

1. 更新 FakeAgent/FakePermissionService，编写构造注入、档位查询、档位设置和 clear 委托测试。
2. 运行测试，确认 ChatSession 缺少 permissions 参数和方法。
3. 增加 `permissions: PermissionService`、`permission_mode()`、`set_permission_mode()`。
4. `clear()` 按顺序清 memory、复位 plan-only、清会话规则/档位，持久规则不变。

**验证：** `pytest -q tests/test_session.py`，期望 Session 转发与清理测试通过。

**提交：** `git add src/mycode/session.py tests/test_session.py; git commit -m "feat: manage session permission mode" -- src/mycode/session.py tests/test_session.py`

## T28：实现 TUI 权限命令和中文审批

**文件：** `src/mycode/tui.py`、`tests/test_tui.py`
**依赖：** T27

**步骤：**

1. 编写 `/permission` 查询、三档设置、非法参数中文用法和命令不发送模型请求测试。
2. 编写普通 `o/y/s/p/n/c`、plan-only/空授权参数不接受 s/p、脱敏参数与中文原因展示测试。
3. 运行测试，确认 TUI 尚不支持新命令和选择。
4. 实现命令解析、档位来源中文映射、动态审批选项和无效输入中文提示；TOOL_CALL_STARTED 文案改为“工具请求”。

**验证：** `pytest -q tests/test_tui.py`，期望所有权限命令与审批交互测试通过且提示语为中文。

**提交：** `git add src/mycode/tui.py tests/test_tui.py; git commit -m "feat: add Chinese permission TUI" -- src/mycode/tui.py tests/test_tui.py`

## T29：装配 CLI 权限服务

**文件：** `src/mycode/cli.py`、`tests/test_cli.py`
**依赖：** T24、T26、T27、T28

**步骤：**

1. 编写 CLI 以 cwd/home 创建服务、共享同一 PathGuard、向 Agent/Session 注入服务的测试。
2. 编写用户/本地/仓库配置错误打印中文 stderr 并返回 1 的测试。
3. 运行测试，确认装配参数不匹配。
4. 按 plan.md 启动顺序创建 PermissionService、registry、executor、interceptor、AgentLoop、ChatSession 和 TUI，并捕获 PermissionConfigError。

**验证：** `pytest -q tests/test_cli.py`，期望成功装配与配置失败路径通过。

**提交：** `git add src/mycode/cli.py tests/test_cli.py; git commit -m "feat: wire permission service in CLI" -- src/mycode/cli.py tests/test_cli.py`

## T30：验证权限元数据不进入供应商请求

**文件：** `tests/test_openai_chat_protocol.py`、`tests/test_openai_responses_protocol.py`
**依赖：** T4

**步骤：**

1. 在两个协议测试中使用非空 `grant_arguments` 的 ToolDefinition。
2. 断言序列化 payload 只含名称、描述和 parameters，不含 `kind` 或 `grant_arguments`。
3. 运行两个测试；若字段泄漏，修改对应协议映射为显式字段映射，不使用 dataclass 全量序列化。
4. 重跑现有 Anthropic/LLM 工具定义测试，确认默认字段不影响兼容性。

**验证：** `pytest -q tests/test_openai_chat_protocol.py tests/test_openai_responses_protocol.py tests/test_anthropic_protocol.py tests/test_llm_base.py`，期望全部通过。

**提交：** `git add tests/test_openai_chat_protocol.py tests/test_openai_responses_protocol.py src/mycode/protocols/openai_chat.py src/mycode/protocols/openai_responses.py; git commit -m "test: keep permission metadata local" -- tests/test_openai_chat_protocol.py tests/test_openai_responses_protocol.py src/mycode/protocols/openai_chat.py src/mycode/protocols/openai_responses.py`

## T31：建立权限端到端场景

**文件：** `tests/test_permission_e2e.py`
**依赖：** T19、T22、T26、T29、T30

**步骤：**

1. 建立 fake LLM、记录型 fake executor、临时 workspace/home 和脚本化审批 provider。
2. 覆盖普通允许、风险本次批准、会话复用、项目持久授权、用户拒绝、无 provider、FORBIDDEN、路径越界和 plan-only。
3. 覆盖恶意仓库 `allow/mode` 启动失败及合法 `deny/ask` 只能收紧，断言仓库文件从未被 HITL 修改。
4. 断言所有阻断场景 executor 调用数为 0，持久授权只出现在临时 home 的工作区哈希目录。

**验证：** `pytest -q tests/test_permission_e2e.py`，期望完整交互链通过且不执行真实命令或网络请求。

**提交：** `git add tests/test_permission_e2e.py; git commit -m "test: cover permission workflow end to end" -- tests/test_permission_e2e.py`

## T32：更新文档与安全示例

**文件：** `README.md`、`examples/mycode.permissions.yaml`、`tests/test_docs.py`
**依赖：** T28、T29

**步骤：**

1. 扩展文档测试，要求 README 包含三档模式、三层来源、仓库只允许 DENY/ASK、HITL 范围、路径沙箱与 shell 非 OS 沙箱边界。
2. 运行 `pytest -q tests/test_docs.py`，确认文档断言失败。
3. 用中文更新 README；新增不含凭据、只含 deny/ask 的仓库策略示例。
4. 明确项目永久允许写入用户目录、FORBIDDEN 不可覆盖及所有权限提示使用中文。

**验证：** `pytest -q tests/test_docs.py`，期望文档行为说明测试通过。

**提交：** `git add README.md examples/mycode.permissions.yaml tests/test_docs.py; git commit -m "docs: explain permission safety model" -- README.md examples/mycode.permissions.yaml tests/test_docs.py`

## T33：执行全量回归并检查关键中文注释

**文件：** 本阶段所有新增与修改文件
**依赖：** T1-T32

**步骤：**

1. 运行 `python -m compileall src`，修复所有语法、导入和循环依赖问题。
2. 运行 `pytest -q`，逐项修复回归；不得通过删除或弱化既有测试绕过失败。
3. 运行 `git diff --check`，确认无空白错误；运行 `rg "tool\.pathing|agent\.approval|agent\.interceptor" src tests`，确认旧实现引用已清除。
4. 人工检查策略优先级、FORBIDDEN、命令降级、路径边界、持久化失败和 plan-only 分支均有解释安全理由的中文注释；检查 TUI/CLI 权限提示均为中文。

**验证：** `python -m compileall src` 退出码 0；`pytest -q` 全部通过；`git diff --check` 无输出；旧模块引用搜索无匹配。

**提交：** 本任务只做总验证。若发现失败，返回负责该行为的 T1-T32 修复并复用该任务的精确 pathspec 提交；全量验证直接通过时不创建空提交。

## 执行顺序

```text
T1
├── T2 → T3 ───────────────┐
├── T4 ────────────────────┤
├── T5 → T6 → T7 → T8 → T9
└── T10 → T11 ┬→ T12 ─────┤
              ├→ T13 ─────┤
              └→ T14 ─────┤
                             ↓
T3 + T4 + T5 → T15 → T16 → T17
T8 + T17 → T18
T12 + T13 + T14 + T18 → T19
T9 + T19 → T20 → T21 → T22 → T23
T3 + T4 → T24
T23 + T24 → T25 → T26
T23 + T26 → T27 → T28
T24 + T26 + T27 + T28 → T29
T4 → T30
T19 + T22 + T26 + T29 + T30 → T31
T28 + T29 → T32
T1-T32 → T33
```

T2/T4/T5/T10 可在 T1 后并行；T12/T13/T14 可在 T11 后并行；T24 可与 T15-T23 的策略主线并行。共享文件上的并行任务必须在执行前确认没有未提交改动，避免覆盖其他任务。

## 需求覆盖

| Spec | 任务 |
|------|------|
| F1 | T15、T20、T25 |
| F2 | T1、T23、T24 |
| F3 | T12、T13、T19 |
| F4 | T14、T19 |
| F5 | T6-T8、T18 |
| F6 | T5、T6、T17 |
| F7 | T8、T18、T27、T28 |
| F8 | T16、T21、T22、T26、T28 |
| F9 | T18、T26、T28 |
| F10 | T2、T3、T15、T24 |
| F11 | T10-T14、T19 |
| F12 | T20、T25、T26 |
| F13 | T5-T9、T29 |
| F14 | T16、T20-T22、T28 |
| F15 | T1-T33 各关键安全分支，T33 汇总检查 |
| F16 | T25-T33 |
