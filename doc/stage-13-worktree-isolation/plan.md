# myCode Stage 13：子 Agent Worktree 隔离技术设计

## 架构概览

本阶段增加独立的 `workspace` 与 `worktree` 领域。`workspace` 只定义运行时通用的工作区身份、上下文和租约；`worktree` 集中负责仓库识别、安全路径、Git 调用、元数据、环境初始化、变更保护、生命周期和过期清理。子 Agent、工具、Hook、Skill、Prompt、Memory 和 MCP 不直接实现 Git 生命周期，只消费显式传入的 `WorkspaceContext`。

定义式子 Agent 的启动改为两阶段：先由任务管理器预留系统任务标识，再捕获主仓库已提交的 `HEAD`、准备隔离环境，最后绑定运行时并进入调度。未声明隔离的定义式角色和 Fork 继续使用共享 `WorkspaceContext`，不经过 Worktree 管理器。隔离准备失败时，已预留任务进入失败终态，不启动模型运行时。

Worktree 使用 `.worktrees/<role>/<system-task-token>`，临时分支使用 `mycode/worktree/<role>/<system-task-token>`。首次创建通过 Git 原生 Worktree 完成并执行全部必需初始化规则；目标已存在时进入严格只读的快速恢复分支。任务终态与后台清理复用同一套保护检查和删除入口，任何无法确认的路径、身份或 Git 状态都按受保护处理。

运行期间不调用 `chdir`。主 Agent、共享子 Agent和隔离子 Agent 都持有不可变工作区上下文，工具调度、权限、Hook、Skill、项目指令、Prompt 和 Memory 使用其中的规范化绝对路径。现有已经使用绝对路径的缓存保持原结构，不增加切换时清缓存逻辑。

### 需求归属

| Spec | 设计归属 |
|---|---|
| F1 | 角色模型、角色加载器、`SubAgentIsolationCoordinator` |
| F2 | `WorkspaceTaskIdentity`、确定性目录与临时分支、基线捕获 |
| F3 | `WorktreePathPolicy` 的安全段和 Git 引用校验 |
| F4 | Worktree 根启动校验、真实路径复核和失败关闭 |
| F5 | Git 网关与管理器的首次创建流程 |
| F6 | sidecar、有限目录结构与只读快速恢复分支 |
| F7 | 配置加载器、规则模型、路径和资源上限校验 |
| F8 | 初始化器的四类规则与 hooks 运行时覆盖 |
| F9 | 分阶段元数据、初始化错误和安全回滚 |
| F10 | `WorkspaceContext`、不可变租约和显式进入语义 |
| F11 | AgentLoop、工具调用上下文、权限、Hook 和 Skill 接入 |
| F12 | 文件缓存、Prompt、项目指令和 Memory 的绝对路径身份 |
| F13 | `subagent.context` 的路径提示与目标项目指令重载 |
| F14 | 变更保护检查器和统一终态释放流程 |
| F15 | 生命周期管理器的删除、保留和处置结果 |
| F16 | 后台清理器、活动任务查询和批量安全过滤 |
| F17 | 按任务身份和真实路径建立的异步锁注册表 |
| F18 | 子 Agent 任务快照、父 Agent 工具、后台通知、任务列表和详情展示 |

## 核心数据结构

以下类型使用 Python 3.10 的冻结 `dataclass` 和字符串枚举。路径字段进入模型前必须转换为规范化绝对 `Path`；集合字段使用不可变元组，避免跨并发任务被修改。

### WorkspaceKind

```python
class WorkspaceKind(str, Enum):
    SHARED = "shared"
    WORKTREE = "worktree"
```

表示运行时工作区来源。它是通用运行时概念，不依赖子 Agent 角色模型。

### WorkspaceTaskIdentity

```python
@dataclass(frozen=True)
class WorkspaceTaskIdentity:
    repository_id: str
    task_id: str
    role_name: str
    task_token: str
    relative_name: str
    branch_name: str
    base_commit: str
```

- `repository_id`：启动时根据规范化仓库根目录和 Git common directory 生成的稳定指纹。
- `task_id`：任务管理器预留的外部可见任务标识。
- `task_token`：经过安全校验、用于目录和 Git 引用的系统令牌。
- `relative_name`：固定为 `<role>/<system-task-token>`。
- `branch_name`：固定为 `mycode/worktree/<role>/<system-task-token>`。
- `base_commit`：准备任务时捕获并验证为 commit 的 `HEAD` OID。

该类型放在 `workspace.models`，防止 `worktree` 与 `subagent` 互相依赖。

### WorkspaceContext

```python
@dataclass(frozen=True)
class WorkspaceContext:
    kind: WorkspaceKind
    root: Path
    repository_root: Path
    repository_id: str
    task_identity: WorkspaceTaskIdentity | None
    branch_name: str | None
    hooks_path: Path | None
```

`root` 是所有路径相关组件唯一可用的当前工作目录。共享模式不携带任务身份和临时分支；Worktree 模式必须携带二者。`hooks_path` 只在初始化规则声明独立 hooks 时存在。

### WorkspaceLease

```python
@dataclass(frozen=True)
class WorkspaceLease:
    context: WorkspaceContext
    preparation: WorkspacePreparation
    metadata_path: Path | None
    initialized_rules: tuple[str, ...]
```

