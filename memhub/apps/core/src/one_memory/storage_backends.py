"""Which graph and vector stores the SDK keeps this home's memories in.

One global, persistent Core setting (`config.json` `storage_backends`), set
only through the CLI and the HTTP API (`memhub settings backends`,
`/api/v1/settings/storage-backends`); the App shows it read-only. The default
is the embedded pair every MemHub has always used: Kuzu for the graph, Chroma
for vectors. Contract: docs/contracts/storage-backends-v1.md.

The setting is the only source. The SDK's own environment variables
(`MEMORY_GRAPH_PROVIDER`, `MEMORY_VECTOR_STORE`, ...) are still overridden by
`build_sdk_config`, so a stray variable never sends memories to a server
nobody chose (`test_hy_network_egress`).

Secrets never reach `config.json`: the graph password and the vector API key
live in the secret store under `GRAPH_SECRET_REF` / `VECTOR_SECRET_REF`.

Switching backends moves nothing. The memories already written stay in the
store they were written to (the old Kuzu/Chroma files are never deleted or
rewritten), and the new store starts as it is. Switching back shows them
again. MemHub's upgrade migrations do not run on a home with a custom
backend (`custom_backends_configured`).
"""

from __future__ import annotations

import json
import re
import secrets as _random
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypedDict
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

if TYPE_CHECKING:
    from one_memory.memory_server.config_state import ConfigStore
    from one_memory.secrets import SecretStore

GraphProvider = Literal["kuzu", "neo4j", "memgraph"]
VectorProvider = Literal["chroma", "qdrant", "faiss"]
GRAPH_PROVIDERS: tuple[GraphProvider, ...] = ("kuzu", "neo4j", "memgraph")
VECTOR_PROVIDERS: tuple[VectorProvider, ...] = ("chroma", "qdrant", "faiss")
REMOTE_GRAPH_PROVIDERS = frozenset({"neo4j", "memgraph"})

#: Secret-store references; the values never appear in config, logs or replies.
GRAPH_SECRET_REF = "storage-backend:graph-password"
VECTOR_SECRET_REF = "storage-backend:vector-api-key"

#: The bolt URL schemes the neo4j driver accepts (memgraph speaks bolt too).
_GRAPH_SCHEMES = frozenset({"bolt", "bolt+s", "bolt+ssc", "neo4j", "neo4j+s", "neo4j+ssc"})
_HOST = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$|^\[[0-9A-Fa-f:.]+\]$")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")
#: A collection prefix: lowercase, so it reads the same on every server.
_PREFIX = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

_LABELS: dict[str, str] = {
    "kuzu": "Kuzu",
    "neo4j": "Neo4j",
    "memgraph": "Memgraph",
    "chroma": "Chroma",
    "qdrant": "Qdrant",
    "faiss": "FAISS",
}

#: Refused by validation; see `VectorBackend`.
FAISS_UNSUPPORTED = "storage_backend_faiss_unsupported"


class StorageBackendError(ValueError):
    """A setting that cannot be used; `code` is the stable refusal."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class GraphBackend(BaseModel):
    """Where the ultra knowledge graph lives (only ultra opens a graph)."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    provider: GraphProvider = "kuzu"
    #: Bolt URL of a Neo4j / Memgraph server, e.g. `bolt://127.0.0.1:7687`.
    url: str | None = None
    username: str | None = None
    #: Neo4j database name; None = the server's default database.
    database: str | None = None

    @model_validator(mode="after")
    def _valid(self) -> GraphBackend:
        if self.provider == "kuzu":
            if self.url is not None or self.username is not None or self.database is not None:
                raise ValueError("storage_backend_graph_kuzu_takes_no_connection")
            return self
        if not self.url:
            raise ValueError("storage_backend_graph_url_required")
        if len(self.url) > 512:
            raise ValueError("storage_backend_graph_url_invalid")
        parts = urlsplit(self.url)
        if (
            parts.scheme not in _GRAPH_SCHEMES
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
            or parts.path not in {"", "/"}
        ):
            # Credentials belong in `username` and the secret store, never a URL.
            raise ValueError("storage_backend_graph_url_invalid")
        try:
            parts.port  # noqa: B018 - raises on a port out of range
        except ValueError:
            raise ValueError("storage_backend_graph_url_invalid") from None
        if self.username is not None and not (0 < len(self.username) <= 128):
            raise ValueError("storage_backend_graph_username_invalid")
        if self.database is not None and _NAME.fullmatch(self.database) is None:
            raise ValueError("storage_backend_graph_database_invalid")
        return self


