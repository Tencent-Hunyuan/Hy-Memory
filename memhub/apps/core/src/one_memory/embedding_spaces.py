"""Which embedding space the home reads and writes: an explicit registry.

The SDK names its vector collections after `vector_store.collection_name`
and the configured width, so the space used to follow whatever the active
profile hashed to (`_collection_base`). Any change to that identity -- a
probe recording the measured width, a new endpoint -- silently opened new,
empty collections beside the old ones, and nothing carried the memories over.

The live space is pinned here instead, in the main Core database beside the
model profiles, so a switch commits the new space and the profile that fills
it in one transaction. The identity of a space is its embedding model and
width only: a new endpoint or provider serving the same model at the same
width keeps the space.

Existing homes keep their collections byte-for-byte. The first start registers
the space the active profile (or the built-in model) already opened under its
existing four-attribute name; only spaces created by a switch use the
model-and-width name. A space whose width was never declared ("auto") keeps
its unsuffixed name and only records the width it was measured at.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from one_memory.database import take_the_write_lock_first
from one_memory.database_access import main_database
from one_memory.embedding_assets import BUILTIN_EMBEDDING_DIMENSIONS, BUILTIN_EMBEDDING_MODEL
from one_memory.memory_server.migrations import embedding_spaces_ddl
from one_memory.model_profiles import ModelProfileSnapshot, ProviderKind

#: How long a space replaced by a switch or folded in by a merge is kept.
RETIRED_SPACE_RETENTION = timedelta(days=14)

SpaceState = Literal["live", "building", "stray", "retired", "dropped"]
SpaceNaming = Literal["legacy", "v2", "discovered"]

#: Every vector collection one space consists of, as the SDK names them.
VECTOR_ROLES = (
    "memory",
    "entities",
    "coding_keys",
    "coding_keys_cv",
    "working_facts",
    "working_facts_cv",
)

#: The graph directory of the first space; later spaces get their own.
DEFAULT_GRAPH_DIR = "graph"

EMBEDDING_SPACES_SCHEMA = embedding_spaces_ddl()


#: Bumped by every registry write in this process, so cached live rows are
#: reread after a change made through any connection (a probe's, a commit's).
_CHANGES = [0]


def _changed() -> None:
    _CHANGES[0] += 1


class EmbeddingSpaceError(RuntimeError):
    """A registry refusal with a stable, product-facing code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class EmbeddingIdentity(BaseModel):
    """What makes two vectors comparable: the model and the width.

    `dims=None` is a width nobody declared or measured yet; it matches any
    width, because the collection it names was opened without one.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str
    dims: int | None = None

    def matches(self, other: EmbeddingIdentity) -> bool:
        if self.model != other.model:
            return False
        return self.dims is None or other.dims is None or self.dims == other.dims


class EmbeddingSpace(BaseModel):
    """One embedding space: the physical collections and graph one model fills."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    space_id: str
    state: SpaceState
    #: None only for a space found on disk that no registry row named.
    model: str | None = None
    #: The vector width stored in this space, once known.
    dims: int | None = None
    #: The width the collection names and embedding requests carry. None is
    #: an "auto" space: unsuffixed names and no `dimensions` request field.
    name_dims: int | None = None
    collection_base: str
    naming: SpaceNaming = "legacy"
    graph_dir: str | None = DEFAULT_GRAPH_DIR
    provider_kind: str | None = None
    #: The profile version whose embedding fills this space; None = built-in.
    profile_id: str | None = None
    profile_revision: int | None = None
    #: Vectors counted the last time the space was scanned.
    vectors: int | None = None
    created_at: str
    live_since: str | None = None
    retired_at: str | None = None
    keep_until: str | None = None
    dropped_at: str | None = None

    @property
    def identity(self) -> EmbeddingIdentity | None:
        if self.model is None:
            return None
        return EmbeddingIdentity(model=self.model, dims=self.dims)

    @property
    def builtin(self) -> bool:
        return self.provider_kind == ProviderKind.LOCAL_ONNX.value and self.profile_id is None

    def collection_names(self) -> dict[str, str]:
        return space_collection_names(self.collection_base, self.name_dims)


