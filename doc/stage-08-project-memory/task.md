# Stage 08 项目知识与长期记忆 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `src/mycode/memory/models.py` | Stage 08 枚举、诊断、指令、会话、笔记、框架上下文和笔记更新结果类型 |
| 新建 | `src/mycode/memory/paths.py` | `~/.mycode` 与项目隔离目录计算、真实路径边界校验 |
| 新建 | `src/mycode/memory/instructions.py` | 三层指令加载、`@include` 展开、诊断和确定性渲染 |
| 新建 | `src/mycode/memory/sessions.py` | JSONL 会话追加、扫描、恢复、工具边界截断和过期清理 |
| 新建 | `src/mycode/memory/notes.py` | Markdown 笔记、frontmatter、索引加载、截断和决策落盘 |
| 新建 | `src/mycode/memory/note_prompt.py` | 自动笔记更新提示构造和 JSON 决策解析 |
| 新建 | `src/mycode/memory/manager.py` | `ProjectMemoryManager` 请求前刷新、会话记录、自动笔记和生命周期门面 |
| 修改 | `src/mycode/memory/__init__.py` | 导出 Stage 08 公共类型和创建入口 |
| 修改 | `src/mycode/llm/base.py` | 增加 `FRAMEWORK_CONTEXT` 消息来源 |
| 修改 | `src/mycode/prompt/models.py` | 增加 `PromptContextBlock`，让 turn 上下文携带框架上下文块 |
| 修改 | `src/mycode/prompt/builder.py` | 把框架上下文作为临时 user-role 消息注入请求 |
| 修改 | `src/mycode/agent/loop.py` | 接入项目记忆刷新、JSONL 记录、自动笔记触发和 `/clear` 生命周期 |
| 修改 | `src/mycode/session.py` | 保持薄转发，补充 clear 与项目记忆集成测试契约 |
| 修改 | `src/mycode/cli.py` | 创建并注入 `ProjectMemoryManager`，按顺序关闭资源 |
| 新建 | `tests/test_memory_models.py` | 数据模型、不可变性、枚举和消息来源测试 |
| 新建 | `tests/test_memory_paths.py` | 路径计算、目录创建和越界拒绝测试 |
| 新建 | `tests/test_memory_instructions.py` | 指令扫描、include、安全边界和渲染测试 |
| 新建 | `tests/test_memory_sessions.py` | JSONL 追加、派生元数据、恢复和清理测试 |
| 新建 | `tests/test_memory_notes.py` | 笔记读写、索引截断、决策应用和失败安全测试 |
| 新建 | `tests/test_memory_note_prompt.py` | 自动笔记提示和解析测试 |
| 新建 | `tests/test_memory_manager.py` | `ProjectMemoryManager` 请求前、记录、后台笔记和生命周期测试 |
| 修改 | `tests/test_llm_base.py` | `FRAMEWORK_CONTEXT` 来源测试 |
| 修改 | `tests/test_prompt_builder.py` | 框架上下文注入和不污染 memory 的测试 |
| 修改 | `tests/test_agent_loop.py` | 项目记忆接入顺序、记录、失败和异步触发测试 |
| 修改 | `tests/test_session.py` | `/clear` 下项目记忆状态复位测试 |
| 修改 | `tests/test_cli.py` | CLI 装配、错误处理和关闭顺序测试 |
| 修改 | `tests/test_docs.py` | README Stage 08 文档契约测试 |
| 新建 | `tests/test_project_memory_e2e.py` | 正常恢复和故障恢复端到端场景 |
| 修改 | `README.md` | Stage 08 配置、存储、指令、会话、笔记和边界文档 |

## T1：建立 Stage 08 数据模型与框架消息来源

**文件：** `src/mycode/memory/models.py`、`src/mycode/memory/__init__.py`、`src/mycode/llm/base.py`、`tests/test_memory_models.py`、`tests/test_llm_base.py`  
**依赖：** 无