class VectorBackend(BaseModel):
    """Where vectors live: chat memories, raw turns, Coding and Working keys."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    provider: VectorProvider = "chroma"
    #: Qdrant server host (required for qdrant: see `_valid`).
    host: str | None = None
    port: StrictInt | None = None
    #: Prepended to every collection this home opens on the server. None =
    #: the home's own `StorageBackends.namespace`, which is what keeps two
    #: homes on one server from reading or dropping each other's collections.
    collection_prefix: str | None = None

    @model_validator(mode="after")
    def _valid(self) -> VectorBackend:
        if self.provider == "chroma":
            if self.host is not None or self.port is not None or self.collection_prefix is not None:
                raise ValueError("storage_backend_vector_chroma_takes_no_connection")
            return self
        if self.provider == "faiss":
            # The SDK's FAISS store keeps its index in each client's memory and
            # rewrites the whole file on every write. MemHub always runs a read
            # client beside the write client (and Coding/Working stores beside
            # those), so they would overwrite each other's writes.
            raise ValueError(FAISS_UNSUPPORTED)
        if self.host is None:
            # Qdrant's embedded mode locks its directory to one client, and
            # MemHub runs several side by side: only a Qdrant server works.
            raise ValueError("storage_backend_vector_host_required")
        if len(self.host) > 253 or _HOST.fullmatch(self.host) is None:
            raise ValueError("storage_backend_vector_host_invalid")
        if self.port is not None and not 0 < self.port < 65536:
            raise ValueError("storage_backend_vector_port_invalid")
        if self.collection_prefix is not None and _PREFIX.fullmatch(self.collection_prefix) is None:
            raise ValueError("storage_backend_vector_prefix_invalid")
        return self

    @property
    def remote(self) -> bool:
        return self.host is not None


class StorageBackends(BaseModel):
    """`config.json` `storage_backends`: the pair the SDK is configured with."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    graph: GraphBackend = Field(default_factory=GraphBackend)
    vector: VectorBackend = Field(default_factory=VectorBackend)
    #: This home's collection prefix on a shared vector server: `mh` and 8
    #: random hex characters, chosen on the first `set` and kept for good,
    #: through `reset` too, so setting the same server again finds the same
    #: collections (`with_namespace`).
    namespace: str | None = None

    @model_validator(mode="after")
    def _valid_namespace(self) -> StorageBackends:
        if self.namespace is not None and _PREFIX.fullmatch(self.namespace) is None:
            raise ValueError("storage_backend_namespace_invalid")
        return self

    @property
    def is_default(self) -> bool:
        return self.graph.provider == "kuzu" and self.vector.provider == "chroma"

    @property
    def label(self) -> str:
        """`Neo4j + Qdrant`: graph first, as the App's read-only line shows it."""
        return f"{_LABELS[self.graph.provider]} + {_LABELS[self.vector.provider]}"

    @property
    def collection_prefix(self) -> str | None:
        """The prefix a Qdrant server's collections get: explicit, else the home's."""
        if self.vector.provider == "chroma":
            return None
        return self.vector.collection_prefix or self.namespace

    def with_namespace(self) -> StorageBackends:
        """The same setting with this home's namespace, generated once if missing."""
        if self.namespace is not None:
            return self
        return self.model_copy(update={"namespace": f"mh{_random.token_hex(4)}"})


DEFAULT_STORAGE_BACKENDS = StorageBackends()


@dataclass(frozen=True)
class ResolvedStorage:
    """A non-default setting with its secrets, as an SDK client is built with it.

    Hashable, so it is part of every client handle key: any change, a new
    password included, builds new clients and retires the old ones exactly as
    a mode or feature change does. Secrets are kept out of `repr`.
    """

    graph_provider: GraphProvider
    vector_provider: VectorProvider
    graph_url: str | None = None
    graph_username: str | None = None
    graph_database: str | None = None
    vector_host: str | None = None
    vector_port: int | None = None
    vector_prefix: str | None = None
    graph_password: str | None = field(default=None, repr=False)
    vector_api_key: str | None = field(default=None, repr=False)

    @property
    def graph_is_remote(self) -> bool:
        return self.graph_provider in REMOTE_GRAPH_PROVIDERS

    @property
    def vector_is_custom(self) -> bool:
        return self.vector_provider != "chroma"

    def collection_name(self, collection_base: str) -> str:
        """The physical base name this home opens for an embedding space."""
        if self.vector_provider == "chroma" or not self.vector_prefix:
            return collection_base
        return f"{self.vector_prefix}_{collection_base}"


