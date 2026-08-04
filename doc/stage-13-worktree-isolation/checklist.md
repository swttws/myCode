# myCode Stage 13：子 Agent Worktree 隔离验收清单

> 所有条目都在实现完成后执行。先运行验证命令、记录实际输出，再勾选结果；不得用代码阅读或主观判断代替可观察证据。

## 验收环境

- [ ] Stage 13 自动化测试只使用临时 Git 仓库、临时 home、受控文件系统、fake LLM、fake 调度器和本地 bare remote，不访问真实网络、真实 API、真实用户 Git 配置、真实远端或工作区外文件。（验证：清空模型供应商 API key 后运行 `python -m pytest tests/test_worktree_e2e.py tests/test_subagent_e2e.py tests/test_permission_e2e.py -q`，期望所有 Git 路径位于 `tmp_path`，远端为测试创建的本地 bare repository。）
- [ ] 本阶段开始前已有工作区变更未被恢复、覆盖或混入 Stage 13 提交。（验证：运行 `git status --short`，期望只出现 Stage 13 文档、实现、测试、示例和进入本阶段前已记录的既有变更。）
- [ ] 测试集合可收集，且 Stage 13 之前的缺失模块问题已独立处理。（验证：运行 `python -m pytest --collect-only -q`，期望退出码 `0`；若出现 `ModuleNotFoundError: mycode.subagent.rendering`，先在独立范围恢复基线，不把该修复混入 Stage 13。）
- [ ] Worktree 相关测试不依赖进程级 cwd 切换。（验证：运行 `rg -n "os\.chdir|Path\.cwd\(" src/mycode/worktree src/mycode/subagent src/mycode/agent src/mycode/tool src/mycode/hook src/mycode/skill`，期望新增 Worktree/运行时路径不包含 `os.chdir`，生产 `AgentLoop` 不依赖 `Path.cwd()`；CLI 首次捕获主工作区的既有 `Path.cwd()` 可保留。）

## 角色声明与任务身份

- [ ] **AC1：** 声明 `isolation: worktree` 的定义式角色使用隔离目录，未声明角色和 Fork 继续使用原工作区，非法隔离值产生中文诊断且角色不启动。（验证：运行 `python -m pytest tests/test_subagent_loader.py tests/test_subagent_service.py tests/test_subagent_isolation.py -q -k "isolation or worktree or fork or invalid"`，期望 defined/worktree、defined/shared 与 fork 分支状态分别符合设计。）
- [ ] **AC2：** 同一角色的两个任务得到不同的受控目录和临时分支，目录为 `.worktrees/<role>/<task-token>`，分支为 `mycode/worktree/<role>/<task-token>`，名称符合长度与字符规则。（验证：运行 `python -m pytest tests/test_worktree_pathing.py tests/test_subagent_tasks.py tests/test_subagent_isolation.py -q -k "name or branch or identity or unique"`，期望两个任务的 `WorkspaceTaskIdentity`、目录和分支互不相同且均通过安全校验。）
- [ ] Worktree 任务身份包含稳定仓库指纹、任务 ID、角色名、任务令牌、相对名、临时分支和启动时 `HEAD`。（验证：运行 `python -m pytest tests/test_workspace_models.py tests/test_worktree_manager.py -q -k "identity or repository_id or base_commit"`，期望字段不可变且 `base_commit` 为有效提交。）

## 路径、配置与 Git 安全

