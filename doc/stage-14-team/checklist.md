# Stage 14 Team Checklist

> 每一项都必须通过运行代码、测试或观察持久化结果来验证。勾选前记录实际结果；未通过项必须修复并重新验证。所有验证使用临时用户目录、临时 Git 仓库和 fake 依赖，不连接真实远端。

## 实现完整性

- [ ] **C1（AC1，F1-F2）小组持久化与 Lead 租约**：创建两个不同名称的小组，观察 `~/.mycode/teams/<name>/` 互不重叠；读取快照确认 repository identity 和固定 target branch；第二个 owner 在租约有效时收到稳定冲突错误，租约明确释放或过期后可接管。（验证：`python -m pytest tests/test_team_storage.py tests/test_team_service.py -q -k "create or attach or lease"`）
- [ ] **C2（AC2，F3）成员花名册与角色快照**：创建成员并重新加载小组，观察角色名、role revision、稳定 worktree、分支、请求/实际后端、审批要求、邮箱、context 路径和状态均存在；修改角色文件后恢复被拒绝且不会静默使用新版本。（验证：`python -m pytest tests/test_team_models.py tests/test_team_service.py tests/test_team_runtime.py -q -k "role or member or recovery"`）
- [ ] **C3（AC3，F4）后端选择与失败收敛**：fake 能力探测按 `tmux → terminal → in_process` 选择并返回原因；显式请求不可用后端直接失败；窗格创建或唤醒端点失败后成员不是 `running`。（验证：`python -m pytest tests/test_team_backends.py -q`）
- [ ] **C4（AC4，F5）成员状态隔离**：启动两个成员，分别写入不同消息、权限、缓存、用量和 worktree 状态；观察其中一个成员进入 `blocked` 或 `idle` 时 Lead 和另一个成员状态不改变。（验证：`python -m pytest tests/test_team_runtime.py tests/test_team_service.py -q -k isolation`）
- [ ] **C5（AC5，F5）idle 通知与磁盘恢复**：成员自然完成任务后状态变为 `idle` 并向 Lead 投递一次 `status_update`；Lead 再发送消息，观察成员从 context checkpoint 恢复原消息顺序并继续指派。（验证：`python -m pytest tests/test_team_runtime.py tests/test_team_mailbox.py -q -k "idle or resume or wake"`）
- [ ] **C6（AC6，F6）入口和角色工具可见性**：未激活小组时只出现稳定 Team 管理入口；激活后的下一安全点出现 Lead 工具；普通子 Agent schema 不含 Team 工具；成员调用递归 `Agent` 或派生动作得到结构化拒绝。（验证：`python -m pytest tests/test_team_tool.py tests/test_agent_loop.py tests/test_subagent_tooling.py -q -k "visibility or recursive or parent"`）
- [ ] **C7（AC7，F7）批次和 DAG 任务板**：为两个用户目标分别创建批次，确认任务增删查改、负责人和状态持久化；悬空依赖、自依赖、循环依赖被拒绝，前置任务未完成时领取失败。（验证：`python -m pytest tests/test_team_tasks.py -q -k "dag or crud or dependency"`）
- [ ] **C8（AC8，F7）原子领取和 Lead 管理**：并发领取同一可执行任务，观察只有一个 owner 成功；Lead 可分配、改派、取消任务并修改批次状态，成员只能领取无负责人且 revision 匹配的任务。（验证：`python -m pytest tests/test_team_tasks.py -q -k "claim or concurrent or reassign"`）
- [ ] **C9（AC9，F7）交付结果约束**：代码任务缺提交 ID、验证摘要或干净 worktree 时完成转换被拒绝；只读任务提交结构化结果和验证摘要后可完成。（验证：`python -m pytest tests/test_team_tasks.py -q -k "delivery or completed or read_only"`）
- [ ] **C10（AC10，F8-F9）邮箱落盘与协议**：`message`、`broadcast`、`plan_submit`、`plan_decision`、`status_update`、`shutdown_request` 和 `shutdown_response` 均成功落盘；每条记录自动有 UTC 时间戳、默认未读和有界摘要；并发邮箱写按配置重试，旧锁可回收。（验证：`python -m pytest tests/test_team_mailbox.py tests/test_team_locking.py -q`）
- [ ] **C11（AC11，F9）确认时序和消息幂等**：模拟消息处理在 checkpoint 前崩溃，观察消息仍未读并可重投；checkpoint 与上下文注入成功后才 ack；重复读取稳定 message ID 不会重复应用。（验证：`python -m pytest tests/test_team_mailbox.py tests/test_team_runtime.py -q -k "ack or crash or dedup or applied"`）
- [ ] **C12（AC12，F10）计划审批隔离**：需要审批的成员可以读取工作区信息并提交 `plan_submit`，批准前文件写、提交和 shell 写均被拒绝；只有匹配 task ID 与 plan revision 的 `plan_decision=approve` 才进入 `running`，驳回带原因并等待新 revision。（验证：`python -m pytest tests/test_team_runtime.py tests/test_team_tasks.py tests/test_team_tool.py -q -k "approval or plan"`）
- [ ] **C13（AC13，F11）Lead 自动编排**：输入一个用户目标，观察 Lead 创建批次、写入带依赖任务、派生成员、转发审批、跟踪状态并收尾；正常路径不要求用户逐项确认。（验证：`python -m pytest tests/test_team_service.py tests/test_team_tool.py tests/test_team_e2e.py -q -k "orchestration or batch"`）
- [ ] **C14（AC14，F12）脏目标拒绝与干净临时合并**：目标 worktree 有未提交修改时集成立即失败，且 `stash`、覆盖和 Lead 文件复制均未发生；目标干净时合并只发生在临时本地集成分支。（验证：`python -m pytest tests/test_team_integration.py -q -k "dirty or preflight or temporary"`）
- [ ] **C15（AC15，F12）冲突任务和目标回滚**：可解决冲突被转成成员代码任务，成员提交后可以继续集成；无法解决时临时合并被 abort，目标分支 ref 保持原值，成员 worktree/branch/context 和诊断仍存在。（验证：`python -m pytest tests/test_team_integration.py -q -k "conflict or rollback"`）
- [ ] **C16（AC16，F13）coordinator 双锁与工具集合**：分别关闭配置开关、未设置环境变量、无活动 Team，只要任一条件缺失就不进入 coordinator；三者满足后文件写工具和普通 `Agent` 消失，而任务/成员编排、消息、审批、查询、读工具和受限 shell 保留。（验证：`python -m pytest tests/test_team_config.py tests/test_team_tool.py tests/test_permission_policy.py -q -k coordinator`）
- [ ] **C17（AC17，F13/F15）不可覆盖的远端写禁令**：coordinator、成员和 fake pane 分别通过文件工具、窗格输入、PowerShell/cmd 包装和 shell 尝试 `git push`、`git -C repo push`、`remote add/set-url`；每次都返回 `FORBIDDEN` 或稳定远端写拒绝诊断，配置/审批不能放宽。（验证：`python -m pytest tests/test_permission_command.py tests/test_permission_policy.py tests/test_team_tool.py tests/test_team_integration.py -q -k "push or remote or forbidden"`）
- [ ] **C18（AC18，F14）优雅终止与保留现场**：Lead 终止成员时先收到 `shutdown_request`，成员保存 checkpoint 并返回 `shutdown_response`；超过配置超时才 force；任务、邮箱、上下文、branch 和 worktree 仍可读取。（验证：`python -m pytest tests/test_team_backends.py tests/test_team_service.py tests/test_team_runtime.py -q -k "shutdown or stop"`）
- [ ] **C19（AC19，F14）可恢复归档**：运行任务或未处理成果存在时 archive 被拒绝；清空前置条件后归档成功，目录和历史邮箱变为只读，新的任务、消息和成员派生均被拒绝。（验证：`python -m pytest tests/test_team_service.py tests/test_team_storage.py tests/test_team_mailbox.py -q -k archive`）
- [ ] **C20（AC20，F2/F5）会话清理和重启接管**：执行 `/clear`、Lead 进程退出和应用重启，观察小组、任务、邮箱、成员和 worktree 未删除；外部成员继续运行，协程成员从 checkpoint 恢复；租约释放/过期后新 Lead 可接管。（验证：`python -m pytest tests/test_session.py tests/test_hook_session_cli.py tests/test_team_e2e.py -q -k "clear or restart or takeover"`）
- [ ] **C21（AC21，N4）并发确定性**：并发操作同一租约、任务、邮箱和批次合并，观察无重复负责人、重复终态、重复通知、双重 ack 或双重删除；所有失败均返回 revision/锁诊断。（验证：`python -m pytest tests/test_team_locking.py tests/test_team_tasks.py tests/test_team_mailbox.py tests/test_team_service.py tests/test_team_integration.py -q -k concurrent`）
- [ ] **C22（AC22，N7）资源上限**：达到成员、active 成员、消息正文/摘要、上下文和锁重试上限时操作返回有界稳定错误；重复操作不会无限增长文件、内存或诊断输出。（验证：`python -m pytest tests/test_team_config.py tests/test_team_storage.py tests/test_team_mailbox.py tests/test_team_backends.py tests/test_team_runtime.py -q -k "limit or bound or max"`）
- [ ] **C23（AC23，N8）跨平台语义**：fake Windows、Linux、macOS capability 环境产生不同后端选择原因，但任务、消息、审批、恢复和合并结果结构一致；显式不可用后端均直接失败而不降级。（验证：`python -m pytest tests/test_team_backends.py tests/test_team_runtime.py tests/test_team_integration.py -q -k "platform or capability"`）
- [ ] **C24（AC24，N10-N12）可观测性、隐私和中文字段**：查询接口能返回小组、成员、批次、任务、消息、后端、唤醒、审批和合并状态及稳定 ID/时间/诊断；检查持久化和日志不含凭据、环境变量、完整对话或无界 Git 输出；新增模型字段有简洁中文注释。（验证：`python -m pytest tests/test_team_models.py tests/test_team_storage.py tests/test_team_mailbox.py tests/test_team_backends.py tests/test_team_service.py tests/test_team_integration.py -q -k "status or diagnostic or privacy"`；并运行 `rg -n "password|api[_-]?key|token=|MYCODE_COORDINATOR=.*[01]|git output" src/mycode/team tests` 复核输出过滤测试覆盖。）
- [ ] **C25（AC25，N13-N14）未激活回归和测试隔离**：不激活 Team 时现有 Agent、Session、Slash、TUI、Permission、Hook、Skill、MCP、Memory、Worktree 回归测试通过；团队测试只创建临时资源并使用 fake LLM、mailbox、lock、pane adapter。（验证：`python -m pytest tests/test_agent_loop.py tests/test_session.py tests/test_config.py tests/test_permission_command.py tests/test_permission_policy.py tests/test_subagent_e2e.py tests/test_worktree_e2e.py -q`，并审阅 `tests/test_team_*.py` 的 fixture 作用域。）

