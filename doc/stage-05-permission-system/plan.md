# myCode Stage 05：纵深权限与安全检查技术设计

## 阶段标识

- 阶段：Stage 05
- 输入：已批准的 `spec.md`
- 输出：权限系统架构、Python 类型、模块交互、文件组织与技术决策

## 目标

在现有 Agent Loop 的工具执行前插入统一权限门面，集中处理安全底线、分层规则、权限档位、HITL 和 `plan-only`，同时将文件工具的路径守卫迁入 `permission` 包并保留执行期复检。

## 架构概览

CLI 在启动时以规范化工作区根目录和用户目录创建唯一 `PermissionService`。该服务持有已校验的规则来源、会话权限状态、纯策略对象、危险命令分析器和共享 `PathGuard`。Agent、Session 和文件工具共享同一服务或它暴露的叶子组件，不各自实现权限优先级。

工具调用的权限链固定为：

```text
工具注册中心校验工具存在
    ↓
参数契约校验与 PermissionSubject 规范化
    ↓
路径沙箱预检查 / 高置信度 FORBIDDEN 检查
    ↓
会话规则
    ↓ 未命中
本地用户项目规则
    ↓ 未命中
仓库项目限制规则
    ↓ 未命中
用户全局规则
    ↓ 未命中
内置高风险分类
    ↓ 未命中
权限档位兜底
    ↓
plan-only 追加约束
    ↓
ALLOW / DENY / ASK / FORBIDDEN
```

`FORBIDDEN`、路径越界和权限组件失败均不会进入工具执行器。`ASK` 由 Agent 通过现有事件流交给 TUI；审批的授权范围和持久化由 `PermissionService` 处理。通过权限检查的调用继续使用现有连续读并发、写工具单独串行的调度方式。

### 启动装配

```text
Path.cwd().resolve()
    ├── PermissionService.create(workspace_root, Path.home())
    │     ├── PermissionStore.load(...)
    │     ├── PermissionPolicy(...)
    │     ├── CommandAnalyzer(...)
    │     └── PathGuard(workspace_root)
    ├── create_default_tool_registry(workspace_root, path_guard=service.path_guard)
    ├── ToolExecutor(registry)
    ├── PermissionInterceptor(service)
    ├── AgentLoop(..., permission=interceptor)
    ├── ChatSession(agent=agent, permissions=service)
    └── ChatTUI(session=session)
```

权限配置错误与现有 LLM/协议配置错误一样在 CLI 启动阶段转换为中文错误并返回退出码 `1`。协议层、Prompt Pipeline、conversation memory 和供应商请求不读取权限配置。

## 核心数据结构

### 基础枚举

```python
class PermissionEffect(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"
    FORBIDDEN = "forbidden"


class PermissionMode(str, Enum):
    STRICT = "strict"
    DEFAULT = "default"
    PERMISSIVE = "permissive"


class RuleSource(str, Enum):
    SESSION = "session"
    LOCAL_PROJECT = "local_project"
    REPOSITORY_PROJECT = "repository_project"
    USER_GLOBAL = "user_global"


class ApprovalDecisionType(str, Enum):
    APPROVE_ONCE = "approve_once"
    APPROVE_SESSION = "approve_session"
    APPROVE_PROJECT = "approve_project"
    REJECT = "reject"
    CANCEL = "cancel"


class ApprovalOutcome(str, Enum):
    EXECUTE = "execute"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    ERROR = "error"
```

`PermissionEffect.FORBIDDEN` 可以出现在内部分析结果和最终决定中，但权限 YAML 解析器拒绝任何配置声明该值。

### 规则与匹配

```python
PermissionScalar = str | int | float | bool


@dataclass(frozen=True)
class ArgumentCondition:
    name: str
    expected: PermissionScalar


@dataclass(frozen=True)
class PermissionRule:
    id: str
    effect: PermissionEffect
    tool: str
    arguments: tuple[ArgumentCondition, ...]
    source: RuleSource


@dataclass(frozen=True, order=True)
class RuleSpecificity:
    exact_tool: int
    constrained_arguments: int
    exact_arguments: int


@dataclass(frozen=True)
class RuleMatch:
    rule: PermissionRule
    specificity: RuleSpecificity
```

字符串条件包含 `*`、`?` 或 `[]` glob 元字符时按 glob 匹配，否则按精确字符串匹配。数字和布尔值始终精确匹配。工具条件只有精确名称和单独的 `*` 两种形式。

### 规范化调用

```python
@dataclass(frozen=True)
class PermissionSubject:
    call: ToolCall
    definition: ToolDefinition
    normalized_arguments: Mapping[str, object]
    grant_arguments: Mapping[str, PermissionScalar]
    display_arguments: Mapping[str, object]
```

