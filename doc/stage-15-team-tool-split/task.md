# Stage 15 Team 工具拆分 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `src/mycode/team/tool_names.py` | 21 个新工具名、角色集合、读写集合和旧名集合 |
| 新建 | `src/mycode/team/tool_helpers.py` | 无状态参数校验、中文失败结果和结果转换 |
| 新建 | `src/mycode/team/tools/__init__.py` | 工具导出及主会话/Member 注册函数 |
| 新建 | `src/mycode/team/tools/lifecycle_tools.py` | 创建、接管、状态、归档工具 |
| 新建 | `src/mycode/team/tools/orchestration_tools.py` | 批次和成员编排工具 |
| 新建 | `src/mycode/team/tools/task_tools.py` | 七个任务工具 |
| 新建 | `src/mycode/team/tools/protocol_tools.py` | 计划、消息、状态和关停工具 |
| 修改 | `src/mycode/team/service.py` | 增加只创建和只接管入口 |
| 修改 | `src/mycode/team/policy.py` | 迁移角色可见性和权限集合 |
| 修改 | `src/mycode/team/runtime.py` | 注册绑定 Member 工具 |
| 修改 | `src/mycode/cli.py` | 注册主会话工具集合 |
| 修改 | `tests/test_team_service.py` | 创建/接管和可见性测试 |
| 新建/迁移 | `tests/test_team_tools.py` | 新工具 schema、执行、错误和角色测试 |
| 修改 | `tests/test_team_runtime.py` | Member 工具注册和工作流测试 |
| 修改 | `tests/test_team_e2e.py` | Lead→Member→整合端到端测试 |
| 修改 | `tests/test_hook_session_cli.py` | CLI 注册集合断言 |
| 修改 | `tests/test_docs.py` | README 和示例中的工具名断言 |
| 修改 | `README.md` | Stage 15 工具目录、角色和迁移说明 |
| 修改 | `examples/mycode.*.yaml` | 迁移工具白名单/Hook 引用 |
| 删除 | `src/mycode/team/tool.py` | 删除旧 TeamTool 聚合实现 |

## T1：建立工具名和辅助函数契约

**文件：** `src/mycode/team/tool_names.py`、`src/mycode/team/tool_helpers.py`、`tests/test_team_tools.py`

**依赖：** 无

**步骤：**

1. 先在测试中断言完整工具名集合包含 21 个新名称，并且不包含 `team`、`team_lead`、`team_member`。
2. 在测试中断言角色集合：未激活主会话只包含创建/接管/状态，Lead 包含编排/任务/审批/消息/关停请求/归档，Member 包含任务/计划提交/消息/状态/关停响应。
3. 在测试中为 `validate_object_arguments`、`required_string`、`required_int`、`required_bool`、`success_result`、`failure_result` 写出成功和失败调用样例。
4. 实现 `tool_names.py`，所有集合使用 `frozenset[str]`，集合中的名称只定义一次。
5. 实现 `tool_helpers.py`。校验失败必须返回包含 `reason_code`、`field` 和中文 `message` 的 `ToolResult`；辅助函数不访问服务、不写文件。
6. 所有新工具文案使用中文；`reason_code` 使用英文小写下划线。

**验证：**

```powershell
pytest tests/test_team_tools.py -k "tool_names or helper" -q
```

期望：新增测试全部通过，且辅助函数测试不产生文件或服务调用。

## T2：拆分 TeamService 创建与接管入口

**文件：** `src/mycode/team/service.py`、`tests/test_team_service.py`

**依赖：** T1

**步骤：**

1. 为 `create_team` 添加失败测试：目标目录已有 `team.json` 时返回 `TeamError.code == "team_exists"`，原团队文件内容不变。
2. 为 `attach_team` 添加失败测试：目标目录没有 `team.json` 时返回 `TeamError.code == "team_not_found"`。
3. 添加成功测试，确认两个入口都建立 Lead lease、Mailbox、TaskBoard，并返回活动快照。
4. 将现有 `create_or_attach` 的租约和注册逻辑提取到私有小函数；`create_team` 和 `attach_team` 只负责存在性分支。
5. 删除或改为内部不可调用的 `create_or_attach`；新工具不得引用该名称。
6. 保持 `_validate_reattach`、团队状态、租约、目录结构和持久化字段不变。

**验证：**