租约表示工作区已准备完成并可交给运行时。它只保存身份和结果，不在析构函数中执行清理；释放必须显式交还协调器或 Worktree 管理器，确保成功、失败和取消走同一条可观测流程。共享模式由协调器产生 `SHARED` 租约，`metadata_path` 为空。

### WorkspacePreparation

```python
class WorkspacePreparation(str, Enum):
    SHARED = "shared"
    CREATED = "created"
    RECOVERED = "recovered"
```

用于任务状态展示，并区分首次创建与快速恢复。

### AgentIsolationMode

```python
class AgentIsolationMode(str, Enum):
    SHARED = "shared"
    WORKTREE = "worktree"
```

`AgentRoleMetadata.isolation` 使用该枚举，缺省为 `SHARED`。仅 `SubAgentKind.DEFINED` 读取角色声明；Fork 请求不会解析或继承该字段。

### ToolWorkspaceScope 与 ToolInvocationContext

```python
class ToolWorkspaceScope(str, Enum):
    WORKSPACE_AWARE = "workspace_aware"
    SHARED_ONLY = "shared_only"

@dataclass(frozen=True)
class ToolInvocationContext:
    workspace: WorkspaceContext
```

`ToolDefinition.workspace_scope` 缺省为 `SHARED_ONLY`，避免未知工具在隔离模式中被误认为安全。默认文件、命令和任务本地工具显式声明 `WORKSPACE_AWARE`；MCP 工具保持 `SHARED_ONLY`。该字段是本地调度元信息，不进入供应商 tool schema。

### WorktreeInitRule 与 WorktreeConfig

```python
class WorktreeRuleType(str, Enum):
    COPY = "copy"
    IGNORED_COPY = "ignored_copy"
    SYMLINK = "symlink"
    HOOKS = "hooks"

@dataclass(frozen=True)
class WorktreeInitRule:
    type: WorktreeRuleType
    source: str
    target: str

@dataclass(frozen=True)
class WorktreeConfig:
    rules: tuple[WorktreeInitRule, ...]
    git_timeout_seconds: float = 30.0
    cleanup_interval_seconds: float = 3600.0
    expire_after_seconds: float = 604800.0
    scan_batch_size: int = 64
    digest: str = ""
```

配置文件固定为 `<repo>/.mycode/worktree.yaml`，顶层版本为 `1`。所有规则都是必需步骤并按声明顺序执行：`copy` 复制明确来源；`ignored_copy` 额外要求来源和目标符合 Git 忽略语义；`symlink` 只创建符号链接，不降级为复制；`hooks` 把来源目录复制到目标位置并将目标登记为该工作区的 hooks 路径。规则最多 128 条，单个路径文本最多 512 字符，所有来源位于主仓库根目录内，所有目标是 Worktree 内的安全相对路径，任意两个规则不得写入相同或祖先/后代冲突的目标。

`digest` 是规范化有效配置的稳定摘要，写入元数据并参与恢复校验。缺少配置文件等价于版本为 `1`、规则为空、其余字段使用默认值；文件存在但非法时应用启动失败。

### WorktreeMetadata

```python
class WorktreePhase(str, Enum):
    CREATING = "creating"
    READY = "ready"
    RETAINED = "retained"

@dataclass(frozen=True)
class WorktreeMetadata:
    schema_version: int
    phase: WorktreePhase
    repository_id: str
    identity: WorkspaceTaskIdentity
    workspace_root: Path
    config_digest: str
    created_at: datetime
    last_active_at: datetime
    initialized_rules: tuple[str, ...]
    retained_reasons: tuple[str, ...]
```

元数据位于 `.worktrees/.metadata/<role>/<system-task-token>.json`，不放进 Git Worktree。单份文件上限 64 KiB；未知版本、未知字段、重复字段、类型错误或路径不匹配都拒绝恢复。首次创建先写 `CREATING`，全部初始化成功后通过同目录临时文件和原子替换写为 `READY`。快速恢复只接受完整、配置摘要一致的 `READY`；`RETAINED` 服务于诊断和后台清理，不可直接进入运行时。

### WorktreeProtectionStatus

```python
@dataclass(frozen=True)
class WorktreeProtectionStatus:
    has_uncommitted_changes: bool
    has_unpushed_commits: bool
    branch_tip: str | None
    upstream: str | None
    reasons: tuple[str, ...]
```

暂存、未暂存和非忽略未跟踪文件都令 `has_uncommitted_changes` 为真。有 upstream 时，当前分支存在 upstream 未包含的提交即为未推送；无 upstream 时，tip 与创建基线不同即为未推送。任何状态读取或解析失败不构造“干净”结果，而是抛出有界领域错误，由调用方按受保护处理。

### WorktreeDispositionResult

```python
class WorktreeDisposition(str, Enum):
    DELETED = "deleted"
    RETAINED = "retained"
    SKIPPED = "skipped"
    FAILED = "failed"

@dataclass(frozen=True)
class WorktreeDispositionResult:
    disposition: WorktreeDisposition
    workspace_root: Path
    branch_name: str
    reasons: tuple[str, ...]
```

该结果写回子 Agent 任务记录，供列表、详情和通知使用。任务执行成功、失败或取消不直接决定处置结果。

### WorktreeError

```python
class WorktreeError(RuntimeError):
    code: str
    phase: str
    message: str
    path: Path | None
    branch_name: str | None
    git_exit_code: int | None
```

