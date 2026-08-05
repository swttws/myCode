# Stage 14 Team Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 myCode 子 Agent、Worktree、权限和 AgentLoop 之上实现可跨进程恢复的长期团队、共享任务/邮箱协作、Lead 本地原子合并和 coordinator 双锁模式。

**Architecture:** 新增 `src/mycode/team/` 领域，使用 JSON 文件和独占锁文件持久化小组、成员、任务、邮箱和上下文。TeamService 负责生命周期和编排，MemberRuntime 复用现有 AgentLoop，BackendSelector 选择协程、tmux 或 Windows Terminal，TeamToolPolicy 同时控制 schema 可见性和执行权限；IntegrationService 使用 Worktree Git gateway 在临时本地分支完成批次合并。

**Tech Stack:** Python 3.10+、`asyncio`、`dataclasses`、`pathlib`、标准库 JSON/`subprocess`、现有 PyYAML 配置解析、现有 AgentLoop/Permission/Worktree/ToolRegistry，以及 pytest 临时目录和 fake 依赖。

---

## 架构概览

### Team 领域

`team` 包集中处理小组目录、Lead 租约、成员花名册、批次、任务 DAG、邮箱协议、成员后端、上下文恢复、工具权限和 Git 集成编排。每个文件只保留一个领域职责，TeamService 负责顺序和状态转换，不把锁、JSON 格式或 Git 命令散落到调用方。

### 现有系统接入

- `src/mycode/config.py` 解析可选 `team` 全局配置并返回 `TeamConfig`。
- `src/mycode/cli.py` 装配 TeamStore、TaskBoard、MailboxStore、TeamService，注册稳定的 Team 主入口，并为外部窗格提供 worker 启动参数。
- `src/mycode/agent/loop.py` 增加可选的动态工具可见性提供者；每次模型请求构建时计算当前工具集合。
- `src/mycode/session.py` 在 `/clear` 和 close 时只释放 Lead 租约并持久化成员检查点，不调用现有会话级子 Agent 清理来删除长期团队。
- `src/mycode/permission/command.py` 把 `git push` 和等价远端写命令加入不可覆盖的 FORBIDDEN 底线；`team.policy` 额外实施 coordinator 文件写和 shell 白名单。
- `src/mycode/worktree/service.py` 和 `src/mycode/worktree/git.py` 增加长期成员 worktree 与本地集成分支所需的结构化入口，不调用 `chdir`。
- `src/mycode/subagent/tooling.py` 保持 Team 工具为 parent-only；普通子 Agent 不会从 `allowed_tools: ["*"]` 获得团队能力。

## 核心数据结构和接口

### `team.models`

定义以下枚举：`TeamState`（`active/archived/recovery_required`）、`MemberState`（`provisioning/running/idle/awaiting_approval/blocked/stopping/stopped/failed`）、`MemberBackend`（`auto/tmux/terminal/in_process`）、`ResolvedBackend`（`tmux/windows_terminal/in_process`）、`TaskKind`（`code/read_only`）、`TeamTaskState`（`pending/claimed/awaiting_approval/running/blocked/completed/failed/cancelled`）、`BatchState`、`ApprovalState` 和 `MessageProtocol`。

定义不可变数据类：

- `TeamRecord`：小组名、仓库绝对路径/身份、固定目标分支、Lead 身份、成员上限、并发上限、状态和版本时间戳。
- `MemberRecord`：成员名、角色名/版本、长期 worktree/分支、请求/实际后端、审批要求、运行状态、邮箱/上下文路径、唤醒端点和版本。
- `BatchRecord`：批次 ID、用户目标、目标基线提交、批次状态、任务 ID、集成诊断和时间戳。
- `TeamTask`：任务 ID、批次 ID、标题、描述、依赖、交付类型、负责人、状态、计划版本、审批状态、提交 ID、验证摘要和错误。
- `TeamMessage`：消息 ID、协议、发件人、目标/广播、正文、摘要、时间戳、已读和投递状态。
- `TeamSnapshot`：TeamRecord、成员、批次、注册表和当前 LeadLease 的只读聚合。
- `TaskPatch`、`TaskResult`：任务 CAS 更新字段及代码/只读交付结果。
- `MemberSpec`、`MemberLaunchSpec`：Lead 请求的成员定义及传给后端的规范化启动参数。
- `BackendEnvironment`、`BackendSelection`、`BackendHandle`：能力探测输入、选择结果和可唤醒运行句柄。
- `LeadLease`、`WakeEndpoint`、`DeliveryReceipt`、`IntegrationReport` 和稳定 `TeamError`。

所有构造函数校验非空 ID、绝对路径、合法状态组合、版本非负、时间戳可解析以及 tuple 字段；新增字段带简洁中文注释。

### `team.config`

实现：

```python
@dataclass(frozen=True)
class TeamConfig:
    max_members: int = 16
    max_active_members: int = 4
    lock_retry_interval_seconds: float = 0.1
    lock_timeout_seconds: float = 5.0
    lock_stale_after_seconds: float = 30.0
    mailbox_message_max_bytes: int = 64 * 1024
    mailbox_summary_max_bytes: int = 4 * 1024
    context_max_bytes: int = 4 * 1024 * 1024
    backend_priority: tuple[MemberBackend, ...] = (
        MemberBackend.TMUX,
        MemberBackend.TERMINAL,
        MemberBackend.IN_PROCESS,
    )
    coordinator_capability_enabled: bool = False
    graceful_shutdown_timeout_seconds: float = 10.0
```

`parse_team_config(raw)` 对缺失 `team` 使用安全默认值；布尔、正数、上限关系、后端枚举和最大字节数严格校验。coordinator 环境锁统一读取 `MYCODE_COORDINATOR=1`，空值、`0`、`false` 和未设置均视为关闭。`LLMConfig` 新增 `team: TeamConfig` 字段。

### `team.storage` / `team.locking`

