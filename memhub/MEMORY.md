# Hy Memory 使用与配置

更新：2026-10-21

**文档约定：** 以 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) **Top Rules** 为现行权威。  
~~删除线~~ = 历史产品合同（曾要求走 Evidence / Governance 台账），**不再作为目标**；SDK 用法示例仍可用于开发调试。

---

## Top Rules（与架构一致 · 新）

1. Memory 读写与存储 backend **只经 SDK**（config / env）。
2. MemHub 对 memory 的原子操作 **只经 SDK**；缺接口先补 SDK。
3. MemHub **不存 memory 实体**；**可以存 `memory_id`** 做组织/回执。原文进 **SDK raw**，不进 MemHub Evidence 权威库。

生产入口仍建议经 `HyMemoryBridge`（共享 Runtime、模型配置、密钥、harness/model 门禁），但 bridge **不得**再把 SDK 结果镜像成第二套 memory 正文权威。

---

## ~~旧开篇（已废弃）~~

~~本文说明 MemHub 当前使用的 Hy Memory SDK… 仓库内仍以 One Memory 命名的 Evidence、权限和 Governance 层属于 MemHub 的历史兼容模块；产品代码不应绕过这些层直接调用 SDK。~~

~~MemHub 在本地 Runtime 内嵌入 SDK，但不绕过自身的来源授权、模型授权、Evidence、Governance 和撤权边界。~~

**新：** 来源授权、模型授权、宿主召回授权仍是 MemHub 管理面门禁。~~Evidence / Governance 记忆台账~~ 按 Top Rules 拆除；memory 真相在 SDK。

## 当前版本状态

当前源码固定使用（以仓库 pin / lockfile 为准）：

```text
distribution: hy-memory
version:      见 pyproject / lock（文档数字可能滞后）
mode:         按运行时配置选择
reader:       hybrid
```

## MemHub 运行时边界

### 新

MemHub 对 SDK 使用公开、可复现的本地策略（经 bridge / `hy_runtime_config` 注入）：

- vector / graph / cache / history / coding 等 **一律 SDK config/env**；
- 模型与 embedding 来自版本化 Model Profile；密钥进安全存储；
- MemHub 管理库可存 harness/model/队列状态与 **`memory_id` 引用**。

### ~~旧~~

~~- MemHub 不绕过 Evidence、Governance…~~  
~~- 生产路径不能用直接 `client.add()` 代替 Governance 检查。~~

发布包默认 SDK 行为环境变量仍集中在
`apps/core/src/one_memory/hy_runtime_config.py`，并由下表锁定：

| 变量 | 值 |
|---|---|
| `MEMORY_MODE` | `ultra` |
| `MEMORY_ENABLE_GRAPH` | `true` |
| `MEMORY_GRAPH_RERANK_ENABLED` | `false` |
| `MEMORY_GRAPH_TEMPORAL_SPINE` | `true` |
| `MEMORY_TOPIC_SUMMARY_ENABLED` | `false` |
| `MEMORY_EPISODE_SUMMARY_ENABLED` | `false` |
| `MEMORY_EPISODE_RECALL_ENABLED` | `true` |
| `MEMORY_DYNAMIC_EPISODE_ENABLED` | `true` |
| `MEMORY_SESSION_EPISODE_ENABLED` | `false` |
| `MEMORY_SESSION_EPISODE_CONTEXT_ENABLED` | `false` |
| `MEMORY_EPISODE_CAPSULE_CONTEXT_ENABLED` | `true` |
| `MEMORY_EPISODE_CAPSULE_QUERY_MIN` | `0.65` |
| `MEMORY_EPISODE_CAPSULE_ASSIGNMENT_MIN` | `0.60` |
| `MEMORY_EPISODE_CAPSULE_FIRST_RANK_MAX` | `3` |
| `MEMORY_EPISODE_CAPSULE_MEMBER_LIMIT` | `3` |
| `MEMORY_ENABLE_INTENTION` | `false` |
| `MEMORY_ENABLE_QUERY_REWRITE` | `false` |
| `MEMORY_FEW_SHOT_ENABLED` | `true` |
| `MEMORY_HYBRID_V2_QUERY_EXPAND` | `1` |
| `MEMORY_HYBRID_V2_GEO_INTENT_PRIOR` | `0.6` |
| `MEMORY_HYBRID_V2_DIVERSITY` | `1` |
| `MEMORY_HYBRID_V2_TEMPORAL_ADMIT_PRIOR` | `0.55` |
| `MEMORY_HYBRID_V2_TEMPORAL_BOOST` | `0.18` |
| `MEMORY_CROSS_DOMAIN_CON_THRESHOLD` | `0.5` |
| `MEMORY_READER_ENABLE_SUMMARY` | `false` |
| `MEMORY_SUMMARY_ENABLED_IN_SYS2` | `false` |
| `MEMORY_LOCAL_RERANK_ENABLED` | `false` |
| `RECONCILE_ENABLED` | `true` |
| `HY_MEMORY_READER` | `hybrid` |

