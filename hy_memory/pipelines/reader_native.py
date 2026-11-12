"""Read-only native retrieval using the factory's shared data and embed services."""

import time

from .base import ReadPipeline, ReadResponse
from ._retrieval.config import VDB_RECALL_LAYERS
from ._retrieval.evolution import resolve_native_hits
from ..data.vector_store_base import NativeQuery
from ..retrieval import NativeRetrievalError


class NativeReadPipeline(ReadPipeline):
    """Qdrant Server native ranking; no legacy postprocessing or resource ownership."""

    VERSION = "native"

    def __init__(self, config, embed_service, vector_store):
        self.config = config
        self.embed_service = embed_service
        self.vector_store = vector_store

    async def initialize(self):
        # Registry owns initialization of the shared store. Preparation is explicit.
        if self.vector_store is None:
            raise NativeRetrievalError("unsupported", "native reader requires a vector store")
        if not getattr(self.vector_store, "_client", None):
            await self.vector_store.initialize()

    async def close(self):
        """Shared resources are closed by their owner."""

    async def read(self, request, ctx=None, tracer=None):
        from ..models.memory import MemoryLayer
        from ..data.qdrant_native import validate_threshold, validate_query
        started = time.perf_counter()
        users = request.user_ids or ([request.user_id] if request.user_id else [])
        if type(request.limit) is not int or not 0 <= request.limit <= 100:
            raise NativeRetrievalError("invalid_request", "native public limit must be 0..100")
        validate_threshold(request.native_threshold, request.native_mode)
        raw = request.raw_query if request.raw_query is not None else request.query
        layers = [MemoryLayer(layer) for layer in request.layers] if request.layers else list(VDB_RECALL_LAYERS)
        plan = NativeQuery(
            texts=[raw], embeddings=[], user_ids=users, layers=layers,
            mode=request.native_mode, limit=max(30, 3 * request.limit),
            agent_ids=request.agent_ids or ([request.agent_id] if request.agent_id else []),
            session_ids=request.session_ids or ([request.session_id] if request.session_id else []),
            include_sources=request.include_sources, exclude_sources=request.exclude_sources,
        )
        # Validate scope and mode even for skipped requests, before embedding.
        validation = NativeQuery(**{**vars(plan), "mode": "keyword", "texts": [raw or "validation"]})
        validate_query(validation)
        if request.native_mode not in ("dense", "keyword", "hybrid"):
            raise NativeRetrievalError("invalid_request", "unknown native mode")
        if not raw.strip() or request.limit == 0:
            return ReadResponse(success=True, extra={"native": {"skipped": True}})
        if request.native_mode != "keyword":
            embedding = request.query_embedding
            if embedding is None:
                embedding = await self.embed_service.embed(request.query)
            plan.embeddings = [embedding]
        hits = await self.vector_store.native_query(plan)
        resolved = await resolve_native_hits(self.vector_store, hits, plan, created_after=request.created_after)
        threshold = request.native_threshold
        memories = []
        for hit in resolved:
            if threshold is not None and not hit["score"] > threshold.value:
                continue
            node = hit["node"]
            memories.append({
                "memory_id": node.node_id, "content": node.content, "layer": node.layer.value,
                "score": hit["score"], "score_kind": hit["score_kind"],
                "mode": request.native_mode, "config_id": hit["config_id"],
                "matched_id": hit["matched_id"], "user_id": node.user_id,
                "agent_id": node.agent_id, "session_id": node.session_id,
                "source": node.source, "tags": list(node.tags or []),
                "memory_at": node.memory_at.timestamp() if node.memory_at else None,
                "evolution_chain": hit["evolution_chain"], "is_evolved": hit["is_evolved"],
            })
            if len(memories) >= request.limit:
                break
        return ReadResponse(success=True, memories=memories, total_found=len(memories),
                            elapsed_ms=(time.perf_counter() - started) * 1000,
                            extra={"native": {"skipped": False, "mode": request.native_mode}})