```python
class FileLease:
    @classmethod
    async def acquire(cls, path: Path, *, config: TeamConfig, owner: str) -> "FileLease": ...
    async def release(self) -> None: ...

class TeamStore:
    def create(self, record: TeamRecord) -> TeamSnapshot: ...
    def load(self, team_name: str) -> TeamSnapshot: ...
    def save(self, snapshot: TeamSnapshot) -> None: ...
    async def acquire_lead(self, team_name: str, owner: str) -> LeadLease: ...
    async def release_lead(self, lease: LeadLease) -> None: ...
    def archive(self, lease: LeadLease) -> TeamRecord: ...
```

`TeamStore` 固定使用 `~/.mycode/teams/<team-name>/`，以 `team.json`、`members/<name>/member.json`、`batches/<id>/batch.json`、`batches/<id>/tasks/<id>.json`、`members/<name>/mailbox.jsonl`、`members/<name>/context.json` 和 `registry.json` 保存状态。所有 JSON 更新写入同目录临时文件后替换；邮箱只在锁内追加完整 JSON 行。

### `team.tasks`

```python
class TaskBoard:
    def create(self, task: TeamTask) -> TeamTask: ...
    def update(self, task_id: str, expected_revision: int, patch: TaskPatch) -> TeamTask: ...
    def delete(self, task_id: str, expected_revision: int) -> None: ...
    def claim(self, task_id: str, member_name: str, expected_revision: int) -> TeamTask: ...
    def transition(
        self,
        task_id: str,
        expected_revision: int,
        state: TeamTaskState,
        result: TaskResult | None = None,
        error: str | None = None,
    ) -> TeamTask: ...
    def get(self, task_id: str) -> TeamTask: ...
    def list(self, batch_id: str | None = None) -> tuple[TeamTask, ...]: ...
```

`create`、依赖更新和 `claim` 均在批次锁内运行 Kahn 拓扑检查；`claim` 仅接受负责人为空、依赖已完成且版本匹配的任务。代码任务进入 `completed` 前验证提交 ID、干净 worktree 和验证摘要；只读任务只验证结果和摘要。状态转换表拒绝回退和重复结算。

### `team.mailbox`

```python
class MailboxStore:
    def register(self, member: MemberRecord) -> None: ...
    def send(self, message: TeamMessage) -> DeliveryReceipt: ...
    def receive(self, member_name: str) -> tuple[TeamMessage, ...]: ...
    def acknowledge(self, member_name: str, message_id: str) -> None: ...
    def unread(self, member_name: str) -> tuple[TeamMessage, ...]: ...
```

消息发送先读取注册表，再锁定目标邮箱追加；广播展开为多个目标消息。`receive` 不改变已读状态；`acknowledge` 只有在成员上下文检查点成功后调用。稳定消息 ID 用于幂等应用和崩溃重投。

### `team.backends` / `team.runtime`

```python
class MemberBackend(Protocol):
    async def start(self, spec: MemberLaunchSpec) -> BackendHandle: ...
    async def wake(self, handle: BackendHandle) -> None: ...
    async def stop(self, handle: BackendHandle, *, force: bool) -> None: ...

class BackendSelector:
    def select(self, requested: MemberBackend, environment: BackendEnvironment) -> BackendSelection: ...

class TeamMemberRuntime:
    async def start(self) -> None: ...
    async def run_until_idle(self) -> None: ...
    async def resume_from_checkpoint(self) -> None: ...
    async def graceful_stop(self) -> None: ...
    async def force_stop(self) -> None: ...
```

`TeamMemberRuntime` 使用现有 `create_task_tool_runtime` 构造工作区工具，再注册成员协作工具；使用文件型 `ConversationMemory` 实现消息恢复；每个 AgentEvent 安全点保存消息。外部 worker 通过 `worker.py` 从命令行参数读取小组名和成员名，调用同一运行时。

### `team.integration` / `team.service`

```python
class IntegrationService:
    async def integrate(self, batch: BatchRecord, *, lead_workspace: WorkspaceContext) -> IntegrationReport: ...

class TeamService:
    async def create_or_attach(self, team_name: str, *, goal: str | None = None) -> TeamSnapshot: ...
    async def spawn_member(self, spec: MemberSpec) -> MemberRecord: ...
    async def terminate_member(self, member_name: str) -> None: ...
    async def start_batch(self, goal: str) -> BatchRecord: ...
    async def send_message(self, message: TeamMessage) -> DeliveryReceipt: ...
    async def integrate_batch(self, batch_id: str) -> IntegrationReport: ...
    async def clear_session(self) -> None: ...
    async def close(self) -> None: ...
```

`TeamService` 是唯一编排入口；工具层不直接调用锁、JSON、Git 或后端命令。

## Spec 覆盖

| Spec | 设计归属 | 实现任务 |
|---|---|---|
| F1 长期小组与仓库绑定 | TeamRecord、TeamStore、TeamService | Task 1、2、9 |
| F2 Lead 租约与接管 | FileLease、TeamStore、Session | Task 2、9、14 |
| F3 花名册与角色快照 | MemberRecord、MemberRuntime、WorktreeService | Task 1、7、8、9 |
| F4 成员运行后端 | BackendSelector、三个 backend | Task 6、14 |
| F5 工作区与上下文 | JsonConversationMemory、MemberRuntime、长期 worktree | Task 5、7、8 |
| F6 工具可见性与权限 | TeamTool、TeamToolPolicy、AgentLoop provider | Task 7、10、11 |
| F7 批次与任务板 | TaskBoard、TeamService | Task 3、9 |
| F8 邮箱与注册表 | TeamStore、MailboxStore | Task 2、4 |
| F9 消息投递与协议 | MailboxStore、backend wake、MemberRuntime | Task 4、6、7 |
| F10 计划审批 | TaskBoard、MemberRuntime、Team tools | Task 3、7、10 |
| F11 Lead 编排 | TaskBoard、TeamService、Lead tools | Task 3、9、10 |
| F12 本地原子合并 | GitWorktreeGateway、IntegrationService | Task 8、13 |
| F13 coordinator 双锁 | TeamConfig、TeamToolPolicy、AgentLoop | Task 1、10、11、12 |
| F14 终止与归档 | TeamService、Session、backend stop | Task 6、9、14 |
| F15 远端写入禁令 | CommandAnalyzer、TeamToolPolicy、IntegrationService | Task 12、13 |

