# Stage 14 Team Tasks

## 前置约束

- 本文件只拆解已批准的 `spec.md` 和 `plan.md`，四份阶段文档全部批准前禁止编写实现代码。
- 所有 Team 逻辑放在 `src/mycode/team/`；现有领域只增加明确接入点。
- 每个任务按“先写失败测试 → 确认失败 → 最小实现 → 通过验证 → 本地提交”执行。
- 允许 `git commit` 作为本地检查点；任何成员、Lead、worker、shell、Git gateway 和测试流程均禁止 `git push`、远端分支创建、远端配置写入及其他远端写操作，且该禁止不可由配置或审批覆盖。
- 测试只能使用临时用户目录、临时 Git 仓库、fake LLM、fake mailbox、fake lock 和 fake backend，不访问真实远端或工作区外路径。

## 文件清单

### 新建

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `src/mycode/team/__init__.py` | 导出公开 Team 类型、服务和工具 |
| 新建 | `src/mycode/team/models.py` | 状态枚举、不可变数据类和稳定错误 |
| 新建 | `src/mycode/team/config.py` | TeamConfig 解析及 coordinator 环境双锁判断 |
| 新建 | `src/mycode/team/locking.py` | 跨进程锁文件、重试和过期锁回收 |
| 新建 | `src/mycode/team/storage.py` | 小组目录、JSON/JSONL 原子持久化 |
| 新建 | `src/mycode/team/context.py` | 文件型 ConversationMemory 和上下文检查点 |
| 新建 | `src/mycode/team/tasks.py` | 持久化 DAG 任务板和状态转换 |
| 新建 | `src/mycode/team/mailbox.py` | 名称注册表、邮箱、消息协议和确认 |
| 新建 | `src/mycode/team/backends.py` | auto 选择、tmux、Windows Terminal、协程后端 |
| 新建 | `src/mycode/team/runtime.py` | 成员 AgentLoop、checkpoint、恢复和 idle |
| 新建 | `src/mycode/team/integration.py` | 临时本地集成分支、合并和冲突报告 |
| 新建 | `src/mycode/team/service.py` | 小组、成员、批次、租约生命周期 |
| 新建 | `src/mycode/team/policy.py` | 工具可见性、coordinator 和成员执行限制 |
| 新建 | `src/mycode/team/tool.py` | Team 主入口、Lead 工具和成员工具 |
| 新建 | `src/mycode/team/worker.py` | 外部窗格 worker 进程入口 |
| 新建 | `tests/test_team_models.py` | 模型状态和字段约束 |
| 新建 | `tests/test_team_config.py` | TeamConfig、环境变量和双锁 |
| 新建 | `tests/test_team_locking.py` | 锁竞争、重试和过期回收 |
| 新建 | `tests/test_team_storage.py` | 目录布局、原子写和损坏恢复 |
| 新建 | `tests/test_team_tasks.py` | DAG、CAS 领取、审批和状态转换 |
| 新建 | `tests/test_team_mailbox.py` | 邮箱协议、广播、幂等和已读 |
| 新建 | `tests/test_team_backends.py` | 后端探测、参数安全和生命周期 |
| 新建 | `tests/test_team_runtime.py` | 成员循环、checkpoint、审批和恢复 |
| 新建 | `tests/test_team_service.py` | 租约、成员、批次、归档 |
| 新建 | `tests/test_team_integration.py` | 本地合并、冲突、回滚和 push 禁止 |
| 新建 | `tests/test_team_tool.py` | schema、动作和角色视图 |
| 新建 | `tests/test_team_e2e.py` | 完整团队工作流和失败场景 |
| 新建 | `examples/mycode.team.yaml` | Team 配置和 coordinator 双锁示例 |

### 修改

| 操作 | 文件 | 改动 |
|---|---|---|
| 修改 | `src/mycode/config.py` | 解析 `team` 配置并挂载 `LLMConfig.team` |
| 修改 | `src/mycode/cli.py` | 装配 TeamService、TeamTool、worker 参数和退出释放 |
| 修改 | `src/mycode/agent/loop.py` | 每轮计算动态工具可见性 |
| 修改 | `src/mycode/session.py` | `/clear`/close 释放 Lead 租约并保存成员检查点 |
| 修改 | `src/mycode/permission/command.py` | 系统级拒绝 `git push` 和远端写命令 |
| 修改 | `src/mycode/permission/policy.py` | 保证 FORBIDDEN 优先于规则和审批 |
| 修改 | `src/mycode/worktree/service.py` | 稳定成员 worktree 准备和 Git gateway 访问 |
| 修改 | `src/mycode/worktree/git.py` | 临时分支、合并、abort 和本地 ref 更新 |
| 修改 | `src/mycode/subagent/tooling.py` | 普通子 Agent 全局排除 Team parent-only 工具 |
| 修改 | `tests/test_config.py` | Team 配置向后兼容和错误校验 |
| 修改 | `tests/test_agent_loop.py` | 动态 schema、隐藏调用拒绝 |
| 修改 | `tests/test_session.py` | `/clear` 和 close 的长期团队语义 |
| 修改 | `tests/test_hook_session_cli.py` | CLI/Session 生命周期回归 |
| 修改 | `tests/test_permission_command.py` | push 和远端写命令拒绝 |
| 修改 | `tests/test_permission_policy.py` | 系统级 FORBIDDEN 不可覆盖 |
| 修改 | `tests/test_worktree_service.py` | 成员 worktree 准备和复用 |
| 修改 | `tests/test_worktree_git.py` | 结构化本地 Git gateway |
| 修改 | `tests/test_subagent_tooling.py` | 普通子 Agent 不可见 Team 工具 |
| 修改 | `tests/test_docs.py` | Team 示例和 README 断言 |
| 修改 | `README.md` | Team 配置、协作、恢复、合并和硬性 push 禁令 |

