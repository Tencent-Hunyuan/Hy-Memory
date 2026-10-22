import asyncio
import json
import logging
import os
import re
import sqlite3
import threading
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Collection,
    Generator,
    Mapping,
)
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from time import monotonic
from typing import Literal, Protocol
from weakref import WeakKeyDictionary

from one_memory.background_work import in_the_background
from one_memory.diagnostic_log import (
    diagnostic_event,
    diagnostic_exception,
    elapsed_ms,
    record_database_error,
)
from one_memory.exception_reporting import report_exception
from one_memory.extraction_settings import ExtractionSettings, ExtractionSettingsStore
from one_memory.hy_memory_bridge import (
    HY_CODING_LAYERS,
    GraphWidthDecision,
    GraphWidthMismatch,
    GraphWidthMismatchError,
    GraphWidthOutcome,
    HyGraphSnapshot,
    HyMemoryBridge,
    HyMemoryBridgeError,
    HyMemoryIngestRequest,
    HyMemoryIngestResult,
    HyMemoryOutput,
    HyMemoryTurn,
    builtin_retrieval_snapshot,
    coding_tool_result_max_bytes,
    decide_profile_graph_width,
    hy_memory_store_exists,
    inspect_profile_graph_width,
)
from one_memory.hy_runtime_config import hy_memory_version
from one_memory.hy_source_context import HySourceContext, HySupportedMemory
from one_memory.imports.access import opaque_workspace_id
from one_memory.imports.models import EvidenceContentKind, EvidenceOrigin, EvidenceRecord
from one_memory.imports.runner import live_sync_arrival
from one_memory.imports.storage import EvidenceStore
from one_memory.input_sanitization import sanitize_model_input
from one_memory.memory_governance import (
    DeletionClaim,
    HyCreate,
    HyDerivation,
    HyLink,
    HyRetire,
    HyRevise,
    HySupersede,
    MemoryDraft,
    MemoryEvidenceOverriddenError,
    MemoryGovernanceStore,
    MemoryKind,
    MemoryMutationConflictError,
    MemoryOrigin,
    MemoryStatus,
)
from one_memory.memory_queue import (
    FRESH_WINDOW,
    MANUAL_EXTRACT_BATCH_SIZE,
    ExtractionWindowPreset,
    MemoryWorkQueue,
    QueueDepths,
    QueuedWork,
    classify,
    extraction_window_for,
)
from one_memory.memory_tasks import (
    TASK_MAX_ATTEMPTS,
    ActiveMemoryTask,
    MemoryTask,
    MemoryTaskKind,
    MemoryTaskOutput,
    MemoryTaskOwnershipError,
    MemoryTaskRequestConflictError,
    MemoryTaskStatus,
    MemoryTaskStore,
    TaskEvidenceRef,
    evidence_ref,
    matches_evidence_ref,
    materialize_evidence_ref,
    segment_evidence_record,
)
from one_memory.model_profiles import (
    ModelProfile,
    ModelProfileSnapshot,
    ModelProfileStore,
)
from one_memory.secrets import SecretStore, SecretStoreUnavailableError
from one_memory.semantic_deadline import SemanticMemoryHit, normalize_semantic_hits
from one_memory.service_levels import (
    ServiceLevel,
    ServiceLevelScheduler,
    ServiceResource,
    current_service_level,
)

# A read bridge warmed before the first vector write can retain Chroma's empty
# collection view. Replace it once after that first successful write. Later
# writes target the same persistent collection and must not create another HY
# client (each client owns native threads); graph replacement resets the gate.
_READ_BRIDGE_FIRST_REFRESH_DELAY_SECONDS = 0.05

_CLAIMABLE_TASK_STATUSES = frozenset(
    {
        MemoryTaskStatus.PENDING,
        MemoryTaskStatus.WAITING_FOR_PERMISSION,
        MemoryTaskStatus.WAITING_FOR_MODEL,
        MemoryTaskStatus.FAILED,
    }
)


def _report_deletion_sync_failure(error: Exception) -> None:
    logging.getLogger(__name__).warning(
        "memory_deletion_sync_failed %s: %s", type(error).__name__, error
    )
    diagnostic_event(
        level="error",
        component="memory.deletion",
        event="memory_deletion.sync_failed",
        outcome="failed",
        error_code="memory_deletion_sync_failed",
        details={"reason": str(error)[:200]},
        **diagnostic_exception(error),
    )


def _report_conversation_prepare_failure(source_id: str, error: Exception) -> None:
    logging.getLogger(__name__).warning(
        "memory_conversation_prepare_failed %s: %s", type(error).__name__, error
    )
    diagnostic_event(
        level="error",
        component="memory.scheduler",
        event="memory_conversation.prepare_failed",
        outcome="failed",
        error_code="memory_conversation_prepare_failed",
        details={"source_id": source_id, "reason": str(error)[:200]},
        **diagnostic_exception(error),
    )


def _report_stale_known_reasons(task_id: str, reasons: Mapping[str, int]) -> None:
    """Emit one schema-valid scalar diagnostic per stale-memory reason."""
    for reason, count in sorted(reasons.items()):
        diagnostic_event(
            level="debug",
            component="memory.context",
            event="known_memory_stale_omitted",
            outcome="omitted",
            task_id=task_id,
            details={"reason": reason, "count": count},
        )


MEMORY_INGEST_POLICY_VERSION = "hy-native-v23"
UNIFIED_HY_AGENT_ID = "one-memory"
#: Deterministic refusals raised before the payload reaches HY, so nothing was
#: sent even though the invocation marker is already recorded by then: the
#: write bindings are built inside `ingest` ahead of the call, and the input
#: window is checked while the request is frozen.
#:
#: They are terminal rather than unknown. `unknown` here means "HY may hold a
#: write we cannot confirm", and that is exactly what did not happen — parking
#: the task on it hid the reason and stopped the queue behind it (issue #44).
#: Kept in step with `durable_queue.PRE_SEND_REFUSAL_CODES`, which the durable
#: write path applies to the same codes.
PRE_SEND_REFUSAL_CODES = frozenset(
    {
        "hy_memory_input_exceeds_window",
    }
)
_BACKFILL_BATCH_SIZE = 8
_BACKFILL_DRAIN_BUDGET_SECONDS = 330.0
_HISTORY_MERGE_TURNS = 8
_HISTORY_MERGE_CHARACTERS = 8_000
#: The longest the deletion sync sleeps with nothing due, so a deletion
#: recorded by another path is still picked up without being woken.
DELETION_SYNC_IDLE_SECONDS = 300.0
_TURN_SETTLE_SECONDS = 0.75
_LIVE_OPEN_TURN_SETTLE_SECONDS = 30.0
_BACKFILL_START_GRACE_SECONDS = 3.0
MEMORY_TASK_TIMEOUT_MARGIN_SECONDS = 60.0
MEMORY_TASK_TIMEOUT_MIN_SECONDS = 180.0
ORPHAN_RECOVERY_INTERVAL_SECONDS = 15.0


def memory_task_timeout_seconds(model_timeout_seconds: float) -> float:
    """Bound the whole HY pipeline without treating one request timeout as its budget."""
    return max(
        MEMORY_TASK_TIMEOUT_MIN_SECONDS,
        float(model_timeout_seconds) + MEMORY_TASK_TIMEOUT_MARGIN_SECONDS,
    )


class MemoryBridgeProtocol(Protocol):
    async def ingest(self, request: HyMemoryIngestRequest) -> HyMemoryIngestResult: ...

    async def ingest_extracted(
        self,
        request: HyMemoryIngestRequest,
        contents: tuple[str, ...],
    ) -> HyMemoryIngestResult: ...

    async def profile_memories(
        self,
        *,
        user_id: str,
        agent_id: str,
    ) -> tuple[HyMemoryOutput, ...]: ...

    async def digest(self, *, user_id: str, agent_id: str) -> dict[str, object]: ...

    async def list_agent_ids(self, *, user_id: str, limit: int = 1_000) -> tuple[str, ...]: ...

    async def outputs_for_request(
        self,
        *,
        request_id: str,
        user_id: str,
        agent_id: str,
        evidence_ids: tuple[str, ...],
    ) -> tuple[HyMemoryOutput, ...]: ...

    async def receipt_for_request(
        self,
        *,
        request_id: str,
        user_id: str,
        agent_id: str,
        evidence_ids: tuple[str, ...],
    ) -> HyMemoryIngestResult | None: ...

    async def graph_snapshot(
        self,
        *,
        user_id: str,
        agent_id: str,
        limit: int = 300,
    ) -> HyGraphSnapshot: ...

    async def update(self, memory_id: str, content: str) -> dict[str, object]: ...

    async def delete(self, memory_id: str) -> dict[str, object]: ...

    async def search_scores(
        self,
        query: str,
        *,
        user_id: str,
        agent_id: str,
        limit: int = 12,
    ) -> tuple[tuple[str, float], ...]: ...

    async def close(self) -> None: ...