**步骤：**
1. 编写模型测试，固定 `MemoryScope`、`MemoryKind`、`InstructionLayer`、`SessionRecordType`、`FrameworkContextKind`、`NoteUpdateAction` 的枚举值。
2. 编写 dataclass 测试，断言 `MemoryDiagnostic`、`InstructionBlock`、`InstructionLoadResult`、`SessionRecord`、`SessionSummary`、`SessionRestoreResult`、`MemoryNote`、`MemoryIndexBundle`、`FrameworkContextBlock`、`FrameworkContext`、`NoteUpdateDecision` 和 `NoteUpdateResult` 均为不可变对象；其中 `SessionRestoreResult` 还应包含 `time_gap_block` 字段。
3. 编写 `MessageOrigin.FRAMEWORK_CONTEXT == "framework_context"` 测试，确认该来源只供内部区分，协议层现有 origin 忽略测试继续通过。
4. 实现 `memory.models` 中 plan.md 定义的全部枚举与数据结构，并在 `memory.__init__` 导出供 Agent、Prompt 和测试使用的公共类型。

**验证：** `python -m pytest tests/test_memory_models.py tests/test_llm_base.py -q`，期望模型字段、不可变性和消息来源测试全部通过。

## T2：实现持久化路径与安全边界

**文件：** `src/mycode/memory/paths.py`、`tests/test_memory_paths.py`  
**依赖：** T1

**步骤：**
1. 编写路径测试，使用临时 `workspace_root` 和 `home`，断言 `project_digest` 等于工作区真实路径 UTF-8 字节的 SHA-256 十六进制值。
2. 编写目录测试，断言 `project_store_root`、`sessions_dir`、`project_memory_dir` 和 `user_memory_dir` 分别落在 `~/.mycode/projects/<digest>/`、`sessions/`、`memory/` 和 `~/.mycode/memory/`。
3. 编写 `ensure_directories()` 测试，断言只创建会话目录、项目记忆目录和用户记忆目录，不创建 `.mewcode` 路径。
4. 编写路径校验测试，覆盖合法项目文件、合法用户文件、`..` 路径穿越、绝对路径越界和符号链接逃逸；拒绝诊断不能包含越界文件正文。
5. 实现 `MemoryPaths`，用 `Path.resolve()` 比较真实父路径；在关键拒绝分支写中文原因注释。

**验证：** `python -m pytest tests/test_memory_paths.py -q`，期望路径计算、目录创建和越界拒绝测试全部通过。

## T3：加载三层项目指令并确定性渲染

**文件：** `src/mycode/memory/instructions.py`、`tests/test_memory_instructions.py`  
**依赖：** T1、T2

**步骤：**
1. 编写三层扫描测试，同时创建 `mycode.md`、`.mycode/instructions.md` 和用户目录 `~/.mycode/instructions.md`，断言 blocks 顺序为项目根、项目 `.mycode`、用户指令。
2. 编写缺失文件测试，断言不存在的三层指令文件不会产生错误诊断，结果只包含实际存在的文件。
3. 编写渲染测试，断言 `rendered_text` 包含每个 block 的稳定标题、原文、路径和 SHA-256，且多次 load 输出完全一致。
4. 编写诊断测试，模拟可读文件中的非法 include 行以外内容仍被保留，诊断只记录 code、path 和 line。
5. 实现 `InstructionLoader.load()` 的基础扫描、优先级、哈希和渲染逻辑。

**验证：** `python -m pytest tests/test_memory_instructions.py -q`，期望三层顺序、缺失容忍和确定性渲染测试通过。

## T4：实现 `@include` 展开与安全诊断

**文件：** `src/mycode/memory/instructions.py`、`tests/test_memory_instructions.py`  
**依赖：** T3

**步骤：**
1. 编写合法 include 测试，在项目指令中写入 `@include docs/rules.md`，断言被包含内容出现在父文件对应位置。
2. 编写嵌套深度测试，构造 6 层 include，断言超过默认深度 5 的文件不被读取，并产生 `include_depth_exceeded` 诊断。
3. 编写循环测试，构造 A include B、B include A，断言第二次访问同一真实路径时产生 `include_cycle` 诊断且渲染终止。
4. 编写安全测试，覆盖 include `../secret.md`、用户级 include 跳出 `~/.mycode`、符号链接逃逸和不存在文件。
5. 实现 include 递归展开、visited 集合和路径边界检查；用中文注释说明 include 不能继承普通文件读取授权的原因。

**验证：** `python -m pytest tests/test_memory_instructions.py -q`，期望合法 include、生效顺序和所有拒绝诊断通过。

## T5：实现 JSONL 会话追加与派生元数据

**文件：** `src/mycode/memory/sessions.py`、`tests/test_memory_sessions.py`  
**依赖：** T1、T2

