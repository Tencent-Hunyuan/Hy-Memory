# 可插拔业务存储与 PostgreSQL

用户已确认：抽出业务存储、增加 PostgreSQL、保留 SQLite 默认；向量层保持现有实现。

## 边界

- `MemoryRepository` 保存 Working/Coding 经验、事实、关联和原始轨迹；`CodingMemoryStore` 继续负责 embedding、向量调用与检索编排。
- `ChainRepository` 保存版本链；`HyCodeMem` 保留 diff 解析、上下文提取和展示。
- SQLite 保留现有文件名、表名、旧表迁移和默认依赖；PostgreSQL 使用可选 psycopg 3。
- 审计缓存、HistoryStore、MemHub 管理数据库的配置保持独立；选择 PostgreSQL 业务库不代表这些数据库也迁移。

## 配置与隔离

`business_store.backend=sqlite|postgres`，默认 sqlite。PostgreSQL 使用 DSN、schema 前缀和连接超时配置；Working/Coding 分 schema，共享轨迹使用独立 schema，版本链使用独立 schema。现有向量集合命名保持不变。

配置错误、缺少驱动和 PostgreSQL 连接失败明确报错，不降级到本地。配置摘要不输出 DSN。

## 生命周期与并发

阻塞的业务库操作使用 SDK 线程池；连接在关闭及初始化失败时释放。PostgreSQL 使用事务级 advisory lock 串行化 schema 初始化，以及同 owner 的版本查找、创建和编号。轨迹去重与保留上限在单个事务内完成。

元数据与外部向量库仍不属于同一事务，本次不引入 outbox 或改变向量写入顺序。切换后端不自动搬迁已有数据。

## 实施与验证

1. 抽出业务仓储契约及共享关系查询；SQLite 实现沿用现有迁移逻辑。
2. 增加 PostgreSQL 适配、配置和可选依赖，接通 client 的存在性检查与版本链生命周期。
3. 运行已有 Coding/Working 回归；共用契约测试覆盖两个真实后端，另测 PostgreSQL 多实例并发与失败清理。
4. 补充部署说明和 CI PostgreSQL service，验证后向上游提交 PR。