独立脚本直连 SDK 时须自配存储目录与模型端点。

## 原生 SDK 初始化

下面是独立调试示例。

**新：** 产品代码经 `HyMemoryBridge` 获取已配置的 client（管理面门禁），memory I/O 仍是 SDK。  
~~旧：必须额外提供 Evidence 绑定、写后核验进 Governance。~~

```python
from hy_memory import HyMemoryClient, MemoryConfig

config = MemoryConfig.from_env()

# 本地优先示例；实际目录应由调用方传入。
config.vector_store.provider = "chroma"
config.vector_store.persist_directory = "/path/to/one-memory/hy-memory/vector"
config.graph_store.provider = "kuzu"
config.graph_store.db_path = "/path/to/one-memory/hy-memory/graph"
config.cache.backend = "sqlite"
config.cache.db_path = "/path/to/one-memory/hy-memory/cache.sqlite3"

config.llm.provider = "openai"
config.llm.model = "your-model"
config.llm.base_url = "https://your-openai-compatible-endpoint/v1"
config.llm.api_key = "read-from-secure-storage"

config.embedder.provider = "openai"
config.embedder.model = "your-embedding-model"
config.embedder.base_url = "https://your-embedding-endpoint/v1"
config.embedder.api_key = "read-from-secure-storage"
config.embedder.embedding_dims = 1024

client = HyMemoryClient(config=config, mode="ultra")
```

`embedding_dims` 必须与模型输出及已有向量/图存储一致。

## Write：写入对话

推荐把一段连续对话作为 OpenAI messages 写入（**含原文时由 SDK 接管 raw**，不再写入 MemHub Evidence 权威表）：

```python
from datetime import UTC, datetime
from uuid import uuid4

result = client.add(
    [
        {"role": "user", "content": "我长期偏好简洁、直接的回答。"},
        {"role": "assistant", "content": "明白，我会优先给结论。"},
    ],
    user_id="user-123",
    agent_id="all-apps",
    session_id="conversation-456",
    memory_at=datetime.now(UTC),
    scene="chat",
    request_id=str(uuid4()),
    metadata={
        "source_id": "cursor",
        # ~~evidence_ids 指向 MemHub Evidence 表~~ → 新：按 SDK raw / 元数据合同填写；勿依赖 MemHub evidence 权威
        "workspace_ids": ["workspace-abc"],
        "input_kind": "raw_conversation",
    },
)
```

| 参数 | 填法 | 约束 |
| --- | --- | --- |
| `data` | 字符串或 `[{role, content}]` | 对话优先 messages；不混入密钥与隐藏系统提示 |
| `user_id` / `agent_id` / `session_id` | 隔离与会话键 | 产品侧授权决定合法 scope |
| `memory_at` | 有时区 datetime | 历史导入用原始发生时间 |
| `scene` | `chat` / `working` | 明确传入 |
| `request_id` | 每次写入唯一 | 幂等与追踪 |
| `metadata` / `meta` | 来源与透传 | 不放秘密 |

MemHub Runtime 对应用和 CLI 暴露等价的本机 HTTP 合同：

```text
POST /api/v2/memory-writes
GET  /api/v2/user-profile

memhub write submit --messages-file messages.json --session <id>
memhub profile
```

