# 静态方法分发复盘

## 问题

后端、任务服务、工具执行器和界面层使用 `getattr(..., "method", None)` 取得方法后再调用。这样隐藏了接口契约，IDE 无法直接跳转到方法实现，也会让缺失方法延迟到运行时才暴露。

## 修复

- TeamService、成员/Lead 工具、TeamTool 直接调用任务、后端和工作区服务方法。
- In-process、进程后端和 worker 直接调用运行时及进程生命周期方法。
- ToolExecutor 和 ToolRegistry 使用显式运行时协议判断，再调用 `execute_async` 或 `should_defer`。
- MCP 传输能力通过明确的传输类型分支调用；MCP 搜索保留测试替身的工具列表回退。
- TUI 优先使用显式 `render` 协议，旧 Session 通过显式 `send` 分支兼容。
- 删除 `_service_method` 和 `_call_task_method` 动态分发辅助函数。

## 验证

- `python -m compileall -q src tests`
- Team、MCP、Tool、TUI 定向测试：`93 passed`
- 完整测试套件：`1416 passed, 12 skipped`
