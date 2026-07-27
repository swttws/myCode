# myCode Stage 10：Skill 系统任务拆解

## 执行约束

- 严格按测试先行执行：先添加能证明缺失行为的测试并确认它按预期失败，再写最小实现，最后重跑目标测试。
- 每个任务只处理列出的文件和行为；实现保持简洁，不为市场分发、版本管理或远程安装预留抽象。
- 只在三级优先级回退、资源路径安全、执行范围传播、热更新保底和独立历史隔离处添加简洁中文注释；自解释代码不添加叙述性注释。
- 所有文件使用 UTF-8；每个 Skill 独占一个目录，入口只认 `SKILL.md`。
- 测试必须使用临时项目目录、临时用户目录、fake LLM 和固定工具注册表，不读取真实用户 Skill，不访问网络或真实 API。
- 工作区已有用户修改。执行提交时只暂存本文件清单中的 Stage 10 变更，不恢复已删除的旧测试，不纳入 `examples/mycode.openai-responses.yaml` 的现有修改。

## 文件清单

### 新建实现文件

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `src/mycode/skill/__init__.py` | 导出 Skill 领域公共类型和装配入口 |
| 新建 | `src/mycode/skill/models.py` | 枚举、不可变数据、错误类型和有界常量 |
| 新建 | `src/mycode/skill/loader.py` | 三级扫描、严格解析、资源清单与安全读取 |
| 新建 | `src/mycode/skill/catalog.py` | 优先级覆盖、启动校验、缓存、热更新与诊断 |
| 新建 | `src/mycode/skill/runtime.py` | 激活状态、参数渲染、Prompt 块和执行范围 |
| 新建 | `src/mycode/skill/context.py` | 完整轮次选择、摘要输入与临时上下文管理 |
| 新建 | `src/mycode/skill/executor.py` | 独立模式模型选择、临时 AgentLoop 和结果摘要 |
| 新建 | `src/mycode/skill/load_tool.py` | 系统级 `load_skill` 工具和稳定结果协议 |
| 新建 | `src/mycode/skill/slash.py` | Skill 动态斜杠命令桥接和热更新诊断 |
| 新建 | `src/mycode/skill/builtins/commit/SKILL.md` | 内置提交 SOP |
| 新建 | `src/mycode/skill/builtins/review/SKILL.md` | 内置审查 SOP |
| 新建 | `src/mycode/skill/builtins/test/SKILL.md` | 内置独立测试 SOP |

### 修改实现与文档文件

| 操作 | 文件 | 职责 |
|---|---|---|
| 修改 | `src/mycode/tool/base.py` | 为工具定义增加 `parallel_safe` |
| 修改 | `src/mycode/tool/registry.py` | 按可见名称过滤工具并显式展开白名单中的延迟工具 |
| 修改 | `src/mycode/agent/scheduler.py` | 把非并行安全工具作为独立顺序边界 |
| 修改 | `src/mycode/agent/loop.py` | 接入 Skill Prompt、范围校验、父任务范围切换和外部摘要写回 |
| 修改 | `src/mycode/session.py` | 路由共享/独立 Skill，并在 `/clear` 清理运行时状态 |
| 修改 | `src/mycode/slash/__init__.py` | 更新斜杠公共导出并移除硬编码 review 导出 |
| 修改 | `src/mycode/slash/builtins.py` | 删除硬编码 `/review` 旁路 |
| 修改 | `src/mycode/slash/controller.py` | 增加 `execute_skill()` 控制协议 |
| 修改 | `src/mycode/slash/registry.py` | 原子替换动态命令 |
| 修改 | `src/mycode/slash/dispatcher.py` | 分发前刷新并展示待处理诊断 |
| 修改 | `src/mycode/slash/completion.py` | 补全前静默刷新 |
| 修改 | `src/mycode/tui.py` | 执行 Skill 并复用现有 AgentEvent 渲染 |
| 修改 | `src/mycode/cli.py` | 按既定顺序装配、校验 Skill 系统并报告启动错误 |
| 修改 | `pyproject.toml` | 将三个内置 `SKILL.md` 声明为包数据 |
| 修改 | `README.md` | 说明目录、格式、两阶段加载、执行模式和热更新 |

### 新建测试文件

| 操作 | 文件 | 覆盖范围 |
|---|---|---|
| 新建 | `tests/skill_test_support.py` | 临时 Skill、fake LLM、fake 工具和运行时测试工厂 |
| 新建 | `tests/test_skill_models.py` | 数据模型、校验规则、常量和错误类型 |
| 新建 | `tests/test_skill_loader.py` | 扫描、解析、边界、资源清单和资源读取 |
| 新建 | `tests/test_skill_catalog.py` | 三级覆盖、启动失败、缓存和热更新保底 |
| 新建 | `tests/test_skill_runtime.py` | 激活、Prompt、参数替换、范围和清理 |
| 新建 | `tests/test_skill_context.py` | 最近轮次、摘要输入和临时上下文预算 |
| 新建 | `tests/test_skill_load_tool.py` | `load_skill` 入口、资源、模式和递归拒绝 |
| 新建 | `tests/test_skill_executor.py` | 三种独立上下文、模型覆盖、摘要和失败隔离 |
| 新建 | `tests/test_skill_slash.py` | 动态注册、冲突、热更新、帮助和补全 |
| 新建 | `tests/test_skill_agent.py` | 工具视图、调度、AgentLoop Prompt 和二次校验 |
| 新建 | `tests/test_skill_cli.py` | 启动装配顺序、fail-fast 和退出码 |
| 新建 | `tests/test_skill_e2e.py` | 共享/独立完整流程、热更新和 `/clear` |
| 新建 | `tests/test_skill_docs.py` | 内置 Skill、包数据和 README 契约 |

