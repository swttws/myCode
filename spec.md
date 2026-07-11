# myCode 纯对话 TUI 规格说明

## 背景

myCode 是一个使用 Python 开发的终端 AI 编程助手项目。当前里程碑不是完整 Coding Agent，而是先建立一个可用的终端对话体验，让用户能在命令行里和大模型连续对话，并为后续 agent 能力留下清晰的 Provider 边界。

本阶段只做纯对话能力。myCode 不会替模型读取项目文件、执行 shell 命令、修改代码，也不会暴露 tool use 或函数调用能力。

## 目标用户

- 希望在终端启动 myCode，并直接与大模型对话的开发者。
- 希望先建立可扩展 Provider 抽象，再逐步加入 tool use、代码编辑等能力的项目维护者。
- 需要通过配置在 Anthropic Claude、OpenAI 原生 API 和 OpenAI-compatible 服务之间切换的用户。

## 目标

- 用户可以从终端启动 myCode，并进入交互式 TUI 对话界面。
- 用户输入问题后，可以立即看到大模型回复以流式方式输出。
- 当前进程内保留多轮对话历史，让后续问题能带上前文上下文。
- 通过 YAML 配置选择 LLM 协议、模型、请求地址和认证信息。
- 支持 Anthropic Claude 和 OpenAI 两类协议后端。
- 支持 OpenAI Responses API 和 OpenAI Chat Completions API 两种 OpenAI 协议入口。
- 支持 Claude extended thinking，但默认不把 thinking 内容显示为普通回复。
- Provider 层暴露统一的流式接口，方便后续加入新的后端。
- 认证信息可以直接写在 YAML 中，也可以从环境变量读取。

## 能力清单

- 从命令行启动终端对话会话。
- 按固定优先级查找配置文件。
- 校验配置中用于选择协议、模型、请求地址和认证的核心字段。
- 根据配置中的协议创建对应 Provider。
- 在每轮请求中携带当前进程内的完整对话历史。
- 在 Provider 返回增量内容时，把 assistant 文本持续打印到终端。
- 把不同 Provider 的原始流式事件转换为统一的内部事件。
- 支持 Anthropic Claude 的流式响应。
- 支持 Claude extended thinking 作为 Anthropic Provider 的可选能力。
- 支持 OpenAI Responses API 的流式响应。
- 支持 OpenAI Chat Completions API 的流式响应，用于兼容性更强的后端。
- 提供基础会话命令，用于退出 TUI 和清空当前会话记忆。
- 在配置、认证、网络请求和流解析失败时给出可理解的终端错误提示。
- 错误提示中不回显真实 API key。

## 非功能要求

- Provider 一旦返回首个内容增量，终端应尽快显示对应输出。
- 单次请求失败后，TUI 应回到可继续输入的状态。
- TUI 层不直接依赖 Anthropic 或 OpenAI 的原始 SSE 事件格式。
- 后续新增 Provider 时，不应要求重写聊天主循环。
- 自动化测试不依赖真实 API key，可以通过 mocked stream 验证核心行为。
- 配置加载和校验结果要稳定、可诊断。
- 终端界面以清晰、可读、轻量为优先，不追求复杂全屏布局。

## 设计骨架

系统分为五层。

CLI 入口层负责解析启动参数，并启动会话。

配置层负责查找 YAML 文件、校验配置、解析认证值，并向其他模块提供 Provider 设置。

TUI 层负责用户输入、终端渲染、当前进程内的对话历史和基础会话命令。

Provider 抽象层定义 TUI 消费的统一流式事件契约。

Provider 实现层负责把 Anthropic 和 OpenAI 的协议细节转换为统一内部事件。

## 不做范围

- tool use 和函数调用。
- 读取、搜索或编辑本地文件。
- 代表模型执行 shell 命令。
- 会话持久化存储。
- 项目索引或检索增强。
- 多 agent 工作流。
- 复杂代码 diff 渲染。
- 认证配置向导。
- 模型价格、配额或成本估算。
- 带多面板、标签页或完整滚动管理的全屏终端应用。

## 完成定义

当用户可以配置任意一个受支持的 Provider，启动 myCode，在一个终端会话中连续提问，看到流式回复，清空当前进程内的会话历史，并正常退出时，本阶段功能完成。

同时，Provider 选择、流解析、配置加载、环境变量认证、Claude thinking 默认隐藏，以及至少一个 mocked 端到端对话流程都必须有自动化检查覆盖。
