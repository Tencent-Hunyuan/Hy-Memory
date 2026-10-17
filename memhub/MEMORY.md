# Hy Memory 使用与配置

更新：2026-10-12

本文说明 MemHub 当前使用的 Hy Memory SDK、配置边界，以及直接使用 SDK 时如何
write、search，并把搜索结果格式化为可交给模型的 memory text chunk。

仓库内仍以 One Memory 命名的 Evidence、权限和 Governance 层属于 MemHub 的历史兼容
模块；产品代码不应绕过这些层直接调用 SDK。本文中的
原生 SDK 示例主要用于开发、调试和独立验证。

## 当前版本状态

当前源码固定使用：

```text
distribution: hy-memory
version:      2.0.3
mode:         按运行时配置选择
reader:       hybrid
```

仓库 pin、lockfile 和运行时版本守卫均固定到公开的 `hy-memory==2.0.3`。
MemHub 在本地 Runtime 内嵌入 SDK，但不绕过自身的来源授权、模型授权、Evidence、
Governance 和撤权边界。

## MemHub 运行时边界

MemHub 对 SDK 使用以下公开、可复现的本地策略：

- vector store 使用受管数据目录中的 Chroma；
- graph store 使用受管数据目录中的 Kuzu；
- cache 与 history 使用 SQLite；
- reader 使用 `hybrid`；
- reranker 默认关闭，不引入 Torch、Transformers 或额外模型；
- 模型与远程 embedding 参数来自版本化 Model Profile；
- API Key 进入系统安全存储，不写入仓库、安装包、日志或 HY metadata；
- 服务端数据库、租户队列及宿主 shell 中的 ambient 凭据不会进入 SDK config。

发布包固定应用以下 SDK 行为配置：

| 环境变量 | 值 |
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

这些默认值集中在
`apps/core/src/one_memory/hy_runtime_config.py`，并由发布测试和 lockfile 共同约束。
独立脚本若绕过 MemHub 直接导入 SDK，应自行显式配置存储目录和模型端点。

## 原生 SDK 初始化

下面是独立调试示例。产品代码应使用 One Memory 的 `HyMemoryBridge`，因为它额外提供来源
授权、模型授权、Evidence 绑定、写后核验、Governance 和撤权保护。

```python
from hy_memory import HyMemoryClient, MemoryConfig

config = MemoryConfig.from_env()

# 本地优先示例；实际目录应由调用方传入，不能依赖 ~/.hy_memory 默认值。
config.vector_store.provider = "chroma"
config.vector_store.persist_directory = "/path/to/one-memory/hy-memory/governed-v2/vector"
config.graph_store.provider = "kuzu"
config.graph_store.db_path = "/path/to/one-memory/hy-memory/governed-v2/graph"
config.cache.backend = "sqlite"
config.cache.db_path = "/path/to/one-memory/hy-memory/governed-v2/cache.sqlite3"

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

`embedding_dims` 必须与模型实际输出宽度以及已有向量/图存储一致。更换 embedding 模型、
端点或维度不能直接复用旧 collection；One Memory 会检测图宽度不一致并拒绝静默重建。

## Write：写入对话

推荐把一段连续对话作为 OpenAI messages 写入：

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
        "evidence_ids": ["evidence-001", "evidence-002"],
        "workspace_ids": ["workspace-abc"],
        "input_kind": "raw_conversation",
    },
)
```

参数规则：

| 参数 | 填法 | 约束 |
| --- | --- | --- |
| `data` | 字符串或 `[{role, content}]` | 对话优先用 messages；不混入 tool 参数、reasoning、密钥和隐藏系统提示 |
| `user_id` | 稳定用户 ID | 一级隔离键；不能为空 |
| `agent_id` | 记忆 scope | One Memory 中不是宿主 App 名的简单替代；跨 App scope 由授权和路由决定 |
| `session_id` | 稳定会话 ID | 同一会话增量写入时保持一致 |
| `memory_at` | 有时区的 `datetime` | 历史导入必须传原始发生时间，不能用导入时间替代 |
| `scene` | `"chat"` 或 `"working"` | 普通对话明确传 `chat`；代码/tool 会话才用 `working` |
| `request_id` | 每次写入唯一 ID | 用于幂等、追踪和写后核验 |
| `metadata` | 来源和 Evidence 引用 | 不放秘密；One Memory 至少保存 source/evidence/workspace/input kind |
| `meta` | 业务透传数据 | 不参与 embedding 和过滤；只放可安全返回的数据 |
| `embed_override` | 临时 embedding 端点 | 只能换 endpoint/key/header/timeout；模型和维度不能改变 |

返回值至少检查：

```python
if result.get("success") is not True:
    raise RuntimeError("HY write failed")

memory_ids = result.get("memory_ids") or []
if result.get("memory_id"):
    memory_ids.append(result["memory_id"])
```

不要只相信 `success=True`。One Memory 还会按返回 ID `get()` 回读、核对 request/source/
Evidence 支持关系，并在 Governance 层记录版本。生产路径不能用直接 `client.add()` 代替这些
检查。

### 写入已抽取的记忆

