# myCode Stage 13：子 Agent Worktree 隔离验收清单

> 所有条目都在实现完成后执行。先运行验证命令、记录实际输出，再勾选结果；不得用代码阅读或主观判断代替可观察证据。

## 验收环境

- [ ] Worktree 自动化测试只使用临时 Git 仓库、临时用户目录、受控文件、fake 时钟和 fake 调度，不访问真实远端、真实用户配置、真实凭据或工作区外文件。（验证：清空模型供应商 API key 和用户级 Git 配置后运行 `python -m pytest tests/test_worktree_*.py tests/test_subagent_e2e.py -q`，期望全部通过，夹具记录的仓库、home 和远端均位于 `tmp_path`。）
- [ ] Git 集成测试只配置临时仓库本地身份，并以本地 bare 仓库模拟 upstream。（验证：运行 `python -m pytest tests/test_worktree_git.py tests/test_worktree_protection.py tests/test_worktree_e2e.py -q`，期望零网络请求、零真实分支推送，所有 Git cwd 均指向测试临时目录。）
- [ ] 测试不依赖切换进程当前目录或真实时间长等待。（验证：运行 `python -m pytest tests/test_worktree_manager.py tests/test_worktree_cleaner.py tests/test_worktree_e2e.py -q -k "cwd or interval or concurrent"`，期望 `Path.cwd`/`os.chdir` 防线、fake 时钟和可控同步原语相关用例全部通过。）
- [ ] 本阶段开始前已有工作区变更未被恢复、覆盖或纳入 Stage 13 提交。（验证：运行 `git status --short` 并与开发前基线对比，期望用户原有文件保持原状态，新增或修改项仅包含 Stage 13 明确列出的文件。）

## 角色、命名与目录边界

- [ ] **AC1：** 声明 `isolation: worktree` 的定义式角色进入隔离目录；未声明的定义式角色和 Fork 继续使用原工作区；未知 isolation 值返回可定位中文诊断且不启动角色。（验证：运行 `python -m pytest tests/test_subagent_models.py tests/test_subagent_loader.py tests/test_subagent_isolation.py -q -k "isolation or worktree or fork or shared"`，期望三类角色的工作区选择和非法值结果分别符合要求。）
- [ ] **AC2：** 同一角色的两个任务得到不同的 `.worktrees/<角色>/<任务标识>` 目录和 `mycode/worktree/<角色>/<任务标识>` 临时分支，重复输入产生确定结果且所有名称满足受控规则。（验证：运行 `python -m pytest tests/test_worktree_pathing.py tests/test_subagent_models.py tests/test_subagent_tasks.py -q -k "identity or token or deterministic or distinct"`，期望目录、分支唯一且均位于受控前缀下。）
- [ ] **AC3：** 空段、`.`、`..`、绝对路径、反斜杠、盘符、控制字符、平台保留名、非法首尾字符、超过 64 字符的段和超过 200 字符的全名全部被拒绝，仓库外哨兵文件保持不变。（验证：运行 `python -m pytest tests/test_worktree_pathing.py -q -k "invalid or traversal or reserved or length or outside"`，期望所有恶意样例稳定失败且外部哨兵内容未改变。）
- [ ] **AC4：** `.worktrees/` 未被 Git 忽略、越出仓库，被替换为指向仓库外的符号链接/目录联接，或在边界检查后被置换时，启动校验、创建、恢复和清理均失败关闭且不自动修改忽略规则。（验证：运行 `python -m pytest tests/test_worktree_pathing.py tests/test_worktree_git.py tests/test_worktree_manager.py tests/test_worktree_cleaner.py -q -k "ignored_root or symlink_escape or junction or boundary or swap"`，期望所有入口拒绝操作，外部哨兵和 `.gitignore` 不变。）

## 创建、恢复与初始化