## T1：建立 Team 模型契约

**文件：** `src/mycode/team/models.py`、`src/mycode/team/__init__.py`、`tests/test_team_models.py`

**依赖：** 无

**步骤：**
1. 为 `TeamState`、`MemberState`、`MemberBackend`、`ResolvedBackend`、`TaskKind`、`TeamTaskState`、`BatchState`、`ApprovalState` 和 `MessageProtocol` 写状态组合、非法值和终态回退失败测试。
2. 运行 `python -m pytest tests/test_team_models.py -q`，期望因模块和类型不存在而失败。
3. 实现 `TeamRecord`、`MemberRecord`、`BatchRecord`、`TeamTask`、`TeamMessage`、`TeamSnapshot`、`TaskPatch`、`TaskResult`、`MemberSpec`、`MemberLaunchSpec`、`BackendEnvironment`、`BackendSelection`、`BackendHandle`、`LeadLease`、`WakeEndpoint`、`DeliveryReceipt`、`IntegrationReport` 及 `TeamError`；构造时校验非空 ID、绝对路径、非负 revision、可解析时间戳和合法状态组合，并为新增字段添加简洁中文注释。
4. 运行同一命令，期望模型、枚举和错误测试全部通过；确认 `team.__all__` 只导出稳定公开类型。
5. 执行 `git add src/mycode/team/models.py src/mycode/team/__init__.py tests/test_team_models.py && git commit -m "feat: define persistent team models"`，不得执行 push。

**验证：** `python -m pytest tests/test_team_models.py -q` 退出码为 0。

## T2：解析 TeamConfig 和 coordinator 双锁

**文件：** `src/mycode/team/config.py`、`src/mycode/config.py`、`tests/test_team_config.py`、`tests/test_config.py`

**依赖：** T1

**步骤：**
1. 测试缺少 `team` 时安全默认、`max_members=16`、`max_active_members=4`、锁/邮箱/上下文上限、后端枚举、布尔值和正数关系；测试 `coordinator_capability_enabled` 与 `MYCODE_COORDINATOR=1` 必须同时满足。
2. 运行 `python -m pytest tests/test_team_config.py tests/test_config.py -q`，期望新测试失败且已有配置测试不被改动。
3. 实现不可变 `TeamConfig`、`parse_team_config(raw)`、`coordinator_enabled_from_env()`，严格拒绝未知后端、非正数、超过系统上限和错误布尔值；将 `team: TeamConfig` 加入 `LLMConfig` 并接入现有 `load_config`。
4. 运行同一命令，期望默认、错误和双锁测试通过。
5. 执行 `git add src/mycode/team/config.py src/mycode/config.py tests/test_team_config.py tests/test_config.py && git commit -m "feat: add team configuration gates"`，不得执行 push。

**验证：** `python -m pytest tests/test_team_config.py tests/test_config.py -q` 通过，且 `MYCODE_COORDINATOR=0`、空值、`false` 和未设置都返回关闭。

## T3：实现文件锁

**文件：** `src/mycode/team/locking.py`、`tests/test_team_locking.py`

**依赖：** T1、T2

**步骤：**
1. 用 `tmp_path` 写两个 owner 的竞争、有限重试、超时、旧锁回收、非持有者释放失败和锁内容字段测试。
2. 运行 `python -m pytest tests/test_team_locking.py -q`，期望未实现错误。
3. 实现 `FileLease.acquire(path, config, owner)` 和 `release()`：使用 `Path.open("x")` 原子建锁，记录 owner、创建时间和进程标识；按配置间隔重试，超过 stale 时间仅在 owner 不活动时回收，`finally` 只删除自身 token。
4. 运行同一命令，期望竞争、过期和释放测试全部通过。
5. 执行 `git add src/mycode/team/locking.py tests/test_team_locking.py && git commit -m "feat: add cross-process team locks"`，不得执行 push。

**验证：** `python -m pytest tests/test_team_locking.py -q` 通过，测试中无无限等待。

## T4：实现 TeamStore 目录和原子 JSON 持久化

**文件：** `src/mycode/team/storage.py`、`tests/test_team_storage.py`

**依赖：** T1、T3

**步骤：**
1. 测试 `~/.mycode/teams/<team-name>/team.json`、`members/<name>/member.json`、`batches/<id>/batch.json`、`batches/<id>/tasks/<id>.json`、`registry.json`、mailbox 和 context 路径；覆盖原子替换、越界路径、损坏 JSON 和归档只读。
2. 运行 `python -m pytest tests/test_team_storage.py -q`，期望未实现错误。
3. 实现 `TeamStore.create/load/save/archive`、成员/批次/任务读写和 registry 读写；所有名称先校验安全目录名，所有路径 `resolve()` 后确认仍在 teams 根目录；JSON 写入同目录临时文件后替换。
4. 运行同一命令，期望目录、原子写、恢复和归档测试通过。
5. 执行 `git add src/mycode/team/storage.py tests/test_team_storage.py && git commit -m "feat: persist team files atomically"`，不得执行 push。

**验证：** `python -m pytest tests/test_team_storage.py -q` 通过，临时文件不会残留为半条 JSON。

## T5：实现成员上下文文件存储

**文件：** `src/mycode/team/context.py`、`tests/test_team_runtime.py`

**依赖：** T1、T4

**步骤：**
1. 为 `JsonConversationMemory` 写 `ChatMessage` role/content/tool-call/origin 的 append、replace、clear、reload、版本字段、损坏文件和上限测试，测试使用 `-k context` 选择。
2. 运行 `python -m pytest tests/test_team_runtime.py -q -k context`，期望文件型 memory 未实现而失败。
3. 实现现有 `ConversationMemory` 抽象的文件适配器；只保存恢复所需消息、schema version、已应用 mailbox ID 和 checkpoint 元数据，不保存密钥、完整对话诊断或无界 Git 输出；写入 UTF-8 临时文件后替换。
4. 运行同一命令，期望顺序、截断/拒绝和损坏文件测试通过。
5. 执行 `git add src/mycode/team/context.py tests/test_team_runtime.py && git commit -m "feat: store resumable member context"`，不得执行 push。

