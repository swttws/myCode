# myCode

myCode 是一个使用 Python 开发的终端 AI 编程助手。当前处于 Stage 02：OpenAI 系列单轮工具调用。

用户启动 `mycode` 后输入问题，程序调用配置中的大模型 API，并通过 SSE 流式输出 assistant 回复。对于 `openai_responses` 和 `openai_chat`，模型可以在本轮回复中发起一次工具调用；myCode 会执行工具、把结构化结果写回对话历史，然后结束本轮，等待用户继续输入。

LLM 调用链路使用异步流式实现：协议客户端基于 `httpx.AsyncClient.stream()` 发起 SSE 请求，session 和 TUI 通过异步流消费模型事件，不等待完整响应结束后再输出。

## 安装

```powershell
python -m pip install -e ".[dev]"
```

## 启动

```powershell
mycode --config examples/mycode.openai-responses.yaml
```

也可以省略 `--config`，myCode 会按顺序查找：

1. 当前目录的 `mycode.yaml`
2. 用户目录的 `~/.mycode/config.yaml`

## 开发调试启动

IDEA/PyCharm 里可以创建 Python 运行配置，模块名填 `mycode.dev_launcher`，工作目录填项目根目录，然后用 Debug 启动。启动器不会再弹出外部 Windows 窗口，而是直接复用 IDEA 的 Run/Debug 控制台；CLI 输入输出和开发日志都会显示在这个控制台里，断点调试也会留在同一个进程内。

也可以直接运行：

```powershell
python -m mycode.dev_launcher --config examples/mycode.openai-responses.yaml
```

安装为可编辑包后，也可以使用：

```powershell
mycode-dev --config examples/mycode.openai-responses.yaml
```

日志会同时输出到 IDEA 控制台和文件。日志文件默认写入系统临时目录下的 `mycode-dev.log`，也可以用 `--log-file path/to/file.log` 指定。

## 配置格式

YAML 配置包含四个核心字段：

```yaml
protocol: openai_responses
model: your-openai-model
base_url: https://api.openai.com/v1
api_key: ${OPENAI_API_KEY}
```

`api_key` 可以直接写字面值，也可以使用 `${ENV_NAME}` 引用环境变量。建议使用环境变量。

## OpenAI Responses

```yaml
protocol: openai_responses
model: your-openai-model
base_url: https://api.openai.com/v1
api_key: ${OPENAI_API_KEY}
```

`openai_responses` 支持 Stage 02 的工具系统，会把工具注册为 Responses API 的 function tools。

## OpenAI Chat Completions

```yaml
protocol: openai_chat
model: your-openai-model
base_url: https://api.openai.com/v1
api_key: ${OPENAI_API_KEY}
```

`openai_chat` 支持 Stage 02 的工具系统，也适合很多 OpenAI-compatible 网关。

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

`thinking` 是可选配置，只对 Anthropic 生效。默认不显示 thinking，也不会把 thinking 写入普通 assistant 历史。Stage 02 只为 Anthropic 预留扩展口，暂不实现 Anthropic 工具调用。

## 核心工具

Stage 02 内置六个工具，工具相关代码集中在 `src/mycode/tool` 包下：

- `read_file`：读取工作目录内的 UTF-8 文本文件。
- `write_file`：写入工作目录内的 UTF-8 文本文件，并创建父目录。
- `edit_file`：只在原文唯一匹配时替换文本，零匹配或多匹配都会返回结构化错误。
- `run_command`：在工作目录内执行 shell 命令，返回退出码、stdout、stderr 和超时状态。
- `find_files`：按 glob 风格模式查找工作目录内文件。
- `search_code`：在 UTF-8 文本文件中搜索代码内容，返回路径、行号和行内容。

读文件、写文件和改文件共用一层带锁文本缓存，避免同一进程内读写状态串扰。

## 交互命令

- `/clear`：清空当前进程内的对话上下文，包括工具调用历史和工具结果历史。
- `/exit`：退出 myCode。

## 当前阶段不做

Stage 02 不做 Agent Loop，不做多工具连环调用，也不做 Anthropic 工具调用。模型拿到一次工具执行结果后不会自动再次请求 LLM，本轮会停下并等待用户继续输入。

本阶段也不包含持久化会话、项目索引、多 agent 工作流和自动 patch。这些能力会在后续阶段基于当前的 LLM、protocols、memory 和 tool 边界继续扩展。