- [ ] **AC5：** 主工作区存在未提交修改时，隔离任务仍从启动时捕获的已提交 `HEAD` 创建，子 Worktree 看不到主工作区未提交内容。（验证：运行 `python -m pytest tests/test_worktree_manager.py tests/test_worktree_e2e.py -q -k "base_commit or parent_dirty"`，期望子目录内容等于基线提交，主目录脏状态保持不变。）
- [ ] **AC6：** 首次创建后，Git 可观察到独立 Worktree 和独立临时分支；主工作区分支、索引、文件内容和有效 Git 配置均未改变。（验证：运行 `python -m pytest tests/test_worktree_git.py tests/test_worktree_manager.py -q -k "create or add_worktree or parent_unchanged"`，期望创建前后的主工作区快照逐项相等，新增条目只属于目标任务。）
- [ ] **AC7：** 合法 `READY` 目录可快速恢复；恢复期间没有 Git 调用、文件写入、初始化动作或仓库遍历。（验证：运行 `python -m pytest tests/test_worktree_metadata.py tests/test_worktree_manager.py tests/test_worktree_e2e.py -q -k "ready or recover or read_only"`，期望安装为“一调用即失败”的 Git、写入和遍历 spy 调用次数均为 `0`，返回状态为 recovered。）
- [ ] **AC8：** 元数据缺失或超限、阶段非 `READY`、仓库/任务/路径/分支/基线/配置摘要不符时恢复失败，既有目录和 sidecar 的字节内容、时间戳与结构均不变。（验证：运行 `python -m pytest tests/test_worktree_metadata.py tests/test_worktree_manager.py -q -k "recover and (missing or mismatch or incomplete or oversized)"`，期望每种异常返回稳定诊断且恢复前后目录快照一致。）
- [ ] **AC9：** 合法 `.mycode/worktree.yaml` 可加载；未知字段或规则、重复或祖先/后代目标、越界来源、循环链接、非法时间和超过 128 条规则或 512 字符路径均在应用启动阶段失败。（验证：运行 `python -m pytest tests/test_worktree_config.py tests/test_e2e_chat.py -q -k "worktree and (valid or invalid or duplicate or cycle or limit or startup)"`，期望合法配置规范化结果稳定，非法配置不启动清理器或子 Agent。）
- [ ] **AC10：** 首次创建按声明顺序完成 `copy`、`ignored_copy`、`symlink` 和 `hooks`；子目录获得所需文件和依赖链接，Git 命令使用子 Worktree hooks，主工作区 hooks 配置与行为不变。（验证：运行 `python -m pytest tests/test_worktree_initializer.py tests/test_worktree_git.py tests/test_tool_command.py -q -k "copy or ignored_copy or symlink or hooks"`，期望四类结果和执行顺序可观察，主工作区 hooks 快照一致。）
- [ ] **AC10（平台失败分支）：** 平台不能创建声明的符号链接时初始化明确失败，不静默改为复制大型依赖目录。（验证：运行 `python -m pytest tests/test_worktree_initializer.py -q -k "symlink and (unsupported or failure)"`，期望返回 symlink 步骤诊断，目标既不是副本也不是伪成功状态。）
- [ ] **AC11：** 首次创建后的必需初始化失败会阻止子 Agent，并仅在保护检查确认安全时回滚本次新建资源；恢复验证失败始终保留既有目录且不写入。（验证：运行 `python -m pytest tests/test_worktree_manager.py tests/test_subagent_isolation.py -q -k "initialization_failure or rollback or recovery_failure"`，期望新建和恢复两条失败路径的目录、分支及启动计数符合各自规则。）

## 显式工作区与上下文隔离