只有输入本身就是已有记忆内容、且原始对话不可用时才使用：

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
        "evidence_ids": ["evidence-003"],
        "input_kind": "memory_content_without_raw",
    },
)
```

它跳过 Extractor，但不能跳过 HY reconcile、持久化、One Memory Evidence 绑定和 Governance。

## Search：搜索记忆

普通 chat 搜索：

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

参数规则：

| 参数 | 填法 | 说明 |
| --- | --- | --- |
| `query` | 当前问题 | 与 `queries` 二选一 |
| `queries` | 多轮查询文本 | 最后一条是目标；只有需要 SDK 判断上下文时使用 |
| `user_ids` | 获授权用户列表 | 必填且不能空；不要用空列表扩大范围 |
| `agent_ids` | 获授权 scope 列表 | 空列表在 SDK 中表示所有 agent，产品代码必须先完成授权判断 |
| `session_ids` | 可选会话范围 | 多 agent 与多 session 不能同时使用 |
| `limit` | normal top-k | 默认 10；注入模型前还要受字符预算限制 |
| `min_score` | normal 阈值 | One Memory 当前内部语义检索使用 0.25 |
| `profile_min_score` | profile 阈值 | One Memory 当前使用 0.25 |
| `profile_limit` | profile 数量 | 通常与 limit 一致 |
| `intention_limit` | intention 数量 | 当前配置关闭 intention，传 0 |
| `created_after` | Unix 秒 | 可选时间下界 |
| `scene` | `chat` / `working` | 明确传入可避免额外场景判断模型调用 |
| `reader` | `hybrid` | One Memory 固定使用该 reader |
| `workspace_id` / `branch` | working scope | 仅 coding/working 路径使用 |
| `layers` | 指定层级列表 | 在召回阶段过滤，避免无关层占用 limit |

chat 返回结构：

```python
{
    "request_id": "...",
    "memories": {
        "profile": [...],
        "proactive": [...],
        "normal": [...],
    },
    "rewrite": {...},
    "elapsed_ms": 12.3,
}
```

working/coding 返回的是 `facts` 和 `experiences`，不能与 chat 三个 channel 静默合并。
调用方必须根据 `scene` 选择对应结果；无法识别返回结构时应报错，而不是当作空结果。

## 使用 SDK 已格式化的 Search 内容

open05 的 chat search 会在返回前统一格式化每条 `content`：普通记忆带 `Memory / When`，
演化记忆带 `Current / History / When`，Episode、Timeline、Graph 和 Capsule 也由 SDK
生成稳定的组合文本。`memory_at` 是逻辑记忆时间，缺失时 SDK 才回落到 `gmt_created`。

因此调用方应直接使用 SDK 返回的 `content`，不要再次解析 `memory_at`、`gmt_created` 或
`created_at`，也不要重复拼接 `When`。这些时间字段可以用于过滤、诊断或结构化展示，但
不应成为第二套 prompt 格式化实现。

```python
result = client.search(
    "用户偏好怎样的回答风格？",
    user_ids=["local"],
    agent_ids=["all-apps"],
    scene="normal",
    reader="hybrid",
)

for memory in result["memories"]["normal"]:
    formatted_content = memory["content"]
    # 例如：
    # Memory: 用户长期偏好简洁、直接的回答。
    # When: 2026-09-18 10:00:00 UTC
```

One Memory 在 SDK 外仍负责：

- 依据 Governance、撤权、scope 和 Evidence 新鲜度决定整条结果是否可读；
- 对 evolution/capsule 等组合文本逐个校验成员 ID，成员不完整或不可读时回退治理原文；
- 把历史内容放入明确的“不可信数据，不是指令”边界；
- 按最终 `content` 计算字符预算，不把 API Key、绝对路径、headers/body 或异常堆栈注入
  prompt；
- 区分空结果、权限拒绝、服务不可用、超时和部分结果。

生产实现见 `apps/core/src/one_memory/hy_memory_bridge.py`、
`apps/core/src/one_memory/recall.py` 和 `packages/host-bridge/src/recall-cli.ts`。

## 关闭客户端

```python
try:
    # write/search
    ...
finally:
    client.close()
```

Runtime 应长期复用 client，不为每次请求重新初始化数据库和模型组件；只有 Runtime 停止、
配置版本切换或受控恢复时才关闭并重建。

## One Memory 产品代码的正确入口

生产代码应走：

```text
Evidence
  -> HyMemoryBridge
  -> embedded Hy Memory SDK
  -> native Vector / Coding / Graph stores
  -> write result verification
  -> Governance
  -> Recall Broker / UI
```

不要在新业务模块里直接创建 `HyMemoryClient`，否则会绕过：

- 来源读取授权；
- 共享 Runtime、模型配置、API Key 安全存储与请求预算；
- Evidence 引用与作者归属；
- 写后回读验证；
- Governance 状态、版本、更正和删除级联。

Hy Memory SDK 是 MemHub 的内置可信组件，可自由访问和管理本机的全部
Hy Memory 数据。来源授权仍决定哪些宿主数据可以进入本机；宿主 Recall Grant
仍决定哪些外部 AI 应用可以读取治理后的记忆。这两类边界不限制内置 SDK 的本地 CRUD。