def space_collection_names(base: str, name_dims: int | None) -> dict[str, str]:
    """The SDK's physical collection name per role for one space.

    Mirrors `VectorStoreBase` (the `_<dims>` suffix), the entity store and the
    coding stores. The SDK's own `vector_space_names` is checked against this
    in the migration adapter when the installed SDK provides it.
    """
    suffix = f"_{name_dims}" if name_dims else ""
    return {
        "memory": f"{base}{suffix}",
        "entities": f"{base}{suffix}_entities",
        "coding_keys": f"{base}_coding_keys{suffix}",
        "coding_keys_cv": f"{base}_coding_keys_cv{suffix}",
        "working_facts": f"{base}_working_facts{suffix}",
        "working_facts_cv": f"{base}_working_facts_cv{suffix}",
    }


def embedding_identity(profile: ModelProfileSnapshot) -> EmbeddingIdentity:
    """The embedding identity a profile asks for: model name and width only."""
    provider = profile.embedding_provider_kind or profile.provider_kind
    local = provider is ProviderKind.LOCAL_ONNX
    model = profile.embedding_model or (BUILTIN_EMBEDDING_MODEL if local else None)
    if not model:
        raise EmbeddingSpaceError("hy_sdk_embedding_model_required")
    dims = profile.embedding_dimensions
    if local and dims is None:
        dims = BUILTIN_EMBEDDING_DIMENSIONS
    return EmbeddingIdentity(model=model, dims=dims)


def v2_collection_base(identity: EmbeddingIdentity, generation: int = 0) -> str:
    """The base name a switch gives a new space: model and width, nothing else."""
    digest = hashlib.sha256(f"{identity.model}\0{identity.dims or 'auto'}".encode()).hexdigest()
    base = f"memhub_e_{digest[:16]}"
    return f"{base}_g{generation}" if generation else base


def graph_dir_for(space_id: str) -> str:
    return f"graph-{space_id[:8]}"


def derived_space(profile: ModelProfileSnapshot) -> EmbeddingSpace:
    """The space `profile` opened before the registry existed, under its old name.

    Never stored as is: bootstrap stores it, and offline tools that run beside
    a home with no registry yet read through it.
    """
    from one_memory.hy_sdk_runtime import legacy_collection_base

    provider = profile.embedding_provider_kind or profile.provider_kind
    identity = embedding_identity(profile)
    builtin = profile.profile_id == _BUILTIN_PROFILE_ID
    return EmbeddingSpace(
        space_id="derived",
        state="live",
        model=identity.model,
        dims=identity.dims,
        name_dims=identity.dims,
        collection_base=legacy_collection_base(profile),
        naming="legacy",
        graph_dir=DEFAULT_GRAPH_DIR,
        provider_kind=provider.value,
        profile_id=None if builtin else profile.profile_id,
        profile_revision=None if builtin else profile.revision,
        created_at=_now(),
    )


_BUILTIN_PROFILE_ID = "builtin-retrieval"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _row(row: sqlite3.Row | Mapping[str, object]) -> EmbeddingSpace:
    return EmbeddingSpace.model_validate(dict(row))


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(EMBEDDING_SPACES_SCHEMA)


def registry_present(connection: sqlite3.Connection) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='embedding_spaces'"
        ).fetchone()
        is not None
    )


def live_space_in(connection: sqlite3.Connection) -> EmbeddingSpace | None:
    if not registry_present(connection):
        return None
    previous = connection.row_factory
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute("SELECT * FROM embedding_spaces WHERE state='live'").fetchone()
    finally:
        connection.row_factory = previous
    return _row(row) if row is not None else None


def read_live_space(database_path: Path) -> EmbeddingSpace | None:
    """The live space, read only, or None for a home with no registry yet."""
    if not database_path.is_file():
        return None
    with main_database(database_path).read(row_factory=sqlite3.Row) as connection:
        return live_space_in(connection)


