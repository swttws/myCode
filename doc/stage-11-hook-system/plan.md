# myCode Stage 11：Hook 系统技术设计

## 架构概览

Stage 11 新增独立的 `mycode.hook` 领域包，负责 Hook 规则模型、YAML 加载、条件匹配、动作执行、提示词注入、工具前拦截和失败隔离。现有 `AgentLoop`、`ChatSession` 和 `CLI` 只增加生命周期触发点，不复制规则匹配或动作执行逻辑。

Hook 运行链路固定为：

```text
CLI 加载 mycode.hooks.yaml
  → 创建 HookRuntime
  → AgentLoop / ChatSession 触发生命周期事件
  → HookRuntime 按声明顺序匹配规则
  → HookActionRunner 执行动作
  → Prompt 动作进入临时 framework block
  → tool_before 拦截返回结构化 ToolResult
  → 运行期失败只记日志
```

配置加载失败属于启动错误；运行期 Hook 失败属于自动化动作失败，只记录诊断并保持 Agent 主流程继续。Hook 可以收紧工具执行，但不能绕过权限审批、路径边界或工具调度规则。

### HookRuntime

`HookRuntime` 保存已校验规则、当前进程内的 `once` 状态、当前用户请求内的提示词注入块和后台任务引用。它暴露统一触发入口，并为工具执行前提供专门的拦截入口。

所有事件先构造 `HookContext`，再由运行时选择同事件规则。普通事件执行全部命中规则；工具执行前按声明顺序执行，第一条产生拦截的规则返回工具结果，后续规则不再运行。

### HookConfigLoader

加载器从显式路径或工作区默认文件 `mycode.hooks.yaml` 读取 YAML。缺失文件等价于空配置。加载器执行严格字段白名单、版本校验、事件校验、条件校验、动作字段校验和执行控制校验。

`tool_before` 规则禁止 `background: true`，因为该事件可能阻止真实工具执行，必须在当前控制流中给出确定结果。

### HookMatcher

条件匹配集中在 `hook.matcher`。匹配器支持权限规则现有的标量精确和 glob 语义，并扩展正则和反向匹配。

工具事件中的参数上下文使用权限系统相同的规范化主体构建逻辑：路径字段通过 `PathGuard` 规范化，命令字段通过权限策略的命令规范化后参与匹配。这样 Hook 对 `path`、`root`、`command` 的判断与权限规则保持一致。

### HookActionRunner

动作执行器支持四种动作：

- `command`：用当前平台 shell 执行固定命令。
- `prompt`：把固定内容加入当前请求的 Hook framework block。
- `http`：发送 HTTP 请求。
- `sub_agent`：只校验并记录占位结果，不真实运行。

动作执行器统一处理超时、异常、后台任务和日志脱敏。后台动作通过 `asyncio.create_task()` 持有引用，完成后取回异常并记录，避免未观察异常。

### AgentLoop 接入

`AgentLoop` 增加可选 `hook_runtime`。它在用户请求、模型轮次、消息、工具执行前后和错误捕获处触发 Hook。

每次构建模型请求时，`AgentLoop` 把 `hook_runtime.prompt_blocks()` 合并进已有 framework blocks。Prompt 注入不写入普通 memory；当前用户请求结束后由运行时清空。

工具执行前的顺序为：

```text
模型发出工具调用
  → 工具存在性和 Skill 白名单检查
  → 权限系统 before_tool
  → 权限允许后触发 Hook tool_before
  → Hook 未拦截才进入 ToolExecutor
```

因此 Hook 只能进一步拒绝已获准调用，不能放行权限拒绝或审批未通过的调用。

### ChatSession 与 CLI 接入

CLI 增加 Hook 配置装配，并在成功加载后触发系统级 `hooks_loaded`。`ChatSession` 负责会话开始、会话结束和清空事件。会话开始采用惰性触发：第一次发送用户请求前触发一次，避免构造函数里执行异步动作。

TUI 正常退出时调用 `ChatSession.close()`，由该方法触发会话结束事件。若上层未调用 close，Hook 不保证退出后继续运行后台动作。

## YAML 格式

默认工作区配置文件：

```text
<workspace>/mycode.hooks.yaml
```

CLI 可通过 `--hook-config` 指定其他路径。显式路径不存在时报配置错误；默认路径不存在时返回空配置。

### 示例