**步骤：**
1. 编写会话 ID 测试，用固定 `now()` 断言新会话 ID 匹配 `YYYYMMDD-HHMMSS-xxxx`，同一秒连续创建不会重复。
2. 编写追加测试，调用 `append_message()` 和 `append_messages()` 写入 user、assistant、tool 三类 `ChatMessage`，断言文件为 UTF-8 JSONL 且一行一个对象。
3. 编写派生元数据测试，断言 `list_sessions()` 从 JSONL 扫描得到 `session_id`、第一条 user 派生标题、有效消息数、最近更新时间和 `recoverable=True`。
4. 编写无 meta 测试，断言 `sessions_dir` 下只出现 `.jsonl` 文件，不创建 `.json`、`.meta` 或 sidecar 文件。
5. 实现 `SessionArchiveStore.start_new_session()`、`append_message()`、`append_messages()`、`list_sessions()` 和 `close()`。

**验证：** `python -m pytest tests/test_memory_sessions.py -q`，期望追加、扫描派生和无 meta 文件测试通过。

## T6：恢复 JSONL 历史并截断不完整工具边界

**文件：** `src/mycode/memory/sessions.py`、`tests/test_memory_sessions.py`  
**依赖：** T5

**步骤：**
1. 编写恢复测试，断言有效 JSONL 被恢复为 `ChatMessage`，已知 `origin` 被还原，未知 `origin` 降级为 `MessageOrigin.CONVERSATION`。
2. 编写坏行测试，混入非 JSON、缺少 role/content 的结构错误行和最后半行，断言有效消息继续恢复，`skipped_lines` 计数正确。
3. 编写工具边界测试，构造 assistant tool call 后有匹配 tool result 的会话，断言完整工具历史保留。
4. 编写悬空工具边界测试，构造末尾 assistant tool call 没有 tool result，断言恢复历史截断到该 assistant tool call 之前，`truncated_at_boundary=True`。
5. 实现逐行解析、结构校验、坏行诊断、工具边界闭合检查和 `restore_latest()`；在截断分支写中文原因注释。

**验证：** `python -m pytest tests/test_memory_sessions.py -q`，期望坏行容错、unknown origin 降级和工具边界截断测试通过。

## T7：选择最近可恢复会话、时间跨度和过期清理

**文件：** `src/mycode/memory/sessions.py`、`tests/test_memory_sessions.py`  
**依赖：** T6

**步骤：**
1. 编写最近会话测试，创建多个未过期 JSONL，断言 `latest_recoverable_session()` 选择 `updated_at` 最大且 `recoverable=True` 的会话。
2. 编写过期过滤测试，创建超过 `max_age_days=30` 的会话，断言它不会被自动恢复。
3. 编写时间跨度测试，断言 `restore_latest()` 返回恢复会话距当前 `now()` 的 `time_gap_seconds`，并保留 `time_gap_block` 字段供上层按阈值决定是否注入提醒。
4. 编写清理测试，断言 `cleanup_expired()` 删除 30 天以上会话，但不删除当前 `current_session_id` 对应文件。
5. 实现最近选择、更新时间解析、时间差计算和过期清理；删除文件失败时返回诊断并继续处理其他会话。

**验证：** `python -m pytest tests/test_memory_sessions.py -q`，期望最近选择、过期过滤、时间跨度和清理测试通过。

## T8：读取笔记、frontmatter 和受限索引

**文件：** `src/mycode/memory/notes.py`、`tests/test_memory_notes.py`  
**依赖：** T1、T2

**步骤：**
1. 编写 frontmatter 解析测试，创建带 `id`、`kind`、`updated_at`、`source_session_id` 的 Markdown 笔记，断言 `MemoryNote` 字段和正文正确。
2. 编写用户级/项目级隔离测试，断言 `load_note_summaries(USER)` 只读取 `~/.mycode/memory/`，`load_note_summaries(PROJECT)` 只读取项目记忆目录。
3. 编写索引加载测试，创建 `index.md`，断言 `MemoryIndexBundle.entries`、`rendered_text`、`line_count`、`byte_count` 和 `truncated=False` 正确。
4. 编写索引限制测试，构造超过 200 行和超过 25KB 的索引，断言按确定性顺序截断，并产生 `memory_index_truncated` 诊断。
5. 实现窄范围 YAML-like frontmatter 解析、索引读取、摘要读取和路径校验；用中文注释说明索引大小限制是提示词预算边界。

