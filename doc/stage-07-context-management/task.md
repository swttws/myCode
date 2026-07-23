# Stage 07 上下文管理 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `src/mycode/compact/__init__.py` | compact 包稳定导出和创建入口 |
| 新建 | `src/mycode/compact/models.py` | 配置、固定策略、结果、错误码 |
| 新建 | `src/mycode/compact/estimator.py` | 字符估算、请求快照和 usage 锚点 |
| 新建 | `src/mycode/compact/archive.py` | 缓存事务、会话锁、生命周期和受限读取工具 |
| 新建 | `src/mycode/compact/light.py` | 工具结果轻量归档 |
| 新建 | `src/mycode/compact/summary_prompt.py` | 摘要提示、输出解析和八章节校验 |
| 新建 | `src/mycode/compact/summary.py` | 重量压缩、递归强制压缩和应急压缩 |
| 新建 | `src/mycode/compact/manager.py` | 请求前编排、重试、熔断和创建入口 |
| 修改 | `src/mycode/llm/base.py` | 增加三种压缩消息来源 |
| 修改 | `src/mycode/config.py` | 解析并校验 compact 配置 |
| 修改 | `src/mycode/memory/base.py` | 增加历史原子替换接口 |
| 修改 | `src/mycode/memory/in_memory.py` | 实现历史原子替换 |
| 修改 | `src/mycode/protocols/openai_chat.py` | 解析 Chat 流式 usage |
| 修改 | `src/mycode/protocols/openai_responses.py` | 解析 Responses 完成事件 usage |
| 修改 | `src/mycode/protocols/anthropic.py` | 累积 Anthropic 流式 usage |
| 修改 | `src/mycode/agent/events.py` | 增加压缩事件、报告和错误码 |
| 修改 | `src/mycode/agent/loop.py` | 接入请求前管理、usage 回写和手动压缩 |
| 修改 | `src/mycode/session.py` | 转发手动压缩和上下文清理 |
| 修改 | `src/mycode/tui.py` | 增加 `/compact` 与中文状态输出 |
| 修改 | `src/mycode/cli.py` | 创建管理器、注册归档工具和退出清理 |
| 新建 | `tests/test_compact_estimator.py` | 估算器单元测试 |
| 新建 | `tests/test_compact_archive.py` | 缓存、事务、安全读取和生命周期测试 |
| 新建 | `tests/test_compact_light.py` | 单项与同批工具结果归档测试 |
| 新建 | `tests/test_compact_summary_prompt.py` | 摘要提示和解析测试 |
| 新建 | `tests/test_compact_summary.py` | 近期选择、摘要、递归和应急测试 |
| 新建 | `tests/test_compact_manager.py` | 请求前编排、重试和熔断测试 |
| 新建 | `tests/test_context_compaction_e2e.py` | 正常与故障端到端场景 |
| 修改 | `tests/test_llm_base.py` | 压缩消息来源测试 |
| 修改 | `tests/test_config.py` | compact 配置加载和失败测试 |
| 修改 | `tests/test_memory.py` | 历史替换测试 |
| 修改 | `tests/test_openai_chat_protocol.py` | Chat usage fixture |
| 修改 | `tests/test_openai_responses_protocol.py` | Responses usage fixture |
| 修改 | `tests/test_anthropic_protocol.py` | Anthropic usage fixture |
| 修改 | `tests/test_agent_events.py` | 压缩事件和错误码测试 |
| 修改 | `tests/helpers.py` | 提供显式 passthrough ContextManager 测试替身 |
| 修改 | `tests/test_agent_loop.py` | 自动、手动、usage 和失败接入测试 |
| 修改 | `tests/test_agent_plan_only.py` | 为既有 Agent 场景注入测试上下文管理器 |
| 修改 | `tests/test_permission_e2e.py` | 为权限端到端场景注入测试上下文管理器 |
| 修改 | `tests/test_session.py` | compact 转发和 clear 测试 |
| 修改 | `tests/test_tui.py` | `/compact` 命令和状态测试 |
| 修改 | `tests/test_cli.py` | 管理器、工具注册和资源回收测试 |
| 修改 | `tests/test_docs.py` | 示例配置和 README 契约测试 |
| 修改 | `examples/mycode.anthropic.yaml` | 增加上下文窗口示例 |
| 修改 | `examples/mycode.openai-chat.yaml` | 增加上下文窗口和 usage 示例 |
| 修改 | `examples/mycode.openai-responses.yaml` | 增加上下文窗口示例 |
| 修改 | `README.md` | 记录配置、命令、归档和故障行为 |

