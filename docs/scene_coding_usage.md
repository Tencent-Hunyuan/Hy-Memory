# Working 与 Coding 场景

Hy Memory 的公开 `scene` 参数支持三个值：

| `scene` | 用途 | 存储与检索路径 |
|---|---|---|
| `"chat"` | 闲聊、个人事实、一般问答 | 通用 memory pipeline |
| `"working"` | 文档、办公、研究、财经、内容创作和电脑操作等生产力任务 | working 双层 memory |
| `"coding"` | 编码、调试、测试、构建、API/SDK、脚本、SQL 等软件工程任务 | coding 双层 memory，并附带版本演进链 |

working 与 coding 复用同一套生产力 pipeline 结构和数据 schema，但使用不同的抽取规范、SQLite 文件和向量集合，数据不会混在一起。

## 1. 写入路由

### 显式指定 scene

生产力写入需要包含 tool message 或 assistant `tool_calls`：

```python
from hy_memory import HyMemoryClient

client = HyMemoryClient(mode="pro")

messages = [
    {"role": "user", "content": "Fix the timeout in the search client."},
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "apply_patch",
                    "arguments": '{"path":"src/search.py"}'
                },
            }
        ],
    },
    {
        "role": "tool",
        "tool_call_id": "call_1",
        "name": "apply_patch",
        "content": "Patch applied successfully",
    },
]

result = client.add(
    messages,
    scene="coding",
    user_id="user_123",
    workspace_id="github.com/example/service",
    branch="fix/search-timeout",
)
```

公开行为：

- `scene="chat"`：跳过 scene judge；tool 轨迹会被移除后进入 chat pipeline。
- `scene="working"`：跳过 scene judge，进入 working pipeline。
- `scene="coding"`：跳过 scene judge，进入 coding pipeline，并尝试记录版本演进链。
- 显式 `working/coding` 但输入没有 tool/tool_calls：当前实现会回退 chat pipeline。
- 非法 scene 会抛出 `ValueError`。

### 不指定 scene

`scene=None` 时：

- 输入没有 tool/tool_calls：直接进入 chat pipeline，不调用 scene judge LLM。
- 输入包含 tool/tool_calls：使用 user query 与工具名的有界摘要进行一次 `chat / working / coding` 三分类。
- judge 失败或输出无法解析：回退 chat。
- 判定为 chat 后，tool 轨迹会被移除再进入 chat pipeline。

分类边界：

- `coding`：代码或开发产物是工作对象或实现媒介，包括源码、脚本、SQL、notebook、测试、构建、编译/运行错误、API/SDK 和代码评审。
- `working`：存在明确生产力操作或交付物，但代码不是核心工作媒介，例如文档、PPT、表格、翻译、财经/业务分析、研究报告、邮件日程、项目管理、设计、文件整理、软件安装和设备操作。
- `chat`：闲聊、个人信息、建议和一般事实问答。仅使用 search/browser 工具不会自动变成 working。

## 2. 搜索路由

显式传 `scene` 会直接选择对应检索路径，不调用 judge/rewrite LLM：

```python
result = client.search(
    "Which timeout implementation was finally kept?",
    scene="coding",
    user_ids=["user_123"],
    workspace_id="github.com/example/service",
    branch="fix/search-timeout",
    limit=5,
)
```

默认与 agentic 行为：

| 调用方式 | 当前行为 |
|---|---|
| `search(query="...", search_mode="default")` | 固定走 chat/normal light search，零 LLM |
| `search(queries=[...], search_mode="default")` | Coding 开启时分类并 rewrite；仅 Query Rewrite 开启时只 rewrite/time；两者关闭时零 LLM |
| `search(query="...", search_mode="agentic")` | LLM 三分类、query rewrite 和时间范围解析 |
| `search(queries=[...], search_mode="agentic")` | 同上，前置 query 作为上下文 |
| 任意模式显式传 `scene` | scene 优先，零 judge/rewrite LLM |

`search_mode="agentic"` 使用有界 query 视图，单 query 与多 query 都会走 judge/rewrite。`search_mode="default"` 的单 query 保证不调用该 LLM。

default 多-query 超过 `MEMORY_QUERY_REWRITE_MAX_CHARS` 时不改写；Coding 开启时仍做有界
场景分类，关闭时直接使用最后一条 query。

若 coding 功能被 `MEMORY_CODING_ENABLED=false` 关闭，生产力搜索会安全回退 chat/normal 检索。

## 3. Extractor 输入窗口

working/coding extractor 将输入分成：

- `LAST 5 USER TURNS`：之前 write 的历史，只用于指代消解和上下文，不重复抽取。
- `NEW MESSAGES`：本次 `add()` 传入的全部消息，全部作为抽取主体，不受 last-5 裁剪。

当前压缩规则：

- tool result 默认最大 `2048` bytes，保留头尾并省略中段；由 `MEMORY_CODING_TOOL_RESULT_MAX_BYTES` 配置。
- 单条 assistant 文本超过 `6000` 字符时，默认保留前 `3000` 和后 `3000` 字符。
- 历史按 user-turn 窗口裁剪；一个 user-turn 包含该 user 消息以及下一条 user 消息前的 assistant/tool 消息。
- `MEMORY_EXTRACT_HISTORY_TURNS` 控制 store 回读多少历史，默认 5；当前 extractor 最终最多注入最近 5 个 user-turn，因此大于 5 的配置不会扩大 prompt 中的历史窗口。

例如本次写入包含 10 个 user-turn 时，10 个 turn 都进入 `NEW MESSAGES`；历史区另外最多提供最近 5 个 user-turn。