- [ ] **AC12-a：** 主 Agent 和两个隔离子 Agent 并发运行时进程 cwd 始终不变；文件与命令工具、权限边界、Hook 和 Skill 只观察到各自显式传入的绝对工作目录。（验证：运行 `python -m pytest tests/test_agent_loop.py tests/test_tool_filesystem.py tests/test_tool_command.py tests/test_permission_e2e.py tests/test_hook_agent.py tests/test_skill_e2e.py tests/test_worktree_e2e.py -q -k "workspace or cwd or isolated or concurrent"`，期望 cwd 哨兵恒定且每个记录器只出现所属根目录。）
- [ ] **AC12-b：** 不能接收或验证工作区上下文的工具在 Worktree 模式中不可见或调用失败，不回退到主目录；MCP 与远程搜索默认仅在共享模式可见。（验证：运行 `python -m pytest tests/test_tool_executor.py tests/test_tool_registry.py tests/test_mcp_tools.py tests/test_subagent_tooling.py -q -k "workspace or shared_only or mcp or fail_closed"`，期望隔离 registry 不含不兼容工具，共享 registry schema 和行为保持不变。）
- [ ] **AC13：** 两个 Worktree 中相同相对路径的文件缓存、System Prompt、项目指令、项目 Memory 和 Memory 路径使用不同绝对身份，一方修改不改变另一方结果且无需清空全局缓存。（验证：运行 `python -m pytest tests/test_tool_cache.py tests/test_memory_paths.py tests/test_memory_instructions.py tests/test_project_memory_e2e.py tests/test_subagent_context.py -q -k "workspace or absolute or isolation or project"`，期望两套 key、内容、索引和 Prompt 相互独立。）
- [ ] **AC14：** 隔离子 Agent 的首个请求包含正确绝对路径、临时分支、任务身份和中文隔离约束，并重新加载目标 Worktree 内的项目指令。（验证：运行 `python -m pytest tests/test_subagent_context.py tests/test_subagent_runtime.py -q -k "worktree or isolation_prompt or project_instruction"`，期望 fake LLM 捕获的首个请求只引用目标目录，不含主工作区版本的项目指令。）

## 退出保护与自动处置

- [ ] **AC15：** 成功、失败和取消三种终态在工作区干净且没有未推送提交时均自动删除 Worktree、临时分支和受控 sidecar，并只释放一次运行资源。（验证：运行 `python -m pytest tests/test_worktree_manager.py tests/test_subagent_runtime.py tests/test_worktree_e2e.py -q -k "clean and (completed or failed or cancelled or deleted)"`，期望三种终态均为 deleted 且 Git 列表中不再存在对应资源。）
- [ ] **AC16：** 暂存、未暂存和非忽略未跟踪文件分别使退出拒绝删除，并向父 Agent 报出绝对目录、分支和“未提交修改”保护原因。（验证：运行 `python -m pytest tests/test_worktree_protection.py tests/test_worktree_manager.py tests/test_subagent_tool.py tests/test_subagent_notifications.py -q -k "staged or unstaged or untracked or retained"`，期望三种状态均保留且状态出口字段一致。）
- [ ] **AC17：** upstream 未包含的新提交使退出保留；无 upstream 的临时分支相对创建基线有新增提交时也判为未推送并保留。（验证：运行 `python -m pytest tests/test_worktree_git.py tests/test_worktree_protection.py tests/test_worktree_manager.py tests/test_worktree_e2e.py -q -k "unpushed or no_upstream or ahead or retained"`，期望目录和分支存在且原因明确为未推送提交。）
- [ ] **AC18：** 新增提交已被 upstream 包含且工作区干净时允许自动删除临时 Worktree 和分支，即使提交尚未合并到创建基线；系统不会自动合并、推送或修改 upstream。（验证：运行 `python -m pytest tests/test_worktree_git.py tests/test_worktree_protection.py tests/test_worktree_manager.py -q -k "upstream_contains or pushed_unmerged"`，期望处置为 deleted，fake Git 记录中没有 merge、push 或 upstream 写操作。）
- [ ] Git 状态、upstream 包含关系或删除阶段无法可靠确认时按受保护处理；Worktree 已删除但分支删除失败时报告部分清理且提交仍可恢复。（验证：运行 `python -m pytest tests/test_worktree_git.py tests/test_worktree_protection.py tests/test_worktree_manager.py -q -k "unknown or partial or delete_failure"`，期望失败关闭、诊断有界且不使用 force 删除。）