## T1：定义 compact 核心模型与消息来源

**文件：** `src/mycode/compact/models.py`、`src/mycode/llm/base.py`、`tests/test_compact_manager.py`、`tests/test_llm_base.py`  
**依赖：** 无

**步骤：**
1. 写测试固定 `CompactConfig`、`CompactPolicy`、动作、状态、失败码、报告和结果对象的字段与默认值。
2. 写测试固定 `COMPACT_PREVIEW`、`COMPACT_SUMMARY`、`COMPACT_BOUNDARY` 三种内部来源，确认来源仍不会成为供应商字段。
3. 运行目标测试，确认因类型或枚举缺失而失败。
4. 在 `compact.models` 实现 plan.md 定义的不可变模型和 `CompactError`，在消息来源枚举中增加三项。

**验证：** `python -m pytest tests/test_compact_manager.py tests/test_llm_base.py -q`，期望相关测试全部通过。

## T2：加载 compact 配置

**文件：** `src/mycode/config.py`、`tests/test_config.py`  
**依赖：** T1

**步骤：**
1. 写测试覆盖缺少 `compact`、缺少 `context_window_tokens`、使用 8K/12K 默认值和显式覆盖阈值。
2. 运行目标测试，确认加载器尚不认识 compact 配置而失败。
3. 为 `LLMConfig` 增加 compact 配置，并增加严格 mapping、整数和布尔值拒绝逻辑。
4. 保持 API key、thinking 和 usage 的现有解析行为不变。

**验证：** `python -m pytest tests/test_config.py -q`，期望配置加载测试全部通过。

## T3：校验预算组合

**文件：** `src/mycode/compact/models.py`、`src/mycode/config.py`、`tests/test_config.py`  
**依赖：** T2

**步骤：**
1. 写参数化测试覆盖非正数、预览不小于单项阈值、单项大于批次、批次达到自动安全线等非法组合。
2. 运行测试，确认非法配置仍被接受。
3. 实现 `0 < 2K < 单项 <= 批次 < 窗口 - 13K` 校验，并让错误文本指出具体字段。
4. 补充一组最小合法边界测试，防止比较符号写反。

**验证：** `python -m pytest tests/test_config.py -q`，期望合法边界通过、所有非法组合抛出 `ConfigError`。

## T4：实现完整字符估算与稳定请求快照

**文件：** `src/mycode/compact/estimator.py`、`tests/test_compact_estimator.py`  
**依赖：** T1

**步骤：**
1. 写测试覆盖纯 ASCII、纯中文、混合文本、消息字段、工具定义和不同输入顺序。
2. 运行测试，确认估算器缺失而失败。
3. 使用排序稳定、非 ASCII 不转义的 JSON 生成快照，统计 ASCII 与非 ASCII 字符并计算 SHA-256。
4. 实现 `ceil(ascii / 4) + ceil(non_ascii / 1.5)`，确认内部 origin 不进入供应商可见快照。

**验证：** `python -m pytest tests/test_compact_estimator.py -q`，期望字符估算和稳定 fingerprint 测试通过。

## T5：实现 usage 锚点与增量估算

**文件：** `src/mycode/compact/estimator.py`、`tests/test_compact_estimator.py`  
**依赖：** T4

**步骤：**
1. 写测试覆盖首次完整估算、有效 input usage、正负字符增量、缺失 usage、负 input 值和 reset。
2. 运行测试，确认当前估算器不会使用锚点。
3. 保存最近有效输入 Token 和对应快照估算，按 plan.md 公式计算 signed delta 并把结果钳制到 0；用中文注释说明锚点为何只接受 input usage。
4. 确认缺失或非法 usage 不覆盖旧锚点，reset 后恢复完整字符估算。

