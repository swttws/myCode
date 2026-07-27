# myCode Stage 10：Skill 系统验收清单

> 所有条目都在实现完成后执行。先运行验证命令、记录实际输出，再勾选结果；不得用代码阅读或主观判断代替可观察证据。

## 验收环境

- [ ] 使用临时项目 Skill 根、临时用户 Skill 根和仓库内置 Skill 根运行自动化测试，未读取真实 `~/.mycode/skills`。（验证：运行 `python -m pytest tests/test_skill_cli.py tests/test_skill_e2e.py -q`，期望全部通过，测试夹具记录的根目录均位于 `tmp_path` 或包资源目录。）
- [ ] 所有模型交互均由 fake LLM 提供固定脚本，不访问网络、不要求真实 API key。（验证：清空模型供应商 API key 后运行 `python -m pytest tests/test_skill_agent.py tests/test_skill_executor.py tests/test_skill_e2e.py -q`，期望全部通过且 fake LLM 捕获全部请求。）
- [ ] 测试不会恢复或依赖工作区中已由用户删除的旧测试文件。（验证：运行 `git status --short`，确认既有删除仍保持原状态；运行当前测试集合时不出现对已删除测试辅助模块的导入错误。）

## 定义、发现与加载

- [ ] **AC1-a：** 只含入口文件的最小 Skill 目录和含模板、示例、脚本、参考文档的目录型 Skill 都能被发现。（验证：运行 `python -m pytest tests/test_skill_loader.py -q -k "scan and valid"`，期望两类目录都出现在扫描结果中。）
- [ ] **AC1-b：** Skill 根目录下散落的 Markdown 不被识别，一级子目录缺少 `SKILL.md` 时产生诊断但不阻断其他 Skill。（验证：运行 `python -m pytest tests/test_skill_loader.py -q -k "scattered or missing_entry"`，期望散落文件不在候选中且有效邻居仍可加载。）
- [ ] **AC1-c：** 缺少必填字段、未知字段、空正文、非法名称、目录名不一致、非法模式和非法上下文策略均被诊断并跳过。（验证：运行 `python -m pytest tests/test_skill_loader.py -q -k "invalid or frontmatter or metadata"`，期望每个坏入口都有稳定诊断，其他入口照常加载。）
- [ ] **AC2-a：** 有参数调用时，SOP 中每个精确 `{{arguments}}` 都替换为未经解释的原始参数。（验证：运行 `python -m pytest tests/test_skill_runtime.py -q -k "arguments and nonempty"`，期望模型捕获的 SOP 保留参数原始字符。）
- [ ] **AC2-b：** 无参数调用时占位符替换为空内容，参数中的表达式、模板语法或代码不会执行。（验证：运行 `python -m pytest tests/test_skill_runtime.py -q -k "arguments and empty or literal"`，期望无未解析标记且无任何表达式副作用。）
- [ ] **AC3-a：** 加载目录型 Skill 时只激活入口 SOP并返回资源清单，不自动读取资源正文或执行辅助脚本。（验证：运行 `python -m pytest tests/test_skill_load_tool.py -q -k "shared or resources"`，期望结果只含名称、模式、版本、清单和范围标记，资源标记文本未出现且脚本未执行。）
- [ ] **AC3-b：** 已激活 Skill 的合法 UTF-8 资源可通过同一个系统工具按相对路径逐个读取。（验证：运行 `python -m pytest tests/test_skill_load_tool.py -q -k "resource and success"`，期望返回指定文件原文且激活状态不变。）
- [ ] **AC3-c：** 路径穿越、绝对路径、目录、未知资源、符号链接和真实路径逃逸都被拒绝。（验证：运行 `python -m pytest tests/test_skill_loader.py tests/test_skill_load_tool.py -q -k "traversal or symlink or escape or unknown_resource"`，期望全部返回稳定资源错误且不泄漏包外内容。）
- [ ] **AC4-a：** 项目、用户、内置同时存在同名 Skill 时，项目版本生效；删除项目版本后自动使用用户版本。（验证：运行 `python -m pytest tests/test_skill_catalog.py -q -k "precedence or delete_project"`，期望来源依次为 project、user。）
- [ ] **AC4-b：** 高优先级入口解析失败时自动回退到低优先级有效版本，并保留可定位诊断。（验证：运行 `python -m pytest tests/test_skill_catalog.py -q -k "fallback and parse"`，期望有效定义来自低层且诊断指向坏入口。）
- [ ] **AC5-a：** 最终有效 Skill 引用一个或多个未知工具时，应用启动立即失败并列出 Skill 名称和未知工具。（验证：运行 `python -m pytest tests/test_skill_cli.py tests/test_skill_catalog.py -q -k "unknown_tool and startup"`，期望退出码 `1`，错误不包含 SOP、凭据或无关文件内容。）
- [ ] **AC5-b：** Skill 名称与固定命令主名称或别名冲突时启动失败，固定命令不被覆盖。（验证：运行 `python -m pytest tests/test_skill_cli.py tests/test_skill_catalog.py -q -k "slash_conflict or reserved"`，期望退出码 `1` 并显示冲突名称。）
- [ ] **AC6：** 首次模型请求只能看到有效 Skill 的名称和一句说明，看不到 SOP、模板、示例、脚本或参考文档中的标记文本。（验证：运行 `python -m pytest tests/test_skill_agent.py tests/test_skill_e2e.py -q -k "first_request or catalog_prompt"`，检查 fake LLM 捕获的首次请求，期望只包含目录摘要。）
- [ ] **AC7-a：** 模型按名称调用系统级 `load_skill` 后，下一模型轮获得完整 SOP 并将 Skill 标记为激活。（验证：运行 `python -m pytest tests/test_skill_load_tool.py tests/test_skill_agent.py -q -k "activate or load_skill"`，期望工具结果不含 SOP，但下一请求的固定框架块含完整 SOP。）
- [ ] **AC7-b：** `load_skill` 不需要出现在 Skill 自身白名单中；空白名单下仍能继续加载入口和合法资源。（验证：运行 `python -m pytest tests/test_skill_load_tool.py tests/test_skill_runtime.py -q -k "system_tool or empty_allowlist"`，期望系统工具始终可见并可执行。）
- [ ] **AC8-a：** 同时激活两个 Skill 后，当前轮后续请求和下一用户轮的每次模型请求都含两份完整 SOP。（验证：运行 `python -m pytest tests/test_skill_agent.py -q -k "multiple_active or persistent"`，逐个检查 fake LLM 请求快照。）
- [ ] **AC8-b：** 多个激活 SOP 按 Skill 名称稳定排序，位于项目指令和记忆之前的高显著框架区域，普通对话历史不含这些 SOP。（验证：运行 `python -m pytest tests/test_skill_runtime.py tests/test_skill_agent.py -q -k "prompt_order or history_clean"`，期望块优先级和 memory 内容符合约束。）