`normalized_arguments` 供规则和风险判断使用；`grant_arguments` 只包含 `ToolDefinition.grant_arguments` 声明的安全相关字段；`display_arguments` 已完成凭据脱敏和长度限制。三类映射在创建后不得被后续组件修改。

实现时先复制三类映射，再使用 `types.MappingProxyType` 包装，避免调用方持有原始字典后修改已经完成安全判断的主体。

### 命令与最终决定

```python
@dataclass(frozen=True)
class CommandAssessment:
    effect: PermissionEffect
    category: str | None
    reason_code: str | None
    message_zh: str | None


@dataclass(frozen=True)
class PermissionDecision:
    effect: PermissionEffect
    reason_code: str
    message_zh: str
    mode: PermissionMode
    display_arguments: Mapping[str, object]
    source: RuleSource | None = None
    rule_id: str | None = None
    risk_category: str | None = None
```

稳定英文 `reason_code` 供测试、模型工具结果和日志使用；所有用户可见说明使用 `message_zh`。`FORBIDDEN` 与路径越界决定不生成候选授权规则。

### 审批对象

```python
@dataclass(frozen=True)
class PermissionGrant:
    tool: str
    arguments: tuple[ArgumentCondition, ...]
    fingerprint: str


@dataclass(frozen=True)
class ApprovalRequest:
    id: str
    tool_call: ToolCall
    decision: PermissionDecision
    options: tuple[ApprovalDecisionType, ...]
    candidate_grant: PermissionGrant | None
    plan_only: bool
    round_index: int


@dataclass(frozen=True)
class ApprovalDecision:
    type: ApprovalDecisionType


@dataclass(frozen=True)
class ApprovalResolution:
    outcome: ApprovalOutcome
    tool_result: ToolResult | None = None
```

`candidate_grant` 由服务根据规范化授权参数创建，但不预先绑定规则来源。用户选择会话或项目后，服务再分别生成 `SESSION` 或 `LOCAL_PROJECT` 规则；TUI 不自行拼装规则。`plan-only` 请求的 `options` 只包含本次允许、拒绝和取消；未声明授权参数的工具也只允许这三个选项。

### 配置与会话状态

```python
@dataclass(frozen=True)
class PermissionFileConfig:
    version: int
    mode: PermissionMode | None
    rules: tuple[PermissionRule, ...]
    workspace: str | None = None


@dataclass(frozen=True)
class PermissionPaths:
    user_global: Path
    local_project: Path
    repository_project: Path


@dataclass
class PermissionSessionState:
    mode_override: PermissionMode | None = None
    rules: list[PermissionRule] = field(default_factory=list)

    def reset(self) -> None: ...
```

会话状态属于 `permission` 包。`AgentMode` 继续只保存 `plan_only`，避免把任务模式与权限档位混成同一个枚举。

### 异常

```python
class PermissionError(RuntimeError): ...
class PermissionConfigError(PermissionError): ...
class PermissionEvaluationError(PermissionError): ...
class PermissionPersistenceError(PermissionError): ...
```

配置错误用于启动失败；判定和持久化错误在运行时转换为 fail-closed 权限结果，不把内部堆栈回填给模型。`ToolPathError` 与 `GuardedPath` 定义在不依赖工具包的 `pathing.py` 中，避免文件工具导入路径守卫时产生循环依赖。

## 核心接口

### PathGuard

```python
class ToolPathError(ValueError): ...


@dataclass(frozen=True)
class GuardedPath:
    resolved: Path
    relative: str
    match_value: str


class PathGuard:
    def __init__(self, workspace_root: str | Path) -> None: ...

    @property
    def workspace_root(self) -> Path: ...

    def inspect(self, path: str) -> GuardedPath: ...
    def resolve(self, path: str) -> Path: ...
```

`resolve()` 保留现有文件工具调用方式并委托给 `inspect()`。`inspect()` 同时返回展示用相对路径和平台规范化后的匹配值。

### CommandAnalyzer

```python
class CommandAnalyzer:
    def __init__(
        self,
        workspace_root: str | Path,
        *,
        home: str | Path | None = None,
        platform: str | None = None,
        max_depth: int = 3,
    ) -> None: ...
    def assess(self, command: str) -> CommandAssessment: ...
```

分析器保存规范化工作区、用户主目录和平台根目录作为受保护目标。`platform` 可在测试中显式传入 `windows` 或 `posix`；生产默认根据 `os.name` 选择与 `subprocess.run(shell=True)` 一致的默认 shell 语义。

### PermissionStore