### 按行为调整的现有测试

| 操作 | 文件 | 调整原因 |
|---|---|---|
| 修改 | `tests/test_slash_builtins.py` | `/review` 从固定命令迁移为内置 Skill |
| 修改 | `tests/test_slash_cli.py` | CLI 新增 Skill 启动校验和装配参数 |
| 修改 | `tests/test_slash_completion.py` | 动态 Skill 补全和补全前刷新 |
| 修改 | `tests/test_slash_dispatcher.py` | 分发前刷新与诊断展示 |
| 修改 | `tests/test_slash_e2e.py` | `/review` 走 `ChatSession.send_skill()` |
| 修改 | `tests/test_slash_registry.py` | 固定命令与动态命令原子共存 |
| 修改 | `tests/test_slash_snapshots.py` | 帮助快照包含动态 Skill |
| 修改 | `tests/test_slash_tui.py` | TUI Skill 执行入口和事件渲染 |

## 任务列表

## T1：建立测试工厂并写领域模型失败测试

**文件：** `tests/skill_test_support.py`、`tests/test_skill_models.py`  
**依赖：** 无

**步骤：**
1. 增加只写临时目录的 `write_skill()`，固定生成目录型 `SKILL.md`，并支持资源文件、原始正文和 frontmatter 覆盖。
2. 增加记录请求的 fake LLM、最小 fake 工具和固定工具注册表工厂；所有返回内容由测试显式传入。
3. 为 `SkillSource`、`SkillMode`、`SkillContextStrategy`、`SOURCE_PRIORITY`、不可变数据类、错误层级和四个大小常量编写测试。
4. 覆盖 `recent` 必须使用正整数轮数、`none/summary` 禁止轮数，以及共享模式保存 `context=None` 的规则。
5. 运行测试并确认失败原因是 `mycode.skill` 领域模型尚未实现，而不是测试夹具访问真实目录或网络。

**验证：** `python -m pytest tests/test_skill_models.py -q`；预期非零退出，失败集中在缺失的 `mycode.skill.models` 接口。

## T2：实现领域模型与公共导出

**文件：** `src/mycode/skill/__init__.py`、`src/mycode/skill/models.py`  
**依赖：** T1

**步骤：**
1. 按 `plan.md` 定义三个枚举、`SOURCE_PRIORITY`、所有不可变数据类、五个错误类型和四个有界常量。
2. 在数据类构造边界执行必要的不变量校验，不把 YAML 或文件系统逻辑放进模型层。
3. 从 `mycode.skill` 导出后续模块需要的稳定公共类型。
4. 保持错误消息短且可定位，不包含正文、历史或凭据。

**验证：** `python -m pytest tests/test_skill_models.py -q`；预期全部通过。

## T3：编写目录扫描失败测试

**文件：** `tests/test_skill_loader.py`  
**依赖：** T2

**步骤：**
1. 为项目、用户、内置三层一级目录扫描和固定入口 `SKILL.md` 编写测试。
2. 验证根目录散落 Markdown 不被识别，缺少入口的一级目录产生诊断。
3. 验证候选来源、包根目录、入口路径和由相对路径、大小、`mtime_ns` 组成的确定性指纹。
4. 验证符号链接目录和符号链接入口被拒绝；平台无法创建符号链接时只跳过对应断言。

**验证：** `python -m pytest tests/test_skill_loader.py -q -k "scan or fingerprint or symlink"`；预期非零退出，失败指向尚未实现的 `SkillLoader.scan()`。

## T4：实现三级目录扫描与指纹

**文件：** `src/mycode/skill/loader.py`  
**依赖：** T3

**步骤：**
1. 实现 `SkillLoader` 构造和 `scan()`，只遍历三个根目录下的一级普通目录。
2. 为入口和资源生成按相对路径排序的指纹，不在未变化判断之外读取正文。
3. 对缺失入口、非普通路径和符号链接生成不泄漏内容的 `SkillDiagnostic`。
4. 在候选发现和符号链接拒绝处添加简洁中文注释，其他分支保持自解释。

**验证：** `python -m pytest tests/test_skill_loader.py -q -k "scan or fingerprint or symlink"`；预期全部通过。

## T5：编写严格 frontmatter 解析失败测试

**文件：** `tests/test_skill_loader.py`  
**依赖：** T4

**步骤：**
1. 覆盖有效 shared、isolated/none、isolated/recent、isolated/summary 和可选模型定义。
2. 覆盖文件不以 `---` 开始、缺少结束线、非映射 YAML、未知字段、缺失字段、非法名称、目录名不一致、重复工具和空正文。
3. 覆盖共享模式携带 `context`、独立模式缺少 `context`、`turns` 类型或范围错误，以及不适用策略携带 `turns`。
4. 覆盖 UTF-8 解码失败、入口超限和 frontmatter 超限均只形成当前候选解析错误。
5. 断言正文原样保留内部 Markdown，仅去除首尾空白，并且加载器使用安全 YAML 解析。

**验证：** `python -m pytest tests/test_skill_loader.py -q -k "load or frontmatter or metadata or limit"`；预期非零退出，失败指向尚未实现的 `SkillLoader.load()`。

## T6：实现严格 frontmatter 与正文解析

**文件：** `src/mycode/skill/loader.py`  
**依赖：** T5