## 共享与独立执行

- [ ] **AC9-a：** 共享 Skill 使用主模型和已有主历史执行，能观察到此前用户轮次。（验证：运行 `python -m pytest tests/test_skill_agent.py tests/test_skill_e2e.py -q -k "shared and history"`，期望 fake 主模型请求包含此前完整主历史且未创建覆盖模型。）
- [ ] **AC9-b：** 共享执行产生的助手回复、工具调用和工具结果完整保留在主历史。（验证：运行 `python -m pytest tests/test_skill_agent.py -q -k "shared and main_history"`，期望主 memory 中保留完整调用链。）
- [ ] **AC9-c：** 共享执行期间只暴露当前 Skill 自身白名单和 `load_skill`，run 结束、取消或异常后恢复完整工具集。（验证：运行 `python -m pytest tests/test_skill_agent.py -q -k "shared and whitelist or restore_scope"`，期望执行前后二次捕获的工具集合符合约束。）
- [ ] **AC10：** 独立 `none` 模式不携带任何主历史，完成后主历史只新增调用文本和结果摘要，不含临时工具详情。（验证：运行 `python -m pytest tests/test_skill_executor.py tests/test_skill_e2e.py -q -k "isolated and none"`，分别检查临时请求和主 memory。）
- [ ] **AC11-a：** 独立 `recent` 模式只携带最近 N 个已经完成的用户轮次。（验证：运行 `python -m pytest tests/test_skill_context.py tests/test_skill_executor.py -q -k "recent or completed_turns"`，期望更早轮次和未完成当前轮均不出现。）
- [ ] **AC11-b：** 被选轮次中的助手工具调用、全部工具结果和最终助手文本保持完整顺序，不在调用链中间截断。（验证：运行 `python -m pytest tests/test_skill_context.py -q -k "tool_chain or incomplete"`，期望只有闭合完整轮次进入结果。）
- [ ] **AC12-a：** 独立 `summary` 模式执行前恰好发生一次摘要模型调用，且该请求的工具列表为空。（验证：运行 `python -m pytest tests/test_skill_executor.py -q -k "summary and tools"`，期望 fake LLM 记录一次摘要请求和 `tools=[]`。）
- [ ] **AC12-b：** 新生成摘要作为临时框架上下文进入独立对话，不读取既有压缩摘要，也不写回主历史。（验证：运行 `python -m pytest tests/test_skill_executor.py -q -k "summary and framework or main_history"`，期望摘要只存在于临时请求。）
- [ ] **AC13-a：** 独立 Skill 指定模型时仅模型 ID 改变，主配置的协议、服务地址、凭据和其他参数保持不变。（验证：运行 `python -m pytest tests/test_skill_executor.py -q -k "model_override"`，比较 fake LLM 工厂接收的完整配置。）
- [ ] **AC13-b：** 独立 Skill 未指定模型时复用主 LLM；共享 Skill 即使声明 `model` 仍使用主模型。（验证：运行 `python -m pytest tests/test_skill_executor.py tests/test_skill_agent.py -q -k "main_model or shared_model"`，期望没有额外模型工厂调用。）
- [ ] **AC14-a：** 两个白名单不同的 Skill 分别执行时，各自只能看到自己的普通工具和系统级 `load_skill`，不会合并其他激活 Skill 的白名单。（验证：运行 `python -m pytest tests/test_skill_runtime.py tests/test_skill_agent.py -q -k "different_allowlists or no_merge"`，检查每次模型请求的工具名集合。）
- [ ] **AC14-b：** 空白名单不暴露普通工具；模型伪造白名单外工具调用时在权限检查和真实执行前被拒绝。（验证：运行 `python -m pytest tests/test_skill_agent.py -q -k "empty_allowlist or preflight"`，期望 fake 权限层和执行器均未收到伪造调用。）
- [ ] **AC14-c：** 白名单内工具仍受原有权限拒绝、用户审批、plan-only、超时和路径保护约束。（验证：运行 `python -m pytest tests/test_skill_agent.py tests/test_skill_e2e.py -q -k "permission or approval or timeout or path_guard"`，期望原有安全决策保持有效。）
- [ ] 独立 Skill 内再次请求独立 Skill 时被稳定拒绝，不创建递归临时 Agent。（验证：运行 `python -m pytest tests/test_skill_load_tool.py -q -k "recursive"`，期望返回固定错误码且执行器调用次数不增加。）
- [ ] 独立上下文超过安全线时返回 `independent_context_too_large`，不触发主归档、压缩或磁盘会话。（验证：运行 `python -m pytest tests/test_skill_context.py tests/test_skill_executor.py -q -k "too_large or ephemeral"`，期望临时错误可定位且主上下文状态不变。）

