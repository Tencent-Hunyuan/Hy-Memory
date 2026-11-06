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
import hashlib
import json
import logging
import os
import shutil
import struct
import threading
import uuid
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

# Indirections so tests can inject a crash or an unavailable backup without
# patching the process-wide os module.
_atomic_replace = os.replace
_hard_link = os.link
_copy_file = shutil.copy2


def _run_in_vdb_pool(func, *args, **kwargs):
    """在 VDB 独立线程池中执行同步函数"""
    import functools
    call = functools.partial(func, *args, **kwargs) if args or kwargs else func
    return run_in_executor_guarded(_vdb_executor, call)


def _backup_path(path: Path) -> Path:
    """Legacy pair backup path, kept for stores written before the container."""
    return path.with_name(path.name + ".bak")


def _fsync_file(path: Path) -> None:
    with open(path, "rb") as handle:
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pair_is_consistent(index: Any, saved: Any) -> bool:
    """Whether the index and its payload describe the same save generation.

    FAISS appends a vector for every upsert/update, so `ntotal` counts all
    appends while `idx_to_id` only keeps live ones. The invariants that must
    hold are: `next_idx` equals the number of appends, every live mapping
    points at that mapping's `idx`, and every data row has exactly one live
    mapping.
    """
    if not isinstance(saved, dict):
        return False
    data = saved.get("data")
    raw_mapping = saved.get("idx_to_id")
    next_idx = saved.get("next_idx")
    if not isinstance(data, dict) or not isinstance(raw_mapping, dict):
        return False
    if not isinstance(next_idx, int) or next_idx < 0:
        return False
    try:
        mapping = {int(key): str(value) for key, value in raw_mapping.items()}
    except (TypeError, ValueError):
        return False
    if int(getattr(index, "ntotal", -1)) != next_idx:
        return False
    if len(mapping) != len(data):
        return False
    if len(set(mapping.values())) != len(mapping):
        return False
    if set(mapping.values()) != set(data):
        return False
    for idx, point_id in mapping.items():
        entry = data.get(point_id)
        if not isinstance(entry, dict) or entry.get("idx") != idx:
            return False
        if idx < 0 or idx >= next_idx:
            return False
    return True


def _load_pair(faiss: Any, index_path: Path, payload_path: Path):
    """Load a legacy index/payload pair; return None when unusable."""
    try:
        index = faiss.read_index(str(index_path))
        with open(payload_path, "r", encoding="utf-8") as handle:
            saved = json.load(handle)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError):
        return None
    if not _pair_is_consistent(index, saved):
        return None
    return index, saved


# One file, one atomic replace: the index bytes and the payload cannot come
# from different generations, which a pair of separately replaced files can.
_CONTAINER_MAGIC = b"OMFAISS1\n"
_CONTAINER_HEADER_LENGTH = 4
_CONTAINER_MAX_HEADER = 1024 * 1024


