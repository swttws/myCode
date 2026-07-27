# myCode Stage 10：Skill 系统技术设计

## 架构概览

Stage 10 新增独立的 `mycode.skill` 领域包，由目录、运行时、加载工具、执行器和斜杠桥接组成。现有 `AgentLoop` 继续负责模型流、权限、工具调度、超时和历史写入，只增加 Prompt 块、工具视图和独立父上下文三个接入点。

### SkillCatalog

负责三级目录发现、YAML/Markdown 解析、优先级覆盖、文件指纹缓存、启动校验和热更新。

目录维护“当前有效快照”和“最后有效版本”。普通解析失败会移除对应候选并回退到低层版本；热更新出现未知工具或系统命令冲突时，只拒绝受影响 Skill 的新版本。

### SkillRuntime

保存两类互相独立的状态：

- 已激活 Skill 集合：用于每轮持续注入完整 SOP。
- 当前执行范围：只保存正在执行的 Skill，用于工具白名单过滤。

激活集合按 Skill 名称确定性排序。执行结束只清除当前范围；`/clear` 同时清除两类状态。

### SkillLoadTool

作为普通工具注册到现有 `ToolRegistry`，但由 Skill 运行时保证始终对模型可见。

加载入口时，它刷新目录、替换参数、激活 Skill，并返回激活结果与资源清单；读取资源时只返回经过目录边界检查的单个文件。完整 SOP 通过下一轮固定环境块提供，不写入普通工具结果历史。

### AgentLoop 接入

现有 `AgentLoop` 增加三个 Skill 接入点：

- 每轮构建 Prompt 时附加 Skill 摘要和已激活 SOP。
- 每轮生成工具定义时采用当前 Skill 白名单视图。
- 每次真实执行工具前再次校验白名单，防止模型伪造工具调用。

Skill 加载工具采用串行调度。若同一批调用中先激活 Skill，后续调用立即受新白名单约束。

### SkillExecutor

共享模式直接调用主 `AgentLoop`，使用主历史、主模型和主上下文管理器。

独立模式复用同一个 `AgentLoop` 核心，但使用临时内存和无持久化上下文适配器：

- `none` 使用空历史。
- `recent` 从主历史提取最近 N 个完整轮次。
- `summary` 先用独立模式所选模型执行一次无工具摘要。
- 指定模型通过复制主 LLM 配置、仅替换模型 ID 创建。
- 独立运行的最终响应作为自包含摘要写回主历史，详细工具历史只留在临时内存中。

该设计不复制 Agent 循环，也不污染主压缩归档和长期会话记录。

### SkillSlashBridge

固定命令注册表移除硬编码 `/review`，Skill 命令按名称排序后动态追加。

补全、分发和 Skill 加载前均调用同一个热更新入口。斜杠处理函数只把 Skill 名称和参数交给 `ChatSession`，不自行拼接 SOP 或执行模型逻辑。

### 启动顺序

```text
创建本地工具
  → 注册上下文与记忆工具
  → 初始化 MCP 并注册远程工具
  → 创建 SkillCatalog、SkillRuntime 与 SkillExecutor
  → 注册 SkillLoadTool
  → 校验 Skill 白名单和固定命令冲突
  → 组合固定命令与 Skill 命令
  → 创建 AgentLoop、ChatSession 和 TUI
```

## Skill 定义格式

### 目录布局

每个 Skill 必须独占一个一级子目录，入口固定为 `SKILL.md`：

```text
<workspace>/.mycode/skills/<name>/SKILL.md
~/.mycode/skills/<name>/SKILL.md
src/mycode/skill/builtins/<name>/SKILL.md
```

目录名必须与 frontmatter 的 `name` 完全一致。Skill 根目录下散落的 Markdown 不识别；一级子目录缺少 `SKILL.md` 时记录诊断；Skill 目录和 `SKILL.md` 必须是普通路径与普通文件，符号链接目录、入口和资源均拒绝加载或从资源清单排除。

### Frontmatter

采用固定字段和严格校验，未知字段视为当前候选解析失败。

```markdown
---
name: investigate
description: 调查指定问题并输出结论
allowed_tools:
  - read_file
  - search_code
  - run_command
mode: isolated
context:
  strategy: recent
  turns: 3
model: gpt-5
---

按照以下步骤调查问题：

1. 阅读相关代码和测试。
2. 复现并定位原因。
3. 输出证据和结论。

用户输入：{{arguments}}
```

