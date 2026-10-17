import asyncio
import json
import logging
import os
import re
import sqlite3
import sys
import threading
import time
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Collection,
    Iterable,
    MutableMapping,
)
from contextlib import asynccontextmanager, nullcontext, suppress
from copy import copy
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from functools import partial
from importlib import import_module
from pathlib import Path
from typing import Any, Literal, cast
from uuid import UUID

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)

from one_memory import __version__, memory_visibility
from one_memory.auth import local_api_guard, renderer_origins
from one_memory.background_work import shutdown_bulk_pool, warm_bulk_pool
from one_memory.config import Settings, load_settings
from one_memory.context_routes import context_router
from one_memory.database_access import (
    MAIN_DATABASE_REPORT_SECONDS,
    close_main_databases,
    log_main_database_stats,
    main_database,
    report_main_database,
)
from one_memory.diagnostic_log import (
    bind_trace_id,
    diagnostic_event,
    diagnostic_exception,
    elapsed_ms,
    flush_diagnostics,
    read_trace_events,
    record_database_error,
    reset_trace_id,
    resource_snapshot,
)
from one_memory.exception_reporting import (
    install_asyncio_exception_handler,
    install_threading_excepthook,
    report_exception,
    watched_task,
)
from one_memory.external_memory_access import external_access, require_external_endpoint
from one_memory.extraction_settings import ExtractionSettings, ExtractionSettingsStore
from one_memory.file_imports import FileImportStore, file_import_router
from one_memory.home_summary import HomeSummary, read_home_summary
from one_memory.hy_lifecycle import HyLifecycle
from one_memory.hy_memory_bridge import (
    GraphWidthDecision,
    GraphWidthMismatch,
    HyMemoryBridge,
    HyMemoryBridgeError,
    builtin_retrieval_snapshot,
)
from one_memory.hy_runtime_config import (
    DiagnosticLogLevel,
    detailed_logging_enabled,
    diagnostic_log_level,
    set_detailed_logging,
    set_diagnostic_log_level,
)
from one_memory.imports.access import (
    AuthorizedSourceRoot,
    SourceAccessError,
    root_identity_matches,
)
from one_memory.imports.adapters import SourceAdapter, SyncCompleteness
from one_memory.imports.candidates import (
    SourceCandidateError,
    SourceCandidateSelection,
    SourceCandidateStore,
)
from one_memory.imports.connections import (
    ConnectionIntentError,
    ConnectionIntentReceipt,
    SourceConnectionStore,
)
from one_memory.imports.grants import SourceGrantReceipt, SourceGrantStore
from one_memory.imports.host_packages import CapabilityState, HostPackageCapabilities
from one_memory.imports.jobs import (
    ImportIssueRecord,
    ImportJob,
    ImportJobStore,
    ImportStatus,
    InvalidJobTransitionError,
)
from one_memory.imports.live_sync import LiveSyncHealth, PollingLiveSyncSupervisor
from one_memory.imports.live_watch import WATCH_MODE, FileWatchLiveSync
from one_memory.imports.locations import SourceLocation, inspect_source_location
from one_memory.imports.models import EvidenceOrigin, EvidenceRecord
from one_memory.imports.runner import live_sync_arrival
from one_memory.imports.sources.common import SourceInstallState
from one_memory.imports.sources.registry import (
    RootCandidateKind,
    SourceRegistration,
    build_source_catalog,
    workbuddy_default_homes,
)
from one_memory.imports.storage import ConversationSearchUnavailableError, EvidenceStore
from one_memory.imports.supervisor import ImportSupervisor
from one_memory.input_sanitization import sanitize_model_input
from one_memory.library_upgrade import LibraryUpgradeError, prepare_library
from one_memory.loop_watchdog import LoopWatchdog
from one_memory.matters.discovery import MatterDiscovery
from one_memory.matters.model import MatterModel
from one_memory.matters.routes import matter_router
from one_memory.matters.store import MatterStore
from one_memory.memory_governance import (
    DeletionStatus,
    GovernedMemory,
    IndexSyncStatus,
    MemoryDeletion,
    MemoryDraft,
    MemoryEvidenceNotFoundError,
    MemoryGovernanceStore,
    MemoryIndexSync,
    MemoryMutationConflictError,
    MemoryMutationRequestConflictError,
    MemoryStatus,
    MemoryVersion,
    PersonWrites,
)
from one_memory.memory_library import (
    MemoryLibraryPage,
    read_conversation_memories,
    read_memory_library,
)
from one_memory.memory_notes import MemoryNoteStore, memory_note_router
from one_memory.memory_processing import (
    MemoryBridgeProtocol,
    MemoryProcessingSupervisor,
    _conversation_chunks,
    memory_task_timeout_seconds,
)
from one_memory.memory_qa import MemoryAnswerer, MemoryAnswerError
from one_memory.memory_queue import (
    ExtractionState,
    ExtractionWindowPreset,
    extraction_window_for,
    resolve_extraction_state,
)
from one_memory.memory_server.config_state import ConfigStore
from one_memory.memory_tasks import MemoryTask, MemoryTaskStatus, MemoryTaskStore
from one_memory.model_content_routes import model_content_router
from one_memory.model_presets import seed_preset_profile
from one_memory.model_probe import ModelProber, ModelProbeResult
from one_memory.model_processing_permission_guard import ModelProcessingPermissionLease
from one_memory.model_profiles import (
    ActiveModelProfileError,
    ModelCreationOperationConflictError,
    ModelCreationOperationLookupError,
    ModelCreationOperationReceipt,
    ModelProfile,
    ModelProfileConflictError,
    ModelProfileInput,
    ModelProfilePatch,
    ModelProfileStore,
    ModelProfileUnavailableError,
)
from one_memory.model_request_budget import BudgetedModelTransport, ModelRequestBudget
from one_memory.onboarding import OnboardingState, OnboardingStore
from one_memory.recall import GovernedRecallTarget, RecallReceipt, RecallRequest
from one_memory.runtime import CoreRuntime
from one_memory.secrets import (
    KeyringSecretStore,
    SecretStore,
    SecretStoreErrorCode,
    SecretStoreUnavailableError,
)
from one_memory.service_levels import (
    ResourceLimit,
    ServiceLevel,
    ServiceLevelScheduler,
    ServiceResource,
    current_service_level,
    service_level_context,
)
from one_memory.source_import_receipts import SourceImportReceipt, build_source_import_receipt
from one_memory.source_read_access import SourceReadAccess
from one_memory.source_totals import SourceTotals
from one_memory.workspace_resolution import resolve_workspace_scopes

logger = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: Literal["one-memory-core"]
    version: str
    instance_id: str
    # Catching up on evidence with no memory yet runs after startup, so its
    # progress is not implied by this process answering at all. "running" is
    # not a degraded state: recall works throughout.
    memory_backfill: Literal["running", "done", "failed"] = "done"
    memory_backfill_error: str | None = None
    # Derived content-index construction is independent of the memory
    # catch-up and must never be reported as ready just because the process
    # is answering. Reads fall back to exact LIKE while this is not ready.
    content_candidate_backfill: Literal["idle", "running", "ready", "failed"] = "idle"
    content_candidate_backfill_failure: str | None = None


class BackupRequest(BaseModel):
    target: str


class BackupResult(BaseModel):
    target: str
    bytes: int


class BackupRestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=4096)


class BackupRestoreResponse(BaseModel):
    state: Literal["restored"]
    restored_bytes: int
    previous_backup_path: str
    source_schema_version: int


class SourceResponse(BaseModel):
    source_id: str
    display_name: str
    storage_kinds: list[str]
    available: bool
    authorized: bool
    detected: bool | None
    #: Conversations the last explicit detect found in the agreed root; None
    #: until a detect has run. Carried so the authorization page can say how
    #: much history is waiting, not just that something is there.
    session_count: int | None = None
    root_label: str | None
    candidate_path: str
    candidate_kind: RootCandidateKind
    imported_messages: int
    imported_conversations: int
    capabilities: HostPackageCapabilities
    sync_completeness: SyncCompleteness
    grant_revision: int = 0
    root_id: str | None = None
    sync_enabled: bool = False
    location_state: Literal["found", "not_found", "inaccessible", "invalid"] = "not_found"
    candidate_root_id: str | None = None
    candidate_revision: int = 0
    candidate_change_allowed: bool = False
    candidate_change_reason: str | None = None


class PreviewSourceCandidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=4096)


class SaveSourceCandidateRequest(PreviewSourceCandidateRequest):
    expected_revision: StrictInt = Field(ge=0)
    expected_root_id: str = Field(min_length=1, max_length=128)
    user_confirmed: StrictBool = False


class AuthorizeSourceRequest(BaseModel):
    confirm_environment_override: bool = False
    expected_root_id: str | None = Field(default=None, min_length=1, max_length=128)
    user_confirmed: StrictBool = False


class SourceSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: StrictBool


class SourceSyncState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    sync_enabled: bool


class DetectionResponse(BaseModel):
    source_id: str
    detected: bool
    #: How many conversations the detect walk actually found. Zero with
    #: `detected=True` is "installed, nothing imported yet"; the authorization
    #: page shows the number instead of implying nothing was found at all.
    session_count: int = 0


class CreateImportJobRequest(BaseModel):
    source_id: str
    operation: Literal["history", "sync"] = "history"


class ConnectSourcesRequest(BaseModel):
    source_ids: tuple[str, ...] = Field(min_length=1, max_length=32)
    confirm_environment_overrides: bool = False
    full_rescan: bool = False
    onboarding: bool = False
    enable_sync: bool = False
    request_id: str | None = Field(default=None, min_length=1, max_length=256)
    source_roots: dict[str, str] = Field(default_factory=dict)
    source_candidate_revisions: dict[str, StrictInt] | None = None
    import_upload_id: UUID | None = None


ONBOARDING_LARGE_HISTORY_SESSIONS = 200
ONBOARDING_HISTORY_WINDOW = timedelta(days=3)


class ConnectSourcesResponse(BaseModel):
    accepted_source_ids: tuple[str, ...]
    failed_sources: dict[str, str]
    jobs: tuple[ImportJob, ...]
    request_id: str | None = None


class CreateMemoryRequest(BaseModel):
    request_id: str
    draft: MemoryDraft


class MemoryMutationRequest(BaseModel):
    request_id: str
    expected_revision: int


class CorrectMemoryRequest(MemoryMutationRequest):
    content: str = Field(min_length=1, max_length=1_000_000)
    scopes: tuple[str, ...] | None = Field(default=None, max_length=128)


class MemoryScopesRequest(MemoryMutationRequest):
    model_config = ConfigDict(extra="forbid")
    scopes: tuple[str, ...] = Field(min_length=1, max_length=128)

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, scopes: tuple[str, ...]) -> tuple[str, ...]:
        if any(not scope.strip() or len(scope) > 512 or scope.strip() == "*" for scope in scopes):
            raise ValueError("invalid_memory_scope")
        cleaned = tuple(dict.fromkeys(scope.strip() for scope in scopes))
        if "private" in cleaned and len(cleaned) != 1:
            raise ValueError("private_scope_must_be_exclusive")
        return cleaned


class EvidenceExcerpt(BaseModel):
    evidence_id: str
    available: bool
    source_id: str | None = None
    role: str | None = None
    written_by: str | None = None
    created_at: datetime | None = None
    verbatim: str | None = None
    #: Three-way state so "not yet a memory" is not ambiguous.
    extraction_state: ExtractionState | None = None


class ConversationSearchHit(BaseModel):
    evidence_id: str
    conversation_id: str
    source_id: str
    workspace: str | None = None
    project: str | None = None
    created_at: datetime | None = None
    excerpt: str
    title: str | None = None
    turn_count: int | None = Field(default=None, ge=0)
    memory_count: int | None = Field(default=None, ge=0)
    turn_offset: int | None = Field(default=None, ge=0)


class ConversationSearchEmptyReason(StrEnum):
    """Why nothing came back, because the three reasons need three answers."""

    NO_MATCHES = "no_matches"
    NOTHING_IMPORTED = "nothing_imported"


class ConversationSearchResponse(BaseModel):
    hits: tuple[ConversationSearchHit, ...] = ()
    #: `None` whenever there are hits. Set only to tell an empty result apart
    #: from an empty library; a search that cannot run at all answers 503
    #: instead, so the interface never has to guess between the three.
    empty_reason: ConversationSearchEmptyReason | None = None
    imported_messages: int = 0


class ConversationMessage(BaseModel):
    evidence_id: str
    role: str
    written_by: str
    created_at: datetime | None = None
    verbatim: str
    turn_index: int = Field(default=0, ge=0)
    system_context: bool = False


class ConversationTranscript(BaseModel):
    conversation_id: str
    source_id: str
    messages: tuple[ConversationMessage, ...] = ()
    total_turns: int = Field(default=0, ge=0)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)
    has_more: bool = False
    #: The last turn asked something and got nothing back. Import and show it;
    #: do not draw a memory from it.
    trailing_turn_unanswered: bool = False


class ConversationSummary(BaseModel):
    evidence_id: str
    conversation_id: str
    source_id: str
    workspace: str | None = None
    project: str | None = None
    title: str
    latest_at: datetime
    turn_count: int = Field(ge=0)
    memory_count: int = Field(ge=0)


class ConversationBrowserPage(BaseModel):
    items: tuple[ConversationSummary, ...] = ()
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)


class ConversationMemories(BaseModel):
    items: tuple[GovernedMemory, ...] = ()


class RecentConversationItem(BaseModel):
    evidence_id: str
    conversation_id: str
    source_id: str
    role: str
    written_by: str
    created_at: datetime | None = None
    imported_at: datetime
    time_basis: Literal["conversation", "import"]
    excerpt: str
    truncated: bool = False


class RecentConversationsPage(BaseModel):
    items: tuple[RecentConversationItem, ...] = ()
    total: int = 0
    offset: int = 0
    limit: int = 30


class MemoryDetail(BaseModel):
    memory: GovernedMemory
    evidence: tuple[EvidenceExcerpt, ...]
    history: tuple[MemoryVersion, ...]
    # `None` for a memory nobody has rewritten: there is no index to be behind,
    # and a state the user never caused is a state the interface would have to
    # explain away.
    index_sync: MemoryIndexSync | None = None


class MemoryProcessingBlock(BaseModel):
    kind: str
    reason: str
    count: int


class WorkBuddyProcessingMetrics(BaseModel):
    injected: int = 0
    timed_out: int = 0
    unavailable: int = 0


class ProcessingMetrics(BaseModel):
    raw_evidence: int = 0
    complete_turns: int = 0
    hy_calls: int = 0
    completed: int = 0
    unknown: int = 0
    failed: int = 0
    cancelled: int = 0
    cancelled_reasons: dict[str, int] = Field(default_factory=dict)
    governed_memories: int = 0
    workbuddy: WorkBuddyProcessingMetrics = Field(default_factory=WorkBuddyProcessingMetrics)


class MemoryProcessingSummary(BaseModel):
    pending: int = 0
    #: `pending` split by how recent the evidence is (R-02's two numbers).
    fresh_pending: int = 0
    backlog_pending: int = 0
    #: Evidence kept out of automatic extraction by the window (S3.2).
    outside_window_pending: int = 0
    #: When work last landed, so a depth that is falling can be told from one
    #: that is stuck.
    recent_completions: tuple[datetime, ...] = ()
    #: Rows the store marks `running`. **Not the current concurrency**: a task
    #: is marked running before anything slow happens, so one a worker later
    #: gives up on stays here until it is recovered. Use `in_flight_count` for
    #: how much is really running; use `stranded_running` for the difference.
    running: int = 0
    #: Marked `running` but owned by no worker: work that is stranded. A
    #: harness is serial, so a stranded task blocks everything behind it.
    #: Optional in the contract so existing readers stay valid; Core always
    #: sends it. A reader that does not see it should treat it as unknown
    #: rather than as zero.
    stranded_running: int | None = None
    #: Failed and never attempted because the continuous policy was not in
    #: force (revoked or superseded). Part of `failed`, but not a failure to
    #: extract: nothing was ever sent to HY. Shown apart so a revoked policy
    #: does not read as a broken pipeline.
    unexecuted_policy_failures: int | None = None
    waiting_for_model: int = 0
    completed: int = 0
    failed: int = 0
    unknown: int = 0
    unknown_reasons: dict[str, int] = Field(default_factory=dict)
    blocked_stages: tuple[MemoryProcessingBlock, ...] = ()
    seconds_per_completion: float | None = None
    extraction_window_preset: ExtractionWindowPreset = ExtractionWindowPreset.THREE_MONTHS
    #: Manual / widen-window batch plan: planned N, current M (0 when idle).
    manual_extract_batches_planned: int = 0
    manual_extract_batches_current: int = 0
    #: Work actually admitted to the scheduler. `pending` is durable parked
    #: work, not the length of this in-memory queue.
    active_queue: int = 0
    in_flight_count: int = 0
    metrics: ProcessingMetrics = Field(default_factory=ProcessingMetrics)


class RetryUnknownMemoryTasksRequest(BaseModel):
    confirm: Literal[True]


class RetryUnknownMemoryTasksReceipt(BaseModel):
    reconciled: int = 0
    requeued: int = 0


class GraphWidthStatus(BaseModel):
    """What the app needs to offer the choice before extraction fails on it."""

    mismatch: GraphWidthMismatch | None = None
    #: How many memories a rebuild would leave without their relations, so the
    #: rebuild option can say what it costs rather than just what it does.
    memory_count: int = 0


class GraphWidthDecisionRequest(BaseModel):
    decision: GraphWidthDecision


class GraphWidthDecisionReceipt(BaseModel):
    #: Only ever set by REBUILD_WITH_NEW. `None` means nothing was archived.
    archived_path: str | None = None
    #: Only ever set by EXPORT_FIRST, and shown to the user verbatim: a backup
    #: whose location they were never told is one they cannot use.
    exported_path: str | None = None
    #: Still set after EXPORT_FIRST, because exporting settles nothing. The
    #: app uses this to decide whether to keep asking.
    mismatch: GraphWidthMismatch | None = None


class ExtractionWindowUpdate(BaseModel):
    window_preset: ExtractionWindowPreset


class DetailedLoggingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    enabled: StrictBool


class DiagnosticLogLevelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    level: Literal["error", "warning", "info", "debug"]


class DiagnosticTraceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    request_id: str | None = Field(default=None, min_length=1, max_length=256)
    trace_id: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def exactly_one_identity(self) -> "DiagnosticTraceRequest":
        if (self.request_id is None) == (self.trace_id is None):
            raise ValueError("exactly_one_trace_identity_required")
        return self


class ManualExtractRequest(BaseModel):
    #: Optional evidence ids; omit to promote the next deferred batch.
    evidence_ids: tuple[str, ...] = ()


class ManualExtractReceipt(BaseModel):
    promoted: int
    batches_planned: int
    batches_current: int


class BoundaryAckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=1024)


class AskMemoryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2_000)
    scope: str = Field(default="all-apps", min_length=1, max_length=512)
    # Host-injected project directory, same boundary as recall. Optional so
    # the web UI and older callers keep working unchanged.
    workspace_path: str | None = Field(default=None, max_length=4_096)


class AskMemoryResponse(BaseModel):
    answer: str
    memories: tuple[GovernedMemory, ...]
    mode: Literal["generated", "extractive", "empty"]


class MemoryGraphNode(BaseModel):
    node_id: str
    node_type: Literal["memory", "source", "schema", "topic"]
    label: str
    memory_id: str | None = None
    source_id: str | None = None
    kind: str | None = None
    status: str | None = None
    confidence: float | None = None


class MemoryGraphEdge(BaseModel):
    edge_id: str
    source_node_id: str
    target_node_id: str
    relation: str
    weight: float | None = None
    origin: Literal["provenance", "same_conversation", "shared_tag", "hy_relation"]


class MemoryGraphResponse(BaseModel):
    total_memories: int
    projected_memories: int
    nodes: tuple[MemoryGraphNode, ...]
    edges: tuple[MemoryGraphEdge, ...]
    hy_graph_available: bool = True