```yaml
version: 1
hooks:
  - id: inject-test-reminder
    event: model_round_start
    if:
      all:
        round_index: 1
    action:
      type: prompt
      content: "本轮请优先考虑测试影响。"

  - id: block-rm-rf
    event: tool_before
    if:
      all:
        tool: run_command
        arguments.command:
          regex: "\\brm\\b.*-rf"
    action:
      type: prompt
      content: "该命令风险过高，请改用更小范围的安全方案。"
      block: true
      reason: "Hook 安全策略拒绝执行该命令。"

  - id: notify-tool-result
    event: tool_after
    background: true
    action:
      type: http
      method: POST
      url: "http://127.0.0.1:8765/hooks/tool"
      json:
        source: mycode
```

### 顶层字段

| 字段 | 类型 | 规则 |
|---|---|---|
| `version` | 整数 | 必填，必须为 `1` |
| `hooks` | 列表 | 可选，缺失等价于空列表 |

### 规则字段

| 字段 | 类型 | 规则 |
|---|---|---|
| `id` | 字符串 | 可选；非空；未声明时生成 `hook-<index>` |
| `event` | 字符串 | 必填；必须是已知事件 |
| `if` | 映射 | 可选；只能声明 `all` 或 `any` |
| `action` | 映射 | 必填；必须包含 `type` |
| `once` | 布尔 | 可选，默认 `false` |
| `background` | 布尔 | 可选，默认 `false` |
| `timeout_seconds` | 数字 | 可选；必须大于 `0` |

未知字段报错。规则 ID 在同一文件内不得重复。

### 事件枚举

```python
class HookEvent(str, Enum):
    APP_STARTED = "app_started"
    HOOKS_LOADED = "hooks_loaded"
    RUNTIME_ERROR = "runtime_error"
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    SESSION_CLEAR = "session_clear"
    USER_REQUEST_START = "user_request_start"
    USER_REQUEST_END = "user_request_end"
    MODEL_ROUND_START = "model_round_start"
    MODEL_ROUND_END = "model_round_end"
    USER_MESSAGE = "user_message"
    ASSISTANT_MESSAGE = "assistant_message"
    TOOL_RESULT_MESSAGE = "tool_result_message"
    TOOL_BEFORE = "tool_before"
    TOOL_AFTER = "tool_after"
```

## 条件设计

### 逻辑结构

```yaml
if:
  all:
    tool: run_command
    arguments.command:
      regex: "\\bgit\\s+push\\b"
```

```yaml
if:
  any:
    message.content:
      glob: "*TODO*"
    result.ok: false
```

`all` 和 `any` 的值必须是非空 mapping。单条规则内不能同时声明二者，不能嵌套 `all` 或 `any`。

### 匹配值语法

谓词值支持两种形式。

标量简写：

| YAML 值 | 含义 |
|---|---|
| `"src/main.py"` | 精确字符串 |
| `"src/**"` | 权限规则式 glob 字符串 |
| `3` | 精确数字 |
| `true` | 精确布尔 |
| `"!src/**"` | 反向匹配 |
| `"re:\\bpytest\\b"` | 正则匹配 |
| `"!re:\\brm\\b"` | 反向正则 |
| `"glob:src/**"` | 显式 glob |
| `"!glob:.env*"` | 反向显式 glob |

映射形式：

```yaml
arguments.command:
  regex: "\\bpytest\\b"
  not: true
```

```yaml
arguments.path:
  glob: "src/**/*.py"
```

映射形式只允许 `exact`、`glob`、`regex` 三者之一，可选 `not: true`。正则在配置加载时编译校验。

### 上下文字段

事件上下文展平成点号路径：

| 字段 | 来源 |
|---|---|
| `event` | 当前 Hook 事件 |
| `rule_id` | 当前规则 ID，仅日志内部使用 |
| `turn_id` | 当前 Agent turn |
| `round_index` | 当前模型 round |
| `tool` | 工具名 |
| `arguments.<name>` | 规范化后的工具参数 |
| `raw_arguments.<name>` | 原始工具参数 |
| `result.ok` | 工具结果成功状态 |
| `result.error` | 工具结果错误摘要 |
| `message.role` | 消息角色 |
| `message.content` | 消息内容 |
| `error.code` | Agent 错误码 |
| `error.message` | 安全摘要 |
| `session.plan_only` | 当前 plan-only 状态 |

