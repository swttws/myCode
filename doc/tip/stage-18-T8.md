# T8 修复笔记 — 改造 Lead Supervisor

## 修复内容

### supervisor.py
1. **移除 `hasattr` 动态调用** — `_update_wait_state` 中 `hasattr(self._service, "list_tasks")` 改为直接调用 `self._service.list_tasks()`
2. **修复 `await status()`** — `_update_wait_state` 中 `self._service.status()` 加 `await`（`TeamService.status()` 是 async 方法）
3. **修复 `stop()` 消费者停止** — `stop()` 中新增 `await self._event_consumer.stop()`，否则消费者永不停止，`stop()` 会永久挂起

### test_team_supervisor.py
1. 新增 `FakeEventStore` 和 `FakeEventNotifier` 类，供 `RoleEventConsumer` 使用
2. `FakeSupervisorService` 新增 `event_store` 和 `event_notifier` 属性
3. `FakeSupervisorService` 的 `list_requests`、`status`、`list_tasks` 方法与真实 `TeamService` 签名对齐（`list_requests`/`list_tasks` 同步，`status` 异步）
4. 移除 `LeadSupervisor()` 调用中的 `poll_interval` 参数
5. 移除 `lead_unread()` 和 `acknowledge_lead()` 方法（不再被 supervisor 使用）
6. 测试 1 改为使用 `submit_member_event` 替代直接操作 `service.messages`
7. 新增 `test_supervisor_user_decision_resumes_lead` 和 `test_supervisor_stop_gracefully`
8. 修复测试 2 的断言位置（在 `stop()` 之前检查状态）

## 关键发现
- `TeamService.status()` 是 async，`list_requests()` 和 `list_tasks()` 是 sync
- `RoleEventConsumer.stop()` 必须被调用，否则 consumer 永远不退出