## 斜杠、热更新与内置 Skill

- [ ] **AC15-a：** 每个有效 Skill 自动出现在 `/help` 和输入补全中，说明来自该 Skill 的 metadata。（验证：运行 `python -m pytest tests/test_skill_slash.py tests/test_slash_completion.py tests/test_slash_snapshots.py -q -k "help or completion"`，期望名称与说明一致且按名称排序。）
- [ ] **AC15-b：** `/skill-name arguments` 调用同名 Skill，原始 arguments 正确传给 Session；shared 和 isolated 均按 frontmatter 路由。（验证：运行 `python -m pytest tests/test_skill_slash.py tests/test_slash_tui.py -q -k "execute_skill or arguments"`，期望控制器捕获精确名称和参数。）
- [ ] **AC15-c：** `/review` 通过内置 review Skill 执行，固定命令表中不存在 review 提示词 handler 或旁路。（验证：运行 `python -m pytest tests/test_slash_builtins.py tests/test_slash_e2e.py -q -k "review"`，期望调用 `send_skill("review", ...)` 而非发送固定 Prompt。）
- [ ] **AC16-a：** 运行中新增 Skill 后无需重启，在下一次补全、分发或 `load_skill` 调用前可见。（验证：运行 `python -m pytest tests/test_skill_catalog.py tests/test_skill_slash.py -q -k "hot_add or refresh"`，期望目录和动态命令同步新增。）
- [ ] **AC16-b：** 修改 Skill SOP 后下一次使用采用新正文；已激活 Skill 使用原始参数重新渲染。（验证：运行 `python -m pytest tests/test_skill_catalog.py tests/test_skill_runtime.py -q -k "hot_modify or rerender"`，期望下一模型请求出现新 SOP 和原参数。）
- [ ] **AC16-c：** 删除高优先级版本后无需重启即回退到较低层版本，动态命令保持可用。（验证：运行 `python -m pytest tests/test_skill_catalog.py tests/test_skill_slash.py -q -k "hot_delete or fallback"`，期望来源和命令说明同步变化。）
- [ ] **AC17-a：** 热更新版本引用未知工具或新增固定命令冲突时，仅拒绝受影响 Skill 的新版本并显示诊断。（验证：运行 `python -m pytest tests/test_skill_catalog.py tests/test_skill_slash.py -q -k "last_valid or rejected_update"`，期望诊断包含名称与无效项。）
- [ ] **AC17-b：** 被拒绝更新的 Skill 继续使用最后有效版本，同次刷新中的其他 Skill 更新正常生效。（验证：运行 `python -m pytest tests/test_skill_catalog.py -q -k "last_valid and unaffected"`，期望两个名称分别保留旧版和采用新版。）
- [ ] **AC18-a：** 激活多个 Skill 后执行 `/clear`，激活集合、当前范围和未回流临时状态都被清除。（验证：运行 `python -m pytest tests/test_skill_runtime.py tests/test_skill_agent.py -q -k "clear"`，期望运行时状态为空且目录代次未重置。）
- [ ] **AC18-b：** `/clear` 后下一次模型请求不含完整 SOP，但仍含可发现名称和一句说明。（验证：运行 `python -m pytest tests/test_skill_e2e.py -q -k "clear and rediscover"`，检查清理前后 fake LLM 请求差异。）
- [ ] **AC19-a：** 默认安装产物包含并可发现 `commit`、`review`、`test` 三个 Skill，三者 description 均为中文。（验证：运行 `python -m pytest tests/test_skill_docs.py -q -k "builtins or package_data"`，期望从临时安装产物加载三个入口。）
- [ ] **AC19-b：** commit、review 为 shared；test 为 isolated/recent/3；三者白名单与批准设计一致且正文含 `{{arguments}}`。（验证：运行 `python -m pytest tests/test_skill_docs.py -q -k "builtin_metadata"`，期望字段逐项匹配。）
- [ ] **AC19-c：** 用户级或项目级同名 Skill 能覆盖内置样板，删除覆盖版本后恢复内置版本。（验证：运行 `python -m pytest tests/test_skill_catalog.py tests/test_skill_e2e.py -q -k "builtin_override"`，期望来源按项目、用户、内置顺序变化。）

