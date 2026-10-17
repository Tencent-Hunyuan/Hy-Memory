import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import threading
from collections.abc import AsyncIterator, Callable, Collection, Coroutine, Mapping, Sequence
from contextlib import asynccontextmanager, closing
from datetime import UTC, datetime
from enum import StrEnum
from functools import wraps
from importlib import import_module
from pathlib import Path
from time import monotonic
from typing import Any, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field

from one_memory.diagnostic_log import diagnostic_event, diagnostic_exception, elapsed_ms
from one_memory.hy_lifecycle import HyLifecycle, HyLifecycleUnreadable, read_hy_lifecycle
from one_memory.hy_presence import HyPresence, confirm_deleted
from one_memory.hy_runtime_config import (
    HY_MEMORY_MODE,
    HY_MEMORY_NAMESPACE,
    HY_MEMORY_READER,
    apply_diagnostic_log_level,
    configure_hy_runtime_environment,
    diagnostic_log_level,
)
from one_memory.hy_source_context import HySourceContext, HyWriteBindings
from one_memory.hy_store_concurrency import native_store_read_slot
from one_memory.model_profiles import ModelProfileSnapshot, ProviderKind, attach_disable_thinking
from one_memory.model_request_audit import audit_precipitation_request
from one_memory.semantic_deadline import SemanticMemoryHit

#: D102: how relevant HY has to call a hit before it counts. Raised from 0.25
#: because the semantic lane was outranking clearly better keyword matches on
#: score alone. Configurable so it can be tuned without a build.
DEFAULT_RELEVANCE_FLOOR = 0.35


def hy_relevance_floor() -> float:
    raw = os.environ.get("ONE_MEMORY_HY_RELEVANCE_FLOOR", "").strip()
    if not raw:
        return DEFAULT_RELEVANCE_FLOOR
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_RELEVANCE_FLOOR
    return min(1.0, max(0.0, value))


def graph_store_enabled() -> bool:
    """Whether HY should open the kuzu graph store.

    Default: no. All 25 crash reports carry kuzu frames, and the two that
    symbolicate resolve to `kuzu::main::Database::~Database()` reached from an
    asyncio task (`task_step` -> `gen_send_ex2`), where the destructor
    checkpoints the graph and faults in `NullMask::setNullRange`. The crash is
    in tearing the database down, not in querying it, so not opening it is the
    reliable fix.

    The packaged app sets nothing, so the graph stays closed unless someone
    opts in with `ONE_MEMORY_GRAPH_STORE=kuzu`. Closing it also moves
    `MEMORY_MODE` off `ultra`, because `ultra` forces the graph open (see
    `client.py`); that part is handled in `build_hy_memory_config`.
    """
    raw = os.environ.get("ONE_MEMORY_GRAPH_STORE", "").strip().lower()
    # Unset means closed. An explicit value other than `none` opts back in.
    if not raw:
        return False
    return raw != "none"


_HY_CLIENT_INITIALIZATION_LOCK = threading.Lock()
_HY_SHARED_RUNTIME_LOCK = threading.RLock()
_HY_SHARED_RUNTIMES: dict[str, tuple[Any, int]] = {}


def _shared_hy_runtime(config: Any, data_dir: Path) -> Any:
    """Return the process-owned HY runtime for one physical data directory."""
    key = str(data_dir.resolve())
    with _HY_SHARED_RUNTIME_LOCK:
        entry = _HY_SHARED_RUNTIMES.get(key)
        if entry is None:
            runtime_type = import_module("hy_memory.runtime").SharedRuntime
            runtime = asyncio.run(runtime_type.create(config))
            _HY_SHARED_RUNTIMES[key] = (runtime, 1)
        else:
            runtime, references = entry
            _HY_SHARED_RUNTIMES[key] = (runtime, references + 1)
        return runtime


def _release_shared_hy_runtime(data_dir: Path) -> None:
    key = str(data_dir.resolve())
    runtime: Any | None = None
    with _HY_SHARED_RUNTIME_LOCK:
        entry = _HY_SHARED_RUNTIMES.get(key)
        if entry is None:
            return
        candidate, references = entry
        if references > 1:
            _HY_SHARED_RUNTIMES[key] = (candidate, references - 1)
            return
        runtime = candidate
        _HY_SHARED_RUNTIMES.pop(key, None)
    runtime.close()


def close_shared_hy_runtimes() -> None:
    """Release process-owned HY loops after all clients have stopped."""
    with _HY_SHARED_RUNTIME_LOCK:
        runtimes = tuple(runtime for runtime, _references in _HY_SHARED_RUNTIMES.values())
        _HY_SHARED_RUNTIMES.clear()
    for runtime in runtimes:
        try:
            runtime.close()
        except Exception:
            logging.getLogger(__name__).exception("hy_shared_runtime_close_failed")