- [ ] **AC3：** 空段、`.`、`..`、绝对路径、反斜杠、盘符、控制字符、平台保留名和超长段全部被拒绝，仓库外文件保持不变。（验证：运行 `python -m pytest tests/test_worktree_pathing.py -q -k "invalid or reserved or boundary"`，期望每个坏值抛出稳定 `WorktreeError`，测试哨兵文件未变化。）
- [ ] **AC4：** `.worktrees/` 被替换为指向仓库外的符号链接或目录联接后，创建、恢复和清理均拒绝执行。（验证：运行 `python -m pytest tests/test_worktree_pathing.py tests/test_worktree_cleaner.py tests/test_worktree_manager.py -q -k "symlink or junction or boundary"`，期望失败关闭且没有 Git 删除或文件删除发生。）
- [ ] **AC5：** 主工作区有未提交修改时创建隔离任务，子 Worktree 基于启动时已提交 `HEAD`，看不到主工作区未提交内容。（验证：运行 `python -m pytest tests/test_worktree_manager.py tests/test_worktree_e2e.py -q -k "uncommitted or base_commit or isolated"`，期望子目录文件内容来自捕获的 `HEAD`。）
- [ ] **AC6：** 首次创建后，Git 可观察到独立工作目录和独立临时分支，主工作区分支、索引和文件内容不变。（验证：运行 `python -m pytest tests/test_worktree_git.py tests/test_worktree_manager.py -q -k "add or branch or main_unchanged"`，期望 `git worktree list --porcelain -z` 解析结果包含隔离目录。）
- [ ] Git 网关始终使用结构化参数、显式 `cwd`、有界输出和配置超时，不通过 shell 字符串拼接执行 Git。（验证：运行 `python -m pytest tests/test_worktree_git.py -q -k "argv or cwd or timeout or bounded or porcelain"`，期望 fake runner 捕获 `shell=False`、参数数组和 stdout/stderr 截断行为。）
- [ ] **AC9：** 合法 `.mycode/worktree.yaml` 可加载；未知规则、重复目标、越界来源、循环链接、超限规则或非法超时在应用启动时产生稳定错误。（验证：运行 `python -m pytest tests/test_worktree_config.py -q`，期望合法配置摘要稳定，非法配置全部失败且错误定位到字段或规则。）

## 元数据、初始化与恢复

- [ ] **AC7：** 目标目录和完整 READY 元数据已存在时可快速恢复，全过程没有 Git 调用、文件写入、重新初始化或仓库遍历。（验证：运行 `python -m pytest tests/test_worktree_metadata.py tests/test_worktree_manager.py -q -k "recover or readonly or no_git"`，期望 spy 记录 Git、write 和 initializer 调用数均为 `0`。）
- [ ] **AC8：** 元数据缺失、初始化未完成、仓库身份不符、任务身份不符、配置摘要不符或路径不符时，恢复失败且既有目录不被修改。（验证：运行 `python -m pytest tests/test_worktree_metadata.py tests/test_worktree_manager.py -q -k "mismatch or incomplete or preserve"`，期望目录 mtime、文件内容和 sidecar 内容保持不变。）
- [ ] **AC10：** 配置声明的本地文件被复制、依赖目录被符号链接、忽略文件被补齐，子 Worktree 使用自己的 hooks 设置且主工作区 hooks 行为不变。（验证：运行 `python -m pytest tests/test_worktree_initializer.py tests/test_tool_command.py -q -k "copy or ignored_copy or symlink or hooks"`，期望完成规则顺序与配置一致，主仓库 Git 配置未变化。）
- [ ] **AC11：** 首次创建后的必需初始化步骤失败时，子 Agent 不启动且安全回滚；快速恢复验证失败时既有目录不会被删除或改写。（验证：运行 `python -m pytest tests/test_worktree_initializer.py tests/test_worktree_manager.py tests/test_subagent_service.py -q -k "failure or rollback or preserve"`，期望任务进入失败终态并报告失败步骤。）
- [ ] 元数据写入为 sidecar、分阶段、原子替换且大小有界。（验证：运行 `python -m pytest tests/test_worktree_metadata.py -q -k "atomic or size or phase or scan"`，期望 `CREATING` 只在初始化完成后变为 `READY`，超过 64 KiB 的元数据被拒绝。）

## 工作区上下文与工具接入

- [ ] **AC12：** 并发运行主 Agent 和两个隔离子 Agent 时，进程当前目录始终不变；文件工具、命令工具、权限、Hook 和 Skill 只观察到显式传入的工作目录。（验证：运行 `python -m pytest tests/test_agent_loop.py tests/test_tool_executor.py tests/test_tool_filesystem.py tests/test_tool_command.py tests/test_hook_agent.py tests/test_skill_agent.py tests/test_subagent_tooling.py -q -k "workspace or cwd or worktree"`，期望每个 spy 记录的 cwd/root 为对应 `WorkspaceContext.root`。）
- [ ] **AC13：** 两个 Worktree 中相同相对路径的文件内容、Prompt、项目指令和项目记忆使用不同绝对身份，一方更新不改变另一方缓存结果。（验证：运行 `python -m pytest tests/test_tool_cache.py tests/test_memory_paths.py tests/test_memory_instructions.py tests/test_project_memory_e2e.py -q -k "workspace or root or project or absolute"`，期望缓存 key、项目 hash、session 和 memory 目录互不相同。）
- [ ] **AC14：** 隔离子 Agent 的首个请求包含正确的绝对路径、临时分支和隔离说明，并加载该 Worktree 内的项目指令。（验证：运行 `python -m pytest tests/test_subagent_context.py tests/test_subagent_runtime.py -q -k "worktree or prompt or instructions"`，期望 fake LLM 捕获的系统上下文只引用子 Worktree 路径。）
- [ ] Worktree 模式下只开放显式 `WORKSPACE_AWARE` 的本地工具，MCP 与未知作用域工具默认隐藏或拒绝；共享模式保持兼容。（验证：运行 `python -m pytest tests/test_tool_registry.py tests/test_mcp_tools.py tests/test_subagent_tooling.py -q -k "workspace or shared_only or mcp"`，期望供应商 tool schema 不包含本地作用域字段。）