缺失字段匹配失败。敏感字段进入日志前脱敏；条件匹配本身使用内存中的结构化值。

## 核心数据结构

### 动作枚举

```python
class HookActionType(str, Enum):
    COMMAND = "command"
    PROMPT = "prompt"
    HTTP = "http"
    SUB_AGENT = "sub_agent"
```

### 条件模型

```python
@dataclass(frozen=True)
class HookPredicate:
    field: str
    matcher: ValueMatcher


@dataclass(frozen=True)
class HookCondition:
    mode: Literal["all", "any"]
    predicates: tuple[HookPredicate, ...]
```

`condition=None` 表示无条件触发。

### 匹配模型

```python
class MatchKind(str, Enum):
    EXACT = "exact"
    GLOB = "glob"
    REGEX = "regex"


@dataclass(frozen=True)
class ValueMatcher:
    kind: MatchKind
    expected: PermissionScalar | str
    negate: bool = False
```

`PermissionScalar` 复用权限规则的标量范围：字符串、数字和布尔值。

### 动作模型

```python
@dataclass(frozen=True)
class HookAction:
    type: HookActionType
    command: str | None = None
    cwd: str | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    content: str | None = None
    method: str | None = None
    url: str | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    json_body: Mapping[str, object] | None = None
    task: str | None = None
    input: Mapping[str, object] | None = None
    output: str | None = None
    block: bool = False
    reason: str | None = None
```

字段按动作类型校验。`block` 和 `reason` 是工具前拦截的公共动作字段；`block` 只能用于 `tool_before`。

字段归属说明：

| 字段 | 适用动作 | 说明 |
|---|---|---|
| `type` | 全部 | 动作分发依据，决定其余字段如何校验 |
| `command` | `command` | 必填，固定 shell 命令文本 |
| `cwd` | `command` | 可选工作目录，缺省为工作区根目录 |
| `env` | `command` | 可选附加环境变量，只覆盖当前命令动作 |
| `content` | `prompt` | 必填，注入模型请求的提示词；拦截时也可作为拒绝说明兜底 |
| `method` | `http` | HTTP 方法，缺省使用 `POST` |
| `url` | `http` | 必填，请求目标地址 |
| `headers` | `http` | 可选请求头，不参与日志明文输出 |
| `json_body` | `http` | 可选 JSON 请求体，首期不把响应自动注入模型 |
| `task` | `sub_agent` | 必填，占位任务描述，本阶段只记录不执行 |
| `input` | `sub_agent` | 可选输入上下文，为后续 SubAgent 对接预留 |
| `output` | `sub_agent` | 可选期望输出位置，为后续 SubAgent 对接预留 |
| `block` | `tool_before` | 是否把命中结果转成拦截工具结果 |
| `reason` | `tool_before` | 面向模型的安全拒绝说明，不能包含内部规则细节 |

### 规则模型

```python
@dataclass(frozen=True)
class HookRule:
    id: str
    event: HookEvent
    condition: HookCondition | None
    action: HookAction
    once: bool
    background: bool
    timeout_seconds: float | None
    index: int
```

`index` 保存 YAML 声明顺序，用于日志和确定性执行。

### 配置模型

```python
@dataclass(frozen=True)
class HookConfig:
    version: int
    rules: tuple[HookRule, ...]
    path: Path | None = None
```

### 事件上下文

```python
@dataclass(frozen=True)
class HookContext:
    event: HookEvent
    workspace_root: Path
    turn_id: int | None = None
    round_index: int | None = None
    user_text: str | None = None
    message: ChatMessage | None = None
    tool_call: ToolCall | None = None
    tool_definition: ToolDefinition | None = None
    normalized_arguments: Mapping[str, object] = field(default_factory=dict)
    raw_arguments: Mapping[str, object] = field(default_factory=dict)
    tool_result: ToolResult | None = None
    error_code: str | None = None
    error_message: str | None = None
    plan_only: bool = False
```

上下文创建后不可变，避免 Hook 动作修改 Agent 状态。

上下文字段说明：