## 后台清理、并发与可观测性

- [ ] **AC19：** 应用启动后执行一次受控扫描，fake 时钟推进默认 1 小时才执行下一次；默认 7 天过期，项目配置可改变扫描间隔与过期线，未过期目录不删除。（验证：运行 `python -m pytest tests/test_worktree_cleaner.py tests/test_e2e_chat.py -q -k "startup or interval or expiry or configured"`，期望扫描次数和候选处置随 fake 时钟确定变化。）
- [ ] **AC20：** 伪造元数据或身份、越界真实路径/链接、活动任务、未提交修改和未推送提交分别在身份边界、过期与活动、变更保护三层过滤的对应位置停止清理。（验证：运行 `python -m pytest tests/test_worktree_cleaner.py -q -k "metadata or boundary or active or dirty or unpushed or filter"`，期望每个候选被跳过、外部哨兵不变且诊断指出确定层级。）
- [ ] **AC21：** 超过 64 个临时目录时单批最多处理 64 个，后续批次继续且顺序稳定；批次间让出事件循环，普通聊天和活动子 Agent 可继续推进。（验证：运行 `python -m pytest tests/test_worktree_cleaner.py tests/test_e2e_chat.py -q -k "batch or sixty_four or non_blocking"`，期望首批计数为 64、后续候选最终被检查，聊天哨兵在批次间运行。）
- [ ] **AC22：** 并发创建、恢复、退出和后台清理同一任务身份或真实路径时只有一个有效所有者、一个终态和至多一次删除，活动任务不会被误判为过期。（验证：运行 `python -m pytest tests/test_worktree_manager.py tests/test_worktree_cleaner.py tests/test_worktree_e2e.py -q -k "concurrent or same_identity or same_path or idempotent"`，期望创建、终态和删除计数分别不超过设计值。）
- [ ] **AC23：** 父 Agent 的任务 list/get、后台通知、`/tasks`、`/task <id>` 和 TUI 可观察一致的隔离模式、绝对路径、临时分支、created/recovered、初始化结果及 deleted/retained 原因；长路径和缺失终态保持可读。（验证：运行 `python -m pytest tests/test_subagent_tool.py tests/test_subagent_notifications.py tests/test_slash_builtins.py tests/test_slash_snapshots.py tests/test_subagent_session_tui.py -q -k "worktree or workspace or preparation or disposition"`，期望所有出口读取同一任务快照且未新增手动 Worktree 命令。）

## 安全、资源与兼容性