**验证：** `python -m pytest tests/test_team_runtime.py -q -k context` 通过，重启后消息顺序和 schema version 保持不变。

## T6：建立任务 DAG 验证器

**文件：** `src/mycode/team/tasks.py`、`tests/test_team_tasks.py`

**依赖：** T1、T4

**步骤：**
1. 测试任务依赖不存在、自依赖、环依赖、跨批次依赖、有效拓扑和前置完成判断。
2. 运行 `python -m pytest tests/test_team_tasks.py -q`，期望 `TaskBoard` 未实现而失败。
3. 实现 `TaskBoard` 的 Kahn 拓扑检查和 ready 判定；将依赖字段规范化为唯一 ID 元组，拒绝悬空、自己依赖和环。
4. 运行同一命令，期望 DAG 和 ready 判定测试通过。
5. 执行 `git add src/mycode/team/tasks.py tests/test_team_tasks.py && git commit -m "feat: validate team task DAGs"`，不得执行 push。

**验证：** `python -m pytest tests/test_team_tasks.py -q` 通过，任一非法依赖在写入前被拒绝。

## T7：实现任务板 CRUD、CAS 和并发领取

**文件：** `src/mycode/team/tasks.py`、`tests/test_team_tasks.py`

**依赖：** T3、T4、T6

**步骤：**
1. 测试 `create/update/delete/get/list/claim` 的 revision CAS、单 owner、未完成前置阻止领取、两个并发 claimant 只有一个成功，以及未开始且无后继任务才可删除。
2. 运行 `python -m pytest tests/test_team_tasks.py -q`，期望 CRUD 和 claim 新测试失败。
3. 在批次锁内实现 `TaskBoard.create/update/delete/claim/get/list`；每次成功更新递增 revision，claim 只接受 `pending`、无负责人、依赖全完成且 revision 匹配的任务。
4. 运行同一命令，期望 CRUD、CAS 和并发测试通过。
5. 执行 `git add src/mycode/team/tasks.py tests/test_team_tasks.py && git commit -m "feat: add atomic task board claims"`，不得执行 push。

**验证：** `python -m pytest tests/test_team_tasks.py -q` 通过，重复领取返回稳定冲突错误且不会改 owner。

## T8：实现任务审批、交付验证和阻塞恢复

**文件：** `src/mycode/team/tasks.py`、`tests/test_team_tasks.py`

**依赖：** T7

**步骤：**
1. 测试 `awaiting_approval → running` 仅接受匹配 plan revision 的批准；驳回必须有原因且递增 revision；代码任务缺提交 ID、验证摘要或脏 worktree 不能完成；只读任务必须有结构化结果；成员异常只能进入 `blocked`。
2. 运行 `python -m pytest tests/test_team_tasks.py -q`，期望状态转换和交付约束测试失败。
3. 实现 `transition(task_id, expected_revision, state, result, error)` 与 plan approval 校验；拒绝回退、重复终态、未批准运行和无明确恢复动作的 blocked 自动重试。
4. 运行同一命令，期望审批、交付、终态和恢复测试通过。
5. 执行 `git add src/mycode/team/tasks.py tests/test_team_tasks.py && git commit -m "feat: enforce task approvals and delivery"`，不得执行 push。

**验证：** `python -m pytest tests/test_team_tasks.py -q` 通过，所有非法状态转换产生稳定诊断。

## T9：实现消息和名称注册表模型

**文件：** `src/mycode/team/mailbox.py`、`tests/test_team_mailbox.py`

**依赖：** T1、T4

**步骤：**
1. 测试 `message/broadcast/plan_submit/plan_decision/status_update/shutdown_request/shutdown_response` 的必需字段、稳定 ID、摘要/正文大小上限、自动 UTC 时间戳和默认未读。
2. 运行 `python -m pytest tests/test_team_mailbox.py -q`，期望协议和注册表模块未实现而失败。
3. 实现成员名称到邮箱、上下文、worktree、backend 和 wake endpoint 的 registry；实现 `TeamMessage` 创建时的字段校验和摘要截断规则。
4. 运行同一命令，期望协议字段和注册表测试通过。
5. 执行 `git add src/mycode/team/mailbox.py tests/test_team_mailbox.py && git commit -m "feat: define team mailbox protocols"`，不得执行 push。

**验证：** `python -m pytest tests/test_team_mailbox.py -q` 通过，持久化消息自动补时间戳且 `read=false`。

## T10：实现邮箱追加、广播、读取和幂等确认

**文件：** `src/mycode/team/mailbox.py`、`src/mycode/team/locking.py`、`tests/test_team_mailbox.py`

**依赖：** T3、T4、T9

**步骤：**
1. 测试名称缺失、点对点先查 registry、广播排除发件人、邮箱锁竞争/重试/旧锁回收、JSONL 顺序、ack 后已读、崩溃重投和稳定 ID 去重。
2. 运行 `python -m pytest tests/test_team_mailbox.py -q`，期望并发和确认测试失败。
3. 实现 `MailboxStore.register/send/receive/unread/acknowledge`；目标邮箱锁内追加完整 JSON 行，广播按当前成员分别写入，读取按文件顺序返回未读消息。
4. 在 checkpoint 成功后更新已读索引和已应用 ID 集合；重复接收不重复注入上下文，写入始终是完整行且不产生半条消息。
5. 运行同一命令，期望邮箱协议、锁、顺序、重投和去重测试通过。
6. 执行 `git add src/mycode/team/mailbox.py src/mycode/team/locking.py tests/test_team_mailbox.py && git commit -m "feat: deliver team mailbox messages safely"`，不得执行 push。

