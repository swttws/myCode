# Stage 08 项目知识与长期记忆 Checklist

> 每一项都通过运行代码、自动化场景或观察外部行为验证；先记录实际证据，再标记通过。

## 请求前上下文

- [ ] **C1（AC1）每次普通请求前都会刷新长期上下文。** 修改磁盘上的 `mycode.md`、`.mycode/instructions.md` 或记忆 `index.md` 后，下一次请求能观察到新内容进入模型请求；刷新诊断不阻断普通请求，除非涉及恢复一致性或路径安全失败。（验证：运行 `python -m pytest tests/test_memory_manager.py tests/test_agent_loop.py -q`，检查刷新调用次数和 LLM 请求内容。）
- [ ] **C2（AC2）三层项目指令顺序固定。** 同时存在项目根 `mycode.md`、项目 `.mycode/instructions.md` 和用户 `~/.mycode/instructions.md` 时，框架上下文顺序为项目根、项目目录、用户目录。（验证：运行 `python -m pytest tests/test_memory_instructions.py -q`，检查 blocks 优先级和 rendered_text 顺序。）
- [ ] **C3（AC3/AC16）`@include` 只读取安全范围内的合法相对 Markdown。** 合法 include 会展开；超过深度、循环、路径穿越、符号链接逃逸和不存在文件都产生诊断，且输出不含越界文件正文。（验证：运行 `python -m pytest tests/test_memory_instructions.py tests/test_memory_paths.py -q`，检查诊断 code、path 和敏感正文缺失。）
- [ ] **C4（AC14）用户级和项目级记忆索引均被注入且受限。** 请求前框架上下文同时包含两级 `index.md`，总量不超过 200 行和 25KB；超出时按确定性规则截断并产生诊断。（验证：运行 `python -m pytest tests/test_memory_notes.py tests/test_memory_manager.py tests/test_prompt_builder.py -q`，检查 line_count、byte_count、truncated 和请求消息。）
- [ ] **C5（AC20）框架上下文不污染普通会话历史。** 项目指令、时间跨度提醒和记忆索引只出现在 `MessageOrigin.FRAMEWORK_CONTEXT` 消息中，不会写入 `ConversationMemory` 或 JSONL 会话记录。（验证：运行 `python -m pytest tests/test_prompt_builder.py tests/test_agent_loop.py tests/test_memory_manager.py -q`，检查 memory、JSONL 和请求消息 origin。）

## 会话恢复

- [ ] **C6（AC4）会话只用 JSONL 追加并从内容派生元数据。** 多轮对话后 JSONL 逐行追加；删除最后半行仍能恢复前面完整记录；会话 ID、标题、消息数和更新时间均通过扫描得到，目录中没有 meta sidecar。（验证：运行 `python -m pytest tests/test_memory_sessions.py tests/test_memory_manager.py -q`，检查文件内容、派生字段和目录文件列表。）
- [ ] **C7（AC5）新进程自动恢复最近未过期会话。** 当前项目存在多个会话时，选择更新时间最大的可恢复 JSONL；超过 30 天的会话不会被选中。（验证：运行 `python -m pytest tests/test_memory_sessions.py tests/test_memory_manager.py -q`，检查 selected session_id 和 recoverable 状态。）
- [ ] **C8（AC6）坏行和结构错误只影响局部恢复。** JSONL 中混入非 JSON、结构错误和半行时，恢复跳过坏行并记录数量，其他有效消息正常进入历史。（验证：运行 `python -m pytest tests/test_memory_sessions.py -q`，检查 skipped_lines、diagnostics 和恢复后的 ChatMessage 列表。）
- [ ] **C9（AC7）悬空工具调用不会恢复给模型。** 末尾 assistant tool call 缺少匹配 tool result 时，历史截断到该边界之前，后续 LLM 请求不含悬空 tool_call。（验证：运行 `python -m pytest tests/test_memory_sessions.py tests/test_project_memory_e2e.py -q`，检查 truncated_at_boundary 和 LLM 请求历史。）
- [ ] **C10（AC8）恢复历史超预算时先复用 Stage 07 压缩保护。** 压缩成功后才发送常规模型请求；压缩失败且仍不安全时返回错误事件，不调用常规 LLM。（验证：运行 `python -m pytest tests/test_memory_manager.py tests/test_agent_loop.py tests/test_project_memory_e2e.py -q`，检查 compact 回调、错误事件和 LLM 调用数。）
- [ ] **C11（AC9）长时间中断会注入时间跨度提醒。** 恢复会话距离上次活动超过阈值时，请求框架上下文包含 restore notice；低于阈值时不注入。（验证：运行 `python -m pytest tests/test_memory_sessions.py tests/test_memory_manager.py tests/test_prompt_builder.py -q`，检查 time_gap_seconds、time_gap_block 和请求消息。）
- [ ] **C12（AC10）过期会话清理不会误删当前会话。** 超过 30 天的 JSONL 被清理；当前正在写入或最近恢复的会话文件保留。（验证：运行 `python -m pytest tests/test_memory_sessions.py tests/test_memory_manager.py -q`，检查清理前后文件存在性和诊断。）

## 自动笔记