所有面向调用方的失败使用稳定 `code` 和中文 `message`。错误只保留有界摘要，不携带复制文件正文、环境变量值、凭据或完整 Git 输出。

### 适配器与批处理结果

```python
@dataclass(frozen=True)
class RepositoryIdentity:
    root: Path
    common_dir: Path
    repository_id: str

@dataclass(frozen=True)
class GitWorktreeEntry:
    path: Path
    head: str
    branch: str | None
    locked: bool
    prunable: bool

@dataclass(frozen=True)
class GitStatus:
    has_staged_changes: bool
    has_unstaged_changes: bool
    untracked_paths: tuple[str, ...]

@dataclass(frozen=True)
class InitializationResult:
    completed_rules: tuple[str, ...]
    hooks_path: Path | None

@dataclass(frozen=True)
class WorktreeDiagnostic:
    code: str
    phase: str
    message: str
    path: Path | None
    branch_name: str | None

@dataclass(frozen=True)
class CleanupBatchResult:
    scanned: int
    deleted: int
    retained: int
    skipped: int
    failed: int
    has_more: bool
    diagnostics: tuple[WorktreeDiagnostic, ...]
```

这些模型位于 `worktree.models`。时间戳统一使用带 UTC 时区的 `datetime`，序列化为固定 ISO 8601 文本；Git OID、分支和相对路径在进入模型前完成格式验证。

## 核心接口

### WorktreeConfigLoader

```python
class WorktreeConfigLoader:
    def load(self, repository_root: Path) -> WorktreeConfig: ...
```

在应用启动装配阶段读取并严格校验项目配置，返回已经规范化且带摘要的不可变配置。

### WorktreePathPolicy

```python
class WorktreePathPolicy:
    def validate_relative_name(self, value: str) -> str: ...
    def validate_branch_name(self, value: str) -> str: ...
    def validate_root(self, repository_root: Path) -> Path: ...
    def resolve_target(self, relative_name: str) -> Path: ...
    def assert_target_boundary(self, target: Path) -> Path: ...
    def resolve_rule_source(self, source: str) -> Path: ...
    def resolve_rule_target(self, workspace_root: Path, target: str) -> Path: ...
```

名称检查先于路径拼接。目录段只接受规定的 ASCII 字符和长度，统一拒绝空段、`.`、`..`、反斜杠、绝对路径、盘符、控制字符和 Windows 保留名。边界判断基于规范化绝对真实路径以及平台大小写语义，并在每个创建、恢复、初始化和删除动作前重新执行。

### GitWorktreeGateway

```python
class GitWorktreeGateway:
    def identify_repository(self, repository_root: Path) -> RepositoryIdentity: ...
    def validate_ignored_root(self, worktrees_root: Path) -> None: ...
    def capture_head(self, repository_root: Path) -> str: ...
    def add(self, identity: WorkspaceTaskIdentity, target: Path) -> None: ...
    def list_porcelain(self, repository_root: Path) -> tuple[GitWorktreeEntry, ...]: ...
    def status(self, target: Path) -> GitStatus: ...
    def upstream(self, target: Path) -> str | None: ...
    def commits_not_in_upstream(self, target: Path, upstream: str) -> tuple[str, ...]: ...
    def remove(self, repository_root: Path, target: Path) -> None: ...
    def delete_branch(self, repository_root: Path, branch: str) -> None: ...
```

网关统一使用参数数组、`shell=False` 和显式 `cwd`。默认超时 30 秒，配置范围 1–120 秒；stdout 与 stderr 分别截断到 64 KiB。Worktree 身份使用稳定的 `git worktree list --porcelain -z` 解析，创建使用 `git worktree add -b <branch> <path> <base-commit>`，删除 Worktree 使用非 force 形式。临时分支只在保护检查通过、Worktree 已移除且名称仍处于受控前缀时删除。

### WorktreeMetadataStore

```python
class WorktreeMetadataStore:
    def read_ready(
        self,
        identity: WorkspaceTaskIdentity,
        target: Path,
        config_digest: str,
    ) -> WorktreeMetadata: ...
    def read_candidate(self, metadata_path: Path) -> WorktreeMetadata: ...
    def write(self, metadata: WorktreeMetadata) -> Path: ...
    def remove(self, identity: WorkspaceTaskIdentity) -> None: ...
    def scan(self, limit: int) -> tuple[Path, ...]: ...
```

`read_ready` 是快速恢复的唯一元数据入口，只接受身份匹配的 `READY` 并执行有界文件读取和结构校验。`read_candidate` 供清理器读取 `CREATING`、`READY` 或 `RETAINED` 候选，但不表示候选可以恢复或删除。`scan` 只枚举管理区 sidecar，不遍历仓库；候选按规范化相对名称排序，保证确定性。

### WorktreeInitializer

```python
class WorktreeInitializer:
    def initialize(
        self,
        identity: WorkspaceTaskIdentity,
        workspace_root: Path,
        config: WorktreeConfig,
    ) -> InitializationResult: ...
```

初始化器逐条执行已经校验的规则，每次动作前重新验证来源、目标及所有现有祖先节点的真实路径。目标已存在、源类型不符、符号链接循环或平台拒绝符号链接时立即失败，不跳过、不覆盖、不降级。它返回完成的规则标识和可选 hooks 目标，不直接启动 Agent。