```powershell
pytest tests/test_team_service.py -k "create_team or attach_team or visibility" -q
```

期望：创建、接管、重复创建、接管不存在团队和原有可见性测试全部通过。

## T3：实现生命周期工具

**文件：** `src/mycode/team/tools/lifecycle_tools.py`、`tests/test_team_tools.py`

**依赖：** T1、T2

**步骤：**

1. 为 `TeamCreateTool`、`TeamAttachTool`、`TeamStatusTool`、`TeamArchiveTool` 各写 schema 测试，确认没有 `action`/`operation`、未知字段被拒绝、描述和字段说明为中文。
2. 实现四个工具类，构造器只接收 `service`；无参数工具的 schema 使用空 `required` 列表。
3. `team_create` 调用 `service.create_team`，`team_attach` 调用 `service.attach_team`，状态和归档分别调用现有服务入口。
4. 成功结果保留现有返回字段：团队名、状态、成员数、批次数或归档状态。
5. 将 `TeamError` 转为结构化中文失败，不把 Python traceback 放入 `ToolResult`。

**验证：**

```powershell
pytest tests/test_team_tools.py -k "lifecycle" -q
```

期望：四个生命周期工具的 schema、成功路径、缺参、多参和服务异常测试通过。

## T4：实现批次和成员编排工具

**文件：** `src/mycode/team/tools/orchestration_tools.py`、`tests/test_team_tools.py`

**依赖：** T1、T2

**步骤：**

1. 为 `TeamBatchStartTool`、`TeamBatchIntegrateTool`、`TeamMemberSpawnTool`、`TeamMemberTerminateTool` 添加 schema 测试。
2. 在 schema 中为 `requested_backend` 使用 `auto`、`tmux`、`terminal`、`in_process` 枚举；`read_only` 和 `approval_required` 必须是布尔值。
3. 实现批次工具，分别直接调用 `service.start_batch` 和 `service.integrate_batch`。
4. 实现成员工具，直接组装现有 `spawn_member`/`terminate_member` 参数；`force` 缺省为 `False`。
5. 保留服务返回的批次、成员、整合提交和冲突任务字段。

**验证：**

```powershell
pytest tests/test_team_tools.py -k "batch or member_spawn or member_terminate" -q
```

期望：枚举/布尔类型错误在服务调用前失败，成功调用字段与现有服务测试一致。

## T5：实现任务读取和创建工具

**文件：** `src/mycode/team/tools/task_tools.py`、`tests/test_team_tools.py`

**依赖：** T1

**步骤：**

1. 为 `TeamTaskCreateTool`、`TeamTaskListTool`、`TeamTaskGetTool` 写失败测试，覆盖缺少任务字段、未知字段、空依赖列表和不存在任务。
2. 实现任务创建，使用现有 `TeamTask` 和 `TaskKind`，通过 `service.task_board.create` 写入。
3. 实现任务列表和读取，分别调用 `service.task_board.list`、`service.task_board.get`，声明为 `ToolKind.READ`。
4. 将任务结果统一转换为任务标识、批次、标题、状态、类型、所有者、revision、计划版本、审批状态、依赖和结果摘要。
5. 允许 Member 使用同一组读取/创建类，但 Member 身份只能来自构造器，不接受模型自定义 sender/owner。

**验证：**

```powershell
pytest tests/test_team_tools.py -k "task_create or task_list or task_get" -q
```

期望：只读工具不改变任务板 revision，创建工具保留现有任务字段和默认状态。

## T6：实现任务修改工具

**文件：** `src/mycode/team/tools/task_tools.py`、`tests/test_team_tools.py`

**依赖：** T5

**步骤：**

1. 为 `TeamTaskUpdateTool`、`TeamTaskDeleteTool`、`TeamTaskClaimTool`、`TeamTaskTransitionTool` 写过期 revision、未知字段、成员身份伪造和所有者不匹配的失败测试。
2. 实现更新和删除，调用 `service.task_board.update/delete`，严格要求非负 `expected_revision`。
3. 实现领取：Lead 使用显式 `member_name`，Member 忽略或拒绝不匹配的参数并使用绑定成员。
4. 实现状态转换：构造可选 `TaskResult`，保留 `blocked` 恢复限制、审批前进入 `running` 限制和错误字段。
5. 成功和失败都不得在工具层直接修改任务对象之外的状态；所有并发保护交给 TaskBoard。

**验证：**

