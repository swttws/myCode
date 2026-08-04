# myCode Stage 13：子 Agent Worktree 隔离 Tasks

> 本文档只拆解已经批准的 `spec.md` 与 `plan.md`。四份文档全部批准后，实施者使用 `superpowers:test-driven-development` 按任务顺序执行；需要并行执行时使用 `superpowers:subagent-driven-development`，否则使用 `superpowers:executing-plans`。每个任务均为约 2–5 分钟的聚焦工作单元。

## 目标与执行约束

- 目标：让声明 `isolation: worktree` 的定义式子 Agent 使用安全、可恢复、可清理的独立 Git Worktree，并让所有路径消费者显式使用任务工作区。
- 技术栈：Python 3.10+、`asyncio`、`pathlib`、`subprocess`、PyYAML、pytest、Git Worktree。
- 禁止在本阶段加入自动合并、推送、远端创建、强制清理入口、Fork 隔离或多 Agent 编排。
- 禁止使用 `chdir`；Git 和工具命令必须传显式 `cwd`。
- 每个实现任务只做使对应失败测试通过的最小改动；不得顺手重构无关模块。
- 每个提交点只暂存任务列出的文件，先执行 `git diff --cached --check` 再提交。
- 当前基线注意：编写本文档时，测试收集因 `src/mycode/subagent/rendering.py` 在当前 `HEAD` 中缺失而失败。该既有问题不属于 Stage 13；进入开发前必须在独立范围恢复可收集基线，不能把修复混入本阶段提交。

## 文件清单

### 新建生产与示例文件

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `src/mycode/workspace/__init__.py` | 导出通用工作区模型 |
| 新建 | `src/mycode/workspace/models.py` | 工作区种类、任务身份、上下文、租约和准备方式 |
| 新建 | `src/mycode/worktree/__init__.py` | 导出 Worktree 领域公开接口 |
| 新建 | `src/mycode/worktree/models.py` | 配置、Git、元数据、保护、诊断和清理结果模型 |
| 新建 | `src/mycode/worktree/pathing.py` | 安全名称、真实路径和边界检查 |
| 新建 | `src/mycode/worktree/config.py` | `.mycode/worktree.yaml` 加载和严格校验 |
| 新建 | `src/mycode/worktree/git.py` | 结构化 Git 子进程与机器格式解析 |
| 新建 | `src/mycode/worktree/metadata.py` | sidecar 有界读取、原子写入、扫描和删除 |
| 新建 | `src/mycode/worktree/initializer.py` | `copy`、`ignored_copy`、`symlink`、`hooks` 初始化 |
| 新建 | `src/mycode/worktree/protection.py` | 未提交修改和未推送提交检测 |
| 新建 | `src/mycode/worktree/manager.py` | 创建、恢复、回滚、释放、处置和并发锁 |
| 新建 | `src/mycode/worktree/cleaner.py` | 启动扫描、周期调度、三层过滤和批次限制 |
| 新建 | `src/mycode/subagent/isolation.py` | 共享与 Worktree 租约协调入口 |
| 新建 | `examples/mycode.worktree.yaml` | 完整且无凭据的项目配置示例 |

### 修改生产与文档文件

| 操作 | 文件 | 职责 |
|---|---|---|
| 修改 | `src/mycode/subagent/__init__.py` | 导出新增隔离和任务状态类型 |
| 修改 | `src/mycode/subagent/models.py` | 角色隔离声明和任务工作区状态 |
| 修改 | `src/mycode/subagent/loader.py` | 解析 `isolation` frontmatter |
| 修改 | `src/mycode/subagent/tasks.py` | 预留身份、绑定租约、活动登记和处置结果 |
| 修改 | `src/mycode/subagent/service.py` | 两阶段准备与调度 |
| 修改 | `src/mycode/subagent/runtime.py` | 按租约构造并统一释放运行时 |
| 修改 | `src/mycode/subagent/tooling.py` | 按工作区构造与过滤工具 |
| 修改 | `src/mycode/subagent/context.py` | Worktree 路径提示和项目指令重载 |
| 修改 | `src/mycode/subagent/tool.py` | `Agent(action=list/get)` 状态序列化 |
| 修改 | `src/mycode/subagent/notifications.py` | 后台通知中的路径与处置结果 |
| 修改 | `src/mycode/tool/__init__.py` | 导出工具工作区类型 |
| 修改 | `src/mycode/tool/base.py` | 工具工作区能力和调用上下文 |
| 修改 | `src/mycode/tool/executor.py` | 每次调用显式传播工作区上下文 |
| 修改 | `src/mycode/tool/defaults.py` | 按工作区创建默认工具 |
| 修改 | `src/mycode/tool/filesystem.py` | 文件工具绑定根目录并校验上下文 |
| 修改 | `src/mycode/tool/command.py` | 显式 cwd、超时和 hooks 环境覆盖 |
| 修改 | `src/mycode/agent/loop.py` | 注入 `WorkspaceContext`，消除生产路径的 `Path.cwd()` |
| 修改 | `src/mycode/hook/runtime.py` | 按调用上下文选择工作区 |
| 修改 | `src/mycode/hook/actions.py` | 基于 Hook 上下文解析 action cwd |
| 修改 | `src/mycode/skill/executor.py` | 在任务工作区加载并执行 Skill |
| 修改 | `src/mycode/mcp/tools.py` | MCP 工具默认标记 `SHARED_ONLY` |
| 修改 | `src/mycode/slash/builtins.py` | `/tasks` 与 `/task` 展示隔离和处置状态 |
| 修改 | `src/mycode/cli.py` | Worktree 领域装配与清理器生命周期 |
| 修改 | `README.md` | Stage 13 配置、角色声明和安全边界说明 |

### 新建测试文件

| 操作 | 文件 | 覆盖 |
|---|---|---|
| 新建 | `tests/worktree_helpers.py` | 临时仓库、bare remote、隔离 Git 环境、fake 时钟和调度 |
| 新建 | `tests/test_workspace_models.py` | 通用工作区模型不变量 |
| 新建 | `tests/test_worktree_models.py` | Worktree 领域模型不变量 |
| 新建 | `tests/test_worktree_pathing.py` | 名称和路径边界 |
| 新建 | `tests/test_worktree_config.py` | 配置加载、摘要和失败关闭 |
| 新建 | `tests/test_worktree_git.py` | Git 网关、NUL 解析、超时和输出上限 |
| 新建 | `tests/test_worktree_metadata.py` | sidecar 生命周期和只读恢复 |
| 新建 | `tests/test_worktree_initializer.py` | 四类初始化规则 |
| 新建 | `tests/test_worktree_protection.py` | 修改和提交保护决策 |
| 新建 | `tests/test_worktree_manager.py` | 创建、恢复、释放、回滚和并发 |
| 新建 | `tests/test_worktree_cleaner.py` | 三层过滤、周期和批量限制 |
| 新建 | `tests/test_worktree_e2e.py` | Worktree 领域端到端场景 |
| 新建 | `tests/test_subagent_isolation.py` | 子 Agent 隔离协调器 |

### 修改测试文件

| 文件 | 改动 |
|---|---|
| `tests/helpers.py` | 增加共享 `WorkspaceContext` 测试工厂 |
| `tests/test_subagent_models.py`、`tests/test_subagent_loader.py` | 角色 isolation 和任务状态字段 |
| `tests/test_subagent_notifications.py`、`tests/test_subagent_tool.py` | 父 Agent 和通知状态出口 |
| `tests/test_subagent_tasks.py`、`tests/test_subagent_service.py` | 两阶段任务身份、活动登记和失败路径 |
| `tests/test_subagent_runtime.py`、`tests/test_subagent_tooling.py`、`tests/test_subagent_context.py` | 租约、工具、Prompt 与释放 |
| `tests/test_subagent_agent.py`、`tests/test_subagent_e2e.py`、`tests/test_subagent_session_tui.py` | 子 Agent 集成与 UI 回归 |
| `tests/test_agent_loop.py`、`tests/test_agent_plan_only.py`、`tests/test_context_compaction_e2e.py` | AgentLoop 显式工作区调用点 |
| `tests/test_tool_executor.py`、`tests/test_tool_filesystem.py`、`tests/test_tool_command.py`、`tests/test_tool_registry.py` | 工具能力、上下文、cwd 和 hooks |
| `tests/test_tool_cache.py`、`tests/test_memory_paths.py`、`tests/test_memory_instructions.py` | 绝对路径缓存、Memory 和项目指令隔离 |
| `tests/test_hook_runtime.py`、`tests/test_hook_actions.py`、`tests/test_hook_agent.py`、`tests/test_hook_session_cli.py` | Hook 动态工作区和主流程回归 |
| `tests/test_skill_executor.py`、`tests/test_skill_agent.py`、`tests/test_skill_e2e.py` | Skill 工作区传播 |
| `tests/test_mcp_tools.py` | MCP 默认共享范围 |
| `tests/test_permission_e2e.py`、`tests/test_project_memory_e2e.py` | 权限和项目 Memory 工作区回归 |
| `tests/test_slash_builtins.py`、`tests/test_slash_snapshots.py` | 任务状态显示和快照回归 |
| `tests/test_e2e_chat.py`、`tests/test_docs.py` | 启动清理生命周期和文档示例 |

## 任务列表

## T00：确认可收集的开发基线

**文件：** 无

**依赖：** 无

**步骤：**

- [ ] 运行 `pytest --collect-only -q`，确认测试模块全部可以导入。
- [ ] 若仍出现 `ModuleNotFoundError: mycode.subagent.rendering` 或其他 Stage 13 之前的错误，停止本阶段开发并在独立范围恢复基线。
- [ ] 运行 `git status --short`，记录并保留所有用户现有改动，后续提交不得包含它们。

**验证：** `pytest --collect-only -q` 退出码为 0；`git status --short` 中没有由 Stage 13 任务意外产生的文件。

## T01：写通用工作区模型失败测试

**文件：** 新建 `tests/test_workspace_models.py`

**依赖：** T00

**步骤：**

- [ ] 添加 `test_shared_workspace_context_rejects_worktree_identity_fields`，构造共享上下文携带分支时应抛出 `ValueError`。
- [ ] 添加 `test_worktree_context_requires_identity_branch_and_absolute_roots`，分别覆盖缺少身份、缺少分支和相对路径。
- [ ] 添加 `test_workspace_lease_is_frozen_and_preserves_preparation`，验证冻结模型及 `SHARED/CREATED/RECOVERED` 值。

**验证：** `pytest tests/test_workspace_models.py -q` 失败，原因为 `mycode.workspace` 尚不存在。

## T02：实现通用工作区模型

**文件：** 新建 `src/mycode/workspace/models.py`、`src/mycode/workspace/__init__.py`

**依赖：** T01

**步骤：**

