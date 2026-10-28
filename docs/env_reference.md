# Hy Memory SDK 环境变量参考

本文只列当前代码实际读取、且对公开 SDK 功能有直接作用的配置。所有变量均可通过进程环境
或 `.env` 设置；未设置时使用表中的默认值。

## 核心配置

| 变量 | 默认值 | 说明 |
|---|---|---|
| `MEMORY_MODE` | `pro` | `lite` / `pro` / `ultra` |
| `MEMORY_DATA_DIR` | `~/.hy_memory` | 本地数据、缓存和日志根目录 |

- `lite`：纯 Embedding 写入，Graph 强制关闭。
- `pro`：LLM 提取与 reconcile；Graph 由 `MEMORY_ENABLE_GRAPH` 控制。
- `ultra`：具备 pro 能力并强制启用 Graph；System 2 由调用方显式执行
  `client.evolve()`，不会在 `add()` 后自动入队。

## LLM

| 变量 | 默认值 | 说明 |
|---|---|---|
| `MEMORY_LLM_PROVIDER` | `openai` | OpenAI-compatible Chat Completions 协议 |
| `MEMORY_LLM_MODEL` | `gpt-4.1-nano` | 模型名 |
| `MEMORY_LLM_API_KEY` | — | 回退：`LLM_API_KEY`、`OPENAI_API_KEY` |
| `MEMORY_LLM_BASE_URL` | — | 回退到 `LLM_BASE_URL` |
| `MEMORY_LLM_TEMPERATURE` | `0.1` | 统一温度；`none` / `omit` / 空值表示不发送 |
| `MEMORY_LLM_OMIT_TEMPERATURE` | `false` | `true` 时完全不发送 `temperature` |
| `MEMORY_LLM_MAX_TOKENS` | `1024` | 通用生成上限 |
| `MEMORY_AGENT_MAX_TOKENS` | `2000` | Extract/Reconcile/System 2 等 Agent 调用上限 |
| `MEMORY_LLM_TIMEOUT` | `180` | 单次超时，秒 |
| `MEMORY_LLM_MAX_RETRIES` | `5` | 最大重试次数 |
| `MEMORY_LLM_RETRY_DELAY` | `1.0` | 重试间隔，秒 |
| `MEMORY_LLM_EXTRA_HEADERS` | — | JSON object，附加 HTTP headers |
| `MEMORY_LLM_EXTRA_BODY` | — | JSON object，附加请求 body |
| `MEMORY_FEW_SHOT_ENABLED` | `false` | 为内置 Extractor/Reconcile prompt 附加 few-shot |
| `MEMORY_ENABLE_INTENTION` | `true` | 是否抽取并写入 L7 Intention |
| `MEMORY_LOG_LLM_CONTENT` | `false` | `true` 才允许日志记录完整 prompt/response |

## Embedding

| 变量 | 默认值 | 说明 |
|---|---|---|
| `MEMORY_EMBEDDER_PROVIDER` | `openai` | OpenAI-compatible Embeddings 协议 |
| `MEMORY_EMBEDDER_MODEL` | `text-embedding-3-small` | 模型名 |
| `MEMORY_EMBEDDER_API_KEY` | — | 回退：`EMBEDDING_API_KEY`、`OPENAI_API_KEY` |
| `MEMORY_EMBEDDER_BASE_URL` | — | 回退到 `EMBEDDING_BASE_URL` |
| `MEMORY_EMBEDDING_DIMS` | `1536` | Collection 维度；应与模型输出一致 |
| `MEMORY_EMBEDDER_SEND_DIMENSIONS` | `true` | 是否向 Embeddings API 发送 `dimensions`；固定输出维度但不接受此参数的模型设为 `false`，仍保留 `MEMORY_EMBEDDING_DIMS` 校验响应与 Collection 维度 |
| `MEMORY_EMBEDDER_TIMEOUT` | `15` | 单次超时，秒 |
| `MEMORY_EMBEDDER_MAX_RETRIES` | `2` | 最大重试次数 |
| `MEMORY_EMBEDDER_RETRY_DELAY` | `0.3` | 重试间隔，秒 |
| `MEMORY_EMBEDDER_EXTRA_HEADERS` | — | JSON object，附加 HTTP headers |
| `MEMORY_EMBEDDER_EXTRA_BODY` | — | JSON object，附加请求 body |