async def _joined_thread[**P, T](call: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    pending = asyncio.create_task(asyncio.to_thread(call, *args, **kwargs))
    try:
        return await asyncio.shield(pending)
    except asyncio.CancelledError:
        while not pending.done():
            try:
                await asyncio.shield(pending)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if pending.done() and not pending.cancelled():
            pending.exception()
        raise


class HyMemoryBridgeError(RuntimeError):
    pass


MEMHUB_DEFAULT_CODING_TOOL_RESULT_MAX_BYTES = 512


def coding_tool_result_max_bytes() -> int:
    """Return the per-tool payload ceiling used by MemHub's HY client."""

    raw = os.environ.get("MEMORY_CODING_TOOL_RESULT_MAX_BYTES")
    if raw is None:
        return MEMHUB_DEFAULT_CODING_TOOL_RESULT_MAX_BYTES
    try:
        value = int(raw)
    except ValueError:
        return MEMHUB_DEFAULT_CODING_TOOL_RESULT_MAX_BYTES
    return value if value > 0 else MEMHUB_DEFAULT_CODING_TOOL_RESULT_MAX_BYTES


def _exclusive[**P, T](
    method: Callable[P, Coroutine[Any, Any, T]],
) -> Callable[P, Coroutine[Any, Any, T]]:
    @wraps(method)
    async def serialized(*args: P.args, **kwargs: P.kwargs) -> T:
        bridge = cast("HyMemoryBridge", args[0])
        async with bridge.exclusive_access():
            return await method(*args, **kwargs)

    return serialized


class UnsupportedHyMemoryProviderError(HyMemoryBridgeError):
    pass


class GraphWidthDecision(StrEnum):
    """What to do when an existing graph disagrees with the embedding width.

    KEEP_EXISTING is the default: leave the graph alone and refuse the switch.
    REBUILD_WITH_NEW archives the graph so the new width can open a fresh one.
    EXPORT_FIRST also leaves the graph alone; the caller is expected to export
    before coming back with either of the other two.
    """

    KEEP_EXISTING = "keep_existing"
    REBUILD_WITH_NEW = "rebuild_with_new"
    EXPORT_FIRST = "export_first"


class GraphWidthMismatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    stored_width: int
    expected_width: int
    graph_path: str


class GraphWidthOutcome(BaseModel):
    """What a decision actually did on disk, said separately.

    Archiving and exporting both hand back a path, and a single field would
    let the app show a rebuild's archive as though it were the user's backup.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    archived_path: str | None = None
    exported_path: str | None = None


class GraphWidthMismatchError(HyMemoryBridgeError):
    """Raised instead of silently archiving a live graph.

    Carries the widths so the UI can offer KEEP_EXISTING / REBUILD_WITH_NEW /
    EXPORT_FIRST without guessing.
    """

    def __init__(self, mismatch: GraphWidthMismatch) -> None:
        super().__init__(
            f"graph_width_mismatch:stored={mismatch.stored_width}"
            f":expected={mismatch.expected_width}"
        )
        self.mismatch = mismatch


HY_CODING_EXPERIENCE_LAYER = "coding_experience"
HY_WORKING_FACT_LAYER = "working_fact"
HY_CODING_LAYERS = frozenset({HY_CODING_EXPERIENCE_LAYER, HY_WORKING_FACT_LAYER})


class HyMemoryTurn(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    role: str = Field(pattern="^(user|assistant|tool)$")
    content: str = Field(min_length=1)


class HyMemoryIngestRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str = Field(min_length=1, max_length=256)
    user_id: str = Field(min_length=1, max_length=256)
    source_id: str = Field(min_length=1, max_length=128)
    agent_id: str = Field(default="one-memory", min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=512)
    turns: tuple[HyMemoryTurn, ...] = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    workspace_ids: tuple[str, ...] = ()
    memory_at: datetime | None = None
    source_context: HySourceContext | None = None


class HyMemoryIngestResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    success: bool
    memories: tuple["HyMemoryOutput", ...] = ()
    #: Ids HY deleted during this write; only a permission lease can observe them.
    retired_memory_ids: tuple[str, ...] = ()
    #: Ids HY superseded during this write, each with the ids that replaced it.
    superseded_memory_ids: dict[str, tuple[str, ...]] = Field(default_factory=dict)


def _string_tuple(value: object) -> tuple[str, ...]:
    """Only the strings, in HY's order, deduplicated.

    HY normalises its own tags and this does not second-guess them. What it
    does drop is anything that is not a string, because a tag becomes a label
    someone reads and a number rendered as one is noise.
    """
    if not isinstance(value, list):
        return ()
    seen: dict[str, None] = {}
    for item in value:
        if isinstance(item, str) and item.strip():
            seen.setdefault(item.strip(), None)
    return tuple(seen)


class HyMemoryOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    memory_id: str
    content: str
    layer: str | None = None
    #: Whose the memory is, in HY's own words: "user" for facts about the
    #: person, "agent" for what the assistant supplied — a recommendation, a
    #: confirmation, something it looked up. HY has always returned this and
    #: we have always dropped it, which left no way to tell the two apart.
    owner: str | None = None
    #: HY's topic tags. It seeds the extract prompt with the tags already in
    #: the library so the model reuses them, so this clustering has been
    #: running all along and arriving here to be discarded.
    tags: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()


# HyMemoryIngestResult names HyMemoryOutput before it exists, so pydantic left
# its schema unbuilt and would have built it on whichever thread first used the
# model. That thread is the event loop, and building it walks
# inspect.signature(dict) into tokenize — which is where one of the real
# `event loop blocked` reports caught the main thread. Built here instead,
# at import, where nobody is waiting on an answer.
HyMemoryIngestResult.model_rebuild()


class HyGraphNode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: str
    content: str
    layer: str | None = None
    confidence: float | None = None


class HyGraphEdge(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_node_id: str
    target_node_id: str
    relation: str
    weight: float | None = None


class HyGraphSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    nodes: tuple[HyGraphNode, ...] = ()
    edges: tuple[HyGraphEdge, ...] = ()
    # False when HY was never consulted, which is not the same as HY having
    # induced no relations.
    consulted: bool = True


class HyMemoryClientProtocol(Protocol):
    def add(
        self,
        data: Sequence[Mapping[str, str]],
        *,
        user_id: str,
        agent_id: str,
        session_id: str,
        metadata: Mapping[str, object],
        memory_at: datetime | None,
        scene: str | None,
        request_id: str,
    ) -> Mapping[str, object]: ...

    def add_extracted(
        self,
        contents: str | Sequence[str],
        *,
        user_id: str,
        agent_id: str,
        session_id: str,
        metadata: Mapping[str, object],
        memory_at: datetime | None,
        request_id: str,
    ) -> Mapping[str, object]: ...

    def close(self) -> None: ...

    def get(self, memory_id: str) -> Mapping[str, object] | None: ...

    def update(self, memory_id: str, content: str) -> Mapping[str, object]: ...

    def delete(self, memory_id: str) -> Mapping[str, object]: ...

    def search(
        self,
        query: str,
        *,
        user_ids: list[str],
        agent_ids: list[str],
        limit: int,
        min_score: float,
        schema_min_score: float,
        schema_limit: int,
        intention_limit: int,
        scene: str = "normal",
        reader: str = "legacy",
    ) -> Mapping[str, object]: ...


class HyMemoryBridge:
    def __init__(
        self,
        *,
        profile: ModelProfileSnapshot,
        api_key: str | None,
        data_dir: Path,
        client: HyMemoryClientProtocol | None = None,
        http_transport_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.profile = profile
        self.api_key = api_key
        self.data_dir = data_dir
        self._client = client
        self._http_transport_factory = http_transport_factory
        self._client_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._last_bindings: HyWriteBindings | None = None
        self._access_lock = asyncio.Lock()
        self._access_owner: asyncio.Task[Any] | None = None

    @asynccontextmanager
    async def exclusive_access(self) -> AsyncIterator[None]:
        task = asyncio.current_task()
        if task is not None and self._access_owner is task:
            yield
            return
        async with self._access_lock:
            self._access_owner = task
            try:
                yield
            finally:
                self._access_owner = None

    async def warm(self) -> None:
        """Initialize the shared client and its cold write-path dependencies."""
        async with self.exclusive_access():
            await self._get_client()
            await _joined_thread(_warm_hy_write_dependencies)

    async def prepare(self) -> None:
        """Initialize HY before a durable model-invocation marker is written."""
        await self._get_client()

    @_exclusive
    async def ingest(self, request: HyMemoryIngestRequest) -> HyMemoryIngestResult:
        with audit_precipitation_request(request.request_id):
            async with self._write_lock:
                return await self._ingest(request)

    async def _ingest(self, request: HyMemoryIngestRequest) -> HyMemoryIngestResult:
        started = monotonic()
        diagnostic_event(
            level="info",
            component="hy.bridge",
            event="hy.ingest_started",
            outcome="started",
            request_id=request.request_id,
            details={"source_id": request.source_id, "count": len(request.turns)},
        )
        client = await self._get_client()
        guards = getattr(client, "_one_memory_write_guards", ())
        from one_memory.hy_response_guard import HyReconcileGuard

        reconcile_guards = tuple(
            guard for guard in guards if isinstance(guard, HyReconcileGuard)
        )
        for guard in reconcile_guards:
            guard.begin_write()
        before = [len(guard.failures) for guard in guards]
        bindings = HyWriteBindings(request.source_context) if request.source_context else None
        if bindings:
            seed = bindings.context.current[0]
            if (
                seed.user_id,
                seed.source_id,
                seed.conversation_id,
                tuple(
                    sorted(
                        {
                            record.workspace
                            for record in (
                                *bindings.context.current,
                                *bindings.context.history,
                                *bindings.context.supporting,
                            )
                            if record.workspace is not None
                        }
                    )
                ),
            ) != (
                request.user_id,
                request.source_id,
                request.session_id,
                tuple(sorted({workspace for workspace in request.workspace_ids if workspace})),
            ):
                raise HyMemoryBridgeError("hy_memory_write_verification_context_scope")
        self._last_bindings = None
        for guard in guards:
            if hasattr(type(guard), "_extract") or hasattr(type(guard), "_check_targets"):
                guard.bindings = bindings
        try:
            raw = await _joined_thread(
                client.add,
                [turn.model_dump() for turn in request.turns],
                user_id=request.user_id,
                agent_id=request.agent_id,
                session_id=request.session_id,
                metadata={
                    "source_id": request.source_id,
                    "evidence_ids": list(request.evidence_ids),
                    "workspace_ids": list(request.workspace_ids),
                    "input_kind": "raw_conversation",
                },
                memory_at=request.memory_at,
                # HY routes chat or coding itself when it sees tool messages.
                scene=None,
                request_id=request.request_id,
            )
        except Exception as error:
            diagnostic_event(
                level="error",
                component="hy.bridge",
                event="hy.ingest_failed",
                outcome="failed",
                request_id=request.request_id,
                duration_ms=elapsed_ms(started),
                error_code="hy_memory_runtime_failed",
                **diagnostic_exception(error),
            )
            for guard, count in zip(guards, before, strict=True):
                if len(guard.failures) > count:
                    raise HyMemoryBridgeError(guard.failures[count]) from error
            raise HyMemoryBridgeError("hy_memory_runtime_failed") from error
        finally:
            for guard in guards:
                if hasattr(type(guard), "_extract") or hasattr(type(guard), "_check_targets"):
                    guard.bindings = None
        for guard, count in zip(guards, before, strict=True):
            if len(guard.failures) > count:
                raise HyMemoryBridgeError(guard.failures[count])
        result = await self._result(client, request, raw, expected_contents=None, bindings=bindings)
        result = await _with_native_mutation_delta(
            client,
            result,
            raw,
            reconcile_guards,
        )
        if bindings:
            outputs = []
            for memory in result.memories:
                if memory.layer in HY_CODING_LAYERS:
                    outputs.append(memory)
                    continue
                ids = bindings.support_for(memory.content, memory.owner)
                if not ids:
                    raise HyMemoryBridgeError("hy_memory_write_verification_support_missing")
                outputs.append(memory.model_copy(update={"evidence_ids": ids}))
            result = result.model_copy(update={"memories": tuple(outputs)})
        self._last_bindings = bindings
        diagnostic_event(
            level="info",
            component="hy.bridge",
            event="hy.ingest_completed",
            outcome="succeeded",
            request_id=request.request_id,
            duration_ms=elapsed_ms(started),
            details={"result_count": len(result.memories)},
        )
        return result

    @_exclusive
    async def ingest_extracted(
        self,
        request: HyMemoryIngestRequest,
        contents: Sequence[str],
    ) -> HyMemoryIngestResult:
        with audit_precipitation_request(request.request_id):
            async with self._write_lock:
                return await self._ingest_extracted(request, contents)

    async def _ingest_extracted(
        self,
        request: HyMemoryIngestRequest,
        contents: Sequence[str],
    ) -> HyMemoryIngestResult:
        """Import source memory content when no raw conversation is available."""
        started = monotonic()
        diagnostic_event(
            level="info",
            component="hy.bridge",
            event="hy.ingest_started",
            outcome="started",
            request_id=request.request_id,
            details={"source_id": request.source_id, "count": len(contents)},
        )
        client = await self._get_client()
        guards = getattr(client, "_one_memory_write_guards", ())
        before = [len(guard.failures) for guard in guards]
        from one_memory.hy_response_guard import HyReconcileGuard

        report_guards = tuple(guard for guard in guards if isinstance(guard, HyReconcileGuard))
        for guard in report_guards:
            guard.begin_write()
        expected_contents = frozenset(content.strip() for content in contents if content.strip())
        for guard in report_guards:
            guard.imported_contents = expected_contents
        try:
            raw = await _joined_thread(
                client.add_extracted,
                contents,
                user_id=request.user_id,
                agent_id=request.agent_id,
                session_id=request.session_id,
                metadata={
                    "source_id": request.source_id,
                    "evidence_ids": list(request.evidence_ids),
                    "workspace_ids": list(request.workspace_ids),
                    "input_kind": "memory_content_without_raw",
                },
                memory_at=request.memory_at,
                request_id=request.request_id,
            )
        except Exception as error:
            diagnostic_event(
                level="error",
                component="hy.bridge",
                event="hy.ingest_failed",
                outcome="failed",
                request_id=request.request_id,
                duration_ms=elapsed_ms(started),
                error_code="hy_memory_runtime_failed",
                **diagnostic_exception(error),
            )
            for guard, count in zip(guards, before, strict=True):
                if len(guard.failures) > count:
                    raise HyMemoryBridgeError(guard.failures[count]) from error
            raise HyMemoryBridgeError("hy_memory_runtime_failed") from error
        finally:
            for guard in report_guards:
                guard.imported_contents = None
        for guard, count in zip(guards, before, strict=True):
            if len(guard.failures) > count:
                raise HyMemoryBridgeError(guard.failures[count])
        result = await self._result(
            client,
            request,
            raw,
            expected_contents=expected_contents,
        )
        result = await _with_native_mutation_delta(
            client,
            result,
            raw,
            report_guards,
        )
        diagnostic_event(
            level="info",
            component="hy.bridge",
            event="hy.ingest_completed",
            outcome="succeeded",
            request_id=request.request_id,
            duration_ms=elapsed_ms(started),
            details={"result_count": len(result.memories)},
        )
        return result

    @_exclusive
    async def list_agent_ids(self, *, user_id: str, limit: int = 1_000) -> tuple[str, ...]:
        """Return the HY scopes that actually hold memories for this user.

        Scopes are read back from HY rather than recomputed from evidence,
        because memories written under an earlier routing policy keep the
        agent id they were stored with.
        """
        client = await self._get_client()
        try:
            raw = await _joined_thread(
                cast(Any, client).list_memories,
                user_id=user_id,
                limit=limit,
            )
        except Exception as error:
            raise HyMemoryBridgeError("hy_memory_list_failed") from error
        agent_ids = {
            agent_id
            for memory in _listed_memories(raw)
            if isinstance(agent_id := memory.get("agent_id"), str) and agent_id
        }
        return tuple(sorted(agent_ids))

    @_exclusive
    async def outputs_for_request(
        self,
        *,
        request_id: str,
        user_id: str,
        agent_id: str,
        evidence_ids: tuple[str, ...],
        scan_limit: int = 10_000,
    ) -> tuple[HyMemoryOutput, ...]:
        """Read back durable HY nodes carrying one exact write request id.

        This is the safe side of unknown-outcome recovery: it does not invoke
        the model or mutate HY. The public SDK listing is paged rather than
        reading HY's private SQLite/Chroma schema, and every result is checked
        against user, agent and request identity before it can reach
        governance.
        """
        client = await self._get_client()
        page_size = 500
        outputs: list[HyMemoryOutput] = []
        seen: set[str] = set()
        for offset in range(0, scan_limit, page_size):
            try:
                raw = await _joined_thread(
                    cast(Any, client).list_memories,
                    user_id=user_id,
                    agent_id=agent_id,
                    limit=page_size,
                    offset=offset,
                )
            except Exception as error:
                raise HyMemoryBridgeError("hy_memory_outcome_inquiry_failed") from error
            records = _listed_memories(raw)
            for record in records:
                custom = record.get("custom")
                if isinstance(custom, str):
                    try:
                        custom = json.loads(custom)
                    except (TypeError, ValueError):
                        custom = None
                if (
                    not isinstance(custom, Mapping)
                    or custom.get("request_id") != request_id
                    or record.get("user_id") != user_id
                    or record.get("agent_id") != agent_id
                ):
                    continue
                memory_id, content = record.get("memory_id"), record.get("content")
                if (
                    not isinstance(memory_id, str)
                    or memory_id in seen
                    or not isinstance(content, str)
                    or not content.strip()
                ):
                    continue
                seen.add(memory_id)
                layer, owner = record.get("layer"), record.get("owner")
                outputs.append(
                    HyMemoryOutput(
                        memory_id=memory_id,
                        content=content,
                        layer=layer if isinstance(layer, str) else None,
                        owner=owner if owner in {"user", "agent"} else None,
                        tags=_string_tuple(record.get("tags")),
                        evidence_ids=evidence_ids,
                    )
                )
            vdb = raw.get("vdb") if isinstance(raw, Mapping) else None
            total = vdb.get("total") if isinstance(vdb, Mapping) else None
            if len(records) < page_size or (isinstance(total, int) and offset + page_size >= total):
                break
        return tuple(outputs)

    @_exclusive
    async def profile_memories(
        self,
        *,
        user_id: str,
        agent_id: str,
    ) -> tuple[HyMemoryOutput, ...]:
        """The user's own profile, read back rather than returned by the write.

        HY writes basic info on a path of its own — `upsert_basic_profile`, an
        evolving chain keyed by what it learns about the person rather than by
        the conversation it learnt it from — and the ids it produces never
        reach the ingest response. Ours therefore never saw them: the whole
        profile layer existed in HY and was invisible here, which is why
        `_memory_scopes` had a profile branch that could not be reached and
        recall without a project directory always came back empty.

        Only the head of each chain is returned, which is the profile as it
        stands. Superseded versions are HY's history, not a second memory.
        """
        client = await self._get_client()
        try:
            raw = await _joined_thread(
                cast(Any, client).get_user_basic_info,
                user_id=user_id,
            )
        except Exception as error:
            raise HyMemoryBridgeError("hy_memory_profile_read_failed") from error
        outputs: list[HyMemoryOutput] = []
        seen: set[str] = set()
        for memory in _basic_info_memories(raw):
            memory_id = memory.get("memory_id")
            content = memory.get("content")
            if not isinstance(memory_id, str) or memory_id in seen:
                continue
            if not isinstance(content, str) or not content.strip():
                continue
            seen.add(memory_id)
            layer = memory.get("layer")
            profile_values = memory.get("basic_info_kv")
            ids = (
                self._last_bindings.profile_support(
                    content, dict(profile_values) if isinstance(profile_values, Mapping) else {}
                )
                if self._last_bindings
                else ()
            )
            if self._last_bindings and not ids:
                # Previously governed profiles are not new output of this transaction.
                continue
            outputs.append(
                HyMemoryOutput(
                    memory_id=memory_id,
                    content=content,
                    # Stated by HY rather than assumed: what makes this a
                    # profile downstream is the layer, and defaulting it here
                    # would be this module deciding that on HY's behalf.
                    layer=layer if isinstance(layer, str) else None,
                    owner="user" if ids else None,
                    evidence_ids=ids,
                )
            )
        return tuple(outputs)

    @_exclusive
    async def digest(self, *, user_id: str, agent_id: str) -> dict[str, object]:
        client = await self._get_client()
        try:
            result = await _joined_thread(
                cast(Any, client).evolve,
                user_id=user_id,
                agent_id=agent_id,
            )
            return dict(result)
        except Exception as error:
            raise HyMemoryBridgeError("hy_memory_digest_failed") from error

    @_exclusive
    async def graph_snapshot(
        self,
        *,
        user_id: str,
        agent_id: str,
        limit: int = 300,
    ) -> HyGraphSnapshot:
        client = await self._get_client()
        try:
            raw_nodes, raw_edges = await _joined_thread(
                _read_graph_snapshot,
                client,
                user_id,
                agent_id,
                limit,
            )
        except Exception as error:
            raise HyMemoryBridgeError("hy_memory_graph_read_failed") from error
        return HyGraphSnapshot(
            nodes=tuple(
                HyGraphNode(
                    node_id=str(row[0]),
                    content=str(row[1]),
                    layer=str(row[2]) if row[2] is not None else None,
                    confidence=_safe_float(row[3]),
                )
                for row in raw_nodes
                if len(row) >= 4 and row[0] and row[1]
            ),
            edges=tuple(
                HyGraphEdge(
                    source_node_id=str(row[0]),
                    target_node_id=str(row[1]),
                    relation=str(row[2]),
                    weight=_safe_float(row[3]),
                )
                for row in raw_edges
                if len(row) >= 4 and row[0] and row[1] and row[2]
            ),
        )

    @_exclusive
    async def update(self, memory_id: str, content: str) -> dict[str, object]:
        """Re-embed a memory in place, keeping its id.

        HY re-embeds and upserts the same node, so every id already handed out
        stays valid — which is why this is the call for a correction and
        delete-then-add is not.

        A memory HY's vector store does not hold comes back as `success: False`
        rather than as an error, so that answer is turned into one here: a
        caller told the write went through would go on to report an index that
        matches when it does not.
        """
        client = await self._get_client()
        try:
            result = await _joined_thread(client.update, memory_id, content)
        except Exception as error:
            raise HyMemoryBridgeError("hy_memory_update_failed") from error
        if not result.get("success"):
            raise HyMemoryBridgeError("hy_memory_update_rejected")
        return dict(result)

    @_exclusive
    async def delete(self, memory_id: str) -> dict[str, object]:
        """Have HY forget a memory; one it no longer holds counts as forgotten.

        HY's own answer settles nothing: `success: False` comes both when
        there was no such memory and when removing it failed, and `success:
        True` does not promise anything was deleted. So done means HY no
        longer holds it, read back from each of its stores after every delete.
        """
        client = await self._get_client()
        result: dict[str, object] = {"success": False}
        failure: Exception | None = None
        try:
            result = dict(await _joined_thread(client.delete, memory_id))
        except Exception as error:
            # Read back anyway: HY failing to delete what it no longer holds
            # leaves nothing to retry.
            failure = error
        try:
            presence = await _joined_thread(confirm_deleted, client, memory_id)
        except Exception as error:
            raise HyMemoryBridgeError("hy_memory_delete_failed") from (failure or error)
        if presence is HyPresence.ABSENT:
            return result
        if failure is not None:
            raise HyMemoryBridgeError("hy_memory_delete_failed") from failure
        if presence is HyPresence.PRESENT:
            raise HyMemoryBridgeError("hy_memory_delete_rejected")
        if presence is HyPresence.UNKNOWN:
            raise HyMemoryBridgeError("hy_memory_delete_unconfirmed")
        return dict(result)

    def lifecycle_now(self, user_id: str, ids: Collection[str] | None = None) -> HyLifecycle | None:
        """HY's set-aside memories, or None while this bridge has not opened HY.

        Not serialized behind ingest: it reads HY's store directly and must
        answer while a long turn is being processed. Raises
        `HyLifecycleUnreadable` rather than answering empty.
        """
        client = self._client
        if client is None:
            if hy_memory_store_exists(self.data_dir):
                raise HyLifecycleUnreadable("hy_lifecycle_not_ready")
            return None
        return read_hy_lifecycle(client, user_id, ids)

    @_exclusive
    async def search_ids(
        self,
        query: str,
        *,
        user_id: str,
        agent_id: str,
        limit: int = 12,
    ) -> tuple[str, ...]:
        return tuple(
            memory_id
            for memory_id, _score in await self.search_scores(
                query, user_id=user_id, agent_id=agent_id, limit=limit
            )
        )

    @_exclusive
    async def search_scores(
        self,
        query: str,
        *,
        user_id: str,
        agent_id: str,
        limit: int = 12,
    ) -> tuple[tuple[str, float], ...]:
        return tuple(
            (hit.memory_id, hit.score)
            for hit in await self.search_memories(
                query,
                user_id=user_id,
                agent_id=agent_id,
                limit=limit,
            )
        )

    async def search_memories(
        self,
        query: str,
        *,
        user_id: str,
        agent_id: str,
        limit: int = 12,
    ) -> tuple[SemanticMemoryHit, ...]:
        """HY's hits with the relevance it gave them.

        D102: `search_ids` threw the score away after sorting by it, so the
        caller could only rank by position — which forced semantic hits to be
        placed as a block ahead of everything the keyword lane found. The
        score is what lets the two lanes be ranked against each other.
        """
        async with native_store_read_slot() as admitted:
            if not admitted:
                raise HyMemoryBridgeError("hy_memory_store_busy")
            client = await self._get_client()
            try:
                result = await _joined_thread(
                    client.search,
                    query,
                    user_ids=[user_id],
                    agent_ids=[] if agent_id == "*" else [agent_id],
                    limit=limit,
                    min_score=hy_relevance_floor(),
                    schema_min_score=hy_relevance_floor(),
                    schema_limit=limit,
                    intention_limit=limit,
                    scene="normal",
                    reader=HY_MEMORY_READER,
                )
            except Exception as error:
                raise HyMemoryBridgeError("hy_memory_search_failed") from error
        hits = _replied_memories(result, "hy_memory_search_shape_unknown")
        found: list[SemanticMemoryHit] = []
        seen: set[str] = set()
        for item in sorted(hits, key=_hy_score, reverse=True):
            memory_id = item.get("memory_id") or item.get("id")
            if isinstance(memory_id, str) and memory_id not in seen:
                seen.add(memory_id)
                formatted_content = item.get("content")
                member_ids, formatting_complete = _formatted_member_ids(item)
                found.append(
                    SemanticMemoryHit(
                        memory_id=memory_id,
                        score=_hy_score(item),
                        formatted_content=(
                            formatted_content.strip()
                            if isinstance(formatted_content, str) and formatted_content.strip()
                            else None
                        ),
                        member_ids=member_ids,
                        sdk_formatted=True,
                        formatting_complete=formatting_complete,
                    )
                )
        return tuple(found[:limit])

    @staticmethod
    async def _result(
        client: HyMemoryClientProtocol,
        request: HyMemoryIngestRequest,
        raw: Mapping[str, object],
        expected_contents: frozenset[str] | None,
        bindings: HyWriteBindings | None = None,
    ) -> HyMemoryIngestResult:
        success = raw.get("success") is True
        if not success:
            raise HyMemoryBridgeError(_safe_error_code(raw))
        if raw.get("scene") == "productivity":
            return await _coding_result(client, request, raw)
        raw_ids = raw.get("memory_ids")
        if not isinstance(raw_ids, list):
            single = raw.get("memory_id")
            raw_ids = [single] if isinstance(single, str) and single else []
        records: list[Mapping[str, object]] = []
        for memory_id in (value for value in raw_ids if isinstance(value, str)):
            record = await _joined_thread(_read_hy_record, client, memory_id)
            if record:
                records.append(record)
        if not records and expected_contents:
            for expected_content in expected_contents:
                found = await _joined_thread(
                    client.search,
                    expected_content,
                    user_ids=[request.user_id],
                    agent_ids=[request.agent_id],
                    limit=20,
                    min_score=0.0,
                    schema_min_score=0.0,
                    schema_limit=20,
                    intention_limit=20,
                    reader=HY_MEMORY_READER,
                )
                records.extend(_exact_content_records(found, expected_content))

        outputs: list[HyMemoryOutput] = []
        seen_ids: set[str] = set()
        for record in records:
            record_memory_id = record.get("memory_id")
            if not isinstance(record_memory_id, str) or record_memory_id in seen_ids:
                continue
            seen_ids.add(record_memory_id)
            if (
                not _record_matches_request(record, request)
                and not (
                    expected_contents is not None
                    and record.get("content") in expected_contents
                    and record.get("user_id") == request.user_id
                    and record.get("agent_id") == request.agent_id
                )
                and not (
                    bindings is not None
                    and record.get("user_id") == request.user_id
                    and record.get("agent_id") == request.agent_id
                    and bindings.verified_operations.get(record_memory_id)
                    == (record.get("content"), record.get("owner"))
                )
            ):
                continue
            record_content = record.get("content")
            if not isinstance(record_content, str) or not record_content.strip():
                continue
            layer = record.get("layer")
            if isinstance(layer, str) and layer.upper().startswith("L1_"):
                continue
            owner = record.get("owner")
            outputs.append(
                HyMemoryOutput(
                    memory_id=record_memory_id,
                    content=record_content,
                    layer=layer if isinstance(layer, str) else None,
                    # Only HY's two words are honoured. It normalises anything
                    # else to nothing itself, and inventing a third value here
                    # would be this module deciding ownership on its behalf.
                    owner=owner if owner in {"user", "agent"} else None,
                    tags=_string_tuple(record.get("tags")),
                )
            )
        return HyMemoryIngestResult(success=True, memories=tuple(outputs))

    async def _get_client(self) -> HyMemoryClientProtocol:
        if self._client is not None:
            return self._client
        async with self._client_lock:
            if self._client is None:
                started = monotonic()
                try:

                    def initialize() -> None:
                        with _HY_CLIENT_INITIALIZATION_LOCK:
                            self._client = create_hy_memory_client(
                                self.profile,
                                api_key=self.api_key,
                                data_dir=self.data_dir,
                        http_transport_factory=self._http_transport_factory,
                            )

                    await _joined_thread(initialize)
                except HyMemoryBridgeError as error:
                    diagnostic_event(
                        level="error",
                        component="hy.client",
                        event="hy.client_initialization.failed",
                        outcome="failed",
                        duration_ms=elapsed_ms(started),
                        error_code="hy_memory_init_failed",
                        **diagnostic_exception(error),
                    )
                    raise
                except Exception as error:
                    diagnostic_event(
                        level="error",
                        component="hy.client",
                        event="hy.client_initialization.failed",
                        outcome="failed",
                        duration_ms=elapsed_ms(started),
                        error_code="hy_memory_init_failed",
                        **diagnostic_exception(error),
                    )
                    raise HyMemoryBridgeError("hy_memory_init_failed") from error
                diagnostic_event(
                    level="info",
                    component="hy.client",
                    event="hy.client_initialization.completed",
                    outcome="succeeded",
                    duration_ms=elapsed_ms(started),
                )
            assert self._client is not None
            return self._client

    @_exclusive
    async def close(self) -> None:
        async with self._client_lock:
            if self._client is None:
                return
            client, self._client = self._client, None
        await _joined_thread(client.close)


def create_hy_memory_client(
    profile: ModelProfileSnapshot,
    *,
    api_key: str | None,
    data_dir: Path,
    http_transport_factory: Callable[[], Any] | None = None,
) -> HyMemoryClientProtocol:
    configure_hy_memory_environment(data_dir)
    config = build_hy_memory_config(
        profile,
        api_key=api_key,
        data_dir=data_dir,
    )
    config.http_transport_factory = http_transport_factory
    reattach_legacy_vector_collection(profile, config)
    mismatch = inspect_graph_width_mismatch(config)
    if mismatch is not None:
        # Do not archive here. Archiving without a choice is what retired a
        # live 13MB graph once already; the caller must pick KEEP_EXISTING,
        # REBUILD_WITH_NEW, or EXPORT_FIRST through apply_graph_width_decision.
        raise GraphWidthMismatchError(mismatch)
    client_module = import_module("hy_memory.client")
    client_type = client_module.HyMemoryClient
    runtime = _shared_hy_runtime(config, data_dir)
    # `ultra` forces the kuzu graph store open regardless of
    # MEMORY_ENABLE_GRAPH (hy_memory/client.py), and tearing that database down
    # is where the packaged app segfaults: kuzu::main::Database::~Database()
    # checkpoints and faults in NullMask::setNullRange. Passing ultra here
    # undid the mode `build_hy_memory_config` had just chosen, so ask the same
    # switch again instead of hard-coding the constant.
    client_mode = HY_MEMORY_MODE if graph_store_enabled() else "pro"
    client = cast(
        HyMemoryClientProtocol,
        client_type(config=config, mode=client_mode, runtime=runtime),
    )
    native_close = client.close
    released = False

    def close_leased_client() -> None:
        nonlocal released
        if released:
            return
        released = True
        try:
            native_close()
        finally:
            _release_shared_hy_runtime(data_dir)

    cast(Any, client).close = close_leased_client
    # The pinned public SDK schedules duplicate cleanup after a search has already returned.
    # That turns a read into an unleased native-store write and invalidates the
    # full-node permission proof saved by the ingest which created the node.
    # MemHub owns lifecycle dedup in its governed store, so its HY read path must
    # remain side-effect free.
    cast(Any, client)._spawn_search_dedup = lambda *args, **kwargs: None
    embedding_provider = profile.embedding_provider_kind or profile.provider_kind
    if embedding_provider is ProviderKind.LOCAL_ONNX:
        # HY builds its own EmbedService with no injection point, so the local
        # encoder is swapped in after construction — in both places that hold
        # a reference. `client.__init__` hands its service to the component
        # factory (client.py:300) and every pipeline takes it from there
        # (registry.py:103/137/177), so replacing only the client's own
        # attribute left the writer on the original, dialling the placeholder
        # URL. That failed as a connection error with nothing extracted, and
        # the test asserting the swap passed throughout because it read the
        # attribute we had just written.
        local_embed_service = LocalHyEmbedService()
        client._embed_service = local_embed_service  # type: ignore[attr-defined]
        registry = getattr(client, "_registry", None)
        if registry is not None:
            registry._shared_embed_service = local_embed_service
            # And drop the pipelines HY pre-warmed. `client.__init__` builds
            # the System2 writer at client.py:332, before this code runs, so
            # that writer captured HY's own service and passed it to the inner
            # writer — and a cached pipeline is never rebuilt. Setting the two
            # references above without this changed nothing that mattered:
            # measured on a real import, still 67 connection errors and not
            # one memory. These are plain lazy caches; HY's own close() clears
            # the same three, and the next call rebuilds them with the service
            # set above.
            registry._system2_writer = None
            registry._writer = None
            registry._reader = None
            # The pinned HY SDK checks its named-reader cache before the legacy singleton.
            registry._readers.clear()
    from one_memory.hy_response_guard import install_hy_write_guards

    install_hy_write_guards(client)
    return client


def _warm_hy_write_dependencies() -> None:
    """Load CPU/disk-heavy helpers used synchronously by the first HY write."""
    embed_service = import_module("hy_memory.core.embed_service")
    embed_service._truncate_for_embedding("one-memory-warmup")


async def _coding_result(
    client: HyMemoryClientProtocol,
    request: HyMemoryIngestRequest,
    raw: Mapping[str, object],
) -> HyMemoryIngestResult:
    """HY's coding route: its experiences and working facts, read back from its store.

    The response only carries ids. Each row is attributed to the whole turn HY
    was given; HY does not say which message a coding row came from.
    """
    experience_ids = _string_tuple(raw.get("experience_ids"))
    fact_ids = _string_tuple(raw.get("fact_ids"))
    if not experience_ids and not fact_ids:
        return HyMemoryIngestResult(success=True)
    native = cast(Any, client)
    store = getattr(native, "_coding_store", None)
    if store is None:
        raise HyMemoryBridgeError("hy_memory_coding_store_missing")

    async def read() -> tuple[list[Any], list[Any]]:
        return (
            await store.get_many(list(experience_ids), user_id=request.user_id),
            await store.get_facts(list(fact_ids), user_id=request.user_id),
        )

    try:
        experiences, facts = await _joined_thread(native._loop_thread.run, read())
    except Exception as error:
        raise HyMemoryBridgeError("hy_memory_coding_read_failed") from error
    outputs: list[HyMemoryOutput] = []
    for row in experiences:
        if (row.user_id, row.agent_id) != (request.user_id, request.agent_id):
            continue
        content = "\n".join(
            part.strip() for part in (row.task, row.solution, row.boundary_envs) if part.strip()
        )
        if content:
            outputs.append(
                HyMemoryOutput(
                    memory_id=row.experience_id,
                    content=content,
                    layer=HY_CODING_EXPERIENCE_LAYER,
                    evidence_ids=request.evidence_ids,
                )
            )
    for row in facts:
        if (row.user_id, row.agent_id) != (request.user_id, request.agent_id):
            continue
        # details is HY's verbatim copy of the turn; the statement is the memory.
        if row.status == "active" and row.statement.strip():
            outputs.append(
                HyMemoryOutput(
                    memory_id=row.fact_id,
                    content=row.statement.strip(),
                    layer=HY_WORKING_FACT_LAYER,
                    evidence_ids=request.evidence_ids,
                )
            )
    return HyMemoryIngestResult(success=True, memories=tuple(outputs))


async def _with_native_mutation_delta(
    client: HyMemoryClientProtocol,
    result: HyMemoryIngestResult,
    raw: Mapping[str, object],
    reconcile_guards: Sequence[Any],
) -> HyMemoryIngestResult:
    """Map native SDK mutations back into MemHub governance.

    Store permission wrappers used to discover this by intercepting every
    native CRUD call. The embedded SDK now owns unrestricted local CRUD, so
    chat targets come from the reconcile observer and coding targets come from
    the SDK's public result document.
    """

    retired = set(result.retired_memory_ids)
    superseded = {
        key: tuple(values) for key, values in result.superseded_memory_ids.items()
    }
    chat_targets = {
        key
        for guard in reconcile_guards
        for key in getattr(guard, "mutation_target_ids", ())
        if isinstance(key, str) and key
    }

    for item in raw.get("ops", ()) if isinstance(raw.get("ops"), list) else ():
        if not isinstance(item, Mapping):
            continue
        if item.get("action") == "DELETE":
            key = item.get("target_experience_id")
            if isinstance(key, str) and key:
                retired.add(key)

    for item in raw.get("fact_ops", ()) if isinstance(raw.get("fact_ops"), list) else ():
        if not isinstance(item, Mapping) or item.get("action") != "SUPERSEDE":
            continue
        old, new = item.get("fact_id"), item.get("superseded_by")
        if isinstance(old, str) and old:
            retired.add(old)
            if isinstance(new, str) and new:
                superseded[old] = (new,)

    for key in chat_targets:
        record = await _joined_thread(_read_hy_record, client, key)
        if record is None:
            retired.add(key)
            continue
        status = str(record.get("status", "")).lower()
        if status in {"deleted", "shadow", "superseded"}:
            retired.add(key)
        replacement = record.get("superseded_by")
        if isinstance(replacement, str) and replacement:
            superseded[key] = (replacement,)
        elif isinstance(replacement, list):
            values = tuple(
                value for value in replacement if isinstance(value, str) and value
            )
            if values:
                superseded[key] = values

    return result.model_copy(
        update={
            "retired_memory_ids": tuple(sorted(retired)),
            "superseded_memory_ids": superseded,
        }
    )


def _read_hy_record(
    client: HyMemoryClientProtocol,
    memory_id: str,
) -> Mapping[str, object] | None:
    record = client.get(memory_id)
    if not record or "owner" in record or record.get("source") != "vdb":
        return record
    # The pinned HY SDK's get serializer omits owner although the stored node has it.
    native = cast(Any, client)
    node = native._loop_thread.run(native._vector_store.get_by_id(memory_id))
    if node is None or node.content != record.get("content"):
        raise HyMemoryBridgeError("hy_memory_record_changed_during_read")
    if node.user_id != record.get("user_id") or node.agent_id != record.get("agent_id"):
        raise HyMemoryBridgeError("hy_memory_record_scope_mismatch")
    return {**record, "owner": node.owner}


#: Reserved, and shaped so it cannot collide with a stored profile: those are
#: uuid4. The bridge cache is keyed by `(profile_id, revision)`, and a
#: collision would hand the search-only bridge back to extraction after the
#: user finally configured a model — which looks, from outside, like pasting
#: a key changed nothing at all.
BUILTIN_RETRIEVAL_PROFILE_ID = "builtin-retrieval"

#: The LLM half is never called on this path and the endpoint is never
#: reached, but HY wants the shape filled in. Same placeholder the embedder
#: uses a few lines up, for the same reason.
_LOCAL_ONNX_PLACEHOLDER_URL = "http://127.0.0.1/local-onnx"


def builtin_retrieval_snapshot() -> ModelProfileSnapshot:
    """A profile that can read the vector store and generate nothing.

    Searching needs an embedding and a collection name; it does not need a
    generation model or a key. Spelling that out as a snapshot keeps the
    read path on the same code as everything else rather than a second way
    of opening the same store.
    """
    from one_memory.embedding_assets import (
        BUILTIN_EMBEDDING_DIMENSIONS,
        BUILTIN_EMBEDDING_MODEL,
    )

    return ModelProfileSnapshot(
        profile_id=BUILTIN_RETRIEVAL_PROFILE_ID,
        revision=0,
        provider_kind=ProviderKind.LOCAL_ONNX,
        base_url=_LOCAL_ONNX_PLACEHOLDER_URL,
        model=BUILTIN_EMBEDDING_MODEL,
        embedding_provider_kind=ProviderKind.LOCAL_ONNX,
        embedding_model=BUILTIN_EMBEDDING_MODEL,
        embedding_dimensions=BUILTIN_EMBEDDING_DIMENSIONS,
        timeout_seconds=60,
        max_concurrency=1,
        context_window=None,
        has_api_key=False,
    )


def hy_memory_store_exists(data_dir: Path) -> bool:
    """Whether extraction has ever written a store here."""
    return (data_dir / "hy-memory" / HY_MEMORY_NAMESPACE / "graph").exists()


class LocalHyEmbedService:
    """Drop-in for HY's EmbedService that runs the built-in ONNX model.

    Covers the methods HY calls on an embed service, counted from HY's own
    source rather than assumed. An earlier version implemented only `embed`, on
    the strength of a comment saying that was all HY used — and the version
    installed here calls `embed_queued` from thirty places in the writer.
    A missing method here does not surface as a missing method; it leaves HY
    on its own service pointed at a placeholder URL, and extraction ends in
    a connection error having produced nothing. test_local_onnx_provider
    counts the surface so the next HY upgrade reports itself.

    No queue, unlike HY's: batching exists to amortise a network round trip,
    and there is no round trip here. Cache is accepted and ignored for the
    same reason — LocalEmbedder is cheap once the session is warm, and HY's
    cache would double-store every vector.
    """

    _shared_embedder: Any | None = None
    _shared_embedder_lock = threading.Lock()

    def __init__(self, embedder: Any | None = None) -> None:
        if embedder is None:
            from one_memory.local_embedding import LocalEmbedder

            with self._shared_embedder_lock:
                if self._shared_embedder is None:
                    self.__class__._shared_embedder = LocalEmbedder()
                embedder = self._shared_embedder
        if embedder is None:
            raise RuntimeError("local_embedder_unavailable")
        self._embedder = embedder

    async def embed(self, text: str, use_cache: bool = True) -> list[float]:
        del use_cache
        vectors = await asyncio.to_thread(self._embedder.encode, [text])
        return cast(list[float], vectors[0])

    async def embed_queued(self, text: str, use_cache: bool = True) -> list[float]:
        return await self.embed(text, use_cache)

    async def embed_queued_with_meta(
        self,
        text: str,
        use_cache: bool = True,
    ) -> tuple[list[float], dict[str, Any]]:
        return await self.embed_with_meta(text, use_cache)

    async def embed_batch(
        self,
        texts: list[str],
        use_cache: bool = True,
    ) -> list[list[float]]:
        del use_cache
        if not texts:
            return []
        vectors = await asyncio.to_thread(self._embedder.encode, list(texts))
        return [cast(list[float], vector) for vector in vectors]

    async def embed_batch_with_meta(
        self,
        texts: list[str],
        use_cache: bool = True,
    ) -> tuple[list[list[float]], list[dict[str, Any]]]:
        vectors = await self.embed_batch(texts, use_cache)
        return vectors, [{"provider": "local-onnx"} for _ in vectors]

    async def embed_with_meta(
        self,
        text: str,
        use_cache: bool = True,
    ) -> tuple[list[float], dict[str, Any]]:
        # HY reads provider metadata off this for its own tracing. There is no
        # provider and no request, so the honest answer is a local marker
        # rather than invented status codes.
        return await self.embed(text, use_cache), {"provider": "local-onnx"}

    def get_embedding_dims(self) -> int:
        from one_memory.embedding_assets import BUILTIN_EMBEDDING_DIMENSIONS

        return int(BUILTIN_EMBEDDING_DIMENSIONS)

    def clear_cache(self) -> None:
        return None

    def get_stats(self) -> dict[str, Any]:
        return {"provider": "local-onnx", "cache_hits": 0, "embed_count": 0}


def build_hy_memory_config(
    profile: ModelProfileSnapshot,
    *,
    api_key: str | None,
    data_dir: Path,
) -> Any:
    try:
        config_module = import_module("hy_memory.config")
    except ModuleNotFoundError as error:
        raise HyMemoryBridgeError("hy_memory_sdk_unavailable") from error
    config = config_module.MemoryConfig.from_env()
    config.debug = False
    config.metrics_enabled = False
    if profile.provider_kind is ProviderKind.HY_DEFAULT:
        raise UnsupportedHyMemoryProviderError("hy_default_not_available")
    if profile.provider_kind is ProviderKind.ANTHROPIC:
        raise UnsupportedHyMemoryProviderError("anthropic_provider_adapter_not_available")
    if profile.provider_kind is ProviderKind.OPENAI_COMPATIBLE and not api_key:
        raise HyMemoryBridgeError("model_api_key_missing")
    if not profile.embedding_model:
        raise HyMemoryBridgeError("embedder_not_configured")

    base_url = _provider_base_url(profile.provider_kind, profile.base_url)
    provider_key = api_key or "ollama"
    # The real-model acceptance measures ONE honest attempt. The SDK's built-in
    # retry loop would multiply both the spend and the request count behind the
    # caller's back, so the acceptance run opts out of it via this env flag.
    # The product default is unchanged: this is scoped to the isolated run.
    single_attempt = os.environ.get("ONE_MEMORY_ACCEPTANCE_SINGLE_ATTEMPT") == "1"
    config.llm.provider = "openai"
    config.llm.model = profile.model
    config.llm.api_key = provider_key
    config.llm.base_url = base_url
    config.llm.timeout = profile.timeout_seconds
    config.llm.max_retries = 0 if single_attempt else 2
    config.llm.retry_delay = 0.0 if single_attempt else 1.0
    config.llm.temperature = 0
    # Output capacity is independent of context size. Mixed-author windows need
    # more space than HY's 2K default; truncation is still a hard failure.
    config.llm.agent_max_tokens = 8192
    config.llm.enable_summary = False
    config.llm.enable_query_rewrite = False
    config.llm.few_shot_enabled = True
    config.llm.enable_intention = False
    config.llm.extra_headers = None
    config.llm.extra_body = attach_disable_thinking(profile, {}) or None
    _write_memories_in_chinese(config.llm)
    # `context_window` describes model input capacity. HY Memory's
    # `max_tokens` fields cap generated output and have safe extraction
    # defaults (1024/2000). Copying a 16K context window into those fields
    # made each atomic cold-start extraction run for minutes.

    embedding_provider = profile.embedding_provider_kind or profile.provider_kind
    dimensions: int | None
    if embedding_provider is ProviderKind.LOCAL_ONNX:
        # Local embedding never leaves the machine. HY still wants an
        # EmbedderConfig shape, so we fill placeholder fields; the real
        # encoder is swapped onto the client after construction (see
        # create_hy_memory_client). The shared-origin credential rule does
        # not apply here for the same reason.
        from one_memory.embedding_assets import (
            BUILTIN_EMBEDDING_DIMENSIONS,
            BUILTIN_EMBEDDING_MODEL,
        )

        config.embedder.provider = "openai"
        config.embedder.model = profile.embedding_model or BUILTIN_EMBEDDING_MODEL
        config.embedder.api_key = "local-onnx"
        config.embedder.base_url = "http://127.0.0.1/local-onnx"
        config.embedder.timeout = profile.timeout_seconds
        config.embedder.max_retries = 0
        config.embedder.retry_delay = 0.0
        config.embedder.extra_headers = None
        config.embedder.extra_body = None
        dimensions = profile.embedding_dimensions or BUILTIN_EMBEDDING_DIMENSIONS
        config.embedder.embedding_dims = dimensions
        embedding_base_url = "local-onnx"
    else:
        embedding_base_url = _provider_base_url(
            embedding_provider,
            profile.embedding_base_url or profile.base_url,
        )
        embedding_key = "ollama" if embedding_provider is ProviderKind.OLLAMA else api_key
        if not embedding_key:
            raise HyMemoryBridgeError("embedding_api_key_missing")
        config.embedder.provider = "openai"
        config.embedder.model = profile.embedding_model
        config.embedder.api_key = embedding_key
        config.embedder.base_url = embedding_base_url
        config.embedder.timeout = profile.timeout_seconds
        config.embedder.max_retries = 2
        config.embedder.retry_delay = 0.3
        config.embedder.extra_headers = None
        config.embedder.extra_body = None
        if profile.embedding_dimensions is not None:
            config.embedder.embedding_dims = profile.embedding_dimensions
        dimensions = profile.embedding_dimensions

    memory_root = data_dir / "hy-memory" / HY_MEMORY_NAMESPACE
    memory_root.mkdir(parents=True, exist_ok=True)
    config.vector_store.provider = "chroma"
    config.vector_store.collection_name = _collection_name(
        embedding_provider,
        embedding_base_url,
        profile.embedding_model or "bge-small-zh-v1.5",
    )
    config.vector_store.persist_directory = str(memory_root / "vector")
    if dimensions is not None:
        config.vector_store.embedding_dims = dimensions
    config.vector_store.host = None
    config.vector_store.port = None
    config.vector_store.api_key = None
    # See `graph_store_enabled`: `ultra` forces the graph open, so switching it
    # off means moving the mode too.
    if graph_store_enabled():
        config.mode = HY_MEMORY_MODE
        config.graph_store.provider = "kuzu"
        config.graph_store.db_path = str(memory_root / "graph")
    else:
        config.mode = "pro"
        config.graph_store.provider = "kuzu"
        # `pro` reads MEMORY_ENABLE_GRAPH from the environment and defaults it
        # off; set it anyway so an inherited value cannot turn the graph on.
        os.environ["MEMORY_ENABLE_GRAPH"] = "false"
        # Moving off `ultra` is not enough on its own. `graph_data_may_exist`
        # ignores the mode: the list and purge paths open an ephemeral kuzu
        # Database whenever db_path still holds data, and tearing that Database
        # down is the same NullMask::setNullRange segfault. With the graph
        # closed, point it at a path that never has data so those paths skip it
        # instead of re-opening the crash.
        config.graph_store.db_path = str(memory_root / "graph-disabled")
    config.graph_store.url = None
    config.graph_store.username = None
    config.graph_store.password = None
    config.graph_store.database = None
    config.graph_store.graph_rerank_enabled = False
    config.graph_store.temporal_spine = True
    config.graph_store.topic_summary_enabled = False
    if profile.embedding_dimensions is None:
        # A snapshot that does not state its embedding width used to let HY
        # apply its own default, and a default that disagrees with the store
        # retires the store: on this machine a snapshot from before the field
        # existed archived a 13MB graph and opened an empty one in its place.
        # The store knows its own geometry, so follow it rather than guess.
        stored_width = _graph_embedding_width(Path(config.graph_store.db_path))
        if stored_width is not None:
            config.embedder.embedding_dims = stored_width
            config.vector_store.embedding_dims = stored_width
    config.cache.backend = "sqlite"
    config.cache.db_path = str(memory_root / "cache.sqlite3")
    config.cache.mysql_host = None
    config.cache.mysql_port = None
    config.cache.mysql_user = None
    config.cache.mysql_password = None
    config.cache.mysql_database = None
    config.api.api_keys = []
    config.llm.eval_user = None
    config.llm.eval_apikey = None
    config.history.enable = True
    config.history.record_searches = False
    config.history.db_path = str(memory_root / "history.sqlite3")
    config.coding.enable = True
    config.coding.db_path = str(memory_root / "coding.sqlite3")
    config.coding.tool_result_max_bytes = coding_tool_result_max_bytes()
    config.recall.episode_recall_enabled = True
    config.recall.episode_summary_enabled = False
    config.recall.dynamic_episode_enabled = True
    config.recall.session_episode_enabled = False
    config.recall.session_episode_context_enabled = False
    config.recall.episode_capsule_context_enabled = True
    config.recall.episode_capsule_query_min = 0.65
    config.recall.episode_capsule_assignment_min = 0.60
    config.recall.episode_capsule_first_rank_max = 3
    config.recall.episode_capsule_member_limit = 3
    config.recall.strength_enabled = False
    config.recall.access_tracking_enabled = False
    config.recall.local_rerank_enabled = False
    return config


#: The one rule in HY's extraction system prompts that hands the language of
#: someone's memories to whatever they happened to paste in. Both language
#: variants carry it, with this exact wording in the Chinese one.
_HY_FOLLOWS_THE_INPUT_LANGUAGE = "* 输出语言必须与输入语言一致"

_ALWAYS_CHINESE = (
    "* 输出语言必须始终是简体中文，即使输入是英文、代码或报错信息\n"
    "* 记忆语言是用户属性，不随单条输入的语言变化"
)

#: HY's extraction principle reads only the user's messages, although its own
#: OWNER and subject rules already say how to keep an AI assistant's words
#: apart from the person's. MemHub records both speakers, so only this line
#: changes; who said what stays with HY's rules.
_HY_READS_ONLY_THE_USER = "只提取 NEW user messages 中首次出现的新信息。"

_BOTH_SPEAKERS = (
    "只提取 NEW messages 中首次出现的新信息：既提取用户说的，"
    "也提取 AI 助手提供的有长期价值的信息（推荐、确认、查到的资料、方案与结论），"
    "按下方主体规则与 OWNER 标明是谁说的。"
)


def _write_memories_in_chinese(llm_config: Any) -> None:
    """Ask HY for Chinese memories whichever language it thinks the input is.

    HY picks the extraction prompt per call from the content itself:
    `is_chinese(content)` at `hy_memory/agent/extractor.py:868`, a CJK
    character ratio with a 0.3 threshold. A Chinese question about a pasted
    English traceback measures well under it, so it takes the English branch
    and the memory is written in English — for a user whose library is
    otherwise Chinese, and who was never asked.

    Two things had to be worked around, both read out of HY's source rather
    than assumed:

    The lever named for this, `extract_system_prompt_zh`, is read *inside*
    the `is_chinese(...)` true branch (`extractor.py:891-901`), so it only
    applies where nothing was wrong. The branch that actually runs reads
    `extract_system_prompt_en` (`extractor.py:922-930`).

    And HY's own Chinese prompt says "输出语言必须与输入语言一致", so serving
    it verbatim would leave the model following the input language anyway —
    a Chinese prompt producing an English memory. So HY's text is kept for
    everything it defines (schema, owner rules, tags, and the rules that
    keep terms and code exactly as pasted) and only that single line is
    replaced. Deriving it at runtime rather than copying the prompt means an
    SDK bump brings HY's improvements with it; if the line ever moves, the
    override is appended instead so the language still holds, and the tests
    pin the line's presence so we hear about it.

    The data-injection template stays HY's built-in English one — the
    override hook is system-prompt-only (`config.py:166-170`) — and both
    language variants take the same four fields and carry the same JSON
    contract, so nothing is lost. The content itself is never touched:
    prefixing it to push the ratio over the threshold would work, and is
    exactly what must not happen to a verbatim.
    """
    try:
        prompts_zh = import_module("hy_memory.agent.prompts_zh")
    except ModuleNotFoundError as error:  # pragma: no cover - SDK guaranteed above
        raise HyMemoryBridgeError("hy_memory_sdk_unavailable") from error
    hy_prompt = prompts_zh.EXTRACT_SYSTEM_PROMPT_ZH
    if _HY_FOLLOWS_THE_INPUT_LANGUAGE in hy_prompt:
        prompt = hy_prompt.replace(_HY_FOLLOWS_THE_INPUT_LANGUAGE, _ALWAYS_CHINESE)
    else:
        logging.getLogger(__name__).warning("hy_language_rule_not_found_appending_override")
        prompt = f"{hy_prompt}\n\n# 语言规则（MemHub）\n\n{_ALWAYS_CHINESE}\n"
    if _HY_READS_ONLY_THE_USER in prompt:
        prompt = prompt.replace(_HY_READS_ONLY_THE_USER, _BOTH_SPEAKERS)
    else:
        logging.getLogger(__name__).warning("hy_extraction_scope_rule_not_found_appending_override")
        prompt = f"{prompt}\n\n# 提取范围（MemHub）\n\n{_BOTH_SPEAKERS}\n"
    llm_config.extract_system_prompt_en = prompt
    llm_config.extract_system_prompt_zh = prompt


def _provider_base_url(provider: ProviderKind, value: str | None) -> str:
    base_url = (value or "").rstrip("/")
    if provider is ProviderKind.OLLAMA and not base_url.endswith("/v1"):
        return f"{base_url}/v1"
    return base_url


def _safe_error_code(result: Mapping[str, object]) -> str:
    value = result.get("error_code")
    allowed = {
        "auth_error",
        "invalid_request",
        "model_error",
        "rate_limit",
        "timeout",
        "unavailable",
    }
    if isinstance(value, str) and value in allowed:
        return f"hy_memory_{value}"
    return "hy_memory_unknown_error"


def _safe_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _basic_info_memories(raw: object) -> tuple[Mapping[str, object], ...]:
    """Parse the basic-info reply, which is shaped unlike the listing reply.

    `list_memories` wraps its rows in `vdb`; `get_user_basic_info` returns
    them at the top level. Reading one with the other's parser is how the
    profile came back empty from a store that visibly held three of them —
    so an unrecognised payload is an error here rather than no memories. A
    person we have learnt nothing about yet is an empty list, which is a
    different thing and stays quiet.
    """
    if not isinstance(raw, Mapping):
        raise HyMemoryBridgeError("hy_memory_profile_shape_unknown")
    memories = raw.get("memories")
    if not isinstance(memories, list):
        raise HyMemoryBridgeError("hy_memory_profile_shape_unknown")
    return tuple(memory for memory in memories if isinstance(memory, Mapping))


def _listed_memories(raw: object) -> tuple[Mapping[str, object], ...]:
    if isinstance(raw, Mapping):
        vdb = raw.get("vdb")
        if isinstance(vdb, Mapping):
            return _rows(vdb.get("memories"), "hy_memory_list_shape_unknown")
    return _replied_memories(raw, "hy_memory_list_shape_unknown")


def _replied_memories(raw: object, error_code: str) -> tuple[Mapping[str, object], ...]:
    """Select the explicit response scene without merging chat and coding rows.

    HY's documented productivity response may also include empty chat groups.
    normal/chat selects memories; productivity/working selects coding_memories.
    Older replies without a scene must expose exactly one channel. Chat accepts
    its flat list or all three named groups; malformed rows are errors, not empty
    recall. This response adapter does not change query or write routing.
    """
    if not isinstance(raw, Mapping):
        raise HyMemoryBridgeError(error_code)
    keys = tuple(key for key in ("memories", "coding_memories") if key in raw)
    if "scene" in raw:
        scene = raw["scene"]
        if not isinstance(scene, str):
            raise HyMemoryBridgeError(error_code)
        if scene in ("normal", "chat"):
            selected = "memories"
        elif scene in ("productivity", "working"):
            selected = "coding_memories"
        else:
            raise HyMemoryBridgeError(error_code)
        if selected not in keys:
            raise HyMemoryBridgeError(error_code)
    elif len(keys) == 1:
        selected = keys[0]
    else:
        raise HyMemoryBridgeError(error_code)

    result: list[Mapping[str, object]] = []
    for key in keys:
        value = raw[key]
        if key == "memories" and isinstance(value, Mapping):
            if set(value) != {"schema", "proactive", "normal"}:
                raise HyMemoryBridgeError(error_code)
            channels = tuple(value.values())
        else:
            channels = (value,)
        for channel in channels:
            if not isinstance(channel, list) or any(
                not isinstance(row, Mapping) for row in channel
            ):
                raise HyMemoryBridgeError(error_code)
            if key == selected:
                result.extend(channel)
    return tuple(result)


def _rows(value: object, error_code: str) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, list):
        raise HyMemoryBridgeError(error_code)
    return tuple(row for row in value if isinstance(row, Mapping))


def _hy_score(memory: Mapping[str, object]) -> float:
    """HY's own relevance score, which was among the fields being dropped.

    Bucketing hits into channels loses the single ranking HY had put them in,
    and recall reads the order these ids arrive in as their rank. Every hit
    still carries the score HY ranked it by, so restoring HY's order is a
    matter of reading a field we already had rather than inventing a
    preference between channels here.
    """
    score = memory.get("score")
    return float(score) if isinstance(score, (int, float)) else 0.0


def _formatted_member_ids(memory: Mapping[str, object]) -> tuple[tuple[str, ...], bool]:
    """Return every id represented inside SDK-formatted content.

    Plain memories have no nested members and are complete by definition.
    Composite/evolved memories are usable only when the SDK also exposes all
    member ids, so One Memory can re-apply current governance to every line.
    """
    represented: dict[str, None] = {}
    composite = False

    raw_members = memory.get("member_ids")
    if raw_members is not None:
        composite = True
        if not isinstance(raw_members, list | tuple) or any(
            not isinstance(member_id, str) or not member_id for member_id in raw_members
        ):
            return (), False
        for member_id in raw_members:
            represented.setdefault(member_id, None)

    chain = memory.get("evolution_chain")
    if chain is not None:
        composite = True
        if not isinstance(chain, list | tuple):
            return (), False
        for entry in chain:
            if not isinstance(entry, Mapping):
                return (), False
            member_id = entry.get("node_id") or entry.get("memory_id")
            if not isinstance(member_id, str) or not member_id:
                return (), False
            represented.setdefault(member_id, None)

    # These public fields announce composite rendering. If no complete member
    # id list accompanied them, the text cannot be safely released as a unit.
    if memory.get("capsule_memories") is not None or memory.get("timeline") is not None:
        composite = True
        if raw_members is None:
            return (), False

    content = memory.get("content")
    if isinstance(content, str) and content.lstrip().startswith(
        ("Related memories:", "Matched memories:", "Memory timeline:")
    ):
        composite = True
        if not represented:
            return (), False

    return tuple(represented), not composite or bool(represented)


def _read_graph_snapshot(
    client: HyMemoryClientProtocol,
    user_id: str,
    agent_id: str,
    limit: int,
) -> tuple[list[list[object]], list[list[object]]]:
    concrete = cast(Any, client)
    graph_store = concrete._graph_store
    if graph_store is None:
        return ([], [])
    loop_thread = concrete._loop_thread
    # Kuzu rejects a prepared statement that is handed a parameter its text
    # never references, so a wildcard read must not bind agent_id at all.
    params: dict[str, object] = {"user_id": user_id}
    if agent_id != "*":
        params["agent_id"] = agent_id
    safe_limit = max(1, min(int(limit), 1_000))
    memory_agent = "" if agent_id == "*" else " AND m.agent_id = $agent_id"
    related_agent = (
        "" if agent_id == "*" else " AND a.agent_id = $agent_id AND b.agent_id = $agent_id"
    )
    source_agent = "" if agent_id == "*" else " AND a.agent_id = $agent_id"
    nodes = (
        loop_thread.run(
            graph_store._run(
                f"""
            MATCH (m:Memory)
            WHERE m.user_id = $user_id{memory_agent}
              AND m.status = 'active'
            RETURN m.node_id, m.content, m.layer, m.confidence
            LIMIT {safe_limit}
            """,
                params,
            )
        )
        or []
    )
    related_edges = (
        loop_thread.run(
            graph_store._run(
                f"""
            MATCH (a:Memory)-[r:RELATED_TO]->(b:Memory)
            WHERE a.user_id = $user_id{related_agent}
              AND b.user_id = $user_id
              AND a.status = 'active' AND b.status = 'active'
            RETURN a.node_id, b.node_id, r.relation_type, r.weight
            LIMIT {safe_limit * 4}
            """,
                params,
            )
        )
        or []
    )
    derived_edges = (
        loop_thread.run(
            graph_store._run(
                f"""
            MATCH (a:Memory)-[:DERIVED_FROM]->(v:VdbRef)
            WHERE a.user_id = $user_id{source_agent}
              AND a.status = 'active'
            RETURN a.node_id, v.node_id, 'derived_from', 1.0
            LIMIT {safe_limit * 4}
            """,
                params,
            )
        )
        or []
    )
    topic_nodes = (
        loop_thread.run(
            graph_store._run(
                f"""
            MATCH (m:Memory)-[:TAGGED_WITH]->(t:Topic)
            WHERE m.user_id = $user_id{memory_agent}
              AND m.status = 'active'
            RETURN DISTINCT t.topic_id, t.name, 'TOPIC', 1.0
            LIMIT {safe_limit}
            """,
                params,
            )
        )
        or []
    )
    tagged_edges = (
        loop_thread.run(
            graph_store._run(
                f"""
            MATCH (m:Memory)-[:TAGGED_WITH]->(t:Topic)
            WHERE m.user_id = $user_id{memory_agent}
              AND m.status = 'active'
            RETURN m.node_id, t.topic_id, 'tagged_with', 1.0
            LIMIT {safe_limit * 4}
            """,
                params,
            )
        )
        or []
    )
    return (
        [*nodes, *topic_nodes],
        [*related_edges, *derived_edges, *tagged_edges],
    )


def _record_matches_request(
    record: Mapping[str, object],
    request: HyMemoryIngestRequest,
) -> bool:
    custom = record.get("custom")
    return (
        isinstance(custom, Mapping)
        and custom.get("request_id") == request.request_id
        and record.get("user_id") == request.user_id
        and record.get("agent_id") == request.agent_id
    )


def _exact_content_records(
    value: object,
    expected_content: str,
) -> list[Mapping[str, object]]:
    records: list[Mapping[str, object]] = []
    if isinstance(value, Mapping):
        if value.get("content") == expected_content and isinstance(value.get("memory_id"), str):
            records.append(value)
        for nested in value.values():
            records.extend(_exact_content_records(nested, expected_content))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for nested in value:
            records.extend(_exact_content_records(nested, expected_content))
    return records


def _collection_name(
    embedding_provider: ProviderKind,
    embedding_base_url: str,
    embedding_model: str,
    *,
    declared_dimensions: str | None = None,
) -> str:
    """Name the collection after the embedding space that fills it.

    The declared width is deliberately not part of the identity: the store
    already carries the real width, and folding a value we merely *learn*
    into the name would strand every memory written before we measured it.
    """
    parts = [embedding_provider.value, embedding_base_url, embedding_model]
    if declared_dimensions is not None:
        parts.append(declared_dimensions)
    suffix = hashlib.sha256(":".join(parts).encode()).hexdigest()[:12]
    return f"one_memory_{suffix}"


def reattach_legacy_vector_collection(profile: ModelProfileSnapshot, config: Any) -> str | None:
    """Rename a collection this embedding space filled under an older name.

    Returns the migrated legacy name, or None when nothing had to move.
    """
    persist = getattr(config.vector_store, "persist_directory", None)
    if not persist:
        return None
    database = Path(persist) / "chroma.sqlite3"
    if not database.exists():
        return None
    embedding_provider = profile.embedding_provider_kind or profile.provider_kind
    # The config holds the normalized endpoint the collection was named after.
    embedding_base_url = getattr(config.embedder, "base_url", "") or ""
    if not profile.embedding_model:
        return None
    expected = config.vector_store.collection_name
    legacy_names = [
        _collection_name(
            embedding_provider,
            embedding_base_url,
            profile.embedding_model,
            declared_dimensions=declared,
        )
        for declared in ("auto", str(profile.embedding_dimensions or "auto"))
    ]
    legacy_names = [name for name in dict.fromkeys(legacy_names) if name != expected]
    if not legacy_names:
        return None
    try:
        with closing(sqlite3.connect(database)) as connection, connection:
            rows = connection.execute("SELECT id, name, dimension FROM collections").fetchall()
            stored = {
                str(name): (str(identifier), dimension) for identifier, name, dimension in rows
            }
            if any(str(name).startswith(f"{expected}_") for name in stored):
                return None
            for legacy in legacy_names:
                match = next(
                    (name for name in stored if name.startswith(f"{legacy}_")),
                    None,
                )
                if match is None:
                    continue
                _identifier, dimension = stored[match]
                width = getattr(config.vector_store, "embedding_dims", None)
                if dimension is None or (width and int(dimension) != int(width)):
                    continue
                renamed = f"{expected}_{int(dimension)}"
                connection.execute(
                    "UPDATE collections SET name = ? WHERE name = ?",
                    (renamed, match),
                )
                logging.getLogger(__name__).warning(
                    "reattached hy vector collection %s as %s", match, renamed
                )
                return match
    except sqlite3.Error:
        return None
    return None


def inspect_graph_width_mismatch(config: Any) -> GraphWidthMismatch | None:
    """Return the mismatch if an existing graph disagrees with the model width.

    Never renames or deletes anything. Callers that want to proceed with a new
    width must go through `apply_graph_width_decision`.
    """
    db_path = Path(config.graph_store.db_path)
    if not db_path.exists():
        return None
    expected = getattr(config.vector_store, "embedding_dims", None) or getattr(
        getattr(config, "embedder", None), "embedding_dims", None
    )
    if not expected:
        return None
    stored = _graph_embedding_width(db_path)
    if stored is None or stored == int(expected):
        return None
    return GraphWidthMismatch(
        stored_width=int(stored),
        expected_width=int(expected),
        graph_path=str(db_path),
    )


def inspect_profile_graph_width(
    profile: ModelProfileSnapshot,
    *,
    api_key: str | None,
    data_dir: Path,
) -> GraphWidthMismatch | None:
    """The mismatch a client create would raise on, without creating one.

    Same config as `create_hy_memory_client` builds, so the answer the app
    shows is the answer extraction will hit. Read-only.
    """
    return inspect_graph_width_mismatch(
        _graph_width_config(profile, api_key=api_key, data_dir=data_dir)
    )


def decide_profile_graph_width(
    profile: ModelProfileSnapshot,
    *,
    api_key: str | None,
    data_dir: Path,
    decision: GraphWidthDecision,
) -> GraphWidthOutcome:
    """Act on the mismatch for this profile.

    Exports land beside the data directory rather than inside it, because a
    rebuild rewrites what is inside.
    """
    return apply_graph_width_decision(
        _graph_width_config(profile, api_key=api_key, data_dir=data_dir),
        decision,
        export_root=data_dir.parent,
    )


def _graph_width_config(
    profile: ModelProfileSnapshot,
    *,
    api_key: str | None,
    data_dir: Path,
) -> Any:
    configure_hy_memory_environment(data_dir)
    config = build_hy_memory_config(profile, api_key=api_key, data_dir=data_dir)
    reattach_legacy_vector_collection(profile, config)
    return config


def apply_graph_width_decision(
    config: Any,
    decision: GraphWidthDecision,
    *,
    export_root: Path | None = None,
) -> GraphWidthOutcome:
    """Act on a width mismatch the way the user chose.

    KEEP_EXISTING changes nothing. REBUILD_WITH_NEW archives the graph.
    EXPORT_FIRST copies it and leaves the question open — it is preparation
    for a decision, not one. Calling this without a mismatch is a no-op.
    """
    mismatch = inspect_graph_width_mismatch(config)
    if mismatch is None:
        return GraphWidthOutcome()
    if decision is GraphWidthDecision.REBUILD_WITH_NEW:
        archive = retire_mismatched_graph_store(config)
        return GraphWidthOutcome(archived_path=str(archive) if archive is not None else None)
    if decision is GraphWidthDecision.EXPORT_FIRST:
        if export_root is None:
            raise HyMemoryBridgeError("export_root_required")
        return GraphWidthOutcome(
            exported_path=str(export_graph_store(config, export_root=export_root))
        )
    return GraphWidthOutcome()


def export_graph_store(config: Any, *, export_root: Path) -> Path:
    """Copy the graph somewhere a rebuild cannot reach, all or nothing.

    Two things this must not do. It must not write inside the data directory,
    because REBUILD_WITH_NEW rewrites that directory and the "backup" would go
    with it. And it must not leave a partial copy behind on failure: a
    half-written graph is indistinguishable from a good one at a glance, so
    the user would rebuild on the strength of it. Staged under a `.partial`
    name and renamed only once every file is across.
    """
    db_path = Path(config.graph_store.db_path)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = export_root / f"one-memory-graph-{stamp}"
    staging = destination.with_name(f"{destination.name}.partial")
    sources = [db_path] + [
        sibling
        for suffix in (".wal", ".shadow", ".tmp")
        if (sibling := db_path.with_name(f"{db_path.name}{suffix}")).exists()
    ]
    try:
        staging.mkdir(parents=True)
        for source in sources:
            shutil.copy2(source, staging / source.name)
        staging.rename(destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    logging.getLogger(__name__).info(
        "exported hy graph store to %s (%d files)",
        destination,
        len(sources),
    )
    return destination


def retire_mismatched_graph_store(config: Any) -> Path | None:
    """Archive a Kuzu graph whose embedding width no longer matches the model.

    Only call this through `apply_graph_width_decision(REBUILD_WITH_NEW)`.
    Direct callers used to run this unconditionally on client create, which is
    how a live graph got retired without anyone choosing to.
    """
    db_path = Path(config.graph_store.db_path)
    if not db_path.exists():
        return None
    expected = getattr(config.vector_store, "embedding_dims", None) or getattr(
        getattr(config, "embedder", None), "embedding_dims", None
    )
    if not expected:
        return None
    stored = _graph_embedding_width(db_path)
    if stored is None or stored == int(expected):
        return None
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    archive = db_path.with_name(f"{db_path.name}.dim{stored}.{stamp}.bak")
    db_path.rename(archive)
    for suffix in (".wal", ".shadow", ".tmp"):
        sibling = db_path.with_name(f"{db_path.name}{suffix}")
        if sibling.exists():
            sibling.rename(archive.with_name(f"{archive.name}{suffix}"))
    logging.getLogger(__name__).warning(
        "retired hy graph store with embedding width %s (model expects %s): %s",
        stored,
        expected,
        archive.name,
    )
    return archive


# The graph store's embedding width is a property of the file, and reading it
# used to open a kuzu Database on every call and then `del` it plus
# `gc.collect()`. kuzu is a C++ library with its own background threads, so
# repeatedly building and tearing that object down corrupted the heap: 25 of
# 26 crash reports pointed at kuzu, three of them at
# `kuzu::common::NullMask::setNullRange`, and four aborted inside malloc with
# `free_list_checksum_botch`. Cache the answer per resolved path instead, and
# never open a second Database for a path already known.
_GRAPH_WIDTH_CACHE: dict[str, int | None] = {}
_GRAPH_WIDTH_LOCK = threading.Lock()


def _graph_embedding_width(db_path: Path) -> int | None:
    try:
        kuzu = import_module("kuzu")
    except ModuleNotFoundError:
        return None
    key = str(db_path.resolve())
    with _GRAPH_WIDTH_LOCK:
        if key in _GRAPH_WIDTH_CACHE:
            return _GRAPH_WIDTH_CACHE[key]
    try:
        database = kuzu.Database(str(db_path), read_only=True)
    except Exception:
        # A store we cannot open has no width to report; do not cache the
        # failure, because the file may still be being created.
        return None
    width: int | None = None
    try:
        connection = kuzu.Connection(database)
        result = connection.execute("CALL TABLE_INFO('Memory') RETURN *")
        while result.has_next():
            row = result.get_next()
            if len(row) < 3 or row[1] != "embedding":
                continue
            match = re.search(r"\[(\d+)\]", str(row[2]))
            width = int(match.group(1)) if match else None
            break
    except Exception:
        width = None
    with _GRAPH_WIDTH_LOCK:
        _GRAPH_WIDTH_CACHE[key] = width
    return width


def configure_hy_memory_environment(data_dir: Path) -> None:
    configure_hy_runtime_environment(data_dir)
    apply_diagnostic_log_level(diagnostic_log_level(data_dir))