**验证：** `python -m pytest tests/test_team_mailbox.py -q` 通过，并证明消息落盘成功不等于目标已处理。

## T11：实现后端环境探测和自动选择

**文件：** `src/mycode/team/backends.py`、`tests/test_team_backends.py`

**依赖：** T1、T2

**步骤：**
1. 用 fake capability runner 测试 `auto` 严格按 `tmux → terminal → in_process` 选择并记录原因；测试显式不可用后端直接失败。
2. 运行 `python -m pytest tests/test_team_backends.py -q`，期望选择器未实现而失败。
3. 实现 `BackendEnvironment`、`BackendSelection`、`BackendHandle` 和 `BackendSelector`；探测只返回 available/unavailable reason，不静默改变请求后端。
4. 运行同一命令，期望自动选择、显式失败和原因记录测试通过。
5. 执行 `git add src/mycode/team/backends.py tests/test_team_backends.py && git commit -m "feat: select team member backends"`，不得执行 push。

**验证：** `python -m pytest tests/test_team_backends.py -q` 通过；不可用终端不会留下 `running` 成员。

## T12：实现 tmux、Windows Terminal 和 in-process 后端

**文件：** `src/mycode/team/backends.py`、`tests/test_team_backends.py`

**依赖：** T11

**步骤：**
1. 用 fake subprocess runner 测试 spawn/wake/stop 的参数数组、显式 cwd、目标成员 endpoint、无 shell 拼接、窗格创建失败和 wake endpoint 缺失。
2. 运行 `python -m pytest tests/test_team_backends.py -q`，期望生命周期测试失败。
3. 实现 `TmuxBackend`、`WindowsTerminalBackend`；使用 `asyncio.create_subprocess_exec` 参数数组启动 worker，正文不进入命令行，wake 只发送成员标识。
4. 实现 `InProcessBackend`；用 asyncio task 运行 `TeamMemberRuntime.run_until_idle`，用事件唤醒，停止先 graceful 后 force。
5. 运行同一命令，期望参数安全、唤醒、优雅停止和超时强制停止测试通过。
6. 执行 `git add src/mycode/team/backends.py tests/test_team_backends.py && git commit -m "feat: add isolated and coroutine backends"`，不得执行 push。

**验证：** `python -m pytest tests/test_team_backends.py -q` 通过，fake runner 观察不到 shell 字符串拼接或正文泄漏。

## T13：实现成员运行时基础和 checkpoint

**文件：** `src/mycode/team/runtime.py`、`tests/test_team_runtime.py`

**依赖：** T5、T8、T10、T12

**步骤：**
1. 用 fake LLM 测试成员加载固定 role revision、独立 worktree cwd、AgentEvent 安全点 checkpoint、邮箱已应用 ID 一起保存和自然完成后进入 `idle`。
2. 运行 `python -m pytest tests/test_team_runtime.py -q -k "runtime or checkpoint or idle"`，期望 runtime 未实现而失败。
3. 实现 `TeamMemberRuntime`；加载 `JsonConversationMemory`，按成员角色/任务生成 AgentLoop，注册成员工具，逐事件保存模型轮次、工具结果和邮箱应用集合。
4. 在无可执行任务或自然完成时写入 `idle` 并发送 `status_update`；异常保留 worktree、branch、context 并写 `blocked` 或 `failed` 诊断。
5. 运行同一命令，期望循环、checkpoint、idle 和失败保留测试通过。
6. 执行 `git add src/mycode/team/runtime.py tests/test_team_runtime.py && git commit -m "feat: run team members with checkpoints"`，不得执行 push。

**验证：** `python -m pytest tests/test_team_runtime.py -q -k "runtime or checkpoint or idle"` 通过，恢复文件只包含有界恢复数据。

## T14：实现成员审批、邮箱唤醒和恢复校验

**文件：** `src/mycode/team/runtime.py`、`src/mycode/subagent/tooling.py`、`tests/test_team_runtime.py`、`tests/test_subagent_tooling.py`

**依赖：** T10、T13

**步骤：**
1. 测试需要审批成员在 `plan_submit` 前只能读文件、不能写文件/提交/shell；匹配 `plan_decision` 批准后才能运行；驳回必须停在待审批；邮箱唤醒从磁盘恢复；普通子 Agent 的 `*` 白名单仍排除 Team 工具。
2. 运行 `python -m pytest tests/test_team_runtime.py tests/test_subagent_tooling.py -q -k team`，期望审批、恢复和隔离测试失败。
3. 在 runtime 中实现 plan submit/decision 协议和 revision 校验；成员状态仅在匹配批准后转 `running`，驳回写原因并等待新 revision。
4. 在启动前校验 team/member/task/role revision、绝对 worktree、repository id 和 context schema；失败写 `recovery_required`/`blocked`，不重建或覆盖已有目录。
5. 将所有 Team parent-only 工具加入普通子 Agent 全局排除集，并验证运行时工具执行也拒绝递归派生。
6. 运行 `python -m pytest tests/test_team_runtime.py tests/test_subagent_tooling.py -q`，期望审批、恢复、普通 Agent 回归全部通过。
7. 执行 `git add src/mycode/team/runtime.py src/mycode/subagent/tooling.py tests/test_team_runtime.py tests/test_subagent_tooling.py && git commit -m "feat: isolate and resume team members"`，不得执行 push。

**验证：** `python -m pytest tests/test_team_runtime.py tests/test_subagent_tooling.py -q` 通过；审批前任何写入口都返回结构化拒绝。

## T15：增加稳定成员 Worktree 身份

**文件：** `src/mycode/worktree/service.py`、`tests/test_worktree_service.py`

**依赖：** T1

