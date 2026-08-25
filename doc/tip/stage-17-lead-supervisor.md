# Stage 17 修复记录

## 目标

让 Lead 在后台自主完成 Team dispatch、member 通知、request/response 和 batch 收尾；只有业务判断不确定时才等待用户。

## 变更

- 为 Team 枚举补充类级和值级注释，增加 `AWAITING_INPUT` 和 Supervisor 生命周期状态。
- 增加持久化 `TeamRequest`，支持澄清、工具审批、计划审核和用户决策请求。
- member 澄清请求会原子地写入 request、任务等待态、成员等待态和 Lead mailbox；checkpoint 后确认当前消息。
- Lead 可列出 request、直接响应 member、处理工具审批或创建用户 pending request。
- member 响应恢复任务；不可恢复异常标记 `FAILED` 并发送结构化 Lead 事件。
- 增加 `LeadSupervisor` 单队列、Lead 执行锁和 mailbox watcher，并接入 session 生命周期。

## 验证

- Team/Lead/Member/Supervisor/session 定向测试通过。
- 全量 `python -m pytest -q`：1416 passed，12 skipped。
