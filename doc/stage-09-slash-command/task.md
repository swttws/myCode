# Stage 09 斜杠命令注册与分发 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `src/mycode/slash/__init__.py` | 稳定导出和默认注册中心创建入口 |
| 新建 | `src/mycode/slash/models.py` | 命令、解析、分发、模式和应用状态模型 |
| 新建 | `src/mycode/slash/controller.py` | 与渲染框架无关的界面控制协议 |
| 新建 | `src/mycode/slash/parser.py` | 空输入、普通输入和斜杠输入解析 |
| 新建 | `src/mycode/slash/registry.py` | 原子注册、冲突检测、查找和补全候选 |
| 新建 | `src/mycode/slash/dispatcher.py` | 命令路由、未知命令和异常隔离 |
| 新建 | `src/mycode/slash/builtins.py` | 十个公开命令、隐藏退出和固定审查提示词 |
| 新建 | `src/mycode/slash/status.py` | Git/MCP 状态采集、故障隔离和格式化 |
| 新建 | `src/mycode/slash/completion.py` | `prompt_toolkit` 补全适配器 |
| 修改 | `src/mycode/compact/models.py` | 增加当前上下文 Token 状态快照 |
| 修改 | `src/mycode/compact/manager.py` | 增加无副作用的当前请求估算 |
| 修改 | `src/mycode/compact/__init__.py` | 导出 Token 状态快照 |
| 修改 | `src/mycode/memory/models.py` | 增加会话来源、会话和记忆状态快照 |
| 修改 | `src/mycode/memory/sessions.py` | 增加当前写入会话摘要查询 |
| 修改 | `src/mycode/memory/manager.py` | 跟踪恢复来源并生成脱敏状态快照 |
| 修改 | `src/mycode/memory/__init__.py` | 导出会话和记忆状态快照 |
| 修改 | `src/mycode/agent/loop.py` | 构造 Token 快照并保存最近框架上下文 |
| 修改 | `src/mycode/session.py` | 转发 Token、会话和记忆状态 |
| 修改 | `src/mycode/tui.py` | 实现控制协议、输入分流、补全和模式栏 |
| 修改 | `src/mycode/cli.py` | 启动期创建注册中心、处理冲突并注入 TUI |
| 新建 | `tests/test_slash_registry.py` | 命令模型、注册冲突、查找和候选测试 |
| 新建 | `tests/test_slash_parser.py` | 输入分类、大小写和参数切分测试 |
| 新建 | `tests/test_slash_dispatcher.py` | 四种分发结果、未知命令和异常测试 |
| 新建 | `tests/test_slash_builtins.py` | 内置元数据、参数和处理行为测试 |
| 新建 | `tests/test_slash_status.py` | 状态模型、Git、MCP 和综合格式测试 |
| 新建 | `tests/test_slash_completion.py` | 单候选、多候选、隐藏和参数位置测试 |
| 新建 | `tests/test_slash_snapshots.py` | Token、会话和记忆领域快照测试 |
| 新建 | `tests/test_slash_tui.py` | TUI 控制接口、输入分流、状态栏和降级测试 |
| 新建 | `tests/test_slash_cli.py` | 注册中心装配和冲突启动失败测试 |
| 新建 | `tests/test_slash_e2e.py` | 完整命令链和审查提示词端到端测试 |
| 修改 | `README.md` | 更新公开命令、别名、模式栏和行为边界 |

## T1：定义斜杠命令核心模型

**文件：** `src/mycode/slash/models.py`、`src/mycode/slash/__init__.py`、`tests/test_slash_registry.py`  
**依赖：** 无

**步骤：**
1. 编写测试固定 `SlashCommandType`、`SlashInputKind`、`SlashHandlerSignal`、`SlashDispatchKind` 和 `SlashMode` 的 plan.md 枚举值。
2. 编写测试断言 `ParsedSlashInput`、`SlashDispatchResult`、`SlashCompletionCandidate`、`SlashCommand` 和 `SlashCommandContext` 均为 frozen dataclass，并核对每个字段及默认值。
3. 运行目标测试，确认 `mycode.slash` 尚不存在而失败。
4. 创建 `slash.models`，按 plan.md 定义模型和 `SlashCommandHandler` 类型别名；为字段保留已确认的简短中文注释。
5. 在 `slash.__init__` 只导出当前已经存在的模型，避免提前导入未创建模块。

**验证：** `python -m pytest tests/test_slash_registry.py -q`，期望核心模型枚举、字段和不可变性测试通过。

## T2：定义应用状态模型与控制协议

**文件：** `src/mycode/slash/models.py`、`src/mycode/slash/controller.py`、`tests/test_slash_status.py`  
**依赖：** T1

