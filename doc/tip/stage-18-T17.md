# Stage-18 T17 修复记录

## 问题
全量 team 测试 `test_team_enum_docs.py::test_every_team_enum_has_class_and_value_documentation` 失败：
`supervisor.py:22-25` 的 4 个 `TeamEventKind` enum 值缺少行内注释。

## 修复内容
给 `supervisor.py` 中 `TeamEventKind` 枚举的 4 个值补上行内注释：

```python
USER_GOAL = "user_goal"          # 用户提交的目标文本
MEMBER_MESSAGE = "member_message"  # member 发来的消息事件
USER_DECISION = "user_decision"    # 用户对审批请求的决策
STOP = "stop"                      # 停止 supervisor 信号
```

## 测试结果
- enum 文档测试: 1/1 pass
- 全量 team 测试: 205/205 pass