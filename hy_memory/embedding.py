"""Public structural contract for injected embedding services."""

from typing import Any, Dict, List, Protocol, Tuple, runtime_checkable


@runtime_checkable
class EmbeddingService(Protocol):
    """Embedding service accepted by :class:`HyMemoryClient`.

    Implementations are borrowed by the client: their lifecycle remains owned
    by the caller and ``HyMemoryClient.close()`` will not close them.
    """

    async def embed(self, text: str, use_cache: bool = True) -> List[float]:
        ...

    async def embed_with_meta(
        self, text: str, use_cache: bool = True
    ) -> Tuple[List[float], Dict[str, Any]]:
        ...

    async def embed_batch(
        self, texts: List[str], use_cache: bool = True
    ) -> List[List[float]]:
        ...

    async def embed_queued(
        self, text: str, use_cache: bool = True
    ) -> List[float]:
        ...

    async def embed_queued_with_meta(
        self, text: str, use_cache: bool = True
    ) -> Tuple[List[float], Dict[str, Any]]:
        ...

    def get_embedding_dims(self) -> int:
        ...


# Explicit alias for callers that prefer the conventional Protocol suffix.
EmbeddingServiceProtocol = EmbeddingService


class EmbeddingDimensionMismatchError(ValueError):
    """Raised before initialization when service and store dimensions differ."""


__all__ = [
    "EmbeddingService",
    "EmbeddingServiceProtocol",
    "EmbeddingDimensionMismatchError",
]