## 非功能与故障隔离

- [ ] **AC20-a：** Skill 发现、激活、执行和斜杠桥接集中在独立领域包，现有 Prompt、工具调度和斜杠基础设施通过接口复用，没有第二套模型循环。（验证：审查 Stage 10 最终差异和依赖方向，运行 `python -m pytest tests/test_skill_agent.py tests/test_skill_slash.py -q`，期望真实调用经过现有 AgentLoop、ToolRegistry 和 SlashCommandRegistry。）
- [ ] **AC20-b：** 三级回退、白名单范围传播、真实路径边界、热更新最后有效版本和独立历史隔离处有简洁中文注释，其他自解释代码没有叙述性注释。（验证：逐处审查最终差异并运行 `git diff --check`，记录五类关键注释位置，无冗余注释和格式错误。）
- [ ] **AC20-c：** 实现中没有市场、远程仓库、安装服务、版本号、依赖解析、后台 watcher 或脚本自动执行入口。（验证：审查公开配置、CLI 帮助和新增依赖，运行 `python -m pytest tests/test_skill_docs.py -q -k "out_of_scope"`，期望未暴露范围外能力。）
- [ ] **AC21-a：** 相同目录、工具表和会话状态重复扫描、加载和激活时，定义、诊断、Prompt 块、工具和命令顺序一致。（验证：运行 `python -m pytest tests/test_skill_catalog.py tests/test_skill_runtime.py tests/test_skill_slash.py -q -k "deterministic or generation"`，期望重复快照完全相等。）
- [ ] **AC21-b：** 超限入口、frontmatter、资源数量和单资源读取被有界拒绝，只影响对应候选或资源。（验证：运行 `python -m pytest tests/test_skill_loader.py -q -k "limit or oversized"`，期望稳定错误且相邻 Skill/资源仍可用。）
- [ ] **AC21-c：** 使用前刷新依靠路径、大小和 `mtime_ns` 指纹，未变化候选不重复解析正文，资源增删能触发变化。（验证：运行 `python -m pytest tests/test_skill_catalog.py -q -k "fingerprint or cache"`，期望可观察加载器的解析次数只随变化增加。）
- [ ] **AC22-a：** 单个 Skill 解析失败、资源读取失败或独立执行失败时，普通聊天和其他 Skill 仍可继续。（验证：运行 `python -m pytest tests/test_skill_e2e.py -q -k "fault_isolation"`，期望失败事件可见且后续普通消息获得正常回复。）
- [ ] **AC22-b：** 固定斜杠命令、权限、上下文压缩、会话恢复、记忆和普通工具执行行为无回归。（验证：运行 `python -m pytest -q`，期望当前仓库全部测试通过。）
- [ ] **AC22-c：** 诊断和错误只包含稳定代码、Skill 名称、路径定位和必要消息，不包含 SOP、完整历史、工具原始输出或凭据。（验证：运行 `python -m pytest tests/test_skill_catalog.py tests/test_skill_load_tool.py tests/test_skill_executor.py tests/test_skill_cli.py -q -k "redact or diagnostic or error"`，期望敏感标记不出现在输出。）
- [ ] **AC23-a：** 新增自动化测试全部使用 `tmp_path`、fake LLM、固定工具和本地数据，不连接网络或读取真实用户 Skill。（验证：运行 `python -m pytest tests/test_skill_models.py tests/test_skill_loader.py tests/test_skill_catalog.py tests/test_skill_runtime.py tests/test_skill_context.py tests/test_skill_load_tool.py tests/test_skill_executor.py tests/test_skill_slash.py tests/test_skill_agent.py tests/test_skill_cli.py tests/test_skill_e2e.py tests/test_skill_docs.py -q`，期望在无 API key 环境下全部通过。）
- [ ] **AC23-b：** UTF-8 中文 description、SOP 和资源正文经过扫描、激活、模型请求和资源读取后保持原样。（验证：运行 `python -m pytest tests/test_skill_loader.py tests/test_skill_runtime.py tests/test_skill_load_tool.py tests/test_skill_docs.py -q -k "utf8 or chinese"`，期望逐字相等。）

