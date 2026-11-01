"""Re-embed the SDK's stores into a new embedding space.

An embedding space is the set of physical collections (and, in ultra mode, the
Kuzu graph) whose vectors were computed by one embedding model at one width.
Switching the model means computing every vector again with the new model and
writing it into a *new* space. The stored text is enough for that: each record
keeps its document and metadata, and the text that was embedded can be rebuilt
from them (`reembed_text`). The original conversations are not needed.

Contract:

- The caller pauses every writer before calling into this module and keeps
  them paused until the switch commits. The source stores are treated as frozen.
- Nothing here writes to the source. Vector sources are only read; the source
  graph is opened read-only.
- Source and target are named explicitly (`VectorSpace`, graph paths). Nothing
  is derived from the embedding provider or endpoint; the identity of a space is
  the model name plus the width (`space_identity`).
- Vector re-embeds are resumable: a target record with the same id is skipped
  (see `skip_existing`). Graph re-embeds restart from scratch: the target is
  built at a staging path that is wiped on every start and moved into place
  only after the copy verifies.

Only the Chroma vector backend and the Kuzu graph backend are supported.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import logging
import os
import random
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Literal,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Union,
)

from .config import MemoryConfig
from .embedding import EmbeddingDimensionMismatchError, EmbeddingService
from .utils.async_executor import run_in_executor_guarded

logger = logging.getLogger(__name__)

VectorRole = Literal[
    "memory", "entities",
    "coding_keys", "coding_keys_cv",
    "working_facts", "working_facts_cv",
]
VECTOR_ROLES: Tuple[str, ...] = (
    "memory", "entities",
    "coding_keys", "coding_keys_cv",
    "working_facts", "working_facts_cv",
)

# Collection metadata keys written on target collections this module creates.
# Records themselves are copied verbatim, so the model can only live here.
COLLECTION_MODEL_KEY = "hy_embedding_model"
COLLECTION_DIMS_KEY = "hy_embedding_dims"

_SELF_RECALL_WARN_RATE = 0.9


class ReembedCancelled(RuntimeError):
    """Raised at a batch boundary when ``cancel()`` returns True.

    Every batch written before the cancel is complete; no batch is half-written.
    ``report`` holds the progress made so far (a ``ReembedReport`` for vectors,
    ``None`` for the graph, whose staging copy is discarded).
    """

    def __init__(self, message: str = "reembed cancelled", report: Any = None) -> None:
        super().__init__(message)
        self.report = report


# ======================================================================
# Space identity and naming
# ======================================================================

@dataclass(frozen=True)
class EmbeddingSpaceIdentity:
    """What makes two embedding spaces the same: model name and width."""

    model: str
    dims: int

    @property
    def key(self) -> str:
        return f"{self.model}#{self.dims}"

    @property
    def digest(self) -> str:
        """Short stable hash, usable inside a collection or directory name."""
        return hashlib.sha256(self.key.encode("utf-8")).hexdigest()[:12]


def space_identity(model: str, dims: int) -> EmbeddingSpaceIdentity:
    """Identity of an embedding space. Provider and endpoint are not part of it."""
    name = " ".join(str(model or "").split())
    if not name:
        raise ValueError("space_identity needs a model name")
    width = int(dims)
    if width <= 0:
        raise ValueError(f"space_identity needs a positive width, got {dims!r}")
    return EmbeddingSpaceIdentity(model=name, dims=width)


@dataclass(frozen=True)
class VectorSpace:
    """One embedding space in a Chroma store, named explicitly.

    ``base_name`` is the collection base without the width suffix, exactly what
    ``vector_store.collection_name`` is for a client. ``collections`` overrides
    the physical name per role when the caller manages names itself; roles left
    out of an explicit mapping are not part of the space.
    """

    base_name: str
    dims: Optional[int]
    persist_directory: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    model: Optional[str] = None
    collections: Optional[Mapping[str, str]] = None

    @classmethod
    def from_config(
        cls, config: MemoryConfig, *, base_name: Optional[str] = None,
    ) -> "VectorSpace":
        vs = config.vector_store
        provider = str(vs.provider or "chroma").lower()
        if provider != "chroma":
            raise NotImplementedError(
                f"re-embed supports the chroma vector store only, got {provider!r}"
            )
        return cls(
            base_name=base_name or vs.collection_name or "agent_memories",
            dims=vs.embedding_dims,
            persist_directory=vs.persist_directory,
            host=vs.host,
            port=vs.port,
            model=getattr(config.embedder, "model", None),
        )

    def names(self) -> Dict[str, str]:
        if self.collections is not None:
            unknown = set(self.collections) - set(VECTOR_ROLES)
            if unknown:
                raise ValueError(f"unknown vector roles: {sorted(unknown)}")
            return {role: self.collections[role] for role in VECTOR_ROLES if role in self.collections}
        return _derived_names(self.base_name, self.dims)

    def location(self) -> Tuple[str, str]:
        if self.host:
            return ("http", f"{self.host}:{self.port or 8000}")
        return ("local", os.path.realpath(str(self.persist_directory or "")))


SpaceLike = Union[VectorSpace, MemoryConfig]


def _derived_names(base_name: str, dims: Optional[int]) -> Dict[str, str]:
    from .coding.store import _derive_coding_collection_name, _derive_fact_collection_name
    from .data.vector_store_base import physical_collection_name
    from .pipelines._retrieval.config import entity_collection_name

    memory = physical_collection_name(base_name, dims)
    return {
        "memory": memory,
        "entities": entity_collection_name(memory),
        "coding_keys": physical_collection_name(_derive_coding_collection_name(base_name, ""), dims),
        "coding_keys_cv": physical_collection_name(_derive_coding_collection_name(base_name, "_cv"), dims),
        "working_facts": physical_collection_name(_derive_fact_collection_name(base_name, ""), dims),
        "working_facts_cv": physical_collection_name(_derive_fact_collection_name(base_name, "_cv"), dims),
    }


def _space(value: SpaceLike) -> VectorSpace:
    if isinstance(value, VectorSpace):
        return value
    if isinstance(value, MemoryConfig):
        return VectorSpace.from_config(value)
    raise TypeError(f"expected VectorSpace or MemoryConfig, got {type(value).__name__}")


def vector_space_names(
    config: SpaceLike, *, base_name: Optional[str] = None,
) -> Dict[str, str]:
    """Every physical collection a client with ``config`` opens for the vector roles.

    The width suffix is included. Pass ``base_name`` to name a space other than
    ``config.vector_store.collection_name``.
    """
    if isinstance(config, MemoryConfig):
        return VectorSpace.from_config(config, base_name=base_name).names()
    space = _space(config)
    if base_name is not None:
        return _derived_names(base_name, space.dims)
    return space.names()


# ======================================================================
# The text a record was embedded from
# ======================================================================

def _custom_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def reembed_text(
    role: str, document: Optional[str], metadata: Optional[Mapping[str, Any]],
) -> str:
    """The exact text the write path embedded for one vector record.

    - memory, ``custom.memory_kind == "canonical_state"``: ``custom.state_retrieval_text``
    - memory with a non-empty ``custom.retrieval_text`` (schemas): that text
    - everything else, coding keys and working facts included: the document,
      falling back to ``metadata["content"]``
    """
    meta = metadata or {}
    if role == "memory":
        custom = _custom_dict(meta.get("custom"))
        if custom.get("memory_kind") == "canonical_state":
            text = str(custom.get("state_retrieval_text") or "").strip()
            if text:
                return text
        text = str(custom.get("retrieval_text") or "").strip()
        if text:
            return text
    if document:
        return document
    return str(meta.get("content") or "")


def _embeddable(text: str) -> str:
    # OpenAI-compatible services reject an empty input with a 400 that would
    # fail the whole batch. A record with no text has no meaning to preserve.
    return text if text and text.strip() else " "


def _record_digest(document: Optional[str], metadata: Optional[Mapping[str, Any]]) -> str:
    payload = json.dumps(
        [document, dict(metadata) if metadata is not None else None],
        sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ======================================================================
# Chroma access (read-only on the source)
# ======================================================================

_VECTOR_PAGE = 500


async def _vdb(func, *args, **kwargs):
    from .data.vector_store_chroma import _run_in_vdb_pool
    return await _run_in_vdb_pool(func, *args, **kwargs)


def _client_for(space: VectorSpace):
    import chromadb

    if space.host:
        return chromadb.HttpClient(host=space.host, port=space.port or 8000)
    # Same settings as ChromaVectorStore.initialize, so both share one system.
    return chromadb.PersistentClient(path=space.persist_directory)


def _get_collection(client, name: str):
    try:
        return client.get_collection(name=name)
    except Exception as exc:  # chromadb NotFoundError / ValueError by version
        if "does not exist" in str(exc).lower() or type(exc).__name__ in (
            "NotFoundError", "InvalidCollectionException",
        ):
            return None
        raise


def _page(collection, offset: int, limit: int, include: Sequence[str]) -> Dict[str, Any]:
    return collection.get(limit=limit, offset=offset, include=list(include))


def _iter_pages(collection, include: Sequence[str], page: int = _VECTOR_PAGE):
    total = collection.count()
    offset = 0
    while offset < total:
        got = _page(collection, offset, page, include)
        ids = got.get("ids") or []
        if not ids:
            break
        yield got
        offset += len(ids)


def _first_width(collection) -> Optional[int]:
    got = collection.get(limit=1, include=["embeddings"])
    embeddings = got.get("embeddings")
    if embeddings is None or len(embeddings) == 0:
        return None
    return len(embeddings[0])


def _check_not_same(source: VectorSpace, target: VectorSpace, src_name: str, tgt_name: str) -> None:
    if source.location() == target.location() and src_name == tgt_name:
        raise ValueError(
            f"source and target are the same collection {src_name!r}; "
            "re-embed always writes a new collection"
        )


def _roles(space: VectorSpace, roles: Optional[Iterable[str]]) -> List[str]:
    names = space.names()
    if roles is None:
        return [role for role in VECTOR_ROLES if role in names]
    wanted = list(roles)
    unknown = [role for role in wanted if role not in VECTOR_ROLES]
    if unknown:
        raise ValueError(f"unknown vector roles: {unknown}")
    return [role for role in wanted if role in names]


# ======================================================================
# Plan
# ======================================================================

@dataclass(frozen=True)
class ReembedCollectionPlan:
    role: str
    source_name: str
    target_name: str
    source_count: int
    target_exists: bool
    target_count: int


@dataclass(frozen=True)
class GraphReembedPlan:
    source_path: str
    node_counts: Dict[str, int]
    rel_counts: Dict[str, int]
    memory_embeddings: int
    beh_embeddings: int
    topic_embeddings: int
    hierarchy_topic_embeddings: int
    source_width: Optional[int]
    target_width: Optional[int]

    @property
    def memory_nodes(self) -> int:
        return self.node_counts.get("Memory", 0)

    @property
    def topics(self) -> int:
        return self.node_counts.get("Topic", 0)

    @property
    def hierarchy_topics(self) -> int:
        return self.node_counts.get("HierarchyTopic", 0)

    @property
    def vectors(self) -> int:
        return (
            self.memory_embeddings + self.beh_embeddings
            + self.topic_embeddings + self.hierarchy_topic_embeddings
        )


@dataclass(frozen=True)
class ReembedPlan:
    collections: Tuple[ReembedCollectionPlan, ...]
    total_vectors: int
    source_dims: Optional[int]
    target_dims: Optional[int]
    graph: Optional[GraphReembedPlan] = None

    @property
    def estimated_vectors(self) -> int:
        """Vectors to compute: every vector record plus every graph embedding."""
        return self.total_vectors + (self.graph.vectors if self.graph else 0)

    @property
    def per_role(self) -> Dict[str, int]:
        return {item.role: item.source_count for item in self.collections}


def _plan_sync(source: VectorSpace, target: VectorSpace) -> Tuple[List[ReembedCollectionPlan], Optional[int]]:
    src_client = _client_for(source)
    tgt_client = _client_for(target)
    src_names, tgt_names = source.names(), target.names()
    out: List[ReembedCollectionPlan] = []
    source_dims: Optional[int] = None
    for role in VECTOR_ROLES:
        if role not in src_names or role not in tgt_names:
            continue
        src = _get_collection(src_client, src_names[role])
        tgt = _get_collection(tgt_client, tgt_names[role])
        count = src.count() if src is not None else 0
        if src is not None and count and source_dims is None:
            source_dims = _first_width(src)
        out.append(ReembedCollectionPlan(
            role=role,
            source_name=src_names[role],
            target_name=tgt_names[role],
            source_count=count,
            target_exists=tgt is not None,
            target_count=tgt.count() if tgt is not None else 0,
        ))
    return out, source_dims


async def plan_reembed(
    source: SpaceLike,
    target: SpaceLike,
    *,
    source_graph_path: Optional[Union[str, Path]] = None,
    target_graph_dims: Optional[int] = None,
) -> ReembedPlan:
    """Per-role record counts and the number of vectors a re-embed computes.

    Pass ``source_graph_path`` to include the Kuzu graph. Read-only.
    """
    src, tgt = _space(source), _space(target)
    collections, source_dims = await _vdb(_plan_sync, src, tgt)
    graph = None
    if source_graph_path is not None and Path(source_graph_path).exists():
        graph = await plan_graph_reembed(
            source_graph_path, target_dims=target_graph_dims or tgt.dims,
        )
    return ReembedPlan(
        collections=tuple(collections),
        total_vectors=sum(item.source_count for item in collections),
        source_dims=source_dims if source_dims is not None else src.dims,
        target_dims=tgt.dims,
        graph=graph,
    )


# ======================================================================
# Vector re-embed
# ======================================================================

@dataclass(frozen=True)
class ReembedProgress:
    role: str
    done: int       # source records handled so far
    total: int      # source records in this role
    embedded: int   # vectors computed and written
    reused: int     # skipped: the target already holds an identical record
    kept: int = 0   # skipped: the target holds a different record with this id (target wins)


@dataclass(frozen=True)
class ReembedReport:
    per_role: Dict[str, ReembedProgress]
    elapsed_s: float

    @property
    def embedded(self) -> int:
        return sum(item.embedded for item in self.per_role.values())

    @property
    def reused(self) -> int:
        return sum(item.reused for item in self.per_role.values())

    @property
    def kept(self) -> int:
        return sum(item.kept for item in self.per_role.values())


def _default_embedding(target: SpaceLike) -> EmbeddingService:
    if isinstance(target, MemoryConfig):
        from .core.embed_service import EmbedService
        return EmbedService(target)
    raise ValueError("pass embedding= when the target is a VectorSpace")


def _check_service_dims(embedding: Any, dims: int) -> None:
    getter = getattr(embedding, "get_embedding_dims", None)
    if not callable(getter):
        return
    try:
        declared = getter()
    except Exception:
        return
    if isinstance(declared, int) and declared and declared != dims:
        raise EmbeddingDimensionMismatchError(
            f"embedding service width {declared} != target width {dims}"
        )


def _validate_vectors(vectors: Sequence[Any], expected: int, dims: int, where: str) -> List[List[float]]:
    if len(vectors) != expected:
        raise ValueError(f"{where}: expected {expected} vectors, got {len(vectors)}")
    out: List[List[float]] = []
    for vector in vectors:
        width = len(vector) if vector is not None else 0
        if width != dims:
            raise EmbeddingDimensionMismatchError(
                f"{where}: embedding width {width} != target width {dims}"
            )
        out.append([float(x) for x in vector])
    return out


async def _embed(embedding: Any, texts: List[str]) -> List[Any]:
    if not texts:
        return []
    return list(await embedding.embed_batch(texts, use_cache=False))


def _target_metadata(source_collection, target: VectorSpace) -> Dict[str, Any]:
    metadata = dict(getattr(source_collection, "metadata", None) or {})
    metadata.setdefault("hnsw:space", "cosine")
    metadata.pop(COLLECTION_MODEL_KEY, None)
    metadata.pop(COLLECTION_DIMS_KEY, None)
    if target.model:
        metadata[COLLECTION_MODEL_KEY] = str(target.model)
    if target.dims:
        metadata[COLLECTION_DIMS_KEY] = int(target.dims)
    return metadata


async def reembed_vector_space(
    source: SpaceLike,
    target: SpaceLike,
    *,
    embedding: Optional[EmbeddingService] = None,
    batch_size: int = 64,
    max_inflight: int = 2,
    roles: Optional[Iterable[str]] = None,
    cancel: Optional[Callable[[], bool]] = None,
    on_progress: Optional[Callable[[ReembedProgress], None]] = None,
    skip_existing: bool = True,
) -> ReembedReport:
    """Re-embed every record of ``source`` into ``target`` with ``embedding``.

    All users and all layers (L1_RAW included) are read by paging the raw
    collections. For each record the text from `reembed_text` is embedded and
    the record is upserted into the target with the same id, document and
    metadata, copied verbatim.

    - Every vector's width is checked against ``target.dims`` before anything
      in its batch is written (`EmbeddingDimensionMismatchError`).
    - ``skip_existing=True`` (default): an id the target already holds is never
      touched, so a rerun resumes and a merge of a stray space never overwrites
      live data (the target wins). ``skip_existing=False``: identical records
      are still skipped, records that differ are re-embedded from the source.
    - ``cancel`` is checked before each round of up to ``max_inflight`` batches;
      a cancel raises `ReembedCancelled` with every finished batch written.
    - The source is never written.
    """
    src, tgt = _space(source), _space(target)
    if not tgt.dims:
        raise ValueError("the target space needs an explicit width (dims)")
    dims = int(tgt.dims)
    service = embedding if embedding is not None else _default_embedding(target)
    _check_service_dims(service, dims)
    batch_size = max(1, int(batch_size))
    max_inflight = max(1, int(max_inflight))
    cancelled = cancel or (lambda: False)
    src_names, tgt_names = src.names(), tgt.names()
    selected = [role for role in _roles(src, roles) if role in tgt_names]
    started = time.monotonic()
    per_role: Dict[str, ReembedProgress] = {}

    def _report() -> ReembedReport:
        return ReembedReport(per_role=dict(per_role), elapsed_s=time.monotonic() - started)

    src_client = await _vdb(_client_for, src)
    tgt_client = await _vdb(_client_for, tgt)
    for role in selected:
        src_name, tgt_name = src_names[role], tgt_names[role]
        _check_not_same(src, tgt, src_name, tgt_name)
        src_coll = await _vdb(_get_collection, src_client, src_name)
        total = await _vdb(src_coll.count) if src_coll is not None else 0
        progress = ReembedProgress(role=role, done=0, total=total, embedded=0, reused=0, kept=0)
        per_role[role] = progress
        if not total:
            if on_progress:
                on_progress(progress)
            continue
        tgt_coll = await _vdb(
            tgt_client.get_or_create_collection,
            name=tgt_name,
            metadata=_target_metadata(src_coll, tgt),
        )
        offset = 0
        while offset < total:
            if cancelled():
                raise ReembedCancelled(report=_report())
            # One round: read up to max_inflight batches, embed them together,
            # validate all of them, then write them in order.
            batches: List[Dict[str, Any]] = []
            for _ in range(max_inflight):
                if offset >= total:
                    break
                got = await _vdb(_page, src_coll, offset, batch_size, ["documents", "metadatas"])
                ids = list(got.get("ids") or [])
                if not ids:
                    offset = total
                    break
                offset += len(ids)
                batches.append({
                    "ids": ids,
                    "documents": list(got.get("documents") or [None] * len(ids)),
                    "metadatas": list(got.get("metadatas") or [None] * len(ids)),
                })
            if not batches:
                break
            reused = kept = 0
            for batch in batches:
                existing = await _vdb(
                    tgt_coll.get, ids=batch["ids"], include=["documents", "metadatas"],
                )
                have = {
                    rid: _record_digest(doc, meta)
                    for rid, doc, meta in zip(
                        existing.get("ids") or [],
                        existing.get("documents") or [],
                        existing.get("metadatas") or [],
                    )
                }
                pending = []
                for i, rid in enumerate(batch["ids"]):
                    doc, meta = batch["documents"][i], batch["metadatas"][i]
                    if rid in have:
                        if have[rid] == _record_digest(doc, meta):
                            reused += 1
                            continue
                        if skip_existing:
                            kept += 1
                            continue
                    pending.append(i)
                batch["pending"] = pending
                batch["texts"] = [
                    _embeddable(reembed_text(role, batch["documents"][i], batch["metadatas"][i]))
                    for i in pending
                ]
            results = await asyncio.gather(*(_embed(service, b["texts"]) for b in batches))
            checked = [
                _validate_vectors(vectors, len(b["pending"]), dims, f"{role}:{tgt_name}")
                for b, vectors in zip(batches, results)
            ]
            embedded = 0
            for batch, vectors in zip(batches, checked):
                if not batch["pending"]:
                    continue
                idx = batch["pending"]
                await _vdb(
                    tgt_coll.upsert,
                    ids=[batch["ids"][i] for i in idx],
                    embeddings=vectors,
                    documents=[batch["documents"][i] for i in idx],
                    metadatas=[batch["metadatas"][i] for i in idx],
                )
                embedded += len(idx)
            done = sum(len(b["ids"]) for b in batches)
            progress = ReembedProgress(
                role=role,
                done=progress.done + done,
                total=total,
                embedded=progress.embedded + embedded,
                reused=progress.reused + reused,
                kept=progress.kept + kept,
            )
            per_role[role] = progress
            if on_progress:
                on_progress(progress)
    report = _report()
    logger.info(
        "[reembed] vector space done: embedded=%d reused=%d kept=%d elapsed=%.1fs",
        report.embedded, report.reused, report.kept, report.elapsed_s,
    )
    return report


# ======================================================================
# Scan and diff (MemHub startup reconcile)
# ======================================================================

_ROLE_SUFFIX = re.compile(
    r"^(?P<base>.+?)_(?P<role>coding_keys_cv|coding_keys|working_facts_cv|working_facts)"
    r"(?:_(?P<dims>\d+))?$"
)
_DIMS_SUFFIX = re.compile(r"^(?P<base>.+)_(?P<dims>\d+)$")


def parse_collection_name(name: str) -> Optional[Tuple[str, Optional[int], str]]:
    """Split a physical collection name into (base, dims, role).

    Returns None for collections that are not vector roles (``_tag_index``).
    """
    if name.endswith("_tag_index"):
        return None
    match = _ROLE_SUFFIX.match(name)
    if match:
        dims = match.group("dims")
        return match.group("base"), int(dims) if dims else None, match.group("role")
    role = "memory"
    if name.endswith("_entities"):
        name, role = name[: -len("_entities")], "entities"
    match = _DIMS_SUFFIX.match(name)
    if match:
        return match.group("base"), int(match.group("dims")), role
    return name, None, role


@dataclass(frozen=True)
class ScannedCollection:
    role: str
    name: str
    count: int
    width: Optional[int]
    per_user: Dict[str, int]
    models: Tuple[str, ...]


@dataclass(frozen=True)
class ScannedSpace:
    base_name: str
    dims: Optional[int]
    roles: Dict[str, ScannedCollection]
    persist_directory: str

    @property
    def total(self) -> int:
        return sum(item.count for item in self.roles.values())

    @property
    def width(self) -> Optional[int]:
        """The stored width when every non-empty role agrees, else None."""
        widths = {item.width for item in self.roles.values() if item.width}
        return widths.pop() if len(widths) == 1 else None

    @property
    def per_user(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for item in self.roles.values():
            for user, count in item.per_user.items():
                out[user] = out.get(user, 0) + count
        return out

    @property
    def models(self) -> Tuple[str, ...]:
        return tuple(sorted({m for item in self.roles.values() for m in item.models}))

    def space(self, *, model: Optional[str] = None) -> VectorSpace:
        """A `VectorSpace` naming exactly the collections found."""
        return VectorSpace(
            base_name=self.base_name,
            dims=self.dims if self.dims is not None else self.width,
            persist_directory=self.persist_directory,
            model=model,
            collections={role: item.name for role, item in self.roles.items()},
        )


def _scan_sync(persist_directory: str, per_user: bool) -> List[ScannedSpace]:
    if not os.path.isdir(persist_directory):
        return []
    import chromadb

    client = chromadb.PersistentClient(path=persist_directory)
    grouped: Dict[Tuple[str, Optional[int]], Dict[str, ScannedCollection]] = {}
    for item in client.list_collections():
        name = item if isinstance(item, str) else item.name
        parsed = parse_collection_name(name)
        if parsed is None:
            continue
        base, dims, role = parsed
        collection = client.get_collection(name=name)
        count = collection.count()
        users: Dict[str, int] = {}
        models = set()
        model = (collection.metadata or {}).get(COLLECTION_MODEL_KEY)
        if model:
            models.add(str(model))
        if per_user and count:
            for got in _iter_pages(collection, ["metadatas"], page=2000):
                for meta in got.get("metadatas") or []:
                    meta = meta or {}
                    user = str(meta.get("user_id") or "")
                    users[user] = users.get(user, 0) + 1
                    record_model = meta.get(COLLECTION_MODEL_KEY) or meta.get("embedding_model")
                    if record_model:
                        models.add(str(record_model))
        grouped.setdefault((base, dims), {})[role] = ScannedCollection(
            role=role,
            name=name,
            count=count,
            width=_first_width(collection) if count else None,
            per_user=users,
            models=tuple(sorted(models)),
        )
    return [
        ScannedSpace(base_name=base, dims=dims, roles=roles, persist_directory=persist_directory)
        for (base, dims), roles in sorted(grouped.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0))
    ]


async def scan_vector_spaces(
    persist_directory: Union[str, Path], *, per_user: bool = True,
) -> List[ScannedSpace]:
    """Every embedding space with collections under ``persist_directory``.

    Read-only. Collections are grouped by (base, width) from their names; for
    each role found the scan reports the record count, the stored width (read
    from one embedding), the per-``user_id`` counts and the models recorded in
    collection or record metadata (often none: records do not carry the model,
    only collections created by `reembed_vector_space` do).
    """
    return await _vdb(_scan_sync, str(persist_directory), per_user)


@dataclass(frozen=True)
class SpaceDiff:
    missing: Dict[str, Tuple[str, ...]]  # per role: ids in source, absent from target

    @property
    def total_missing(self) -> int:
        return sum(len(ids) for ids in self.missing.values())

    @property
    def empty(self) -> bool:
        return self.total_missing == 0


def _all_ids(collection) -> List[str]:
    if collection is None:
        return []
    out: List[str] = []
    for got in _iter_pages(collection, [], page=5000):
        out.extend(got.get("ids") or [])
    return out


def _diff_sync(source: VectorSpace, target: VectorSpace, roles: Optional[Iterable[str]]) -> SpaceDiff:
    src_client, tgt_client = _client_for(source), _client_for(target)
    src_names, tgt_names = source.names(), target.names()
    missing: Dict[str, Tuple[str, ...]] = {}
    for role in _roles(source, roles):
        src_ids = _all_ids(_get_collection(src_client, src_names[role]))
        tgt_ids = set(_all_ids(
            _get_collection(tgt_client, tgt_names[role]) if role in tgt_names else None
        ))
        missing[role] = tuple(rid for rid in src_ids if rid not in tgt_ids)
    return SpaceDiff(missing=missing)


async def diff_spaces(
    source: SpaceLike, target: SpaceLike, *, roles: Optional[Iterable[str]] = None,
) -> SpaceDiff:
    """Per role, the ids the source holds that the target does not.

    Every record counts whatever its ``status`` (deleted and superseded records
    included). An empty diff means the source holds nothing unmerged.
    """
    return await _vdb(_diff_sync, _space(source), _space(target), roles)


# ======================================================================
# Verify and drop
# ======================================================================

@dataclass(frozen=True)
class GraphVerification:
    ok: bool
    node_counts: Dict[str, Tuple[int, int]]  # table: (source, target)
    rel_counts: Dict[str, Tuple[int, int]]
    target_width: Optional[int]
    vector_index: bool
    problems: Tuple[str, ...]


@dataclass(frozen=True)
class ReembedVerification:
    ok: bool
    per_role: Dict[str, Dict[str, int]]  # source, target, missing, extra, mismatched, bad_dims
    per_user: Dict[str, int]             # target record count by metadata user_id
    self_recall_hits: int
    self_recall_samples: int
    problems: Tuple[str, ...]
    warnings: Tuple[str, ...] = ()
    graph: Optional[GraphVerification] = None


def _collect(collection, include: Sequence[str], dims: Optional[int]):
    digests: Dict[str, str] = {}
    users: Dict[str, int] = {}
    bad_dims = 0
    if collection is None:
        return digests, users, bad_dims
    for got in _iter_pages(collection, include):
        ids = got.get("ids") or []
        docs = got.get("documents") or [None] * len(ids)
        metas = got.get("metadatas") or [None] * len(ids)
        embeddings = got.get("embeddings")
        for i, rid in enumerate(ids):
            digests[rid] = _record_digest(docs[i], metas[i])
            user = str((metas[i] or {}).get("user_id") or "")
            users[user] = users.get(user, 0) + 1
            if dims is not None and embeddings is not None and len(embeddings[i]) != dims:
                bad_dims += 1
    return digests, users, bad_dims


def _verify_sync(source: VectorSpace, target: VectorSpace, roles: Optional[Iterable[str]]):
    src_client, tgt_client = _client_for(source), _client_for(target)
    src_names, tgt_names = source.names(), target.names()
    per_role: Dict[str, Dict[str, int]] = {}
    per_user_src: Dict[str, int] = {}
    per_user_tgt: Dict[str, int] = {}
    common: List[Tuple[str, str]] = []  # (role, id)
    problems: List[str] = []
    for role in _roles(source, roles):
        if role not in tgt_names:
            continue
        src_digests, src_users, _ = _collect(
            _get_collection(src_client, src_names[role]), ["documents", "metadatas"], None,
        )
        tgt_digests, tgt_users, bad = _collect(
            _get_collection(tgt_client, tgt_names[role]),
            ["documents", "metadatas", "embeddings"], int(target.dims or 0) or None,
        )
        missing = [rid for rid in src_digests if rid not in tgt_digests]
        extra = [rid for rid in tgt_digests if rid not in src_digests]
        mismatched = [
            rid for rid, digest in src_digests.items()
            if rid in tgt_digests and tgt_digests[rid] != digest
        ]
        per_role[role] = {
            "source": len(src_digests), "target": len(tgt_digests),
            "missing": len(missing), "extra": len(extra),
            "mismatched": len(mismatched), "bad_dims": bad,
        }
        for label, values in (("missing", missing), ("extra", extra), ("mismatched", mismatched)):
            if values:
                problems.append(f"{role}: {len(values)} {label} (e.g. {values[0]})")
        if bad:
            problems.append(f"{role}: {bad} target vectors not {target.dims} wide")
        for user, count in src_users.items():
            per_user_src[user] = per_user_src.get(user, 0) + count
        for user, count in tgt_users.items():
            per_user_tgt[user] = per_user_tgt.get(user, 0) + count
        common.extend((role, rid) for rid in src_digests if rid in tgt_digests)
    for user in sorted(set(per_user_src) | set(per_user_tgt)):
        if per_user_src.get(user, 0) != per_user_tgt.get(user, 0):
            problems.append(
                f"user {user!r}: source {per_user_src.get(user, 0)} != "
                f"target {per_user_tgt.get(user, 0)}"
            )
    return per_role, per_user_tgt, common, problems


def _sample_records(target: VectorSpace, picks: List[Tuple[str, str]]):
    client = _client_for(target)
    names = target.names()
    out = []
    by_role: Dict[str, List[str]] = {}
    for role, rid in picks:
        by_role.setdefault(role, []).append(rid)
    for role, ids in by_role.items():
        collection = _get_collection(client, names[role])
        got = collection.get(ids=ids, include=["documents", "metadatas"])
        for rid, doc, meta in zip(got["ids"], got["documents"], got["metadatas"]):
            out.append((role, rid, reembed_text(role, doc, meta)))
    return out


def _query_sync(target: VectorSpace, role: str, vectors: List[List[float]], k: int) -> List[List[str]]:
    collection = _get_collection(_client_for(target), target.names()[role])
    got = collection.query(query_embeddings=vectors, n_results=k, include=[])
    return [list(ids) for ids in got.get("ids") or []]


async def verify_reembed(
    source: SpaceLike,
    target: SpaceLike,
    *,
    embedding: Optional[EmbeddingService] = None,
    samples: int = 50,
    recall_k: int = 3,
    roles: Optional[Iterable[str]] = None,
    seed: Optional[int] = 0,
    source_graph_path: Optional[Union[str, Path]] = None,
    target_graph_path: Optional[Union[str, Path]] = None,
) -> ReembedVerification:
    """Check a finished re-embed.

    - Per role, the id sets are equal and every id has the same document and
      metadata on both sides (sha256 of the canonical JSON).
    - Every target vector is ``target.dims`` wide.
    - Per ``user_id``, source and target hold the same number of records.
    - Self-recall: for ``samples`` random records, querying the target with
      the re-embedded text returns the record in the top ``recall_k``. Below
      90% this is a warning, not a failure, because near-duplicates exist.
    - With both graph paths: `verify_graph_reembed`.
    """
    src, tgt = _space(source), _space(target)
    per_role, per_user, common, problems = await _vdb(_verify_sync, src, tgt, roles)
    warnings: List[str] = []
    hits = sampled = 0
    if samples > 0 and common:
        service = embedding
        if service is None and isinstance(target, MemoryConfig):
            service = _default_embedding(target)
        if service is None:
            warnings.append("self-recall skipped: no embedding service")
        else:
            rng = random.Random(seed)
            picks = rng.sample(common, min(samples, len(common)))
            records = await _vdb(_sample_records, tgt, picks)
            by_role: Dict[str, List[Tuple[str, str]]] = {}
            for role, rid, text in records:
                by_role.setdefault(role, []).append((rid, text))
            for role, items in by_role.items():
                vectors = await _embed(service, [_embeddable(text) for _, text in items])
                vectors = _validate_vectors(vectors, len(items), int(tgt.dims), f"verify:{role}")
                found = await _vdb(_query_sync, tgt, role, vectors, recall_k)
                for (rid, _), ids in zip(items, found):
                    sampled += 1
                    hits += int(rid in ids)
            if sampled and hits / sampled < _SELF_RECALL_WARN_RATE:
                warnings.append(f"self-recall {hits}/{sampled} below {_SELF_RECALL_WARN_RATE:.0%}")
    graph = None
    if source_graph_path is not None and target_graph_path is not None:
        graph = await verify_graph_reembed(source_graph_path, target_graph_path, target_dims=tgt.dims)
        problems.extend(f"graph: {p}" for p in graph.problems)
    return ReembedVerification(
        ok=not problems,
        per_role=per_role,
        per_user=per_user,
        self_recall_hits=hits,
        self_recall_samples=sampled,
        problems=tuple(problems),
        warnings=tuple(warnings),
        graph=graph,
    )


def _drop_sync(space: VectorSpace, roles: Optional[Iterable[str]]) -> List[str]:
    from .data.vector_store_chroma import open_collection_names

    names = space.names()
    selected = [names[role] for role in _roles(space, roles)]
    busy = sorted(set(selected) & open_collection_names(space.location()))
    if busy:
        raise RuntimeError(
            f"refusing to drop collections held open by a live client: {busy}"
        )
    client = _client_for(space)
    dropped = []
    for name in selected:
        if _get_collection(client, name) is None:
            continue
        client.delete_collection(name=name)
        dropped.append(name)
    return dropped


async def drop_vector_space(
    space: SpaceLike, *, roles: Optional[Iterable[str]] = None,
) -> List[str]:
    """Delete the space's collections and return the names deleted.

    Refuses when a live store in this process holds any of them open.
    """
    return await _vdb(_drop_sync, _space(space), roles)


# ======================================================================
# Graph (Kuzu)
# ======================================================================

# All Kuzu work of a re-embed runs on this one thread: Kuzu connections are not
# thread-safe and a QueryResult must be consumed on the thread that produced it.
_graph_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="kuzu-reembed",
)
_GRAPH_PAGE = 2000
_GRAPH_WRITE_TX = 1000
_KUZU_SIDECARS = (".wal", ".shadow", ".lock", ".tmp")
_SDK_VECTOR_INDEX = (
    "CALL CREATE_VECTOR_INDEX('Memory', 'memory_content_idx', 'embedding', "
    "metric := 'cosine');"
)

# Embedding columns rebuilt from text. Everything else is copied verbatim.
_GRAPH_EMBEDDING_COLUMNS: Dict[str, Tuple[str, ...]] = {
    "Memory": ("embedding", "beh_embedding"),
    "Topic": ("embedding",),
    "HierarchyTopic": ("embedding",),
}


async def _graph(func, *args, **kwargs):
    import functools
    return await run_in_executor_guarded(_graph_executor, functools.partial(func, *args, **kwargs))


def hierarchy_topic_label(subject: str, path_json: Any, name: str = "") -> Tuple[str, bool]:
    """Rebuild the label a HierarchyTopic embedding was computed from.

    ``add_topic_path`` embeds ``"{subject}: kind:segment > kind:segment"`` and
    stores ``path_json`` as ``{"names": [...], "types": [...]}`` for the same
    prefix, so that form rebuilds exactly (returns ``exact=True``). A legacy
    list-form ``path_json`` has no stored types: the label is rebuilt with the
    default Topic -> Facet types the current code would use, and flagged
    inexact. With no usable path the stored ``name`` is used, also inexact.
    """
    from .structure import parse_hierarchy_path_payload

    raw = path_json
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            raw = None
    parsed = parse_hierarchy_path_payload(path_json or "")
    path, types = parsed["path"], parsed["node_types"]
    if not path:
        fallback = str(name or subject or "").strip()
        return (f"{subject}: {fallback}" if subject and name else fallback), False
    exact = isinstance(raw, dict) and isinstance(raw.get("types"), list)
    label = f"{subject}: " + " > ".join(f"{kind}:{segment}" for segment, kind in zip(path, types))
    return label, exact


def _memory_text(row: Mapping[str, Any], column: str) -> str:
    custom = _custom_dict(row.get("custom_json"))
    if column == "beh_embedding":
        return str(custom.get("beh_abstraction") or "").strip()
    text = str(custom.get("retrieval_text") or "").strip()
    return text or str(row.get("content") or "")


def _kuzu():
    try:
        import kuzu
    except ImportError as exc:  # pragma: no cover - kuzu is a core dependency
        raise ImportError("kuzu is required. Install with: pip install kuzu") from exc
    return kuzu


def _rows(conn, query: str, params: Optional[Dict[str, Any]] = None) -> List[List[Any]]:
    result = conn.execute(query, params) if params else conn.execute(query)
    out = []
    while result.has_next():
        out.append(list(result.get_next()))
    return out


def _tables(conn) -> Dict[str, str]:
    result = conn.execute("CALL show_tables() RETURN *;")
    columns = result.get_column_names()
    name_i, type_i = columns.index("name"), columns.index("type")
    out = {}
    while result.has_next():
        row = result.get_next()
        out[str(row[name_i])] = str(row[type_i]).upper()
    return out


def _table_columns(conn, table: str) -> List[Tuple[str, str, bool]]:
    """(name, type, is_primary_key) per property, in declaration order."""
    out = []
    for row in _rows(conn, f"CALL table_info('{table}') RETURN *;"):
        out.append((str(row[1]), str(row[2]), row[4] is True))
    return out


def _array_width(type_name: str) -> Optional[int]:
    match = re.match(r"^FLOAT\[(\d+)\]$", type_name.strip().upper())
    return int(match.group(1)) if match else None


def _count(conn, query: str) -> int:
    rows = _rows(conn, query)
    return int(rows[0][0]) if rows else 0


def _graph_counts(conn) -> Tuple[Dict[str, int], Dict[str, int]]:
    nodes, rels = {}, {}
    for name, kind in _tables(conn).items():
        if kind == "NODE":
            nodes[name] = _count(conn, f"MATCH (n:{name}) RETURN count(n);")
        elif kind == "REL":
            rels[name] = _count(conn, f"MATCH ()-[r:{name}]->() RETURN count(r);")
    return nodes, rels


def _target_ddl(dims: int) -> List[Tuple[str, str]]:
    from .data.graph_store_kuzu import _SCHEMA_DDL_TABLES, _make_memory_ddl

    out = []
    for name, ddl in _SCHEMA_DDL_TABLES.items():
        out.append((name, _make_memory_ddl(dims) if name == "Memory" else ddl))
    return out


def _open_readonly(path: Path):
    kuzu = _kuzu()
    db = kuzu.Database(str(path), read_only=True)
    return db, kuzu.Connection(db)


def _close(conn, db) -> None:
    for obj in (conn, db):
        if obj is None:
            continue
        try:
            obj.close()
        except Exception:
            pass


def _kuzu_files(path: Path) -> List[Path]:
    out = [path] if path.exists() else []
    for suffix in _KUZU_SIDECARS:
        sidecar = path.with_name(path.name + suffix)
        if sidecar.exists():
            out.append(sidecar)
    return out


def _remove_kuzu(path: Path) -> None:
    for item in _kuzu_files(path):
        if item.is_dir() and not item.is_symlink():
            shutil.rmtree(item)
        else:
            item.unlink()


def _source_width(conn) -> Optional[int]:
    for name, type_name, _ in _table_columns(conn, "Memory"):
        if name == "embedding":
            return _array_width(type_name)
    return None


def _assert_not_open(path: Path, what: str) -> None:
    from .data.graph_store_kuzu import open_graph_paths

    if os.path.realpath(str(path)) in open_graph_paths():
        raise RuntimeError(f"{what} graph {path} is held open by a live client in this process")


def _plan_graph_sync(path: Path, target_dims: Optional[int]) -> GraphReembedPlan:
    db, conn = _open_readonly(path)
    try:
        nodes, rels = _graph_counts(conn)
        tables = _tables(conn)

        def _non_null(table: str, column: str) -> int:
            if table not in tables:
                return 0
            return _count(conn, f"MATCH (n:{table}) WHERE n.{column} IS NOT NULL RETURN count(n);")

        return GraphReembedPlan(
            source_path=str(path),
            node_counts=nodes,
            rel_counts=rels,
            memory_embeddings=_non_null("Memory", "embedding"),
            beh_embeddings=_non_null("Memory", "beh_embedding"),
            topic_embeddings=_non_null("Topic", "embedding"),
            hierarchy_topic_embeddings=_non_null("HierarchyTopic", "embedding"),
            source_width=_source_width(conn) if "Memory" in tables else None,
            target_width=target_dims,
        )
    finally:
        _close(conn, db)


async def plan_graph_reembed(
    source_graph_path: Union[str, Path], *, target_dims: Optional[int] = None,
) -> GraphReembedPlan:
    """Node, edge and embedding counts of a graph. Opens it read-only."""
    path = Path(source_graph_path)
    if not path.exists():
        raise FileNotFoundError(f"graph not found: {path}")
    _assert_not_open(path, "source")
    return await _graph(_plan_graph_sync, path, target_dims)


@dataclass(frozen=True)
class GraphReembedProgress:
    table: str
    done: int
    total: int
    embedded: int


@dataclass(frozen=True)
class GraphReembedReport:
    target_path: str
    source_width: Optional[int]
    target_width: int
    node_counts: Dict[str, int]
    rel_counts: Dict[str, int]
    embedded: Dict[str, int]      # "Table.column": vectors computed
    cleared: Dict[str, int]       # "Table.column": vectors set to NULL (no text to rebuild from)
    hierarchy_labels_exact: int
    hierarchy_labels_inexact: Tuple[str, ...]  # hierarchy_topic_ids rebuilt from a fallback text
    skipped_tables: Tuple[str, ...]            # source tables this SDK does not know
    dropped_columns: Tuple[str, ...]           # "Table.column" in the source but not the target
    vector_indexes: Tuple[str, ...]
    elapsed_s: float


@dataclass
class _GraphCopy:
    source: Path
    staging: Path
    dims: int
    src_db: Any = None
    src: Any = None
    dst_db: Any = None
    dst: Any = None
    node_tables: List[str] = field(default_factory=list)
    rel_tables: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    dropped: List[str] = field(default_factory=list)
    columns: Dict[str, List[str]] = field(default_factory=dict)
    pk: Dict[str, str] = field(default_factory=dict)
    source_width: Optional[int] = None

    def open(self) -> None:
        kuzu = _kuzu()
        self.src_db, self.src = _open_readonly(self.source)
        self.staging.parent.mkdir(parents=True, exist_ok=True)
        self.dst_db = kuzu.Database(str(self.staging))
        self.dst = kuzu.Connection(self.dst_db)
        for _, ddl in _target_ddl(self.dims):
            self.dst.execute(ddl + ";")
        src_tables, dst_tables = _tables(self.src), _tables(self.dst)
        self.skipped = sorted(name for name in src_tables if name not in dst_tables)
        for name, kind in dst_tables.items():
            if name not in src_tables:
                continue
            src_cols = _table_columns(self.src, name)
            dst_names = {col for col, _, _ in _table_columns(self.dst, name)}
            self.columns[name] = [col for col, _, _ in src_cols if col in dst_names]
            self.dropped.extend(f"{name}.{col}" for col, _, _ in src_cols if col not in dst_names)
            if kind == "NODE":
                self.node_tables.append(name)
                self.pk[name] = next(col for col, _, is_pk in src_cols if is_pk)
            else:
                self.rel_tables.append(name)
        if "Memory" in src_tables:
            self.source_width = _source_width(self.src)

    def node_total(self, table: str) -> int:
        return _count(self.src, f"MATCH (n:{table}) RETURN count(n);")

    def read_nodes(self, table: str, after: Optional[Any], limit: int) -> List[Dict[str, Any]]:
        pk = self.pk[table]
        embed_cols = _GRAPH_EMBEDDING_COLUMNS.get(table, ())
        plain = [c for c in self.columns[table] if c not in embed_cols]
        flags = [c for c in embed_cols if c in self.columns[table]]
        select = [f"n.{c}" for c in plain] + [f"n.{c} IS NOT NULL" for c in flags]
        where = f"WHERE n.{pk} > $after " if after is not None else ""
        query = (
            f"MATCH (n:{table}) {where}RETURN {', '.join(select)} "
            f"ORDER BY n.{pk} LIMIT {int(limit)};"
        )
        params = {"after": after} if after is not None else None
        out = []
        for row in _rows(self.src, query, params):
            record = dict(zip(plain, row[: len(plain)]))
            record["__has"] = {c: bool(v) for c, v in zip(flags, row[len(plain):])}
            out.append(record)
        return out

    def write_nodes(self, table: str, records: List[Dict[str, Any]]) -> None:
        if not records:
            return
        cols = self.columns[table]
        assigns = ", ".join(f"{c}: $p{i}" for i, c in enumerate(cols))
        query = f"CREATE (n:{table} {{{assigns}}});"
        self.dst.execute("BEGIN TRANSACTION;")
        try:
            for record in records:
                self.dst.execute(query, {f"p{i}": record.get(c) for i, c in enumerate(cols)})
            self.dst.execute("COMMIT;")
        except Exception:
            try:
                self.dst.execute("ROLLBACK;")
            except Exception:
                pass
            raise

    def copy_rels(self, table: str) -> int:
        props = self.columns[table]
        copied = 0
        for row in _rows(self.src, f"CALL show_connection('{table}') RETURN *;"):
            src_table, dst_table = str(row[0]), str(row[1])
            src_pk, dst_pk = self.pk.get(src_table), self.pk.get(dst_table)
            if src_pk is None or dst_pk is None:
                continue
            select = ", ".join([f"a.{src_pk}", f"b.{dst_pk}"] + [f"r.{p}" for p in props])
            result = self.src.execute(
                f"MATCH (a:{src_table})-[r:{table}]->(b:{dst_table}) RETURN {select};"
            )
            assigns = ", ".join(f"{p}: $p{i}" for i, p in enumerate(props))
            create = (
                f"MATCH (a:{src_table} {{{src_pk}: $from}}), (b:{dst_table} {{{dst_pk}: $to}}) "
                f"CREATE (a)-[:{table} {{{assigns}}}]->(b);"
            )
            pending = 0
            self.dst.execute("BEGIN TRANSACTION;")
            try:
                while result.has_next():
                    values = list(result.get_next())
                    params = {"from": values[0], "to": values[1]}
                    params.update({f"p{i}": v for i, v in enumerate(values[2:])})
                    self.dst.execute(create, params)
                    copied += 1
                    pending += 1
                    if pending >= _GRAPH_WRITE_TX:
                        self.dst.execute("COMMIT;")
                        self.dst.execute("BEGIN TRANSACTION;")
                        pending = 0
                self.dst.execute("COMMIT;")
            except Exception:
                try:
                    self.dst.execute("ROLLBACK;")
                except Exception:
                    pass
                raise
        return copied

    def create_indexes(self) -> List[str]:
        """Replay the source's vector indexes, after the data is in."""
        statements: List[str] = []
        try:
            result = self.src.execute("CALL SHOW_INDEXES() RETURN *;")
            columns = result.get_column_names()
            def_i = columns.index("index_definition")
            name_i = columns.index("index_name")
            table_i = columns.index("table_name")
            while result.has_next():
                row = result.get_next()
                definition = str(row[def_i] or "").strip()
                if (
                    definition.upper().startswith("CALL CREATE_VECTOR_INDEX(")
                    and str(row[table_i]) in self.node_tables
                ):
                    statements.append((str(row[name_i]), definition))
        except Exception as exc:
            logger.warning("[reembed] could not read source indexes: %s", exc)
        names = [name for name, _ in statements]
        if "memory_content_idx" not in names and "Memory" in self.node_tables:
            statements.append(("memory_content_idx", _SDK_VECTOR_INDEX))
        built = []
        for name, statement in statements:
            self.dst.execute(statement if statement.endswith(";") else statement + ";")
            built.append(name)
        return built

    def counts(self) -> Tuple[Dict[str, int], Dict[str, int], Dict[str, int], Dict[str, int]]:
        src_nodes, src_rels = _graph_counts(self.src)
        dst_nodes, dst_rels = _graph_counts(self.dst)
        return src_nodes, src_rels, dst_nodes, dst_rels

    def close(self) -> None:
        _close(self.src, self.src_db)
        _close(self.dst, self.dst_db)
        self.src = self.src_db = self.dst = self.dst_db = None