**步骤：**
1. 编写状态模型测试，固定 `PermissionStatusSnapshot`、`GitStatusSnapshot`、`MCPServerStatus`、`MCPStatusSnapshot`、`StatusSection` 和 `ApplicationStatusSnapshot` 的字段。
2. 断言状态模型不可变，并验证 Python 3.10 的 `Generic[StatusValue]` 可以构造成功与失败两种 `StatusSection`。
3. 编写结构化协议测试，创建不继承任何基类的 fake controller，实现 plan.md 全部方法后断言其满足 `SlashCommandController` 的运行时结构检查。
4. 在 `slash.models` 实现状态模型，在 `TYPE_CHECKING` 分支引用尚未落地的领域快照类型；在 `slash.controller` 定义 `@runtime_checkable` 的 `SlashCommandController(Protocol)`。
5. 更新 `slash.__init__` 导出控制协议和状态模型。

**验证：** `python -m pytest tests/test_slash_status.py -q`，期望状态字段、泛型容器和控制协议测试通过。

## T3：实现注册中心的原子冲突检测

**文件：** `src/mycode/slash/registry.py`、`tests/test_slash_registry.py`  
**依赖：** T1

**步骤：**
1. 编写合法注册测试，登记两条带处理函数的命令，断言构造成功且输入序列未被修改。
2. 编写非法标识测试，覆盖空名称、前导 `/`、名称含空白、空别名和别名含空白；断言抛出 `SlashCommandRegistrationError`。
3. 编写冲突测试，覆盖主名称重复、大小写变体、同命令别名重复、跨命令别名重复、别名占用主名称和隐藏命令撞名。
4. 运行目标测试，确认注册中心尚未实现而失败。
5. 实现临时索引校验；只有全部命令通过后才赋值 `_commands`、`_index`，并用中文注释说明为何禁止部分注册。

**验证：** `python -m pytest tests/test_slash_registry.py -q`，期望所有合法与冲突场景通过，错误文本包含冲突标识和双方主名称。

## T4：实现注册查找、公开顺序和补全候选

**文件：** `src/mycode/slash/registry.py`、`tests/test_slash_registry.py`  
**依赖：** T3

**步骤：**
1. 编写 `resolve()` 测试，覆盖主名称、别名、大小写变体、未知标识和 `include_hidden=False`。
2. 编写 `public_commands()` 测试，断言隐藏命令被排除，其余命令保持登记顺序。
3. 编写补全测试，断言主名称和别名都产生带 `/` 的候选，按大小写不敏感前缀过滤，隐藏命令及隐藏别名被排除。
4. 实现只读查找、公开序列和候选生成；候选 `description` 使用所属命令描述，候选顺序使用命令登记顺序及该命令别名顺序。
5. 更新 `slash.__init__` 导出注册中心和注册错误。

**验证：** `python -m pytest tests/test_slash_registry.py -q`，期望查找、隐藏过滤、稳定顺序和补全候选测试通过。

## T5：解析空输入、普通输入和斜杠输入

**文件：** `src/mycode/slash/parser.py`、`tests/test_slash_parser.py`  
**依赖：** T1

**步骤：**
1. 编写空输入测试，覆盖 `""`、空格、Tab 和换行，断言得到 `EMPTY`。
2. 编写普通输入测试，断言整体首尾空白被去除，内部空白保持，结果为 `NORMAL`。
3. 编写命令测试，覆盖 `/help`、`/HeLp`、`/?` 和前后空白，断言命令名不带 `/` 且已 `casefold()`。
4. 编写参数测试，覆盖一个空格、多个空格、Tab 分隔和参数内部空白，断言只切分一次并正确保留内部内容。
5. 实现 `parse_slash_input()`，使用首个空白边界而非对完整输入调用无限次 `split()`。

**验证：** `python -m pytest tests/test_slash_parser.py -q`，期望输入分类、大小写和参数边界测试通过。

## T6：实现分发器的四种结果

**文件：** `src/mycode/slash/dispatcher.py`、`tests/test_slash_dispatcher.py`  
**依赖：** T3、T5

**步骤：**
1. 创建 self-contained fake controller 和 recording handler，不导入 Rich、TUI 或真实 Agent。
2. 编写空输入与普通输入测试，断言分别返回 `EMPTY` 和携带规范化正文的 `NOT_COMMAND`，处理函数未调用。
3. 编写已知主名称、别名和大小写命令测试，断言参数原样传给处理函数，`CONTINUE` 映射为 `HANDLED`。
4. 编写 `EXIT` handler 测试，断言分发结果为 `EXIT`。
5. 实现 `SlashCommandDispatcher.dispatch()` 和 `SlashCommandContext` 创建逻辑，更新包导出。

**验证：** `python -m pytest tests/test_slash_dispatcher.py -q`，期望四种分发结果和参数转发测试通过。

## T7：隔离未知命令与处理函数异常

**文件：** `src/mycode/slash/dispatcher.py`、`tests/test_slash_dispatcher.py`  
**依赖：** T6

