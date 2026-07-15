# myCode

myCode 是一个使用 Python 开发的终端 AI 编程助手。当前处于 Stage 03：Agent Loop 与事件流。

用户启动 `mycode` 后输入问题，程序会把请求交给独立 Agent Loop。Agent 会调用配置中的大模型 API，通过稳定事件流向 TUI 输出用户消息、thinking、文本增量、工具开始、工具结果、最终回复、错误、取消和等待审批状态。对于 `openai_responses` 和 `openai_chat`，模型可以在同一用户回合中发起工具调用；myCode 会执行工具、把结构化结果写回对话历史，并自动进入下一轮 LLM 调用，直到模型输出最终文本或触发错误、取消、超时、最大轮数等终止条件。

LLM 调用链路使用异步流式实现：协议客户端基于 `httpx.AsyncClient.stream()` 发起 SSE 请求，Agent 将协议层事件转换为上层稳定事件，session 和 TUI 只消费 Agent 事件，不直接理解供应商事件细节。

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

IDEA/PyCharm 里可以创建 Python 运行配置，然后用 Debug 启动。不要在 IDEA 的 Terminal 或外部 PowerShell 里直接运行 `python -m ...` 来期待断点生效；断点只有在 IDE Debug 配置启动的进程里才会命中。

推荐配置如下：

| 配置项 | 值 |
| --- | --- |
| 配置类型 | Python |
| 运行目标 | Module name |
| 模块名 | `mycode.dev_launcher` |
| 工作目录 | 项目根目录，例如 `D:\java\project\myCode\myCode` |
| 参数 | `--config examples/mycode.openai-responses.yaml` |

启动器不会再弹出外部 Windows 窗口，而是直接复用 IDEA 的 Run/Debug 控制台；CLI 输入输出和开发日志都会显示在这个控制台里，断点调试也会留在同一个进程内。如果 IDEA Debug 控制台不支持 `prompt_toolkit` 的 Windows 控制台能力，TUI 会自动降级为普通输入模式。

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

`openai_responses` 支持 Stage 03 的工具系统，会把工具注册为 Responses API 的 function tools。工具读写分类只用于本地 Agent 调度，不会进入 OpenAI payload。

## OpenAI Chat Completions

```yaml
protocol: openai_chat
model: your-openai-model
base_url: https://api.openai.com/v1
api_key: ${OPENAI_API_KEY}
```

`openai_chat` 支持 Stage 03 的工具系统，也适合很多 OpenAI-compatible 网关。工具调用历史和工具结果历史会继续转换为 Chat Completions 可理解的 message。

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

`thinking` 是可选配置，只对 Anthropic 生效。默认不显示 thinking，也不会把 thinking 写入普通 assistant 历史。Stage 03 只为 Anthropic 保留纯对话和 thinking 流式能力，暂不实现 Anthropic 工具调用。

## 核心工具

Stage 03 内置六个工具，工具相关代码集中在 `src/mycode/tool` 包下：

- `read_file`：读取工作目录内的 UTF-8 文本文件。
- `write_file`：写入工作目录内的 UTF-8 文本文件，并创建父目录。
- `edit_file`：只在原文唯一匹配时替换文本，零匹配或多匹配都会返回结构化错误。
- `run_command`：在工作目录内执行 shell 命令，返回退出码、stdout、stderr 和超时状态。
- `find_files`：按 glob 风格模式查找工作目录内文件。
- `search_code`：在 UTF-8 文本文件中搜索代码内容，返回路径、行号和行内容。

读文件、写文件和改文件共用一层带锁文本缓存，避免同一进程内读写状态串扰。

## Agent Loop 与事件流

Stage 03 新增 `src/mycode/agent` 包作为 Agent 主边界。Agent Loop 每轮会构造最小 system prompt、读取 memory、调用 LLM、收集工具调用、执行工具、回填工具结果，并在需要时进入下一轮。

上层只依赖 Agent 事件流：

- `user_message`：用户消息进入本轮。
- `thinking_delta`：模型 thinking 增量，可由 TUI 配置决定是否显示。
- `text_delta`：assistant 文本增量。
- `tool_call_started`：工具开始执行。
- `tool_result`：工具执行结果，成功和失败都以结构化结果输出。
- `final_response`：本轮最终回复。
- `approval_required`：`plan-only` 下写工具等待用户审批。
- `error`：包含机器可读错误类别的失败事件。
- `cancelled`：本轮被取消。

## 工具分批

每个工具定义都显式声明读/写分类。读类包括 `read_file`、`find_files`、`search_code`；写类包括 `write_file`、`edit_file`、`run_command`。

当模型在同一轮返回多个工具调用时，Agent 会按模型给出的顺序做工具分批：连续读工具并发执行，写工具单独串行执行，写工具后的读工具进入后续批次。工具失败不会自动回滚；失败结果会回填给模型，由下一轮决定如何继续。

## plan-only

`/plan-only on` 会开启会话内 plan-only 模式。该模式下读工具仍可执行，写工具会先产出等待审批事件；用户输入 `y` 批准当前写工具一次，输入 `n` 拒绝并把结构化拒绝结果回填给模型，输入 `c` 取消本轮。批准只放行当前工具一次，不会关闭 plan-only。

`/plan-only off` 关闭该模式，`/plan-only` 显示当前状态。`/clear` 会清空会话历史并复位 plan-only 状态。

## 交互命令

- `/clear`：清空当前进程内的对话上下文，包括工具调用历史和工具结果历史。
- `/plan-only`：显示当前 plan-only 状态。
- `/plan-only on`：开启写工具审批模式。
- `/plan-only off`：关闭写工具审批模式。
- `/exit`：退出 myCode。

## 当前阶段不做

Stage 03 不做复杂 system prompt 组装，不做完整复杂权限策略，不做 Agent 递归调用、子任务委派或多 Agent 调度，不做项目索引、RAG、长期记忆、代码符号图谱或上下文压缩，也不做复杂 TUI 全屏面板。

本阶段也不实现 Anthropic 工具调用，不做真实网络或真实 API key 依赖的验收，不做工具失败后的自动回滚。后续能力会基于当前的 LLM、protocols、memory、tool 和 agent 边界继续扩展。