例如固定输出 2560 维、但不接受 `dimensions` 参数的兼容服务，设置
`MEMORY_EMBEDDING_DIMS=2560` 和 `MEMORY_EMBEDDER_SEND_DIMENSIONS=false`。
Python 配置等价于 `EmbedderConfig(embedding_dims=2560, send_dimensions=False)`；
默认仍发送已配置的维度，保持现有支持降维的模型行为。响应维度与配置不符时立即失败，
不会为这个配置错误重复请求。

## Vector Store

| 变量 | 默认值 | 说明 |
|---|---|---|
| `MEMORY_VECTOR_STORE` | `chroma` | `chroma` / `qdrant` / `faiss` |
| `MEMORY_COLLECTION_NAME` | `agent_memories` | 主 collection 名 |
| `MEMORY_PERSIST_DIR` | `{DATA_DIR}/data/vector_db` | 本地持久化目录 |
| `MEMORY_VECTOR_HOST` | — | Qdrant 等远程后端主机 |
| `MEMORY_VECTOR_PORT` | — | 远程后端端口 |
| `MEMORY_VECTOR_API_KEY` | — | 远程后端 API Key |
`hybrid` 的关键词候选依赖后端实现。Chroma 会退化为纯向量；Qdrant 可使用
sparse/BM25 候选。

## Graph Store

| 变量 | 默认值 | 说明 |
|---|---|---|
| `MEMORY_ENABLE_GRAPH` | `false` | 只在 pro 生效；lite 强制关，ultra 强制开 |
| `MEMORY_GRAPH_PROVIDER` | `kuzu` | `kuzu` / `neo4j` |
| `MEMORY_GRAPH_DB_PATH` | `{DATA_DIR}/data/kuzu_db` | Kuzu 数据库路径 |
| `NEO4J_URL` | `bolt://localhost:7687` | Neo4j Bolt URL；可回退到 `MEMGRAPH_URL` |
| `NEO4J_USERNAME` | — | 可回退到 `MEMGRAPH_USERNAME` |
| `NEO4J_PASSWORD` | — | 可回退到 `MEMGRAPH_PASSWORD` |
| `NEO4J_DATABASE` | `neo4j` | Neo4j database |

Graph 写入和读侧 Rerank 的高级开关见 [pro_graph_design.md](pro_graph_design.md)。

## Cache

| 变量 | 默认值 | 说明 |
|---|---|---|
| `MEMORY_CACHE_BACKEND` | `sqlite` | `sqlite` / `mysql`；不支持 Redis |
| `MEMORY_CACHE_DB_PATH` | `{DATA_DIR}/data/cache.db` | SQLite 文件路径 |
| `MEMORY_MYSQL_HOST` | `localhost` | MySQL 主机 |
| `MEMORY_MYSQL_PORT` | `3306` | MySQL 端口 |
| `MEMORY_MYSQL_USER` | `root` | MySQL 用户 |
| `MEMORY_MYSQL_PASSWORD` | — | MySQL 密码 |
| `MEMORY_MYSQL_DATABASE` | `hy_memory` | MySQL 数据库 |
| `MEMORY_MYSQL_POOL_SIZE` | `10` | 连接池大小 |
| `MEMORY_MYSQL_POOL_RECYCLE` | `3600` | 连接回收周期，秒 |

## 写入、场景与 Query Rewrite

| 变量 | 默认值 | 说明 |
|---|---|---|
| `RECONCILE_ENABLED` | `true` | 是否执行候选检索和 reconcile |
| `MEMORY_ENABLE_SEARCH_QUERY` | `false` | Reconcile 前是否额外用 LLM 生成检索 query |
| `MEMORY_EXTRACT_HISTORY_TURNS` | `5` | 本次输入之外注入 Extractor 的历史 user-turn 数 |
| `MEMORY_CODING_ENABLED` | `true` | 是否启用 working/coding 独立链路 |
| `MEMORY_CODING_DB_PATH` | `{DATA_DIR}/data/coding_memory.db` | 生产力记忆 SQLite 路径 |
| `MEMORY_CODING_TOOL_RESULT_MAX_BYTES` | `2048` | 单条 tool result 的最大字节数 |
| `MEMORY_CODING_VERBATIM_FALLBACK_ENABLED` | `false` | 无抽取结果时是否把渲染对话保存成 working fact |
| `MEMORY_ENABLE_QUERY_REWRITE` | `false` | Coding 关闭时控制 default 多-query 是否 rewrite；Coding 开启时多-query 无论此值都会分类并 rewrite |
| `MEMORY_QUERY_REWRITE_MAX_CHARS` | `1000` | default 多-query rewrite 长度保护 |

