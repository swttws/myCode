# Stage-18 T10 修复记录

## 问题
`in_process` backend 原来使用 `run_until_idle()` + `graceful_stop()` 的轮询模式，需要改造为事件驱动消费者模式 `run_event_consumer()` + `stop_consumer()`。`tmux`/`terminal` backend 返回 `event_driven_unsupported` 错误。

## 修复内容

### 1. `src/mycode/team/backends.py`
- `InProcessBackend.start()`: 移除 `asyncio.iscoroutine()` 动态检查，直接调用 `runtime.run_event_consumer()` 创建 `asyncio.create_task()`
- `InProcessBackend.wake()`: 改为 `pass`（事件驱动下消费者持续运行，不需要手动唤醒）
- `InProcessBackend.stop()`: 移除 `getattr(hash, ...)` 动态调用，改为直接调用 `runtime.stop_consumer()`
- `TmuxBackend.start()` / `WindowsTerminalBackend.start()`: 返回 `TeamError(code="event_driven_unsupported")`
- `BackendRouter`: 移除 `iscoroutine()` 动态检查

### 2. `tests/test_team_backends.py`
- `FakeRuntime` / `BlockingRuntime`: 方法名从 `run_until_idle()` / `graceful_stop()` 改为 `run_event_consumer()` / `stop_consumer()`
- `test_in_process_backend_runs_member_runtime_and_wakes_existing_handle`: `run_count` 从 2 → 1（消费者只启动一次）
- `test_in_process_backend_does_not_drop_wake_during_runtime`: `run_count` 从 2 → 1
- `test_in_process_backend_coalesces_wakes_without_concurrent_runtime`: `run_count` 从 2 → 1
- 旧 tmux/terminal 测试（进程启动、argv 构建）替换为 `test_tmux_backend_reports_event_driven_unsupported` 和 `test_terminal_backend_reports_event_driven_unsupported`

## 测试结果
- backend: 12/12 pass
- 全量: 69/69 pass