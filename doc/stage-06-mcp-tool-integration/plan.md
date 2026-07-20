# myCode Stage 06: MCP 远端工具接入 Plan

## 架构概览

新增 `mycode.mcp` 包作为 MCP 客户端边界，负责独立配置解析、两类传输、JSON-RPC 双向分发、协议生命周期、每 server 会话缓存和远端工具包装。它不依赖 Agent、TUI 或权限实现；只向上提供已发现的工具及结构化诊断。

现有 `ToolRegistry` 扩展为三个视图：完整注册工具、模型当前可见的工具定义、未发现延迟工具的“名称 + 描述”摘要。`MCPToolWrapper` 始终声明自己为延迟工具，完整 schema 只有在 `ToolSearch` 成功标记后才会从下一轮进入模型工具列表。

`ToolExecutor` 新增对异步工具实现的支持。既有本地工具继续在线程中以同步方式执行；远端 MCP wrapper 在 Agent 运行的事件循环中异步调用其所属 server。这样一个 server 的网络等待不会占用或破坏其他工具与连接。

CLI 在同一事件循环内加载 MCP 配置、建立可用 server、注册其工具，再启动 TUI；退出时统一关闭 HTTP 资源和 stdio 子进程。单个 server 的配置或连接失败只产生诊断，不阻断其余 server、本地工具和聊天功能。

## 核心数据结构

### MCP 配置

```python
class MCPTransportKind(str, Enum):
    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable_http"

@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    transport: MCPTransportKind
    timeout_seconds: float
    command: str | None
    args: tuple[str, ...]
    env: Mapping[str, str]
    url: str | None
    headers: Mapping[str, str]
    read_tools: frozenset[str]

@dataclass(frozen=True)
class MCPConfig:
    servers: tuple[MCPServerConfig, ...]

@dataclass(frozen=True)
class MCPDiagnostic:
    server_name: str | None
    category: str
    message: str
```

配置加载返回有效 server 与非致命的逐项诊断；配置文件不存在、无法解析或显式路径无效则抛出致命配置错误。环境变量在加载时解析，诊断中只保留变量名，不保留值。

### JSON-RPC 与会话

```python
@dataclass(frozen=True)
class JSONRPCError:
    code: int
    message: str
    data: Mapping[str, object] | None = None

class MCPTransport(Protocol):
    async def open(self) -> None: ...
    async def send(self, message: Mapping[str, object]) -> None: ...
    async def receive(self) -> AsyncIterator[Mapping[str, object]]: ...
    async def close(self) -> None: ...

class MCPConnection:
    async def initialize(self) -> tuple[RemoteTool, ...]: ...
    async def request(self, method: str, params: Mapping[str, object]) -> Mapping[str, object]: ...
    async def notify(self, method: str, params: Mapping[str, object]) -> None: ...
    async def close(self) -> None: ...
```

`MCPConnection` 独占请求 id 生成器、待响应 Future 映射、接收循环、已协商协议版本、server 能力与 HTTP 会话标识。接收循环负责按 id 完成 Future、记录通知、响应 `ping`、拒绝其他 server 请求，并将无效消息转换为协议故障。

### Server 池与远端工具

```python
class MCPServerState(str, Enum):
    NEW = "new"
    CONNECTING = "connecting"
    READY = "ready"
    FAILED = "failed"
    CLOSED = "closed"

@dataclass(frozen=True)
class RemoteTool:
    server_name: str
    remote_name: str
    public_name: str
    description: str
    parameters: JSONSchema
    kind: ToolKind

class MCPServerPool:
    async def initialize_all(self) -> tuple[MCPDiagnostic, ...]: ...
    async def call_tool(
        self, server_name: str, remote_name: str, arguments: ToolArguments
    ) -> ToolResult: ...
    def is_available(self, server_name: str) -> bool: ...
    async def close(self) -> None: ...
```

池为每个配置 server 维护独立锁、状态、连接与发现到的 `RemoteTool`。`public_name` 固定由 `server_name` 与 `remote_name` 组成，`kind` 默认为写，仅在 `read_tools` 精确匹配时为读。

### 延迟工具与异步执行