默认单 query Search 是零 LLM light search。`search_mode=agentic` 下，单 query、多 queries
都会执行一次合并的场景判断、rewrite 和时间解析。default 多-query 超过长度保护时不做
rewrite；Coding 开启时只分类，关闭时零 LLM。

## System 2 与 User Profile

| 变量 | 默认值 | 说明 |
|---|---|---|
| `SWEEPER_ENABLED` | `true` | Ultra evolve 是否执行 cross-domain sweeper |
| `MEMORY_SUMMARY_ENABLED_IN_SYS2` | `false` | System 2 material 是否包含 L3 Summary |
| `MEMORY_EVOLING_BALANCED_CATALOG_ENABLED` | `false` | Ultra evolve 是否生成 balanced catalog |
| `MEMORY_EVOLING_BALANCED_CATALOG_MAX_FACTS` | `120` | 单次最多审阅的 L2 数 |
| `MEMORY_EVOLING_BALANCED_CATALOG_CONTENT_CHARS` | `180` | Prompt 中每条 L2 的字符上限 |
| `MEMORY_EVOLING_BALANCED_CATALOG_MAX_TOKENS` | `512` | Catalog 选择输出上限 |
| `MEMORY_EVOLING_SPECIFIC_SUPPLEMENT_ENABLED` | `false` | Ultra evolve 是否生成 supplement |
| `MEMORY_USER_PROFILE_BALANCED_CATALOG_ENABLED` | `false` | `get_user_profile()` 是否附加 active catalog |
| `MEMORY_USER_PROFILE_SPECIFIC_SUPPLEMENT_ENABLED` | `false` | 是否附加 active supplement |

Catalog 和 supplement 只由 `user_id` 确定，不作为普通 L6 出现在 Search，也不占
`schema_limit`。

### Cross-domain Sweeper

| 变量 | 默认值 | 说明 |
|---|---|---|
| `MEMORY_CROSS_DOMAIN_ENABLE` | `true` | 是否执行 cross-domain 碰撞扫描 |
| `MEMORY_CROSS_DOMAIN_MIN_BASICS` | `5` | 进入通用碰撞扫描所需的最低 schema 数 |
| `MEMORY_CROSS_DOMAIN_BEH_THRESHOLD` | `0.70` | 行为向量相似度阈值 |
| `MEMORY_CROSS_DOMAIN_CON_THRESHOLD` | `0.60` | 内容向量的跨域差异阈值 |

## Reader 与本地 Rerank

| 变量 | 默认值 | 说明 |
|---|---|---|
| `HY_MEMORY_READER` | `legacy` | `legacy`（向量）/ `hybrid`（向量 + 后端关键词候选） |
| `MEMORY_HYBRID_V2_VDB_WEIGHT_BM25` | `0.35` | Hybrid BM25 加成权重；当前仍保留旧前缀 |
| `MEMORY_LOCAL_RERANK_ENABLED` | `false` | 两个 Reader 共用的本地 cross-encoder Rerank |
| `MEMORY_LOCAL_RERANK_MODEL` | `nreimers/mmarco-mMiniLMv2-L6-H384-v1` | 小型多语言 Hugging Face 模型名或本地路径 |
| `MEMORY_LOCAL_RERANK_DEVICE` | `auto` | `auto` / `cpu` / `cuda` 等 device |
| `MEMORY_LOCAL_RERANK_BATCH_SIZE` | `16` | 一次 forward 的 batch，不是请求并发限制 |
| `MEMORY_LOCAL_RERANK_MAX_LENGTH` | `512` | Tokenizer 最大长度 |
| `MEMORY_LOCAL_RERANK_POOL_FACTOR` | `3` | 候选池相对最终 limit 的倍数 |
| `HY_MEMORY_TAG_INDEX_WRITE` | `true` | 是否维护写侧 Tag Index |

SDK 不设置 Rerank Search 并发上限。开启后模型在 `HyMemoryClient` 初始化阶段加载并
执行一次预热；初始化失败会直接阻止 Client ready。运行中的单次推理异常仍 fail-open。
详见 [hy_memory_search_skill.md](hy_memory_search_skill.md)。