## 文件清单

### 新建

| 文件 | 职责 |
|---|---|
| `src/mycode/team/__init__.py` | 导出公开 Team 类型和服务 |
| `src/mycode/team/models.py` | 状态、数据类、稳定错误 |
| `src/mycode/team/config.py` | TeamConfig 解析与 coordinator 环境判断 |
| `src/mycode/team/locking.py` | 跨进程锁文件 |
| `src/mycode/team/storage.py` | 小组目录、JSON/JSONL 原子持久化 |
| `src/mycode/team/context.py` | 文件型 ConversationMemory 和成员上下文检查点 |
| `src/mycode/team/tasks.py` | DAG 任务板和状态转换 |
| `src/mycode/team/mailbox.py` | 注册表、邮箱、消息协议和确认 |
| `src/mycode/team/backends.py` | auto 选择、tmux、Windows Terminal、协程后端 |
| `src/mycode/team/runtime.py` | 成员 AgentLoop、checkpoint 和恢复 |
| `src/mycode/team/integration.py` | 临时集成分支和冲突报告 |
| `src/mycode/team/service.py` | 小组、成员、批次、租约生命周期 |
| `src/mycode/team/policy.py` | 工具可见性、coordinator 和成员执行限制 |
| `src/mycode/team/tool.py` | Team 主入口、Lead/成员协作工具 |
| `src/mycode/team/worker.py` | 外部窗格 worker 进程入口 |
| `tests/test_team_models.py` | 数据类和状态约束 |
| `tests/test_team_config.py` | 配置和双锁 |
| `tests/test_team_locking.py` | 锁竞争、重试和过期 |
| `tests/test_team_storage.py` | 目录、原子写、恢复 |
| `tests/test_team_tasks.py` | DAG、领取、审批、状态转换 |
| `tests/test_team_mailbox.py` | 邮箱协议、广播、去重、已读 |
| `tests/test_team_backends.py` | 后端探测和 fake adapter |
| `tests/test_team_runtime.py` | 成员 AgentLoop、checkpoint、恢复 |
| `tests/test_team_service.py` | 租约、成员、批次和归档 |
| `tests/test_team_integration.py` | 本地合并、冲突和回滚 |
| `tests/test_team_tool.py` | schema、动作和角色视图 |
| `tests/test_team_e2e.py` | 完整团队工作流 |
| `examples/mycode.team.yaml` | Team 配置示例和 coordinator 双锁说明 |

### 修改

| 文件 | 改动 |
|---|---|
| `src/mycode/config.py` | 解析 `team` 配置并挂载 `LLMConfig.team` |
| `src/mycode/cli.py` | 装配 TeamService、注册 TeamTool、处理 worker 参数和退出释放 |
| `src/mycode/agent/loop.py` | 支持每轮动态工具可见性 |
| `src/mycode/session.py` | `/clear`/close 只释放 Team 租约并保存成员检查点 |
| `src/mycode/permission/command.py` | 硬性拒绝 `git push` 和远端写命令 |
| `src/mycode/permission/policy.py` | 保证 FORBIDDEN 优先于所有配置和审批 |
| `src/mycode/worktree/service.py` | 增加稳定成员 worktree 准备及 Git gateway 访问 |
| `src/mycode/worktree/git.py` | 增加结构化临时分支、合并、abort 和本地 ref 更新 |
| `src/mycode/subagent/tooling.py` | 把 Team parent-only 工具加入普通子 Agent 全局排除集 |
| `tests/test_config.py` | Team 配置向后兼容和错误校验 |
| `tests/test_agent_loop.py` | 动态工具 schema 和隐藏工具调用拒绝 |
| `tests/test_session.py` | `/clear` 和 close 的长期团队语义 |
| `tests/test_permission_command.py` | push/远端写命令拒绝 |
| `tests/test_permission_policy.py` | 系统级 FORBIDDEN 不可覆盖 |
| `tests/test_worktree_service.py` | 成员 worktree 和集成 Git gateway |
| `tests/test_subagent_tooling.py` | 普通子 Agent 不可见 Team 工具 |
| `README.md` | Team 配置、工具动作、后端选择、恢复、coordinator 和 push 禁令 |

## 模块交互

```text
CLI
 ├─ Config / Permission / Worktree
 ├─ TeamStore / TaskBoard / MailboxStore
 ├─ TeamService
 │   ├─ MemberRuntime → AgentLoop → TaskToolRuntime
 │   ├─ BackendSelector → TmuxBackend / WindowsTerminalBackend / InProcessBackend
 │   └─ IntegrationService → Worktree Git gateway
 └─ TeamTool → dynamic ToolVisibilityProvider → AgentLoop
```

成员消息流：`TeamTool/MemberTool → TeamService → MailboxStore → lock + JSONL → backend.wake → TeamMemberRuntime → checkpoint → acknowledge`。

批次合并流：`Lead TeamTool → TaskBoard ready check → IntegrationService temporary branch → member conflict task → member commit → validation → target branch update → member branch baseline sync`。

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 持久化格式 | 标准库 JSON + 邮箱 JSONL | 便于原子替换、追加、人工诊断和跨进程读取，不增加数据库依赖 |
| 并发锁 | 独占创建锁文件、时间戳、有限重试 | Windows/Linux/macOS 都可用，满足过期锁语义 |
| Team 包边界 | 所有团队逻辑放 `src/mycode/team/` | 调用链集中、便于测试，避免污染 SubAgent 领域 |
| 成员循环 | 复用现有 AgentLoop | 保持协议、Hook、Skill、权限和上下文行为一致 |
| 成员 worktree | 复用 WorktreeService 长期 lease | 继承现有路径边界、Git 身份和保护检查 |
| 外部进程 | CLI worker 参数 + 结构化 subprocess 参数 | tmux/Windows Terminal 可启动完整实例，避免 shell 字符串注入 |
| 工具过滤 | schema 可见性提供者 + 执行前 TeamToolPolicy | 防止动态 schema 泄漏和旧 schema/窗格输入绕过 |
| coordinator 环境锁 | `MYCODE_COORDINATOR=1` 且配置 capability 为 true | 两把锁都由用户主动控制，默认关闭 |
| Git 集成 | 临时本地集成分支，成功后更新目标分支 | 满足整批原子语义，不直接修改 Lead 脏工作区 |
| push 禁令 | CommandAnalyzer 系统级 FORBIDDEN | 任何规则、权限档位或人工审批都不能放宽 |
| 阻塞恢复 | 任务进入 blocked，Lead 明确恢复/改派/取消 | 避免成员崩溃后自动重复执行和覆盖成果 |