**步骤：**
1. 测试 `team/<team>/<member>` 受控相对路径、稳定分支、同成员恢复复用、不同成员隔离、并发准备单 owner、路径越界拒绝和无进程级 `chdir`。
2. 运行 `python -m pytest tests/test_worktree_service.py -q -k team`，期望长期成员入口不存在而失败。
3. 增加 `member_identity` 和 `prepare_member`；复用现有 path policy、metadata store、初始化和保护检查，返回长期 worktree/branch 身份；lease 保留到归档或显式清理。
4. 运行同一命令，期望成员路径、分支、复用和并发测试通过。
5. 执行 `git add src/mycode/worktree/service.py tests/test_worktree_service.py && git commit -m "feat: provision stable team worktrees"`，不得执行 push。

**验证：** `python -m pytest tests/test_worktree_service.py -q -k team` 通过，成员目录未被单任务完成自动删除。

## T16：增加结构化本地 Git gateway

**文件：** `src/mycode/worktree/git.py`、`tests/test_worktree_git.py`

**依赖：** T15

**步骤：**
1. 用 fake runner 测试创建临时分支/worktree、status/head、merge、abort merge、本地 ref 更新和临时对象删除；验证所有参数数组、cwd、超时和有界输出。
2. 运行 `python -m pytest tests/test_worktree_git.py -q -k team`，期望 gateway 新方法不存在而失败。
3. 增加结构化 Git gateway 方法，禁止 `chdir`、stash、reset、远端写操作和无界 stdout/stderr；每个方法返回可诊断的稳定结果。
4. 运行同一命令，期望 gateway 参数和失败清理测试通过。
5. 执行 `git add src/mycode/worktree/git.py tests/test_worktree_git.py && git commit -m "feat: add structured local git gateway"`，不得执行 push。

**验证：** `python -m pytest tests/test_worktree_git.py -q -k team` 通过，fake runner 记录中不存在 `push` 或远端配置写命令。

## T17：实现 TeamService 小组和 Lead 租约生命周期

**文件：** `src/mycode/team/service.py`、`tests/test_team_service.py`

**依赖：** T2、T4、T15

**步骤：**
1. 测试 create/attach 的仓库身份、固定目标分支、名称安全、单 Lead 租约竞争/释放/过期接管、`/clear` 后可接管、成员和小组归档只读。
2. 运行 `python -m pytest tests/test_team_service.py -q -k "team or lease or archive"`，期望 TeamService 未实现而失败。
3. 实现 `TeamService.create/attach/acquire_lead/release_lead/archive/status`，按 `team → member/task → mailbox` 固定锁顺序更新；租约失效前阻止第二 Lead，明确释放或确认过期后才允许接管。
4. 实现 `clear_session/close` 的租约释放和持久化检查点入口，但不删除 TeamStore、邮箱、成员或 worktree。
5. 运行同一命令，期望租约、仓库绑定、接管和归档前置测试通过。
6. 执行 `git add src/mycode/team/service.py tests/test_team_service.py && git commit -m "feat: manage team and lead leases"`，不得执行 push。

**验证：** `python -m pytest tests/test_team_service.py -q -k "team or lease or archive"` 通过；同时活跃 Team 只有一个有效 Lead。

## T18：实现成员派生、终止和批次编排

**文件：** `src/mycode/team/service.py`、`src/mycode/team/runtime.py`、`tests/test_team_service.py`

**依赖：** T11、T12、T13、T14、T16、T17

**步骤：**
1. 测试成员总数 16、active 4 上限、role revision 固定、worktree 保留、spawn 后状态、shutdown_request/response、超时 force、start_batch、ready 任务检查和 blocked 通知。
2. 运行 `python -m pytest tests/test_team_service.py -q -k "member or batch or shutdown"`，期望派生和批次测试失败。
3. 实现成员派生：解析并冻结角色 revision，准备长期 worktree，注册 mailbox/wake endpoint，调用 BackendSelector 启动；超过上限返回稳定资源错误。
4. 实现成员终止：先发送结构化 `shutdown_request`，等待 checkpoint 和 `shutdown_response`，超时才 force，保留成果和状态。
5. 实现批次创建、任务写入、ready 检查、plan/status 消息转发和恢复失败置 `blocked`。
6. 运行同一命令，期望生命周期、资源上限和批次测试通过。
7. 执行 `git add src/mycode/team/service.py src/mycode/team/runtime.py tests/test_team_service.py && git commit -m "feat: orchestrate team members and batches"`，不得执行 push。

**验证：** `python -m pytest tests/test_team_service.py -q -k "member or batch or shutdown"` 通过，终止和异常都不删除成员目录。

## T19：建立 Team 工具策略和动态角色视图

**文件：** `src/mycode/team/policy.py`、`tests/test_team_tool.py`

**依赖：** T1、T17

**步骤：**
1. 测试 parent、lead、member、coordinator 四种可见名称集合，parent-only 工具、普通子 Agent 排除集、coordinator 写工具移除和隐藏调用拒绝。
2. 运行 `python -m pytest tests/test_team_tool.py -q -k policy`，期望策略未实现而失败。
3. 实现 `TeamToolPolicy`：分别计算 parent/lead/member/coordinator schema，执行前检查调用者身份、Team 状态、成员名、任务 owner 和 coordinator 双锁；返回稳定中文结构化拒绝。
4. 运行同一命令，期望策略和角色隔离测试通过。
5. 执行 `git add src/mycode/team/policy.py tests/test_team_tool.py && git commit -m "feat: enforce team tool role policy"`，不得执行 push。

**验证：** `python -m pytest tests/test_team_tool.py -q -k policy` 通过，隐藏 schema 和旧 schema 直接调用均被拒绝。

## T20：实现 Team 主入口和 Lead 工具

**文件：** `src/mycode/team/tool.py`、`tests/test_team_tool.py`

**依赖：** T8、T10、T18、T19