class EmbeddingSpaceStore:
    """The `embedding_spaces` table of the main Core database."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        #: The live row, read once per change: every read and write asks for
        #: it. Only this process writes the registry while Core serves.
        self._live_cache: tuple[int, EmbeddingSpace] | None = None
        with self._connect(writing=True) as connection:
            ensure_schema(connection)

    def invalidate(self) -> None:
        _changed()

    @contextmanager
    def _connect(
        self, *, writing: bool = False, lock_first: bool = True
    ) -> Iterator[sqlite3.Connection]:
        database = main_database(self.database_path)
        checkout = (
            database.write(row_factory=sqlite3.Row)
            if writing
            else database.read(row_factory=sqlite3.Row)
        )
        with checkout as connection, connection:
            if writing and lock_first:
                take_the_write_lock_first(connection)
            yield connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """One `BEGIN IMMEDIATE` on the main database, committed on success."""
        _changed()
        try:
            with self._connect(writing=True, lock_first=False) as connection:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
        finally:
            _changed()

    def live(self) -> EmbeddingSpace | None:
        generation = _CHANGES[0]
        cached = self._live_cache
        if cached is not None and cached[0] == generation:
            return cached[1]
        with self._connect() as connection:
            space = live_space_in(connection)
        self._live_cache = (generation, space) if space is not None else None
        return space

    def require_live(self) -> EmbeddingSpace:
        space = self.live()
        if space is None:
            raise EmbeddingSpaceError("embedding_space_not_bootstrapped")
        return space

    def get(self, space_id: str) -> EmbeddingSpace | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM embedding_spaces WHERE space_id=?", (space_id,)
            ).fetchone()
        return _row(row) if row is not None else None

    def list_spaces(self, *, include_dropped: bool = False) -> list[EmbeddingSpace]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM embedding_spaces "
                + ("" if include_dropped else "WHERE state != 'dropped' ")
                + "ORDER BY created_at, space_id"
            ).fetchall()
        return [_row(row) for row in rows]

    def bootstrap(self, active: ModelProfileSnapshot | None) -> EmbeddingSpace:
        """Register the space this home already uses, once, under its existing name.

        `active` is the active profile, or None for the built-in model. The
        collection name is derived exactly as before the registry, so nothing
        on disk moves.
        """
        from one_memory.hy_sdk_runtime import builtin_retrieval_snapshot

        with self.transaction() as connection:
            existing = live_space_in(connection)
            if existing is not None:
                return existing
            derived = derived_space(active or builtin_retrieval_snapshot())
            space = derived.model_copy(
                update={"space_id": str(uuid4()), "live_since": derived.created_at}
            )
            _insert(connection, space)
            return space

    def record_width(self, space_id: str, dims: int) -> EmbeddingSpace:
        """A relabel: the space's stored width is now known. Names never change."""
        with self.transaction() as connection:
            record_width_in(connection, space_id, dims)
        space = self.get(space_id)
        if space is None:  # pragma: no cover - the row was just updated
            raise EmbeddingSpaceError("embedding_space_not_found")
        return space

    def record_vectors(self, counts: Mapping[str, int]) -> None:
        """Cache the vector count per space id from the last scan (display only)."""
        if not counts:
            return
        _changed()
        with self._connect(writing=True) as connection:
            connection.executemany(
                "UPDATE embedding_spaces SET vectors=? WHERE space_id=?",
                [(count, space_id) for space_id, count in counts.items()],
            )

    def register_stray(
        self,
        *,
        collection_base: str,
        name_dims: int | None,
        dims: int | None,
        vectors: int,
    ) -> EmbeddingSpace:
        """Name a non-empty space the scan found that no row described."""
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM embedding_spaces WHERE collection_base=? "
                "AND COALESCE(name_dims,0)=? AND state != 'dropped'",
                (collection_base, name_dims or 0),
            ).fetchone()
            if row is not None:
                return _row(row)
            space = EmbeddingSpace(
                space_id=str(uuid4()),
                state="stray",
                dims=dims,
                name_dims=name_dims,
                collection_base=collection_base,
                naming="discovered",
                graph_dir=None,
                vectors=vectors,
                created_at=_now(),
            )
            _insert(connection, space)
            return space

    def mark_dropped(self, space_id: str) -> None:
        _changed()
        with self._connect(writing=True) as connection:
            connection.execute(
                "UPDATE embedding_spaces SET state='dropped', dropped_at=? "
                "WHERE space_id=? AND state IN ('retired','building')",
                (_now(), space_id),
            )

    def expired(self, *, now: datetime | None = None) -> list[EmbeddingSpace]:
        moment = (now or datetime.now(UTC)).isoformat()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM embedding_spaces WHERE state='retired' "
                "AND keep_until IS NOT NULL AND keep_until <= ?",
                (moment,),
            ).fetchall()
        return [_row(row) for row in rows]


def registry_for(profiles: object) -> EmbeddingSpaceStore | None:
    """The registry beside a profile store, or None for a store with no database."""
    path = getattr(profiles, "database_path", None)
    return EmbeddingSpaceStore(path) if isinstance(path, Path) else None