**验证：** `python -m pytest tests/test_compact_estimator.py -q`，期望 full_chars 与 usage_delta 两种来源均通过。

## T6：增加 Memory 原子替换

**文件：** `src/mycode/memory/base.py`、`src/mycode/memory/in_memory.py`、`tests/test_memory.py`  
**依赖：** 无

**步骤：**
1. 写测试确认 `replace()` 一次性替换完整历史、复制输入序列且不影响 append/clear。
2. 运行测试，确认抽象和实现均缺少该方法。
3. 在抽象类声明 `replace()`，在内存实现中用新列表一次赋值。

**验证：** `python -m pytest tests/test_memory.py -q`，期望 Memory 全部测试通过。

## T7：创建工作区和会话隔离缓存

**文件：** `src/mycode/compact/archive.py`、`tests/test_compact_archive.py`  
**依赖：** T1、T4

**步骤：**
1. 写测试固定工作区 SHA-256、会话 UUID、用户目录根路径和不同工作区/会话隔离。
2. 写测试模拟超过 24 小时的无锁遗留目录，以及仍持有活动锁的旧目录。
3. 运行测试，确认存储实现缺失。
4. 使用标准库实现目录创建、跨平台活动文件锁、启动清理和当前会话登记。

**验证：** `python -m pytest tests/test_compact_archive.py -q`，期望隔离、锁保护和过期清理测试通过。

## T8：实现归档事务与完整性信息

**文件：** `src/mycode/compact/archive.py`、`tests/test_compact_archive.py`  
**依赖：** T7

**步骤：**
1. 写测试覆盖 UTF-8 JSON envelope、精确正文、字符数、估算 Token、SHA-256、commit 和 rollback。
2. 运行测试，确认事务接口缺失。
3. 实现临时文件写入、flush 后原子重命名、提交登记和回滚删除；用中文注释说明文件提交与历史替换的先后约束。
4. 模拟写入异常，确认临时文件和允许路径集合均不残留。

**验证：** `python -m pytest tests/test_compact_archive.py -q`，期望提交、回滚和异常清理测试通过。

## T9：实现受限归档读取工具

**文件：** `src/mycode/compact/archive.py`、`tests/test_compact_archive.py`  
**依赖：** T8

**步骤：**
1. 写测试覆盖已提交路径、未提交路径、其他会话路径、路径穿越、符号链接、非法 offset 和超过 2K 的 max_tokens。
2. 写测试确认分片返回 `next_offset/eof`，多次读取可还原完整正文。
3. 运行测试，确认读取工具缺失。
4. 实现 `read_compact_artifact` 的只读定义、参数校验和基于估算器的字符切片；用中文注释说明不能复用工作区路径授权的原因。

**验证：** `python -m pytest tests/test_compact_archive.py -q`，期望安全拒绝与分片还原测试全部通过。

## T10：归档超大单个工具结果

**文件：** `src/mycode/compact/light.py`、`tests/test_compact_light.py`  
**依赖：** T4、T8

**步骤：**
1. 写测试构造超过单项阈值的工具消息，固定首尾预览、路径、原大小、截断标记和原 `tool_call_id`。
2. 运行测试，确认轻量压缩器缺失。
3. 实现单项扫描、精确原文归档和结构化 JSON 预览，预览最多 2K。
4. 读取归档并断言与原 `ChatMessage.content` 完全一致。

**验证：** `python -m pytest tests/test_compact_light.py -q`，期望单项归档测试通过。

## T11：按同次响应处理工具结果合计

**文件：** `src/mycode/compact/light.py`、`tests/test_compact_light.py`  
**依赖：** T10

**步骤：**
1. 写测试覆盖同轮多个 tool call/result、跨轮结果、相同大小稳定排序和最大项优先。
2. 写测试覆盖每次替换后重新估算，以及固定预览导致下一次归档不再减小的停止条件。
3. 运行测试，确认当前实现只处理单项。
4. 根据连续调用和 `tool_call_id` 建立批次，按估算大小降序、原索引稳定地处理。