| 字段 | 类型 | 规则 |
|---|---|---|
| `name` | 字符串 | 必填；匹配 `^[a-z][a-z0-9_-]{0,63}$` |
| `description` | 字符串 | 必填；单行、非空，最多 200 字符 |
| `allowed_tools` | 字符串列表 | 必填；允许空列表；名称区分大小写、不得重复 |
| `mode` | 枚举 | 必填；`shared` 或 `isolated` |
| `context` | 映射 | `isolated` 必填，`shared` 禁止 |
| `context.strategy` | 枚举 | `none`、`recent` 或 `summary` |
| `context.turns` | 正整数 | 仅 `recent` 必填；其他策略禁止 |
| `model` | 字符串 | 可选；非空；共享模式允许声明但运行时忽略 |

入口文件必须以 `---` 开始并具有结束分隔线。正文去除首尾空白后必须非空。

参数只替换正文中的精确字面量 `{{arguments}}`，使用原始用户参数，不支持其他变量、转义规则或模板表达式。

## 核心数据结构

### 领域枚举

```python
class SkillSource(str, Enum):
    BUILTIN = "builtin"
    USER = "user"
    PROJECT = "project"


class SkillMode(str, Enum):
    SHARED = "shared"
    ISOLATED = "isolated"


class SkillContextStrategy(str, Enum):
    NONE = "none"
    RECENT = "recent"
    SUMMARY = "summary"
```

来源优先级使用固定映射，不依赖枚举字符串排序：

```python
SOURCE_PRIORITY = {
    SkillSource.BUILTIN: 100,
    SkillSource.USER: 200,
    SkillSource.PROJECT: 300,
}
```

### SkillContextPolicy

```python
@dataclass(frozen=True)
class SkillContextPolicy:
    strategy: SkillContextStrategy
    turns: int = 0
```

- `RECENT` 要求 `turns > 0`。
- `NONE` 和 `SUMMARY` 要求 `turns == 0`。
- 共享模式保存 `context=None`。

### SkillMetadata

```python
@dataclass(frozen=True)
class SkillMetadata:
    name: str
    description: str
    allowed_tools: tuple[str, ...]
    mode: SkillMode
    context: SkillContextPolicy | None
    model: str | None
```

该类型包含第一阶段可使用的元信息和执行配置，不包含正文或路径。

### SkillCandidate 与 SkillScanResult

```python
@dataclass(frozen=True)
class SkillCandidate:
    source: SkillSource
    package_root: Path
    entry_path: Path
    fingerprint: tuple[tuple[str, int, int], ...]


@dataclass(frozen=True)
class SkillScanResult:
    candidates: tuple[SkillCandidate, ...]
    diagnostics: tuple[SkillDiagnostic, ...]
```

`fingerprint` 包含入口和资源的相对路径、大小与 `mtime_ns`。该中间类型让 Catalog 可以只解析变化候选，同时保留缺失入口和目录安全诊断。

### SkillDefinition

```python
@dataclass(frozen=True)
class SkillDefinition:
    metadata: SkillMetadata
    instruction: str
    source: SkillSource
    entry_path: Path
    package_root: Path
    resources: tuple[str, ...]
    revision: str
```

- `resources` 使用 `/` 分隔的相对路径并按字典序排列。
- `revision` 是入口正文和资源清单的 SHA-256。
- 资源清单只包含包内普通文件，排除入口文件和所有符号链接。

### SkillActivation

```python
@dataclass(frozen=True)
class SkillActivation:
    name: str
    arguments: str
    rendered_instruction: str
    revision: str
```

保存原始参数，使热更新后可以用新正文重新渲染。

### SkillExecutionScope

```python
@dataclass(frozen=True)
class SkillExecutionScope:
    name: str
    allowed_tools: frozenset[str]
```

执行范围保存在当前 Agent run 的上下文变量中，不作为全局单例状态。

### SkillRunContext

```python
@dataclass(frozen=True)
class SkillRunContext:
    history: tuple[ChatMessage, ...]
    framework_blocks: tuple[PromptContextBlock, ...]
    approval_provider: ApprovalProvider | None
    scope: SkillExecutionScope | None
    isolated_depth: int
```

`history` 是当前用户消息加入前的主历史，避免独立模式把尚未完成的当前轮计入最近 N 轮。

### SkillExecutionResult

```python
@dataclass(frozen=True)
class SkillExecutionResult:
    ok: bool
    summary: str
    error_code: str | None = None
```

独立执行只返回最终摘要和稳定错误码，不暴露临时历史。

### SkillCatalogSnapshot

