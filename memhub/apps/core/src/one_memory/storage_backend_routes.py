"""HTTP surface of the storage-backend setting: show, set, test, reset.

Contract: `memhub/docs/contracts/storage-backends-v1.md`. The CLI
(`memhub settings backends`) is the only product surface that changes it; the
App reads `pipeline.storage_backends` from `/api/v1/memory-processing` and
shows it as one read-only line. Errors keep Core's shape `{"detail": code}`;
a refusal that ran a probe also carries it as `"probe"`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast

from fastapi import APIRouter, Request
from fastapi.params import Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from one_memory.diagnostic_log import diagnostic_event
from one_memory.secrets import SecretStoreUnavailableError
from one_memory.storage_backend_probe import (
    StorageBackendHealth,
    StorageProbe,
    driver_available,
    probe_storage,
)
from one_memory.storage_backends import (
    FAISS_UNSUPPORTED,
    GRAPH_PROVIDERS,
    KEEP,
    VECTOR_PROVIDERS,
    GraphBackend,
    GraphProvider,
    ResolvedStorage,
    SecretChange,
    StorageBackends,
    StorageBackendStore,
    StorageCapability,
    VectorBackend,
    VectorProvider,
    capabilities,
)

#: HTTP status per refusal code; anything not listed is a 409.
_STATUS = {
    "secure_storage_unavailable": 503,
    "storage_backend_probe_failed": 422,
    "storage_backend_driver_missing": 422,
    "storage_backend_unreachable": 422,
    "storage_backend_auth_failed": 422,
    "storage_backend_database_not_found": 422,
}


# ----------------------------------------------------------------------
# Wire models
# ----------------------------------------------------------------------


class GraphBackendState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: GraphProvider
    url: str | None = None
    username: str | None = None
    database: str | None = None
    has_password: bool = False
    driver_available: bool


class VectorBackendState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: VectorProvider
    host: str | None = None
    port: int | None = None
    #: The prefix this home's collections carry on the server (None on Chroma).
    collection_prefix: str | None = None
    has_api_key: bool = False
    driver_available: bool


class ProviderChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    #: False when MemHub refuses it whatever the driver (`faiss`).
    supported: bool
    driver_available: bool
    code: str | None = None


class StorageBackendsState(BaseModel):
    """`GET /api/v1/settings/storage-backends`."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    graph: GraphBackendState
    vector: VectorBackendState
    #: Kuzu + Chroma, the embedded pair every MemHub had before the setting.
    is_default: bool
    #: `Neo4j + Qdrant`: graph first.
    label: str
    #: This home's namespace on shared servers; kept through `reset`.
    namespace: str | None = None
    #: Per MemHub feature: works, skipped or refused on these backends now.
    capabilities: dict[str, StorageCapability]
    #: The last probe of the active backends; None until one has run.
    health: StorageProbe | None = None
    providers: dict[str, list[ProviderChoice]]


class StorageBackendsSummary(BaseModel):
    """`pipeline.storage_backends` in `/api/v1/memory-processing`: the status line."""

    model_config = ConfigDict(extra="forbid")

    graph: GraphProvider
    vector: VectorProvider
    is_default: bool
    label: str
    #: `ok` (servers answered), `local` (embedded files), `failed` (a server
    #: did not answer: `health_code` says why) or `unknown` (not probed yet).
    health: Literal["ok", "local", "failed", "unknown"]
    health_code: str | None = None


class GraphBackendUpdate(BaseModel):
    """One side of a `set`. An absent `password` keeps the stored one; null removes it."""

    model_config = ConfigDict(extra="forbid")

    provider: GraphProvider
    url: str | None = Field(default=None, max_length=512)
    username: str | None = Field(default=None, max_length=128)
    database: str | None = Field(default=None, max_length=63)
    password: str | None = Field(default=None, max_length=1024)


class VectorBackendUpdate(BaseModel):
    """One side of a `set`. An absent `api_key` keeps the stored one; null removes it."""

    model_config = ConfigDict(extra="forbid")

    provider: VectorProvider
    host: str | None = Field(default=None, max_length=253)
    port: int | None = None
    collection_prefix: str | None = Field(default=None, max_length=32)
    api_key: str | None = Field(default=None, max_length=1024)


