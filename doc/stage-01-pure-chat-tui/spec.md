# myCode 阶段 01：纯对话 TUI 规格说明

## 阶段标识

- 阶段编号：Stage 01
- 阶段名称：纯对话 TUI
- 阶段目标：先完成可流式多轮对话的命令行 AI 助手，不包含 agent 工具能力。

## 背景

myCode 是一个使用 Python 开发的终端 AI 编程助手项目，目标形态类似 Claude Code。本阶段只构建最小可用的命令行对话体验：用户在终端启动 myCode 后进入交互式界面，输入问题，myCode 调用大模型 API，并把回复以流式方式打印出来。

本阶段不是完整 Coding Agent。系统不会读取项目文件、执行 shell 命令、编辑代码、调用工具或替用户操作本地环境。当前目标是先把对话主流程、LLM 抽象、协议层边界、配置加载和会话记忆打牢，为后续 agent 能力留下可扩展结构。

## 目标用户

- 希望直接在终端里与大模型连续对话的开发者。
- 希望先搭建清晰 LLM Provider 架构，再逐步加入工具调用和代码编辑能力的项目维护者。
- 需要通过 YAML 配置在 Anthropic Claude、OpenAI 官方接口和 OpenAI-compatible 服务之间切换的用户。

## 目标

- 用户可以通过命令启动 myCode，并进入增强型终端对话界面。
- 用户输入消息后，可以看到 assistant 回复以 SSE 流式增量输出。
- 当前进程内支持多轮对话，后续请求会携带前文上下文。
- 会话记忆通过独立 memory 包抽象，当前只实现进程内记忆，并为后续持久化预留接口。
- LLM 层提供统一抽象基类，所有具体 LLM 客户端都继承它。
- LLM 调用链路必须采用异步接口，不能在协议请求和流式消费中使用同步 HTTP 调用。
- 协议实现统一放在 protocols 包下，TUI 和记忆层不直接依赖具体供应商协议。
- YAML 配置使用四个核心字段描述 LLM 供应商信息：协议、模型、请求地址和认证。
- 支持 Anthropic Claude 协议。
- 支持 OpenAI Responses API 协议。
- 支持 OpenAI Chat Completions API 协议。
- 支持 Claude extended thinking 的可选配置。

## 能力清单

- 从命令行启动交互式对话应用。
- 使用增强 TUI 提供可读的输入提示和流式输出体验。
- 从 YAML 配置中读取 LLM 协议、模型、请求地址和认证信息。
- 支持认证字段直接写入字面值或引用环境变量。
- 根据配置协议创建对应的 LLM 客户端。
- 通过统一 LLM 抽象发起流式聊天请求。
- 使用异步 SSE 处理供应商返回的流式事件。
- 将不同协议的流式数据映射为统一内部事件。
- 在终端中逐段打印 assistant 正文增量。
- 支持 Claude extended thinking，并将 thinking 与普通 assistant 正文区分。
- 默认不把 thinking 内容写入普通 assistant 对话历史。
- 在当前进程内保存多轮 user 和 assistant 消息。
- 支持清空当前会话记忆。
- 支持正常退出交互会话。
- 在配置错误、认证错误、网络错误或流解析错误时给出可理解的错误提示。
- 错误提示不得泄露真实 API key。

## 非功能要求

- TUI 主循环只能依赖统一 LLM 抽象，不能直接消费 Anthropic 或 OpenAI 的原始事件结构。
- TUI 和会话协调逻辑应通过异步流消费 LLM 事件，避免模型请求阻塞主流程。
- 会话逻辑只能依赖 memory 抽象，不能把历史存储细节写死在 TUI 中。
- 协议层集中在 protocols 包内，新增协议时不应改写 TUI 主循环。
- 协议层 HTTP 客户端应使用异步客户端。
- Provider 一旦收到正文增量，应尽快把内容交给 TUI 渲染。
- 协议客户端不能先读取完整响应正文再返回，必须在首个 SSE 正文增量到达时产出统一流式事件。
- 单次请求失败后，应用应回到可继续输入的状态。
- 自动化测试不依赖真实 API key 或真实外部网络。
- 配置加载、协议选择、SSE 解析、会话记忆和 TUI 命令行为都应可测试。
- 终端界面应保持轻量、清晰、可读，不做复杂全屏布局。