| 字段 | 说明 |
|---|---|
| `event` | 当前触发事件，所有上下文必填 |
| `workspace_root` | 当前工作区，用于命令工作目录兜底和路径规范化 |
| `turn_id` | Agent 用户请求序号；系统级或会话级事件可为空 |
| `round_index` | 模型轮次；非模型轮次事件可为空 |
| `user_text` | 当前用户请求原文，仅用于用户请求相关事件 |
| `message` | 当前消息对象，用于消息级事件 |
| `tool_call` | 当前工具调用，用于工具级事件 |
| `tool_definition` | 当前工具定义，用于工具级事件和权限规范化 |
| `normalized_arguments` | 复用权限规则规范化后的工具参数，供条件匹配使用 |
| `raw_arguments` | 模型原始工具参数快照，便于少数场景观察原值 |
| `tool_result` | 当前工具结果，用于工具后和工具结果消息事件 |
| `error_code` | 捕获到的稳定错误码 |
| `error_message` | 脱敏后的错误摘要，不包含内部堆栈 |
| `plan_only` | 当前请求是否处于只规划模式 |

### 执行结果

```python
@dataclass(frozen=True)
class HookActionResult:
    ok: bool
    output: str = ""
    error: str | None = None
    blocked: bool = False
    block_reason: str | None = None


@dataclass(frozen=True)
class HookTriggerResult:
    actions: tuple[HookActionResult, ...]
    blocked_tool_result: ToolResult | None = None
```

普通事件的 `blocked_tool_result` 始终为 `None`。

### 提示词注入

```python
@dataclass(frozen=True)
class HookPromptInjection:
    id: str
    rule_id: str
    content: str
    created_event: HookEvent
```

运行时把注入转换为：

```python
PromptContextBlock(
    id=f"hook:{injection.id}",
    kind="hook",
    priority=-150,
    content=injection.content,
)
```

该优先级让 Hook 注入位于项目记忆前，低于已激活 Skill 的完整 SOP。

### 异常

```python
class HookError(RuntimeError): ...
class HookConfigError(HookError): ...
class HookExecutionError(HookError): ...
```

`HookConfigError` 由 CLI 转成启动失败；`HookExecutionError` 只在运行时被捕获并记录。

## 核心接口

### 配置加载

```python
def load_hook_file(path: str | Path) -> HookConfig: ...


def load_hook_config(
    *,
    workspace_root: Path,
    explicit_path: Path | None = None,
) -> HookConfig: ...
```

`load_hook_config()` 负责默认路径解析。`load_hook_file()` 只解析给定文件；文件不存在时返回空配置，除非上层已经判断它是显式路径。

### 条件匹配

```python
def match_condition(
    condition: HookCondition | None,
    context: HookContext,
) -> bool: ...


def flatten_context(context: HookContext) -> Mapping[str, object]: ...


def parse_matcher(value: object, *, location: str) -> ValueMatcher: ...
```

`flatten_context()` 不把完整工具结果、完整环境变量或内部堆栈放入展平结果。

### 工具上下文规范化

```python
def build_tool_hook_context(
    *,
    event: HookEvent,
    workspace_root: Path,
    path_guard: PathGuard,
    call: ToolCall,
    definition: ToolDefinition,
    round_index: int,
    turn_id: int,
    plan_only: bool,
    result: ToolResult | None = None,
) -> HookContext: ...
```

该函数内部调用权限策略已有的主体构建逻辑，获取规范化工具参数。若规范化失败，Hook 记录诊断并返回仅含原始参数的上下文；此时匹配失败不会影响主权限系统已经做出的决定。

### 动作执行器

```python
class HookActionRunner:
    def __init__(
        self,
        *,
        workspace_root: Path,
        http_client_factory: Callable[[], AbstractAsyncContextManager[httpx.AsyncClient]] | None = None,
    ) -> None: ...

    async def run(
        self,
        rule: HookRule,
        context: HookContext,
    ) -> HookActionResult: ...
```

HTTP 测试通过注入 `http_client_factory` 使用 fake transport，不访问真实网络。

### 运行时

```python
class HookRuntime:
    def __init__(
        self,
        *,
        config: HookConfig,
        workspace_root: Path,
        path_guard: PathGuard,
        runner: HookActionRunner | None = None,
    ) -> None: ...

    async def trigger(self, context: HookContext) -> HookTriggerResult: ...

    async def before_tool(
        self,
        *,
        call: ToolCall,
        definition: ToolDefinition,
        round_index: int,
        turn_id: int,
        plan_only: bool,
    ) -> HookTriggerResult: ...

    async def after_tool(
        self,
        *,
        call: ToolCall,
        definition: ToolDefinition,
        result: ToolResult,
        round_index: int,
        turn_id: int,
        plan_only: bool,
    ) -> HookTriggerResult: ...

    def prompt_blocks(self) -> tuple[PromptContextBlock, ...]: ...
    def clear_request_state(self) -> None: ...
```