```powershell
pytest tests/test_team_tools.py -k "task_update or task_delete or task_claim or task_transition" -q
```

期望：过期 revision 和身份错误不调用对应 TaskBoard 写方法，正常路径返回最新 revision。

## T7：实现计划、消息和关停工具

**文件：** `src/mycode/team/tools/protocol_tools.py`、`tests/test_team_tools.py`

**依赖：** T1、T6

**步骤：**

1. 为六个协议工具写 schema 和协议字段测试，确认所有描述、字段说明和失败消息为中文。
2. 实现 `TeamMessage` 构造辅助：发送者使用绑定 Member 身份或 Lead 默认值；Member 发送者不允许被模型覆盖。
3. 实现 `TeamMessageSendTool`，广播为 `True` 时不要求 `target_name`，定向消息缺少目标时失败。
4. 实现 `TeamPlanSubmitTool`：先更新任务计划，再转换为 `awaiting_approval`，最后发送 `PLAN_SUBMIT` 消息。
5. 实现 `TeamPlanDecideTool`：先读取并比对 `plan_revision`，再更新审批状态，批准时转换为 `running`，可选发送 `PLAN_DECISION` 消息。
6. 实现状态更新、关停请求和关停响应，分别使用现有协议枚举，并保留 checkpoint/ack 由 Worker 负责的边界。

**验证：**

```powershell
pytest tests/test_team_tools.py -k "plan or message or status_update or shutdown" -q
```

期望：消息协议、目标、发送者、任务/批次元数据和计划版本校验全部通过。

## T8：建立显式注册函数和导出

**文件：** `src/mycode/team/tools/__init__.py`、`tests/test_team_tools.py`

**依赖：** T3、T4、T5、T6、T7

**步骤：**

1. 测试 `register_parent_team_tools` 注册的名称集合等于主会话允许的全集，并且每个名称只注册一次。
2. 测试 `register_member_team_tools` 只注册 Member 集合，并确认任务/消息类带有指定 `member_name`。
3. 实现两个注册函数，使用显式 tuple，不使用反射、模块扫描或动态名称拼接。
4. 在 `__init__.py` 导出 21 个工具类和两个注册函数；不要从旧 `team.tool` 重新导出。

**验证：**

```powershell
pytest tests/test_team_tools.py -k "registration" -q
```

期望：注册集合和构造参数固定可预测，重复注册会继续由 `ToolRegistry` 拒绝。

## T9：迁移角色可见性和权限策略

**文件：** `src/mycode/team/policy.py`、`src/mycode/team/service.py`、`tests/test_team_tool.py`、`tests/test_team_service.py`

**依赖：** T1、T8

**步骤：**

1. 将旧 `team`/`team_lead`/`team_member` 可见性测试改写为新工具集合测试。
2. 修改 `TeamToolPolicy.visible_names` 使用 `tool_names.py` 的 PARENT/LEAD/MEMBER 集合。
3. 保留协调器模式对 Lead 的 `Agent` 禁止、shell Git 白名单和写工具限制。
4. 保留 Member 的工作区写入审批判断；新 team 任务/消息工具不得绕过普通 PermissionInterceptor。
5. 修改 `TeamService.visible_team_tools`：未激活只返回 `team_create`、`team_attach`、`team_status`；激活后返回 Lead 集合与候选工具的交集。
6. 失败结果中的 reason code 继续使用稳定英文，用户消息使用中文。

**验证：**

```powershell
pytest tests/test_team_tool.py tests/test_team_service.py -k "policy or visible or coordinator or member_approval" -q
```

期望：三个角色的集合精确匹配 spec，隐藏工具和协调器限制均返回结构化拒绝。

## T10：迁移 CLI 和 Member Worker

**文件：** `src/mycode/cli.py`、`src/mycode/team/runtime.py`、`src/mycode/team/worker.py`、`tests/test_hook_session_cli.py`、`tests/test_team_runtime.py`

**依赖：** T8、T9

**步骤：**

1. 将 CLI 的三次 `TeamTool(...)` 注册替换为 `register_parent_team_tools(tool_registry, team_service)`。
2. 保留 `team_service.visible_team_tools` 作为 AgentLoop 的可见工具 provider。
3. 将 Member Runtime 的 `TeamTool(..., name="team_member")` 替换为 `register_member_team_tools(..., member_name=member_name)`。
4. 删除 Worker/Runtime 对旧 `TeamTool` 的导入。
5. 更新 CLI 和 Runtime 测试的注册名称断言。