- [ ] 按 plan 定义 `WorkspaceKind`、`WorkspacePreparation`、`WorkspaceTaskIdentity`、`WorkspaceContext` 和 `WorkspaceLease`，全部使用冻结 dataclass 或字符串枚举。
- [ ] 在 `__post_init__` 校验绝对路径、共享/Worktree 字段组合、非空仓库身份、受控任务字段和 hooks 路径。
- [ ] 从 `workspace.__init__` 明确导出五个公共类型，不导入 `subagent` 或 `worktree`。

**验证：** `pytest tests/test_workspace_models.py -q` 全部通过。

## T03：写 Worktree 领域模型失败测试

**文件：** 新建 `tests/test_worktree_models.py`

**依赖：** T02

**步骤：**

- [ ] 参数化验证 `WorktreeRuleType`、`WorktreePhase`、`WorktreeDisposition` 的精确字符串值。
- [ ] 验证 `WorktreeConfig` 的默认 30 秒、3600 秒、604800 秒和批量 64，并拒绝空摘要和超范围值。
- [ ] 验证元数据、Git 状态、保护状态、初始化结果、诊断和批处理结果均不可变且字段组合合法。

**验证：** `pytest tests/test_worktree_models.py -q` 失败，原因为 `mycode.worktree.models` 尚不存在。

## T04：实现 Worktree 领域模型与错误

**文件：** 新建 `src/mycode/worktree/models.py`、`src/mycode/worktree/__init__.py`

**依赖：** T03

**步骤：**

- [ ] 按 plan 定义规则、配置、仓库身份、Git entry/status、阶段元数据、初始化、保护、处置、诊断和清理批次模型。
- [ ] 定义带稳定 `code`、`phase`、中文 `message`、可选路径/分支/Git 退出码的 `WorktreeError`，错误文本不得拼入完整 stdout/stderr。
- [ ] 校验 UTC 时间戳、绝对路径、OID/分支非空、非负计数和不可变元组；从包入口只导出公开类型。

**验证：** `pytest tests/test_worktree_models.py -q` 全部通过。

## T05：提交工作区与领域模型

**文件：** T01–T04 的四个生产文件和两个测试文件

**依赖：** T04

**步骤：**

- [ ] 运行 `pytest tests/test_workspace_models.py tests/test_worktree_models.py -q`。
- [ ] 仅暂存上述六个文件并运行 `git diff --cached --check`。
- [ ] 提交 `feat: add workspace and worktree domain models`。

**验证：** 两个测试文件全部通过；`git show --name-only --format= HEAD` 只列出 T01–T04 文件。

## T06：写安全名称失败测试

**文件：** 新建 `tests/test_worktree_pathing.py`

**依赖：** T05

**步骤：**

- [ ] 添加合法边界：1/64 字符段、200 字符总长、点/下划线/连字符只出现在段中间、ASCII 多段。
- [ ] 添加非法表：空文本、空段、`.`、`..`、首尾标点、65 字符段、201 字符整体、反斜杠、绝对路径、盘符、控制字符、非 ASCII。
- [ ] 参数化 Windows 保留名及带扩展名、大小写变体，并验证分支只能由安全段生成且固定在 `mycode/worktree/` 前缀。

**验证：** `pytest tests/test_worktree_pathing.py -q` 失败，原因为 `WorktreePathPolicy` 尚不存在。

## T07：实现安全名称和分支校验

**文件：** 新建 `src/mycode/worktree/pathing.py`

**依赖：** T06

**步骤：**

- [ ] 实现 `validate_relative_name()`，先按 `/` 分段再执行 ASCII、长度、首尾、保留名和控制字符检查。
- [ ] 实现 `validate_branch_name()`，只接受由受控前缀与已校验相对名称构成的引用，不接受调用方任意 Git ref。
- [ ] 所有失败抛出稳定 `WorktreeError`，消息包含字段位置但不回显无界输入。

**验证：** `pytest tests/test_worktree_pathing.py -q -k "name or branch"` 全部通过。

## T08：写真实路径边界失败测试

**文件：** 修改 `tests/test_worktree_pathing.py`

**依赖：** T07

**步骤：**

- [ ] 验证仓库根、`.worktrees` 根、目标和元数据路径都返回规范化绝对路径。
- [ ] 验证仓库外路径、`..`、指向仓库外的符号链接，以及可用时的 Windows directory junction/reparse point 被拒绝。
- [ ] 验证规则来源只能在主仓库内、规则目标只能在目标 Worktree 内，重复解析后替换祖先节点会再次失败。

**验证：** `pytest tests/test_worktree_pathing.py -q -k "boundary or symlink or source or target"` 失败，原因为边界方法尚未实现。

## T09：实现真实路径与规则边界

**文件：** 修改 `src/mycode/worktree/pathing.py`

**依赖：** T08

**步骤：**

- [ ] 实现 `validate_root()`、`resolve_target()`、`assert_target_boundary()`、`resolve_rule_source()` 和 `resolve_rule_target()`。
- [ ] 使用规范化绝对真实路径和平台大小写语义比较共同路径；逐个检查现有祖先，不跟随越界链接或联接。
- [ ] 每个公开动作都重新调用边界检查，不能依赖首次校验后的字符串结果。

**验证：** `pytest tests/test_worktree_pathing.py -q` 全部通过。

## T10：写配置默认值与合法规则失败测试

**文件：** 新建 `tests/test_worktree_config.py`

**依赖：** T09

**步骤：**

- [ ] 验证配置文件缺失时得到版本 1、空规则及 plan 中四个默认值。
- [ ] 写包含 `copy`、`ignored_copy`、`symlink`、`hooks` 的合法 YAML，验证声明顺序、来源/目标和稳定非空摘要。
- [ ] 验证同一语义配置的 key 顺序变化不改变摘要，规则顺序变化会改变摘要。

**验证：** `pytest tests/test_worktree_config.py -q` 失败，原因为 `WorktreeConfigLoader` 尚不存在。

## T11：实现配置加载与规范化摘要

**文件：** 新建 `src/mycode/worktree/config.py`

**依赖：** T10

**步骤：**

- [ ] 实现 `WorktreeConfigLoader.load(repository_root)`，固定读取 `.mycode/worktree.yaml`，缺失时返回默认配置。
- [ ] 严格解析 `version`、`git_timeout_seconds`、`cleanup` 和 `rules`，拒绝 bool 冒充数值。
- [ ] 将有效配置序列化为稳定字段顺序和 UTF-8 文本后计算 SHA-256 摘要。

**验证：** `pytest tests/test_worktree_config.py -q -k "default or valid or digest"` 全部通过。

## T12：写非法配置和目标冲突失败测试

**文件：** 修改 `tests/test_worktree_config.py`

**依赖：** T11

**步骤：**

- [ ] 覆盖未知顶层/规则字段、未知版本/规则、非 mapping、空 source/target、绝对路径和越界来源。
- [ ] 覆盖 129 条规则、513 字符路径、Git 超时小于 1 或大于 120、非正清理时间、批量大于 64。
- [ ] 覆盖完全重复目标、大小写等价目标、祖先/后代目标冲突、来源符号链接循环和多条 hooks 规则。

**验证：** `pytest tests/test_worktree_config.py -q -k "invalid or unknown or limit or conflict or cycle"` 失败，原因为严格校验未完整实现。

## T13：实现配置失败关闭规则

**文件：** 修改 `src/mycode/worktree/config.py`

**依赖：** T12

**步骤：**

- [ ] 添加未知字段拒绝、精确类型检查、128/512/1–120/64 上限和中文位置诊断。
- [ ] 使用 `WorktreePathPolicy` 校验规则文本与真实来源，按平台规范化目标后检测重复、祖先/后代冲突及多 hooks 冲突。
- [ ] 遍历来源链接链并拒绝循环或越界，保证全部错误在应用装配阶段产生。

**验证：** `pytest tests/test_worktree_config.py -q` 全部通过。

## T14：提交路径与配置

**文件：** `src/mycode/worktree/pathing.py`、`src/mycode/worktree/config.py`、`tests/test_worktree_pathing.py`、`tests/test_worktree_config.py`

**依赖：** T13

**步骤：**

- [ ] 运行两个测试文件并确认无 skip 以外失败。
- [ ] 仅暂存四个文件并运行 `git diff --cached --check`。
- [ ] 提交 `feat: validate worktree paths and configuration`。

**验证：** `pytest tests/test_worktree_pathing.py tests/test_worktree_config.py -q` 全部通过。

## T15：建立隔离 Git 测试夹具

**文件：** 新建 `tests/worktree_helpers.py`

**依赖：** T14

**步骤：**

- [ ] 实现创建临时普通仓库和本地 bare remote 的 helper，只设置仓库本地 `user.name`、`user.email`。
- [ ] helper 返回显式 Git 环境，设置临时 `HOME`、`GIT_CONFIG_GLOBAL` 和 `GIT_CONFIG_NOSYSTEM`，不读取真实用户配置。
- [ ] 实现结构化 `run_git(args, cwd, env)` 测试辅助和可记录调用的 fake runner，不使用 shell 字符串。

**验证：** `python -m py_compile tests/worktree_helpers.py` 退出码为 0；T16 首个测试将实际调用 helper 创建普通仓库和 bare remote。

## T16：写 Git 进程边界失败测试

**文件：** 新建 `tests/test_worktree_git.py`

**依赖：** T15

**步骤：**

- [ ] 验证网关向 runner 传参数数组、显式 cwd、`shell=False` 和配置超时，不允许命令字符串。
- [ ] fake runner 返回超过 64 KiB 的 stdout/stderr，验证各自截断且诊断不超过 4 KiB。
- [ ] 模拟 timeout、非零退出码、不可解码字节和 Git 不存在，断言稳定错误码、阶段和中文摘要。

**验证：** `pytest tests/test_worktree_git.py -q -k "runner or timeout or output or missing"` 失败，原因为 `GitWorktreeGateway` 尚不存在。

## T17：实现有界 Git 子进程执行器

**文件：** 新建 `src/mycode/worktree/git.py`

**依赖：** T16

**步骤：**

- [ ] 在网关内部实现唯一 `_run(args, cwd, env_overrides=None)`，固定 `shell=False`、捕获 bytes、检查退出码和超时。
- [ ] 对 stdout/stderr 分别保留最多 64 KiB，安全解码后只生成最多 4 KiB 的错误摘要。
- [ ] 将 timeout、missing executable 和 nonzero exit 映射为不同稳定 `WorktreeError.code`，不记录环境变量值。

**验证：** `pytest tests/test_worktree_git.py -q -k "runner or timeout or output or missing"` 全部通过。

## T18：写仓库身份与 Worktree 列表解析失败测试

**文件：** 修改 `tests/test_worktree_git.py`

**依赖：** T17

**步骤：**