def _insert(connection: sqlite3.Connection, space: EmbeddingSpace) -> None:
    values = space.model_dump()
    columns = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    connection.execute(
        f"INSERT INTO embedding_spaces ({columns}) VALUES ({marks})",
        tuple(values.values()),
    )


def insert_space_in(connection: sqlite3.Connection, space: EmbeddingSpace) -> None:
    _changed()
    _insert(connection, space)


def record_width_in(connection: sqlite3.Connection, space_id: str, dims: int) -> None:
    _changed()
    changed = connection.execute(
        "UPDATE embedding_spaces SET dims=? WHERE space_id=? AND (dims IS NULL OR dims=?)",
        (dims, space_id, dims),
    )
    if changed.rowcount != 1:
        raise EmbeddingSpaceError("embedding_space_width_conflict")


def space_in(connection: sqlite3.Connection, space_id: str) -> EmbeddingSpace | None:
    previous = connection.row_factory
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT * FROM embedding_spaces WHERE space_id=?", (space_id,)
        ).fetchone()
    finally:
        connection.row_factory = previous
    return _row(row) if row is not None else None


def spaces_in(connection: sqlite3.Connection) -> list[EmbeddingSpace]:
    previous = connection.row_factory
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute("SELECT * FROM embedding_spaces").fetchall()
    finally:
        connection.row_factory = previous
    return [_row(row) for row in rows]


def set_state_in(
    connection: sqlite3.Connection,
    space_id: str,
    *,
    state: SpaceState,
    expected: tuple[SpaceState, ...],
    now: datetime | None = None,
) -> None:
    """Move one space between states, refusing any it was not expected in."""
    _changed()
    moment = now or datetime.now(UTC)
    updates: dict[str, object] = {"state": state}
    if state == "live":
        updates["live_since"] = moment.isoformat()
    elif state == "retired":
        updates["retired_at"] = moment.isoformat()
        updates["keep_until"] = (moment + RETIRED_SPACE_RETENTION).isoformat()
    elif state == "dropped":
        updates["dropped_at"] = moment.isoformat()
    assignments = ", ".join(f"{column}=?" for column in updates)
    marks = ",".join("?" for _ in expected)
    changed = connection.execute(
        f"UPDATE embedding_spaces SET {assignments} WHERE space_id=? AND state IN ({marks})",
        (*updates.values(), space_id, *expected),
    )
    if changed.rowcount != 1:
        raise EmbeddingSpaceError("embedding_space_state_changed")


def next_v2_base(
    connection: sqlite3.Connection,
    identity: EmbeddingIdentity,
    *,
    stored_names: frozenset[str],
) -> str:
    """A base for a new space no registry row and no stored collection uses.

    Switching back to a model whose retired space is still kept gets a fresh
    `_g<n>` base, so stale retired vectors are never mixed with fresh ones.
    """
    used = {space.collection_base for space in spaces_in(connection)}
    generation = 0
    while True:
        base = v2_collection_base(identity, generation)
        names = space_collection_names(base, identity.dims).values()
        if base not in used and not any(name in stored_names for name in names):
            return base
        generation += 1


def probe_width_allowed(
    connection: sqlite3.Connection,
    profile_id: str,
    width: int,
    *,
    data_dir: Path,
) -> bool:
    """Whether a probe may record `width` on a profile without moving the space.

    A profile that fills the live space (the active one, or the one the space
    was built with) may record its measured width only when it is the
    space's: equal to the width already known, or -- for an "auto" space
    whose width was never recorded -- equal to the width Chroma stored, or
    any width when nothing is stored yet. That is a relabel; the names never
    change. Any other width needs a switch (B2: it used to silently open a
    new, empty collection). Every other profile records what it measured.
    """
    live = live_space_in(connection)
    if live is None:
        return True
    active = connection.execute(
        "SELECT profile_id FROM model_profiles WHERE is_active=1"
    ).fetchone()
    fills_live = live.profile_id == profile_id or (active is not None and active[0] == profile_id)
    if not fills_live:
        return True
    if live.dims is not None:
        return live.dims == width
    from one_memory.sdk_vector_reattach import stored_collection_width

    stored = stored_collection_width(data_dir, live.collection_names()["memory"])
    if stored is not None and stored != width:
        return False
    record_width_in(connection, live.space_id, width)
    return True