**步骤：**
1. 使用 `yaml.safe_load` 和固定字段白名单解析入口，禁止隐式接受未知配置。
2. 按字段表构造 `SkillMetadata` 和上下文策略，强制目录名等于 `name`。
3. 在读取前检查入口和 frontmatter 字节上限，以 UTF-8 严格解码。
4. 生成 `SkillDefinition`，使 `revision` 基于入口内容和资源清单的 SHA-256，错误统一转换为 `SkillParseError`。

**验证：** `python -m pytest tests/test_skill_loader.py -q -k "load or frontmatter or metadata or limit"`；预期全部通过。

## T7：编写资源清单与安全读取失败测试

**文件：** `tests/test_skill_loader.py`  
**依赖：** T6

**步骤：**
1. 验证资源清单排除入口和全部符号链接，使用 `/` 分隔并按字典序返回。
2. 验证资源数量超过 256 时候选解析失败，单个资源超过 1 MiB 时仍列出但读取失败。
3. 验证相对路径穿越、绝对路径、目录、未知资源、符号链接逃逸和包外真实路径均被拒绝。
4. 验证合法 UTF-8 模板、示例、脚本和参考文档可逐个读取，且加载入口不会自动读取正文或执行脚本。

**验证：** `python -m pytest tests/test_skill_loader.py -q -k "resource or traversal or escape"`；预期非零退出，失败指向资源边界逻辑尚未完成。

## T8：实现资源清单与安全读取

**文件：** `src/mycode/skill/loader.py`  
**依赖：** T7

**步骤：**
1. 在 `load()` 中生成有界、排序、无符号链接的资源清单并纳入版本摘要。
2. 实现 `read_resource()`，先规范化相对路径，再同时校验词法边界和解析后的真实路径边界。
3. 对不存在、目录、符号链接、越界、超限和 UTF-8 错误返回稳定 `SkillResourceError`。
4. 在真实路径边界检查处添加简洁中文注释，确保辅助脚本永不被加载器执行。

**验证：** `python -m pytest tests/test_skill_loader.py -q`；预期全部通过。

## T9：编写目录初始化、覆盖和 fail-fast 失败测试

**文件：** `tests/test_skill_catalog.py`  
**依赖：** T8

**步骤：**
1. 验证项目高于用户、用户高于内置，定义按名称排序，普通高层解析失败时回退低层有效版本并保留诊断。
2. 验证单个坏文件不阻断其他 Skill，同一输入重复初始化得到相同定义和诊断顺序。
3. 验证最终有效白名单引用未知工具时 `initialize()` 抛出 `SkillStartupError`，消息包含 Skill 名称和全部未知工具。
4. 验证 Skill 名称与固定命令主名称或别名冲突时启动失败。
5. 验证 `snapshot()`、`get()` 和 `read_resource()` 的成功及未知名称行为。

**验证：** `python -m pytest tests/test_skill_catalog.py -q -k "initialize or precedence or fallback or startup"`；预期非零退出，失败指向 `SkillCatalog` 尚未实现。

## T10：实现目录初始化、三级覆盖与启动校验

**文件：** `src/mycode/skill/catalog.py`  
**依赖：** T9

**步骤：**
1. 实现不可变 `SkillCatalogSnapshot` 的首次构建、按名称覆盖和确定性排序。
2. 将单候选 `SkillParseError` 转为诊断，并继续计算较低优先级同名候选。
3. 在最终有效集合上集中校验工具名称和固定斜杠名称，启动错误一次列全受影响项。
4. 实现 `snapshot()`、`get()` 和资源读取委托；目录层不持有激活状态。
5. 只在三级回退选择处添加简洁中文注释。

**验证：** `python -m pytest tests/test_skill_catalog.py -q -k "initialize or precedence or fallback or startup"`；预期全部通过。

## T11：编写增量热更新与最后有效版本失败测试

**文件：** `tests/test_skill_catalog.py`  
**依赖：** T10

**步骤：**
1. 使用可观察加载器验证未变化候选不重读正文，入口或资源指纹变化时只重载受影响候选。
2. 验证新增、修改、删除高优先级版本，以及解析失败后回退低层版本。
3. 验证热更新引入未知工具或固定命令冲突时拒绝该名称的新版本并保留最后有效定义。
4. 验证无最后有效版本的新坏 Skill 不注册，其他有效更新仍生效。
5. 验证有效定义变化才递增 `generation`，仅诊断变化不递增，并对重复诊断去重。

**验证：** `python -m pytest tests/test_skill_catalog.py -q -k "refresh or generation or last_valid or cache"`；预期非零退出，失败指向增量刷新语义尚未实现。

## T12：实现增量热更新和原子快照

**文件：** `src/mycode/skill/catalog.py`  
**依赖：** T11

**步骤：**
1. 缓存候选指纹与解析结果，只重新加载新增或指纹变化候选，并移除已删除候选。
2. 对受影响名称重新计算覆盖，先在临时结构完成语义校验，再原子替换目录快照。
3. 为未知工具和命令冲突实现按名称的最后有效版本保底，不影响同次刷新中的其他名称。
4. 稳定生成诊断、代次和定义顺序，在热更新保底处添加简洁中文注释。

**验证：** `python -m pytest tests/test_skill_catalog.py -q`；预期全部通过。

## T13：编写激活、参数渲染与两阶段 Prompt 失败测试

**文件：** `tests/test_skill_runtime.py`  
**依赖：** T12