**步骤：**
1. 测试稳定主入口 schema 的 `create/attach/status/archive`，以及 Lead 的 member spawn/stop、task CRUD/claim/update、message/broadcast、plan_decision、integrate 动作；测试未知字段、错误 revision 和无效成员名。
2. 运行 `python -m pytest tests/test_team_tool.py -q -k lead`，期望 TeamTool 未实现而失败。
3. 实现 `TeamTool` 和固定 action schema；创建/接管成功后只更新 capability 状态，下一模型轮次再展示 Lead 工具；结果仅返回有界摘要、ID、状态和诊断。
4. 按 task revision、plan revision 和 member name 做 CAS，调用 TeamService、TaskBoard、MailboxStore 和 IntegrationService，不让成员调用 Lead-only action。
5. 运行同一命令，期望主入口、Lead 动作、参数校验和错误诊断测试通过。
6. 执行 `git add src/mycode/team/tool.py tests/test_team_tool.py && git commit -m "feat: add team lead tools"`，不得执行 push。

**验证：** `python -m pytest tests/test_team_tool.py -q -k lead` 通过；Team 主入口在未激活 Team 时仍可发现。

## T21：实现成员协作工具和审批消息

**文件：** `src/mycode/team/tool.py`、`src/mycode/team/runtime.py`、`tests/test_team_tool.py`、`tests/test_team_runtime.py`

**依赖：** T10、T14、T20

**步骤：**
1. 测试成员 task create/list/claim/update、自身 message/broadcast、plan_submit、status_update、shutdown response；测试成员不能 spawn、archive、integrate 或递归 `Agent`。
2. 运行 `python -m pytest tests/test_team_tool.py tests/test_team_runtime.py -q -k member`，期望成员工具测试失败。
3. 实现成员工具参数解析和权限校验；成员只能改自己的任务/状态，任务领取遵守 DAG/CAS，计划提交写入对应 task ID 和 revision。
4. 将工具调用接入 runtime 的 mailbox 消费和 checkpoint，审批通过前拒绝写文件、提交和 shell，完成后发送一次 `status_update`。
5. 运行同一命令，期望成员动作、审批和状态通知测试通过。
6. 执行 `git add src/mycode/team/tool.py src/mycode/team/runtime.py tests/test_team_tool.py tests/test_team_runtime.py && git commit -m "feat: add member collaboration tools"`，不得执行 push。

**验证：** `python -m pytest tests/test_team_tool.py tests/test_team_runtime.py -q -k member` 通过，成员看不到 Lead-only schema 且不能递归派生。

## T22：接入 AgentLoop 每轮动态工具视图

**文件：** `src/mycode/agent/loop.py`、`tests/test_agent_loop.py`、`tests/test_team_tool.py`

**依赖：** T19、T20、T21

**步骤：**
1. 测试 Team create 返回后当前模型请求 schema 不变、下一轮才显示 Lead 工具；coordinator 隐藏文件写工具和 `Agent`；普通 Agent 和旧 schema 调用均被执行前拒绝。
2. 运行 `python -m pytest tests/test_agent_loop.py tests/test_team_tool.py -q -k visibility`，期望 AgentLoop 尚无 visibility provider 而失败。
3. 在 AgentLoop 构造函数加入 `visible_tool_names_provider: Callable[[], frozenset[str] | None] | None`；每轮构建 model definitions 和 deferred summaries 时取 provider，并与 Skill 可见集合求交集。
4. 将 TeamToolPolicy 接入执行前 interceptor，任何隐藏工具、窗格输入或供应商复用旧 schema 的调用都返回中文结构化拒绝。
5. 运行同一命令，再运行 `python -m pytest tests/test_agent_loop.py -q`，期望动态 schema 和现有 AgentLoop 全量测试通过。
6. 执行 `git add src/mycode/agent/loop.py tests/test_agent_loop.py tests/test_team_tool.py && git commit -m "feat: support dynamic team tool visibility"`，不得执行 push。

**验证：** 两条 pytest 命令均退出码为 0，并证明 schema 切换只发生在下一轮。

## T23：将 push 和远端写入设为系统硬禁令

**文件：** `src/mycode/permission/command.py`、`tests/test_permission_command.py`

**依赖：** T1

**步骤：**
1. 测试 `git push`、`git -C repo push`、链式 push、PowerShell/cmd 包装、远端分支创建、`remote add/set-url` 和等价远端写命令均返回 `FORBIDDEN`。
2. 运行 `python -m pytest tests/test_permission_command.py -q -k "push or remote"`，期望当前 analyzer 未覆盖全部包装形式而失败。
3. 在 token 化命令分析中识别 Git push、远端写配置和网络写链，不依赖提示词或配置；返回不可被覆盖的 `FORBIDDEN`，保持 pull/fetch 既有明确语义。
4. 运行同一命令，再运行 `python -m pytest tests/test_permission_command.py -q`，期望硬禁令和已有命令测试通过。
5. 执行 `git add src/mycode/permission/command.py tests/test_permission_command.py && git commit -m "feat: forbid remote git writes"`，不得执行 push。

**验证：** 两条 pytest 命令通过，任意权限档位和人工审批都不能把这些命令变为可执行。

## T24：实现 coordinator shell 和权限优先级

**文件：** `src/mycode/permission/policy.py`、`src/mycode/team/policy.py`、`tests/test_permission_policy.py`、`tests/test_team_tool.py`

**依赖：** T2、T19、T23