## 集成检查

- [ ] CLI 在本地、上下文、记忆和 MCP 工具全部注册后创建 Skill 系统，先注册系统级 `load_skill`，再校验最终白名单和固定命令冲突。（验证：运行 `python -m pytest tests/test_skill_cli.py -q -k "startup_order"`，期望装配调用序列与设计一致。）
- [ ] `load_skill` 被标记为只读但非并行安全；同批调用中它形成独立顺序边界。（验证：运行 `python -m pytest tests/test_skill_agent.py tests/test_skill_load_tool.py -q -k "parallel_safe or schedule"`，期望加载调用先完成。）
- [ ] 共享加载结果只有在成功且包含有效 `action=activated`、`set_scope=true` 标记时，才由父 AgentLoop 切换当前范围。（验证：运行 `python -m pytest tests/test_skill_agent.py -q -k "set_scope"`，期望失败、伪造或其他工具结果均不切换范围。）
- [ ] 模型可见工具过滤与真实执行前白名单校验同时生效，显式白名单中的延迟 MCP 工具可直接展开 schema。（验证：运行 `python -m pytest tests/test_skill_agent.py -q -k "tool_view or deferred or preflight"`，期望两个约束层都被观察到。）
- [ ] 独立 Agent 复用现有 AgentLoop、权限和工具执行器，但使用临时 memory 与不落盘上下文管理器。（验证：运行 `python -m pytest tests/test_skill_executor.py -q -k "ephemeral or reuse"`，期望临时详情与主存储隔离。）
- [ ] 独立最终摘要通过统一外部 exchange 同时写入主 memory、会话记录和既有后台记忆流程。（验证：运行 `python -m pytest tests/test_skill_agent.py -q -k "external_exchange"`，期望三个观察点收到同一用户文本和摘要。）
- [ ] 动态斜杠替换在临时索引验证后原子交换；刷新失败不会留下半更新帮助、补全或分发状态。（验证：运行 `python -m pytest tests/test_skill_slash.py tests/test_slash_registry.py -q -k "atomic or conflict"`，期望失败前后旧快照完全一致。）
- [ ] 补全触发的热更新诊断被缓存，并在下一次分发时只展示一次。（验证：运行 `python -m pytest tests/test_skill_slash.py tests/test_slash_dispatcher.py tests/test_slash_completion.py -q -k "diagnostic"`，期望补全无 UI 输出、分发一次输出、再次分发不重复。）
- [ ] 未启用 SkillRuntime 的旧构造路径仍可创建 AgentLoop、ChatSession、Dispatcher 和 Completer。（验证：运行现有斜杠与普通会话测试，期望可选依赖默认值保持兼容。）

