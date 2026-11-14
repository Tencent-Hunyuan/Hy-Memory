# 业务存储后端

Working / Coding 的经验、事实、关联、原始对话轨迹和 Coding 版本演进链，支持 SQLite（默认）与 PostgreSQL。向量库配置独立，仍可选择 Chroma、Qdrant 或 FAISS。

## 默认 SQLite

无需增加配置或依赖。现有 `MEMORY_CODING_DB_PATH`、SQLite 文件名、向量集合名和旧表升级行为继续生效。

## PostgreSQL

从包含本功能的版本安装可选依赖；在源码目录可以运行：

```bash
pip install '.[postgres]'
export MEMORY_BUSINESS_STORE=postgres
export MEMORY_POSTGRES_DSN='postgresql://memory:password@localhost:5432/memory'
export MEMORY_POSTGRES_SCHEMA=hy_memory
export MEMORY_POSTGRES_CONNECT_TIMEOUT=10
```

也可以通过已有配置入口指定：

```python
from hy_memory import HyMemoryClient

client = HyMemoryClient.from_config({
    "business_store": {
        "backend": "postgres",
        "postgres_dsn": "postgresql://memory:password@localhost:5432/memory",
        "postgres_schema": "hy_memory",
        "connect_timeout": 10,
    },
})
```

数据库需要预先创建，连接用户需要在该数据库创建 schema、表和索引的权限。SDK 在第一次使用相应场景时初始化 schema；缺少 DSN、驱动或连接失败都会报错，不会自动回退到 SQLite。建议生产环境通过环境变量或密钥管理注入 DSN。

`MEMORY_POSTGRES_SCHEMA` 是命名空间前缀，要求 1–48 个小写字母、数字或下划线，首字符不能为数字。默认使用：

| Schema | 内容 |
|---|---|
| `hy_memory_working` | Working 经验与事实 |
| `hy_memory_coding` | Coding 经验与事实 |
| `hy_memory_trace` | 两个场景共享的原始轨迹，按用户、agent、source 读取 |
| `hy_memory_chain` | Coding 版本链，按用户与工作区组成的 owner 隔离 |

同一逻辑记忆库的客户端应使用一致的 schema 前缀和向量集合配置；互相独立的记忆库应使用不同前缀。

每个 schema 记录自身版本；初始化和升级使用事务锁。多实例追加同一 owner 的版本链时，查找 thread、合并 hunk 和分配序号在一个事务内串行执行。单个 client 的版本链操作在线程池内执行，避免 PostgreSQL 网络请求阻塞异步事件循环。

## 切换与备份边界

- 切换配置会选择新的业务存储，不会自动复制 SQLite 中的已有数据。需要保留旧记忆时，应先停写、备份并通过单独的数据迁移流程搬迁业务记录，保留 ID、关联、variant 和向量集合对应关系，核对后再切换；本功能不提供自动迁移命令。
- Working/Coding 的向量集合和向量 provider 保持独立。服务端多实例部署时，应同时选择支持共享访问的向量服务，并自行配置对应连接；仅改业务库不会把本地 Chroma 变成共享服务。
- 关系数据与向量库仍分步写入，不具备跨库原子事务。PostgreSQL 连接成功不代表向量写入也成功。
- `MEMORY_CACHE_BACKEND` 管理的审计／观测／写入状态存储、`HistoryStore` 和 MemHub 管理库不受本配置控制，仍使用各自后端设置。本配置不承诺整个应用零 SQLite。
- `snapshot_local_data_plane` 只备份本地文件，不能包含 PostgreSQL 业务数据；PostgreSQL 需使用数据库备份工具独立备份，并与向量快照协调停写。

## 扩展接口与测试

`hy_memory.coding.repository_base.MemoryRepository` 定义经验、事实、作用域统计、关联与轨迹操作。可以向 `CodingMemoryStore(..., repository=...)` 注入自定义实现；仓储不负责向量读写。版本链的协议位于 `hy_memory.coding.chain_repository.ChainRepository`，通过 `HyCodeMem(..., repository=...)` 注入。

业务仓储方法是同步方法，由现有 SDK 线程池执行；实现应正确管理连接和并发。SDK 负责调用 initialize/close，自定义实现不应在业务数据层自动切换后端。

```bash
HY_MEMORY_TEST_POSTGRES_DSN='postgresql://localhost:5432/memory_test' \
  uv run --extra dev --extra postgres pytest tests/coding/test_business_repository.py
```

