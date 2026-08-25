# Stage-18 T14 修复记录

## 问题
需要静态检查守卫，确保 Stage-18 重构后不引入回归：
1. 核心 team 模块没有未授权的 `getattr()`/`hasattr()` 动态调用
2. 旧 mailbox 消费 API 不会泄漏回主链路
3. 各关键文件（supervisor/runtime/service/backend）没有旧消费入口

## 修复内容

### 新建 `tests/test_team_static.py`（11 个测试）

#### 1. TestTeamCoreNoDynamicAttributeCalls（2 tests）
- **test_no_unlisted_getattr_or_hasattr**: AST 扫描所有 `getattr`/`hasattr` 调用，必须全部在白名单中
- **test_whitelist_entries_still_exist**: 防止白名单条目过期导致假通过

**白名单（16 处，分类及原因）**:

| 分类 | 文件 | 行号 | 原因 |
|------|------|------|------|
| dataclass 字段校验 | models.py | 830,850,859,869,886,896 | `_normalize_*` 接收 `object` + `field_name`，`getattr` 是唯一合法方式 |
| dataclass 序列化 | storage.py | 307,309 | `_encode_dataclass` 遍历 `fields(record)`，`getattr` 是唯一合法方式 |
| dataclass 校验 | state.py | 71 | `__post_init__` 中遍历字段名元组 |
| enum 提取 | service.py | 66 | `_event_value` 提取 `.value` 或原值 |
| 可选属性 | service.py | 555,1414,1415 | `wake_endpoint`/`backend` 可选属性降级 |
| 配置降级 | worker.py | 105,229,235 | `getattr(config, ...)` 配置默认值 |

#### 2. TestOldMailboxConsumptionApiAbsent（5 tests）
- `MailboxStore`、`lead_unread`、`acknowledge_lead`、`watch_mailbox`、`.unread(` 文本扫描 → 全部零匹配

#### 3. TestPerFileOldConsumption（4 tests）
- supervisor.py: 无 `mailbox`/`unread`/`watch_mailbox`/`acknowledge_lead`
- runtime.py: 无 `mailbox`/`unread`/`acknowledge_lead`
- service.py: 无 `lead_unread`/`acknowledge_lead`/`MailboxStore`
- backends.py: 无 `run_until_idle`

## 测试结果
- 静态检查: 11/11 pass