**验证：** `python -m pytest tests/test_memory_notes.py -q`，期望笔记解析、隔离和索引限制测试通过。

## T9：应用自动笔记决策并保证写入安全

**文件：** `src/mycode/memory/notes.py`、`tests/test_memory_notes.py`  
**依赖：** T8

**步骤：**
1. 编写 `CREATE` 测试，断言新笔记文件名为稳定 slug 加短哈希，frontmatter 写入 scope、kind、updated_at 和 source_session_id，索引原子重建。
2. 编写 `UPDATE` 测试，断言目标笔记正文被替换，`updated_at` 更新，旧文件路径不变。
3. 编写 `MERGE` 测试，断言新正文追加到目标笔记正文末尾，中间使用空行、`---`、空行分隔，并更新索引。
4. 编写 `IGNORE` 和非法决策测试，断言忽略动作只增加 ignored 计数；缺少 scope、kind、title、body 或 target_note_id 的写入动作产生诊断且不落盘。
5. 编写失败安全测试，模拟笔记写入失败和索引替换失败，断言已有笔记和旧索引保持不变。
6. 实现 `apply_decisions()`、原子索引替换和失败回滚。

**验证：** `python -m pytest tests/test_memory_notes.py -q`，期望四种动作、非法决策和失败安全测试通过。

## T10：构造并解析自动笔记更新提示

**文件：** `src/mycode/memory/note_prompt.py`、`tests/test_memory_note_prompt.py`  
**依赖：** T1、T8

**步骤：**
1. 编写提示构造测试，断言 prompt 包含用户消息、assistant 消息、用户级索引、项目级索引、四种 `MemoryKind` 说明和默认归属规则。
2. 编写输出格式测试，断言 prompt 明确要求 JSON 对象、顶层 `decisions` 数组，并说明不要返回工具调用。
3. 编写解析成功测试，覆盖 `create`、`merge`、`update` 和 `ignore` 四种 action，断言枚举、scope、kind 和字段被正确转换。
4. 编写解析失败测试，覆盖非 JSON、缺少 `decisions`、`decisions` 非数组、未知 action、未知 scope、未知 kind 和写入动作缺少必要字段。
5. 实现 `NoteUpdatePrompt.build()` 和 `parse()`；解析失败返回空 tuple 或只保留合法 ignore，并产生可由 manager 记录的诊断。

**验证：** `python -m pytest tests/test_memory_note_prompt.py -q`，期望提示内容和解析边界测试通过。

## T11：实现请求前长期上下文刷新门面

**文件：** `src/mycode/memory/manager.py`、`tests/test_memory_manager.py`  
**依赖：** T3、T7、T8

**步骤：**
1. 编写刷新测试，断言 `before_user_request()` 每次都会调用 `InstructionLoader.load()`、`MemoryNoteStore.load_index_bundle(USER)` 和 `MemoryNoteStore.load_index_bundle(PROJECT)`。
2. 编写首次恢复测试，断言进程第一次请求前调用 `SessionArchiveStore.cleanup_expired()` 和 `restore_latest()`，并把恢复历史写入 `ConversationMemory.replace()`。
3. 编写只恢复一次测试，断言后续普通请求不会重复恢复旧会话，但仍会重新加载指令和索引。
4. 编写框架上下文测试，断言返回的 `FrameworkContext.blocks` 至少包含 instructions 和 memory_index；当 `SessionRestoreResult.time_gap_block` 存在且时间间隔超过 `time_gap_notice_seconds` 时额外包含 restore_notice。
5. 实现 `ProjectMemoryManager.before_user_request()` 的刷新、恢复状态和 block 组装逻辑。

**验证：** `python -m pytest tests/test_memory_manager.py -q`，期望刷新频率、首次恢复和框架上下文测试通过。

## T12：复用 Stage 07 压缩保护恢复历史

**文件：** `src/mycode/memory/manager.py`、`tests/test_memory_manager.py`  
**依赖：** T11