## 执行任务

### Task 1: 建立 Team 模型与全局配置

**Files:**
- Create: `src/mycode/team/__init__.py`
- Create: `src/mycode/team/models.py`
- Create: `src/mycode/team/config.py`
- Modify: `src/mycode/config.py`
- Test: `tests/test_team_models.py`
- Test: `tests/test_team_config.py`
- Test: `tests/test_config.py`

**Dependencies:** None

- [ ] **Step 1: 写失败测试**：覆盖团队/成员/任务/消息状态组合、绝对路径、上限关系、默认 `max_members=16`、`max_active_members=4`、缺失 `team` 的安全默认、非法后端、非法锁数值，以及仅当配置 capability 和 `MYCODE_COORDINATOR=1` 同时满足时 coordinator 为真。
- [ ] **Step 2: 运行测试确认失败**：运行 `python -m pytest tests/test_team_models.py tests/test_team_config.py tests/test_config.py -q`，期望新测试因模块、类型和 `LLMConfig.team` 不存在而失败，既有配置测试保持通过。
- [ ] **Step 3: 实现最小模型和配置解析**：按本计划接口创建枚举、不可变 dataclass、`parse_team_config`、`coordinator_enabled_from_env`；在 `LLMConfig` 增加 `team` 字段并在 `load_config` 调用解析器，所有新增字段加中文注释。
- [ ] **Step 4: 运行专项测试**：再次运行同一 pytest 命令，期望所有模型、默认值、环境变量和错误校验通过。
- [ ] **Step 5: 提交**：`git add src/mycode/team src/mycode/config.py tests/test_team_models.py tests/test_team_config.py tests/test_config.py && git commit -m "feat: add team models and config"`。

### Task 2: 实现跨进程锁和小组目录存储

**Files:**
- Create: `src/mycode/team/locking.py`
- Create: `src/mycode/team/storage.py`
- Test: `tests/test_team_locking.py`
- Test: `tests/test_team_storage.py`

**Dependencies:** Task 1

- [ ] **Step 1: 写失败测试**：用 `tmp_path` 验证 `~/.mycode/teams/<name>` 布局、临时 JSON 替换、邮箱目录创建、两个 owner 的锁竞争、有限重试、旧锁回收、路径越界和损坏 JSON 拒绝恢复。
- [ ] **Step 2: 运行测试确认失败**：运行 `python -m pytest tests/test_team_locking.py tests/test_team_storage.py -q`，期望 `ModuleNotFoundError` 或未实现错误。
- [ ] **Step 3: 实现锁**：使用 `Path.open("x")` 创建锁文件，内容保存 owner、创建时间和进程标识；在配置重试窗口内按固定间隔重试，超过 stale 时间先验证 owner 非活动再回收，`finally` 中只删除自己持有的锁。
- [ ] **Step 4: 实现存储**：提供 `TeamStore`、JSON 序列化/反序列化、原子写入、team/member/batch/task 路径解析和 registry 读写；所有路径用 `resolve()` 后确认仍位于 teams 根目录。
- [ ] **Step 5: 运行测试确认通过**：运行同一 pytest 命令，期望并发和损坏恢复场景全部通过。
- [ ] **Step 6: 提交**：`git add src/mycode/team/locking.py src/mycode/team/storage.py tests/test_team_locking.py tests/test_team_storage.py && git commit -m "feat: persist team state with file locks"`。

### Task 3: 实现共享任务板和简单 DAG

**Files:**
- Create: `src/mycode/team/tasks.py`
- Test: `tests/test_team_tasks.py`

**Dependencies:** Task 1, Task 2

- [ ] **Step 1: 写失败测试**：覆盖创建、更新、删除、读取、列表、依赖悬空/自依赖/环检测、前置未完成阻止领取、版本冲突、并发领取单胜、代码任务提交约束、只读任务完成和 blocked 恢复。
- [ ] **Step 2: 运行测试确认失败**：运行 `python -m pytest tests/test_team_tasks.py -q`，期望未实现错误。
- [ ] **Step 3: 实现 `TaskBoard`**：所有 mutating 操作锁定批次；使用 Kahn 拓扑检查；`claim` 仅接受 `pending`、无负责人、依赖全完成和 expected revision 匹配；状态转换表拒绝回退、重复终态和未批准运行。
- [ ] **Step 4: 实现交付验证**：`completed` 转换要求代码任务有提交 ID、验证摘要和干净 worktree，或只读任务有结构化结果和验证摘要；删除只允许未开始且无后继依赖的任务。
- [ ] **Step 5: 运行测试确认通过**：运行 `python -m pytest tests/test_team_tasks.py -q`，期望所有 DAG、CAS 和状态测试通过。
- [ ] **Step 6: 提交**：`git add src/mycode/team/tasks.py tests/test_team_tasks.py && git commit -m "feat: add persistent team task board"`。

### Task 4: 实现名称注册表和邮箱协议

**Files:**
- Create: `src/mycode/team/mailbox.py`
- Test: `tests/test_team_mailbox.py`

**Dependencies:** Task 1, Task 2