**验证：** `python -m pytest tests/test_compact_light.py -q`，期望批次边界、顺序和停止条件通过。

## T12：保证轻量处理幂等与失败安全

**文件：** `src/mycode/compact/light.py`、`tests/test_compact_light.py`  
**依赖：** T11

**步骤：**
1. 写测试确认 `COMPACT_PREVIEW` 消息不会再次归档，未超过阈值的历史保持对象内容不变。
2. 注入归档写入失败，确认返回前不替换任何工具消息。
3. 运行测试，确认失败路径或幂等行为不满足。
4. 将候选选择与历史替换分离，只在事务写入全部成功后生成结果历史。

**验证：** `python -m pytest tests/test_compact_light.py -q`，期望幂等和失败安全测试通过。

## T13：构建并解析结构化摘要 Prompt

**文件：** `src/mycode/compact/summary_prompt.py`、`tests/test_compact_summary_prompt.py`  
**依赖：** T1

**步骤：**
1. 写测试固定中文禁止工具指令、JSON 数据区、草稿标签、正式标签和八个章节名。
2. 写参数化测试覆盖合法输出、缺草稿、缺摘要、标签顺序错误、缺章节和空章节。
3. 运行测试，确认构建器与解析器缺失。
4. 实现唯一提示模板和严格解析器，仅返回正式摘要正文。

**验证：** `python -m pytest tests/test_compact_summary_prompt.py -q`，期望合法解析和所有拒绝场景通过。

## T14：选择近期后缀并闭合工具边界

**文件：** `src/mycode/compact/summary.py`、`tests/test_compact_summary.py`  
**依赖：** T4、T13

**步骤：**
1. 写测试覆盖按约 10K 向前选择、Token 不足时至少 5 条、历史不足 5 条和正好位于边界。
2. 写测试构造同轮多个工具调用，确认任一保留结果都会把匹配调用及同组必要消息纳入近期区。
3. 运行测试，确认选择函数缺失。
4. 实现从尾部累计和基于 `tool_call_id` 的切点闭包，保持原顺序。

**验证：** `python -m pytest tests/test_compact_summary.py -q`，期望近期数量和工具边界测试通过。

## T15：构造用户原文优先的压缩历史

**文件：** `src/mycode/compact/summary.py`、`tests/test_compact_summary.py`  
**依赖：** T8、T14

**步骤：**
1. 写测试确认旧用户消息逐字保留，旧 assistant/tool 进入摘要输入，最终顺序为旧用户、assistant 摘要、user 边界、近期原文。
2. 写测试让旧用户原文本身阻止预算恢复，确认从最早用户消息开始归档并留下预览与路径。
3. 运行测试，确认历史布局函数缺失。
4. 实现正式摘要、边界消息和用户预览构造，全部使用 compact 内部来源。

**验证：** `python -m pytest tests/test_compact_summary.py -q`，期望用户原文、降级顺序和历史布局通过。

## T16：调用无工具摘要并丢弃草稿

**文件：** `src/mycode/compact/summary.py`、`tests/test_compact_summary.py`  
**依赖：** T5、T13

**步骤：**
1. 使用脚本 LLM 写测试，确认请求只有一个 user 消息且 `tools=[]`，TEXT_DELTA 被拼接，THINKING_DELTA 不进入正文。
2. 写测试覆盖模型工具调用、ERROR、缺 DONE、超时、取消、摘要超过 3K 和有效 usage 回写。
3. 运行测试，确认摘要收集器缺失。
4. 实现受 model timeout 与 run deadline 双重限制的流式收集和错误映射。

**验证：** `python -m pytest tests/test_compact_summary.py -q`，期望无工具、草稿丢弃和全部失败映射通过。

## T17：递归压缩单条超限消息

**文件：** `src/mycode/compact/summary.py`、`tests/test_compact_summary.py`  
**依赖：** T8、T16

**步骤：**
1. 写测试构造一条超过“窗口减 3K”的用户消息，确认先归档完整原文再按字符预算分片。
2. 写测试确认每个分片请求均在限制内、临时摘要不进入最终历史、归档可还原原文。
3. 运行测试，确认单消息超限无法处理。
4. 实现确定性字符分片、逐片临时摘要和无法缩小时的终止错误。