- [ ] **C13（AC11）自动笔记按类别和作用域隔离。** 用户偏好和纠正反馈默认写入用户级目录，项目知识和参考资料默认写入项目级目录；LLM 返回合法归属调整时写入对应级别。（验证：运行 `python -m pytest tests/test_memory_notes.py tests/test_memory_note_prompt.py tests/test_memory_manager.py -q`，检查路径、frontmatter 和索引内容。）
- [ ] **C14（AC12）自然停止后异步更新笔记且不阻塞最终回复。** 模型 final response 事件先返回给用户，随后后台任务使用 `tools=[]` 调用 fake LLM 更新笔记；事件顺序不被改变。（验证：运行 `python -m pytest tests/test_memory_manager.py tests/test_agent_loop.py -q`，检查 FINAL_RESPONSE 与 after_final_response 调用顺序。）
- [ ] **C15（AC13）已有索引参与 LLM 去重决策。** 自动更新 prompt 包含用户级和项目级索引；fake LLM 返回 create、merge、update、ignore 时，笔记文件和索引结果符合动作语义。（验证：运行 `python -m pytest tests/test_memory_note_prompt.py tests/test_memory_notes.py tests/test_memory_manager.py -q`，检查决策解析和落盘结果。）
- [ ] **C16（AC19）笔记和索引写入失败保持旧状态。** 模拟笔记写入失败或索引原子替换失败时，已有笔记与旧索引不变；会话 JSONL 追加失败时本轮事件给出可观察错误。（验证：运行 `python -m pytest tests/test_memory_notes.py tests/test_memory_manager.py tests/test_agent_loop.py -q`，检查文件快照和错误诊断。）

## 安全与观测

- [ ] **C17（AC15）新增持久化路径全部使用 `mycode` 命名。** 会话、项目记忆、用户记忆、README 和测试均不写入 `.mewcode` 路径。（验证：运行 `python -m pytest tests/test_memory_paths.py tests/test_docs.py tests/test_project_memory_e2e.py -q`，并运行 `rg -n "\\.mewcode" src tests README.md`，期望只出现在“不支持/不迁移”的文档断言中。）
- [ ] **C18（AC18）诊断可观察且不泄露敏感正文。** 可以观察加载文件数、include 错误数、恢复会话 ID、坏行数量、截断原因、索引大小和笔记更新结果；诊断、事件和日志不包含 API key 或未授权文件正文。（验证：运行含唯一敏感标记的 `tests/test_memory_instructions.py tests/test_memory_sessions.py tests/test_memory_manager.py tests/test_project_memory_e2e.py -q`，搜索事件和诊断输出。）
- [ ] **C19（AC21）命名清晰并保留关键中文原因注释。** 新模块、类、函数和测试文件符合现有风格；include 路径校验、JSONL 崩溃恢复、工具边界截断、异步笔记写入和索引限制路径均有中文原因注释。（验证：运行 `python -m compileall -q src`，并用 `rg -n "#.*[一-龥]" src/mycode/memory src/mycode/agent/loop.py src/mycode/prompt/builder.py` 人工检查关键分支。）
- [ ] **C20（N4/N12/N13）请求前扫描轻量且测试可控。** 指令扫描、JSONL 读取和索引注入不调用真实模型、网络、真实 API key 或用户本机 `~/.mycode`；只有恢复压缩和后台笔记使用 fake LLM。（验证：运行 Stage 08 测试时清空真实凭据环境，检查 fake LLM 调用记录和临时 home 目录。）

## 集成回归

- [ ] **C21（AC17）Stage 07 和既有 Agent 行为不回归。** `/compact`、`/clear`、工具调用、权限审批、`plan-only`、普通聊天和事件流继续通过；项目记忆接入不改变协议层语义。（验证：运行 `python -m pytest tests/test_context_compaction_e2e.py tests/test_agent_loop.py tests/test_agent_plan_only.py tests/test_permission_e2e.py tests/test_session.py tests/test_tui.py tests/test_protocol_factory.py tests/test_sse.py -q`。）
- [ ] **C22（文档）README 记录 Stage 08 行为与边界。** README 包含三层指令、JSONL 会话、自动恢复、自动笔记、两级记忆目录、`index.md` 限制、`.mewcode` 不迁移和高级检索不做。（验证：运行 `python -m pytest tests/test_docs.py -q`。）
- [ ] **C23 项目可编译。**（验证：运行 `python -m compileall -q src`，期望退出码 0 且无输出。）
- [ ] **C24 全部自动化测试通过。**（验证：运行 `python -m pytest -q`，记录实际通过数量，期望 0 failed/0 errors。）
- [ ] **C25 格式与冲突检查通过。** 仓库未配置独立 lint 命令，以编译、测试和 diff 检查作为本阶段静态门槛。（验证：运行 `git diff --check`，期望退出码 0 且无冲突标记或空白错误。）

## 端到端场景

- [ ] **C26（AC22）正常重启后自动接续项目工作。** 用户在一个项目中连续对话并产生 JSONL 与自动笔记后，重启程序发送新请求；系统自动恢复最近会话，注入项目指令和两级记忆索引，模型能基于旧历史继续任务。（验证：运行 `python -m pytest tests/test_project_memory_e2e.py::test_project_memory_restores_recent_session_and_injects_memory -q`，检查 JSONL、LLM 请求和最终回复。）
- [ ] **C27（AC23）故障项目状态能安全降级或阻断。** 构造坏行、悬空工具调用、超预算历史和损坏索引后发送请求；系统跳过坏行、截断工具边界、压缩历史、降级索引注入并给出诊断；不能安全压缩时不发送常规模型请求。（验证：运行 `python -m pytest tests/test_project_memory_e2e.py::test_project_memory_faults_are_recovered_or_blocked_safely -q`，检查诊断、LLM 调用数和请求历史。）
