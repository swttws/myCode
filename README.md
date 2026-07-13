# myCode

myCode 是一个使用 Python 开发的终端 AI 编程助手。当前处于 Stage 01：纯对话 TUI。

这一阶段只提供命令行多轮对话：用户启动 `mycode` 后输入问题，程序调用配置中的大模型 API，并通过 SSE 流式输出 assistant 回复。

LLM 调用链路使用异步流式实现：协议客户端基于 `httpx.AsyncClient.stream()` 发起 SSE 请求，session 和 TUI 通过异步流消费模型事件，不等待完整响应结束后再输出。

## 安装

```powershell
python -m pip install -e ".[dev]"
```

## 启动

```powershell
mycode --config examples/mycode.anthropic.yaml
```

也可以省略 `--config`，myCode 会按顺序查找：

1. 当前目录的 `mycode.yaml`
2. 用户目录的 `~/.mycode/config.yaml`

## 配置格式

YAML 配置包含四个核心字段：

```yaml
protocol: anthropic
model: your-claude-model
base_url: https://api.anthropic.com
api_key: ${ANTHROPIC_API_KEY}
```

`api_key` 可以直接写字面值，也可以使用 `${ENV_NAME}` 引用环境变量。建议使用环境变量。

## Anthropic

```yaml
protocol: anthropic
model: your-claude-model
base_url: https://api.anthropic.com
api_key: ${ANTHROPIC_API_KEY}
thinking:
  enabled: true
  budget_tokens: 2048
  show: false
```

`thinking` 是可选配置，只对 Anthropic 生效。默认不显示 thinking，也不会把 thinking 写入普通 assistant 历史。

## OpenAI Responses

```yaml
protocol: openai_responses
model: your-openai-model
base_url: https://api.openai.com/v1
api_key: ${OPENAI_API_KEY}
```

## OpenAI Chat Completions

```yaml
protocol: openai_chat
model: your-openai-model
base_url: https://api.openai.com/v1
api_key: ${OPENAI_API_KEY}
```

`openai_chat` 也适合很多 OpenAI-compatible 网关。

## 交互命令

- `/clear`：清空当前进程内的对话上下文。
- `/exit`：退出 myCode。

## 当前阶段不做

Stage 01 不包含 tool use、文件操作、代码编辑、shell 执行、持久化会话、项目索引、多 agent 工作流和自动 patch。

这些能力会在后续阶段基于当前的 LLM、protocols 和 memory 边界继续扩展。