### WorktreeProtectionInspector

```python
class WorktreeProtectionInspector:
    def inspect(self, lease: WorkspaceLease) -> WorktreeProtectionStatus: ...
```

检查器解析 NUL 分隔的 porcelain v2 状态。upstream 判断只使用本地跟踪引用，不执行 `fetch` 或任何网络操作。无 upstream 时以租约中的 `base_commit` 判断新增提交。

### WorktreeManager

```python
class WorktreeManager:
    async def prepare(self, identity: WorkspaceTaskIdentity) -> WorkspaceLease: ...
    async def release(self, lease: WorkspaceLease) -> WorktreeDispositionResult: ...
    async def inspect_and_dispose(
        self,
        metadata: WorktreeMetadata,
        *,
        require_expired: bool,
    ) -> WorktreeDispositionResult: ...
```

`prepare` 先按仓库、任务身份和目标真实路径获取进程内异步锁。目标不存在时执行创建状态落盘、Git 创建、初始化和 `READY` 落盘；目标存在时只调用路径策略和元数据存储的只读接口，不调用 Git、初始化器或任何写入接口。`release` 与清理器都复用 `inspect_and_dispose`，保证保护语义和删除顺序一致。

### WorktreeCleaner

```python
class WorktreeCleaner:
    async def start(self) -> None: ...
    async def run_batch(self) -> CleanupBatchResult: ...
    async def close(self) -> None: ...
```

`start` 创建后台协程并立即执行首批扫描，之后按配置间隔运行。每批最多处理 64 个按稳定顺序选出的 sidecar；过滤顺序固定为身份与真实路径、过期且无活动任务、无受保护变更。每个候选的失败只产生诊断，不终止本批其他候选。`close` 取消并等待后台协程，不遗留清理任务。

### SubAgentIsolationCoordinator

```python
class SubAgentIsolationCoordinator:
    async def prepare(
        self,
        role: AgentRoleDefinition | None,
        identity: WorkspaceTaskIdentity | None,
    ) -> WorkspaceLease: ...
    async def release(self, lease: WorkspaceLease) -> WorktreeDispositionResult | None: ...
```

协调器是子 Agent 对工作区领域的唯一入口。共享角色和 Fork 传入空身份并返回主工作区租约，不捕获 Git 基线；Worktree 角色必须传入完整身份并委托管理器。它不负责模型运行、合并、提交或推送。

### SubAgentTaskManager 扩展

```python
async def reserve(request: SubAgentLaunchRequest) -> SubAgentTaskSnapshot: ...
async def bind_workspace(task_id: str, lease: WorkspaceLease) -> SubAgentTaskSnapshot: ...
async def start_reserved(task_id: str, runner: SubAgentRunner) -> SubAgentTaskSnapshot: ...
async def fail_reserved(task_id: str, error_code: str, error_message: str) -> SubAgentTaskSnapshot: ...
def is_workspace_active(identity: WorkspaceTaskIdentity) -> bool: ...
```

预留记录先获得稳定任务标识，再绑定工作区。只有完成绑定且存在 runner 的记录可以从队列进入运行态。活动查询供后台清理第二层过滤使用；任务完成释放租约后才撤销活动登记并写入处置结果。

### AgentLoop 与工具调度扩展

```python
class AgentLoop:
    def __init__(..., workspace: WorkspaceContext, ...) -> None: ...

class ToolExecutor:
    async def execute(
        self,
        call: ToolCall,
        context: ToolInvocationContext,
    ) -> ToolResult: ...
```

`AgentLoop` 不再读取 `Path.cwd()`，所有 Prompt、Hook 事件、工具调用、上下文归档和 Memory 操作都从 `workspace.root` 派生。执行器在 Worktree 模式拒绝 `SHARED_ONLY` 工具；任务工具工厂使用同一工作区上下文构造权限服务、文件工具、命令工具、Hook 和 Skill 运行时，并校验绑定根目录与调用上下文一致。

## 模块设计

### `workspace.models`

**职责：** 定义不依赖子 Agent 或 Git 实现的工作区公共模型。

**对外接口：** `WorkspaceKind`、`WorkspaceTaskIdentity`、`WorkspaceContext`、`WorkspaceLease`、`WorkspacePreparation`。

**依赖：** 仅 Python 标准库。`worktree`、`subagent`、`agent` 和 `tool` 可以依赖它，它不反向依赖这些包。

### `worktree.config`

**职责：** 读取 `.mycode/worktree.yaml`，验证版本、字段、类型、资源上限、规则路径文本、目标冲突和时间范围，生成配置摘要。

**对外接口：** `WorktreeConfigLoader.load()`。

**依赖：** `worktree.models`、`worktree.pathing`、PyYAML。不得执行初始化或延迟非法配置到任务运行期。

### `worktree.pathing`

**职责：** 安全名称、受控 Git 引用、Worktree 根目录、元数据路径、规则来源和规则目标的统一验证。

**对外接口：** `WorktreePathPolicy`。

**依赖：** `workspace.models` 和标准库路径 API。不得调用 Git。

### `worktree.git`

**职责：** 封装所有 Git 子进程、结构化参数、超时、输出截断、退出码和机器格式解析。

**对外接口：** `GitWorktreeGateway` 及只读结果模型。