**步骤：**
1. 验证未激活时只返回 `skill-catalog`，内容只有名称和一句说明，不含 SOP 或资源正文。
2. 验证 `activate()` 只用原始参数执行字面量 `str.replace("{{arguments}}", arguments)`，空参数不残留标记，不执行表达式。
3. 验证多个激活项按名称排序进入优先级 `-200` 的 `active-skills`，目录摘要使用优先级 `-100`。
4. 验证有效热更新后使用原始参数重渲染激活项；Skill 被有效删除后移除对应激活项。
5. 验证加载资源不会新增激活记录或改变原有参数。

**验证：** `python -m pytest tests/test_skill_runtime.py -q -k "activate or prompt or render or refresh"`；预期非零退出，失败指向 `SkillRuntime` 尚未实现。

## T14：实现激活状态与 Prompt 块

**文件：** `src/mycode/skill/runtime.py`  
**依赖：** T13

**步骤：**
1. 实现目录刷新、入口激活、原始参数保存和按名称确定性排序。
2. 生成 `skill-catalog` 与 `active-skills` 两个动态 `PromptContextBlock`，完整 SOP 只进入后者。
3. 在刷新后按新版本重建激活记录，不把激活 SOP 写入普通历史。
4. 在热更新重渲染分支添加简洁中文注释。

**验证：** `python -m pytest tests/test_skill_runtime.py -q -k "activate or prompt or render or refresh"`；预期全部通过。

## T15：编写执行范围、白名单与清理失败测试

**文件：** `tests/test_skill_runtime.py`  
**依赖：** T14

**步骤：**
1. 验证无范围时 `visible_tool_names()` 返回 `None`，有范围时只返回当前 Skill 白名单和系统级 `load_skill`。
2. 验证 `allows_tool()` 始终允许 `load_skill`，空白名单拒绝全部普通工具，且不合并其他已激活 Skill 的白名单。
3. 验证 `execution_scope()` 保存完整 `SkillRunContext`，嵌套进入和退出后恢复父 `ContextVar` 状态。
4. 验证 `set_current_scope()` 只接受已激活 Skill，并供父 AgentLoop 应用共享加载标记。
5. 验证 `clear()` 同时清除激活项和当前范围，但不重新初始化目录缓存。

**验证：** `python -m pytest tests/test_skill_runtime.py -q -k "scope or visible or allows or clear"`；预期非零退出，失败指向运行范围尚未实现。

## T16：实现 ContextVar 范围和白名单判定

**文件：** `src/mycode/skill/runtime.py`  
**依赖：** T15

**步骤：**
1. 使用会话运行时实例内的 `ContextVar` 保存当前范围和当前 `SkillRunContext`。
2. 实现上下文管理器、父任务范围设置、工具可见性与执行前判定，固定保留 `load_skill`。
3. 实现 `clear()`，确保异常退出和嵌套独立执行后恢复父范围。
4. 在父子范围恢复处添加简洁中文注释，说明范围不能依赖工具子任务传播。

**验证：** `python -m pytest tests/test_skill_runtime.py -q`；预期全部通过。

## T17：以 TDD 接入工具可见视图与串行调度

**文件：** `tests/test_skill_agent.py`、`src/mycode/tool/base.py`、`src/mycode/tool/registry.py`、`src/mycode/agent/scheduler.py`  
**依赖：** T16

**步骤：**
1. 先测试 `ToolDefinition.parallel_safe` 默认 `True`，`model_definitions(visible_names=...)` 只返回指定名称，显式白名单中的延迟 MCP 工具直接展开完整定义。
2. 先测试非并行安全 READ 工具会切断连续读批次并独立调度，前后读工具保持模型给出的顺序。
3. 运行目标测试，确认现有实现因缺少字段、过滤参数和串行边界而失败。
4. 最小修改工具定义、注册表和调度器；无范围时保持现有延迟发现行为。
5. 重跑测试，确保 `load_skill` 可被标记为 `READ` 且 `parallel_safe=False`。

**验证：** `python -m pytest tests/test_skill_agent.py -q -k "tool_view or deferred or parallel_safe or schedule"`；预期最终全部通过。

## T18：编写共享入口加载和资源工具失败测试

**文件：** `tests/test_skill_load_tool.py`  
**依赖：** T17

**步骤：**
1. 验证工具名、JSON Schema、`ToolKind.READ` 和 `parallel_safe=False`。
2. 验证共享入口调用先刷新目录，再激活并返回 `action=activated`、模式、版本、资源清单和 `set_scope=True`。
3. 断言工具结果不包含完整 SOP、资源正文或其他 Skill 元数据。
4. 验证资源读取要求已激活、禁止同时传 `arguments`，成功时返回单个文本且不改变当前范围。
5. 验证未知 Skill、未知资源、越界路径和参数类型错误转换为稳定失败 `ToolResult`。

**验证：** `python -m pytest tests/test_skill_load_tool.py -q -k "definition or shared or resource"`；预期非零退出，失败指向 `SkillLoadTool` 尚未实现。

## T19：实现系统级加载工具的共享与资源路径

**文件：** `src/mycode/skill/load_tool.py`、`src/mycode/skill/__init__.py`  
**依赖：** T18

**步骤：**
1. 实现固定工具定义和参数校验，保持工具自身始终独立于 Skill 白名单。
2. 实现共享入口激活结果和资源读取结果，完整 SOP 只交给运行时 Prompt 块。
3. 将 `SkillResourceError`、未知名称和无效参数转换为不泄漏内部异常的失败结果。
4. 为后续独立执行保留显式执行器依赖，不在本任务实现临时 Agent。