- [ ] 用临时仓库验证 `identify_repository()` 返回 `RepositoryIdentity`，包含真实仓库根、common directory 和稳定 SHA-256 身份。
- [ ] 参数化 `git worktree list --porcelain -z` 的普通、detached、locked、prunable、多 entry 和路径含空格输出，结果逐项为 `GitWorktreeEntry`。
- [ ] 验证缺字段、重复字段、非法 OID、非绝对路径和越界 common directory 解析失败关闭。

**验证：** `pytest tests/test_worktree_git.py -q -k "identity or porcelain or list"` 失败，原因为身份和 parser 尚未实现。

## T19：实现仓库识别、忽略验证和列表解析

**文件：** 修改 `src/mycode/worktree/git.py`

**依赖：** T18

**步骤：**

- [ ] 实现 `identify_repository()`，分别取得 top-level 与 git-common-dir，规范化后生成仓库身份。
- [ ] 实现 `validate_ignored_root()`，用 `git check-ignore` 确认 `.worktrees/` 已忽略，失败时不修改 ignore 文件。
- [ ] 实现严格 NUL parser 和 `list_porcelain()`，不解析本地化人类输出。

**验证：** `pytest tests/test_worktree_git.py -q -k "identity or ignored or porcelain or list"` 全部通过。

## T20：写状态与 upstream 保护所需 Git 测试

**文件：** 修改 `tests/test_worktree_git.py`

**依赖：** T19

**步骤：**

- [ ] 创建暂存、未暂存、未跟踪、忽略文件和重命名状态，验证 porcelain v2 `-z` 解析分类。
- [ ] 验证有/无 upstream 的读取；本地 bare remote 场景验证 `commits_not_in_upstream()` 的确定顺序。
- [ ] 对 malformed status、missing branch、missing upstream ref 和 Git 错误验证失败关闭。

**验证：** `pytest tests/test_worktree_git.py -q -k "status or upstream or commits"` 失败，原因为状态方法尚未实现。

## T21：实现状态、upstream 和提交差集

**文件：** 修改 `src/mycode/worktree/git.py`

**依赖：** T20

**步骤：**

- [ ] 实现 `status()` 的 porcelain v2 NUL 解析，忽略 ignored 项但保留 staged、unstaged 和 untracked 标志。
- [ ] 实现 `upstream()`，只把明确的“未配置 upstream”转换为 `None`，其他错误继续失败。
- [ ] 实现 `commits_not_in_upstream()`，只读取本地引用，不调用 fetch 或网络命令。

**验证：** `pytest tests/test_worktree_git.py -q -k "status or upstream or commits"` 全部通过。

## T22：写 Git Worktree 生命周期失败测试

**文件：** 修改 `tests/test_worktree_git.py`

**依赖：** T21

**步骤：**

- [ ] 验证 `capture_head()` 返回 commit OID，主工作区 dirty 时仍只捕获已提交 HEAD。
- [ ] 验证 `add()` 使用 `worktree add -b <branch> <path> <base-oid>` 并产生独立分支和目录。
- [ ] 验证 `remove()` 不传 `--force`；`delete_branch()` 仅接受完全匹配租约且处于受控前缀的本地分支。

**验证：** `pytest tests/test_worktree_git.py -q -k "capture or add or remove or delete_branch"` 失败，原因为生命周期方法尚未实现。

## T22A：写 pushed-but-unmerged 临时分支删除测试

**文件：** 修改 `tests/test_worktree_git.py`

**依赖：** T22

**步骤：**

- [ ] 构造提交已被 upstream 包含但未合并到主分支的本地 bare remote 场景。
- [ ] 验证 manager 保护前置条件通过后内部临时分支删除完成，同时没有公开绕过保护的入口。

**验证：** `pytest tests/test_worktree_git.py -q -k "pushed and unmerged"` 失败，原因为受控内部删除尚未实现。

## T23：实现 Git Worktree 生命周期方法

**文件：** 修改 `src/mycode/worktree/git.py`

**依赖：** T22A

**步骤：**

- [ ] 实现 `capture_head()` 并验证返回值是 commit OID，不读取或复制工作区 dirty 内容。
- [ ] 实现结构化 `add()` 与非 force `remove()`，每次调用前校验绝对 cwd、目标和受控分支。
- [ ] 实现受保护的 `delete_branch()`：manager 保护检查通过后内部使用 `git branch -D` 删除临时分支；分支名称不处于受控前缀或与租约身份不匹配时拒绝。

**验证：** `pytest tests/test_worktree_git.py -q` 全部通过。

## T24：提交 Git 网关与测试夹具

**文件：** `src/mycode/worktree/git.py`、`tests/worktree_helpers.py`、`tests/test_worktree_git.py`

**依赖：** T23

**步骤：**

- [ ] 运行 Git 网关完整测试两次，确认不依赖运行顺序和真实用户配置。
- [ ] 仅暂存三个文件并运行 `git diff --cached --check`。
- [ ] 提交 `feat: add bounded git worktree gateway`。

**验证：** `pytest tests/test_worktree_git.py -q` 连续两次全部通过，临时仓库之外没有分支或文件变化。

## T25：写元数据编码与上限失败测试

**文件：** 新建 `tests/test_worktree_metadata.py`

**依赖：** T24

**步骤：**

- [ ] 构造完整 `CREATING/READY/RETAINED` 元数据，验证固定 schema version、UTC ISO 8601、身份字段和稳定 JSON。
- [ ] 覆盖未知/重复字段、错误类型、naive datetime、相对路径、非法阶段和超过 64 KiB 文件。
- [ ] 验证错误只包含元数据路径和稳定中文摘要，不回显整个 JSON 或保留原因正文。

**验证：** `pytest tests/test_worktree_metadata.py -q -k "schema or size or encode or decode"` 失败，原因为 `WorktreeMetadataStore` 尚不存在。

## T26：实现有界元数据读写与原子替换

**文件：** 新建 `src/mycode/worktree/metadata.py`

**依赖：** T25

**步骤：**

- [ ] 实现严格 JSON 编解码，读取前先检查文件大小且最多读取 64 KiB，拒绝未知/重复字段。
- [ ] 实现 `write()`：在同一元数据目录写临时文件、flush 后用原子替换发布，不把元数据放入 Worktree。
- [ ] 每次读写前用路径策略重验 `.worktrees/.metadata/<role>/<token>.json` 边界。

**验证：** `pytest tests/test_worktree_metadata.py -q -k "schema or size or encode or decode or atomic"` 全部通过。

## T27：写 READY 恢复、候选扫描和删除失败测试

**文件：** 修改 `tests/test_worktree_metadata.py`

**依赖：** T26

**步骤：**

- [ ] 验证 `read_ready()` 只接受仓库、任务、目标、分支、基线、配置摘要全部匹配的 `READY`。
- [ ] 验证 `read_candidate()` 可以读取合法三种阶段，但不把 `CREATING/RETAINED` 视为可恢复。
- [ ] 创建 65 个乱序 sidecar，验证 `scan(64)` 稳定排序且只返回 64 个；删除只移除精确 sidecar 和空父目录。

**验证：** `pytest tests/test_worktree_metadata.py -q -k "ready or candidate or scan or remove"` 失败，原因为恢复和扫描方法尚未实现。

## T28：实现恢复读取、扫描与安全删除

**文件：** 修改 `src/mycode/worktree/metadata.py`

**依赖：** T27

**步骤：**

- [ ] 实现 `read_ready(identity, target, config_digest)` 的逐字段比较和确定诊断顺序，不执行 Git 或写操作。
- [ ] 实现 `read_candidate(metadata_path)`，只验证元数据自身，不跳过后续清理过滤。
- [ ] 实现有界稳定 `scan(limit)` 和精确 `remove(identity)`，拒绝链接管理目录或越界目标。

**验证：** `pytest tests/test_worktree_metadata.py -q` 全部通过。

## T29：提交元数据存储

**文件：** `src/mycode/worktree/metadata.py`、`tests/test_worktree_metadata.py`

**依赖：** T28

**步骤：**

- [ ] 运行元数据完整测试，并用 monkeypatch 确认 `read_ready()` 没有调用写入函数。
- [ ] 仅暂存两个文件并运行 `git diff --cached --check`。
- [ ] 提交 `feat: add bounded worktree metadata store`。

**验证：** `pytest tests/test_worktree_metadata.py -q` 全部通过；提交只包含两个文件。

## T30：写 copy 与 ignored_copy 初始化失败测试

**文件：** 新建 `tests/test_worktree_initializer.py`

**依赖：** T29

**步骤：**

- [ ] 以 `WorktreeInitializer.initialize()` 为入口，验证 `copy` 按配置顺序复制文件/目录并保留内容，目标已存在或源类型变化时失败且不覆盖。
- [ ] 验证 `ignored_copy` 仅在 Git 确认来源和目标均被忽略时复制；任一不被忽略时初始化失败。
- [ ] fake `validate_ignored_root(path)` 分别返回成功、未忽略错误和 Git 执行错误，验证初始化器只在成功时写入。

**验证：** `pytest tests/test_worktree_initializer.py -q -k "copy or ignored"` 失败，原因为初始化器尚未实现。

## T31：实现 copy、ignored_copy 和忽略查询

**文件：** 新建 `src/mycode/worktree/initializer.py`

**依赖：** T30

**步骤：**

- [ ] 初始化器按规则索引顺序执行 copy，动作前后都重新验证来源和目标真实边界，禁止覆盖。
- [ ] `ignored_copy` 在任何写入前使用 Git 网关既有忽略验证入口分别验证来源与目标，失败诊断只含规则索引和路径。
- [ ] 文件使用元数据复制，目录使用无覆盖的递归复制；任一中间目标已存在立即失败。

**验证：** `pytest tests/test_worktree_initializer.py -q -k "copy or ignored"` 全部通过。

## T32：写 symlink 与 hooks 初始化失败测试

**文件：** 修改 `tests/test_worktree_initializer.py`

**依赖：** T31

**步骤：**

- [ ] 验证 `symlink` 创建指向声明来源的链接，来源循环、目标存在、越界解析和平台拒绝均明确失败且不改为复制。
- [ ] 验证 `hooks` 将来源目录复制到目标并返回绝对 hooks 路径，运行期源类型变成文件时失败关闭。
- [ ] 验证四类规则混合时执行顺序与 `completed_rules` 稳定，任一步失败立即停止后续规则。

**验证：** `pytest tests/test_worktree_initializer.py -q -k "symlink or hooks or order or failure"` 失败，原因为两类动作尚未实现。

## T33：实现 symlink、hooks 和初始化结果

**文件：** 修改 `src/mycode/worktree/initializer.py`

**依赖：** T32

**步骤：**

- [ ] 使用平台原生符号链接 API 实现 `symlink`，不捕获后静默降级；动作后重新解析并确认链接目标在声明范围。
- [ ] 实现 hooks 目录复制并把 Worktree 内绝对目标写入 `InitializationResult.hooks_path`，不执行 `git config`。
- [ ] 统一生成 `<rule-index>:<type>:<target>` 规则标识，失败时抛出包含索引和类型的稳定错误。

