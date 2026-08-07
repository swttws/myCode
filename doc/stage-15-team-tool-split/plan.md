# Stage 15 Team 工具拆分 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将当前聚合式 team 工具改造成 21 个无 `action` 二次路由的原子工具，并保持团队业务、持久化、权限和 Agent Loop 语义不变。

**Architecture:** 模型可见层按单一意图拆分为四组具体工具类；工具类直接调用 `TeamService`、`TaskBoard` 或 `MailboxStore`，只共享无状态校验和结果辅助函数。CLI 主会话和 Member Worker 使用两个显式注册函数，角色可见性由集中式工具名集合和现有策略类控制。

**Tech Stack:** Python 3.10+、现有 `ToolDefinition`/`ToolResult`/`ToolRegistry`、`pytest`、PyYAML 配置和当前 TeamStore/TaskBoard/Mailbox/Worktree/Backend 实现。

---

## 架构概览

### 模型工具层

删除 `TeamTool` 的 `name + action + oneOf` 分发器，改为四个小模块中的具体工具类：

- `lifecycle_tools.py`：团队创建、接管、状态、归档。
- `orchestration_tools.py`：批次启动、批次整合、成员启动、成员终止。
- `task_tools.py`：任务创建、列表、读取、更新、删除、领取、状态转换。
- `protocol_tools.py`：计划提交、计划决策、消息、状态更新、关停请求、关停响应。

每个工具类只实现 `definition` 和 `execute_async`，在方法内完成参数校验、身份绑定、一次业务调用和结果转换。工具之间不互相调用。

### 共享辅助层

`tool_helpers.py` 只提供无状态函数：

- `validate_object_arguments(arguments, allowed_fields)`：验证对象类型和未知字段。
- `required_string/required_int/required_bool` 与对应可选值函数。
- `success_result(tool_name, content)` 和 `failure_result(tool_name, reason_code, message_zh, field=None)`。
- `task_content(task)`、`message_content(receipt)` 等纯结果转换函数。

不新增基类、依赖注入容器、工具 dispatcher 或通用运行时上下文对象。工具构造器直接接收 `service`，Member 专属工具额外接收 `member_name`。

### 注册与角色层

- 主 CLI 通过 `register_parent_team_tools(registry, service)` 注册主会话需要的团队工具全集。
- Member Worker 通过 `register_member_team_tools(registry, service, member_name)` 注册 Member 工具集合。
- `tool_names.py` 集中维护完整工具名、旧工具名、PARENT/LEAD/MEMBER 可见集合以及读写集合。
- `TeamService.visible_team_tools` 只根据当前是否激活返回 PARENT/LEAD 集合的交集；`TeamToolPolicy.visible_names` 处理角色和协调器限制。

## 核心数据结构与接口

### 工具名常量

`src/mycode/team/tool_names.py` 定义：

```python
TEAM_TOOL_NAMES: frozenset[str]
PARENT_TEAM_TOOL_NAMES: frozenset[str]
LEAD_TEAM_TOOL_NAMES: frozenset[str]
MEMBER_TEAM_TOOL_NAMES: frozenset[str]
READ_TEAM_TOOL_NAMES: frozenset[str]
WRITE_TEAM_TOOL_NAMES: frozenset[str]
LEGACY_TEAM_TOOL_NAMES: frozenset[str]
```

集合内容必须与 `spec.md` 的 21 个名称和角色矩阵完全一致。禁止在其他模块重新写字符串集合。

### TeamService 显式入口

修改 `src/mycode/team/service.py`：

```python
async def create_team(self, team_name: str, *, goal: str | None = None) -> TeamSnapshot
async def attach_team(self, team_name: str) -> TeamSnapshot
```

两者共享私有的租约获取和运行时注册步骤：

- `create_team` 在 `team.json` 已存在时抛出 `TeamError(code="team_exists", phase="create")`，不得覆盖现有团队。
- `attach_team` 在 `team.json` 不存在时抛出 `TeamError(code="team_not_found", phase="attach")`，存在时复用现有 `_validate_reattach`。
- 现有 `create_or_attach` 改为内部兼容实现或删除；任何模型工具不得继续调用它。

### 工具注册接口

在 `src/mycode/team/tools/__init__.py` 暴露两个简单函数：

```python
def register_parent_team_tools(registry: ToolRegistry, service: TeamService) -> None

def register_member_team_tools(
    registry: ToolRegistry,
    service: object,
    *,
    member_name: str,
) -> None
```

