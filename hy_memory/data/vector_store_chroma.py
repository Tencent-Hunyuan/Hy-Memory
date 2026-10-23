"""
Agent Memory V2 - VectorStore (Chroma)

基于 ChromaDB 的向量存储层。

特点:
- PersistentClient 本地嵌入式（默认，零外部依赖）
- HttpClient 远程连接
- 同步 API → _run_in_vdb_pool() 包装为异步
"""

from typing import Optional, List, Dict, Any
import concurrent.futures
import functools
import logging

from ..models.memory import (
    MemoryNode, MemoryLayer, MemoryStatus,
)
from ..config import MemoryConfig
from ..utils.async_executor import run_in_executor_guarded
from .vector_store_base import VectorStoreBase, passes_created_after

# created_after 走 post-filter，先按倍数扩池再过滤，避免过滤后不够 limit
_CREATED_AFTER_POOL_MULTIPLIER = 4

logger = logging.getLogger(__name__)

# VDB 独立线程池（不与 Graph/SQLite 竞争）
_VDB_POOL_SIZE = 64
_vdb_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=_VDB_POOL_SIZE, thread_name_prefix="vdb"
)


async def _run_in_vdb_pool(func, *args, **kwargs):
    """Run a synchronous Chroma call in the dedicated VDB executor."""
    call = functools.partial(func, *args, **kwargs) if args or kwargs else func
    return await run_in_executor_guarded(_vdb_executor, call)


