# Stage-18 T12 修复记录

## 问题
工具层（lead_tools, member_tools, tool.py, tool_helpers.py）存在多处 `getattr` 动态属性访问，需要改为直接字段访问。

## 修复内容

### 1. `src/mycode/team/lead_tools.py`
- 第 244、270 行：`getattr(member.state, "value", member.state)` → `member.state.value`
  - `MemberRecord.state` 是 `MemberState` 枚举，`.value` 始终可用

### 2. `src/mycode/team/member_tools.py`
- 第 125 行：`getattr(task, "owner", None)` → `task.owner`
- 第 585 行：`getattr(current, "owner", None)` → `current.owner`
- 第 697 行：`getattr(current, "owner", None)` → `current.owner`
  - `TeamTask.owner` 是 `str | None = None`，始终存在

### 3. `src/mycode/team/tool.py`
- 第 124、137 行：`getattr(member.state, "value", member.state)` → `member.state.value`
- 第 278 行：`getattr(exc, "code", "team_action_failed")` → `exc.code if isinstance(exc, TeamError) else "team_action_failed"`
- 第 338 行：`getattr(task, "owner", None)` → `task.owner`

### 4. `src/mycode/team/tool_helpers.py`
- 新增 `from mycode.team.models import TeamError` 导入
- 第 75 行：`getattr(task, "result", None)` → `task.result`
  - `TeamTask.result` 是 `TaskResult | None = None`
- 第 90 行：`getattr(batch, "revision", None)` → `batch.revision`
  - `BatchRecord.revision` 是 `int = 0`
- 第 99 行：`getattr(exc, "code", None)` → `exc.code if isinstance(exc, TeamError) else ...`

### 5. 测试更新
- `tests/test_team_tool.py`: FakeService 中 `spawn_member` 和 `terminate_member` 返回的 `state` 从字符串改为 `MemberState.RUNNING`/`MemberState.STOPPED`
- `tests/test_team_lead_tools.py`: 同上，`spawn_member` 返回 `MemberState.RUNNING`

## 测试结果
- 工具测试: 38/38 pass
- 全量 team: 112/112 pass