- [ ] **Step 1: 写失败测试**：覆盖稳定消息 ID、协议字段、自动 UTC 时间戳、默认未读、点对点、广播展开、注册表缺失、邮箱锁重试、按顺序读取、ack 后已读、崩溃重投和重复 ID 去重。
- [ ] **Step 2: 运行测试确认失败**：运行 `python -m pytest tests/test_team_mailbox.py -q`，期望未实现错误。
- [ ] **Step 3: 实现注册表和 JSONL 邮箱**：实现 `register/send/receive/unread/acknowledge`；发送先解析成员名称，再在目标 mailbox lock 内追加完整 JSON 行；广播为每个当前成员生成独立投递记录。
- [ ] **Step 4: 实现确认与幂等**：`receive` 只返回未读消息，`acknowledge` 在锁内更新已读索引；使用 message ID 记录已应用集合，重复读取不重复注入。
- [ ] **Step 5: 运行测试确认通过**：运行 `python -m pytest tests/test_team_mailbox.py -q`，期望所有协议、锁和去重测试通过。
- [ ] **Step 6: 提交**：`git add src/mycode/team/mailbox.py tests/test_team_mailbox.py && git commit -m "feat: add team mailbox protocols"`。

### Task 5: 实现可恢复成员上下文

**Files:**
- Create: `src/mycode/team/context.py`
- Test: `tests/test_team_runtime.py`

**Dependencies:** Task 1, Task 2

- [ ] **Step 1: 写失败测试**：用 `ChatMessage` 序列化/恢复覆盖 role、content、tool call 字段和 origin；验证 append/replace/clear、原子保存、损坏文件拒绝、超限截断或拒绝，以及重启后消息顺序保持。
- [ ] **Step 2: 运行测试确认失败**：运行 `python -m pytest tests/test_team_runtime.py -q -k context`，期望文件型 memory 未实现。
- [ ] **Step 3: 实现 `JsonConversationMemory`**：实现现有 `ConversationMemory` 抽象，采用 schema 版本、临时文件替换和 UTF-8 JSON；只保存恢复所需消息，不保存供应商密钥或完整诊断。
- [ ] **Step 4: 连接 checkpoint 边界**：让成员运行时在每个 `AgentEvent` 安全点调用 memory store，模型轮次、工具结果和 mailbox 已应用 ID 与上下文一起保存。
- [ ] **Step 5: 运行测试确认通过**：运行 `python -m pytest tests/test_team_runtime.py -q -k context`，期望持久化和恢复测试通过。
- [ ] **Step 6: 提交**：`git add src/mycode/team/context.py tests/test_team_runtime.py && git commit -m "feat: persist resumable team contexts"`。

### Task 6: 实现后端探测和窗格生命周期

**Files:**
- Create: `src/mycode/team/backends.py`
- Test: `tests/test_team_backends.py`

**Dependencies:** Task 1

- [ ] **Step 1: 写失败测试**：使用 fake subprocess runner 覆盖 `auto` 的 tmux → Windows Terminal → in_process 选择、显式不可用失败、无窗格端点失败、spawn/wake/stop 参数不含 shell 拼接、优雅停止超时和强制停止。
- [ ] **Step 2: 运行测试确认失败**：运行 `python -m pytest tests/test_team_backends.py -q`，期望未实现错误。
- [ ] **Step 3: 实现选择器和协议**：定义 `BackendEnvironment`、`BackendSelection`、`BackendHandle` 和 `MemberBackend`；能力探测只返回明确的 available/unavailable reason，不修改环境。
- [ ] **Step 4: 实现 `TmuxBackend` 和 `WindowsTerminalBackend`**：使用 `asyncio.create_subprocess_exec` 的参数数组创建指定 cwd 的窗格/进程，登记可寻址成员端点；wake 只发送目标成员标识，不把正文拼入 shell。
- [ ] **Step 5: 实现 `InProcessBackend`**：把 `TeamMemberRuntime.run_until_idle` 作为 asyncio task，wake 通过事件唤醒，stop 先 graceful 后 force。
- [ ] **Step 6: 运行测试确认通过**：运行 `python -m pytest tests/test_team_backends.py -q`，期望选择、参数和生命周期测试通过。
- [ ] **Step 7: 提交**：`git add src/mycode/team/backends.py tests/test_team_backends.py && git commit -m "feat: add team member backends"`。

### Task 7: 接入长期成员 AgentLoop

**Files:**
- Create: `src/mycode/team/runtime.py`
- Modify: `src/mycode/subagent/tooling.py`
- Test: `tests/test_team_runtime.py`
- Test: `tests/test_subagent_tooling.py`

**Dependencies:** Task 3, Task 4, Task 5, Task 6

- [ ] **Step 1: 写失败测试**：用 fake LLM 验证成员使用角色 revision、独立 worktree、成员协作工具、计划审批前只读、审批后执行、完成进入 idle、邮箱消息恢复、普通子 Agent 看不到 Team 工具。
- [ ] **Step 2: 运行测试确认失败**：运行 `python -m pytest tests/test_team_runtime.py tests/test_subagent_tooling.py -q -k team`，期望成员 runtime 或隔离策略未实现。
- [ ] **Step 3: 实现成员工具构造**：调用现有 `create_task_tool_runtime` 创建工作区工具，再注册 `TeamMemberTools`；把 Team parent-only 工具加入全局排除集，避免普通角色的 `*` 白名单暴露团队能力。
- [ ] **Step 4: 实现 `TeamMemberRuntime`**：加载 `JsonConversationMemory`，根据角色和任务生成 AgentLoop；运行 mailbox 消费、任务 prompt、计划状态、AgentEvent checkpoint、idle/status_update 和 blocked 转换。
- [ ] **Step 5: 实现恢复检查**：启动前校验 team/member/task/role revision、workspace 绝对路径、repository id 和 context schema；失败只写 `recovery_required`/`blocked`，不重建或覆盖已有目录。
- [ ] **Step 6: 运行测试确认通过**：运行 `python -m pytest tests/test_team_runtime.py tests/test_subagent_tooling.py -q`，期望成员隔离、恢复、审批和普通子 Agent 回归通过。
- [ ] **Step 7: 提交**：`git add src/mycode/team/runtime.py src/mycode/subagent/tooling.py tests/test_team_runtime.py tests/test_subagent_tooling.py && git commit -m "feat: run resumable team members"`。

### Task 8: 支持长期成员 Worktree 和结构化 Git gateway