class StorageBackendsUpdate(BaseModel):
    """`PUT` and `POST .../test`. An absent side keeps what is configured."""

    model_config = ConfigDict(extra="forbid")

    graph: GraphBackendUpdate | None = None
    vector: VectorBackendUpdate | None = None
    #: Use a Neo4j/Memgraph database that already holds a memory graph. Needed
    #: because the graph has no per-home namespace: it may be another home's.
    adopt_existing_graph: bool = False


class StorageBackendsChange(StorageBackendsState):
    """`PUT` / `DELETE`: the new state and what the change did."""

    changed: bool
    previous_label: str
    #: `existing_memories_not_moved` whenever a backend changed: what is in
    #: the store that was left stays there and the new store starts as it is.
    notice: Literal["existing_memories_not_moved"] | None = None
    probe: StorageProbe | None = None


class StorageBackendsTest(BaseModel):
    """`POST /api/v1/settings/storage-backends/test`: a probe, nothing saved."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    label: str
    probe: StorageProbe
    #: True when `set` would also need `adopt_existing_graph`.
    graph_adoption_required: bool = False


# ----------------------------------------------------------------------
# Wiring
# ----------------------------------------------------------------------


@dataclass
class StorageBackendHooks:
    """What the routes need from the rest of Core."""

    store: StorageBackendStore
    health: StorageBackendHealth
    memory_mode: Callable[[], str]
    #: Held while a backend changes, the same lock a mode change takes.
    lifecycle_lock: asyncio.Lock
    #: `embedding_maintenance_in_progress` / `memory_lifecycle_busy`, or None.
    busy: Callable[[], str | None]
    #: Rebuild the SDK clients and anything derived (`api._after_storage_change`).
    after_change: Callable[[StorageBackends, StorageBackends], Awaitable[None]]
    probe: Callable[[ResolvedStorage | None], StorageProbe] = probe_storage


def _hooks(request: Request) -> StorageBackendHooks:
    return cast(StorageBackendHooks, request.app.state.storage_backend_hooks)


def refusal(code: str, *, probe: StorageProbe | None = None) -> JSONResponse:
    content: dict[str, Any] = {"detail": code}
    if probe is not None:
        content["probe"] = probe.model_dump(mode="json")
    return JSONResponse(status_code=_STATUS.get(code, 409), content=content)


def _validation_code(error: ValidationError) -> str:
    for item in error.errors():
        cause = (item.get("ctx") or {}).get("error")
        if isinstance(cause, ValueError) and str(cause).startswith("storage_backend_"):
            return str(cause)
    return "storage_backend_invalid"


REMOVE = SecretChange(keep=False, value=None)


def target_settings(
    current: StorageBackends, update: StorageBackendsUpdate
) -> tuple[StorageBackends, SecretChange, SecretChange]:
    """The setting a request asks for, and what it does to each secret.

    An absent secret keeps the stored one only for the same server (and, for
    the graph, the same user); null removes it. Raises `ValidationError` for a
    setting that cannot be used.
    """
    graph = current.graph
    graph_secret = KEEP
    if update.graph is not None:
        graph = GraphBackend(
            provider=update.graph.provider,
            url=update.graph.url,
            username=update.graph.username,
            database=update.graph.database,
        )
        if "password" in update.graph.model_fields_set:
            graph_secret = SecretChange(keep=False, value=update.graph.password)
        elif (graph.url, graph.username) != (current.graph.url, current.graph.username):
            # A stored password is never sent to another server or user.
            graph_secret = REMOVE
    vector = current.vector
    vector_secret = KEEP
    if update.vector is not None:
        vector = VectorBackend(
            provider=update.vector.provider,
            host=update.vector.host,
            port=update.vector.port,
            collection_prefix=update.vector.collection_prefix,
        )
        if "api_key" in update.vector.model_fields_set:
            vector_secret = SecretChange(keep=False, value=update.vector.api_key)
        elif vector.host != current.vector.host:
            # A stored API key is never sent to another server.
            vector_secret = REMOVE
    target = StorageBackends(graph=graph, vector=vector, namespace=current.namespace)
    if not target.is_default:
        target = target.with_namespace()
    return target, graph_secret, vector_secret


def _graph_target(settings: StorageBackends) -> tuple[object, ...]:
    return (settings.graph.provider, settings.graph.url, settings.graph.database)


def adoption_required(
    current: StorageBackends, target: StorageBackends, probe: StorageProbe
) -> bool:
    """A remote graph that already holds memories, and is not the one in use."""
    return bool(
        target.graph.provider != "kuzu"
        and _graph_target(target) != _graph_target(current)
        and probe.graph.existing_memory_nodes
    )


def state(hooks: StorageBackendHooks) -> StorageBackendsState:
    settings = hooks.store.settings()
    return _state(hooks, settings, hooks.health.snapshot())


def _state(
    hooks: StorageBackendHooks, settings: StorageBackends, health: StorageProbe | None
) -> StorageBackendsState:
    return StorageBackendsState(
        graph=GraphBackendState(
            provider=settings.graph.provider,
            url=settings.graph.url,
            username=settings.graph.username,
            database=settings.graph.database,
            has_password=settings.graph.provider != "kuzu" and hooks.store.has_graph_password(),
            driver_available=driver_available(settings.graph.provider),
        ),
        vector=VectorBackendState(
            provider=settings.vector.provider,
            host=settings.vector.host,
            port=settings.vector.port,
            collection_prefix=settings.collection_prefix,
            has_api_key=settings.vector.remote and hooks.store.has_vector_api_key(),
            driver_available=driver_available(settings.vector.provider),
        ),
        is_default=settings.is_default,
        label=settings.label,
        namespace=settings.namespace,
        capabilities=capabilities(settings, mode=hooks.memory_mode()),
        health=health,
        providers={
            "graph": [
                ProviderChoice(
                    provider=name, supported=True, driver_available=driver_available(name)
                )
                for name in GRAPH_PROVIDERS
            ],
            "vector": [
                ProviderChoice(
                    provider=name,
                    supported=name != "faiss",
                    driver_available=driver_available(name),
                    code=FAISS_UNSUPPORTED if name == "faiss" else None,
                )
                for name in VECTOR_PROVIDERS
            ],
        },
    )


def summary(settings: StorageBackends, health: StorageProbe | None) -> StorageBackendsSummary:
    """The status line: which pair, and whether it answers."""
    if health is None:
        state_, code = "unknown", None
    elif not health.ok:
        failed = health.graph if not health.graph.ok else health.vector
        state_, code = "failed", failed.code
    elif health.graph.state == "local" and health.vector.state == "local":
        state_, code = "local", None
    else:
        state_, code = "ok", None
    return StorageBackendsSummary(
        graph=settings.graph.provider,
        vector=settings.vector.provider,
        is_default=settings.is_default,
        label=settings.label,
        health=cast(Literal["ok", "local", "failed", "unknown"], state_),
        health_code=code,
    )


def storage_backend_router(dependencies: Sequence[Depends]) -> APIRouter:
    router = APIRouter(dependencies=dependencies)

    @router.get(
        "/api/v1/settings/storage-backends",
        response_model=StorageBackendsState,
    )
    async def get_storage_backends(request: Request) -> StorageBackendsState:
        """The configured graph and vector stores, what works on them, their health."""
        return await asyncio.to_thread(state, _hooks(request))

    @router.post(
        "/api/v1/settings/storage-backends/test",
        response_model=None,
    )
    async def test_storage_backends(
        request: Request, value: StorageBackendsUpdate | None = None
    ) -> Any:
        """Probe a setting (or, without a body, the one in use). Saves nothing."""
        hooks = _hooks(request)
        current = await asyncio.to_thread(hooks.store.settings)
        try:
            target, graph_secret, vector_secret = target_settings(
                current, value or StorageBackendsUpdate()
            )
        except ValidationError as error:
            return JSONResponse(status_code=422, content={"detail": _validation_code(error)})
        try:
            resolved = await asyncio.to_thread(
                hooks.store.resolve, target, graph_secret, vector_secret
            )
        except SecretStoreUnavailableError:
            return refusal("secure_storage_unavailable")
        probe = await asyncio.to_thread(hooks.probe, resolved)
        if target == current and graph_secret == KEEP and vector_secret == KEEP:
            hooks.health.record(resolved, probe)
        return StorageBackendsTest(
            label=target.label,
            probe=probe,
            graph_adoption_required=adoption_required(current, target, probe),
        )

    @router.put(
        "/api/v1/settings/storage-backends",
        response_model=None,
    )
    async def put_storage_backends(value: StorageBackendsUpdate, request: Request) -> Any:
        """Connect to the chosen stores, then save them; the next client uses them.

        Nothing is moved: the memories in the store that was left stay there,
        untouched, and the new store starts as it is (`notice`). A graph
        database that already holds a graph needs `adopt_existing_graph`.
        """
        hooks = _hooks(request)
        async with hooks.lifecycle_lock:
            busy = hooks.busy()
            if busy is not None:
                return refusal(busy)
            current = await asyncio.to_thread(hooks.store.settings)
            try:
                target, graph_secret, vector_secret = target_settings(current, value)
            except ValidationError as error:
                return JSONResponse(status_code=422, content={"detail": _validation_code(error)})
            try:
                resolved = await asyncio.to_thread(
                    hooks.store.resolve, target, graph_secret, vector_secret
                )
            except SecretStoreUnavailableError:
                return refusal("secure_storage_unavailable")
            probe = await asyncio.to_thread(hooks.probe, resolved)
            if not probe.ok:
                failed = probe.graph if not probe.graph.ok else probe.vector
                return refusal(failed.code or "storage_backend_probe_failed", probe=probe)
            if adoption_required(current, target, probe) and not value.adopt_existing_graph:
                return refusal("storage_backend_graph_not_empty", probe=probe)
            secrets_changed = graph_secret != KEEP or vector_secret != KEEP
            changed = target != current or secrets_changed
            if changed:
                try:
                    await asyncio.to_thread(hooks.store.apply, target, graph_secret, vector_secret)
                except SecretStoreUnavailableError:
                    return refusal("secure_storage_unavailable")
                hooks.health.record(resolved, probe)
                await hooks.after_change(current, target)
                diagnostic_event(
                    level="info",
                    component="storage.backends",
                    event="storage_backends.changed",
                    outcome="succeeded",
                    details={
                        "graph": target.graph.provider,
                        "vector": target.vector.provider,
                        "previous_graph": current.graph.provider,
                        "previous_vector": current.vector.provider,
                        "secrets_changed": secrets_changed,
                    },
                )
            moved = (target.graph, target.vector) != (current.graph, current.vector)
            return StorageBackendsChange(
                **_state(hooks, target, probe).model_dump(),
                changed=changed,
                previous_label=current.label,
                notice="existing_memories_not_moved" if changed and moved else None,
                probe=probe,
            )

    @router.delete(
        "/api/v1/settings/storage-backends",
        response_model=None,
    )
    async def reset_storage_backends(request: Request) -> Any:
        """Back to Kuzu + Chroma; the stored secrets go. The namespace stays."""
        hooks = _hooks(request)
        async with hooks.lifecycle_lock:
            busy = hooks.busy()
            if busy is not None:
                return refusal(busy)
            current = await asyncio.to_thread(hooks.store.settings)
            target = StorageBackends(namespace=current.namespace)
            changed = target != current
            try:
                await asyncio.to_thread(hooks.store.apply, target, REMOVE, REMOVE)
            except SecretStoreUnavailableError:
                return refusal("secure_storage_unavailable")
            if changed:
                await hooks.after_change(current, target)
                diagnostic_event(
                    level="info",
                    component="storage.backends",
                    event="storage_backends.reset",
                    outcome="succeeded",
                    details={
                        "previous_graph": current.graph.provider,
                        "previous_vector": current.vector.provider,
                    },
                )
            probe = await asyncio.to_thread(hooks.probe, None)
            return StorageBackendsChange(
                **_state(hooks, target, probe).model_dump(),
                changed=changed,
                previous_label=current.label,
                notice="existing_memories_not_moved" if changed else None,
                probe=probe,
            )

    return router