**验证：** `python -m pytest tests/test_skill_load_tool.py -q -k "definition or shared or resource"`；预期全部通过。

## T20：以 TDD 实现最近完整轮次选择

**文件：** `tests/test_skill_context.py`、`src/mycode/skill/context.py`  
**依赖：** T2

**步骤：**
1. 先覆盖空历史、少于 N 轮、超过 N 轮、系统/框架消息与普通用户轮次边界。
2. 覆盖包含助手工具调用、多个工具结果和最终助手文本的完整轮次，断言链条不被截断。
3. 覆盖缺少最终助手文本、尚未结束的工具链和当前未完成用户轮次均被排除。
4. 运行测试确认失败后，实现 `select_completed_turns(history, count)`，只返回最近 N 个完整轮次。
5. 重跑目标测试并确认消息对象和原顺序保持不变。

**验证：** `python -m pytest tests/test_skill_context.py -q -k "completed_turns or recent"`；预期最终全部通过。

## T21：以 TDD 实现无持久化临时上下文

**文件：** `tests/test_skill_context.py`、`src/mycode/skill/context.py`  
**依赖：** T20

**步骤：**
1. 先测试 `EphemeralContextManager.prepare_auto()` 使用现有 `TokenEstimator` 构建 `PreparedContext`，但不归档、不压缩、不创建磁盘会话。
2. 先测试 `record_usage()` 只更新临时估算器，不修改主上下文管理器。
3. 先测试请求达到主配置上下文窗口安全线时返回稳定 `independent_context_too_large` 错误。
4. 运行测试确认失败后实现最小适配器，复用现有紧凑模型和估算结构，不复制归档生命周期。
5. 重跑全部上下文测试。

**验证：** `python -m pytest tests/test_skill_context.py -q`；预期最终全部通过。

## T22：编写 AgentLoop 两阶段 Prompt 和父上下文失败测试

**文件：** `tests/test_skill_agent.py`  
**依赖：** T17、T19、T21

**步骤：**
1. 用记录请求的 fake LLM 验证首次模型请求只含 `skill-catalog`，不含任何 SOP 或资源标记文本。
2. 激活两个 Skill 后验证当前轮后续模型请求及下一用户轮都含排序稳定的 `active-skills`，且普通 memory 中无框架 SOP。
3. 验证 `prepare_skill_run_context()` 在当前用户消息写入前捕获主历史、框架块、审批提供者、范围和独立深度。
4. 验证显式传入 `framework_blocks` 的临时 Agent run 跳过项目记忆恢复和记录，主对话默认路径保持原行为。

**验证：** `python -m pytest tests/test_skill_agent.py -q -k "catalog_prompt or active_prompt or run_context or framework_blocks"`；预期非零退出，失败指向 AgentLoop 尚未接入运行时。

## T23：接入 AgentLoop Skill Prompt 与父运行上下文

**文件：** `src/mycode/agent/loop.py`  
**依赖：** T22

**步骤：**
1. 为构造函数增加可选 `skill_runtime`，为 `run()` 增加 `initial_skill_scope` 和 `framework_blocks`，默认值保持兼容。
2. 在写入当前用户消息前建立 `SkillRunContext`，并用运行时范围上下文包住整次 run。
3. 每个模型 round 重取 `runtime.prompt_blocks()`，使激活和热更新在同轮后续请求中可见。
4. 显式框架块路径跳过项目记忆生命周期，主路径继续使用现有构建、压缩和记录逻辑。
5. 实现 `prepare_skill_run_context()` 的只读快照接口。

**验证：** `python -m pytest tests/test_skill_agent.py -q -k "catalog_prompt or active_prompt or run_context or framework_blocks"`；预期全部通过。

## T24：编写工具收窄、二次校验与父范围切换失败测试

**文件：** `tests/test_skill_agent.py`  
**依赖：** T23

**步骤：**
1. 验证每轮模型工具定义只包含当前 Skill 白名单和 `load_skill`，白名单外延迟工具摘要也不进入提醒。
2. 模拟模型伪造白名单外调用，断言权限检查和真实执行器都不会收到该调用，并返回稳定工具范围错误。
3. 模拟同批 `load_skill` 后跟普通工具，断言加载工具先单独执行，父 AgentLoop 读取结构化标记后设置范围，再校验后续调用。
4. 验证只有成功、工具名为 `load_skill`、`action=activated` 且 `set_scope is True` 才切换范围。
5. 验证 run 完成、取消或异常后恢复进入前工具范围。

**验证：** `python -m pytest tests/test_skill_agent.py -q -k "whitelist or preflight or set_scope or restore_scope"`；预期非零退出，失败指向 AgentLoop 工具约束尚未接入。

## T25：实现模型工具视图、执行前校验和父范围切换

**文件：** `src/mycode/agent/loop.py`  
**依赖：** T24

**步骤：**
1. 在每次构建模型请求时把 `runtime.visible_tool_names()` 传给工具注册表；有范围时不生成白名单外延迟摘要。
2. 在权限检查前调用 `runtime.allows_tool()`，使可见性过滤不能替代真实执行约束。
3. 按工具结果顺序解析共享激活标记，并由父 AgentLoop 调用 `runtime.set_current_scope(name)`。
4. 不依赖 `asyncio.wait_for` 子任务中的 `ContextVar` 修改；在该关键传播点添加简洁中文注释。
5. 保持现有审批、超时、读写调度和历史记录顺序不变。