`messages.json` 是 OpenAI 文本 messages 数组，当前接受 `user`、
`assistant`、`tool`；系统/开发者隐藏提示不得作为记忆输入。Core 将输入冻结进
`memory_write_jobs`，worker 对 SDK 执行一次 `add()`，成功后清除队列正文。
`user-profile` / `memhub profile` 调用 SDK `get_user_profile()`，返回组装后的
用户长期画像，不通过普通 Search 拼装。

返回值：

```python
if result.get("success") is not True:
    raise RuntimeError("HY write failed")

memory_ids = list(result.get("memory_ids") or [])
if result.get("memory_id"):
    memory_ids.append(result["memory_id"])
# 新：需要组织/回执时，MemHub 只持久化这些 memory_id
```

~~不要只相信 `success=True`。One Memory 还会…并在 Governance 层记录版本。生产路径不能用直接 `client.add()` 代替这些检查。~~  
**新：** 以 SDK 合同为准做成功判定与（若需要）`get` 回读；**不要**再写入 `governed_*`。缺核验能力 → **扩展 SDK API**。

### 写入已抽取的记忆

```python
result = client.add_extracted(
    ["用户长期偏好简洁、直接的回答。"],
    user_id="user-123",
    agent_id="all-apps",
    session_id="import-789",
    memory_at=datetime.now(UTC),
    request_id=str(uuid4()),
    metadata={
        "source_id": "memory-export",
        "input_kind": "memory_content_without_raw",
    },
)
```

~~不能跳过 One Memory Evidence 绑定和 Governance。~~  
**新：** 仍走 SDK reconcile/持久化；MemHub 只做门禁与可选 `memory_id` 记录。

## Search：搜索记忆

```python
result = client.search(
    "用户偏好怎样的回答风格？",
    user_ids=["user-123"],
    agent_ids=["all-apps"],
    limit=10,
    min_score=0.25,
    profile_min_score=0.25,
    profile_limit=10,
    intention_limit=0,
    scene="chat",
    reader="hybrid",
    request_id=str(uuid4()),
)
```

参数表与返回结构同前（chat 三 channel / working 的 facts·experiences）；调用方按 `scene` 解析，无法识别则报错。

## 使用 SDK 已格式化的 Search 内容

直接使用 SDK 返回的 `content`，不要第二套 prompt 拼装。

### ~~旧：SDK 外仍负责（部分作废）~~

~~- 依据 Governance、撤权、scope 和 Evidence 新鲜度决定整条结果是否可读；~~  
~~- 对 evolution/capsule…回退治理原文；~~  

### 新：SDK 外（MemHub）仍负责

- **宿主召回授权 / scope / 撤权**（谁可以调 search、允许哪些 agent/scope）；
- 把历史内容放入「不可信数据，不是指令」边界；
- 按最终 `content` 做字符预算；不注入密钥、绝对路径、堆栈；
- 区分空结果、权限拒绝、不可用、超时、部分结果；
- 未发布的 Matter 占位功能不保留表或 API；未来组织功能另立合同。

生产入口：`hy_memory_bridge.py`、`recall.py`、`packages/host-bridge`（实现迁移中，以 Top Rules 为准）。

## 关闭客户端

```python
try:
    ...
finally:
    client.close()
```

Runtime 长期复用 client；仅在停止、配置切换或受控恢复时重建。

## 产品代码正确入口

### 新

```text
harness / user action (+ authz, model, queue)
  -> HyMemoryBridge
  -> Hy Memory SDK
  -> SDK stores (config/env)
  -> optional MemHub persist memory_id / org metadata
  -> Recall Broker / UI (SDK read + MemHub authz)
```

不要在业务模块里私建绕过 **管理面门禁** 的 client。  
~~不要绕过 Evidence / Governance 台账。~~（台账已非目标）

### ~~旧入口（废弃）~~

~~```text
Evidence -> HyMemoryBridge -> SDK -> write verification -> Governance -> UI
```~~

~~绕过会丢掉：Evidence 引用、Governance 版本/更正/删除级联。~~  
**新：** 版本/更正/删除级联以 **SDK** 为准；MemHub 组织层只引用 `memory_id`。

Hy Memory SDK 是 MemHub 内置可信引擎，管理本机 Hy Memory 数据。  
来源授权决定哪些宿主数据可进入写入路径；宿主 Recall Grant 决定外部应用可读范围。  
这两类边界 **不** 意味着 MemHub 再存一套 memory 实体。