**步骤：**
1. 测试 coordinator 下文件写、任意 shell 写、命令拼接写入和非 Git 命令被拒绝；只读命令、明确的本地 Git 集成命令可执行；系统 `FORBIDDEN` 优先于规则、审批和配置。
2. 运行 `python -m pytest tests/test_permission_policy.py tests/test_team_tool.py -q -k "coordinator or forbidden"`，期望 wrapper 和优先级测试失败。
3. 实现 `coordinator_write_forbidden` policy wrapper：只允许只读工具、Team 编排、消息、审批、合并和经过结构化检查的本地 Git 命令；禁止通过 shell 绕过文件写限制。
4. 修改 PermissionPolicy 让 `FORBIDDEN` 在所有其他决策前返回，不允许配置覆盖；保留普通 Team Lead 的文件写和 `Agent` 行为。
5. 运行两条专项命令，期望 coordinator 和全局权限回归通过。
6. 执行 `git add src/mycode/permission/policy.py src/mycode/team/policy.py tests/test_permission_policy.py tests/test_team_tool.py && git commit -m "feat: enforce coordinator shell limits"`，不得执行 push。

**验证：** `python -m pytest tests/test_permission_policy.py tests/test_team_tool.py -q -k "coordinator or forbidden"` 与 `python -m pytest tests/test_permission_policy.py -q` 均通过。

## T25：实现集成前置检查和临时本地分支

**文件：** `src/mycode/team/integration.py`、`tests/test_team_integration.py`

**依赖：** T8、T16、T18、T24

**步骤：**
1. 在临时 Git 仓库测试目标 worktree 脏、目标分支/批次基线不符、repository id 不符、Lead 未提交修改、依赖拓扑顺序和禁止 stash/reset/覆盖。
2. 运行 `python -m pytest tests/test_team_integration.py -q -k preflight`，期望 IntegrationService 未实现而失败。
3. 实现 `IntegrationService` 前置检查和临时本地集成分支/worktree 创建；记录起始 ref，按 TaskBoard 拓扑顺序准备成员提交，所有 Git 调用用参数数组、有界输出和显式 cwd。
4. 运行同一命令，期望脏目标、身份不符、拓扑顺序和临时对象创建测试通过。
5. 执行 `git add src/mycode/team/integration.py tests/test_team_integration.py && git commit -m "feat: prepare atomic team integration"`，不得执行 push。

**验证：** `python -m pytest tests/test_team_integration.py -q -k preflight` 通过，前置失败时目标 ref 与用户文件均不改变。

## T26：实现冲突任务、成功合并和失败回滚

**文件：** `src/mycode/team/integration.py`、`tests/test_team_integration.py`

**依赖：** T25

**步骤：**
1. 测试冲突 abort 后创建 `TaskKind.CODE` conflict task、成员通知、解决后重试、无法解决时目标不变、成功更新本地目标 ref、已集成且干净成员分支基线同步和禁止 push。
2. 运行 `python -m pytest tests/test_team_integration.py -q -k "conflict or success or rollback"`，期望冲突和成功路径失败。
3. 实现冲突路径：abort 临时合并，保留成员 worktree/branch/context，创建带依赖的 conflict task 并发送通知；无解时删除临时对象但不修改目标分支。
4. 实现成功路径：所有合并和批次验证通过后仅更新目标本地 ref，写 `IntegrationReport` 和批次状态，为已集成、idle、干净成员同步基线。
5. 运行同一命令，期望成功、冲突、回滚和成员保留测试通过。
6. 执行 `git add src/mycode/team/integration.py tests/test_team_integration.py && git commit -m "feat: integrate team batches atomically"`，不得执行 push。

**验证：** `python -m pytest tests/test_team_integration.py -q -k "conflict or success or rollback"` 通过；失败路径目标分支字节级保持原样。

## T27：接入 CLI Team 主入口和动态策略

**文件：** `src/mycode/cli.py`、`tests/test_hook_session_cli.py`、`tests/test_team_e2e.py`

**依赖：** T18、T20、T22、T24、T26

**步骤：**
1. 测试 `_run_application` 装配顺序、未激活 Team 时稳定主入口可发现、激活后下一轮显示 Lead 工具、退出时关闭 TeamService，以及用户已有聊天/Agent/MCP/Hooks 行为不变。
2. 运行 `python -m pytest tests/test_hook_session_cli.py tests/test_team_e2e.py -q -k "cli or assembly"`，期望 CLI 尚无 TeamService 接入而失败。
3. 按 Worktree/Permission → TeamStore/TaskBoard/MailboxStore → TeamService → TeamTool → AgentLoop 顺序装配；注入 Team visibility provider 和 policy wrapper，finally 调用 `TeamService.close`。
4. 只注册稳定 Team 主入口给 parent；Lead/member/coordinator 工具由下一轮动态视图决定，不把 Team 工具加入普通子 Agent。
5. 运行专项命令，期望装配、发现性、关闭和既有 CLI 回归通过。
6. 执行 `git add src/mycode/cli.py tests/test_hook_session_cli.py tests/test_team_e2e.py && git commit -m "feat: wire teams into cli assembly"`，不得执行 push。

**验证：** `python -m pytest tests/test_hook_session_cli.py tests/test_team_e2e.py -q -k "cli or assembly"` 通过，退出清理不会删除小组目录。

## T28：接入 worker、Session `/clear` 和 close

**文件：** `src/mycode/team/worker.py`、`src/mycode/cli.py`、`src/mycode/session.py`、`tests/test_session.py`、`tests/test_hook_session_cli.py`、`tests/test_team_e2e.py`

**依赖：** T12、T14、T17、T27

**步骤：**
1. 测试隐藏参数 `--team-worker <team>/<member>` 启动指定成员 worktree，worker 不创建 Lead 租约/主入口；测试 `/clear` 释放 Lead 租约但保留 TeamStore，close 保存协程 checkpoint 且不杀外部窗格成员。
2. 运行 `python -m pytest tests/test_session.py tests/test_hook_session_cli.py tests/test_team_e2e.py -q -k team`，期望 worker 和 Session 团队语义失败。
3. 实现 worker 参数解析和入口：使用成员 worktree cwd 与同一配置构造 `TeamMemberRuntime`，只消费 mailbox、checkpoint 和 wake，不创建新团队或 Lead 租约。
4. 拆分 Session 的普通 subagent 清理与 Team detach；`/clear`/close 调用 `TeamService.clear_session/close`，停止本地协程、保存检查点、释放租约，保留外部成员、邮箱、任务和 worktree。
5. 运行专项命令，期望 worker、clear、close、重启恢复和外部成员存活测试通过。
6. 执行 `git add src/mycode/team/worker.py src/mycode/cli.py src/mycode/session.py tests/test_session.py tests/test_hook_session_cli.py tests/test_team_e2e.py && git commit -m "feat: preserve teams across sessions"`，不得执行 push。

