# myCode 阶段 01：对话执行流程

## 流程概览

用户在终端启动 `mycode` 后，程序会加载配置、创建具体 LLM 客户端、创建进程内会话记忆，并启动异步 TUI。每次用户输入一条普通消息时，TUI 会通过 session 触发 LLM 的异步流式调用，并把模型返回的 SSE 增量实时打印到终端。

整体调用链如下：

```text
mycode 命令
  -> cli.main()
  -> ChatTUI.run()
  -> 用户输入消息
  -> ChatTUI._render_stream()
  -> ChatSession.send()
  -> BaseLLM.stream_chat()
  -> 具体协议客户端 stream_chat()
  -> httpx.AsyncClient.stream()
  -> parse_sse_events_async()
  -> TUI 逐段打印 assistant 输出
```

## 启动阶段

入口方法是 `src/mycode/cli.py` 中的 `main()`。

启动时会依次执行：

1. 解析命令行参数，读取可选的 `--config`。
2. 调用 `load_config()` 读取 YAML 配置。
3. 调用 `create_llm(config)` 根据 `protocol` 创建具体 LLM 客户端。
4. 创建 `InMemoryConversationMemory()` 作为当前进程内记忆。
5. 创建 `ChatSession(llm=llm, memory=memory)`。
6. 创建 `ChatTUI(session=session, show_thinking=config.thinking.show)`。
7. 使用 `asyncio.run(tui.run())` 启动异步 TUI。

CLI 只负责组装依赖，不直接处理模型协议、SSE 事件或对话历史。

## 用户输入阶段

交互循环在 `src/mycode/tui.py` 的 `ChatTUI.run()` 中。

TUI 会持续读取用户输入：

- 空输入会被忽略。
- `/exit` 会退出程序。
- `/clear` 会调用 `session.clear()` 清空当前进程内记忆。
- 普通文本会进入 `_render_stream(user_text)`。

真正开始一轮对话的是 `ChatTUI._render_stream()`：

```python
async for event in self._session.send(user_text):
    ...
```

也就是说，TUI 不直接调用协议客户端，而是通过 `ChatSession` 触发模型调用。

## 会话协调阶段

会话协调逻辑在 `src/mycode/session.py` 的 `ChatSession.send()` 中。

每轮普通消息会按这个顺序处理：

1. 将当前 user 消息追加到 memory。
2. 从 memory 读取完整上下文。
3. 调用统一 LLM 抽象的 `stream_chat()`。
4. 异步消费模型返回的 `StreamEvent`。
5. 将 `TEXT_DELTA` 拼成完整 assistant 回复。
6. 把每个事件继续 yield 给 TUI。
7. 流结束后，把完整 assistant 回复追加到 memory。

`THINKING_DELTA` 只会透传给 TUI，不会拼进普通 assistant 回复，也不会进入下一轮普通对话上下文。

如果 LLM 调用抛出 `LLMError`，session 会把它转换成 `ERROR` 事件返回给 TUI，TUI 显示错误后继续回到输入循环。

## LLM 抽象阶段

统一 LLM 抽象定义在 `src/mycode/llm/base.py`。

核心接口是：

```python
def stream_chat(self, messages: list[ChatMessage]) -> AsyncIterable[StreamEvent]:
    ...
```

所有具体 LLM 客户端都必须继承 `BaseLLM`，并实现异步流式 `stream_chat()`。

TUI 和 session 只依赖这个抽象，不依赖 Anthropic、OpenAI Responses 或 OpenAI Chat Completions 的原始协议格式。

## 协议选择阶段

协议工厂在 `src/mycode/protocols/factory.py`。

`create_llm(config)` 根据配置中的 `protocol` 选择具体客户端：

- `anthropic` -> `AnthropicLLM`
- `openai_responses` -> `OpenAIResponsesLLM`
- `openai_chat` -> `OpenAIChatLLM`

新增协议时，应优先新增一个继承 `BaseLLM` 的协议客户端，然后在工厂中注册，不应改写 TUI 主循环。

## 协议调用阶段

具体协议客户端位于 `src/mycode/protocols/`。

以 OpenAI Chat Completions 为例，`OpenAIChatLLM.stream_chat()` 会：

1. 组装请求 URL。
2. 组装请求 payload。
3. 在 payload 中设置 `stream: True`。
4. 使用 `httpx.AsyncClient.stream()` 发起异步流式请求。
5. 使用 `response.aiter_lines()` 异步读取 SSE 行。
6. 调用 `parse_sse_events_async()` 解析 SSE。
7. 将供应商事件转换成统一 `StreamEvent`。
8. 立即 yield 给上层 session。

这个阶段不能等待完整响应结束后再返回。只要首个正文增量到达，就应该产出对应的 `TEXT_DELTA` 事件。

## SSE 解析阶段

SSE 解析器在 `src/mycode/protocols/sse.py`。

异步入口是 `parse_sse_events_async()`。

它负责把 HTTP 行流转换成统一的 `SSEEvent`：

- 支持 `event:` 字段。
- 支持 `data:` 字段。
- 支持多行 `data:` 合并。
- 使用空行作为一个 SSE 事件结束标记。
- 忽略注释行。

协议客户端再根据各自供应商的 `data` JSON，把 SSEEvent 映射成统一的 `StreamEvent`。

## 流式输出阶段

TUI 在 `ChatTUI._render_stream()` 中异步消费 session 事件：

- `TEXT_DELTA`：立即打印到终端。
- `THINKING_DELTA`：只有配置 `thinking.show: true` 时才用弱化样式打印。
- `ERROR`：打印错误提示。
- `DONE`：当前轮输出结束。

因此，用户看到的 assistant 回复是边生成边显示的，不是等模型完整生成后一次性打印。

## 多轮对话阶段

多轮记忆由 `src/mycode/memory/` 包负责。

当前阶段使用 `InMemoryConversationMemory`：

- 本进程内保存 user 和 assistant 消息。
- 每轮 LLM 请求都会携带 memory 中的完整上下文。
- `/clear` 会清空 memory。
- 退出进程后历史丢失。

后续如果要做持久化记忆，应新增 memory 实现，而不是把持久化逻辑写进 TUI 或协议客户端。

## 当前边界

Stage 01 只做纯对话。

当前流程不会：

- 调用 tool use。
- 读取或编辑本地文件。
- 执行 shell 命令。
- 生成 patch。
- 持久化保存会话历史。

后续阶段如果加入 agent 能力，应优先接在 session 和 memory 之间，或作为新的 tool 调度层接入，不应破坏当前 LLM 协议抽象和异步流式输出链路。