**Files:**
- Modify: `src/mycode/worktree/service.py`
- Modify: `src/mycode/worktree/git.py`
- Test: `tests/test_worktree_service.py`
- Test: `tests/test_worktree_git.py`

**Dependencies:** Task 1

- [ ] **Step 1: 写失败测试**：验证成员身份使用受控 `team/<team>/<member>` 相对路径和分支；同成员恢复只读复用；不同成员路径不同；并发准备只有一个 owner；不调用进程级 `chdir`。
- [ ] **Step 2: 运行测试确认失败**：运行 `python -m pytest tests/test_worktree_service.py tests/test_worktree_git.py -q -k team`，期望长期成员入口和 Git 方法不存在。
- [ ] **Step 3: 增加成员准备入口**：在 WorktreeService 增加 `member_identity`/`prepare_member`，复用现有 path policy、metadata store、初始化和保护检查；成员 lease 由 TeamService 持有直到归档或明确清理。
- [ ] **Step 4: 增加结构化本地 Git 方法**：在 GitWorktreeGateway 增加创建临时分支、创建临时 worktree、读取 status/head、merge、abort merge、更新本地 ref 和删除临时对象的方法；所有命令用参数数组、显式 cwd、有界输出和现有 timeout。
- [ ] **Step 5: 运行测试确认通过**：运行同一 pytest 命令，期望成员 worktree、恢复、并发和 Git 参数测试通过。
- [ ] **Step 6: 提交**：`git add src/mycode/worktree/service.py src/mycode/worktree/git.py tests/test_worktree_service.py tests/test_worktree_git.py && git commit -m "feat: support persistent team worktrees"`。

### Task 9: 实现 TeamService 生命周期、租约和批次编排

**Files:**
- Create: `src/mycode/team/service.py`
- Test: `tests/test_team_service.py`

**Dependencies:** Task 2, Task 3, Task 4, Task 6, Task 7, Task 8

- [ ] **Step 1: 写失败测试**：覆盖 create/attach、仓库身份和目标分支固定、Lead 租约竞争/释放/过期接管、成员上限、active 上限、角色版本记录、成员 worktree 保留、start_batch、idle/blocked 通知和 archive 前置条件。
- [ ] **Step 2: 运行测试确认失败**：运行 `python -m pytest tests/test_team_service.py -q`，期望 TeamService 未实现。
- [ ] **Step 3: 实现生命周期**：让 TeamService 串联 TeamStore、TaskBoard、MailboxStore、BackendSelector、MemberRuntime 和 WorktreeService；所有跨对象更新按 team → member/task → mailbox 的固定顺序获取锁。
- [ ] **Step 4: 实现成员派生/终止**：解析并冻结角色 revision，准备长期 worktree，注册 endpoint，启动后端；终止先发送 shutdown_request，超时 force，保留成员状态和成果。
- [ ] **Step 5: 实现批次编排**：创建批次基线、写入任务、检查 ready 任务、转发 plan_submit/plan_decision/status_update，并在异常恢复失败时把任务置 blocked。
- [ ] **Step 6: 实现 `/clear`/close/archive**：clear/close 只保存本地成员 checkpoint、停止同进程 task、释放 Lead lease；archive 检查无运行任务和未处理成果后标记只读。
- [ ] **Step 7: 运行测试确认通过**：运行 `python -m pytest tests/test_team_service.py -q`，期望生命周期和并发场景通过。
- [ ] **Step 8: 提交**：`git add src/mycode/team/service.py tests/test_team_service.py && git commit -m "feat: manage persistent team lifecycle"`。

### Task 10: 实现 Team 主入口、Lead/成员协作工具和可见性策略

**Files:**
- Create: `src/mycode/team/policy.py`
- Create: `src/mycode/team/tool.py`
- Test: `tests/test_team_tool.py`

**Dependencies:** Task 3, Task 4, Task 9

- [ ] **Step 1: 写失败测试**：固定 Team 主入口 schema；覆盖 `create/attach/status/archive`、Lead 的 member/task/message/approval/integrate 动作、成员的 task/message/plan/status 动作、未知字段拒绝、角色权限拒绝和 coordinator action 集合。
- [ ] **Step 2: 运行测试确认失败**：运行 `python -m pytest tests/test_team_tool.py -q`，期望 TeamTool 和 policy 未实现。
- [ ] **Step 3: 实现稳定入口**：创建 `TeamTool`，定义 `ToolRuntimeScope.PARENT_ONLY`、固定 action schema 和稳定中文结果；创建成功后只更新 TeamService capability 状态，下一轮由 visibility provider 展示 Lead 工具。
- [ ] **Step 4: 实现 Lead/成员工具**：按统一参数解析调用 TeamService/TaskBoard/MailboxStore；使用 task revision、plan revision 和 member name 做 CAS；结果只返回有界摘要、ID、状态和诊断。
- [ ] **Step 5: 实现 TeamToolPolicy**：分别计算 parent、lead、member、coordinator 可见名称；隐藏工具的旧调用在执行前返回结构化拒绝；普通子 Agent 的候选集合永远排除 Team 工具。
- [ ] **Step 6: 运行测试确认通过**：运行 `python -m pytest tests/test_team_tool.py -q`，期望 schema、权限和 action 测试通过。
- [ ] **Step 7: 提交**：`git add src/mycode/team/policy.py src/mycode/team/tool.py tests/test_team_tool.py && git commit -m "feat: add team collaboration tools"`。

### Task 11: 接入 AgentLoop 动态工具视图

**Files:**
- Modify: `src/mycode/agent/loop.py`
- Test: `tests/test_agent_loop.py`
- Test: `tests/test_team_tool.py`

**Dependencies:** Task 10