class MemoryTaskRunner:
    def __init__(
        self,
        *,
        tasks: MemoryTaskStore,
        profiles: ModelProfileStore,
        secrets: SecretStore,
        evidence: EvidenceStore,
        governance: MemoryGovernanceStore,
        data_dir: Path,
        bridge_factory: (
            Callable[[ModelProfileSnapshot, str | None, Path], MemoryBridgeProtocol] | None
        ) = None,
        close_bridge: bool = True,
        sdk_authority: Callable[[], bool] | None = None,
    ) -> None:
        self.tasks = tasks
        self.profiles = profiles
        self.secrets = secrets
        self.evidence = evidence
        self.governance = governance
        self.data_dir = data_dir
        self.bridge_factory = bridge_factory or self._create_bridge
        self.close_bridge = close_bridge
        self.sdk_authority = sdk_authority or (lambda: False)

    async def run(
        self,
        task_id: str,
        *,
        timeout: float | None = None,
        before_hy_invocation: Callable[[], Awaitable[None]] | None = None,
        input_records: tuple[EvidenceRecord, ...] | None = None,
    ) -> MemoryTask:
        started = monotonic()
        diagnostic_event(
            level="info",
            component="memory.task",
            event="memory_task.started",
            outcome="started",
            task_id=task_id,
        )
        try:
            if input_records is None:
                body = (
                    self._run_without_content_permission(task_id)
                    if before_hy_invocation is None
                    else self._run_without_content_permission(
                        task_id,
                        before_hy_invocation=before_hy_invocation,
                    )
                )
            else:
                body = (
                    self._run_without_content_permission(
                        task_id,
                        input_records=input_records,
                    )
                    if before_hy_invocation is None
                    else self._run_without_content_permission(
                        task_id,
                        before_hy_invocation=before_hy_invocation,
                        input_records=input_records,
                    )
                )
            # The ceiling bounds how long this task holds its harness's slot:
            # a harness is serial, so one hung task starves every turn behind
            # it. It is insurance against a single bad call, not a way to run
            # faster.
            if timeout is None:
                result = await body
            else:
                try:
                    result = await asyncio.wait_for(body, timeout=timeout)
                except TimeoutError:
                    # The task was claimed as `running` before anything slow
                    # happened, so leaving it there would hold the slot until
                    # the next process start. Put it back so the turns behind
                    # it can run, then say why.
                    await in_the_background(
                        self.tasks.recover_one_running,
                        task_id,
                        reason="hy_memory_timeout",
                    )
                    # `from None`: this is a deliberate translation of a
                    # timeout into the vocabulary the caller classifies on,
                    # not a new failure while handling one.
                    raise HyMemoryBridgeError("hy_memory_timeout") from None
        except Exception as error:
            if isinstance(error, sqlite3.Error):
                record_database_error("memory.task", error)
            diagnostic_event(
                level="error",
                component="memory.task",
                event="memory_task.failed",
                outcome="failed",
                task_id=task_id,
                duration_ms=elapsed_ms(started),
                error_code="memory_task_execution_failed",
                **diagnostic_exception(error),
            )
            raise
        outputs, counts = await asyncio.gather(
            in_the_background(self.tasks.outputs, task_id),
            in_the_background(self.tasks.counts),
        )
        outstanding = sum(
            counts.get(status, 0)
            for status in (
                MemoryTaskStatus.PENDING,
                MemoryTaskStatus.WAITING_FOR_PERMISSION,
                MemoryTaskStatus.WAITING_FOR_MODEL,
                MemoryTaskStatus.FAILED,
            )
        )
        diagnostic_event(
            level="error" if result.status is MemoryTaskStatus.FAILED else "info",
            component="memory.task",
            event="memory_task.finished",
            outcome=str(result.status),
            task_id=task_id,
            duration_ms=elapsed_ms(started),
            state_after=str(result.status),
            error_code=result.last_error_code,
            details={
                "result_count": len(outputs),
                "pending_count": outstanding,
                "attempt": result.attempts,
            },
        )
        return result

    @staticmethod
    def _log_step(task_id: str, step: int, action: str, started: float) -> None:
        diagnostic_event(
            level="debug",
            component="memory.task",
            event="memory_task.step_completed",
            outcome="succeeded",
            task_id=task_id,
            duration_ms=elapsed_ms(started),
            details={"phase": str(step), "action": action},
        )

    async def _run_without_content_permission(
        self,
        task_id: str,
        *,
        before_hy_invocation: Callable[[], Awaitable[None]] | None = None,
        input_records: tuple[EvidenceRecord, ...] | None = None,
    ) -> MemoryTask:
        # Every store call here is synchronous, and several are writes that
        # wait on the write lock for up to five seconds. Made from the loop,
        # with hundreds of tasks each making a dozen of them, they blocked the
        # loop in bursts: /health, which does no work at all, was measured at
        # 3.5s and a library read at 20.7s, past the renderer's timeout. The
        # stores open a connection per call, so a thread is safe here.
        step_started = monotonic()
        task, runner_token = await in_the_background(self.tasks.claim, task_id)
        self._log_step(task_id, 1, "claim", step_started)
        hy_invoked = False
        # Before anything is read, so an overruling made while this task runs
        # is one the writes below can tell from those its context already left out.
        sdk_authority = self.sdk_authority()
        derivation = (
            None if sdk_authority else await in_the_background(self.governance.hy_derivation)
        )
        if task.kind is not MemoryTaskKind.EXTRACT:
            return await in_the_background(
                self.tasks.fail,
                task_id,
                runner_token=runner_token,
                error_code="memory_task_kind_not_wired",
            )
        # Reaches the macOS keychain, which is not something to wait for on
        # the loop either.
        step_started = monotonic()
        try:
            secret = await in_the_background(
                self.profiles.resolve_snapshot_secret,
                task.profile_id,
                task.profile_revision,
                self.secrets,
            )
        except SecretStoreUnavailableError:
            return await in_the_background(
                self.tasks.wait_for_model,
                task_id,
                runner_token=runner_token,
                error_code="secure_storage_unavailable",
            )
        if task.profile_snapshot.has_api_key and secret is None:
            return await in_the_background(
                self.tasks.wait_for_model,
                task_id,
                runner_token=runner_token,
                error_code="model_secret_unavailable",
            )
        self._log_step(task_id, 2, "credential", step_started)
        # Off the loop: this reads and rebuilds a whole conversation, and a
        # long one is seconds of it. Run here, with hundreds of tasks doing it
        # in turn, it held the loop for 17 seconds at a stretch — the whole
        # time, every host asking to recall something was simply waiting.
        step_started = monotonic()
        frozen = await in_the_background(self.tasks.inputs, task.task_id)
        chunk: tuple[EvidenceRecord, ...] | None
        if input_records is not None:
            chunk = input_records
        elif frozen:
            try:
                chunk = await in_the_background(self._frozen_chunk, task)
            except HyMemoryBridgeError as error:
                return await in_the_background(
                    self.tasks.fail, task_id, runner_token=runner_token, error_code=str(error)
                )
        else:
            conversation = await in_the_background(self.evidence.list_conversation, task.input_ref)
            chunk = next(
                (
                    candidate
                    for candidate in _conversation_chunks(conversation)
                    if task.input_ref in {r.evidence_id for r in candidate}
                ),
                None,
            )
        if chunk is None:
            return await in_the_background(self.tasks.complete, task_id, runner_token=runner_token)
        self._log_step(task_id, 3, "load_context", step_started)
        step_started = monotonic()
        bridge = self.bridge_factory(task.profile_snapshot, secret, self.data_dir)
        self._log_step(task_id, 4, "bridge_context", step_started)
        try:
            prepare = getattr(bridge, "prepare", None)
            if prepare is not None:
                await prepare()
            request = self._request(task, chunk)
            if sdk_authority and request.source_context is not None:
                request = request.model_copy(update={"source_context": None})
            elif request.source_context is not None:
                context = await in_the_background(
                    self._source_context, task, request.source_context
                )
                request = request.model_copy(update={"source_context": context})
            uses_frozen_input = bool(frozen) or input_records is not None
            grant_token = (
                await in_the_background(self._grant_token, task, chunk[0])
                if uses_frozen_input
                else None
            )
            memory_contents = tuple(
                sanitize_model_input(record.verbatim)
                for record in chunk
                if _imported_as_said(record)
            )
            only_contents = bool(memory_contents) and all(_imported_as_said(r) for r in chunk)
            step_started = monotonic()
            if before_hy_invocation is not None:
                await before_hy_invocation()
            await in_the_background(
                self.tasks.record_hy_invocation,
                task_id,
                runner_token=runner_token,
            )
            hy_invoked = True
            if only_contents:
                result = await bridge.ingest_extracted(
                    request,
                    memory_contents,
                )
            else:
                result = await bridge.ingest(request)
            self._log_step(task_id, 5, "hy_ingest", step_started)
            if not result.success:
                raise HyMemoryBridgeError("hy_memory_runtime_failed")
            if only_contents and not result.memories:
                raise HyMemoryBridgeError("hy_memory_import_no_readable_output")
            step_started = monotonic()
            await in_the_background(
                self.tasks.save_sdk_receipt,
                task_id=task.task_id,
                request_id=request.request_id,
                raw_ids=result.raw_ids,
                memory_ids=tuple(memory.memory_id for memory in result.memories),
            )
            if sdk_authority:
                if uses_frozen_input:
                    if input_records is None:
                        await in_the_background(self._frozen_chunk, task)
                    if grant_token != await in_the_background(
                        self._grant_token,
                        task,
                        chunk[0],
                    ):
                        raise HyMemoryBridgeError("hy_memory_source_changed")
                self._log_step(task_id, 6, "sdk_receipt", step_started)
                return await in_the_background(
                    self.tasks.complete,
                    task_id,
                    runner_token=runner_token,
                )
            assert derivation is not None
            outputs = result.memories
            # What HY learnt about the person, as opposed to about this
            # conversation. It arrives by being read back because the write
            # does not report it (see `profile_memories`), and it goes through
            # the same governance below as everything else — including the
            # dedupe, which is what stops the same unchanged profile being
            # recorded again by every task that follows.
            outputs = (
                *outputs,
                *await bridge.profile_memories(
                    user_id=request.user_id,
                    agent_id=request.agent_id,
                ),
            )
            if uses_frozen_input:
                if input_records is None:
                    await in_the_background(self._frozen_chunk, task)
                if grant_token != await in_the_background(self._grant_token, task, chunk[0]):
                    raise HyMemoryBridgeError("hy_memory_source_changed")
            if request.source_context is not None:
                await in_the_background(
                    self._check_source_context,
                    request.source_context,
                    skip_current=input_records is not None,
                )
            # One thread hop for the whole batch rather than a dozen writes
            # made from the loop.
            step_started = monotonic()
            await in_the_background(
                self._record_outputs,
                task=task,
                chunk=chunk,
                outputs=outputs,
                request=request,
                derivation=derivation,
                retired=result.retired_memory_ids,
                superseded=result.superseded_memory_ids,
            )
            self._log_step(task_id, 6, "governance", step_started)
        except HyMemoryBridgeError as error:
            code = str(error)
            if code == "model_sedimentation_disabled" and not hy_invoked:
                return await in_the_background(
                    self.tasks.release_unstarted,
                    task_id,
                    runner_token=runner_token,
                    error_code=code,
                )
            model_wait_errors = {
                "hy_memory_unavailable",
                "hy_memory_rate_limit",
                "hy_memory_timeout",
                "hy_memory_runtime_failed",
                "model_secret_unavailable",
                "hy_memory_model_authentication_failed",
                "hy_memory_model_payment_required",
                "hy_memory_model_access_denied",
                "hy_memory_model_rate_limited",
                "hy_memory_model_service_unavailable",
                "hy_memory_model_timeout",
            }
            safe_terminal_after_invocation = {"hy_memory_import_no_readable_output"}
            if code in PRE_SEND_REFUSAL_CODES:
                # Raised before the call, so the marker only says "about to":
                # fail with the reason rather than parking on a write HY never
                # saw. See the constant and issue #44.
                return await in_the_background(
                    self.tasks.fail,
                    task_id,
                    runner_token=runner_token,
                    error_code=code,
                )
            if hy_invoked and (
                code == "hy_memory_evidence_changed"
                or (
                    code not in {"hy_memory_source_changed", "hy_memory_source_revoked"}
                    and code not in model_wait_errors | safe_terminal_after_invocation
                )
            ):
                return await in_the_background(
                    self.tasks.mark_unknown,
                    task_id,
                    runner_token=runner_token,
                    error_code=code,
                )
            if code in {"hy_memory_source_changed", "hy_memory_source_revoked"}:
                # The source grant changed after this task was claimed.
                return await in_the_background(
                    self.tasks.stop,
                    task_id,
                    runner_token=runner_token,
                    error_code=code,
                )
            if code in model_wait_errors:
                return await in_the_background(
                    self.tasks.wait_for_model,
                    task_id,
                    runner_token=runner_token,
                    error_code=code,
                )
            if code == "model_content_transport_request_failed":
                return await in_the_background(
                    self.tasks.mark_unknown,
                    task_id,
                    runner_token=runner_token,
                    error_code=code,
                )
            return await in_the_background(
                self.tasks.fail,
                task_id,
                runner_token=runner_token,
                error_code=code,
            )
        finally:
            if self.close_bridge:
                await bridge.close()
        return await in_the_background(self.tasks.complete, task_id, runner_token=runner_token)

    def _frozen_chunk(self, task: MemoryTask) -> tuple[EvidenceRecord, ...]:
        records = []
        for ref in self.tasks.inputs(task.task_id):
            record = self.evidence.get(ref["evidence_id"])
            if record is None or record.user_id != task.user_id:
                raise HyMemoryBridgeError("hy_memory_evidence_changed")
            materialized = materialize_evidence_ref(record, ref)
            if materialized is None:
                raise HyMemoryBridgeError("hy_memory_evidence_changed")
            with self.tasks._connect() as db:
                grant = db.execute(
                    "SELECT revoked_at FROM source_grants WHERE user_id=? AND source_id=?",
                    (record.user_id, record.source_id),
                ).fetchone()
            if grant is None or grant[0] is not None:
                raise HyMemoryBridgeError("hy_memory_source_revoked")
            records.append(materialized)
        return tuple(records)

    def _original_records(self, records: tuple[EvidenceRecord, ...]) -> tuple[EvidenceRecord, ...]:
        originals: dict[str, EvidenceRecord] = {}
        for record in records:
            current = self.evidence.get(record.evidence_id)
            if current is None:
                raise HyMemoryBridgeError("hy_memory_evidence_changed")
            originals.setdefault(current.evidence_id, current)
        return tuple(originals.values())

    def _grant_token(self, task: MemoryTask, seed: EvidenceRecord) -> str:
        with self.tasks._connect() as db:
            row = db.execute(
                "SELECT granted_at,revoked_at FROM source_grants WHERE user_id=? AND source_id=?",
                (task.user_id, seed.source_id),
            ).fetchone()
        if row is None or row[1] is not None:
            raise HyMemoryBridgeError("hy_memory_source_revoked")
        return str(row[0])

    def _source_context(
        self,
        task: MemoryTask,
        current: HySourceContext,
        *,
        persist_inputs: bool = True,
    ) -> HySourceContext:
        current_ids = {record.evidence_id for record in current.current}
        history: dict[str, EvidenceRecord] = {}
        known: dict[str, HySupportedMemory] = {}
        latest: dict[str, tuple[MemoryTaskOutput, tuple[TaskEvidenceRef, ...]]] = {}
        stale_known_reasons: dict[str, int] = {}
        for previous in self.tasks.completed_conversation_tasks(task, current.current[0]):
            if not previous.request_id.startswith(f"conversation:{MEMORY_INGEST_POLICY_VERSION}:"):
                continue
            refs = self.tasks.inputs(previous.task_id)
            if not refs:
                continue
            try:
                records = self._frozen_chunk(previous)
            except HyMemoryBridgeError:
                continue
            if any(
                (r.user_id, r.source_id, r.conversation_id)
                != (task.user_id, current.current[0].source_id, current.current[0].conversation_id)
                for r in records
            ):
                continue
            for record in records:
                if record.evidence_id not in current_ids and _is_hy_turn_record(record):
                    history[record.evidence_id] = record
            for output in self.tasks.outputs(previous.task_id):
                latest[output.memory_id] = (output, output.support_refs or refs)
        for output, supports in latest.values():
            memory = self.governance.get(output.memory_id, user_id=task.user_id)
            # HY replaced it itself: its evidence still stands, but it is no
            # longer a memory HY may revise, so it is neither known nor a block.
            if memory is not None and memory.status is MemoryStatus.SUPERSEDED:
                continue
            if (
                memory is None
                or memory.revision != output.memory_revision
                or memory.status is not MemoryStatus.CONFIRMED
            ):
                reason = (
                    "missing"
                    if memory is None
                    else (
                        "revision_changed"
                        if memory.revision != output.memory_revision
                        else "not_confirmed"
                    )
                )
                stale_known_reasons[reason] = stale_known_reasons.get(reason, 0) + 1
                continue
            if output.owner not in {"user", "agent"}:
                continue
            if set(memory.evidence_ids) != {ref["evidence_id"] for ref in supports}:
                # Merged support is not covered by this old output snapshot.
                # Keep permitted original history, but do not expand its authority.
                continue
            known[output.hy_memory_id] = HySupportedMemory(
                hy_memory_id=output.hy_memory_id,
                content=memory.content,
                owner="user" if output.owner == "user" else "agent",
                governed_memory_id=memory.memory_id,
                revision=memory.revision,
                evidence_ids=tuple(ref["evidence_id"] for ref in supports),
            )
        if stale_known_reasons:
            _report_stale_known_reasons(task.task_id, stale_known_reasons)
        blocked_evidence_ids = self.governance.blocked_source_ids(
            user_id=task.user_id,
            evidence_ids=tuple(current_ids | set(history)),
        )
        if blocked_evidence_ids & current_ids:
            raise HyMemoryBridgeError("hy_memory_evidence_changed")
        selected: list[EvidenceRecord] = []
        used_chars = 0
        for record in reversed(tuple(history.values())):
            if record.evidence_id in blocked_evidence_ids:
                continue
            if len(selected) == 10 or used_chars + len(record.verbatim) > 12_000:
                break
            selected.append(record)
            used_chars += len(record.verbatim)
        selected.reverse()
        selected_ids = {record.evidence_id for record in selected}
        supporting = tuple(
            r
            for key, r in history.items()
            if key not in selected_ids and key not in blocked_evidence_ids
        )
        ids = current_ids | selected_ids | {r.evidence_id for r in supporting}
        context = HySourceContext(
            current=current.current,
            history=tuple(selected),
            supporting=supporting,
            known_memories=tuple(
                m
                for m in known.values()
                if set(m.evidence_ids) <= ids and not set(m.evidence_ids) & blocked_evidence_ids
            ),
        )
        if persist_inputs:
            try:
                self.tasks.save_context_inputs(
                    task.task_id, (*context.history, *context.supporting)
                )
            except MemoryTaskRequestConflictError as error:
                raise HyMemoryBridgeError("hy_memory_evidence_changed") from error
        return context

    def _check_source_context(
        self,
        context: HySourceContext,
        *,
        skip_current: bool = False,
    ) -> None:
        checked = (
            (*context.history, *context.supporting)
            if skip_current
            else (*context.current, *context.history, *context.supporting)
        )
        for original in checked:
            record = self.evidence.get(original.evidence_id)
            if record is None or not matches_evidence_ref(record, evidence_ref(original)):
                raise HyMemoryBridgeError("hy_memory_evidence_changed")
        # A deletion/correction during the model call cannot become an old-memory update.
        for known in context.known_memories:
            memory = self.governance.get(
                known.governed_memory_id, user_id=context.current[0].user_id
            )
            if (
                memory is None
                or memory.revision != known.revision
                or memory.content != known.content
                or memory.status is not MemoryStatus.CONFIRMED
            ):
                raise HyMemoryBridgeError("hy_memory_evidence_changed")

    def _record_provenance(
        self,
        task: MemoryTask,
        memory_id: str,
        output: HyMemoryOutput,
        support: tuple[EvidenceRecord, ...],
    ) -> None:
        memory = self.governance.get(memory_id, user_id=task.user_id)
        if memory is None:
            raise HyMemoryBridgeError("hy_memory_governance_missing")
        self.tasks.save_output(
            MemoryTaskOutput(
                task_id=task.task_id,
                memory_id=memory_id,
                memory_revision=memory.revision,
                hy_memory_id=output.memory_id,
                owner=output.owner,
                layer=output.layer,
                engine_version=hy_memory_version(),
                support_refs=tuple(evidence_ref(record) for record in support),
            )
        )

    def _record_outputs(
        self,
        *,
        task: MemoryTask,
        chunk: tuple[EvidenceRecord, ...],
        outputs: tuple[HyMemoryOutput, ...],
        request: HyMemoryIngestRequest,
        derivation: HyDerivation,
        retired: tuple[str, ...] = (),
        superseded: Mapping[str, tuple[str, ...]] | None = None,
    ) -> None:
        try:
            self._record_outputs_checked(
                task=task,
                chunk=chunk,
                outputs=outputs,
                request=request,
                derivation=derivation,
                retired=retired,
                superseded=superseded or {},
            )
        except MemoryMutationConflictError as error:
            # Overruled evidence, or a memory the person changed while HY was
            # working: either way what HY wrote was about something else.
            raise HyMemoryBridgeError("hy_memory_evidence_changed") from error

    def _record_outputs_checked(
        self,
        *,
        task: MemoryTask,
        chunk: tuple[EvidenceRecord, ...],
        outputs: tuple[HyMemoryOutput, ...],
        request: HyMemoryIngestRequest,
        derivation: HyDerivation,
        retired: tuple[str, ...] = (),
        superseded: Mapping[str, tuple[str, ...]] | None = None,
    ) -> None:
        """Writes what HY produced into governance. Synchronous on purpose:
        the caller runs it in a thread, so the loop stays free to answer."""
        source_records = (
            (
                *request.source_context.current,
                *request.source_context.history,
                *request.source_context.supporting,
            )
            if request.source_context
            else chunk
        )
        by_id = {record.evidence_id: record for record in source_records}
        # A deletion or replacement is HY's reading of the whole turn, so it
        # rests on everything the turn showed it.
        whole_turn = derivation.citing(by_id)
        for output in outputs:
            layer = output.layer
            ids = output.evidence_ids or request.evidence_ids
            if not set(ids) <= set(by_id):
                raise HyMemoryBridgeError("hy_memory_write_verification_support_missing")
            support = tuple(by_id[key] for key in ids)
            # HY's own attribution. An undecided memory counts as the person's:
            # HY's prompt defaults that way.
            from_agent = output.owner == "agent" or all(
                record.role == "assistant"
                and record.content_kind is EvidenceContentKind.RAW_CONVERSATION
                for record in support
            )
            content = sanitize_model_input(output.content)
            known = (
                next(
                    (
                        memory
                        for memory in request.source_context.known_memories
                        if memory.hy_memory_id == output.memory_id
                    ),
                    None,
                )
                if request.source_context
                else None
            )
            if known is not None and known.content != content:
                if known.owner != output.owner:
                    raise HyMemoryBridgeError("hy_memory_write_verification_authority_invalid")
                try:
                    updated = self.governance.write_from_hy(
                        HyRevise(
                            memory_id=known.governed_memory_id,
                            request_id=f"hy-update:{task.task_id}:{output.memory_id}",
                            expected_revision=known.revision,
                            content=content,
                            evidence_ids=ids,
                        ),
                        user_id=task.user_id,
                        derivation=derivation.citing(ids),
                    )
                except MemoryMutationConflictError as error:
                    raise HyMemoryBridgeError("hy_memory_evidence_changed") from error
                self._record_provenance(task, updated.memory_id, output, support)
                continue
            revised = (
                self.governance.get(output.memory_id, user_id=task.user_id)
                if known is None and layer in HY_CODING_LAYERS
                else None
            )
            if revised is not None and revised.content != content:
                # HY's coding store only lets it revise rows this lease attests.
                if revised.status is not MemoryStatus.CONFIRMED or not set(
                    revised.evidence_ids
                ) <= set(by_id):
                    raise HyMemoryBridgeError("hy_memory_write_verification_authority_invalid")
                try:
                    updated = self.governance.write_from_hy(
                        HyRevise(
                            memory_id=revised.memory_id,
                            request_id=f"hy-update:{task.task_id}:{output.memory_id}",
                            expected_revision=revised.revision,
                            content=content,
                            evidence_ids=tuple(dict.fromkeys((*revised.evidence_ids, *ids))),
                        ),
                        user_id=task.user_id,
                        derivation=derivation.citing(ids),
                    )
                except MemoryMutationConflictError as error:
                    raise HyMemoryBridgeError("hy_memory_evidence_changed") from error
                self._record_provenance(task, updated.memory_id, output, support)
                continue
            existing = self.governance.get(output.memory_id, user_id=task.user_id)
            if existing is not None and existing.content == content:
                linked = existing
                for evidence_id in ids:
                    linked = self.governance.write_from_hy(
                        HyLink(memory_id=existing.memory_id, evidence_id=evidence_id),
                        user_id=task.user_id,
                        derivation=derivation.citing(ids),
                    )
                self._record_provenance(task, linked.memory_id, output, support)
                continue
            memory = self.governance.write_from_hy(
                HyCreate(
                    draft=MemoryDraft(
                        user_id=task.user_id,
                        kind=_memory_kind(layer),
                        content=content,
                        scopes=_memory_scopes(support, layer=layer),
                        evidence_ids=ids,
                        tags=output.tags[:32],
                        origin=MemoryOrigin.AGENT if from_agent else MemoryOrigin.EXTRACTED,
                    ),
                    request_id=f"extract:{task.task_id}:{output.memory_id}",
                    memory_id=output.memory_id,
                ),
                user_id=task.user_id,
                derivation=derivation.citing(ids),
            )
            # Usable on arrival whoever said it; `origin` tells recall and
            # matters that an assistant's sentence is not the person's own.
            self._record_provenance(task, memory.memory_id, output, support)
        for memory_id in retired:
            mirrored = self.governance.get(memory_id, user_id=task.user_id)
            if mirrored is None or mirrored.status is MemoryStatus.DELETED:
                continue
            self.governance.write_from_hy(
                HyRetire(
                    memory_id=memory_id,
                    request_id=f"hy-delete:{task.task_id}:{memory_id}",
                    expected_revision=mirrored.revision,
                ),
                user_id=task.user_id,
                derivation=whole_turn,
            )
        for memory_id, replaced_by in sorted((superseded or {}).items()):
            mirrored = self.governance.get(memory_id, user_id=task.user_id)
            if mirrored is None or mirrored.status not in {
                MemoryStatus.CONFIRMED,
                MemoryStatus.SUPERSEDED,
            }:
                continue
            # Only replacements the ledger holds; otherwise the old memory
            # would leave every list while pointing at nothing the user can read.
            heads = tuple(
                key
                for key in replaced_by
                if (head := self.governance.get(key, user_id=task.user_id)) is not None
                and head.status is not MemoryStatus.DELETED
            )
            if not heads:
                continue
            self.governance.write_from_hy(
                HySupersede(
                    memory_id=memory_id,
                    request_id=f"hy-supersede:{task.task_id}:{memory_id}",
                    expected_revision=mirrored.revision,
                    superseded_by=heads,
                ),
                user_id=task.user_id,
                derivation=whole_turn,
            )
        self._save_extraction_for_training(task, outputs)

    def _save_extraction_for_training(
        self,
        task: MemoryTask,
        outputs: tuple[HyMemoryOutput, ...],
    ) -> None:
        """Persist what this task extracted so training can read it later.

        `save_extraction` sat unused (A-20): governance got the memories, the
        training table got nothing. One row per task — short enough to be a
        single supervised target (1–160 chars), or a null row when the turn
        produced no trainable memory (negative examples matter as much as
        positives). Longer outputs are skipped rather than truncated: a cut
        sentence is worse training data than an honest null.
        """
        for output in outputs:
            content = sanitize_model_input(output.content).strip()
            layer = output.layer
            if not layer or not (1 <= len(content) <= 160):
                continue
            try:
                self.tasks.save_extraction(task.task_id, content=content, layer=layer)
            except MemoryTaskRequestConflictError:
                return
            return
        try:
            self.tasks.save_extraction(task.task_id, content=None, layer=None)
        except MemoryTaskRequestConflictError:
            return

    @staticmethod
    def _request(
        task: MemoryTask,
        chunk: tuple[EvidenceRecord, ...],
    ) -> HyMemoryIngestRequest:
        return MemoryTaskRunner.request_for(task.request_id, chunk)

    @staticmethod
    def request_for(
        request_id: str,
        chunk: tuple[EvidenceRecord, ...],
    ) -> HyMemoryIngestRequest:
        if not chunk:
            # A task with no inputs has nothing to name the request after and
            # nothing to attribute an answer to. Left alone this reached
            # `chunk[0]` below and came out as IndexError, which no caller can
            # read: a reconcile pass catches domain errors per task and moves
            # on, so one such task ended the whole pass and left every other
            # unknown task unreconciled. On this machine 68 of 303 unknown
            # tasks had no inputs, and the pass raised on the first of them.
            raise HyMemoryBridgeError("hy_memory_task_without_inputs")
        has_raw = any(
            record.content_kind is EvidenceContentKind.RAW_CONVERSATION for record in chunk
        )
        source = next(
            (record for record in chunk if record.role == "user"),
            chunk[0],
        )
        request_turns = tuple(
            record
            for record in chunk
            if (
                record.content_kind is EvidenceContentKind.RAW_CONVERSATION
                and (
                    # Protocol role is authoritative. A sub-agent question is
                    # an ordinary user message regardless of a host parser's
                    # former author guess.
                    record.role == "user"
                    # The assistant's side goes too. Without it 「就按你说的那个来」
                    # arrives with nothing to resolve it against, and HY's own
                    # prompt is written expecting both — its first example is
                    # about resolving a pronoun against an earlier assistant
                    # turn. It is context, not evidence of what the person
                    # said; what it produces is filtered on the way back.
                    or record.role == "assistant"
                    # Tool calls go as they happened; whether they make the
                    # turn a coding turn is HY's call, not ours.
                    or record.role == "tool"
                )
            )
            or (not has_raw and record.content_kind is EvidenceContentKind.MEMORY_CONTENT)
        )
        return HyMemoryIngestRequest(
            request_id=request_id,
            user_id=source.user_id,
            source_id=source.source_id,
            agent_id=_hy_agent_id(chunk),
            session_id=source.conversation_id,
            turns=tuple(
                HyMemoryTurn(
                    role=item.role if item.role in {"user", "assistant", "tool"} else "user",
                    content=sanitize_model_input(item.verbatim),
                )
                for item in request_turns
            ),
            evidence_ids=tuple(item.evidence_id for item in request_turns),
            workspace_ids=tuple(
                sorted({item.workspace for item in chunk if item.workspace is not None})
            ),
            memory_at=max(
                (item.created_at for item in chunk if item.created_at is not None),
                default=None,
            ),
            source_context=HySourceContext(current=request_turns)
            if not any("segment_hash" in evidence_ref(item) for item in request_turns)
            and all(
                item.content_kind is EvidenceContentKind.RAW_CONVERSATION for item in request_turns
            )
            else None,
        )

    @staticmethod
    def _create_bridge(
        profile: ModelProfileSnapshot,
        secret: str | None,
        data_dir: Path,
    ) -> HyMemoryBridge:
        return HyMemoryBridge(
            profile=profile,
            api_key=secret,
            data_dir=data_dir,
        )