函数体只实例化具体工具并逐个调用 `registry.register`，不做动态扫描或反射。

### 具体工具接口

所有类遵循现有 Tool/AsyncTool 协议。以下仅列出构造器和业务输入；工具定义中的中文描述、字段描述和 `additionalProperties: false` 在对应模块中直接声明。

| 类 | 构造器 | 业务输入 |
|---|---|---|
| `TeamCreateTool` | `(service)` | `team_name`, `goal?` |
| `TeamAttachTool` | `(service)` | `team_name` |
| `TeamStatusTool` | `(service)` | 无 |
| `TeamArchiveTool` | `(service)` | 无 |
| `TeamBatchStartTool` | `(service)` | `goal` |
| `TeamBatchIntegrateTool` | `(service)` | `batch_id` |
| `TeamMemberSpawnTool` | `(service)` | `member_name`, `role_name`, `role_revision`, `requested_backend`, `task_id`, `batch_id`, `goal`, `read_only`, `approval_required` |
| `TeamMemberTerminateTool` | `(service)` | `member_name`, `force?` |
| `TeamTaskCreateTool` | `(service, member_name=None)` | `task_id`, `batch_id`, `title`, `description`, `dependency_ids?`, `kind` |
| `TeamTaskListTool` | `(service, member_name=None)` | `batch_id?` |
| `TeamTaskGetTool` | `(service, member_name=None)` | `task_id` |
| `TeamTaskUpdateTool` | `(service, member_name=None)` | `task_id`, `expected_revision`, patch fields |
| `TeamTaskDeleteTool` | `(service)` | `task_id`, `expected_revision` |
| `TeamTaskClaimTool` | `(service, member_name=None)` | `task_id`, `expected_revision`, `member_name?` |
| `TeamTaskTransitionTool` | `(service, member_name=None)` | `task_id`, `expected_revision`, `state`, result/error fields? |
| `TeamPlanSubmitTool` | `(service, member_name)` | plan fields and message fields |
| `TeamPlanDecideTool` | `(service)` | task/revision/approval fields |
| `TeamMessageSendTool` | `(service, member_name=None)` | message fields |
| `TeamStatusUpdateTool` | `(service, member_name)` | status message fields |
| `TeamShutdownRequestTool` | `(service)` | shutdown message fields |
| `TeamShutdownResponseTool` | `(service, member_name)` | response message fields |

Lead-only tools must reject a bound Member construction; Member-only tools must require a non-empty bound member name at construction. This prevents accidental registration under the wrong runtime.

## 模块设计

### `src/mycode/team/tools/lifecycle_tools.py`

**职责：** 团队生命周期的四个原子工具。

**直接依赖：** `TeamService`、`TeamSnapshot`、`ToolDefinition`、`tool_helpers`。

**验证：** 创建/接管的团队名非空；状态和归档不接受任何参数；未知字段在调用服务前失败。

### `src/mycode/team/tools/orchestration_tools.py`

**职责：** 批次和成员编排工具。

**直接依赖：** `TeamService`、`MemberBackend`、`ToolDefinition`、`tool_helpers`。

**验证：** 后端值使用现有 `MemberBackend` 枚举；角色版本、任务、批次和审批布尔值严格校验；成员终止的 `force` 默认 `False`。

### `src/mycode/team/tools/task_tools.py`

**职责：** 任务板的七个原子工具。

**直接依赖：** `TaskBoard`/`service.task_board`、`TeamTask`、`TaskPatch`、`TaskResult`、`tool_helpers`。

**验证：** 所有修改操作验证 `expected_revision`；Member 绑定时对更新、领取和状态转换做所有者校验；进入 `running` 前保留审批和 blocked 恢复限制。

### `src/mycode/team/tools/protocol_tools.py`

**职责：** 计划、消息、状态和关停协议工具。

**直接依赖：** `TeamService.send_message`、`TeamMessage`、`MessageProtocol`、`TaskPatch`、`tool_helpers`。

**验证：** 发送者从绑定身份或 Lead 默认值解析；广播时禁止要求 `target_name`；计划决策先检查当前 `plan_revision`；Member 不能伪造 sender。

### `src/mycode/team/policy.py`

**职责：** 将现有 `TeamToolPolicy` 和 `TeamPermissionInterceptor` 的工具集合迁移到 `tool_names.py`。