**验证：** `python -m pytest tests/test_compact_summary.py -q`，期望单消息分片、还原和终止保护通过。

## T18：递归收缩全量待摘要历史

**文件：** `src/mycode/compact/summary.py`、`tests/test_compact_summary.py`  
**依赖：** T17

**步骤：**
1. 写测试构造多条合计超过摘要窗口的历史，记录每次 LLM 输入顺序和估算大小。
2. 断言系统优先压缩最早可容纳块，重复收缩后仍执行一次覆盖全部工作副本的正式摘要。
3. 运行测试，确认当前实现不会递归收缩多消息历史。
4. 实现工作副本替换循环，并在每轮要求估算大小严格下降；用中文注释说明终止条件如何防止递归死循环。

**验证：** `python -m pytest tests/test_compact_summary.py -q`，期望多层收缩、最终全量摘要和临时内容隔离通过。

## T19：实现确定性应急压缩

**文件：** `src/mycode/compact/summary.py`、`tests/test_compact_summary.py`  
**依赖：** T8、T14、T15

**步骤：**
1. 写测试确认应急路径不调用 LLM，归档旧历史确定性 JSON，并生成索引、预览和边界。
2. 写测试让近期消息本身过大，确认保留消息数量但把必要正文替换为可恢复预览。
3. 写测试模拟归档失败，确认原历史不变且返回 archive error。
4. 实现本地应急构造和自动安全线复检。

**验证：** `python -m pytest tests/test_compact_summary.py -q`，期望无 LLM、可恢复、安全线和失败保护通过。

## T20：编排安全请求与轻量提交

**文件：** `src/mycode/compact/manager.py`、`src/mycode/compact/__init__.py`、`tests/test_compact_manager.py`  
**依赖：** T5、T6、T12、T18、T19

**步骤：**
1. 写测试记录 `轻量 → 重建请求 → 估算` 顺序，覆盖无变化安全返回和轻量变化提交。
2. 运行测试，确认 ContextManager 缺失。
3. 实现 `prepare_auto()` 的安全分支、轻量事务提交、Memory replace 和 PreparedContext。
4. 确认低于安全线时没有摘要 LLM 调用。

**验证：** `python -m pytest tests/test_compact_manager.py -q`，期望顺序、安全返回和轻量提交测试通过。

## T21：实现重量压缩三次完整尝试

**文件：** `src/mycode/compact/manager.py`、`tests/test_compact_manager.py`  
**依赖：** T20

**步骤：**
1. 写脚本测试让前两次完整流程失败、第三次成功，记录事务、Memory replace 和失败计数。
2. 运行测试，确认管理器没有重试。
3. 每次从同一份轻量后原历史开始新工作副本和新归档事务，失败回滚、成功提交。
4. 成功后重建完整请求并要求严格低于“窗口减 13K”，随后清零失败计数。

**验证：** `python -m pytest tests/test_compact_manager.py -q`，期望三次调用、一次历史替换和计数清零通过。

## T22：实现熔断与应急降级

**文件：** `src/mycode/compact/manager.py`、`tests/test_compact_manager.py`  
**依赖：** T21

**步骤：**
1. 写测试让三次完整尝试全部失败，确认熔断打开并立即执行一次应急压缩。
2. 写测试确认熔断期间再次超线不调用摘要 LLM，而是直接应急处理。
3. 运行测试，确认当前实现继续尝试摘要。
4. 实现会话级熔断状态、应急事务提交和安全线复检；用中文注释说明第三次失败到应急压缩的状态转换。

**验证：** `python -m pytest tests/test_compact_manager.py -q`，期望熔断、直接应急和继续返回安全请求通过。

## T23：实现手动压缩、清理和创建入口

**文件：** `src/mycode/compact/manager.py`、`src/mycode/compact/__init__.py`、`tests/test_compact_manager.py`  
**依赖：** T9、T22

