# Stage 18 Development Notes

## T1 - Event model foundation

- Added explicit persistent-event, role-cursor, and failure-record models for the
  new Agent Team communication path.
- Replaced generic dynamic attribute access in model validation helpers with
  explicit values supplied by each model.

## T2 - Event storage paths

- Added stable, root-bounded paths for the event log, role cursors, and event
  failure records.
- Stopped member persistence from creating the deprecated mailbox file; the
  upcoming event store owns creation of event files when it writes events.

## T3 - Persistent event store

- Added a team-scoped event store with a monotonic sequence under a file lease.
- Implemented per-role acknowledgement cursors, replay of unacknowledged work,
  retry accounting, and structured failure records after the third failure.
- Added focused regression coverage for direct delivery, independent cursors,
  terminal failures, and recovery after reload.

## T4 - In-process event notifier

- Added per-role bounded wakeup queues with registration and cleanup.
- Duplicate notifications collapse into one signal; payloads remain in the event store.

## T5-T9 - Consumer and runtime integration

- Added the shared role consumer and terminal failure callback behavior.
- Connected service activation/send paths to event registration, persistence, and
  role notification.
- Added event-consumer entry points for member runtime, worker, backend runtime
  execution, and Lead supervisor, while retaining compatibility fallbacks for
  callers that do not provide event dependencies.

## T6 — 已重新实现 ✓

- T6 revert 已过时。当前代码中 T6 全部 5 步已在位：
  1. 激活时创建事件存储和通知器 ✓
  2. 激活时注册 Lead 订阅 ✓
  3. attach 时恢复订阅 ✓
  4. spawn 后注册 member 订阅 ✓
  5. 删除 _mailbox_or_error ✓

## T17 — enum 文档修复 ✓

- supervisor.py TeamEventKind 4 个值补了行内注释，205/205 pass

## T20 — P0 回归修复 ✓

- supervisor.start() 在 team 未激活时优雅降级，不再崩溃
- 全量 1440 passed, 3 failed (P2 docs 配置无关), 12 skipped