**步骤：**
1. 编写未知命令测试，输入 `/missing value`，断言显示 `/missing` 和 `/help`，返回 `HANDLED`，不调用 `send_user_message()`。
2. 编写 handler 抛异常测试，断言用户只看到稳定的 `slash_command_failed` 和命令主名称，不看到异常消息中的模拟 secret。
3. 使用 `caplog` 断言开发日志包含异常堆栈和主命令名，方便定位真实故障。
4. 在分发器增加未知分支和 `try/except Exception`；用中文注释说明未知斜杠输入不能降级给模型。
5. 再发送一条正常命令，断言前一条异常不破坏注册中心或后续分发。

**验证：** `python -m pytest tests/test_slash_dispatcher.py -q`，期望未知引导、脱敏错误、日志和后续可用性测试通过。

## T8：定义内置命令元数据目录和公共校验

**文件：** `src/mycode/slash/builtins.py`、`src/mycode/slash/__init__.py`、`tests/test_slash_builtins.py`  
**依赖：** T2、T4

**步骤：**
1. 编写元数据目录测试，断言十个公开条目按 `help, compact, clear, plan, do, session, memory, permission, status, review` 顺序定义，隐藏 `exit` 位于末尾。
2. 逐条断言主名称、固定别名、描述、用法、命令类型、参数提示和隐藏状态符合 plan.md 表格；目录本身不保存 handler。
3. 编写共享参数校验测试：无参数命令接收空字符串，非空参数统一显示该命令用法并停止执行动作。
4. 实现不可变的内置元数据目录、固定 `REVIEW_PROMPT` 常量和无参数校验辅助函数；不创建空 handler 或 `NotImplementedError` 占位。
5. 更新 `slash.__init__` 只导出当前已完整实现的元数据与审查提示词。

**验证：** `python -m pytest tests/test_slash_builtins.py -q`，期望元数据顺序、字段、固定别名、隐藏标记和公共参数校验测试通过。

## T9：实现 `/help` 列表与详情

**文件：** `src/mycode/slash/builtins.py`、`tests/test_slash_builtins.py`  
**依赖：** T8

**步骤：**
1. 编写 `/help` 无参数测试，断言十个公开命令各出现一次，并按注册顺序显示名称、描述和用法。
2. 编写详情测试，分别用主名称 `status` 和别名 `stat` 查询，断言显示主名称、别名、描述、用法、类型和参数提示。
3. 编写隐藏与未知测试，`exit`、`quit` 和不存在的名称都只显示未找到与 `/help` 引导，不泄漏隐藏元数据。
4. 实现列表与详情纯文本格式化，帮助查询使用 `resolve(..., include_hidden=False)`。
5. 断言所有输出经 `show_message()`，不调用任何状态查询或 Agent 方法。

**验证：** `python -m pytest tests/test_slash_builtins.py -q`，期望帮助列表、别名详情、隐藏隔离和固定顺序通过。

## T10：实现 `/compact` 与 `/clear`

**文件：** `src/mycode/slash/builtins.py`、`tests/test_slash_builtins.py`  
**依赖：** T8

**步骤：**
1. 编写 `/compact` 无参数测试，断言只调用一次 `compact_context()`；带任意参数时只显示 `/compact` 用法。
2. 编写 `/clear` 无参数测试，断言只调用一次 `clear_session()` 并显示清空和复位结果；带参数时不清空并显示用法。
3. 断言两条命令均不调用 `send_user_message()`。
4. 实现共享的无参数校验辅助函数，避免十个 handler 重复参数判断。
5. 实现两个处理函数并返回 `CONTINUE`。

**验证：** `python -m pytest tests/test_slash_builtins.py -q`，期望压缩、清空、非法参数和零 Agent 调用测试通过。

## T11：实现 `/plan`、`/do` 与隐藏退出

**文件：** `src/mycode/slash/builtins.py`、`tests/test_slash_builtins.py`  
**依赖：** T8

**步骤：**
1. 编写 `/plan` 测试，断言控制器切换到 `SlashMode.PLAN`；已在 PLAN 时不重复写状态但显示确定消息。
2. 编写 `/do` 测试，断言控制器切换到 `SlashMode.DEFAULT`；已在 DEFAULT 时同样幂等。
3. 编写 `/exit` 和 `/quit` 分发测试，断言 handler 返回 `EXIT`，额外参数时显示用法并返回 `CONTINUE`。
4. 实现三个处理函数；模式输出固定包含 `[PLAN]` 或 `[DEFAULT]`。
5. 断言三条命令均不调用 Agent、Token、Git、MCP 或记忆状态方法。

**验证：** `python -m pytest tests/test_slash_builtins.py tests/test_slash_dispatcher.py -q`，期望模式幂等、隐藏退出和参数拒绝通过。

## T12：实现 `/permission` 查询与设置

**文件：** `src/mycode/slash/builtins.py`、`tests/test_slash_builtins.py`  
**依赖：** T8

