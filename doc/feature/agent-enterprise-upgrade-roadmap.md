# myCode Agent 升级与企业级分布式改造路线图

> 面向：开发团队、架构实现和测试维护人员
> 基线：当前仓库截至 2026-08-20 的实现和 `stage-01` 至 `stage-19` 文档
> 文档目标：记录当前 agent 仍需补齐的工程能力，并给出从单机开发助手演进到企业级分布式 Agent 平台的可执行路线。

## 1. 结论先行

当前 myCode 已经具备一个功能完整的本地优先 Agent 原型：有异步 Agent Loop、流式模型协议、工具执行、权限和审批、MCP、上下文压缩、项目记忆、Skill、Hook、子 Agent、Worktree，以及 Lead/member Team 的事件驱动消费链路。

但当前系统的可靠边界仍然是“单机、单用户、本地文件、进程内运行”。核心状态主要落在 `~/.mycode` 下的 JSON/JSONL 文件，Team 的完整事件消费目前以 `in_process` 为主，CLI/TUI 直接组装运行时，也没有面向多租户的身份、配额、审计、服务 API 和统一运维面板。因此，当前最重要的工作不是继续堆叠 Agent 功能，而是先建立可靠的运行时边界，再把状态、事件和执行能力拆成可以水平扩展的服务。

企业级目标不是简单地把 CLI 放进容器，而是形成以下结构：

```text
用户 / CLI / IDE
        |
        v
API Gateway + 身份认证 + 租户配额
        |
        v
控制面：Run API / 编排器 / 策略 / 审计 / 调度
        |
        +--------------------+
        |                    |
        v                    v
持久化状态与事件账本      Outbox -> 事件总线 -> Inbox/Consumer
        |                    |
        +---------+----------+
                  v
数据面：Agent Worker / Team Worker / Tool Sandbox Worker
                  |
                  v
       模型供应商、MCP、代码仓库、对象存储
```

推荐的演进原则是：先把本地接口抽象成可替换的端口，再引入共享数据库和可靠消息投递，最后才扩展多副本和跨节点调度。所有阶段都必须保留本地开发模式，不能为了分布式部署破坏开发者的快速反馈。

## 2. 当前能力盘点

| 能力域 | 当前实现 | 当前成熟度 | 主要限制 |
| --- | --- | --- | --- |
| Agent 执行 | `src/mycode/agent/loop.py` 负责轮次、流式事件、工具调用和终止语义 | 可用 | 生命周期与进程绑定，长任务恢复和跨节点接管不足 |
| 模型协议 | `src/mycode/protocols/` 支持 OpenAI Chat、OpenAI Responses、Anthropic | 可用 | 缺少供应商路由、熔断、配额、成本和版本治理 |
| 工具系统 | `src/mycode/tool/` 提供注册、调度、执行和文件/命令工具 | 可用 | 命令执行仍需更强的隔离、资源限制和租户级审计 |
| 权限与 Hook | `src/mycode/permission/`、`src/mycode/hook/` 提供规则、审批和生命周期动作 | 可用 | 主要是本地配置，缺少中心化策略、身份上下文和策略版本管理 |
| MCP | `src/mycode/mcp/` 支持 stdio 和 Streamable HTTP | 可用 | 连接生命周期、凭据轮换、租户隔离和远端服务治理不足 |
| 上下文与记忆 | `src/mycode/compact/`、`src/mycode/memory/` 使用本地归档、JSONL 和 Markdown | 可用 | 不适合多副本并发、跨用户共享和大规模检索 |
| Sub Agent | `src/mycode/subagent/` 支持 shared/isolated 等模式 | 可用 | 任务编排、取消、预算和子任务恢复还没有统一工作流模型 |
| Agent Team | `src/mycode/team/` 已有 Lead/member、事件存储、消费者和唤醒链 | 进程内可用 | `TeamStore` 和事件日志是本地文件，分布式后无法安全共享 |
| 会话与 TUI | `src/mycode/session.py`、`src/mycode/tui.py`、`src/mycode/cli.py` | 本地可用 | CLI 直接拥有运行时，不具备服务端会话和多终端接入能力 |
| 日志 | `src/mycode/dev_logging.py`、`log_context.py` | 开发可用 | 缺少 trace、metric、审计事件和集中采集规范 |
| 测试 | `tests/` 覆盖单元、集成和部分 E2E | 较好 | 缺少多节点、消息重复、宕机恢复、网络分区、负载和安全测试 |