```python
@dataclass(frozen=True)
class SkillCatalogSnapshot:
    definitions: tuple[SkillDefinition, ...]
    diagnostics: tuple[SkillDiagnostic, ...]
    generation: int
```

`definitions` 按名称排序。有效目录状态改变时递增 `generation`；仅诊断变化而有效定义不变时保持当前代次。

### SkillDiagnostic

```python
@dataclass(frozen=True)
class SkillDiagnostic:
    code: str
    source: SkillSource
    path: str
    message: str
    skill_name: str | None = None
```

诊断不包含完整正文、历史或凭据。

### 有界常量

```python
MAX_SKILL_FILE_BYTES = 128 * 1024
MAX_FRONTMATTER_BYTES = 16 * 1024
MAX_RESOURCE_BYTES = 1024 * 1024
MAX_RESOURCE_COUNT = 256
```

超限入口按候选解析失败处理；资源数量超限使候选解析失败；单个超限资源保留在清单中，但读取时返回稳定错误。

## 核心接口

### SkillLoader

`SkillLoader` 只负责文件系统与格式解析，不保存运行状态。

```python
class SkillLoader:
    def __init__(
        self,
        *,
        workspace_root: Path,
        home: Path,
        builtin_root: Path,
    ) -> None: ...

    def scan(self) -> SkillScanResult: ...

    def load(self, candidate: SkillCandidate) -> SkillDefinition: ...

    def read_resource(
        self,
        definition: SkillDefinition,
        relative_path: str,
    ) -> str: ...
```

`scan()` 只检查一级 Skill 目录和文件元数据；`load()` 才读取变化入口并生成定义。

### SkillCatalog

```python
class SkillCatalog:
    def __init__(
        self,
        *,
        loader: SkillLoader,
        tool_names: Callable[[], frozenset[str]],
        reserved_slash_names: frozenset[str],
    ) -> None: ...

    def initialize(self) -> SkillCatalogSnapshot: ...

    def refresh(self) -> SkillCatalogSnapshot: ...

    def snapshot(self) -> SkillCatalogSnapshot: ...

    def get(self, name: str) -> SkillDefinition | None: ...

    def read_resource(self, name: str, relative_path: str) -> str: ...
```

- `initialize()` 对最终有效定义中的未知工具和固定命令冲突 fail-fast。
- `refresh()` 只重新加载指纹变化候选。
- 解析失败移除当前候选并重新计算三级覆盖。
- 热更新的未知工具或命令冲突只拒绝对应 Skill，保留该名称最后有效定义。
- 新 Skill 无最后有效版本时不注册并产生诊断。
- 其他 Skill 的有效更新不受单个拒绝项影响。

### SkillRuntime

```python
class SkillRuntime:
    LOAD_TOOL_NAME = "load_skill"

    def __init__(self, catalog: SkillCatalog) -> None: ...

    def refresh(self) -> SkillCatalogSnapshot: ...

    def activate(self, name: str, arguments: str) -> SkillActivation: ...

    def prompt_blocks(self) -> tuple[PromptContextBlock, ...]: ...

    def execution_scope(
        self,
        scope: SkillExecutionScope | None,
        *,
        history: tuple[ChatMessage, ...],
        framework_blocks: tuple[PromptContextBlock, ...],
        approval_provider: ApprovalProvider | None,
        isolated_depth: int = 0,
    ) -> ContextManager[None]: ...

    def set_current_scope(self, name: str) -> SkillExecutionScope: ...

    def current_scope(self) -> SkillExecutionScope | None: ...

    def current_run_context(self) -> SkillRunContext | None: ...

    def visible_tool_names(self) -> frozenset[str] | None: ...

    def allows_tool(self, name: str) -> bool: ...

    def clear(self) -> None: ...
```

`prompt_blocks()` 返回：

| 块 ID | 优先级 | 内容 |
|---|---:|---|
| `active-skills` | `-200` | 已激活的完整 SOP；为空时省略 |
| `skill-catalog` | `-100` | 有效 Skill 的名称与一句说明 |

现有项目指令从优先级 `100` 开始，因此已激活 SOP 位于框架上下文首部。激活集合保存在会话级运行时中并按名称排序；执行范围由 `ContextVar` 建立和恢复。

### SkillLoadTool

模型工具名固定为 `load_skill`，参数结构为：

```json
{
  "type": "object",
  "properties": {
    "name": {"type": "string"},
    "arguments": {"type": "string"},
    "resource": {"type": "string"}
  },
  "required": ["name"]
}
```

