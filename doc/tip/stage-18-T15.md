# Stage-18 T15 修复记录

## 问题
需要补齐端到端事件驱动链路测试，覆盖 5 个核心场景。

## 修复内容

### 新建 6 个事件驱动 e2e 测试（追加到 `tests/test_team_e2e.py`）

| 测试 | 场景 | 验证点 |
|------|------|--------|
| `test_event_driven_lead_to_member_message_is_consumed_and_acked` | Lead → member | 事件写入、通知器通知、消费、确认 |
| `test_event_driven_member_to_lead_message_is_consumed_and_acked` | Member → Lead | 事件写入、Lead 通知、消费、确认 |
| `test_event_driven_member_to_member_message_is_consumed_in_order` | Member → member | 按 sequence 顺序消费、独立确认 |
| `test_event_driven_team_round_trip_uses_shared_consumer` | Lead → alpha → beta → Lead | 完整链路，所有事件走同一机制 |
| `test_event_driven_member_failure_after_three_attempts_is_recorded` | 失败 3 次 | 前 2 次 PENDING，第 3 次 FAILED |
| `test_event_driven_broadcast_expands_recipients_at_append_time` | 广播 | 发送时固定接收者，Lead 不收到自己广播 |

### 修复的兼容性问题

1. **`test_team_worker_default_runtime_builds_real_agent_loop`**: 工具名 `team_member` → `team_clarification_request`（工具拆分后改名）
2. **TASK_ASSIGNMENT 事件**: `spawn_member` 自动生成任务分配事件，测试需过滤 `message_id` 而非精确计数
3. **`fail_event` API**: 参数名 `error` → `reason`，返回值 `EventFailure | None`（非 `TeamEvent`）

### 辅助函数
- `_make_e2e_service`: 创建带 FakeBackend 的 TeamService
- `_activate_and_spawn`: 激活 team + 创建 batch + spawn member
- `_make_e2e_message`: 构造测试消息

## 测试结果
- e2e: 9/9 pass（3 原有 + 6 新增）
- 全量 team: 173/173 pass