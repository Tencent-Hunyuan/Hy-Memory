# 跨会话经验巩固独立分支验证记录

日期：2026-11-14（Asia/Shanghai）。专项测试通过，完整 SDK 在本分支和未修改的上游基线上
均未全绿；本贡献以 draft 提交，不声明已满足普通待审或合并条件。

## 提交与范围

- 上游基线：`7b85404be1b1fbe3c09a02a9409835128877a390`。
- 原实现提交：`00f6fca5a23ed1d4e66d18ae6cc9fd441884c0b3`，位于另一个 Qdrant 功能分支。
- 独立分支：`codex/cross-session-consolidation`；移植后的代码提交为
  `ea99d854a3f5cd5edf82aab4fac728d5b17f706c`。
- 移植冲突仅涉及 `hy_memory/client.py` 和 `docs/env_reference.md`，保留经验巩固新增内容，
  未带入 Qdrant native retrieval 的准备接口或文档段落。
- 新分支不依赖 PR #447，不修改 VDB adapter、依赖清单、锁文件、工作流或 MemHub。
  后续提交仅补充本记录并区分历史验收材料，未再次改动被测代码。

原方案在实现前已有验收条目；未提供可独立核对的“新增测试首次失败早于实现”的执行记录，
因此不补写或倒填 test-first 历史。原实现附带的模型质量评审和回归结果保留在
[业务存储文档的历史记录](../business_storage.md#2026-11-14-原-qdrant-分支上的历史实施验收记录)，
不作为本分支的当前检查结果。

## 本轮环境与隔离

| 项目 | 实际版本或运行模式 |
| --- | --- |
| 系统 / Python | Windows / CPython 3.12.10 |
| SQLite | 3.49.1，测试临时目录 |
| PostgreSQL | 17.7，`postgres:17` 一次性 Docker 容器；psycopg 3.3.6 |
| Chroma | 1.5.9，嵌入式 |
| Qdrant | client 1.19.1；Local 模式及 Server 1.20.0 一次性 Docker 容器 |
| FAISS | faiss-cpu 1.15.1 |
| pytest / pytest-asyncio / Ruff | 9.1.1 / 1.4.0 / 0.17.1 |
| 模型 / embedding | 现有 MockLLMProvider / MockEmbedService，无真实模型质量重测 |

数据库与 Qdrant 均使用本轮新建容器和随机映射到 loopback 的端口；PostgreSQL 用例使用随机
namespace，向量合同使用独立随机集合。未使用日常、共享或生产实例。镜像 ID：

- PostgreSQL：`sha256:f337b026cb1fb954a93f2e33a62bf9ea7fffc1dafc1586c53b93922a8d6ee018`。
- Qdrant：`sha256:9bfc89f9914fc14876a88c05270685921a55b8877b254a98bcc1cc2f0a424cd2`。

本轮复用现有虚拟环境，以 `uv pip install --python .venv/Scripts/python.exe --no-deps
qdrant-client==1.19.1` 将客户端恢复到上游锁文件对应版本；没有声称重新同步了整套锁定环境。
FastEmbed 未安装，本轮仅验证已有稠密经验向量通道，不验证稀疏/BM25 能力。

## 原生检索依赖声明

综合经验复用现有 Working/Coding 经验向量集合及 `upsert_batch()` / `search()` / `delete()`
接口；新增 SQL 表、约束和反向依赖索引仅管理发布、依据有效性、撤回、幂等及业务可见性，
不生成语义或词项检索候选，不新增 VDB 外检索索引、关键词、BM25、全文或混合检索通道。

| 后端 | 本轮使用的原生能力 | 固定版本官方接口依据 |
| --- | --- | --- |
| Chroma 1.5.9 | `Collection.upsert()`、`query(query_embeddings=...)` | [upsert](https://github.com/chroma-core/chroma/blob/1.5.9/chromadb/api/models/Collection.py#L497)、[query](https://github.com/chroma-core/chroma/blob/1.5.9/chromadb/api/models/Collection.py#L194) |
| Qdrant client 1.19.1 / Server 1.20.0 | `QdrantClient.upsert()`、`query_points(query=dense_vector)` | [upsert](https://github.com/qdrant/qdrant-client/blob/v1.19.1/qdrant_client/qdrant_client.py#L867)、[query_points](https://github.com/qdrant/qdrant-client/blob/v1.19.1/qdrant_client/qdrant_client.py#L269)；本轮真实 Server 组合合同通过 |
| FAISS 1.15.1 | `IndexFlatIP`、`add()`、`search()` 及既有 selector | [IndexFlatIP](https://github.com/facebookresearch/faiss/blob/v1.15.1/faiss/IndexFlat.h#L76)、[add](https://github.com/facebookresearch/faiss/blob/v1.15.1/faiss/python/class_wrappers.py#L289)、[search](https://github.com/facebookresearch/faiss/blob/v1.15.1/faiss/python/class_wrappers.py#L421) |

这些材料说明依赖、声明边界和本次执行结果，不替代实现正确性、隔离效果或质量的代码审查。

## 实际执行结果

下列 `python` 指现有 `.venv/Scripts/python.exe`，工作目录为仓库根目录。
`HY_MEMORY_TEST_POSTGRES_DSN` 和 `HY_MEMORY_TEST_QDRANT_PORT` 仅指向上面的一次性实例。

| 检查 | 退出码 | 结果 |
| --- | --- | --- |
| 新增三个巩固测试文件 + prompt registry | 0 | 161 passed，0 failed，0 skipped |
| 方案指定聚焦回归 | 1 | 803 passed / 12 failed / 4 skipped / 15 teardown errors |
| 本分支完整 SDK | 1 | 2002 passed / 72 failed / 33 skipped / 18 errors；45 deselected |
| 未修改上游基线完整 SDK | 1 | 1919 passed / 73 failed / 33 skipped / 18 errors；45 deselected |
| 跨会话来源数量校验变异 | 1，预期红灯 | 1 failed / 15 passed / 18 deselected |
| 正常代码下的相同变异目标测试 | 0 | 16 passed / 18 deselected |
| 全部改动 Python 文件的 Ruff / compileall | 0 | 通过 |
| `git diff --check origin/main...HEAD` | 0 | 通过 |

主要命令：

```powershell
python -m pytest tests/coding/test_consolidator.py tests/coding/test_consolidation_repository.py tests/coding/test_consolidation_client.py tests/test_prompt_registry.py -q -o log_cli=false --show-capture=no
python -m pytest tests/coding tests/test_prompt_registry.py tests/test_prompt_language_selection.py tests/test_client_loop_lifecycle.py -q -o log_cli=false
python -m pytest tests/ -q -o log_cli=false --show-capture=no
python -m pytest tests/coding/test_consolidator.py -k bad_output -q -o log_cli=false --show-capture=no
```

161 项专项结果包含 34 项协调器测试、32 项仓储测试、16 项客户端/向量合同及 79 项 prompt
registry 测试。仓储测试中 16 项为真实 PostgreSQL 参数，全部通过，没有缺连接/驱动跳过。
向量合同包括 SQLite/PostgreSQL × Chroma / Qdrant Local / Qdrant Server / FAISS，共 8 个
真实组合。模型与 embedding 仍为替身；这不是对模型归纳语义质量的验证。

Qdrant Local 合同仅打开经验集合，不代表完整事实+经验双层 store 初始化已通过；
Qdrant Server 合同执行完整 store 初始化。没有测试 Chroma HTTP、多 Python 版本或规模性能。
客户端合同覆盖公共同步/异步入口及诊断；其他 VDB 的存储合同不等同于所有客户端组合 E2E。

## 基线对照与未解决项

以 `git archive origin/main` 导出完整未修改基线，在同一 Python、依赖和测试服务上执行相同
SDK 命令。已核对 import 路径来自导出目录，而不是当前分支。两次完整运行的 JUnit 按完整
用例 ID 比较，本分支没有独有的失败或错误 ID；双方共有 90 个失败/错误用例 ID。基线另有
`test_reembed_copies_everything_and_never_touches_source[1024-512-old-new]` 失败，本分支该项
通过；未把这一单轮差异宣称为修复成果或稳定性保证。

共有失败涉及 Windows 文件占用、FAISS 持久化相关用例、信号与编码相关测试等；本轮没有逐项
裁定全部根因，也没有修改无关测试或弱化断言。完整 SDK 仍未通过；同名失败对照不能证明
这些用例中绝无新问题。33 个 skip、45 个 deselect 均未计为通过，默认命令不执行 integration。

原始日志与 JUnit 保留在本地 `.git/codex-validation/consolidation-20261114/`，不将含本机路径的
原始日志直接提交。本记录仅发布结果摘要；CI 尚须在 draft 的实际 head 上运行并核对。

## 变异验证

在独立 Python 进程中读取 `_validate_output()`，仅在内存里将 `len(sessions) < 2` 改为
`len(sessions) < 1`，放宽单会话结论限制。现有 `test_bad_output_fails_the_entire_batch` 的
空 `conditions`、仅一个支持会话参数按预期报 `DID NOT RAISE CodingWriteError`。
正常代码在新进程中运行同组 16 项全部通过。没有修改工作树源文件，变异进程退出即消失。

## Draft 后续

保持 draft，等待当前 head 的 CI 结果及代码审查；失败、跳过和未执行项均保持如实记录。
本轮不重跑历史真实模型抽样，不声称完成所有后端/运行模式的全链路验收。
回滚时先停用新巩固调用；升级后的业务库不能假定可直接交给旧版本打开，需先备份并按经过
验证的迁移或恢复流程处理。本轮未演练数据库降级/恢复。