```python
@dataclass(frozen=True)
class DeferredToolSummary:
    name: str
    description: str

class MCPToolWrapper:
    @property
    def definition(self) -> ToolDefinition: ...
    def should_defer(self) -> bool: ...
    async def execute_async(self, arguments: ToolArguments) -> ToolResult: ...

class ToolSearch:
    @property
    def definition(self) -> ToolDefinition: ...
    def execute(self, arguments: ToolArguments) -> ToolResult: ...
```

`MCPToolWrapper.should_defer()` 固定返回 `True`。`ToolSearch` 只接受完整的公开工具名；它检查注册表和 server 可用性，返回完整定义并仅在成功时标记为已发现。

`ToolRegistry` 新增 `model_definitions()`、`deferred_summaries()` 与 `mark_discovered(name)`。`model_definitions()` 包含本地工具、`ToolSearch` 和已发现远端工具；`deferred_summaries()` 只返回尚未发现远端工具的名称及描述。`ToolExecutor` 检测 `execute_async()`：存在时直接 await，否则保留既有的 `asyncio.to_thread()` 同步工具执行路径。

## 模块设计

### `mycode.mcp.config`
**职责：** 定位、读取、解析并验证独立 MCP YAML 配置；解析环境变量引用；产出有效 server 与不泄露敏感值的诊断。
**对外接口：** `load_mcp_config()`、`MCPConfig`、`MCPServerConfig`、`MCPDiagnostic`、`MCPConfigError`。
**依赖：** 标准库、PyYAML。

### `mycode.mcp.jsonrpc`
**职责：** 校验 JSON-RPC 2.0 消息形状，创建请求、通知、成功响应、错误响应和取消通知；定义协议层错误。
**对外接口：** 消息编解码辅助函数、`JSONRPCError`、`MCPProtocolError`。
**依赖：** 标准库。

### `mycode.mcp.transports`
**职责：** 定义统一异步传输协议，提供 stdio 和 Streamable HTTP 实现。stdio 实现负责子进程启动、逐行 JSON 消息、stderr 消耗与分级终止。HTTP 实现负责请求头合并、MCP 协议与会话头、POST JSON 响应、SSE 事件解析及可选 GET 事件流。
**对外接口：** `MCPTransport`、`StdioTransport`、`StreamableHTTPTransport`。
**依赖：** `asyncio`、`httpx`、标准库。

### `mycode.mcp.connection`
**职责：** 在单一 transport 上运行双向 JSON-RPC：生成 id、维护 pending 请求、分派乱序响应、处理通知和 server 请求、执行 MCP 初始化与工具发现、在超时时尽力取消请求。
**对外接口：** `MCPConnection.initialize()`、`request()`、`notify()`、`close()`。
**依赖：** `jsonrpc`、`transports`。

### `mycode.mcp.pool`
**职责：** 按配置拥有多个独立连接及其状态锁；并行初始化；缓存已发现工具；隔离故障；在下次调用时重新连接和发现；统一释放所有资源。
**对外接口：** `MCPServerPool.initialize_all()`、`call_tool()`、`is_available()`、`close()`、`diagnostics`。
**依赖：** `config`、`connection`、`tools`、日志。

### `mycode.mcp.tools`
**职责：** 将已发现的远端工具映射为 myCode 工具，生成带 server 前缀的名称，应用读工具白名单，异步转发 `tools/call`，并提供常驻的 `ToolSearch`。
**对外接口：** `MCPToolWrapper`、`ToolSearch`、`RemoteTool`、`DeferredToolSummary`。
**依赖：** `pool`、`mycode.tool.base`、`mycode.tool.registry`。

### `mycode.tool`
**职责：** 保持本地工具行为不变，同时扩展注册表与执行器支持延迟工具和异步工具。
**对外接口变化：** `ToolRegistry` 新增模型可见定义、延迟摘要和发现状态；`ToolExecutor` 按工具能力选择同步线程执行或异步执行。
**依赖：** 不依赖 `mycode.mcp`，避免通用工具层反向耦合协议实现。

### `mycode.agent.loop` 与 `mycode.cli`
**职责：** Agent 在每轮从注册表取得模型可见定义，并将未发现工具摘要作为运行时系统提醒传给 `PromptBuilder`；CLI 新增 `--mcp-config`，在 TUI 所在事件循环内初始化、注册和清理 MCP server。
**依赖：** `mycode.mcp`、现有 Prompt、Tool、权限和 Session 边界。

