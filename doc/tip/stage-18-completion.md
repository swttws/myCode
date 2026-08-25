# Stage-18 Completion Fixes

## 2026-08-20

- 修复事件存储接受未注册 recipient 的问题。事件写入现在要求目标角色已注册，避免产生不可消费事件。
- 修复越序确认。角色只能确认当前最小未确认事件，失败终态事件可以被跳过但不能被越序确认。
- 扩展终态失败记录，保存团队、消息、协议、任务/批次、reason code 和最终事件状态。
- 移除 consumer 的 100ms timeout polling，正常消费只由角色 notifier 唤醒；终态失败回调异常不会杀死 consumer。
- 修复 member runtime 工具硬编码投递 Lead 的问题，定向消息和广播消息按目标角色写入并通知。
- service 发送前校验 sender，拒绝未知角色和不支持事件消费的 sender。
- attach 时恢复已有 in-process member，避免未确认事件必须等待下一次新消息才能消费。
- 增加旧 mailbox 忽略、不创建 mailbox、sender 校验和恢复行为测试，并修正 stage-18 checklist 中不存在的测试引用。
- 更新旧的 clear-session 回归断言：attach 自动恢复的 in-process member 属于当前 session，应被正常停止；外部 TMUX member 仍不应被停止。

## 验证

- 新增 `tests/test_stage18_completion.py` 的事件存储、consumer、runtime 路由、sender 和恢复测试通过。
- 既有 event、consumer、runtime、service、wake-chain 和 event-driven E2E 选测通过。
- `tests/test_team_static.py` 仍通过；使用仓库内 `--basetemp` 可绕过 Windows pytest 临时目录 ACL 限制。
- 最终使用仓库内 `--basetemp` 完成验证：team 相关 227 项通过；全量 1453 项通过、12 项跳过、3 项失败。3 项失败均来自工作区已有的 OpenAI 示例配置硬编码 API key，与 stage-18 事件链路无关。
- 修复 IntelliJ 多处 `Unresolved reference`：IDE 使用的 `Python 3.11 (codeAgent) (2)` 环境未安装本项目，导致 `mycode.team.*` 无法解析；已在该解释器中执行项目 editable 安装，并验证 consumer/events/models/notifier 符号可导入。
- 进一步修复 IDE 索引错源：`.idea/codeAgent.iml` 和 `pySourceRootDetection.xml` 曾把多个旧 worktree 的 `src` 注册为 source root；这些副本没有 `TeamEvent`，会遮蔽当前源码。现已只保留项目根 `src`。
- 安全清理 Agent Team 失效代码：移除 `worker.py` 未使用的 `AgentMode` 导入，并将事件锁 `__exit__` 的未使用参数标记为私有占位名；保留所有仍有 CLI、权限、持久化或兼容调用方的代码。