def _publish(staging: Path, target: Path, overwrite: bool) -> None:
    if _kuzu_files(target):
        if not overwrite:
            raise FileExistsError(f"graph target exists: {target}")
        _remove_kuzu(target)
    for item in _kuzu_files(staging):
        suffix = item.name[len(staging.name):]
        os.replace(item, target.with_name(target.name + suffix))


def _staging_path(target: Path) -> Path:
    return target.with_name(target.name + ".reembed-partial")


async def reembed_graph(
    source_graph_path: Union[str, Path],
    target_graph_path: Union[str, Path],
    *,
    embedding: EmbeddingService,
    target_dims: int,
    batch_size: int = 64,
    cancel: Optional[Callable[[], bool]] = None,
    on_progress: Optional[Callable[[GraphReembedProgress], None]] = None,
    overwrite: bool = False,
) -> GraphReembedReport:
    """Build a new Kuzu graph at ``target_graph_path`` with ``target_dims`` vectors.

    The source is opened read-only and never modified, so it stays the
    rollback copy. The new DB gets the SDK's current DDL at the new width; every
    node and every edge is copied verbatim except the embedding columns, which
    are recomputed with ``embedding``:

    - ``Memory.embedding``: ``custom.retrieval_text``, else ``content``
    - ``Memory.beh_embedding``: ``custom.beh_abstraction`` (NULL when missing)
    - ``Topic.embedding``: ``name``
    - ``HierarchyTopic.embedding``: the label from `hierarchy_topic_label`

    A NULL source vector stays NULL. Indexes are built after the data is in.
    The same path is used whether or not the width changes: Kuzu cannot SET an
    indexed column, so an in-place update is not possible anyway.

    Restart semantics: the copy is built at ``<target>.reembed-partial``, which
    is wiped at every start and on failure or cancel, then moved to the target
    only after node and edge counts match the source. A rerun starts over. An
    existing target raises `FileExistsError` unless ``overwrite=True``.
    """
    source = Path(source_graph_path)
    target = Path(target_graph_path)
    if os.path.realpath(str(source)) == os.path.realpath(str(target)):
        raise ValueError("source and target graph paths are the same")
    if not source.exists():
        raise FileNotFoundError(f"graph not found: {source}")
    if _kuzu_files(target) and not overwrite:
        raise FileExistsError(f"graph target exists: {target}")
    _assert_not_open(source, "source")
    _assert_not_open(target, "target")
    dims = int(target_dims)
    _check_service_dims(embedding, dims)
    cancelled = cancel or (lambda: False)
    page = max(1, int(batch_size))
    started = time.monotonic()
    staging = _staging_path(target)
    await _graph(_remove_kuzu, staging)
    copy = _GraphCopy(source=source, staging=staging, dims=dims)
    embedded: Dict[str, int] = {}
    cleared: Dict[str, int] = {}
    labels_exact = 0
    labels_inexact: List[str] = []
    published = False
    try:
        await _graph(copy.open)
        for table in copy.node_tables:
            embed_cols = [c for c in _GRAPH_EMBEDDING_COLUMNS.get(table, ()) if c in copy.columns[table]]
            limit = page if embed_cols else _GRAPH_PAGE
            total = await _graph(copy.node_total, table)
            done = computed = 0
            after = None
            while done < total:
                if cancelled():
                    raise ReembedCancelled("graph reembed cancelled")
                records = await _graph(copy.read_nodes, table, after, limit)
                if not records:
                    break
                after = records[-1][copy.pk[table]]
                for column in embed_cols:
                    wanted, texts = [], []
                    for record in records:
                        record[column] = None
                        if not record["__has"].get(column):
                            continue
                        if table == "Memory":
                            text = _memory_text(record, column)
                        elif table == "Topic":
                            text = str(record.get("name") or "")
                        else:
                            text, exact = hierarchy_topic_label(
                                str(record.get("subject") or ""),
                                record.get("path_json"),
                                str(record.get("name") or ""),
                            )
                            if exact:
                                labels_exact += 1
                            else:
                                labels_inexact.append(str(record.get(copy.pk[table])))
                        if not text.strip():
                            key = f"{table}.{column}"
                            cleared[key] = cleared.get(key, 0) + 1
                            continue
                        wanted.append(record)
                        texts.append(text)
                    vectors = _validate_vectors(
                        await _embed(embedding, texts), len(texts), dims, f"graph:{table}.{column}",
                    )
                    for record, vector in zip(wanted, vectors):
                        record[column] = vector
                    if texts:
                        key = f"{table}.{column}"
                        embedded[key] = embedded.get(key, 0) + len(texts)
                        computed += len(texts)
                await _graph(copy.write_nodes, table, records)
                done += len(records)
                if on_progress:
                    on_progress(GraphReembedProgress(table=table, done=done, total=total, embedded=computed))
        for table in copy.rel_tables:
            if cancelled():
                raise ReembedCancelled("graph reembed cancelled")
            copied = await _graph(copy.copy_rels, table)
            if on_progress:
                on_progress(GraphReembedProgress(table=table, done=copied, total=copied, embedded=0))
        indexes = await _graph(copy.create_indexes)
        src_nodes, src_rels, dst_nodes, dst_rels = await _graph(copy.counts)
        mismatch = [
            f"{name}: {src_nodes[name]} != {dst_nodes.get(name, 0)}"
            for name in copy.node_tables if src_nodes.get(name, 0) != dst_nodes.get(name, 0)
        ] + [
            f"{name}: {src_rels[name]} != {dst_rels.get(name, 0)}"
            for name in copy.rel_tables if src_rels.get(name, 0) != dst_rels.get(name, 0)
        ]
        if mismatch:
            raise RuntimeError(f"graph copy count mismatch: {mismatch}")
        await _graph(copy.close)
        await _graph(_publish, staging, target, overwrite)
        published = True
    finally:
        await _graph(copy.close)
        if not published:
            await _graph(_remove_kuzu, staging)
    if labels_inexact:
        logger.warning(
            "[reembed] %d HierarchyTopic labels rebuilt from a fallback text (legacy path_json)",
            len(labels_inexact),
        )
    return GraphReembedReport(
        target_path=str(target),
        source_width=copy.source_width,
        target_width=dims,
        node_counts={name: dst_nodes.get(name, 0) for name in copy.node_tables},
        rel_counts={name: dst_rels.get(name, 0) for name in copy.rel_tables},
        embedded=embedded,
        cleared=cleared,
        hierarchy_labels_exact=labels_exact,
        hierarchy_labels_inexact=tuple(labels_inexact),
        skipped_tables=tuple(copy.skipped),
        dropped_columns=tuple(copy.dropped),
        vector_indexes=tuple(indexes),
        elapsed_s=time.monotonic() - started,
    )