## 3. 当前必须升级的问题

优先级定义：

- **P0**：不解决就不应进入生产或分布式改造主线。
- **P1**：单机可用但会限制企业化扩展，应在第一轮平台化改造中完成。
- **P2**：影响长期可维护性、成本或开发体验，可在核心运行时稳定后推进。

### 3.1 P0：运行状态与事件状态没有服务化边界

当前 `TeamStore` 使用 `~/.mycode/teams/<team>` 下的 JSON、JSONL 和文件锁，`TeamEventStore` 也直接读写本地事件日志与 cursor。这个设计对单进程、单工作区很清晰，但多个 worker 节点不能把本地文件当成共享真相：

- 节点 A 写入的事件，节点 B 不一定能看到。
- 文件锁只能保护同一台机器上的进程，不能解决跨节点竞争。
- 节点故障时无法可靠判断租约是否过期、事件是否正在处理。
- 事件日志、状态快照和通知没有统一事务边界。

**改造要求：** 抽象 `TeamRepository`、`EventRepository`、`LeaseRepository` 和 `ArtifactRepository` 接口；先提供 file-backed adapter 保持现有测试，再增加 PostgreSQL adapter。事件写入和待投递消息必须通过 outbox 保持原子关系，不能继续让“写文件”和“发通知”分别成功或失败。

### 3.2 P0：Agent Loop 仍然是进程内长调用

`AgentLoop` 的上下文、轮次、工具结果和取消状态主要在当前 Python 进程内。进程被杀死、容器重启或节点摘除后，无法按统一的 run 状态机恢复到上一个安全检查点。

**改造要求：** 把一次用户请求建模为可持久化的 `AgentRun`，把模型轮次、工具调用、审批等待、重试、暂停和完成写成显式状态；运行时只保留短期内存缓存，所有可恢复信息写入状态存储或对象存储。执行 worker 必须支持 `claim -> heartbeat -> checkpoint -> ack/release`。

### 3.3 P0：没有企业级身份、租户和授权上下文

当前配置包含 `api_key`、工作区和本地策略，但不存在统一的用户身份、组织/租户、项目、角色、服务账号和跨请求授权上下文。单机权限规则不能直接承担企业环境的责任。

**改造要求：** 引入 `TenantContext`，至少包含 `tenant_id`、`subject_id`、`roles`、`project_id`、`request_id` 和策略版本；所有存储键、事件、日志、工具调用和模型调用都必须携带租户与请求边界。授权采用 RBAC + ABAC 组合，默认拒绝；本地配置作为开发模式适配器，服务模式走中心化策略服务。

### 3.4 P0：工具执行的安全边界不足以直接承载企业代码

权限和 Hook 可以阻止部分调用，但命令工具、文件工具、MCP 工具仍需要在独立运行环境中执行。仅靠 Python 进程内拦截，无法防止恶意依赖、无限资源消耗、工作区逃逸或工具供应商返回的恶意内容。

**改造要求：** 将高风险工具迁移到独立 sandbox worker，提供 CPU、内存、磁盘、网络、进程数和执行时长限制；使用只读基础镜像、短期凭据和一次性工作区；工具结果经过大小限制、敏感信息脱敏和内容安全检查后才回到模型上下文。

### 3.5 P0：缺少可观测性、审计和成本控制

当前日志主要服务本地调试，无法回答企业运行中的关键问题：一次请求经过了哪些模型和工具、谁批准了高风险操作、哪个租户消耗了多少 token、消息为什么重试、哪个节点发生了故障。

**改造要求：** 统一使用 OpenTelemetry 语义贯穿 API、Agent run、模型请求、工具调用、Team event 和审批；日志、指标、trace、审计记录和成本记录分开定义，禁止把完整 prompt、密钥或工具敏感参数直接写入普通日志。

### 3.6 P1：模型与供应商治理不足

当前 `LLMConfig` 以单个 protocol、model、base_url 和 api_key 为中心，适合开发配置，不适合企业路由。

需要补齐：

- 模型注册表、能力标签和版本锁定。
- 按租户、项目和任务类型的路由策略。
- 超时、指数退避、熔断、限流和备用模型。
- token、请求数、延迟和失败率统计。
- 供应商凭据由 Secret Manager 管理并支持轮换。
- 供应商返回错误统一映射为稳定的内部错误码。