- [ ] **Step 1: 写失败测试**：验证 Team create 工具返回后当前模型轮次不变，下一次请求才显示 Lead 工具；coordinator 视图隐藏 WriteFile/EditFile/Agent；旧工具调用仍被 TeamToolPolicy 拒绝。
- [ ] **Step 2: 运行测试确认失败**：运行 `python -m pytest tests/test_agent_loop.py tests/test_team_tool.py -q -k visibility`，期望 AgentLoop 构造函数没有 visibility provider。
- [ ] **Step 3: 增加动态 provider**：在 AgentLoop 构造函数添加 `visible_tool_names_provider: Callable[[], frozenset[str] | None] | None`；`_model_definitions` 与 deferred summaries 取 provider 结果后再与 Skill visible names 求交集。
- [ ] **Step 4: 增加执行前拒绝**：TeamToolPolicy 由 TeamService 暴露给 PermissionInterceptor wrapper；即使供应商复用旧 schema、直接构造隐藏工具调用或窗格输入，before_tool 都返回中文结构化拒绝。
- [ ] **Step 5: 运行测试确认通过**：运行 `python -m pytest tests/test_agent_loop.py tests/test_team_tool.py -q -k visibility`，期望 schema 切换和旧调用拒绝通过，现有 AgentLoop 全量测试不回归。
- [ ] **Step 6: 提交**：`git add src/mycode/agent/loop.py tests/test_agent_loop.py tests/test_team_tool.py && git commit -m "feat: support dynamic team tool visibility"`。

### Task 12: 实现 coordinator shell 限制和全局 push 禁令

**Files:**
- Modify: `src/mycode/permission/command.py`
- Modify: `src/mycode/permission/policy.py`
- Modify: `src/mycode/team/policy.py`
- Test: `tests/test_permission_command.py`
- Test: `tests/test_permission_policy.py`
- Test: `tests/test_team_tool.py`

**Dependencies:** Task 1, Task 10

- [ ] **Step 1: 写失败测试**：覆盖 `git push`、`git -C repo push`、链式 push、PowerShell/cmd 包装 push、远端分支创建和等价远端写命令始终 FORBIDDEN；覆盖 coordinator 文件写、任意 shell 写和非 Git 命令拒绝；只读命令与允许的本地 Git 集成命令可执行。
- [ ] **Step 2: 运行测试确认失败**：运行 `python -m pytest tests/test_permission_command.py tests/test_permission_policy.py tests/test_team_tool.py -q -k "push or coordinator"`，期望现有 analyzer 把 push 当作安全/可审批。
- [ ] **Step 3: 扩展 CommandAnalyzer**：在 token 化后的 git 分支检测中识别 push、remote add/set-url、push 包装和网络写链，返回不可被规则覆盖的 `FORBIDDEN` 决策；保持现有 pull/fetch 相关测试的明确语义。
- [ ] **Step 4: 实现 coordinator policy wrapper**：对 `ToolKind.WRITE` 文件工具和 `run_command` 非白名单命令返回稳定 `coordinator_write_forbidden`；本地 Git 合并命令通过结构化参数检查后放行，再交给既有 PermissionService。
- [ ] **Step 5: 运行测试确认通过**：运行上述专项测试和 `python -m pytest tests/test_permission_command.py tests/test_permission_policy.py -q`，期望所有硬底线和现有权限回归通过。
- [ ] **Step 6: 提交**：`git add src/mycode/permission/command.py src/mycode/permission/policy.py src/mycode/team/policy.py tests/test_permission_command.py tests/test_permission_policy.py tests/test_team_tool.py && git commit -m "feat: enforce team coordinator and push safety"`。

### Task 13: 实现批次本地原子合并

**Files:**
- Create: `src/mycode/team/integration.py`
- Test: `tests/test_team_integration.py`

**Dependencies:** Task 3, Task 8, Task 9, Task 12

- [ ] **Step 1: 写失败测试**：在临时 Git 仓库验证目标脏拒绝、基线/分支身份不符拒绝、依赖拓扑顺序、临时集成分支、成功更新目标、冲突转任务、无法解决时目标不变、已集成干净成员分支同步、禁止 push。
- [ ] **Step 2: 运行测试确认失败**：运行 `python -m pytest tests/test_team_integration.py -q`，期望 IntegrationService 未实现。
- [ ] **Step 3: 实现前置检查**：读取目标 worktree status/head，确认干净、分支和 repository id 与 BatchRecord 一致；记录起始 ref，不执行 stash、reset 或复制 Lead 文件。
- [ ] **Step 4: 实现临时集成**：通过 GitWorktreeGateway 创建临时本地分支/worktree，按 TaskBoard 的拓扑排序合并成员提交；每个命令使用结构化参数和有界输出。
- [ ] **Step 5: 实现冲突路径**：abort 临时合并，创建带 `TaskKind.CODE` 的 conflict task 和成员通知；成员提交解决后可重新进入集成；最终失败删除临时对象但保留成员 worktree/branch。
- [ ] **Step 6: 实现成功提交**：所有合并和批次验证通过后只更新本地目标 ref；记录 IntegrationReport，更新批次状态，并为已集成、空闲、干净成员调用基线同步。
- [ ] **Step 7: 运行测试确认通过**：运行 `python -m pytest tests/test_team_integration.py -q`，期望成功、脏目标、冲突和失败关闭测试通过。
- [ ] **Step 8: 提交**：`git add src/mycode/team/integration.py tests/test_team_integration.py && git commit -m "feat: atomically integrate local team batches"`。

### Task 14: 接入 CLI、worker 和 Session 生命周期

**Files:**
- Create: `src/mycode/team/worker.py`
- Modify: `src/mycode/cli.py`
- Modify: `src/mycode/session.py`
- Modify: `tests/test_session.py`
- Modify: `tests/test_hook_session_cli.py`
- Create: `tests/test_team_e2e.py`

**Dependencies:** Task 6, Task 7, Task 9, Task 10, Task 11, Task 12, Task 13