## 集成检查

- [ ] **I1：Team 包边界**：所有团队状态、存储、锁、任务、邮箱、后端、运行时、策略、工具、服务和集成都可从 `src/mycode/team/` 追踪；现有模块只有 plan 中列出的接入点。（验证：`rg --files src/mycode/team` 与 `rg -n "from mycode\.team|import mycode\.team" src/mycode`，人工核对调用链无无关 Team 逻辑。）
- [ ] **I2：公开入口和动态 schema**：CLI 总能发现 parent-only Team 主入口；成功 create/attach 后下一 AgentLoop 轮次才切换工具集合；工具 schema 可见性与执行前 policy 一致。（验证：`python -m pytest tests/test_team_tool.py tests/test_agent_loop.py tests/test_hook_session_cli.py -q -k "visibility or assembly or entry"`）
- [ ] **I3：任务、邮箱、运行时闭环**：成员领取任务、提交计划、收到审批、运行、checkpoint、idle 通知和消息唤醒恢复能通过稳定 ID 串成一条链，且 ack 在 checkpoint 之后发生。（验证：`python -m pytest tests/test_team_tasks.py tests/test_team_mailbox.py tests/test_team_runtime.py tests/test_team_service.py -q -k "workflow or checkpoint or approval"`）
- [ ] **I4：本地 Git 集成边界**：IntegrationService 只通过结构化 Git gateway 操作临时本地分支/worktree，目标脏时拒绝，冲突失败时目标 unchanged，成功后才更新目标 ref；全链路不存在 push。（验证：`python -m pytest tests/test_team_integration.py tests/test_worktree_git.py tests/test_permission_command.py -q`）
- [ ] **I5：生命周期边界**：CLI finally、Session `/clear`、close、worker、shutdown 和 archive 之间不互相删除持久化数据；外部进程与协程成员分别按规定停止/恢复。（验证：`python -m pytest tests/test_session.py tests/test_hook_session_cli.py tests/test_team_backends.py tests/test_team_service.py tests/test_team_e2e.py -q -k "lifecycle or clear or worker or archive"`）
- [ ] **I6：硬权限优先级**：FORBIDDEN 在所有审批、配置和角色 policy 之前执行；coordinator 不能用 shell、pane input、旧 schema 或隐藏参数绕过文件写和远端写限制。（验证：`python -m pytest tests/test_permission_command.py tests/test_permission_policy.py tests/test_team_tool.py tests/test_agent_loop.py -q -k "forbidden or bypass or coordinator"`）