def _verify_graph_sync(source: Path, target: Path, dims: Optional[int]) -> GraphVerification:
    src_db, src = _open_readonly(source)
    try:
        dst_db, dst = _open_readonly(target)
        try:
            src_nodes, src_rels = _graph_counts(src)
            dst_nodes, dst_rels = _graph_counts(dst)
            width = _source_width(dst) if "Memory" in dst_nodes else None
            try:
                index_names = [str(row[1]) for row in _rows(dst, "CALL SHOW_INDEXES() RETURN *;")]
            except Exception:
                index_names = []
            problems: List[str] = []
            node_counts = {}
            for name in sorted(set(src_nodes) | set(dst_nodes)):
                pair = (src_nodes.get(name, 0), dst_nodes.get(name, 0))
                node_counts[name] = pair
                if pair[0] != pair[1]:
                    problems.append(f"node table {name}: source {pair[0]} != target {pair[1]}")
            rel_counts = {}
            for name in sorted(set(src_rels) | set(dst_rels)):
                pair = (src_rels.get(name, 0), dst_rels.get(name, 0))
                rel_counts[name] = pair
                if pair[0] != pair[1]:
                    problems.append(f"rel table {name}: source {pair[0]} != target {pair[1]}")
            if dims is not None and width != int(dims):
                problems.append(f"Memory.embedding width {width} != {dims}")
            has_index = "memory_content_idx" in index_names
            if "Memory" in dst_nodes and not has_index:
                problems.append("vector index memory_content_idx missing")
            for table, column in (("Memory", "embedding"), ("Topic", "embedding"), ("HierarchyTopic", "embedding")):
                if table not in src_nodes or table not in dst_nodes:
                    continue
                query = f"MATCH (n:{table}) WHERE n.{column} IS NOT NULL RETURN count(n);"
                before, after = _count(src, query), _count(dst, query)
                if before != after:
                    problems.append(f"{table}.{column}: {before} vectors in source, {after} in target")
            return GraphVerification(
                ok=not problems,
                node_counts=node_counts,
                rel_counts=rel_counts,
                target_width=width,
                vector_index=has_index,
                problems=tuple(problems),
            )
        finally:
            _close(dst, dst_db)
    finally:
        _close(src, src_db)