**步骤：**
1. 编写 compact 回调测试，传入 restored_history 后由 `compact_prepare(restored_history)` 返回压缩后的历史，断言 `ConversationMemory.replace()` 使用压缩结果。
2. 编写无回调测试，断言没有 `compact_prepare` 时恢复历史原样进入 memory，并产生 `restore_compaction_unavailable` 诊断。
3. 编写压缩失败测试，让回调抛出异常，断言 `FrameworkContext.diagnostics` 包含 `restore_compaction_failed`，且不会继续覆盖为不安全历史。
4. 编写诊断内容测试，断言诊断只包含稳定 code、message 和会话路径，不包含完整恢复正文。
5. 实现恢复预算保护分支；manager 不直接导入 `compact` 包，只通过回调复用 Stage 07 能力。

**验证：** `python -m pytest tests/test_memory_manager.py -q`，期望成功压缩、无回调降级和失败诊断测试通过。

## T13：记录会话历史并异步更新自动笔记

**文件：** `src/mycode/memory/manager.py`、`tests/test_memory_manager.py`  
**依赖：** T9、T10、T11

**步骤：**
1. 编写记录测试，断言 `record_user_message()`、`record_assistant_message()` 和 `record_tool_history()` 分别追加 user、assistant tool call、tool result 和 assistant final 到当前 JSONL。
2. 编写去重测试，断言同一条 final assistant 消息不会因为 `record_assistant_message()` 和 `after_final_response()` 被写入两次。
3. 编写异步笔记测试，调用 `after_final_response()` 后立即返回；后台任务随后使用 `llm.stream_chat([prompt], tools=[])`、`NoteUpdatePrompt.parse()` 和 `MemoryNoteStore.apply_decisions()`。
4. 编写模型工具调用测试，fake LLM 返回 `TOOL_CALL` 时本次笔记更新失败并形成诊断，不修改任何笔记。
5. 编写后台失败测试，模拟 LLM 错误、解析错误和写入错误，断言 final response 流程不受影响。
6. 实现记录方法、后台任务调度和内部诊断收集。

**验证：** `python -m pytest tests/test_memory_manager.py -q`，期望 JSONL 记录、无重复写入和异步笔记测试通过。

## T14：实现项目记忆 clear 与 close 生命周期

**文件：** `src/mycode/memory/manager.py`、`tests/test_memory_manager.py`  
**依赖：** T13

**步骤：**
1. 编写 `clear_session_state()` 测试，断言当前会话关闭并调用 `SessionArchiveStore.start_new_session()`，同时进程内恢复标记复位。
2. 编写 clear 后恢复测试，断言 `/clear` 后下一次请求不会把刚清理前的短期 memory 覆盖回来。
3. 编写 `close()` 测试，断言未完成后台笔记任务被取消或收尾，`SessionArchiveStore.close()` 被调用一次。
4. 编写重复 close 测试，断言多次调用不会重复取消已完成任务或抛出异常。
5. 实现生命周期方法，并在后台写入取消分支写中文原因注释。

**验证：** `python -m pytest tests/test_memory_manager.py -q`，期望 clear、恢复标记和 close 测试通过。

## T15：让 PromptBuilder 注入框架上下文

**文件：** `src/mycode/prompt/models.py`、`src/mycode/prompt/builder.py`、`tests/test_prompt_builder.py`  
**依赖：** T1

**步骤：**
1. 编写模型测试，定义 `PromptContextBlock(id, kind, priority, content)`，并让 `TurnPromptContext` 携带 `framework_blocks`。
2. 编写 `begin_turn()` 测试，断言调用方传入 framework blocks 后被不可变保存；未传入时默认空 tuple。
3. 编写 build 注入测试，断言框架上下文按 `priority`、`id` 稳定排序并合并为一个 `<framework-context>` user-role 消息。
4. 编写边界测试，断言框架上下文消息的 `origin` 为 `MessageOrigin.FRAMEWORK_CONTEXT`，位于普通 history 之后、system reminder 和 environment context 之前。
5. 编写污染防护测试，断言传入 history 不会被修改，framework context 不进入 `ConversationMemory`。
6. 实现 `PromptContextBlock`、`TurnPromptContext.framework_blocks`、`PromptBuilder.begin_turn()` 参数和 `build()` 注入逻辑。

**验证：** `python -m pytest tests/test_prompt_builder.py -q`，期望框架上下文排序、位置和来源测试通过。

## T16：在 Agent 请求前接入项目记忆刷新

**文件：** `src/mycode/agent/loop.py`、`tests/helpers.py`、`tests/test_agent_loop.py`  
**依赖：** T12、T15

