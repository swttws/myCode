# Stage-18 T20 修复记录

## 问题
全量测试 10 个失败，其中 6 个 P0 回归（e2e_chat），1 个 P1（enum 文档），3 个 P2（docs 配置无关）。

### P0：6 个 e2e_chat 测试崩溃
**根因：** `supervisor.py:start()` 无条件调用 `self._service.event_store`，但 `event_store` 只在 team 激活后才可用。e2e 测试中 session 启动时 team 未激活，导致 `TeamError("team is not active")` 崩溃。

**调用链：** `session.start()` → `supervisor.start()` → `service.event_store` → `_events_or_error()` → `TeamError`

**受影响测试：**
- test_e2e_cli_tui_session_memory_streams_and_sends_previous_context
- test_e2e_clear_removes_previous_context_before_next_request
- test_e2e_tool_call_result_is_stored_for_next_request
- test_e2e_failed_edit_tool_call_returns_structured_error_and_continues
- test_e2e_next_turn_sends_previous_tool_history_to_llm
- test_e2e_clear_removes_tool_history_before_next_request

## 修复内容

### supervisor.py: start() 优雅降级
用 `try-except TeamError` 包裹 `event_store` 访问，team 未激活时只启动队列 worker，跳过事件消费者设置。

```python
try:
    event_store = self._service.event_store
except TeamError:
    return  # team 未激活，跳过事件消费者
```

同时导入 `TeamError`：
```python
from mycode.team.models import BatchState, TeamError, TeamMessage, TeamTaskState
```

### supervisor.py: enum 注释补全
给 `TeamEventKind` 的 4 个值补上行内注释：
```python
USER_GOAL = "user_goal"          # 用户提交的目标文本
MEMBER_MESSAGE = "member_message"  # member 发来的消息事件
USER_DECISION = "user_decision"    # 用户对审批请求的决策
STOP = "stop"                      # 停止 supervisor 信号
```

## 测试结果
- e2e_chat: 6/6 pass ✅
- enum 文档: 1/1 pass ✅
- 全量 team: 205/205 pass ✅
- 全量测试: 1440 passed, 3 failed (P2 docs 配置，无关), 12 skipped ✅
- 静态扫描: 11/11 pass ✅
- 回归测试: 12/12 pass ✅
- 旧 mailbox 扫描: 源码零匹配 ✅
- getattr/hasattr 扫描: 16 处全在白名单 ✅