class ChromaVectorStore(VectorStoreBase):
    """
    Chroma 向量存储实现

    使用 chromadb 的 PersistentClient (本地) 或 HttpClient (远程)。
    Chroma 原生是同步 API，通过 _run_in_vdb_pool() 包装。
    """

    def __init__(self, config: MemoryConfig):
        super().__init__(config)
        self._client = None
        self._collection = None
        self._entity_collection = None  # lazy: {collection}_entities

    async def initialize(self) -> None:
        """初始化 Chroma 客户端，确保集合存在"""
        try:
            import chromadb
        except ImportError:
            raise ImportError(
                "chromadb is required for Chroma backend. "
                "Install with: pip install chromadb"
            )

        vs_config = self.config.vector_store

        def _init():
            import chromadb

            # 优先使用远程连接
            if vs_config.host:
                client = chromadb.HttpClient(
                    host=vs_config.host,
                    port=vs_config.port or 8000,
                )
            else:
                client = chromadb.PersistentClient(
                    path=vs_config.persist_directory,
                )

            # 获取或创建集合
            # Chroma 内部管理向量维度，首次 add 时自动推断
            collection = client.get_or_create_collection(
                name=self._collection_name,
                metadata={"hnsw:space": "cosine"},
            )
            return client, collection

        self._client, self._collection = await _run_in_vdb_pool(_init)
        logger.debug(f"VectorStore initialized (Chroma), collection={self._collection_name}")

    # ================================================================
    # 写入
    # ================================================================

    async def upsert(self, node: MemoryNode) -> str:
        """写入或更新一个 MemoryNode"""
        if node.embedding is None:
            raise ValueError(f"Node {node.node_id} has no embedding, cannot upsert to vector store")

        payload = self._node_to_payload(node)
        point_id = self._node_id_to_point_id(node.node_id)
        metadata = self._payload_to_chroma_metadata(payload)

        def _upsert():
            self._collection.upsert(
                ids=[point_id],
                embeddings=[node.embedding],
                metadatas=[metadata],
                documents=[node.content],
            )

        await _run_in_vdb_pool(_upsert)
        logger.debug(
            f"[vector-store] upsert: id={node.node_id} "
            f"layer={node.layer.value if hasattr(node.layer, 'value') else node.layer} "
            f"content={node.content}"
        )
        return node.node_id

    async def upsert_batch(self, nodes: List[MemoryNode]) -> List[str]:
        """批量写入"""
        valid_nodes = [n for n in nodes if n.embedding is not None]
        if not valid_nodes:
            return []

        for n in nodes:
            if n.embedding is None:
                logger.warning(f"Skipping node {n.node_id}: no embedding")

        ids = []
        embeddings = []
        metadatas = []
        documents = []
        for node in valid_nodes:
            payload = self._node_to_payload(node)
            ids.append(self._node_id_to_point_id(node.node_id))
            embeddings.append(node.embedding)
            metadatas.append(self._payload_to_chroma_metadata(payload))
            documents.append(node.content)

        def _upsert_batch():
            self._collection.upsert(
                ids=ids,
                embeddings=embeddings,
                metadatas=metadatas,
                documents=documents,
            )

        await _run_in_vdb_pool(_upsert_batch)
        return [n.node_id for n in valid_nodes]

    async def update_embedding(self, node_id: str, embedding: List[float]) -> bool:
        """仅更新向量（payload 不变）"""
        point_id = self._node_id_to_point_id(node_id)
        try:
            def _update():
                # Chroma 的 update 需要至少 embeddings
                self._collection.update(
                    ids=[point_id],
                    embeddings=[embedding],
                )
            await _run_in_vdb_pool(_update)
            return True
        except Exception as e:
            logger.warning(f"Failed to update embedding for {node_id}: {e}")
            return False

    async def update_payload(self, node_id: str, updates: Dict[str, Any]) -> bool:
        """更新 payload 字段；若 updates 含 'embedding'，同时更新向量与 documents。"""
        if not updates:
            return True
        point_id = self._node_id_to_point_id(node_id)
        updates = dict(updates)
        new_embedding = updates.pop("embedding", None)  # 取出向量单独处理
        # Chroma metadata 只支持 str/int/float/bool，序列化复杂类型
        import json as _json_up
        chroma_updates = {}
        for k, v in updates.items():
            if v is None:
                chroma_updates[k] = ""
            elif isinstance(v, (list, dict)):
                chroma_updates[k] = _json_up.dumps(v, ensure_ascii=False, default=str)
            elif isinstance(v, bool):
                chroma_updates[k] = v
            elif isinstance(v, (int, float, str)):
                chroma_updates[k] = v
            else:
                chroma_updates[k] = str(v)
        try:
            def _update():
                kwargs: Dict[str, Any] = {"ids": [point_id]}
                if chroma_updates:
                    kwargs["metadatas"] = [chroma_updates]
                if new_embedding is not None:
                    kwargs["embeddings"] = [new_embedding]
                if "content" in updates:  # content 变更同步全文 documents
                    kwargs["documents"] = [updates["content"]]
                self._collection.update(**kwargs)
            await _run_in_vdb_pool(_update)
            logger.debug(
                f"[vector-store] update_payload: id={node_id} updates={list(updates.keys())} "
                f"embedding={'yes' if new_embedding is not None else 'no'}"
            )
            return True
        except Exception as e:
            logger.warning(f"Failed to update payload for {node_id}: {e}")
            return False

    # ================================================================
    # 检索
    # ================================================================

    async def search(
        self,
        query_embedding: List[float],
        isolation_key: str = "",
        isolation_keys: Optional[List[str]] = None,
        user_id: Optional[str] = None,
        user_ids: Optional[List[str]] = None,
        agent_ids: Optional[List[str]] = None,
        include_sources: Optional[List[str]] = None,
        exclude_sources: Optional[List[str]] = None,
        layers: Optional[List[MemoryLayer]] = None,
        limit: int = 10,
        score_threshold: float = 0.0,
        status_filter: Optional[List[MemoryStatus]] = None,
        only_latest: bool = True,
        tags_match_any: Optional[List[str]] = None,  # 兼容底层过滤能力；chroma 暂未实现
        created_after: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """语义检索"""
        # 构建 Chroma where 过滤条件
        where_conditions = []

        # 隔离过滤: 精确 keys > 单 key > user_ids/user_id + agent 级
        if isolation_keys:
            if len(isolation_keys) == 1:
                where_conditions.append({"isolation_key": {"$eq": isolation_keys[0]}})
            else:
                where_conditions.append({"isolation_key": {"$in": isolation_keys}})
        elif isolation_key:
            where_conditions.append({"isolation_key": {"$eq": isolation_key}})
        else:
            # user_ids 优先于 user_id
            effective_uids = user_ids if user_ids else ([user_id] if user_id else [])
            if effective_uids:
                if len(effective_uids) == 1:
                    where_conditions.append({"user_id": {"$eq": effective_uids[0]}})
                else:
                    where_conditions.append({"user_id": {"$in": effective_uids}})
                if agent_ids:
                    if len(agent_ids) == 1:
                        where_conditions.append({"agent_id": {"$eq": agent_ids[0]}})
                    else:
                        where_conditions.append({"agent_id": {"$in": agent_ids}})

        if layers:
            layer_values = [l.value for l in layers]
            if len(layer_values) == 1:
                where_conditions.append({"layer": {"$eq": layer_values[0]}})
            else:
                where_conditions.append({"layer": {"$in": layer_values}})

        if include_sources:
            where_conditions.append({"source": {"$in": list(include_sources)}})
        if exclude_sources:
            where_conditions.append({"source": {"$nin": list(exclude_sources)}})

        if status_filter:
            status_values = [s.value for s in status_filter]
            if len(status_values) == 1:
                where_conditions.append({"status": {"$eq": status_values[0]}})
            else:
                where_conditions.append({"status": {"$in": status_values}})
        else:
            where_conditions.append({"status": {"$eq": MemoryStatus.ACTIVE.value}})

        # 只搜索演化链末端节点
        if only_latest:
            where_conditions.append({"is_latest": {"$eq": True}})

        if len(where_conditions) == 1:
            where = where_conditions[0]
        else:
            where = {"$and": where_conditions}

        # created_after 走 post-filter：语义是「事件时间 memory_at 优先，为空才回退
        # gmt_created」，而 Chroma 的 where DSL 表达不了 null 判断（本后端把空值写成
        # `""`，`$gte` 对空串无意义）。改为扩池召回后在 Python 侧精确过滤。
        _n_results = limit * _CREATED_AFTER_POOL_MULTIPLIER if created_after else limit

        def _query():
            return self._collection.query(
                query_embeddings=[query_embedding],
                where=where,
                n_results=_n_results,
                include=["metadatas", "documents", "distances"],
            )

        results = await _run_in_vdb_pool(_query)

        output = []
        if results and results["ids"] and results["ids"][0]:
            ids = results["ids"][0]
            metadatas = results["metadatas"][0] if results.get("metadatas") else [{}] * len(ids)
            distances = results["distances"][0] if results.get("distances") else [0.0] * len(ids)

            for i, (pid, meta, dist) in enumerate(zip(ids, metadatas, distances)):
                # Chroma cosine distance → similarity score: score = 1 - distance
                score = 1.0 - dist
                if score_threshold is not None and score < score_threshold:
                    continue

                payload = self._chroma_metadata_to_payload(meta)
                if not passes_created_after(payload, created_after):
                    continue
                node = self._payload_to_node(payload)
                output.append({
                    "node_id": node.node_id,
                    "score": score,
                    "node": node,
                })
                if len(output) >= limit:
                    break

        logger.debug(
            f"[vector-store] search: user_id={user_id} agent_ids={agent_ids} "
            f"limit={limit} found={len(output)}"
        )

        return output

    async def get_by_id(self, node_id: str) -> Optional[MemoryNode]:
        """按 ID 获取"""
        point_id = self._node_id_to_point_id(node_id)
        try:
            # Keep the accepted read's collection alive across the executor hop.
            # close() clears self._collection while a queued worker may still run.
            collection = self._collection

            def _get():
                return collection.get(
                    ids=[point_id],
                    include=["metadatas", "embeddings", "documents"],
                )
            results = await _run_in_vdb_pool(_get)

            if results and results["ids"]:
                meta = results["metadatas"][0] if results.get("metadatas") else {}
                payload = self._chroma_metadata_to_payload(meta)
                node = self._payload_to_node(payload)
                raw_emb = results.get("embeddings")
                if raw_emb is not None and len(raw_emb) > 0:
                    emb = raw_emb[0]
                    if emb is not None:
                        node.embedding = list(emb) if not isinstance(emb, list) else emb
                return node
        except Exception as e:
            logger.warning(f"Failed to get vector point {node_id}: {e}")
        return None

    # ================================================================
    # 删除
    # ================================================================

    async def delete(self, node_id: str) -> bool:
        """删除单个向量点"""
        point_id = self._node_id_to_point_id(node_id)
        try:
            def _delete():
                self._collection.delete(ids=[point_id])
            await _run_in_vdb_pool(_delete)
            return True
        except Exception as e:
            logger.warning(f"Failed to delete vector point {node_id}: {e}")
            return False

    async def delete_by_isolation_key(self, isolation_key: str) -> int:
        """删除某隔离键下的所有向量"""
        try:
            def _delete():
                # 先获取匹配的 ids，再按 ids 删除（Chroma where-delete 不返回数量）
                results = self._collection.get(
                    where={"isolation_key": {"$eq": isolation_key}},
                    include=[],
                )
                ids = results["ids"] if results and results["ids"] else []
                if ids:
                    self._collection.delete(ids=ids)
                return len(ids)
            return await _run_in_vdb_pool(_delete)
        except Exception as e:
            logger.warning(f"Failed to delete vectors for {isolation_key}: {e}")
            return 0

    async def delete_by_metadata(
        self,
        user_id: str,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> int:
        """按 metadata 字段组合删除向量"""
        try:
            def _delete():
                where_conditions = [{"user_id": {"$eq": user_id}}]
                if agent_id is not None:
                    where_conditions.append({"agent_id": {"$eq": agent_id}})
                if session_id is not None:
                    where_conditions.append({"session_id": {"$eq": session_id}})

                if len(where_conditions) == 1:
                    where = where_conditions[0]
                else:
                    where = {"$and": where_conditions}

                results = self._collection.get(where=where, include=[])
                ids = results["ids"] if results and results["ids"] else []
                if ids:
                    self._collection.delete(ids=ids)
                return len(ids)
            return await _run_in_vdb_pool(_delete)
        except Exception as e:
            logger.warning(f"Failed to delete vectors by metadata (user_id={user_id}): {e}")
            return 0

    # ================================================================
    # 枚举
    # ================================================================

    async def list_by_user(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        limit: int = 10000,
        status_filter: Optional[List] = None,
        layers: Optional[List] = None,
        user_ids: Optional[List[str]] = None,
        agent_ids: Optional[List[str]] = None,
        include_sources: Optional[List[str]] = None,
        exclude_sources: Optional[List[str]] = None,
        only_latest: bool = False,
        with_vectors: bool = True,
        allow_raw_layer: bool = False,
    ) -> List[MemoryNode]:
        """枚举用户的记忆节点，支持多 user / 多 agent

        allow_raw_layer: 默认 False，即不返回 L1_RAW（对外读路径）。
        """
        import asyncio

        effective_uids = list(user_ids) if user_ids else ([user_id] if user_id else [])
        if not effective_uids:
            logger.warning("[vector-store] list_by_user called without user_id/user_ids, refused")
            return []
        effective_aids = list(agent_ids) if agent_ids else ([agent_id] if agent_id else [])

        def _list():
            if len(effective_uids) == 1:
                where_conditions = [{"user_id": {"$eq": effective_uids[0]}}]
            else:
                where_conditions = [{"user_id": {"$in": effective_uids}}]
            if effective_aids:
                if len(effective_aids) == 1:
                    where_conditions.append({"agent_id": {"$eq": effective_aids[0]}})
                else:
                    where_conditions.append({"agent_id": {"$in": effective_aids}})
            if include_sources:
                where_conditions.append({"source": {"$in": list(include_sources)}})
            if exclude_sources:
                where_conditions.append({"source": {"$nin": list(exclude_sources)}})
            if status_filter:
                status_values = [
                    s.value if hasattr(s, "value") else str(s)
                    for s in status_filter
                ]
                if len(status_values) == 1:
                    where_conditions.append({"status": {"$eq": status_values[0]}})
                else:
                    where_conditions.append({"status": {"$in": status_values}})
            if only_latest:
                where_conditions.append({"is_latest": {"$eq": True}})
            if layers:
                layer_values = [l.value if hasattr(l, 'value') else str(l) for l in layers]
                if not allow_raw_layer:
                    layer_values = [v for v in layer_values if v != MemoryLayer.L1_RAW.value]
                if len(layer_values) == 1:
                    where_conditions.append({"layer": {"$eq": layer_values[0]}})
                elif layer_values:
                    where_conditions.append({"layer": {"$in": layer_values}})
                else:
                    # 只点名了 raw：补排除条件，结果必然为空集
                    where_conditions.append({"layer": {"$ne": MemoryLayer.L1_RAW.value}})
            elif not allow_raw_layer:
                where_conditions.append({"layer": {"$ne": MemoryLayer.L1_RAW.value}})

            if len(where_conditions) == 1:
                where = where_conditions[0]
            else:
                where = {"$and": where_conditions}

            include = ["metadatas"]
            if with_vectors:
                include.append("embeddings")
            return self._collection.get(
                where=where,
                include=include,
                limit=limit,
            )

        results = await _run_in_vdb_pool(_list)
        nodes = []
        if results and results["ids"]:
            metadatas = results.get("metadatas", [])
            embeddings = results.get("embeddings", []) if with_vectors else []
            for i, _id in enumerate(results["ids"]):
                meta = metadatas[i] if i < len(metadatas) else {}
                payload = self._chroma_metadata_to_payload(meta)
                node = self._payload_to_node(payload)
                if with_vectors and embeddings is not None and i < len(embeddings) and embeddings[i] is not None:
                    emb = embeddings[i]
                    node.embedding = list(emb) if hasattr(emb, '__iter__') else emb
                nodes.append(node)

        logger.info(
            f"[vector-store] list_by_user: user_ids={effective_uids} "
            f"agent_ids={effective_aids} found={len(nodes)}"
        )
        return nodes

    # ================================================================
    # 统计
    # ================================================================

    async def get_stats(self) -> Dict[str, Any]:
        """获取集合统计"""
        try:
            def _stats():
                return self._collection.count()
            total = await _run_in_vdb_pool(_stats)
            return {
                "collection": self._collection_name,
                "points_count": total,
                "vectors_count": total,
                "status": "ok",
            }
        except Exception as e:
            return {"error": str(e)}

    async def count(self, isolation_key: str) -> int:
        """统计某隔离键下的向量数量"""
        try:
            def _count():
                results = self._collection.get(
                    where={"isolation_key": {"$eq": isolation_key}},
                    include=[],  # 只要 ids
                )
                return len(results["ids"]) if results and results["ids"] else 0
            return await _run_in_vdb_pool(_count)
        except Exception as e:
            logger.warning(f"Failed to count vectors for {isolation_key}: {e}")
            return 0

    async def close(self) -> None:
        """关闭客户端"""
        # Chroma PersistentClient 无需显式关闭
        self._client = None
        self._collection = None
        self._entity_collection = None

    async def list_by_episode(
        self,
        episode_ids: List[str],
        *,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        layers: Optional[List[MemoryLayer]] = None,
        limit: int = 200,
        include_sources: Optional[List[str]] = None,
        exclude_sources: Optional[List[str]] = None,
    ) -> List[MemoryNode]:
        """Use Chroma metadata predicates before applying ``limit``."""
        wanted = [value for value in episode_ids if value]
        if not wanted:
            return []
        conditions = [{"episode_id": {"$in": wanted}}]
        if user_id:
            conditions.append({"user_id": {"$eq": user_id}})
        if agent_id:
            conditions.append({"agent_id": {"$eq": agent_id}})
        if layers:
            conditions.append({"layer": {"$in": [layer.value for layer in layers]}})
        if include_sources:
            conditions.append({"source": {"$in": list(include_sources)}})
        if exclude_sources:
            conditions.append({"source": {"$nin": list(exclude_sources)}})
        where = conditions[0] if len(conditions) == 1 else {"$and": conditions}

        def _get():
            return self._collection.get(
                where=where, include=["metadatas"], limit=limit,
            )

        result = await _run_in_vdb_pool(_get)
        return [
            self._payload_to_node(self._chroma_metadata_to_payload(meta))
            for meta in (result.get("metadatas") or [])
        ]

    # ================================================================
    # Keyword Search (full-text, jieba + $contains)
    # ================================================================

    async def keyword_search(
        self,
        query: str,
        top_k: int = 10,
        user_id: Optional[str] = None,
        agent_ids: Optional[List[str]] = None,
        layers: Optional[List[MemoryLayer]] = None,
        status_filter: Optional[List[MemoryStatus]] = None,
        only_latest: bool = True,
        isolation_key: str = "",
        isolation_keys: Optional[List[str]] = None,
        include_sources: Optional[List[str]] = None,
        exclude_sources: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Chroma 不支持原生 BM25 / sparse 检索 —— 不提供关键词通道。

        之前的实现用 where_document=$contains 做子串匹配，命中返回 binary 1.0，
        再靠 reader 端 normalize_bm25 sigmoid 归一。但该 sigmoid 是为"经典 BM25
        原始分（0~20）"标定的，binary 1.0 会被压成 ~0.039，对融合几乎无贡献，
        反而稀释强语义结果。与其用这种失真的假 BM25，不如老实退化：

          → keyword_search 返回 []，hybrid reader 自动退化为纯向量召回。

        如需真关键词检索，请使用支持 sparse/BM25 的后端（qdrant / tencent）。
        """
        return []


    # ================================================================
    # Entity Store（独立 collection {collection}_entities）
    # ================================================================

    async def _get_entity_collection(self):
        """Lazy 获取/创建 entity store collection。"""
        if self._entity_collection is not None:
            return self._entity_collection
        from ..pipelines._retrieval.config import entity_collection_name
        name = entity_collection_name(self._collection_name)

        def _init():
            return self._client.get_or_create_collection(
                name=name,
                metadata={"hnsw:space": "cosine"},
            )

        self._entity_collection = await _run_in_vdb_pool(_init)
        logger.debug(f"[vector-store] entity collection ready: {name}")
        return self._entity_collection

    @staticmethod
    def _entity_where(user_id: str, agent_ids: Optional[List[str]]) -> Dict[str, Any]:
        clauses = [{"user_id": user_id}]
        if agent_ids:
            if len(agent_ids) == 1:
                clauses.append({"agent_id": agent_ids[0]})
            else:
                clauses.append({"agent_id": {"$in": list(agent_ids)}})
        return clauses[0] if len(clauses) == 1 else {"$and": clauses}

    async def upsert_entity(
        self,
        *,
        entity_text: str,
        entity_type: str,
        embedding: List[float],
        memory_id: str,
        user_id: str,
        agent_id: str = "",
        merge_threshold: float = 0.95,
    ) -> str:
        import json
        import uuid as _uuid
        coll = await self._get_entity_collection()
        where = self._entity_where(user_id, [agent_id] if agent_id else None)

        def _do():
            # 1) 查最相似的同 user entity
            try:
                res = coll.query(
                    query_embeddings=[embedding],
                    n_results=1,
                    where=where,
                    include=["metadatas", "distances"],
                )
            except Exception:
                res = None

            if res and res.get("ids") and res["ids"][0]:
                top_id = res["ids"][0][0]
                dist = (res.get("distances") or [[None]])[0][0]
                sim = (1.0 - dist) if dist is not None else 0.0
                if sim >= merge_threshold:
                    # 合并 linked_memory_ids
                    meta = (res.get("metadatas") or [[{}]])[0][0] or {}
                    try:
                        linked = json.loads(meta.get("linked_memory_ids") or "[]")
                    except Exception:
                        linked = []
                    if memory_id not in linked:
                        linked.append(memory_id)
                        meta["linked_memory_ids"] = json.dumps(linked, ensure_ascii=False)
                        coll.update(ids=[top_id], metadatas=[meta])
                    return top_id

            # 2) 新建
            eid = str(_uuid.uuid4())
            meta = {
                "data": entity_text,
                "entity_type": entity_type or "",
                "linked_memory_ids": json.dumps([memory_id], ensure_ascii=False),
                "user_id": user_id,
                "agent_id": agent_id or "",
            }
            coll.upsert(ids=[eid], embeddings=[embedding], metadatas=[meta], documents=[entity_text])
            return eid

        return await _run_in_vdb_pool(_do)

    async def search_entities(
        self,
        *,
        query_embedding: List[float],
        user_id: str,
        agent_ids: Optional[List[str]] = None,
        top_k: int = 500,
        min_score: float = 0.0,
    ) -> List[Dict[str, Any]]:
        import json
        coll = await self._get_entity_collection()
        where = self._entity_where(user_id, agent_ids)

        def _do():
            return coll.query(
                query_embeddings=[query_embedding],
                n_results=top_k,
                where=where,
                include=["metadatas", "distances"],
            )

        res = await _run_in_vdb_pool(_do)
        out: List[Dict[str, Any]] = []
        if not res or not res.get("ids") or not res["ids"][0]:
            return out
        ids = res["ids"][0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        for i, eid in enumerate(ids):
            meta = metas[i] or {}
            dist = dists[i] if i < len(dists) else None
            score = (1.0 - dist) if dist is not None else 0.0
            if score < min_score:
                continue
            try:
                linked = json.loads(meta.get("linked_memory_ids") or "[]")
            except Exception:
                linked = []
            out.append({
                "entity_id": eid,
                "data": meta.get("data", ""),
                "entity_type": meta.get("entity_type", ""),
                "linked_memory_ids": linked,
                "score": score,
            })
        out.sort(key=lambda x: x["score"], reverse=True)
        return out

    async def list_entities(
        self, *, user_id: str, agent_ids: Optional[List[str]] = None, top_k: int = 10000
    ) -> List[Dict[str, Any]]:
        import json
        coll = await self._get_entity_collection()
        where = self._entity_where(user_id, agent_ids)

        def _do():
            return coll.get(where=where, limit=top_k, include=["metadatas"])

        res = await _run_in_vdb_pool(_do)
        out: List[Dict[str, Any]] = []
        ids = (res or {}).get("ids") or []
        metas = (res or {}).get("metadatas") or []
        for i, eid in enumerate(ids):
            meta = metas[i] or {}
            try:
                linked = json.loads(meta.get("linked_memory_ids") or "[]")
            except Exception:
                linked = []
            out.append({
                "entity_id": eid,
                "data": meta.get("data", ""),
                "entity_type": meta.get("entity_type", ""),
                "linked_memory_ids": linked,
            })
        return out

    async def delete_entities_for_memory(
        self, *, memory_id: str, user_id: str, agent_ids: Optional[List[str]] = None
    ) -> int:
        import json
        coll = await self._get_entity_collection()
        where = self._entity_where(user_id, agent_ids)

        def _do():
            res = coll.get(where=where, limit=100000, include=["metadatas"])
            ids = (res or {}).get("ids") or []
            metas = (res or {}).get("metadatas") or []
            affected = 0
            for i, eid in enumerate(ids):
                meta = metas[i] or {}
                try:
                    linked = json.loads(meta.get("linked_memory_ids") or "[]")
                except Exception:
                    linked = []
                if memory_id not in linked:
                    continue
                affected += 1
                remaining = [m for m in linked if m != memory_id]
                if not remaining:
                    coll.delete(ids=[eid])
                else:
                    meta["linked_memory_ids"] = json.dumps(remaining, ensure_ascii=False)
                    coll.update(ids=[eid], metadatas=[meta])
            return affected

        return await _run_in_vdb_pool(_do)

    # ================================================================
    # Chroma 特有：metadata 转换
    # ================================================================
    # Chroma metadata 只支持 str/int/float/bool，不支持 list/dict/None。
    # 需要将 payload 中的复杂类型序列化。

    @staticmethod
    def _payload_to_chroma_metadata(payload: Dict[str, Any]) -> Dict[str, Any]:
        """将通用 payload 转换为 Chroma 兼容的 metadata（扁平化）"""
        import json
        meta = {}
        for k, v in payload.items():
            if v is None:
                meta[k] = ""  # Chroma 不支持 None
            elif isinstance(v, list):
                meta[k] = json.dumps(v, ensure_ascii=False)
            elif isinstance(v, dict):
                meta[k] = json.dumps(v, ensure_ascii=False)
            elif isinstance(v, bool):
                meta[k] = v
            elif isinstance(v, (int, float, str)):
                meta[k] = v
            else:
                meta[k] = str(v)
        return meta

    @staticmethod
    def _chroma_metadata_to_payload(meta: Dict[str, Any]) -> Dict[str, Any]:
        """将 Chroma metadata 还原为通用 payload"""
        import json
        payload = {}
        # 需要从 JSON string 还原为 list/dict 的字段
        json_fields = {
            "meta_tags", "tags",
            "supersedes", "superseded_by",
            "evidence_chain", "custom",
        }
        for k, v in meta.items():
            if k in json_fields and isinstance(v, str):
                try:
                    payload[k] = json.loads(v)
                except (json.JSONDecodeError, ValueError):
                    payload[k] = v
            elif isinstance(v, str) and v == "":
                # 可能是 None 的占位
                payload[k] = None
            else:
                payload[k] = v
        return payload
