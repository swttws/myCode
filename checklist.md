# myCode 纯对话 TUI 验收清单

## 配置

- [ ] 使用 `--config path/to/config.yaml` 启动时，读取该文件，而不是其他配置位置。
- [ ] 省略 `--config` 且当前目录存在 `mycode.yaml` 时，读取当前目录的 `mycode.yaml`。
- [ ] 没有显式配置和当前目录配置时，读取 `~/.mycode/config.yaml`。
- [ ] YAML 配置包含 `protocol`、`model`、`base_url`、`api_key` 时，配置校验通过。
- [ ] YAML 配置缺少 `protocol`、`model`、`base_url`、`api_key` 中任意一项时，在 TUI 启动前报错。
- [ ] `api_key: ${MYCODE_TEST_API_KEY}` 会解析为 `MYCODE_TEST_API_KEY` 的环境变量值。
- [ ] `api_key: sk-test-literal` 会被接受为字面值。
- [ ] `api_key` 引用了未设置的环境变量时，不发起任何 HTTP 请求，并在启动阶段报错。
- [ ] 错误输出不打印解析后的真实 API key。

## Provider 选择

- [ ] `protocol: anthropic` 会创建 Anthropic Provider。
- [ ] `protocol: openai_responses` 会创建 OpenAI Responses Provider。
- [ ] `protocol: openai_chat` 会创建 OpenAI Chat Completions Provider。
- [ ] 未知 `protocol` 值会在 TUI 启动前报错。
- [ ] TUI 只消费统一 Provider 流式事件，不直接检查 Anthropic 或 OpenAI 的原始 SSE payload。

## 流式输出

- [ ] mock SSE 输入包含两个文本增量时，终端在流完成前能看到两个可见输出片段。
- [ ] Anthropic 正文增量会被映射为 assistant 文本流式事件。
- [ ] Anthropic extended thinking 增量会被映射为 thinking 流式事件。
- [ ] OpenAI Responses 输出文本增量会被映射为 assistant 文本流式事件。
- [ ] OpenAI Chat Completions 内容增量会被映射为 assistant 文本流式事件。
- [ ] 流解析失败时，进程不崩溃，并回到输入提示符。

## Claude Extended Thinking

- [ ] 未开启 thinking 显示配置时，Anthropic thinking 增量不会打印到终端。
- [ ] 开启 thinking 显示配置时，Anthropic thinking 增量会用视觉上较弱且可区分的样式打印。
- [ ] Thinking 增量不会追加到普通对话历史里的 assistant 消息正文。

## TUI 行为

- [ ] 启动 `mycode` 后进入交互式终端输入提示符。
- [ ] 输入用户消息后，该消息会发送给配置中的 Provider。
- [ ] Provider 流仍在进行时，assistant 输出会逐步显示。
- [ ] 成功回复后，下一次请求会包含上一轮用户消息和 assistant 回复。
- [ ] 输入 `/clear` 会清空当前进程内的对话历史。
- [ ] 输入 `/exit` 会终止 TUI，并返回退出码 `0`。
- [ ] Provider 请求失败时，终端显示错误信息，并回到输入提示符。

## 端到端验收

- [ ] 不需要真实 API key，`pytest` 也能通过 mocked HTTP stream 完成测试。
- [ ] mocked 端到端运行会启动公开命令，发送 `hello`，流式收到 `hi`，再发送第二条消息，并验证第二次 Provider 请求包含第一轮上下文。
- [ ] mocked 端到端运行输入 `/clear` 后，下一次 Provider 请求不包含之前的对话轮次。
- [ ] mocked 端到端运行输入 `/exit` 后，进程正常退出。

## 文档

- [ ] `README.md` 包含一个 Anthropic 配置示例。
- [ ] `README.md` 包含一个 OpenAI Responses 配置示例。
- [ ] `README.md` 包含一个 OpenAI Chat Completions 配置示例。
- [ ] 示例配置使用 `${ANTHROPIC_API_KEY}` 或 `${OPENAI_API_KEY}`，不包含真实密钥。
- [ ] README 说明 tool use、文件操作、代码编辑和持久化会话不属于当前里程碑。