**验证：** `pytest tests/test_worktree_initializer.py -q` 全部通过或仅对当前平台不具备符号链接权限的成功场景 skip；拒绝场景必须通过。

## T34：提交环境初始化器

**文件：** `src/mycode/worktree/initializer.py`、`tests/test_worktree_initializer.py`

**依赖：** T33

**步骤：**

- [ ] 运行初始化器与 Git 网关完整测试。
- [ ] 仅暂存两个文件并确认没有测试仓库或复制产物进入暂存区。
- [ ] 提交 `feat: initialize isolated worktree environments`。

**验证：** `pytest tests/test_worktree_initializer.py tests/test_worktree_git.py -q` 全部通过。

## T35：写变更保护决策失败测试

**文件：** 新建 `tests/test_worktree_protection.py`

**依赖：** T34

**步骤：**

- [ ] 参数化 staged、unstaged、untracked、ignored-only 和完全干净的 `GitStatus`，断言得到精确 `WorktreeProtectionStatus` 字段。
- [ ] 覆盖有 upstream 且无差集、有 upstream 且有差集、无 upstream 且 tip 等于基线、无 upstream 且 tip 不同基线。
- [ ] 模拟 status、tip、upstream 和提交差集任一读取失败，验证调用方收到失败关闭错误而非干净状态。

**验证：** `pytest tests/test_worktree_protection.py -q` 失败，原因为 `WorktreeProtectionInspector` 尚不存在。

## T36：实现未提交与未推送保护检查

**文件：** 新建 `src/mycode/worktree/protection.py`

**依赖：** T35

**步骤：**

- [ ] 实现 `inspect(lease)`，先检查工作区状态，再读取分支 tip/upstream，最后判断提交差集。
- [ ] 按固定顺序生成“未提交修改”“未推送提交”中文原因，ignored-only 不触发保护。
- [ ] 无 upstream 时只比较当前 tip 与 `base_commit`；不得 fetch、push 或修改 upstream。

**验证：** `pytest tests/test_worktree_protection.py -q` 全部通过。

## T37：提交变更保护检查器

**文件：** `src/mycode/worktree/protection.py`、`tests/test_worktree_protection.py`

**依赖：** T36

**步骤：**

- [ ] 运行保护决策测试并检查错误路径没有“可能干净”的 fallback。
- [ ] 仅暂存两个文件并运行 `git diff --cached --check`。
- [ ] 提交 `feat: protect worktree changes and commits`。

**验证：** `pytest tests/test_worktree_protection.py -q` 全部通过。

## T38：写首次创建生命周期失败测试

**文件：** 新建 `tests/test_worktree_manager.py`

**依赖：** T37

**步骤：**

- [ ] 用 fake 依赖验证调用顺序：边界检查、`CREATING`、Git add、初始化、边界复核、`READY`。
- [ ] 用真实临时仓库验证目录/分支来自身份、基于固定 `base_commit`，主工作区 dirty 内容不可见。
- [ ] 验证目录冲突、分支冲突、Git 失败和 READY 落盘失败不返回租约，并产生稳定阶段诊断。

**验证：** `pytest tests/test_worktree_manager.py -q -k "create or base or conflict"` 失败，原因为 `WorktreeManager` 尚不存在。

## T39：实现首次创建与租约返回

**文件：** 新建 `src/mycode/worktree/manager.py`

**依赖：** T38

**步骤：**

- [ ] 实现构造依赖与 `prepare(identity)` 的目标不存在分支，阻塞文件/Git 操作用 `asyncio.to_thread` 离开事件循环。
- [ ] 严格按测试顺序写阶段、创建、初始化和 READY；从初始化结果构造绝对 hooks 路径与 `WorkspaceLease(CREATED)`。
- [ ] 任何异常转换为带原始阶段的 `WorktreeError`，不能启动或返回半完成租约。

**验证：** `pytest tests/test_worktree_manager.py -q -k "create or base or conflict"` 全部通过。

## T40：写严格只读快速恢复失败测试

**文件：** 修改 `tests/test_worktree_manager.py`

**依赖：** T39

**步骤：**

- [ ] 建立合法 READY 和目录结构，注入一调用就失败的 Git 网关、初始化器和写入接口，验证返回 `RECOVERED` 租约。
- [ ] 记录文件打开模式和目录枚举次数，验证只读有限 sidecar、`.git` 指针和精确目标，不遍历仓库。
- [ ] 参数化缺失 READY、非 READY、仓库/任务/路径/分支/基线/配置不匹配和越界 `.git` 指针，验证目录字节不变。

**验证：** `pytest tests/test_worktree_manager.py -q -k "recover or readonly"` 失败，原因为恢复分支尚未实现。

## T41：实现只读快速恢复分支

**文件：** 修改 `src/mycode/worktree/manager.py`

**依赖：** T40

**步骤：**

- [ ] 在 `target.exists()` 后只调用路径策略、`read_ready()` 和有界 `.git` 指针读取，禁止 Git、初始化和任何 metadata write。
- [ ] 校验 `.git` 指针指向启动时缓存的 common directory 管理区，并从配置确定性重建 hooks 路径。
- [ ] 返回 `WorkspaceLease(RECOVERED)`；任一校验失败原样保留目标和 sidecar。

**验证：** `pytest tests/test_worktree_manager.py -q -k "recover or readonly"` 全部通过，并且 spy 的 Git/写入调用计数均为 0。

## T42：写回滚、释放和处置失败测试

**文件：** 修改 `tests/test_worktree_manager.py`

**依赖：** T41

**步骤：**

- [ ] 模拟初始化失败且工作区可证明干净，验证新建 Worktree、分支和 sidecar 按顺序回滚。
- [ ] 模拟保护检查报告修改/提交或检查本身失败，验证写 `RETAINED`、保留目录分支并返回原因。
- [ ] 模拟干净释放、Worktree 删除失败、分支删除失败，验证删除顺序、部分失败状态和 sidecar 处理。

**验证：** `pytest tests/test_worktree_manager.py -q -k "rollback or release or dispose or retained"` 失败，原因为统一处置尚未实现。

## T43：实现回滚、释放和统一处置

**文件：** 修改 `src/mycode/worktree/manager.py`

**依赖：** T42

**步骤：**

- [ ] 实现 `release()` 和 `inspect_and_dispose()`，任务结果不参与删除判断，保护错误统一转为保留。
- [ ] 删除顺序固定为非 force Worktree remove、受控临时分支 delete、sidecar remove；每步前重验身份和边界。
- [ ] 初始化失败只对本次创建资源执行同一保护检查；无法证明安全时写 `RETAINED`，不清理既有恢复候选。

**验证：** `pytest tests/test_worktree_manager.py -q -k "rollback or release or dispose or retained"` 全部通过。

## T44：写同身份与同路径并发失败测试

**文件：** 修改 `tests/test_worktree_manager.py`

**依赖：** T43

**步骤：**

- [ ] 并发两次 `prepare()` 同一身份，fake Git 在临界区暂停，验证仅一次创建且另一调用恢复同一有效租约。
- [ ] 并发 `release()` 与 `inspect_and_dispose()` 同一路径，验证保护/删除串行且至多删除一次。
- [ ] 验证不同身份和不同目标可以并行，锁完成后移除空闲 key，不无限增长。

**验证：** `pytest tests/test_worktree_manager.py -q -k "concurrent or lock"` 失败，原因为 keyed lock 尚未完整实现。

## T45：实现 keyed asyncio 锁与幂等终态

**文件：** 修改 `src/mycode/worktree/manager.py`

**依赖：** T44

**步骤：**

- [ ] 使用 `(repository_id, task_token, normalized_target)` 构造锁 key，并用一个短持有 registry lock 管理每-key `asyncio.Lock`。
- [ ] 创建、恢复、释放和清理都在同一 key 锁内重新读取状态，不能依赖等待前快照。
- [ ] 记录终态并让重复删除返回同一结果或稳定 skip，不对已运行目录执行第二次 Git 删除。

**验证：** `pytest tests/test_worktree_manager.py -q` 全部通过。

## T46：提交 Worktree 生命周期管理器

**文件：** `src/mycode/worktree/manager.py`、`tests/test_worktree_manager.py`

**依赖：** T45

**步骤：**

- [ ] 运行 manager、metadata、initializer、protection 和 Git 相关测试。
- [ ] 仅暂存两个 manager 文件并运行 `git diff --cached --check`。
- [ ] 提交 `feat: manage worktree lifecycle safely`。

**验证：** `pytest tests/test_worktree_manager.py tests/test_worktree_metadata.py tests/test_worktree_initializer.py tests/test_worktree_protection.py tests/test_worktree_git.py -q` 全部通过。

## T47：写后台清理三层过滤失败测试

**文件：** 新建 `tests/test_worktree_cleaner.py`

**依赖：** T46

**步骤：**

- [ ] 为伪造 schema/身份、越界路径和非法 `.git` 指针分别断言第一层 skip 且不调用 Git 保护检查。
- [ ] 为未过期与活动任务断言第二层 skip；为未提交、未推送和状态错误断言第三层 retained。
- [ ] 验证三层都通过才调用管理器统一处置，返回 `CleanupBatchResult`，诊断顺序与 sidecar 排序一致。

**验证：** `pytest tests/test_worktree_cleaner.py -q -k "filter or identity or active or protected"` 失败，原因为 `WorktreeCleaner` 尚不存在。

## T48：实现三层过滤与有界批次

**文件：** 新建 `src/mycode/worktree/cleaner.py`

**依赖：** T47

**步骤：**

- [ ] 定义只读 `ActiveWorkspaceRegistry` 协议，清理器不得导入 `subagent.tasks`。
- [ ] 实现 `run_batch()` 固定三层顺序，每候选调用 manager 同一处置入口并把失败压缩为 `WorktreeDiagnostic`。
- [ ] 每批最多使用配置的 64 个候选，计数和 `has_more` 准确；单候选失败不终止本批。

**验证：** `pytest tests/test_worktree_cleaner.py -q -k "filter or identity or active or protected or batch"` 全部通过。

## T49：写启动扫描与周期调度失败测试

**文件：** 修改 `tests/test_worktree_cleaner.py`、`tests/worktree_helpers.py`

**依赖：** T48

**步骤：**

- [ ] 增加 fake UTC clock 和可推进 scheduler，验证 `start()` 立即扫描一次且不重复启动。
- [ ] 推进默认 3600 秒和自定义间隔，验证准确执行下一批；未到期不扫描。
- [ ] 验证 `close()` 取消并等待后台任务，批次异常被记录后下个周期仍继续。

**验证：** `pytest tests/test_worktree_cleaner.py -q -k "start or interval or close or scheduler"` 失败，原因为调度生命周期尚未实现。