```python
class PermissionStore:
    @classmethod
    def load(
        cls,
        workspace_root: str | Path,
        *,
        home: str | Path | None = None,
    ) -> "PermissionStore": ...

    @property
    def paths(self) -> PermissionPaths: ...

    def rules_for(self, source: RuleSource) -> tuple[PermissionRule, ...]: ...
    def effective_mode(self) -> tuple[PermissionMode, RuleSource | None]: ...
    def set_session_mode(self, mode: PermissionMode) -> None: ...
    def add_session_rule(self, rule: PermissionRule) -> None: ...
    async def persist_local_project_rule(self, rule: PermissionRule) -> None: ...
    def clear_session(self) -> None: ...
```

`persist_local_project_rule()` 使用 `asyncio.to_thread()` 包装同步原子文件替换，成功后再更新内存中的本地项目规则。

### PermissionPolicy

```python
class PermissionPolicy:
    def __init__(
        self,
        *,
        store: PermissionStore,
        path_guard: PathGuard,
        command_analyzer: CommandAnalyzer,
    ) -> None: ...

    def evaluate(
        self,
        call: ToolCall,
        definition: ToolDefinition,
        *,
        plan_only: bool,
    ) -> tuple[PermissionSubject, PermissionDecision]: ...
```

策略返回规范化主体和最终决定，使服务可以基于同一份规范化数据创建审批请求，避免重复解析造成差异。

### PermissionService

```python
class PermissionService:
    @classmethod
    def create(
        cls,
        workspace_root: str | Path,
        *,
        home: str | Path | None = None,
    ) -> "PermissionService": ...

    @property
    def path_guard(self) -> PathGuard: ...

    def evaluate(
        self,
        call: ToolCall,
        definition: ToolDefinition,
        *,
        plan_only: bool,
        round_index: int,
    ) -> PermissionDecision: ...

    def create_approval_request(
        self,
        call: ToolCall,
        decision: PermissionDecision,
        *,
        plan_only: bool,
        round_index: int,
    ) -> ApprovalRequest: ...

    async def resolve_approval(
        self,
        request: ApprovalRequest,
        decision: ApprovalDecision,
    ) -> ApprovalResolution: ...

    def denied_result(self, call: ToolCall, decision: PermissionDecision) -> ToolResult: ...
    def effective_mode(self) -> tuple[PermissionMode, RuleSource | None]: ...
    def set_session_mode(self, mode: PermissionMode) -> None: ...
    def clear_session(self) -> None: ...
```

服务只对 effect 为 `ASK` 的调用按工具调用 ID 缓存最近一次 `(PermissionSubject, PermissionDecision)`，用于创建紧随其后的审批请求；请求创建后立即移除。未找到匹配缓存时 fail-closed，不重新解释可能已变化的调用。

### Agent 适配接口

```python
class PermissionInterceptor:
    def __init__(self, service: PermissionService) -> None: ...

    async def before_tool(
        self,
        call: ToolCall,
        definition: ToolDefinition,
        *,
        plan_only: bool,
        round_index: int,
    ) -> PermissionDecision: ...

    async def resolve_approval(
        self,
        request: ApprovalRequest,
        decision: ApprovalDecision,
    ) -> ApprovalResolution: ...

    async def after_tool(self, call: ToolCall, result: ToolResult) -> ToolResult: ...
```

`after_tool()` 保留现有后置扩展点，首期只写脱敏诊断并原样返回工具结果。它不承担安全补救，因为工具此时已经执行。

## 模块设计

### `permission/models.py`

**职责：** 保存本阶段全部权限枚举、不可变数据类、审批类型和权限异常。

**依赖：** 标准库、`mycode.tool.base` 中的工具调用与结果类型。

**约束：** 不读取文件、不解析命令、不依赖 Agent、TUI 或配置加载器。关键枚举的注释说明 `FORBIDDEN`、普通拒绝和审批之间的安全差异。

### `permission/policy.py`

**职责：** 完成工具参数契约校验、规范化、脱敏、规则匹配、具体度排序和完整权限判定链。

**主要内部单元：**

- `build_subject()`：校验 `ToolCall.arguments`，处理必填字段和 JSON Schema 基础类型。
- `_normalize_arguments()`：对路径、命令和普通标量生成匹配值。
- `_redact_arguments()`：隐藏凭据并限制展示长度。
- `match_rule()` / `select_rule()`：执行单规则匹配和来源内确定性选择。
- `PermissionPolicy.evaluate()`：按安全底线、来源、风险、档位、`plan-only` 顺序组合结果。

**依赖：** `models.py`、`command.py`、`pathing.py`、`config.py` 的只读 store 接口、`tool.base`。