**验证：** `python -m pytest tests/test_skill_agent.py -q`；预期全部通过。

## T26：编写独立执行三种上下文和模型隔离失败测试

**文件：** `tests/test_skill_executor.py`  
**依赖：** T21、T25

**步骤：**
1. 验证 `none` 使用空主历史，`recent` 使用选出的完整 N 轮，三种策略都只开放当前 Skill 白名单和 `load_skill`。
2. 验证 `summary` 在执行前只调用一次所选模型，`tools=[]`，固定中文提示词保留目标、约束、事实、进度和问题。
3. 验证未指定模型复用主 LLM；指定模型只替换 `LLMConfig.model`，协议、地址、凭据和其余配置不变。
4. 验证临时 AgentLoop 使用独立内存和 `EphemeralContextManager`，不调用主项目记忆或主归档。
5. 验证最终助手响应直接成为 `SkillExecutionResult.summary`，无最终响应、模型错误和上下文过大映射为稳定错误码。

**验证：** `python -m pytest tests/test_skill_executor.py -q`；预期非零退出，失败指向 `SkillExecutor` 尚未实现。

## T27：实现独立 Skill 执行器

**文件：** `src/mycode/skill/executor.py`、`src/mycode/skill/__init__.py`  
**依赖：** T26

**步骤：**
1. 按 `none/recent/summary` 构造临时历史和框架块，摘要调用使用独立所选模型且无工具。
2. 使用 `dataclasses.replace(llm_config, model=...)` 创建模型覆盖配置，共享模式不进入该路径。
3. 创建临时内存、临时上下文管理器和复用核心的 AgentLoop，以 `initial_skill_scope` 执行渲染后的 SOP。
4. 收集最终响应作为自包含摘要，不把临时工具历史合并到主历史。
5. 将预期失败转换为 `SkillExecutionResult` 稳定错误码，在历史隔离处添加简洁中文注释。

**验证：** `python -m pytest tests/test_skill_executor.py -q`；预期全部通过。

## T28：编写加载工具独立路径与递归拒绝失败测试

**文件：** `tests/test_skill_load_tool.py`  
**依赖：** T27

**步骤：**
1. 验证独立入口加载后立即调用 `SkillExecutor.execute_isolated()`，并传入父 `SkillRunContext` 与当前 Agent 模式。
2. 验证成功结果使用 `action=completed`、`mode=isolated` 和摘要，不返回临时历史。
3. 验证独立深度大于零时再次请求独立 Skill 返回稳定递归拒绝，不启动第二个临时 Agent。
4. 验证独立失败只返回必要错误码和安全消息，仍不泄漏内部异常、历史或工具输出。

**验证：** `python -m pytest tests/test_skill_load_tool.py -q -k "isolated or recursive or completed"`；预期非零退出，失败指向加载工具尚未连接执行器。

## T29：接通加载工具的独立执行路径

**文件：** `src/mycode/skill/load_tool.py`  
**依赖：** T28

**步骤：**
1. 从运行时读取当前父运行上下文，缺失上下文时返回稳定执行错误。
2. 独立 Skill 激活后调用执行器，成功返回摘要，失败映射为稳定失败 `ToolResult`。
3. 在调用前检查 `isolated_depth`，拒绝独立模式递归，不影响共享 Skill 和资源读取。
4. 保持 `load_skill` 在所有白名单下可用。

**验证：** `python -m pytest tests/test_skill_load_tool.py -q`；预期全部通过。

## T30：编写 ChatSession 两种模式和清理失败测试

**文件：** `tests/test_skill_agent.py`  
**依赖：** T27、T29

**步骤：**
1. 验证 `send_skill()` 在共享模式激活 Skill，并以该 Skill 的 `initial_skill_scope` 调用主 Agent，事件直接留在主流中。
2. 验证独立模式先取得主运行上下文，再调用执行器，不把临时事件逐条转发到主流。
3. 验证独立完成后把原始 `/{name} {arguments}` 和摘要作为一组外部 exchange 写入主 memory 与现有会话记录路径。
4. 验证独立失败产生可观察错误事件，并只记录安全摘要。
5. 验证 `clear()` 在现有 memory、模式和权限清理之外清除激活 Skill 和当前范围，不清 Catalog 缓存。

**验证：** `python -m pytest tests/test_skill_agent.py -q -k "send_skill or external_exchange or clear_skill"`；预期非零退出，失败指向 ChatSession 尚未接入 Skill。

## T31：实现 ChatSession 路由和外部摘要写回

**文件：** `src/mycode/session.py`、`src/mycode/agent/loop.py`  
**依赖：** T30

**步骤：**
1. 为 ChatSession 增加可选运行时和执行器依赖，保持未配置 Skill 时现有调用兼容。
2. 实现 `send_skill()` 的 shared/isolated 分支和审批提供者传递。
3. 实现 `AgentLoop.record_external_exchange()`，统一更新主 memory、JSONL 会话记录和既有后台记忆流程。
4. 在独立摘要写回处保留历史隔离边界，避免写入临时工具调用与结果。
5. 扩展 `clear()` 清理 Skill 状态，并保持目录缓存和磁盘文件不变。

**验证：** `python -m pytest tests/test_skill_agent.py -q`；预期全部通过。

## T32：编写动态斜杠注册、刷新与诊断失败测试

**文件：** `tests/test_skill_slash.py`、`tests/test_slash_registry.py`、`tests/test_slash_dispatcher.py`、`tests/test_slash_completion.py`  
**依赖：** T31