### 3.7 P1：上下文和记忆不能直接水平扩展

当前归档和长期记忆是本地文件、Markdown 索引和 JSONL 会话。多个副本同时更新时会出现竞争、索引不一致和恢复顺序不确定。

需要将“会话事实”和“检索索引”分开：会话事件进入数据库或对象存储，记忆条目有版本和来源，索引采用异步构建；向量检索是可选增强，不能替代可审计的原始记录。必须保留删除、保留期和租户隔离能力。

### 3.8 P1：Team、Sub Agent 和普通 Agent 缺少统一任务模型

Team 已经有事件、角色队列、确认和重试，但 Sub Agent、普通 Agent run 和工具任务仍然存在不同的生命周期表达。继续增加角色类型会放大恢复、取消、预算和审计的分叉。

建议统一为 `Run -> WorkItem -> Attempt -> Event` 四层模型：Run 表示用户可见目标，WorkItem 表示可调度单元，Attempt 表示一次执行租约，Event 表示可恢复事实或通知。

### 3.9 P1：测试覆盖没有进入分布式故障语义

现有测试已经覆盖大量本地行为，但还需要固定以下契约：消息至少一次投递、重复消费幂等、消费者宕机重平衡、租约过期、数据库提交成功但发布失败、发布成功但 ACK 丢失、节点网络分区和升级期间 schema 兼容。

### 3.10 P2：开发体验和扩展机制需要稳定契约

需要补齐插件/Skill/MCP 的版本、依赖、能力声明和兼容性检查；CLI、TUI、IDE 和服务 API 应共享同一套事件模型，避免客户端各自解析供应商协议或内部状态。

## 4. 企业级目标架构

### 4.1 控制面与数据面分离

**控制面**负责身份、租户、项目、策略、模型目录、任务创建、调度、配额、审计和运维接口。控制面不直接执行用户命令，不持有长时间运行的模型流。

**数据面**负责 Agent worker、Team worker、Tool sandbox、MCP connector 和模型调用。数据面节点可以水平扩展、滚动升级和按队列伸缩，不能依赖某个节点的本地磁盘保存唯一状态。

**客户端层**包括现有 CLI/TUI、未来的 IDE 插件和 Web API 客户端。客户端只依赖稳定的 Run/Event API，不依赖内部 Python 类。

### 4.2 推荐的基础设施分层

| 领域 | 第一版推荐 | 适用职责 | 备注 |
| --- | --- | --- | --- |
| 关系数据库 | PostgreSQL | 租户、项目、Run、WorkItem、状态、策略版本、审计索引 | 事务真相和查询入口 |
| 事件总线 | NATS JetStream 或 Kafka | 可靠通知、消费组、重放、跨节点唤醒 | 初期低延迟可用 NATS；高吞吐审计流再考虑 Kafka |
| 缓存/租约 | Redis | 短期缓存、限流、租约辅助、幂等窗口 | 不作为唯一业务真相 |
| 对象存储 | S3/MinIO | 大型 prompt、工具结果、归档、构建产物 | 数据库只存元数据和校验值 |
| 密钥 | Vault/KMS/云 Secret Manager | 模型、MCP、仓库和数据库凭据 | 不再把长期密钥写入 YAML 或数据库明文 |
| 可观测性 | OpenTelemetry + Prometheus/Grafana + Loki/Tempo | trace、指标、日志和告警 | 所有服务使用统一 correlation id |
| 工作流 | 自研状态机起步，复杂后评估 Temporal | 长任务、重试、暂停、恢复、人工审批 | 先建立领域接口，避免一开始锁死具体产品 |
| 部署 | Docker + Kubernetes | 多副本、滚动发布、资源隔离、自动伸缩 | 本地仍保留单进程开发入口 |

这里不建议把 Redis、Kafka 或某个工作流产品直接散落到业务代码中。业务代码依赖端口接口，基础设施通过 adapter 接入；这样本地测试可以使用内存实现，生产环境再切换到真实组件。

### 4.3 核心数据模型