`before_tool()` 是唯一能返回 `blocked_tool_result` 的入口。`trigger()` 用于非工具事件和系统事件。

### 空运行时

```python
class NullHookRuntime:
    async def trigger(self, context: HookContext) -> HookTriggerResult: ...
    async def before_tool(...) -> HookTriggerResult: ...
    async def after_tool(...) -> HookTriggerResult: ...
    def prompt_blocks(self) -> tuple[PromptContextBlock, ...]: ...
    def clear_request_state(self) -> None: ...
```

测试和未配置 Hook 时可使用空运行时，避免 AgentLoop 中到处判断 `None`。

## 模块设计

### `hook.models`

**职责：** 保存事件、动作、匹配、规则、上下文、执行结果和异常类型。  
**依赖：** 标准库、`mycode.tool.base`、`mycode.llm.base`、`mycode.prompt.models`。  
**限制：** 不读取文件、不执行动作、不导入 Agent。

### `hook.config`

**职责：** 解析 `mycode.hooks.yaml`、校验版本、字段、事件、条件、动作和执行控制。  
**依赖：** `hook.models`、`hook.matcher`、PyYAML。  
**关键注释：** `tool_before` 禁止后台异步，避免拦截结果脱离当前工具控制流。

### `hook.matcher`

**职责：** 解析匹配器、展平上下文、执行精确、glob、正则和反向匹配。  
**依赖：** `fnmatch`、`re`、`hook.models`、权限标量类型。  
**限制：** 不读取配置文件，不执行动作。

### `hook.context`

**职责：** 从 Agent、消息和工具数据构造不可变 `HookContext`，并复用权限系统规范化工具参数。  
**依赖：** `permission.policy.build_subject`、`permission.pathing.PathGuard`、工具和消息类型。  
**关键注释：** 权限已完成判断后，Hook 只复用规范化结果，不重新决定权限。

### `hook.actions`

**职责：** 执行 command、prompt、http、sub_agent 四种动作，统一处理超时、后台任务和失败日志。  
**依赖：** `asyncio`、`subprocess`、`httpx`、`hook.models`。  
**限制：** 不知道 Agent memory，不直接写模型请求。

### `hook.runtime`

**职责：** 按事件选择规则、维护 once 状态、收集提示词注入、构造拦截工具结果、隔离动作失败。  
**依赖：** `hook.models`、`hook.matcher`、`hook.context`、`hook.actions`、`mycode.tool.base`。  
**关键注释：** 拦截回填必须模拟工具结果，而不是 Agent 错误，让模型能继续调整。

### `hook.__init__`

**职责：** 提供包级文档和必要公共类型导出。  
**限制：** 不做初始化副作用。

## Agent 与会话交互

### AgentLoop 触发点

| 位置 | Hook 事件 |
|---|---|
| `run()` 开始，用户消息写入前 | `user_request_start` |
| 用户消息写入 memory 后 | `user_message` |
| 每轮构建模型请求前 | `model_round_start` |
| 每轮模型流结束后 | `model_round_end` |
| 最终助手文本写入前后 | `assistant_message` |
| 权限允许后、真实工具执行前 | `tool_before` |
| 真实工具执行并经权限 after_tool 后 | `tool_after` |
| 工具结果写入 memory 前 | `tool_result_message` |
| `run()` 正常或失败结束前 | `user_request_end` |
| 捕获可恢复 Agent 错误时 | `runtime_error` |

`user_request_end` 在 `finally` 中触发，并清空本次请求内的提示词注入。

### Prompt 合并

`AgentLoop._framework_blocks()` 增加 Hook blocks：

```text
项目记忆 blocks
  + Skill runtime blocks
  + Hook runtime prompt blocks
```

合并后仍交给现有 `PromptBuilder` 排序渲染。Hook block 不写入 `ConversationMemory`，也不进入项目记忆。

### 工具前拦截

工具调用进入真实执行器前执行：

```text
permission before_tool == ALLOW
  → hook_runtime.before_tool(...)
      ├─ 无拦截：加入 executable_calls
      └─ 有拦截：写入 ToolResult、跳过真实工具
```

拦截结果结构：