## 模块交互

### 启动与注册

1. CLI 解析 `--config` 与 `--mcp-config`。
2. 主配置加载和 LLM 创建保持现有流程；MCP 配置加载器按显式路径、工作目录、用户目录的顺序解析独立文件。
3. CLI 进入唯一的应用事件循环，创建 `MCPServerPool`，调用 `initialize_all()`。
4. 每个有效 server 独立执行：打开传输 → `initialize` 请求 → 保存协商版本、能力与 HTTP 会话标识 → `notifications/initialized` → `tools/list`。
5. 每个成功发现的远端工具被包装为 `MCPToolWrapper` 并注册到既有 `ToolRegistry`；`ToolSearch` 只注册一次。
6. 单个 server 的加载、握手或发现失败进入池诊断并保留为 `FAILED`，不阻断 Agent、TUI、本地工具或其他 server。
7. TUI 退出后，CLI 在同一事件循环调用 `MCPServerPool.close()`，再结束应用。

### 每轮模型请求与延迟发现

1. Agent 从注册表读取模型可见定义：本地工具、`ToolSearch` 与已发现的 MCP 工具。
2. Agent 从注册表读取未发现 MCP 工具的“名称 + 描述”，以运行时系统提醒传给 `PromptBuilder`。
3. `PromptBuilder` 将该提醒仅注入当前请求，且完整 schema 仍不在模型工具列表中。
4. 当模型调用 `ToolSearch` 时，工具按公开名称检查该工具和所属 server；成功后返回完整 schema 并在注册表中标记为已发现。
5. Agent 将 `ToolSearch` 结果写入既有工具历史；下一轮模型请求才会包含该工具的正常工具定义。

### MCP 工具调用

1. 模型调用已发现的公开工具名。
2. Agent 保持既有顺序和读写批处理；权限拦截器使用 wrapper 的 `ToolDefinition.kind` 决定审批与 plan-only 行为。
3. `ToolExecutor` 识别 wrapper 的 `execute_async()`，不进入同步工具线程。
4. wrapper 调用池的 `call_tool(server_name, remote_name, arguments)`。
5. 池确认 server 为 `READY`；若为 `FAILED` 或连接已失效，先以该 server 的锁重新连接、初始化和发现。
6. `MCPConnection` 发送带新 id 的 `tools/call`，pending 映射等待对应响应；传输接收循环可在此期间分派其他响应、通知与 server 请求。
7. 远端成功内容或任意失败被转为现有 `ToolResult`；Agent 将其写入既有工具历史并开始下一轮模型请求。

### 接收、超时与故障恢复

- 接收循环按消息类型分流：响应完成对应 Future；通知记录日志；`ping` 原样成功响应；其他 server 请求返回 `-32601`。
- 单次请求超时时，连接移除 pending 条目、尽力发送取消通知、向调用方返回结构化超时；不等待迟到响应。
- transport 断连、解析失败或协议错误使所属 server 转为 `FAILED`，完成该 server 的 pending 请求为失败，并关闭其资源；其他 server 的任务不受影响。
- 下一次该 server 被 ToolSearch 或 wrapper 使用时，池在锁内重新建立会话、重新发现工具并恢复 `READY`；重连失败则返回结构化失败。

