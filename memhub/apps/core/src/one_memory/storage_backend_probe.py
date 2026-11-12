"""Connect to a chosen storage backend before it is saved, and report its health.

`PUT /api/v1/settings/storage-backends` saves nothing the probe could not
reach; `memhub settings backends test` runs the probe alone; status asks for
a cached result (`StorageBackendHealth`). The probe only connects and reads:
it creates no collection and writes no node. The embedded defaults (Kuzu,
Chroma) are local files in this home and are not probed.

Neo4j/Memgraph keep no per-home namespace (the SDK's graph has fixed labels
and one vector index per database), so the probe counts the `Memory` nodes
already there: a database that already holds a graph must be adopted
explicitly (`storage_backend_graph_not_empty`). Qdrant collections carry this
home's prefix, and the probe counts the ones that already do.

Only drivers are imported here, never the SDK, and only lazily.
"""

from __future__ import annotations

import importlib.util
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from one_memory.storage_backends import ResolvedStorage

logger = logging.getLogger(__name__)

#: The import name of the driver each provider needs.
DRIVERS: dict[str, str] = {
    "kuzu": "kuzu",
    "neo4j": "neo4j",
    "memgraph": "neo4j",
    "chroma": "chromadb",
    "qdrant": "qdrant_client",
    "faiss": "faiss",
}

DEFAULT_TIMEOUT_S = 5.0

ProbeState = Literal["ok", "failed", "local"]