**步骤：**
1. 编写无参数测试，fake controller 返回 `PermissionMode.STRICT` 和 `RuleSource.LOCAL_PROJECT`，断言输出档位与来源。
2. 参数化测试 `strict`、`default`、`permissive` 及大小写变体，断言设置对应会话档位并显示结果。
3. 测试非法值、两个参数和多余空白后的非法组合，断言显示完整用法且不修改权限。
4. 实现权限参数解析和现有枚举映射；不得引入新的权限值或命令级授权。
5. 断言查询和设置均不调用 `send_user_message()`。

**验证：** `python -m pytest tests/test_slash_builtins.py -q`，期望权限查询、三档设置、非法用法和零模型调用通过。

## T13：实现 `/session` 与 `/memory` 输出

**文件：** `src/mycode/slash/builtins.py`、`tests/test_slash_builtins.py`  
**依赖：** T2、T8

**步骤：**
1. 构造字段契约与 `SessionStatusSnapshot` 一致的 new/restored fake，断言 `/session` 输出当前 ID、消息数、来源、恢复 ID 和最近时间。
2. 构造字段契约与 `MemoryScopeStatus` 一致的用户级/项目级 fake，断言 `/memory` 输出路径、笔记数、索引行数、字节数和诊断代码。
3. 在诊断和测试 fixture 中放入模拟笔记正文，断言命令输出不包含该正文。
4. 测试两个命令带参数时只显示用法，不执行磁盘状态查询。
5. 实现纯文本格式化与异步状态调用，字段顺序固定为 plan.md 定义顺序。

**验证：** `python -m pytest tests/test_slash_builtins.py -q`，期望会话来源、双作用域记忆、正文隔离和参数测试通过。

## T14：实现 `/review` 固定提示词

**文件：** `src/mycode/slash/builtins.py`、`tests/test_slash_builtins.py`  
**依赖：** T8

**步骤：**
1. 编写提示词常量测试，断言包含 staged、unstaged、untracked、ignored、缺陷、回归、安全和测试缺口对应中文语义。
2. 编写 `/review` 测试，断言 `send_user_message()` 收到完整 `REVIEW_PROMPT`，而不是 `/review` 字面量。
3. 断言 handler 只发送一次普通用户消息并返回 `CONTINUE`。
4. 测试额外参数时显示 `/review` 用法且不发送消息。
5. 实现固定提示词处理函数，不读取工作区、不拼接动态内容、不绕过 Agent 权限链。

**验证：** `python -m pytest tests/test_slash_builtins.py -q`，期望固定提示词、普通消息转发和参数拒绝测试通过。

## T15：组装默认命令注册表

**文件：** `src/mycode/slash/builtins.py`、`src/mycode/slash/__init__.py`、`tests/test_slash_builtins.py`  
**依赖：** T9-T14

**步骤：**
1. 编写默认注册表测试，断言十个公开命令和隐藏退出都绑定到对应的最终 handler，顺序与 T8 元数据目录一致。
2. 断言主名称、全部固定别名和大小写变体解析到同一个 `SlashCommand`；`/plan-only` 无法解析。
3. 为 `/status` 定义最终异步 handler：校验无参数、调用一次 `application_status()`、再调用 `slash.status` 的纯文本格式化函数；该依赖在函数执行时解析，避免模块循环导入。
4. 实现 `create_default_slash_registry()`，用明确的主名称到 handler 映射合并 T8 元数据；缺失或多余 handler 时立即抛注册错误。
5. 更新 `slash.__init__` 导出默认注册中心创建入口，并重新运行全部内置命令测试。

**验证：** `python -m pytest tests/test_slash_builtins.py tests/test_slash_registry.py -q`，期望完整元数据、handler 绑定、固定别名、隐藏退出和 `/plan-only` 移除测试通过。

## T16：解析 Git 仓库与分支头信息

**文件：** `src/mycode/slash/status.py`、`tests/test_slash_status.py`  
**依赖：** T2

**步骤：**
1. 在临时目录初始化 Git 仓库并提交基线，设置本地用户配置，断言仓库根目录和当前分支可读。
2. 创建本地 bare 上游或用固定 porcelain fixture，覆盖 `branch.upstream` 和 `branch.ab +2 -1`，断言 upstream、ahead、behind 正确。
3. 测试 detached HEAD fixture，断言分支显示稳定的 detached 标记而不是空字符串。
4. 测试非 Git 目录，断言返回 `is_repository=False` 和零计数，不抛异常。
5. 实现受超时限制的 `subprocess.run()`、`rev-parse` 和 porcelain v2 branch header 解析；命令参数使用列表，不经 shell 拼接。

**验证：** `python -m pytest tests/test_slash_status.py -q`，期望仓库、分支、上游、ahead/behind、detached 和非仓库测试通过。

## T17：统计 Git staged、unstaged 与 untracked

**文件：** `src/mycode/slash/status.py`、`tests/test_slash_status.py`  
**依赖：** T16

