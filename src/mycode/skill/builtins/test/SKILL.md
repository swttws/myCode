---
name: test
description: 选择并运行相关测试
allowed_tools:
  - read_file
  - find_files
  - search_code
  - run_command
mode: isolated
context:
  strategy: recent
  turns: 3
---

请根据目标和最近上下文选择最小但有代表性的测试范围。
先说明选择依据，再运行测试；如果失败，定位首个关键失败并给出后续修复建议。
不要访问网络，不要使用真实 API key。

用户补充参数：{{arguments}}