@dataclass
class _BackfillAcceptance:
    revision: int | None
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None
    error: str | None = None


_digest_guard_lock = threading.Lock()
_digest_guard: asyncio.Lock | None = None
_source_guard_lock = threading.Lock()
_source_guards: WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, "_PrioritySourceLock"]] = (
    WeakKeyDictionary()
)
_direct_task_guard = threading.Lock()
_direct_tasks_by_database: dict[str, dict[str, int]] = {}


def _digest_mutex() -> asyncio.Lock:
    """The one lock that orders digests, for the whole process.

    A per-supervisor lock could not order two digests started from different
    supervisors, which is exactly the case that matters: one harness's
    catch-up overlapping another's live sync.
    """
    global _digest_guard
    with _digest_guard_lock:
        if _digest_guard is None:
            _digest_guard = asyncio.Lock()
    return _digest_guard


class _PrioritySourceLock:
    """One-at-a-time source admission with P0/P1 ahead of queued P2 work."""

    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._locked = False
        self._sequence = 0
        self._waiters: list[tuple[ServiceLevel, int]] = []

    async def acquire(self, level: ServiceLevel) -> None:
        async with self._condition:
            self._sequence += 1
            waiter = (level, self._sequence)
            self._waiters.append(waiter)
            try:
                while self._locked or min(self._waiters) != waiter:
                    await self._condition.wait()
            except BaseException:
                with suppress(ValueError):
                    self._waiters.remove(waiter)
                self._condition.notify_all()
                raise
            self._waiters.remove(waiter)
            self._locked = True

    async def release(self) -> None:
        async with self._condition:
            if not self._locked:
                raise RuntimeError("source lock is not held")
            self._locked = False
            self._condition.notify_all()

    def locked(self) -> bool:
        return self._locked


def _shared_source_lock(source_id: str) -> _PrioritySourceLock:
    """One source lock shared by every supervisor on the current event loop."""
    loop = asyncio.get_running_loop()
    with _source_guard_lock:
        guards = _source_guards.setdefault(loop, {})
        return guards.setdefault(source_id, _PrioritySourceLock())


def _mark_direct_task(database: Path, task_id: str) -> None:
    key = str(database.resolve())
    with _direct_task_guard:
        active = _direct_tasks_by_database.setdefault(key, {})
        active[task_id] = active.get(task_id, 0) + 1


def _unmark_direct_task(database: Path, task_id: str) -> None:
    key = str(database.resolve())
    with _direct_task_guard:
        active = _direct_tasks_by_database.get(key)
        if active is None:
            return
        remaining = active.get(task_id, 0) - 1
        if remaining > 0:
            active[task_id] = remaining
        else:
            active.pop(task_id, None)
        if not active:
            _direct_tasks_by_database.pop(key, None)


def _direct_tasks_for(database: Path) -> tuple[str, ...]:
    key = str(database.resolve())
    with _direct_task_guard:
        return tuple(_direct_tasks_by_database.get(key, ()))