- 未传 `resource`：刷新目录、加载入口、替换参数并激活。
- 传入 `resource`：要求 Skill 已激活，只读取该相对资源；此时禁止 `arguments`。
- 共享 Skill：返回结构化范围切换标记，由父 `AgentLoop` 设置当前 run 范围，后续 round 继续主对话。
- 独立 Skill：调用 `SkillExecutor` 完成隔离执行，并把摘要作为工具结果返回。
- 独立执行内部再次请求独立 Skill 时拒绝。
- 激活结果只返回名称、模式、版本和资源清单；完整 SOP 由下一轮动态块注入。

```python
class SkillLoadTool:
    @property
    def definition(self) -> ToolDefinition: ...

    async def execute_async(self, arguments: ToolArguments) -> ToolResult: ...
```

其定义使用 `ToolKind.READ` 和 `parallel_safe=False`。

成功结果使用固定判别字段：

```python
# 共享入口加载
{"action": "activated", "name": name, "mode": "shared", "revision": revision,
 "resources": resources, "set_scope": True}

# 独立入口执行完成
{"action": "completed", "name": name, "mode": "isolated", "summary": summary}

# 单个资源读取
{"action": "resource", "name": name, "path": relative_path, "text": text}
```

父 `AgentLoop` 只在 `tool_name == "load_skill"`、结果成功、`action == "activated"` 且 `set_scope is True` 时调用 `runtime.set_current_scope(name)`；同时由运行时再次确认该名称已激活。

### ToolDefinition 与 ToolRegistry

```python
@dataclass(frozen=True)
class ToolDefinition:
    ...
    parallel_safe: bool = True
```

```python
class ToolRegistry:
    def model_definitions(
        self,
        *,
        visible_names: frozenset[str] | None = None,
    ) -> list[ToolDefinition]: ...
```

- 无执行范围时保持现有延迟工具行为。
- 有执行范围时只返回白名单和 `load_skill`。
- 白名单显式列出的延迟 MCP 工具直接暴露完整定义。
- Skill 执行期间不注入白名单外的延迟工具摘要。
- `AgentLoop` 在执行前调用 `allows_tool()` 二次校验。

### SkillExecutor

```python
class SkillExecutor:
    def __init__(
        self,
        *,
        runtime: SkillRuntime,
        main_llm: BaseLLM,
        llm_config: LLMConfig,
        llm_factory: Callable[[LLMConfig], BaseLLM],
        tool_registry: ToolRegistry,
        tool_executor: ToolExecutor,
        permission: PermissionInterceptor,
        agent_config: AgentConfig,
        workspace_root: Path,
    ) -> None: ...

    async def execute_isolated(
        self,
        definition: SkillDefinition,
        arguments: str,
        *,
        run_context: SkillRunContext,
        mode: AgentMode,
    ) -> SkillExecutionResult: ...
```

模型选择为：

```python
selected_llm = (
    main_llm
    if definition.metadata.model is None
    else llm_factory(replace(llm_config, model=definition.metadata.model))
)
```

共享模式不进入该模型选择逻辑。

### 独立上下文

```python
def select_completed_turns(
    history: Sequence[ChatMessage],
    count: int,
) -> tuple[ChatMessage, ...]: ...
```

完整轮次从普通用户消息开始，到下一条普通用户消息之前结束，并且必须含有最终助手文本。尚未结束的工具调用链或当前用户轮次不进入结果。

摘要策略使用固定中文提示词：

```text
请将以下主对话概括为独立任务所需的背景。
保留用户目标、已确认约束、关键技术事实、已完成工作和未解决问题。
不要添加新结论，不要复述工具原始输出，不要给出执行建议。
```

摘要使用独立执行所选模型和 `tools=[]`，作为优先级 `0` 的临时框架块进入独立对话，不写回主历史。

### EphemeralContextManager

```python
class EphemeralContextManager:
    async def prepare_auto(self, *, build_request, run_deadline) -> PreparedContext: ...

    def record_usage(self, snapshot, usage: UsageObservation) -> None: ...
```

它使用现有 `TokenEstimator` 估算请求，但不归档、不压缩、不创建磁盘会话。请求超过主配置上下文窗口安全线时返回 `independent_context_too_large`，提示减少 `recent.turns` 或改用 `summary`。

### AgentLoop

构造函数新增可选依赖：

```python
skill_runtime: SkillRuntime | None = None
```

运行入口新增可选参数：

```python
async def run(
    self,
    user_text: str,
    *,
    mode: AgentMode,
    approval_provider: ApprovalProvider | None = None,
    initial_skill_scope: SkillExecutionScope | None = None,
    framework_blocks: Sequence[PromptContextBlock] | None = None,
) -> AsyncIterable[AgentEvent]: ...
```