| 实体 | 必要字段 | 关键约束 |
| --- | --- | --- |
| `Tenant` | `tenant_id`, `status`, `quota_policy_version` | 所有业务数据必须带租户键 |
| `Project` | `project_id`, `tenant_id`, `repo_ref`, `policy_ref` | 项目不能跨租户引用 |
| `AgentRun` | `run_id`, `tenant_id`, `project_id`, `status`, `idempotency_key`, `version` | 状态迁移必须有版本条件 |
| `WorkItem` | `work_item_id`, `run_id`, `kind`, `priority`, `status`, `attempt` | 调度和执行解耦 |
| `Attempt` | `attempt_id`, `worker_id`, `lease_until`, `heartbeat_at` | 租约过期可重新领取 |
| `Event` | `event_id`, `aggregate_id`, `sequence`, `type`, `payload_ref`, `schema_version` | 追加事实，不覆盖原始事件 |
| `OutboxMessage` | `outbox_id`, `event_id`, `topic`, `published_at`, `attempts` | 状态提交与待发布消息同事务 |
| `InboxRecord` | `consumer`, `message_id`, `processed_at` | 消费幂等去重 |
| `Artifact` | `artifact_id`, `tenant_id`, `uri`, `sha256`, `retention_until` | 大对象外置，内容可校验 |
| `PolicyDecision` | `decision_id`, `subject`, `action`, `resource`, `reason`, `policy_version` | 可审计、可复盘 |
| `AuditRecord` | `actor`, `action`, `resource`, `result`, `trace_id`, `created_at` | 与普通调试日志分离 |

### 4.4 分布式消息语义

企业级版本必须明确采用 **at-least-once** 投递，而不是对外承诺无法证明的 exactly-once。正确性依赖以下组合：

1. 生产者为每次外部请求生成幂等键，重复提交返回同一个 Run。
2. 状态变更和 Outbox 记录在一个数据库事务中提交。
3. 发布者可重复发布，消费者通过 Inbox 或业务版本条件幂等处理。
4. 消费者先获得租约，再处理模型或工具调用；处理完成后提交结果并 ACK。
5. ACK 丢失只会导致重复投递，不能导致重复业务副作用；有副作用的工具必须支持幂等键或补偿动作。
6. 重试采用指数退避和抖动，超过上限进入 dead-letter 队列并保留结构化失败记录。
7. 同一 Run 或 Team role 的顺序由分区键保证；不要求全局顺序。

## 5. 分阶段改造路线

### Phase 0：基线收敛与契约冻结（P0）

**目标：** 把当前单机行为变成可测试、可替换的稳定契约。

**主要工作：**

- 固化 Run、WorkItem、Event、ToolCall、Approval、Artifact 的状态枚举和错误码。
- 为 Team store、事件通知、模型客户端、工具执行器、记忆存储定义端口接口。
- 在 `AgentLoop`、Team consumer 和 Sub Agent runtime 中统一 request/trace/run 上下文。
- 为所有外部副作用增加幂等键和结构化结果。
- 清理“成功写入但通知失败”“通知成功但状态未提交”的隐式路径。
- 维持 file-backed adapter，新增 contract tests，确保后续 PostgreSQL adapter 可替换。

**代码落点：** `agent/loop.py`、`team/events.py`、`team/storage.py`、`team/consumer.py`、`subagent/runtime.py`、`tool/executor.py`、`config.py`。

**完成标准：** 本地模式全量测试通过；同一请求重复提交、重复事件和重复工具回调不会造成重复业务结果；所有核心事件带稳定 schema version。

### Phase 1：持久化与事务边界（P0）

**目标：** 让状态从“本地文件实现”升级为“可替换的持久化实现”。

**主要工作：**

- 建立 PostgreSQL schema、迁移工具和 repository adapter。
- 将 Team JSON/JSONL 映射到关系表和对象存储；保留导入脚本和只读回滚路径。
- 引入 outbox/inbox 表和发布 worker。
- 增加版本条件更新、租约、心跳、过期接管和 dead-letter 记录。
- 所有迁移支持 expand/contract：先加字段和双写，再切读路径，最后清理旧字段。

**代码落点：** 新增 `src/mycode/persistence/`、`src/mycode/events/`、`migrations/`；重构 `team/storage.py`、`team/events.py`。

**完成标准：** 任意一个 worker 在提交后立即退出，另一个 worker 能继续处理未完成 WorkItem；数据库提交成功但发布失败时，outbox worker 能补发；重复发布不产生重复状态变更。

