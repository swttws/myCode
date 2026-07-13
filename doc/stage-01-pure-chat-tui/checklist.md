# myCode 阶段 01：纯对话 TUI 验收清单

## 阶段标识

- 阶段编号：Stage 01
- 阶段名称：纯对话 TUI
- 阶段目标：验收可流式多轮对话的命令行 AI 助手是否达到本阶段完成定义。

## 配置

- [ ] 使用 `--config path/to/config.yaml` 启动时，读取该文件而不是默认位置。
- [ ] 省略 `--config` 且当前目录存在 `mycode.yaml` 时，读取当前目录的 `mycode.yaml`。
- [ ] 没有显式配置和当前目录配置时，读取 `~/.mycode/config.yaml`。
- [ ] YAML 同时包含 `protocol`、`model`、`base_url`、`api_key` 时，配置校验通过。
- [ ] YAML 缺少 `protocol`、`model`、`base_url`、`api_key` 中任意字段时，在 TUI 启动前报错。
- [ ] `api_key: sk-test-literal` 会被当作字面值接受。
- [ ] `api_key: ${MYCODE_TEST_API_KEY}` 会解析为环境变量 `MYCODE_TEST_API_KEY` 的值。
- [ ] `api_key` 引用未设置的环境变量时，不发起任何 HTTP 请求，并在启动阶段报错。
- [ ] 任意配置错误的输出中不包含真实 API key。

## LLM 抽象

- [ ] 存在统一 LLM 抽象基类。
- [ ] 统一 LLM 抽象基类暴露异步流式聊天接口。
- [ ] Anthropic 客户端继承统一 LLM 抽象基类。
- [ ] OpenAI Responses 客户端继承统一 LLM 抽象基类。
- [ ] OpenAI Chat Completions 客户端继承统一 LLM 抽象基类。
- [ ] TUI 模块不直接 import 具体协议客户端。
- [ ] Session 模块不直接 import 具体协议客户端。
- [ ] LLM 抽象层定义统一聊天消息结构。
- [ ] LLM 抽象层定义统一流式事件结构。

## 协议选择

- [ ] `protocol: anthropic` 会创建 Anthropic LLM 客户端。
- [ ] `protocol: openai_responses` 会创建 OpenAI Responses LLM 客户端。
- [ ] `protocol: openai_chat` 会创建 OpenAI Chat Completions LLM 客户端。
- [ ] 未知 `protocol` 值会在 TUI 启动前报错。
- [ ] 具体协议实现集中在 `src/mycode/protocols/` 包下。
- [ ] 共享 SSE parser 位于 `src/mycode/protocols/` 包下。
- [ ] 具体协议客户端使用异步 HTTP 客户端发起请求。

## SSE 与流式输出

- [ ] SSE parser 能解析单个 `data:` 事件。
- [ ] SSE parser 能解析带 `event:` 名称的事件。
- [ ] SSE parser 能解析多行 `data:` 事件。
- [ ] SSE parser 遇到空行时产出一个完整事件。
- [ ] SSE parser 能识别流结束标记。
- [ ] 协议层能从异步 HTTP 行流中解析 SSE 事件。
- [ ] mock SSE 输入包含两个文本增量时，TUI 能在流完成前收到两个可见输出片段。
- [ ] mock SSE 响应未结束时，协议客户端已能产出第一段 assistant 文本事件。
- [ ] 流解析失败时，进程不崩溃，并回到输入提示符。

## Anthropic 协议

- [ ] Anthropic 请求使用配置中的 `model`、`base_url` 和 `api_key`。
- [ ] Anthropic 正文增量会被映射为统一 assistant 文本事件。
- [ ] Anthropic message stop 或等价结束事件会被映射为统一完成事件。
- [ ] Anthropic 协议错误会被映射为可展示的应用错误。
- [ ] 开启 thinking 配置时，Anthropic 请求包含 thinking 设置。
- [ ] Anthropic thinking 增量会被映射为统一 thinking 事件。

## OpenAI Responses 协议

- [ ] OpenAI Responses 请求使用配置中的 `model`、`base_url` 和 `api_key`。
- [ ] OpenAI Responses 输出文本增量会被映射为统一 assistant 文本事件。
- [ ] OpenAI Responses 完成事件会被映射为统一完成事件。
- [ ] OpenAI Responses 协议错误会被映射为可展示的应用错误。

## OpenAI Chat Completions 协议

- [ ] OpenAI Chat Completions 请求使用配置中的 `model`、`base_url` 和 `api_key`。
- [ ] OpenAI Chat Completions 内容 delta 会被映射为统一 assistant 文本事件。
- [ ] OpenAI Chat Completions 完成事件或 `[DONE]` 会被映射为统一完成事件。
- [ ] OpenAI Chat Completions 协议错误会被映射为可展示的应用错误。

## Claude Extended Thinking

- [ ] 未配置显示 thinking 时，thinking 增量不会打印到普通终端正文里。
- [ ] 配置显示 thinking 时，thinking 增量使用可区分的弱化样式打印。
- [ ] Thinking 增量不会追加到普通 assistant 消息正文。
- [ ] Thinking 增量不会进入下一轮普通对话上下文。

## Memory

- [ ] 记忆相关代码集中在 `src/mycode/memory/` 包下。
- [ ] 存在统一会话记忆抽象接口。
- [ ] 当前阶段存在进程内记忆实现。
- [ ] 追加 user 消息后，memory 返回的上下文包含该 user 消息。
- [ ] assistant 正文流完成后，memory 返回的上下文包含完整 assistant 回复。
- [ ] 调用清空方法后，memory 返回空上下文。
- [ ] 退出进程后不要求恢复上一轮会话历史。

## TUI 行为

- [ ] 启动 `mycode` 后进入交互式输入提示符。
- [ ] 输入用户消息后，该消息会发送给配置中的 LLM 客户端。
- [ ] TUI 通过异步流消费 LLM 输出事件。
- [ ] LLM 流仍在进行时，assistant 输出会逐步显示。
- [ ] 成功回复后，下一次请求会包含上一轮 user 消息和 assistant 回复。
- [ ] 输入 `/clear` 会清空当前进程内的对话历史。
- [ ] 输入 `/exit` 会终止 TUI，并返回退出码 `0`。
- [ ] 输入空消息不会发起 LLM 请求。
- [ ] LLM 请求失败时，终端显示错误信息，并回到输入提示符。

## 端到端验收

- [ ] 不需要真实 API key，`pytest` 也能通过 mocked stream 完成测试。
- [ ] mocked 端到端运行会启动公开命令路径，输入 `hello` 后流式收到 `hi`。
- [ ] mocked 端到端运行发送第二条消息时，第二次 LLM 请求包含第一轮 user 和 assistant 上下文。
- [ ] mocked 端到端运行输入 `/clear` 后，下一次 LLM 请求不包含清空前的对话轮次。
- [ ] mocked 端到端运行输入 `/exit` 后，进程正常退出。

## 文档

- [ ] `README.md` 包含 Anthropic 配置示例。
- [ ] `README.md` 包含 OpenAI Responses 配置示例。
- [ ] `README.md` 包含 OpenAI Chat Completions 配置示例。
- [ ] 示例配置使用 `${ANTHROPIC_API_KEY}` 或 `${OPENAI_API_KEY}`，不包含真实密钥。
- [ ] README 说明 Claude extended thinking 的配置方式。
- [ ] README 说明当前阶段不支持 tool use、文件操作、代码编辑、shell 执行和持久化会话。