- [ ] **Step 1: 写失败测试**：覆盖 CLI 装配顺序、TeamTool 始终可发现、worker 参数启动指定成员、`/clear` 释放租约但不清 TeamStore、close 保存协程 checkpoint、外部成员不被主进程退出杀死。
- [ ] **Step 2: 运行测试确认失败**：运行 `python -m pytest tests/test_session.py tests/test_hook_session_cli.py tests/test_team_e2e.py -q -k team`，期望 CLI/Session 没有 TeamService 接入。
- [ ] **Step 3: 修改 CLI 装配**：在 `_run_application` 中按 Worktree/Permission → TeamStore/Board/Mailbox → TeamService → TeamTool → AgentLoop 的顺序构造；给 AgentLoop 注入 Team visibility provider 和 policy wrapper；退出 finally 调用 TeamService.close。
- [ ] **Step 4: 增加 worker 模式**：解析隐藏的 `--team-worker <team>/<member>` 参数，使用成员 worktree cwd 和同一配置构造 TeamMemberRuntime；worker 只处理邮箱和 checkpoint，不创建 Lead 租约或 Team 主入口。
- [ ] **Step 5: 修改 Session**：将 `/clear` 和 `close` 路径拆分为普通 subagent 清理与长期 Team detach；调用 `TeamService.clear_session/close` 释放租约、停止本地协程并保存检查点，保留外部成员和小组目录。
- [ ] **Step 6: 运行专项测试确认通过**：运行 `python -m pytest tests/test_session.py tests/test_hook_session_cli.py tests/test_team_e2e.py -q -k team`，期望装配、worker、clear、close 场景通过。
- [ ] **Step 7: 提交**：`git add src/mycode/team/worker.py src/mycode/cli.py src/mycode/session.py tests/test_session.py tests/test_hook_session_cli.py tests/test_team_e2e.py && git commit -m "feat: wire persistent teams into CLI and session"`。

### Task 15: 增加配置示例、文档和公开导出

**Files:**
- Modify: `src/mycode/team/__init__.py`
- Create: `examples/mycode.team.yaml`
- Modify: `README.md`
- Test: `tests/test_docs.py`

**Dependencies:** Task 1, Task 10, Task 14

- [ ] **Step 1: 写失败文档测试**：检查 README 和示例包含团队目录、`max_members=16`、`max_active_members=4`、后端 auto 顺序、`MYCODE_COORDINATOR=1`、消息协议、恢复、归档和不可 push 规则。
- [ ] **Step 2: 运行测试确认失败**：运行 `python -m pytest tests/test_docs.py -q -k team`，期望缺少 Stage 14 文档段落或示例。
- [ ] **Step 3: 编写示例**：在 `examples/mycode.team.yaml` 写完整 `team` 配置、锁/邮箱/上下文上限和 coordinator capability，明确环境变量仍需单独设置。
- [ ] **Step 4: 更新 README**：记录 Team 主入口动作、成员后端、任务状态/依赖、结构化消息、审批、`/clear`、接管、批次合并和 hard push 禁令；不记录完整邮箱正文或凭据。
- [ ] **Step 5: 更新导出并运行测试**：补齐 `team.__all__`，运行 `python -m pytest tests/test_docs.py -q -k team`，期望文档和 package 导出测试通过。
- [ ] **Step 6: 提交**：`git add src/mycode/team/__init__.py examples/mycode.team.yaml README.md tests/test_docs.py && git commit -m "docs: document stage 14 team workflows"`。

### Task 16: 完成端到端和全量回归验收

**Files:**
- Modify: `tests/test_team_e2e.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_agent_loop.py`
- Modify: `tests/test_permission_command.py`
- Modify: `tests/test_permission_policy.py`
- Modify: `tests/test_subagent_e2e.py`
- Modify: `tests/test_worktree_e2e.py`

**Dependencies:** Task 14, Task 15

- [ ] **Step 1: 写失败的完整场景**：使用 fake LLM 和 fake backends 建立两名成员，完成“Lead 创建批次 → DAG 任务 → 计划审批 → 成员提交 → idle 通知 → mailbox 唤醒恢复 → 本地原子合并”的完整路径；另建脏目标、恢复失败、锁占用和冲突无法解决场景。
- [ ] **Step 2: 运行团队专项测试**：运行 `python -m pytest tests/test_team_*.py -q`，期望所有新场景通过；失败先按错误码修复再继续。
- [ ] **Step 3: 运行跨领域回归**：运行 `python -m pytest tests/test_agent_loop.py tests/test_session.py tests/test_config.py tests/test_permission_command.py tests/test_permission_policy.py tests/test_subagent_e2e.py tests/test_worktree_e2e.py -q`，期望零失败。
- [ ] **Step 4: 运行全量测试**：运行 `python -m pytest tests -q`，期望项目全量测试通过。
- [ ] **Step 5: 执行静态检查**：运行 `python -m compileall src` 和 `git diff --check`，期望无编译错误、无空白错误；检查日志中没有凭据、环境变量或无界 Git 输出。
- [ ] **Step 6: 提交验收修复**：运行 `git status --short` 核对范围后，使用 `git add tests/test_team_e2e.py tests/test_config.py tests/test_agent_loop.py tests/test_permission_command.py tests/test_permission_policy.py tests/test_subagent_e2e.py tests/test_worktree_e2e.py && git commit -m "test: verify stage 14 team workflows"`；如专项修复改动了其他 Stage 14 文件，先把该文件补入这条显式命令，不得使用 `git add .`，也不得带入用户已有修改。

## 执行顺序

```text
Task 1 → Task 2 → Task 3 ─┐
             ├→ Task 4 ───┼→ Task 7 ─┐
             └→ Task 5 ───┘          │
Task 1 → Task 6 ─────────────────────┼→ Task 9 → Task 10 → Task 11 ─┐
Task 1 → Task 8 ─────────────────────┘          └→ Task 12 → Task 13 ┼→ Task 14
Task 14 → Task 15 → Task 16
```

每个 Task 完成后必须先运行其验证命令，再提交；未通过的测试不能标记任务完成，也不能开始依赖任务。

## 计划自检

- Spec F1-F15 已在“Spec 覆盖”表逐条映射；N1-N14 由 Task 2/5/8/11/12/13/14/15/16 覆盖。
- 无占位内容或模糊的跨任务引用；所有接口名称在核心接口章节和任务步骤中一致。
- Team 逻辑全部位于 `src/mycode/team/`；现有 AgentLoop、Permission、Worktree、Session 和 CLI 仅增加明确接入点。
- 所有代码任务包含失败测试、失败确认、最小实现、通过验证和提交步骤。
