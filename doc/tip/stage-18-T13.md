# Stage-18 T13 修复记录

## 问题
旧 `MailboxStore` 主消费链路（send/receive/unread/acknowledge/watch）已被事件驱动模型替代，但源码和测试文件仍保留。

## 修复内容

### 1. 删除 `src/mycode/team/mailbox.py`（513 行）
- `MailboxStore` 已无任何源文件导入（零引用）
- 消费链路已由 `TeamEventStore` + `RoleEventConsumer` + `TeamEventNotifier` 替代
- `__init__.py` 未导出，无外部依赖

### 2. 删除 `tests/test_team_mailbox.py`（281 行）
- 7 个测试全部针对 `MailboxStore` 的 send/receive/acknowledge/lead_unread/archive 行为
- 已有对应事件存储测试覆盖（`test_team_events.py` 4 个、"test_team_consumer.py` 2 个）

### 3. 搜索确认
- `rg "MailboxStore|lead_unread|acknowledge_lead|\.unread\(|watch_mailbox" src tests` → **零匹配**
- 主链路中不再有旧 mailbox 消费 API 调用

### 4. 保留项
- `models.MemberRecord.mailbox_path` 字段 — 数据字段，非消费 API
- `storage.TeamStore.mailbox_path()` 方法 — 路径构造，仍被 `MemberRecord` 使用
- `models.MemberLaunchSpec.mailbox_path` 字段 — 传递路径信息

## 测试结果
- 全量 team: 112/112 pass