**步骤：**
1. 在测试辅助中增加 `PassthroughProjectMemoryManager`，记录 `before_user_request()`、记录方法、`after_final_response()` 和 `clear_session_state()` 调用。
2. 编写顺序测试，断言 `ProjectMemoryManager.before_user_request()` 发生在当前 user message append 之前，随后才 yield `USER_MESSAGE`。
3. 编写框架上下文传递测试，断言 manager 返回的 blocks 被转换为 `PromptContextBlock` 并传给 `PromptBuilder.begin_turn(framework_blocks=...)`。
4. 编写无项目记忆测试，断言 `project_memory=None` 时现有 AgentLoop 事件顺序和请求内容不变。
5. 实现 `AgentLoop.__init__(project_memory=None)` 和 run 前刷新；compact 回调用现有 `ContextManager` 包装恢复历史，manager 不直接认识 compact 实现。

**验证：** `python -m pytest tests/test_agent_loop.py -q`，期望请求前顺序、框架上下文和无项目记忆回归测试通过。

## T17：同步记录 Agent 历史并在 final 后触发笔记

**文件：** `src/mycode/agent/loop.py`、`tests/test_agent_loop.py`  
**依赖：** T13、T16

**步骤：**
1. 编写 user 记录测试，断言当前 user message 写入 `ConversationMemory` 后同步调用 `project_memory.record_user_message()`。
2. 编写 assistant final 测试，断言 assistant final message 写入 memory 后调用 `record_assistant_message()`，随后先 yield `FINAL_RESPONSE`，再调用 `after_final_response()`。
3. 编写工具历史测试，覆盖 assistant tool call、成功 tool result、权限拒绝 tool result 和工具失败 tool result，断言都调用 `record_tool_history()`。
4. 编写非污染测试，断言 system instruction、system reminder、environment context 和 framework context 都不会写入 JSONL 记录接口。
5. 实现所有 `_memory.append(...)` 附近的项目记忆通知，保持既有工具调度、权限审批和事件流顺序。

**验证：** `python -m pytest tests/test_agent_loop.py tests/test_agent_plan_only.py tests/test_permission_e2e.py -q`，期望新增记录测试和既有审批场景通过。

## T18：处理恢复失败和 `/clear` 生命周期

**文件：** `src/mycode/agent/loop.py`、`src/mycode/session.py`、`tests/test_agent_loop.py`、`tests/test_session.py`  
**依赖：** T14、T16

**步骤：**
1. 编写恢复失败测试，manager 返回 `restore_compaction_failed` 诊断时，Agent 产出 `ERROR`，不追加当前 user，不调用常规 LLM。
2. 编写普通诊断测试，include 缺失、坏行跳过和索引截断等非致命诊断不阻断常规请求。
3. 编写 `AgentLoop.clear_memory()` 测试，断言先调用 `ContextManager.clear()`，再调用 `ProjectMemoryManager.clear_session_state()`。
4. 编写 `ChatSession.clear()` 测试，断言 session 仍只转发 Agent clear、复位 `plan_only`、清会话权限，不直接读写 project memory。
5. 实现诊断分类和 clear 接线；保持 `project_memory=None` 时 clear 行为不变。

**验证：** `python -m pytest tests/test_agent_loop.py tests/test_session.py -q`，期望恢复失败保护和 clear 生命周期测试通过。

## T19：在 CLI 中装配 ProjectMemoryManager

**文件：** `src/mycode/cli.py`、`tests/test_cli.py`  
**依赖：** T2、T3、T7、T9、T10、T14、T18

**步骤：**
1. 编写 CLI 装配测试，断言 `_run_application()` 用当前 `Path.cwd()`、`Path.home()`、共享 `memory` 和同一个 `llm` 创建 `MemoryPaths`、`InstructionLoader`、`SessionArchiveStore`、`MemoryNoteStore`、`NoteUpdatePrompt` 和 `ProjectMemoryManager`。
2. 编写注入测试，断言创建的 `ProjectMemoryManager` 被传入 `AgentLoop(project_memory=...)`，且 ContextManager 仍使用同一个 `ConversationMemory`。
3. 编写错误测试，模拟目录创建或路径解析失败，断言 CLI 返回非零退出码，stderr 输出 `myCode 项目记忆错误`，不泄露目标文件正文。
4. 编写关闭顺序测试，断言 finally 中先 `await project_memory.close()`，再 `context_manager.close()`，最后 `await pool.close()`。
5. 实现 CLI 创建和关闭逻辑，保留现有 MCP 初始化、权限服务、compact artifact tool 和 TUI 创建顺序。

