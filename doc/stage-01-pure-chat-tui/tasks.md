# myCode 阶段 01：纯对话 TUI 任务拆分

## 阶段标识

- 阶段编号：Stage 01
- 阶段名称：纯对话 TUI
- 阶段目标：按可测试的小步任务完成可流式多轮对话的命令行 AI 助手。

## 1. 初始化 Python 包与依赖

- 影响文件：`pyproject.toml`、`src/mycode/__init__.py`、`src/mycode/__main__.py`
- 依赖任务：无
- 工作内容：建立 src-layout Python 包结构，声明运行依赖和测试依赖，提供可执行入口。
- 参考资料定位：`doc/stage-01-pure-chat-tui/spec.md` 的“架构骨架”和“完成定义”。

## 2. 建立 CLI 入口

- 影响文件：`src/mycode/cli.py`、`src/mycode/__main__.py`、`tests/test_cli.py`
- 依赖任务：任务 1
- 工作内容：实现公开命令入口，支持传入可选配置文件路径，并把启动流程连接到配置加载和 TUI 启动点。
- 参考资料定位：`doc/stage-01-pure-chat-tui/spec.md` 的“能力清单”中命令行启动相关条目。

## 3. 实现 YAML 配置加载与校验

- 影响文件：`src/mycode/config.py`、`tests/test_config.py`
- 依赖任务：任务 1
- 工作内容：加载 YAML 配置，校验 `protocol`、`model`、`base_url`、`api_key` 四个核心字段，支持默认配置查找顺序。
- 参考资料定位：`doc/stage-01-pure-chat-tui/spec.md` 的“配置设计”和 `doc/stage-01-pure-chat-tui/checklist.md` 的“配置”。

## 4. 实现环境变量认证解析

- 影响文件：`src/mycode/config.py`、`tests/test_config.py`
- 依赖任务：任务 3
- 工作内容：支持 `api_key` 直接使用字面值或 `${ENV_NAME}` 形式引用环境变量；缺失环境变量时在启动前失败。
- 参考资料定位：`doc/stage-01-pure-chat-tui/checklist.md` 的“配置”。

## 5. 定义统一 LLM 抽象层

- 影响文件：`src/mycode/llm/__init__.py`、`src/mycode/llm/base.py`、`tests/test_llm_base.py`
- 依赖任务：任务 1
- 工作内容：定义 LLM 抽象基类、聊天消息结构、异步流式事件结构和错误类型，要求具体 LLM 客户端统一继承该抽象。
- 参考资料定位：`doc/stage-01-pure-chat-tui/spec.md` 的“架构骨架”和“协议边界”。

## 6. 建立 memory 包和进程内记忆

- 影响文件：`src/mycode/memory/__init__.py`、`src/mycode/memory/base.py`、`src/mycode/memory/in_memory.py`、`tests/test_memory.py`
- 依赖任务：任务 5
- 工作内容：定义 `ConversationMemory` 抽象接口，实现当前进程内的消息追加、读取和清空能力。
- 参考资料定位：`doc/stage-01-pure-chat-tui/spec.md` 的“多轮对话与记忆边界”。

## 7. 实现协议工厂

- 影响文件：`src/mycode/protocols/__init__.py`、`src/mycode/protocols/factory.py`、`tests/test_protocol_factory.py`
- 依赖任务：任务 3、任务 5
- 工作内容：根据配置中的 `protocol` 创建对应 LLM 客户端，并确保工厂返回统一 LLM 抽象类型。
- 参考资料定位：`doc/stage-01-pure-chat-tui/spec.md` 的“协议边界”和 `doc/stage-01-pure-chat-tui/checklist.md` 的“协议选择”。

## 8. 实现共享 SSE 解析器

- 影响文件：`src/mycode/protocols/sse.py`、`tests/test_sse.py`
- 依赖任务：任务 1
- 工作内容：实现轻量 SSE parser，处理 `event:`、`data:`、空行分隔、多行 data 和结束标记，为协议实现提供可复用解析能力。
- 参考资料定位：`doc/stage-01-pure-chat-tui/spec.md` 的“协议边界”和 `doc/stage-01-pure-chat-tui/checklist.md` 的“流式输出”。