## 文档、编译与测试

- [ ] **V1：配置和文档**：示例配置可被现有配置加载器读取，README 说明默认上限、auto 顺序、双锁、消息协议、恢复、归档、批次合并和不可 push 规则。（验证：`python -m pytest tests/test_docs.py tests/test_team_config.py -q`）
- [ ] **V2：Team 专项测试**：所有新增团队单元、集成和端到端测试通过，失败项已修复并重新运行。（验证：`python -m pytest tests/test_team_*.py -q`）
- [ ] **V3：跨领域回归**：AgentLoop、Session、Config、Permission、SubAgent 和 Worktree 现有测试通过。（验证：`python -m pytest tests/test_agent_loop.py tests/test_session.py tests/test_config.py tests/test_permission_command.py tests/test_permission_policy.py tests/test_subagent_e2e.py tests/test_worktree_e2e.py -q`）
- [ ] **V4：全量测试**：项目所有测试通过，退出码为 0。（验证：`python -m pytest tests -q`）
- [ ] **V5：静态检查和差异范围**：源码可编译、补丁无空白错误、工作区只有预期 Stage 14 文件和用户已有修改。（验证：`python -m compileall src`、`git diff --check`、`git status --short`）
- [ ] **V6：远端写静态复核**：代码和测试中所有 push/远端写路径都指向拒绝逻辑，未出现允许 push 的配置项、提示词或测试 fake 例外。（验证：`rg -n "git push|remote add|remote set-url|push_allowed|allow_push" src tests examples README.md`；命中项逐一确认只用于拒绝或文档说明。）