**约束：** 不执行工具、不请求用户输入、不持久化配置。优先级、同级冲突和 `plan-only` 叠加处必须有中文注释说明安全理由。

### `permission/command.py`

**职责：** 识别 shell 家族、保守切分命令链、递归分析常见嵌套 shell，并输出内置风险分类。

**实现边界：**

- 在引号外识别 `|`、`&&`、`||`、`;` 和换行。
- 跟踪 POSIX 反斜杠、cmd 的 `^` 和 PowerShell 的反引号转义。
- 识别显式 `cmd /c`、`powershell -command`、`pwsh -command`、`sh -c`、`bash -c`。
- 递归深度上限为 `3`；引号不闭合、编码命令、命令替换、超长命令或未知启动器返回 `ASK`。
- 高置信度分类覆盖根目录递归删除、磁盘格式化/原始设备覆盖、系统关键目录破坏和同一命令链下载即执行。
- 高风险分类覆盖工作区删除、破坏性 Git、包安装、网络、权限、提权、服务和进程操作。

**依赖：** `models.py`、标准库 `os`、`re`、`shlex`；`shlex` 只用于已识别的 POSIX 片段，不能用于解释 cmd 或 PowerShell。

**约束：** 不执行任何命令。无法确定时返回 `ASK`，对应分支使用中文注释说明为什么不能按安全命令放行。

### `permission/pathing.py`

**职责：** 保存 `PathGuard`、工作区规范化和符号链接边界检查。

**实现方式：**

- 构造时保存 `Path(workspace_root).resolve()`。
- 输入路径相对工作区拼接，绝对路径直接检查。
- 对已存在路径解析真实位置；对不存在目标解析最近的已存在父目录，再拼接剩余部分。
- 使用平台规范化比较处理 Windows 盘符大小写、UNC 和分隔符差异。
- `relative` 保留展示形式，`match_value` 在 Windows 上按 `normcase` 规范化。
- 文件工具执行前再次调用 `inspect()`；查找和搜索在读取每个候选文件前复检。

**依赖：** 仅标准库 `dataclasses`、`pathlib` 和 `os.path`。`GuardedPath` 与 `ToolPathError` 直接定义在本文件中。

**约束：** 不导入 `permission.models`、`permission.service` 或 `mycode.tool`，从而允许文件工具单向依赖该叶子模块。符号链接和 fail-closed 分支写中文安全注释。

### `permission/config.py`

**职责：** 权限 YAML 解析、来源约束、多来源加载、会话状态、有效档位和原子持久化。

**加载顺序：**

1. 用户全局：`~/.mycode/permissions.yaml`。
2. 本地项目：`~/.mycode/projects/<workspace_sha256>/permissions.yaml`。
3. 仓库项目：`<workspace>/mycode.permissions.yaml`。
4. 会话状态：启动时为空。

工作区摘要输入为 `os.path.normcase(str(workspace_root.resolve()))` 的 UTF-8 字节，使用完整 SHA-256 十六进制字符串。工作区被移动后使用新的本地项目授权目录，避免授权静默跟随到另一位置。

**来源约束：**

- 用户全局与本地项目允许 `mode` 和 `allow/deny/ask`。
- 仓库项目禁止 `mode`、`allow` 和 `workspace`。
- 本地项目要求 `workspace` 与当前规范化工作区一致。
- 所有文件要求 `version: 1`，拒绝未知字段、未知 effect、`forbidden`、重复 ID 和相同条件冲突。

**持久化：** 使用同目录 `NamedTemporaryFile(delete=False)` 写入 UTF-8 YAML，`flush()` 和 `os.fsync()` 后关闭，再通过 `os.replace()` 原子替换。异常时删除临时文件、保留旧文件并抛出 `PermissionPersistenceError`。生成 YAML 使用 `yaml.safe_dump(..., allow_unicode=True, sort_keys=False)`。

**依赖：** `models.py`、PyYAML、标准库 `hashlib`、`tempfile`、`os`、`pathlib`、`asyncio`。

### `permission/service.py`

**职责：** 合并 `PermissionService`、审批处理、Agent 拦截适配和默认装配，作为权限领域唯一高层入口。

**审批规则：**

- 候选授权不预先绑定来源；会话或项目审批通过后生成 effect 为 `ALLOW` 的对应来源规则。
- ID 为 `hitl-<tool>-<grant_arguments_sha256前12位>`。
- 会话允许生成 `SESSION` 规则并调用 `add_session_rule()`；项目允许生成 `LOCAL_PROJECT` 规则并调用 `persist_local_project_rule()`。
- 同一 ID 且条件相同时幂等替换；同一 ID 条件不同视为安全错误。
- 持久化成功后才返回 `EXECUTE`。
- `REJECT` 和无 provider 生成 `tool_rejected_by_user` 结果；`CANCEL` 终止当前 turn；持久化失败生成 `permission_persist_failed` 结果。

