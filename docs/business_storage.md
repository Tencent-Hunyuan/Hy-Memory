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