**依赖：** `workspace.models`、`worktree.models`、`subprocess`。不得依赖子 Agent、工具或 UI。

### `worktree.metadata`

**职责：** 有界读取、严格 JSON 解码、阶段元数据原子落盘、管理区扫描和安全删除 sidecar。

**对外接口：** `WorktreeMetadataStore`。

**依赖：** `workspace.models`、`worktree.models`、`worktree.pathing`。快速恢复只通过其只读入口读取元数据。

### `worktree.initializer`

**职责：** 按声明顺序执行 `copy`、`ignored_copy`、`symlink` 和 `hooks`，返回初始化结果。

**对外接口：** `WorktreeInitializer.initialize()`。

**依赖：** `worktree.models`、`worktree.pathing`、`worktree.git`。Git 依赖只用于首次创建阶段验证忽略语义，不参与快速恢复。

### `worktree.protection`

**职责：** 检测未提交修改、upstream 和未推送提交，并生成确定顺序的中文保护原因。

**对外接口：** `WorktreeProtectionInspector.inspect()`。

**依赖：** `workspace.models`、`worktree.models`、`worktree.git`。

### `worktree.manager`

**职责：** 生命周期编排、按 key 串行化、首次创建、快速恢复、回滚、释放和统一处置。

**对外接口：** `prepare()`、`release()`、`inspect_and_dispose()`。

**依赖：** Worktree 领域内其他模块和 `workspace.models`。它是唯一组合 Git 创建、初始化、保护和删除的组件。

### `worktree.cleaner`

**职责：** 启动与周期扫描、批量限制、活动任务过滤和候选诊断。

**对外接口：** `start()`、`run_batch()`、`close()`。

**依赖：** `worktree.metadata`、`worktree.manager` 和只读活动任务协议，不依赖具体子 Agent 任务管理器类型。

### `subagent.isolation`

**职责：** 将角色隔离声明和通用任务身份转换为共享或 Worktree 租约。

**对外接口：** `SubAgentIsolationCoordinator`。

**依赖：** `workspace.models`、Worktree 管理器协议、子 Agent 角色模型。Worktree 领域不依赖本模块。

### 子 Agent 现有模块

`subagent.models` 增加隔离声明和任务工作区状态；`subagent.loader` 解析 frontmatter；`subagent.tasks` 负责预留、绑定、活动登记和处置结果；`subagent.service` 执行两阶段准备；`subagent.runtime` 在 `finally` 路径释放租约；`subagent.tooling` 按工作区构造任务工具；`subagent.context` 注入路径、分支和隔离约束并从目标目录重载项目指令；`subagent.tool` 和 `subagent.notifications` 把同一组工作区与处置字段传给父 Agent 的 `Agent(action=list/get)` 结果和后台通知。公共入口只转出已定义的隔离类型，不复制领域逻辑。

### 路径消费者

`agent.loop`、`tool`、`hook`、`skill.executor` 和 `mcp.tools` 只消费 `WorkspaceContext` 或 `ToolInvocationContext`。文件工具与命令工具在创建时绑定 `workspace.root`，执行时再次校验调用上下文；Hook action 的相对 `cwd`、Skill 资源路径、权限服务以及 Prompt/Memory 根目录都从同一上下文派生。MCP 默认 `SHARED_ONLY`，在 Worktree 模式从可见工具集合中移除。

### 启动与展示

`cli.py` 装配仓库身份、配置、路径策略、Git 网关、管理器、协调器和清理器，并在 TUI 生命周期结束时关闭清理器。`slash.builtins`、`subagent.tool` 和 `subagent.notifications` 在现有状态出口展示隔离模式、绝对路径、临时分支、创建或恢复、初始化结果、删除或保留原因，不增加新命令或新 Agent action。`README.md` 与示例文件说明配置和安全语义。

## 模块交互

### 应用启动

1. CLI 解析主工作区绝对路径，不修改进程当前目录。
2. Git 网关识别仓库根目录与 common directory，生成 `repository_id`。
3. 配置加载器读取并验证 `.mycode/worktree.yaml`。
4. 路径策略确认 `.worktrees/` 的规范化位置在仓库内，Git 网关确认该位置已被忽略；失败时应用启动失败，不自动修改 `.gitignore`。
5. CLI 构造 Worktree 管理器、隔离协调器和任务服务。
6. 清理器启动后台协程并执行首批扫描，普通聊天初始化不等待后续批次。

### 隔离任务准备

1. `SubAgentService` 解析定义式角色；Fork 和共享角色直接选择共享模式。
2. 任务管理器预留 `task_id`，但不创建 runner，也不进入运行态。
3. 只有 Worktree 角色才由 Git 网关从主仓库捕获已提交 `HEAD` 并构造 `WorkspaceTaskIdentity`；共享角色和 Fork 不增加 Git 调用。
4. 隔离协调器调用管理器 `prepare()`；管理器按身份和目标真实路径加锁。
5. 目标不存在时，管理器写入 `CREATING` 元数据，调用 Git 创建 Worktree，执行初始化规则，复核边界并原子写入 `READY`。
6. 目标存在时，管理器只读取管理区元数据、目标目录结构和 `.git` 指针，校验仓库、任务、路径、分支、基线、配置摘要和 `READY` 状态；该分支不得调用 Git、写文件、重新初始化或遍历仓库。
7. 协调器返回不可变租约；任务管理器绑定租约，服务据此创建 runtime 和 runner，再调用 `start_reserved()`。
8. 任一步失败时调用 `fail_reserved()`；仅本次新建且经保护检查证明安全的资源可以回滚，既有恢复候选保持不变。

