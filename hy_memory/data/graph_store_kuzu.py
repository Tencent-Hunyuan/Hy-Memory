"""
Agent Memory V2 - KuzuGraphStore

基于 Kuzu 嵌入式图数据库的图存储层实现。

Kuzu 优势:
- 嵌入式部署 (无需额外服务进程)
- 支持 Cypher 查询语言
- 高性能图遍历
- 支持属性图模型

注意: Kuzu 嵌入式 DB 只支持单进程访问。在多进程环境中会降级为 no-op 模式。
"""

from typing import Optional, List, Dict, Any, Tuple, Sequence
from datetime import datetime
from pathlib import Path
import asyncio
import concurrent.futures
import functools
import logging
import json
import threading
import time
import weakref

from ..models.memory import MemoryNode, MemoryLayer, MemoryStatus
from ..config import MemoryConfig
from ..structure import parse_hierarchy_path_payload
from ..utils.async_executor import run_in_executor_guarded
from .graph_store_base import GraphStoreBase, embed_text_for_graph

logger = logging.getLogger(__name__)

# Kuzu 专属单线程 executor。
# 关键约束：
#   1) Kuzu 的 Connection 不是线程安全的 —— 并发在同一连接上 execute 会数据损坏/崩溃。
#   2) Kuzu 的 QueryResult 有线程亲和性 —— execute 与结果消费（has_next/get_next）
#      必须在同一线程完成。
# 因此用 max_workers=1 的单线程池：既把同步的 Kuzu 调用挪出 event loop（不再阻塞
# 整个 server 的唯一 loop），又天然串行化所有 Kuzu 操作，满足上面两条约束。
# 绝不要调大 max_workers。
_KUZU_POOL_SIZE = 1
_kuzu_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=_KUZU_POOL_SIZE, thread_name_prefix="kuzu"
)
_kuzu_stats_lock = threading.Lock()
_kuzu_active = 0
_kuzu_max_active = 0
_kuzu_wait_count = 0
_kuzu_total_wait_ms = 0.0
_kuzu_max_wait_ms = 0.0


async def _run_in_kuzu_pool(func, *args, **kwargs):
    """在 Kuzu 单线程池中执行同步函数（execute + 结果消费必须整体在此运行）。"""
    submitted = time.perf_counter()
    call = functools.partial(func, *args, **kwargs) if (args or kwargs) else func

    def _measured():
        global _kuzu_active, _kuzu_max_active, _kuzu_wait_count
        global _kuzu_total_wait_ms, _kuzu_max_wait_ms
        wait_ms = (time.perf_counter() - submitted) * 1000
        with _kuzu_stats_lock:
            _kuzu_wait_count += 1
            _kuzu_total_wait_ms += wait_ms
            _kuzu_max_wait_ms = max(_kuzu_max_wait_ms, wait_ms)
            _kuzu_active += 1
            _kuzu_max_active = max(_kuzu_max_active, _kuzu_active)
        try:
            if wait_ms >= 1000:
                logger.info("TRACE_PERF KUZU_EXECUTOR_WAIT wait_ms=%.1f", wait_ms)
            return call()
        finally:
            with _kuzu_stats_lock:
                _kuzu_active -= 1

    return await run_in_executor_guarded(_kuzu_executor, _measured)


# 本进程里已打开、尚未 close 的 store。drop_graph 据此拒绝删掉一个正被
# 活着的 client 使用的图库。WeakSet：store 被回收后自动消失。
_LIVE_STORES: "weakref.WeakSet[KuzuGraphStore]" = weakref.WeakSet()


def open_graph_paths() -> List[str]:
    """Real paths of the graph databases live stores in this process hold open."""
    import os
    return [
        os.path.realpath(str(store._db_path))
        for store in list(_LIVE_STORES)
        if store._db is not None
    ]


def kuzu_executor_snapshot() -> Dict[str, Any]:
    with _kuzu_stats_lock:
        return {
            "workers": _KUZU_POOL_SIZE,
            "active": _kuzu_active,
            "max_active": _kuzu_max_active,
            "wait_count": _kuzu_wait_count,
            "total_wait_ms": round(_kuzu_total_wait_ms, 3),
            "max_wait_ms": round(_kuzu_max_wait_ms, 3),
        }


def _materialize_row(row: Any) -> List[Any]:
    """把 Kuzu 结果行物化成跨线程安全的普通 list。

    行内元素可能是：
      - Kuzu Node 对象（RETURN m 时 row[0]）→ 转成 dict（_row_to_memory_node 能吃 dict）
      - 标量（RETURN m.x, m.y 时的各列）→ 原样保留
    物化后脱离 QueryResult，可安全跨线程传回 event loop。
    """
    out: List[Any] = []
    for col in row:
        if isinstance(col, dict) or isinstance(col, (str, int, float, bool)) or col is None:
            out.append(col)
        elif hasattr(col, "__iter__") and not isinstance(col, (list, tuple)):
            # Kuzu Node / Rel 对象（dict-like）
            try:
                out.append(dict(col))
            except Exception:
                out.append(col)
        else:
            out.append(col)
    return out


# Kuzu Cypher schema DDL
# 注意: Kuzu < 0.4.0 不支持 IF NOT EXISTS，需要先检查表是否存在再创建
# embedding / beh_embedding 列使用 FLOAT[{dims}]，dims 在 init_schema 时动态替换
_SCHEMA_DDL_TABLES = {
    # Node tables
    "User": """
CREATE NODE TABLE User(
    isolation_key STRING,
    user_id STRING,
    agent_id STRING,
    created_at TIMESTAMP,
    PRIMARY KEY (isolation_key)
)""",
    "Memory": None,  # 动态生成，依赖 embedding_dims
    # Topic 带 embedding：tag 是自由文本，"work"/"job"/"career" 不归一化就是三个孤岛，
    # 连画像聚合（按 Topic 度数排序）都做不了。写 tag 时按 embedding 余弦复用已有 Topic。
    # 注：Kuzu 带索引的列 CREATE 后不能 SET，但 Topic.embedding 没建 HNSW 索引，可以更新。
    "Topic": """
CREATE NODE TABLE Topic(
    topic_id STRING,
    isolation_key STRING,
    name STRING,
    embedding DOUBLE[],
    created_at TIMESTAMP,
    PRIMARY KEY (topic_id)
)""",
    "HierarchyTopic": """
CREATE NODE TABLE HierarchyTopic(
    hierarchy_topic_id STRING,
    isolation_key STRING,
    subject STRING,
    name STRING,
    path_json STRING,
    depth INT64,
    embedding DOUBLE[],
    created_at TIMESTAMP,
    PRIMARY KEY (hierarchy_topic_id)
)""",
    # 正文 NER 实体（≠ Topic）。Kuzu 对存量 DB 可补建缺失表；新列不行。
    "Entity": """
CREATE NODE TABLE Entity(
    entity_id STRING,
    isolation_key STRING,
    name STRING,
    name_norm STRING,
    label STRING,
    created_at TIMESTAMP,
    PRIMARY KEY (entity_id)
)""",
    # Rel tables
    "HAS_MEMORY": """
CREATE REL TABLE HAS_MEMORY(
    FROM User TO Memory,
    created_at TIMESTAMP
)""",
    "TAGGED_WITH": """
CREATE REL TABLE TAGGED_WITH(
    FROM Memory TO Topic,
    created_at TIMESTAMP
)""",
    "MEMBER_OF": """
CREATE REL TABLE MEMBER_OF(
    FROM Memory TO HierarchyTopic,
    created_at TIMESTAMP
)""",
    "SUBTOPIC_OF": """
CREATE REL TABLE SUBTOPIC_OF(
    FROM HierarchyTopic TO HierarchyTopic,
    created_at TIMESTAMP
)""",
    "MENTIONS": """
CREATE REL TABLE MENTIONS(
    FROM Memory TO Entity,
    created_at TIMESTAMP
)""",
    "RELATED_TO": """
CREATE REL TABLE RELATED_TO(
    FROM Memory TO Memory,
    relation_type STRING,
    weight DOUBLE,
    created_at TIMESTAMP
)""",
    # VdbRef shadow node + DERIVED_FROM edge
    "VdbRef": """
CREATE NODE TABLE VdbRef(
    node_id STRING,
    layer STRING,
    PRIMARY KEY (node_id)
)""",
    "DERIVED_FROM": """
CREATE REL TABLE DERIVED_FROM(
    FROM Memory TO VdbRef,
    created_at TIMESTAMP
)""",
    # Cross-domain schema induction: L6 basic → L6 core (单向)
    "CROSS_ABSTRACTS_TO": """
CREATE REL TABLE CROSS_ABSTRACTS_TO(
    FROM Memory TO Memory,
    created_at TIMESTAMP
)""",
    # 时序脊：同一 Topic 内按 memory_at 串起的**有向**递进链。
    #
    # 与 supersedes 的区别很重要：supersedes 表达**矛盾取代**（旧的不再成立），
    # TEMPORAL_NEXT 表达**递进**（旧的仍然成立，只是更早）。这两个语义此前混在一起。
    #
    # 注：Kuzu 的 initialize 会在已有 DB 上创建**缺失的表**（只有新增列不行），
    # 所以新增这张 REL TABLE 对存量 DB 是安全的。
    "TEMPORAL_NEXT": """
CREATE REL TABLE TEMPORAL_NEXT(
    FROM Memory TO Memory,
    topic_id STRING,
    created_at TIMESTAMP
)""",
}


def _make_memory_ddl(dims: int) -> str:
    """生成 Memory 表 DDL，embedding 列维度由配置决定"""
    return f"""
CREATE NODE TABLE Memory(
    node_id STRING,
    isolation_key STRING,
    user_id STRING,
    agent_id STRING,
    source STRING,
    layer STRING,
    content STRING,
    content_type STRING,
    status STRING,
    version INT64,
    confidence DOUBLE,
    source_type STRING,
    emotional_valence DOUBLE,
    emotional_arousal DOUBLE,
    specificity_score DOUBLE,
    rarity_score DOUBLE,
    longtail_flag BOOLEAN,
    meta_tags STRING,
    source_session_id STRING,
    source_turn_index INT64,
    temporal_anchor STRING,
    access_count INT64,
    created_at TIMESTAMP,
    valid_from TIMESTAMP,
    valid_until TIMESTAMP,
    last_accessed_at TIMESTAMP,
    previous_version_id STRING,
    superseded_by_id STRING,
    change_reason STRING,
    evidence_chain STRING,
    custom_json STRING,
    tags STRING,
    extra_json STRING,
    embedding FLOAT[{dims}],
    beh_embedding FLOAT[{dims}],
    PRIMARY KEY (node_id)
)"""