## 配置设计

配置文件使用 YAML。四个核心字段是：

- `protocol`：决定走哪一种协议。
- `model`：指定模型。
- `base_url`：指定请求地址。
- `api_key`：用于认证。

Claude extended thinking 使用可选配置描述，不属于四个核心字段。thinking 配置只影响 Anthropic 协议实现。

## 架构骨架

系统分为六个主要部分：

- CLI 入口层：解析命令行参数，加载配置，组装应用依赖，启动 TUI。
- 配置层：查找 YAML 文件，校验核心字段，解析环境变量认证。
- TUI 层：处理用户输入、终端渲染、内置命令和错误提示。
- LLM 抽象层：定义统一 LLM 抽象基类、消息结构和流式事件结构。
- 协议层：在 protocols 包中实现 Anthropic、OpenAI Responses、OpenAI Chat Completions 和共享 SSE 解析。
- 记忆层：在 memory 包中定义会话记忆抽象，并实现当前进程内记忆。

数据流为：

1. 用户在 TUI 输入消息。
2. TUI 将消息交给会话协调逻辑。
3. 会话协调逻辑把 user 消息写入 memory。
4. 会话协调逻辑读取 memory 中的完整上下文。
5. 统一 LLM 客户端根据上下文发起异步流式请求。
6. protocols 包中的具体实现异步调用供应商 API 并解析 SSE。
7. LLM 客户端产出统一异步流式事件。
8. TUI 通过异步事件流逐段渲染 assistant 输出。
9. assistant 正文流完成后写入 memory。

## 多轮对话与记忆边界

本阶段实现当前进程内的多轮对话记忆。用户启动 myCode 后，当前会话中的 user 和 assistant 消息会保存在内存里；每次新请求都会携带当前内存中的完整对话上下文。

记忆相关代码统一放在 memory 包下。TUI 和会话协调逻辑只依赖记忆抽象，不直接依赖具体存储实现。

本阶段不做历史持久化。退出 myCode 后，本次会话历史丢失。后续可以在不重写 TUI 的前提下增加文件、数据库或其他持久化记忆实现。

## 协议边界

协议相关代码统一放在 protocols 包下。每个具体协议客户端都继承统一 LLM 抽象基类，并负责把供应商协议转换为内部统一事件。

Anthropic 协议负责 Claude Messages API 的请求构造、SSE 消费、正文增量映射和 extended thinking 映射。

OpenAI Responses 协议负责 Responses API 的请求构造、SSE 消费和输出文本增量映射。

OpenAI Chat Completions 协议负责 Chat Completions API 的请求构造、SSE 消费和 chat delta 映射。

共享 SSE parser 应放在 protocols 包中，供各协议实现复用。

## Claude Extended Thinking

Claude extended thinking 是 Anthropic 协议的可选能力。配置开启后，请求会携带 thinking 设置。协议层会把 thinking 增量映射为独立的内部事件。

默认情况下，TUI 不显示 thinking 内容，也不会把 thinking 内容写入普通 assistant 消息历史。若用户配置显示 thinking，TUI 应用可区分的弱化样式输出 thinking，而不是把它混成 assistant 正文。

## Out of Scope

- tool use 或函数调用。
- 读取、搜索、索引本地项目文件。
- 执行 shell 命令。
- 创建、修改或删除代码文件。
- 自动生成 patch 或 diff。
- 会话历史持久化。
- 会话列表、会话恢复和历史搜索。
- 多 agent 工作流。
- 项目级 RAG。
- 成本统计、用量统计或价格估算。
- 复杂全屏 TUI、多面板布局、标签页和滚动管理。
- 认证配置向导。

## 完成定义

当用户可以配置任意一个受支持协议，启动 myCode，在增强终端界面中连续提问，看到 assistant 回复流式输出，确认后续问题携带前文上下文，使用命令清空当前记忆，并正常退出时，本阶段功能完成。

同时，配置加载、环境变量认证、协议选择、统一 LLM 抽象、三种协议的流式事件映射、Claude thinking 默认不进入普通历史、memory 抽象、多轮上下文传递、清空记忆和 mocked 端到端对话流程都必须有自动化检查覆盖。