**步骤：**
1. 在临时仓库创建一个 staged 文件、一个 unstaged 文件、一个 untracked 文件和一个 ignored 文件，断言计数分别为 1、1、1 且 ignored 不计入。
2. 创建同一文件同时 staged 与 unstaged 的状态，断言它分别计入两个计数。
3. 使用 porcelain v2 `1`、`2`、`u`、`?` fixture 覆盖普通、重命名、冲突和未跟踪记录。
4. 实现 XY 状态解析：X 非 `.` 计 staged，Y 非 `.` 计 unstaged，`?` 计 untracked；忽略 `!` 记录。
5. 对 Git 超时和不可执行错误抛出内部状态异常，供上层 `StatusSection` 隔离，不在异常中包含环境变量。

**验证：** `python -m pytest tests/test_slash_status.py -q`，期望三类计数、双状态文件、ignored 排除和异常测试通过。

## T18：读取 MCP 连接池内存快照

**文件：** `src/mycode/slash/status.py`、`tests/test_slash_status.py`  
**依赖：** T2

**步骤：**
1. 创建只实现 `server_names`、`server_state()`、`is_available()`、`tools` 和 `diagnostics` 的 fake pool，禁止调用 `initialize_all()` 或 `ensure_available()`。
2. 构造 READY、FAILED、CLOSED 三种服务，断言名称、状态、可用性和按服务统计的工具数正确。
3. 构造带模拟 header secret 的诊断消息，断言快照只保留 `category`，不保留消息正文。
4. 断言服务顺序保持 `server_names` 顺序，每个服务的诊断类别稳定去重。
5. 实现 `collect_mcp_status()`，只调用连接池同步公开接口。

**验证：** `python -m pytest tests/test_slash_status.py -q`，期望 MCP 状态、工具计数、诊断脱敏和零网络方法调用通过。

## T19：隔离并格式化综合 `/status`

**文件：** `src/mycode/slash/status.py`、`src/mycode/slash/builtins.py`、`tests/test_slash_status.py`、`tests/test_slash_builtins.py`  
**依赖：** T13、T15、T16、T17、T18

**步骤：**
1. 编写 `StatusSection` 捕获辅助函数测试，成功返回 `value`，异常只返回稳定错误类别而不包含模拟 secret。
2. 构造完整 `ApplicationStatusSnapshot`，断言格式顺序为工作区、模式、权限、Token、会话、记忆、Git、MCP。
3. 将 Git 和 memory 两项设为失败，断言对应项显示未知，其余项完整显示。
4. 实现综合格式化和 `/status` handler；handler 只调用一次 `application_status()`，额外参数显示用法。
5. 断言输出不包含 API key、MCP headers、记忆正文或异常原始 repr。

**验证：** `python -m pytest tests/test_slash_status.py tests/test_slash_builtins.py -q`，期望固定顺序、单项故障隔离、脱敏和命令行为通过。

## T20：增加无副作用 Token 状态估算

**文件：** `src/mycode/compact/models.py`、`src/mycode/compact/manager.py`、`src/mycode/compact/__init__.py`、`tests/test_slash_snapshots.py`  
**依赖：** 无

**步骤：**
1. 编写 `ContextTokenStatus` 不可变模型测试，固定四个字段及中文注释对应语义。
2. 用 fake request builder 构造包含历史与工具定义的请求，断言 `estimate_current()` 返回估算 Token、窗口上限、比例和估算来源。
3. 记录 usage 锚点后再次估算，断言来源可切换为 `usage_delta`，且方法本身不改变锚点。
4. 断言估算前后 memory、归档 store、熔断计数和会话目录均未改变，fake LLM 未调用。
5. 实现 `ContextManager.estimate_current()` 并从 compact 包导出模型；比例使用 `estimated/context_window`，结果钳制在非负范围。

**验证：** `python -m pytest tests/test_slash_snapshots.py -q`，期望 Token 字段、两种来源和无副作用测试通过。

## T21：让 Agent 构造当前完整 Token 快照

**文件：** `src/mycode/agent/loop.py`、`tests/test_slash_snapshots.py`  
**依赖：** T20

**步骤：**
1. 使用 recording context manager 和 fake prompt builder，断言 `context_token_status(mode=...)` 传入当前 memory、工具定义、模式和延迟工具提醒。
2. 断言尚无请求时使用空框架上下文；完成一次带项目记忆框架块的 run 后，状态估算复用最近框架块。
3. 断言状态查询不调用 `ProjectMemoryManager.before_user_request()`，不递增 `_next_turn_id`，不产生 AgentEvent。
4. 在 Agent 初始化时保存空的最近框架上下文，在普通 run 成功准备后更新缓存；实现只读 turn context 和 request builder。
5. 增加 `session_status()`、`memory_status()` 对项目记忆门面的明确转发；项目记忆不可用时抛出稳定 unavailable 错误供上层隔离。

**验证：** `python -m pytest tests/test_slash_snapshots.py -q`，期望完整请求估算、框架缓存和无请求副作用测试通过。

## T22：定义会话与记忆状态并查询当前会话摘要