## T50：实现清理器启动、循环与关闭

**文件：** 修改 `src/mycode/worktree/cleaner.py`

**依赖：** T49

**步骤：**

- [ ] 实现幂等 `start()`、私有调度循环和幂等 `close()`，使用注入 clock/sleep 支持确定测试。
- [ ] 首批通过后台 task 调度并在批次间 `await asyncio.sleep(0)` 让出事件循环，不阻塞普通聊天。
- [ ] 捕获单周期异常为有界诊断，但让取消异常正常终止并等待 task。

**验证：** `pytest tests/test_worktree_cleaner.py -q` 全部通过。

## T51：提交后台清理器

**文件：** `src/mycode/worktree/cleaner.py`、`tests/test_worktree_cleaner.py`、`tests/worktree_helpers.py`

**依赖：** T50

**步骤：**

- [ ] 运行 cleaner 与 manager 并发测试，确认 active task 不会被清理。
- [ ] 仅暂存三个文件并运行 `git diff --cached --check`。
- [ ] 提交 `feat: clean expired worktrees safely`。

**验证：** `pytest tests/test_worktree_cleaner.py tests/test_worktree_manager.py -q` 全部通过。

## T51A：固定 Worktree 包公开接口

**文件：** 修改 `src/mycode/worktree/__init__.py`、`tests/test_worktree_models.py`

**依赖：** T51

**步骤：**

- [ ] 从包入口导出 plan 中的配置加载器、路径策略、Git 网关、元数据存储、初始化器、保护检查器、管理器、清理器及公开模型。
- [ ] 添加导出 smoke test，验证导入包不会反向加载 `subagent`，`__all__` 无重复或私有 helper。

**验证：** `pytest tests/test_worktree_models.py -q -k "public or export"` 全部通过；`python -c "import mycode.worktree"` 退出码为 0。

## T52：写角色 isolation 声明失败测试

**文件：** 修改 `tests/test_subagent_models.py`、`tests/test_subagent_loader.py`

**依赖：** T51A

**步骤：**

- [ ] 验证 `AgentIsolationMode.SHARED/WORKTREE` 精确值，`AgentRoleMetadata` 缺省为共享。
- [ ] 验证定义式角色 frontmatter 的 `isolation: worktree` 可加载，未声明保持共享且角色 revision 包含该字段。
- [ ] 参数化未知值、非字符串和 Fork 输入携带 isolation，断言可定位中文诊断且角色不启动。

**验证：** `pytest tests/test_subagent_models.py tests/test_subagent_loader.py -q -k "isolation"` 失败，原因为枚举和 loader 字段尚未实现。

## T53：实现角色 isolation 模型与解析

**文件：** 修改 `src/mycode/subagent/models.py`、`src/mycode/subagent/loader.py`、`src/mycode/subagent/__init__.py`

**依赖：** T52

**步骤：**

- [ ] 增加 `AgentIsolationMode` 与 `AgentRoleMetadata.isolation`，缺省 `SHARED` 并在包入口导出。
- [ ] 把 `isolation` 加入角色 frontmatter 允许字段，仅接受 `worktree` 或缺省；共享值由缺省表达，不接受任意别名。
- [ ] 生成含路径、字段和值类型的稳定中文诊断；Fork 构造路径不读取定义式角色 isolation。

**验证：** `pytest tests/test_subagent_models.py tests/test_subagent_loader.py -q -k "isolation"` 全部通过。

## T54：写隔离协调器失败测试

**文件：** 新建 `tests/test_subagent_isolation.py`

**依赖：** T53

**步骤：**

- [ ] 验证共享定义式角色和 Fork 传空 identity，返回主工作区 `SHARED` 租约且 manager 调用数为 0。
- [ ] 验证 Worktree 角色缺 identity 明确失败，完整 identity 时原样委托 manager 并返回其租约。
- [ ] 验证 `release()` 对共享租约返回 `None`，对 Worktree 租约调用 manager 一次并透传处置结果。

**验证：** `pytest tests/test_subagent_isolation.py -q` 失败，原因为 `SubAgentIsolationCoordinator` 尚不存在。

## T55：实现共享与 Worktree 隔离协调器

**文件：** 新建 `src/mycode/subagent/isolation.py`，修改 `src/mycode/subagent/__init__.py`

**依赖：** T54

**步骤：**

- [ ] 实现协调器构造函数，接收不可变主工作区上下文和 Worktree manager 协议。
- [ ] 实现 `prepare(role, identity)` 的共享/Fork零 Git 路径和 Worktree 必需身份路径。
- [ ] 实现 `release(lease)` 的种类分派；从包入口导出协调器，不让 `worktree` 反向导入子 Agent。

**验证：** `pytest tests/test_subagent_isolation.py -q` 全部通过。

## T56：写任务令牌、租约绑定与可观测字段失败测试

**文件：** 修改 `tests/test_subagent_models.py`、`tests/test_subagent_tasks.py`

**依赖：** T55

**步骤：**

- [ ] 给任务管理器注入确定 token factory，验证每次 `reserve()` 产生唯一安全 token，`cancel_all_and_clear()` 后也不复用。
- [ ] 验证 `bind_workspace()` 只能对已预留、未启动任务执行一次，并把 kind、绝对路径、分支、preparation、initialized rules 写入 snapshot。
- [ ] 验证 `SubAgentTaskSummary` 只含轻量工作区字段，`SubAgentTaskSnapshot` 额外含初始化与处置详情。

**验证：** `pytest tests/test_subagent_models.py tests/test_subagent_tasks.py -q -k "workspace or token or bind"` 失败，原因为任务字段和绑定方法尚未实现。

## T57：实现任务工作区记录和活动查询

**文件：** 修改 `src/mycode/subagent/models.py`、`src/mycode/subagent/tasks.py`

**依赖：** T56

**步骤：**

- [ ] 在预留记录中生成一次安全 `workspace_token`，默认使用 `task-id` 加随机 ASCII hex，测试可注入 factory。
- [ ] 实现 `bind_workspace()`，保存租约并阻止未绑定 Worktree 记录启动；共享租约保持现有调度行为。
- [ ] 实现 `is_workspace_active(identity)`，仅对已绑定且非终态的完全匹配 Worktree 身份返回真。

**验证：** `pytest tests/test_subagent_models.py tests/test_subagent_tasks.py -q -k "workspace or token or bind or active"` 全部通过。

## T58：写处置结果、父 Agent 和通知序列化失败测试

**文件：** 修改 `tests/test_subagent_tasks.py`、`tests/test_subagent_tool.py`、`tests/test_subagent_notifications.py`

**依赖：** T57

**步骤：**

- [ ] 验证执行报告携带 `WorktreeDispositionResult` 后，任务终态保存 disposition 和确定顺序 reasons，并撤销活动登记。
- [ ] 验证 `Agent(action=list/get)` JSON 包含 isolation、workspace、branch、preparation、initialization、disposition 和 reasons；共享任务使用明确空值。
- [ ] 验证 detached 通知在原 4 KiB 上限内包含保留路径、分支和原因，截断时不破坏 UTF-8 或泄露其他字段。

**验证：** `pytest tests/test_subagent_tasks.py tests/test_subagent_tool.py tests/test_subagent_notifications.py -q -k "workspace or disposition or retained"` 失败，原因为终态传播尚未实现。

## T59：实现任务处置结果入库

**文件：** 修改 `src/mycode/subagent/models.py`、`src/mycode/subagent/tasks.py`

**依赖：** T58

**步骤：**

- [ ] 扩展 `SubAgentExecutionReport`、任务 record、summary 和 snapshot，字段名与 plan 的枚举/结果模型一致。
- [ ] `_finalize()` 在设置终态时原子保存处置结果并撤销活动身份；无 Worktree 结果时保持共享兼容值。
- [ ] 生成 notification 模型时填入同一工作区和处置字段，但暂不改渲染文本。

**验证：** `pytest tests/test_subagent_tasks.py -q -k "workspace or disposition or retained"` 全部通过。

## T59A：实现父 Agent 任务状态序列化

**文件：** 修改 `src/mycode/subagent/tool.py`

**依赖：** T59

**步骤：**

- [ ] 更新 summary 与 snapshot JSON，按 plan 字段输出 isolation、workspace、branch、preparation、initialization、disposition 和 reasons。
- [ ] 共享任务使用明确 `null`/空数组，不删除或重命名现有字段。

**验证：** `pytest tests/test_subagent_tool.py -q -k "workspace or disposition or retained"` 全部通过。

## T59B：实现后台通知处置文本

**文件：** 修改 `src/mycode/subagent/notifications.py`

**依赖：** T59

**步骤：**

- [ ] 在通知身份与 summary 后加入有界 workspace、branch、disposition 和 reasons 文本。
- [ ] 复用 UTF-8 截断上限，保留原顺序、reservation 和 usage 语义。

**验证：** `pytest tests/test_subagent_notifications.py -q -k "workspace or disposition or retained"` 全部通过。

## T60：提交角色、协调器与任务状态

**文件：** T52–T59 修改和新增的 subagent 文件及四个测试文件

**依赖：** T59A、T59B

**步骤：**

- [ ] 运行 models、loader、isolation、tasks、tool 和 notifications 测试。
- [ ] 只暂存 T52–T59 文件并运行 `git diff --cached --check`。
- [ ] 提交 `feat: declare and track subagent worktree isolation`。

**验证：** `pytest tests/test_subagent_models.py tests/test_subagent_loader.py tests/test_subagent_isolation.py tests/test_subagent_tasks.py tests/test_subagent_tool.py tests/test_subagent_notifications.py -q` 全部通过。

## T61：写两阶段任务准备失败测试

**文件：** 修改 `tests/test_subagent_service.py`

**依赖：** T60、T74

**步骤：**

- [ ] 记录调用顺序并断言 `reserve → capture_head/identity → isolation.prepare → bind_workspace → runtime.create → start_reserved`。
- [ ] 验证共享角色和 Fork 跳过 capture_head/manager，仍得到共享租约和原有前后台行为。
- [ ] 参数化 HEAD、prepare、bind 和 runtime factory 失败，断言 `fail_reserved()`、不启动 runner、错误码稳定。

**验证：** `pytest tests/test_subagent_service.py -q -k "prepare or reserve or isolation or workspace"` 失败，原因为 service 仍先构造 runtime。

## T62：实现两阶段 SubAgentService 调度

**文件：** 修改 `src/mycode/subagent/service.py`

**依赖：** T61

**步骤：**

- [ ] 将 `run()` 改为先预留记录，再根据角色 kind/isolation 决定是否捕获 HEAD 和构造 `WorkspaceTaskIdentity`。
- [ ] 成功准备后绑定租约，再按租约上下文创建 runtime/runner 并启动预留任务。
- [ ] 每个准备失败点映射为 `fail_reserved()`，保留 foreground wait、detach 和队列语义不变。