## 编译与测试

- [ ] Python 源码可完整编译，无语法错误。（验证：运行 `python -m compileall -q src`，期望退出码 `0`。）
- [ ] 所有新增 Skill 单元与集成测试通过。（验证：运行 `python -m pytest tests/test_skill_models.py tests/test_skill_loader.py tests/test_skill_catalog.py tests/test_skill_runtime.py tests/test_skill_context.py tests/test_skill_load_tool.py tests/test_skill_executor.py tests/test_skill_slash.py tests/test_skill_agent.py tests/test_skill_cli.py tests/test_skill_e2e.py tests/test_skill_docs.py -q`，期望零失败。）
- [ ] 所有现存 Stage 09 斜杠测试通过。（验证：运行 `python -m pytest tests/test_slash_builtins.py tests/test_slash_cli.py tests/test_slash_completion.py tests/test_slash_dispatcher.py tests/test_slash_e2e.py tests/test_slash_parser.py tests/test_slash_registry.py tests/test_slash_snapshots.py tests/test_slash_status.py tests/test_slash_tui.py -q`，期望零失败。）
- [ ] 仓库当前全部自动化测试通过。（验证：运行 `python -m pytest -q`，记录测试总数、通过数和退出码 `0`。）
- [ ] 构建配置包含三个内置入口，临时安装或 wheel 解包后仍可通过普通 Loader 发现。（验证：运行 `python -m pytest tests/test_skill_docs.py -q -k "package_data or installed"`，期望安装产物中三个 `SKILL.md` 均存在并可解析。）
- [ ] 最终差异无空白错误、冲突标记或意外修改用户已有文件。（验证：运行 `git diff --check` 和 `git status --short`，期望前者退出码 `0`，后者只列批准的 Stage 10 文件及进入本阶段前已存在的用户变更。）