**依赖：** 其余五个权限实现文件、`mycode.tool.base`。

**约束：** 不依赖 Agent、Session 或 TUI 的具体类。面向用户的消息全部在本文件或模型对象中以中文定义；审批失败不执行的关键分支写中文安全注释。

## 规则选择算法

### 来源优先级

策略按下列顺序查找“第一个存在匹配项的来源”：

```text
SESSION > LOCAL_PROJECT > REPOSITORY_PROJECT > USER_GLOBAL
```

找到来源后不再读取较低来源的匹配项。`LOCAL_PROJECT` 因而可以覆盖仓库普通限制，代表本地用户对当前工作区的显式授权。任何来源都位于 `FORBIDDEN` 和路径沙箱之后。

### 来源内具体度

每条匹配规则生成：

```text
RuleSpecificity(
    exact_tool = 1 if tool != "*" else 0,
    constrained_arguments = len(arguments),
    exact_arguments = 不含 glob 元字符的字符串条件数量 + 标量条件数量,
)
```

按具体度降序选出候选。多个候选具体度相同时，effect 排序为 `DENY > ASK > ALLOW`。effect 仍相同时按规则 ID 升序选择用于诊断，使结果与 YAML 声明顺序无关。

### 权限档位

有效档位来源：

```text
会话覆盖 > 本地项目 mode > 用户全局 mode > DEFAULT
```

仓库项目文件不能设置 mode。档位只处理没有规则和内置高风险命中的调用：

| 档位 | 读工具 | 写/命令工具 |
|------|--------|-------------|
| `STRICT` | `ASK` | `ASK` |
| `DEFAULT` | `ALLOW` | `ASK` |
| `PERMISSIVE` | `ALLOW` | `ALLOW` |

`STRICT` 下显式规则 `ALLOW` 仍直接允许；表格只描述未命中调用。

### `plan-only` 叠加

在普通权限结果得出后，对写工具应用：

```text
ALLOW → ASK(plan_only_write)
ASK → ASK（保留原风险原因，审批选项缩减）
DENY → DENY
FORBIDDEN → FORBIDDEN
```

## 权限配置格式

### 用户全局

```yaml
version: 1
mode: default
rules:
  - id: allow-source-read
    effect: allow
    tool: read_file
    arguments:
      path: "src/**"
```

### 本地项目

```yaml
version: 1
workspace: "D:/resolved/workspace"
mode: strict
rules:
  - id: hitl-run_command-a1b2c3d4e5f6
    effect: allow
    tool: run_command
    arguments:
      command: "pytest -q"
```

### 仓库项目策略

```yaml
version: 1
rules:
  - id: protect-env
    effect: deny
    tool: write_file
    arguments:
      path: ".env*"
```

`rules` 缺失时等价于空列表；`mode` 缺失时该来源不提供档位。规则 `arguments` 缺失时按空映射处理。所有参数值必须为字符串、数字或布尔值，不接受列表、嵌套映射或 null。

## 危险命令设计

### 保守扫描

分析器先限制命令长度。超过 `32768` 个字符、包含空字符、引号不闭合、递归超过三层或无法识别显式 shell 参数时返回 `ASK(command_ambiguous)`。

扫描器在引号和转义状态外切分控制操作符，并保留操作符类型。这样可以识别“下载器输出通过管道进入解释器”和“下载到文件后在同一 `&&`/`;` 链执行”的关系，而不会把引号内的 `|` 当作管道。

### `FORBIDDEN` 分类

- `rm/rmdir/del/Remove-Item` 等递归或强制删除命令的目标解析为工作区根、用户主目录、文件系统根、磁盘根或系统关键目录。
- `mkfs*`、`format`、`diskpart`、`Format-Volume`、`Clear-Disk` 等格式化或磁盘清理命令。
- `dd` 的输出目标为 `/dev/*` 等原始设备。
- `curl/wget/iwr/irm/Invoke-WebRequest/Invoke-RestMethod` 的输出直接通过管道进入 shell、解释器、`eval`、`iex/Invoke-Expression`。
- 同一控制链先下载到明确文件，后续片段立即通过 shell 或解释器执行该文件。

### `ASK` 分类

