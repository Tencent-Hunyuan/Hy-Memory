# Hy Memory 使用与配置

更新：2026-10-21

架构边界以 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) 为准。

---

## Architecture Principles

1. Memory 读写与存储 backend **只经 SDK**（config / env）。
2. MemHub 运行时对 memory 的原子操作 **只经 SDK 公共 Client**；缺正常业务
   接口先补 SDK。Runtime writers 停止后的 App 启动迁移可直接操作底层存储，
   但不得把迁移专用方法加入公共 Client。
3. MemHub **不存 memory 实体**；**可以存 `memory_id`** 做组织/回执。原文进入 **SDK raw**。
4. SDK raw 只保留滚动三天；一条 raw 对应一个完整 turn，只含真人 user 与
   assistant，tool 不持久化。提取后的高层 memory 不受该窗口影响。

生产入口通过共享 Runtime 租约取得公开 `HyMemoryClient`；MemHub 只在调用前执行
模型配置、密钥和 harness/model 门禁，不维护第二套 memory 正文权威。

来源授权、模型授权和宿主召回授权仍是 MemHub 管理面门禁；memory
数据平面的权威在 SDK。

## 当前版本状态

当前源码固定使用（以仓库 pin / lockfile 为准）：

```text
distribution: hy-memory
version:      见 pyproject / lock（文档数字可能滞后）
mode:         按运行时配置选择
reader:       hybrid
```

## MemHub 运行时边界

MemHub 通过公开 `HyMemoryClient` 配置使用可复现的本地策略：

- vector / graph / cache / history / coding 等 **一律 SDK config/env**；
- 模型与 embedding 来自版本化 Model Profile；密钥进安全存储；
- MemHub 管理库可存 harness/model/队列状态与 **`memory_id` 引用**。

MemHub 不再通过 import-time 环境变量覆盖 SDK 的提取、reconcile、episode、
intention、few-shot、summary 或去重策略。产品只配置模型/密钥/endpoint、本地
backend 路径、公开 embedding service，以及每次公开 Client 调用的参数。

独立脚本直连 SDK 时须自配存储目录与模型端点。

## 原生 SDK 初始化

下面是独立调试示例。

产品代码经 `hy_sdk_runtime.SdkClientHandle` 获取已配置的公开 client；memory I/O
由 SDK 完成。

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

推荐把一段连续对话作为 OpenAI messages 写入；含原文时由 SDK 接管 raw：

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
# 需要组织或回执时，MemHub 只持久化这些 memory_id。
```

以 SDK 合同为准做成功判定；需要写后核验时使用 SDK `get` 回读。缺少所需
核验能力时扩展 SDK API，不在 MemHub 新建镜像台账。

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

该入口仍走 SDK reconcile/持久化；MemHub 只做管理面门禁与可选
`memory_id` 记录。

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

### SDK 外（MemHub）仍负责

- **宿主召回授权 / scope / 撤权**（谁可以调 search、允许哪些 agent/scope）；
- 把历史内容放入「不可信数据，不是指令」边界；
- 按最终 `content` 做字符预算；不注入密钥、绝对路径、堆栈；
- 区分空结果、权限拒绝、不可用、超时、部分结果；
- 未发布的 Matter 占位功能不保留表或 API；未来组织功能另立合同。

生产入口：`hy_sdk_runtime.py`、`sdk_write_processor.py`、`recall.py` 和
`packages/host-bridge`。

## 关闭客户端

```python
try:
    ...
finally:
    client.close()
```

Runtime 长期复用 client；仅在停止、配置切换或受控恢复时重建。

## 产品代码正确入口

```text
harness / user action (+ authz, model, queue)
  -> public HyMemoryClient
  -> SDK stores (config/env)
  -> optional MemHub persist memory_id / org metadata
  -> Recall Broker / UI (SDK read + MemHub authz)
```

不要在业务模块里私建绕过管理面门禁的 client。版本、纠正和删除语义以 SDK
合同为准；MemHub 组织层只引用 `memory_id`。

Hy Memory SDK 是 MemHub 内置可信引擎，管理本机 Hy Memory 数据。  
来源授权决定哪些宿主数据可进入写入路径；宿主 Recall Grant 决定外部应用可读范围。  
这两类边界 **不** 意味着 MemHub 再存一套 memory 实体。