## 退出保护、处置与清理

- [ ] **AC15：** 成功、失败和取消任务在工作区干净且没有未推送提交时，均自动删除 Worktree 和临时分支。（验证：运行 `python -m pytest tests/test_worktree_protection.py tests/test_worktree_manager.py tests/test_worktree_e2e.py -q -k "deleted or clean or cancel"`，期望 Git worktree、临时分支和 sidecar 均被清理。）
- [ ] **AC16：** 存在暂存、未暂存或非忽略未跟踪文件时退出拒绝删除，并报告目录、分支及未提交修改原因。（验证：运行 `python -m pytest tests/test_worktree_protection.py tests/test_worktree_manager.py -q -k "staged or unstaged or untracked or retained"`，期望 disposition 为 `RETAINED` 且原因包含中文未提交修改说明。）
- [ ] **AC17：** 存在 upstream 未包含的提交时退出拒绝删除；新分支无 upstream 且相对基线有新增提交时同样视为未推送。（验证：运行 `python -m pytest tests/test_worktree_protection.py tests/test_worktree_e2e.py -q -k "unpushed or upstream or base_commit"`，期望保留目录并报告未推送提交。）
- [ ] **AC18：** 新增提交已被 upstream 包含且工作区干净时允许自动删除，不要求自动合并、推送或创建远端分支。（验证：运行 `python -m pytest tests/test_worktree_protection.py tests/test_worktree_manager.py -q -k "upstream_contains or pushed or clean"`，期望只执行本地删除流程。）
- [ ] **AC19：** 启动时执行一次清理扫描，fake 时钟推进 1 小时后执行下一次；默认 7 天过期线和项目配置覆盖值均生效，未过期目录不会被删除。（验证：运行 `python -m pytest tests/test_worktree_cleaner.py tests/test_e2e_chat.py -q -k "startup or interval or expire"`，期望 fake scheduler 的扫描时间与配置一致。）
- [ ] **AC20：** 伪造元数据、越界链接、活动任务、未提交修改或未推送提交分别使后台清理在对应过滤层停止。（验证：运行 `python -m pytest tests/test_worktree_cleaner.py -q -k "metadata or boundary or active or protected"`，期望每个候选只记录有界诊断，不删除受保护目录。）
- [ ] **AC21：** 超过 64 个临时目录时首批只处理 64 个并在后续批次继续，普通聊天和活动子 Agent 仍可运行。（验证：运行 `python -m pytest tests/test_worktree_cleaner.py tests/test_e2e_chat.py tests/test_subagent_service.py -q -k "batch or has_more or foreground"`，期望 `CleanupBatchResult.has_more=True` 且前台请求未阻塞。）
- [ ] **AC22：** 并发创建、恢复、退出和清理同一路径时，只产生一个有效所有者、一个终态和至多一次删除。（验证：运行 `python -m pytest tests/test_worktree_manager.py tests/test_worktree_cleaner.py tests/test_worktree_e2e.py -q -k "concurrent or lock or single"`，期望删除 spy 调用数不超过 `1`。）

## 可观测性、CLI 与文档