**规则：** 先做角色隐藏判断，再执行协调器特例和通用权限判断；任何新工具名不在允许集合时返回现有结构化拒绝结果。

### `src/mycode/team/runtime.py`

**职责：** Member Worker 注册新 Member 工具并继续使用现有 Member 权限策略和工作树上下文。

### `src/mycode/cli.py`

**职责：** 用 `register_parent_team_tools` 替换三次 `TeamTool` 注册；在工具注册完成后继续把 `team_service.visible_team_tools` 传给 AgentLoop。

## 模块交互

```text
CLI 创建 TeamService
  -> 注册主会话工具全集
  -> AgentLoop 根据 visible_team_tools 过滤模型 schema
  -> 权限拦截器检查角色/协调器/全局权限
  -> 原子工具校验参数
  -> TeamService 或 TaskBoard 执行一次业务操作
  -> ToolResult 进入事件流、Hook 和对话历史
```

```text
Worker 创建 Member Service
  -> 注册绑定 member_name 的 Member 工具
  -> Member Policy 过滤工具
  -> Member 工具发送 mailbox 消息或更新 TaskBoard
  -> TeamMemberRuntime 继续处理 checkpoint、ack 和状态回报
```

## 文件组织

### 新建

- `src/mycode/team/tool_names.py`：工具名和角色集合。
- `src/mycode/team/tool_helpers.py`：无状态参数校验、结果构造和纯转换函数。
- `src/mycode/team/tools/__init__.py`：工具类导出和两个注册函数。
- `src/mycode/team/tools/lifecycle_tools.py`：生命周期工具。
- `src/mycode/team/tools/orchestration_tools.py`：批次和成员工具。
- `src/mycode/team/tools/task_tools.py`：任务工具。
- `src/mycode/team/tools/protocol_tools.py`：协议工具。
- `tests/test_team_tools.py`：新工具定义、校验、执行和角色场景。

### 修改

- `src/mycode/team/service.py`：增加 `create_team`、`attach_team`，保留现有业务入口。
- `src/mycode/team/policy.py`：迁移工具集合、协调器集合和隐藏判断。
- `src/mycode/team/runtime.py`：注册 Member 工具集合。
- `src/mycode/cli.py`：注册主会话工具集合。
- `tests/test_team_service.py`：创建/接管和可见性测试。
- `tests/test_team_runtime.py`：Member 工具名称和工作流测试。
- `tests/test_team_e2e.py`：端到端工具名称和流程断言。
- `tests/test_hook_session_cli.py`：CLI 注册集合断言。
- `tests/test_team_tool.py`：迁移为新工具测试，最终删除旧聚合测试文件或保留为新文件的迁移提交记录。
- `tests/test_docs.py`、`README.md`、`examples/*.yaml`：更新工具名和中文说明。
- `src/mycode/team/tool.py`：删除旧聚合实现及其导出。

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 对外 schema | 每个工具独立对象 schema | 避免模型在大 `oneOf` 中二次分类 |
| 代码抽象 | 无基类，仅共享无状态函数 | 满足低抽象和浅调用链要求 |
| 业务边界 | TeamService/TaskBoard/Mailbox 保持单一事实源 | 不复制状态机和持久化规则 |
| 角色边界 | 集中常量 + 现有策略类 | 统一可见性、权限和配置校验 |
| 注册方式 | 显式 tuple/list 注册 | 代码可读、启动顺序清晰、便于测试 |
| 兼容方式 | 旧工具名直接移除 | 避免模型继续选择旧聚合工具 |
| 文案 | 所有模型/用户提示中文 | 满足工具提示语言要求 |
| 读写分类 | 依据实际副作用分类 | 保持批处理和审批行为准确 |

## Spec 覆盖检查

- F1-F5：由四个工具模块和工具名集合实现。
- F6、F8：由 `tool_names.py`、`TeamToolPolicy`、CLI/Worker 注册实现。
- F7、N2、N6、N7：由四个工具模块的独立 schema 和 `tool_helpers.py` 实现。
- F9：由 CLI、Worker、配置/Hook/Skill/文档迁移任务实现。
- F10、N8：由新工具单测、角色/权限测试、端到端测试和文档测试实现。
- N1、N3、N4：通过复用现有服务、权限、AgentLoop 和 Worker 链路保证。
- N5：通过文件组织、命名表和代码审查检查。