**验证：** `python -m pytest tests/test_cli.py -q`，期望装配、错误处理和资源关闭测试全部通过。

## T20：导出创建入口并保持包边界

**文件：** `src/mycode/memory/__init__.py`、`src/mycode/memory/manager.py`、`tests/test_memory_manager.py`、`tests/test_cli.py`  
**依赖：** T19

**步骤：**
1. 编写导出测试，断言 `from mycode.memory import ProjectMemoryManager, MemoryPaths, create_project_memory_manager` 可用。
2. 编写创建入口测试，断言 `create_project_memory_manager(workspace_root, home, llm, memory, now=...)` 组装默认组件，并调用 `paths.ensure_directories()`。
3. 编写包边界测试，断言 `memory.models`、`memory.paths`、`memory.instructions`、`memory.sessions`、`memory.notes` 和 `memory.note_prompt` 不导入 `mycode.agent`、`mycode.prompt.builder` 或 `mycode.compact`。
4. 调整 CLI 使用创建入口，减少 CLI 对 memory 内部类的手动拼装。
5. 实现导出和创建入口，避免 `memory.__init__` 导入会触发后台任务或 IO。

**验证：** `python -m pytest tests/test_memory_manager.py tests/test_cli.py -q`，期望创建入口和边界测试通过。

## T21：更新 README 与文档契约

**文件：** `README.md`、`tests/test_docs.py`  
**依赖：** T19、T20

**步骤：**
1. 在 README 增加 Stage 08 章节，说明项目指令文件 `mycode.md`、`.mycode/instructions.md`、`~/.mycode/instructions.md` 的加载顺序和 `@include` 安全边界。
2. 说明会话 JSONL 路径 `~/.mycode/projects/<workspace_sha256>/sessions/`、30 天清理、无 meta sidecar、坏行跳过和工具边界截断。
3. 说明用户级笔记 `~/.mycode/memory/`、项目级笔记 `~/.mycode/projects/<workspace_sha256>/memory/`、`index.md`、200 行/25KB 限制和自动笔记四类。
4. 说明 Stage 08 不做向量数据库、RAG、团队同步、图形化管理 UI、`.mewcode` 迁移和真实 LLM 自动化验收。
5. 扩展 `tests/test_docs.py`，断言 README 包含 Stage 08、三层指令、JSONL、自动恢复、自动笔记、`~/.mycode/projects`、`index.md`、`.mewcode` 不支持和高级检索不做。

**验证：** `python -m pytest tests/test_docs.py -q`，期望 README 契约测试通过。

## T22：覆盖正常恢复端到端流程

**文件：** `tests/test_project_memory_e2e.py`  
**依赖：** T17、T19、T21

**步骤：**
1. 构造真实 `InMemoryConversationMemory`、`PromptBuilder`、`ToolRegistry`、`ContextManager` passthrough fake、`ProjectMemoryManager` 和脚本 LLM。
2. 第一轮发送用户消息并获得 final response，断言生成当前项目 JSONL，会话记录包含 user 和 assistant final。
3. 写入用户级和项目级 `index.md`，并创建 `mycode.md` 指令；模拟重启时创建新的 memory 和新的 `ProjectMemoryManager`。
4. 第二轮发送新请求，断言系统自动恢复最近会话，LLM 请求中包含旧历史、项目指令和记忆索引框架上下文。
5. 让 fake LLM 完成自动笔记更新，断言笔记文件和索引结果符合 `NoteUpdateDecision.CREATE`。

**验证：** `python -m pytest tests/test_project_memory_e2e.py::test_project_memory_restores_recent_session_and_injects_memory -q`，期望正常接续场景通过。

## T23：覆盖故障恢复端到端流程

**文件：** `tests/test_project_memory_e2e.py`  
**依赖：** T18、T22

**步骤：**
1. 构造包含坏 JSONL 行、结构错误行、末尾悬空 assistant tool call、超过预算的恢复历史和损坏 `index.md` 的项目状态。
2. 使用 compact 回调 fake 把恢复历史压缩为安全历史，断言 Agent 没有向 LLM 发送超预算原历史。
3. 断言恢复诊断包含坏行数量、工具边界截断、索引降级和压缩结果，且不包含坏行正文或越界文件内容。
4. 再构造 compact 回调失败场景，断言 Agent 返回 `COMPACTION_ERROR` 或项目记忆恢复错误，不调用常规 LLM。
5. 断言整个流程不创建 `.mewcode` 路径、不改变权限审批或工具调度语义。