**步骤：**
1. 写测试覆盖未到自动线仍手动压缩、无旧历史 no-op、熔断期间手动成功、手动失败保持熔断。
2. 写测试确认 `clear()` 清空 Memory/锚点/计数并轮换归档会话，`close()` 删除当前目录。
3. 运行测试，确认手动与生命周期接口缺失。
4. 实现 `compact_manual()`、`record_usage()`、`artifact_tool`、创建工厂和稳定导出。

**验证：** `python -m pytest tests/test_compact_manager.py -q`，期望手动、复位、清理和工厂测试通过。

## T24：归一化 OpenAI Chat usage

**文件：** `src/mycode/protocols/openai_chat.py`、`tests/test_openai_chat_protocol.py`  
**依赖：** T2、T5

**步骤：**
1. 写 fixture 覆盖 `request_stream_usage=true` 时发送 include_usage、最终空 choices usage chunk 和 `[DONE]`。
2. 写 fixture 覆盖 usage 缺失、字段缺失和格式错误时仍产生正常 DONE。
3. 运行测试，确认协议未发送或解析 usage。
4. 在不改变文本和工具调用顺序的前提下，把合法字段映射到 `UsageObservation` 并附在 DONE。

**验证：** `python -m pytest tests/test_openai_chat_protocol.py -q`，期望原协议测试及新增 usage fixture 全部通过。

## T25：归一化 OpenAI Responses usage

**文件：** `src/mycode/protocols/openai_responses.py`、`tests/test_openai_responses_protocol.py`  
**依赖：** T5

**步骤：**
1. 写 fixture 覆盖 `response.completed.response.usage` 的 input/output/total/cache 字段。
2. 写 fixture 覆盖缺失和格式错误 usage，不影响原有完成和失败事件。
3. 运行测试，确认 DONE 不携带 usage。
4. 增加窄范围解析函数并把统一观测附到完成事件。

**验证：** `python -m pytest tests/test_openai_responses_protocol.py -q`，期望原测试和 usage fixture 全部通过。

## T26：归一化 Anthropic usage

**文件：** `src/mycode/protocols/anthropic.py`、`tests/test_anthropic_protocol.py`  
**依赖：** T5

**步骤：**
1. 写 fixture 覆盖 message_start 输入/cache usage、message_delta 累计输出 usage 和 message_stop。
2. 写 fixture 覆盖字段缺失、顺序中夹杂文本/thinking 和无 usage 响应。
3. 运行测试，确认当前 stateless 映射无法附加 usage。
4. 在单次 stream_chat 内维护 usage 累积器，并只在停止事件产生统一 DONE usage。

**验证：** `python -m pytest tests/test_anthropic_protocol.py -q`，期望原测试和新增 usage fixture 全部通过。

## T27：增加 Agent 压缩事件契约

**文件：** `src/mycode/agent/events.py`、`tests/test_agent_events.py`  
**依赖：** T1

**步骤：**
1. 写测试固定 `COMPACTION` 事件值、`compaction` 报告字段和 `COMPACTION_ERROR` 错误码。
2. 运行测试，确认枚举与字段缺失。
3. 扩展现有事件模型，不改变已有枚举值和默认构造行为。

**验证：** `python -m pytest tests/test_agent_events.py -q`，期望事件契约测试全部通过。

## T28：在 Agent 请求前接入 ContextManager

**文件：** `src/mycode/agent/loop.py`、`tests/helpers.py`、`tests/test_agent_loop.py`、`tests/test_agent_plan_only.py`、`tests/test_permission_e2e.py`  
**依赖：** T20、T27

**步骤：**
1. 使用假的 ContextManager 写测试，记录 user append、PromptBuilder、prepare_auto 和 LLM 发送顺序。
2. 写测试确认 Agent 发送 PreparedContext.request，而不是压缩前请求；有动作时产生 COMPACTION 事件。
3. 写测试让 prepare_auto 抛出 `CompactError`，确认产生 COMPACTION_ERROR 且常规 LLM 未调用。
4. 实现每轮 request builder 闭包和必填 ContextManager 接线；在测试辅助层增加显式 passthrough fake，并更新所有直接构造 AgentLoop 的既有测试。