class BackendProbe(BaseModel):
    """One side of a probe; `code` is a stable refusal when `ok` is false."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    ok: bool
    #: `local`: an embedded store in this home, nothing to connect to.
    state: ProbeState
    code: str | None = None
    #: The error class only, never its message (it can carry a URL or a user).
    error_type: str | None = None
    #: Neo4j/Memgraph: `Memory` nodes already in the database (None = unknown).
    existing_memory_nodes: int | None = None
    #: Qdrant: collections already carrying this home's prefix.
    collections_in_namespace: int | None = None
    latency_ms: int | None = None


class StorageProbe(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ok: bool
    graph: BackendProbe
    vector: BackendProbe


def driver_available(provider: str) -> bool:
    module = DRIVERS.get(provider)
    if module is None:
        return False
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def driver_availability(*, import_them: bool = False) -> dict[str, bool]:
    """Per provider, whether this Core can import its driver (packaging guard).

    `import_them` really imports each driver, as the SDK will: a frozen Core
    that finds a package but lacks one of its submodules fails here too.
    """
    if not import_them:
        return {provider: driver_available(provider) for provider in DRIVERS}
    result: dict[str, bool] = {}
    for provider, module in DRIVERS.items():
        try:
            importlib.import_module(module)
            if module == "qdrant_client":
                importlib.import_module("qdrant_client.models")
            result[provider] = True
        except Exception:  # noqa: BLE001 - any failure is "not importable"
            result[provider] = False
    return result


_DEFAULT = ResolvedStorage(graph_provider="kuzu", vector_provider="chroma")


def probe_storage(
    resolved: ResolvedStorage | None,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> StorageProbe:
    """Probe both sides of a resolved setting (None = the default pair)."""
    resolved = resolved or _DEFAULT
    graph = probe_graph(resolved, timeout_s=timeout_s)
    vector = probe_vector(resolved, timeout_s=timeout_s)
    return StorageProbe(ok=graph.ok and vector.ok, graph=graph, vector=vector)


def probe_graph(resolved: ResolvedStorage, *, timeout_s: float) -> BackendProbe:
    provider = resolved.graph_provider
    if not driver_available(provider):
        return _missing(provider)
    if provider == "kuzu":
        return BackendProbe(provider=provider, ok=True, state="local")
    started = time.monotonic()
    try:
        nodes = _neo4j_memory_nodes(resolved, timeout_s)
    except Exception as error:  # noqa: BLE001 - classified into a stable code
        return _failed(provider, error, started)
    return BackendProbe(
        provider=provider,
        ok=True,
        state="ok",
        existing_memory_nodes=nodes,
        latency_ms=_elapsed(started),
    )


def probe_vector(resolved: ResolvedStorage, *, timeout_s: float) -> BackendProbe:
    provider = resolved.vector_provider
    if not driver_available(provider):
        return _missing(provider)
    if provider == "chroma" or resolved.vector_host is None:
        return BackendProbe(provider=provider, ok=True, state="local")
    started = time.monotonic()
    try:
        count = _qdrant_namespace_collections(resolved, timeout_s)
    except Exception as error:  # noqa: BLE001 - classified into a stable code
        return _failed(provider, error, started)
    return BackendProbe(
        provider=provider,
        ok=True,
        state="ok",
        collections_in_namespace=count,
        latency_ms=_elapsed(started),
    )


def _neo4j_memory_nodes(resolved: ResolvedStorage, timeout_s: float) -> int | None:
    import neo4j

    auth: Any = None
    if resolved.graph_username:
        auth = (resolved.graph_username, resolved.graph_password or "")
    driver = neo4j.GraphDatabase.driver(
        resolved.graph_url or "",
        auth=auth,
        connection_timeout=timeout_s,
        connection_acquisition_timeout=timeout_s,
        max_transaction_retry_time=0,
    )
    try:
        driver.verify_connectivity()
        try:
            records, _summary, _keys = driver.execute_query(
                "MATCH (m:Memory) RETURN count(m) AS n",
                database_=resolved.graph_database,
                routing_=neo4j.RoutingControl.READ,
            )
        except neo4j.exceptions.ClientError as error:
            if "DatabaseNotFound" in str(getattr(error, "code", "")):
                raise
            # Connected and authorised, but the count itself was refused
            # (a role without read rights): unknown, not a failure.
            return None
        return int(records[0]["n"]) if records else 0
    finally:
        driver.close()


def _qdrant_namespace_collections(resolved: ResolvedStorage, timeout_s: float) -> int:
    from qdrant_client import QdrantClient

    client = QdrantClient(
        host=resolved.vector_host,
        port=resolved.vector_port or 6333,
        api_key=resolved.vector_api_key or None,
        timeout=max(1, int(timeout_s)),
    )
    try:
        collections = client.get_collections().collections
    finally:
        client.close()
    prefix = f"{resolved.vector_prefix}_" if resolved.vector_prefix else ""
    return sum(1 for item in collections if str(item.name).startswith(prefix))


def _failed(provider: str, error: BaseException, started: float) -> BackendProbe:
    code = classify_probe_error(error)
    logger.info(
        "storage_backend_probe_failed provider=%s code=%s error_type=%s",
        provider,
        code,
        type(error).__name__,
    )
    return BackendProbe(
        provider=provider,
        ok=False,
        state="failed",
        code=code,
        error_type=type(error).__name__,
        latency_ms=_elapsed(started),
    )


_UNREACHABLE = frozenset(
    {
        "ServiceUnavailable",
        "SessionExpired",
        "ConnectError",
        "ConnectTimeout",
        "TimeoutException",
        "ResponseHandlingException",
        "ConnectionError",
        "TimeoutError",
        "OSError",
    }
)


def classify_probe_error(error: BaseException) -> str:
    """A stable code for a driver error, by its class and status only."""
    names = {cls.__name__ for cls in type(error).__mro__}
    if names & {"AuthError", "Forbidden", "Unauthorized"}:
        return "storage_backend_auth_failed"
    if getattr(error, "status_code", None) in {401, 403}:
        return "storage_backend_auth_failed"
    if "DatabaseNotFound" in str(getattr(error, "code", "")):
        return "storage_backend_database_not_found"
    if names & _UNREACHABLE:
        return "storage_backend_unreachable"
    return "storage_backend_probe_failed"


def _missing(provider: str) -> BackendProbe:
    return BackendProbe(
        provider=provider,
        ok=False,
        state="failed",
        code="storage_backend_driver_missing",
    )


def _elapsed(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


# ----------------------------------------------------------------------
# Health, for status
# ----------------------------------------------------------------------


def _health_key(resolved: ResolvedStorage) -> tuple[object, ...]:
    return (
        resolved.graph_provider,
        resolved.graph_url or "",
        resolved.graph_database,
        resolved.vector_provider,
        resolved.vector_host,
        resolved.vector_port,
        resolved.vector_prefix,
    )


@dataclass
class _HealthEntry:
    key: tuple[object, ...]
    probe: StorageProbe
    checked_at: float


@dataclass
class StorageBackendHealth:
    """The last probe of the active backends, refreshed in the background.

    `snapshot` never holds a status request on a network round trip: it
    returns the last result (None until the first one) and starts a refresh
    when that result is older than `max_age_s`. The default pair is local and
    is reported without a probe.
    """

    resolved: Callable[[], ResolvedStorage | None]
    max_age_s: float = 60.0
    timeout_s: float = DEFAULT_TIMEOUT_S
    _entry: _HealthEntry | None = None
    _running: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot(self) -> StorageProbe | None:
        try:
            resolved = self.resolved()
        except Exception:  # noqa: BLE001 - an unreadable secret is reported by the clients
            return None
        if resolved is None:
            return probe_storage(None)
        key = _health_key(resolved)
        with self._lock:
            entry = self._entry
            current = entry if entry is not None and entry.key == key else None
            fresh = current is not None and time.monotonic() - current.checked_at < self.max_age_s
            if not fresh and not self._running:
                self._running = True
                threading.Thread(
                    target=self._refresh,
                    args=(resolved,),
                    name="one-memory-storage-health",
                    daemon=True,
                ).start()
            return current.probe if current is not None else None

    def record(self, resolved: ResolvedStorage | None, probe: StorageProbe) -> None:
        """Keep a probe a `set` or `test` just ran, so status shows it at once."""
        if resolved is None:
            return
        with self._lock:
            self._entry = _HealthEntry(
                key=_health_key(resolved), probe=probe, checked_at=time.monotonic()
            )

    def _refresh(self, resolved: ResolvedStorage) -> None:
        try:
            probe = probe_storage(resolved, timeout_s=self.timeout_s)
            self.record(resolved, probe)
        except Exception:  # noqa: BLE001 - health is best effort; the next status retries
            logger.warning("storage_backend_health_refresh_failed", exc_info=True)
        finally:
            with self._lock:
                self._running = False