- 工作区内删除与清理。
- `git clean`、`git reset --hard`、强制 checkout/restore 等丢失修改的操作。
- `pip/npm/yarn/pnpm/cargo/gem` 等安装行为。
- 网络上传下载、远程 Git 写入。
- `sudo/runas/Start-Process -Verb RunAs` 等提权。
- `chmod/chown/icacls/takeown` 等权限或所有权修改。
- 服务、计划任务和进程终止操作。
- 编码命令、命令替换、未知 shell 或无法可靠解析的结构。

分类器只判断高置信度类别，不承诺覆盖所有 shell 语法。普通规则中的精确 `ALLOW` 可以满足 `ASK`，但不能覆盖 `FORBIDDEN`。

## 工具定义调整

`ToolDefinition` 增加本地字段：

```python
@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: JSONSchema
    kind: ToolKind
    grant_arguments: tuple[str, ...] = ()
```

协议适配器继续只发送名称、描述和 JSON Schema，不发送 `kind` 或 `grant_arguments`。

内置声明：

| 工具 | `grant_arguments` |
|------|-------------------|
| `read_file` | `("path",)` |
| `write_file` | `("path",)` |
| `edit_file` | `("path",)` |
| `find_files` | `("root",)`，缺失时规范化为 `.` |
| `search_code` | `("root",)`，缺失时规范化为 `.` |
| `run_command` | `("command",)` |

未声明授权参数的自定义工具可以被手工规则匹配，但 HITL 不为其生成会话或项目授权。

## Agent、Session 与 TUI 交互

### AgentLoop

`AgentLoop` 构造函数将现有可选 `interceptor` 改为必需的 `permission: PermissionInterceptor`，防止其他装配入口遗漏权限系统。工具批次内仍先按模型顺序发布 `TOOL_CALL_STARTED`，TUI 文案改为“工具请求”以避免暗示已经执行。

每个调用按以下结果处理：

- `ALLOW`：加入当前批次的可执行调用。
- `DENY/FORBIDDEN`：通过 `PermissionService.denied_result()` 生成结果，yield `TOOL_RESULT` 并写入 memory。
- `ASK`：创建 `ApprovalRequest`、yield `APPROVAL_REQUIRED`、调用 provider，再由拦截器解析结果。
- 无 provider：构造拒绝结果，yield `TOOL_RESULT` 并继续，不产生终止 Agent 的 `ERROR`。
- `ApprovalOutcome.EXECUTE`：加入可执行调用。
- `REJECTED/ERROR`：yield 对应失败 `TOOL_RESULT` 并继续。
- `CANCELLED`：yield `CANCELLED` 并终止当前 turn。

多个读调用的权限判断和审批保持顺序执行；只有最终获准的读调用进入 `asyncio.gather()`。这自然保证 TUI 不会并发等待多个审批。

### ChatSession

构造函数增加 `permissions: PermissionService`。新增：

```python
def permission_mode(self) -> tuple[PermissionMode, RuleSource | None]: ...
def set_permission_mode(self, mode: PermissionMode) -> None: ...
```

`clear()` 依次清 memory、复位 `AgentMode.plan_only` 并调用 `PermissionService.clear_session()`。持久化规则不清除。

### ChatTUI

新增命令：

- `/permission`：显示中文档位和来源。
- `/permission strict|default|permissive`：设置会话档位。
- 其他 `/permission ...`：显示中文用法，不发送模型请求。

普通审批提示完整展示中文选项和脱敏参数，接受 `o/y`（本次）、`s`（会话）、`p`（项目）、`n`（拒绝）和 `c`（取消）。`plan-only` 或没有授权参数时不展示 `s/p`，即使输入也按取消处理并提示选项无效。

## 失败处理

| 失败点 | 系统行为 | 是否执行工具 |
|--------|----------|--------------|
| 用户/本地/仓库权限配置非法 | CLI 中文报错，退出码 `1` | 否 |
| 未知工具 | 保持现有 `UNKNOWN_TOOL` Agent 错误 | 否 |
| 非法 JSON 或参数契约不符 | `invalid_tool_arguments` 工具结果 | 否 |
| 路径越界或无法确认边界 | `path_outside_workspace` 工具结果 | 否 |
| 命令或策略内部异常 | `security_check_failed` 工具结果 | 否 |
| 普通规则拒绝 | `permission_denied` 工具结果 | 否 |
| 安全底线命中 | `forbidden_operation` 工具结果 | 否 |
| 无审批 provider | `tool_rejected_by_user` 工具结果 | 否 |
| 用户拒绝 | `tool_rejected_by_user` 工具结果 | 否 |
| 用户取消 | `CANCELLED` Agent 事件，终止 turn | 否 |
| 本地项目授权持久化失败 | `permission_persist_failed` 工具结果 | 否 |
| 获准后的工具超时/失败 | 保持现有 `ToolResult` | 已尝试 |
| Agent run 超时/取消 | 保持现有 Agent 事件与 memory 契约 | 依现有时序 |