working/coding 的 Extractor、经验 Reconcile、事实 Reconcile 与跨 session 进展总结均有
中英文模板。路由优先检查本次新输入前 10 个有效语言字符；Extractor 只看本次 NEW user
messages，不使用历史或 tool result 决定语言。中文开头后接大量代码、路径或错误信息时仍走
中文模板；英文开头后面偶尔出现中文术语时保持英文模板。

## 4. 返回结构

working/coding 写入返回双层结果：

```python
{
    "success": True,
    "scene": "productivity",
    "request_id": "...",
    "ops": [...],
    "ops_summary": {...},
    "experience_ids": [...],
    "fact_ops": [...],
    "fact_ops_summary": {...},
    "fact_ids": [...],
    "memory_id": "..." or None,
    "elapsed_ms": 123.4,
}
```

`scene="coding"` 还会返回 `chain_rounds`。出于历史兼容，该字段当前统计本次识别并处理
的 diff hunk 数，而不保证等于最终合并后的唯一演进 round 数。`0` 表示没有识别到可记录
的代码修改，不代表双层 memory 写入失败。

working/coding 搜索返回：

```python
{
    "request_id": "...",
    "scene": "productivity",
    "facts": [...],
    "experiences": [...],
    "elapsed_ms": 123.4,
}
```

`limit` 分别限制 facts 与 experiences，因此两层最多各返回 `limit` 条。

`scene="coding"` 额外返回 `chain_block` 和 `chain_used` 字段（键始终存在；默认内容为空）：

- 注入 diff / 版本演进块需显式开启 `MEMORY_CODING_CHAIN_BLOCK_ENABLED=true`
  （或 `CodingConfig.chain_block_enabled=True`）。默认关闭，以节约 prompt token。
- 开启后，`chain_block` 是与已召回 fact 对齐的版本演进上下文；无法挂链时为 `""`。
- `chain_used` 是已经进入 chain block 的 `facts` 下标。
- SDK 不会从 `facts` 删除这些条目。调用方若要拼成“chain block + 剩余 facts”，需自行按 `chain_used` 过滤。

## 5. 存储与作用域

默认本地文件：

```text
<coding db directory>/
├── coding_memory.db          # working 经验/事实 metadata
├── coding_memory_cv.db       # coding 经验/事实 metadata
├── coding_memory_trace.db    # working/coding 共用的历史消息
└── hy_code_chain/
    └── hy_code.db            # coding 版本演进链
```

向量集合：

| 变体 | 经验层 | 事实层 |
|---|---|---|
| working | `{base}_coding_keys` | `{base}_working_facts` |
| coding | `{base}_coding_keys_cv` | `{base}_working_facts_cv` |

同一个 client 可以交替使用 working 与 coding；实现会按变体分别缓存 writer/store。

`workspace_id` 与 `branch` 用于生产力 memory 的作用域：

- 不传 `workspace_id`：不按 workspace 过滤。
- 传 `workspace_id`：精确匹配该 workspace。
- `branch` 只有在同时传 `workspace_id` 时生效。
- branch 过滤会保留 `branch IS NULL` 的历史数据，避免未记录分支的通用经验被误删。

建议写入和搜索使用相同的规范化 `workspace_id`。版本链的 owner 由 `user_id` 与 `workspace_id` 共同构成，避免不同用户或仓库的 patch 串链。

## 6. Coding 版本演进链

版本演进链由 `hy_memory/coding/chain_store.py` 实现。它不调用 LLM 或 embedding，而是从以下来源识别修改：

- 设置 `MEMORY_CODING_CHAIN_REPO_ROOT` 后，从本地 Git 工作区或变更中读取 diff。
- 从工具轨迹中识别带 path、old、new 的确定性替换动作。

无法可靠解析的编辑形式会被跳过。链层是 best-effort：记录失败不会阻断 working/coding 的双层 memory 写入或检索。

## 7. 配置

| 变量 | 默认值 | 作用 |
|---|---|---|
| `MEMORY_CODING_ENABLED` | `true` | 启用 working/coding pipeline |
| `MEMORY_CODING_DB_PATH` | `{DATA_DIR}/data/coding_memory.db` | working 基础 SQLite 路径；coding/trace 路径由此派生 |
| `MEMORY_CODING_TOOL_RESULT_MAX_BYTES` | `2048` | 单条 tool result 的字节上限 |
| `MEMORY_CODING_VERBATIM_FALLBACK_ENABLED` | `false` | 模型无结果时是否保存原文 fact；默认关闭，避免 assistant/tool trace 直接展示 |
| `MEMORY_CODING_CHAIN_BLOCK_ENABLED` | `false` | search 是否注入版本演进链块（`chain_block` / diff）；默认关闭（opt-in） |
| `MEMORY_EXTRACT_HISTORY_TURNS` | `5` | 回读的历史 user-turn 数；当前 prompt 最终最多使用 5 |
| `MEMORY_CODING_CHAIN_REPO_ROOT` | 空 | coding 版本链读取本地 Git diff 的仓库路径 |

`HCM_*` 变量属于版本链的高级调优项，不是稳定的公共配置契约；普通接入不应依赖其默认值。
其中 `HCM_BLOCK_OFF=1` 可在已开启 `MEMORY_CODING_CHAIN_BLOCK_ENABLED` 时做消融（完全不出块）。

## 8. 定向验证

```bash
python -m pytest -q \
  tests/coding/test_write_scene_routing.py \
  tests/coding/test_search_scene_routing.py \
  tests/coding/test_scene_coding_variant.py \
  tests/coding/test_scene_coding_search.py \
  tests/coding/test_coding_pipeline_log.py
```