class MemoryProcessingSupervisor:
    def __init__(
        self,
        *,
        tasks: MemoryTaskStore,
        profiles: ModelProfileStore,
        secrets: SecretStore,
        evidence: EvidenceStore,
        governance: MemoryGovernanceStore,
        data_dir: Path,
        extraction_settings: ExtractionSettingsStore | None = None,
        runner_factory: Callable[[], MemoryTaskRunner] | None = None,
        bridge_factory: (
            Callable[[ModelProfileSnapshot, str | None, Path], MemoryBridgeProtocol] | None
        ) = None,
        max_concurrent_tasks: int | None = None,
        source_writes_permitted: Callable[[str], bool] | None = None,
        service_levels: ServiceLevelScheduler | None = None,
        sedimentation_enabled: Callable[[], bool] | None = None,
        sdk_authority: Callable[[], bool] | None = None,
    ) -> None:
        self.tasks = tasks
        self.profiles = profiles
        self.secrets = secrets
        self.evidence = evidence
        self.governance = governance
        self.data_dir = data_dir
        #: Per-source "may this become a memory" switch. Optional so tests and
        #: a Core without a grant store keep their current behaviour; when
        #: absent every source is treated as writable, which is what the
        #: product did before the switch existed.
        self.source_writes_permitted = source_writes_permitted
        #: User-level "stop turning conversations into memories" switch. Optional
        #: so tests and a Core without a config file keep their current
        #: behaviour; when absent sedimentation is on, which is what the product
        #: did before the switch existed. Read per decision rather than cached,
        #: so stopping takes effect on the next dispatch without a restart.
        self.sedimentation_enabled = sedimentation_enabled
        self.sdk_authority = sdk_authority or (lambda: False)
        self.service_levels = service_levels or ServiceLevelScheduler()
        self.extraction_settings = extraction_settings or ExtractionSettingsStore(
            data_dir / "one-memory.sqlite3"
        )
        self.runner_factory = runner_factory or self._create_runner
        self.bridge_factory = bridge_factory or MemoryTaskRunner._create_bridge
        # `None` means "whatever the model profile allows". The profile has
        # carried a `max_concurrency` all along — copied into every task's
        # snapshot and then never consulted — so the number is taken from the
        # snapshot rather than read back out of the store: it is the same
        # value, it is already in hand, and reading it costs nothing.
        self._fixed_concurrent = max_concurrent_tasks
        self._profile_concurrent = 1
        # Wall-clock ceiling for one task, taken from the profile the same way
        # `max_concurrency` is. `None` means "not configured", in which case no
        # budget is imposed rather than a guess being invented.
        self._profile_timeout: float | None = None
        self._queue = MemoryWorkQueue()
        self._workers: dict[int, asyncio.Task[None]] = {}
        self._in_flight: dict[str, QueuedWork] = {}
        self._active_window_cursors: dict[bool, tuple[datetime | None, str]] = {}
        self._bridges: dict[tuple[str, int, str | None], MemoryBridgeProtocol] = {}
        # Reads share the same on-disk HY store but never the write bridge's
        # serialization lock. A long ingest must not make host recall queue
        # behind it; WAL and the HY read APIs provide the storage boundary.
        self._read_bridges: dict[tuple[str, int], MemoryBridgeProtocol] = {}
        self._read_bridges_ready: set[tuple[str, int]] = set()
        self._read_bridge_refresh_lock = asyncio.Lock()
        self._read_bridge_generations: dict[tuple[str, int], int] = {}
        self._read_bridge_refresh_tasks: dict[tuple[str, int], asyncio.Task[None]] = {}
        self._deletions_due = asyncio.Event()
        self._debounces: dict[tuple[str, str, str, str | None], asyncio.Task[None]] = {}
        self._preparations: dict[tuple[str, str, str, str | None], asyncio.Task[None]] = {}
        self._shutting_down = False
        self._manual_batches_planned = 0
        self._manual_batches_promoted = 0
        self._manual_batch_remaining: list[str] = []
        self._continuing_older = False
        # Set while durable pending work may remain outside the current
        # in-memory window; cleared by the first empty refill.
        self._older_waiting = False
        self._backfill_lock = asyncio.Lock()
        self._backfill_clock = monotonic
        self._backfill_round_budget_seconds = _BACKFILL_DRAIN_BUDGET_SECONDS
        self._backfill_preparations: set[asyncio.Task[list[QueuedWork] | None]] = set()
        self._dispatch_checks: set[asyncio.Task[list[QueuedWork]]] = set()
        self._live_backfill: asyncio.Task[None] | None = None
        self._backfill_acceptance: _BackfillAcceptance | None = None
        self._accept_backfill_lock = asyncio.Lock()
        self._shutdown_task: asyncio.Task[None] | None = None
        self._orphan_reclaimer: asyncio.Task[None] | None = None
        self._diagnostic_idle_reason: str | None = None

    def _sedimentation_stopped(self) -> bool:
        """Whether the user stopped model-driven sedimentation.

        Stopping must not look like a broken pipeline: nothing is sent to the
        model, so the pending count stays as it is and the scheduler reports
        why it is idle. Starting again picks the same work up.
        """
        if self.sedimentation_enabled is None:
            return False
        if self.sedimentation_enabled():
            return False
        self._record_idle("sedimentation_stopped")
        return True

    def _record_idle(self, reason: str) -> None:
        if self._diagnostic_idle_reason == reason:
            return
        self._diagnostic_idle_reason = reason
        diagnostic_event(
            level="debug",
            component="memory.scheduler",
            event="memory_scheduler.idle",
            outcome="idle",
            details={
                "reason": reason,
                "queue_depth": len(self._queue),
                "in_flight_count": len(self._in_flight),
            },
        )

    async def process_records(
        self,
        records: tuple[EvidenceRecord, ...],
        *,
        profile_id: str | None = None,
        on_progress: Callable[[tuple[MemoryTask, ...], int], None] | None = None,
        before_hy_invocation: Callable[[], Awaitable[None]] | None = None,
        source_admitted: bool = False,
    ) -> tuple[MemoryTask, ...]:
        """Process a bounded, authorized snapshot through the existing HY runner."""
        async with self._explicit_processing_lock(records):
            if self._sedimentation_stopped():
                raise HyMemoryBridgeError("model_sedimentation_disabled")
            active = await in_the_background(self.profiles.active, self.secrets)
            if active is None or (profile_id is not None and profile_id != active.profile_id):
                raise HyMemoryBridgeError("model_profile_not_active")
            # The durable drain and explicit processing entry reach HY through
            # this method, not through the backfill scheduler. New work from a
            # frozen Harness must be refused before any task or bridge call.
            if (
                not source_admitted
                and self.source_writes_permitted is not None
                and not await in_the_background(self._source_may_write, list(records))
            ):
                raise HyMemoryBridgeError("hy_memory_source_disabled")
            conversations: dict[tuple[str, str, str | None], list[EvidenceRecord]] = {}
            for record in records:
                conversations.setdefault(
                    (record.source_id, record.conversation_id, record.workspace), []
                ).append(record)
            # Tool events belong to the surrounding complete turn. A durable
            # intake can still deliver one on its own, before the adjacent
            # user/assistant message is available; creating a standalone task
            # for it makes the coverage check (correctly) reject the batch.
            # Keep the Evidence searchable and let the next complete-turn
            # delivery include it as context instead.
            if not any(_is_hy_input_turn(record) for record in records):
                return ()
            chunks = [
                chunk
                for conversation in conversations.values()
                for chunk in _conversation_chunks(conversation, max_turns=12, completed_only=True)
            ]
            prepared: list[tuple[MemoryTask, tuple[EvidenceRecord, ...]]] = []
            for chunk in chunks:
                task = await in_the_background(
                    self._enqueue_chunk,
                    chunk,
                    active,
                    freeze_inputs=True,
                )
                prepared.append((task, chunk))
            runner = self.runner_factory()
            completed = []
            if on_progress:
                on_progress(tuple(task for task, _chunk in prepared), 0)
            for task, chunk in prepared:
                if task.status is not MemoryTaskStatus.COMPLETED:
                    # Another importer may already own this task. Never race its claim.
                    task = await in_the_background(self.tasks.require, task.task_id)
                    if task.status is MemoryTaskStatus.RUNNING:
                        raise HyMemoryBridgeError("hy_memory_task_busy")
                    if task.status is not MemoryTaskStatus.COMPLETED:
                        # The profile's own wall-clock ceiling, exactly like the
                        # scheduler path below. Without it this direct entry
                        # (the durable drain calls `process_records`, not the
                        # scheduler) had no bound at all: a hung model call
                        # would hold the single harness slot until the process
                        # died, and the drain's lease renewal would keep
                        # extending a job that can never finish. The runner
                        # turns a timeout into `hy_memory_timeout`, which
                        # `classify` maps to `wait_for_model` rather than a
                        # blind retry.
                        _mark_direct_task(self.tasks.database_path, task.task_id)
                        try:
                            timeout = memory_task_timeout_seconds(
                                task.profile_snapshot.timeout_seconds
                            )
                            level = current_service_level()
                            async with self.service_levels.admit(
                                ServiceResource.MODEL,
                                level,
                                work_id=task.task_id,
                                scope_key=chunk[0].source_id,
                            ):
                                if isinstance(runner, MemoryTaskRunner):
                                    task = (
                                        await runner.run(
                                            task.task_id,
                                            timeout=timeout,
                                            input_records=chunk,
                                        )
                                        if before_hy_invocation is None
                                        else await runner.run(
                                            task.task_id,
                                            timeout=timeout,
                                            before_hy_invocation=before_hy_invocation,
                                            input_records=chunk,
                                        )
                                    )
                                else:
                                    task = (
                                        await runner.run(task.task_id, timeout=timeout)
                                        if before_hy_invocation is None
                                        else await runner.run(
                                            task.task_id,
                                            timeout=timeout,
                                            before_hy_invocation=before_hy_invocation,
                                        )
                                    )
                        finally:
                            _unmark_direct_task(self.tasks.database_path, task.task_id)
                if task.status is not MemoryTaskStatus.COMPLETED:
                    raise HyMemoryBridgeError(task.last_error_code or "hy_memory_incomplete")
                completed.append(task)
                if on_progress:
                    on_progress(
                        tuple(prepared_task for prepared_task, _chunk in prepared),
                        len(completed),
                    )
            expected = {record.evidence_id for record in records if _is_hy_turn_record(record)}
            actual = {
                row["evidence_id"] for task in completed for row in self.tasks.inputs(task.task_id)
            }
            if expected != actual:
                raise HyMemoryBridgeError("hy_memory_input_coverage_incomplete")
            if completed:
                await self._refresh_read_bridge(completed[-1].profile_snapshot)
            return tuple(completed)

    async def process_sdk_job(
        self,
        records: tuple[EvidenceRecord, ...],
        *,
        request_id: str,
        before_hy_invocation: Callable[[], Awaitable[None]],
    ) -> HyMemoryIngestResult:
        """Execute one durable SDK-authority job as exactly one SDK write."""
        if not self.sdk_authority():
            raise HyMemoryBridgeError("sdk_memory_authority_required")
        if not records:
            raise HyMemoryBridgeError("hy_memory_task_without_inputs")
        if self._sedimentation_stopped():
            raise HyMemoryBridgeError("model_sedimentation_disabled")
        if self.source_writes_permitted is not None and not await in_the_background(
            self._source_may_write, list(records)
        ):
            raise HyMemoryBridgeError("hy_memory_source_disabled")
        credentials = await self._active_credentials()
        if credentials is None:
            raise HyMemoryBridgeError("model_profile_not_active")
        snapshot, secret = credentials
        bridge = self._bridge(
            snapshot,
            secret,
            self.data_dir,
            scope_key=records[0].source_id,
        )
        prepare = getattr(bridge, "prepare", None)
        if prepare is not None:
            await prepare()
        request = MemoryTaskRunner.request_for(request_id, records).model_copy(
            update={"source_context": None}
        )
        contents = tuple(
            sanitize_model_input(record.verbatim) for record in records if _imported_as_said(record)
        )
        only_contents = bool(contents) and all(_imported_as_said(record) for record in records)
        await before_hy_invocation()
        result = (
            await bridge.ingest_extracted(request, contents)
            if only_contents
            else await bridge.ingest(request)
        )
        if not result.success:
            raise HyMemoryBridgeError("hy_memory_runtime_failed")
        if only_contents and not result.memories:
            raise HyMemoryBridgeError("hy_memory_import_no_readable_output")
        await self._refresh_read_bridge(snapshot)
        return result

    async def reconcile_unknown_outputs(
        self,
        *,
        limit: int = 128,
        exclude_error_codes: tuple[str, ...] = (),
    ) -> int:
        """Publish HY outputs that can be proven by an unknown task's request id.

        This inquiry is read-only on HY and never invokes the model. Rows with
        no exact match remain unknown for the person's explicit retry choice.
        """
        reconciled = 0
        tasks = await in_the_background(
            self.tasks.unknown_tasks,
            limit=limit,
            exclude_error_codes=exclude_error_codes,
        )
        for task in tasks:
            runner = self.runner_factory()
            bridge: MemoryBridgeProtocol | None = None
            try:
                chunk = await in_the_background(runner._frozen_chunk, task)
                request = runner._request(task, chunk)
                secret = await in_the_background(
                    self.profiles.resolve_snapshot_secret,
                    task.profile_id,
                    task.profile_revision,
                    self.secrets,
                )
                if task.profile_snapshot.has_api_key and secret is None:
                    continue
                bridge = self.bridge_factory(task.profile_snapshot, secret, self.data_dir)
                receipt_inquiry = getattr(bridge, "receipt_for_request", None)
                if receipt_inquiry is not None:
                    sdk_result = await receipt_inquiry(
                        request_id=request.request_id,
                        user_id=request.user_id,
                        agent_id=request.agent_id,
                        evidence_ids=request.evidence_ids,
                    )
                    if sdk_result is None or not sdk_result.success:
                        continue
                    await in_the_background(
                        self.tasks.save_sdk_receipt,
                        task_id=task.task_id,
                        request_id=request.request_id,
                        raw_ids=sdk_result.raw_ids,
                        memory_ids=tuple(memory.memory_id for memory in sdk_result.memories),
                    )
                    outputs = sdk_result.memories
                else:
                    inquiry = getattr(bridge, "outputs_for_request", None)
                    if inquiry is None:
                        continue
                    outputs = await inquiry(
                        request_id=request.request_id,
                        user_id=request.user_id,
                        agent_id=request.agent_id,
                        evidence_ids=request.evidence_ids,
                    )
                if not outputs and self.tasks.sdk_receipt(task.task_id) is None:
                    continue
                derivation = await in_the_background(self.governance.hy_derivation)
                await in_the_background(
                    runner._record_outputs,
                    task=task,
                    chunk=chunk,
                    outputs=outputs,
                    request=request,
                    derivation=derivation,
                )
                resolved = await in_the_background(
                    self.tasks.resolve_unknown,
                    task.task_id,
                    found=True,
                    detail="model_content_outcome_reconciled",
                )
                if resolved is MemoryTaskStatus.COMPLETED:
                    reconciled += 1
                    await self._refresh_read_bridge(task.profile_snapshot)
            except (HyMemoryBridgeError, SecretStoreUnavailableError, ValueError):
                continue
            finally:
                if bridge is not None:
                    await bridge.close()
        return reconciled

    @asynccontextmanager
    async def _explicit_processing_lock(
        self, records: tuple[EvidenceRecord, ...]
    ) -> AsyncIterator[None]:
        async with self._source_processing_lock({record.source_id for record in records}):
            yield

    @asynccontextmanager
    async def _source_processing_lock(
        self,
        source_ids: Collection[str],
        *,
        level: ServiceLevel | None = None,
    ) -> AsyncIterator[None]:
        """Serialize HY work per source while prioritizing queued realtime turns."""
        level = level or current_service_level()
        entries = [
            (source_id, _shared_source_lock(source_id)) for source_id in sorted(set(source_ids))
        ]
        acquired: list[_PrioritySourceLock] = []
        try:
            for _source_id, lock in entries:
                await lock.acquire(level)
                acquired.append(lock)
            yield
        finally:
            for lock in reversed(acquired):
                await lock.release()

    def extraction_window_settings(self) -> ExtractionSettings:
        return self.extraction_settings.get()

    def set_extraction_window(
        self,
        preset: ExtractionWindowPreset,
    ) -> ExtractionSettings:
        """Persist the preset and reclassify deferred work under the new window.

        Widening does not dump every newly eligible task onto the scheduler:
        those that still fall outside stay deferred; those that now fit are
        promoted in the same small batches as a manual extract.
        """
        settings = self.extraction_settings.set_window_preset(preset)
        window = extraction_window_for(preset)
        now = datetime.now(UTC)
        # Narrowing: pending work that no longer fits must leave the live queue.
        self._classify(
            self.tasks.list(
                statuses=(
                    MemoryTaskStatus.PENDING,
                    MemoryTaskStatus.WAITING_FOR_MODEL,
                    MemoryTaskStatus.FAILED,
                )
            )
        )
        newly_eligible: list[str] = []
        for task in self.tasks.list(statuses=(MemoryTaskStatus.DEFERRED_OUTSIDE_WINDOW,)):
            record = self.evidence.get(task.input_ref)
            classified = classify(
                task.task_id,
                at=record.created_at if record else None,
                imported_at=record.imported_at if record else None,
                workspace=record.workspace if record else None,
                length=len(record.verbatim) if record else 1_000_000,
                now=now,
                extraction_window=window,
            )
            if classified is not None:
                newly_eligible.append(task.task_id)
        if newly_eligible:
            self._plan_manual_batches(newly_eligible)
            self.promote_next_manual_batch()
        return settings

    def _plan_manual_batches(self, task_ids: list[str]) -> None:
        self._manual_batch_remaining = list(task_ids)
        total = len(task_ids)
        self._manual_batches_planned = (
            (total + MANUAL_EXTRACT_BATCH_SIZE - 1) // MANUAL_EXTRACT_BATCH_SIZE if total else 0
        )
        self._manual_batches_promoted = 0

    def manual_batch_progress(self) -> tuple[int, int]:
        """`(planned, current)` for the task centre copy.

        `current` is the batch that has actually been handed to the queue, not
        the one that will be next: a plan of three that says "第 3 批" while
        only one has been let through would be the same overstatement the
        single 675 total was.
        """
        if self._manual_batches_planned == 0:
            return 0, 0
        return self._manual_batches_planned, min(
            self._manual_batches_promoted,
            self._manual_batches_planned,
        )

    def promote_next_manual_batch(
        self,
        *,
        task_ids: tuple[str, ...] | None = None,
    ) -> list[MemoryTask]:
        """Promote at most one small batch of deferred work into the queue."""
        if task_ids is not None:
            self._plan_manual_batches(list(task_ids))
        elif not self._manual_batch_remaining and self._manual_batches_planned == 0:
            # Ad-hoc "next batch" with no prior plan.
            self._manual_batches_planned = 1
        if self._manual_batch_remaining:
            take = self._manual_batch_remaining[:MANUAL_EXTRACT_BATCH_SIZE]
            self._manual_batch_remaining = self._manual_batch_remaining[MANUAL_EXTRACT_BATCH_SIZE:]
            promoted = self.tasks.promote_deferred(
                limit=len(take),
                task_ids=tuple(take),
            )
        else:
            promoted = self.tasks.promote_deferred(limit=MANUAL_EXTRACT_BATCH_SIZE)
        if promoted:
            self._manual_batches_promoted += 1
            self._schedule_tasks(self._classify(promoted, within_window=False))
        return promoted

    def _refill_active_window(self) -> list[QueuedWork]:
        """Refill from parked pending work without promoting old evidence."""
        work = self._prepare_active_window()
        if not work:
            self._older_waiting = False
        return work

    def _prepare_active_window(self) -> list[QueuedWork]:
        recovery_cutoff = datetime.now(UTC)
        occupied = tuple(
            self._queue.task_ids()
            | self._in_flight.keys()
            | set(_direct_tasks_for(self.tasks.database_path))
        )
        self.tasks.recover_orphan_running(
            exclude_task_ids=occupied,
            claimed_before=recovery_cutoff,
        )
        rows = self.tasks.active_window(
            now=datetime.now(UTC),
            fresh_window=FRESH_WINDOW,
            extraction_window=self._extraction_window(),
            exclude_task_ids=occupied,
            fresh_before=self._active_window_cursors.get(True),
            backlog_before=self._active_window_cursors.get(False),
        )
        for fresh in (True, False):
            lane = [row for row in rows if row.fresh is fresh and not row.live]
            if lane:
                last = lane[-1]
                self._active_window_cursors[fresh] = (
                    last.at or last.imported_at,
                    last.task.task_id,
                )
        return self._classify_active_window(rows)

    def _classify_active_window(self, rows: list[ActiveMemoryTask]) -> list[QueuedWork]:
        work: list[QueuedWork] = []
        for row in rows:
            task = row.task
            if not self._guard_task(task):
                continue
            self._profile_concurrent = task.profile_snapshot.max_concurrency
            self._profile_timeout = memory_task_timeout_seconds(
                task.profile_snapshot.timeout_seconds
            )
            moment = row.at or row.imported_at
            if moment is None:
                moment = datetime.min.replace(tzinfo=UTC)
            elif moment.tzinfo is None:
                moment = moment.replace(tzinfo=UTC)
            work.append(
                QueuedWork(
                    task_id=task.task_id,
                    at=moment,
                    fresh=row.fresh,
                    all_apps=row.workspace is None,
                    length=row.length,
                    live=row.live,
                    session=row.session,
                )
            )
        return work

    def _extraction_window(self) -> timedelta | None:
        return extraction_window_for(self.extraction_settings.get().window_preset)

    def enqueue_evidence(self, record: EvidenceRecord) -> MemoryTask | None:
        if self._shutting_down:
            return None
        if self._sedimentation_stopped():
            return None
        if (
            record.role not in {"user", "assistant"}
            and record.content_kind is not EvidenceContentKind.MEMORY_CONTENT
        ):
            return None
        # Arrival only coalesces an in-memory timer. prepare_conversation checks
        # current permission off-thread before reading profiles or creating tasks.
        key = (record.user_id, record.source_id, record.conversation_id, record.workspace)
        pending = self._debounces.pop(key, None)
        if pending is not None:
            pending.cancel()
        live = live_sync_arrival.get()
        self._debounces[key] = asyncio.create_task(
            self._enqueue_after_quiet(
                record,
                key,
                datetime.now(UTC),
                live=live,
                delay=(
                    _LIVE_OPEN_TURN_SETTLE_SECONDS
                    if live and _opens_turn(record)
                    else _TURN_SETTLE_SECONDS
                ),
            )
        )
        return None

    def prepare_backfill(
        self,
        should_stop: Callable[[], bool] = lambda: False,
    ) -> list[QueuedWork]:
        """Synchronous compatibility entry; preparation never starts a model."""
        if self._sedimentation_stopped():
            return []
        work = [item for batch in self._prepare_backfill_batches(should_stop) for item in batch]
        return [] if should_stop() or self._shutting_down else work

    def _prepare_backfill_batches(
        self,
        should_stop: Callable[[], bool] = lambda: False,
    ) -> Generator[list[QueuedWork], None, None]:
        """Read bodies as needed; yield the first task before the next conversation."""
        if should_stop() or self._shutting_down or self._sedimentation_stopped():
            return
        active = self.profiles.active(self.secrets)
        if active is None:
            self._record_idle("no_active_profile")
            return
        tasks: list[MemoryTask] = []
        first = True
        visited = 0
        seed_ids = self.evidence.list_conversation_seed_ids()
        diagnostic_event(
            level="debug",
            component="memory.scheduler",
            event="memory_scheduler.scan_inventory",
            outcome="observed",
            details={"pending_count": len(seed_ids)},
        )
        for evidence_id in seed_ids:
            if should_stop() or self._shutting_down:
                return
            self._check_backfill_context(active)
            conversation = self.evidence.list_conversation(evidence_id)
            if should_stop() or self._shutting_down:
                return
            visited += 1
            # A frozen Harness keeps imported conversations and pending jobs,
            # but preparation must not create additional processing tasks.
            if not self._source_may_write(conversation):
                continue
            chunks = self._prepare_conversation_chunks(conversation, active)
            for chunk in chunks:
                if should_stop() or self._shutting_down:
                    return
                # Historical turns may be coalesced across their original
                # boundaries. Freeze that exact union so the runner cannot
                # reconstruct only the final turn from `input_ref` later.
                tasks.append(
                    self._enqueue_chunk(
                        chunk,
                        active,
                        freeze_inputs=sum(_opens_turn(record) for record in chunk) > 1,
                    )
                )
                if len(tasks) >= (1 if first else _BACKFILL_BATCH_SIZE):
                    self._check_backfill_context(active)
                    classified = self._classify(tasks)
                    yield classified
                    tasks = []
                    first = first and not classified
                    visited = 0
            if visited >= _BACKFILL_BATCH_SIZE:
                self._check_backfill_context(active)
                yield self._classify(tasks)
                tasks = []
                visited = 0
        if not should_stop() and not self._shutting_down:
            self._check_backfill_context(active)
            if tasks:
                yield self._classify(tasks)

    def _check_backfill_context(self, active: ModelProfile) -> None:
        if self.profiles.active_identity() != (
            active.profile_id,
            active.revision,
        ):
            raise HyMemoryBridgeError("model_profile_changed")

    def backfill(self) -> None:
        self._schedule_tasks(self.prepare_backfill())

    async def backfill_off_thread(
        self,
        *,
        on_first_scheduled: Callable[[], None] | None = None,
        cohort_size: int | None = None,
    ) -> None:
        """Dispatch bounded preparations while later history is still being read."""
        notified = False
        started = monotonic()
        prepared_count = 0
        diagnostic_event(
            level="debug",
            component="memory.scheduler",
            event="memory_scheduler.scan_started",
            outcome="started",
            details={
                "queue_depth": len(self._queue),
                "in_flight_count": len(self._in_flight),
            },
        )
        stop = threading.Event()
        batches = self._prepare_backfill_batches(stop.is_set)
        exhausted = False
        stopped_saturated = False
        rounds = 0
        scan_error: BaseException | None = None
        try:
            while not self._shutting_down and not exhausted:
                rounds += 1
                round_started = self._backfill_clock()
                handled_batch = False
                async with self._backfill_lock:
                    while not self._shutting_down:
                        if (
                            handled_batch
                            and self._backfill_clock() - round_started
                            >= self._backfill_round_budget_seconds
                        ):
                            break
                        if (
                            cohort_size is not None
                            and len(self._queue) + len(self._in_flight) >= cohort_size
                        ):
                            # A bounded scan stops at its ceiling instead of
                            # waiting for the queue to drain. Waiting here slept
                            # inside the scan lock, so whoever asked for the
                            # scan — a switch toggle, over HTTP — waited with
                            # it, and on a saturated backlog that is not a
                            # moment but the whole backlog. The caller's
                            # timeout then reported a failure for work that had
                            # already been accepted.
                            stopped_saturated = True
                            exhausted = True
                            break
                        if (
                            handled_batch
                            and self._backfill_clock() - round_started
                            >= self._backfill_round_budget_seconds
                        ):
                            break
                        work = await self._next_backfill_batch(batches, stop)
                        if work is None:
                            exhausted = True
                            break
                        handled_batch = True
                        await self._schedule_tasks_off_thread(work)
                        prepared_count += len(work)
                        if work and on_first_scheduled is not None and not notified:
                            notified = True
                            on_first_scheduled()
                if not exhausted and not self._shutting_down:
                    diagnostic_event(
                        level="debug",
                        component="memory.scheduler",
                        event="memory_scheduler.scan_yielded",
                        outcome="yielded",
                        details={
                            "attempt": rounds,
                            "prepared_count": prepared_count,
                            "queue_depth": len(self._queue),
                            "in_flight_count": len(self._in_flight),
                        },
                    )
                    # Release the scan owner between rounds. Realtime preparation
                    # and workers already use the priority scheduler; this yield
                    # lets them claim the next available resource before P2 scans on.
                    await asyncio.sleep(0)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            scan_error = error
            report_exception(
                "memory.scheduler",
                "memory_scheduler.scan_failed",
                error,
                context={"round": rounds},
            )
            raise
        finally:
            stop.set()
            batches.close()
            if prepared_count == 0 and not self._shutting_down:
                if stopped_saturated:
                    # Parked behind a full queue, not out of work: the status
                    # must not read "no_tasks" while hundreds wait.
                    self._record_idle("queue_saturated")
                elif scan_error is None:
                    self._record_idle("no_tasks")
                else:
                    self._record_idle("scan_failed")
            elif prepared_count:
                self._diagnostic_idle_reason = None
            diagnostic_event(
                level="debug",
                component="memory.scheduler",
                event="memory_scheduler.scan_completed",
                outcome=(
                    "failed"
                    if scan_error is not None
                    else "succeeded"
                    if not self._shutting_down
                    else "stopped"
                ),
                duration_ms=elapsed_ms(started),
                error_type=type(scan_error).__name__ if scan_error is not None else None,
                details={
                    "prepared_count": prepared_count,
                    "queue_depth": len(self._queue),
                    "in_flight_count": len(self._in_flight),
                },
            )

    async def accept_backfill_off_thread(self) -> None:
        """Accept processing after the first scheduled batch; the rest continues."""
        if self._shutting_down:
            raise HyMemoryBridgeError("hy_memory_processing_unavailable")
        async with self._accept_backfill_lock:
            if self._shutting_down:
                raise HyMemoryBridgeError("hy_memory_processing_unavailable")
            state = self._backfill_acceptance
            running = state.task if state is not None else None
            if running is not None and not running.done() and state is not None:
                pass
            else:
                state = None
            if state is None:
                state = _BackfillAcceptance(None)
                self._backfill_acceptance = state
                state.task = asyncio.create_task(self._run_accepted_backfill(state))
                self._live_backfill = state.task
        await state.ready.wait()
        if self._shutting_down or state.task is None or state.task.cancelled():
            raise HyMemoryBridgeError("hy_memory_processing_unavailable")
        if state.error is not None:
            raise HyMemoryBridgeError(state.error)

    async def _prewarm_write_bridges(self, source_ids: tuple[str, ...]) -> None:
        credentials = await self._active_credentials()
        if credentials is None:
            return
        snapshot, secret = credentials

        if not source_ids:
            return
        bridge = self._bridge(snapshot, secret, self.data_dir)
        prewarm = getattr(bridge, "warm", None)
        if prewarm is not None:
            await prewarm()
        read_bridge = self._read_bridge(snapshot, secret, self.data_dir)
        read_prewarm = getattr(read_bridge, "warm", None)
        if read_prewarm is not None:
            await read_prewarm()
        self._read_bridges_ready.add((snapshot.profile_id, snapshot.revision))

    async def _run_accepted_backfill(self, state: _BackfillAcceptance) -> None:
        try:
            # Confirmation is an interactive boundary. Return acceptance now,
            # then leave a short window for the first live turn before P2
            # backfill starts occupying the same source.
            await self.service_levels.note_interactive(
                work_id="model-content-enabled",
                for_seconds=_BACKFILL_START_GRACE_SECONDS,
            )
            state.ready.set()
            await asyncio.sleep(_BACKFILL_START_GRACE_SECONDS)
            await self.backfill_off_thread()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            code = str(error)
            state.error = (
                code
                if re.fullmatch(r"model_content_[a-z_]{1,80}", code)
                else "model_content_processing_unavailable"
            )
            report_exception(
                "memory.scheduler",
                "memory_scheduler.accepted_backfill_failed",
                error,
                context={},
            )
            logging.getLogger(__name__).warning("memory_backfill_failed: %s", state.error)
        finally:
            state.ready.set()

    def accepted_backfill_status(
        self,
    ) -> tuple[Literal["running", "done", "failed"], str | None] | None:
        state = self._backfill_acceptance
        if state is None or state.task is None:
            return None
        if state.error is not None:
            return "failed", state.error
        if state.task.cancelled():
            return "failed", "model_content_processing_interrupted"
        return ("done" if state.task.done() else "running"), None

    async def _next_backfill_batch(
        self, batches: Generator[list[QueuedWork], None, None], stop: threading.Event
    ) -> list[QueuedWork] | None:
        async with self.service_levels.admit(
            ServiceResource.IMPORT,
            ServiceLevel.BACKGROUND,
            work_id="memory-backfill-prepare",
        ):
            task = asyncio.create_task(in_the_background(next, batches, None))
            self._backfill_preparations.add(task)
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                stop.set()
                # Executor cancellation does not stop a SQLite reader. Wait before
                # closing its generator or allowing the data-directory lock to go.
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                with suppress(asyncio.CancelledError, Exception):
                    task.result()
                raise
            finally:
                self._backfill_preparations.discard(task)

    def prepare_resume(self) -> list[QueuedWork]:
        """Load one bounded window of unfinished tasks for the work queue."""
        active = self.profiles.active(self.secrets)
        if active is None:
            return []
        self._older_waiting = True
        self.tasks.cancel_superseded(
            profile_id=active.profile_id,
            profile_revision=active.revision,
        )
        self.tasks.cancel_superseded_policy(
            active_prefix=f"conversation:{MEMORY_INGEST_POLICY_VERSION}:"
        )
        # Past refusals recorded while a policy was stopped are cancellations,
        # not failures. Converted here, in bounded batches, so a database that
        # predates the change stops counting a switched-off feature as breakage.
        self.tasks.reclassify_policy_stopped()
        # Dead-end payloads are reaped here, where the other bounded
        # maintenance already runs, because this is the one path every start
        # and every policy change goes through. Bounded, and after the
        # reclassification above: a task that was really a cancellation only
        # becomes one once it has been converted, so reaping first would miss
        # exactly the 1759-row backlog this exists for.
        self.tasks.clear_terminal_payloads()
        self._active_window_cursors.clear()
        return self._prepare_active_window()

    def resume(self) -> None:
        self._schedule_tasks(self.prepare_resume())

    async def resume_off_thread(self) -> None:
        """`resume`, without holding the event loop for the reading."""
        preparation = asyncio.create_task(in_the_background(self.prepare_resume))
        self._dispatch_checks.add(preparation)
        try:
            work = await asyncio.shield(preparation)
        except asyncio.CancelledError:
            # Cancelling the coroutine cannot stop its SQLite worker. Drain it
            # before returning control so shutdown cannot close the database
            # while the preparation thread is still using it.
            while not preparation.done():
                try:
                    await asyncio.shield(preparation)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            with suppress(asyncio.CancelledError, Exception):
                preparation.result()
            raise
        finally:
            self._dispatch_checks.discard(preparation)
        await self._schedule_tasks_off_thread(work)

    async def resume_allowed(self) -> None:
        """Compatibility entry: recover waiting tasks and start backfill."""
        await self.resume_off_thread()
        await self.backfill_off_thread(cohort_size=8)

    def _classify(
        self,
        tasks: list[MemoryTask],
        *,
        live: bool = False,
        within_window: bool = True,
    ) -> list[QueuedWork]:
        """Attach each task's time and class, one evidence read apiece.

        Done here rather than when the work is taken, because here is off the
        event loop: hundreds of store reads on the loop is the regression
        that stalled every host's recall for seventeen seconds.

        Work that falls outside the extraction window is marked deferred so
        the pending count stops pretending it is about to run.
        """
        if self._sedimentation_stopped():
            return []
        now = datetime.now(UTC)
        window = self._extraction_window() if within_window else None
        exempt_all_apps_from_window = True
        work: list[QueuedWork] = []
        deferred_count = 0
        for task in tasks:
            if task.status not in _CLAIMABLE_TASK_STATUSES:
                continue
            if task.last_error_code in {
                "hy_memory_input_exceeds_window",
                "model_content_outcome_unknown",
            }:
                continue
            # A `failed` task is retryable, but only up to a ceiling. Without
            # this a permanently broken task is re-claimed on every dispatch
            # forever, burning model calls and holding a harness slot.
            if task.status is MemoryTaskStatus.FAILED and task.attempts >= TASK_MAX_ATTEMPTS:
                continue
            if not self._guard_task(task):
                continue
            record = self.evidence.get(task.input_ref)
            classified = classify(
                task.task_id,
                at=record.created_at if record else None,
                imported_at=record.imported_at if record else None,
                workspace=record.workspace if record else None,
                length=len(record.verbatim) if record else 1_000_000,
                now=now,
                extraction_window=window,
                live=live,
                session=(record.user_id, record.source_id, record.conversation_id)
                if record
                else None,
                exempt_all_apps_from_window=exempt_all_apps_from_window,
            )
            self._profile_concurrent = task.profile_snapshot.max_concurrency
            self._profile_timeout = memory_task_timeout_seconds(
                task.profile_snapshot.timeout_seconds
            )
            if classified is None:
                self.tasks.defer_outside_window(task.task_id)
                self._older_waiting = True
                deferred_count += 1
            else:
                work.append(classified)
        if deferred_count and not work:
            self._record_idle("outside_window")
        return work

    def queue_depths(self) -> QueueDepths:
        """The two numbers the app shows, plus when work last landed."""
        fresh, backlog = self.tasks.pending_by_recency(fresh_since=datetime.now(UTC) - FRESH_WINDOW)
        return QueueDepths(
            fresh=fresh,
            backlog=backlog,
            recent_completions=self.tasks.recent_completion_times(),
        )

    def active_depths(self) -> tuple[int, int]:
        """The bounded scheduler window, separate from durable parked work."""
        return len(self._queue), len(self._in_flight)

    def _schedule_tasks(self, work: list[QueuedWork]) -> None:
        if self._shutting_down:
            return
        self._queue_validated_work(work)

    async def _schedule_tasks_off_thread(self, work: list[QueuedWork]) -> None:
        if self._shutting_down:
            return
        self._queue_validated_work(work)

    def _queue_validated_work(self, work: list[QueuedWork]) -> None:
        if self._shutting_down:
            return
        for item in work:
            if item.task_id in self._in_flight:
                continue
            self._queue.add(item)
        if work:
            self._diagnostic_idle_reason = None
            diagnostic_event(
                level="debug",
                component="memory.scheduler",
                event="memory_scheduler.tasks_queued",
                outcome="succeeded",
                details={
                    "prepared_count": len(work),
                    "queue_depth": len(self._queue),
                    "in_flight_count": len(self._in_flight),
                },
            )
        self._ensure_workers()
        if self._older_waiting and not self._workers and not self._continuing_older:
            # Nothing queued means no worker will ever find the queue dry and
            # reach for deferred work, so one goes to look.
            self._start_worker()

    @property
    def _max_concurrent(self) -> int:
        if self._fixed_concurrent is not None:
            return self._fixed_concurrent
        return max(1, self._profile_concurrent)

    def _busy_sessions(self) -> set[tuple[str, str, str]]:
        return {work.session for work in self._in_flight.values() if work.session is not None}

    def _harness_load(self) -> dict[str, int]:
        """How much work each harness (`source_id`) has in the model now.

        Counted from what is actually running, so it needs no separate book
        keeping that could drift out of step with `_in_flight`.
        """
        load: dict[str, int] = {}
        for work in self._in_flight.values():
            if work.session is None:
                continue
            source = work.session[1]
            load[source] = load.get(source, 0) + 1
        return load

    #: How much work one harness (`source_id`) may have in the model at once.
    #:
    #: **One.** Within a harness, work runs in order. Two turns of the same
    #: harness in HY at once can each conclude without seeing the other, and HY
    #: decides whether a memory supersedes an older one from what it has
    #: already been told — so the second can overwrite a conclusion the first
    #: had not finished reaching. In HY Memory App the serialisation unit is
    #: the user, and the user is what a harness corresponds to here.
    #:
    #: Harnesses run *alongside* each other, which is where the parallelism
    #: comes from: several sources are several independent conversations and
    #: none needs to wait for another's turns.
    #:
    #: Deliberately not a configurable number and not a hardcoded "10". Ten
    #: was an observation about how many harnesses exist today, used to argue
    #: the load is small — it is not a ceiling to encode. One harness is one
    #: at a time; how many harnesses run at once falls out of how many have
    #: work and how many workers exist.
    HARNESS_SERIAL = 1

    @property
    def _harness_limit(self) -> int:
        """The per-harness cap: one, which is what makes a harness serial."""
        return self.HARNESS_SERIAL

    def _ensure_workers(self) -> None:
        if self._shutting_down or self._sedimentation_stopped():
            return
        load = self._harness_load()
        background_capacity = (
            self._max_concurrent if self._max_concurrent == 1 else self._max_concurrent - 1
        )
        while len(self._workers) < self._max_concurrent and self._queue.ready(
            self._busy_sessions(),
            load,
            self._harness_limit,
        ):
            if len(self._workers) >= background_capacity and not self._queue.live_ready(
                self._busy_sessions(),
                load,
                self._harness_limit,
            ):
                break
            self._start_worker()

    async def _reclaim_orphans(self) -> None:
        """Recover task rows whose owning worker disappeared without a restart."""
        while not self._shutting_down:
            await asyncio.sleep(ORPHAN_RECOVERY_INTERVAL_SECONDS)
            recovery_cutoff = datetime.now(UTC)
            occupied = tuple(
                set(self._in_flight) | set(_direct_tasks_for(self.tasks.database_path))
            )
            try:
                recovered = await in_the_background(
                    self.tasks.recover_orphan_running,
                    exclude_task_ids=occupied,
                    claimed_before=recovery_cutoff,
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                diagnostic_event(
                    level="error",
                    component="memory.scheduler",
                    event="memory_scheduler.orphan_recovery_failed",
                    outcome="failed",
                    error_code="memory_task_orphan_recovery_failed",
                    **diagnostic_exception(error),
                )
                continue
            if recovered:
                diagnostic_event(
                    level="warn",
                    component="memory.scheduler",
                    event="memory_scheduler.orphans_recovered",
                    outcome="recovered",
                    details={"count": recovered},
                )

    def _start_worker(self) -> None:
        worker = asyncio.create_task(self._work())
        self._workers[id(worker)] = worker
        worker.add_done_callback(self._worker_finished)

    def _worker_finished(self, worker: asyncio.Task[None]) -> None:
        self._workers.pop(id(worker), None)
        self._ensure_workers()

    def start_maintenance(self) -> None:
        if self._shutting_down:
            return
        if self._orphan_reclaimer is None or self._orphan_reclaimer.done():
            self._orphan_reclaimer = asyncio.create_task(self._reclaim_orphans())

    async def _work(self) -> None:
        """Take the next piece of work when free, not when it arrived.

        The order used to be fixed by turning the whole prepared list into
        tasks at once, which is why nothing that arrived later could be
        reached until the catch-up finished.
        """
        looked_for_older = False
        while not self._shutting_down:
            if self._sedimentation_stopped():
                return
            # The harness cap is recomputed from what is running, so a worker
            # that just finished frees its harness's slot immediately.
            work = self._queue.take(
                self._busy_sessions(),
                self._harness_load(),
                self._harness_limit,
            )
            if work is None:
                if looked_for_older or self._continuing_older or not self._older_waiting:
                    return
                looked_for_older = True
                self._continuing_older = True
                try:
                    older = await in_the_background(self._refill_active_window)
                except Exception:
                    return
                finally:
                    self._continuing_older = False
                # Look at the queue again either way: work that arrived during
                # the lookup found this worker busy and started no other.
                self._queue_validated_work(older)
                continue
            looked_for_older = False
            self._in_flight[work.task_id] = work
            _mark_direct_task(self.tasks.database_path, work.task_id)
            try:
                task = await in_the_background(self.tasks.require, work.task_id)
                if task.status not in _CLAIMABLE_TASK_STATUSES or (
                    task.status is MemoryTaskStatus.FAILED and task.attempts >= TASK_MAX_ATTEMPTS
                ):
                    continue
                if not await in_the_background(self._guard_task, task):
                    continue
                runner = self.runner_factory()
                # A wall-clock ceiling, because a harness is serial: a task
                # that hangs holds the only slot its harness has, and every
                # later turn of that harness waits behind it. The runner owns
                # the task's lease, so the budget is handed to it rather than
                # enforced here — it raises `hy_memory_timeout`, which its own
                # classification turns into `wait_for_model` (a retry) rather
                # than a terminal failure.
                level = ServiceLevel.REALTIME if work.live else ServiceLevel.BACKGROUND
                scope_key = work.session[1] if work.session is not None else None
                source_ids = () if scope_key is None else (scope_key,)
                async with (
                    self._source_processing_lock(source_ids, level=level),
                    self.service_levels.admit(
                        ServiceResource.MODEL,
                        level,
                        work_id=work.task_id,
                        scope_key=scope_key,
                    ),
                ):

                    async def require_sedimentation() -> None:
                        if self._sedimentation_stopped():
                            raise HyMemoryBridgeError("model_sedimentation_disabled")

                    finished = await runner.run(
                        work.task_id,
                        timeout=self._profile_timeout,
                        before_hy_invocation=require_sedimentation,
                    )
                if finished.status is MemoryTaskStatus.COMPLETED:
                    self._request_read_bridge_refresh(finished.profile_snapshot)
                elif (
                    finished.status is MemoryTaskStatus.CANCELLED
                    and finished.last_error_code == "model_content_continuous_binding_invalid"
                ):
                    # The policy or Evidence changed after scheduling but before
                    # HY was called. Retire that stale binding and immediately
                    # build a replacement from the latest conversation instead
                    # of leaving a permanent failed row until a later scan.
                    await self._replace_stale_binding(finished)
            except asyncio.CancelledError:
                raise
            except (HyMemoryBridgeError, MemoryTaskOwnershipError):
                # Classified by the runner, which has already logged it and
                # either settled the task (a timeout goes back to `pending`)
                # or lost it to whoever owns it now. Overriding that here
                # would turn a retry into a terminal failure.
                continue
            except Exception as error:
                await self._record_dispatch_failure(work.task_id, error)
                continue
            finally:
                # Releases the harness's slot either way, so a task that timed
                # out cannot starve the turns waiting behind it.
                self._in_flight.pop(work.task_id, None)
                _unmark_direct_task(self.tasks.database_path, work.task_id)

    async def _replace_stale_binding(self, task: MemoryTask) -> None:
        latest = await in_the_background(self.evidence.get, task.input_ref)
        if latest is not None:
            await self.enqueue_conversation_off_thread(latest)

    async def _record_dispatch_failure(self, task_id: str, error: Exception) -> None:
        """Make a failure nobody classified visible, and take the task off the board.

        Whatever raised it — the call to the runner itself, or the runner
        crashing between claim and finish — left the task `pending` or
        `running`, where it reads as work in progress that never ends. The
        worker keeps going either way: one bad task must not stop the rest.
        """
        diagnostic_event(
            level="error",
            component="memory.scheduler",
            event="memory_scheduler.dispatch_failed",
            outcome="failed",
            task_id=task_id,
            error_code="memory_task_dispatch_failed",
            **diagnostic_exception(error),
        )
        try:
            await in_the_background(
                self.tasks.fail_unsettled,
                task_id,
                error_code="memory_task_dispatch_failed",
            )
        except Exception as settle_error:
            if isinstance(settle_error, sqlite3.Error):
                record_database_error("memory.scheduler", settle_error)
            diagnostic_event(
                level="error",
                component="memory.scheduler",
                event="memory_scheduler.dispatch_failure_unrecorded",
                outcome="failed",
                task_id=task_id,
                **diagnostic_exception(settle_error),
            )

    async def shutdown(self) -> None:
        self._shutting_down = True
        if self._shutdown_task is None:
            self._shutdown_task = asyncio.create_task(self._finish_shutdown())
        try:
            await asyncio.shield(self._shutdown_task)
        except asyncio.CancelledError:
            # A cancelled lifespan/request must not cancel the executor wrappers
            # before their actual preparation threads finish.
            while not self._shutdown_task.done():
                try:
                    await asyncio.shield(self._shutdown_task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            with suppress(asyncio.CancelledError, Exception):
                self._shutdown_task.result()
            raise

    async def _finish_shutdown(self) -> None:
        reclaimer = self._orphan_reclaimer
        if reclaimer is not None and not reclaimer.done():
            reclaimer.cancel()
        workers = tuple(self._workers.values())
        debounces = tuple(self._debounces.values())
        for task in debounces:
            if not task.done():
                task.cancel()
        for task in workers:
            if not task.done():
                task.cancel()
        await asyncio.gather(
            *(() if reclaimer is None else (reclaimer,)),
            *debounces,
            *workers,
            return_exceptions=True,
        )
        # A thread already preparing durable tasks cannot be cancelled safely.
        # Let it finish; _schedule_tasks rejects work after shutdown starts.
        live = self._live_backfill
        if live is not None and not live.done():
            live.cancel()
        await asyncio.gather(
            *tuple(self._preparations.values()),
            *tuple(self._backfill_preparations),
            *tuple(self._dispatch_checks),
            *(() if live is None else (live,)),
            return_exceptions=True,
        )
        refreshes = tuple(self._read_bridge_refresh_tasks.values())
        for task in refreshes:
            if not task.done():
                task.cancel()
        await asyncio.gather(*refreshes, return_exceptions=True)
        self._preparations.clear()
        self._debounces.clear()
        self._workers.clear()
        self._orphan_reclaimer = None
        self._in_flight.clear()
        self._active_window_cursors.clear()
        self._queue.clear()
        await asyncio.gather(
            *(bridge.close() for bridge in (*self._bridges.values(), *self._read_bridges.values())),
            return_exceptions=True,
        )
        self._bridges.clear()
        self._read_bridges.clear()
        self._read_bridges_ready.clear()
        self._read_bridge_generations.clear()
        self._read_bridge_refresh_tasks.clear()

    async def _active_credentials(self) -> tuple[ModelProfileSnapshot, str | None] | None:
        def read() -> tuple[ModelProfileSnapshot, str | None] | None:
            identity = self.profiles.active_identity()
            if identity is None:
                return None
            snapshot = self.profiles.snapshot(*identity)
            # active() probes has(), which resolves the same secret a second time.
            # Read once per call, then reject a profile changed during the OS read.
            secret = self.profiles.resolve_snapshot_secret(
                *identity,
                self.secrets,
            )
            if self.profiles.active_identity() != identity:
                raise HyMemoryBridgeError("model_profile_changed")
            if snapshot.has_api_key and secret is None:
                raise HyMemoryBridgeError("model_secret_unavailable")
            return snapshot, secret

        try:
            return await in_the_background(read)
        except SecretStoreUnavailableError:
            raise HyMemoryBridgeError("secure_storage_unavailable") from None

    async def digest(self) -> dict[str, object]:
        async with _digest_mutex():
            return await self._digest_locked()

    async def _digest_locked(self) -> dict[str, object]:
        credentials = await self._active_credentials()
        if credentials is None:
            raise HyMemoryBridgeError("model_profile_not_active")
        snapshot, secret = credentials
        bridge = self._bridge(snapshot, secret, self.data_dir)
        if self.sdk_authority():
            agent_ids = await bridge.list_agent_ids(user_id="local")
            sdk_digests = [
                {
                    "agent_id": agent_id,
                    "schemas_recorded": 0,
                    **dict(
                        await bridge.digest(
                            user_id="local",
                            agent_id=agent_id,
                        )
                    ),
                }
                for agent_id in agent_ids
            ]
            return {
                "success": all(result.get("success") is not False for result in sdk_digests),
                "scope_count": len(sdk_digests),
                "skipped_conversation_scopes": 0,
                "schemas_recorded": 0,
                "digests": sdk_digests,
            }
        derivation = await in_the_background(self.governance.hy_derivation)
        agent_ids, skipped_conversation_scopes = await self._digest_scopes(bridge)
        evidence_by_scope = self._digest_evidence()
        digests: list[dict[str, object]] = []
        recorded = 0
        # A digest reads the memory it is about to summarise and writes the
        # summary. Two of them running at once — yesterday's harness and
        # today's, say — each see a state the other is mid-way through
        # changing, and each concludes from it. The result is two summaries of
        # two different half-updated memories, neither of them right.
        #
        # So digests are serialised across every harness, while ordinary work
        # between them still runs alongside. This is the time lock: it costs a
        # wait only when two digests would otherwise overlap.
        for agent_id in agent_ids:
            result = dict(
                await bridge.digest(
                    user_id="local",
                    agent_id=agent_id,
                )
            )
            kept = await self._record_schemas(
                bridge,
                agent_id=agent_id,
                derivation=derivation.citing(evidence_by_scope.get(agent_id, ())),
            )
            recorded += kept
            digests.append({"agent_id": agent_id, "schemas_recorded": kept, **result})
        return {
            "success": all(result.get("success") is not False for result in digests),
            "scope_count": len(digests),
            "skipped_conversation_scopes": skipped_conversation_scopes,
            "schemas_recorded": recorded,
            "digests": digests,
        }

    async def _record_schemas(
        self,
        bridge: MemoryBridgeProtocol,
        *,
        agent_id: str,
        derivation: HyDerivation,
    ) -> int:
        """Bring System 2's mental models back from the graph into governance.

        `digest()` used to compute these and hand them to whoever called the
        endpoint, writing none of them down — so the one layer HY calls a
        profile alongside basic info existed only in Kuzu. Five of them sat
        there on this machine, matching nothing in governance.

        Both halves of that profile come back, not just the schemas. The fix
        above kept only `L6_SCHEMA` and left basic info where it had been:
        in the graph. The snapshot query filters no layer, so those nodes were
        already being read and then dropped one line later — and basic info is
        the half that answers "who is this person", which is the whole point
        of inducing a profile at all.

        They are scoped to every app rather than to the project they were
        induced in: a schema says how the person works, not what one codebase
        contains. A scope with no local evidence left is skipped, because a
        memory we cannot say why we believe does not belong in governance.
        Nor does one induced from evidence the person overruled while the
        digest ran; the next digest induces again from what is left.
        """
        evidence_ids = derivation.evidence_ids
        if not evidence_ids:
            return 0
        try:
            snapshot = await bridge.graph_snapshot(user_id="local", agent_id=agent_id)
        except HyMemoryBridgeError:
            # A digest that ran is still worth reporting; the schemas it wrote
            # will be picked up by the next one.
            return 0
        recorded = 0
        for node in snapshot.nodes:
            if not _is_profile_layer(node.layer):
                continue
            content = sanitize_model_input(node.content)
            if not content.strip():
                continue
            if self.governance.get(node.node_id, user_id="local") is not None:
                continue
            try:
                self.governance.write_from_hy(
                    HyCreate(
                        draft=MemoryDraft(
                            user_id="local",
                            kind=_memory_kind(node.layer),
                            content=content,
                            scopes=("all-apps",),
                            evidence_ids=evidence_ids,
                        ),
                        request_id=f"digest:{agent_id}:{node.node_id}",
                        memory_id=node.node_id,
                    ),
                    user_id="local",
                    derivation=derivation,
                )
            except MemoryEvidenceOverriddenError:
                continue
            recorded += 1
        return recorded

    def _digest_evidence(self) -> dict[str, tuple[str, ...]]:
        """Which evidence each HY scope's confirmed memories rest on.

        A schema is induced from a whole scope rather than from one exchange,
        so what it cites is everything that scope was built from.
        """
        collected: dict[str, set[str]] = {}
        offset = 0
        while True:
            memories = self.governance.list(
                user_id="local",
                status=MemoryStatus.CONFIRMED,
                limit=500,
                offset=offset,
            )
            for memory in memories:
                records = tuple(
                    record
                    for evidence_id in memory.evidence_ids
                    if (record := self.evidence.get(evidence_id)) is not None
                )
                if not records:
                    continue
                collected.setdefault(_hy_agent_id(records), set()).update(
                    record.evidence_id for record in records
                )
            if len(memories) < 500:
                break
            offset += len(memories)
        # Capped at what a draft accepts; the cite is a sample of a scope this
        # large either way, and an oversized one would be refused outright.
        return {
            agent_id: tuple(sorted(evidence_ids))[:10_000]
            for agent_id, evidence_ids in collected.items()
        }

    async def _digest_scopes(self, bridge: MemoryBridgeProtocol) -> tuple[tuple[str, ...], int]:
        """Pick the HY scopes worth running System 2 over.

        HY is the source of truth for which scopes exist, so memories written
        under an earlier routing policy are still reachable. Evidence-derived
        scopes are only a fallback for when HY cannot be listed.
        """
        stable, skipped = await in_the_background(self._digest_targets)
        try:
            stored = await bridge.list_agent_ids(user_id="local")
        except HyMemoryBridgeError:
            return stable, skipped
        if not stored:
            return stable, skipped
        return tuple(sorted({*stable, *stored})), skipped

    def _digest_targets(self) -> tuple[tuple[str, ...], int]:
        """Return stable HY scopes with confirmed memories.

        Workspace and imported-memory scopes are stable enough for System 2.
        Unattributed raw conversations intentionally remain quarantined per
        conversation; digesting each one would be expensive and cannot produce
        meaningful cross-conversation relations.
        """
        targets: set[str] = set()
        skipped: set[str] = set()
        offset = 0
        while True:
            memories = self.governance.list(
                user_id="local",
                status=MemoryStatus.CONFIRMED,
                limit=500,
                offset=offset,
            )
            for memory in memories:
                records = tuple(
                    record
                    for evidence_id in memory.evidence_ids
                    if (record := self.evidence.get(evidence_id)) is not None
                )
                if not records:
                    continue
                if not self._source_may_write(list(records)):
                    # A revoked source is not a memory source any more: it
                    # cannot be read for a new task and must not be handed
                    # back to System 2 through digest either.
                    continue
                if any(record.workspace is not None for record in records) or all(
                    record.content_kind is EvidenceContentKind.MEMORY_CONTENT for record in records
                ):
                    targets.add(_hy_agent_id(records))
                else:
                    skipped.add(_hy_agent_id(records))
            if len(memories) < 500:
                break
            offset += len(memories)
        return tuple(sorted(targets)), len(skipped)

    async def graph_snapshot(self, *, limit: int = 300) -> HyGraphSnapshot:
        credentials = await self._active_credentials()
        if credentials is None:
            # Not "HY found no relations" but "HY was never asked"; the map has
            # to be able to tell the reader which one it is looking at.
            return HyGraphSnapshot(consulted=False)
        snapshot, secret = credentials
        bridge = self._bridge(snapshot, secret, self.data_dir)
        return await bridge.graph_snapshot(
            user_id="local",
            agent_id="*",
            limit=limit,
        )

    async def update_hy_memory(self, memory_id: str, content: str) -> dict[str, object]:
        credentials = await self._active_credentials()
        if credentials is None:
            raise HyMemoryBridgeError("model_profile_not_active")
        snapshot, secret = credentials
        bridge = self._bridge(snapshot, secret, self.data_dir)
        return await bridge.update(memory_id, content)

    async def delete_hy_memory(self, memory_id: str) -> dict[str, object]:
        credentials = await self._active_credentials()
        if credentials is None:
            raise HyMemoryBridgeError("model_profile_not_active")
        snapshot, secret = credentials
        bridge = self._bridge(snapshot, secret, self.data_dir)
        return await bridge.delete(memory_id)

    async def finish_deletion(self, memory_id: str) -> None:
        """Tell HY to forget this deleted memory now, unless someone else already is."""
        for claim in self.governance.claim_deletions(user_id="local", memory_id=memory_id):
            await self._work_deletion(claim)

    async def finish_deletions(self) -> None:
        """One round: the deletions that are due, at most a batch of them."""
        for claim in self.governance.claim_deletions(user_id="local"):
            await self._work_deletion(claim)

    async def _work_deletion(self, claim: DeletionClaim) -> None:
        """Tell HY to forget a deleted memory, recording whether that landed.

        A failure stays on record as failed, with when it is next due; a
        memory HY no longer holds counts as forgotten.
        """
        try:
            await self.delete_hy_memory(claim.memory_id)
        except HyMemoryBridgeError as error:
            self.governance.settle_deletion(claim, user_id="local", error_code=str(error))
        except Exception as error:
            # Recorded like any other failure, so it waits its turn instead of
            # being due again at once, and the sync that called this lives on.
            logging.getLogger(__name__).warning(
                "memory_deletion_unexpected_failure: %s", type(error).__name__
            )
            self.governance.settle_deletion(
                claim, user_id="local", error_code="hy_memory_delete_unexpected"
            )
        else:
            self.governance.settle_deletion(claim, user_id="local", error_code=None)

    def wake_deletions(self) -> None:
        """Have the deletion sync look again now rather than when it next would."""
        self._deletions_due.set()

    async def keep_deletions_in_sync(self) -> None:
        """Runs for as long as Core does, taking each deletion when it falls due.

        A deletion HY did not take has no other moment to be retried: the
        memory is already deleted, so no request will come for it.
        """
        while True:
            try:
                await self.finish_deletions()
                due = self.governance.next_deletion_due(user_id="local")
            except Exception as error:
                _report_deletion_sync_failure(error)
                due = None
            wait = DELETION_SYNC_IDLE_SECONDS
            if due is not None:
                wait = min(wait, max(0.0, (due - self.governance.clock()).total_seconds()))
            await self._wait_for_deletions(wait)

    async def _wait_for_deletions(self, timeout: float) -> None:
        with suppress(TimeoutError):
            await asyncio.wait_for(self._deletions_due.wait(), timeout=timeout)
        self._deletions_due.clear()

    async def graph_width_mismatch(self) -> GraphWidthMismatch | None:
        """What the app shows before extraction starts failing on it.

        Read off the active profile rather than off a live bridge: the whole
        point is that the bridge cannot be built while the widths disagree.
        """
        credentials = await self._active_credentials()
        if credentials is None:
            return None
        snapshot, secret = credentials
        return await in_the_background(
            inspect_profile_graph_width,
            snapshot,
            api_key=secret,
            data_dir=self.data_dir,
        )

    async def decide_graph_width(self, decision: GraphWidthDecision) -> GraphWidthOutcome:
        """Carry out the choice, then drop any bridge built on the old answer."""
        credentials = await self._active_credentials()
        if credentials is None:
            raise HyMemoryBridgeError("model_profile_not_active")
        snapshot, secret = credentials
        outcome = await in_the_background(
            decide_profile_graph_width,
            snapshot,
            api_key=secret,
            data_dir=self.data_dir,
            decision=decision,
        )
        if outcome.archived_path is not None:
            for key in tuple(self._bridges):
                if key[:2] == (snapshot.profile_id, snapshot.revision):
                    self._bridges.pop(key, None)
            self._read_bridges.pop((snapshot.profile_id, snapshot.revision), None)
            self._read_bridges_ready.discard((snapshot.profile_id, snapshot.revision))
            self._read_bridge_generations.pop((snapshot.profile_id, snapshot.revision), None)
            refresh = self._read_bridge_refresh_tasks.pop(
                (snapshot.profile_id, snapshot.revision), None
            )
            if refresh is not None and not refresh.done():
                refresh.cancel()
        return outcome

    async def semantic_search(
        self, query: str, *, limit: int = 12
    ) -> tuple[tuple[str, float], ...]:
        return tuple(
            (hit.memory_id, hit.score) for hit in await self.semantic_recall(query, limit=limit)
        )

    async def semantic_recall(
        self, query: str, *, limit: int = 12
    ) -> tuple[SemanticMemoryHit, ...]:
        credentials = await self._active_credentials()
        if credentials is None:
            return await self._search_with_builtin_embedding(query, limit=limit)
        snapshot, secret = credentials
        key = (snapshot.profile_id, snapshot.revision)
        bridge = self._read_bridges.get(key)
        if bridge is None or key not in self._read_bridges_ready:
            return ()
        return await self._semantic_bridge_hits(bridge, query, limit=limit)

    @staticmethod
    async def _semantic_bridge_hits(
        bridge: MemoryBridgeProtocol,
        query: str,
        *,
        limit: int,
    ) -> tuple[SemanticMemoryHit, ...]:
        search_memories = getattr(bridge, "search_memories", None)
        if callable(search_memories):
            found = await search_memories(
                query,
                user_id="local",
                agent_id="*",
                limit=limit,
            )
        else:
            found = await bridge.search_scores(
                query,
                user_id="local",
                agent_id="*",
                limit=limit,
            )
        normalized = normalize_semantic_hits(found)
        if normalized is None:
            raise HyMemoryBridgeError("hy_memory_search_shape_unknown")
        return normalized[:limit]

    async def _search_with_builtin_embedding(
        self,
        query: str,
        *,
        limit: int,
    ) -> tuple[SemanticMemoryHit, ...]:
        """Read a store whose profile is gone, using the encoder that filled it.

        Retiring a profile does not retire the memories it produced, and the
        embedding those vectors were written with is built in, so there is
        nothing here that needs a generation model.

        Bounded by a store that already exists, on purpose: building the
        client is what creates one, and the width it would create is the
        built-in encoder's. A user who has not chosen a model yet would then
        meet a width mismatch on their first wider profile, invented by a
        search they ran while waiting.
        """
        if not hy_memory_store_exists(self.data_dir):
            return ()
        bridge = self._read_bridge(builtin_retrieval_snapshot(), None, self.data_dir)
        try:
            return await self._semantic_bridge_hits(bridge, query, limit=limit)
        except GraphWidthMismatchError:
            # Written by a wider embedding than the built-in one, so it
            # cannot be read from here. The app already asks the user what to
            # do about that; answering it as a search failure would move the
            # decision somewhere they cannot act on it.
            return ()

    def _create_runner(self) -> MemoryTaskRunner:
        return MemoryTaskRunner(
            tasks=self.tasks,
            profiles=self.profiles,
            secrets=self.secrets,
            evidence=self.evidence,
            governance=self.governance,
            data_dir=self.data_dir,
            bridge_factory=self._bridge,
            close_bridge=False,
            sdk_authority=self.sdk_authority,
        )

    def _bridge(
        self,
        profile: ModelProfileSnapshot,
        secret: str | None,
        data_dir: Path,
        *,
        scope_key: str | None = None,
    ) -> MemoryBridgeProtocol:
        """One bridge per profile revision and write harness.

        It built `HyMemoryBridge` directly until R-27, which left the cascades
        that reach HY through here — deletion, and now correction — with no
        seam to stand in for HY. The cache is the reason to keep the method:
        the bridge owns a client and an embedding model, so asking for one per
        call would pay for that per call.
        """
        key = (profile.profile_id, profile.revision, scope_key)
        bridge = self._bridges.get(key)
        if bridge is None:
            bridge = self.bridge_factory(profile, secret, data_dir)
            self._bridges[key] = bridge
        return bridge

    def _read_bridge(
        self,
        profile: ModelProfileSnapshot,
        secret: str | None,
        data_dir: Path,
    ) -> MemoryBridgeProtocol:
        """One read-only client per profile revision, isolated from write locks."""
        key = (profile.profile_id, profile.revision)
        bridge = self._read_bridges.get(key)
        if bridge is None:
            bridge = self.bridge_factory(profile, secret, data_dir)
            self._read_bridges[key] = bridge
        return bridge

    async def _refresh_read_bridge(self, snapshot: ModelProfileSnapshot) -> None:
        """Publish a fresh HY read client after a successful vector write.

        Chroma clients opened before another client writes do not reliably
        observe the new vector collection. Warm the replacement first, then
        swap it into the recall path so queries never see an unready bridge.
        """
        key = (snapshot.profile_id, snapshot.revision)
        async with self._read_bridge_refresh_lock:
            if self._shutting_down:
                return
            replacement: MemoryBridgeProtocol | None = None
            try:
                secret = await in_the_background(
                    self.profiles.resolve_snapshot_secret,
                    snapshot.profile_id,
                    snapshot.revision,
                    self.secrets,
                )
                if snapshot.has_api_key and secret is None:
                    raise HyMemoryBridgeError("model_secret_unavailable")
                replacement = self.bridge_factory(snapshot, secret, self.data_dir)
                warm = getattr(replacement, "warm", None)
                if warm is not None:
                    await warm()
            except Exception as error:
                if replacement is not None:
                    with suppress(Exception):
                        await replacement.close()
                diagnostic_event(
                    level="warn",
                    component="memory.semantic",
                    event="semantic_read_bridge.refresh_failed",
                    outcome="degraded",
                    error_code="semantic_read_bridge_refresh_failed",
                    details={
                        "generation": self._read_bridge_generations.get(key, 0),
                    },
                    **diagnostic_exception(error),
                )
                return

            assert replacement is not None
            previous = self._read_bridges.get(key)
            generation = self._read_bridge_generations.get(key, 0) + 1
            self._read_bridges[key] = replacement
            self._read_bridges_ready.add(key)
            self._read_bridge_generations[key] = generation
            diagnostic_event(
                level="debug",
                component="memory.semantic",
                event="semantic_read_bridge.refreshed",
                outcome="succeeded",
                details={
                    "generation": generation,
                },
            )
            if previous is not None and previous is not replacement:
                await previous.close()

    def _request_read_bridge_refresh(self, snapshot: ModelProfileSnapshot) -> None:
        """Refresh once after the first write creates the vector collection."""
        if self._shutting_down:
            return
        key = (snapshot.profile_id, snapshot.revision)
        if self._read_bridge_generations.get(key, 0) >= 1:
            return
        current = self._read_bridge_refresh_tasks.get(key)
        if current is not None and not current.done():
            return
        task = asyncio.create_task(self._refresh_read_bridge_after_first_write(snapshot))
        self._read_bridge_refresh_tasks[key] = task

        def finish_refresh(
            finished: asyncio.Task[None], refresh_key: tuple[str, int] = key
        ) -> None:
            self._finish_read_refresh(refresh_key, finished)

        task.add_done_callback(finish_refresh)

    async def _refresh_read_bridge_after_first_write(self, snapshot: ModelProfileSnapshot) -> None:
        await asyncio.sleep(_READ_BRIDGE_FIRST_REFRESH_DELAY_SECONDS)
        await self._refresh_read_bridge(snapshot)

    def _finish_read_refresh(
        self,
        key: tuple[str, int],
        task: asyncio.Task[None],
    ) -> None:
        if self._read_bridge_refresh_tasks.get(key) is task:
            self._read_bridge_refresh_tasks.pop(key, None)
        if not task.cancelled():
            task.exception()

    def enqueue_conversation(self, seed: EvidenceRecord) -> list[MemoryTask]:
        created, work = self._prepare_conversation_work(seed)
        self._schedule_tasks(work)
        return created

    async def enqueue_conversation_off_thread(self, seed: EvidenceRecord) -> list[MemoryTask]:
        """`enqueue_conversation`, without holding the loop for the reading.

        Split the same way as the catch-up above, and for the same reason: the
        reading wants a thread and only the scheduling needs the loop. Live
        sync arrives here for every conversation that changes, and rebuilding
        one is seconds for a long one — the watchdog caught this path holding
        the loop 2.1s, which is 2.1s of no host getting an answer.
        """
        created, work = await in_the_background(self._prepare_conversation_work, seed)
        await self._schedule_tasks_off_thread(work)
        return created

    def _prepare_conversation_work(
        self,
        seed: EvidenceRecord,
        *,
        arrival_at: datetime | None = None,
        live: bool = False,
    ) -> tuple[list[MemoryTask], list[QueuedWork]]:
        """Record one conversation's tasks and classify them, off the loop."""
        created = self.prepare_conversation(seed, arrival_at=arrival_at, live=live)
        return created, self._classify(created, live=live)

    def prepare_conversation(
        self,
        seed: EvidenceRecord,
        *,
        arrival_at: datetime | None = None,
        live: bool = False,
    ) -> list[MemoryTask]:
        """Record the tasks for one conversation, without scheduling them.

        Nothing here touches asyncio, and each store call opens its own
        connection, so a worker thread is safe.
        """
        active = self.profiles.active(self.secrets)
        if active is None:
            return []
        conversation = self.evidence.list_conversation(seed.evidence_id)
        if not self._source_may_write(conversation):
            return []
        # D98: a conversation whose only questions came from a sub agent used
        # to be dropped whole. Its turns are ordinary user-assistant data, so
        # the only reason to skip now is having nothing to send at all.
        if not any(
            item.role == "user" or item.content_kind is EvidenceContentKind.MEMORY_CONTENT
            for item in conversation
        ):
            return []
        # A realtime turn that is still open must not become a task, whichever
        # path gets here first. The debounce is the record of "this arrival is
        # still waiting for its reply"; without this check a periodic scan
        # could prepare the half turn before the closing event arrives, which
        # is exactly what the complete-turn rule forbids.
        if live and self._open_arrival_pending(seed, conversation):
            return []
        chunks = self._prepare_conversation_chunks(conversation, active)
        admitted = self._admitted_chunks(chunks, active)
        return [done or self._enqueue_chunk(chunk, active) for chunk, done in admitted]

    def _open_arrival_pending(
        self, seed: EvidenceRecord, conversation: list[EvidenceRecord]
    ) -> bool:
        """Whether this conversation has a live arrival still waiting to close."""
        key = (seed.user_id, seed.source_id, seed.conversation_id, seed.workspace)
        pending = self._debounces.get(key)
        if pending is None or pending.done():
            return False
        tail = [record for record in conversation if _is_hy_turn_record(record)]
        if not tail:
            return False
        last = tail[-1]
        return bool(last.role == "user" and _opens_turn(last))

    def _admitted_chunks(
        self,
        chunks: list[tuple[EvidenceRecord, ...]],
        profile: ModelProfile,
    ) -> list[tuple[tuple[EvidenceRecord, ...], MemoryTask | None]]:
        """Each chunk still to be sent, with the completed task that already holds it, if any.

        A finished turn is not previewed again. An unfinished one resting on
        evidence the person overruled stays held back by that evidence, alone:
        a deletion must not stop the turns that come after it.
        """
        admitted: list[tuple[tuple[EvidenceRecord, ...], MemoryTask | None]] = []
        for chunk in chunks:
            done = self._finished_chunk_task(chunk)
            admitted.append((chunk, done))
        return admitted

    def _not_yet_queued(
        self, chunks: list[tuple[EvidenceRecord, ...]], profile: ModelProfile
    ) -> list[tuple[EvidenceRecord, ...]]:
        """Drop turns an earlier ingest policy already extracted.

        Changing how a conversation is cut must not send its history to HY a
        second time. Tool records alone do not reopen a turn that was sent
        before tool calls were imported.
        """
        if not chunks:
            return chunks
        queued = {
            ref["evidence_id"]: ref
            for ref in self.tasks.completed_inputs_before(
                chunks[0][0],
                active_prefix=f"conversation:{MEMORY_INGEST_POLICY_VERSION}:",
                profile_id=profile.profile_id,
                profile_revision=profile.revision,
            )
        }
        if not queued:
            return chunks

        def sent(record: EvidenceRecord) -> bool:
            ref = queued.get(record.evidence_id)
            return ref is not None and matches_evidence_ref(record, ref)

        return [
            chunk
            for chunk in chunks
            if not all(sent(r) for r in ([r for r in chunk if r.role != "tool"] or chunk))
        ]

    def _prepare_conversation_chunks(
        self,
        conversation: list[EvidenceRecord],
        profile: ModelProfile,
        *,
        merge_history: bool = False,
    ) -> list[tuple[EvidenceRecord, ...]]:
        try:
            chunks = self._not_yet_queued(
                _conversation_chunks(conversation, completed_only=True), profile
            )
            return _merge_historical_chunks(chunks) if merge_history else chunks
        except ValueError as error:
            if str(error) != "hy_memory_input_exceeds_window":
                raise
        records = tuple(record for record in conversation if _is_hy_turn_record(record))
        if self._shutting_down or not records:
            return []
        self._check_backfill_context(profile)
        # Keep a full, immutable failure snapshot. Appended originals remain
        # unprocessed while the same blocker persists; they are never marked done.
        identity = sha256(
            json.dumps([evidence_ref(record) for record in records], sort_keys=True).encode()
        ).hexdigest()
        seed = records[0]
        group = sha256(
            json.dumps(
                {
                    "policy": MEMORY_INGEST_POLICY_VERSION,
                    "profile": (profile.profile_id, profile.revision),
                    "conversation": (
                        seed.user_id,
                        seed.source_id,
                        seed.conversation_id,
                        seed.workspace,
                        seed.project,
                    ),
                    "blockers": [
                        evidence_ref(record) for record in records if len(record.verbatim) > 12_000
                    ],
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        self.tasks.create(
            request_id=f"oversized:{group}:{identity}",
            user_id=records[0].user_id,
            kind=MemoryTaskKind.EXTRACT,
            input_ref=records[0].evidence_id,
            profile=profile,
            input_records=records,
            preparation_error="hy_memory_input_exceeds_window",
            preparation_group=group,
        )
        return []

    def _original_records(self, records: tuple[EvidenceRecord, ...]) -> tuple[EvidenceRecord, ...]:
        originals: dict[str, EvidenceRecord] = {}
        for record in records:
            current = self.evidence.get(record.evidence_id)
            if current is None:
                raise HyMemoryBridgeError("hy_memory_evidence_changed")
            originals.setdefault(current.evidence_id, current)
        return tuple(originals.values())

    def _guard_task(self, task: MemoryTask) -> bool:
        if task.last_error_code in {
            "hy_memory_input_exceeds_window",
            "model_content_outcome_unknown",
        }:
            return False
        if self.tasks.profile_stop_reason(task.profile_id, task.profile_revision) is not None:
            return False
        if self.source_writes_permitted is not None:
            record = self.evidence.get(task.input_ref)
            if record is None or not self.source_writes_permitted(record.source_id):
                # Freeze: the durable row stays pending. Reopening the Harness
                # causes the ordinary resume pass to classify it again.
                return False
        return True

    def _source_may_write(self, conversation: list[EvidenceRecord]) -> bool:
        """Whether this conversation's source is allowed to become memories."""
        if self.source_writes_permitted is None:
            return True
        return all(self.source_writes_permitted(record.source_id) for record in conversation)

    def _finished_chunk_task(
        self,
        chunk: tuple[EvidenceRecord, ...],
    ) -> MemoryTask | None:
        """Task identity already deduplicates completed chunks."""
        del chunk
        return None

    def _enqueue_chunk(
        self,
        chunk: tuple[EvidenceRecord, ...],
        profile: ModelProfile,
        *,
        freeze_inputs: bool = False,
    ) -> MemoryTask:
        if any("segment_hash" in evidence_ref(record) for record in chunk):
            freeze_inputs = True
        source = chunk[0]
        input_ref = _chunk_input_ref(chunk)
        finished = self._finished_chunk_task(chunk)
        if finished is not None:
            return finished
        digest = sha256(
            "|".join(
                (
                    MEMORY_INGEST_POLICY_VERSION,
                    *(("frozen-v1",) if freeze_inputs else ()),
                    f"{profile.profile_id}:{profile.revision}",
                    *(
                        ":".join(
                            (
                                item.evidence_id,
                                str(item.revision),
                                item.workspace or "",
                                item.project or "",
                                str(evidence_ref(item).get("segment_index", "")),
                                str(evidence_ref(item).get("segment_hash", "")),
                            )
                        )
                        for item in chunk
                    ),
                )
            ).encode()
        ).hexdigest()[:24]
        task = self.tasks.create(
            request_id=(
                f"conversation:{MEMORY_INGEST_POLICY_VERSION}:{source.source_id}:"
                f"{profile.profile_id}:{profile.revision}:{digest}"
            ),
            user_id=source.user_id,
            kind=MemoryTaskKind.EXTRACT,
            input_ref=input_ref,
            profile=profile,
            input_records=chunk if freeze_inputs else (),
        )
        if task.status is MemoryTaskStatus.PENDING:
            self.tasks.supersede_unstarted(
                task,
                request_prefix=(
                    f"conversation:{MEMORY_INGEST_POLICY_VERSION}:{source.source_id}:"
                    f"{profile.profile_id}:{profile.revision}:"
                ),
            )
        return task

    async def _enqueue_after_quiet(
        self,
        record: EvidenceRecord,
        key: tuple[str, str, str, str | None],
        arrival_at: datetime,
        *,
        live: bool = False,
        delay: float = _TURN_SETTLE_SECONDS,
    ) -> None:
        try:
            await asyncio.sleep(delay)
            previous = self._preparations.get(key)
            if previous is not None:
                await asyncio.shield(previous)
            if self._shutting_down:
                return
            preparation = asyncio.create_task(
                self._prepare_live_conversation(record, key, arrival_at, live=live)
            )
            self._preparations[key] = preparation
            await asyncio.shield(preparation)
        finally:
            if self._debounces.get(key) is asyncio.current_task():
                self._debounces.pop(key, None)

    async def _prepare_live_conversation(
        self,
        record: EvidenceRecord,
        key: tuple[str, str, str, str | None],
        arrival_at: datetime,
        *,
        live: bool = False,
    ) -> None:
        preparation: asyncio.Task[tuple[list[MemoryTask], list[QueuedWork]]] | None = None
        try:
            if self._shutting_down:
                return
            if live and _opens_turn(record):
                # A realtime user message is an open turn, not a model input
                # on its own. The assistant/tool event that closes the turn
                # will schedule the complete conversation. A quiet-period
                # timeout must not manufacture an incomplete HY task.
                return
            preparation = asyncio.create_task(
                in_the_background(
                    self._prepare_conversation_work,
                    record,
                    arrival_at=arrival_at,
                    live=live,
                )
            )
            _, work = await asyncio.shield(preparation)
            if not self._shutting_down:
                await self._schedule_tasks_off_thread(work)
        except asyncio.CancelledError:
            # Cancelling the wrapper cannot stop its SQLite worker. Drain it
            # without dispatching the eventual result, including repeated cancel.
            if preparation is not None:
                while not preparation.done():
                    try:
                        await asyncio.shield(preparation)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                with suppress(asyncio.CancelledError, Exception):
                    preparation.result()
            raise
        except HyMemoryBridgeError as error:
            if str(error).startswith("model_content_"):
                logging.getLogger(__name__).info("%s", str(error))
            else:
                _report_conversation_prepare_failure(record.source_id, error)
        except Exception as error:
            # No evidence or credentials in logs. Persisted work remains retryable.
            _report_conversation_prepare_failure(record.source_id, error)
        finally:
            if self._preparations.get(key) is asyncio.current_task():
                self._preparations.pop(key, None)


def _memory_kind(layer: str | None) -> MemoryKind:
    normalized = (layer or "").lower()
    if "coding" in normalized:
        return MemoryKind.CODING
    if "working" in normalized:
        return MemoryKind.WORKING
    if "schema" in normalized:
        return MemoryKind.SCHEMA
    if "intention" in normalized:
        return MemoryKind.INTENTION
    return MemoryKind.CHAT


def _hy_agent_id(records: tuple[EvidenceRecord, ...]) -> str:
    source_days = sorted(
        {
            f"{record.source_id}\0"
            f"{(record.created_at or record.imported_at).astimezone(UTC).date().isoformat()}"
            for record in records
        }
    )
    workspaces = sorted({record.workspace for record in records if record.workspace is not None})
    if workspaces:
        identity = "\0".join(("workspace", *source_days, *workspaces))
    elif records and all(record.source_id == "memory-content-export" for record in records):
        identity = "\0".join(
            ("unverified-memory-report", *sorted({r.conversation_id for r in records}))
        )
    elif all(record.content_kind is EvidenceContentKind.MEMORY_CONTENT for record in records):
        return UNIFIED_HY_AGENT_ID
    else:
        conversations = sorted(
            {f"{record.source_id}\0{record.conversation_id}" for record in records}
        )
        identity = "\0".join(("conversation", *source_days, *conversations))
    suffix = sha256(identity.encode()).hexdigest()[:32]
    return f"{UNIFIED_HY_AGENT_ID}:scope:{suffix}"


def _is_profile_layer(layer: str | None) -> bool:
    """HY names the profile layers itself; this follows rather than re-decides.

    Its client holds `_PROFILE_LAYERS = {"l0_basic_info", "l6_schema"}`. This
    list drifted from that once already, to `L1_` — the raw conversation
    layer: had anything ever reached it, whole transcripts would have been
    broadcast to every app as though they were facts about the person.

    One predicate, two callers: what counts as a profile decides both the
    scope an extracted memory gets and which graph nodes a digest writes down.
    Those were separate judgements until the second one was found keeping only
    half the layers, so drift between them is no longer possible.
    """
    return (layer or "").upper().startswith(("L0_", "L6_SCHEMA"))


def _is_memory_note(record: EvidenceRecord) -> bool:
    """Words the person saved as a memory themselves, rather than said in a conversation."""
    meta = record.origin_meta.get(EvidenceOrigin.MANUAL_IMPORT)
    return (
        record.source_id == "one-memory-manual"
        and meta is not None
        and meta.raw_meta.get("input_mode") == "direct_memory_note"
    )


def _imported_as_said(record: EvidenceRecord) -> bool:
    """HY writes it as it stands and only deduplicates, instead of extracting from it.

    A note stays raw conversation because it is still the person's own words, which
    matter discovery and the raw index read; what it is not is a conversation for
    HY to judge whether to keep.
    """
    return record.content_kind is EvidenceContentKind.MEMORY_CONTENT or _is_memory_note(record)


def _memory_scopes(
    records: tuple[EvidenceRecord, ...],
    *,
    layer: str | None,
) -> tuple[str, ...]:
    if records and all(
        _is_memory_note(record)
        and record.origin_meta[EvidenceOrigin.MANUAL_IMPORT].raw_meta.get("recall_scope")
        == "all-apps"
        for record in records
    ):
        return ("all-apps",)
    workspaces = {record.workspace for record in records if record.workspace is not None}
    if _is_profile_layer(layer):
        return ("all-apps", *sorted(workspaces))
    if not workspaces and all(
        record.content_kind is EvidenceContentKind.MEMORY_CONTENT for record in records
    ):
        return ("all-apps",)
    specific = workspaces or {
        scope
        for record in records
        if (
            scope := opaque_workspace_id(
                record.source_id,
                f"unscoped-conversation:{record.conversation_id}",
            )
        )
        is not None
    }
    # Granting a source is what shares it; the specific scope stays so a
    # caller scoped to that project still matches (`all-apps` is not a wildcard).
    return ("all-apps", *sorted(specific))


_HARNESS_PREFIXES = (
    "<codex_internal_context",
    "# agents.md instructions",
    "<environment_context>",
    "<permissions instructions>",
    "<system-reminder>",
    "<system_notification>",
    "<instructions>",
)
_HOST_ENVELOPE = re.compile(
    r"(?:"
    r"^<timestamp>.*?<system_(?:notification|reminder)>|"
    r"^<system_(?:notification|reminder)>|"
    r'^\{\s*"request_id"\s*:.*?"steps"\s*:'
    r")",
    re.IGNORECASE | re.DOTALL,
)


#: D74: HY itself drops tool records on the chat path and cuts each one to
#: 2 KiB on the coding path, so a turn is sent whole and only the text HY will
#: actually read counts toward the safety ceiling. Splitting by raw length
#: before HY chose a path cut long answers mid-way and sent tool-heavy turns as
#: dozens of calls. ONE_MEMORY_SPLIT_TURNS=1 restores the 12,000 / 24 split.
_WHOLE_TURN_CHARACTERS = 60_000
_HY_TOOL_CHARACTERS = coding_tool_result_max_bytes()
#: Hard ceiling on the UTF-8 bytes one window may hold, whatever HY counts.
#: HY reads the payload in-process, so a turn made of enormous raw tool
#: records is held in memory even though HY will cut each one down. Cut by
#: raw size once a window passes this, so no single call can grow without
#: bound. Counted in bytes, not characters: one CJK character is three bytes,
#: so a character count would let a Chinese window hold three times as much.
_WINDOW_BYTE_CEILING = 2_000_000


def _window_bytes(record: EvidenceRecord) -> int:
    return len(record.verbatim.encode("utf-8"))


def _whole_turns() -> bool:
    return os.environ.get("ONE_MEMORY_SPLIT_TURNS", "").strip() != "1"


def _hy_read_length(record: EvidenceRecord) -> int:
    if record.role == "tool":
        return min(len(record.verbatim), _HY_TOOL_CHARACTERS)
    return len(record.verbatim)


def _conversation_chunks(
    records: list[EvidenceRecord],
    *,
    max_characters: int | None = None,
    max_turns: int | None = None,
    completed_only: bool = False,
) -> list[tuple[EvidenceRecord, ...]]:
    """One HY window per turn: a question and everything that answered it.

    HY decides chat or coding from what a turn contains, so a turn is sent
    whole and alone; earlier turns reach it as history. A turn too long for
    one window is split, and every window is led by the question so HY never
    reads answers without what they answer.

    A proposal nobody has answered yet is not a turn of its own: an
    `assistant` prefix with no user reply yet joins the user turn that adopts
    it, so "the AI suggested X" and "the person adopted X" reach HY together.
    Splitting them sent HY an answer without what it answers and lost the
    proposal/adoption pair the author attribution gate reads.
    """
    turns: list[list[EvidenceRecord]] = []
    for record in records:
        if not _is_hy_turn_record(record):
            continue
        pending_proposal = bool(turns) and not _opens_turn(turns[-1][0])
        if not turns or (_opens_turn(record) and not pending_proposal):
            turns.append([record])
        else:
            # Either the reply to the turn already open, or the user message
            # that adopts a proposal nobody had answered yet.
            turns[-1].append(record)

    # Import keeps the unanswered tail as Evidence so it remains searchable
    # and can be completed by a later sync. It is not yet a settled turn for
    # HY, however. Put the rule here, before every caller's chunk preparation,
    # so backfill, ordinary imports and live capture cannot disagree about it.
    # Explicit memory content remains a valid one-record input, and an
    # assistant proposal followed by the person's adoption remains the joined
    # proposal/adoption turn established above rather than a user-opened turn.
    if completed_only and turns and _trailing_user_turn_is_open(turns[-1]):
        turns.pop()

    whole = _whole_turns() and max_characters is None
    if whole:
        size = _hy_read_length
        max_characters = _WHOLE_TURN_CHARACTERS
        max_turns = max_turns or 1_000_000
    else:
        size = lambda record: len(record.verbatim)  # noqa: E731
        max_characters = max_characters or 12_000
        max_turns = max_turns or 24

    chunks: list[tuple[EvidenceRecord, ...]] = []
    for turn in turns:
        original_question = turn[0] if _opens_turn(turn[0]) else None
        expanded: list[EvidenceRecord] = []
        for record in turn:
            reserve = (
                size(original_question)
                if original_question is not None
                and record is not original_question
                and size(original_question) < max_characters
                else 0
            )
            segment_limit = max(1, max_characters - reserve)
            if size(record) <= segment_limit:
                expanded.append(record)
                continue
            spans = _semantic_segment_spans(record.verbatim, segment_limit)
            expanded.extend(
                segment_evidence_record(record, index=index, start=start, end=end)
                if len(spans) > 1
                else record
                for index, (start, end) in enumerate(spans)
            )
        question = expanded[0] if expanded and _opens_turn(expanded[0]) else None
        current: list[EvidenceRecord] = []
        for record in expanded:
            if current and (
                len(current) >= max_turns
                or sum(size(item) for item in current) + size(record) > max_characters
                or sum(_window_bytes(item) for item in current) + _window_bytes(record)
                > _WINDOW_BYTE_CEILING
            ):
                chunks.append(tuple(current))
                current = (
                    [question]
                    if question is not None
                    and max_turns > 1
                    and size(question) + size(record) <= max_characters
                    else []
                )
            current.append(record)
        chunks.append(tuple(current))
    return _merge_tool_only_chunks(chunks)


def _semantic_segment_spans(text: str, limit: int) -> list[tuple[int, int]]:
    """Split a single oversized message at paragraph/sentence boundaries."""
    if len(text) <= limit:
        return [(0, len(text))]
    spans: list[tuple[int, int]] = []
    start = 0
    separators = ("\n\n", "\n", "。", "！", "？", ". ", "! ", "? ")
    while start < len(text):
        hard_end = min(len(text), start + limit)
        if hard_end == len(text):
            end = hard_end
        else:
            floor = start + max(1, limit // 2)
            candidates = [text.rfind(separator, floor, hard_end) for separator in separators]
            boundary = max(candidates)
            if boundary < floor:
                end = hard_end
            else:
                matched = next(
                    separator for separator in separators if text.startswith(separator, boundary)
                )
                end = boundary + len(matched)
        spans.append((start, end))
        start = end
    return spans


def _merge_tool_only_chunks(
    chunks: list[tuple[EvidenceRecord, ...]],
) -> list[tuple[EvidenceRecord, ...]]:
    """Never hand HY a window with only tool records.

    Splitting a long turn can leave tool calls in a later window after the
    question and answer filled the first one. HY's ``add`` treats that input as
    empty in the live WorkBuddy/Codex shape and answers ``content or messages
    is required``. Merge each such window into the previous one, or into the
    next one when it is the first; record order is preserved.
    """
    if not any(chunk and all(record.role == "tool" for record in chunk) for chunk in chunks):
        return chunks
    merged: list[tuple[EvidenceRecord, ...]] = []
    leading_tools: list[EvidenceRecord] = []
    for chunk in chunks:
        if not chunk:
            continue
        if chunk and all(record.role == "tool" for record in chunk):
            if merged:
                merged[-1] = (*merged[-1], *chunk)
            else:
                leading_tools.extend(chunk)
            continue
        if leading_tools:
            chunk = (*leading_tools, *chunk)
            leading_tools = []
        merged.append(chunk)
    # A conversation made only of tool records has no HY input turn at all;
    # dropping those windows is the same rule that keeps tool-only windows out.
    if leading_tools:
        return []
    return merged


def _merge_historical_chunks(
    chunks: list[tuple[EvidenceRecord, ...]],
    *,
    max_turns: int = _HISTORY_MERGE_TURNS,
    max_characters: int = _HISTORY_MERGE_CHARACTERS,
) -> list[tuple[EvidenceRecord, ...]]:
    """Coalesce adjacent unsent turns from one historical conversation.

    Callers pass chunks from exactly one conversation. Completed turns were
    removed before this function, so every merged member is still unstarted.
    Oversized turns stay intact and alone; the existing segment path owns them.
    """
    if not chunks:
        return []
    merged: list[tuple[EvidenceRecord, ...]] = []
    current: list[EvidenceRecord] = []
    current_turns = 0
    current_characters = 0
    for chunk in chunks:
        chunk_characters = sum(len(record.verbatim) for record in chunk)
        if current and (
            current_turns >= max_turns or current_characters + chunk_characters > max_characters
        ):
            merged.append(tuple(current))
            current = []
            current_turns = 0
            current_characters = 0
        current.extend(chunk)
        current_turns += 1
        current_characters += chunk_characters
    if current:
        merged.append(tuple(current))
    return merged


def _chunk_input_ref(chunk: tuple[EvidenceRecord, ...]) -> str:
    user_records = tuple(item for item in chunk if item.role == "user")
    return user_records[-1].evidence_id if user_records else chunk[-1].evidence_id


def _opens_turn(record: EvidenceRecord) -> bool:
    return record.role == "user"


def _trailing_user_turn_is_open(turn: list[EvidenceRecord]) -> bool:
    if len(turn) != 1:
        return False
    opener = turn[0]
    return (
        _opens_turn(opener)
        and opener.content_kind is EvidenceContentKind.RAW_CONVERSATION
        and not _is_memory_note(opener)
    )


def _is_hy_turn_record(record: EvidenceRecord) -> bool:
    """What HY receives of a turn: the input turns and the tool calls made in it."""
    if record.role == "tool":
        return record.content_kind is EvidenceContentKind.RAW_CONVERSATION and bool(
            record.verbatim.strip()
        )
    return _is_hy_input_turn(record)


def _is_hy_input_turn(record: EvidenceRecord) -> bool:
    source = record.verbatim.strip()
    if not source:
        return False
    if record.content_kind is EvidenceContentKind.MEMORY_CONTENT:
        return True
    # Assistant output rides along as context. The hygiene checks below are
    # about telling the person's own words apart from what a harness typed on
    # their behalf, and none of that applies to a reply we already know came
    # from a model.
    if record.role == "assistant":
        return True
    # A sub-agent question is still a protocol `user` message. Host-specific
    # author guesses do not decide whether it reaches HY.
    if record.role != "user":
        return False
    if source.casefold().startswith(_HARNESS_PREFIXES):
        return False
    return _HOST_ENVELOPE.search(source[:2_000]) is None