**文件：** `src/mycode/memory/models.py`、`src/mycode/memory/sessions.py`、`src/mycode/memory/__init__.py`、`tests/test_slash_snapshots.py`  
**依赖：** 无

**步骤：**
1. 编写 `SessionSource`、`SessionStatusSnapshot`、`MemoryScopeStatus` 和 `MemoryStatusSnapshot` 的枚举、字段与不可变性测试。
2. 创建空的当前会话文件，断言 `current_summary()` 返回当前 ID、零消息和 `updated_at=None`。
3. 追加 user/assistant 消息后再次查询，断言当前摘要的消息数和更新时间来自当前 JSONL。
4. 实现 `SessionArchiveStore.current_summary()`，复用现有 `_scan_session()`，不扫描或选择其他会话。
5. 从 memory 包导出新增模型。

**验证：** `python -m pytest tests/test_slash_snapshots.py -q`，期望状态模型、空会话和追加后摘要测试通过。

## T23：生成项目记忆的会话与双作用域快照

**文件：** `src/mycode/memory/manager.py`、`tests/test_slash_snapshots.py`  
**依赖：** T22

**步骤：**
1. 编写初始状态测试，断言当前写入 ID、内存消息数、`source=NEW`、无恢复 ID和当前摘要时间。
2. 让 `before_user_request()` 恢复旧会话，断言来源变为 `RESTORED`、保存旧会话 ID，消息数包含恢复历史，当前 ID仍是新写入会话。
3. 调用 `clear_session_state()`，断言生成新 ID、来源回到 NEW、恢复 ID清空且消息数随已清空 memory 为零。
4. 构造两个作用域的笔记和索引，断言 `memory_status()` 返回路径、笔记数、行数、字节数和最近十个去重诊断代码，不包含正文。
5. 实现恢复来源跟踪、最近诊断保存、`session_status()` 和 `memory_status()`；后台诊断合并只输出代码。

**验证：** `python -m pytest tests/test_slash_snapshots.py -q`，期望 new/restored/clear、双作用域计数和正文隔离测试通过。

## T24：通过 ChatSession 转发状态

**文件：** `src/mycode/session.py`、`tests/test_slash_snapshots.py`  
**依赖：** T21、T23

**步骤：**
1. 扩展 fake agent，记录 `context_token_status(mode=...)`、`session_status()` 和 `memory_status()` 调用。
2. 编写当前模式转发测试，开启 plan-only 后断言 Token 查询收到同一个 PLAN 状态对象。
3. 编写会话与记忆快照透传测试，断言 ChatSession 不复制、不修改领域快照。
4. 实现三个薄转发方法，保持现有 send、compact、clear 和权限方法不变。
5. 运行快照测试并检查 `session.py` 不导入 Rich、prompt_toolkit、Git 或 MCP。

**验证：** `python -m pytest tests/test_slash_snapshots.py -q`，期望模式、Token、会话和记忆薄转发测试通过。

## T25：适配 `prompt_toolkit` 命令补全

**文件：** `src/mycode/slash/completion.py`、`tests/test_slash_completion.py`  
**依赖：** T4

**步骤：**
1. 用真实 `Document` 测试 `/sta`，断言只得到 `/status` 与 `/stat` 的匹配候选，`start_position` 覆盖当前片段。
2. 测试唯一前缀、大小写 `/STA`、多个候选 `/c` 和仅斜杠 `/`，断言候选文本和 `display_meta` 稳定。
3. 测试 `/status `、`/permission d`、普通文本、`/exit` 和 `/quit`，断言不返回参数或隐藏候选。
4. 实现 `SlashCommandCompleter.get_completions()`，只读取 `document.text_before_cursor`，不访问文件或模型。
5. 更新 `slash.__init__` 导出补全器。

**验证：** `python -m pytest tests/test_slash_completion.py -q`，期望唯一、多候选、大小写、参数位置和隐藏过滤测试通过。

## T26：让 TUI 通过分发器处理输入

**文件：** `src/mycode/tui.py`、`tests/test_slash_tui.py`  
**依赖：** T7、T15、T24

**步骤：**
1. 创建 self-contained fake session、fake pool、recording dispatcher 和 StringIO Console，不复用已删除的旧测试 helper。
2. 编写输入循环测试，依次返回 EMPTY、HANDLED、NOT_COMMAND 和 EXIT，断言只有 `NOT_COMMAND.normal_text` 进入 session send；启动提示只引导 `/help`，不暴露隐藏 `/exit`。
3. 编写未知命令集成测试，断言不会调用 Agent；编写 `/review` 集成测试，断言展开提示词进入现有 `_render_stream()`。
4. 在 `ChatTUI` 构造器注入 dispatcher、registry、MCP pool 和 workspace；实现 `show_message`、`send_user_message`、`compact_context`、`clear_session`、模式和权限控制方法。
5. 删除 `run()` 中 `/exit`、`/clear`、`/compact`、`/plan-only` 和 `/permission` 的硬编码条件链，改为统一分发结果分支，并把旧 Stage 07 启动文案改成通用 `/help` 引导。