拒绝结果只向模型提供 `ok`、工具名、调用 ID、原因码和中文摘要，不提供规则全集、黑名单模式或异常堆栈。

## 脱敏与诊断

规则和审批日志包含：调用 ID、工具名、effect、reason code、档位、来源、规则 ID、风险分类和审批范围。

以下数据在审批展示和日志中脱敏：

- 名称包含 `api_key`、`apikey`、`token`、`password`、`passwd`、`secret`、`credential` 的参数。
- URL 的 userinfo 和常见 `access_token`、`api_key` 查询参数。
- 命令中的 `--token`、`--password`、`--api-key` 等参数值。
- `NAME=value` 中名称包含敏感关键字的环境变量赋值。

展示字符串上限为 `512` 个字符，超出后追加中文截断标记。文件正文参数不进入 `grant_arguments`，日志只记录参数名和脱敏摘要。

## 文件组织

```text
src/mycode/
├── permission/
│   ├── __init__.py       — 仅包文档，不做急切导出
│   ├── models.py         — 枚举、数据类、审批类型、异常
│   ├── policy.py         — 规范化、脱敏、规则匹配、判定链
│   ├── command.py        — shell 扫描和危险命令分类
│   ├── pathing.py        — PathGuard 和路径边界
│   ├── config.py         — YAML、多来源、会话状态、持久化
│   └── service.py        — 门面、审批、拦截器、默认装配
├── agent/
│   ├── __init__.py       — 从 permission 兼容导出审批类型
│   ├── events.py         — ApprovalRequest 导入迁移
│   ├── loop.py           — 接入 PermissionInterceptor
│   ├── state.py          — 继续只保存 plan_only
│   ├── approval.py       — 删除
│   └── interceptor.py    — 删除
├── tool/
│   ├── base.py           — ToolDefinition 增加 grant_arguments
│   ├── defaults.py       — 接收共享 PathGuard
│   ├── filesystem.py     — 迁移导入并复检候选路径
│   ├── command.py        — 声明 command 授权参数
│   ├── __init__.py       — 移除工具领域 PathGuard 导出
│   └── pathing.py        — 删除
├── cli.py                — 装配服务并处理权限配置错误
├── session.py            — 权限档位与 clear 委托
└── tui.py                — 中文权限命令和审批交互

tests/
├── test_permission_policy.py
├── test_permission_command.py
├── test_permission_pathing.py
├── test_permission_config.py
├── test_permission_service.py
├── test_permission_e2e.py
├── test_agent_loop.py        — 注入权限拦截器并保留调度回归
├── test_agent_plan_only.py   — 迁移到统一审批类型
├── test_agent_events.py      — 新 ApprovalRequest 契约
├── test_session.py           — 档位与 clear
├── test_tui.py               — 命令和五种审批输入
├── test_cli.py               — 权限装配和启动失败
├── test_tool_filesystem.py   — 文件工具执行期复检
├── test_tool_command.py      — grant_arguments 元数据
├── test_tool_registry.py     — 工具定义兼容
├── test_openai_chat_protocol.py       — 本地元数据不出站
├── test_openai_responses_protocol.py  — 本地元数据不出站
└── test_docs.py              — README 权限说明

examples/
└── mycode.permissions.yaml   — 仅 DENY/ASK 的仓库策略示例

README.md                     — 权限模式、规则来源、HITL 和边界
```

测试不再按每个小类型拆文件；同类规则、规范化和脱敏测试集中在 `test_permission_policy.py`，配置解析、来源和持久化集中在 `test_permission_config.py`，审批与 Agent 适配集中在 `test_permission_service.py`。

## 测试策略

### 单元测试

- `test_permission_policy.py`：非法参数、路径/命令规范化、敏感值脱敏、来源优先级、项目子来源、glob、具体度、同级 effect、规则 ID 确定性、三档模式和 `plan-only`。
- `test_permission_command.py`：POSIX、cmd、PowerShell 的安全、ASK、FORBIDDEN、嵌套、管道、链式下载执行、引号、转义、编码、超长和深度边界。
- `test_permission_pathing.py`：合法相对/绝对路径、父目录逃逸、符号链接、最近已存在父目录、UNC、盘符大小写和规范化匹配值。
- `test_permission_config.py`：三类 YAML、来源限制、未知字段、重复/冲突、工作区哈希、workspace 校验、会话清理、幂等规则、原子写入和模拟失败。
- `test_permission_service.py`：决定转换、候选规则、五种审批、无授权参数、无 provider、持久化失败、中文结果和 after_tool 不改结果。