- [ ] **AC23：** `Agent(action=list|get)`、`/tasks`、`/task <id>` 和后台通知展示隔离模式、绝对路径、临时分支、创建或恢复、初始化结果及删除或保留原因。（验证：运行 `python -m pytest tests/test_subagent_tool.py tests/test_subagent_notifications.py tests/test_slash_builtins.py tests/test_slash_snapshots.py tests/test_subagent_session_tui.py -q -k "task or worktree or workspace or disposition"`，期望字段顺序稳定且缺失值以中文“未知”显示。）
- [ ] CLI 按顺序装配共享 `WorkspaceContext`、仓库身份、Worktree 配置、忽略根、manager/coordinator、cleaner 和 Agent 运行时，启动失败时不启动 cleaner 或子 Agent。（验证：运行 `python -m pytest tests/test_e2e_chat.py tests/test_hook_session_cli.py tests/test_subagent_service.py -q -k "worktree or cleaner or startup"`，期望 fake 组件记录的装配与关闭顺序符合设计。）
- [ ] Worktree 示例配置可被真实加载器解析，README 记录角色声明、目录与分支、初始化、显式 cwd、保护、清理和不做范围，且不包含真实凭据。（验证：运行 `python -m pytest tests/test_docs.py -q -k "worktree or example or readme"` 后运行 `rg -n "sk-[A-Za-z0-9]" examples/mycode.worktree.yaml README.md`，期望测试通过且凭据扫描无匹配。）
- [ ] 系统生成的隔离提示、错误、保护原因和清理诊断均为中文，复杂安全判断只保留必要注释。（验证：运行 `python -m pytest tests/test_subagent_docs.py tests/test_subagent_context.py tests/test_worktree_models.py tests/test_worktree_cleaner.py -q -k "chinese or diagnostic or reason"`，并审查 `git diff --check` 输出为零错误。）

## 失败关闭、隐私与兼容性

- [ ] **AC24：** 路径、仓库身份、元数据、Git 状态或变更保护无法确认时，创建、恢复和删除均失败关闭；错误与日志不包含配置正文、环境变量值、凭据或无界 Git 输出。（验证：运行 `python -m pytest tests/test_worktree_pathing.py tests/test_worktree_git.py tests/test_worktree_metadata.py tests/test_worktree_protection.py tests/test_worktree_cleaner.py -q -k "fail_closed or redacted or bounded"`，期望诊断包含 code/phase/path 摘要且敏感哨兵字符串不存在。）
- [ ] **AC25：** 临时仓库测试覆盖 Windows、Linux 和 macOS 路径语义，平台不支持符号链接时初始化明确失败；测试不访问真实远端、真实用户配置或工作区外文件。（验证：运行 `python -m pytest tests/test_worktree_pathing.py tests/test_worktree_initializer.py tests/test_worktree_git.py -q -k "windows or posix or symlink or isolated_git"`，期望平台差异只体现在显式 skip 或稳定失败分支。）
- [ ] **AC26：** 未声明隔离的角色、Fork、普通聊天、权限、Hook、Skill、Memory、MCP、上下文管理、会话恢复和现有工具 schema 保持兼容。（验证：运行 `python -m pytest tests/test_agent_loop.py tests/test_session.py tests/test_subagent_e2e.py tests/test_permission_e2e.py tests/test_hook_agent.py tests/test_skill_e2e.py tests/test_mcp_tools.py tests/test_project_memory_e2e.py tests/test_context_compaction_e2e.py tests/test_tool_registry.py -q`，期望零失败。）
- [ ] 本阶段不新增自动合并、推送、远端创建、手动 Worktree 管理命令、强制清理入口、Fork 隔离或多 Agent 编排能力。（验证：运行 `python -m pytest tests/test_docs.py tests/test_slash_builtins.py tests/test_subagent_loader.py -q -k "out_of_scope or worktree"`，并审查 CLI help、README 和 slash 命令列表，期望没有新增手动 Worktree 命令或强制删除入口。）

## 编译与测试

- [ ] Worktree 领域单元测试全部通过。（验证：运行 `python -m pytest tests/test_workspace_models.py tests/test_worktree_models.py tests/test_worktree_pathing.py tests/test_worktree_config.py tests/test_worktree_git.py tests/test_worktree_metadata.py tests/test_worktree_initializer.py tests/test_worktree_protection.py tests/test_worktree_manager.py tests/test_worktree_cleaner.py -q`，期望零失败。）
- [ ] 子 Agent Worktree 接入与回归测试全部通过。（验证：运行 `python -m pytest tests/test_subagent_isolation.py tests/test_subagent_models.py tests/test_subagent_loader.py tests/test_subagent_tasks.py tests/test_subagent_service.py tests/test_subagent_runtime.py tests/test_subagent_tooling.py tests/test_subagent_context.py tests/test_subagent_tool.py tests/test_subagent_agent.py tests/test_subagent_e2e.py tests/test_subagent_session_tui.py -q`，期望零失败。）
- [ ] 工具、Hook、Skill、MCP、Memory、Permission、Slash、AgentLoop 和 CLI 接入回归通过。（验证：运行 `python -m pytest tests/test_agent_loop.py tests/test_tool_executor.py tests/test_tool_filesystem.py tests/test_tool_command.py tests/test_tool_registry.py tests/test_hook_runtime.py tests/test_hook_actions.py tests/test_hook_agent.py tests/test_skill_executor.py tests/test_skill_agent.py tests/test_skill_e2e.py tests/test_mcp_tools.py tests/test_memory_paths.py tests/test_memory_instructions.py tests/test_permission_e2e.py tests/test_slash_builtins.py tests/test_slash_snapshots.py tests/test_e2e_chat.py -q`，期望零失败。）
- [ ] Python 源码和测试可完整编译，无语法错误。（验证：运行 `python -m compileall -q src tests`，期望退出码 `0`。）
- [ ] 仓库当前全部自动化测试通过。（验证：运行 `python -m pytest -q`，记录测试总数、通过数和退出码 `0`。）
- [ ] 最终差异无空白错误、冲突标记或意外修改用户已有文件。（验证：运行 `git diff --check` 和 `git status --short`，期望前者退出码 `0`，后者只列 Stage 13 相关文件及进入本阶段前已存在的用户变更。）

