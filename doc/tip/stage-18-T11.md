# Stage-18 T11 修复记录

## 问题
`worker.py` 的 `_run_runtime` 已改为调用 `runtime.run_event_consumer()`，但 e2e 测试中的 mock 仍使用旧方法名 `run_until_idle()`。

## 修复内容

### 1. `src/mycode/team/worker.py`
**无需修改** — worker 代码已经完成事件驱动改造：
- 不引用 `MailboxStore`（第 1-24 行无 mailbox 导入）
- 已注入 `event_store` 和 `notifier`（第 125-126 行）
- 已使用 `resume_from_checkpoint()` + `run_event_consumer()`（第 155-156 行）
- `_MailboxOnlyAgent` 不存在于代码库中，无需清理

### 2. `tests/test_team_e2e.py`
- 第 119 行：mock Runtime 的 `run_until_idle` → `run_event_consumer`
- 该测试验证 worker 的 `_run_runtime` 正确调用 `resume_from_checkpoint()` + `run_event_consumer()`

## 测试结果
- e2e worker: 4/4 pass
- e2e 全量: 5/5 pass
- 全量 team: 74/74 pass