```python
ToolResult(
    ok=False,
    tool_name=call.name,
    content={
        "tool_call_id": call.id,
        "reason_code": "hook_blocked",
        "hook_rule_id": rule.id,
    },
    error=reason,
)
```

`reason` 优先使用动作的 `reason`，其次使用 prompt 内容，最后使用固定中文兜底。

### ChatSession

构造函数新增可选 `hook_runtime`。新增：

```python
async def start(self) -> None: ...
async def close(self) -> None: ...
```

`send()` 和 `send_skill()` 在第一次请求前惰性调用 `start()`。`clear()` 触发 `session_clear`，再继续执行已有 memory、Skill 和权限清理。

### CLI

命令行参数新增：

```text
--hook-config PATH
```

启动装配顺序：

```text
加载 LLM 配置
  → 加载 MCP 配置
  → 创建权限服务
  → 加载 Hook 配置
  → 创建 HookRuntime
  → 创建工具、Skill、Agent、Session、TUI
  → 触发 app_started / hooks_loaded
```

Hook 配置错误以中文输出到 stderr，返回退出码 `1`。

## 文件组织

```text
src/mycode/
├── hook/
│   ├── __init__.py
│   ├── models.py
│   ├── config.py
│   ├── matcher.py
│   ├── context.py
│   ├── actions.py
│   └── runtime.py
├── agent/
│   └── loop.py
├── session.py
├── cli.py
└── tui.py

tests/
├── test_hook_config.py
├── test_hook_matcher.py
├── test_hook_actions.py
├── test_hook_runtime.py
├── test_hook_agent.py
└── test_hook_cli.py

examples/
└── mycode.hooks.yaml
```

现有模块调整：

| 文件 | 调整 |
|---|---|
| `src/mycode/agent/loop.py` | 增加 Hook runtime、生命周期触发、Prompt block 合并和工具前拦截 |
| `src/mycode/session.py` | 增加会话 start/close/clear Hook 触发 |
| `src/mycode/cli.py` | 增加 `--hook-config`、加载 Hook 配置、装配运行时 |
| `src/mycode/tui.py` | 在退出路径调用 `session.close()` |
| `examples/mycode.hooks.yaml` | 添加声明式 Hook 示例 |
| `README.md` | 增加 Hook 配置、事件、条件和动作说明 |

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 默认配置文件 | `<workspace>/mycode.hooks.yaml` | 与 `mycode.permissions.yaml`、`mycode.mcp.yaml` 风格一致 |
| 显式配置 | CLI 增加 `--hook-config` | 测试和临时运行可指定隔离文件 |
| 缺失默认文件 | 空 Hook 配置 | Hook 是可选自动化能力 |
| 配置错误 | 启动失败 | 避免在规则含义不明确时运行 |
| 规则顺序 | YAML 声明顺序 | 本阶段不做显式优先级 |
| once 状态 | 进程内 set | 满足本阶段，不引入持久化 |
| 条件逻辑 | 单层 `all` 或 `any` | 足够表达固定策略，避免 DSL 膨胀 |
| 匹配语法 | 标量简写 + 映射形式 | 兼容权限规则简写，同时避免复杂值转义困难 |
| 正则校验 | 加载阶段编译 | 尽早发现非法配置 |
| 工具参数规范化 | 复用权限主体构建逻辑 | 路径和命令匹配与权限系统一致 |
| Hook 权限语义 | 只收紧不放宽 | 不破坏现有安全模型 |
| Prompt 注入 | framework block | 不污染普通历史且已有渲染路径可复用 |
| Hook block 优先级 | `-150` | 位于项目记忆前，低于已激活 Skill SOP |
| 工具前异步 | 禁止后台 | 拦截结果必须同步决定 |
| 命令动作 | `asyncio.create_subprocess_shell` | 便于超时和异步集成 |
| HTTP 动作 | `httpx.AsyncClient` 可注入 | 项目已有依赖，测试可 fake transport |
| HTTP 响应 | 首期不注入模型 | 降低泄漏面和复杂度 |
| 子 Agent | 只占位日志 | 满足后续章节接口，不提前实现多 Agent |
| 后台任务 | 保留引用并消费异常 | 避免任务异常无人观察 |
| Hook 失败 | 日志 + 继续 | 自动化辅助不能破坏 Agent 主流程 |
| 拦截反馈 | 结构化 ToolResult | 模型可在下一轮看到并调整 |
| 敏感日志 | 日志只记录规则、事件和安全摘要 | 避免泄漏工具正文、凭据和内部堆栈 |