### 集成与端到端

- fake LLM 产生允许、拒绝、询问和禁止调用，fake executor 证明被阻断调用从未执行。
- 连续多个读调用中部分需要审批，验证审批串行且获准部分仍并发执行。
- 恶意仓库策略尝试 `allow` 或 `mode` 时启动失败；合法 `deny/ask` 只能收紧。
- `/permission`、`/plan-only`、项目永久授权和 `/clear` 走完整 TUI/Session/Agent 流程。
- 永久授权写入临时 home，不修改临时工作区内的仓库策略。
- 所有危险命令测试只调用分析器或 fake executor，不访问网络、不执行真实命令。

### 回归命令

```powershell
pytest -q
```

项目当前没有独立 lint 配置，因此本阶段以全量 pytest、`python -m compileall src` 和文档检查作为自动化验证入口。

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 权限主边界 | 独立 `permission` 包 | 满足职责集中，Agent 与工具不复制策略 |
| 文件粒度 | 6 个实现文件 | 按用户要求合并同类逻辑，避免过度拆分 |
| Agent 接入 | 必需 `PermissionInterceptor` | 防止其他装配路径遗漏安全检查 |
| 安全底线 | 内置、不可配置覆盖 | 防止会话和仓库策略突破核心保护 |
| 仓库项目策略 | 仅 `DENY/ASK`，禁止 mode | 工作区内容不作为可信授权来源 |
| 项目永久授权 | 用户目录按工作区 SHA-256 隔离 | 不修改仓库，不让恶意仓库携带授权 |
| 工作区移动 | 生成新的授权目录 | 避免权限静默跟随到不同路径 |
| 规则语法 | glob + 标量精确匹配 | 可预测、可审计，不引入任意表达式 |
| 同级冲突 | 具体度后 `DENY > ASK > ALLOW` | 确定性且默认保守 |
| HITL 授权粒度 | ToolDefinition 显式 `grant_arguments` | 防止正文、凭据或无关参数进入规则 |
| 未声明授权参数 | 只允许本次 | 避免生成工具级过宽授权 |
| 命令分析 | 标准库保守扫描 | 无新增依赖，不错误宣称完整 shell 解析 |
| 解析不确定 | `ASK` | 在可用性和 fail-closed 之间保留用户判断 |
| 路径防御 | 策略前检查 + 工具执行期复检 | 避免单点失效，保持现有工具保护 |
| 持久化 | 临时文件 + fsync + os.replace | 避免部分写入，兼容 Windows |
| 无审批 provider | 拒绝并继续 turn | 非交互环境不放行，也让模型能调整 |
| `plan-only` | 最终追加至少 ASK | 保持任务模式独立，不让普通授权绕过 |
| 用户提示 | 中文消息 + 英文原因码 | 满足交互要求并保持机器契约稳定 |
| 关键注释 | 中文解释安全理由 | 记录优先级、降级和 fail-closed 的意图 |
| 外部策略引擎 | 不引入 | 当前内置工具规模不需要额外 DSL 与依赖 |

## Spec 覆盖自查

- F1/F12：`PermissionService`、必需拦截器、Agent 结果分支覆盖统一入口和执行顺序。
- F2：`permission` 包文件组织及依赖方向覆盖权限领域集中。
- F3：策略前置 `FORBIDDEN` 与命令分类覆盖不可绕过安全底线。
- F4/F11：`CommandAnalyzer` 的 ASK/FORBIDDEN 分类和保守扫描覆盖高风险命令。
- F5/F6：`PermissionStore` 来源顺序、`PermissionRule` 和具体度算法覆盖分层与冲突。
- F7：有效档位算法、Session 与 TUI 命令覆盖三档权限模式。
- F8：审批对象、`grant_arguments`、会话/项目持久化和五种结果覆盖 HITL。
- F9：`plan-only` 最终叠加和审批选项缩减覆盖兼容要求。
- F10：`PathGuard.inspect()`、策略前检查及文件工具候选复检覆盖路径沙箱。
- F13：版本化 YAML、来源校验、启动失败和原子替换覆盖配置生命周期。
- F14：决定元数据、脱敏、中文消息和安全工具结果覆盖诊断要求。
- F15：模块设计中列出的关键中文注释位置覆盖注释要求。
- F16：Agent 调度、memory、协议元数据过滤、超时取消和全量回归覆盖现有行为兼容。

所有功能需求均有明确模块、接口和测试归属；本设计没有引入 OS 进程沙箱、网络隔离、正则规则、热加载、外部策略引擎或持久化审计数据库。