def resolve_storage(
    settings: StorageBackends,
    *,
    graph_password: str | None,
    vector_api_key: str | None,
) -> ResolvedStorage | None:
    """None for the default pair: callers then build exactly what they always did."""
    if settings.is_default:
        return None
    graph, vector = settings.graph, settings.vector
    return ResolvedStorage(
        graph_provider=graph.provider,
        vector_provider=vector.provider,
        graph_url=graph.url,
        graph_username=graph.username,
        graph_database=graph.database,
        vector_host=vector.host,
        vector_port=(vector.port or 6333) if vector.host else None,
        vector_prefix=settings.collection_prefix,
        graph_password=graph_password if graph.provider != "kuzu" else None,
        vector_api_key=vector_api_key if vector.remote else None,
    )


class StorageKwargs(TypedDict, total=False):
    storage: ResolvedStorage


def storage_kwargs(storage: ResolvedStorage | None) -> StorageKwargs:
    """`storage=` for a client constructor, only for a custom backend.

    Like `language_kwargs`: with the default pair every call is exactly the
    call it was before the setting existed, so stand-ins keep working.
    """
    return {"storage": storage} if storage is not None else {}


def custom_backends_configured(data_dir: Path) -> bool:
    """Whether this home's `config.json` selects a backend other than Kuzu + Chroma.

    Read straight from the file, without a `ConfigStore`, for the offline
    upgrade path (`sdk-migrate`, `sdk-migration-status`): those never touch
    a custom-backend home. An unreadable file is not proof of a custom
    backend; the migration's own reads report it.
    """
    try:
        document = json.loads((data_dir / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    value = document.get("storage_backends") if isinstance(document, dict) else None
    if not isinstance(value, dict):
        return False
    try:
        return not StorageBackends.model_validate(value).is_default
    except ValueError:
        # A setting this build cannot read is still not the default pair:
        # leave the home alone rather than migrate it on a guess.
        return True


# ----------------------------------------------------------------------
# What works where
# ----------------------------------------------------------------------

CapabilityState = Literal["works", "skipped", "refused"]


class StorageCapability(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    state: CapabilityState
    #: The stable refusal or skip code; None when the feature works.
    code: str | None = None


#: Embedding switch on a vector store the SDK cannot re-embed (Chroma only, as
#: of hy-memory 2.0.15), or a remote graph in ultra (one fixed-width index).
EMBEDDING_SWITCH_UNSUPPORTED = "embedding_switch_backend_unsupported"
#: `graph-width` export/rebuild copy or rename the Kuzu file; a server has none.
GRAPH_EXPORT_UNSUPPORTED = "graph_export_backend_unsupported"
#: Only a Kuzu file has a width MemHub can read before the SDK opens it.
GRAPH_WIDTH_SKIPPED = "graph_width_check_not_kuzu"
#: The startup open probe and quarantine (#413) guard a Kuzu file only.
GRAPH_GUARD_SKIPPED = "graph_open_guard_not_kuzu"
#: The startup stray-space scan and merge read the home's Chroma store.
SCAN_SKIPPED = "embedding_scan_skipped_custom_backend"
MIGRATION_SKIPPED = "upgrade_migration_skipped_custom_backend"
#: Restoring a backup's memories onto a Qdrant server (`memory_backup`).
RESTORE_REMOTE_UNSUPPORTED = "backup_restore_remote_sdk_unsupported"


def embedding_switch_refusal(settings: StorageBackends, *, mode: str) -> str | None:
    """Why an embedding switch cannot run on these backends now; None if it can.

    The SDK's re-embed copies Chroma collections and Kuzu graphs only. A
    Neo4j/Memgraph graph is opened only in ultra and keeps one vector index of
    one width per database, so it blocks a switch only in ultra.
    """
    if settings.vector.provider != "chroma":
        return EMBEDDING_SWITCH_UNSUPPORTED
    if settings.graph.provider != "kuzu" and mode == "ultra":
        return EMBEDDING_SWITCH_UNSUPPORTED
    return None


def capabilities(settings: StorageBackends, *, mode: str) -> dict[str, StorageCapability]:
    """Per MemHub feature: works, skipped or refused on these backends."""
    kuzu = settings.graph.provider == "kuzu"
    chroma = settings.vector.provider == "chroma"
    switch = embedding_switch_refusal(settings, mode=mode)
    return {
        "embedding_switch": StorageCapability(state="refused" if switch else "works", code=switch),
        # A backup holds the memories on every backend: the local stores'
        # files, and on Qdrant an export through the SDK. Restoring memories
        # onto Qdrant needs an SDK write that keeps ids and vectors, which
        # hy-memory 2.0.15 does not have (docs/contracts/backup-v2.md).
        "backup": StorageCapability(state="works"),
        "restore": StorageCapability(
            state="works" if chroma else "refused",
            code=None if chroma else RESTORE_REMOTE_UNSUPPORTED,
        ),
        "graph_export": StorageCapability(
            state="works" if kuzu else "refused",
            code=None if kuzu else GRAPH_EXPORT_UNSUPPORTED,
        ),
        # The memory map reads the SDK graph through the same Cypher on
        # either store (`hy_graph_projection`).
        "graph_view": StorageCapability(state="works"),
        "graph_width_check": StorageCapability(
            state="works" if kuzu else "skipped",
            code=None if kuzu else GRAPH_WIDTH_SKIPPED,
        ),
        "embedding_space_scan": StorageCapability(
            state="works" if chroma else "skipped",
            code=None if chroma else SCAN_SKIPPED,
        ),
        "graph_open_guard": StorageCapability(
            state="works" if kuzu else "skipped",
            code=None if kuzu else GRAPH_GUARD_SKIPPED,
        ),
        "upgrade_migration": StorageCapability(
            state="works" if settings.is_default else "skipped",
            code=None if settings.is_default else MIGRATION_SKIPPED,
        ),
    }


# ----------------------------------------------------------------------
# The setting, with its secrets
# ----------------------------------------------------------------------


class SecretChange(BaseModel):
    """What a `set` does to one secret: keep it, replace it or remove it."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    keep: StrictBool = True
    value: str | None = None


KEEP = SecretChange()


class StorageBackendStore:
    """Reads and writes the setting and its secrets; caches the resolved pair.

    Resolving reads the secret store, which can reach the system keychain, so
    the result is cached until the setting changes.
    """

    def __init__(self, config_store: ConfigStore, secrets: SecretStore) -> None:
        self._config = config_store
        self._secrets = secrets
        self._lock = threading.RLock()
        self._resolved: tuple[StorageBackends, ResolvedStorage | None] | None = None

    def settings(self) -> StorageBackends:
        return self._config.storage_backends()

    def has_graph_password(self) -> bool:
        return self._has(GRAPH_SECRET_REF)

    def has_vector_api_key(self) -> bool:
        return self._has(VECTOR_SECRET_REF)

    def _has(self, ref: str) -> bool:
        try:
            return bool(self._secrets.has(ref))
        except Exception:  # noqa: BLE001 - an unreadable store is "not known to have it"
            return False

    def resolved(self) -> ResolvedStorage | None:
        """The pair clients are built with; None for Kuzu + Chroma (no secret read)."""
        settings = self.settings()
        with self._lock:
            cached = self._resolved
            if cached is not None and cached[0] == settings:
                return cached[1]
        resolved = self.resolve(settings, KEEP, KEEP)
        with self._lock:
            self._resolved = (settings, resolved)
        return resolved

    def resolve(
        self,
        settings: StorageBackends,
        graph_password: SecretChange,
        vector_api_key: SecretChange,
    ) -> ResolvedStorage | None:
        """`settings` with the secrets it would be used with after a change."""
        if settings.is_default:
            return None
        return resolve_storage(
            settings,
            graph_password=self._secret(GRAPH_SECRET_REF, graph_password)
            if settings.graph.provider != "kuzu"
            else None,
            vector_api_key=self._secret(VECTOR_SECRET_REF, vector_api_key)
            if settings.vector.remote
            else None,
        )

    def _secret(self, ref: str, change: SecretChange) -> str | None:
        if not change.keep:
            return change.value
        return self._secrets.resolve(ref)

    def apply(
        self,
        settings: StorageBackends,
        graph_password: SecretChange,
        vector_api_key: SecretChange,
    ) -> None:
        """Persist a validated setting: secrets first, then `config.json`.

        A secret that no longer applies (the graph back on Kuzu, a local
        vector store) is removed, so nothing is left behind in the keychain.
        """
        self._write_secret(
            GRAPH_SECRET_REF,
            graph_password,
            applies=settings.graph.provider != "kuzu",
        )
        self._write_secret(VECTOR_SECRET_REF, vector_api_key, applies=settings.vector.remote)
        self._config.set_storage_backends(settings)
        with self._lock:
            self._resolved = None

    def _write_secret(self, ref: str, change: SecretChange, *, applies: bool) -> None:
        if not applies or (not change.keep and not change.value):
            if self._has(ref):
                self._secrets.delete(ref)
            return
        if not change.keep and change.value:
            self._secrets.put(ref, change.value)

    def invalidate(self) -> None:
        with self._lock:
            self._resolved = None