### 运行时路径传播

1. Runtime 把租约中的 `WorkspaceContext` 传给 `AgentLoop`、工具工厂、权限服务、Hook、Skill、Prompt 和 Memory。
2. AgentLoop 为每次工具调用创建 `ToolInvocationContext`；执行器核对工具工作区能力并显式传递上下文。
3. 文件、命令、Hook 和 Skill 使用 `workspace.root`，命令调用通过环境覆盖设置 Worktree 专属 `core.hooksPath`。
4. 项目指令和 Prompt 从 Worktree 重新加载；文件缓存与项目 Memory 继续以规范化绝对路径作为 key。
5. 任一组件不能接收或验证工作区上下文时，不得回退主目录；该工具在隔离模式隐藏或调用失败关闭。

### 任务结束

1. 成功、失败或取消都先结束模型流、工具任务和运行时资源。
2. Runtime 的统一 `finally` 路径把租约交还隔离协调器。
3. Worktree 管理器在锁内读取工作区状态和提交保护状态。
4. 存在未提交修改、未推送提交或无法确认状态时，元数据写为 `RETAINED`，保留目录与分支。
5. 工作区干净且没有未推送提交时，先用非 force Git 操作移除 Worktree，再删除仍处于受控前缀且身份匹配的临时分支，最后删除 sidecar。
6. 任务管理器撤销活动登记并写入 `WorktreeDispositionResult`；任务结果与处置结果分别保留。
7. 任务列表、详情和后台通知展示绝对路径、分支以及删除或保留原因。

### 后台清理

1. 元数据存储按稳定顺序选取最多 64 个 sidecar。
2. 第一层验证 schema、仓库身份、任务身份、受控名称、真实路径、`.git` 指针和 Worktree 根边界。
3. 第二层验证 `last_active_at` 已超过过期线，并通过只读活动任务协议确认没有对应任务。
4. 第三层调用统一保护检查器，确认没有未提交修改或未推送提交。
5. 三层全部通过后调用管理器的统一处置入口；创建、恢复、退出和清理共享同一锁 key。
6. 任一层失败即跳过候选并记录有界中文诊断，后续候选继续；达到批量上限后让出事件循环，下一批再继续。

## 文件组织

### 新增生产文件

```text
src/mycode/
├── workspace/
│   ├── __init__.py          — 导出通用工作区类型
│   └── models.py            — WorkspaceTaskIdentity、WorkspaceContext、WorkspaceLease
├── worktree/
│   ├── __init__.py          — Worktree 领域公开接口
│   ├── models.py            — 配置、元数据、保护状态和处置结果
│   ├── config.py            — 加载并校验项目配置
│   ├── pathing.py           — 安全名称、真实路径和边界检查
│   ├── git.py               — Git 子进程适配器和机器格式解析
│   ├── metadata.py          — sidecar 有界读取、原子写入和扫描
│   ├── initializer.py       — copy、ignored_copy、symlink、hooks
│   ├── protection.py        — 未提交修改和未推送提交检测
│   ├── manager.py           — 创建、恢复、释放、删除和串行化
│   └── cleaner.py           — 启动扫描、周期调度和三层过滤
└── subagent/
    └── isolation.py         — 共享模式与 Worktree 模式协调入口
```

### 修改生产文件

```text
src/mycode/
├── agent/loop.py            — 注入 WorkspaceContext，消除 Path.cwd() 依赖
├── subagent/
│   ├── __init__.py          — 导出新增隔离与任务状态类型
│   ├── models.py            — 角色隔离声明和任务工作区状态
│   ├── loader.py            — 解析 isolation frontmatter
│   ├── tasks.py             — 预留身份、绑定租约、活动登记和处置结果
│   ├── service.py           — 两阶段准备与调度
│   ├── runtime.py           — 按租约构造并统一释放运行时
│   ├── tooling.py           — 按工作区构造和过滤工具
│   ├── context.py           — 路径提示与目标项目指令
│   ├── tool.py              — Agent list/get 工作区状态序列化
│   └── notifications.py     — 后台通知中的处置结果
├── tool/
│   ├── __init__.py          — 导出工具工作区能力和调用上下文
│   ├── base.py              — 工具工作区能力和调用上下文
│   ├── executor.py          — 每次调用显式传播上下文
│   ├── defaults.py          — 按目标工作区创建默认工具
│   ├── filesystem.py        — 文件路径绑定和上下文一致性
│   └── command.py           — 显式 cwd 与 hooks 环境覆盖
├── hook/
│   ├── runtime.py           — 按调用上下文选择工作区
│   └── actions.py           — 基于目标工作区解析 action cwd
├── skill/executor.py        — 在当前任务工作区执行 Skill
├── mcp/tools.py             — MCP 工作区兼容策略
├── slash/builtins.py        — 任务隔离和处置状态展示
└── cli.py                   — 启动装配与清理器生命周期

examples/mycode.worktree.yaml — 完整项目配置示例
README.md                     — 配置、角色声明和生命周期说明
```