**验证：** `python -m pytest tests/test_slash_tui.py -q`，期望统一分流、普通消息唯一发送、未知隔离和提示词转发测试通过。

## T27：实现 TUI 状态采集、补全和模式栏

**文件：** `src/mycode/tui.py`、`tests/test_slash_tui.py`  
**依赖：** T19、T25、T26

**步骤：**
1. 编写控制接口测试，断言 `token_status()`、`session_status()`、`memory_status()` 分别调用 ChatSession 对应方法，并通过 `asyncio.to_thread()` 隔离磁盘查询。
2. 编写 `application_status()` 测试，让 Token 和 Git 抛异常，断言两项失败、其余权限/会话/记忆/MCP 项仍成功。
3. monkeypatch `PromptSession` 捕获构造参数，断言注入 `SlashCommandCompleter`、`complete_while_typing=False`、`CompleteStyle.COLUMN` 和动态 `bottom_toolbar`。
4. 切换 `/plan`、`/do` 后调用 toolbar callable，断言分别返回 `[PLAN]`、`[DEFAULT]`；模拟无控制台降级时断言相同标记进入普通提示符。
5. 实现状态采集、纯文本 `markup=False` 输出、PromptSession 配置和降级提示；用中文注释说明模式必须在渲染时读取而非启动时缓存。

**验证：** `python -m pytest tests/test_slash_tui.py tests/test_slash_status.py tests/test_slash_completion.py -q`，期望控制协议、故障隔离、补全配置、状态栏和降级测试通过。

## T28：在 CLI 启动期装配命令系统

**文件：** `src/mycode/cli.py`、`tests/test_slash_cli.py`  
**依赖：** T26、T27

**步骤：**
1. 编写成功装配测试，monkeypatch 默认注册中心和 TUI，断言 dispatcher、registry、pool 和 `Path.cwd()` 被注入。
2. 编写冲突测试，让默认注册中心工厂抛 `SlashCommandRegistrationError`，断言 `main()` 返回 `1`、stderr 指出冲突名称且 TUI/MCP 初始化均未启动。
3. 调整 `main()` 在进入 `asyncio.run()` 前构建注册中心和分发器，并把二者传给 `_run_application()`。
4. `_run_application()` 创建 ChatTUI 时注入注册中心、分发器、现有 MCP pool 和工作区；保持资源关闭顺序不变。
5. 记录启动错误时只输出安全注册元数据，不输出 handler repr 或进程环境。

**验证：** `python -m pytest tests/test_slash_cli.py -q`，期望成功注入、冲突早失败、退出码和零 MCP 初始化测试通过。

## T29：更新公开命令文档

**文件：** `README.md`  
**依赖：** T28

**步骤：**
1. 将现有交互命令章节更新为十个公开命令，写清固定别名、参数和本地/界面/提示词行为。
2. 删除 `/plan-only` 公开说明，改写为 `/plan` 进入和 `/do` 退出，并说明底部 `[DEFAULT]/[PLAN]` 标记。
3. 说明 `/status` 的 Git 与 MCP 快照边界、`/review` 会进入普通会话、`/exit` 仍兼容但隐藏。
4. 说明第一版不支持用户命令、参数补全、会话切换、记忆正文和主动 MCP 探测。
5. 用 `rg` 核对十个公开主名称都出现，`/plan-only` 不再出现在交互命令章节。

**验证：** `rg -n "/(help|compact|clear|plan|do|session|memory|permission|status|review)" README.md` 显示十个公开命令；`rg -n "/plan-only" README.md` 在交互命令章节无匹配。

## T30：覆盖完整命令链端到端场景

**文件：** `tests/test_slash_e2e.py`  
**依赖：** T28

**步骤：**
1. 组装真实默认注册中心、分发器和 ChatTUI，使用 fake Agent/Session、临时 Git 仓库、fake MCP pool 与 StringIO Console。
2. 输入 `/help`、`/plan`、`/status`、普通问题、`/do`、`/clear`、`/exit`，断言只发送一次普通问题。
3. 断言模式标记依次出现 PLAN 和 DEFAULT，clear 清空并复位权限，status 包含 Git/MCP/Token/会话/记忆摘要。
4. 在 fake Agent 记录所有输入，断言本地及界面命令字面量均未进入 Agent 或会话历史。
5. 断言输出顺序稳定且不包含 fixture 中的 API key、MCP header 或记忆正文。

**验证：** `python -m pytest tests/test_slash_e2e.py::test_slash_command_workflow_end_to_end -q`，期望完整命令链通过且只有普通问题进入 Agent。

## T31：覆盖 `/review` 与权限端到端场景

**文件：** `tests/test_slash_e2e.py`  
**依赖：** T14、T30