新增主对话协作接口：

```python
async def prepare_skill_run_context(
    self,
    *,
    approval_provider: ApprovalProvider | None,
) -> SkillRunContext: ...

def record_external_exchange(
    self,
    *,
    user_text: str,
    assistant_text: str,
) -> None: ...
```

- `framework_blocks=None` 时保持现有项目记忆刷新流程。
- 独立执行传入父对话项目框架块，跳过项目记忆恢复和记录。
- 当前用户消息写入前建立 `SkillRunContext`。
- 每个 model round 重新获取 `runtime.prompt_blocks()`。
- 成功的共享 Skill 加载结果由父 `AgentLoop` 按工具调用顺序应用到当前 `ContextVar`；不得依赖工具子任务传播上下文变量。
- 工具定义过滤和执行前校验都读取当前执行范围。
- 外部 exchange 同时更新主 memory、JSONL 会话记录和既有后台记忆流程。

### ChatSession

```python
class ChatSession:
    def __init__(
        self,
        *,
        agent: AgentLoop,
        permissions: PermissionService,
        skill_runtime: SkillRuntime | None = None,
        skill_executor: SkillExecutor | None = None,
        mode: AgentMode | None = None,
    ) -> None: ...

    async def send_skill(
        self,
        name: str,
        arguments: str,
        *,
        approval_provider: ApprovalProvider | None = None,
    ) -> AsyncIterable[AgentEvent]: ...
```

- 共享模式激活 Skill，使用 `initial_skill_scope` 调用主 Agent。
- 独立模式准备主上下文，调用执行器，再把 `/{name} {arguments}` 与摘要作为外部 exchange 写入主历史。
- `clear()` 在现有清理之后调用 `skill_runtime.clear()`。

### SkillSlashBridge

```python
class SkillSlashBridge:
    def __init__(
        self,
        *,
        runtime: SkillRuntime,
        registry: SlashCommandRegistry,
    ) -> None: ...

    def refresh(self) -> tuple[SkillDiagnostic, ...]: ...

    def refresh_silent(self) -> None: ...
```

`refresh()` 原子替换动态命令，并返回尚未展示的新诊断。`refresh_silent()` 供补全使用，只更新注册表并缓存诊断。

动态命令统一调用：

```python
await context.controller.execute_skill(skill_name, arguments)
```

### 斜杠基础设施

```python
class SlashCommandRegistry:
    def replace_dynamic_commands(
        self,
        commands: Sequence[SlashCommand],
    ) -> None: ...
```

替换过程先在临时索引验证固定命令、别名和所有 Skill 名称，成功后一次性交换。

```python
class SlashCommandDispatcher:
    def __init__(
        self,
        registry: SlashCommandRegistry,
        *,
        before_dispatch: Callable[[], Sequence[SkillDiagnostic]] | None = None,
    ) -> None: ...
```

```python
class SlashCommandCompleter:
    def __init__(
        self,
        registry: SlashCommandRegistry,
        *,
        before_complete: Callable[[], None] | None = None,
    ) -> None: ...
```

`SlashCommandController` 增加：

```python
def execute_skill(self, name: str, arguments: str) -> Awaitable[None]: ...
```

TUI 实现该方法并复用现有 AgentEvent 渲染逻辑。

## 模块设计

### `skill.models`

**职责：** 定义 Skill 元信息、候选、定义、激活、执行范围、快照和诊断等不可变数据。  
**依赖：** 标准库、现有消息与 Prompt 块类型。  
**限制：** 不读取文件、不持有会话状态。

### `skill.loader`

**职责：** 发现三级目录、读取有界 UTF-8 文件、解析 frontmatter、生成资源清单、执行路径边界检查。  
**依赖：** `skill.models`、PyYAML。  
**关键注释：** 候选发现、目录名校验和符号链接拒绝。

### `skill.catalog`

**职责：** 三级覆盖、文件指纹缓存、启动校验、热更新、最后有效版本保留和诊断去重。  
**依赖：** `skill.loader`、`skill.models`，通过回调读取工具名称。  
**限制：** 不管理激活状态，不调用模型。

### `skill.runtime`

**职责：** 管理激活集合、参数渲染、Prompt 块、`ContextVar` 执行范围和白名单判定。  
**依赖：** `skill.catalog`、`skill.models`、现有 Prompt 模型。  
**关键注释：** 父子范围恢复、热更新重渲染和清理。

### `skill.context`