## 失败处理

| 失败点 | 行为 |
|---|---|
| 默认 Hook 文件缺失 | 使用空配置 |
| 显式 Hook 文件缺失 | CLI 启动失败 |
| YAML 结构非法 | CLI 启动失败 |
| 未知事件或动作 | CLI 启动失败 |
| 非法正则 | CLI 启动失败 |
| `tool_before` 后台动作 | CLI 启动失败 |
| 命令退出码非 0 | 记录 Hook 失败，主流程继续 |
| 命令超时 | 终止子进程，记录失败，主流程继续 |
| HTTP 请求失败 | 记录失败，主流程继续 |
| HTTP 响应非 2xx | 记录失败，主流程继续 |
| Prompt 内容缺失 | 配置加载失败 |
| 子 Agent 动作命中 | 记录占位，不启动模型 |
| Hook 内部异常 | 捕获、脱敏日志、主流程继续 |

## Spec 覆盖

| 需求 | 设计归属 |
|---|---|
| F1 YAML 格式 | `hook.config`、规则字段表 |
| F2 生命周期事件 | `HookEvent`、AgentLoop 和 ChatSession 触发点 |
| F3 条件表达式 | `hook.matcher`、匹配值语法和上下文字段 |
| F4 工具前拦截 | `HookRuntime.before_tool`、工具前顺序、拦截 ToolResult |
| F5 Shell 动作 | `hook.actions` command 分支 |
| F6 提示词注入 | `HookPromptInjection`、Prompt 合并 |
| F7 HTTP 请求 | `hook.actions` http 分支 |
| F8 子 Agent 占位 | `HookActionType.SUB_AGENT`、占位行为 |
| F9 执行控制 | `HookRule.once/background/timeout_seconds`、运行时状态 |
| F10 执行顺序 | 规则 `index`、运行时声明顺序 |
| F11 失败隔离 | 动作执行器、运行时异常捕获、失败处理表 |
| F12 装配接入 | CLI、AgentLoop、ChatSession 接口 |
| N1 架构边界 | 独立 `hook` 包和接入表 |
| N2 安全保守 | 权限后触发、只收紧不放宽 |
| N3 代码简洁 | 6 个 Hook 模块，不做优先级和持久化 |
| N4 中文注释 | 模块设计关键注释位置 |
| N5 确定性 | YAML 顺序、once set、稳定 block ID |
| N6 测试隔离 | fake HTTP、临时目录、fake LLM |
| N7 可观测性 | 规则 ID、事件名和失败日志 |
| N8 兼容性 | 可选 NullHookRuntime、现有流程只加触发点 |
| N9 有界执行 | 超时和后台任务管理 |
| N10 配置可审计 | YAML 安全解析、无远程加载、无脚本条件 |

F1-F12 和 N1-N10 均有明确设计归属。

## 测试设计

- **配置层：** 加载空配置、合法 YAML、缺少事件、缺少动作、未知字段、非法事件、非法动作、重复 ID、非法正则和 `tool_before background`。
- **匹配层：** 精确、隐式 glob、显式 glob、正则、反向匹配、缺失字段、`all`、`any` 和非法混用。
- **上下文层：** 工具参数中 `path`、`root` 和 `command` 使用权限规范化结果；普通消息和工具结果字段可被匹配。
- **动作层：** command 前台成功、退出码失败、超时、后台异常消费、prompt 注入、HTTP fake transport 成功/失败、sub_agent 占位。
- **运行时层：** once 当前进程只执行一次、规则声明顺序、第一条拦截停止后续拦截、动作失败不影响后续规则。
- **Agent 集成：** fake LLM 触发工具调用，Hook 拦截后 fake 工具未执行，工具结果进入下一轮请求；Prompt 注入进入 framework block 且不写入 memory。
- **权限兼容：** 权限拒绝或审批未通过时 Hook 不放行；权限允许后 Hook 可进一步拒绝。
- **CLI 集成：** 默认文件缺失可启动，显式非法文件返回退出码 `1`，合法配置注入 Agent。
- **会话层：** 第一次发送前触发 session_start，`clear()` 触发 session_clear，TUI close 触发 session_end。
- **回归：** 运行现有 Agent、工具、权限、Skill、上下文、斜杠和配置测试。