**验证：** `pytest tests/test_subagent_service.py -q` 全部通过。

## T63：写运行时租约释放失败测试

**文件：** 修改 `tests/test_subagent_runtime.py`

**依赖：** T62

**步骤：**

- [ ] 对 completed、failed、cancelled 和 factory 后置失败分别验证运行资源先关闭、随后协调器 release 恰好一次。
- [ ] 验证 release 的 `DELETED/RETAINED/FAILED` 结果进入 `SubAgentExecutionReport`，不覆盖原任务 state/result/error。
- [ ] release 自身返回失败处置时仍结束 runtime，不抛出导致 task manager 丢失终态。

**验证：** `pytest tests/test_subagent_runtime.py -q -k "lease or release or disposition"` 失败，原因为 runtime 尚未持有租约和协调器。

## T64：实现运行时统一 finally 释放

**文件：** 修改 `src/mycode/subagent/runtime.py`

**依赖：** T63

**步骤：**

- [ ] `SubAgentRuntimeFactory` 按调用接收 `WorkspaceLease`，不再把启动时主目录固化为所有任务目录。
- [ ] `SubAgentRuntime` 在统一 `finally` 中先关闭工具/Hook/Skill/流资源，再调用协调器 release。
- [ ] 使用冻结模型替换生成带 disposition 的执行报告，确保 completed/failed/cancelled 原字段不变。

**验证：** `pytest tests/test_subagent_runtime.py -q -k "lease or release or disposition"` 全部通过。

## T65：写子 Agent Prompt 与任务工具工作区失败测试

**文件：** 修改 `tests/test_subagent_context.py`、`tests/test_subagent_tooling.py`、`tests/test_subagent_runtime.py`

**依赖：** T64

**步骤：**

- [ ] 验证定义式首轮消息包含 Worktree 绝对路径、分支和“只操作该目录”的中文约束，共享角色不出现 Worktree 文本。
- [ ] 在两个临时工作区放不同项目指令和 project skill，验证 runtime 分别加载目标内容而非主目录缓存。
- [ ] 验证任务工具工厂按租约根创建 PathGuard、PermissionService、命令工具、Hook 和 SkillExecutor，并拒绝根目录与调用上下文不一致。

**验证：** `pytest tests/test_subagent_context.py tests/test_subagent_tooling.py tests/test_subagent_runtime.py -q -k "workspace or instruction or prompt or skill"` 失败，原因为 factory 仍绑定主工作区。

## T66：实现子 Agent Worktree Prompt

**文件：** 修改 `src/mycode/subagent/context.py`

**依赖：** T65

**步骤：**

- [ ] `build_defined_agent_messages()` 接收 `WorkspaceContext` 并仅在 Worktree 模式注入绝对路径、分支和文件操作约束。
- [ ] `_freeze_tool_definition()` 保留 `workspace_scope`，不改变供应商 schema。

**验证：** `pytest tests/test_subagent_context.py -q` 全部通过。

## T66A：实现按租约构造任务工具

**文件：** 修改 `src/mycode/subagent/tooling.py`

**依赖：** T66

**步骤：**

- [ ] `TaskToolRegistryFactory.create(workspace)` 按调用根创建默认工具、PathGuard、PermissionService 和 ToolExecutor。
- [ ] 按 workspace kind 过滤 scope；仅把工厂已知的任务本地 Skill/load 工具克隆为 `WORKSPACE_AWARE`，未知工具维持 `SHARED_ONLY`。
- [ ] 为每个租约创建对应 SkillLoader/Executor 与任务 Hook，不读取构造期主目录。

**验证：** `pytest tests/test_subagent_tooling.py -q -k "workspace or instruction or skill"` 全部通过。

## T66B：实现按租约重建运行时项目资源

**文件：** 修改 `src/mycode/subagent/runtime.py`

**依赖：** T66A

**步骤：**

- [ ] RuntimeFactory 使用租约根重新加载项目指令、Prompt builder 和项目 Memory 身份。
- [ ] 共享租约继续使用主工作区内容，Worktree 租约不得复用主项目路径缓存对象。

**验证：** `pytest tests/test_subagent_runtime.py -q -k "workspace or instruction or prompt"` 全部通过。

## T67：提交子 Agent 两阶段运行时接入

**文件：** `src/mycode/subagent/service.py`、`runtime.py`、`tooling.py`、`context.py` 及对应四个测试文件

**依赖：** T66B

**步骤：**

- [ ] 运行 service/runtime/tooling/context 和 isolation/task manager 测试。
- [ ] 仅暂存 T61–T66 文件并运行 `git diff --cached --check`。
- [ ] 提交 `feat: run isolated subagents in workspace leases`。

**验证：** `pytest tests/test_subagent_service.py tests/test_subagent_runtime.py tests/test_subagent_tooling.py tests/test_subagent_context.py tests/test_subagent_isolation.py tests/test_subagent_tasks.py -q` 全部通过。

## T68：写工具工作区能力与调用上下文失败测试

**文件：** 修改 `tests/test_tool_executor.py`、`tests/test_tool_registry.py`

**依赖：** T51A

**步骤：**

- [ ] 验证 `ToolWorkspaceScope.WORKSPACE_AWARE/SHARED_ONLY` 精确值，`ToolDefinition` 缺省为 `SHARED_ONLY` 且字段不进入供应商 schema。
- [ ] 验证 `ToolInvocationContext` 只接受合法 `WorkspaceContext`，执行器每次调用必须显式传入。
- [ ] 在 Worktree 上下文执行 `SHARED_ONLY` 工具应返回稳定拒绝；共享上下文保持现有执行、超时和 deferred 行为。

**验证：** `pytest tests/test_tool_executor.py tests/test_tool_registry.py -q -k "workspace or scope"` 失败，原因为工具工作区类型尚未实现。

## T69：实现工具工作区能力和执行器门禁

**文件：** 修改 `src/mycode/tool/base.py`、`src/mycode/tool/executor.py`、`src/mycode/tool/__init__.py`

**依赖：** T68

**步骤：**

- [ ] 增加 `ToolWorkspaceScope`、`ToolInvocationContext` 和 `ToolDefinition.workspace_scope`，从包入口导出。
- [ ] `ToolExecutor` 绑定 `WorkspaceContext`，以 `execute(call, context)` 为唯一入口；拒绝根不一致调用及 Worktree 下的 `SHARED_ONLY`，共享上下文保持现有行为。
- [ ] 保持 `ToolDefinition` 的模型 payload 冻结逻辑不包含本地 scope，保留原 runtime scope、权限和 timeout 语义。

**验证：** `pytest tests/test_tool_executor.py tests/test_tool_registry.py -q` 全部通过。

## T70：写默认文件与命令工具工作区失败测试

**文件：** 修改 `tests/test_tool_filesystem.py`、`tests/test_tool_command.py`、`tests/test_tool_registry.py`

**依赖：** T69

**步骤：**

- [ ] 验证默认文件和命令工具均标记 `WORKSPACE_AWARE`，内部 PathGuard/命令 cwd 与 registry/executor 绑定的规范化根一致。
- [ ] 在两个根目录使用相同相对路径读写/搜索，验证结果和副作用只发生在各自目录；上下文根不匹配时执行器拒绝。
- [ ] 验证命令 `cwd` 等于 Worktree 根；有 hooks path 时注入连续 `GIT_CONFIG_COUNT/KEY_0/VALUE_0`，无 hooks 时不污染环境。

**验证：** `pytest tests/test_tool_filesystem.py tests/test_tool_command.py tests/test_tool_registry.py -q -k "workspace or hooks or cwd"` 失败，原因为默认工具仍只接收静态路径。

## T71：实现工作区绑定文件与命令工具

**文件：** 修改 `src/mycode/tool/defaults.py`、`src/mycode/tool/filesystem.py`、`src/mycode/tool/command.py`

**依赖：** T70

**步骤：**

- [ ] `create_default_tool_registry(workspace)` 使用 `workspace.root` 构造 PathGuard 和命令工具，所有本地定义显式标记 `WORKSPACE_AWARE`。
- [ ] 文件/命令工具暴露只读绑定根供执行器核对，不接受调用参数覆盖根目录。
- [ ] 命令环境从当前进程环境副本构造 hooks runtime override，使用绝对 hooks path，不修改仓库 config。

**验证：** `pytest tests/test_tool_filesystem.py tests/test_tool_command.py tests/test_tool_registry.py -q` 全部通过。

## T72：写 AgentLoop 显式工作区失败测试

**文件：** 修改 `tests/helpers.py`、`tests/test_agent_loop.py`

**依赖：** T71

**步骤：**

- [ ] 在 `tests/helpers.py` 增加 `shared_workspace(root)`，返回合法主工作区上下文，测试不得调用 `chdir`。
- [ ] 验证 `AgentLoop` 构造必须传 workspace，Prompt builder、Hook context、project memory 和每个 ToolInvocationContext 都观察同一绝对根。
- [ ] monkeypatch `Path.cwd` 为抛错函数，完整运行一轮含工具调用，验证 AgentLoop 不读取进程 cwd。

**验证：** `pytest tests/test_agent_loop.py -q -k "workspace or cwd"` 失败，原因为 AgentLoop 仍调用 `Path.cwd()`。

## T73：实现 AgentLoop 工作区传播

**文件：** 修改 `src/mycode/agent/loop.py`、`tests/helpers.py`、`tests/test_agent_loop.py`

**依赖：** T72

**步骤：**

- [ ] 给 `AgentLoop.__init__` 增加必需 `workspace`，保存不可变上下文并把所有生产 `Path.cwd()` 替换为 `workspace.root`。
- [ ] 所有工具执行路径构造 `ToolInvocationContext(workspace)`，Prompt、Hook、归档和 project memory 使用同一根。
- [ ] 更新 `test_agent_loop.py` 构造 helper 显式传 `shared_workspace(root)`，不改变原断言语义。

**验证：** `rg -n "Path\.cwd\(" src/mycode/agent/loop.py` 无匹配；`pytest tests/test_agent_loop.py -q` 全部通过。

## T73A：更新 Agent、Hook 与权限测试构造点

**文件：** 修改 `tests/test_agent_plan_only.py`、`tests/test_hook_agent.py`、`tests/test_permission_e2e.py`、`tests/test_subagent_agent.py`

**依赖：** T73

**步骤：**

- [ ] 将四个文件中的 `AgentLoop(...)` 构造统一增加 `workspace=shared_workspace(root)`。
- [ ] 只调整测试 fixture/import，不改变 Hook、权限、plan-only 或子 Agent 原断言。

**验证：** `pytest tests/test_agent_plan_only.py tests/test_hook_agent.py tests/test_permission_e2e.py tests/test_subagent_agent.py -q` 全部通过。

## T73B：更新上下文、Memory、Skill 与 Slash 测试构造点