**职责：** 提取完整轮次、生成摘要输入、提供 `EphemeralContextManager`。  
**依赖：** 现有消息、Token 估算和上下文模型。  
**限制：** 不写磁盘，不修改主历史。

### `skill.executor`

**职责：** 选择独立模型、构造临时历史、运行临时 `AgentLoop`、收集自包含摘要。  
**依赖：** `skill.context`、`skill.runtime`、Agent、LLM、权限和工具抽象。  
**限制：** 不实现第二套模型循环。

### `skill.load_tool`

**职责：** 定义并执行 `load_skill`，区分入口激活与资源读取，按执行模式继续共享 run 或调用独立执行器。  
**依赖：** `skill.runtime`、`skill.executor`、工具基础类型。  
**限制：** 工具结果不携带完整 SOP。

### `skill.slash`

**职责：** 把当前有效 Skill 转为动态命令，同步热更新诊断并原子刷新注册表。  
**依赖：** `skill.runtime`、现有 slash 模型与注册表。  
**限制：** 不调用模型，不拼接 SOP。

### 内置 Skill

```text
src/mycode/skill/builtins/
├── commit/
│   └── SKILL.md
├── review/
│   └── SKILL.md
└── test/
    └── SKILL.md
```

- `commit`：共享模式；允许 `read_file`、`search_code`、`run_command`。
- `review`：共享模式；允许 `read_file`、`find_files`、`search_code`、`run_command`。
- `test`：独立模式，携带最近 3 个完整轮次；允许 `read_file`、`find_files`、`search_code`、`run_command`。

三个文件均使用中文说明和 `{{arguments}}`，通过普通 Skill 加载流程工作。

```toml
[tool.setuptools.package-data]
"mycode.skill" = ["builtins/*/SKILL.md"]
```

## 模块交互

### 启动与第一阶段加载

```text
CLI
 ├─ 创建本地、上下文、记忆和 MCP 工具
 ├─ 创建 SkillLoader → SkillCatalog → SkillRuntime
 ├─ 创建 SkillExecutor 与 load_skill
 ├─ 把 load_skill 注册进 ToolRegistry
 ├─ SkillCatalog.initialize()
 │   ├─ 解析三级 Skill 目录
 │   ├─ 计算同名覆盖
 │   ├─ 校验工具白名单
 │   └─ 校验固定斜杠命令冲突
 ├─ SkillSlashBridge 注册动态命令
 └─ 创建 AgentLoop → ChatSession → TUI
```

首次 Prompt 只注入 `skill-catalog`，不读取或注入完整 SOP。

### 模型加载共享 Skill

```text
主 AgentLoop
  → 模型调用 load_skill
  → SkillLoadTool 刷新并激活 Skill，返回范围切换标记
  → 父 AgentLoop 设置当前共享执行范围
  → 下一 model round
      ├─ active-skills 注入完整 SOP
      ├─ ToolRegistry 只返回该 Skill 白名单
      └─ AgentLoop 执行前再次校验白名单
  → 最终回复和工具历史保留在主 memory
  → run 结束后恢复原工具范围
```

### 模型加载独立 Skill

```text
主 AgentLoop
  → 模型调用 load_skill
  → SkillLoadTool 激活 Skill
  → SkillExecutor 读取父 SkillRunContext
  → 按 none / recent / summary 构造临时上下文
  → 临时 AgentLoop 使用独立工具范围运行
  → 返回 SkillExecutionResult
  → 摘要写入 load_skill 工具结果
  → 主 AgentLoop 根据摘要完成当前回复
```

### 斜杠执行

```text
/skill-name arguments
  → Dispatcher 调用 SkillSlashBridge.refresh()
  → 动态命令调用 TUI.execute_skill()
  → ChatSession.send_skill()
      ├─ shared：主 AgentLoop 执行
      └─ isolated：SkillExecutor 执行后记录外部 exchange
```

### 热更新

```text
补全 / 分发 / load_skill / 新用户 turn
  → SkillCatalog.refresh()
  → 只重读指纹变化的 SKILL.md
  → 重新计算受影响名称
      ├─ 有效：更新目录快照和动态命令
      ├─ 解析失败：回退到较低来源
      └─ 未知工具或命令冲突：保留最后有效版本
  → SkillRuntime 用原始参数重渲染已激活 Skill
```

## 文件组织