**验证：**

```powershell
pytest tests/test_hook_session_cli.py -k "team_tool or registered" -q
pytest tests/test_team_runtime.py -k "member.*tool or team.*tool" -q
```

期望：CLI 可创建 Agent，Member Worker 只看到绑定的 Member 工具，旧名称不出现在注册结果。

## T11：迁移扩展引用、文档和配置

**文件：** `src/mycode/subagent/config.py`、`src/mycode/subagent/loader.py`、`src/mycode/hook/config.py`、`src/mycode/permission/config.py`、`src/mycode/skill/catalog.py`、`README.md`、`examples/*.yaml`、`tests/test_docs.py`

**依赖：** T1、T9、T10

**步骤：**

1. 让 `src/mycode/subagent/config.py` 和 `src/mycode/subagent/loader.py` 的工具白名单校验使用 `TEAM_TOOL_NAMES`，遇到旧名称时返回包含字段位置和中文原因的配置错误。
2. 在 `src/mycode/hook/config.py` 检查 `tool` 条件中的精确旧名称，在 `src/mycode/permission/config.py` 检查规则的精确旧工具名，在 `src/mycode/skill/catalog.py` 保留现有未知工具诊断并为旧名称补充迁移提示；未知新名称继续沿用现有未知工具错误。
3. 将示例 YAML 中的 team 工具引用替换为新名称，按角色只列出必要集合。
4. 更新 README Stage 14/Stage 15 说明，列出新工具目录、角色可见性、中文提示和不再支持旧名。
5. 将 `tests/test_docs.py` 的断言改为检查 21 个新名称和旧名称移除说明。

**验证：**

```powershell
pytest tests/test_docs.py -q
```

期望：文档和示例包含完整新目录、旧名称迁移说明及中文提示约束。

## T12：删除旧聚合实现并迁移工具测试

**文件：** `src/mycode/team/tool.py`、`tests/test_team_tool.py`、`tests/test_team_tools.py`

**依赖：** T10、T11

**步骤：**

1. 将现有 `tests/test_team_tool.py` 中针对聚合 `action` 的测试迁移到 `tests/test_team_tools.py`，每个测试改为直接调用具体工具类。
2. 将旧的未知 action/未知参数测试改为每个工具的未知字段和缺失字段测试。
3. 迁移成员身份、防伪造、计划版本、审批和协议测试到对应工具测试。
4. 使用 `rg` 检查 `src/` 和 `tests/` 不再导入 `mycode.team.tool.TeamTool`。
5. 删除 `src/mycode/team/tool.py` 和只服务旧聚合实现的测试文件；不要删除仍被其他模块使用的模型或服务代码。

**验证：**

```powershell
rg -n "TeamTool|team_lead|team_member" src tests
rg -n "action" src/mycode/team tests/test_team_tools.py
pytest tests/test_team_tools.py -q
```

期望：搜索无旧实现引用；新工具单元测试全部通过。

## T13：端到端和全量验证

**文件：** `tests/test_team_e2e.py`、`tests/test_team_integration.py`、必要时 `tests/test_agent_loop.py`

**依赖：** T12

**步骤：**

1. 将端到端场景中的旧工具调用替换为 `team_create`、`team_batch_start`、`team_task_create`、`team_member_spawn`、Member 任务/协议工具和 `team_batch_integrate`。
2. 断言每一轮模型收到的工具定义只包含当前角色允许集合，且工具结果 `tool_name` 为新名称。
3. 保留任务依赖、提交整合、冲突报告、Member checkpoint 和 shutdown ack 断言。
4. 运行团队相关测试，再运行完整测试套件。

**验证：**

```powershell
pytest tests/test_team_*.py tests/test_agent_loop.py tests/test_hook_session_cli.py tests/test_docs.py -q
pytest -q
```

期望：团队相关测试和全量测试均通过；没有旧工具名注册或引用导致的失败。

## 执行顺序

```text
T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8 → T9 → T10 → T11 → T12 → T13
```

每个任务完成后先运行该任务的验证命令，再进入下一个任务；验证失败必须在当前任务内修复并重新运行。实现阶段每个任务单独提交，提交信息使用 `feat(team): ...`、`refactor(team): ...` 或 `test(team): ...` 的约定格式。