class KuzuGraphStore(GraphStoreBase):
    """
    Kuzu 图存储

    使用嵌入式 Kuzu 数据库，通过 Cypher 查询实现图操作。

    注意: Kuzu 嵌入式 DB 只支持单进程访问。在多进程（如 tRPC 多 worker）环境中，
    只有第一个获取到锁的进程能正常使用 GraphStore，其他进程会降级为 no-op 模式。
    """

    _supports_batch_topic_tags = True

    def __init__(self, config: MemoryConfig):
        super().__init__(config)
        self._db = None
        self._conn = None
        self._available = False  # 是否成功初始化

        # embedding 维度（从 VectorStore 或 Embedder 配置中取）
        vs_dims = getattr(getattr(config, 'vector_store', None), 'embedding_dims', None)
        emb_dims = getattr(getattr(config, 'embedder', None), 'embedding_dims', None)
        self._embedding_dims = vs_dims or emb_dims or 1536

        # 图数据库存储路径
        graph_config = getattr(config, 'graph_store', None)
        if graph_config and hasattr(graph_config, 'db_path'):
            self._db_path = graph_config.db_path
        else:
            self._db_path = str(
                Path(config.vector_store.persist_directory).parent / "kuzu_db"
            )

    @staticmethod
    def executor_snapshot() -> Dict[str, Any]:
        return kuzu_executor_snapshot()

    async def initialize(self) -> None:
        """初始化 Kuzu 数据库并创建 Schema
        
        在多进程环境中，如果 Kuzu 文件被其他进程锁定，会降级为 no-op 模式。
        使用线程池 + 超时来防止 Kuzu Database() 构造函数阻塞事件循环。
        """
        try:
            import kuzu
        except ImportError:
            raise ImportError(
                "kuzu is required. Install with: pip install kuzu"
            )

        db_path = Path(self._db_path)
        # Kuzu >= 0.11 自行管理数据库文件，不接受已存在的空目录
        db_path.parent.mkdir(parents=True, exist_ok=True)
        if db_path.is_dir():
            # 如果路径是一个空目录，删除它让 Kuzu 自己创建
            try:
                if not any(db_path.iterdir()):
                    logger.info(f"Removing empty directory at {db_path} for Kuzu initialization")
                    db_path.rmdir()
                else:
                    # 非空目录可能是旧版 Kuzu 数据，尝试直接使用
                    logger.info(f"Non-empty directory at {db_path}, attempting Kuzu open")
            except Exception as rm_err:
                logger.warning(f"Could not clean up {db_path}: {rm_err}")

        def _open_kuzu_db():
            """在线程池中执行 Kuzu Database 构造（可能阻塞获取文件锁）"""
            db = kuzu.Database(self._db_path)
            conn = kuzu.Connection(db)
            return db, conn

        try:
            # 用 guarded executor await，避免这台机器上已完成的线程任务丢失
            # event-loop wakeup 而被误判成文件锁超时。
            db, conn = await asyncio.wait_for(
                run_in_executor_guarded(None, _open_kuzu_db),
                timeout=5.0,
            )
            self._db = db
            self._conn = conn
            _LIVE_STORES.add(self)
        except asyncio.TimeoutError:
            logger.warning(
                f"GraphStore init timed out after 5s (Kuzu file lock contention). "
                f"Degrading to no-op mode. This is expected in multi-worker environments."
            )
            self._db = None
            self._conn = None
            self._available = False
            return
        except Exception as db_err:
            # Kuzu 初始化失败 — 降级为 no-op，记录完整诊断信息
            import os as _os
            try:
                import kuzu as _kuzu_mod
                _kuzu_ver = getattr(_kuzu_mod, '__version__', 'unknown')
            except Exception:
                _kuzu_ver = 'import_failed'
            logger.error(
                f"GraphStore init failed (will degrade to no-op): {db_err}. "
                f"db_path={self._db_path}, "
                f"path_exists={_os.path.exists(self._db_path)}, "
                f"kuzu_version={_kuzu_ver}",
            )
            logger.debug("GraphStore init traceback:", exc_info=True)
            self._db = None
            self._conn = None
            self._available = False
            return

        # 获取 embedding 维度 → 动态生成 Memory 表 DDL
        dims = self._embedding_dims
        ddl_tables = dict(_SCHEMA_DDL_TABLES)
        ddl_tables["Memory"] = _make_memory_ddl(dims)

        # 执行 Schema DDL (先检查表是否存在，不存在才创建)
        ddl_errors = []
        for table_name, ddl_stmt in ddl_tables.items():
            if ddl_stmt is None:
                continue
            if self._table_exists(table_name):
                logger.debug(f"Table {table_name} already exists, skipping")
                continue
            try:
                self._conn.execute(ddl_stmt + ";")
                logger.debug(f"Created table {table_name}")
            except Exception as e:
                err_msg = str(e)
                # "already exists" 类错误可以忽略
                if "already exist" in err_msg.lower() or "duplicate" in err_msg.lower():
                    logger.debug(f"Schema DDL note (ignored): {e}")
                else:
                    ddl_errors.append((table_name, err_msg))
                    logger.warning(f"Schema DDL failed for {table_name}: {e}")

        # 检查关键表是否存在
        critical_tables = ["User", "Memory", "Topic"]
        missing = [t for t in critical_tables if not self._table_exists(t)]
        if missing:
            logger.error(
                f"GraphStore schema incomplete: missing tables {missing}. "
                f"DDL errors: {ddl_errors}. Degrading to no-op."
            )
            self._db = None
            self._conn = None
            self._available = False
            return

        # 创建 HNSW 向量索引（已有则跳过）
        self._ensure_vector_indexes(dims)

        self._available = True
        logger.info(f"GraphStore initialized (Kuzu), db_path={self._db_path}, embedding_dims={dims}")

    def _ensure_vector_indexes(self, dims: int) -> None:
        """确保 Memory 表上的 HNSW 向量索引存在

        注意: Kuzu 不允许 SET 被索引的列。因此：
        - embedding (V_con): 建索引，CREATE 时一次性写入，不做后续 SET
        - beh_embedding (V_beh): 不建索引，允许 sweeper 后续 SET 写入
        beh_embedding 的检索走内存矩阵计算（N < 100，毫秒级）
        """
        try:
            self._conn.execute(
                "CALL CREATE_VECTOR_INDEX('Memory', 'memory_content_idx', 'embedding', "
                "metric := 'cosine');"
            )
            logger.info("Created vector index memory_content_idx on Memory.embedding")
        except Exception as e:
            err_msg = str(e).lower()
            if "already exist" in err_msg or "duplicate" in err_msg:
                logger.debug("Vector index memory_content_idx already exists")
            else:
                logger.warning(f"Failed to create vector index memory_content_idx: {e}")

    def _execute_materialized(
        self, query: str, params: Optional[Dict[str, Any]] = None
    ) -> Optional[List[List[Any]]]:
        """【必须在 kuzu 单线程 executor 内调用】执行 query 并把 QueryResult 完整
        物化成跨线程安全的 List[row]（row 为 list，Node 元素转 dict）后返回。

        execute 与 has_next/get_next 消费在同一 worker 线程完成（QueryResult 线程
        亲和）。返回的 list 已脱离 QueryResult，可安全传回 event loop。
        无结果的写语句返回空 list；GraphStore 不可用返回 None。
        """
        if not self._available or self._conn is None:
            return None
        result = self._conn.execute(query, params) if params else self._conn.execute(query)
        rows: List[List[Any]] = []
        try:
            while result.has_next():
                rows.append(_materialize_row(result.get_next()))
        except Exception:
            # 写语句可能没有可迭代结果集
            pass
        return rows

    async def _run(
        self, query: str, params: Optional[Dict[str, Any]] = None
    ) -> Optional[List[List[Any]]]:
        """异步执行 Cypher：把同步的 Kuzu 调用卸载到单线程 executor，不阻塞 event loop。

        返回物化后的 List[row]（row 为 list）；不可用时返回 None。
        写语句忽略返回值即可。
        """
        if not self._available or self._conn is None:
            return None
        return await _run_in_kuzu_pool(self._execute_materialized, query, params)

    def _execute(self, query: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """【已废弃/仅 init 用】同步执行，直接跑在调用线程上。

        运行期严禁调用（会阻塞 event loop，正是死锁根因）。仅 initialize() 期间、
        服务尚未对外时使用。运行期请一律用 await self._run(...)。
        """
        if not self._available or self._conn is None:
            return None
        if params:
            return self._conn.execute(query, params)
        return self._conn.execute(query)

    def _table_exists(self, table_name: str) -> bool:
        """检查 Kuzu 中某个 node/rel table 是否存在（仅 init 期同步调用）

        兼容不同 Kuzu 版本:
        - Kuzu 0.4.x: show_tables() 返回 [name, type, comment]
        - Kuzu 0.11+: show_tables() 返回 [id, name, type, database, comment]
        """
        if self._conn is None:
            return False
        try:
            result = self._conn.execute("CALL show_tables() RETURN *;")
            while result.has_next():
                row = result.get_next()
                # 遍历所有列查找表名（兼容不同版本）
                for col in row:
                    if col == table_name:
                        return True
            return False
        except Exception as e:
            logger.debug(f"_table_exists check failed: {e}")
            return False


    # ================================================================
    # User 节点管理
    # ================================================================

    async def ensure_user_node(self, isolation_key: str, user_id: str = "",
                                agent_id: str = "") -> None:
        """确保 User 节点存在
        
        Kuzu 0.4.x 不支持 MERGE ... ON CREATE SET，改用 exists check + CREATE/UPDATE
        """
        if not self._available:
            return
        try:
            # 检查是否已存在
            rows = await self._run(
                "MATCH (u:User {isolation_key: $ik}) RETURN u.isolation_key;",
                {"ik": isolation_key}
            )
            exists = bool(rows)

            if not exists:
                await self._run(
                    "CREATE (u:User {isolation_key: $ik, user_id: $uid, "
                    "agent_id: $sid, created_at: $now});",
                    {"ik": isolation_key, "uid": user_id,
                     "sid": agent_id, "now": datetime.now()}
                )
        except Exception as e:
            logger.warning(f"ensure_user_node failed: {e}")

    # ================================================================
    # Memory 节点 CRUD
    # ================================================================

    async def upsert_memory_node(self, node: MemoryNode) -> str:
        """创建或更新 Memory 节点 + User→Memory 边"""
        if not self._available:
            return node.node_id
        isolation_key = node.get_isolation_key()
        await self.ensure_user_node(
            isolation_key, node.user_id, node.agent_id
        )

        now = datetime.now()

        # 安全获取属性（MemoryNode 在不同版本/场景下可能缺少某些字段）
        def _val(attr, default=""):
            v = getattr(node, attr, default)
            if v is None:
                return default
            return v.value if hasattr(v, 'value') else v

        meta_tags_raw = getattr(node, 'meta_tags', []) or []
        meta_tags_strs = [
            (t.value if hasattr(t, 'value') else str(t)) for t in meta_tags_raw
        ]

        params = {
            "nid": node.node_id,
            "ik": isolation_key,
            "uid": node.user_id,
            "sid": node.agent_id,
            "source": node.source,
            "layer": node.layer.value,
            "content": node.content,
            "ctype": _val('content_type', ''),
            "status": node.status.value,
            "ver": getattr(node, 'version', 1) or 1,
            "conf": node.confidence,
            "stype": node.source_type.value,
            "eval": getattr(node, 'emotional_valence', 0.0) or 0.0,
            "earou": getattr(node, 'emotional_arousal', 0.0) or 0.0,
            "spec": getattr(node, 'specificity_score', 0.0) or 0.0,
            "rar": getattr(node, 'rarity_score', 0.0) or 0.0,
            "lt": getattr(node, 'longtail_flag', False) or False,
            "mtags": json.dumps(meta_tags_strs),
            "ssid": getattr(node, 'source_session_id', '') or '',
            "stidx": getattr(node, 'source_turn_index', 0) or 0,
            "tanc": getattr(node, 'temporal_anchor', '') or '',
            "ac": getattr(node, 'access_count', 0) or 0,
            "cat": getattr(node, 'created_at', None) or now,
            "vf": node.valid_from or now,
            "vu": getattr(node, 'valid_until', None),
            "laat": getattr(node, 'last_accessed_at', None),
            "pvid": getattr(node, 'previous_version_id', '') or '',
            "sbid": getattr(node, 'superseded_by_id', '') or '',
            "cr": getattr(node, 'change_reason', '') or '',
            "ec": json.dumps(getattr(node, 'evidence_chain', []) or []),
            "cust": json.dumps(getattr(node, 'custom', {}) or {}),
            "tags": json.dumps(getattr(node, 'tags', []) or []),
            "extra": "{}",
        }

        # 向量属性：从 MemoryNode._graph_embedding / _graph_beh_embedding 获取
        # 这些是瞬态属性，不序列化，仅在 create_graph_node 流程中传递
        embedding = getattr(node, '_graph_embedding', None)
        beh_embedding = getattr(node, '_graph_beh_embedding', None)

        # Kuzu 0.4.x 不支持 MERGE ... ON CREATE/MATCH SET，改用 exists check + CREATE/UPDATE
        exists_rows = await self._run(
            "MATCH (m:Memory {node_id: $nid}) RETURN m.node_id;",
            {"nid": node.node_id}
        )
        node_exists = bool(exists_rows)

        if not node_exists:
            # CREATE 新节点（Kuzu 被索引的列只能在 CREATE 时写入，不能 SET）
            params["emb"] = embedding
            params["beh_emb"] = beh_embedding
            await self._run(
                """
                CREATE (m:Memory {
                    node_id: $nid, isolation_key: $ik, user_id: $uid,
                    agent_id: $sid, source: $source, layer: $layer, content: $content,
                    content_type: $ctype, status: $status, version: $ver,
                    confidence: $conf, source_type: $stype,
                    emotional_valence: $eval, emotional_arousal: $earou,
                    specificity_score: $spec, rarity_score: $rar,
                    longtail_flag: $lt, meta_tags: $mtags,
                    source_session_id: $ssid, source_turn_index: $stidx,
                    temporal_anchor: $tanc, access_count: $ac,
                    created_at: $cat, valid_from: $vf, valid_until: $vu,
                    last_accessed_at: $laat,
                    previous_version_id: $pvid, superseded_by_id: $sbid,
                    change_reason: $cr, evidence_chain: $ec,
                    custom_json: $cust, tags: $tags, extra_json: $extra,
                    embedding: $emb, beh_embedding: $beh_emb
                });
                """,
                params,
            )
        else:
            # UPDATE 已有节点（content embedding 有索引，不能 SET，见 update_embedding）
            update_sets = [
                "m.content = $content", "m.status = $status", "m.version = $ver",
                "m.source = $source",
                "m.confidence = $conf", "m.emotional_valence = $eval",
                "m.emotional_arousal = $earou", "m.meta_tags = $mtags",
                "m.access_count = $ac", "m.last_accessed_at = $laat",
                "m.valid_until = $vu", "m.superseded_by_id = $sbid",
                "m.change_reason = $cr", "m.custom_json = $cust",
                "m.tags = $tags",
            ]
            if beh_embedding is not None:
                params["beh_emb"] = beh_embedding
                update_sets.append("m.beh_embedding = $beh_emb")
            if embedding is not None:
                logger.debug(
                    f"Kuzu upsert: node {node.node_id} exists; content embedding "
                    f"ignored on MATCH (set at CREATE via _graph_embedding only)"
                )
            await self._run(
                f"""
                MATCH (m:Memory {{node_id: $nid}})
                SET {", ".join(update_sets)};
                """,
                params,
            )

        # 确保 HAS_MEMORY 边 (Kuzu 0.4.x 不支持 MERGE edge，用 CREATE)
        try:
            await self._run(
                """
                MATCH (u:User {isolation_key: $ik}), (m:Memory {node_id: $nid})
                CREATE (u)-[:HAS_MEMORY {created_at: $now}]->(m);
                """,
                {"ik": isolation_key, "nid": node.node_id, "now": now},
            )
        except Exception as e:
            logger.debug(f"HAS_MEMORY edge note: {e}")

        return node.node_id

    async def get_node(self, node_id: str) -> Optional[MemoryNode]:
        """获取单个 Memory 节点"""
        if not self._available:
            return None
        rows = await self._run(
            "MATCH (m:Memory {node_id: $nid}) RETURN m;",
            {"nid": node_id},
        )
        if not rows:
            return None
        return self._row_to_memory_node(rows[0][0])

    async def get_nodes_by_ids(self, node_ids: List[str]) -> List[MemoryNode]:
        """批量获取"""
        if not self._available or not node_ids:
            return []
        nodes = []
        for nid in node_ids:
            n = await self.get_node(nid)
            if n:
                nodes.append(n)
        return nodes

    async def get_all_nodes(
        self,
        isolation_key: str,
        layer: Optional[MemoryLayer] = None,
        status: Optional[MemoryStatus] = None,
        limit: int = 100,
    ) -> List[MemoryNode]:
        """按条件获取节点列表"""
        if not self._available:
            return []
        conditions = ["m.isolation_key = $ik"]
        params: Dict[str, Any] = {"ik": isolation_key}

        if layer:
            conditions.append("m.layer = $layer")
            params["layer"] = layer.value
        if status:
            conditions.append("m.status = $status")
            params["status"] = status.value

        where = " AND ".join(conditions)
        # Kuzu 0.4.x 不支持参数化 LIMIT，使用字符串拼接
        query = f"MATCH (m:Memory) WHERE {where} RETURN m LIMIT {int(limit)};"
        rows = await self._run(query, params)

        nodes = []
        for row in (rows or []):
            nodes.append(self._row_to_memory_node(row[0]))
        return nodes

    async def get_nodes_by_user(
        self,
        user_id: str,
        layer: Optional[MemoryLayer] = None,
        status: Optional[MemoryStatus] = None,
        limit: int = 100,
    ) -> List[MemoryNode]:
        """Return one user's nodes across all agent/session isolation keys."""
        if not self._available or not user_id:
            return []
        conditions = ["m.user_id = $uid"]
        params: Dict[str, Any] = {"uid": user_id}
        if layer:
            conditions.append("m.layer = $layer")
            params["layer"] = layer.value
        if status:
            conditions.append("m.status = $status")
            params["status"] = status.value
        where = " AND ".join(conditions)
        rows = await self._run(
            f"MATCH (m:Memory) WHERE {where} RETURN m LIMIT {int(limit)};",
            params,
        )
        return [self._row_to_memory_node(row[0]) for row in (rows or [])]

    async def get_profile(self, isolation_key: str) -> Optional[MemoryNode]:
        """获取 L5 Identity Profile 节点"""
        if not self._available:
            return None
        rows = await self._run(
            "MATCH (m:Memory) WHERE m.isolation_key = $ik AND m.layer = $layer "
            "AND m.status = 'active' RETURN m LIMIT 1;",
            {"ik": isolation_key, "layer": MemoryLayer.L4_IDENTITY.value},
        )
        if not rows:
            return None
        return self._row_to_memory_node(rows[0][0])

    async def update_node(self, node_id: str, updates: Dict[str, Any]) -> bool:
        """更新节点属性"""
        if not self._available:
            return False
        if not updates:
            return True
        set_clauses = []
        params: Dict[str, Any] = {"nid": node_id}
        for key, value in updates.items():
            safe_key = key.replace("-", "_")
            set_clauses.append(f"m.{safe_key} = ${safe_key}")
            params[safe_key] = value

        set_str = ", ".join(set_clauses)
        try:
            await self._run(
                f"MATCH (m:Memory {{node_id: $nid}}) SET {set_str};",
                params,
            )
            return True
        except Exception as e:
            logger.warning(f"update_node failed for {node_id}: {e}")
            return False

    async def delete_node(self, node_id: str) -> bool:
        """删除 Memory 节点及其关联边"""
        if not self._available:
            return False
        try:
            await self._run(
                "MATCH (m:Memory {node_id: $nid}) DETACH DELETE m;",
                {"nid": node_id},
            )
            return True
        except Exception as e:
            logger.warning(f"delete_node failed for {node_id}: {e}")
            return False

    async def delete_all_nodes(self, isolation_key: str) -> int:
        """删除某隔离键下所有 Memory 节点"""
        if not self._available:
            return 0
        try:
            # 先统计
            count_rows = await self._run(
                "MATCH (m:Memory) WHERE m.isolation_key = $ik RETURN count(m);",
                {"ik": isolation_key},
            )
            count = count_rows[0][0] if count_rows else 0

            await self._run(
                "MATCH (m:Memory) WHERE m.isolation_key = $ik DETACH DELETE m;",
                {"ik": isolation_key},
            )
            return count
        except Exception as e:
            logger.warning(f"delete_all_nodes failed for {isolation_key}: {e}")
            return 0

    async def clear_projection(
        self,
        user_id: str,
        agent_id: Optional[str] = None,
        *,
        layer: str = "l2_fact",
    ) -> Dict[str, int]:
        """见基类。按 layer 收窄，避免连带删掉 System 2 的 l6_schema。"""
        out = {"memories": 0, "topics": 0, "entities": 0}
        if not self._available:
            return out
        try:
            cond = "m.layer = $layer AND m.user_id = $uid"
            params: Dict[str, Any] = {"layer": layer, "uid": user_id}
            if agent_id:
                cond += " AND m.agent_id = $aid"
                params["aid"] = agent_id

            rows = await self._run(
                f"MATCH (m:Memory) WHERE {cond} RETURN count(m);", params
            )
            out["memories"] = int(rows[0][0]) if rows else 0
            await self._run(f"MATCH (m:Memory) WHERE {cond} DETACH DELETE m;", params)

            # 仅清目标 user/agent scope 内的孤儿 Topic。不能让单用户回填
            # 顺手清理全库其他 namespace 的历史孤儿节点。
            topic_scope = (
                f"{user_id}::{agent_id}" if agent_id else f"{user_id}::"
            )
            topic_condition = (
                "(t.isolation_key = $scope OR starts_with(t.isolation_key, $prefix))"
            )
            rows = await self._run(
                f"""
                MATCH (t:Topic)
                WHERE {topic_condition}
                  AND NOT EXISTS {{ MATCH (:Memory)-[:TAGGED_WITH]->(t) }}
                RETURN count(t);
                """,
                {"scope": topic_scope, "prefix": topic_scope + "::"},
            )
            n = int(rows[0][0]) if rows else 0
            if n:
                await self._run(
                    f"""
                    MATCH (t:Topic)
                    WHERE {topic_condition}
                      AND NOT EXISTS {{ MATCH (:Memory)-[:TAGGED_WITH]->(t) }}
                    DETACH DELETE t;
                    """,
                    {"scope": topic_scope, "prefix": topic_scope + "::"},
                )
            out["topics"] = n

            # HierarchyTopic is cross-session but still user/agent scoped.
            # Never prune orphan nodes belonging to another user's graph.
            hierarchy_scope = (
                f"{user_id}::{agent_id}" if agent_id else f"{user_id}::"
            )
            if agent_id:
                out["topics"] += await self.prune_hierarchy_topics(hierarchy_scope)
            else:
                scope_rows = await self._run(
                    "MATCH (t:HierarchyTopic) "
                    "WHERE t.isolation_key STARTS WITH $prefix "
                    "RETURN DISTINCT t.isolation_key;",
                    {"prefix": hierarchy_scope},
                )
                for row in scope_rows:
                    out["topics"] += await self.prune_hierarchy_topics(str(row[0]))

            # 孤儿 Entity，同样限制在目标 scope。
            try:
                rows = await self._run(
                    f"""
                    MATCH (e:Entity)
                    WHERE {topic_condition.replace('t.', 'e.')}
                      AND NOT EXISTS {{ MATCH (:Memory)-[:MENTIONS]->(e) }}
                    RETURN count(e);
                    """,
                    {"scope": topic_scope, "prefix": topic_scope + "::"},
                )
                ne = int(rows[0][0]) if rows else 0
                if ne:
                    await self._run(
                        f"""
                        MATCH (e:Entity)
                        WHERE {topic_condition.replace('t.', 'e.')}
                          AND NOT EXISTS {{ MATCH (:Memory)-[:MENTIONS]->(e) }}
                        DETACH DELETE e;
                        """,
                        {"scope": topic_scope, "prefix": topic_scope + "::"},
                    )
                out["entities"] = ne
            except Exception as ee:
                logger.debug(f"clear_projection orphan Entity skipped: {ee}")
        except Exception as e:
            logger.warning(f"clear_projection failed for {user_id}: {e}")
        return out

    async def delete_by_metadata(
        self,
        user_id: str,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> int:
        """按 metadata 删除该用户范围内的图数据（Memory + User + Topic + 孤立 VdbRef）"""
        if not self._available:
            return 0
        try:
            conditions = ["m.user_id = $uid"]
            params: Dict[str, Any] = {"uid": user_id}
            if agent_id is not None:
                conditions.append("m.agent_id = $aid")
                params["aid"] = agent_id
            if session_id is not None:
                ik = f"{user_id}::{agent_id or 'default_agent'}::{session_id}"
                conditions.append("m.isolation_key = $ik")
                params["ik"] = ik

            where = " AND ".join(conditions)
            total = 0

            count_rows = await self._run(
                f"MATCH (m:Memory) WHERE {where} RETURN count(m);",
                params,
            )
            mem_count = count_rows[0][0] if count_rows else 0
            await self._run(
                f"MATCH (m:Memory) WHERE {where} DETACH DELETE m;",
                params,
            )
            total += mem_count

            # User 节点（按 user_id / agent_id / session 粒度）
            user_conds = ["u.user_id = $uid"]
            user_params: Dict[str, Any] = {"uid": user_id}
            if session_id is not None:
                ik = f"{user_id}::{agent_id or 'default_agent'}::{session_id}"
                user_conds.append("u.isolation_key = $ik")
                user_params["ik"] = ik
            elif agent_id is not None:
                user_conds.append("u.agent_id = $aid")
                user_params["aid"] = agent_id
            user_where = " AND ".join(user_conds)
            await self._run(
                f"MATCH (u:User) WHERE {user_where} DETACH DELETE u;",
                user_params,
            )

            # Topic 节点（isolation_key 与 User 同格式）
            topic_conds = ["t.isolation_key STARTS WITH $pfx"]
            topic_params: Dict[str, Any] = {"pfx": f"{user_id}::"}
            if agent_id is not None and session_id is None:
                topic_params["pfx"] = f"{user_id}::{agent_id}::"
            elif session_id is not None:
                topic_params["pfx"] = f"{user_id}::{agent_id or 'default_agent'}::{session_id}"
                topic_conds = ["t.isolation_key = $pfx"]
            topic_where = " AND ".join(topic_conds)
            await self._run(
                f"MATCH (t:Topic) WHERE {topic_where} DETACH DELETE t;",
                topic_params,
            )

            # HierarchyTopic is scoped across sessions. Deleting one session
            # removes its Memory edges above, then prunes only now-empty leaves
            # and ancestors. Deleting an agent/user can remove that whole scope.
            hierarchy_scope = f"{user_id}::{agent_id or 'default_agent'}"
            if session_id is not None:
                await self.prune_hierarchy_topics(hierarchy_scope)
            else:
                hierarchy_params: Dict[str, Any] = {"pfx": f"{user_id}::"}
                hierarchy_where = "t.isolation_key STARTS WITH $pfx"
                if agent_id is not None:
                    hierarchy_params["scope"] = hierarchy_scope
                    hierarchy_where = "t.isolation_key = $scope"
                await self._run(
                    f"MATCH (t:HierarchyTopic) WHERE {hierarchy_where} "
                    "DETACH DELETE t;",
                    hierarchy_params,
                )

            # 孤立 VdbRef（DERIVED_FROM 已随 Memory 删除）
            await self._run(
                "MATCH (v:VdbRef) "
                "OPTIONAL MATCH ()-[r:DERIVED_FROM]->(v) "
                "WITH v, count(r) AS cnt WHERE cnt = 0 "
                "DETACH DELETE v;",
                {},
            )

            logger.info(
                f"[graph-store] delete_by_metadata user={user_id} agent={agent_id} "
                f"session={session_id} memory_nodes={mem_count}"
            )
            return total
        except Exception as e:
            logger.warning(f"delete_by_metadata failed (user_id={user_id}): {e}")
            return 0

    # ================================================================
    # 边操作
    # ================================================================

    async def add_edge(
        self,
        source_id: str,
        target_id: str,
        edge_type: str,
        properties: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """添加 Memory→Memory 关系边 (RELATED_TO)，自动建双向边"""
        if not self._available:
            return False
        props = properties or {}
        now = datetime.now()

        edge_type_upper = edge_type.upper()
        if edge_type_upper != "RELATED_TO":
            logger.warning(f"Unsupported edge type: {edge_type}, only RELATED_TO is supported")
            return False

        params = {
            "src": source_id, "tgt": target_id,
            "rtype": props.get("relation_type", "related"),
            "w": props.get("weight", 1.0),
            "now": now,
        }

        # 必须用 MERGE 而不是 CREATE：同一对节点之间会**反复**建同类型边 ——
        # UPDATE 是原地更新（node_id 不变），一条记忆被合并多次就会多次参与投影；
        # System 2 的 agent 循环更可能对同一对反复调用（线上实测 `related` 有 20 万对
        # 重复、最严重 32 份）。重复边不放大打分（build_adjacency 折叠成最强），
        # 但会吃掉 get_related_edges 的 max_edges 预算（Cypher LIMIT 先截断、去重在
        # Python 侧），把本该返回的真实边挤掉。
        # ON MATCH 取较大权重，与读侧"折叠成最强"语义一致。
        _merge = """
            MATCH (a:Memory {{node_id: ${src}}}), (b:Memory {{node_id: ${tgt}}})
            MERGE (a)-[r:RELATED_TO {{relation_type: $rtype}}]->(b)
                ON CREATE SET r.weight = $w, r.created_at = $now
                ON MATCH SET r.weight =
                    CASE WHEN $w > r.weight THEN $w ELSE r.weight END;
        """
        try:
            # 正向 A→B
            await self._run(_merge.format(src="src", tgt="tgt"), params)
            # 反向 B→A
            await self._run(_merge.format(src="tgt", tgt="src"), params)
            return True
        except Exception as e:
            logger.warning(f"add_edge failed ({source_id})-[{edge_type}]->({target_id}): {e}")
            return False

    async def add_topic_tag(
        self,
        memory_node_id: str,
        topic_name: str,
        isolation_key: str,
        embed_service=None,
        similarity_threshold: float = 0.85,
    ) -> str:
        """记忆节点关联主题 (Memory→Topic)。

        传入 embed_service 时做**语义归一化**：先在该 isolation_key 下找语义最近的
        已有 Topic，余弦 ≥ similarity_threshold 就复用它，否则新建。
        不归一化的话 tag 图会碎成一堆同义孤岛（work / job / career 各自成 Topic）。
        """
        if not self._available:
            return ""
        import uuid as _uuid
        topic_id = f"topic_{_uuid.uuid5(_uuid.NAMESPACE_URL, f'{isolation_key}:{topic_name}').hex[:12]}"
        now = datetime.now()
        embedding: Optional[List[float]] = None

        try:
            # Kuzu 0.4.x 不支持 MERGE ... ON CREATE SET，改用 exists check + CREATE
            topic_rows = await self._run(
                "MATCH (t:Topic {topic_id: $tid}) RETURN t.topic_id;",
                {"tid": topic_id}
            )
            topic_exists = bool(topic_rows)

            # 精确同名不存在时，才尝试语义归一化到已有 Topic
            if not topic_exists and embed_service is not None:
                try:
                    embedding = await embed_text_for_graph(embed_service, topic_name)
                    matched = self._match_existing_topic(
                        await self.get_all_topics(isolation_key),
                        embedding,
                        similarity_threshold,
                    )
                    if matched:
                        topic_id, topic_exists = matched, True
                        logger.debug(
                            f"[graph] topic '{topic_name}' normalized to existing {topic_id}"
                        )
                except Exception as e:
                    logger.debug(f"topic normalization skipped: {e}")

            if not topic_exists:
                await self._run(
                    """
                    CREATE (t:Topic {topic_id: $tid, isolation_key: $ik, name: $name,
                                     embedding: $emb, created_at: $now});
                    """,
                    {"tid": topic_id, "ik": isolation_key, "name": topic_name,
                     "emb": [float(x) for x in embedding] if embedding else None,
                     "now": now},
                )
            await self._run(
                """
                MATCH (m:Memory {node_id: $mid}), (t:Topic {topic_id: $tid})
                CREATE (m)-[:TAGGED_WITH {created_at: $now}]->(t);
                """,
                {"mid": memory_node_id, "tid": topic_id, "now": now},
            )
        except Exception as e:
            logger.debug(f"add_topic_tag note: {e}")

        return topic_id

    async def add_topic_tags_batch(
        self,
        assignments: List[Tuple[str, str, str]],
        *,
        embed_service=None,
        similarity_threshold: float = 0.85,
    ) -> List[Tuple[str, str]]:
        """Project many tags with one topic snapshot and one executor job.

        Kuzu must stay single-threaded, so throughput comes from reducing queue
        entries and repeated full-topic reads, not increasing worker count.
        """
        if not self._available or not assignments:
            return []
        import uuid as _uuid

        normalized = []
        seen = set()
        for memory_node_id, topic_name, isolation_key in assignments:
            key = (str(memory_node_id), str(topic_name).strip(), str(isolation_key))
            if not key[0] or not key[1] or not key[2] or key in seen:
                continue
            seen.add(key)
            normalized.append(key)
        if not normalized:
            return []

        scopes = sorted({item[2] for item in normalized})
        topics_by_scope = {
            scope: list(await self.get_all_topics(scope) or [])
            for scope in scopes
        }
        existing_ids = {
            str(topic.get("topic_id"))
            for topics in topics_by_scope.values()
            for topic in topics
            if topic.get("topic_id")
        }

        missing_tag_texts = list(dict.fromkeys(
            topic_name
            for _memory_node_id, topic_name, isolation_key in normalized
            if f"topic_{_uuid.uuid5(_uuid.NAMESPACE_URL, f'{isolation_key}:{topic_name}').hex[:12]}"
            not in existing_ids
        ))
        embeddings: Dict[str, Optional[List[float]]] = {
            tag: None for tag in missing_tag_texts
        }
        if embed_service is not None and missing_tag_texts:
            try:
                embed_batch = getattr(embed_service, "embed_batch", None)
                if callable(embed_batch):
                    vectors = await embed_batch(missing_tag_texts)
                else:
                    vectors = [
                        await embed_text_for_graph(embed_service, tag)
                        for tag in missing_tag_texts
                    ]
                for tag, vector in zip(missing_tag_texts, vectors or []):
                    if vector:
                        embeddings[tag] = list(vector)
            except Exception as exc:
                logger.debug("batch topic embedding skipped: %s", exc)

        resolved: List[Tuple[str, str]] = []
        new_topics: Dict[str, Tuple[str, str, Optional[List[float]]]] = {}
        for memory_node_id, topic_name, isolation_key in normalized:
            topic_id = f"topic_{_uuid.uuid5(_uuid.NAMESPACE_URL, f'{isolation_key}:{topic_name}').hex[:12]}"
            embedding = embeddings.get(topic_name)
            if topic_id not in existing_ids and embedding:
                matched = self._match_existing_topic(
                    topics_by_scope.get(isolation_key, []),
                    embedding,
                    similarity_threshold,
                )
                if matched:
                    topic_id = matched
            if topic_id not in existing_ids:
                existing_ids.add(topic_id)
                new_topics[topic_id] = (isolation_key, topic_name, embedding)
                topics_by_scope.setdefault(isolation_key, []).append({
                    "topic_id": topic_id,
                    "name": topic_name,
                    "embedding": embedding,
                })
            resolved.append((memory_node_id, topic_id))

        def _apply_batch():
            now = datetime.now()
            output: List[Tuple[str, str]] = []
            for topic_id, (isolation_key, topic_name, embedding) in new_topics.items():
                self._execute_materialized(
                    """
                    CREATE (t:Topic {topic_id: $tid, isolation_key: $ik,
                                     name: $name, embedding: $emb,
                                     created_at: $now});
                    """,
                    {
                        "tid": topic_id, "ik": isolation_key,
                        "name": topic_name,
                        "emb": [float(x) for x in embedding] if embedding else None,
                        "now": now,
                    },
                )
            for memory_node_id, topic_id in resolved:
                self._execute_materialized(
                    """
                    MATCH (m:Memory {node_id: $mid}), (t:Topic {topic_id: $tid})
                    MERGE (m)-[:TAGGED_WITH]->(t);
                    """,
                    {"mid": memory_node_id, "tid": topic_id},
                )
                output.append((memory_node_id, topic_id))
            return output

        try:
            return await _run_in_kuzu_pool(_apply_batch)
        except Exception as exc:
            logger.warning("add_topic_tags_batch failed: %s", exc)
            return []

    async def clear_hierarchy_memberships(self, memory_node_id: str) -> int:
        if not self._available or not memory_node_id:
            return 0
        try:
            rows = await self._run(
                "MATCH (m:Memory {node_id: $mid})-[r:MEMBER_OF]->(:HierarchyTopic) "
                "RETURN count(r);",
                {"mid": memory_node_id},
            )
            count = int(rows[0][0]) if rows else 0
            if count:
                await self._run(
                    "MATCH (m:Memory {node_id: $mid})-[r:MEMBER_OF]->(:HierarchyTopic) "
                    "DELETE r;",
                    {"mid": memory_node_id},
                )
            return count
        except Exception as e:
            logger.debug(f"clear_hierarchy_memberships note: {e}")
            return 0

    async def prune_hierarchy_topics(self, isolation_key: str) -> int:
        if not self._available or not isolation_key:
            return 0
        total = 0
        try:
            while True:
                rows = await self._run(
                    """
                    MATCH (t:HierarchyTopic {isolation_key: $ik})
                    WHERE NOT EXISTS { MATCH (:Memory)-[:MEMBER_OF]->(t) }
                      AND NOT EXISTS { MATCH (:HierarchyTopic)-[:SUBTOPIC_OF]->(t) }
                    RETURN count(t);
                    """,
                    {"ik": isolation_key},
                )
                count = int(rows[0][0]) if rows else 0
                if not count:
                    break
                await self._run(
                    """
                    MATCH (t:HierarchyTopic {isolation_key: $ik})
                    WHERE NOT EXISTS { MATCH (:Memory)-[:MEMBER_OF]->(t) }
                      AND NOT EXISTS { MATCH (:HierarchyTopic)-[:SUBTOPIC_OF]->(t) }
                    DETACH DELETE t;
                    """,
                    {"ik": isolation_key},
                )
                total += count
        except Exception as e:
            logger.debug(f"prune_hierarchy_topics note: {e}")
        return total

    async def add_topic_path(
        self, memory_node_id: str, topic_path: List[str], isolation_key: str,
        subject: str, *, node_types: Optional[List[str]] = None,
        role: str = "subject", embed_service=None,
    ) -> List[str]:
        """Create a typed subject-scoped hierarchy and attach the L2 to its leaf."""
        if not self._available or not memory_node_id or not subject:
            return []
        import json as _json
        from ..structure import (
            hierarchy_path_payload,
            hierarchy_topic_id,
            normalize_hierarchy_node_types,
            normalize_topic_paths,
        )

        paths = normalize_topic_paths(topic_path)
        if not paths:
            return []
        path = paths[0]
        typed_identity = node_types is not None
        types = normalize_hierarchy_node_types(node_types, len(path))
        now = datetime.now()
        ids: List[str] = []
        try:
            parent_id = ""
            for depth, name in enumerate(path, start=1):
                prefix = path[:depth]
                prefix_types = types[:depth]
                topic_id = hierarchy_topic_id(
                    isolation_key, subject, prefix,
                    prefix_types if typed_identity else None,
                )
                if not topic_id:
                    return []
                ids.append(topic_id)
                rows = await self._run(
                    "MATCH (t:HierarchyTopic {hierarchy_topic_id: $tid}) "
                    "RETURN t.hierarchy_topic_id;",
                    {"tid": topic_id},
                )
                if not rows:
                    label = (
                        f"{subject}: "
                        + " > ".join(
                            f"{kind}:{segment}"
                            for segment, kind in zip(prefix, prefix_types)
                        )
                    )
                    embedding = await embed_text_for_graph(embed_service, label)
                    await self._run(
                        """
                        CREATE (t:HierarchyTopic {
                            hierarchy_topic_id: $tid, isolation_key: $ik,
                            subject: $subject, name: $name, path_json: $path,
                            depth: $depth, embedding: $emb, created_at: $now
                        });
                        """,
                        {
                            "tid": topic_id, "ik": isolation_key,
                            "subject": subject, "name": name,
                            "path": hierarchy_path_payload(prefix, prefix_types),
                            "depth": depth,
                            "emb": [float(x) for x in embedding] if embedding else None,
                            "now": now,
                        },
                    )
                if parent_id:
                    rows = await self._run(
                        "MATCH (c:HierarchyTopic {hierarchy_topic_id: $child})"
                        "-[r:SUBTOPIC_OF]->(p:HierarchyTopic {hierarchy_topic_id: $parent}) "
                        "RETURN count(r);",
                        {"child": topic_id, "parent": parent_id},
                    )
                    if not rows or int(rows[0][0]) == 0:
                        await self._run(
                            "MATCH (c:HierarchyTopic {hierarchy_topic_id: $child}), "
                            "(p:HierarchyTopic {hierarchy_topic_id: $parent}) "
                            "CREATE (c)-[:SUBTOPIC_OF {created_at: $now}]->(p);",
                            {"child": topic_id, "parent": parent_id, "now": now},
                        )
                parent_id = topic_id

            leaf_id = ids[-1]
            rows = await self._run(
                "MATCH (m:Memory {node_id: $mid})-[r:MEMBER_OF]->"
                "(t:HierarchyTopic {hierarchy_topic_id: $tid}) RETURN count(r);",
                {"mid": memory_node_id, "tid": leaf_id},
            )
            if not rows or int(rows[0][0]) == 0:
                await self._run(
                    "MATCH (m:Memory {node_id: $mid}), "
                    "(t:HierarchyTopic {hierarchy_topic_id: $tid}) "
                    "CREATE (m)-[:MEMBER_OF {created_at: $now}]->(t);",
                    {"mid": memory_node_id, "tid": leaf_id, "now": now},
                )
            return ids
        except Exception as e:
            logger.warning(f"add_topic_path failed for {memory_node_id}/{path}: {e}")
            return []

    async def get_hierarchy_topics(
        self, isolation_key: str, *, subject: str = "",
    ) -> List[Dict[str, Any]]:
        if not self._available:
            return []
        import json as _json
        condition = "t.isolation_key = $ik"
        params: Dict[str, Any] = {"ik": isolation_key}
        if subject:
            condition += " AND lower(t.subject) = lower($subject)"
            params["subject"] = subject
        rows = await self._run(
            f"""
            MATCH (t:HierarchyTopic) WHERE {condition}
            OPTIONAL MATCH (m:Memory)-[:MEMBER_OF]->(t)
            WHERE m.status = 'active'
            RETURN t.hierarchy_topic_id, t.name, t.subject, t.path_json,
                   t.depth, t.embedding, count(DISTINCT m);
            """,
            params,
        )
        result = []
        for row in rows:
            parsed_path = parse_hierarchy_path_payload(row[3])
            result.append({
                "topic_id": row[0], "name": row[1], "subject": row[2],
                "path": parsed_path["path"],
                "node_types": parsed_path["node_types"],
                "node_type": (
                    parsed_path["node_types"][-1]
                    if parsed_path["node_types"] else "facet"
                ),
                "depth": int(row[4] or 0), "embedding": row[5],
                "direct_member_count": int(row[6] or 0),
            })
        edge_rows = await self._run(
            """
            MATCH (c:HierarchyTopic)-[:SUBTOPIC_OF]->(p:HierarchyTopic)
            WHERE c.isolation_key = $ik AND p.isolation_key = $ik
            RETURN c.hierarchy_topic_id, p.hierarchy_topic_id;
            """,
            {"ik": isolation_key},
        )
        parent_by_child = {row[0]: row[1] for row in edge_rows}
        child_counts: Dict[str, int] = {}
        for _child, parent in edge_rows:
            child_counts[parent] = child_counts.get(parent, 0) + 1
        for item in result:
            item["parent_id"] = parent_by_child.get(item["topic_id"], "")
            item["child_count"] = child_counts.get(item["topic_id"], 0)
        return result

    async def get_hierarchy_topic_memories(
        self, hierarchy_topic_id: str, *, limit: int = 200,
    ) -> List[Dict[str, Any]]:
        if not self._available:
            return []
        rows = await self._run(
            """
            MATCH (m:Memory)-[:MEMBER_OF]->
                  (t:HierarchyTopic {hierarchy_topic_id: $tid})
            WHERE m.status = 'active'
            RETURN m.node_id, m.content LIMIT $limit;
            """,
            {"tid": hierarchy_topic_id, "limit": int(limit)},
        )
        return [{"node_id": row[0], "content": row[1]} for row in rows]

    async def get_hierarchy_memberships(
        self, isolation_key: str,
    ) -> List[Dict[str, str]]:
        if not self._available:
            return []
        rows = await self._run(
            """
            MATCH (m:Memory)-[:MEMBER_OF]->(t:HierarchyTopic {isolation_key: $ik})
            WHERE m.status = 'active'
            RETURN m.node_id, t.hierarchy_topic_id;
            """,
            {"ik": isolation_key},
        )
        return [
            {"node_id": row[0], "topic_id": row[1]} for row in rows
        ]

    async def add_entity_mention(
        self,
        memory_node_id: str,
        name: str,
        label: str,
        isolation_key: str,
    ) -> str:
        """Memory -[:MENTIONS]-> Entity（精确名归一：casefold）。"""
        if not self._available or not memory_node_id or not name:
            return ""
        from .graph_store_base import make_entity_id, normalize_entity_name
        name_disp = str(name).strip()
        name_norm = normalize_entity_name(name_disp)
        lab = (label or "").strip().upper()
        if not name_norm or not lab:
            return ""
        entity_id = make_entity_id(isolation_key, lab, name_disp)
        now = datetime.now()
        try:
            rows = await self._run(
                "MATCH (e:Entity {entity_id: $eid}) RETURN e.entity_id;",
                {"eid": entity_id},
            )
            if not rows:
                await self._run(
                    """
                    CREATE (e:Entity {
                        entity_id: $eid, isolation_key: $ik, name: $name,
                        name_norm: $nn, label: $lab, created_at: $now
                    });
                    """,
                    {
                        "eid": entity_id, "ik": isolation_key, "name": name_disp,
                        "nn": name_norm, "lab": lab, "now": now,
                    },
                )
            # 避免重复边：先查再 CREATE
            existing = await self._run(
                """
                MATCH (m:Memory {node_id: $mid})-[r:MENTIONS]->(e:Entity {entity_id: $eid})
                RETURN r;
                """,
                {"mid": memory_node_id, "eid": entity_id},
            )
            if not existing:
                await self._run(
                    """
                    MATCH (m:Memory {node_id: $mid}), (e:Entity {entity_id: $eid})
                    CREATE (m)-[:MENTIONS {created_at: $now}]->(e);
                    """,
                    {"mid": memory_node_id, "eid": entity_id, "now": now},
                )
            return entity_id
        except Exception as e:
            logger.debug(f"add_entity_mention note: {e}")
            return ""

    async def clear_entity_mentions(self, memory_node_id: str) -> int:
        if not self._available or not memory_node_id:
            return 0
        try:
            rows = await self._run(
                """
                MATCH (m:Memory {node_id: $mid})-[r:MENTIONS]->(:Entity)
                RETURN count(r);
                """,
                {"mid": memory_node_id},
            )
            n = int(rows[0][0]) if rows else 0
            if n:
                await self._run(
                    """
                    MATCH (m:Memory {node_id: $mid})-[r:MENTIONS]->(:Entity)
                    DELETE r;
                    """,
                    {"mid": memory_node_id},
                )
            return n
        except Exception as e:
            logger.debug(f"clear_entity_mentions note: {e}")
            return 0

    async def find_memories_by_entities(
        self,
        isolation_key: str,
        entities: Sequence[Tuple[str, str]],
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        if not self._available or not isolation_key or not entities:
            return []
        from .graph_store_base import normalize_entity_name
        norms = sorted({
            normalize_entity_name(n) for n, _ in entities if n and str(n).strip()
        })
        if not norms:
            return []
        try:
            # Kuzu 对 list IN 支持因版本而异；逐 norm 查再合并
            seen: Dict[str, Dict[str, Any]] = {}
            for nn in norms:
                rows = await self._run(
                    """
                    MATCH (m:Memory)-[:MENTIONS]->(e:Entity)
                    WHERE e.isolation_key = $ik AND e.name_norm = $nn
                          AND m.status = 'active'
                    RETURN m.node_id, e.name, e.label
                    LIMIT $lim;
                    """,
                    {"ik": isolation_key, "nn": nn, "lim": int(limit)},
                )
                for r in rows or []:
                    nid = r[0] if isinstance(r, (list, tuple)) else r.get("m.node_id") or r.get("node_id")
                    ename = r[1] if isinstance(r, (list, tuple)) else r.get("e.name") or r.get("name")
                    elab = r[2] if isinstance(r, (list, tuple)) else r.get("e.label") or r.get("label")
                    if not nid or nid in seen:
                        continue
                    seen[nid] = {
                        "node_id": nid,
                        "entity_name": ename,
                        "entity_label": elab,
                        "match_score": 1.0,
                    }
                    if len(seen) >= limit:
                        break
                if len(seen) >= limit:
                    break
            return list(seen.values())
        except Exception as e:
            logger.debug(f"find_memories_by_entities note: {e}")
            return []

    @staticmethod
    def _match_existing_topic(
        topics: List[Dict[str, Any]],
        embedding: List[float],
        threshold: float,
    ) -> Optional[str]:
        """在已有 Topic 里找余弦 ≥ threshold 的最近者，返回 topic_id；没有则 None。"""
        best_id, best_sim = None, threshold
        norm_q = sum(x * x for x in embedding) ** 0.5
        if norm_q == 0:
            return None
        for t in topics:
            emb = t.get("embedding")
            if not emb:
                continue
            norm_t = sum(x * x for x in emb) ** 0.5
            if norm_t == 0:
                continue
            sim = sum(a * b for a, b in zip(embedding, emb)) / (norm_q * norm_t)
            if sim >= best_sim:
                best_id, best_sim = t.get("topic_id"), sim
        return best_id

    async def get_all_topics(self, isolation_key: str) -> List[Dict[str, Any]]:
        """列出该 isolation_key 下所有 Topic（含 embedding、度数）。

        度数用于画像聚合：Topic 挂的记忆越多、越新，越代表"用户是什么样的人"。
        """
        if not self._available:
            return []
        try:
            rows = await self._run(
                """
                MATCH (t:Topic {isolation_key: $ik})
                OPTIONAL MATCH (m:Memory)-[:TAGGED_WITH]->(t)
                RETURN t.topic_id, t.name, t.embedding, count(m) AS degree;
                """,
                {"ik": isolation_key},
            )
            return [
                {
                    "topic_id": r[0],
                    "name": r[1],
                    "embedding": list(r[2]) if r[2] else None,
                    "degree": int(r[3]) if r[3] is not None else 0,
                }
                for r in (rows or [])
            ]
        except Exception as e:
            logger.debug(f"get_all_topics: {e}")
            return []

    # ================================================================
    # V3: VdbRef (shadow node) + DERIVED_FROM edge
    # ================================================================

    async def ensure_vdbref(self, node_id: str, layer: str) -> None:
        """确保 VdbRef 影子节点存在"""
        if not self._available:
            return
        try:
            rows = await self._run(
                "MATCH (v:VdbRef {node_id: $nid}) RETURN v.node_id;",
                {"nid": node_id}
            )
            if rows:
                return  # 已存在
            await self._run(
                "CREATE (v:VdbRef {node_id: $nid, layer: $layer});",
                {"nid": node_id, "layer": layer},
            )
        except Exception as e:
            logger.debug(f"ensure_vdbref note: {e}")

    async def add_derived_from(self, memory_node_id: str, vdbref_node_id: str) -> bool:
        """添加 DERIVED_FROM 边: Memory → VdbRef"""
        if not self._available:
            return False
        try:
            now = datetime.now()
            await self._run(
                """
                MATCH (m:Memory {node_id: $mid}), (v:VdbRef {node_id: $vid})
                MERGE (m)-[r:DERIVED_FROM]->(v)
                ON CREATE SET r.created_at = $now;
                """,
                {"mid": memory_node_id, "vid": vdbref_node_id, "now": now},
            )
            return True
        except Exception as e:
            logger.warning(f"add_derived_from failed ({memory_node_id})->({vdbref_node_id}): {e}")
            return False

    async def find_referencing_memories(
        self,
        vdb_node_ids: List[str],
        limit: int = 50,
        include_sources: Optional[List[str]] = None,
        exclude_sources: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """反向查找: VDB node_id → 引用它的 Graph Memory 节点"""
        if not self._available or not vdb_node_ids:
            return []
        results = []
        source_conditions = []
        if include_sources:
            source_conditions.append("list_contains($include_sources, m.source)")
        if exclude_sources:
            source_conditions.append("NOT list_contains($exclude_sources, m.source)")
        source_filter = (
            " AND " + " AND ".join(source_conditions) if source_conditions else ""
        )
        for vdb_id in vdb_node_ids:
            try:
                rows = await self._run(
                    f"""
                    MATCH (v:VdbRef {{node_id: $vid}})<-[:DERIVED_FROM]-(m:Memory)
                    WHERE m.status = 'active' {source_filter}
                      AND NOT starts_with(m.node_id, 'catalog_')
                    RETURN m.node_id, m.content, m.layer, m.confidence,
                           m.custom_json, m.source
                    LIMIT {int(limit)};
                    """,
                    {
                        "vid": vdb_id,
                        **(
                            {"include_sources": list(include_sources)}
                            if include_sources else {}
                        ),
                        **(
                            {"exclude_sources": list(exclude_sources)}
                            if exclude_sources else {}
                        ),
                    },
                )
                for row in (rows or []):
                    results.append({
                        "node_id": row[0],
                        "content": row[1],
                        "layer": row[2],
                        "confidence": row[3],
                        "custom_json": row[4],
                        "evidence_vdb_id": vdb_id,
                        "retrieval_source": "reverse_evidence",
                        "memory_source": row[5] or "default",
                    })
            except Exception as e:
                logger.debug(f"find_referencing_memories for {vdb_id}: {e}")
        return results[:limit]

    async def get_evidence_vdbrefs(self, memory_node_id: str) -> List[Dict[str, Any]]:
        """获取一个 Graph Memory 的全部 DERIVED_FROM evidence VdbRef"""
        if not self._available:
            return []
        try:
            rows = await self._run(
                """
                MATCH (m:Memory {node_id: $mid})-[:DERIVED_FROM]->(v:VdbRef)
                RETURN v.node_id, v.layer;
                """,
                {"mid": memory_node_id},
            )
            refs = []
            for row in (rows or []):
                refs.append({"node_id": row[0], "layer": row[1]})
            return refs
        except Exception as e:
            logger.debug(f"get_evidence_vdbrefs for {memory_node_id}: {e}")
            return []

    # ================================================================
    # 图检索
    # ================================================================

    async def expand_from_anchors(
        self,
        anchor_ids: List[str],
        hop: int = 1,
        max_nodes: int = 50,
    ) -> List[Dict[str, Any]]:
        """从锚点节点出发，沿图边扩展 N 跳"""
        if not self._available or not anchor_ids:
            return []

        expanded = []
        seen = set(anchor_ids)

        for anchor_id in anchor_ids:
            try:
                # 正向边
                rows = await self._run(
                    f"""
                    MATCH (a:Memory {{node_id: $aid}})-[r]->(b:Memory)
                    WHERE b.status = 'active'
                    RETURN b.node_id, b.content, b.layer, b.confidence,
                           label(r) AS edge_type
                    LIMIT {int(max_nodes)};
                    """,
                    {"aid": anchor_id},
                )
                for row in (rows or []):
                    nid = row[0]
                    if nid not in seen:
                        seen.add(nid)
                        expanded.append({
                            "node_id": nid,
                            "content": row[1],
                            "layer": row[2],
                            "confidence": row[3],
                            "edge_type": row[4],
                            "from_anchor": anchor_id,
                            "source": "graph_expand",
                        })

                # 反向边
                rows2 = await self._run(
                    f"""
                    MATCH (b:Memory)-[r]->(a:Memory {{node_id: $aid}})
                    WHERE b.status = 'active'
                    RETURN b.node_id, b.content, b.layer, b.confidence,
                           label(r) AS edge_type
                    LIMIT {int(max_nodes)};
                    """,
                    {"aid": anchor_id},
                )
                for row in (rows2 or []):
                    nid = row[0]
                    if nid not in seen:
                        seen.add(nid)
                        expanded.append({
                            "node_id": nid,
                            "content": row[1],
                            "layer": row[2],
                            "confidence": row[3],
                            "edge_type": row[4],
                            "from_anchor": anchor_id,
                            "source": "graph_expand",
                        })

            except Exception as e:
                logger.debug(f"expand_from_anchors for {anchor_id}: {e}")

            if len(expanded) >= max_nodes:
                break

        return expanded[:max_nodes]

    async def get_topic_memories(
        self, topic_id: str, *, limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """列出某 Topic 下的 active 记忆。语义见基类。

        注意只能按 created_at 排 —— 图里的 Memory 表没有 memory_at 列
        （事件时间以 VDB 为准，调用方回 VDB 取）。
        """
        if not self._available or not topic_id:
            return []
        try:
            rows = await self._run(
                f"""
                MATCH (m:Memory)-[:TAGGED_WITH]->(t:Topic {{topic_id: $tid}})
                WHERE m.status = 'active'
                RETURN DISTINCT m.node_id, m.content, m.created_at
                ORDER BY m.created_at
                LIMIT {int(limit)};
                """,
                {"tid": topic_id},
            )
            return [
                {"node_id": r[0], "content": r[1]}
                for r in (rows or []) if r[0]
            ]
        except Exception as e:
            logger.debug(f"get_topic_memories({topic_id}): {e}")
            return []

    async def add_temporal_edge(
        self, prev_id: str, next_id: str, topic_id: str = "",
    ) -> bool:
        """建 Topic 内时序脊边（TEMPORAL_NEXT，有向 prev→next）。语义见基类。"""
        if not self._available or not prev_id or not next_id or prev_id == next_id:
            return False
        try:
            # 幂等：已存在同一对就不重复建
            rows = await self._run(
                """
                MATCH (a:Memory {node_id: $p})-[r:TEMPORAL_NEXT]->(b:Memory {node_id: $n})
                RETURN a.node_id LIMIT 1;
                """,
                {"p": prev_id, "n": next_id},
            )
            if rows:
                return True
            await self._run(
                """
                MATCH (a:Memory {node_id: $p}), (b:Memory {node_id: $n})
                CREATE (a)-[:TEMPORAL_NEXT {topic_id: $tid, created_at: $now}]->(b);
                """,
                {"p": prev_id, "n": next_id, "tid": topic_id or "",
                 "now": datetime.now()},
            )
            return True
        except Exception as e:
            logger.debug(f"add_temporal_edge({prev_id}->{next_id}): {e}")
            return False

    async def get_related_edges(
        self,
        node_ids: List[str],
        *,
        max_edges: int = 2000,
        relation_types: Optional[List[str]] = None,
        include_temporal: bool = True,
        include_sources: Optional[List[str]] = None,
        exclude_sources: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """取一端落在 node_ids 里的 RELATED_TO（+ 可选 TEMPORAL_NEXT）边。语义见基类。"""
        if not self._available or not node_ids:
            return []

        # add_edge 建的是双向边，所以只查正向即可覆盖两个方向；
        # 无向去重用 frozenset(pair) 做 key。
        rtype_filter = ""
        if relation_types:
            _quoted = ", ".join(f"'{t}'" for t in relation_types if t)
            if _quoted:
                rtype_filter = f"AND r.relation_type IN [{_quoted}]"
        source_conditions = []
        if include_sources:
            source_conditions.append("list_contains($include_sources, b.source)")
        if exclude_sources:
            source_conditions.append("NOT list_contains($exclude_sources, b.source)")
        source_filter = (
            " AND " + " AND ".join(source_conditions) if source_conditions else ""
        )
        source_params: Dict[str, Any] = {}
        if include_sources:
            source_params["include_sources"] = list(include_sources)
        if exclude_sources:
            source_params["exclude_sources"] = list(exclude_sources)

        out: List[Dict[str, Any]] = []
        seen_pairs: set = set()
        try:
            rows = await self._run(
                f"""
                MATCH (a:Memory)-[r:RELATED_TO]->(b:Memory)
                WHERE list_contains($ids, a.node_id) {rtype_filter} {source_filter}
                RETURN a.node_id, b.node_id, r.relation_type, r.weight,
                       b.status, b.layer
                LIMIT {int(max_edges) * 2};
                """,
                {"ids": list(node_ids), **source_params},
            )
            for row in (rows or []):
                src, tgt = row[0], row[1]
                if not src or not tgt or src == tgt:
                    continue
                # 去重 key 必须带 relation_type：add_edge 建的是双向边，需要把
                # A→B / B→A 折叠成一条；但同一对节点之间**可以有多种关系**
                # （既 co_occurs 又 causes），那是两条不同的边，不能合并。
                pair = (frozenset((src, tgt)), row[2] or "related")
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                out.append({
                    "source_id": src,
                    "target_id": tgt,
                    "relation_type": row[2] or "related",
                    "weight": float(row[3]) if row[3] is not None else 1.0,
                    "target_status": row[4],
                    "target_layer": row[5],
                    "directed": False,
                })
                if len(out) >= max_edges:
                    break
        except Exception as e:
            logger.debug(f"get_related_edges: {e}")

        # ── 时序脊：TEMPORAL_NEXT 是有向边（source=更早，target=更晚）──
        # 一端落在 node_ids 里就要取（两个方向都查），否则"X 之前发生了什么"走不通。
        if include_temporal and (
            not relation_types or "temporal_next" in relation_types
        ):
            for direction in ("out", "in"):
                if len(out) >= max_edges:
                    break
                match = (
                    "MATCH (a:Memory)-[r:TEMPORAL_NEXT]->(b:Memory) "
                        "WHERE list_contains($ids, a.node_id)"
                    if direction == "out" else
                    "MATCH (a:Memory)-[r:TEMPORAL_NEXT]->(b:Memory) "
                        "WHERE list_contains($ids, b.node_id)"
                )
                endpoint_filter = source_filter if direction == "out" else source_filter.replace(
                    "b.source", "a.source"
                )
                try:
                    rows = await self._run(
                        f"""
                        {match} {endpoint_filter}
                        RETURN a.node_id, b.node_id, b.status, b.layer, a.status, a.layer
                        LIMIT {int(max_edges)};
                        """,
                        {"ids": list(node_ids), **source_params},
                    )
                    for row in (rows or []):
                        src, tgt = row[0], row[1]
                        if not src or not tgt or src == tgt:
                            continue
                        pair = (frozenset((src, tgt)), "temporal_next")
                        if pair in seen_pairs:
                            continue
                        seen_pairs.add(pair)
                        out.append({
                            "source_id": src,
                            "target_id": tgt,
                            "relation_type": "temporal_next",
                            "weight": 1.0,
                            # target/source 状态都带上：读侧不管从哪端命中都能过滤 shadow
                            "target_status": row[2],
                            "target_layer": row[3],
                            "source_status": row[4],
                            "source_layer": row[5],
                            "directed": True,
                        })
                        if len(out) >= max_edges:
                            break
                except Exception as e:
                    logger.debug(f"get_related_edges(temporal/{direction}): {e}")

        return out

    async def expand_with_tags(
        self,
        anchor_ids: List[str],
        hop: int = 2,
        max_nodes: int = 500,
        isolation_key: str = "",
    ) -> List[Dict[str, Any]]:
        """BFS 展开：RELATED_TO + TAGGED_WITH(双向)，N 跳。"""
        if not self._available or not anchor_ids:
            return []

        seen = set(anchor_ids)
        all_expanded = []
        frontier = list(anchor_ids)

        ik_filter = f"AND m.isolation_key = '{isolation_key}'" if isolation_key else ""

        for current_hop in range(1, hop + 1):
            if not frontier or len(all_expanded) >= max_nodes:
                break

            next_frontier = []

            for aid in frontier:
                # --- RELATED_TO 邻居（双向）---
                try:
                    rows = await self._run(
                        f"""
                        MATCH (a:Memory {{node_id: $aid}})-[:RELATED_TO]-(m:Memory)
                        WHERE m.status = 'active' {ik_filter}
                        RETURN DISTINCT m.node_id, m.content, m.layer, m.confidence
                        LIMIT {int(max_nodes)};
                        """,
                        {"aid": aid},
                    )
                    for row in (rows or []):
                        nid = row[0]
                        if nid not in seen:
                            seen.add(nid)
                            all_expanded.append({
                                "node_id": nid,
                                "content": row[1],
                                "layer": row[2],
                                "confidence": row[3],
                                "hop": current_hop,
                                "source": "related_to",
                            })
                            next_frontier.append(nid)
                except Exception as e:
                    logger.debug(f"expand_with_tags RELATED_TO {aid}: {e}")

                # --- TAGGED_WITH 桥接 ---
                try:
                    rows = await self._run(
                        f"""
                        MATCH (a:Memory {{node_id: $aid}})-[:TAGGED_WITH]->(t:Topic)<-[:TAGGED_WITH]-(m:Memory)
                        WHERE m.status = 'active' AND m.node_id <> $aid {ik_filter}
                        RETURN DISTINCT m.node_id, m.content, m.layer, m.confidence, t.name
                        LIMIT {int(max_nodes)};
                        """,
                        {"aid": aid},
                    )
                    for row in (rows or []):
                        nid = row[0]
                        if nid not in seen:
                            seen.add(nid)
                            all_expanded.append({
                                "node_id": nid,
                                "content": row[1],
                                "layer": row[2],
                                "confidence": row[3],
                                "hop": current_hop,
                                "source": f"tag:{row[4]}",
                            })
                            next_frontier.append(nid)
                except Exception as e:
                    logger.debug(f"expand_with_tags TAGGED_WITH {aid}: {e}")

                if len(all_expanded) >= max_nodes:
                    break

            frontier = next_frontier

        return all_expanded[:max_nodes]

    # ================================================================
    # 向量检索 (HNSW)
    # ================================================================

    async def vector_search(
        self,
        query_embedding: List[float],
        isolation_key: str,
        layers: Optional[List[str]] = None,
        limit: int = 10,
        score_threshold: float = 0.0,
        user_id: str = "",
        include_sources: Optional[List[str]] = None,
        exclude_sources: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """V_con 向量检索 Graph Memory 节点"""
        if not self._available or not query_embedding:
            return []

        try:
            conditions = ["m.status = 'active'", "m.embedding IS NOT NULL"]
            params: Dict[str, Any] = {
                "vec": query_embedding,
                "threshold": score_threshold,
            }
            if isolation_key:
                conditions.append("m.isolation_key = $ik")
                params["ik"] = isolation_key
            elif user_id:
                conditions.append("starts_with(m.isolation_key, $ik_prefix)")
                params["ik_prefix"] = user_id + "::"
            else:
                return []
            if layers:
                conditions.append("list_contains($layers, m.layer)")
                params["layers"] = list(layers)
            if include_sources:
                conditions.append("list_contains($include_sources, m.source)")
                params["include_sources"] = list(include_sources)
            if exclude_sources:
                conditions.append("NOT list_contains($exclude_sources, m.source)")
                params["exclude_sources"] = list(exclude_sources)

            # 先在数据库内按属性缩小候选集，再计算 cosine 并排序。不能使用
            # QUERY_VECTOR_INDEX 的全局 top-k 后置过滤，否则 source 会吃掉名额。
            r = await self._run(
                f"""
                MATCH (m:Memory)
                WHERE {' AND '.join(conditions)}
                WITH m, array_cosine_similarity(m.embedding, $vec) AS score
                WHERE score >= $threshold
                RETURN m.node_id, m.content, m.layer, m.confidence,
                       m.custom_json, m.source, score
                ORDER BY score DESC
                LIMIT {int(limit)};
                """,
                params,
            )
            results = []
            for row in (r or []):
                results.append({
                    "node_id": row[0],
                    "content": row[1],
                    "layer": row[2],
                    "confidence": row[3],
                    "score": float(row[6]),
                    "custom_json": row[4],
                    "retrieval_source": "graph_vector_search",
                    "memory_source": row[5] or "default",
                })
            return results
        except Exception as e:
            logger.warning(f"vector_search failed: {e}")
            return []

    async def beh_vector_search(
        self,
        query_beh_embedding: List[float],
        isolation_key: str,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """V_beh 向量检索 L6 basic 节点 (sweeper 用)

        beh_embedding 不建 HNSW 索引（Kuzu 限制：被索引列不能 SET），
        所以用全量扫描 + 内存 cosine 计算。N < 100 时毫秒级。
        """
        if not self._available or not query_beh_embedding:
            return []

        try:
            import numpy as np

            # 全量取 L6 basic 的 beh_embedding + embedding
            r = await self._run(
                "MATCH (m:Memory) WHERE m.isolation_key = $ik "
                "AND m.layer = 'l6_schema' AND m.status = 'active' "
                "AND m.beh_embedding IS NOT NULL "
                "RETURN m.node_id, m.content, m.embedding, m.beh_embedding;",
                {"ik": isolation_key},
            )
            candidates = []
            for row in (r or []):
                candidates.append({
                    "node_id": row[0],
                    "content": row[1],
                    "embedding": row[2],
                    "beh_embedding": row[3],
                })

            if not candidates:
                return []

            # 内存 cosine 计算
            q_vec = np.array(query_beh_embedding, dtype=np.float32)
            q_norm = q_vec / (np.linalg.norm(q_vec) + 1e-10)

            scored = []
            for c in candidates:
                beh = np.array(c["beh_embedding"], dtype=np.float32)
                beh_norm = beh / (np.linalg.norm(beh) + 1e-10)
                beh_sim = float(np.dot(q_norm, beh_norm))
                scored.append((beh_sim, c))

            # 按 beh_sim 降序，取 topk
            scored.sort(key=lambda x: x[0], reverse=True)
            results = []
            for beh_sim, c in scored[:limit]:
                results.append({
                    "node_id": c["node_id"],
                    "content": c["content"],
                    "embedding": c["embedding"],
                    "beh_distance": 1.0 - beh_sim,
                })
            return results
        except Exception as e:
            logger.warning(f"beh_vector_search failed: {e}")
            return []

    async def update_embedding(
        self,
        node_id: str,
        embedding: Optional[List[float]] = None,
        beh_embedding: Optional[List[float]] = None,
    ) -> bool:
        """更新节点的 embedding / beh_embedding 向量属性

        Kuzu 限制：被 HNSW 索引的列（embedding）不能 SET。
        - beh_embedding：无索引，可直接 SET
        - embedding：有索引，需在 CREATE 时写入（不支持后续更新）
          如果确实需要更新 embedding，调用方应 delete + upsert（带新 embedding）
        """
        if not self._available:
            return False
        try:
            if beh_embedding is not None:
                await self._run(
                    "MATCH (m:Memory {node_id: $nid}) SET m.beh_embedding = $vec;",
                    {"nid": node_id, "vec": beh_embedding},
                )
            if embedding is not None:
                # Kuzu 被索引列不能 SET；应在 upsert CREATE 时传 _graph_embedding
                logger.debug(
                    f"Kuzu: skip SET indexed column 'embedding' for {node_id} "
                    f"(use upsert_memory_node with _graph_embedding on CREATE)"
                )
                if beh_embedding is None:
                    return False
            return True
        except Exception as e:
            logger.warning(f"update_embedding failed for {node_id}: {e}")
            return False

    # ================================================================
    # 跨域归纳 (Cross-Domain)
    # ================================================================

    async def add_cross_abstracts_to(self, basic_id: str, core_id: str) -> bool:
        """添加 CROSS_ABSTRACTS_TO 边: L6 basic → L6 core (单向)"""
        if not self._available:
            return False
        try:
            now = datetime.now()
            await self._run(
                """
                MATCH (a:Memory {node_id: $src}), (b:Memory {node_id: $tgt})
                CREATE (a)-[:CROSS_ABSTRACTS_TO {created_at: $now}]->(b);
                """,
                {"src": basic_id, "tgt": core_id, "now": now},
            )
            return True
        except Exception as e:
            logger.warning(f"add_cross_abstracts_to failed ({basic_id})->({core_id}): {e}")
            return False

    async def find_cores_from_basics(
        self,
        basic_ids: List[str],
        max_hops: int = 2,
    ) -> List[Dict[str, Any]]:
        """从 L6 basic 出发，沿 RELATED_TO / CROSS_ABSTRACTS_TO 找 L6 core"""
        if not self._available or not basic_ids:
            return []

        seen = set()
        results = []
        for nid in basic_ids:
            try:
                r = await self._run(
                    f"""
                    MATCH (start:Memory {{node_id: $nid}})
                          -[:CROSS_ABSTRACTS_TO*1..{int(max_hops)}]->
                          (core:Memory)
                    WHERE core.status = 'active'
                    RETURN DISTINCT core.node_id, core.content, core.confidence;
                    """,
                    {"nid": nid},
                )
                for row in (r or []):
                    cid = row[0]
                    if cid not in seen:
                        seen.add(cid)
                        results.append({
                            "node_id": cid,
                            "content": row[1],
                            "confidence": row[2],
                        })
            except Exception as e:
                logger.debug(f"find_cores_from_basics for {nid}: {e}")
        return results

    async def get_cross_abstracts_targets(self, basic_id: str) -> List[str]:
        """查询某个 L6 basic 已有的 CROSS_ABSTRACTS_TO → core node_id 列表"""
        if not self._available:
            return []
        try:
            r = await self._run(
                """
                MATCH (a:Memory {node_id: $nid})-[:CROSS_ABSTRACTS_TO]->(b:Memory)
                RETURN b.node_id;
                """,
                {"nid": basic_id},
            )
            ids = [row[0] for row in (r or [])]
            return ids
        except Exception as e:
            logger.debug(f"get_cross_abstracts_targets for {basic_id}: {e}")
            return []

    # ================================================================
    # 统计
    # ================================================================

    async def get_stats(self) -> Dict[str, Any]:
        """获取图统计"""
        if not self._available:
            return {"backend": "kuzu", "available": False, "reason": "file lock not acquired"}
        try:
            mem_rows = await self._run("MATCH (m:Memory) RETURN count(m);")
            user_rows = await self._run("MATCH (u:User) RETURN count(u);")

            mc = mem_rows[0][0] if mem_rows else 0
            uc = user_rows[0][0] if user_rows else 0

            return {
                "backend": "kuzu",
                "db_path": self._db_path,
                "memory_nodes": mc,
                "user_nodes": uc,
            }
        except Exception as e:
            return {"backend": "kuzu", "error": str(e)}

    async def close(self) -> None:
        """关闭连接"""
        conn, db = self._conn, self._db
        _LIVE_STORES.discard(self)
        self._conn = None
        self._db = None
        self._available = False

        def _close():
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass

        await _run_in_kuzu_pool(_close)
        logger.info("GraphStore (Kuzu) closed")

    # ================================================================
    # 内部工具
    # ================================================================

    @staticmethod
    def _row_to_memory_node(row_data: Any) -> MemoryNode:
        """将 Kuzu 查询结果行转为 MemoryNode"""
        if isinstance(row_data, dict):
            d = row_data
        else:
            # Kuzu Node 对象有 dict-like 接口
            d = dict(row_data) if hasattr(row_data, '__iter__') else {}

        # 解析 JSON 字段
        meta_tags_raw = d.get("meta_tags", "[]")
        if isinstance(meta_tags_raw, str):
            try:
                meta_tags_raw = json.loads(meta_tags_raw)
            except (json.JSONDecodeError, TypeError):
                meta_tags_raw = []

        evidence_chain = d.get("evidence_chain", "[]")
        if isinstance(evidence_chain, str):
            try:
                evidence_chain = json.loads(evidence_chain)
            except (json.JSONDecodeError, TypeError):
                evidence_chain = []

        custom = d.get("custom_json", "{}")
        if isinstance(custom, str):
            try:
                custom = json.loads(custom)
            except (json.JSONDecodeError, TypeError):
                custom = {}

        tags = d.get("tags", "[]")
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except (json.JSONDecodeError, TypeError):
                tags = []

        return MemoryNode.from_dict({
            "node_id": d.get("node_id", ""),
            "user_id": d.get("user_id", ""),
            "agent_id": d.get("agent_id", ""),
            "source": d.get("source", "default") or "default",
            "layer": d.get("layer", "l1_raw"),
            "content": d.get("content", ""),
            "content_type": d.get("content_type", "raw"),
            "status": d.get("status", "active"),
            "version": d.get("version", 1),
            "confidence": d.get("confidence", 1.0),
            "source_type": d.get("source_type", "explicit"),
            "emotional_valence": d.get("emotional_valence", 0.0),
            "emotional_arousal": d.get("emotional_arousal", 0.0),
            "specificity_score": d.get("specificity_score", 0.0),
            "rarity_score": d.get("rarity_score", 0.0),
            "longtail_flag": d.get("longtail_flag", False),
            "meta_tags": meta_tags_raw,
            "source_session_id": d.get("source_session_id", ""),
            "source_turn_index": d.get("source_turn_index"),
            "temporal_anchor": d.get("temporal_anchor"),
            "access_count": d.get("access_count", 0),
            "created_at": d.get("created_at"),
            "valid_from": d.get("valid_from"),
            "valid_until": d.get("valid_until"),
            "last_accessed_at": d.get("last_accessed_at"),
            "previous_version_id": d.get("previous_version_id"),
            "superseded_by_id": d.get("superseded_by_id"),
            "change_reason": d.get("change_reason"),
            "evidence_chain": evidence_chain,
            "embedding": list(d.get("embedding") or []),
            "custom": custom,
            "tags": tags,
        })