async def verify_graph_reembed(
    source_graph_path: Union[str, Path],
    target_graph_path: Union[str, Path],
    *,
    target_dims: Optional[int] = None,
) -> GraphVerification:
    """Node and edge counts per table, target width, vector index, vector counts."""
    source, target = Path(source_graph_path), Path(target_graph_path)
    _assert_not_open(source, "source")
    _assert_not_open(target, "target")
    return await _graph(_verify_graph_sync, source, target, target_dims)


async def drop_graph(graph_path: Union[str, Path]) -> bool:
    """Delete a Kuzu graph (and its sidecar files). Returns False if absent.

    Refuses when a live store in this process holds it open.
    """
    path = Path(graph_path)
    _assert_not_open(path, "the")
    if not _kuzu_files(path):
        return False
    await _graph(_remove_kuzu, path)
    return True


__all__ = [
    "VECTOR_ROLES",
    "VectorRole",
    "VectorSpace",
    "EmbeddingSpaceIdentity",
    "space_identity",
    "vector_space_names",
    "reembed_text",
    "ReembedCancelled",
    "ReembedCollectionPlan",
    "ReembedPlan",
    "plan_reembed",
    "ReembedProgress",
    "ReembedReport",
    "reembed_vector_space",
    "ScannedCollection",
    "ScannedSpace",
    "scan_vector_spaces",
    "parse_collection_name",
    "SpaceDiff",
    "diff_spaces",
    "ReembedVerification",
    "verify_reembed",
    "drop_vector_space",
    "GraphReembedPlan",
    "plan_graph_reembed",
    "GraphReembedProgress",
    "GraphReembedReport",
    "reembed_graph",
    "hierarchy_topic_label",
    "GraphVerification",
    "verify_graph_reembed",
    "drop_graph",
]