- [ ] **AC24-a：** 路径、仓库身份、元数据、Git 状态或保护状态无法确认时，创建、恢复和删除均失败关闭；Git 使用结构化参数与显式 cwd，超时和非零退出码产生稳定错误。（验证：运行 `python -m pytest tests/test_worktree_pathing.py tests/test_worktree_git.py tests/test_worktree_metadata.py tests/test_worktree_manager.py -q -k "fail_closed or timeout or nonzero or uncertain"`，期望危险动作调用数为 `0` 或停在安全阶段。）
- [ ] **AC24-b：** Git stdout/stderr 各最多保留 64 KiB、元数据最多 64 KiB、单条诊断最多 4 KiB；错误、日志、任务详情和通知不含配置正文、复制文件内容、环境变量值、凭据或完整 Git 输出。（验证：运行 `python -m pytest tests/test_worktree_git.py tests/test_worktree_metadata.py tests/test_worktree_manager.py tests/test_subagent_notifications.py -q -k "bounded or truncate or redact or secret"`，期望长度上限、截断标记和敏感哨兵缺失断言全部通过。）
- [ ] **AC25：** 参数化测试覆盖 Windows、Linux 和 macOS 的分隔符、大小写、保留名、符号链接/目录联接语义；所有仓库、home、远端和哨兵均受临时目录控制。（验证：运行 `python -m pytest tests/test_worktree_pathing.py tests/test_worktree_initializer.py tests/test_worktree_git.py tests/test_worktree_e2e.py -q -k "windows or linux or macos or platform or temporary"`，期望各平台语义分支通过，不访问真实用户环境。）
- [ ] **AC26-a：** 普通聊天、共享定义式角色和 Fork 的工作区、工具 schema、权限、Hook、Skill、Memory、MCP、上下文管理和会话行为保持 Stage 12 兼容。（验证：运行 `python -m pytest tests/test_e2e_chat.py tests/test_subagent_e2e.py tests/test_agent_loop.py tests/test_permission_e2e.py tests/test_hook_agent.py tests/test_skill_e2e.py tests/test_project_memory_e2e.py tests/test_mcp_tools.py tests/test_context_compaction_e2e.py tests/test_session.py -q`，期望零失败。）
- [ ] **AC26-b：** Worktree 隔离不会修改主工作区文件、分支、索引、Git 配置或 hooks，也不会新增自动合并、推送、强制删除和手动管理命令。（验证：运行 `python -m pytest tests/test_worktree_git.py tests/test_worktree_initializer.py tests/test_slash_builtins.py tests/test_docs.py -q -k "parent_unchanged or hooks or no_manual_command or out_of_scope"`，期望主工作区快照一致且所有越界入口均不存在。）
- [ ] 系统生成的隔离提示、启动错误、保护原因和清理诊断均为中文，新增 schema 字段带简洁中文说明；外部角色正文和项目文件内容保持原文。（验证：运行 `python -m pytest tests/test_worktree_models.py tests/test_worktree_pathing.py tests/test_worktree_config.py tests/test_subagent_models.py tests/test_subagent_context.py tests/test_subagent_notifications.py tests/test_slash_builtins.py -q -k "chinese or diagnostic or description or prompt"`，期望系统文本与字段说明为中文，外部内容逐字不变。）
- [ ] 项目示例可由真实配置加载器解析，覆盖 Git 超时、清理间隔、过期时间、批量上限和四类初始化规则，且示例和 README 不含字面凭据。（验证：运行 `python -m pytest tests/test_docs.py -q -k "worktree"` 和 `rg -n "sk-[A-Za-z0-9]" examples/mycode.worktree.yaml README.md`，期望测试通过且凭据扫描无匹配。）

## 实现完整性与集成

- [ ] 工作区身份、路径与配置、Git 边界、元数据、初始化、保护、生命周期和后台清理均可通过公开领域入口组合运行，不要求调用方自行拼装 Git 生命周期逻辑。（验证：运行 `python -m pytest tests/test_workspace_models.py tests/test_worktree_models.py tests/test_worktree_pathing.py tests/test_worktree_config.py tests/test_worktree_git.py tests/test_worktree_metadata.py tests/test_worktree_initializer.py tests/test_worktree_protection.py tests/test_worktree_manager.py tests/test_worktree_cleaner.py -q`，期望全部通过。）
- [ ] 隔离协调器在创建 runner 前准备并绑定工作区，成功、失败和取消都通过统一终态路径释放；准备失败的任务不进入运行态。（验证：运行 `python -m pytest tests/test_subagent_isolation.py tests/test_subagent_service.py tests/test_subagent_runtime.py tests/test_subagent_tasks.py -q -k "prepare or reserved or release or disposition"`，期望 runner、release 和终态计数符合两阶段生命周期。）
- [ ] CLI 先校验仓库、配置和忽略根再构造运行时，启动清理器并在最外层关闭；启动失败时不启动清理器或子 Agent。（验证：运行 `python -m pytest tests/test_e2e_chat.py tests/test_hook_session_cli.py tests/test_subagent_service.py -q -k "worktree or cleaner or startup or close"`，期望装配顺序、关闭次数和失败短路均可观察。）
- [ ] 文件、命令、权限、Hook、Skill、Prompt、Memory 和 MCP 的工作区接入共同通过集成回归。（验证：运行 `python -m pytest tests/test_tool_executor.py tests/test_tool_filesystem.py tests/test_tool_command.py tests/test_permission_e2e.py tests/test_hook_actions.py tests/test_hook_runtime.py tests/test_skill_executor.py tests/test_skill_agent.py tests/test_mcp_tools.py tests/test_subagent_tooling.py tests/test_subagent_context.py tests/test_project_memory_e2e.py -q`，期望零失败且隔离记录器只出现所属绝对根。）

