"""What the embedding switch needs from the SDK's re-embed module, in Core terms.

The switch worker, the startup merge and the retention sweep only talk to a
`ReembedBackend`. The production one (`hy_sdk_migration.SdkReembedBackend`)
is the single place that calls `hy_memory`'s re-embed functions; tests use
an in-memory one. Every space is named explicitly -- collection names and
graph paths -- and nothing here derives a name from a provider or endpoint.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from one_memory.embedding_spaces import EmbeddingSpace, space_collection_names
from one_memory.model_profiles import ModelProfileSnapshot


class ReembedUnavailableError(RuntimeError):
    """The installed SDK has no re-embed module (before hy-memory 2.0.10)."""

    def __init__(self, code: str = "sdk_reembed_unavailable") -> None:
        super().__init__(code)
        self.code = code


class ReembedCancelledError(RuntimeError):
    """A cancel was seen at a batch boundary; every finished batch is written."""


class EmbeddingWidthError(RuntimeError):
    """The embedding service returned vectors of another width than the target's."""


@dataclass(frozen=True)
class PhysicalSpace:
    """One space's collections, named explicitly."""

    collection_base: str
    name_dims: int | None
    #: The width of the vectors in it, or to be written to it.
    dims: int | None
    model: str | None = None
    #: Explicit role -> collection name; None derives them from the base.
    collections: Mapping[str, str] | None = None

    def names(self) -> dict[str, str]:
        if self.collections is not None:
            return dict(self.collections)
        return space_collection_names(self.collection_base, self.name_dims)

    @classmethod
    def of(cls, space: EmbeddingSpace) -> PhysicalSpace:
        return cls(
            collection_base=space.collection_base,
            name_dims=space.name_dims,
            dims=space.dims if space.dims is not None else space.name_dims,
            model=space.model,
        )


@dataclass(frozen=True)
class ScannedSpace:
    """A space found in the vector store, grouped from its collection names."""

    collection_base: str
    name_dims: int | None
    #: The stored width when every non-empty role agrees.
    width: int | None
    counts: Mapping[str, int]
    collections: Mapping[str, str]
    per_user: Mapping[str, int] = field(default_factory=dict)

    @property
    def vectors(self) -> int:
        return sum(self.counts.values())

    @property
    def key(self) -> tuple[str, int]:
        return (self.collection_base, self.name_dims or 0)

    def physical(self) -> PhysicalSpace:
        return PhysicalSpace(
            collection_base=self.collection_base,
            name_dims=self.name_dims,
            dims=self.width if self.width is not None else self.name_dims,
            collections=dict(self.collections),
        )


@dataclass(frozen=True)
class ReembedStep:
    """Progress of one role (vectors) or table (graph)."""

    phase: str
    role: str
    done: int
    total: int
    embedded: int = 0
    reused: int = 0
    kept: int = 0


@dataclass(frozen=True)
class VectorCheck:
    per_role: Mapping[str, Mapping[str, int]]
    #: Target records per metadata `user_id`.
    per_user: Mapping[str, int]
    problems: tuple[str, ...]
    warnings: tuple[str, ...] = ()
    self_recall_hits: int = 0
    self_recall_samples: int = 0


@dataclass(frozen=True)
class GraphCheck:
    ok: bool
    problems: tuple[str, ...] = ()


@dataclass(frozen=True)
class EmbeddingTarget:
    """The embedding that fills a space: the profile to reach it, and its key."""

    profile: ModelProfileSnapshot
    space: EmbeddingSpace
    api_key: str | None


Cancel = Callable[[], bool]
OnStep = Callable[[ReembedStep], None]


class ReembedBackend(Protocol):
    def unavailable_reason(self) -> str | None:
        """None when re-embedding works; otherwise the code that says why not."""

    async def scan(self, *, per_user: bool = False) -> tuple[ScannedSpace, ...]:
        """Every space with collections in the home's vector store. Read-only."""

    async def diff(self, source: PhysicalSpace, target: PhysicalSpace) -> dict[str, int]:
        """Per role, how many ids the source holds that the target does not."""

    async def reembed(
        self,
        source: PhysicalSpace,
        target: PhysicalSpace,
        *,
        embedding: EmbeddingTarget,
        cancel: Cancel,
        on_step: OnStep,
        batch_size: int = 64,
    ) -> None:
        """Embed every source record the target lacks into the target. Target wins."""

    async def verify(
        self, source: PhysicalSpace, target: PhysicalSpace, *, embedding: EmbeddingTarget
    ) -> VectorCheck: ...

    async def graph_vectors(self, path: Path) -> int | None:
        """Embeddings a graph holds, or None when there is no graph there."""

    async def reembed_graph(
        self,
        source: Path,
        target: Path,
        *,
        embedding: EmbeddingTarget,
        target_dims: int,
        cancel: Cancel,
        on_step: OnStep,
    ) -> None: ...

    async def verify_graph(self, source: Path, target: Path, *, target_dims: int) -> GraphCheck: ...

    async def drop(self, space: PhysicalSpace) -> None: ...

    async def drop_graph(self, path: Path) -> None: ...