## Episode 与摘要

| 变量 | 默认值 | 说明 |
|---|---|---|
| `MEMORY_EPISODE_GAP_SECONDS` | `1800` | 超过该间隔时开启新 episode |
| `MEMORY_EPISODE_MAX_MEMORIES` | `50` | 单 episode 的 user-side 记忆上限 |
| `MEMORY_EPISODE_SUMMARY_ENABLED` | `false` | Episode 结束时是否生成 L3 Summary |
| `MEMORY_EPISODE_SUMMARY_MIN_MEMORIES` | `3` | Summary 最低记忆数 |
| `MEMORY_EPISODE_RECALL_ENABLED` | `false` | Search 是否使用 Episode 关系加权 |
| `MEMORY_EPISODE_RECALL_WEIGHT` | `0.7` | Episode 结构支持权重 |
| `MEMORY_TOPIC_SUMMARY_ENABLED` | `false` | 是否生成 Topic Summary |
| `MEMORY_TOPIC_SUMMARY_MIN_MEMORIES` | `5` | Topic Summary 最低记忆数 |
| `MEMORY_TOPIC_SUMMARY_STEP` | `5` | Topic 新增多少条后重新生成 Summary |
| `MEMORY_SESSION_EPISODE_ENABLED` | `false` | 是否允许 `finalize_session()` 归档指定 session |
| `MEMORY_SESSION_EPISODE_CONTEXT_ENABLED` | `false` | Reader 是否召回 Session Episode 上下文 |
| `MEMORY_SESSION_EPISODE_LIMIT` | `5` | Session Episode 候选上限 |
| `MEMORY_SESSION_EPISODE_FACT_LIMIT` | `2` | 每个 Session Episode 附带事实数 |

## Recall 辅助能力

| 变量 | 默认值 | 说明 |
|---|---|---|
| `MEMORY_RECALL_STRENGTH_ENABLED` | `false` | 是否按访问次数和闲置时长重排 normal 结果 |
| `MEMORY_RECALL_STRENGTH_TAU` | `180` | 衰减时间常数，天 |
| `MEMORY_RECALL_ACCESS_TRACKING` | `true` | Strength 开启时是否回写访问信息 |
| `MEMORY_ENTITY_STORE_ENABLED` | `false` | 是否维护独立 entity collection；当前 Reader 不直接读取 |

旧数据可调用当前公开方法 `client.build_entity_store(user_id)` 补建 Entity Store。

## 日志、Trace 与历史

| 变量 | 默认值 | 说明 |
|---|---|---|
| `MEMORY_LOG_LEVEL` | `INFO` | 运行日志级别 |
| `MEMORY_LOG_FORMAT` | `text` | `text` / `json` |
| `MEMORY_LOG_DIR` | — | 统一日志根目录 |
| `MEMORY_TRACE_LOG_DIR` | — | JSONL 日志目录的兼容旧名；优先使用 `MEMORY_LOG_DIR` |
| `MEMORY_PIPELINE_TRACE_ENABLED` | `true` | 是否写入 cache 的 pipeline trace 表 |
| `MEMORY_MEMORY_OPERATIONS_ENABLED` | `false` | 是否写入 memory operation 审计记录 |
| `MEMORY_HISTORY_ENABLE` | `true` | 是否启用历史记录 |
| `MEMORY_HISTORY_DB_PATH` | `{DATA_DIR}/data/history.db` | History SQLite 路径 |
| `MEMORY_HISTORY_RECORD_SEARCHES` | `false` | 是否记录 Search 操作 |
| `MEMORY_CODING_CHAIN_REPO_ROOT` | — | coding 版本链读取本地 Git diff 的仓库根目录 |
| `MEMORY_CODING_CHAIN_BLOCK_ENABLED` | `false` | search(scene=coding) 是否注入版本演进链块（diff / `chain_block`）；默认关 |

## HTTP Server

| 变量 | 默认值 | 说明 |
|---|---|---|
| `HY_MEMORY_SERVER_HOST` | `127.0.0.1` | Server 监听地址 |
| `HY_MEMORY_SERVER_PORT` | `19527` | Server 监听端口 |

历史配置不会生效；当前支持项以本页表格和 `.env.example` 为准。Cache backend 当前仅支持
`sqlite` 和 `mysql`。