## 编译与测试

- [ ] Worktree 领域单元与集成测试全部通过。（验证：运行 `python -m pytest tests/test_workspace_models.py tests/test_worktree_models.py tests/test_worktree_pathing.py tests/test_worktree_config.py tests/test_worktree_git.py tests/test_worktree_metadata.py tests/test_worktree_initializer.py tests/test_worktree_protection.py tests/test_worktree_manager.py tests/test_worktree_cleaner.py -q`，期望零失败。）
- [ ] 子 Agent、运行时路径传播和状态展示测试全部通过。（验证：运行 `python -m pytest tests/test_subagent_models.py tests/test_subagent_loader.py tests/test_subagent_isolation.py tests/test_subagent_tasks.py tests/test_subagent_service.py tests/test_subagent_runtime.py tests/test_subagent_context.py tests/test_subagent_tooling.py tests/test_subagent_tool.py tests/test_subagent_notifications.py tests/test_slash_builtins.py tests/test_subagent_session_tui.py -q`，期望零失败。）
- [ ] Stage 13 端到端测试全部通过。（验证：运行 `python -m pytest tests/test_worktree_e2e.py tests/test_subagent_e2e.py tests/test_permission_e2e.py -q -k "worktree or isolated or retained or unpushed or concurrent"`，期望零失败且不访问网络或真实远端。）
- [ ] 仓库当前全部自动化测试通过。（验证：运行 `python -m pytest -q`，记录测试总数、通过数、跳过数和退出码 `0`。）
- [ ] Python 源码和测试可完整编译，无语法错误。（验证：运行 `python -m compileall -q src tests`，期望退出码 `0`。）
- [ ] 生产运行时没有 `os.chdir`，除 CLI 首次捕获主工作区外不读取进程 cwd。（验证：运行 `rg -n "os\.chdir|Path\.cwd\(" src/mycode/cli.py src/mycode/worktree src/mycode/subagent src/mycode/agent src/mycode/tool src/mycode/hook src/mycode/skill`，期望无 `os.chdir`；`Path.cwd()` 只允许出现在 CLI 启动边界，不出现在 AgentLoop 或并发运行路径。）
- [ ] 最终差异无空白错误、冲突标记或意外修改用户已有文件。（验证：运行 `git diff --check` 和 `git status --short`，期望前者退出码 `0`，后者只列 Stage 13 文件及进入本阶段前已记录的用户变更。）

## 端到端场景