### Phase 2：服务化 Agent Runtime（P0/P1）

**目标：** 将 CLI 直接拥有的 AgentLoop 拆为服务端 Run API 和无状态 worker。

**主要工作：**

- 增加 `POST /runs`、`GET /runs/{id}`、事件流订阅、取消、暂停、恢复和审批接口。
- CLI/TUI 改成 API client，保留 offline/local adapter。
- 把模型轮次、工具调用、审批等待和上下文 checkpoint 写入持久化状态。
- 引入 worker claim、heartbeat、lease expiry 和 graceful drain。
- 对模型调用增加超时、退避、熔断、备用模型和供应商错误映射。

**代码落点：** 新增 `src/mycode/api/`、`src/mycode/runtime/`、`src/mycode/scheduler/`；拆分 `agent/loop.py` 的状态机、模型调用和事件输出职责；改造 `cli.py`、`session.py`、`tui.py`。

**完成标准：** API worker 重启后 Run 可恢复；客户端断线重连可从事件 cursor 继续；取消请求具备幂等性；一个租户的故障不会阻塞其他租户的队列。

### Phase 3：分布式 Team 与工具执行（P1）

**目标：** 让 Team、Sub Agent 和 Tool worker 可以跨节点调度。

**主要工作：**

- 用事件总线替换进程内 notifier，角色队列映射为消费组和分区键。
- 将 `in_process`、`tmux`、`terminal` 后端统一到 `WorkerBackend`，明确能力和故障语义。
- 将命令、文件和高风险 MCP 调用迁移到 sandbox worker。
- 增加资源池、优先级、并发上限、租户配额和 backpressure。
- 为 Team 广播、member-to-lead、审批响应和失败上报定义消息 schema 兼容策略。

**代码落点：** `team/backends.py`、`team/notifier.py`、`team/consumer.py`、`team/worker.py`、`team/runtime.py`、`member_tools.py`、`lead_tools.py`、`mcp/`、`tool/`。

**完成标准：** Lead 和 member 可在不同节点运行；节点宕机后未 ACK 事件可被其他节点接管；工具执行超时或 worker 被杀不会让 Run 永久卡住；所有副作用工具都有幂等或补偿策略。

### Phase 4：企业安全、治理和多租户（P1）

**目标：** 满足企业环境的身份、数据隔离、审计和合规要求。

**主要工作：**

- 接入 OIDC/OAuth2，建立用户、服务账号、组织和项目层级。
- 引入 RBAC + ABAC 策略，支持工具、仓库、MCP、模型和网络资源的最小权限。
- 接入 Secret Manager，支持密钥轮换、短期 token 和租户级凭据。
- 对 prompt、工具参数、文件内容和模型输出执行敏感信息脱敏与审计策略。
- 增加数据保留、删除、导出和租户隔离验证。
- 建立高风险操作的人工审批、双人复核和不可抵赖审计。

**完成标准：** 跨租户读取在 API、数据库、对象存储、事件和日志层全部被拒绝；权限变更可追踪；密钥不出现在普通日志、事件 payload 或错误信息中。

### Phase 5：高可用、弹性和成本治理（P1/P2）

**目标：** 在可观测和可审计的前提下扩大规模并控制成本。

**主要工作：**

- 多可用区部署数据库、事件总线、API 和 worker；建立备份、恢复和灾备演练。
- 根据队列深度、等待时间、token 预算和工具资源使用自动伸缩。
- 增加租户级预算、模型降级、上下文预算和并发配额。
- 建立 SLO、错误预算、容量模型和压测基线。
- 支持灰度发布、feature flag、schema 兼容和自动回滚。

**完成标准：** 在单节点、单消费者和单供应商故障下，服务能自动恢复或明确失败；延迟、成功率、成本和资源使用都有可查询的租户维度数据。

## 6. 模块改造映射