`tool/cache.py` 已按解析后的绝对文件路径建 key，`memory/paths.py` 已按解析后的绝对工作区根目录生成项目身份，因此不修改生产代码，只用回归测试确认不同 Worktree 不共享条目。

### 测试文件

```text
tests/
├── helpers.py
├── worktree_helpers.py
├── test_workspace_models.py
├── test_worktree_models.py
├── test_worktree_config.py
├── test_worktree_pathing.py
├── test_worktree_git.py
├── test_worktree_metadata.py
├── test_worktree_initializer.py
├── test_worktree_protection.py
├── test_worktree_manager.py
├── test_worktree_cleaner.py
├── test_worktree_e2e.py
├── test_subagent_isolation.py
├── test_subagent_models.py
├── test_subagent_loader.py
├── test_subagent_notifications.py
├── test_subagent_tasks.py
├── test_subagent_service.py
├── test_subagent_runtime.py
├── test_subagent_tooling.py
├── test_subagent_context.py
├── test_subagent_tool.py
├── test_subagent_agent.py
├── test_subagent_e2e.py
├── test_subagent_session_tui.py
├── test_agent_loop.py
├── test_agent_plan_only.py
├── test_context_compaction_e2e.py
├── test_tool_executor.py
├── test_tool_filesystem.py
├── test_tool_command.py
├── test_tool_cache.py
├── test_tool_registry.py
├── test_hook_runtime.py
├── test_hook_actions.py
├── test_hook_agent.py
├── test_hook_session_cli.py
├── test_skill_executor.py
├── test_skill_agent.py
├── test_skill_e2e.py
├── test_mcp_tools.py
├── test_memory_instructions.py
├── test_memory_paths.py
├── test_permission_e2e.py
├── test_project_memory_e2e.py
├── test_slash_builtins.py
├── test_slash_snapshots.py
├── test_e2e_chat.py
└── test_docs.py
```

`worktree_helpers.py` 提供临时 Git 仓库、本地 bare remote、隔离的 Git 配置环境、受控文件系统、fake 时钟和 fake 调度器。新增测试集中验证 Worktree 领域；现有测试文件追加接入和兼容性回归。

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 领域边界 | `workspace` 保存通用模型，`worktree` 集中生命周期 | 避免子 Agent、工具和 Git 逻辑互相渗透 |
| 任务处理 | 预留身份、准备隔离、绑定运行时、进入调度 | Worktree 路径依赖任务标识，且准备失败不能启动 Agent |
| 目录与分支 | 确定性受控名称 | 可验证、可恢复、可按任务定位，不接受模型自由路径 |
| 创建基线 | 准备时捕获已提交 `HEAD` OID | 排除主工作区未提交内容和排队期间基线漂移 |
| Git Worktree | 原生 `worktree add -b` 与非 force `worktree remove` | 共享对象库，同时由 Git 维护工作目录注册关系 |
| Git 能力 | 启动时探测所需命令与机器格式，不按版本字符串分支 | 避免厂商版本字符串差异，缺少能力时稳定失败 |
| 元数据 | 管理区 sidecar、阶段字段、原子替换 | 不污染子 Worktree 状态，中断后能识别半成品 |
| 快速恢复 | 仅只读元数据和有限目录结构 | 保证无 Git、无写入、无仓库遍历的恢复路径 |
| 并发 | 规范化身份与真实路径组成 key 的 `asyncio.Lock` | 与现有单进程异步调度一致，防止重复生命周期动作 |
| hooks | 命令环境中的 `GIT_CONFIG_COUNT/KEY/VALUE` 覆盖 `core.hooksPath` | 不修改共享仓库配置，也不启用 `extensions.worktreeConfig` |
| 工具能力 | `WORKSPACE_AWARE` 显式允许，未知工具默认 `SHARED_ONLY` | 隔离模式失败关闭，不回退到主目录 |
| MCP | 默认 `SHARED_ONLY` | 现有 MCP 接口没有可靠的工作区契约，优先保证隔离 |
| 缓存 | 规范化绝对路径作为 key | 不需要切换时清缓存，天然区分相同相对路径 |
| upstream | 只读取本地跟踪引用，不自动 fetch | 不引入网络、认证、推送或远程状态变更 |
| 清理 | 启动一次、默认每小时、七天过期、每批 64 个 | 在资源回收与前台响应之间保持有界开销 |
| 删除 | 状态不确定即保留；不暴露强制删除入口 | 保护修改和提交优先于自动回收 |

### Git 命令边界

- 仓库身份、忽略状态、基线、创建、状态检查和删除全部通过 `GitWorktreeGateway`。
- 参数始终以字符串数组传给子进程，禁止 shell 拼接；每次调用使用显式 `cwd`。
- Worktree 列表和状态使用 NUL 分隔机器格式，不能解析本地化的人类输出。
- 默认超时 30 秒，项目仅能配置 1–120 秒；stdout、stderr 分别限制 64 KiB，用户诊断限制 4 KiB。
- hooks 使用单次命令环境覆盖，不执行 `git config --worktree`，不修改主仓库本地配置。
- 分支删除只针对与租约完全一致、处于 `mycode/worktree/` 前缀且已经通过保护检查的本地临时分支。

### 路径与文件系统边界

