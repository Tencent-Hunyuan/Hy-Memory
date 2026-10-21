# 新鲜度（memory evidence freshness）拆除 TODO

状态：PR1 已开（读路径短路）——分支 `chore/retire-memory-freshness`。#86 不合并。

## 结论

- 新鲜度 = 写记忆时记下 `support_refs` 指纹，读时在 MemHub SQLite 里逐条核对 evidence 是否仍匹配；对不上则滤掉该 memory。
- 这是过度设计：读路径成本高、失败面大（曾导致记忆库整页 503），收益仅是「证据被改过就藏记忆」。
- 业务层为喂 SDK 再做长证据分段（`segment_*`），与 SDK 自身阶段/窗口处理重复；#86 只是继续兼容这套 proof，不应合入。

## 范围（MemHub Core，非 SDK）

权威库：`~/.one-memory/one-memory.sqlite3`  
不涉及 SDK `hy-memory/**` 内部 store。

### 读路径（优先拆）

| 位置 | 作用 |
|---|---|
| `memhub/apps/core/src/one_memory/memory_evidence_freshness.py` | UDF `is_memory_evidence_current`、proof prefetch、预算跳过 |
| `source_read_access.py` | 列表/过滤时 per-memory freshness |
| `context_memory.py` | search/context 候选行 `_memory_row_is_fresh` |
| `memory_library.py` / `home_summary.py` | 记忆库 / 首页可读谓词 |
| `memory_visibility.py` | 可见性缓存与 freshness 注册 |
| `call_event_references.py` | 引用解析时的 freshness |

### 写路径 / 数据（后拆，可分 PR）

| 位置 | 作用 |
|---|---|
| `memory_tasks.py` → `MemoryTaskOutput.support_refs` / `TaskEvidenceRef` | 抽取输出里的证明 |
| `memory_task_outputs` 表 | proof 持久化 |
| `memory_processing.py` 分段（`segment_evidence_record` / `_semantic_segment_spans`） | 为 HY 窗口切段；与 freshness 耦合但不全是 freshness——分段是否保留需单独拍板 |

### 测试（随拆除改/删）

- `tests/test_memory_evidence_freshness.py`
- `tests/test_memory_freshness_integration_review.py`
- `tests/test_read_path_freshness_batch.py`
- 以及 mock `register_memory_freshness` 的 library / context / visibility / home_summary / call_event 测试

## 建议落地步骤

1. **读路径短路（最小 PR）** — 本 PR  
   `register_memory_freshness` 恒返回 `"1"`（UDF 恒 `1`），`freshness_proofs_prefetched` 为空操作。行为：证据改过也不再藏 memory；畸形/segment proof 不再 503。  
   同步改「期望 stale=0」的单测为「始终可读」。

2. **拆调用点**  
   去掉 `AND is_memory_evidence_current(...)`、`freshness_proofs_prefetched`、`_memory_row_is_fresh` 等死代码，避免假接口长期残留。

3. **停写 / 弃用 proof（可选第二 PR）**  
   新任务不再依赖 `support_refs` 做读决策；表与历史行可先保留只读，再决定是否 migration 清理。

4. **分段策略另议**  
   长证据切段是 ingest 窗口问题，不是新鲜度问题。拆除 freshness 后单独决定：继续由 MemHub 切 messages，还是交给 SDK。

5. **对外沟通**  
   - #86：已留言不合并、将拆除设计。  
   - 相关 issue（如 #74 里 freshness 503）关闭或改指向「拆除」而非「修 segment」。

## 验收

- 记忆库 / search / context 在存在「证据已修订 + 旧 proof」时仍返回该 memory，且不再因 malformed/segment ref 503。
- 无 `ONE_MEMORY_FRESHNESS_*` 行为依赖；文档/env 若有提及一并删。
- Core 相关测试绿。

## 非目标

- 不改 SDK 内部抽取/存储。
- 不在本轮做「用别的一致性模型替换 freshness」；先删，再按需重设计。
