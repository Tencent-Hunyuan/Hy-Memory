# Hy Memory 使用指南

本文只描述当前 `HyMemoryClient` 已公开的方法和返回结构。

## 安装与快速开始

```bash
pip install hy-memory==2.0.4

# 可选后端
pip install 'hy-memory[qdrant]==2.0.4'
pip install 'hy-memory[faiss]==2.0.4'
pip install 'hy-memory[graph]==2.0.4'     # Neo4j；Kuzu 已在核心依赖中
pip install 'hy-memory[mysql]==2.0.4'
pip install 'hy-memory[rerank]==2.0.4'    # 本地多语言 cross-encoder rerank
```

默认 LLM 和 Embedding 均使用 OpenAI-compatible 接口。使用 OpenAI 时可只设置：

```bash
export OPENAI_API_KEY="sk-your-key"
```

```python
from hy_memory import HyMemoryClient

with HyMemoryClient(mode="pro") as client:
    client.add(
        "用户喜欢科幻电影，特别是《星际穿越》",
        user_id="user_001",
    )
    result = client.search(
        "用户喜欢什么？",
        user_ids=["user_001"],
    )
    for channel, memories in result["memories"].items():
        for memory in memories:
            print(channel, memory["score"], memory["content"])
```

## 配置

SDK 会加载项目目录中的 `.env`。完整配置见 [env_reference.md](env_reference.md)。

```bash
MEMORY_MODE=pro
MEMORY_LLM_MODEL=deepseek-chat
MEMORY_LLM_API_KEY=sk-your-key
MEMORY_LLM_BASE_URL=https://api.deepseek.com
MEMORY_EMBEDDER_MODEL=text-embedding-v4
MEMORY_EMBEDDER_API_KEY=sk-your-key
MEMORY_EMBEDDER_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
MEMORY_EMBEDDING_DIMS=1024
```

也可以使用当前公开的 `from_config()`：

```python
client = HyMemoryClient.from_config({
    "mode": "pro",
    "llm": {
        "model": "deepseek-chat",
        "api_key": "sk-xxx",
        "base_url": "https://api.deepseek.com",
    },
    "embedder": {
        "model": "text-embedding-v4",
        "api_key": "sk-xxx",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "embedding_dims": 1024,
    },
    "vector_store": {
        "provider": "chroma",
        "embedding_dims": 1024,
    },
})
```

通过 `from_config()` 配置时，Embedder 与 Vector Store 的维度是两个独立字段，必须保持
一致；环境变量方式则统一使用 `MEMORY_EMBEDDING_DIMS`。

支持的向量后端为 `chroma`、`qdrant`、`faiss`。Graph 后端通常使用
`kuzu` 或 `neo4j`。

## Client API

同步和异步方法使用相同参数。异步方法以 `async_` 为前缀。

### `add()` / `async_add()`

```python
result = client.add(
    [
        {"role": "user", "content": "推荐一部电影"},
        {"role": "assistant", "content": "推荐《星际穿越》"},
    ],
    user_id="user_001",
    agent_id="default_agent",
    session_id="default_session",
    source="openclaw",
    scene=None,
    memory_at=None,
    metadata=None,
    meta=None,
    embed_override=None,
)
```

`metadata` 与 `meta` 不是同一字段：

- `metadata` 是兼容旧调用方保留的 SDK 处理选项，会进入内部
  `WriteRequest.extra`，不保证持久化或随查询结果返回。
- `meta` 是调用方的业务上下文，会持久化并在 `search()`、`get()` 等结果中返回。
  同一记忆被多次合并时，各次 `meta` 会按写入顺序保存在列表中。

需要以后读回的数据必须放在 `meta`，不要放在 `metadata`。

`scene` 的公开取值：

- `None`：普通文本直接走 chat；messages 含 tool 时执行快速场景判断。
- `chat`：强制走普通记忆链路。
- `working`：强制走生产力记忆链路。
- `coding`：强制走软件工程变体，并维护版本演进链。

