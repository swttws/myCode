---
name: review
description: 审查当前变更中的缺陷和风险
allowed_tools:
  - read_file
  - find_files
  - search_code
  - run_command
mode: shared
---

请审查当前 Git 工作区的所有未提交改动，包括已暂存、未暂存和未跟踪文件，并忽略 Git 已忽略文件。
优先查找会导致错误行为的缺陷、行为回归、安全风险和缺失测试。
先按严重程度列出发现，并给出对应文件与位置；如果没有发现，明确说明，并指出剩余测试风险。

用户补充参数：{{arguments}}