def _write_container(index_path: Path, container_path: Path, payload: bytes) -> None:
    """Write index + payload into one container file and fsync it."""
    index_len = index_path.stat().st_size
    header = json.dumps(
        {
            "schema": 1,
            "payload_len": len(payload),
            "payload_sha256": hashlib.sha256(payload).hexdigest(),
            "index_len": index_len,
            "index_sha256": _sha256_file(index_path),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(header) > _CONTAINER_MAX_HEADER:
        raise ValueError("FAISS container header is too large")
    with open(container_path, "wb") as out:
        out.write(_CONTAINER_MAGIC)
        out.write(struct.pack(">I", len(header)))
        out.write(header)
        out.write(payload)
        with open(index_path, "rb") as source:
            shutil.copyfileobj(source, out, length=1024 * 1024)
        out.flush()
        os.fsync(out.fileno())


def _load_container(faiss: Any, container_path: Path):
    """Load and verify one container file; return None when unusable."""
    try:
        file_size = container_path.stat().st_size
        with open(container_path, "rb") as handle:
            if handle.read(len(_CONTAINER_MAGIC)) != _CONTAINER_MAGIC:
                return None
            length_bytes = handle.read(_CONTAINER_HEADER_LENGTH)
            if len(length_bytes) != _CONTAINER_HEADER_LENGTH:
                return None
            header_length = struct.unpack(">I", length_bytes)[0]
            if header_length <= 0 or header_length > _CONTAINER_MAX_HEADER:
                return None
            header = json.loads(handle.read(header_length))
            payload_len = int(header["payload_len"])
            index_len = int(header["index_len"])
            body_start = len(_CONTAINER_MAGIC) + _CONTAINER_HEADER_LENGTH + header_length
            if payload_len < 0 or index_len < 0:
                return None
            if body_start + payload_len + index_len != file_size:
                return None
            payload = handle.read(payload_len)
            index_bytes = handle.read(index_len)
            if len(payload) != payload_len or len(index_bytes) != index_len:
                return None
            if handle.read(1):
                return None
        if hashlib.sha256(payload).hexdigest() != str(header["payload_sha256"]):
            return None
        if hashlib.sha256(index_bytes).hexdigest() != str(header["index_sha256"]):
            return None
        saved = json.loads(payload)
        index = faiss.deserialize_index(np.frombuffer(index_bytes, dtype=np.uint8))
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        RuntimeError,
        json.JSONDecodeError,
    ):
        return None
    if not _pair_is_consistent(index, saved):
        return None
    return index, saved


def _stage_backup(source: Path, backup: Path) -> Path:
    """Snapshot one container beside it; raise rather than lose the old one.

    The backup lands under a unique name first and is promoted only after the
    live file has been replaced. A hard link costs no copy; filesystems that
    refuse it fall back to a copy. If neither works, the caller aborts before
    touching the live file and the previous recovery point stays intact.
    """
    staging = backup.with_name(backup.name + f".next-{os.getpid()}-{uuid.uuid4().hex}")
    try:
        try:
            _hard_link(source, staging)
        except OSError:
            _copy_file(source, staging)
        _fsync_file(staging)
    except BaseException:
        staging.unlink(missing_ok=True)
        raise
    return staging


class FaissVectorStore(VectorStoreBase):
    """
    FAISS 向量存储实现

    使用 faiss-cpu 做相似度检索，内存 dict 存储 payload，
    持久化到磁盘（index 字节与 payload 存在同一个容器文件里）。
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
        # The live container was unreadable and its backup could not be
        # restored. Never stage that damaged file over the good backup.
        self._live_container_damaged = False

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

            container_path = persist_dir / f"{self._collection_name}.omfaiss"
            backup_path = container_path.with_name(container_path.name + ".bak")
            index_path = persist_dir / f"{self._collection_name}.faiss"
            payload_path = persist_dir / f"{self._collection_name}.json"

            loaded = None
            if container_path.exists():
                loaded = _load_container(faiss, container_path)
                if loaded is None:
                    logger.warning(
                        f"[faiss] container is inconsistent: {container_path}"
                    )
                    if backup_path.exists():
                        loaded = _load_container(faiss, backup_path)
                        if loaded is not None:
                            logger.warning(
                                f"[faiss] recovered the previous save from "
                                f"{backup_path.name}; restoring it"
                            )
                            staging = container_path.with_name(
                                container_path.name
                                + f".restore-{os.getpid()}-{uuid.uuid4().hex}"
                            )
                            try:
                                _copy_file(backup_path, staging)
                                _fsync_file(staging)
                                _atomic_replace(staging, container_path)
                            except OSError as error:
                                self._live_container_damaged = True
                                logger.warning(
                                    f"[faiss] could not restore the backup: {error}"
                                )
                            finally:
                                staging.unlink(missing_ok=True)
            elif index_path.exists() and payload_path.exists():
                # Pre-container stores. The first successful save writes the
                # container; the legacy pair is left in place so an older
                # build still finds the state it understands.
                loaded = _load_pair(faiss, index_path, payload_path)
                if loaded is None:
                    logger.warning(
                        f"[faiss] legacy index/payload pair is inconsistent: {index_path}"
                    )
                    backup_index = _backup_path(index_path)
                    backup_payload = _backup_path(payload_path)
                    if backup_index.exists() and backup_payload.exists():
                        loaded = _load_pair(faiss, backup_index, backup_payload)
                        if loaded is not None:
                            logger.warning(
                                f"[faiss] recovered the previous save from "
                                f"{backup_index.name}"
                            )
            elif index_path.exists() or payload_path.exists():
                logger.warning(
                    f"[faiss] incomplete legacy index/payload pair: {index_path}"
                )

            if loaded is not None:
                self._index, saved = loaded
                self._data = saved.get("data", {})
                self._idx_to_id = {
                    int(k): v for k, v in saved.get("idx_to_id", {}).items()
                }
                self._next_idx = saved.get("next_idx", 0)
                logger.info(f"FAISS index loaded, points={self._index.ntotal}")
            elif (
                not container_path.exists()
                and not index_path.exists()
                and not payload_path.exists()
            ):
                # 创建新索引 (使用 IndexFlatIP，余弦相似度需先归一化)
                self._index = faiss.IndexFlatIP(self._dims)
                logger.info(f"Created new FAISS index, dims={self._dims}")
            else:
                # 有文件但一个有效代都读不出来：保留现场，拒绝用空索引覆盖。
                raise RuntimeError(
                    f"FAISS index and payload cannot be loaded consistently and no "
                    f"usable backup exists: {container_path}"
                )

        await _run_in_vdb_pool(_init)
        logger.debug(
            f"VectorStore initialized (FAISS), collection={self._collection_name}"
        )

    # ================================================================
    # 持久化
    # ================================================================

    def _persist(self) -> None:
        """持久化索引和 payload 到磁盘。

        FAISS 的原生 `write_index` 直接覆盖目标文件；磁盘写满会在旧数据上
        留下半个索引，进程被杀会在 JSON 写入途中留下空文件——这正是
        issue #17 里“新增一条记录导致整库打不开”的原因。

        现在 index 字节和 payload 写进同一个容器文件，标题记录两者长度和
        SHA-256，再用一次 `os.replace` 原子换入。单个文件意味着 index 和
        payload 不可能来自不同代。换入前先给旧容器做备份；备份失败就中止，
        旧容器和旧备份都保持不动。
        """
        import faiss

        persist_dir = Path(self.config.vector_store.persist_directory)
        persist_dir.mkdir(parents=True, exist_ok=True)

        container_path = persist_dir / f"{self._collection_name}.omfaiss"
        backup_path = container_path.with_name(container_path.name + ".bak")
        token = f".tmp-{os.getpid()}-{uuid.uuid4().hex}"
        temp_index = persist_dir / f".{self._collection_name}.index{token}"
        temp_container = container_path.with_name(container_path.name + token)
        staged_backup: Path | None = None

        try:
            faiss.write_index(self._index, str(temp_index))
            _fsync_file(temp_index)

            saved = {
                "data": self._data,
                "idx_to_id": {str(k): v for k, v in self._idx_to_id.items()},
                "next_idx": self._next_idx,
            }
            payload = json.dumps(saved, ensure_ascii=False).encode("utf-8")
            _write_container(temp_index, temp_container, payload)

            # Secure the previous recovery point before touching the live
            # file. If this raises, nothing live has moved.
            if container_path.exists() and not self._live_container_damaged:
                staged_backup = _stage_backup(container_path, backup_path)

            _atomic_replace(temp_container, container_path)
            self._live_container_damaged = False
            if staged_backup is not None:
                # A single-file promotion; the old backup is replaced only
                # after the live container is already the new generation.
                _atomic_replace(staged_backup, backup_path)
                staged_backup = None
            _fsync_directory(persist_dir)
        finally:
            temp_index.unlink(missing_ok=True)
            temp_container.unlink(missing_ok=True)
            if staged_backup is not None:
                staged_backup.unlink(missing_ok=True)

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

                if point_id in self._data:
                    # 更新: FAISS 不支持原地更新，记录新索引位置
                    old_idx = self._data[point_id]["idx"]
                    # 标记旧索引无效
                    if old_idx in self._idx_to_id:
                        del self._idx_to_id[old_idx]

                idx = self._next_idx
                self._next_idx += 1
                self._index.add(vec)
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
                ids = []
                vecs = []
                for node in valid_nodes:
                    point_id = self._node_id_to_point_id(node.node_id)
                    payload = self._node_to_payload(node)
                    vec = self._normalize(node.embedding)

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
                    ids.append(node.node_id)
                    vecs.append(vec)

                if vecs:
                    batch = np.vstack(vecs)
                    self._index.add(batch)
                    self._persist()

                return ids

        return await _run_in_vdb_pool(_upsert_batch)

    async def update_embedding(self, node_id: str, embedding: List[float]) -> bool:
        """仅更新向量（payload 不变）"""
        point_id = self._node_id_to_point_id(node_id)
        try:
            def _update():
                with self._lock:
                    if point_id not in self._data:
                        return False

                    old_idx = self._data[point_id]["idx"]
                    if old_idx in self._idx_to_id:
                        del self._idx_to_id[old_idx]

                    vec = self._normalize(embedding)
                    idx = self._next_idx
                    self._next_idx += 1
                    self._index.add(vec)
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
                    # 1) payload 字段
                    for k, v in updates.items():
                        self._data[point_id]["payload"][k] = v
                    # 2) 向量：重新 add（FaissFlat 不支持原地改，沿用 update_embedding 思路）
                    if new_embedding is not None:
                        old_idx = self._data[point_id]["idx"]
                        if old_idx in self._idx_to_id:
                            del self._idx_to_id[old_idx]
                        vec = self._normalize(new_embedding)
                        idx = self._next_idx
                        self._next_idx += 1
                        self._index.add(vec)
                        self._idx_to_id[idx] = point_id
                        self._data[point_id]["idx"] = idx
                        self._data[point_id]["embedding"] = list(new_embedding)
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
