---
name: commit
description: 检查变更并形成清晰提交
allowed_tools:
  - read_file
  - search_code
  - run_command
mode: shared
---

请按以下步骤协助完成提交准备：
1. 查看当前变更范围，只关注用户要求提交的文件。
2. 运行必要的验证命令，失败时先总结失败原因。
3. 给出简洁提交信息和需要暂存的文件清单。

用户补充参数：{{arguments}}