## 文件组织

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `src/mycode/mcp/__init__.py` | 导出 MCP 配置、池和工具适配公开入口 |
| 新建 | `src/mycode/mcp/models.py` | 配置、诊断、server 状态、远端工具与延迟摘要数据结构 |
| 新建 | `src/mycode/mcp/config.py` | 独立 YAML 配置发现、解析、校验和环境变量解析 |
| 新建 | `src/mycode/mcp/jsonrpc.py` | JSON-RPC 消息创建、校验、错误与取消通知 |
| 新建 | `src/mycode/mcp/transport.py` | 异步传输协议与共享接口 |
| 新建 | `src/mycode/mcp/stdio.py` | 子进程 stdio transport 和进程清理 |
| 新建 | `src/mycode/mcp/streamable_http.py` | Streamable HTTP、SSE、协议/会话头与 HTTP 资源管理 |
| 新建 | `src/mycode/mcp/connection.py` | 单 server 双向 JSON-RPC、初始化、pending 映射和入站分派 |
| 新建 | `src/mycode/mcp/pool.py` | 多 server 生命周期、连接复用、故障隔离、重连和统一关闭 |
| 新建 | `src/mycode/mcp/tools.py` | `MCPToolWrapper`、`ToolSearch` 与远端工具注册辅助 |
| 修改 | `src/mycode/tool/base.py` | 补充异步工具与延迟工具的类型契约 |
| 修改 | `src/mycode/tool/registry.py` | 模型可见定义、延迟摘要和已发现状态 |
| 修改 | `src/mycode/tool/executor.py` | 分流同步线程工具与异步工具 |
| 修改 | `src/mycode/tool/__init__.py` | 导出新增通用工具类型 |
| 修改 | `src/mycode/agent/loop.py` | 每轮构建延迟工具系统提醒并读取模型可见工具 |
| 修改 | `src/mycode/cli.py` | 增加 `--mcp-config`，在 TUI 事件循环内初始化和关闭 MCP 池 |
| 新建 | `tests/mcp_helpers.py` | 可控 stdio server 与 Streamable HTTP 测试辅助 |
| 新建 | `tests/test_mcp_config.py` | 配置发现、校验、环境变量和脱敏 |
| 新建 | `tests/test_mcp_jsonrpc.py` | 消息校验、id 分派、入站通知、`ping`、未知请求和超时 |
| 新建 | `tests/test_mcp_stdio.py` | stdio 生命周期、消息流、超时和子进程清理 |
| 新建 | `tests/test_mcp_streamable_http.py` | HTTP JSON/SSE、会话头、断连和资源关闭 |
| 新建 | `tests/test_mcp_pool.py` | 多 server 初始化、复用、隔离、重连和诊断 |
| 新建 | `tests/test_mcp_tools.py` | 名称前缀、默认写分类、读白名单、延迟 wrapper 与 ToolSearch |
| 修改 | `tests/test_tool_registry.py` | 模型可见定义、摘要和发现状态 |
| 修改 | `tests/test_tool_executor.py` | 异步工具执行分流 |
| 修改 | `tests/test_agent_loop.py` | 提醒注入、下一轮 schema 出现与权限链路 |
| 修改 | `tests/test_cli.py` | CLI 参数、MCP 启动、无配置兼容和关闭路径 |
| 新建 | `examples/mycode.mcp.yaml` | stdio 与 Streamable HTTP 配置示例 |
| 修改 | `README.md` | MCP 配置、支持范围、延迟发现和边界说明 |

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 协议实现 | 自建最小 MCP 客户端 | 只实现本期生命周期和工具能力，复用现有 `httpx`，避免 SDK API 与行为边界反向影响 Agent 集成。 |
| MCP 规范基线 | 当前 2025-11-25 规范 | 覆盖 JSON-RPC 2.0、stdio、Streamable HTTP、初始化、会话头和取消语义。 |
| 远端执行模型 | `execute_async()` 可选契约 | MCP I/O 留在应用事件循环；同步本地工具继续使用线程，不进行全量异步重写。 |
| 连接缓存 | 每 server、每进程、事件循环内一个池条目 | 复用初始化与工具清单，避免跨线程/跨事件循环共享不安全的 HTTP 或子进程资源。 |
| 延迟注入 | 注册表保存发现状态，ToolSearch 精确按名发现 | 保证完整 schema 默认不进工具列表，模型仍可从名称与描述判断工具用途，过程可审计。 |
| 公共工具名 | `server_name__remote_name` | 保证多 server 同名工具可共存，且工具来源对模型和权限规则稳定可见。 |
| 权限默认值 | 远端工具均为写；`read_tools` 精确白名单降为读 | MCP 未声明读写语义，保守接入既有审批和 plan-only。远端工具不声明可持久化授权参数。 |
| Streamable HTTP | 仅当前规范的 POST JSON/SSE 与可选 GET 事件流 | 支持双向入站消息和会话标识，明确排除已废弃 HTTP+SSE。 |
| 失败策略 | server 级状态隔离，按需锁内重连 | 一个 server 的错误不影响其他工具；重连不会造成重复并发握手或工具清单竞争。 |
| 测试策略 | 本地受控 stdio/HTTP 模拟 | 覆盖协议边界且不依赖外网、真实凭据或真实 MCP server。 |