```text
src/mycode/
├── skill/
│   ├── __init__.py
│   ├── models.py
│   ├── loader.py
│   ├── catalog.py
│   ├── runtime.py
│   ├── context.py
│   ├── executor.py
│   ├── load_tool.py
│   ├── slash.py
│   └── builtins/
│       ├── commit/SKILL.md
│       ├── review/SKILL.md
│       └── test/SKILL.md
├── agent/
│   ├── loop.py
│   └── scheduler.py
├── tool/
│   ├── base.py
│   └── registry.py
├── slash/
│   ├── models.py
│   ├── controller.py
│   ├── registry.py
│   ├── dispatcher.py
│   ├── completion.py
│   └── builtins.py
├── session.py
├── tui.py
└── cli.py

tests/
├── test_skill_models.py
├── test_skill_loader.py
├── test_skill_catalog.py
├── test_skill_runtime.py
├── test_skill_context.py
├── test_skill_load_tool.py
├── test_skill_executor.py
├── test_skill_slash.py
├── test_skill_agent.py
├── test_skill_cli.py
├── test_skill_e2e.py
└── test_skill_docs.py
```

现有模块调整：

| 文件 | 调整 |
|---|---|
| `pyproject.toml` | 将 `skill/builtins/*/SKILL.md` 声明为包数据 |
| `src/mycode/tool/base.py` | 增加 `parallel_safe` |
| `src/mycode/tool/registry.py` | 支持可见名称过滤和延迟工具显式展开 |
| `src/mycode/agent/scheduler.py` | 非并行安全工具单独调度 |
| `src/mycode/agent/loop.py` | Skill Prompt、工具范围、执行前校验和外部摘要写回 |
| `src/mycode/session.py` | 共享/独立 Skill 路由与清理 |
| `src/mycode/slash/registry.py` | 原子动态命令替换 |
| `src/mycode/slash/dispatcher.py` | 分发前刷新 |
| `src/mycode/slash/completion.py` | 补全前刷新 |
| `src/mycode/slash/controller.py` | Skill 执行协议 |
| `src/mycode/slash/builtins.py` | 移除硬编码 `review` |
| `src/mycode/tui.py` | Skill 事件渲染 |
| `src/mycode/cli.py` | 启动装配与错误报告 |
| `README.md` | 目录、格式、执行模式和热更新说明 |

按行为变化修改现有 Stage 09 斜杠测试；不恢复工作区中已由用户删除的旧测试文件。

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| Skill 入口 | 每个一级目录固定使用 `SKILL.md` | 目录即能力包，最小与扩展形态一致 |
| YAML 解析 | `yaml.safe_load` + 严格字段白名单 | 防止对象构造，并尽早发现拼写错误 |
| 名称关系 | 目录名必须等于 frontmatter `name` | 路径、命令和内部 ID 始终一致 |
| 覆盖状态 | 不可变目录快照，按名称原子替换 | 热更新失败不留下半更新状态 |
| 普通解析失败 | 丢弃当前候选并回退低层版本 | 符合单文件故障隔离要求 |
| 热更新语义错误 | 未知工具或命令冲突保留最后有效版本 | 运行中的能力不因一次错误保存而失效 |
| 变化检测 | 入口及资源的路径、大小、`mtime_ns` 指纹 | 未变化时不重读正文，资源增删也可发现 |
| 激活顺序 | 按 Skill 名称排序 | 不受加载顺序或并行完成顺序影响 |
| 参数替换 | 只执行 `str.replace()` | 无模板执行面，行为直接可预测 |
| Prompt 位置 | `active-skills=-200`，`skill-catalog=-100` | 位于项目指令和记忆块之前 |
| 完整 SOP 传递 | 动态框架块，不写进工具结果 | 每轮稳定存在且不污染普通历史 |
| 工具约束 | schema 过滤 + 执行前二次校验 | 可见性优化不能代替服务端约束 |
| 延迟 MCP 工具 | 白名单显式列出时直接展开 schema | 白名单本身就是明确发现信号 |
| 加载工具调度 | `READ`、`parallel_safe=False` | 不提升写权限，同时保证范围切换顺序 |
| 执行范围 | `ContextVar` | 独立执行结束后自动恢复父范围 |
| 范围切换传播 | 加载工具返回标记，父 `AgentLoop` 应用 | `asyncio.wait_for` 子任务中的 `ContextVar` 修改不会传播到父任务 |
| 独立上下文 | 临时内存 + 不落盘上下文管理器 | 不污染主会话和归档 |
| 独立上下文过大 | 返回稳定错误 | 避免第二套归档生命周期 |
| 摘要模型 | 独立 Skill 选定模型，`tools=[]` | 指定模型对独立流程一致生效 |
| 结果回流 | 独立 Agent 最终响应直接作为摘要 | 避免额外模型调用 |
| 模型覆盖 | 只替换 `model` | 保留协议、地址、凭据和其他选项 |
| 动态斜杠 | 固定命令不变，Skill 命令原子替换 | 帮助、补全、分发共享快照 |
| 固定冲突 | 主名称和别名都保留 | 防止间接覆盖固定入口 |
| 热更新诊断 | 补全缓存，下一次分发展示一次 | 补全接口不能安全输出 UI 消息 |
| 清空行为 | 清激活和范围，不清目录缓存 | 会话重置且避免无意义重解析 |
| 内置 Skill | setuptools 包数据 | 不形成硬编码旁路 |
| 代码注释 | 只注释关键非直观约束 | 保持代码简洁 |