**步骤：**
1. 在临时 Git 仓库创建 staged、unstaged、untracked 和 ignored 文件，输入 `/review` 后退出。
2. 断言 fake Agent 收到完整固定提示词，提示词包含四类范围语义且不包含 `/review` 字面量。
3. 断言提示词作为普通用户消息被记录一次，Agent 返回事件按现有 TUI 流输出。
4. 让 fake Agent 产生写工具审批事件，断言仍调用现有 approval provider，内置提示词没有绕过权限。
5. 断言 ignored 文件正文和测试 API key 未出现在命令状态或错误输出中。

**验证：** `python -m pytest tests/test_slash_e2e.py::test_review_command_uses_normal_agent_and_permission_flow -q`，期望提示词持久化、范围、事件流和权限链通过。

## T32：执行 Stage 09 回归与代码质量检查

**文件：** 本阶段全部新增和修改文件  
**依赖：** T1-T31

**步骤：**
1. 运行所有 Stage 09 测试，修复失败后重新运行，直到结果全绿。
2. 运行当前工作区仍存在的全部测试，确认没有破坏项目记忆和其他保留模块；不恢复用户已删除的旧测试。
3. 运行 `python -m compileall -q src`，确认所有新增类型注解兼容 Python 3.10 语法。
4. 用 `rg` 检查 `src/mycode/tui.py` 不再包含 `/plan-only` 或按命令名展开的条件链；检查 `src/mycode/slash` 不导入 `mycode.tui` 或 Rich。
5. 检查注册冲突、未知输入、提示词转发、模式栏、Git 解析和状态故障隔离路径有简短中文原因注释；运行 `git diff --check` 检查空白问题。

**验证：**

```powershell
python -m pytest tests/test_slash_registry.py tests/test_slash_parser.py tests/test_slash_dispatcher.py tests/test_slash_builtins.py tests/test_slash_status.py tests/test_slash_completion.py tests/test_slash_snapshots.py tests/test_slash_tui.py tests/test_slash_cli.py tests/test_slash_e2e.py -q
python -m pytest -q
python -m compileall -q src
rg -n "/plan-only|command == \"/|command.startswith\(\"/" src/mycode/tui.py
rg -n "from mycode\.tui|from rich" src/mycode/slash
git diff --check
```

期望：Stage 09 测试和当前保留测试全部通过，编译无错误；两个边界 `rg` 命令无匹配；`git diff --check` 无输出。

## Spec 覆盖自检

| Spec | 对应任务 |
|---|---|
| F1 | T1、T8、T15 |
| F2 | T3、T28 |
| F3 | T5 |
| F4 | T6、T26 |
| F5 | T7 |
| F6 | T2、T26、T27 |
| F7 | T9 |
| F8-F9 | T10、T23 |
| F10 | T11、T27 |
| F11-F12 | T13、T22-T24 |
| F13 | T12 |
| F14 | T16-T19、T27 |
| F15 | T14、T31 |
| F16 | T8、T15 |
| F17 | T4、T25、T27 |
| F18 | T11、T27 |
| F19 | T11、T15、T26 |
| N1-N6 | T3、T7、T16-T24、T32 |
| N7-N10 | T7、T13-T19、T27、T30-T32 |
| N11-N12 | T1-T8、T15、T32 |

## 执行顺序

```text
T1 -> T2
 |
 +-> T3 -> T4 -> T5 -> T6 -> T7 ---------------------------------------------+
 |        |                                                                   |
 |        +-> T25 ------------------------------------------------------------+
 |                                                                            |
 +-> T8 -> T9 -> T10 -> T11 -> T12 -> T13 -> T14 -> T15 ---------------------+
                                                                              |
T2 -> T16 -> T17 --------------------------------------------------+           |
 |                                                                |           |
 +-> T18 ----------------------------------------------------------+-> T19 ----+
                                                                              |
T20 -> T21 -------------------------------------------------------------------+
T22 -> T23 -> T24 ------------------------------------------------------------+
                                                                              v
                                                                     T26 -> T27 -> T28
                                                                                     |
                                                                                     +-> T29
                                                                                     +-> T30 -> T31
                                                                                                  |
                                                                                                  v
                                                                                                 T32
```

## 建议提交点

| 完成任务 | 提交信息 | 文件范围 |
|---|---|---|
| T1-T7 | `feat: add slash command core` | `src/mycode/slash/{models,controller,registry,parser,dispatcher}.py` 及核心测试 |
| T8-T15 | `feat: register built-in slash commands` | `slash/builtins.py`、包导出和内置命令测试 |
| T16-T19 | `feat: report local application status` | `slash/status.py` 和状态测试 |
| T20-T24 | `feat: expose session status snapshots` | compact、memory、agent、session 和快照测试 |
| T25-T28 | `feat: integrate slash commands into tui` | completion、tui、cli 及对应测试 |
| T29-T31 | `docs: document slash command workflow` | README 和端到端测试 |
| T32 | `test: verify slash command workflow` | 仅 T32 中确有修复的文件；无改动则不创建空提交 |

每次提交只暂存表中列出的 Stage 09 文件，避免把工作区已有的测试删除或其他用户改动带入提交。