| 当前模块 | 近期改造 | 企业级归属 |
| --- | --- | --- |
| `config.py` | 拆分本地配置、服务配置、租户配置和 Secret 引用 | Config service / Secret adapter |
| `agent/loop.py` | 拆出状态机、checkpoint、模型调用和事件发布 | Agent Runtime Worker |
| `session.py` | 从本地会话管理改为 Run client + event cursor | API client / Session Gateway |
| `protocols/` | 增加统一 provider adapter、路由、重试和成本回调 | Model Gateway |
| `tool/` | 工具描述、授权、执行和结果清洗分层 | Tool Registry + Sandbox Worker |
| `permission/` | 本地规则适配器保留，新增中心化策略接口 | Policy Service |
| `hook/` | 明确同步拦截与异步审计边界 | Policy/Event Automation |
| `mcp/` | 连接池、凭据、租户和能力版本纳入运行上下文 | MCP Connector Service |
| `compact/` | 归档对象化、异步摘要、成本和失败指标 | Context Service |
| `memory/` | 原始事实、索引和检索分离 | Memory Service / Search Index |
| `subagent/` | 映射到 WorkItem 和父子 Run | Scheduler / Workflow Runtime |
| `team/storage.py` | repository 接口、DB adapter、迁移工具 | State Store |
| `team/events.py` | outbox、inbox、版本化事件、重放 | Event Ledger / Event Bus |
| `team/notifier.py` | 从本地唤醒器变为 bus publisher | Event Bus Adapter |
| `team/consumer.py` | lease、heartbeat、幂等、死信和重平衡 | Distributed Consumer |
| `team/backends.py` | 统一 worker 能力和后端健康状态 | Worker Pool |
| `dev_logging.py`、`log_context.py` | OTel trace/log/metric/audit 适配 | Observability Platform |
| `cli.py`、`tui.py` | 默认连接 Run API，保留 local mode | Client Layer |

## 7. 分布式一致性与失败处理细则

### 7.1 发送顺序

业务状态变更、事件账本和 outbox 必须在同一数据库事务中完成。事务提交后由发布 worker 把 outbox 投递到事件总线；任何发送方都不能先通知后写入事实记录。

### 7.2 消费顺序

消费者按 `tenant_id + aggregate_id` 或 `team_id + role_name` 分区。领取消息后写入 Attempt 和租约，处理期间持续 heartbeat。租约过期后，其他消费者可接管，但必须重新检查状态版本，避免旧 worker 覆盖新结果。

### 7.3 重试与死信

重试分为三层：网络/供应商短重试、WorkItem 级重试、业务事件终态失败。每层都有独立次数、退避和错误分类。不可重试错误（权限拒绝、参数不合法、目标不存在）应立即进入结构化失败，不得无意义地重复调用外部工具。

### 7.4 人工审批与恢复

审批等待必须是持久化状态，不能只依赖 TUI 内存队列。审批响应携带 `approval_id` 和幂等键，重复响应只返回已有结果。服务重启后，待审批 Run 重新进入等待状态，不自动绕过审批。

### 7.5 外部副作用

写仓库、发送消息、创建云资源等副作用必须声明幂等能力。对无法幂等的操作使用补偿记录、人工确认或一次性执行令牌；不要用“模型没有再次调用”作为副作用不重复的保证。

## 8. 安全与治理基线

- **身份：** 所有 API、事件和 worker 调用都必须可验证主体；服务间使用 mTLS 或短期 service token。
- **租户隔离：** 数据库行级条件、对象存储前缀、事件分区、缓存键、日志和 trace 都带 `tenant_id`。
- **最小权限：** 工具权限按主体、项目、资源、动作和环境判断；默认拒绝，审批不能绕过系统级禁止规则。
- **执行隔离：** 高风险命令在临时 sandbox 中运行，禁止复用宿主机凭据和长期工作区。
- **秘密保护：** API key、MCP header、仓库 token 只从 Secret Manager 读取；日志和错误信息使用统一 redact 函数。
- **提示注入防护：** 工具结果、外部文档和仓库内容标记为不可信输入，不能覆盖系统策略或授权边界。
- **审计：** 记录谁在什么时间对哪个资源执行了什么动作、使用了什么策略版本以及结果；审计数据不可被普通业务路径修改。
- **数据治理：** 定义 prompt、工具结果、记忆、artifact 和审计记录的保留期、删除、导出和加密策略。

## 9. 可观测性与 SLO 建议

### 9.1 必须采集的指标