- 名称校验发生在路径拼接前；Git 引用只由校验后的段生成。
- Worktree 根必须是仓库内真实目录并已被 Git 忽略，应用不自动修复忽略规则。
- 每次实际创建、复制、链接、恢复、扫描或删除前重新解析目标及现有祖先，拒绝符号链接、目录联接、重解析点和越界替换。
- 初始化器不覆盖既有目标；删除 Worktree 交给 Git，sidecar 删除只针对再次验证位于管理区的精确文件。
- Windows 路径比较使用大小写不敏感语义并拒绝保留名、盘符和反斜杠；POSIX 同样执行统一的安全段规则。

### 失败处置

| 失败位置 | 处置 |
|---|---|
| 启动配置、仓库或根目录验证失败 | 应用启动失败，不启动清理器 |
| 名称、分支或规则路径非法 | 隔离任务不进入准备阶段 |
| Git 创建失败 | 返回有界诊断，只处理能证明属于本次操作的半成品 |
| 初始化失败 | 子 Agent 不启动；新建资源经保护检查后回滚 |
| 新建资源无法证明可安全回滚 | 写为 `RETAINED` 并保留，不猜测删除 |
| 快速恢复校验失败 | 既有目录与元数据保持不变，不调用 Git 修复 |
| Runtime 构造或启动失败 | 释放已取得租约并执行正常保护检查 |
| 状态或 upstream 无法确认 | 按受保护处理，拒绝自动删除 |
| Worktree 删除失败 | 保留分支和元数据，报告失败阶段和退出状态 |
| Worktree 已删但分支删除失败 | 保留分支及诊断，报告部分清理，不丢失提交 |
| 后台单候选失败 | 跳过该候选，继续同批其他候选 |
| 后台协程异常 | 记录有界诊断，调度循环继续后续周期 |

失败日志只包含任务身份、阶段、规范化路径、分支、Git 退出状态和保护原因。复制内容、环境变量值、凭据和完整 Git 输出不得进入错误、任务详情或日志。

## 测试设计

### 纯单元测试

- 参数化验证 ASCII 段、长度边界、空段、`.`、`..`、绝对路径、反斜杠、盘符、控制字符、Windows 保留名和大小写语义。
- 验证配置版本、未知字段、未知规则、128 条上限、512 字符路径上限、重复或祖先/后代目标冲突、时间范围和稳定摘要。
- 验证元数据 64 KiB 上限、schema、阶段、身份、配置摘要和路径不匹配全部失败关闭。
- 用决策表覆盖工作区修改、upstream、有无新增提交和 Git 状态不确定时的保护结果。

### Git 适配器测试

- 每个测试创建临时仓库并只写仓库本地 `user.name`、`user.email`；通过临时环境屏蔽系统和真实用户 Git 配置。
- 使用本地 bare repository 模拟 upstream，不访问网络、不使用真实凭据、不推送真实分支。
- 验证结构化参数、显式 `cwd`、超时、退出码、64 KiB 输出截断、porcelain NUL 解析和含特殊字符文件名。
- 验证 hooks 环境覆盖只影响子 Worktree 命令，主工作区 Git 配置和 hooks 行为保持不变。

### 领域集成测试

- 覆盖首次创建、四类初始化规则、`READY` 落盘、快速恢复、初始化失败回滚和恢复失败保留。
- 快速恢复安装一调用即失败的 Git 网关与写入适配器，证明该路径没有 Git 调用和写操作，也不遍历仓库。
- 覆盖暂存、未暂存、非忽略未跟踪、无 upstream 新提交、upstream 未包含提交和 upstream 已包含提交。
- 使用 fake 时钟和 fake 调度验证启动扫描、一小时周期、七天过期、配置值、64 个批量上限和后续批次。
- 并发调用同一身份和路径的创建、恢复、退出和清理，验证一个有效所有者、一个终态和至多一次删除。
- 参数化 Windows、Linux 和 macOS 路径规则；真实符号链接测试在平台支持时执行，不支持时验证初始化明确失败。

### 端到端与回归测试

- 主 Agent 启动两个隔离角色，验证各自目录、分支、文件、Prompt、项目指令、权限、Hook、Skill 和工具 cwd 相互独立，进程当前目录始终不变。
- 子 Agent 修改并提交后，验证主工作区不变且任务因未推送提交保留；无变更任务无论成功、失败还是取消都删除 Worktree 和分支。
- 验证 slash 任务列表与详情、父 Agent 的 `Agent(action=list/get)` 结果和后台通知都展示隔离模式、绝对路径、分支、创建或恢复、初始化和处置结果。
- 运行聊天、共享定义式角色、Fork、权限、Hook、Skill、Memory、MCP、上下文压缩、会话恢复、工具和子 Agent 现有回归测试，确认未声明隔离时 schema 和行为不变。
- 文档测试验证示例配置可解析、README 引用有效且不包含真实凭据。

## 依赖方向

```text
workspace.models
      ↑
      ├── worktree.models ← config / pathing / git / metadata
      │                          ↑
      │              initializer / protection
      │                          ↑
      │                       manager ← cleaner
      │                          ↑
      └── subagent.models ← subagent.isolation
                                  ↑
                       service / runtime / tasks
                                  ↑
                 agent / tool / hook / skill / cli
```

`WorktreeCleaner` 只依赖活动任务查询协议，`worktree` 包不导入 `subagent`。`workspace.models` 不导入任何业务包。UI 和 CLI 只负责装配与展示，不被领域模块反向依赖，因此依赖图无环。