**步骤：**
1. 验证 `replace_dynamic_commands()` 在临时索引校验固定主名称、固定别名和所有动态名称，冲突时旧快照完整保留。
2. 验证 `SkillSlashBridge.refresh()` 按 Skill 名称生成命令，帮助说明来自 metadata，处理函数原样传递名称和 arguments 给 `controller.execute_skill()`。
3. 验证热更新新增、修改、删除 Skill 后帮助、补全和分发共享同一个原子快照。
4. 验证 Dispatcher 在解析命令前调用刷新并把新诊断展示一次；Completer 只静默刷新并缓存诊断供下次分发展示。
5. 验证固定命令永不被 Skill 覆盖，未知命令仍沿用现有错误行为。

**验证：** `python -m pytest tests/test_skill_slash.py tests/test_slash_registry.py tests/test_slash_dispatcher.py tests/test_slash_completion.py -q`；预期非零退出，失败指向动态斜杠接口尚未实现。

## T33：实现动态斜杠基础设施与 SkillSlashBridge

**文件：** `src/mycode/skill/slash.py`、`src/mycode/slash/__init__.py`、`src/mycode/slash/controller.py`、`src/mycode/slash/registry.py`、`src/mycode/slash/dispatcher.py`、`src/mycode/slash/completion.py`  
**依赖：** T32

**步骤：**
1. 为控制协议增加 `execute_skill()`，由动态处理函数只做名称和参数转发。
2. 在注册表中分开固定和动态命令，先验证临时索引再一次性交换动态快照。
3. 实现 `SkillSlashBridge.refresh()` 和 `refresh_silent()`，对目录代次与诊断展示状态做最小缓存。
4. 给 Dispatcher 和 Completer 注入刷新回调，保证刷新发生在解析或补全读取注册表之前。
5. 更新公共导出，保持原固定命令调用方无需了解 Skill 内部类型。

**验证：** `python -m pytest tests/test_skill_slash.py tests/test_slash_registry.py tests/test_slash_dispatcher.py tests/test_slash_completion.py -q`；预期全部通过。

## T34：编写内置 Skill、review 迁移与 TUI 失败测试

**文件：** `tests/test_skill_docs.py`、`tests/test_slash_builtins.py`、`tests/test_slash_e2e.py`、`tests/test_slash_snapshots.py`、`tests/test_slash_tui.py`  
**依赖：** T33

**步骤：**
1. 验证内置目录只包含 `commit/review/test` 三个能力包，入口均可走普通 Loader，description 使用中文且正文包含字面量 `{{arguments}}`。
2. 验证 commit、review 为 shared，test 为 isolated/recent/3，白名单精确等于 `plan.md` 的定义。
3. 删除测试对 `REVIEW_PROMPT` 和固定 `/review` handler 的依赖，改为动态 review Skill 调用 `send_skill("review", arguments)`。
4. 验证 TUI 的 `execute_skill()` 调用 Session、传入现有审批提供者并复用普通 AgentEvent 渲染。
5. 更新帮助、快照和端到端预期：动态 `/commit`、`/review`、`/test` 可见，隐藏固定命令行为保持不变。

**验证：** `python -m pytest tests/test_skill_docs.py tests/test_slash_builtins.py tests/test_slash_e2e.py tests/test_slash_snapshots.py tests/test_slash_tui.py -q`；预期非零退出，失败指向内置文件和 TUI 接口尚未完成。

## T35：添加内置 Skill，迁移 review 并接入 TUI

**文件：** `src/mycode/skill/builtins/commit/SKILL.md`、`src/mycode/skill/builtins/review/SKILL.md`、`src/mycode/skill/builtins/test/SKILL.md`、`src/mycode/slash/__init__.py`、`src/mycode/slash/builtins.py`、`src/mycode/tui.py`  
**依赖：** T34

**步骤：**
1. 按统一格式编写三个简洁 SOP，使用中文说明、精确白名单、既定执行模式和 `{{arguments}}`。
2. 删除硬编码 review 常量、handler 和固定命令注册，不保留旁路。
3. 实现 TUI `execute_skill()`，通过 `ChatSession.send_skill()` 获取事件并调用现有流渲染逻辑。
4. 调整现有 Stage 09 测试夹具，使动态桥接由测试显式装配，不访问真实用户目录。

**验证：** `python -m pytest tests/test_skill_docs.py tests/test_slash_builtins.py tests/test_slash_e2e.py tests/test_slash_snapshots.py tests/test_slash_tui.py -q`；预期全部通过。

## T36：以 TDD 完成 CLI 启动装配、fail-fast 与包数据

**文件：** `tests/test_skill_cli.py`、`tests/test_slash_cli.py`、`src/mycode/cli.py`、`pyproject.toml`  
**依赖：** T35

**步骤：**
1. 先测试启动顺序：本地、上下文、记忆和 MCP 工具全部注册后，创建 Catalog、Runtime 与 Executor，注册 `load_skill`，再初始化 Catalog 并装配动态命令、Agent、Session 和 TUI。
2. 先测试项目根为 `<cwd>/.mycode/skills`、用户根为 `<home>/.mycode/skills`、内置根通过包资源解析，所有组件共享同一 Runtime/Catalog 实例。
3. 先测试未知工具和固定命令冲突使 CLI 返回 `1`，stderr 含 Skill 名称、无效项和中文定位，但不含正文或凭据。
4. 先测试 `pyproject.toml` 声明 `mycode.skill` 的 `builtins/*/SKILL.md` 包数据；运行测试确认现有装配失败。
5. 最小修改 CLI 装配和异常捕获，向 SkillExecutor 传入主 LLM 配置、`create_llm` 工厂、权限、工具和 Agent 配置。
6. 更新包数据声明并重跑测试，确保 MCP 延迟工具注册完成后再校验白名单。