测试创建随机前缀的 schema，并在结束时清理。应使用独立测试数据库；未提供测试 DSN 时只运行 SQLite 部分。


## 跨会话巩固发布与升级

SQLite / PostgreSQL 仓储显式声明 `supports_consolidation=True`，提供一致材料快照、
SQL-only 回执读取和单事务发布/重激活。旧自定义 `MemoryRepository` 不新增必需抽象方法，
普通读写契约保持兼容；默认不支持新能力，巩固在调用模型和写向量前抛 `NotImplementedError`，
不能降级为多个独立 upsert 的非事务发布。

`consolidation_receipt` 保存精确范围、稳定依据身份、原始决策、当前选择、依据有效性、
激活代次、权威产物清单及撤回 ID。`consolidation_read_set` 保存完整实际输入的类型、ID、
session、指纹和事件时间，不复制历史原文。`(group_key,basis_digest)` 唯一，部分唯一索引保证
每组最多一个当前回执，材料反向依赖索引用于事务内失效。经验表增加 `consolidation_id`
和 `evidence_refs`。这些 SQL 表和索引只管理业务状态，不生成语义、关键词、BM25、全文或
向量检索候选，检索仍由配置的 VDB 负责。

发布流程：短一致读事务冻结完整材料和 `(当前巩固 ID, activation_epoch)`；事务外模型归纳、
embedding、严格候选向量批写；短写事务锁源记录，重验冻结指纹，锁巩固组并检查激活代次，
一次性写回执、依赖和正文、切换当前决策。候选经验 ID 每次随机生成，未发布候选经业务库
可见性过滤。确认未提交的冲突或向量预写失败才清理本次独有节点；提交结果不确定时保留节点。
`no_result` 也参与换代。同依据并发仅有一个权威清单；A→B→A 旧请求不能绕过代次检查。

SQLite 使用 WAL；新列、新表和约束在 `BEGIN IMMEDIATE` 升级事务内完成，事务内不使用
隐式提交的 executescript。PostgreSQL 业务 schema 升至版本 2，显式从版本 1 增加两列与
回执/依赖表；升级沿用 namespace advisory lock。初始读取为 REPEATABLE READ 快照。
发布和来源编辑按源记录→巩固组→回执/产物顺序加锁，编辑取得源锁后才读取最新依赖，
因此等待发布结束的编辑也能使刚发布回执失效。PostgreSQL 按源 ID 使用事务级 advisory lock
覆盖删除/重插时缺失的源行，并对现有源行取 FOR UPDATE。直接撤回综合经验先锁组再删除正文，
提交后清向量；迟到向量和清理失败均不能恢复业务可见性。

正常经验读取、分页、计数和任务列表共用当前且依据有效的回执条件；普通经验保持可见。
依据语义投影统一定义于 `coding/evidence.py`，不把事实状态、演化摘要或互链计入指纹。
历史回执重激活仅保留未撤回的产物；依据失效后恢复原文仍需要重新归纳与发布。


### 2026-11-14 本地实施验收记录

代码基点：`b591b4076c747f9b0310bce776251115e1ff4b8d` 加本地未提交改动。
Python 3.12.10（Windows），SQLite 3.49.1，PostgreSQL Server 17.7 / psycopg 3.3.6；
Chroma 1.5.9（嵌入式），Qdrant client 1.20.0（当前仓库锁定版本，较原方案的 1.19.1 更新）
/ Server 1.20.0（独立 Docker 实例），FAISS CPU 1.15.1（嵌入式）。
业务仓储与 VDB 分开配置，没有新增运行依赖或修改锁文件。模型和 embedding 合同测试使用
现有 MockLLMProvider / MockEmbedService；下面的模型质量抽样另行使用真实 provider。

依赖安装实际使用 `uv export --locked --extra dev --extra postgres --format requirements.txt
--no-hashes --no-emit-project` 导出锁定约束后，通过 PyPI 镜像安装；额外安装当前锁定的
`qdrant-client==1.20.0`、`faiss-cpu==1.15.1` 执行可选后端合同。
原始 PyPI 文件下载在本机阻塞，未把中断的安装命令当作成功。

实际执行（`python` 指本仓库 `.venv/Scripts/python.exe`，外部服务均为本次独立测试实例）：