| 指标 | 维度 | 用途 |
| --- | --- | --- |
| Run 成功率/失败率 | tenant、project、model、error_code | 判断业务可用性 |
| 首 token 延迟、完整响应延迟 | model、region、worker_pool | 体验和供应商比较 |
| 工具调用延迟、超时、拒绝率 | tool、policy_version、tenant | 发现工具瓶颈和误拒绝 |
| 队列等待时间、租约过期次数 | topic、role、worker_pool | 判断调度和容量 |
| 事件重试、死信、重复消费 | event_type、consumer | 判断可靠性问题 |
| 输入/输出 token、估算成本 | tenant、project、model、run | 配额和成本治理 |
| 上下文压缩次数、失败率 | run、model、context_size | 发现长会话问题 |
| sandbox CPU、内存、磁盘、网络 | tenant、tool、worker | 资源隔离和计费 |

### 9.2 Trace 边界

一次请求至少形成以下 span 链：`api.request -> run.execute -> model.request -> tool.invoke -> event.publish/consume -> artifact.store`。每个 span 只记录安全摘要和引用，不记录完整秘密或未经脱敏的仓库内容。

### 9.3 SLO 起点

具体数值应通过压测校准，初始可以先建立目标：API 可用性、事件投递成功率、Run 恢复成功率、P95 首 token 延迟、工具超时率和死信率都必须有仪表板、告警阈值和负责人。没有负责人和响应手册的指标不算完成。

## 10. 测试与验收策略

### 10.1 单元和契约测试

- repository 在 file-backed、memory-backed、PostgreSQL adapter 上执行同一套 contract tests。
- Event schema 采用向后兼容测试，旧消费者可以读取新事件中的可选字段。
- Model provider、Tool executor、Policy adapter 和 Event bus adapter 都有 fake、故障 fake 和超时 fake。

### 10.2 集成测试

- 数据库事务提交、outbox 发布、inbox 幂等和重试。
- 消费者宕机、租约过期、重复消息、乱序消息和重平衡。
- API 断线重连、Run 取消、审批恢复和跨节点接管。
- Team Lead/member 跨进程、跨节点通信。
- sandbox 资源限制、网络策略、凭据注入和结果脱敏。

### 10.3 E2E 和混沌测试

至少固化以下场景：

1. 用户创建 Run，模型调用工具，工具返回结果，Run 完成，客户端从事件 cursor 收到完整结果。
2. worker 在模型调用后、ACK 前退出，另一节点接管，业务结果只落一次。
3. 数据库提交成功但消息发布失败，outbox 自动补发。
4. 事件发布成功但客户端断线，重连后不重复展示已确认事件。
5. Lead 与 member 位于不同节点，消息、审批、失败上报和恢复都可完成。
6. 一个租户超额或故障时，其他租户仍可运行。
7. 恶意工具结果包含提示注入和敏感信息时，策略阻止越权并产生审计记录。

### 10.4 负载和安全测试

- 逐步增加并发 Run、长上下文、工具结果大小和 Team member 数量，记录容量拐点。
- 测试数据库连接池、事件分区、对象存储、模型供应商限流和 sandbox 池的背压。
- 执行依赖漏洞扫描、镜像扫描、SAST、秘密扫描、权限绕过和租户越权测试。

## 11. 交付、部署和迁移策略

### 11.1 环境分层

- **local：** 单进程、file-backed、fake provider，可离线运行。
- **dev：** Docker Compose，PostgreSQL、Redis、NATS/MinIO 和本地 OTel。
- **staging：** Kubernetes 多副本、真实模型测试租户、故障注入和迁移演练。
- **production：** 多可用区、独立租户策略、Secret Manager、备份和灾备。

### 11.2 迁移顺序

1. 先建立新表和 adapter，不改变现有本地默认行为。
2. 提供 `~/.mycode` 到数据库/对象存储的显式导入命令，导入前生成校验报告。
3. 在灰度租户启用双写或旁路校验，比较事件数量、状态和摘要哈希。
4. 切换读取路径，保留旧文件只读一段时间。
5. 验证恢复、回滚和审计后，再停止旧写入。

### 11.3 发布门禁

- 数据库 migration 必须可前向/后向兼容，且在 staging 完成回滚演练。
- 新 worker 必须支持旧事件 schema，旧 worker 不得误处理未知强制字段。
- feature flag 支持按租户、项目和百分比灰度。
- 每次发布包含变更说明、指标看板、回滚命令和责任人。

## 12. 开发团队可直接拆分的任务

### P0-1：稳定核心领域模型

定义 Run、WorkItem、Attempt、Event、Artifact、PolicyDecision、AuditRecord 的 schema、状态迁移和错误码；补齐状态机非法迁移测试。

