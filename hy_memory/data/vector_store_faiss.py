"""
Agent Memory V2 - VectorStore (FAISS)

基于 FAISS 的向量存储层。

特点:
- 使用 faiss-cpu，纯 CPU 计算
- 内存 dict 存 payload，search 后手动过滤 metadata
- faiss.write_index() + JSON 持久化 payload
- 同步 API → _run_in_vdb_pool() 包装为异步
"""

from typing import Optional, List, Dict, Any
from pathlib import Path
import asyncio
import concurrent.futures
import json
import logging
import threading
import numpy as np

from ..models.memory import (
    MemoryNode, MemoryLayer, MemoryStatus,
)
from ..config import MemoryConfig
from ..utils.async_executor import run_in_executor_guarded
from .vector_store_base import VectorStoreBase, passes_created_after

logger = logging.getLogger(__name__)

# VDB 独立线程池
from .vector_store_chroma import _vdb_executor


def _run_in_vdb_pool(func, *args, **kwargs):
    """在 VDB 独立线程池中执行同步函数"""
    import functools
    call = functools.partial(func, *args, **kwargs) if args or kwargs else func
    return run_in_executor_guarded(_vdb_executor, call)


class FaissVectorStore(VectorStoreBase):
    """
    FAISS 向量存储实现

    使用 faiss-cpu 做相似度检索，内存 dict 存储 payload，
    持久化到磁盘（faiss index 文件 + JSON payload 文件）。
    """

    def __init__(self, config: MemoryConfig):
        super().__init__(config)
        self._index = None
        # point_id (str) → { "payload": dict, "embedding": list, "idx": int }
        self._data: Dict[str, Dict[str, Any]] = {}
        # faiss 内部索引 int → point_id str 的映射
        self._idx_to_id: Dict[int, str] = {}
        self._next_idx: int = 0
        self._lock = threading.Lock()
        self._dims = config.vector_store.embedding_dims

    async def initialize(self) -> None:
        """初始化 FAISS 索引"""
        try:
            import faiss
        except ImportError:
            raise ImportError(
                "faiss-cpu is required for FAISS backend. "
                "Install with: pip install hy-memory[faiss]"
            )

        def _init():
            persist_dir = Path(self.config.vector_store.persist_directory)
            persist_dir.mkdir(parents=True, exist_ok=True)

            index_path = persist_dir / f"{self._collection_name}.faiss"
            payload_path = persist_dir / f"{self._collection_name}.json"

            if index_path.exists() and payload_path.exists():
                # 从磁盘加载
                self._index = faiss.read_index(str(index_path))
                with open(payload_path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                self._data = saved.get("data", {})
                self._idx_to_id = {int(k): v for k, v in saved.get("idx_to_id", {}).items()}
                self._next_idx = saved.get("next_idx", 0)
                logger.info(
                    f"FAISS index loaded from {index_path}, "
                    f"points={self._index.ntotal}"
                )
            else:
                # 创建新索引 (使用 IndexFlatIP，余弦相似度需先归一化)
                self._index = faiss.IndexFlatIP(self._dims)
                logger.info(f"Created new FAISS index, dims={self._dims}")

        await _run_in_vdb_pool(_init)
        logger.debug(f"VectorStore initialized (FAISS), collection={self._collection_name}")

    # ================================================================
    # 持久化
    # ================================================================

    def _persist(self) -> None:
        """持久化索引和 payload 到磁盘"""
        import faiss

        persist_dir = Path(self.config.vector_store.persist_directory)
        persist_dir.mkdir(parents=True, exist_ok=True)

        index_path = persist_dir / f"{self._collection_name}.faiss"
        payload_path = persist_dir / f"{self._collection_name}.json"

        faiss.write_index(self._index, str(index_path))
        saved = {
            "data": self._data,
            "idx_to_id": {str(k): v for k, v in self._idx_to_id.items()},
            "next_idx": self._next_idx,
        }
        with open(payload_path, "w", encoding="utf-8") as f:
            json.dump(saved, f, ensure_ascii=False)

    @staticmethod
    def _normalize(vec: List[float]) -> np.ndarray:
        """L2 归一化，用于余弦相似度"""
        arr = np.array(vec, dtype=np.float32).reshape(1, -1)
        norm = np.linalg.norm(arr)
        if norm > 0:
            arr /= norm
        return arr

    # ================================================================
    # 写入
    # ================================================================

    async def upsert(self, node: MemoryNode) -> str:
        """写入或更新一个 MemoryNode"""
        if node.embedding is None:
            raise ValueError(f"Node {node.node_id} has no embedding, cannot upsert to vector store")

        payload = self._node_to_payload(node)
        point_id = self._node_id_to_point_id(node.node_id)

        def _upsert():
            with self._lock:
                vec = self._normalize(node.embedding)
                # FAISS can reject the vector (for example, wrong dimensions).
                # Keep the old mapping and next index intact until it accepts it.
                self._index.add(vec)

                if point_id in self._data:
                    # 更新: FAISS 不支持原地更新，记录新索引位置
                    old_idx = self._data[point_id]["idx"]
                    # 标记旧索引无效
                    if old_idx in self._idx_to_id:
                        del self._idx_to_id[old_idx]

                idx = self._next_idx
                self._next_idx += 1
                self._idx_to_id[idx] = point_id
                self._data[point_id] = {
                    "payload": payload,
                    "idx": idx,
                }
                self._persist()

        await _run_in_vdb_pool(_upsert)
        logger.debug(
            f"[vector-store] upsert: id={node.node_id} "
            f"layer={node.layer.value if hasattr(node.layer, 'value') else node.layer} "
            f"isolation_key={payload.get('isolation_key', '')} "
            f"content={node.content[:200]}"
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

        def _upsert_batch():
            with self._lock:
                prepared = []
                for node in valid_nodes:
                    point_id = self._node_id_to_point_id(node.node_id)
                    payload = self._node_to_payload(node)
                    vec = self._normalize(node.embedding)
                    prepared.append((node.node_id, point_id, payload, vec))

                # Prepare and append the complete batch before publishing any
                # payloads. Both vstack and FAISS may reject incompatible shapes.
                batch = np.vstack([entry[3] for entry in prepared])
                self._index.add(batch)
                for _, point_id, payload, _ in prepared:
                    if point_id in self._data:
                        old_idx = self._data[point_id]["idx"]
                        if old_idx in self._idx_to_id:
                            del self._idx_to_id[old_idx]

                    idx = self._next_idx
                    self._next_idx += 1
                    self._idx_to_id[idx] = point_id
                    self._data[point_id] = {
                        "payload": payload,
                        "idx": idx,
                    }
                self._persist()
                return [entry[0] for entry in prepared]

        return await _run_in_vdb_pool(_upsert_batch)

    async def update_embedding(self, node_id: str, embedding: List[float]) -> bool:
        """仅更新向量（payload 不变）"""
        point_id = self._node_id_to_point_id(node_id)
        try:
            def _update():
                with self._lock:
                    if point_id not in self._data:
                        return False

                    vec = self._normalize(embedding)
                    self._index.add(vec)
                    old_idx = self._data[point_id]["idx"]
                    if old_idx in self._idx_to_id:
                        del self._idx_to_id[old_idx]

                    idx = self._next_idx
                    self._next_idx += 1
                    self._idx_to_id[idx] = point_id
                    self._data[point_id]["idx"] = idx
                    self._persist()
                    return True

            return await _run_in_vdb_pool(_update)
        except Exception as e:
            logger.warning(f"Failed to update embedding for {node_id}: {e}")
            return False

    async def update_payload(self, node_id: str, updates: Dict[str, Any]) -> bool:
        """更新 payload 字段；若 updates 含 'embedding'，同时更新向量（重建索引项）。"""
        if not updates:
            return True
        point_id = self._node_id_to_point_id(node_id)
        updates = dict(updates)
        new_embedding = updates.pop("embedding", None)  # 取出向量单独处理
        try:
            def _update():
                with self._lock:
                    if point_id not in self._data:
                        return False
                    # Accept the vector before changing either the payload or
                    # its mapping, so a rejected edit leaves the old node intact.
                    if new_embedding is not None:
                        vec = self._normalize(new_embedding)
                        self._index.add(vec)
                        old_idx = self._data[point_id]["idx"]
                        if old_idx in self._idx_to_id:
                            del self._idx_to_id[old_idx]
                        idx = self._next_idx
                        self._next_idx += 1
                        self._idx_to_id[idx] = point_id
                        self._data[point_id]["idx"] = idx
                        self._data[point_id]["embedding"] = list(new_embedding)
                    for k, v in updates.items():
                        self._data[point_id]["payload"][k] = v
                    self._persist()
                    return True
            result = await _run_in_vdb_pool(_update)
            if result:
                logger.debug(
                    f"[vector-store] update_payload: id={node_id} updates={list(updates.keys())} "
                    f"embedding={'yes' if new_embedding is not None else 'no'}"
                )
            return result
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
        tags_match_any: Optional[List[str]] = None,  # reader_hybrid_tag 路 B；faiss 暂未实现，忽略
        created_after: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """语义检索（FAISS 搜索 + 手动 metadata 过滤）"""

        # 预处理过滤集合
        keys_set = set(isolation_keys) if isolation_keys else None
        effective_uids = user_ids if user_ids else ([user_id] if user_id else [])
        uids_set = set(effective_uids) if effective_uids else None
        agent_ids_set = set(agent_ids) if agent_ids else None
        include_sources_set = set(include_sources) if include_sources else None
        exclude_sources_set = set(exclude_sources) if exclude_sources else set()

        layer_values = set(l.value for l in layers) if layers else None
        if status_filter:
            status_values = set(s.value for s in status_filter)
        else:
            status_values = {MemoryStatus.ACTIVE.value}

        def _search():
            with self._lock:
                if self._index.ntotal == 0:
                    return []

                vec = self._normalize(query_embedding)
                # IndexFlatIP 支持 IDSelector：先由 metadata 构造允许的内部 ID，
                # 再让 FAISS 在该子集内做精确 top-k。不能先取 limit*N 再过滤，
                # 否则黑白名单会吃掉名额并改变召回语义。
                import faiss
                allowed_indices = []
                for point_id, entry in self._data.items():
                    idx = entry.get("idx")
                    if idx is None or self._idx_to_id.get(int(idx)) != point_id:
                        continue
                    payload = entry.get("payload") or {}
                    if keys_set:
                        if payload.get("isolation_key") not in keys_set:
                            continue
                    elif isolation_key:
                        if payload.get("isolation_key") != isolation_key:
                            continue
                    elif uids_set:
                        if payload.get("user_id") not in uids_set:
                            continue
                        if agent_ids_set and payload.get("agent_id") not in agent_ids_set:
                            continue
                    source = payload.get("source") or "default"
                    if include_sources_set and source not in include_sources_set:
                        continue
                    if source in exclude_sources_set:
                        continue
                    if layer_values and payload.get("layer") not in layer_values:
                        continue
                    if payload.get("status") not in status_values:
                        continue
                    if only_latest and not payload.get("is_latest", True):
                        continue
                    if not passes_created_after(payload, created_after):
                        continue
                    allowed_indices.append(int(idx))
                if not allowed_indices:
                    return []
                selector = faiss.IDSelectorBatch(
                    np.asarray(allowed_indices, dtype=np.int64)
                )
                params = faiss.SearchParameters(sel=selector)
                k = min(limit, len(allowed_indices))
                scores, indices = self._index.search(vec, k, params=params)

                results = []
                for score, idx in zip(scores[0], indices[0]):
                    if idx < 0:
                        continue
                    point_id = self._idx_to_id.get(int(idx))
                    if point_id is None:
                        # 已被删除或更新的旧索引
                        continue

                    entry = self._data.get(point_id)
                    if entry is None:
                        continue

                    payload = entry["payload"]

                    # metadata 过滤: 精确 keys > 单 key > user_ids/user_id + agent 级
                    if keys_set:
                        if payload.get("isolation_key") not in keys_set:
                            continue
                    elif isolation_key:
                        if payload.get("isolation_key") != isolation_key:
                            continue
                    elif uids_set:
                        if payload.get("user_id") not in uids_set:
                            continue
                        if agent_ids_set and payload.get("agent_id") not in agent_ids_set:
                            continue
                    if layer_values and payload.get("layer") not in layer_values:
                        continue
                    if payload.get("status") not in status_values:
                        continue
                    # 只搜索演化链末端节点
                    if only_latest and not payload.get("is_latest", True):
                        continue
                    # 时间过滤：事件时间 memory_at 优先，为空回退 gmt_created
                    if not passes_created_after(payload, created_after):
                        continue

                    # score 是内积（归一化后 = cosine similarity）
                    sim = float(score)
                    if sim < score_threshold:
                        continue

                    results.append({
                        "point_id": point_id,
                        "payload": payload,
                        "score": sim,
                    })

                    if len(results) >= limit:
                        break

                return results

        raw_results = await _run_in_vdb_pool(_search)

        output = []
        for r in raw_results:
            node = self._payload_to_node(r["payload"])
            output.append({
                "node_id": node.node_id,
                "score": r["score"],
                "node": node,
            })

        logger.debug(
            f"[vector-store] search: isolation_key={isolation_key} "
            f"isolation_keys={isolation_keys} user_id={user_id} agent_ids={agent_ids} "
            f"limit={limit} found={len(output)}"
        )
        for i, item in enumerate(output):
            logger.debug(
                f"[vector-store] hit[{i}] score={item['score']:.4f} "
                f"id={item['node_id']} "
                f"layer={item['node'].layer.value if hasattr(item['node'].layer, 'value') else ''} "
                f"content={item['node'].content[:200]}"
            )

        return output

    async def get_by_id(self, node_id: str) -> Optional[MemoryNode]:
        """按 ID 获取"""
        point_id = self._node_id_to_point_id(node_id)
        try:
            entry = self._data.get(point_id)
            if entry is None:
                return None
            node = self._payload_to_node(entry["payload"])
            # FAISS 不方便按 ID 取向量，跳过 embedding
            return node
        except Exception as e:
            logger.warning(f"Failed to get vector point {node_id}: {e}")
            return None

    async def get_embeddings(self, node_ids: List[str]) -> Dict[str, List[float]]:
        """批量取向量，供去重 / episode 摘要计算 cosine。

        ``_data`` 只保存 payload 与 FAISS 内部下标；普通 upsert 不额外存一份
        embedding，向量本体在 FAISS index 里。这里按当前有效下标 reconstruct，
        并用 ``_idx_to_id`` 校验，避免读到已被替换的旧下标。
        """

        def _get() -> Dict[str, List[float]]:
            out: Dict[str, List[float]] = {}
            with self._lock:
                for nid in node_ids:
                    point_id = self._node_id_to_point_id(nid)
                    entry = self._data.get(point_id)
                    if entry is None:
                        continue
                    embedding = entry.get("embedding")
                    if embedding:
                        out[nid] = list(embedding)
                        continue
                    idx = entry.get("idx")
                    if (
                        idx is None
                        or self._index is None
                        or self._idx_to_id.get(int(idx)) != point_id
                    ):
                        continue
                    try:
                        out[nid] = self._index.reconstruct(int(idx)).tolist()
                    except Exception:
                        continue
            return out

        return await _run_in_vdb_pool(_get)

    # ================================================================
    # 删除
    # ================================================================

    async def delete(self, node_id: str) -> bool:
        """删除单个向量点"""
        point_id = self._node_id_to_point_id(node_id)
        try:
            def _delete():
                with self._lock:
                    if point_id not in self._data:
                        return False
                    idx = self._data[point_id]["idx"]
                    if idx in self._idx_to_id:
                        del self._idx_to_id[idx]
                    del self._data[point_id]
                    self._persist()
                    return True
            return await _run_in_vdb_pool(_delete)
        except Exception as e:
            logger.warning(f"Failed to delete vector point {node_id}: {e}")
            return False

    async def delete_by_isolation_key(self, isolation_key: str) -> int:
        """删除某隔离键下的所有向量"""
        try:
            def _delete():
                with self._lock:
                    to_delete = []
                    for pid, entry in self._data.items():
                        if entry["payload"].get("isolation_key") == isolation_key:
                            to_delete.append(pid)

                    for pid in to_delete:
                        idx = self._data[pid]["idx"]
                        if idx in self._idx_to_id:
                            del self._idx_to_id[idx]
                        del self._data[pid]

                    if to_delete:
                        self._persist()
                    return len(to_delete)
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
                with self._lock:
                    to_delete = []
                    for pid, entry in self._data.items():
                        payload = entry["payload"]
                        if payload.get("user_id") != user_id:
                            continue
                        if agent_id is not None and payload.get("agent_id") != agent_id:
                            continue
                        if session_id is not None and payload.get("session_id") != session_id:
                            continue
                        to_delete.append(pid)

                    for pid in to_delete:
                        idx = self._data[pid]["idx"]
                        if idx in self._idx_to_id:
                            del self._idx_to_id[idx]
                        del self._data[pid]

                    if to_delete:
                        self._persist()
                    return len(to_delete)
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
        import numpy as np

        effective_uids = list(user_ids) if user_ids else ([user_id] if user_id else [])
        if not effective_uids:
            logger.warning("[vector-store] list_by_user called without user_id/user_ids, refused")
            return []
        uid_set = set(effective_uids)
        aid_set = set(agent_ids) if agent_ids else ({agent_id} if agent_id else None)
        include_set = set(include_sources) if include_sources else None
        exclude_set = set(exclude_sources) if exclude_sources else set()

        status_values = {s.value if hasattr(s, 'value') else str(s) for s in status_filter} if status_filter else None
        layer_values = {l.value if hasattr(l, 'value') else str(l) for l in layers} if layers else None
        _raw_value = MemoryLayer.L1_RAW.value
        if layer_values is not None and not allow_raw_layer:
            layer_values.discard(_raw_value)
            if not layer_values:
                # 只点名了 raw：置为不可能命中的空白名单，结果必然为空集
                layer_values = {"__blocked__"}

        def _list():
            nodes = []
            with self._lock:
                for pid, entry in self._data.items():
                    payload = entry["payload"]
                    if payload.get("user_id") not in uid_set:
                        continue
                    if aid_set and payload.get("agent_id") not in aid_set:
                        continue
                    source = payload.get("source") or "default"
                    if include_set and source not in include_set:
                        continue
                    if source in exclude_set:
                        continue
                    if only_latest and not payload.get("is_latest", True):
                        continue
                    if status_values and payload.get("status", "") not in status_values:
                        continue
                    if layer_values and payload.get("layer", "") not in layer_values:
                        continue
                    if not allow_raw_layer and payload.get("layer", "") == _raw_value:
                        continue
                    node = self._payload_to_node(payload)
                    # 从 FAISS index 取向量
                    idx = entry.get("idx")
                    if with_vectors and idx is not None and self._index is not None:
                        try:
                            vec = self._index.reconstruct(int(idx))
                            node.embedding = vec.tolist()
                        except Exception:
                            pass
                    nodes.append(node)
                    if len(nodes) >= limit:
                        break
            return nodes

        result = await _run_in_vdb_pool(_list)
        logger.info(
            f"[vector-store] list_by_user: user_ids={effective_uids} "
            f"agent_ids={sorted(aid_set) if aid_set else []} found={len(result)}"
        )
        return result

    # ================================================================
    # 统计
    # ================================================================

    async def get_stats(self) -> Dict[str, Any]:
        """获取集合统计"""
        try:
            return {
                "collection": self._collection_name,
                "points_count": len(self._data),
                "vectors_count": self._index.ntotal if self._index else 0,
                "status": "ok",
            }
        except Exception as e:
            return {"error": str(e)}

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
        wanted = {value for value in episode_ids if value}
        include_set = set(include_sources or [])
        exclude_set = set(exclude_sources or [])
        layer_set = {layer.value for layer in layers} if layers else None
        if not wanted:
            return []

        def _list():
            nodes = []
            with self._lock:
                for point_id, entry in self._data.items():
                    payload = entry.get("payload") or {}
                    if payload.get("episode_id") not in wanted:
                        continue
                    if user_id and payload.get("user_id") != user_id:
                        continue
                    if agent_id and payload.get("agent_id") != agent_id:
                        continue
                    if layer_set and payload.get("layer") not in layer_set:
                        continue
                    source = payload.get("source") or "default"
                    if include_set and source not in include_set:
                        continue
                    if source in exclude_set:
                        continue
                    idx = entry.get("idx")
                    if idx is None or self._idx_to_id.get(int(idx)) != point_id:
                        continue
                    nodes.append(self._payload_to_node(payload))
                    if len(nodes) >= limit:
                        break
            return nodes

        return await _run_in_vdb_pool(_list)

    async def count(self, isolation_key: str) -> int:
        """统计某隔离键下的向量数量"""
        try:
            c = 0
            for entry in self._data.values():
                if entry["payload"].get("isolation_key") == isolation_key:
                    c += 1
            return c
        except Exception as e:
            logger.warning(f"Failed to count vectors for {isolation_key}: {e}")
            return 0

    async def close(self) -> None:
        """关闭并持久化"""
        if self._index is not None:
            try:
                await _run_in_vdb_pool(self._persist)
            except Exception as e:
                logger.warning(f"Failed to persist FAISS index on close: {e}")
            self._index = None
            self._data.clear()
            self._idx_to_id.clear()