**验证：** `python -m pytest tests/test_project_memory_e2e.py::test_project_memory_faults_are_recovered_or_blocked_safely -q`，期望故障恢复和不安全阻断场景通过。

## T24：执行 Stage 08 回归与静态检查

**文件：** 本阶段全部新增与修改文件  
**依赖：** T1-T23

**步骤：**
1. 运行 Python 编译检查，修复导入环、语法错误和未导出类型。
2. 运行 Stage 08 专项测试，确认 paths、instructions、sessions、notes、note_prompt、manager、prompt、agent、cli 和 e2e 全部通过。
3. 运行 Stage 07、Agent、权限、plan-only、tool、MCP、协议、Session、TUI 和 docs 回归测试，确认既有行为不变。
4. 运行完整测试套件和 `git diff --check`，修复尾随空格、冲突标记和测试临时文件泄漏。
5. 对照 spec 的 F1-F18、N1-N13 和 AC1-AC23 确认每条至少有一个测试或文档断言覆盖。

**验证：** `python -m compileall -q src`、`python -m pytest -q`、`git diff --check` 均以退出码 0 完成。

## 需求覆盖

| Spec | 实现与验证任务 |
|---|---|
| F1 | T11、T16、T21、T22 |
| F2 | T3、T15、T21、T22 |
| F3 | T4、T21、T23 |
| F4 | T5、T13、T17、T22 |
| F5 | T5、T7、T22 |
| F6 | T7、T11、T16、T22 |
| F7 | T6、T23 |
| F8 | T6、T17、T23 |
| F9 | T12、T16、T18、T23 |
| F10 | T7、T11、T15、T22 |
| F11 | T7、T11、T21 |
| F12 | T1、T9、T10、T13、T22 |
| F13 | T13、T17、T22 |
| F14 | T8、T9、T10、T13 |
| F15 | T8、T9、T11、T21 |
| F16 | T8、T11、T15、T22 |
| F17 | T2、T19、T21、T23 |
| F18 | T8、T9、T10、T21 |
| N1 | T2、T4、T6、T8、T9、T23 |
| N2 | T3、T4、T5、T7、T8 |
| N3 | T5、T6、T7、T23 |
| N4 | T11、T13、T23 |
| N5 | T2、T7、T8、T19、T21 |
| N6 | T15-T19、T24 |
| N7 | T1、T3、T6、T8、T12、T13、T23 |
| N8 | T5、T9、T13、T23 |
| N9 | T15、T17、T22、T23 |
| N10 | T1-T20、T24 |
| N11 | T2、T4、T6、T9、T13、T24 |
| N12 | T2-T24 |
| N13 | T2-T21、T24 |

## 执行顺序

```text
T1
├─ T2
│  ├─ T3 → T4
│  ├─ T5 → T6 → T7
│  └─ T8 ─┬─ T9
│         └─ T10
└─ T15

T3 + T7 + T8 → T11 → T12
T9 + T10 + T11 → T13 → T14
T12 + T15 → T16 → T17 → T18
T2 + T3 + T7 + T9 + T10 + T14 + T18 → T19 → T20
T19 + T20 → T21 → T22 → T23
T1-T23 → T24
```

T3/T4、T5/T6/T7、T8/T9 和 T10 在 T1/T2 后可并行推进，其中 T10 只需等 T8 完成即可开始。T15 可在 memory 存储实现之前完成，但 T16 必须等 T12 和 T15 都完成后再接入 Agent。T22 和 T23 只在 CLI、README 和所有单元集成测试通过后执行。

## 建议提交点

| 完成任务 | 提交信息 |
|---|---|
| T1-T4 | `feat: add project memory models and instructions` |
| T5-T7 | `feat: archive and restore project sessions` |
| T8-T10 | `feat: add file backed memory notes` |
| T11-T14 | `feat: orchestrate project memory refresh` |
| T15-T18 | `feat: integrate project memory into agent prompts` |
| T19-T21 | `feat: wire project memory into cli and docs` |
| T22-T23 | `test: cover project memory end to end` |
| T24 | `test: verify stage 08 project memory` |