### P0-2：抽象存储与事件端口

把 `TeamStore`、`TeamEventStore` 和本地 notifier 的直接依赖替换为接口；保留现有 file-backed 实现，新增内存 fake 和 contract tests。

### P0-3：引入幂等和可靠投递

实现 idempotency key、outbox、inbox、lease、heartbeat、dead-letter 和结构化失败；覆盖“提交/发布/ACK”三类故障。

### P0-4：拆分 AgentLoop 状态机

将模型调用、上下文准备、工具调度、审批等待和事件输出分成可独立测试的组件；每个检查点都有持久化表示。

### P0-5：统一上下文和观测

建立 request/run/tenant/trace context，所有日志、事件和 provider 请求使用同一 correlation id；敏感字段走统一脱敏。

### P1-1：PostgreSQL adapter 和迁移工具

完成 schema、migration、repository、outbox publisher、导入命令和双读校验。

### P1-2：Run API 与无状态 worker

提供创建、查询、事件流、取消、暂停、恢复和审批 API；CLI/TUI 通过 API client 运行。

### P1-3：分布式 Team consumer

用事件总线消费组替换进程内唤醒；实现跨节点租约、重平衡和 Team role 分区。

### P1-4：Tool sandbox

把命令、文件写入和高风险 MCP 调用移入受限 worker；补齐资源限制、网络策略和结果脱敏。

### P1-5：身份、策略和审计

接入 OIDC、租户/项目模型、RBAC/ABAC、中心化策略、Secret Manager 和不可变审计。

### P2-1：模型网关与成本治理

实现模型目录、路由、fallback、熔断、限流、token 计量和预算策略。

### P2-2：弹性与运维

完成 Kubernetes 部署、自动伸缩、备份恢复、混沌测试、灰度发布和 SLO 告警。

## 13. 不建议现在做的事情

- 在没有 Run/WorkItem/Attempt 统一模型之前继续增加更多 Team 角色或复杂调度策略。
- 在没有可靠事件账本和幂等语义之前直接引入跨节点并发。
- 把 Redis 当作永久状态库，把事件总线当作唯一业务真相。
- 直接把完整 prompt、工具结果和密钥写入日志或消息队列。
- 为了“企业级”一次性引入大量基础设施，却不提供本地 adapter、contract tests 和迁移回滚。
- 把向量数据库当作记忆系统的唯一来源；原始事实必须可审计、可恢复。
- 对外承诺 exactly-once，而没有证明外部副作用的幂等性。

## 14. 企业级完成定义

只有同时满足以下条件，才可以称为“企业级基础版本”完成：

- Run、WorkItem、Attempt、Event 和 Artifact 有稳定版本化 schema 和状态机。
- PostgreSQL/对象存储保存业务真相，事件总线负责可靠分发，outbox/inbox 和幂等策略已验证。
- Agent、Team、Sub Agent 和工具都能在多节点 worker 上恢复、重试、取消和审计。
- API、CLI/TUI、IDE 使用统一 Run/Event 契约，客户端断线可恢复。
- 身份、租户、项目、RBAC/ABAC、Secret Manager、审计和数据保留策略已落地。
- 高风险工具运行在受限 sandbox，具备资源、网络、凭据和结果安全边界。
- 有完整的 trace、指标、日志、成本、告警、SLO 和故障响应手册。
- 通过多节点 E2E、故障注入、负载、安全、迁移和灾备演练。
- 支持本地开发模式与生产分布式模式并存，二者共享领域契约和测试套件。

## 15. 最终建议

下一轮开发应从 **P0-1 到 P0-5** 开始，先冻结领域模型、抽象存储与事件端口、补齐可靠投递和可观测性，再推进 PostgreSQL 和 Run API。分布式架构的关键不是“把进程复制多份”，而是让每一次状态变化、事件投递、工具副作用和恢复动作都有明确的所有权、幂等键、租约和审计记录。

在此基础上，Phase 1 和 Phase 2 可以逐步把当前本地 Agent 变成可恢复的服务；Phase 3 再把 Team 和工具执行扩展到多节点；Phase 4、Phase 5 负责企业安全、合规、弹性和成本治理。这样既保留当前项目的开发效率，也为后续多租户、跨区域和大规模任务调度留下清晰边界。
