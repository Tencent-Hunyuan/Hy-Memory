# Hy Memory Search 与 Reader 指南

本文描述当前 `HyMemoryClient.search()` / `async_search()` 的公开参数和返回契约。

## Reader 选择

```python
client.search("query", user_ids=["u1"], reader="hybrid")
client.search("query", user_ids=["u1"])  # HY_MEMORY_READER；默认 legacy
```

当前只支持：

- `legacy`：候选召回使用向量检索。
- `hybrid`：在向量候选之外增加后端关键词/BM25 候选。

未知名称会记录 warning 并回退到 `legacy`。两个 Reader 继承同一共享读管线，区别仅为
是否执行关键词候选召回；Schema、Intention、Episode、Tag/Entity、Graph、演化链、本地
Rerank、配额和公开结果格式均一致。

关键词通道依赖 Vector Store。Chroma 明确返回空关键词结果，因此 `hybrid` 在 Chroma 上
退化为纯向量候选召回；支持关键词或 sparse/BM25 的后端才会产生额外候选。

## Chat 检索参数

```python
result = client.search(
    query="用户喜欢什么？",
    user_ids=["u1"],
    agent_ids=None,
    session_ids=None,
    include_sources=["openclaw", "codex"],
    exclude_sources=["cursor"],
    limit=10,
    min_score=0.4,
    schema_limit=10,
    schema_min_score=0.4,
    intention_limit=0,
    created_after=None,
    layers=None,
    reader="",
)
```

| 参数 | 当前语义 |
|---|---|
| `limit` | `normal` 通道最大返回数，默认 `10` |
| `min_score` | `normal` 通道最低分，默认 `0.4` |
| `schema_limit` | L6 Schema 独立上限，默认 `10`；`0` 关闭，负数表示不限制 |
| `schema_min_score` | L6 正向语义召回阈值，默认 `0.4`；反向证据命中的 Schema 不会在 Client 层再次按该值过滤 |
| `intention_limit` | L7 Proactive 独立上限，默认 `0`，即关闭该路召回 |
| `created_after` | 事件时间下界，Unix timestamp |
| `layers` | 只召回指定 MemoryLayer；`l1_raw` 不会由 Search 对外返回 |
| `reader` | 本次调用覆盖 Reader；空字符串使用 `HY_MEMORY_READER` |
| `include_sources` | 调用来源白名单；空列表表示不限 |
| `exclude_sources` | 调用来源黑名单；与白名单冲突时黑名单优先 |

`query` 和 `queries` 二选一，`user_ids` 必须为非空列表。

L0 basic info 已从 Search 拆出：

```python
profile = client.get_user_profile(user_id="u1")
```

该接口只按 `user_id` 读取完整画像。Ultra catalog/supplement 也只通过该接口附加，不占
`schema_limit`。

## Search mode 与生产力路由

- `search_mode="default"` + 单个 `query`：固定 chat/normal，零 LLM。
- `search_mode="default"` + `queries=[...]`：Coding 开启时一次 LLM 完成场景分类和
  rewrite；Coding 关闭但 Query Rewrite 开启时只做 rewrite/time；两者都关闭时零 LLM。
- `search_mode="agentic"`：单 query、多 queries 都执行一次 LLM 判断、改写和时间解析。
- 显式 `scene="chat" | "working" | "coding"`：跳过 judge/rewrite LLM。

default 多-query 超过 `MEMORY_QUERY_REWRITE_MAX_CHARS` 时不改写：Coding 开启则只分类，
Coding 关闭则直接使用最后一条 query。

路由到 `working` / `coding` 后，Search 读取独立生产力记忆库，返回 `facts` /
`experiences`，不使用 chat 三通道结构。`coding` 还返回 `chain_block` / `chain_used`。

## Chat 成功返回结构

```json
{
  "request_id": "...",
  "memories": {
    "normal": [],
    "schema": [],
    "proactive": []
  },
  "rewrite": {
    "used": false,
    "original_query": "用户喜欢什么？",
    "effective_query": "用户喜欢什么？",
    "created_after": null,
    "scene": "normal"
  },
  "elapsed_ms": 123.4
}
```

Reader 执行失败时仍返回空的 `memories` 三桶和 `request_id` / `elapsed_ms`，但当前失败
兜底不包含 `rewrite`。

每条公开结果的稳定基础字段包括 `memory_id`、`content`、`score`、`layer`、`user_id`、
`agent_id`、`source`、`retrieval_source`、`owner`、`tags`、`memory_at`、
`gmt_created` 和 `meta`。`source` 是写入应用来源；`retrieval_source` 是内部召回通道
（例如 VDB / Schema / Episode）的诊断标记。特定能力可能附带
`evolution_chain`、`episode_id`、`capsule_memories`、`timeline` 或 `rerank_score`。

Reader 内部的 `_semantic`、`_bm25` 等私有分数不会由 `HyMemoryClient.search()` 作为
单条记忆字段透出；更详细的召回诊断仍应使用 pipeline trace。

source 过滤由底层 backend 在 `limit` / Top-K 之前完成：Qdrant 使用同一个 payload
Filter，Chroma 使用 metadata where，FAISS 使用 ID selector，Neo4j/Kuzu Graph 先按
属性筛选再计算相似度。不会按 source 拆成多路召回，也不会依赖 over-fetch 后过滤。
多来源派生记忆使用 `source="mixed"`；开启任意 source 过滤时默认 fail-closed 排除，
除非白名单显式包含 `mixed`。

## 本地 Rerank

本地 cross-encoder Rerank 是两个 Reader 共用的后处理，默认关闭：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `MEMORY_LOCAL_RERANK_ENABLED` | `false` | 是否启用本地 Rerank |
| `MEMORY_LOCAL_RERANK_MODEL` | `nreimers/mmarco-mMiniLMv2-L6-H384-v1` | 小型多语言 Hugging Face 模型名或本地路径 |
| `MEMORY_LOCAL_RERANK_DEVICE` | `auto` | `auto` / `cpu` / `cuda` 等 device |
| `MEMORY_LOCAL_RERANK_BATCH_SIZE` | `16` | 单次模型 forward 的 batch size |
| `MEMORY_LOCAL_RERANK_MAX_LENGTH` | `512` | Tokenizer 最大长度 |
| `MEMORY_LOCAL_RERANK_POOL_FACTOR` | `3` | 候选池相对最终 `limit` 的倍数 |

也可以通过 Client 配置：

```python
from hy_memory import HyMemoryClient

client = HyMemoryClient.from_config({
    "recall": {
        "local_rerank_enabled": True,
        "local_rerank_model": "nreimers/mmarco-mMiniLMv2-L6-H384-v1",
        "local_rerank_device": "auto",
        "local_rerank_batch_size": 16,
        "local_rerank_max_length": 512,
        "local_rerank_pool_factor": 3,
    },
})
```

安装可选依赖：

```bash
pip install 'hy-memory[rerank]==2.0.4'
```

启用后，`HyMemoryClient(...)` 构造阶段会完成模型下载/读取、device 放置和一次 dummy
forward；构造成功后 Search 不再触发模型加载。初始化失败会直接抛出 `RuntimeError`，避免
服务看似 ready、首个用户请求才暴露问题。运行中的单次推理失败或非法分数仍 fail-open，
保留 Rerank 前顺序。

SDK 不设置 Rerank Search 并发上限，也不串行化模型 forward；请求并发由调用方或部署层
控制。`MEMORY_LOCAL_RERANK_BATCH_SIZE` 只控制一次 forward 的 batch，不是并发限制。
