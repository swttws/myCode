# T9 修复笔记 — 改造 Member Runtime

## 修复内容

### runtime.py
**无需修改** — 代码已经是事件驱动模型：
- `run_event_consumer()` 使用 `RoleEventConsumer` 消费事件
- `_handle_event()` 处理 shutdown/clarification/approval/普通任务消息
- `_send_event_message()` 走 `_event_store.append_message()` 发送消息
- 无 `getattr`/`hasattr`/`mailbox` 依赖

### test_team_runtime.py
**1 处重命名：**
- `test_member_runtime_team_member_tool_can_send_status_to_lead_mailbox` → `test_member_runtime_team_member_tool_can_send_status_to_lead`（测试实际使用事件存储，非 mailbox）

**保留的 mailbox 引用（合理）：**
- `make_member()` 中 `mailbox_path=store.mailbox_path(...)` — MemberRecord 仍有此字段，属数据模型层
- `getattr(record, "message_id", None)` — 日志记录属性访问，Python 标准模式

## T9 步骤对照

| 步骤 | 状态 |
|------|------|
| 1. 删除 mailbox unread 扫描式消费主循环 | 已实现 |
| 2. 接入 member 角色队列和统一消费者 | 已实现（`run_event_consumer`） |
| 3. 实现 member 事件处理器 | 已实现（`_handle_event`） |
| 4. shutdown/clarification/approval 迁移到事件入口 | 已实现 |
| 5. 成功处理后由消费者确认 | 已实现 |
| 6. member 发送状态/失败/shutdown 走事件发送 | 已实现（`_send_event_message`） |
| 7. 清理 getattr 和旧 mailbox 依赖 | 已实现（无残留） |
| 8. 更新测试 | 18/18 通过，仅重命名 1 处 |