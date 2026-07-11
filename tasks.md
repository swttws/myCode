# myCode 纯对话 TUI 任务拆分

## 1. 初始化 Python 包结构

- 影响文件：`pyproject.toml`、`src/mycode/__init__.py`、`src/mycode/__main__.py`
- 依赖任务：无
- 工作内容：创建可安装的 Python 包结构，提供命令行入口，并声明运行和测试所需依赖。
- 参考位置：Python packaging 用户指南、项目布局约定。

## 2. 添加 CLI 入口和启动参数解析

- 影响文件：`src/mycode/cli.py`、`src/mycode/__main__.py`、`tests/test_cli.py`
- 依赖任务：任务 1
- 工作内容：支持启动聊天应用，并允许用户传入可选配置文件路径。
- 参考位置：`spec.md` 的“目标”和“设计骨架”。

## 3. 实现 YAML 配置加载

- 影响文件：`src/mycode/config.py`、`tests/test_config.py`
- 依赖任务：任务 1
- 工作内容：按显式路径、当前工作目录、用户级目录的顺序加载 YAML，并校验 Provider 所需核心配置。
- 参考位置：`checklist.md` 的配置查找和必填字段验收项。

## 4. 实现环境变量认证解析

- 影响文件：`src/mycode/config.py`、`tests/test_config.py`
- 依赖任务：任务 3
- 工作内容：允许 YAML 中的认证字段引用环境变量，同时保留直接写入字面值的能力。
- 参考位置：`checklist.md` 的认证相关验收项。

## 5. 定义统一 Provider 事件模型和 Provider 工厂

- 影响文件：`src/mycode/providers/base.py`、`src/mycode/providers/__init__.py`、`tests/test_provider_factory.py`
- 依赖任务：任务 3
- 工作内容：定义 assistant 文本、thinking 文本、完成和错误等流式事件，并根据协议选择 Provider。
- 参考位置：`spec.md` 的“能力清单”和“设计骨架”。

## 6. 实现 SSE 解析辅助模块

- 影响文件：`src/mycode/sse.py`、`tests/test_sse.py`
- 依赖任务：任务 1
- 工作内容：从 HTTP 响应行中增量解析 server-sent events，并产出 Provider 可复用的中间事件记录。
- 参考位置：Anthropic streaming 文档 `https://platform.claude.com/docs/en/build-with-claude/streaming`；OpenAI streaming 文档 `https://developers.openai.com/api/docs/guides/streaming-responses`。

## 7. 实现 Anthropic Provider

- 影响文件：`src/mycode/providers/anthropic.py`、`tests/test_anthropic_provider.py`
- 依赖任务：任务 4、任务 5、任务 6
- 工作内容：向 Anthropic Messages API 发送对话历史，消费 SSE，并把正文增量和 extended thinking 增量映射为统一事件。
- 参考位置：Anthropic Messages streaming 和 extended thinking 文档。

## 8. 实现 OpenAI Responses Provider

- 影响文件：`src/mycode/providers/openai_responses.py`、`tests/test_openai_responses_provider.py`
- 依赖任务：任务 4、任务 5、任务 6
- 工作内容：以流式模式调用 OpenAI Responses API，并把输出文本增量映射为统一事件。
- 参考位置：OpenAI Responses API streaming 文档。

## 9. 实现 OpenAI Chat Completions Provider

- 影响文件：`src/mycode/providers/openai_chat.py`、`tests/test_openai_chat_provider.py`
- 依赖任务：任务 4、任务 5、任务 6
- 工作内容：以流式模式调用 OpenAI Chat Completions API，并把 chat delta 映射为统一事件。
- 参考位置：OpenAI Chat Completions API reference 和 OpenAI-compatible 网关行为。

## 10. 构建 TUI 聊天循环

- 影响文件：`src/mycode/tui.py`、`tests/test_tui.py`
- 依赖任务：任务 2、任务 5
- 工作内容：提供 prompt 输入、rich 终端流式输出、当前进程内消息历史和基础会话命令。
- 参考位置：`spec.md` 的“目标”、“能力清单”和“不做范围”。

## 11. 连接流式输出和对话记忆

- 影响文件：`src/mycode/tui.py`、`tests/test_tui.py`
- 依赖任务：任务 7、任务 8、任务 9、任务 10
- 工作内容：收集流式 assistant 文本，形成完整 assistant 消息，并在每轮成功回复后追加到内存历史。
- 参考位置：`checklist.md` 的多轮对话和流式输出验收项。

## 12. 添加面向用户的示例和最小文档

- 影响文件：`README.md`、`examples/mycode.anthropic.yaml`、`examples/mycode.openai-responses.yaml`、`examples/mycode.openai-chat.yaml`
- 依赖任务：任务 3、任务 7、任务 8、任务 9、任务 10
- 工作内容：说明受支持协议、配置示例、环境变量认证和首次运行命令。
- 参考位置：`checklist.md` 的示例配置验收项。

## 13. 接入主流程

- 影响文件：`src/mycode/cli.py`、`src/mycode/tui.py`、`src/mycode/providers/__init__.py`、`tests/test_cli.py`
- 依赖任务：任务 2 到任务 12
- 工作内容：把配置加载、Provider 创建、TUI 启动和优雅退出连接到用户实际运行的命令。
- 参考位置：`spec.md` 的“完成定义”。

## 14. 端到端验证

- 影响文件：`tests/test_e2e_chat.py`、`README.md`
- 依赖任务：任务 13
- 工作内容：通过公开命令路径运行 mocked 流式对话，验证流式输出、历史记忆、清空历史和退出行为。
- 参考位置：`checklist.md` 的端到端验收项。