**验证：** `python -m pytest tests/test_session.py tests/test_hook_session_cli.py tests/test_team_e2e.py -q -k team` 通过；`/clear` 后新 Lead 可在租约释放或过期后接管。

## T29：增加配置示例、README 和公开导出

**文件：** `examples/mycode.team.yaml`、`README.md`、`src/mycode/team/__init__.py`、`tests/test_docs.py`

**依赖：** T2、T20、T27、T28

**步骤：**
1. 为 README/示例缺失断言写测试，覆盖 Team 目录、默认 16/4 上限、`auto` 顺序、`MYCODE_COORDINATOR=1`、消息协议、审批、恢复、归档、合并和 hard push 禁令。
2. 运行 `python -m pytest tests/test_docs.py -q -k team`，期望文档或示例断言失败。
3. 创建 `examples/mycode.team.yaml`，包含 team 配置、锁/邮箱/上下文上限和 coordinator capability，并明确环境变量需单独设置。
4. 更新 README 记录主入口动作、成员后端、任务状态/依赖、结构化消息、审批、`/clear`、接管、批次合并、只读归档和不可 push 规则，不写完整邮箱正文、凭据或远端写例外。
5. 补齐 `team.__all__` 后运行文档测试，期望示例、README 和导出断言通过。
6. 执行 `git add examples/mycode.team.yaml README.md src/mycode/team/__init__.py tests/test_docs.py && git commit -m "docs: describe persistent team workflows"`，不得执行 push。

**验证：** `python -m pytest tests/test_docs.py -q -k team` 通过，文档中没有允许 push 的措辞。

## T30：完成端到端场景和跨领域回归

**文件：** `tests/test_team_e2e.py`、`tests/test_config.py`、`tests/test_agent_loop.py`、`tests/test_permission_command.py`、`tests/test_permission_policy.py`、`tests/test_subagent_e2e.py`、`tests/test_worktree_e2e.py`

**依赖：** T28、T29

**步骤：**
1. 用 fake LLM、fake backend、临时用户目录和临时 Git 仓库写完整场景：Lead 创建批次 → 写入 DAG → 派生两名成员 → 计划审批 → 成员本地提交 → idle 通知 → mailbox 唤醒恢复 → 本地原子合并；同时覆盖脏目标、角色版本变化、恢复失败、锁长期占用和冲突无法解决。
2. 运行 `python -m pytest tests/test_team_*.py -q`，期望所有团队专项测试通过；失败时只修复对应失败后再继续。
3. 运行 `python -m pytest tests/test_agent_loop.py tests/test_session.py tests/test_config.py tests/test_permission_command.py tests/test_permission_policy.py tests/test_subagent_e2e.py tests/test_worktree_e2e.py -q`，期望跨领域回归零失败。
4. 运行 `python -m pytest tests -q`，期望全量测试通过。
5. 运行 `python -m compileall src` 和 `git diff --check`；检查测试日志和持久化文件不含凭据、环境变量值、完整对话或无界 Git 输出，并用 `rg -n "git push|remote add|remote set-url" src tests` 确认这些路径均被硬拒绝测试覆盖。
6. 仅在前述命令全部通过后，执行 `git status --short` 核对范围，再显式添加 Stage 14 测试文件并提交 `git commit -m "test: verify stage 14 team workflows"`；不得使用 `git add .`，不得执行 push。

**验证：** 团队专项、跨领域回归、全量 pytest、`compileall` 和 `git diff --check` 均退出码为 0；端到端结果证明目标分支只发生本地原子更新且没有任何 push。

## 执行顺序

```text
T1 → T2 ─┬→ T3 → T4 ─┬→ T5 → T13 → T14 ─┐
         │            ├→ T6 → T7 → T8 ────┤
         │            └→ T9 → T10 ────────┤
         └→ T11 → T12 ────────────────────┤
T1 → T15 → T16 ──────────────────────────┤
T17 → T18 ────────────────────────────────┤
T19 → T20 → T21 → T22 ───────────────────┤
T23 → T24 ────────────────────────────────┤
T8,T16,T18,T24 → T25 → T26 ──────────────┤
T18,T20,T22,T24,T26 → T27 → T28 → T29 → T30
```

每个任务必须先通过自己的验证命令再标记完成；任何失败都必须在原任务内修复并重新验证。所有提交仅限本地仓库检查点，禁止 push。

## 自查

- F1–F2：T1–T4、T17；F3–F5：T11–T18；F6：T19–T22；F7：T6–T8、T18、T20–T21；F8–F9：T9–T10、T14、T21；F10：T8、T14、T20–T21；F11：T17–T22、T27；F12：T15–T16、T25–T26；F13：T2、T19、T22–T24；F14：T12、T17–T18、T28–T29；F15：T23–T24、T26、T30。
- N1–N6：T3–T5、T11–T14、T19、T22–T24；N7：T2、T3、T9、T10、T11、T30；N8：T11–T12、T30；N9：T16、T23–T26、T30；N10–N12：T1–T2、T9–T10、T13–T14、T17–T30；N13–N14：T27–T30。
- 每个计划组件均有任务归属；所有任务都有明确文件、依赖、失败测试、通过命令和本地提交命令；无 `TODO`、`TBD` 或“稍后实现”等占位符；任务依赖图无环。
