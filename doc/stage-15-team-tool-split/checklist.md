# Stage 15 Team 工具拆分 Checklist

> 每一项都通过运行代码、读取实际工具定义或观察运行结果验证。执行时记录命令输出或测试结果，再勾选条目。

## 实现完整性

- [ ] **工具目录完整**（验证：启动注册路径并打印 `ToolRegistry.definitions()` 的名称集合；期望包含 21 个新工具，且不包含 `team`、`team_lead`、`team_member`。）
- [ ] **工具名称符合规范**（验证：对名称集合运行正则 `^team_[a-z0-9]+_[a-z0-9_]+$`；期望所有新工具通过，工具类使用 `PascalCase`。）
- [ ] **Schema 已原子化**（验证：遍历 21 个 `ToolDefinition.parameters`；期望顶层为 object、无 `action`/`operation`、无跨动作大型 `oneOf`、`additionalProperties` 为 `False`。）
- [ ] **未知字段被拒绝**（验证：向每个工具追加一个未声明字段执行；期望返回失败结果且服务调用计数为零。）
- [ ] **生命周期工具可用**（验证：执行创建、接管、状态、归档成功和失败路径；期望创建/接管语义分离，归档仍遵守安静团队约束。）
- [ ] **批次和成员工具可用**（验证：启动批次、启动成员、终止成员、整合批次；期望返回批次/成员状态、整合提交或冲突任务。）
- [ ] **任务工具可用**（验证：创建、列表、读取、更新、删除、领取、状态转换；期望 revision、所有者、审批状态和结果字段符合现有 TaskBoard 行为。）
- [ ] **协议工具可用**（验证：计划提交/决策、定向/广播消息、状态更新、关停请求/响应；期望协议枚举、目标、发送者和任务/批次元数据正确。）
- [ ] **工具文案为中文**（验证：检查每个工具的 `description`、参数 `description`、权限拒绝消息和工具失败消息；期望用户/模型可见文本为中文，稳定 `reason_code` 和枚举值除外。）
- [ ] **读写分类正确**（验证：读取 `ToolDefinition.kind`；期望 `team_status`、`team_task_list`、`team_task_get` 为 `READ`，其余 team 工具为 `WRITE`。）

## 集成与安全

- [ ] **主会话角色可见性正确**（验证：未激活服务调用 `visible_team_tools`；期望只返回 `team_create`、`team_attach`、`team_status`。）
- [ ] **Lead 角色可见性正确**（验证：激活团队后读取 Lead 工具集合；期望包含批次、成员、任务、计划决策、消息、关停请求、状态和归档工具，不包含 Member 专属工具。）
- [ ] **Member 角色可见性正确**（验证：创建 Member Worker 工具列表；期望只包含任务、计划提交、消息、状态更新和关停响应工具。）
- [ ] **隐藏工具无法绕过**（验证：手工构造隐藏工具的 `ToolCall`；期望 `TeamToolPolicy` 返回拒绝和稳定 `reason_code`，真实服务方法不被调用。）
- [ ] **成员身份不可伪造**（验证：绑定 `member_name="dev"` 的工具传入 `member_name="ops"` 或 `sender="ops"`；期望失败，TaskBoard/Mailbox 无调用。）
- [ ] **并发版本保护保持**（验证：使用过期 `expected_revision` 更新、删除、领取和状态转换；期望失败且任务 revision 和持久化文件不变。）
- [ ] **权限审批未降级**（验证：默认、严格、`/plan`、Member 审批和协调器模式下执行代表性读写调用；期望审批、拒绝、Git 命令白名单和协调器限制与 Stage 14 一致。）
- [ ] **CLI 和 Worker 注册链路正确**（验证：运行 CLI 初始化测试和 Member Runtime 测试；期望不再导入 `TeamTool`，注册结果只出现新工具名，AgentLoop 仍收到可见工具 provider。）
- [ ] **配置和扩展迁移正确**（验证：在 Sub-agent、Hook、Permission、Skill 配置中分别写入旧工具名；期望加载阶段失败并给出中文位置提示；写入新工具名时加载成功。）
- [ ] **事件和结果使用新名称**（验证：执行一个成功和一个失败工具调用并检查 Agent 事件、Hook 上下文、日志和 `ToolResult.tool_name`；期望全部使用新完整工具名。）
- [ ] **持久化和协议未变化**（验证：执行创建团队、发送消息、Member checkpoint、任务整合后读取原有 `team.json`、JSONL 和上下文文件；期望字段结构与 Stage 14 兼容。）

## 编译、测试与文档

- [ ] **Python 编译通过**（验证：运行 `python -m compileall -q src`；期望退出码为 0。）
- [ ] **新工具单元测试通过**（验证：运行 `pytest tests/test_team_tools.py -q`；期望所有新工具 schema、执行、错误和身份测试通过。）
- [ ] **团队服务/策略/运行时测试通过**（验证：运行 `pytest tests/test_team_service.py tests/test_team_runtime.py tests/test_team_integration.py -q`；期望全部通过。）
- [ ] **CLI、Hook、Sub-agent 和文档测试通过**（验证：运行 `pytest tests/test_hook_session_cli.py tests/test_docs.py tests/test_subagent_config.py tests/test_subagent_loader.py tests/test_subagent_catalog.py tests/test_subagent_runtime.py tests/test_subagent_tooling.py tests/test_subagent_docs.py -q`；期望全部通过。）
- [ ] **全量回归通过**（验证：运行 `pytest -q`；期望没有旧名称、schema、权限或 Agent Loop 回归失败。）
- [ ] **旧实现引用清理**（验证：运行 `rg -n "TeamTool|from mycode\\.team\\.tool" src tests examples README.md`；期望无旧实现、旧导入或旧注册引用；再人工检查旧名称只出现在集中迁移诊断和对应失败测试中。）
- [ ] **文档目录完整**（验证：确认 `spec.md`、`plan.md`、`task.md`、`checklist.md` 均存在且 README 列出 21 个新工具和角色范围。）

## 端到端场景

- [ ] **Lead 编排并整合 Member**（验证：运行 `pytest tests/test_team_e2e.py -q`；用户请求触发 `team_create` → `team_batch_start` → `team_task_create` → `team_member_spawn`，Member 使用 `team_task_claim`、`team_plan_submit`、`team_task_transition` 和 `team_status_update`，Lead 最后调用 `team_batch_integrate`；期望任务完成、Member 状态回报、整合提交或冲突报告可观察。）
- [ ] **Member 审批和身份边界**（验证：运行 `pytest tests/test_team_tools.py -k "member and (spoof or approval or blocked)" -q`；期望伪造身份、未审批写入和 blocked 恢复均被拒绝，并且不产生越权写入。）
- [ ] **旧配置迁移失败**（验证：使用临时配置分别引用 `team`、`team_lead`、`team_member` 并启动加载；期望每次都在加载阶段给出中文迁移错误，不注册旧工具。）