## 端到端场景

- [ ] **场景 1：模型按需加载共享 Skill。** 启动后首次请求只见目录摘要，模型调用 `load_skill`，下一轮获得完整 SOP；后续只见该 Skill 白名单，最终回复与工具链留在主历史。（验证：运行 `python -m pytest tests/test_skill_e2e.py -q -k "shared_load"`，逐阶段比对 fake LLM 请求、工具集合和主 memory。）
- [ ] **场景 2：斜杠执行独立 test Skill。** 主历史至少四个完整轮次时执行 `/test target`，临时对话只收到最近三轮和渲染后的 SOP，完成摘要回流，临时工具详情不回流。（验证：运行 `python -m pytest tests/test_skill_e2e.py -q -k "isolated_test"`，期望上下文边界和主历史均符合设计。）
- [ ] **场景 3：摘要上下文与指定模型。** 执行配置为 `summary` 且指定模型的独立 Skill，先发生无工具摘要调用，再由仅替换模型 ID 的同一配置执行 SOP。（验证：运行 `python -m pytest tests/test_skill_e2e.py -q -k "summary_model"`，期望两次临时请求和模型配置可观察。）
- [ ] **场景 4：运行中热更新与保底。** 新增 Skill 后命令立即出现；修改 SOP 后激活内容更新；再写入未知工具时更新被拒绝且最后有效版本仍能执行，其他 Skill 正常更新。（验证：运行 `python -m pytest tests/test_skill_e2e.py -q -k "hot_update"`，期望命令、诊断、版本和执行结果逐步匹配。）
- [ ] **场景 5：资源能力包安全读取。** 加载含模板、示例和辅助脚本的 Skill，只列清单；合法资源可读，脚本未自动执行，包外路径和符号链接被拒绝。（验证：运行 `python -m pytest tests/test_skill_e2e.py -q -k "resource_package"`，期望安全边界和无副作用均有断言。）
- [ ] **场景 6：清空后重新发现。** 激活两个 Skill 并执行一次后运行 `/clear`，下一请求不再含 SOP，但仍能从目录摘要和补全重新发现并再次加载。（验证：运行 `python -m pytest tests/test_skill_e2e.py -q -k "clear_and_rediscover"`，期望激活、范围、目录缓存和命令状态分别符合约束。）
- [ ] **场景 7：故障隔离。** 一个高优先级 Skill 解析失败、另一个资源读取失败时，低层回退、普通聊天和第三个有效 Skill 均继续工作。（验证：运行 `python -m pytest tests/test_skill_e2e.py -q -k "fault_isolation"`，期望错误可定位且后续交互成功。）

## 验收覆盖矩阵

| Spec 验收标准 | 对应检查区域 |
|---|---|
| AC1-AC5 | 定义、发现与加载 |
| AC6-AC8 | 定义、发现与加载；端到端场景 1 |
| AC9 | 共享与独立执行；端到端场景 1 |
| AC10-AC14 | 共享与独立执行；端到端场景 2-3 |
| AC15 | 斜杠、热更新与内置 Skill |
| AC16-AC17 | 斜杠、热更新与内置 Skill；端到端场景 4 |
| AC18 | 斜杠、热更新与内置 Skill；端到端场景 6 |
| AC19 | 斜杠、热更新与内置 Skill；编译与测试 |
| AC20-AC23 | 非功能与故障隔离；集成检查；编译与测试；端到端场景 5、7 |

23 条 Spec 验收标准均至少对应一个可执行检查项；共享、独立、热更新、资源安全和清空流程均有端到端场景覆盖。