### 错误类型

```python
class SkillError(RuntimeError): ...
class SkillParseError(SkillError): ...
class SkillStartupError(SkillError): ...
class SkillResourceError(SkillError): ...
class SkillExecutionError(SkillError): ...
```

- `SkillParseError` 转换为单候选诊断。
- `SkillStartupError` 由 CLI 捕获并返回退出码 `1`。
- `SkillResourceError` 转换为失败的 `ToolResult`。
- `SkillExecutionError` 转换为独立执行失败摘要，不泄漏内部异常。

## Spec 覆盖

| 需求 | 设计归属 |
|---|---|
| F1 定义格式 | `skill.models`、`skill.loader` |
| F2 参数替换 | `skill.runtime` |
| F3 统一目录型 Skill | `skill.loader`、`skill.load_tool` |
| F4 三级覆盖 | `skill.catalog` |
| F5 启动校验 | `skill.catalog`、`cli` |
| F6 第一阶段加载 | `skill.runtime`、`agent.loop` |
| F7 按需加载工具 | `skill.load_tool`、`skill.loader` |
| F8 激活持续注入 | `skill.runtime`、`agent.loop` |
| F9 共享执行 | `session`、`agent.loop`、`skill.runtime` |
| F10 独立执行 | `skill.context`、`skill.executor`、`session` |
| F11 工具白名单 | `skill.runtime`、`tool.registry`、`agent.scheduler`、`agent.loop` |
| F12 斜杠短命令 | `skill.slash`、`slash.*`、`tui` |
| F13 热更新 | `skill.catalog`、`skill.runtime`、`skill.slash` |
| F14 清空会话 | `session`、`skill.runtime` |
| F15 内置样板 | `skill/builtins/*/SKILL.md`、`pyproject.toml` |
| N1-N3 架构、简洁、中文注释 | 独立包、明确依赖方向、关键路径注释规则 |
| N4 确定性 | 不可变快照、名称排序、原子动态命令 |
| N5 安全边界 | 安全 YAML、真实路径校验、现有权限复用 |
| N6 有界加载 | 限制常量、`skill.loader` |
| N7 热更新开销 | 文件指纹缓存、差量重解析 |
| N8 故障可观测 | `SkillDiagnostic`、CLI 与 TUI 映射 |
| N9 兼容性 | 可选运行时依赖、Stage 09 回归测试 |
| N10 模型隔离 | `skill.executor` 配置复制 |
| N11 测试隔离 | 临时目录、fake LLM、固定工具注册表 |
| N12 文本兼容 | UTF-8、中文内置说明、正文原样保留 |

F1-F15 和 N1-N12 均有明确设计归属。

## 测试设计

- **解析层：** 严格 frontmatter、目录名一致、UTF-8、大小限制、占位符和三种上下文配置。
- **文件层：** 临时三级根目录、缺失入口、资源清单、路径穿越和符号链接。
- **目录层：** 启动 fail-fast、解析回退、最后有效版本、指纹缓存和诊断去重。
- **运行时层：** 多 Skill 激活、热更新重渲染、`ContextVar` 恢复、白名单和 `/clear`。
- **执行层：** fake LLM 覆盖共享历史、三种独立上下文、指定模型、摘要回流、递归拒绝和上下文超限。
- **斜杠层：** 动态注册、固定名称/别名冲突、帮助、补全、参数和热更新诊断。
- **Agent 集成：** 捕获每轮请求，验证两阶段加载、SOP 位置、加载后收窄工具和执行前拒绝。
- **CLI 集成：** 验证 MCP 工具注册后校验白名单，以及错误退出码和中文定位。
- **端到端：** fake LLM 完成 `/test` 独立执行、摘要回流、热修改和 `/clear`。
- **回归：** 运行全部现存测试，不恢复用户已删除的旧测试文件。