```powershell
$env:HY_MEMORY_TEST_POSTGRES_DSN = 'postgresql://hy_memory:hy_memory_test@127.0.0.1:54541/hy_memory_test'
$env:HY_MEMORY_TEST_QDRANT_PORT = '53388'
python -m pytest tests/coding/test_consolidator.py tests/coding/test_consolidation_repository.py tests/coding/test_consolidation_client.py tests/test_prompt_registry.py -q -o log_cli=false --show-capture=no
# exit 0: 161 passed, 0 failed, 0 skipped
python -m pytest tests/coding/test_consolidation_repository.py -k postgres -q -rs -o log_cli=false --show-capture=no
# exit 0: 16 passed, 16 deselected, 0 skipped（全部为真实 PostgreSQL 参数）
```

原生向量合同为 SQLite/PostgreSQL × Chroma / Qdrant Local 经验集合 /
Qdrant Server / FAISS，共 8 个真实组合。Local 用例仅打开经验集合，验证已有通道的预写、
检索、完整 session 集合、复用和撤回；**不代表 Qdrant Local 完整双层 store 初始化已通过**。
当前基线的完整双层初始化会用两个独立 QdrantClient 打开同一目录，实测触发目录锁冲突；
本改动未修改 adapter 或该初始化行为。Qdrant Server 用例执行完整 store 初始化与两集合，
服务端版本通过真实实例查询确认。没有测试 Chroma HTTP、多 Python 版本或大数据量性能。
检索对隐藏历史向量按原生 ANN 自适应扩容，沿用每次最多 2,000 候选的安全上限。

真实模型抽样为当前配置网关的 `deepseek-flash`，五组输入均为固定人工材料：

| 材料组 | 人工检查结论 |
| --- | --- |
| 死锁三会话 | 保留 PostgreSQL/SQLite 的不同验证范围和源→组→产物顺序；默认 4,096 输出预算截断并被整批拒绝，8,192 重跑后结构及引用通过，验证标签偏保守 |
| 相反环境 | 按 Python 3.8 / 3.12 区分 executor 与 to_thread，不把新版本 API 推广到旧环境 |
| 未验证计划 | 明确 unverified，未把评审同意或计划写成已验证成功 |
| 相关与无关会话混合 | 只引用两条数据库材料；午餐会话仍在完整 read_set，未强行支撑结论 |
| 多个独立主题 | 分为 HTTP 429 重试和 SQLite 迁移两条经验，各自保留条件和引用 |

其余四组在 4,096 输出预算下通过；样例评审不等于对其他模型、提示注入或开放材料质量的普遍保证。
网关不支持默认 gpt-4.1-nano，最初调用收到 HTTP 400；质量评审临时选择网关声明支持的模型，
没有改动 SDK 默认模型配置。默认预算下的截断失败仍按真实结果记录，未计为有效产物。


全 SDK 回归实际命令 `python -m pytest tests/ -q -o log_cli=false --show-capture=no`
（不包含 integration 标记），最终 exit 1：2,048 passed / 72 failed / 33 skipped /
18 teardown errors，71 deselected。以 `git archive HEAD` 导出的完整、未修改基线在同一 Python、
依赖与 PostgreSQL 实例上运行同一命令，exit 1：1,965 passed / 73 failed / 33 skipped /
18 teardown errors，71 deselected。逐项比较 JUnit 失败用例集合，**没有仅出现在本改动的失败**；
基线另有一项 reembed 用例失败，本改动运行时该项通过，未将其视作修复成果。

方案指定的聚焦范围也执行过：`tests/coding tests/test_prompt_registry.py
 tests/test_prompt_language_selection.py tests/test_client_loop_lifecycle.py`，该轮结果为
798 passed / 12 failed / 4 skipped / 15 teardown errors；其失败与错误集合与未修改基线一致。
此后补充的原子批回滚、第二产物向量失败及 Qdrant Server 用例已纳入最终 161 项和全 SDK 回归。
旧用例中的 Windows 文件占用、非 ASCII FAISS 持久化路径、SIGALRM 不可用、默认 GBK 读源码等
失败仍保留，未为了验收改动无关模块或隐藏错误。故本次没有宣称全量回归通过。

本次修改范围的 Ruff、Python 编译及 `git diff --check` 通过。双轴代码审查发现的提交取消、
隐藏产物公共撤回、历史向量遮蔽和无范围查询全量扫描问题均已修复，并补有回归用例。