class _MapCache:
    """The last map that was drawn successfully, and the library it drew.

    D91: the map is the app's slowest read, and while a library is being
    written to it can cross its five second interrupt. Without this the
    memory view then has nothing to show, even though the same map was drawn
    a moment ago from a library that has not changed since.

    Keyed on the library rather than on time: a count and the newest
    `updated_at` are two indexed reads, and they answer whether redrawing
    could produce anything different.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._key: tuple[object, ...] | None = None
        self._value: MemoryGraphResponse | None = None

    @staticmethod
    def library_version(app: FastAPI) -> tuple[int, str]:
        database_path = app.state.settings.data_dir / "one-memory.sqlite3"
        with main_database(database_path).read() as db:
            row = db.execute(
                "SELECT COUNT(*), COALESCE(MAX(updated_at),'') FROM governed_memories "
                "WHERE user_id='local' AND status='confirmed' AND deleted_at IS NULL"
            ).fetchone()
        return (int(row[0]), str(row[1]))

    @staticmethod
    def grant_version(app: FastAPI) -> tuple[int, int, str, str]:
        """A fingerprint of the authorization table.

        D95: a plain "is anything revoked" flag cannot tell a second
        withdrawal from the first — both are just true — so the map drawn
        before it kept being served with the newly withdrawn source still on
        it. Counting the live grants and taking the newest grant and
        withdrawal times changes on a withdrawal, a restoration and a fresh
        grant alike.
        """
        database_path = app.state.settings.data_dir / "one-memory.sqlite3"
        with main_database(database_path).read() as db:
            row = db.execute(
                "SELECT COUNT(*), COALESCE(SUM(revoked_at IS NULL),0), "
                "COALESCE(MAX(revoked_at),''), COALESCE(MAX(granted_at),'') FROM source_grants"
            ).fetchone()
        return (int(row[0]), int(row[1] or 0), str(row[2]), str(row[3]))

    def get(self, key: tuple[object, ...]) -> MemoryGraphResponse | None:
        with self._lock:
            return self._value if self._key == key else None

    def remember(self, key: tuple[object, ...], value: MemoryGraphResponse) -> None:
        with self._lock:
            self._key = key
            self._value = value


def create_app(
    settings: Settings | None = None,
    *,
    manage_runtime: bool = False,
    source_catalog: dict[str, SourceRegistration] | None = None,
    secret_store: SecretStore | None = None,
) -> FastAPI:
    runtime_settings = settings or load_settings()
    if runtime_settings.retrieval_index_enabled and not manage_runtime:
        raise ValueError("retrieval_runtime_ownership_required")

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        lifecycle_started = time.monotonic()
        loop = asyncio.get_running_loop()
        install_asyncio_exception_handler(loop)
        install_threading_excepthook()
        diagnostic_event(
            level="info",
            component="core.lifecycle",
            event="core.initializing",
            outcome="started",
            instance_id=runtime_settings.instance_id,
        )
        runtime = CoreRuntime(runtime_settings) if manage_runtime else nullcontext()
        with runtime:
            database_path = runtime_settings.data_dir / "one-memory.sqlite3"
            # Before any store: each one below changes the library as it opens.
            try:
                # Anti-downgrade: a database a newer build wrote must not be
                # opened by this one. Runs before anything else touches it.
                from one_memory.schema_gate import guard_schema_version

                app.state.schema_version = guard_schema_version(database_path)
                prepared = prepare_library(database_path)
            except LibraryUpgradeError as error:
                logging.getLogger("one_memory.library_upgrade").error("%s", error)
                raise
            app.state.memory_governance_store = MemoryGovernanceStore(
                database_path, prepared=prepared
            )
            # Hold the rebuild until every startup write below has finished; the
            # gate is released just before the app starts serving.
            app.state.memory_governance_store.start_content_candidate_backfill(
                hold_until_startup_release=True
            )
            from one_memory.ui_session import UISessionStore

            app.state.ui_sessions = UISessionStore(instance_id=runtime_settings.instance_id)
            # Taken before anything else holds the store, so nothing HY drives
            # can take it: the person's writes come through the API alone.
            app.state.person_writes = app.state.memory_governance_store.claim_person_writes("api")
            app.state.evidence_store = EvidenceStore(database_path)
            app.state.memory_write_queue_path = database_path
            app.state.memory_write_evidence_store = app.state.evidence_store
            from one_memory.memory_server.data_safety import MIN_FREE_BYTES
            from one_memory.memory_server.durable_queue import (
                DurableWriteDrain,
                DurableWriteQueue,
                execute_job,
                production_drain_budget,
            )

            memory_write_queue = DurableWriteQueue(
                database_path,
                min_free_bytes=MIN_FREE_BYTES,
                evidence=app.state.evidence_store,
            )
            app.state.memory_write_queue = memory_write_queue
            repaired_identity_failures = await asyncio.to_thread(
                memory_write_queue.repair_pre_send_identity_failures
            )
            if repaired_identity_failures:
                diagnostic_event(
                    level="info",
                    component="memory.write_queue",
                    event="memory.identity_failures_repaired",
                    outcome="requeued",
                    details={"count": repaired_identity_failures},
                )
            # Off the startup path: on a real corpus this index costs seconds,
            # and the app cannot be unresponsive for them. Until it lands the
            # ordering query is slow but correct.
            app.state.evidence_store.ensure_imported_index_in_background()
            from one_memory.session_registry import SessionRegistry

            app.state.session_registry = SessionRegistry(database_path)
            app.state.session_registry.initialize()
            from one_memory.change_outbox import ChangeOutbox

            app.state.change_outbox = ChangeOutbox(database_path)
            app.state.change_outbox.initialize()
            from one_memory.session_inbox import SessionInbox

            app.state.session_inbox = SessionInbox(database_path)
            app.state.session_inbox.initialize()
            from one_memory.session_lease import SessionLease

            app.state.session_lease = SessionLease(database_path)
            app.state.session_lease.initialize()
            app.state.call_event_store = None
            app.state.call_event_recording_error = False
            if (
                runtime_settings.call_event_retention_days is not None
                and runtime_settings.call_event_max_events is not None
            ):
                from one_memory.call_event_store import CallEventStore

                app.state.call_event_store = CallEventStore(
                    database_path,
                    retention_days=runtime_settings.call_event_retention_days,
                    max_events=runtime_settings.call_event_max_events,
                )
                app.state.call_event_store.initialize()
            app.state.source_totals = SourceTotals(app.state.evidence_store)
            app.state.source_read_access = SourceReadAccess(database_path)
            evidence_store = app.state.evidence_store
            app.state.recall_target = GovernedRecallTarget(
                app.state.memory_governance_store,
                filter_memories=app.state.source_read_access.memories,
                semantic_search=lambda query: _memory_processing_supervisor(app).semantic_recall(
                    query
                ),
                # Read per request rather than cached: an import that lands
                # while the app is open should make its workspace reachable
                # without a restart. Affordable only because the read is served
                # by a covering index; see `EvidenceStore.known_workspaces`.
                resolve_workspace=lambda workspace_path: resolve_workspace_scopes(
                    workspace_path,
                    evidence_store.known_workspaces(user_id="local"),
                ),
            )
            app.state.import_job_store = ImportJobStore(database_path)
            app.state.onboarding_store = OnboardingStore(database_path)
            app.state.extraction_settings_store = ExtractionSettingsStore(database_path)
            app.state.source_grant_store = SourceGrantStore(
                database_path,
                home=runtime_settings.source_home,
            )
            app.state.config_store = ConfigStore(runtime_settings.data_dir)
            app.state.source_candidate_store = SourceCandidateStore(
                database_path, home=runtime_settings.source_home
            )
            app.state.source_catalog = (
                source_catalog
                if source_catalog is not None
                else build_source_catalog(
                    home=runtime_settings.source_home,
                    import_root=runtime_settings.data_dir / "import-files" / "ready",
                )
            )
            if not runtime_settings.imports_enabled:
                app.state.source_catalog = {}
            app.state.source_permission_locks = {
                source_id: asyncio.Lock() for source_id in app.state.source_catalog
            }
            _follow_workbuddy_home(app, runtime_settings.source_home)
            app.state.source_detection = {}
            app.state.secret_store = secret_store or KeyringSecretStore()
            app.state.model_profile_store = ModelProfileStore(database_path)

            async def cleanup_stale_model_secrets() -> None:
                try:
                    removed = await asyncio.to_thread(
                        app.state.model_profile_store.cleanup_unreferenced_secrets,
                        app.state.secret_store,
                    )
                    if removed:
                        diagnostic_event(
                            level="info",
                            component="model.credentials",
                            event="model_credentials.cleaned",
                            outcome="succeeded",
                            details={"count": removed},
                        )
                except SecretStoreUnavailableError as error:
                    diagnostic_event(
                        level="warn",
                        component="model.credentials",
                        event="model_credentials.cleanup_failed",
                        outcome="degraded",
                        error_code=_secret_store_detail(error),
                    )

            app.state.model_secret_cleanup = watched_task(
                asyncio.get_running_loop(),
                lambda: cleanup_stale_model_secrets(),
                name="one-memory-model-secret-cleanup",
            )
            app.state.matter_store = MatterStore(
                database_path,
                lifecycle_source=_matter_lifecycle_source(
                    app,
                    runtime_settings.data_dir,
                ),
            )
            app.state.matter_store.recover_runs()
            app.state.matter_discovery = MatterDiscovery(
                app.state.matter_store,
                MatterModel(app.state.model_profile_store, app.state.secret_store),
                governance=app.state.memory_governance_store,
            )
            app.state.memory_task_store = MemoryTaskStore(database_path)
            # Derived, unversioned: created if absent, and the version ledger
            # is left alone so an older package can still open this library.
            try:
                await asyncio.to_thread(memory_visibility.startup, database_path)
            except sqlite3.Error as error:
                # Not fatal: without the tables every read checks exactly, as
                # it does today.
                app.state.memory_visibility_error = str(error)
            app.state.model_request_budget = (
                ModelRequestBudget(
                    database_path,
                    request_limit=runtime_settings.model_request_limit,
                    body_byte_limit=runtime_settings.model_request_body_byte_limit,
                    output_token_limit=runtime_settings.model_request_output_token_limit,
                )
                if runtime_settings.model_request_limit is not None
                else None
            )
            app.state.memory_answerer = MemoryAnswerer(
                governance=app.state.memory_governance_store,
                filter_memories=app.state.source_read_access.memories,
                profiles=app.state.model_profile_store,
                secrets=app.state.secret_store,
                authorize_generation=lambda profile, question, grounding: (
                    _authorize_answer_generation(app, profile, question, grounding)
                ),
                semantic_search=lambda query: _memory_processing_supervisor(app).semantic_recall(
                    query
                ),
                resolve_workspace=lambda workspace_path: resolve_workspace_scopes(
                    workspace_path,
                    evidence_store.known_workspaces(user_id="local"),
                ),
            )
            # Before anything asks for an active profile: a first launch that
            # carries a preset should show it already filled in, so the user
            # meets one empty field instead of a form they cannot answer.
            # A build without the preset seeds nothing and says nothing.
            try:
                seed_preset_profile(app.state.model_profile_store, app.state.secret_store)
            except (SecretStoreUnavailableError, sqlite3.Error) as error:
                # Never fatal. Not having a prefilled profile is a smaller
                # problem than not starting, and the user can still add one.
                logging.getLogger(__name__).warning("model_preset_seed_failed: %s", error)
            app.state.model_prober = ModelProber(
                store=app.state.model_profile_store,
                secrets=app.state.secret_store,
            )
            app.state.memory_processing_supervisor = MemoryProcessingSupervisor(
                tasks=app.state.memory_task_store,
                profiles=app.state.model_profile_store,
                secrets=app.state.secret_store,
                evidence=app.state.evidence_store,
                governance=app.state.memory_governance_store,
                data_dir=runtime_settings.data_dir,
                extraction_settings=app.state.extraction_settings_store,
                # `None` means "whatever the active profile allows". Pinned to
                # 1 in `a59f4b5` as temporary load-shedding for an acceptance
                # package, and left pinned after it: a hardcoded 1 is rule 9's
                # exact anti-pattern (a small number baked into code that
                # cannot be tuned per machine). Measured before removing — at
                # profile concurrency 4, recall latency is unchanged while
                # precipitation goes 4x.
                max_concurrent_tasks=runtime_settings.precipitation_max_concurrent_tasks,
                model_content_config=app.state.config_store,
                permission_bridge_factory=lambda lease, secret, directory: _permission_context(
                    app, lease, secret, directory
                ),
                source_writes_permitted=lambda source_id: _source_writes_on(app, source_id),
                service_levels=app.state.service_levels,
            )
            # C2 owns the single production budget (handle 5s + reserve 250ms,
            # total 5.25s, lease 10s, renew 1s, claim 250ms). The composition
            # root only consumes it: no local formula, and the 250ms claim
            # budget is independent of the ordinary 5000ms writer wait and the
            # response-tail 250ms.
            drain_budget = production_drain_budget()

            async def reconcile_unknown_writes() -> None:
                await app.state.memory_processing_supervisor.reconcile_unknown_outputs()
                unknown = await asyncio.to_thread(memory_write_queue.unknown_jobs, limit=128)
                for job in unknown:
                    await asyncio.to_thread(
                        memory_write_queue.resolve_unknown_from_linked_output,
                        job.job_id,
                    )
                await asyncio.to_thread(
                    memory_write_queue.retry_timed_out_unknown_after_reconcile,
                    older_than=datetime.now(UTC) - timedelta(minutes=5),
                    limit=32,
                )

            async def admit_hy_invocation(
                source_id: str,
                mark_invoked: Callable[[], Awaitable[bool]],
            ) -> bool:
                """Serialize pause with the last boundary before an HY call."""
                lock = app.state.source_permission_locks.setdefault(source_id, asyncio.Lock())
                async with lock:
                    if not _source_writes_on(app, source_id):
                        raise RuntimeError("model_content_source_disabled")
                    return await mark_invoked()

            memory_write_drain = DurableWriteDrain(
                memory_write_queue,
                execute=lambda job_id: execute_job(
                    job_id,
                    queue=memory_write_queue,
                    supervisor=app.state.memory_processing_supervisor,
                    evidence=app.state.evidence_store,
                    source_writes_permitted=lambda source_id: _source_writes_on(app, source_id),
                    admit_hy_invocation=admit_hy_invocation,
                ),
                execute_owned=lambda job_id, owner: execute_job(
                    job_id,
                    queue=memory_write_queue,
                    supervisor=app.state.memory_processing_supervisor,
                    evidence=app.state.evidence_store,
                    owner=owner,
                    source_writes_permitted=lambda source_id: _source_writes_on(app, source_id),
                    admit_hy_invocation=admit_hy_invocation,
                ),
                drain_handle_budget=drain_budget.handle_budget,
                drain_cleanup_reserve=drain_budget.cleanup_reserve,
                lease=drain_budget.lease_ttl,
                renew_every=drain_budget.renew_every,
                claim_budget_ms=drain_budget.claim_budget_ms,
                work_ceiling=lambda: timedelta(
                    seconds=memory_task_timeout_seconds(
                        active.timeout_seconds
                        if (active := app.state.model_profile_store.active(app.state.secret_store))
                        else 240
                    )
                    + 10
                ),
                reconcile_unknown=reconcile_unknown_writes,
                source_writes_permitted=lambda source_id: _source_writes_on(app, source_id),
                source_claim_enabled=lambda source_id: _config_store(app).sync_enabled(source_id),
            )
            app.state.memory_write_drain = memory_write_drain
            app.state.memory_write_drain_task = watched_task(
                asyncio.get_running_loop(),
                lambda: memory_write_drain.run_forever(),
                name="one-memory-durable-write-drain",
            )
            # D52: every entry that already holds committed Evidence goes
            # through the one durable queue. The intake turns one record into
            # one complete-turn request; the drain stays the only consumer, so
            # turn closure/settle are unchanged. `entry:<evidence_id>` makes a
            # retry return the original row instead of creating a second job.
            from one_memory.memory_server.unified_entry import UnifiedWriteIntake

            app.state.memory_write_intake = UnifiedWriteIntake(
                memory_write_queue,
                source_writes_permitted=lambda source_id: _source_writes_on(app, source_id),
            )
            app.state.start_memory_backfill = lambda: _start_backfill(app)

            async def enqueue_stable_evidence(record: EvidenceRecord) -> None:
                priority = "live" if live_sync_arrival.get() else "normal"
                conversation = await asyncio.to_thread(
                    app.state.evidence_store.list_conversation, record.evidence_id
                )
                for chunk in _conversation_chunks(conversation, max_turns=12, completed_only=True):
                    if (
                        record not in chunk
                        or not any(
                            item.role == "user" and item.written_by.value == "person"
                            for item in chunk
                        )
                        or not any(item.role == "assistant" for item in chunk)
                    ):
                        continue
                    await asyncio.to_thread(
                        app.state.memory_write_intake.submit_records,
                        chunk,
                        priority=priority,
                    )

            app.state.matter_discovery.processor = app.state.memory_processing_supervisor
            app.state.matter_discovery.write_intake = app.state.memory_write_intake
            app.state.matter_discovery.write_queue = memory_write_queue
            app.state.processing_coordinator = None
            app.state.memory_note_store = MemoryNoteStore(
                app.state.evidence_store,
                app.state.memory_processing_supervisor,
                app.state.memory_write_intake,
            )
            app.state.file_import_store = FileImportStore(
                runtime_settings.data_dir / "import-files"
            )
            app.state.memory_task_store.recover_running()
            app.state.import_job_store.recover_stale_running(heartbeat_before=datetime.now(UTC))
            app.state.import_supervisor = ImportSupervisor(
                adapters=_active_adapters(
                    _catalog(app),
                    app.state.source_grant_store,
                ),
                evidence_store=app.state.evidence_store,
                job_store=app.state.import_job_store,
                source_enabled=lambda source_id: _source_processing_enabled(app, source_id),
                max_concurrent_jobs=runtime_settings.import_max_concurrent_jobs,
                on_stable_evidence=(
                    enqueue_stable_evidence
                    if runtime_settings.automatic_memory_processing
                    else None
                ),
                service_levels=app.state.service_levels,
            )
            if runtime_settings.imports_enabled:
                app.state.matter_discovery.importer = app.state.import_supervisor
            app.state.import_job_store.replace_never_published_snapshot_failures()
            # A file's Evidence and its pending-dispatch row commit atomically,
            # but the callback that turns them into a task runs after the
            # transaction. A crash in that window leaves durable Evidence with
            # no in-memory timer left to notice it; drain those rows once on
            # startup through the same permission/debounce path as live work.
            if runtime_settings.automatic_memory_processing:
                # Two-phase, like the live runner: enumerate the file
                # identities, read each without consuming its pending marker,
                # await the durable submit, and only then consume. A crash or
                # a failed/cancelled submit leaves the marker for the next
                # startup instead of dropping the committed Evidence. The
                # legacy `take_all_stable_records()` consumes while reading and
                # is deliberately no longer the startup path.
                for user_id, source_id, file_key in await asyncio.to_thread(
                    app.state.evidence_store.list_pending_dispatch_files
                ):
                    try:
                        for dispatch in await asyncio.to_thread(
                            app.state.evidence_store.take_stable_file_dispatches,
                            user_id=user_id,
                            source_id=source_id,
                            file_key=file_key,
                        ):
                            # Same intake as the live callback, so the crash
                            # window between Evidence commit and durable submit
                            # is closed by the same idempotent path rather than
                            # a second one.
                            token = live_sync_arrival.set(dispatch.priority == "live")
                            try:
                                await enqueue_stable_evidence(dispatch.record)
                            finally:
                                live_sync_arrival.reset(token)
                    except Exception:
                        # The submit did not succeed, so the pending marker is
                        # deliberately left in place: the next startup will
                        # replay this file. Startup continues for the other
                        # files instead of failing the whole composition root.
                        logger.warning(
                            "stable_evidence_startup_replay_deferred source=%s file=%s",
                            source_id,
                            file_key,
                            exc_info=True,
                        )
                        continue
                    await asyncio.to_thread(
                        app.state.evidence_store.consume_stable_file_records,
                        user_id=user_id,
                        source_id=source_id,
                        file_key=file_key,
                    )
            app.state.import_supervisor.resume_interrupted()
            app.state.source_connection_store = SourceConnectionStore(
                app.state.source_grant_store,
                app.state.import_job_store,
                home=runtime_settings.source_home,
                candidates=app.state.source_candidate_store,
            )
            live_sync_sources = tuple(
                source_id
                for source_id, registration in app.state.source_catalog.items()
                if registration.capabilities.live_sync is CapabilityState.IMPLEMENTED
            )
            # Resolved before the supervisor is built so it can state truthfully
            # how it is being driven: watching only applies to roots that exist
            # and are granted, which is not every source that supports it.
            watch_roots = _granted_roots(
                live_sync_sources,
                app.state.source_grant_store,
                app.state.config_store,
            )
            watching = runtime_settings.live_sync_watch_enabled and bool(watch_roots)
            app.state.live_sync_supervisor = PollingLiveSyncSupervisor(
                source_ids=live_sync_sources,
                import_supervisor=app.state.import_supervisor,
                job_store=app.state.import_job_store,
                interval_seconds=runtime_settings.live_sync_interval_seconds,
                mode=WATCH_MODE if watching else "polling",
                sync_authorized=lambda source_id: _source_processing_enabled(app, source_id),
            )
            app.state.live_sync_watch = (
                FileWatchLiveSync(
                    roots=watch_roots,
                    supervisor=app.state.live_sync_supervisor,
                    debounce_seconds=runtime_settings.live_sync_watch_debounce_seconds,
                    min_interval_seconds=runtime_settings.live_sync_watch_min_interval_seconds,
                )
                if watching
                else None
            )
            app.state.live_sync_watch_lock = asyncio.Lock()
            app.state.live_sync_supervisor.set_watched_sources(
                watch_roots if watching and runtime_settings.live_sync_enabled else ()
            )
            if runtime_settings.live_sync_enabled:
                app.state.live_sync_supervisor.start()
                if app.state.live_sync_watch is not None:
                    app.state.live_sync_watch.start(prime=True)
            await _reconcile_source_sync_from_config()
            # Taking up the memory work again — the tasks left unfinished, then
            # the conversations with no memory yet — reads every conversation
            # ever imported: 13.5 seconds on this machine's real database, and
            # longer with every import. Run inline it was the last thing
            # standing between the desktop app and a Core it had given up
            # waiting for. Nothing this process serves depends on it having
            # finished, recall included, so readiness no longer waits for it.
            # Warmed before the catch-up starts handing work to it: a pool
            # starts its threads wherever the first work arrives from, and
            # from a busy loop that wait was 2.5 seconds.
            warm_bulk_pool()
            _start_backfill(app)
            _start_visibility_recompute(app)
            app.state.deletion_sync = watched_task(
                asyncio.get_running_loop(),
                lambda: _memory_processing_supervisor(app).keep_deletions_in_sync(),
                name="one-memory-deletion-sync",
            )
            # Both times the loop was blocked here it was found from outside,
            # by hand, days later. This says so at the time, and says what was
            # holding it.
            app.state.loop_watchdog = LoopWatchdog(
                stall_seconds=runtime_settings.loop_stall_report_seconds
            )
            app.state.loop_watchdog.start()
            app.state.loop_heartbeat = watched_task(
                asyncio.get_running_loop(),
                lambda: _keep_loop_heartbeat(app.state.loop_watchdog),
            )
            app.state.main_database_report = watched_task(
                asyncio.get_running_loop(),
                lambda: report_main_database(MAIN_DATABASE_REPORT_SECONDS),
            )
            app.state.resource_diagnostics = watched_task(
                asyncio.get_running_loop(), _emit_resource_snapshots
            )
            app.state.retrieval_runtime = None
            try:
                if runtime_settings.retrieval_index_enabled:
                    from one_memory.retrieval.index_provisioning import provision_retrieval_index
                    from one_memory.retrieval.runtime import ExistingRetrievalRuntime

                    # The runtime only resumes an index someone initialized. Turning
                    # the feature on is that someone; otherwise enabling it would
                    # leave every search on the lexical lane with nothing to say why.
                    await asyncio.to_thread(provision_retrieval_index, runtime_settings.data_dir)
                    app.state.retrieval_runtime = ExistingRetrievalRuntime(
                        runtime_settings.data_dir,
                        minimum_free_bytes=runtime_settings.retrieval_minimum_free_bytes,
                        service_levels=app.state.service_levels,
                    )
                    await app.state.retrieval_runtime.start()
                # Startup writes are done. Only now let the derived-content
                # rebuild take the single write checkout: it is a long
                # background job, and on a large library it would otherwise
                # hold the writer while the store initializations above still
                # need it, so startup would wait out the write budget and fail
                # with `database is locked`
                # (ISSUE-CONTENT-BACKFILL-BLOCKS-STARTUP).
                app.state.memory_governance_store.release_content_backfill_startup_gate()
                diagnostic_event(
                    level="info",
                    component="core.lifecycle",
                    event="core.ready",
                    outcome="succeeded",
                    instance_id=runtime_settings.instance_id,
                    duration_ms=elapsed_ms(lifecycle_started),
                )
                yield
            finally:
                shutdown_started = time.monotonic()
                diagnostic_event(
                    level="info",
                    component="core.lifecycle",
                    event="core.stopping",
                    outcome="started",
                    instance_id=runtime_settings.instance_id,
                )
                retrieval_stop_error: BaseException | None = None
                if app.state.retrieval_runtime is not None:
                    try:
                        await app.state.retrieval_runtime.stop()
                    except (Exception, asyncio.CancelledError) as error:
                        # Preserve failure while cleaning other owned services.
                        # Experimental: an exceptional live-thread failure still
                        # needs an outer ownership fence before production enablement.
                        retrieval_stop_error = error
                await app.state.matter_discovery.shutdown()
                app.state.main_database_report.cancel()
                app.state.loop_heartbeat.cancel()
                with suppress(asyncio.CancelledError):
                    await app.state.loop_heartbeat
                app.state.loop_watchdog.stop()
                app.state.resource_diagnostics.cancel()
                with suppress(asyncio.CancelledError):
                    await app.state.resource_diagnostics
                with suppress(asyncio.CancelledError):
                    await app.state.model_secret_cleanup
                app.state.memory_backfill.cancel()
                app.state.deletion_sync.cancel()
                # Awaited, not just cancelled: they hold database connections,
                # and shutdown releases the data directory's lock.
                with suppress(asyncio.CancelledError):
                    await app.state.memory_backfill
                with suppress(asyncio.CancelledError):
                    await app.state.deletion_sync
                await asyncio.to_thread(
                    app.state.memory_governance_store.wait_content_candidate_backfill,
                    5.0,
                )
                if app.state.live_sync_watch is not None:
                    await app.state.live_sync_watch.shutdown()
                await app.state.live_sync_supervisor.shutdown()
                await app.state.import_supervisor.shutdown()
                app.state.memory_write_drain.stop()
                with suppress(asyncio.CancelledError):
                    await app.state.memory_write_drain_task
                await app.state.memory_processing_supervisor.shutdown()
                # HY clients share two process-owned loop threads. Close them
                # after every bridge is down, while HY's SQLite executor is
                # still available; interpreter atexit is too late for that.
                from one_memory.hy_memory_bridge import close_shared_hy_runtimes

                await asyncio.to_thread(close_shared_hy_runtimes)
                # Last: what has just been shut down was using it.
                shutdown_bulk_pool()
                log_main_database_stats("shutdown")
                close_main_databases()
                diagnostic_event(
                    level="info",
                    component="core.lifecycle",
                    event="core.stopped",
                    outcome="succeeded",
                    instance_id=runtime_settings.instance_id,
                    duration_ms=elapsed_ms(shutdown_started),
                )
                # Diagnostic writes happen on their own thread now, so the
                # record of this shutdown is still in a queue at this point.
                # Waited for off the loop: this is the task whose whole point
                # is that the loop never waits on a disk.
                await asyncio.to_thread(flush_diagnostics)
                if retrieval_stop_error is not None:
                    raise retrieval_stop_error

    app = FastAPI(
        title="MemHub Core",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.service_levels = ServiceLevelScheduler(
        {
            ServiceResource.IMPORT: ResourceLimit(
                capacity=runtime_settings.import_max_concurrent_jobs + 1,
                background_capacity=runtime_settings.import_max_concurrent_jobs,
            ),
            ServiceResource.MODEL: ResourceLimit(capacity=32, background_capacity=31),
            ServiceResource.INDEX: ResourceLimit(capacity=1, background_capacity=1),
            ServiceResource.INTERACTIVE_READ: ResourceLimit(
                capacity=32,
                background_capacity=0,
            ),
        }
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["one-memory://app", "http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.settings = runtime_settings

    def ui_session_valid(cookie: str) -> bool:
        sessions = getattr(app.state, "ui_sessions", None)
        return sessions is not None and sessions.valid_session(cookie)

    protected = [
        Depends(local_api_guard(runtime_settings, valid_ui_session=ui_session_valid)),
        Depends(require_external_endpoint),
    ]
    app.include_router(matter_router(protected))
    app.include_router(memory_note_router(protected))
    app.include_router(file_import_router(protected))
    app.include_router(model_content_router(protected))
    app.include_router(context_router(protected))

    def require_workbuddy_boundary(request: Request) -> None:
        access = external_access(request.headers)
        if access is None or access.host_id != "workbuddy":
            raise HTTPException(403, "external_installation_permission_required")
        access.require("om_recall", frozenset({"all-apps"}))

    @app.post("/api/v1/boundary/peek", dependencies=protected)
    async def boundary_peek(value: dict[str, Any], request: Request) -> dict[str, object]:
        require_workbuddy_boundary(request)
        from one_memory.workbuddy_boundary import peek_output

        return await asyncio.to_thread(peek_output, value, _evidence_store(app).database_path)

    @app.post("/api/v1/boundary/ack", dependencies=protected)
    async def boundary_ack(value: BoundaryAckRequest, request: Request) -> dict[str, bool]:
        require_workbuddy_boundary(request)
        from one_memory.session_inbox import CursorRejected
        from one_memory.workbuddy_boundary import confirm

        try:
            acknowledged = await asyncio.to_thread(
                confirm,
                value.token,
                _evidence_store(app).database_path,
            )
        except (ValueError, CursorRejected):
            raise HTTPException(422, "invalid_boundary_token") from None
        return {"acknowledged": bool(acknowledged)}

    # A4-01: scan-root configuration, so the CLI's `path` command has something
    # to read instead of refusing.
    from one_memory.scan_root_routes import scan_root_router

    app.include_router(scan_root_router(protected))
    # A3-01: the unified write entry. Mounted alongside the others so every
    # write path is reachable through one door rather than per-caller routes.
    from one_memory.memory_server.registry import WriteRegistry
    from one_memory.memory_server.routes import router as memory_write_router

    if getattr(app.state, "memory_write_registry", None) is None:
        app.state.memory_write_registry = WriteRegistry()
    app.include_router(memory_write_router(protected))
    from one_memory.call_event_routes import call_event_router

    app.include_router(call_event_router(protected))

    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(renderer_origins(runtime_settings)),
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Accept", "Authorization", "Content-Type", "X-One-Memory-Trace-Id"],
    )

    @app.middleware("http")
    async def diagnostic_trace(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        trace_id, token = bind_trace_id(request.headers.get("X-One-Memory-Trace-Id"))
        started = time.monotonic()
        interactive = request.method == "GET" or request.url.path in {
            "/api/v1/recall",
            "/api/v1/context/search",
            "/api/v1/ask",
        }
        event = _diagnostic_request_event(request.method, request.url.path)
        diagnostic_details = _diagnostic_request_details(request, event)
        if event is not None:
            diagnostic_event(
                level="info",
                component="core.api",
                event=f"{event}.started",
                outcome="started",
                trace_id=trace_id,
                instance_id=runtime_settings.instance_id,
                details=diagnostic_details,
            )
        try:
            if interactive:
                async with app.state.service_levels.admit(
                    ServiceResource.INTERACTIVE_READ,
                    ServiceLevel.INTERACTIVE,
                    work_id=trace_id,
                ):
                    response = await call_next(request)
            else:
                response = await call_next(request)
            response.headers["X-One-Memory-Trace-Id"] = trace_id
            if event is not None:
                diagnostic_event(
                    level="info" if response.status_code < 400 else "warn",
                    component="core.api",
                    event=f"{event}.completed",
                    outcome="succeeded" if response.status_code < 400 else "rejected",
                    trace_id=trace_id,
                    instance_id=runtime_settings.instance_id,
                    duration_ms=elapsed_ms(started),
                    details={**diagnostic_details, "status": str(response.status_code)},
                )
            return response
        except Exception as error:
            if event is not None:
                diagnostic_event(
                    level="error",
                    component="core.api",
                    event=f"{event}.failed",
                    outcome="failed",
                    trace_id=trace_id,
                    instance_id=runtime_settings.instance_id,
                    duration_ms=elapsed_ms(started),
                    error_code="core_request_failed",
                    **diagnostic_exception(error),
                    details=diagnostic_details,
                )
            raise
        finally:
            reset_trace_id(token)

    @app.exception_handler(RequestValidationError)
    async def validation_error(
        _: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "detail": "request_validation_failed",
                "errors": [
                    {
                        "location": [
                            str(part) for part in item.get("loc", ()) if part not in {"body"}
                        ],
                        "code": item.get("type", "invalid"),
                    }
                    for item in error.errors()
                ],
            },
        )

    @app.exception_handler(sqlite3.OperationalError)
    async def sqlite_error(request: Request, error: sqlite3.OperationalError) -> JSONResponse:
        record_database_error("core.api", error, route_path=request.url.path)
        return JSONResponse(
            status_code=503,
            content={"detail": "local_store_unavailable"},
        )

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, error: Exception) -> JSONResponse:
        report_exception(
            "core.api",
            "core.request.unhandled",
            error,
            context={"route_path": request.url.path},
        )
        # Preserve the framework's own 500 shape; only the report is added.
        return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})

    # --- Reader-only internal endpoints. Registered here, before the public
    # routes below; no early return, so every route after this still registers.

    @app.get("/internal/semantic-ids")
    async def internal_semantic_ids(
        query: str = Query(min_length=1, max_length=2_000),
        limit: int = Query(default=12, ge=1, le=20),
    ) -> dict[str, object]:
        """Semantic search IDs for the Reader process. Reader-only."""
        try:
            hits = await _memory_processing_supervisor(app).semantic_search(query, limit=limit)
        except Exception:
            return {"ids": []}
        return {"ids": [memory_id for memory_id, _score in hits]}

    @app.post("/internal/call-event")
    async def internal_call_event(request: Request) -> dict[str, bool]:
        """Side effects the Reader cannot write: it opens with query_only."""
        try:
            value = await request.json()
        except Exception:
            return {"recorded": False}
        store = getattr(app.state, "call_event_store", None)
        if store is None:
            return {"recorded": False}
        try:
            from one_memory.call_event_store import CallEvent

            event = CallEvent.model_validate(value)
            recorded = await asyncio.to_thread(store.record, event)
        except Exception:
            return {"recorded": False}
        return {"recorded": bool(recorded)}

    @app.get("/internal/graph-snapshot")
    async def internal_graph_snapshot(
        limit: int = Query(default=160, ge=1, le=300),
    ) -> dict[str, object]:
        """HY graph overlay for the Reader process. Reader-only."""
        try:
            snapshot = await _memory_processing_supervisor(app).graph_snapshot(limit=limit)
        except Exception:
            return {"consulted": False, "nodes": [], "edges": []}
        return snapshot.model_dump(mode="json")

    @app.get("/api/v1/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        backfill = cast("asyncio.Task[None] | None", getattr(app.state, "memory_backfill", None))
        error = cast(str | None, getattr(app.state, "memory_backfill_error", None))
        status = _backfill_state(backfill, error)
        governance = getattr(app.state, "memory_governance_store", None)
        supervisor = getattr(app.state, "memory_processing_supervisor", None)
        if supervisor is not None:
            accepted = supervisor.accepted_backfill_status()
            if accepted is not None:
                status, error = accepted
        return HealthResponse(
            status="ok",
            service="one-memory-core",
            version=__version__,
            instance_id=runtime_settings.instance_id,
            memory_backfill=status,
            memory_backfill_error=error,
            content_candidate_backfill=(
                governance.content_candidate_backfill_state() if governance is not None else "idle"
            ),
            content_candidate_backfill_failure=(
                governance.content_candidate_backfill_failure() if governance is not None else None
            ),
        )

    @app.get("/health", response_model=HealthResponse, include_in_schema=False)
    def health_probe() -> HealthResponse:
        """OBS-03: a bare `/health` used to reach the web app's catch-all.

        The catch-all answers every unknown path with the single-page app's
        index.html, so probes that hit `/health` got a 200 HTML page and read
        it as a healthy Core. Answer the real payload instead.
        """
        return health()

    @app.get("/api/v1/diagnostics/settings", dependencies=protected)
    def diagnostic_settings() -> dict[str, bool | int | str]:
        level = diagnostic_log_level(runtime_settings.data_dir)
        return {
            "schema_version": 1,
            "detailed_logging": level == "debug",
            "log_level": level,
        }

    @app.put("/api/v1/diagnostics/settings", dependencies=protected)
    def update_diagnostic_settings(
        request: DetailedLoggingRequest,
    ) -> dict[str, bool | int | str]:
        enabled = set_detailed_logging(runtime_settings.data_dir, request.enabled)
        return {
            "schema_version": 1,
            "detailed_logging": enabled,
            "log_level": diagnostic_log_level(runtime_settings.data_dir),
        }

    @app.put("/api/v1/diagnostics/log-level", dependencies=protected)
    def update_diagnostic_log_level(
        request: DiagnosticLogLevelRequest,
    ) -> dict[str, bool | int | str]:
        level: DiagnosticLogLevel = request.level
        applied = set_diagnostic_log_level(runtime_settings.data_dir, level)
        return {
            "schema_version": 1,
            "detailed_logging": applied == "debug",
            "log_level": applied,
        }

    @app.post("/api/v1/diagnostics/trace", dependencies=protected)
    async def diagnostic_trace_lookup(request: DiagnosticTraceRequest) -> dict[str, object]:
        if not detailed_logging_enabled(runtime_settings.data_dir):
            return {"schema_version": 1, "state": "disabled", "events": []}
        await asyncio.to_thread(flush_diagnostics)
        events = await asyncio.to_thread(
            read_trace_events,
            runtime_settings.data_dir,
            request_id=request.request_id,
            trace_id=request.trace_id,
        )
        return {
            "schema_version": 1,
            "state": "ok" if events else "not_found",
            "events": events,
        }

    @app.get(
        "/api/v1/runtime/identity",
        response_model=HealthResponse,
        dependencies=protected,
    )
    def runtime_identity() -> HealthResponse:
        """Authenticated proof that a discovery receipt names this Core."""
        return health()

    @app.post(
        "/api/v1/backup",
        response_model=BackupResult,
        dependencies=protected,
    )
    async def create_backup(request: BackupRequest) -> BackupResult:
        """Copy the committed database to `target`, for the CLI's `backup create`.

        Run in a thread: the copy is a blocking file operation, and doing it on
        the event loop is exactly what the loop watchdog reports (real logs
        showed `event loop blocked for 12.9s`). It also holds the writer for
        the duration, so a long copy would otherwise stall every other caller.

        A target outside the data directory is refused: the CLI can name any
        path, and an unrestricted server-side write would turn a read-only
        client into a file-write primitive.
        """
        settings = cast(Settings, app.state.settings)
        data_dir = settings.data_dir.resolve()
        target = Path(request.target)
        resolved = target.resolve() if target.is_absolute() else (data_dir / target).resolve()
        if not (resolved == data_dir or data_dir in resolved.parents):
            raise HTTPException(status_code=422, detail="backup_target_outside_data_dir")
        resolved.parent.mkdir(parents=True, exist_ok=True)
        # `back_up` is on the database access point, not the evidence store.
        await asyncio.to_thread(main_database(_evidence_store(app).database_path).back_up, resolved)
        return BackupResult(target=str(resolved), bytes=resolved.stat().st_size)

    @app.post(
        "/api/v1/backup/restore",
        response_model=BackupRestoreResponse,
        dependencies=protected,
    )
    async def restore_backup(request: BackupRestoreRequest) -> BackupRestoreResponse:
        """Restore a managed backup; only a non-runtime maintenance Core may do so."""
        if manage_runtime:
            raise HTTPException(status_code=409, detail="backup_restore_runtime_running")
        from one_memory.managed_restore import RestoreRejected, restore_managed_backup

        settings = cast(Settings, app.state.settings)
        try:
            result = await asyncio.to_thread(
                restore_managed_backup,
                data_dir=settings.data_dir,
                source=request.source,
            )
        except RestoreRejected as error:
            raise HTTPException(status_code=422, detail=error.code) from error
        return BackupRestoreResponse(
            state="restored",
            restored_bytes=result.restored_bytes,
            previous_backup_path=str(result.previous_backup_path),
            source_schema_version=result.source_schema_version,
        )

    def web_assets_root() -> Path | None:
        configured = os.environ.get("ONE_MEMORY_WEB_DIR")
        candidates = []
        if configured:
            candidates.append(Path(configured))
        # Packaged layout: <root>/core/one-memory next to <root>/web.
        executable = Path(sys.argv[0]).resolve()
        candidates.append(executable.parent.parent / "web")
        for candidate in candidates:
            if (candidate / "index.html").is_file():
                return candidate
        return None

    @app.post("/api/v1/ui/session", dependencies=protected)
    async def create_ui_session(request: Request) -> dict[str, object]:
        if web_assets_root() is None:
            raise HTTPException(status_code=503, detail="ui_web_assets_unavailable")
        settings = cast(Settings, app.state.settings)
        code, expires_at = app.state.ui_sessions.issue_exchange()
        return {
            "schema_version": 1,
            "exchange_code": code,
            "expires_in_seconds": max(
                0,
                int((expires_at - datetime.now(UTC)).total_seconds()),
            ),
            "url": f"http://{settings.host}:{settings.port}/ui/login?code={code}",
        }

    @app.get("/ui/login")
    def ui_login(request: Request, code: str) -> Response:
        if web_assets_root() is None:
            raise HTTPException(status_code=503, detail="ui_web_assets_unavailable")
        session_id = app.state.ui_sessions.consume_exchange(code)
        if session_id is None:
            raise HTTPException(status_code=401, detail="ui_session_code_invalid")
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie(
            "one_memory_session",
            session_id,
            httponly=True,
            samesite="strict",
            secure=False,
            path="/",
        )
        return response

    @app.post("/api/v1/ui/session/logout", dependencies=protected)
    def ui_logout(request: Request) -> Response:
        session_id = request.cookies.get("one_memory_session")
        if session_id is not None:
            app.state.ui_sessions.revoke_session(session_id)
        response = JSONResponse({"schema_version": 1, "state": "logged_out"})
        response.delete_cookie("one_memory_session", path="/")
        return response

    @app.get(
        "/api/v1/onboarding",
        response_model=OnboardingState,
        dependencies=protected,
    )
    def get_onboarding() -> OnboardingState:
        return _onboarding_store(app).get()

    @app.post(
        "/api/v1/onboarding/complete",
        response_model=OnboardingState,
        dependencies=protected,
    )
    async def complete_onboarding() -> OnboardingState:
        """Record UI completion only; finishing or skipping is never model consent."""
        return await asyncio.to_thread(_onboarding_store(app).complete)

    @app.post(
        "/api/v1/onboarding/reset",
        response_model=OnboardingState,
        dependencies=protected,
    )
    async def reset_onboarding() -> OnboardingState:
        """Go back to the first-run screens. Only the completion flag moves.

        Memories, sources and settings live in other tables and are untouched,
        which is the point: someone who wants to see the cold-start flow again
        should not have to delete anything to do it.
        """
        return await asyncio.to_thread(_onboarding_store(app).reset)

    @app.get(
        "/api/v1/sources",
        response_model=list[SourceResponse],
        dependencies=protected,
    )
    async def list_sources() -> list[SourceResponse]:
        detection = _detection(app)
        # Read in threads. The desktop polls this every few seconds, and both
        # reads below are synchronous SQLite — the totals across every source,
        # then a grant receipt per source. Made on the loop, as they were,
        # they blocked the whole process for as long as they took: the
        # watchdog caught this endpoint holding the loop for 5.5s.
        #
        # The totals are also not counted again for each ask: counting is a
        # pass over every row, and answering this took up to 6.70s during a
        # catch-up, nearly all of it the same count repeated.
        totals_by_source = await app.state.source_totals.by_source(user_id="local")
        # Status polling must not trigger history discovery: detect can walk an
        # entire host tree and recheck the grant in SQLite for every file, with
        # concurrent polls duplicating that work. Only the explicit detect
        # endpoint populates this diagnostic; None means not yet checked.
        # Authorization and root identity are still read afresh below.

        def describe() -> list[SourceResponse]:
            grant_store = _grant_store(app)
            config_store = _config_store(app)
            return [
                _source_response(
                    registration,
                    grant_store.receipt(user_id="local", source_id=source_id),
                    config_store.sync_enabled(source_id),
                    detection.get(source_id),
                    runtime_settings.source_home,
                    totals_by_source.get(source_id, (0, 0)),
                    candidate_change_reason=app.state.source_candidate_store.change_reason(
                        registration
                    ),
                )
                for source_id, registration in _catalog(app).items()
            ]

        return await asyncio.to_thread(describe)

    @app.get(
        "/api/v1/adapters",
        dependencies=protected,
    )
    async def list_adapters() -> dict[str, object]:
        """Adapter readiness per harness: what each one can do, and whether it is usable now.

        The CLI's `adapter list` needs this. It is derived from the catalog and
        the grant state rather than stored separately, so it cannot drift from
        what an adapter would actually be asked to do.
        """
        catalog = _catalog(app)
        grant_store = _grant_store(app)

        def describe() -> list[dict[str, object]]:
            rows: list[dict[str, object]] = []
            for source_id, registration in catalog.items():
                grant = grant_store.authorized_root(user_id="local", source_id=source_id)
                capabilities = registration.capabilities
                rows.append(
                    {
                        "source_id": source_id,
                        "display_name": registration.descriptor.display_name,
                        "root": str(registration.candidate.path),
                        "available": registration.candidate.path.exists(),
                        "authorized": grant is not None,
                        "capabilities": {
                            "historical_import": str(capabilities.historical_import),
                            "incremental_import": str(capabilities.incremental_import),
                            "live_sync": str(capabilities.live_sync),
                            "recall": str(capabilities.recall),
                            "installer": str(capabilities.installer),
                            "verification": str(capabilities.verification),
                        },
                    }
                )
            return sorted(rows, key=lambda row: str(row["source_id"]))

        rows = await asyncio.to_thread(describe)
        return {"schema_version": 1, "adapters": rows}

    @app.post(
        "/api/v1/adapters/probe",
        dependencies=protected,
    )
    async def probe_adapter(value: dict[str, object]) -> dict[str, object]:
        """Probe one harness adapter for readiness.

        Ready means: known, directory exists, and authorized. Reported as three
        separate facts, because collapsing them into one boolean is how a caller
        concludes "working" when what is true is "installed but not connected".
        """
        source_id = str(value.get("source") or "")
        if not source_id:
            raise HTTPException(status_code=422, detail="adapter_source_required")
        registration = _catalog(app).get(source_id)
        if registration is None:
            raise HTTPException(status_code=404, detail="adapter_unknown")
        path = registration.candidate.path
        exists = await asyncio.to_thread(path.exists)
        grant = await asyncio.to_thread(
            _grant_store(app).authorized_root,
            user_id="local",
            source_id=source_id,
        )
        adapter = _supervisor(app).adapters.get(source_id)
        detected: bool | None = None
        session_count: int | None = None
        if adapter is not None:
            try:
                state = _install_state(adapter)
                if state is None:
                    detected = await adapter.detect()
                else:
                    resolved = await state
                    detected = resolved.detected
                    session_count = resolved.session_count
            except Exception:
                # A probe must not raise: it is the thing you run to find out
                # whether the adapter answers at all.
                detected = None
        capabilities = registration.capabilities
        return {
            "schema_version": 1,
            "source_id": source_id,
            "path": str(path),
            "exists": exists,
            "authorized": grant is not None,
            "detected": detected,
            "session_count": session_count,
            "ready": bool(exists and grant is not None and detected),
            "capabilities": {
                "historical_import": str(capabilities.historical_import),
                "live_sync": str(capabilities.live_sync),
                "recall": str(capabilities.recall),
            },
        }

    @app.get(
        "/api/v1/sources/{source_id}/import-receipt",
        response_model=SourceImportReceipt,
        dependencies=protected,
    )
    async def source_import_receipt(
        source_id: str,
        query_evidence_id: str | None = Query(default=None, min_length=1, max_length=512),
    ) -> SourceImportReceipt:
        _require_registration(app, source_id)
        grant_store = _grant_store(app)
        before = await asyncio.to_thread(grant_store.receipt, user_id="local", source_id=source_id)
        if before is None or before.revoked_at is not None:
            raise HTTPException(status_code=403, detail="source_not_authorized")
        try:
            receipt = await asyncio.to_thread(
                build_source_import_receipt,
                app.state.evidence_store.database_path,
                user_id="local",
                source_id=source_id,
                query_evidence_id=query_evidence_id,
            )
        except sqlite3.Error:
            raise HTTPException(
                status_code=503, detail="source_import_receipt_unavailable"
            ) from None
        after = await asyncio.to_thread(grant_store.receipt, user_id="local", source_id=source_id)
        if after is None or after.revoked_at is not None or after.granted_at != before.granted_at:
            raise HTTPException(status_code=403, detail="source_not_authorized")
        return receipt

    @app.post(
        "/api/v1/sources/{source_id}/authorize",
        response_model=SourceGrantReceipt,
        dependencies=protected,
    )
    async def authorize_source(
        source_id: str,
        request: AuthorizeSourceRequest,
    ) -> SourceGrantReceipt:
        async with app.state.source_permission_locks.setdefault(source_id, asyncio.Lock()):
            return await _authorize_source(source_id, request)

    async def _authorize_source(
        source_id: str, request: AuthorizeSourceRequest
    ) -> SourceGrantReceipt:
        registration = _require_registration(app, source_id)
        if registration.candidate.kind is RootCandidateKind.USER_SELECTED:
            raise HTTPException(status_code=409, detail="source_candidate_confirmation_required")
        if not request.user_confirmed or request.expected_root_id is None:
            raise HTTPException(status_code=409, detail="source_confirmation_required")
        if (
            registration.candidate.kind is RootCandidateKind.ENVIRONMENT_OVERRIDE
            and not request.confirm_environment_override
        ):
            raise HTTPException(
                status_code=409,
                detail="environment_override_confirmation_required",
            )
        prior = await asyncio.to_thread(
            _grant_store(app).receipt,
            user_id="local",
            source_id=source_id,
        )
        if not root_identity_matches(registration.candidate.path, request.expected_root_id):
            raise HTTPException(status_code=409, detail="source_identity_changed")
        root_changed = prior is not None and prior.root_id != request.expected_root_id
        if root_changed:
            source_jobs = [job for job in _job_store(app).list_jobs() if job.source_id == source_id]
            if any(job.status is ImportStatus.RUNNING for job in source_jobs):
                raise HTTPException(status_code=409, detail="source_import_busy")
            for job in source_jobs:
                if job.status not in {
                    ImportStatus.PENDING,
                    ImportStatus.PAUSED,
                    ImportStatus.INTERRUPTED,
                    ImportStatus.FAILED,
                }:
                    continue
                await _supervisor(app).cancel(job.job_id)
        try:
            receipt = await asyncio.to_thread(
                _grant_store(app).grant,
                user_id="local",
                source_id=source_id,
                root=registration.candidate.path,
                candidate_kind=registration.candidate.kind,
                expected_root_id=request.expected_root_id,
            )
            grant = await asyncio.to_thread(
                _grant_store(app).authorized_root,
                user_id="local",
                source_id=source_id,
            )
        except SourceAccessError as error:
            raise HTTPException(status_code=409, detail=error.code) from None
        if grant is None:
            raise HTTPException(status_code=409, detail="grant_not_available")
        if root_changed:
            await _live_sync(app).stop_source(source_id)
            await asyncio.to_thread(_config_store(app).set_sync, source_id, False)
        _supervisor(app).register_adapter(registration.create_adapter(grant))
        _detection(app).pop(source_id, None)
        await _refresh_live_watch(app)
        return receipt

    @app.post(
        "/api/v1/sources/{source_id}/detect",
        response_model=DetectionResponse,
        dependencies=protected,
    )
    async def detect_source(source_id: str) -> DetectionResponse:
        _require_registration(app, source_id)
        adapter = _supervisor(app).adapters.get(source_id)
        if adapter is None:
            raise HTTPException(status_code=409, detail="source_not_authorized")
        pending_state = _install_state(adapter)
        if pending_state is None:
            detected = await adapter.detect()
            state = SourceInstallState(
                installed=detected, session_count=(1 if detected else 0), detected=detected
            )
        else:
            state = await pending_state
        _detection(app)[source_id] = state
        return DetectionResponse(
            source_id=source_id, detected=state.detected, session_count=state.session_count
        )

    @app.post(
        "/api/v1/sources/{source_id}/location",
        response_model=SourceLocation,
        dependencies=protected,
    )
    async def source_location(source_id: str) -> SourceLocation:
        registration = _require_registration(app, source_id)
        return await asyncio.to_thread(
            inspect_source_location, source_id, registration.candidate.path
        )

    @app.post(
        "/api/v1/sources/{source_id}/candidate/preview",
        response_model=SourceCandidateSelection,
        dependencies=protected,
    )
    async def preview_source_candidate(
        source_id: str, request: PreviewSourceCandidateRequest
    ) -> SourceCandidateSelection:
        registration = _require_registration(app, source_id)
        store = cast(SourceCandidateStore, app.state.source_candidate_store)
        try:
            return await asyncio.to_thread(store.preview, registration, Path(request.path))
        except SourceCandidateError as error:
            raise HTTPException(status_code=409, detail=error.code) from None

    @app.put(
        "/api/v1/sources/{source_id}/candidate",
        response_model=SourceCandidateSelection,
        dependencies=protected,
    )
    async def save_source_candidate(
        source_id: str, request: SaveSourceCandidateRequest
    ) -> SourceCandidateSelection:
        async with app.state.source_permission_locks.setdefault(source_id, asyncio.Lock()):
            registration = _require_registration(app, source_id)
            store = cast(SourceCandidateStore, app.state.source_candidate_store)
            try:
                result = await asyncio.to_thread(
                    store.select,
                    registration,
                    Path(request.path),
                    expected_revision=request.expected_revision,
                    expected_root_id=request.expected_root_id,
                    user_confirmed=request.user_confirmed,
                )
            except SourceCandidateError as error:
                raise HTTPException(status_code=409, detail=error.code) from None
            _detection(app).pop(source_id, None)
            return result

    @app.post(
        "/api/v1/sources/{source_id}/sync-permission",
        response_model=SourceSyncState,
        dependencies=protected,
    )
    async def source_sync_permission(source_id: str, request: SourceSyncRequest) -> SourceSyncState:
        registration = _require_registration(app, source_id)
        if registration.capabilities.live_sync is not CapabilityState.IMPLEMENTED:
            raise HTTPException(status_code=409, detail="live_sync_unavailable")
        async with app.state.source_permission_locks.setdefault(source_id, asyncio.Lock()):
            return await _change_source_sync(source_id, request)

    async def _change_source_sync(source_id: str, request: SourceSyncRequest) -> SourceSyncState:
        previous = _config_store(app).sync_enabled(source_id)
        if previous is request.enabled:
            return SourceSyncState(source_id=source_id, sync_enabled=previous)
        if request.enabled:
            try:
                grant = await asyncio.to_thread(
                    _grant_store(app).authorized_root,
                    user_id="local",
                    source_id=source_id,
                )
            except SourceAccessError as error:
                raise HTTPException(status_code=409, detail=error.code) from None
            if grant is None:
                raise HTTPException(status_code=409, detail="grant_required")
            await _prepare_source_resume(source_id)
        else:
            await _live_sync(app).stop_source(source_id)
        try:
            await asyncio.to_thread(_config_store(app).set_sync, source_id, request.enabled)
        except BaseException:
            if request.enabled:
                await _live_sync(app).stop_source(source_id)
            else:
                await _prepare_source_resume(source_id)
                await _activate_source_resume(source_id)
            await _refresh_live_watch(app)
            raise
        try:
            if request.enabled:
                await _activate_source_resume(source_id)
            await _refresh_live_watch(app)
        except BaseException:
            await asyncio.to_thread(_config_store(app).set_sync, source_id, previous)
            if request.enabled:
                await _live_sync(app).stop_source(source_id)
            else:
                await _prepare_source_resume(source_id)
                await _activate_source_resume(source_id)
            await _refresh_live_watch(app)
            raise
        return SourceSyncState(source_id=source_id, sync_enabled=request.enabled)

    async def _prepare_source_resume(source_id: str) -> None:
        """Prepare catch-up work before committing the new desired state."""
        await _live_sync(app).initialize_source(source_id, baseline_missing_files=True)
        active = {
            ImportStatus.PENDING,
            ImportStatus.PAUSED,
            ImportStatus.INTERRUPTED,
            ImportStatus.RUNNING,
            ImportStatus.FAILED,
        }
        if any(
            job.source_id == source_id and job.operation == "history" and job.status in active
            for job in _job_store(app).list_jobs()
        ):
            return
        await asyncio.to_thread(
            _job_store(app).create,
            user_id="local",
            source_id=source_id,
            operation="history",
        )

    async def _activate_source_resume(source_id: str) -> None:
        """Run work prepared for a source after config.json has committed."""
        try:
            for job in _job_store(app).list_jobs():
                if job.source_id != source_id:
                    continue
                if job.status is ImportStatus.PENDING:
                    _supervisor(app).enqueue(job.job_id)
                elif job.status in {
                    ImportStatus.PAUSED,
                    ImportStatus.INTERRUPTED,
                    ImportStatus.FAILED,
                }:
                    _supervisor(app).resume(job.job_id)
        except SourceAccessError as error:
            raise HTTPException(status_code=409, detail=error.code) from None
        if app.state.settings.automatic_memory_processing:
            _start_backfill(app)

    async def _reconcile_source_sync_from_config() -> None:
        for source_id, source in _config_store(app).document().sources.items():
            if not source.sync_enabled:
                continue
            if source_id not in _supervisor(app).adapters:
                continue
            await _prepare_source_resume(source_id)
            await _activate_source_resume(source_id)

    @app.post(
        "/api/v1/sources/{source_id}/revoke",
        response_model=SourceGrantReceipt,
        dependencies=protected,
    )
    async def revoke_source(source_id: str) -> SourceGrantReceipt:
        _require_registration(app, source_id)
        receipt = _grant_store(app).receipt(
            user_id="local",
            source_id=source_id,
        )
        if receipt is None:
            raise HTTPException(status_code=404, detail="grant_not_found")
        active_statuses = {
            ImportStatus.PENDING,
            ImportStatus.RUNNING,
            ImportStatus.PAUSED,
            ImportStatus.INTERRUPTED,
            ImportStatus.FAILED,
        }
        for job in _job_store(app).list_jobs():
            if job.source_id == source_id and job.status in active_statuses:
                await _supervisor(app).cancel(job.job_id)
        # Revocation is the user's node for "stop using this source" (D73):
        # park unfinished extraction instead of leaving it to fail later, so
        # re-authorizing resumes the same work.
        await asyncio.to_thread(
            app.state.memory_task_store.park_source_for_permission,
            source_id=source_id,
            error_code="model_content_permission_required",
        )
        await _live_sync(app).stop_source(source_id)
        _supervisor(app).unregister_adapter(source_id)
        _detection(app).pop(source_id, None)
        receipt = _grant_store(app).revoke(
            user_id="local",
            source_id=source_id,
        )
        await asyncio.to_thread(_config_store(app).set_sync, source_id, False)
        await _refresh_live_watch(app)
        return receipt

    @app.post(
        "/api/v1/import-jobs",
        response_model=ImportJob,
        dependencies=protected,
    )
    async def create_import_job(request: CreateImportJobRequest) -> ImportJob:
        _require_registration(app, request.source_id)
        if request.source_id not in _supervisor(app).adapters:
            raise HTTPException(status_code=409, detail="source_not_authorized")
        try:
            job = await asyncio.to_thread(
                _supervisor(app).create_job,
                request.source_id,
                operation=request.operation,
            )
        except SourceAccessError as error:
            raise HTTPException(status_code=409, detail=error.code) from None
        _supervisor(app).enqueue(job.job_id)
        return job

    @app.post(
        "/api/v1/sources/connect",
        response_model=ConnectSourcesResponse,
        dependencies=protected,
    )
    async def connect_sources(
        request: ConnectSourcesRequest,
    ) -> ConnectSourcesResponse:
        source_ids = tuple(dict.fromkeys(request.source_ids))
        registrations = [_require_registration(app, source_id) for source_id in source_ids]
        if request.request_id is None:
            raise HTTPException(status_code=409, detail="source_confirmation_required")
        history_after = await _onboarding_history_after(
            request,
            registrations,
            home=runtime_settings.source_home,
        )
        import_binding = None
        if request.import_upload_id is not None:
            try:
                upload = await asyncio.to_thread(
                    app.state.file_import_store.get, request.import_upload_id
                )
                expected = app.state.file_import_store.root / "ready" / upload.source_id
                if (
                    not upload.committed
                    or source_ids != (upload.source_id,)
                    or registrations[0].candidate.path.resolve(strict=True)
                    != expected.resolve(strict=True)
                ):
                    raise ValueError("import_upload_scope_mismatch")
                import_binding = (str(upload.upload_id), upload.checksum)
            except (KeyError, ValueError, OSError):
                raise HTTPException(409, "import_upload_scope_mismatch") from None
        try:
            connect = partial(
                app.state.source_connection_store.connect,
                request_id=request.request_id,
                registrations=tuple(registrations),
                source_roots=request.source_roots,
                confirm_overrides=request.confirm_environment_overrides,
                full_rescan=request.full_rescan,
                onboarding=request.onboarding,
                history_after=history_after,
                **({"import_binding": import_binding} if import_binding is not None else {}),
                **(
                    {"source_candidate_revisions": request.source_candidate_revisions}
                    if request.source_candidate_revisions is not None
                    else {}
                ),
            )
            result = await asyncio.to_thread(connect)
        except ConnectionIntentError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        configured: list[str] = []
        for job in result.jobs:
            registration = _require_registration(app, job.source_id)
            grant = await asyncio.to_thread(
                _grant_store(app).authorized_root,
                user_id="local",
                source_id=job.source_id,
            )
            if grant is not None:
                _supervisor(app).register_adapter(registration.create_adapter(grant))
                _detection(app).pop(job.source_id, None)
                if request.enable_sync:
                    await _prepare_source_resume(job.source_id)
                else:
                    await _live_sync(app).stop_source(job.source_id)
                configured.append(job.source_id)
        if configured:
            previous = {
                source_id: _config_store(app).sync_enabled(source_id) for source_id in configured
            }
            try:
                await asyncio.to_thread(
                    _config_store(app).set_sync_many,
                    {source_id: request.enable_sync for source_id in configured},
                )
            except BaseException:
                for source_id in configured:
                    if request.enable_sync:
                        await _live_sync(app).stop_source(source_id)
                    elif previous[source_id]:
                        await _prepare_source_resume(source_id)
                        await _activate_source_resume(source_id)
                await _refresh_live_watch(app)
                raise
            try:
                if request.enable_sync:
                    for source_id in configured:
                        await _activate_source_resume(source_id)
                await _refresh_live_watch(app)
            except BaseException:
                await asyncio.to_thread(_config_store(app).set_sync_many, previous)
                for source_id in configured:
                    if request.enable_sync:
                        await _live_sync(app).stop_source(source_id)
                    elif previous[source_id]:
                        await _prepare_source_resume(source_id)
                        await _activate_source_resume(source_id)
                await _refresh_live_watch(app)
                raise
        return ConnectSourcesResponse(**result.model_dump())

    @app.get(
        "/api/v1/source-connections/{request_id}",
        response_model=ConnectionIntentReceipt,
        dependencies=protected,
    )
    async def source_connection_receipt(request_id: str) -> ConnectionIntentReceipt:
        receipt = await asyncio.to_thread(app.state.source_connection_store.get, request_id)
        if receipt is None:
            raise HTTPException(status_code=404, detail="source_intent_not_found")
        return cast(ConnectionIntentReceipt, receipt)

    @app.get(
        "/api/v1/import-jobs",
        response_model=list[ImportJob],
        dependencies=protected,
    )
    def list_import_jobs() -> list[ImportJob]:
        return _job_store(app).list_jobs()

    @app.get(
        "/api/v1/live-sync",
        response_model=list[LiveSyncHealth],
        dependencies=protected,
    )
    def list_live_sync_health() -> list[LiveSyncHealth]:
        return list(_live_sync(app).all_health())

    @app.post(
        "/api/v1/sources/{source_id}/sync",
        response_model=ImportJob,
        dependencies=protected,
    )
    async def trigger_live_sync(source_id: str) -> ImportJob:
        _require_registration(app, source_id)
        # Asked for by hand, so it reports a job even when nothing was new;
        # `live_sync_unavailable` keeps meaning the source cannot be synced.
        job = await _live_sync(app).trigger_now(source_id)
        if job is None:
            raise HTTPException(status_code=409, detail="live_sync_unavailable")
        return job

    @app.get(
        "/api/v1/import-jobs/{job_id}",
        response_model=ImportJob,
        dependencies=protected,
    )
    def get_import_job(job_id: str) -> ImportJob:
        return _require_job(app, job_id)

    @app.get(
        "/api/v1/import-jobs/{job_id}/issues",
        response_model=list[ImportIssueRecord],
        dependencies=protected,
    )
    def list_import_issues(job_id: str) -> list[ImportIssueRecord]:
        _require_job(app, job_id)
        return _job_store(app).list_issues(job_id)

    @app.get(
        "/api/v1/import-jobs/{job_id}/skip-reasons",
        response_model=dict[str, int],
        dependencies=protected,
    )
    def import_skip_reasons(job_id: str) -> dict[str, int]:
        _require_job(app, job_id)
        return _job_store(app).skip_reason_counts(job_id)

    @app.post(
        "/api/v1/import-jobs/{job_id}/pause",
        response_model=ImportJob,
        dependencies=protected,
    )
    async def pause_import_job(job_id: str) -> ImportJob:
        _require_job(app, job_id)
        return await _translate_transition(_supervisor(app).pause(job_id))

    @app.post(
        "/api/v1/import-jobs/{job_id}/resume",
        response_model=ImportJob,
        dependencies=protected,
    )
    async def resume_import_job(job_id: str) -> ImportJob:
        job = _require_job(app, job_id)
        if job.status in {ImportStatus.COMPLETED, ImportStatus.CANCELLED}:
            raise HTTPException(status_code=409, detail="job_not_resumable")
        try:
            return _supervisor(app).resume(job_id)
        except InvalidJobTransitionError:
            raise HTTPException(409, "invalid_job_transition") from None

    @app.post(
        "/api/v1/import-jobs/{job_id}/cancel",
        response_model=ImportJob,
        dependencies=protected,
    )
    async def cancel_import_job(job_id: str) -> ImportJob:
        _require_job(app, job_id)
        return await _translate_transition(_supervisor(app).cancel(job_id))

    @app.get(
        "/api/v1/model-profiles",
        response_model=list[ModelProfile],
        dependencies=protected,
    )
    def list_model_profiles() -> list[ModelProfile]:
        try:
            return _model_profile_store(app).list(_secret_store(app))
        except SecretStoreUnavailableError as error:
            raise HTTPException(
                status_code=503,
                detail=_secret_store_detail(error),
            ) from None

    @app.post(
        "/api/v1/model-profiles",
        response_model=ModelProfile,
        dependencies=protected,
    )
    def create_model_profile(request: ModelProfileInput) -> ModelProfile:
        try:
            return _model_profile_store(app).create(request, _secret_store(app))
        except ModelCreationOperationConflictError:
            raise HTTPException(409, "model_creation_operation_conflict") from None
        except ModelCreationOperationLookupError:
            raise HTTPException(503, "model_creation_operation_unavailable") from None
        except SecretStoreUnavailableError as error:
            raise HTTPException(
                status_code=503,
                detail=_secret_store_detail(error),
            ) from None

    @app.get("/api/v1/model-profile-creations/{operation_id}", dependencies=protected)
    def model_creation_operation(operation_id: str) -> ModelCreationOperationReceipt:
        try:
            return _model_profile_store(app).lookup_creation_operation(operation_id)
        except ValueError:
            raise HTTPException(422, "invalid_creation_operation_id") from None
        except ModelCreationOperationLookupError:
            raise HTTPException(503, "model_creation_operation_lookup_unavailable") from None

    @app.post(
        "/api/v1/model-profile-creations/{operation_id}/abandon",
        response_model=ModelCreationOperationReceipt,
        dependencies=protected,
    )
    def abandon_model_creation(operation_id: str) -> ModelCreationOperationReceipt:
        try:
            return _model_profile_store(app).abandon_creation_operation(operation_id)
        except ValueError:
            raise HTTPException(422, "invalid_creation_operation_id") from None
        except ModelCreationOperationLookupError:
            raise HTTPException(409, "model_creation_operation_changed") from None

    @app.patch(
        "/api/v1/model-profiles/{profile_id}",
        response_model=ModelProfile,
        dependencies=protected,
    )
    def update_model_profile(
        profile_id: str,
        request: ModelProfilePatch,
    ) -> ModelProfile:
        try:
            return _model_profile_store(app).update(
                profile_id,
                request,
                _secret_store(app),
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="model_profile_not_found") from None
        except ModelProfileConflictError:
            raise HTTPException(status_code=409, detail="model_profile_conflict") from None
        except SecretStoreUnavailableError as error:
            raise HTTPException(
                status_code=503,
                detail=_secret_store_detail(error),
            ) from None

    @app.post(
        "/api/v1/model-profiles/{profile_id}/activate",
        response_model=ModelProfile,
        dependencies=protected,
    )
    async def activate_model_profile(profile_id: str) -> ModelProfile:
        # Asynchronous for the sake of what follows the activation: scheduling
        # work needs the event loop this coroutine runs on, and a synchronous
        # path operation is handed to a worker thread that has none. Activating
        # with any evidence already imported — the ordinary order, since
        # importing comes first — used to fail here outright.
        try:
            profile = await asyncio.to_thread(
                _model_profile_store(app).activate,
                profile_id,
                _secret_store(app),
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="model_profile_not_found") from None
        except ModelProfileUnavailableError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        # D75: work queued for the previous model continues with this one.
        # Only tasks that never reached the model are re-bound.
        await asyncio.to_thread(app.state.memory_task_store.rebind_unstarted_profile, profile)
        # Not awaited: the catch-up reads every conversation ever imported, and
        # the answer to "which profile is active" does not depend on it.
        _start_backfill(app)
        return profile

    from one_memory.model_probe_operations import (
        ModelProbeOperationReceipt,
        ModelProbeOperationRequest,
        ModelProbeOperations,
        ProbeOperationConflict,
        ProbeOperationUnavailable,
    )

    @app.get(
        "/api/v1/model-probe-operations/{operation_id}",
        response_model=ModelProbeOperationReceipt,
        dependencies=protected,
    )
    async def model_probe_operation(operation_id: str) -> ModelProbeOperationReceipt:
        try:
            return await asyncio.to_thread(
                ModelProbeOperations(_model_profile_store(app)).lookup, operation_id
            )
        except ValueError:
            raise HTTPException(422, "invalid_probe_operation_id") from None
        except ProbeOperationUnavailable:
            raise HTTPException(503, "model_probe_operation_unavailable") from None

    @app.post(
        "/api/v1/model-profiles/{profile_id}/probe",
        response_model=ModelProbeResult | ModelProbeOperationReceipt,
        dependencies=protected,
    )
    async def probe_model_profile(
        profile_id: str, operation: ModelProbeOperationRequest | None = None
    ) -> ModelProbeResult | ModelProbeOperationReceipt:
        started = time.monotonic()
        operation_id = operation.operation_id if operation is not None else None
        diagnostic_event(
            level="info",
            component="model.probe",
            event="model_probe.started",
            outcome="started",
            operation_id=operation_id,
        )
        try:
            result = await _model_prober(app).probe(
                profile_id, **(operation.model_dump() if operation is not None else {})
            )
            completed_probe = (
                result
                if isinstance(result, ModelProbeResult)
                else result.result
                if result.state == "completed"
                else None
            )
            if completed_probe is not None and all(
                (
                    completed_probe.capabilities.endpoint_reachable,
                    completed_probe.capabilities.model_available,
                    completed_probe.capabilities.embedding_available,
                )
            ):
                resumed = await asyncio.to_thread(
                    app.state.memory_task_store.resume_profile_after_probe,
                    completed_probe.profile_id,
                    completed_probe.profile_revision,
                )
                if resumed:
                    _start_backfill(app)
            diagnostic_event(
                level="info",
                component="model.probe",
                event="model_probe.completed",
                outcome="succeeded",
                operation_id=operation_id,
                duration_ms=elapsed_ms(started),
            )
            return result
        except ProbeOperationConflict as error:
            diagnostic_event(
                level="warn",
                component="model.probe",
                event="model_probe.rejected",
                outcome="rejected",
                operation_id=operation_id,
                duration_ms=elapsed_ms(started),
                error_code="model_probe_operation_conflict",
                **diagnostic_exception(error),
            )
            raise HTTPException(409, str(error)) from None
        except ProbeOperationUnavailable:
            diagnostic_event(
                level="error",
                component="model.probe",
                event="model_probe.failed",
                outcome="failed",
                operation_id=operation_id,
                duration_ms=elapsed_ms(started),
                error_code="model_probe_operation_unavailable",
            )
            raise HTTPException(503, "model_probe_operation_unavailable") from None
        except KeyError:
            raise HTTPException(status_code=404, detail="model_profile_not_found") from None
        except ModelProfileConflictError:
            raise HTTPException(status_code=409, detail="model_profile_conflict") from None
        except SecretStoreUnavailableError as error:
            raise HTTPException(
                status_code=503,
                detail=_secret_store_detail(error),
            ) from None

    @app.delete(
        "/api/v1/model-profiles/{profile_id}",
        status_code=204,
        dependencies=protected,
    )
    def delete_model_profile(profile_id: str) -> Response:
        try:
            _model_profile_store(app).delete(profile_id, _secret_store(app))
        except KeyError:
            raise HTTPException(status_code=404, detail="model_profile_not_found") from None
        except ActiveModelProfileError:
            raise HTTPException(
                status_code=409,
                detail="active_model_profile",
            ) from None
        except SecretStoreUnavailableError as error:
            raise HTTPException(
                status_code=503,
                detail=_secret_store_detail(error),
            ) from None
        # D75: the configuration is gone, so queued work waits instead of
        # failing. Configuring a model again resumes the same tasks.
        app.state.memory_task_store.pause_profile_for_model(profile_id)
        return Response(status_code=204)

    @app.get("/api/v1/home-summary", response_model=HomeSummary, dependencies=protected)
    def home_summary() -> HomeSummary:
        return read_home_summary(app.state.evidence_store.database_path)

    @app.get("/api/v1/memory-library", response_model=MemoryLibraryPage, dependencies=protected)
    def memory_library(
        query: str | None = Query(default=None, max_length=512),
        status: MemoryStatus | None = None,
        active_only: bool = False,
        source: str | None = Query(default=None, min_length=1),
        offset: int = Query(default=0, ge=0, le=2**63 - 1),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> MemoryLibraryPage:
        if active_only and status is not None:
            raise HTTPException(422, "active_only_conflicts_with_status")
        try:
            return read_memory_library(
                _evidence_store(app).database_path,
                user_id="local",
                query=query,
                status=status,
                active_only=active_only,
                source=source,
                offset=offset,
                limit=limit,
            )
        except Exception:
            # Stored-data/IO failures are not empty results or invalid client input.
            raise HTTPException(status_code=503, detail="memory_library_unavailable") from None

    @app.get(
        "/api/v1/memories",
        response_model=list[GovernedMemory],
        dependencies=protected,
    )
    def list_memories(
        status: MemoryStatus | None = None,
        query: str | None = None,
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> list[GovernedMemory]:
        return _read_access(app).memories(
            _memory_store(app).list(
                user_id="local",
                status=status,
                query=query,
                limit=limit,
                offset=offset,
            )
        )

    @app.get(
        "/api/v1/conversations",
        response_model=RecentConversationsPage,
        dependencies=protected,
    )
    def list_recent_conversations(
        request: Request,
        limit: int = Query(default=30, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        source_id: str | None = Query(default=None, min_length=1, max_length=128),
        recent_activity: bool = False,
    ) -> RecentConversationsPage:
        """Messages in the order they were imported, before any memory exists.

        This is the app's own board. It does not record a recall call: polling
        it must not show up as the user asking an assistant for memories.
        """
        evidence = _evidence_store(app)
        access = external_access(request.headers)
        raw_sources: frozenset[str] | None = None
        if access is not None:
            access.require("om_conversations", frozenset({"all-apps"}))
            raw_sources = access.readable_source_ids(evidence.database_path)
            if source_id is not None:
                raw_sources = frozenset({source_id}) & raw_sources
        elif source_id is not None:
            raw_sources = frozenset({source_id})
        if raw_sources is not None and not raw_sources:
            return RecentConversationsPage(limit=limit, offset=offset)
        records, total = evidence.list_recent_conversations(
            user_id="local",
            limit=limit,
            offset=offset,
            source_ids=tuple(raw_sources) if raw_sources is not None else None,
            recent_activity=recent_activity,
        )
        visible = _read_access(app).records(records)
        return RecentConversationsPage(
            items=tuple(_recent_conversation_item(record) for record in visible),
            total=total,
            offset=offset,
            limit=limit,
        )

    @app.get(
        "/api/v1/conversations/search",
        response_model=ConversationSearchResponse,
        dependencies=protected,
    )
    def search_conversations(
        request: Request,
        response: Response,
        query: str = Query(min_length=1, max_length=512),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> ConversationSearchResponse:
        """Search imported conversations, with or without a generation model.

        This is the one thing the app can do on the day it is installed. It
        reads nothing that was not already imported: `verbatim` and the
        conversation metadata beside it, no new collection of any kind.
        """
        started = time.monotonic()
        evidence = _evidence_store(app)
        access = external_access(request.headers)
        raw_sources: frozenset[str] = frozenset()
        if access is not None:
            access.require("om_conversations", frozenset({"all-apps"}))
            raw_sources = access.readable_source_ids(evidence.database_path)
        try:
            found = (
                []
                if access is not None and not raw_sources
                else _read_access(app).records(
                    evidence.search_conversations(
                        query,
                        user_id="local",
                        limit=limit,
                        source_ids=tuple(raw_sources) if access is not None else None,
                    )
                )
            )
            if access is not None:
                found = [record for record in found if record.source_id in raw_sources]
        except ConversationSearchUnavailableError:
            raise HTTPException(
                status_code=503,
                detail="conversation_search_unavailable",
            ) from None
        imported = (
            evidence.count()
            if access is None
            else sum(
                evidence.source_totals(user_id="local", source_id=source_id)[0]
                for source_id in raw_sources
            )
        )
        if found:
            reason = None
        elif imported == 0:
            reason = ConversationSearchEmptyReason.NOTHING_IMPORTED
        else:
            reason = ConversationSearchEmptyReason.NO_MATCHES
        summaries = {}
        for record in found:
            key = (record.source_id, record.conversation_id, record.workspace)
            if key not in summaries:
                summaries[key] = evidence.conversation_summary(record.evidence_id)
        unique: dict[tuple[str, str, str | None], ConversationSearchHit] = {}
        for record in found:
            key = (record.source_id, record.conversation_id, record.workspace)
            summary = summaries[key]
            if key in unique:
                continue
            unique[key] = ConversationSearchHit(
                evidence_id=record.evidence_id,
                conversation_id=record.conversation_id,
                source_id=record.source_id,
                workspace=record.workspace,
                project=record.project,
                created_at=record.created_at,
                excerpt=record.verbatim,
                title=summary.title if summary else None,
                turn_count=summary.turn_count if summary else None,
                memory_count=summary.memory_count if summary else None,
                turn_offset=summary.anchor_turn_offset if summary else None,
            )
        result = ConversationSearchResponse(
            hits=tuple(unique.values()),
            empty_reason=reason,
            imported_messages=imported,
        )
        if query != "__om_probe__" and access is not None:
            from one_memory.call_event_recorder import conversation_call_event, try_record

            try_record(
                request,
                lambda store: store.record(
                    conversation_call_event(result, access, (time.monotonic() - started) * 1000)
                ),
                response,
            )
        return result

    @app.get(
        "/api/v1/conversation-browser",
        response_model=ConversationBrowserPage,
        dependencies=protected,
    )
    def list_conversation_browser(
        request: Request,
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        source_id: str | None = Query(default=None, min_length=1, max_length=128),
    ) -> ConversationBrowserPage:
        evidence = _evidence_store(app)
        access = external_access(request.headers)
        raw_sources: frozenset[str] | None = None
        if access is not None:
            access.require("om_conversations", frozenset({"all-apps"}))
            raw_sources = access.readable_source_ids(evidence.database_path)
            if source_id is not None:
                raw_sources = frozenset({source_id}) & raw_sources
        elif source_id is not None:
            raw_sources = frozenset({source_id})
        if raw_sources is not None and not raw_sources:
            return ConversationBrowserPage(total=0, offset=offset, limit=limit)
        items, total = evidence.list_conversation_summaries(
            user_id="local",
            limit=limit,
            offset=offset,
            source_ids=tuple(raw_sources) if raw_sources is not None else None,
        )
        return ConversationBrowserPage(
            items=tuple(ConversationSummary(**item.__dict__) for item in items),
            total=total,
            offset=offset,
            limit=limit,
        )

    @app.get(
        "/api/v1/conversations/{evidence_id}/memories",
        response_model=ConversationMemories,
        dependencies=protected,
    )
    def get_conversation_memories(evidence_id: str) -> ConversationMemories:
        evidence = _evidence_store(app)
        seed = evidence.get(evidence_id)
        if seed is None or not _read_access(app).records([seed]):
            raise HTTPException(status_code=404, detail="conversation_not_found")
        items = read_conversation_memories(
            evidence.database_path,
            user_id=seed.user_id,
            source_id=seed.source_id,
            conversation_id=seed.conversation_id,
            workspace=seed.workspace,
        )
        return ConversationMemories(items=tuple(_read_access(app).memories(items)))

    @app.get(
        "/api/v1/conversations/{evidence_id}",
        response_model=ConversationTranscript,
        dependencies=protected,
    )
    def get_conversation(
        evidence_id: str,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> ConversationTranscript:
        page = _evidence_store(app).list_conversation_turns(evidence_id, offset=offset, limit=limit)
        messages = _read_access(app).records(page.records)
        if not messages:
            raise HTTPException(status_code=404, detail="conversation_not_found")
        return ConversationTranscript(
            conversation_id=messages[0].conversation_id,
            source_id=messages[0].source_id,
            messages=tuple(
                ConversationMessage(
                    evidence_id=record.evidence_id,
                    role=record.role,
                    written_by=record.written_by,
                    created_at=record.created_at,
                    verbatim=record.verbatim,
                    turn_index=page.turn_by_evidence_id[record.evidence_id],
                    system_context=_conversation_system_context(record.verbatim),
                )
                for record in messages
            ),
            total_turns=page.total_turns,
            offset=page.offset,
            limit=page.limit,
            has_more=page.offset + page.limit < page.total_turns,
            trailing_turn_unanswered=page.trailing_turn_unanswered,
        )

    @app.post(
        "/api/v1/memories/ask",
        response_model=AskMemoryResponse,
        dependencies=protected,
    )
    async def ask_memories(
        request: AskMemoryRequest, http_request: Request, response: Response
    ) -> AskMemoryResponse:
        started = time.monotonic()
        access = external_access(http_request.headers)
        await _require_external_recall_scope(
            app, http_request, request.scope, request.workspace_path, "om_ask"
        )
        try:
            result = await (
                await _request_answerer(app, http_request, workspace_path=request.workspace_path)
            ).answer(
                request.question,
                scope=request.scope,
                workspace_path=request.workspace_path,
            )
        except MemoryAnswerError as error:
            raise HTTPException(status_code=503, detail=str(error)) from None
        answer = AskMemoryResponse(
            answer=result.answer,
            memories=result.memories,
            mode=result.mode,
        )
        if request.question != "__om_probe__" and access is not None:
            from one_memory.call_event_recorder import ask_call_event, try_record

            try_record(
                http_request,
                lambda store: store.record(
                    ask_call_event(
                        result,
                        access,
                        (time.monotonic() - started) * 1000,
                    )
                ),
                response,
            )
        return answer

    @app.post(
        "/api/v1/memories/ask/stream",
        dependencies=protected,
    )
    async def ask_memories_stream(
        request: AskMemoryRequest, http_request: Request
    ) -> StreamingResponse:
        await _require_external_recall_scope(
            app, http_request, request.scope, request.workspace_path, "om_ask"
        )
        answerer = await _request_answerer(app, http_request, workspace_path=request.workspace_path)
        started = time.monotonic()
        access = external_access(http_request.headers)

        async def events() -> AsyncIterator[str]:
            from one_memory.call_event_recorder import ask_call_event, try_record
            from one_memory.memory_qa import MemoryAnswer

            final: dict[str, object] | None = None
            try:
                async for event in answerer.answer_stream(
                    request.question,
                    scope=request.scope,
                    workspace_path=request.workspace_path,
                ):
                    if event.get("type") == "final":
                        final = event
                    yield _sse(_serialize_ask_event(event))
                if final is not None and request.question != "__om_probe__" and access is not None:
                    memories = final.get("memories")
                    mode = final.get("mode")
                    recorded_mode: Literal["generated", "extractive", "empty"] = (
                        mode if mode in {"generated", "extractive", "empty"} else "empty"
                    )
                    try_record(
                        http_request,
                        lambda store: store.record(
                            ask_call_event(
                                MemoryAnswer(
                                    answer="",
                                    memories=tuple(memories) if isinstance(memories, tuple) else (),
                                    mode=recorded_mode,
                                ),
                                access,
                                (time.monotonic() - started) * 1000,
                            )
                        ),
                    )
            except MemoryAnswerError as error:
                yield _sse({"type": "error", "code": str(error)})

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    async def _catch_hy_up_on(memory_ids: tuple[str, ...]) -> None:
        """Re-index the corrections among the memories a recall just returned.

        Runs after the response, so the recall pays nothing for it — which is
        also why this, and not a sweep at startup: an outage that made every
        launch reach a dead HY would turn one network fault into an app that
        will not open, while a recall that skips the catch-up costs nothing at
        all. The memory it returned was answered from the governance store and
        already reads correctly.

        Only the memories this recall returned. One that is never recalled is
        never read, so its index being behind is a difference nobody can
        observe, and sweeping for it would be work with no reader.

        `hy_memory_update_rejected` is left where it is: HY holds no such
        memory to re-embed, so retrying cannot end differently, and recall is
        the path that runs constantly.
        """
        store = _memory_store(app)
        for record in store.pending_index_syncs(user_id="local", memory_ids=memory_ids):
            if record.last_error_code == "hy_memory_update_rejected":
                continue
            memory = store.get(record.memory_id, user_id="local")
            if memory is not None and _read_access(app).memories([memory]):
                await _reindex(memory)

    @app.post(
        "/api/v1/recall",
        response_model=RecallReceipt,
        dependencies=protected,
    )
    async def recall_memories(
        request: RecallRequest,
        background: BackgroundTasks,
        http_request: Request,
    ) -> RecallReceipt:
        from one_memory.call_event_recorder import record_legacy_result

        started = time.monotonic()
        await _require_external_recall_scope(
            app, http_request, request.scope, request.workspace_path
        )
        target = _recall_target(app)
        access = external_access(http_request.headers)
        if access is not None:
            target = copy(target)
            original_filter = target.filter_memories
            target.filter_memories = lambda memories: tuple(
                item
                for item in original_filter(memories)
                if not access.scopes.isdisjoint(item.scopes)
            )
        receipt = await target.recall_async(request)
        event_store = getattr(app.state, "call_event_store", None)
        if event_store is not None and request.query != "__om_probe__":
            try:
                await asyncio.to_thread(
                    record_legacy_result,
                    event_store,
                    receipt,
                    access,
                    (time.monotonic() - started) * 1000,
                )
            except Exception:
                app.state.call_event_recording_error = True
        index_ids = tuple(
            hit.memory_id for hit in receipt.hits if hit.memory_id and hit.kind != "self_check"
        )
        if index_ids:
            background.add_task(
                _catch_hy_up_on,
                index_ids,
            )
        return receipt

    @app.get(
        "/api/v1/memories/graph",
        response_model=MemoryGraphResponse,
        dependencies=protected,
    )
    async def memory_graph(
        limit: int = Query(default=160, ge=1, le=300),
    ) -> MemoryGraphResponse:
        cache = _map_cache(app)
        revoked = await asyncio.to_thread(_read_access(app).has_revoked_sources)
        key = (
            limit,
            revoked,
            *_MapCache.library_version(app),
            *_MapCache.grant_version(app),
        )
        cached = cache.get(key)
        if cached is not None:
            return cached

        # Read in a thread. Every store call below is synchronous, and the
        # evidence is fetched one id at a time — hundreds of queries for a
        # full map. Run on the loop, as this was, the whole process stopped
        # answering for the duration: the watchdog caught it here holding the
        # loop 2.1s, and /health was measured at 17.9s while a map was being
        # drawn. It is the map the memory view needs, so the app blocked
        # itself every time it opened.
        def read_governed_part() -> tuple[
            list[GovernedMemory],
            list[GovernedMemory],
            dict[str, MemoryGraphNode],
            list[MemoryGraphEdge],
        ]:
            return _governed_graph_part(app, limit)

        try:
            all_memories, projected, nodes, edges = await asyncio.to_thread(read_governed_part)
            try:
                hy_graph = (
                    None
                    if revoked
                    else await _memory_processing_supervisor(app).graph_snapshot(limit=limit)
                )
            except HyMemoryBridgeError:
                hy_graph = None

            revoked_now = await asyncio.to_thread(_read_access(app).has_revoked_sources)
            if revoked_now:
                # Native aggregate nodes do not carry per-source provenance
                # yet. Keep the governed map; withhold an untraceable HY
                # overlay.
                hy_graph = None
                if not revoked:
                    # A revocation landed mid-read; the first pass was drawn
                    # under the older grants.
                    all_memories, projected, nodes, edges = await asyncio.to_thread(
                        read_governed_part
                    )
            if hy_graph is not None:
                governed_ids = {memory.memory_id for memory in projected}

                def projected_node_id(node_id: str) -> str:
                    return f"memory:{node_id}" if node_id in governed_ids else f"hy:{node_id}"

                for node in hy_graph.nodes:
                    node_id = projected_node_id(node.node_id)
                    if node_id in nodes:
                        continue
                    nodes[node_id] = MemoryGraphNode(
                        node_id=node_id,
                        node_type="topic" if node.layer == "TOPIC" else "schema",
                        label=node.content,
                        confidence=node.confidence,
                    )
                for edge in hy_graph.edges:
                    source_node_id = projected_node_id(edge.source_node_id)
                    target_node_id = projected_node_id(edge.target_node_id)
                    if edge.relation == "derived_from" and target_node_id not in nodes:
                        continue
                    if source_node_id not in nodes or target_node_id not in nodes:
                        continue
                    edges.append(
                        MemoryGraphEdge(
                            edge_id=(f"{source_node_id}->{target_node_id}:{edge.relation}"),
                            source_node_id=source_node_id,
                            target_node_id=target_node_id,
                            relation=edge.relation,
                            weight=edge.weight,
                            origin="hy_relation",
                        )
                    )
        except sqlite3.OperationalError:
            # D91: the read crossed its interrupt, most likely because the
            # library is being written to at the same time. A map drawn from
            # this same library is a better answer than an error, so serve it
            # when one is known rather than making the view empty.
            fallback = cache.get(key)
            if fallback is None:
                raise
            return fallback

        drawn = MemoryGraphResponse(
            total_memories=len(all_memories),
            projected_memories=len(projected),
            nodes=tuple(nodes.values()),
            edges=tuple(edges),
            hy_graph_available=hy_graph is not None and hy_graph.consulted,
        )
        cache.remember(key, drawn)
        return drawn

    @app.post(
        "/api/v1/memories/graph/digest",
        response_model=dict[str, object],
        dependencies=protected,
    )
    async def digest_memory_graph() -> dict[str, object]:
        if await asyncio.to_thread(_read_access(app).has_revoked_sources):
            raise HTTPException(status_code=403, detail="memory_source_revoked")
        try:
            return await _memory_processing_supervisor(app).digest()
        except HyMemoryBridgeError as error:
            raise HTTPException(status_code=503, detail=str(error)) from None

    @app.get(
        "/api/v1/memory-tasks",
        response_model=list[MemoryTask],
        dependencies=protected,
    )
    def list_memory_tasks(
        status: MemoryTaskStatus | None = None,
    ) -> list[MemoryTask]:
        return _memory_task_store(app).list(statuses=(status,) if status is not None else None)

    @app.get(
        "/api/v1/memory-processing",
        response_model=MemoryProcessingSummary,
        dependencies=protected,
    )
    def memory_processing() -> MemoryProcessingSummary:
        store = _memory_task_store(app)
        counts = store.counts()
        # Two numbers, not one: 675 as a single total reads as "stuck", when
        # what is true is that the three from this morning are nearly done
        # and the rest is catch-up (R-02).
        supervisor = app.state.memory_processing_supervisor
        depths = supervisor.queue_depths()
        active_queue, in_flight_count = supervisor.active_depths()
        planned, current = supervisor.manual_batch_progress()
        # `running` counts rows the store still marks running, which includes
        # work a worker has already given up on. Reporting that as "what is
        # running" is how a stalled queue looks busy, so the strand is given
        # its own number.
        running_rows = counts.get(MemoryTaskStatus.RUNNING, 0)
        return MemoryProcessingSummary(
            pending=counts.get(MemoryTaskStatus.PENDING, 0),
            fresh_pending=depths.fresh,
            backlog_pending=depths.backlog,
            outside_window_pending=counts.get(
                MemoryTaskStatus.DEFERRED_OUTSIDE_WINDOW,
                0,
            ),
            recent_completions=depths.recent_completions,
            running=running_rows,
            stranded_running=max(0, running_rows - in_flight_count),
            unexecuted_policy_failures=store.unexecuted_policy_failures(),
            waiting_for_model=counts.get(MemoryTaskStatus.WAITING_FOR_MODEL, 0),
            completed=counts.get(MemoryTaskStatus.COMPLETED, 0),
            failed=counts.get(MemoryTaskStatus.FAILED, 0),
            unknown=counts.get(MemoryTaskStatus.UNKNOWN, 0),
            unknown_reasons=store.unknown_error_counts(),
            blocked_stages=tuple(
                MemoryProcessingBlock(kind=kind, reason=reason, count=count)
                for kind, reason, count in store.blocked_stages()[:6]
            ),
            seconds_per_completion=store.seconds_per_completion(),
            extraction_window_preset=supervisor.extraction_window_settings().window_preset,
            manual_extract_batches_planned=planned,
            manual_extract_batches_current=current,
            active_queue=active_queue,
            in_flight_count=in_flight_count,
            metrics=ProcessingMetrics.model_validate(store.processing_metrics()),
        )

    @app.post(
        "/api/v1/memory-processing/retry-unknown",
        response_model=RetryUnknownMemoryTasksReceipt,
        dependencies=protected,
    )
    async def retry_unknown_memory_tasks(
        request: RetryUnknownMemoryTasksRequest,
    ) -> RetryUnknownMemoryTasksReceipt:
        """Retry unknown writes only behind the user's explicit confirmation."""
        del request
        reconciled = await _memory_processing_supervisor(app).reconcile_unknown_outputs()
        from one_memory.memory_server.durable_queue import DurableWriteQueue

        queue = DurableWriteQueue(app.state.memory_write_queue_path)
        unknown = queue.unknown_jobs(limit=2048)
        for job in unknown:
            queue.resolve_unknown_from_linked_output(job.job_id)
        requeued = sum(queue.retry_unknown_confirmed(job.job_id) for job in unknown)
        requeued += app.state.memory_task_store.retry_unknown_confirmed()
        return RetryUnknownMemoryTasksReceipt(reconciled=reconciled, requeued=requeued)

    @app.get(
        "/api/v1/graph-width",
        response_model=GraphWidthStatus,
        dependencies=protected,
    )
    async def get_graph_width() -> GraphWidthStatus:
        """Report the mismatch. Never writes: this is polled by the app."""
        try:
            mismatch = await _memory_processing_supervisor(app).graph_width_mismatch()
        except HyMemoryBridgeError as error:
            raise HTTPException(status_code=503, detail=str(error)) from None
        if mismatch is None:
            return GraphWidthStatus()
        return GraphWidthStatus(
            mismatch=mismatch,
            memory_count=len(_memory_store(app).list(user_id="local")),
        )

    @app.post(
        "/api/v1/graph-width",
        response_model=GraphWidthDecisionReceipt,
        dependencies=protected,
    )
    async def decide_graph_width(
        request: GraphWidthDecisionRequest,
    ) -> GraphWidthDecisionReceipt:
        """Carry out the user's choice. Only REBUILD_WITH_NEW archives anything.

        There is deliberately no default here: a request that does not name a
        decision is rejected by validation rather than assumed, because the
        assumption that cost a live graph once was made in code, not by a user.
        """
        supervisor = _memory_processing_supervisor(app)
        try:
            outcome = await supervisor.decide_graph_width(request.decision)
        except HyMemoryBridgeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except OSError as error:
            # A failed export left nothing behind, but the user must be told
            # so, or they will go on to rebuild believing they have a copy.
            raise HTTPException(status_code=500, detail="graph_export_failed") from error
        return GraphWidthDecisionReceipt(
            archived_path=outcome.archived_path,
            exported_path=outcome.exported_path,
            mismatch=await supervisor.graph_width_mismatch(),
        )

    @app.get(
        "/api/v1/extraction-settings",
        response_model=ExtractionSettings,
        dependencies=protected,
    )
    def get_extraction_settings() -> ExtractionSettings:
        return _memory_processing_supervisor(app).extraction_window_settings()

    @app.put(
        "/api/v1/extraction-settings",
        response_model=ExtractionSettings,
        dependencies=protected,
    )
    def put_extraction_settings(request: ExtractionWindowUpdate) -> ExtractionSettings:
        return _memory_processing_supervisor(app).set_extraction_window(request.window_preset)

    @app.post(
        "/api/v1/memory-processing/manual-extract",
        response_model=ManualExtractReceipt,
        dependencies=protected,
    )
    def manual_extract(request: ManualExtractRequest) -> ManualExtractReceipt:
        supervisor = _memory_processing_supervisor(app)
        task_ids: tuple[str, ...] | None = None
        if request.evidence_ids:
            resolved: list[str] = []
            for evidence_id in request.evidence_ids:
                task = _memory_task_store(app).task_for_evidence(evidence_id)
                if task is not None and task.status is MemoryTaskStatus.DEFERRED_OUTSIDE_WINDOW:
                    resolved.append(task.task_id)
            task_ids = tuple(resolved)
            if not task_ids:
                planned, current = supervisor.manual_batch_progress()
                return ManualExtractReceipt(
                    promoted=0,
                    batches_planned=planned,
                    batches_current=current,
                )
        promoted = supervisor.promote_next_manual_batch(task_ids=task_ids)
        planned, current = supervisor.manual_batch_progress()
        return ManualExtractReceipt(
            promoted=len(promoted),
            batches_planned=planned,
            batches_current=current,
        )

    from one_memory.memory_mutation_receipts import (
        MemoryMutationReceipt,
        MemoryMutationReceiptForbidden,
        MemoryMutationReceiptUnavailable,
        read_memory_mutation_receipt,
    )

    @app.get(
        "/api/v1/memory-mutations/{request_id}",
        response_model=MemoryMutationReceipt,
        dependencies=protected,
    )
    async def memory_mutation_receipt(request_id: str) -> MemoryMutationReceipt:
        try:
            return await asyncio.to_thread(
                read_memory_mutation_receipt,
                app.state.evidence_store.database_path,
                request_id=request_id,
                user_id="local",
            )
        except ValueError:
            raise HTTPException(422, "invalid_memory_mutation_request_id") from None
        except MemoryMutationReceiptForbidden:
            raise HTTPException(403, "memory_mutation_receipt_forbidden") from None
        except MemoryMutationReceiptUnavailable:
            raise HTTPException(503, "memory_mutation_receipt_unavailable") from None

    @app.post(
        "/api/v1/memories",
        response_model=GovernedMemory,
        dependencies=protected,
    )
    def create_memory(request: CreateMemoryRequest) -> GovernedMemory:
        if request.draft.user_id != "local":
            raise HTTPException(status_code=403, detail="memory_user_forbidden")
        try:
            return _person_writes(app).create(
                request.draft,
                request_id=request.request_id,
            )
        except MemoryEvidenceNotFoundError:
            raise HTTPException(
                status_code=409,
                detail="memory_evidence_not_found",
            ) from None
        except MemoryMutationRequestConflictError:
            raise HTTPException(
                status_code=409,
                detail="memory_request_conflict",
            ) from None

    @app.get(
        "/api/v1/memories/{memory_id}",
        response_model=MemoryDetail,
        dependencies=protected,
    )
    def get_memory(memory_id: str) -> MemoryDetail:
        memory = _memory_store(app).get(memory_id, user_id="local")
        if memory is None:
            raise HTTPException(status_code=404, detail="memory_not_found")
        if not _read_access(app).memories([memory]):
            raise HTTPException(status_code=403, detail="memory_source_revoked")
        evidence = []
        for evidence_id in memory.evidence_ids:
            record = _evidence_store(app).get(evidence_id)
            if record is not None and record.user_id == "local":
                evidence.append(_evidence_excerpt(app, record))
            else:
                evidence.append(
                    EvidenceExcerpt(
                        evidence_id=evidence_id,
                        available=False,
                    )
                )
        return MemoryDetail(
            memory=memory,
            evidence=tuple(evidence),
            history=tuple(_memory_store(app).history(memory_id, user_id="local")),
            index_sync=_memory_store(app).index_sync(memory_id, user_id="local"),
        )

    async def _reindex(memory: GovernedMemory) -> MemoryIndexSync:
        """Put the rewritten text into HY's index, and record what happened.

        Sends the memory's stored content rather than the text the caller had
        in hand, so two corrections made while HY was unreachable converge on
        the latest wording instead of replaying an intermediate one.

        HY being unreachable does not undo the user's edit — the edit is
        already committed when this runs, and a failure here only records that
        search is still matching on text they replaced.
        """
        store = _memory_store(app)
        try:
            await _memory_processing_supervisor(app).update_hy_memory(
                memory.memory_id,
                memory.content,
            )
        except HyMemoryBridgeError as error:
            return store.fail_index_sync(
                memory.memory_id,
                user_id="local",
                error_code=str(error),
            )
        return store.complete_index_sync(memory.memory_id, user_id="local")

    @app.post(
        "/api/v1/memories/{memory_id}/actions/{action}",
        response_model=GovernedMemory,
        dependencies=protected,
    )
    async def mutate_memory(
        memory_id: str,
        action: Literal["undo", "delete"],
        request: MemoryMutationRequest,
    ) -> GovernedMemory:
        operation = getattr(_person_writes(app), action)
        try:
            memory = cast(
                GovernedMemory,
                operation(
                    memory_id,
                    user_id="local",
                    request_id=request.request_id,
                    expected_revision=request.expected_revision,
                ),
            )
            if action == "undo":
                # An undo restores earlier text, so HY is left matching on the
                # wording that was just withdrawn — the same staleness a
                # correction causes, which is why both cascade from here.
                sync = _memory_store(app).index_sync(memory_id, user_id="local")
                if sync is not None and sync.status is IndexSyncStatus.PENDING:
                    await _reindex(memory)
            if action == "delete":
                deletion = _memory_store(app).deletion(memory_id, user_id="local")
                if deletion is None or deletion.status is not DeletionStatus.COMPLETED:
                    await _memory_processing_supervisor(app).finish_deletion(memory_id)
            return memory
        except KeyError:
            raise HTTPException(status_code=404, detail="memory_not_found") from None
        except MemoryMutationConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        except MemoryMutationRequestConflictError:
            raise HTTPException(
                status_code=409,
                detail="memory_request_conflict",
            ) from None

    @app.post(
        "/api/v1/memories/{memory_id}/correct",
        response_model=GovernedMemory,
        dependencies=protected,
    )
    async def correct_memory(
        memory_id: str,
        request: CorrectMemoryRequest,
    ) -> GovernedMemory:
        try:
            memory = _person_writes(app).correct(
                memory_id,
                user_id="local",
                request_id=request.request_id,
                expected_revision=request.expected_revision,
                content=request.content,
                scopes=request.scopes,
            )
            sync = _memory_store(app).index_sync(memory_id, user_id="local")
            if sync is not None and sync.status is IndexSyncStatus.PENDING:
                await _reindex(memory)
            return memory
        except KeyError:
            raise HTTPException(status_code=404, detail="memory_not_found") from None
        except MemoryMutationConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        except MemoryMutationRequestConflictError:
            raise HTTPException(
                status_code=409,
                detail="memory_request_conflict",
            ) from None

    @app.post(
        "/api/v1/memories/{memory_id}/scopes",
        response_model=GovernedMemory,
        dependencies=protected,
    )
    def set_memory_scopes(memory_id: str, request: MemoryScopesRequest) -> GovernedMemory:
        try:
            return _person_writes(app).set_scopes(
                memory_id,
                user_id="local",
                request_id=request.request_id,
                expected_revision=request.expected_revision,
                scopes=request.scopes,
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="memory_not_found") from None
        except MemoryMutationConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        except MemoryMutationRequestConflictError:
            raise HTTPException(status_code=409, detail="memory_request_conflict") from None

    @app.post(
        "/api/v1/memories/{memory_id}/index-sync",
        response_model=MemoryIndexSync,
        dependencies=protected,
    )
    async def sync_memory_index(memory_id: str) -> MemoryIndexSync:
        """Ask HY again for a correction it has not taken yet.

        Takes no request id: re-indexing the memory's current content is the
        same operation however many times it runs, and accepting one would
        suggest a repeat is deduplicated when there is nothing to deduplicate.

        A failed retry is a 200 whose body still says `pending`, not an error.
        The attempt happened, and its outcome — including which error stopped
        it — is what the caller has to show either way.
        """
        store = _memory_store(app)
        memory = store.get(memory_id, user_id="local")
        if memory is None:
            raise HTTPException(status_code=404, detail="memory_not_found")
        sync = store.index_sync(memory_id, user_id="local")
        if sync is None:
            raise HTTPException(status_code=404, detail="memory_index_sync_not_found")
        if sync.status is IndexSyncStatus.SYNCED:
            return sync
        return await _reindex(memory)

    @app.get(
        "/api/v1/memories/{memory_id}/deletion",
        response_model=MemoryDeletion,
        dependencies=protected,
    )
    def memory_deletion(memory_id: str) -> MemoryDeletion:
        deletion = _memory_store(app).deletion(memory_id, user_id="local")
        if deletion is None:
            raise HTTPException(status_code=404, detail="memory_deletion_not_found")
        return deletion

    assets = web_assets_root()
    if assets is not None:
        assets_root = assets.resolve()
        app.mount("/assets", StaticFiles(directory=assets / "assets"), name="ui-assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def ui_spa(path: str) -> Response:
            if path.startswith(("api/", "ui/")):
                raise HTTPException(status_code=404, detail="not_found")
            candidate = (assets / path).resolve()
            if candidate.is_file() and assets_root in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(assets / "index.html")

    return app


async def _require_external_recall_scope(
    app: FastAPI, request: Request, scope: str, workspace_path: str | None, tool: str = "om_recall"
) -> None:
    # The broker supplies this marker; the renderer keeps its authenticated
    # all-library view. A model-supplied scope is not a new grant.
    access = external_access(request.headers)
    if access is None:
        return
    # Off the loop: this is the read the loop watchdog caught the loop inside,
    # 27 of the 60 stalls it reported. On this machine's database it answers
    # 299 rows from a covering index, about 52ms warm — enough, repeated and
    # serialised on the loop, to answer nobody for seconds.
    scope_started = time.monotonic()
    known = await asyncio.to_thread(_evidence_store(app).known_workspaces, user_id="local")
    # Measured because nothing else records it. The ~52ms figure is from a copy
    # of one database at one point in time; this says what it costs here, on a
    # database that has since grown, often enough to show whether the read is
    # still the one worth moving off the loop.
    diagnostic_event(
        level="info",
        component="core.api",
        event="recall.workspace_scope_resolved",
        outcome="succeeded",
        duration_ms=elapsed_ms(scope_started),
        # Keys are fixed by `diagnostic_log._DETAIL_KEYS`: an unlisted key
        # raises, and this sits on a request path, so an unknown key here turns
        # every external recall into a 500. `count` is the listed one that fits.
        details={"count": len(known)},
    )
    workspace_scopes = resolve_workspace_scopes(workspace_path, known)
    if scope != "all-apps" and scope not in workspace_scopes:
        raise HTTPException(status_code=403, detail="external_recall_scope_denied")
    access.require(tool, frozenset({scope}))


async def _request_answerer(
    app: FastAPI, request: Request, *, workspace_path: str | None = None
) -> MemoryAnswerer:
    answerer = _memory_answerer(app)
    access = external_access(request.headers)
    if access is None:
        return answerer
    # Per-request copy: concurrent hosts never alter the shared reader's scope.
    answerer = copy(answerer)
    original_filter = answerer.filter_memories
    answerer.filter_memories = lambda memories: tuple(
        memory
        for memory in original_filter(memories)
        if not access.scopes.isdisjoint(memory.scopes)
    )
    # The shared answerer's `resolve_workspace` reads `known_workspaces` inline,
    # and `MemoryAnswerer.retrieve` calls it straight from its own async body:
    # the same ~52ms read, on the loop, reached from /ask and /ask/stream. That
    # file is not ours to change, so the read is done here — off the loop — and
    # the answerer is given a callable that only resolves against the list
    # already fetched. `retrieve` calls it synchronously, so it has to stay
    # synchronous; what changes is that no database work happens inside it.
    # The scopes are the same, so the boundary enforced is the same.
    if getattr(answerer, "resolve_workspace", None) is not None:
        known = await asyncio.to_thread(_evidence_store(app).known_workspaces, user_id="local")
        answerer.resolve_workspace = lambda path: resolve_workspace_scopes(path, known)
    return answerer


async def _authorize_answer_generation(
    app: FastAPI,
    profile: ModelProfile,
    question: str,
    grounding: tuple[GovernedMemory, ...],
) -> Callable[[], Awaitable[None]]:
    # The explicit question is this operation's input, not a standing permission for history.
    if not question.strip() or not grounding:
        raise MemoryAnswerError("memory_answer_generation_not_authorized")
    if not await asyncio.to_thread(_config_store(app).model_content_allowed):
        raise MemoryAnswerError("memory_answer_generation_not_authorized")

    def check() -> None:
        if not _config_store(app).model_content_allowed():
            raise MemoryAnswerError("memory_answer_generation_not_authorized")
        if _model_profile_store(app).active_identity() != (
            profile.profile_id,
            profile.revision,
        ):
            raise MemoryAnswerError("memory_answer_generation_not_authorized")
        records: dict[str, EvidenceRecord] = {}
        for memory in grounding:
            current = _memory_store(app).get(memory.memory_id, user_id="local")
            if current != memory:
                raise MemoryAnswerError("memory_answer_generation_not_authorized")
            for identifier in memory.evidence_ids:
                record = _evidence_store(app).get(identifier)
                if record is None:
                    raise MemoryAnswerError("memory_answer_generation_not_authorized")
                records[identifier] = record

    async def recheck() -> None:
        await asyncio.to_thread(check)

    await recheck()
    return recheck


@asynccontextmanager
async def _permission_context(
    app: FastAPI,
    lease: ModelProcessingPermissionLease,
    secret: str | None,
    directory: Path,
) -> AsyncIterator[MemoryBridgeProtocol]:
    module = await asyncio.to_thread(import_module, "one_memory.hy_permission_context")
    budget = cast(ModelRequestBudget | None, app.state.model_request_budget)
    source_id = lease.records[0].source_id if lease.records else "manual"
    factory = module.permission_bridge_factory(
        lambda profile, api_key, data_dir: _memory_processing_supervisor(app)._bridge(
            profile,
            api_key,
            data_dir,
            scope_key=source_id,
        ),
        **({"http_transport": BudgetedModelTransport(budget)} if budget else {}),
    )
    async with factory(lease, secret, directory) as bridge:
        yield cast(MemoryBridgeProtocol, bridge)


class _LazyASGIApp:
    """Compatibility ASGI app that is built only when a server imports it.

    `create_app()` must not run at import time. The managed `serve()` entry
    imports `create_app` from this module before it can construct its own app
    with `manage_runtime=True`; an eager module-level app was therefore built
    first with the default `manage_runtime=False`. With retrieval index enabled
    that default app hit `retrieval_runtime_ownership_required` and killed the
    process before the managed app could bind a port.
    """

    def __init__(self) -> None:
        self._app: FastAPI | None = None

    def _resolve(self) -> FastAPI:
        if self._app is None:
            self._app = create_app()
        return self._app

    async def __call__(
        self,
        scope: MutableMapping[str, Any],
        receive: Callable[[], Awaitable[MutableMapping[str, Any]]],
        send: Callable[[MutableMapping[str, Any]], Awaitable[None]],
    ) -> None:
        await self._resolve()(scope, receive, send)


app = _LazyASGIApp()


def _granted_roots(
    source_ids: Iterable[str],
    grant_store: SourceGrantStore,
    config_store: ConfigStore,
) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for source_id in source_ids:
        if not config_store.sync_enabled(source_id):
            continue
        try:
            grant = grant_store.authorized_root(user_id="local", source_id=source_id)
        except SourceAccessError:
            continue
        if grant is not None:
            roots[source_id] = grant.root
    return roots


def _active_adapters(
    catalog: dict[str, SourceRegistration],
    grant_store: SourceGrantStore,
) -> dict[str, SourceAdapter]:
    adapters: dict[str, SourceAdapter] = {}
    for source_id, registration in catalog.items():
        try:
            grant = grant_store.authorized_root(
                user_id="local",
                source_id=source_id,
            )
            # A registered-but-paused source can hold a live grant and still
            # refuse to instantiate (openclaw fails closed with grant_required).
            # Guarding only `authorized_root` let that refusal escape to the
            # lifespan and take the whole Core down. Either refusal means "do
            # not assemble this source"; the pause itself stays in openclaw.py.
            if grant is None:
                continue
            adapters[source_id] = registration.create_adapter(grant)
        except SourceAccessError:
            continue
    return adapters


def _source_response(
    registration: SourceRegistration,
    receipt: SourceGrantReceipt | None,
    sync_enabled: bool,
    detection: SourceInstallState | None,
    home: Path,
    totals: tuple[int, int],
    *,
    candidate_change_reason: str | None = None,
) -> SourceResponse:
    location = inspect_source_location(
        registration.descriptor.source_id, registration.candidate.path
    )
    authorized = (
        receipt is not None
        and receipt.revoked_at is None
        and receipt.revision > 0
        and location.root_id is not None
        and _grant_root_matches(registration.candidate.path, receipt.root_id)
    )
    candidate_root_id = receipt.root_id if authorized and receipt else location.root_id
    return SourceResponse(
        source_id=registration.descriptor.source_id,
        display_name=registration.descriptor.display_name,
        storage_kinds=sorted(registration.descriptor.storage_kinds),
        available=location.state == "found",
        authorized=authorized,
        detected=(None if detection is None else detection.detected),
        session_count=(None if detection is None else detection.session_count),
        root_label=(
            receipt.root_label if receipt is not None and receipt.revoked_at is None else None
        ),
        candidate_path=_display_candidate_path(registration.candidate.path, home),
        candidate_kind=registration.candidate.kind,
        imported_messages=totals[0],
        imported_conversations=totals[1],
        capabilities=registration.capabilities,
        sync_completeness=registration.descriptor.capabilities.sync_completeness,
        grant_revision=receipt.revision if receipt else 0,
        root_id=receipt.root_id if receipt else None,
        sync_enabled=bool(authorized and sync_enabled),
        location_state=location.state,
        candidate_root_id=candidate_root_id,
        candidate_revision=registration.candidate.revision,
        candidate_change_allowed=(
            SourceCandidateStore.allowed(registration.descriptor.source_id)
            and candidate_change_reason is None
        ),
        candidate_change_reason=candidate_change_reason,
    )


def _follow_workbuddy_home(app: FastAPI, home: Path) -> None:
    registration = _catalog(app).get("workbuddy")
    if registration is None or registration.candidate.kind is not RootCandidateKind.DEFAULT:
        return
    try:
        _grant_store(app).follow_default_home(
            user_id="local",
            source_id="workbuddy",
            root=registration.candidate.path,
            default_homes=workbuddy_default_homes(home),
        )
    except (SourceAccessError, sqlite3.Error):
        logger.warning("workbuddy_home_follow_failed")


def _grant_root_matches(root: Path, recorded: str | None) -> bool:
    if recorded is None:
        return False
    try:
        return root_identity_matches(root, recorded)
    except SourceAccessError:
        return False


def _require_registration(app: FastAPI, source_id: str) -> SourceRegistration:
    registration = _catalog(app).get(source_id)
    if registration is None:
        raise HTTPException(status_code=404, detail="source_not_found")
    return registration


def _diagnostic_request_event(method: str, request_path: str) -> str | None:
    if method == "GET" or request_path in {"/api/v1/health", "/api/v1/runtime/identity"}:
        return None
    families = (
        ("/api/v1/import", "import.request"),
        ("/api/v1/sources", "source.request"),
        ("/api/v1/model", "model.request"),
        ("/api/v1/memor", "memory.request"),
        ("/api/v1/context", "recall.request"),
        ("/api/v1/recall", "recall.request"),
        ("/api/v1/continuous", "processing.request"),
    )
    return next(
        (event for prefix, event in families if request_path.startswith(prefix)),
        "api.request",
    )


def _diagnostic_request_details(request: Request, event: str | None) -> dict[str, str]:
    details = {"method": request.method}
    if event != "recall.request":
        return details
    source_id = request.headers.get("x-one-memory-host-id")
    if source_id and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", source_id):
        details["source_id"] = source_id
    return details


def _require_job(app: FastAPI, job_id: str) -> ImportJob:
    try:
        return _job_store(app).require(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="job_not_found") from None


async def _translate_transition(operation: Awaitable[ImportJob]) -> ImportJob:
    try:
        return await operation
    except InvalidJobTransitionError as error:
        raise HTTPException(
            status_code=409,
            detail="invalid_job_transition",
        ) from error


def _catalog(app: FastAPI) -> dict[str, SourceRegistration]:
    store = cast(SourceCandidateStore, app.state.source_candidate_store)
    return store.resolve_catalog(cast(dict[str, SourceRegistration], app.state.source_catalog))


def _grant_store(app: FastAPI) -> SourceGrantStore:
    return cast(SourceGrantStore, app.state.source_grant_store)


def _config_store(app: FastAPI) -> ConfigStore:
    return cast(ConfigStore, app.state.config_store)


def _source_processing_enabled(app: FastAPI, source_id: str) -> bool:
    if not _config_store(app).sync_enabled(source_id):
        return False
    try:
        return _grant_store(app).authorized_root(user_id="local", source_id=source_id) is not None
    except SourceAccessError:
        return False


def _job_store(app: FastAPI) -> ImportJobStore:
    return cast(ImportJobStore, app.state.import_job_store)


def _onboarding_store(app: FastAPI) -> OnboardingStore:
    return cast(OnboardingStore, app.state.onboarding_store)


def _supervisor(app: FastAPI) -> ImportSupervisor:
    return cast(ImportSupervisor, app.state.import_supervisor)


def _live_sync(app: FastAPI) -> PollingLiveSyncSupervisor:
    return cast(PollingLiveSyncSupervisor, app.state.live_sync_supervisor)


async def _refresh_live_watch(app: FastAPI) -> None:
    # A grant changes while the app is running. Serialize watch replacement
    # so concurrent connect/revoke requests cannot restore an older root set.
    async with app.state.live_sync_watch_lock:
        settings = cast(Settings, app.state.settings)
        live = _live_sync(app)
        roots = (
            await asyncio.to_thread(
                _granted_roots, live.source_ids, _grant_store(app), _config_store(app)
            )
            if settings.live_sync_enabled and settings.live_sync_watch_enabled
            else {}
        )
        old = cast(FileWatchLiveSync | None, app.state.live_sync_watch)
        if old is not None and old.roots == roots and not old.stopped_unexpectedly:
            return
        if old is not None:
            await old.shutdown()
        watch = (
            FileWatchLiveSync(
                roots=roots,
                supervisor=live,
                debounce_seconds=settings.live_sync_watch_debounce_seconds,
                min_interval_seconds=settings.live_sync_watch_min_interval_seconds,
            )
            if roots
            else None
        )
        app.state.live_sync_watch = watch
        live.set_watched_sources(watch.watching if watch is not None else ())
        if watch is not None:
            watch.start(prime=True)


def _recall_target(app: FastAPI) -> GovernedRecallTarget:
    return cast(GovernedRecallTarget, app.state.recall_target)


def _detection(app: FastAPI) -> dict[str, SourceInstallState]:
    """The two-level answer each explicit detect left behind.

    Keyed by source and dropped on every change that could invalidate it
    (authorize, revoke, candidate select, job start), so a cached count is
    never shown for a root the user has since changed.
    """
    return cast(dict[str, SourceInstallState], app.state.source_detection)


async def _onboarding_history_after(
    request: ConnectSourcesRequest,
    registrations: list[SourceRegistration],
    *,
    home: Path,
    now: datetime | None = None,
) -> datetime | None:
    """Return one shared cutoff when any selected harness has large history.

    The cutoff is decided before any history job is created, then persisted on
    every accepted job. This is deliberately global: independently limiting
    only the large source would leave different applications on incompatible
    timelines.
    """
    if not request.onboarding:
        return None
    revisions = request.source_candidate_revisions or {}
    for registration in registrations:
        source_id = registration.descriptor.source_id
        expected_root = request.source_roots.get(source_id)
        if expected_root is None:
            continue
        if (
            registration.candidate.kind is RootCandidateKind.ENVIRONMENT_OVERRIDE
            and not request.confirm_environment_overrides
        ):
            continue
        if (
            registration.candidate.kind is RootCandidateKind.USER_SELECTED
            and revisions.get(source_id) != registration.candidate.revision
        ):
            continue
        if not root_identity_matches(registration.candidate.path, expected_root):
            continue
        try:
            grant = await asyncio.to_thread(
                AuthorizedSourceRoot.grant,
                source_id=source_id,
                root=registration.candidate.path,
                user_confirmed=True,
                home=home,
            )
            adapter = registration.create_adapter(grant)
            count_sessions = cast(
                Callable[..., Awaitable[int]] | None,
                getattr(adapter, "count_sessions", None),
            )
            if count_sessions is not None:
                session_count = await count_sessions(limit=ONBOARDING_LARGE_HISTORY_SESSIONS + 1)
            else:
                pending = _install_state(adapter)
                if pending is None:
                    continue
                session_count = (await pending).session_count
        except (OSError, SourceAccessError):
            # A supported source whose metadata count cannot be completed is
            # treated conservatively. The later connection still owns the
            # precise authorization error, while onboarding never falls back
            # to an unbounded history import.
            session_count = ONBOARDING_LARGE_HISTORY_SESSIONS + 1
        if session_count > ONBOARDING_LARGE_HISTORY_SESSIONS:
            anchor = now or datetime.now(UTC)
            return anchor - ONBOARDING_HISTORY_WINDOW
    return None


def _install_state(adapter: object) -> Awaitable[SourceInstallState] | None:
    """Ask an adapter for the two-level answer when it has one.

    Every adapter still answers `detect()`; only the JSONL adapters expose the
    count. `getattr` rather than an isinstance import so a source module that
    does not know about `SourceInstallState` keeps working unchanged.
    """
    install_state = getattr(adapter, "install_state", None)
    return None if install_state is None else install_state()


def _model_profile_store(app: FastAPI) -> ModelProfileStore:
    return cast(ModelProfileStore, app.state.model_profile_store)


_RECENT_EXCERPT = 180


def _recent_conversation_item(record: EvidenceRecord) -> RecentConversationItem:
    text = record.verbatim
    file_origin = record.origin_meta.get(EvidenceOrigin.FILE)
    approximate = bool(
        file_origin is not None and file_origin.raw_meta.get("time_basis") == "file_mtime"
    )
    return RecentConversationItem(
        evidence_id=record.evidence_id,
        conversation_id=record.conversation_id,
        source_id=record.source_id,
        role=record.role,
        written_by=record.written_by.value,
        created_at=record.created_at,
        imported_at=record.imported_at,
        time_basis="import" if approximate or record.created_at is None else "conversation",
        excerpt=text[:_RECENT_EXCERPT],
        truncated=len(text) > _RECENT_EXCERPT,
    )


def _conversation_system_context(value: str) -> bool:
    text = value.lstrip().lower()
    return text.startswith(
        (
            "<codex_internal_context",
            "<environment_context>",
            "<heartbeat>",
            "/goal ",
            "# agents.md instructions",
        )
    )


def _secret_store(app: FastAPI) -> SecretStore:
    return cast(SecretStore, app.state.secret_store)


def _secret_store_detail(error: SecretStoreUnavailableError) -> str:
    """Why the store failed, as the code the frontend branches on.

    "Click Allow", "this is slow, try again", and "there is no store here" are
    three different instructions; sending one string for all of them is why a
    user whose Keychain needs one click was told the store was broken.
    """
    return str(getattr(error, "code", None) or SecretStoreErrorCode.STORE_UNAVAILABLE)


def _memory_store(app: FastAPI) -> MemoryGovernanceStore:
    return cast(MemoryGovernanceStore, app.state.memory_governance_store)


def _person_writes(app: FastAPI) -> PersonWrites:
    return cast(PersonWrites, app.state.person_writes)


def _memory_answerer(app: FastAPI) -> MemoryAnswerer:
    return cast(MemoryAnswerer, app.state.memory_answerer)


def _read_access(app: FastAPI) -> SourceReadAccess:
    return cast(SourceReadAccess, app.state.source_read_access)


def _map_cache(app: FastAPI) -> _MapCache:
    cache = cast("_MapCache | None", getattr(app.state, "memory_graph_cache", None))
    if cache is None:
        cache = _MapCache()
        app.state.memory_graph_cache = cache
    return cache


def _serialize_ask_event(event: dict[str, object]) -> dict[str, object]:
    memories = event.get("memories")
    if not isinstance(memories, tuple):
        return event
    return {
        **event,
        "memories": [memory.model_dump(mode="json") for memory in memories],
    }


def _sse(payload: dict[str, object]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _source_catalog(app: FastAPI) -> dict[str, SourceRegistration]:
    return _catalog(app)


def _memory_task_store(app: FastAPI) -> MemoryTaskStore:
    return cast(MemoryTaskStore, app.state.memory_task_store)


def _backfill_state(
    task: "asyncio.Task[None] | None",
    error: str | None,
) -> Literal["running", "done", "failed"]:
    if error is not None:
        return "failed"
    if task is None or task.done():
        return "done"
    return "running"


def _governed_graph_part(
    app: FastAPI,
    limit: int,
) -> tuple[
    list[GovernedMemory],
    list[GovernedMemory],
    dict[str, MemoryGraphNode],
    list[MemoryGraphEdge],
]:
    """The part of the map that comes from our own stores.

    Synchronous on purpose: the caller runs it in a thread, because every call
    in here blocks and there are hundreds of them.
    """
    confirmed = [
        memory
        for memory in _memory_store(app).list(user_id="local", limit=500)
        if memory.status is MemoryStatus.CONFIRMED
    ]
    # Only the memories the map draws are checked row by row. Verifying the
    # whole read first cost one freshness call plus one citation lookup per
    # memory: on a 1,226-memory library that passed the 5 s interrupt and the
    # map answered 503 every time, while the memory library, which verifies a
    # single page, stayed at 0.1 s. The count is the cheap one: no per-row
    # freshness, but a revoked source is still left out, in one batched
    # lookup (D22's settled total semantics).
    counted = _read_access(app).revoked_free(confirmed)
    projected = _read_access(app).memories(counted[:limit])
    nodes: dict[str, MemoryGraphNode] = {}
    edges: list[MemoryGraphEdge] = []
    conversations: dict[str, list[str]] = {}
    # Resolved once per request, outside the loop. `_source_catalog` re-reads
    # and re-resolves the whole catalog on every call, and this loop called it
    # once per distinct source of every drawn memory: 1,274 calls on a
    # 160-memory map, which is where most of the map's 5 s actually went.
    catalog = _source_catalog(app)
    # One read for every citation the map will draw. Looking each one up
    # separately is 1,274 round trips on a 160-memory map, and each takes a
    # turn through the pool: against a library that is also being written to,
    # that is what pushed the map past its five second budget (D89).
    records_by_id = {
        record.evidence_id: record
        for record in _evidence_store(app).get_many(
            [evidence_id for memory in projected for evidence_id in memory.evidence_ids]
        )
    }
    for memory in projected:
        memory_node_id = f"memory:{memory.memory_id}"
        nodes[memory_node_id] = MemoryGraphNode(
            node_id=memory_node_id,
            node_type="memory",
            label=memory.content,
            memory_id=memory.memory_id,
            kind=memory.kind,
            status=memory.status,
        )
        records = [
            record
            for evidence_id in memory.evidence_ids
            if (record := records_by_id.get(evidence_id)) is not None
        ]
        for conversation_id in sorted({record.conversation_id for record in records}):
            conversations.setdefault(conversation_id, []).append(memory_node_id)
        source_ids = {record.source_id for record in records}
        for source_id in sorted(source_ids):
            source_node_id = f"source:{source_id}"
            registration = catalog.get(source_id)
            nodes[source_node_id] = MemoryGraphNode(
                node_id=source_node_id,
                node_type="source",
                label=(registration.descriptor.display_name if registration else source_id),
                source_id=source_id,
            )
            edges.append(
                MemoryGraphEdge(
                    edge_id=f"{source_node_id}->{memory_node_id}",
                    source_node_id=source_node_id,
                    target_node_id=memory_node_id,
                    relation="evidence_source",
                    origin="provenance",
                )
            )
    # Chain memories that came out of one conversation. Every drawn pair is
    # a fact we can point at, which keeps the map readable before System 2
    # has induced anything.
    for _conversation_id, node_ids in sorted(conversations.items()):
        for left, right in zip(node_ids, node_ids[1:], strict=False):
            edges.append(
                MemoryGraphEdge(
                    edge_id=f"{left}->{right}:same_conversation",
                    source_node_id=left,
                    target_node_id=right,
                    relation="same_conversation",
                    origin="same_conversation",
                )
            )
    # Topics, from the tags HY put on each memory. Only where two or more
    # memories share one: a tag on a single memory draws a line to nothing
    # else, which is the same memory twice rather than a grouping, and a
    # young library is mostly tags like that.
    #
    # This is the axis the map was missing. Without it the only things with
    # more than one memory hanging off them are the source apps, so a library
    # that has just started importing looks like it is organised by which app
    # you were using — which is the one thing it is not supposed to be about.
    by_tag: dict[str, list[str]] = {}
    for memory in projected:
        for tag in memory.tags:
            by_tag.setdefault(tag, []).append(f"memory:{memory.memory_id}")
    for tag, memory_node_ids in sorted(by_tag.items()):
        if len(memory_node_ids) < 2:
            continue
        topic_node_id = f"topic:{tag}"
        nodes[topic_node_id] = MemoryGraphNode(
            node_id=topic_node_id,
            node_type="topic",
            label=tag,
        )
        for memory_node_id in memory_node_ids:
            edges.append(
                MemoryGraphEdge(
                    edge_id=f"{topic_node_id}->{memory_node_id}:shared_tag",
                    source_node_id=topic_node_id,
                    target_node_id=memory_node_id,
                    relation="shared_tag",
                    origin="shared_tag",
                )
            )
    return counted, projected, nodes, edges


async def _keep_loop_heartbeat(watchdog: LoopWatchdog) -> None:
    """The loop saying it is still able to run something."""
    # The watchdog already logs each stall with a stack, but a stack is prose:
    # "is the loop still blocked, and is it still the same thing" meant reading
    # log text by hand. Reported here as a count alongside the database
    # snapshot, so a fix has a before and an after that are both just numbers.
    # `stalls` is the watchdog's own list, so this adds an observer, not state.
    reported = 0
    while True:
        watchdog.beat()
        while len(watchdog.stalls) > reported:
            safe_stack = (
                watchdog.stall_stacks[reported] if len(watchdog.stall_stacks) > reported else ()
            )
            stalled_for = watchdog.stalls[reported]
            reported += 1
            diagnostic_event(
                level="warn",
                component="core.loop",
                event="loop.blocked",
                outcome="observed",
                error_code="event_loop_blocked",
                safe_stack=safe_stack,
                details={
                    # Keys are fixed by `diagnostic_log._DETAIL_KEYS`: an
                    # unlisted key raises, and this runs on the heartbeat, so
                    # a bad key would take the heartbeat down with it. `count`
                    # is the stall total, `unaccounted_ms` the longest one.
                    "count": reported,
                    "unaccounted_ms": round(stalled_for * 1000),
                },
            )
        await asyncio.sleep(watchdog.heartbeat_seconds)


async def _emit_resource_snapshots(interval_seconds: float = 60.0) -> None:
    while True:
        diagnostic_event(
            level="info",
            component="core.resources",
            event="core.resource_snapshot",
            outcome="observed",
            details=resource_snapshot(),
        )
        await asyncio.sleep(interval_seconds)


def _start_backfill(app: FastAPI) -> None:
    """(Re)start the catch-up on evidence that has no memory yet.

    A running one is cancelled first: whoever asks for another has changed the
    model profile whose tasks the old walk was recording.
    """
    running = cast("asyncio.Task[None] | None", getattr(app.state, "memory_backfill", None))
    if running is not None and not running.done():
        running.cancel()
    app.state.memory_backfill = watched_task(
        asyncio.get_running_loop(),
        lambda: _run_backfill(app),
        name="one-memory-memory-backfill",
    )


def _start_visibility_recompute(app: FastAPI) -> None:
    """Keep the stored visibility conclusions current, in the background.

    D93 A: writes mark the affected memories dirty; this is what answers
    them. It runs in Core, at the background service level, so it never
    competes with a read for the read connection.

    Not started while the switch is `exact`. Nothing reads the conclusions in
    that mode, so this would only walk every memory through the freshness
    check at startup: real work, holding the GIL, for a cache nobody
    consults. A build with the switch off must behave as it did before, and
    the triggers are still installed, so turning it on later finds a library
    already marked.
    """
    if memory_visibility.mode() == "exact":
        app.state.memory_visibility_task = None
        return
    running = cast("asyncio.Task[None] | None", getattr(app.state, "memory_visibility_task", None))
    if running is not None and not running.done():
        running.cancel()
    app.state.memory_visibility_task = watched_task(
        asyncio.get_running_loop(),
        lambda: _run_visibility_recompute(app),
        name="one-memory-visibility-recompute",
    )


async def _run_visibility_recompute(app: FastAPI) -> None:
    database_path = app.state.settings.data_dir / "one-memory.sqlite3"
    last_audit = 0.0
    while True:
        try:
            changed = await asyncio.to_thread(_visibility_batch, database_path)
        except sqlite3.Error as error:
            # Recorded, not swallowed: a silent zero here is indistinguishable
            # from "nothing was dirty", and conclusions would quietly go stale.
            _report_visibility_failure("recompute", error)
            changed = 0
        # Every ten minutes, recompute a random slice and compare. A trigger
        # that never fires for some change would leave a wrong conclusion in
        # place with nothing reporting it.
        if time.monotonic() - last_audit > 600:
            last_audit = time.monotonic()
            try:
                disagreements = await asyncio.to_thread(_visibility_audit, database_path)
            except sqlite3.Error as error:
                _report_visibility_failure("audit", error)
                disagreements = 0
            if disagreements:
                diagnostic_event(
                    level="warn",
                    component="memory.visibility",
                    event="memory_visibility.audit_mismatch",
                    outcome="failed",
                    details={"count": disagreements},
                )
        # Nothing to do: wait for a write to mark something dirty again.
        await asyncio.sleep(1.0 if changed else 15.0)


def _report_visibility_failure(phase: str, error: BaseException) -> None:
    diagnostic_event(
        level="warn",
        component="memory.visibility",
        event="memory_visibility.background_failed",
        outcome="failed",
        error_type=type(error).__name__,
        details={"phase": phase, "reason": str(error)[:200]},
    )


def _visibility_audit(database_path: Path) -> int:
    from one_memory.source_read_access import _MEMORY_READABLE

    with main_database(database_path).read() as db:
        db.execute("BEGIN")
        return memory_visibility.audit(db, readable_sql=_MEMORY_READABLE)


def _visibility_batch(database_path: Path) -> int:
    """One short transaction: the first backfill first, then what went dirty."""
    from one_memory.source_read_access import _MEMORY_READABLE

    # Applied here, in the thread that does the work: around the `to_thread`
    # call it leaves the worker under the caller's level, not the background
    # one.
    with service_level_context(current_service_level()), main_database(database_path).write() as db:
        memory_visibility.ensure_tables(db)
        batch = memory_visibility.missing_ids(db)
        if not batch:
            batch = memory_visibility.pending(db)
        if not batch:
            return 0
        changed = memory_visibility.recompute(db, batch, readable_sql=_MEMORY_READABLE)
        db.commit()
    return changed


async def _run_backfill(app: FastAPI) -> None:
    """Put every complete historical turn missing a write receipt onto the queue.

    This is recovery for the Evidence-first crash window and for databases
    imported before the unified queue existed. It does not call the legacy
    memory-task scheduler; the durable drain remains the only write executor.
    """
    app.state.memory_backfill_error = None
    if not app.state.settings.automatic_memory_processing:
        return
    if not await asyncio.to_thread(_config_store(app).model_content_allowed):
        return
    try:
        queue = app.state.memory_write_queue
        intake = app.state.memory_write_intake
        cursor: str | None = None
        while True:
            evidence_ids = await asyncio.to_thread(
                queue.unsubmitted_evidence_ids,
                limit=512,
                after_cursor=cursor,
            )
            if not evidence_ids:
                break
            seen_conversations: set[tuple[str, str, str | None]] = set()
            for evidence_id in evidence_ids:
                record = await asyncio.to_thread(_evidence_store(app).get, evidence_id)
                if record is None:
                    continue
                conversation_key = (record.source_id, record.conversation_id, record.workspace)
                if conversation_key in seen_conversations:
                    continue
                seen_conversations.add(conversation_key)
                if not _source_writes_on(app, record.source_id):
                    continue
                conversation = await asyncio.to_thread(
                    _evidence_store(app).list_conversation,
                    evidence_id,
                )
                for chunk in _conversation_chunks(
                    conversation,
                    max_turns=12,
                    completed_only=True,
                ):
                    if not any(item.evidence_id in evidence_ids for item in chunk):
                        continue
                    await asyncio.to_thread(intake.submit_records, chunk, priority="normal")
            cursor = evidence_ids[-1]
    except asyncio.CancelledError:
        raise
    except Exception as error:  # noqa: BLE001 - reported, not handled
        report_exception(
            "memory.scheduler",
            "memory_scheduler.catch_up_failed",
            error,
        )
        app.state.memory_backfill_error = repr(error)
        # Also logged with its traceback, because the string alone cannot be
        # acted on. When this really failed on this machine, `/health` said
        # `OperationalError('database is locked')` and that was the entire
        # record: the whole corpus went unextracted and there was nothing
        # anywhere saying which store, which method, or which query.
        logger.exception("memory catch-up failed, no further memories will be made")


def _evidence_excerpt(app: FastAPI, record: EvidenceRecord) -> EvidenceExcerpt:
    supervisor = _memory_processing_supervisor(app)
    task = _memory_task_store(app).task_for_evidence(record.evidence_id)
    window = extraction_window_for(supervisor.extraction_window_settings().window_preset)
    return EvidenceExcerpt(
        evidence_id=record.evidence_id,
        available=True,
        source_id=record.source_id,
        role=record.role,
        written_by=record.written_by,
        created_at=record.created_at,
        verbatim=sanitize_model_input(record.verbatim),
        extraction_state=resolve_extraction_state(
            task_status=task.status if task is not None else None,
            at=record.created_at,
            imported_at=record.imported_at,
            workspace=record.workspace,
            now=datetime.now(UTC),
            extraction_window=window,
        ),
    )


def _memory_processing_supervisor(app: FastAPI) -> MemoryProcessingSupervisor:
    return cast(
        MemoryProcessingSupervisor,
        app.state.memory_processing_supervisor,
    )


class _MatterLifecycleSource:
    """Read-only HY lifecycle view for `MatterStore`.

    A derived Matter dossier must be invalidated when HY has set one of its
    cited memories aside, even if the ledger mirror has not caught up. This
    source builds a `HyMemoryBridge` over the same `data_dir` the memory
    processing supervisor uses -- never a second store or writer -- and only
    opens its client on the first lifecycle read. `MatterStore` maps a `None`
    or unreadable result to a fail-closed `memory_changed`, so a not-yet-opened
    HY store can never turn a stale dossier into a ready one.

    The bridge is built **once** and cached: constructing one per read would
    create a fresh client/config lifecycle each time and could observe a
    different not-open state than the runtime's own bridge.
    """

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._bridge: HyMemoryBridge | None = None

    def _lifecycle_bridge(self) -> HyMemoryBridge:
        bridge = self._bridge
        if bridge is None:
            bridge = HyMemoryBridge(
                profile=builtin_retrieval_snapshot(),
                api_key=None,
                data_dir=self._data_dir,
            )
            self._bridge = bridge
        return bridge

    def lifecycle_now(self, user_id: str, ids: Collection[str]) -> HyLifecycle | None:
        bridge = self._lifecycle_bridge()
        return bridge.lifecycle_now(user_id, ids)


def _matter_lifecycle_source(app: FastAPI, data_dir: Path) -> _MatterLifecycleSource:
    return _MatterLifecycleSource(data_dir)


def _evidence_store(app: FastAPI) -> EvidenceStore:
    return cast(EvidenceStore, app.state.evidence_store)


def _source_writes_on(app: FastAPI, source_id: str) -> bool:
    """Whether this source's conversations may become memories."""
    if source_id == "one-memory-manual":
        receipt = _grant_store(app).receipt(user_id="local", source_id=source_id)
        return receipt is not None and receipt.revoked_at is None
    return _source_processing_enabled(app, source_id)


def _model_prober(app: FastAPI) -> ModelProber:
    return cast(ModelProber, app.state.model_prober)


def _display_candidate_path(path: Path, home: Path) -> str:
    try:
        return f"~/{path.expanduser().relative_to(home.expanduser()).as_posix()}"
    except ValueError:
        return str(path.expanduser())
