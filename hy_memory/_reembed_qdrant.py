"""Qdrant access for `hy_memory.reembed`, shaped like the Chroma calls it makes.

`reembed` pages, reads, writes and queries collections through a handful of
Chroma client and collection calls. This module answers the same calls over
Qdrant, so the re-embed algorithm (resume, cancel, digests, verify) is one code
path for both stores. What a record *is* differs per store and is mapped here:

- id: the Qdrant point id, as a string (an integer id is given back as an int).
- metadata: the point payload, verbatim; written back verbatim.
- document: the text the Qdrant write path embedded the record from, read from
  the payload. ``content`` for memory, Coding keys and Working facts (what the
  Chroma store keeps as the document), ``data`` for entities, ``tag`` for the
  tag index, and for a retrieval clause its clause of the parent memory
  (`split_retrieval_clauses(parent content)[clause_index]`, as written).
  `reembed.reembed_text` then applies the same rules as for Chroma.
- vector: the unnamed dense vector. A collection with the sparse ``bm25``
  vector keeps it beside the dense one; a target gets the source's sparse
  vector verbatim (it is computed from the text, not by the embedding model).

A target collection is created like its source: dense width from the target
space, the source's distance, the source's sparse config and, on a server, the
source's payload indexes. Nothing here writes a source collection.

Clients are cached per location (`close_qdrant_clients`): local Qdrant locks
its directory to one client per process, and source and target usually share
a location. A local directory is driven from a single owner thread, as the
SDK's store does.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

_SPARSE = "bm25"
_CLAUSE_SUFFIX = "__retrieval_clauses"
_TAG_SUFFIX = "_tag_index"
_ENTITY_SUFFIX = "_entities"
_SCROLL = 500

_CLIENTS: Dict[Tuple[str, str], "QdrantFacade"] = {}
_CLIENTS_LOCK = threading.Lock()


class NotFoundError(LookupError):
    """A collection that does not exist (the name `reembed` looks for)."""


def qdrant_client(space: Any) -> "QdrantFacade":
    key = space.location()
    with _CLIENTS_LOCK:
        facade = _CLIENTS.get(key)
        if facade is None:
            facade = QdrantFacade(_open(space), local=space.host is None)
            _CLIENTS[key] = facade
        return facade


def close_qdrant_clients() -> None:
    """Close every client this module opened (local directories are unlocked)."""
    with _CLIENTS_LOCK:
        facades = list(_CLIENTS.values())
        _CLIENTS.clear()
    for facade in facades:
        try:
            facade.raw.close()
        except Exception:  # pragma: no cover - best effort
            pass


def _open(space: Any) -> Any:
    from qdrant_client import QdrantClient

    if space.host:
        return QdrantClient(
            host=space.host,
            port=space.port or 6333,
            api_key=space.api_key or None,
            timeout=60,
        )
    from .data.vector_store_qdrant import _LocalQdrantOwner

    path = str(space.persist_directory or "")
    if not path:
        raise ValueError("a qdrant space needs a host or a persist_directory")
    return _LocalQdrantOwner(lambda: QdrantClient(path=path))


def role_of(name: str) -> str:
    """The role a physical Qdrant collection plays, from its name."""
    from .reembed import parse_collection_name

    if name.endswith(_CLAUSE_SUFFIX):
        return "retrieval_clauses"
    if name.endswith(_TAG_SUFFIX):
        return "tag_index"
    parsed = parse_collection_name(name)
    return parsed[2] if parsed else "memory"


def parse_qdrant_collection_name(name: str) -> Optional[Tuple[str, Optional[int], str]]:
    """(base, dims, role) of a Qdrant collection, its two extra roles included."""
    from .reembed import parse_collection_name

    for suffix, role in ((_CLAUSE_SUFFIX, "retrieval_clauses"), (_TAG_SUFFIX, "tag_index")):
        if name.endswith(suffix):
            parsed = parse_collection_name(name[: -len(suffix)])
            if parsed is None or parsed[2] != "memory":
                return None
            return parsed[0], parsed[1], role
    return parse_collection_name(name)


def _point_id(value: str) -> Any:
    return int(value) if isinstance(value, str) and value.isdigit() else value


def _dense(vector: Any) -> Optional[List[float]]:
    if isinstance(vector, dict):
        vector = vector.get("")
    return list(vector) if vector is not None else None


class QdrantFacade:
    """The Chroma client calls `reembed` makes, over one Qdrant client."""

    def __init__(self, raw: Any, *, local: bool) -> None:
        self.raw = raw
        self.local = local

    def _names(self) -> List[str]:
        return [item.name for item in self.raw.get_collections().collections]

    def list_collections(self) -> List[str]:
        return self._names()

    def get_collection(self, name: str) -> "QdrantCollection":
        if not self.raw.collection_exists(collection_name=name):
            raise NotFoundError(f"collection {name} does not exist")
        return QdrantCollection(self, name)

    def delete_collection(self, name: str) -> None:
        self.raw.delete_collection(collection_name=name)

    def create_like(
        self, name: str, source: "QdrantCollection", dims: int,
    ) -> "QdrantCollection":
        """The target collection, created like ``source`` at ``dims``; kept if it exists."""
        from qdrant_client.models import Distance, VectorParams

        if self.raw.collection_exists(collection_name=name):
            return QdrantCollection(self, name)
        info = source.info()
        params = info.config.params
        vectors = params.vectors
        if isinstance(vectors, dict):
            raise NotImplementedError(
                f"{source.name}: named dense vectors are not written by the SDK store"
            )
        distance = getattr(vectors, "distance", None) or Distance.COSINE
        kwargs: Dict[str, Any] = {
            "collection_name": name,
            "vectors_config": VectorParams(size=int(dims), distance=distance),
        }
        if params.sparse_vectors:
            kwargs["sparse_vectors_config"] = dict(params.sparse_vectors)
        self.raw.create_collection(**kwargs)
        if not self.local:
            for field_name, schema in (info.payload_schema or {}).items():
                self.raw.create_payload_index(
                    collection_name=name,
                    field_name=field_name,
                    field_schema=schema.params or schema.data_type,
                )
        return QdrantCollection(self, name)


class QdrantCollection:
    """The Chroma collection calls `reembed` makes, over one Qdrant collection."""

    def __init__(self, facade: QdrantFacade, name: str) -> None:
        self.facade = facade
        self.raw = facade.raw
        self.name = name
        self.role = role_of(name)
        #: Records carry no collection metadata in Qdrant: no model is recorded.
        self.metadata: Dict[str, Any] = {}
        self._cursors: Dict[int, Any] = {0: None}
        self._source: Optional[QdrantCollection] = None
        self._sparse: Optional[bool] = None

    # -- reading ---------------------------------------------------------

    def info(self) -> Any:
        return self.raw.get_collection(collection_name=self.name)

    def has_sparse(self) -> bool:
        if self._sparse is None:
            sparse = self.info().config.params.sparse_vectors
            self._sparse = bool(sparse) and _SPARSE in sparse
        return self._sparse

    def count(self) -> int:
        return int(self.raw.count(collection_name=self.name, exact=True).count)

    def get(
        self,
        ids: Optional[Sequence[str]] = None,
        *,
        limit: Optional[int] = None,
        offset: int = 0,
        include: Iterable[str] = (),
    ) -> Dict[str, Any]:
        include = list(include)
        with_payload = "documents" in include or "metadatas" in include
        with_vectors: Any = [""] if "embeddings" in include else False
        if self.has_sparse() and with_vectors:
            with_vectors = True
        if ids is not None:
            points = self.raw.retrieve(
                collection_name=self.name,
                ids=[_point_id(value) for value in ids],
                with_payload=with_payload,
                with_vectors=bool(with_vectors),
            )
        else:
            points = self._scroll(offset, limit or _SCROLL, with_payload, bool(with_vectors))
        return self._shape(points, include)

    def _scroll(self, offset: int, limit: int, payload: bool, vectors: bool) -> List[Any]:
        if offset not in self._cursors:
            # Not the page after the last one read: walk there from the start.
            self._cursors = {0: None}
            position = 0
            while position < offset:
                step = min(_SCROLL, offset - position)
                _, cursor = self.raw.scroll(
                    collection_name=self.name, limit=step,
                    offset=self._cursors[position], with_payload=False, with_vectors=False,
                )
                position += step
                self._cursors[position] = cursor
                if cursor is None:
                    return []
        cursor = self._cursors[offset]
        if offset and cursor is None:
            return []
        points, after = self.raw.scroll(
            collection_name=self.name, limit=limit, offset=cursor,
            with_payload=payload, with_vectors=vectors,
        )
        self._cursors[offset + len(points)] = after
        return list(points)

    def _shape(self, points: List[Any], include: List[str]) -> Dict[str, Any]:
        out: Dict[str, Any] = {"ids": [str(point.id) for point in points]}
        payloads = [dict(point.payload or {}) for point in points]
        if "metadatas" in include:
            out["metadatas"] = payloads
        if "documents" in include:
            out["documents"] = self._documents(payloads)
        if "embeddings" in include:
            out["embeddings"] = [_dense(point.vector) for point in points]
        return out

    def _documents(self, payloads: List[Dict[str, Any]]) -> List[Optional[str]]:
        if self.role == "entities":
            return [payload.get("data") for payload in payloads]
        if self.role == "tag_index":
            return [payload.get("tag") for payload in payloads]
        if self.role == "retrieval_clauses":
            return self._clause_texts(payloads)
        return [payload.get("content") for payload in payloads]

    def _clause_texts(self, payloads: List[Dict[str, Any]]) -> List[Optional[str]]:
        from .data.vector_store_base import VectorStoreBase
        from .pipelines._retrieval.retrieval_clauses import split_retrieval_clauses

        parent_name = self.name[: -len(_CLAUSE_SUFFIX)]
        wanted = sorted({
            VectorStoreBase._node_id_to_point_id(str(payload.get("parent_id")))
            for payload in payloads if payload.get("parent_id")
        })
        contents: Dict[str, str] = {}
        if wanted and self.raw.collection_exists(collection_name=parent_name):
            for point in self.raw.retrieve(
                collection_name=parent_name, ids=wanted, with_payload=True, with_vectors=False,
            ):
                payload = point.payload or {}
                contents[str(payload.get("node_id") or "")] = str(payload.get("content") or "")
        out: List[Optional[str]] = []
        for payload in payloads:
            clauses = split_retrieval_clauses(contents.get(str(payload.get("parent_id")), ""))
            index = payload.get("clause_index")
            out.append(
                clauses[index]
                if isinstance(index, int) and 0 <= index < len(clauses)
                else None
            )
        return out

    # -- writing (targets only) -------------------------------------------

    def bind_source(self, source: Any) -> None:
        """The collection this target is filled from: its sparse vectors are kept."""
        self._source = source if isinstance(source, QdrantCollection) else None

    def upsert(
        self,
        *,
        ids: Sequence[str],
        embeddings: Sequence[Sequence[float]],
        documents: Sequence[Optional[str]] = (),
        metadatas: Sequence[Optional[Dict[str, Any]]] = (),
    ) -> None:
        from qdrant_client.models import PointStruct

        del documents  # derived from the payload, never stored apart from it
        sparse: Dict[str, Any] = {}
        if self.has_sparse() and self._source is not None and self._source.has_sparse():
            for point in self.raw.retrieve(
                collection_name=self._source.name,
                ids=[_point_id(value) for value in ids],
                with_payload=False,
                with_vectors=[_SPARSE],
            ):
                vector = point.vector if isinstance(point.vector, dict) else {}
                if vector.get(_SPARSE) is not None:
                    sparse[str(point.id)] = vector[_SPARSE]
        points = []
        for rid, dense, payload in zip(ids, embeddings, metadatas):
            vector: Any = list(dense)
            if self.has_sparse():
                vector = {"": list(dense)}
                if rid in sparse:
                    vector[_SPARSE] = sparse[rid]
            points.append(PointStruct(id=_point_id(rid), vector=vector, payload=dict(payload or {})))
        self.raw.upsert(collection_name=self.name, points=points, wait=True)

    # -- querying ----------------------------------------------------------

    def query(
        self, *, query_embeddings: Sequence[Sequence[float]], n_results: int, include: Any = (),
    ) -> Dict[str, Any]:
        del include
        ids = []
        for vector in query_embeddings:
            found = self.raw.query_points(
                collection_name=self.name, query=list(vector), limit=int(n_results),
                with_payload=False, with_vectors=False,
            ).points
            ids.append([str(point.id) for point in found])
        return {"ids": ids}