- [ ] **AC27 / 场景 1：未推送提交受保护保留。** 主工作区带未提交修改时启动 `isolation: worktree` 角色，系统从已提交基线创建并初始化 Worktree；子 Agent 通过真实文件、命令、权限、Hook 和 Skill 路径修改并提交文件，主工作区及另一 Worktree 不变；终态因提交未推送而保留，并在父 Agent、通知和 `/task` 中报告相同绝对路径、分支和保护原因。（验证：运行 `python -m pytest tests/test_worktree_e2e.py tests/test_subagent_e2e.py tests/test_permission_e2e.py -q -k "retained or unpushed or isolated"`，期望场景通过且不访问真实远端。）
- [ ] **AC28 / 场景 2：无变更任务自动删除。** 在场景 1 的受保护目录仍存在时运行一个不产生变更的隔离任务；成功结束后只删除新任务的 Worktree、临时分支和 sidecar，前一个受保护目录及主工作区保持不变。（验证：运行 `python -m pytest tests/test_worktree_e2e.py tests/test_subagent_e2e.py -q -k "clean_delete or protected_sibling or no_change"`，期望新任务处置为 deleted，前一任务仍为 retained。）
- [ ] **场景 3：只读快速恢复后正常运行。** 模拟 `READY` 后进程重建，以相同任务身份恢复目录并运行子 Agent；恢复阶段 Git、写入和初始化 spy 均未调用，运行阶段使用恢复出的绝对工作区。（验证：运行 `python -m pytest tests/test_worktree_e2e.py tests/test_subagent_e2e.py -q -k "recover or read_only"`，期望 preparation 为 recovered 且任务完成。）
- [ ] **场景 4：主 Agent 与两个隔离任务并发。** 三方同时运行并修改相同相对路径，进程 cwd 恒定，三个目录、分支、Prompt、项目指令、Memory 和缓存身份相互独立；退出与清理竞争时活动任务不删除，终态后至多删除一次。（验证：运行 `python -m pytest tests/test_worktree_e2e.py tests/test_subagent_e2e.py -q -k "concurrent or parallel"`，期望隔离、所有权和幂等计数全部通过。）

## 验收覆盖矩阵

| Spec 验收标准 | 对应检查区域 |
|---|---|
| AC1 | 角色、命名与目录边界 AC1 |
| AC2 | 角色、命名与目录边界 AC2 |
| AC3 | 角色、命名与目录边界 AC3 |
| AC4 | 角色、命名与目录边界 AC4 |
| AC5 | 创建、恢复与初始化 AC5；端到端场景 1 |
| AC6 | 创建、恢复与初始化 AC6 |
| AC7 | 创建、恢复与初始化 AC7；端到端场景 3 |
| AC8 | 创建、恢复与初始化 AC8 |
| AC9 | 创建、恢复与初始化 AC9 |
| AC10 | 创建、恢复与初始化 AC10 及平台失败分支 |
| AC11 | 创建、恢复与初始化 AC11 |
| AC12 | 显式工作区与上下文隔离 AC12-a 至 AC12-b；端到端场景 4 |
| AC13 | 显式工作区与上下文隔离 AC13；端到端场景 4 |
| AC14 | 显式工作区与上下文隔离 AC14 |
| AC15 | 退出保护与自动处置 AC15；端到端场景 2 |
| AC16 | 退出保护与自动处置 AC16 |
| AC17 | 退出保护与自动处置 AC17；端到端场景 1 |
| AC18 | 退出保护与自动处置 AC18 |
| AC19 | 后台清理、并发与可观测性 AC19 |
| AC20 | 后台清理、并发与可观测性 AC20 |
| AC21 | 后台清理、并发与可观测性 AC21 |
| AC22 | 后台清理、并发与可观测性 AC22；端到端场景 4 |
| AC23 | 后台清理、并发与可观测性 AC23；端到端场景 1 |
| AC24 | 安全、资源与兼容性 AC24-a 至 AC24-b |
| AC25 | 验收环境；安全、资源与兼容性 AC25 |
| AC26 | 安全、资源与兼容性 AC26-a 至 AC26-b；编译与测试全量回归 |
| AC27 | 端到端场景 1 |
| AC28 | 端到端场景 2 |

28 条 Spec 验收标准均至少对应一个可执行检查项；角色声明、路径防护、创建与只读恢复、四类初始化、显式 cwd、上下文隔离、变更保护、三层清理、并发幂等、可观测性、兼容回归，以及“未推送保留”和“无变更删除”端到端闭环均有独立证据入口。