## 9. 实现 Anthropic 协议客户端

- 影响文件：`src/mycode/protocols/anthropic.py`、`tests/test_anthropic_protocol.py`
- 依赖任务：任务 5、任务 8
- 工作内容：实现继承统一 LLM 抽象的 Anthropic 异步客户端，构造 Messages API 请求，异步消费 SSE，并映射正文增量和 thinking 增量。
- 参考资料定位：`doc/stage-01-pure-chat-tui/spec.md` 的“Claude Extended Thinking”和 `doc/stage-01-pure-chat-tui/checklist.md` 的“Claude Extended Thinking”。

## 10. 实现 OpenAI Responses 协议客户端

- 影响文件：`src/mycode/protocols/openai_responses.py`、`tests/test_openai_responses_protocol.py`
- 依赖任务：任务 5、任务 8
- 工作内容：实现继承统一 LLM 抽象的 OpenAI Responses 异步客户端，构造流式请求，并把输出文本增量映射为统一事件。
- 参考资料定位：`doc/stage-01-pure-chat-tui/spec.md` 的“协议边界”和 `doc/stage-01-pure-chat-tui/checklist.md` 的“流式输出”。

## 11. 实现 OpenAI Chat Completions 协议客户端

- 影响文件：`src/mycode/protocols/openai_chat.py`、`tests/test_openai_chat_protocol.py`
- 依赖任务：任务 5、任务 8
- 工作内容：实现继承统一 LLM 抽象的 OpenAI Chat Completions 异步客户端，构造流式请求，并把 chat delta 映射为统一事件。
- 参考资料定位：`doc/stage-01-pure-chat-tui/spec.md` 的“协议边界”和 `doc/stage-01-pure-chat-tui/checklist.md` 的“流式输出”。

## 12. 构建会话协调层

- 影响文件：`src/mycode/session.py`、`tests/test_session.py`
- 依赖任务：任务 5、任务 6
- 工作内容：连接 TUI、memory 和 LLM；负责写入 user 消息、读取上下文、异步收集 assistant 正文、请求成功后写回 assistant 消息。
- 参考资料定位：`doc/stage-01-pure-chat-tui/spec.md` 的“数据流”和“多轮对话与记忆边界”。

## 13. 构建增强 TUI 聊天循环

- 影响文件：`src/mycode/tui.py`、`tests/test_tui.py`
- 依赖任务：任务 6、任务 12
- 工作内容：使用 `prompt_toolkit` 实现输入体验，使用 `rich` 渲染提示、正文流式输出、thinking 样式、错误提示和内置命令。
- 参考资料定位：`doc/stage-01-pure-chat-tui/spec.md` 的“能力清单”和“Claude Extended Thinking”。

## 14. 接入主流程

- 影响文件：`src/mycode/cli.py`、`src/mycode/config.py`、`src/mycode/protocols/factory.py`、`src/mycode/session.py`、`src/mycode/tui.py`、`tests/test_cli.py`
- 依赖任务：任务 2 到任务 13
- 工作内容：把配置加载、协议工厂、memory、session 和 TUI 启动串成用户实际运行路径。
- 参考资料定位：`doc/stage-01-pure-chat-tui/spec.md` 的“完成定义”。

## 15. 添加示例配置和使用文档

- 影响文件：`README.md`、`examples/mycode.anthropic.yaml`、`examples/mycode.openai-responses.yaml`、`examples/mycode.openai-chat.yaml`
- 依赖任务：任务 3、任务 7、任务 9、任务 10、任务 11
- 工作内容：说明安装、启动、配置格式、三种协议示例、环境变量认证、thinking 配置和当前阶段不支持的能力。
- 参考资料定位：`doc/stage-01-pure-chat-tui/spec.md` 的“配置设计”和“Out of Scope”。

## 16. 端到端验证

- 影响文件：`tests/test_e2e_chat.py`、`README.md`
- 依赖任务：任务 14、任务 15
- 工作内容：通过 mocked LLM 或 mocked HTTP stream 跑公开命令路径，验证启动、流式输出、多轮上下文、清空记忆和退出。
- 参考资料定位：`doc/stage-01-pure-chat-tui/checklist.md` 的“端到端验收”。