**文件：** 修改 `tests/test_context_compaction_e2e.py`、`tests/test_project_memory_e2e.py`、`tests/test_skill_agent.py`、`tests/test_skill_e2e.py`、`tests/test_slash_snapshots.py`、`tests/test_subagent_e2e.py`

**依赖：** T73

**步骤：**

- [ ] 将六个文件中的 `AgentLoop(...)` 构造统一增加显式共享 workspace。
- [ ] 保留每个测试原有 tmp root 与隔离 home，不使用全局 cwd 作为替代 fixture。

**验证：** `pytest tests/test_context_compaction_e2e.py tests/test_project_memory_e2e.py tests/test_skill_agent.py tests/test_skill_e2e.py tests/test_slash_snapshots.py tests/test_subagent_e2e.py -q` 全部通过。

## T74：提交工具与 AgentLoop 工作区传播

**文件：** T68–T73 的 tool、agent 和测试文件

**依赖：** T73A、T73B

**步骤：**

- [ ] 运行工具、AgentLoop 和所有更新构造调用点测试。
- [ ] 仅暂存 T68–T73 文件并运行 `git diff --cached --check`。
- [ ] 提交 `feat: propagate explicit workspace through tools`。

**验证：** T73 的完整 pytest 命令与 `tests/test_tool_executor.py tests/test_tool_filesystem.py tests/test_tool_command.py tests/test_tool_registry.py` 全部通过。

## T75：写 Hook 动态工作区失败测试

**文件：** 修改 `tests/test_hook_actions.py`、`tests/test_hook_runtime.py`、`tests/test_hook_agent.py`

**依赖：** T67、T74

**步骤：**

- [ ] 同一个 Hook 配置在两个 `HookContext.workspace_root` 下执行相对 cwd，验证各自只写目标目录。
- [ ] 验证绝对 cwd、`..`、链接越界和 context/runner 根不一致失败关闭。
- [ ] 验证 AgentLoop 的 user/model/tool/error hooks 收到租约根，共享主 Agent 行为不变。

**验证：** `pytest tests/test_hook_actions.py tests/test_hook_runtime.py tests/test_hook_agent.py -q -k "workspace or cwd"` 失败，原因为 action runner 仍使用构造期根。

## T76：实现 Hook 上下文驱动的 cwd

**文件：** 修改 `src/mycode/hook/actions.py`、`src/mycode/hook/runtime.py`

**依赖：** T75

**步骤：**

- [ ] `HookActionRunner` 从每次传入的 `HookContext.workspace_root` 解析相对 cwd，并用路径边界策略拒绝越界。
- [ ] `HookRuntime` 为每个触发点使用调用方工作区构造 context，不把主工作区覆盖到任务 context。
- [ ] 命令 action 继续显式传 cwd 与有界环境，不调用 `chdir`。

**验证：** `pytest tests/test_hook_actions.py tests/test_hook_runtime.py tests/test_hook_agent.py -q` 全部通过。

## T77：写 Skill 工作区传播失败测试

**文件：** 修改 `tests/test_skill_executor.py`、`tests/test_skill_agent.py`、`tests/test_skill_e2e.py`

**依赖：** T67、T74

**步骤：**

- [ ] 在两个工作区放同名 project Skill 与不同资源，验证各自 loader、entry_path、资源和嵌套 AgentLoop 使用对应根。
- [ ] monkeypatch `Path.cwd` 抛错，执行 isolated none/recent/summary 三种策略均不得读取进程 cwd。
- [ ] 验证 workspace 与 skill definition package root 不一致时失败关闭，不回退主工作区版本。

**验证：** `pytest tests/test_skill_executor.py tests/test_skill_agent.py tests/test_skill_e2e.py -q -k "workspace or project"` 失败，原因为 SkillExecutor 仍持有主工作区。

## T78：实现 Skill 工作区执行

**文件：** 修改 `src/mycode/skill/executor.py`、`src/mycode/subagent/tooling.py`

**依赖：** T67、T77

**步骤：**

- [ ] `SkillExecutor` 接收 `WorkspaceContext` 并传给内部 AgentLoop、Prompt 和权限路径。
- [ ] 任务工具工厂为每个租约创建对应 `SkillLoader`、catalog/runtime 和 executor，不复用主工作区 project Skill 对象。
- [ ] 校验 skill package root 位于 workspace 项目范围或明确的用户/内置范围，越界时返回稳定诊断。

**验证：** `pytest tests/test_skill_executor.py tests/test_skill_agent.py tests/test_skill_e2e.py tests/test_subagent_tooling.py -q` 全部通过。

## T79：写 MCP 默认共享范围失败测试

**文件：** 修改 `tests/test_mcp_tools.py`、`tests/test_subagent_tooling.py`

**依赖：** T67、T74

**步骤：**

- [ ] 验证 `MCPToolWrapper` 与 `ToolSearch` 定义默认 `SHARED_ONLY`，远端返回的 schema 不包含 scope。
- [ ] 验证共享子 Agent 继续克隆 MCP wrapper/search，Worktree 子 Agent 的 registry 中完全隐藏两者。
- [ ] 验证显式本地 workspace-aware 工具仍可见，过滤不改变 parent registry。

**验证：** `pytest tests/test_mcp_tools.py tests/test_subagent_tooling.py -q -k "workspace or shared_only or mcp"` 失败，原因为 MCP 定义和过滤尚未实现。

## T80：实现 MCP 隔离模式过滤

**文件：** 修改 `src/mycode/mcp/tools.py`、`src/mycode/subagent/tooling.py`

**依赖：** T79

**步骤：**

- [ ] MCP wrapper 和 search 明确设置 `ToolWorkspaceScope.SHARED_ONLY`，保留原 kind、deferred 和 timeout。
- [ ] 任务 registry 在克隆前按 `WorkspaceContext.kind` 过滤，Worktree 模式不注册 MCP 或 tool search。
- [ ] 共享模式沿用现有连接池克隆和清理回调，不修改 MCP schema 或连接行为。

**验证：** `pytest tests/test_mcp_tools.py tests/test_subagent_tooling.py -q` 全部通过。

## T81：提交 Hook、Skill 与 MCP 接入

**文件：** T75–T80 的生产和测试文件

**依赖：** T76、T78、T80

**步骤：**

- [ ] 运行 Hook、Skill、MCP 和 subagent tooling 测试。
- [ ] 仅暂存 T75–T80 文件并运行 `git diff --cached --check`。
- [ ] 提交 `feat: isolate hooks skills and remote tools by workspace`。

**验证：** `pytest tests/test_hook_actions.py tests/test_hook_runtime.py tests/test_hook_agent.py tests/test_skill_executor.py tests/test_skill_agent.py tests/test_skill_e2e.py tests/test_mcp_tools.py tests/test_subagent_tooling.py -q` 全部通过。

## T82：写 CLI 装配与清理生命周期失败测试

**文件：** 修改 `tests/test_e2e_chat.py`、`tests/test_hook_session_cli.py`、`tests/test_subagent_service.py`

**依赖：** T81

**步骤：**

- [ ] fake 组件记录 CLI 顺序：共享 workspace、仓库身份、配置、忽略根、manager/coordinator、cleaner start、应用、cleaner close。
- [ ] 验证非法配置、非仓库、未忽略根和 Git 不可用在启动期返回稳定错误且不启动 cleaner/子 Agent。
- [ ] 验证共享角色和 Fork 即使 Worktree manager 无调用，也继续使用主工作区及现有 Hook/session 清理顺序。

**验证：** `pytest tests/test_e2e_chat.py tests/test_hook_session_cli.py tests/test_subagent_service.py -q -k "worktree or cleaner or workspace"` 失败，原因为 CLI 尚未装配 Worktree 领域。

## T83：实现 CLI Worktree 领域装配

**文件：** 修改 `src/mycode/cli.py`

**依赖：** T82

**步骤：**

- [ ] 从启动时绝对根构造共享 `WorkspaceContext`，装配 config/pathing/Git/metadata/initializer/protection/manager/coordinator。
- [ ] 在创建 Agent/TUI 前校验仓库身份、项目配置与 `.worktrees` ignored root，失败时返回稳定启动错误。

**验证：** `pytest tests/test_e2e_chat.py -q -k "worktree and (config or repository or ignored)"` 全部通过。

## T83A：把共享工作区和协调器注入运行时工厂

**文件：** 修改 `src/mycode/cli.py`

**依赖：** T83

**步骤：**

- [ ] 主 AgentLoop、Hook、Skill 和默认工具使用同一个共享 `WorkspaceContext`。
- [ ] SubAgentService/RuntimeFactory 接收隔离协调器、仓库身份和动态任务工具工厂，不再固化主目录。

**验证：** `pytest tests/test_subagent_service.py tests/test_hook_session_cli.py -q -k "workspace or isolation"` 全部通过。

## T83B：实现清理器启动与关闭生命周期

**文件：** 修改 `src/mycode/cli.py`

**依赖：** T83A

**步骤：**

- [ ] 把 task manager 的只读 active query 注入 cleaner，应用启动后调用一次 `cleaner.start()`。
- [ ] 在应用最外层 `finally` 幂等 `await cleaner.close()`，再按现有顺序关闭其他运行资源。

**验证：** `pytest tests/test_e2e_chat.py tests/test_hook_session_cli.py -q -k "cleaner or close or startup"` 全部通过。

## T84：写 slash 状态展示失败测试

**文件：** 修改 `tests/test_slash_builtins.py`、`tests/test_slash_snapshots.py`、`tests/test_subagent_session_tui.py`

**依赖：** T83B

**步骤：**

- [ ] `/tasks` 对共享、created、recovered、retained、deleted 任务显示紧凑 isolation/workspace/branch/disposition，不打印初始化详情。
- [ ] `/task <id>` 显示绝对路径、分支、preparation、completed rules、处置和确定顺序原因；空值使用现有中文未知/无格式。
- [ ] 验证窄终端和长路径不破坏现有快照结构，且没有新增 slash 命令。

**验证：** `pytest tests/test_slash_builtins.py tests/test_slash_snapshots.py tests/test_subagent_session_tui.py -q -k "task or worktree or workspace"` 失败，原因为 slash formatter 尚未输出字段。

## T85：实现任务列表、详情和 TUI 状态

**文件：** 修改 `src/mycode/slash/builtins.py`

**依赖：** T84

**步骤：**

- [ ] 扩展 `_format_subagent_task_list()` 的单行字段，长路径使用现有文本换行/截断策略，不新增卡片或命令。
- [ ] 扩展 `_format_subagent_task_detail()`，按 isolation、workspace、branch、preparation、initialization、disposition、reasons 顺序输出。
- [ ] 共享任务和没有处置结果的活动任务保持可读兼容输出。

**验证：** `pytest tests/test_slash_builtins.py tests/test_slash_snapshots.py tests/test_subagent_session_tui.py -q` 全部通过。