## 端到端场景

- [ ] **E1：成功团队流程（AC26）**：Lead 创建绑定本地仓库的 Team 和批次，拆分两个带依赖的代码任务并派生两名成员；成员分别通过计划审批、本地提交和验证，其中一名进入 idle 后由 Lead 消息唤醒恢复；批次按依赖拓扑在临时本地分支完成原子合并，目标分支更新，成员分支同步，远端没有任何写操作。（验证：`python -m pytest tests/test_team_e2e.py -q -k success`；记录批次 ID、任务 ID、commit ID、目标 head 前后值和 fake backend 调用记录。）
- [ ] **E2：脏目标失败（AC14、AC27）**：在 Lead worktree 预先写入未提交修改后触发集成，观察稳定拒绝；目标文件、目标 head、成员分支、任务、邮箱和诊断保持原样，无 stash/reset/覆盖。（验证：`python -m pytest tests/test_team_e2e.py tests/test_team_integration.py -q -k dirty`）
- [ ] **E3：角色版本或恢复失败（AC2、AC20、AC27）**：修改角色 revision 或损坏 context checkpoint 后重启 worker，观察进入 `recovery_required`/`blocked`，不加载新角色、不覆盖旧 context、不重复执行任务；Lead 可查询并明确恢复、改派或取消。（验证：`python -m pytest tests/test_team_e2e.py tests/test_team_runtime.py -q -k "recovery or revision"`）
- [ ] **E4：锁占用与崩溃重投（AC10、AC11、AC21、AC27）**：让邮箱锁长期占用并模拟成员在 checkpoint 前崩溃，观察有限重试后返回有界错误或重投；锁过期可回收，稳定消息 ID 只应用一次，其他邮箱和租约不受影响。（验证：`python -m pytest tests/test_team_e2e.py tests/test_team_locking.py tests/test_team_mailbox.py -q -k "lock or crash or retry"`）
- [ ] **E5：冲突无法解决（AC15、AC27）**：构造成员提交冲突并让 conflict task 失败，观察临时集成被 abort、目标分支保持原样、成员 worktree/branch/context/诊断保留，批次进入可查询失败状态。（验证：`python -m pytest tests/test_team_e2e.py tests/test_team_integration.py -q -k "conflict or unresolved"`）
- [ ] **E6：coordinator 安全流程（AC16、AC17）**：同时打开配置 capability、`MYCODE_COORDINATOR=1` 和活动 Team，确认 Lead 只能编排、查询、读文件、发消息、审批、合并和受限 shell；尝试文件写、普通 Agent、pane 输入 push 和 shell 远端写均被拒绝。（验证：`python -m pytest tests/test_team_e2e.py tests/test_team_tool.py tests/test_permission_command.py tests/test_permission_policy.py -q -k coordinator`）

## 通过门槛

- [ ] AC1–AC27 全部勾选并记录实际证据。
- [ ] I1–I6 全部通过，且没有发现 schema 可见性与执行权限不一致。
- [ ] V1–V6 全部通过；全量测试、编译和差异检查结果已记录。
- [ ] E1–E6 全部通过；失败场景均保留现场且未覆盖用户内容。
- [ ] `git status --short` 核对过范围；没有执行 `git push`，也没有产生远端分支或远端配置写入。