## 端到端场景

- [ ] **AC27：提交后保留。** 主 Agent 启动一个 `isolation: worktree` 定义式角色，系统创建并初始化 Worktree；子 Agent 在其中修改并提交文件；主工作区不受影响，任务结束后因提交未推送而保留目录并报告保护原因。（验证：运行 `python -m pytest tests/test_worktree_e2e.py tests/test_subagent_e2e.py tests/test_permission_e2e.py -q -k "retained or unpushed or isolated"`，期望主目录、另一个 Worktree 和缓存均不变，任务详情报告同一绝对路径与分支。）
- [ ] **AC28：无变更删除与恢复。** 再次运行一个未产生变更的隔离任务，系统完成运行后自动删除其 Worktree 和临时分支，不影响前一个受保护目录；READY 目录可在重启后只读恢复。（验证：运行 `python -m pytest tests/test_worktree_e2e.py tests/test_subagent_e2e.py -q -k "deleted or recovered or no_changes"`，期望无变更任务被清理，受保护目录仍存在，快速恢复 spy 无 Git/写入调用。）
- [ ] 并发隔离端到端场景保持确定性。（验证：运行 `python -m pytest tests/test_worktree_e2e.py tests/test_subagent_e2e.py -q -k "concurrent or parallel"`，期望主 Agent 与两个隔离子 Agent 的目录、分支、缓存和 cwd 互相独立，退出与后台清理对同一路径至多删除一次。）

## 验收覆盖矩阵

| Spec 验收标准 | 对应检查区域 |
|---|---|
| AC1 | 角色声明与任务身份 |
| AC2 | 角色声明与任务身份；路径、配置与 Git 安全 |
| AC3 | 路径、配置与 Git 安全 |
| AC4 | 路径、配置与 Git 安全；退出保护、处置与清理 |
| AC5 | 路径、配置与 Git 安全；端到端场景 |
| AC6 | 路径、配置与 Git 安全 |
| AC7 | 元数据、初始化与恢复 |
| AC8 | 元数据、初始化与恢复 |
| AC9 | 路径、配置与 Git 安全 |
| AC10 | 元数据、初始化与恢复 |
| AC11 | 元数据、初始化与恢复 |
| AC12 | 工作区上下文与工具接入 |
| AC13 | 工作区上下文与工具接入 |
| AC14 | 工作区上下文与工具接入 |
| AC15 | 退出保护、处置与清理；端到端场景 |
| AC16 | 退出保护、处置与清理 |
| AC17 | 退出保护、处置与清理；端到端场景 |
| AC18 | 退出保护、处置与清理 |
| AC19 | 退出保护、处置与清理 |
| AC20 | 退出保护、处置与清理 |
| AC21 | 退出保护、处置与清理 |
| AC22 | 退出保护、处置与清理；端到端场景 |
| AC23 | 可观测性、CLI 与文档 |
| AC24 | 失败关闭、隐私与兼容性 |
| AC25 | 失败关闭、隐私与兼容性 |
| AC26 | 失败关闭、隐私与兼容性；编译与测试 |
| AC27 | 端到端场景 |
| AC28 | 端到端场景 |

28 条 Spec 验收标准均至少对应一个可执行检查项；Worktree 创建、快速恢复、初始化、显式 cwd、缓存隔离、保护处置、后台清理、并发一致性、可观测性、兼容回归和端到端流程均有独立证据入口。