**验证：** `python -m pytest tests/test_skill_cli.py tests/test_slash_cli.py -q`；预期最终全部通过。

## T37：完成端到端场景、README 和全量回归

**文件：** `tests/test_skill_e2e.py`、`tests/test_skill_docs.py`、`README.md`  
**依赖：** T36

**步骤：**
1. 用 fake LLM 完成共享 Skill 场景：首次只见目录摘要，模型调用 `load_skill`，下一轮持续看到 SOP 且工具被白名单收窄，最终历史保留主执行记录。
2. 完成 `/test` 独立场景：只携带最近 3 个完整轮次，临时工具详情不进入主历史，最终摘要作为外部 exchange 回流。
3. 完成热更新场景：使用前修改高优先级 Skill 后命令和激活 SOP 更新；未知工具的新版本被拒绝并继续使用最后有效版本。
4. 完成 `/clear` 场景：激活和范围被清除，下一轮仍能从目录摘要重新发现 Skill。
5. 先在 `test_skill_docs.py` 中断言 README 必须说明三级目录、字段、两阶段加载、两种模式、三种独立上下文、白名单、热更新和不做范围；确认失败后补齐 README。
6. 运行新增 Skill 测试、现存 Stage 09 斜杠测试、全量测试和源码编译检查；任何失败先修复并重跑对应范围。

**验证：** 依次运行 `python -m pytest tests/test_skill_e2e.py tests/test_skill_docs.py -q`、`python -m pytest -q`、`python -m compileall -q src`；预期三个命令退出码均为 `0`。

## 执行顺序

```text
T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8 → T9 → T10 → T11 → T12
                                                        │
                                                        ▼
T13 → T14 → T15 → T16 ──┬──→ T17 ──────────────────────┐
                         ├──→ T18 → T19 ────────────────┤
                         └──→ T20 → T21 ────────────────┤
                                                       ▼
T22 → T23 → T24 → T25 → T26 → T27 → T28 → T29 → T30 → T31
                                                       │
                                                       ▼
T32 → T33 → T34 → T35 → T36 → T37
```

T17、T18 和 T20 在 T16 后可按文件边界并行准备，但 T22 必须等待 T17、T19、T21 全部通过。其余任务按编号顺序执行，避免多个任务同时修改 `AgentLoop`、斜杠注册表或 CLI 装配。

## 建议提交分组

| 提交 | 任务 | 内容 | 提交前验证 |
|---|---|---|---|
| C1 | T1-T12 | 模型、Loader、Catalog 与热更新 | `python -m pytest tests/test_skill_models.py tests/test_skill_loader.py tests/test_skill_catalog.py -q` |
| C2 | T13-T21 | Runtime、工具范围、加载工具共享路径和上下文适配 | `python -m pytest tests/test_skill_runtime.py tests/test_skill_load_tool.py tests/test_skill_context.py tests/test_skill_agent.py -q` |
| C3 | T22-T31 | AgentLoop、独立执行器和 Session 路由 | `python -m pytest tests/test_skill_agent.py tests/test_skill_executor.py tests/test_skill_load_tool.py -q` |
| C4 | T32-T35 | 动态斜杠、内置 Skill、review 迁移和 TUI | `python -m pytest tests/test_skill_slash.py tests/test_slash_builtins.py tests/test_slash_completion.py tests/test_slash_dispatcher.py tests/test_slash_registry.py tests/test_slash_snapshots.py tests/test_slash_tui.py tests/test_slash_e2e.py -q` |
| C5 | T36-T37 | CLI、包数据、文档、端到端和回归 | 依次运行 `python -m pytest -q`、`python -m compileall -q src` |

每次提交前先运行 `git status --short`，只暂存该提交对应的 Stage 10 文件；不得把用户已有的示例配置修改或已删除旧测试带入提交。

## 覆盖自检

| Plan 组件或需求 | 任务归属 |
|---|---|
| `skill.models` 与 F1 元信息 | T1-T2 |
| `skill.loader`、目录型包、资源安全与有界加载 | T3-T8 |
| `skill.catalog`、三级覆盖、启动校验和热更新 | T9-T12 |
| `skill.runtime`、参数替换、两阶段 Prompt、多激活和清理 | T13-T16 |
| 工具白名单、延迟工具展开、串行加载和执行前二次校验 | T17、T24-T25 |
| 系统级 `load_skill`、资源读取和共享范围标记 | T18-T19、T28-T29 |
| 最近完整轮次、摘要上下文和临时预算 | T20-T21、T26-T27 |
| AgentLoop 父上下文、持续注入与外部 exchange | T22-T25、T30-T31 |
| 独立模式、三种上下文、指定模型和历史隔离 | T26-T31 |
| 动态斜杠、帮助、补全、热更新诊断和固定冲突 | T32-T33 |
| commit/review/test 内置样板与 review 迁移 | T34-T35 |
| CLI 装配、MCP 后校验、包数据和错误报告 | T36 |
| 端到端行为、README、兼容回归和不做范围 | T37 |

依赖链无环；`plan.md` 中每个新增组件和所有现有接入文件均至少由一个任务覆盖。每个任务都有可执行验证命令，接口名称与已批准的技术设计一致，未引入市场分发、版本管理或远程安装工作。