**验证：** `python -m pytest tests/test_agent_loop.py tests/test_agent_plan_only.py tests/test_permission_e2e.py -q`，期望新增顺序测试和全部既有 Agent 场景通过。

## T29：接入 Agent usage 回写与手动压缩

**文件：** `src/mycode/agent/loop.py`、`tests/test_agent_loop.py`  
**依赖：** T23、T24、T25、T26、T28

**步骤：**
1. 写测试确认常规 DONE usage 与发送请求的 RequestSnapshot 成对回写，再原样产生 USAGE 事件。
2. 写测试确认缺失 usage 不调用 record_usage。
3. 写测试调用 `AgentLoop.compact(mode)`，确认不追加用户消息、不调用普通聊天，只产生 COMPACTION 事件。
4. 复用现有 run deadline/model timeout 计算，完成 usage 和手动入口接线。

**验证：** `python -m pytest tests/test_agent_loop.py -q`，期望 usage、手动压缩和既有循环测试通过。

## T30：让 Session 转发压缩并统一 clear

**文件：** `src/mycode/session.py`、`tests/test_session.py`  
**依赖：** T29

**步骤：**
1. 写测试确认 `ChatSession.compact()` 传递当前 mode 并逐个转发 Agent 事件。
2. 更新 clear 测试，确认 Agent 清理会同时重置上下文管理器，随后再重置 mode 和权限。
3. 运行测试，确认 compact 方法缺失。
4. 添加薄转发方法，不在 Session 重复压缩判断。

**验证：** `python -m pytest tests/test_session.py -q`，期望转发顺序和现有 Session 测试通过。

## T31：增加 `/compact` TUI 命令与状态输出

**文件：** `src/mycode/tui.py`、`tests/test_tui.py`  
**依赖：** T30

**步骤：**
1. 扩展 FakeSession 并写测试确认 `/compact` 不进入 `send()`，而是消费 `session.compact()`。
2. 写参数化测试覆盖成功、无需压缩、三次失败后应急、不可恢复失败和熔断状态的中文输出。
3. 运行测试，确认命令被当作普通用户消息。
4. 增加命令分支和 COMPACTION 渲染，并把启动提示更新到 Stage 07。

**验证：** `python -m pytest tests/test_tui.py -q`，期望命令路由、中文状态和既有 TUI 测试通过。

## T32：在 CLI 装配并清理上下文管理器

**文件：** `src/mycode/cli.py`、`tests/test_cli.py`  
**依赖：** T2、T9、T23、T29

**步骤：**
1. 写测试确认 CLI 使用当前 workspace/home、LLM、Memory、compact config 和 timeout 创建管理器。
2. 写测试确认 `read_compact_artifact` 注册到同一 ToolRegistry，Agent 获得真实管理器。
3. 写测试覆盖正常退出、TUI 抛错和 MCP 初始化失败后的 context close/pool close 顺序。
4. 实现装配与 finally 清理；创建缓存失败时输出不含正文的中文错误并返回非零退出码。

**验证：** `python -m pytest tests/test_cli.py -q`，期望装配、工具注册和清理测试全部通过。

## T33：更新示例配置与用户文档

**文件：** `examples/mycode.anthropic.yaml`、`examples/mycode.openai-chat.yaml`、`examples/mycode.openai-responses.yaml`、`README.md`、`tests/test_docs.py`  
**依赖：** T2、T31、T32

**步骤：**
1. 在三个示例增加 `compact.context_window_tokens`，OpenAI Chat 示例展示可选 usage 开关。
2. 在 README 记录必填窗口、8K/12K 可调阈值、固定 2K/13K/3K/10K 策略、`/compact`、缓存位置和 24 小时清理。
3. 记录摘要熔断、应急压缩和 `read_compact_artifact` 分段读取行为。
4. 更新文档测试，确认所有示例均能加载且 README 包含关键命令和边界。

**验证：** `python -m pytest tests/test_docs.py tests/test_config.py -q`，期望文档契约和示例加载通过。

## T34：覆盖正常长会话端到端流程