## T86：验证文件缓存与 MemoryPaths 绝对路径隔离

**文件：** 修改 `tests/test_tool_cache.py`、`tests/test_memory_paths.py`

**依赖：** T73、T83

**步骤：**

- [ ] 两个 Worktree 使用相同相对文件名和不同内容，验证 `FileTextCache` 形成两个绝对 key，一方更新不改变另一方命中。
- [ ] 验证 `MemoryPaths` 为两个绝对 Worktree 根生成不同项目哈希、session 和 memory 目录。

**验证：** `pytest tests/test_tool_cache.py tests/test_memory_paths.py -q -k "workspace or root or absolute"` 全部通过；若测试暴露生产缺口，返回对应 plan 模块任务修正。

## T86A：验证项目指令与项目 Memory 隔离

**文件：** 修改 `tests/test_memory_instructions.py`、`tests/test_project_memory_e2e.py`

**依赖：** T86

**步骤：**

- [ ] 两个 Worktree 放不同 `mycode.md` 与 `.mycode/instructions.md`，验证各自指令加载结果。
- [ ] 在一方更新项目 Memory，验证另一方索引、session 和 Prompt 不变且无需清空全局缓存。

**验证：** `pytest tests/test_memory_instructions.py tests/test_project_memory_e2e.py -q -k "workspace or root or project"` 全部通过；若暴露生产缺口，返回所属实现任务修正，不在测试中伪造通过。

## T87：写并通过“提交后保留”端到端场景

**文件：** 新建 `tests/test_worktree_e2e.py`，修改 `tests/test_subagent_e2e.py`、`tests/test_permission_e2e.py`

**依赖：** T85、T86A

**步骤：**

- [ ] 主工作区带未提交文件，启动 `isolation: worktree` 角色，验证 Worktree 只含捕获 base commit 且进程 cwd 未变。
- [ ] 子 Agent 通过真实文件/命令/权限路径修改并提交文件，验证主工作区、另一个 Worktree 和缓存均不变。
- [ ] 任务终态因无 upstream 新提交返回 `RETAINED`，父 Agent tool、通知和 `/task` 都报告同一绝对路径、分支和保护原因。

**验证：** `pytest tests/test_worktree_e2e.py tests/test_subagent_e2e.py tests/test_permission_e2e.py -q -k "retained or unpushed or isolated"` 全部通过，不访问网络或真实远端。

## T88：写并通过“无变更删除与恢复”端到端场景

**文件：** 修改 `tests/test_worktree_e2e.py`、`tests/test_subagent_e2e.py`

**依赖：** T87

**步骤：**

- [ ] 对 completed、failed、cancelled 三种无变更任务验证 Worktree 和临时分支自动删除，前一受保护目录不受影响。
- [ ] 模拟 READY 后进程重建，同 identity 快速恢复时安装 Git/写入失败 spy，验证恢复和任务运行成功。

**验证：** `pytest tests/test_worktree_e2e.py tests/test_subagent_e2e.py -q` 全部通过。

## T88A：写并通过并发隔离端到端场景

**文件：** 修改 `tests/test_worktree_e2e.py`、`tests/test_subagent_e2e.py`

**依赖：** T88

**步骤：**

- [ ] 并发运行主 Agent 与两个隔离子 Agent，验证进程 cwd 恒定、三个目录和绝对缓存身份相互独立。
- [ ] 并发退出与后台清理同一路径，验证活动任务不被删除且终态后至多删除一次。

**验证：** `pytest tests/test_worktree_e2e.py tests/test_subagent_e2e.py -q -k "concurrent or parallel"` 全部通过。

## T89：增加 Worktree 配置示例

**文件：** 新建 `examples/mycode.worktree.yaml`，修改 `tests/test_docs.py`

**依赖：** T88A

**步骤：**

- [ ] 示例覆盖 version、Git 超时、清理间隔/过期时间/批量和四类规则，路径使用无凭据占位文件名。
- [ ] 文档测试通过真实 `WorktreeConfigLoader` 解析示例，并断言四类规则、清理值和无字面凭据。

**验证：** `pytest tests/test_docs.py -q -k "worktree and example"` 全部通过；`rg -n "sk-[A-Za-z0-9]" examples/mycode.worktree.yaml` 无匹配。

## T89A：补充 README Stage 13 文档

**文件：** 修改 `README.md`、`tests/test_docs.py`

**依赖：** T89

**步骤：**

- [ ] 说明 `isolation: worktree`、目录/分支、只读恢复、初始化、显式 cwd、保护与清理。
- [ ] 明确不自动合并、推送或强制删除，并用测试断言必需关键词和没有新增手动 Worktree 命令。

**验证：** `pytest tests/test_docs.py -q` 全部通过；`rg -n "sk-[A-Za-z0-9]" README.md` 无匹配。

## T90：运行完整回归与静态范围检查

**文件：** 本阶段全部文件

**依赖：** T89A

**步骤：**

- [ ] 运行 `pytest -q`，记录实际通过/失败/skip 数量；任何失败必须定位并回到所属任务修复后重跑。
- [ ] 运行 `rg -n "os\.chdir|Path\.cwd\(" src/mycode/worktree src/mycode/subagent src/mycode/agent src/mycode/tool src/mycode/hook src/mycode/skill`，确认新增 Worktree/运行时路径没有 `chdir`，生产 AgentLoop 没有 `Path.cwd()`。
- [ ] 运行 `git diff --check` 和 `git status --short`，确认用户原有改动未被覆盖或暂存。

**验证：** `pytest -q` 退出码为 0；范围扫描只允许 CLI 初次捕获主工作区的既有 `Path.cwd()`，不允许 `os.chdir`；`git diff --check` 退出码为 0。

## T91：提交集成、文档与最终回归

**文件：** T82–T89 尚未提交的 CLI、状态、回归、E2E、示例和文档文件

**依赖：** T90

**步骤：**

- [ ] 仅暂存 T82–T89 文件，运行 `git diff --cached --check` 和 `git diff --cached --name-only` 核对范围。
- [ ] 提交 `feat: integrate subagent worktree isolation`。
- [ ] 提交后重新运行 `pytest -q` 和 `git status --short`，记录最终证据和未纳入的用户改动。

**验证：** 完整测试退出码为 0；最终提交只包含 Stage 13 文件；工作区只剩任务开始前已记录的用户改动。

## 执行顺序

```text
T00
 └─ T01–T05  工作区与领域模型
     └─ T06–T14  路径与配置
         └─ T15–T24  Git 网关
             └─ T25–T29  元数据
                 ├─ T30–T34  初始化器
                 └─ T35–T37  保护检查
                      └─ T38–T46  生命周期管理器
                          └─ T47–T51  后台清理
                              ├─ T52–T60  角色、协调器和任务状态
                              └─ T68–T74  工具与 AgentLoop
                                      两支汇合于 T61–T67  两阶段子 Agent 运行时
                                          ├─ T75–T76  Hook
                                          ├─ T77–T78  Skill
                                          └─ T79–T80  MCP
                                               └─ T81
                                                   └─ T82–T85  CLI 与状态展示
                                                       └─ T86–T89  隔离回归、E2E 与文档
                                                           └─ T90–T91  完整验证与提交
```

T75–T76、T77–T78、T79–T80 在 T67 与 T74 都完成后可以并行，但必须在 T81 汇合并统一验证。任务编号按模块分组，执行图和每项“依赖”是权威顺序；不得在基础类型或生命周期接口未稳定时提前修改上层接入。

## Spec 与验收覆盖

| 需求 | 实现任务 | 主要验收 |
|---|---|---|
| F1 | T52–T55 | AC1 |
| F2 | T22–T23、T38–T39、T56–T62 | AC2、AC5、AC6 |
| F3 | T06–T09 | AC2、AC3 |
| F4 | T08–T09、T18–T19、T82–T83 | AC3、AC4 |
| F5 | T22–T23、T38–T39 | AC5、AC6 |
| F6 | T25–T28、T40–T41 | AC7、AC8 |
| F7 | T10–T13 | AC9 |
| F8 | T30–T33、T70–T71 | AC10 |
| F9 | T38–T43 | AC11 |
| F10 | T01–T02、T68–T74 | AC12 |
| F11 | T65–T80、T82–T83B | AC12 |
| F12 | T65–T66B、T72–T73B、T86–T86A | AC13 |
| F13 | T65–T66 | AC14 |
| F14 | T20–T21、T35–T36、T42–T43 | AC15–AC18 |
| F15 | T42–T43、T58–T59B、T84–T85 | AC15–AC18 |
| F16 | T47–T51、T82–T83B | AC19–AC21 |
| F17 | T44–T45、T88A | AC22 |
| F18 | T56–T60、T84–T85 | AC23 |
| N1–N3 | T01–T14、T38–T51 | AC24 |
| N4 | T16–T24、T70–T71 | AC24 |
| N5–N7 | T40–T45、T68–T80、T88A | AC12、AC22 |
| N8–N9 | T16–T17、T25–T29、T40–T41、T47–T51 | AC7、AC21、AC24 |
| N10 | T06–T09、T32–T33 | AC25 |
| N11 | T52–T55、T68–T90 | AC26 |
| N12 | T16–T17、T25–T28、T35–T36、T58–T59B | AC24 |
| N13 | T00、T15、T87–T90 | AC25–AC28 |
| N14 | 所有新增诊断任务，重点 T07、T13、T17、T36、T48 | AC24 |
| 端到端 | T87、T88、T88A | AC27、AC28 |

### AC 逐条索引

| 验收标准 | 任务 |
|---|---|
| AC1 | T52–T55 |
| AC2 | T06–T07、T56–T57 |
| AC3 | T06–T09 |
| AC4 | T08–T09、T47–T48 |
| AC5 | T22–T23、T38–T39、T87 |
| AC6 | T38–T39 |
| AC7 | T27–T28、T40–T41 |
| AC8 | T27–T28、T40–T41 |
| AC9 | T10–T13 |
| AC10 | T30–T33、T70–T71 |
| AC11 | T42–T43 |
| AC12 | T72–T80、T88A |
| AC13 | T65–T66B、T86–T86A |
| AC14 | T65–T66 |
| AC15 | T42–T43、T63–T64、T88 |
| AC16 | T35–T36、T42–T43 |
| AC17 | T35–T36、T42–T43、T87 |
| AC18 | T35–T36、T42–T43 |
| AC19 | T47–T50 |
| AC20 | T47–T48 |
| AC21 | T47–T50 |
| AC22 | T44–T45、T88A |
| AC23 | T58–T59B、T84–T85 |
| AC24 | T06–T09、T16–T17、T35–T36、T47–T48 |
| AC25 | T06–T09、T15、T32–T33 |
| AC26 | T52–T90 |
| AC27 | T87 |
| AC28 | T88 |