强制 `working` / `coding` 时输入必须含 tool 消息，否则回退 chat。普通 chat 写入返回
`success`、`scene`、`memory_id`、`request_id`、`elapsed_ms` 等字段；生产力链路返回其
双层 reconcile 操作、`experience_ids`、`fact_ids` 等字段，完整结构见
[scene_coding_usage.md](scene_coding_usage.md#4-返回结构)。

`mode="lite"` 不执行 LLM 抽取，写入内容直接以 `L1_RAW` 形式保存。lite 的 Search 是
严格的 raw-only 向量检索：无论调用方传入什么 `layers`、`scene` 或 `search_mode`，都只
召回 `L1_RAW`，不会进入 working/coding、Schema 或 Intention 链路，也不会调用场景判断
LLM。pro / ultra 仍遵循各自的层级与场景参数，默认不公开原始对话。

`source` 是调用方应用来源，例如 `openclaw`、`cursor`、`codex`。它与 `agent_id`
正交：前者说明“从哪个产品/入口写入”，后者仍是记忆隔离中的 agent 身份。source 会
执行 `strip().lower()` 后存储，并随 chat、working、coding 记忆一起返回。

### `search()` / `async_search()`

普通 chat 检索：

```python
result = client.search(
    query="用户喜欢什么？",
    user_ids=["user_001"],
    agent_ids=None,
    session_ids=None,
    include_sources=["openclaw", "codex"],
    exclude_sources=["cursor"],
    limit=10,
    min_score=0.4,
    schema_limit=10,
    schema_min_score=0.4,
    intention_limit=0,
    reader="",                  # HY_MEMORY_READER；默认 legacy
    layers=None,
    created_after=None,
    search_mode="default",
)
```

source 过滤语义为：

```text
source IN include_sources AND source NOT IN exclude_sources
```

任一列表为空时省略对应条件；两者冲突时黑名单优先。Chroma/Qdrant/Graph 会在数据库
候选截断前执行过滤，FAISS 使用 ID selector 在索引搜索前限定候选，不采用多路召回或
扩大 top-k 后过滤。多来源证据派生出的聚合记忆标记为 `mixed`；开启任意 source 过滤
时默认排除 `mixed`，只有显式将 `mixed` 放入白名单才返回。

chat 成功路径返回：

```python
{
    "request_id": "...",
    "memories": {
        "normal": [],      # L2/L3 普通记忆；L4/L5 当前未启用
        "schema": [],      # L6 Schema
        "proactive": [],   # L7 Intention；默认关闭
    },
    "rewrite": {
        "used": False,
        "original_query": "用户喜欢什么？",
        "effective_query": "用户喜欢什么？",
        "created_after": None,
        "scene": "normal",
    },
    "elapsed_ms": 12.3,
}
```

Reader 执行失败时返回空的 `memories` 三桶和 `request_id` / `elapsed_ms`；当前失败兜底不
包含 `rewrite`。

L0 用户画像不在 Search 中。`schema_limit` 只控制 L6，不会被 L0 占位。

Search 也可以路由到独立生产力记忆库：

```python
result = client.search(
    "这个异常最后是怎么修复的？",
    user_ids=["user_001"],
    scene="coding",
    workspace_id="repo-id",
    branch="main",
)
```

`working` / `coding` 路径返回 `scene="productivity"`、`facts`、`experiences`，不使用
chat 的三桶结构；`coding` 还会返回 `chain_block` 和 `chain_used`。

#### `search_mode`

| `search_mode` | `scene` | 行为 |
|---|---|---|
| `default` | `None` + 单个 `query` | 固定 chat/normal，零 LLM |
| `default` | `None` + `queries=[...]` | 行为由 Coding 与 Query Rewrite 开关共同决定，见下表 |
| `agentic` | `None` + 单/多 query | 一次 LLM 完成场景判断、改写和时间解析 |
| 任意 | 显式 `chat/working/coding` | 直接路由，不调用 judge/rewrite LLM |

`query` 与 `queries` 二选一；`queries` 最后一项是目标 query，前面的项用于上下文消解。
LLM 或解析失败时回退到 chat/normal 和原 query。

default 多-query 在总字符数不超过 `MEMORY_QUERY_REWRITE_MAX_CHARS` 时：

| `MEMORY_CODING_ENABLED` | `MEMORY_ENABLE_QUERY_REWRITE` | 行为 |
|---|---|---|
| `true` | 任意 | 一次 LLM 完成 `chat / working / coding` 分类和 query rewrite |
| `false` | `true` | 一次 LLM 完成 rewrite 与时间解析，不做场景分类 |
| `false` | `false` | 零 LLM，固定 chat/normal，使用最后一条 query |

超过长度保护时不做 rewrite；Coding 开启时只执行有界场景分类，关闭时零 LLM。

### `get_user_profile()` / `async_get_user_profile()`

用户画像只由 `user_id` 唯一确定，不接收 `agent_id` 或 `session_id`：

```python
result = client.get_user_profile(user_id="user_001")
profile = result["profile"]
```

```python
{
    "request_id": "...",
    "profile": {
        "memory_id": "...",
        "user_id": "user_001",
        "content": "...",
        "layer": "l0_basic_info",
        "basic_info": {"name": "Alice"},
        "catalog": {"attached": False},
        "supplement": {"attached": False},
        "tags": [],
        "memory_at": 1234567890,
        "gmt_created": 1234567890,
    },
    "elapsed_ms": 1.2,
}
```

不存在 L0 且没有可附加的 balanced catalog 时，`profile` 为 `None`。该接口不调用
Embedding 或 LLM。启用对应开关后，active catalog 和 supplement 会附加到同一画像；
catalog 也可以在没有基础 L0 时形成只读的合成画像。

### CRUD、列表与克隆

以下均为当前公开方法：

```python
memory = client.get("memory_id")
# lite 模式写入的是 L1_RAW；按 ID 读取时必须显式选择返回原文。
raw_memory = client.get("memory_id", include_raw=True)
result = client.update("memory_id", "更新后的内容")
result = client.delete("memory_id")
result = client.delete_all(user_id="user_001")

page = client.list_memories(
    user_id="user_001",
    limit=100,
    offset=0,
    order="desc",
    layers=None,
    with_evolution=False,
)

result = client.clone_user("user_001", "user_002", agent_id=None)
```

对应异步方法为 `async_get()`、`async_update()`、`async_delete()`、
`async_delete_all()`、`async_list_memories()`、`async_clone_user()`。

`get()` / `async_get()` 默认继续隐藏 `L1_RAW`，因此不会改变现有调用方的原文访问
边界。只有显式传入 `include_raw=True` 才返回该层；这也是 lite 模式按 ID 读取
写入内容及其 `meta` 的方式。MemHub 的 HTTP 读取接口不开放此参数。

普通 `list_memories()` 返回 `vdb` 桶，并在 Graph 有结果时附带 `graph`；传入
`workspace_id` 时返回生产力记忆的 `scene="productivity"` 和 `experiences`。

### `evolve()` / `async_evolve()`（Ultra）

System 2 不在 `add()` 中自动触发，当前也没有 `get_write_status()` 接口。调用方完成一批
写入后显式执行：

```python
result = client.evolve(
    user_id="user_001",
    agent_id="default_agent",
)
```

`evolve()` 仅允许 `mode="ultra"`；同步方法阻塞到本次认知加工完成。它读取当前 VDB
全量，生成或更新 L6 Schema，并执行配置允许的 System 2 处理。

### 其他当前公开方法

- `finalize_session()` / `async_finalize_session()`：在功能开关开启时归档指定 session。
- `get_user_basic_info()` / `async_get_user_basic_info()`：读取 L0 basic info 记录。
- `sweep()` / `async_sweep()`：Ultra 下显式执行 cross-domain sweeper。
- `get_metrics()` / `async_get_metrics()`：读取 SDK 指标。
- `build_memory_graph()` / `async_build_memory_graph()`：回填普通记忆关系图。
- `build_entity_store()` / `async_build_entity_store()`：回填独立 Entity Store。
- `history()` / `async_history()`、`get_recent_history()` / `async_get_recent_history()`。

## Embedding 端点覆盖

`add()` / `async_add()` / `search()` / `async_search()` 支持 per-call
`embed_override`：

```python
override = {
    "base_url": "http://embedding-host:8000/v1",
    "api_key": "sk-xxx",
    "extra_headers": {"X-Route": "direct"},
    "timeout": 15,
}
client.add("用户喜欢篮球", user_id="u1", embed_override=override)
client.search("兴趣爱好", user_ids=["u1"], embed_override=override)
```

它只覆盖接入端点，不覆盖 model、维度或 `extra_body`。新端点必须运行与已入库数据兼容的
Embedding 模型。

## 数据隔离与处理模式

普通记忆使用 `user_id → agent_id → session_id` 三层作用域。Search 必须传非空
`user_ids`；不传 `agent_ids` / `session_ids` 时可跨对应维度检索。

用户画像是例外：L0、balanced catalog 和 specific supplement 从生成到读取都只由
`user_id` 唯一确定。

- `lite`：原文向量化，不调用提取 LLM。
- `pro`：Embedding + LLM 提取 + reconcile；Graph 可通过 `MEMORY_ENABLE_GRAPH` 开启。
- `ultra`：具备 pro 能力并强制启用 Graph；System 2 由调用方显式调用 `evolve()`。

Reader、Rerank 和 Search 参数详见 [hy_memory_search_skill.md](hy_memory_search_skill.md)。