**文件：** `tests/test_context_compaction_e2e.py`  
**依赖：** T32、T33

**步骤：**
1. 构造脚本 LLM 和真实 Memory/PromptBuilder/ToolRegistry/ContextManager，先产生多个超大工具结果。
2. 断言下一轮先轻量归档，继续增长后在普通请求前生成合法结构化摘要。
3. 让模型调用归档读取工具分片读取旧结果，确认能够恢复原始细节并完成最终文本。
4. 断言所有常规请求估算均低于对应安全线，事件顺序不破坏现有工具语义。

**验证：** `python -m pytest tests/test_context_compaction_e2e.py -q`，期望正常长会话场景通过。

## T35：覆盖摘要失败与熔断端到端流程

**文件：** `tests/test_context_compaction_e2e.py`  
**依赖：** T22、T34

**步骤：**
1. 让摘要 LLM 连续三次返回 API/格式失败，记录完整尝试次数和历史快照。
2. 断言系统打开熔断、归档旧历史、执行应急压缩并继续下一次常规请求。
3. 在熔断状态执行 `/compact` 并返回合法摘要，断言熔断和失败计数清零。
4. 增加归档写入失败分支，确认不发送不安全请求且原文仍在 Memory。

**验证：** `python -m pytest tests/test_context_compaction_e2e.py -q`，期望故障、恢复和不可恢复保护场景通过。

## T36：执行全量回归和静态编译检查

**文件：** 本阶段全部文件  
**依赖：** T1-T35

**步骤：**
1. 运行 Python 编译检查，修复导入环、语法错误和未导出类型。
2. 运行完整 pytest，修复所有上下文管理和既有功能回归。
3. 运行 `git diff --check`，修复尾随空格和冲突标记。
4. 对照 spec 的 F1-F17 和 plan 的接口逐项确认测试归属，同时检查关键路径中文注释和 compact 包边界，不以删测方式消除失败。

**验证：** `python -m compileall -q src`、`python -m pytest -q`、`git diff --check` 均以退出码 0 完成。

## 需求覆盖

| Spec | 实现与验证任务 |
|---|---|
| F1 | T1-T3 |
| F2 | T20、T28 |
| F3 | T8、T10 |
| F4 | T11、T12 |
| F5 | T7-T9、T23、T32 |
| F6 | T4、T5、T24-T26、T29 |
| F7 | T20-T22 |
| F8 | T23、T29-T31 |
| F9 | T14 |
| F10 | T15、T17、T19 |
| F11 | T13 |
| F12 | T13、T16 |
| F13 | T17、T18 |
| F14 | T8、T12、T21 |
| F15 | T19、T22、T23、T35 |
| F16 | T15、T19 |
| F17 | T12、T16、T19、T22、T27-T31、T35 |

## 执行顺序

```text
T1 → T2 → T3
 ├→ T4 → T5
 ├→ T6
 └→ T27

T1 + T4 → T7 → T8 → T9
T4 + T8 → T10 → T11 → T12
T1 → T13
T13 + T4 → T14
T8 + T14 → T15
T13 + T5 → T16 → T17 → T18
T8 + T14 + T15 → T19

T5 + T6 + T12 + T18 + T19 → T20 → T21 → T22 → T23
T2 + T5 → T24
T5 → T25、T26（可并行）
T20 + T27 → T28
T23 + T24 + T25 + T26 + T28 → T29 → T30 → T31
T2 + T9 + T23 + T29 → T32 → T33 → T34
T22 + T34 → T35
T1-T35 → T36
```

## 建议提交点

| 完成任务 | 提交信息 |
|---|---|
| T1-T6 | `feat: add compact configuration and token estimation` |
| T7-T12 | `feat: archive oversized tool results` |
| T13-T19 | `feat: add conversation summary compaction` |
| T20-T23 | `feat: orchestrate context compaction and fallback` |
| T24-T26 | `feat: normalize streaming usage observations` |
| T27-T32 | `feat: integrate context management into agent cli` |
| T33-T35 | `docs: document and verify context compaction` |
| T36 | `test: verify stage 07 context management` |
