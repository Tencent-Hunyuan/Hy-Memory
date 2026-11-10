import asyncio
import hashlib
import importlib.util
import json
import logging
import os
import re
import sqlite3
import sys
import time
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Iterable,
    MutableMapping,
)
from contextlib import asynccontextmanager, nullcontext, suppress
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any, Literal, cast
from uuid import UUID

from fastapi import (
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
    model_validator,
)

from one_memory import __version__, memory_visibility
from one_memory.auth import local_api_guard, renderer_origins
from one_memory.background_work import (
    in_sdk_read,
    in_the_background,
    in_the_write_queue,
    shutdown_bulk_pool,
    warm_bulk_pool,
)
from one_memory.config import Settings, load_settings
from one_memory.context_routes import sdk_context_router
from one_memory.conversation_processing import (
    memory_task_timeout_seconds,
    turn_batches,
)
from one_memory.database_access import (
    MAIN_DATABASE_REPORT_SECONDS,
    checkpoint_idle_main_databases,
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
from one_memory.embedding_health import EMBEDDING_UNAVAILABLE, EmbeddingUnavailableError
from one_memory.embedding_spaces import EmbeddingSpaceStore
from one_memory.embedding_switch import EmbeddingSwitchJob, EmbeddingSwitchService
from one_memory.exception_reporting import (
    install_asyncio_exception_handler,
    install_threading_excepthook,
    report_exception,
    watched_task,
)
from one_memory.external_memory_access import (
    ExternalMemoryAccess,
    external_access,
    require_external_endpoint,
)
from one_memory.extraction_settings import ExtractionSettings, ExtractionSettingsStore
from one_memory.file_imports import FileImportStore, file_import_router
from one_memory.graph_open_guard import GraphOpenGuard, GraphOpenOutcome, GraphStoreStatus
from one_memory.hy_graph_admin import (
    GraphWidthDecision,
    GraphWidthMismatch,
    effective_memory_mode,
    graph_store_enabled,
    live_graph_path,
)
from one_memory.hy_runtime_config import (
    HY_MEMORY_READER,
    DiagnosticLogLevel,
    detailed_logging_enabled,
    diagnostic_log_level,
    set_detailed_logging,
    set_diagnostic_log_level,
)
from one_memory.hy_sdk_runtime import (
    AmbiguousMemoryIdError,
    SdkClientHandle,
    close_all_shared_runtimes,
    hold_stores_offline,
    release_stores,
    sdk_feature_defaults,
    sdk_relevance_floor,
)
from one_memory.hy_sdk_types import (
    HySdkError,
    HySdkMemoryPage,
    HySdkMemoryRecord,
    HySdkProfileVersions,
    HySdkRawHit,
    HySdkRawPage,
    HySdkRawRecord,
    HySdkReadError,
    HySdkUserProfile,
    LibraryHit,
    library_keyword_hits,
    parse_sdk_lane_search,
    parse_sdk_raw_search,
    parse_sdk_search,
    raw_turn_messages,
)
from one_memory.imports.access import (
    AuthorizedSourceRoot,
    SourceAccessError,
    resolved_source_root,
    root_identity_matches,
    source_root_identity,
)
from one_memory.imports.adapters import (
    ScanFailure,
    ScannedEvidence,
    ScanOptions,
    SourceAdapter,
    SyncCompleteness,
)
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
from one_memory.imports.grants import (
    SourceAuthorizationState,
    SourceGrantReceipt,
    SourceGrantStore,
)
from one_memory.imports.host_packages import CapabilityState, HostPackageCapabilities
from one_memory.imports.jobs import (
    PRUNE_BATCH_ROWS,
    ImportIssueRecord,
    ImportJob,
    ImportJobStore,
    ImportStatus,
    InvalidJobTransitionError,
)
from one_memory.imports.live_sync import LiveSyncHealth, PollingLiveSyncSupervisor
from one_memory.imports.live_watch import WATCH_MODE, FileWatchLiveSync
from one_memory.imports.locations import SourceLocation, inspect_source_location
from one_memory.imports.models import EvidenceRecord
from one_memory.imports.runner import live_sync_arrival
from one_memory.imports.sources.common import SourceInstallState
from one_memory.imports.sources.hermes import (
    HERMES_ROOT_IS_PROFILES_CONTAINER,
    hermes_root_conflict,
    hermes_root_is_profiles_container,
)
from one_memory.imports.sources.registry import (
    RootCandidateKind,
    SourceRegistration,
    SourceRootCandidate,
    build_source_catalog,
    workbuddy_default_homes,
)
from one_memory.imports.storage import EvidenceStore
from one_memory.imports.supervisor import ImportSupervisor
from one_memory.library_search_window import LibrarySearchWindows, window_key
from one_memory.library_upgrade import LibraryUpgradeError, prepare_library
from one_memory.loop_watchdog import LoopWatchdog
from one_memory.memory_additions import (
    LegacyMemoryAdditionReader,
    MemoryAdditionStore,
    memory_add_router,
)
from one_memory.memory_evolution import (
    EvolveModeError,
    EvolveRunReceipt,
    EvolveScheduler,
    EvolveService,
    EvolveStateStore,
    GraphBuildState,
    MemoryGraphBackfill,
    UserEvolveState,
)
from one_memory.memory_features import (
    FEATURES,
    PARAMETERS,
    SETTING_NAMES,
    FeatureCost,
    FeatureOverrides,
    FeatureReason,
    FeatureSide,
    MemoryFeatureName,
    MemoryFeatures,
    MemoryParameterName,
    MemorySettingName,
    SettingValue,
    effective_features,
    feature_activity,
    non_default_features,
    parameter_value,
)
from one_memory.memory_governance import (
    MemoryGovernanceStore,
)
from one_memory.memory_qa import MemoryAnswerError
from one_memory.memory_queue import (
    ExtractionWindowPreset,
)
from one_memory.memory_server.config_state import (
    MEMORY_MODES,
    ConfigStore,
    EvolveSchedule,
    MemoryMode,
)
from one_memory.memory_tasks import (
    MemoryTaskStatus,
    MemoryTaskStore,
)
from one_memory.model_backoff import BackedOffModelTransport, ModelBackoff
from one_memory.model_presets import seed_preset_profile
from one_memory.model_probe import ModelProber, ModelProbeResult
from one_memory.model_profiles import (
    ActiveModelProfileError,
    EmbeddingBaseUrlRequiredError,
    ModelCreationOperationConflictError,
    ModelCreationOperationLookupError,
    ModelCreationOperationReceipt,
    ModelProfile,
    ModelProfileConflictError,
    ModelProfileInput,
    ModelProfilePatch,
    ModelProfileStore,
    ModelProfileUnavailableError,
    ProviderKind,
)
from one_memory.model_request_audit import (
    AuditedModelTransport,
    ModelRequestAuditStore,
)
from one_memory.model_request_budget import BudgetedModelTransport, ModelRequestBudget
from one_memory.onboarding import (
    OnboardingCompletedError,
    OnboardingProgressUpdate,
    OnboardingState,
    OnboardingStore,
)
from one_memory.raw_turns import RawDisplayTurn, raw_display_turns, raw_prefix_added
from one_memory.recall import (
    GovernedRecallTarget,
    RecallReceipt,
    RecallRequest,
    SdkRecallTarget,
)
from one_memory.retired_authority import (
    RetiredEvidenceStore,
    RetiredGovernanceStore,
    RetiredMemoryTaskStore,
)
from one_memory.runtime import CoreRuntime
from one_memory.sdk_data_plane_migration import (
    MemoryAuthority,
    SdkDataPlaneMigrationStore,
)
from one_memory.sdk_memory_qa import (
    SdkAskMemory,
    SdkMemoryAnswer,
    SdkMemoryAnswerer,
)
from one_memory.sdk_memory_runtime import SdkMemoryRuntime
from one_memory.sdk_migration_log import error_chain_text, error_frames
from one_memory.sdk_read_handle import ActiveProfileReadHandle
from one_memory.sdk_search_options import (
    MEMORY_LAYERS,
    MEMORY_STATUSES,
    SdkSearchFilters,
    SearchLane,
    split_csv,
)
from one_memory.sdk_write_processor import SdkWriteCoordinator, sdk_write_in_flight
from one_memory.secrets import (
    KeyringSecretStore,
    SecretStore,
    SecretStoreErrorCode,
    SecretStoreUnavailableError,
)
from one_memory.semantic_deadline import SemanticMemoryHit
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
from one_memory.strict_query import forbid_unknown_query_params
from one_memory.target_schema import bootstrap_fresh_management_schema
from one_memory.ui_language import (
    SDK_LANGUAGE_AUTO,
    UI_LANGUAGES,
    UiLanguage,
    sdk_language,
)
from one_memory.ui_projection import (
    HomeProjectionCapabilities,
    HomeProjectionV1,
    home_capabilities,
    raw_list_filters,
    read_home_projection,
)
from one_memory.user_scope import (
    DEFAULT_USER_ID,
    MULTI_USER_SOURCES,
    USER_ID_PATTERN,
    UserId,
    lane_key,
    owns,
    split_lane,
)
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
    # Picking up work an earlier run left unfinished starts after readiness.
    # "running" is not degraded: Core is ready and answers throughout; the
    # failed steps are named so a stuck recovery is visible without a log.
    recovery: Literal["pending", "running", "done", "failed"] = "done"
    recovery_failed_steps: tuple[str, ...] = ()
    # An embedding switch or a startup merge of stray spaces, while one runs
    # (`{kind, state, percent, eta_s}`): the loading screen and the status
    # line read it. Reads are served throughout; model writes are paused.
    embedding_switch: dict[str, object] | None = None


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
    #: Why `authorized` is false, so the UI can ask for the one thing that
    #: would fix it instead of offering every recovery at once.
    authorization_state: SourceAuthorizationState = SourceAuthorizationState.OK


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


class SedimentationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: StrictBool


class SedimentationState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool
    #: What the switch means for the model bill and for what still runs. Read
    #: by the app so the setting can say so without hardcoding the wording.
    pending_retained: bool = True
    imports_unaffected: bool = True


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


class CreateImportJobV2Request(CreateImportJobRequest):
    """`POST /api/v2/import-jobs`: v1's body plus whose lane it runs on."""

    model_config = ConfigDict(extra="forbid")

    #: Only Hermes holds roots for a user other than `local`.
    user_id: UserId = DEFAULT_USER_ID


class SourceSyncNowRequest(BaseModel):
    """Optional body of `POST /api/v2/sources/{id}/sync`."""

    model_config = ConfigDict(extra="forbid")

    user_id: UserId = DEFAULT_USER_ID


class ConnectSourcesRequest(BaseModel):
    source_ids: tuple[str, ...] = Field(min_length=1, max_length=32)
    #: Whose grant this is. Only Hermes may connect a root for a user other
    #: than `local`: a Hermes profile is another person's history.
    user_id: UserId = DEFAULT_USER_ID
    #: A root chosen by the caller instead of the source's candidate, such as
    #: `~/.hermes/profiles/<name>`. Hermes only; Core computes its identity.
    root: str | None = Field(default=None, min_length=1, max_length=4096)
    confirm_environment_overrides: bool = False
    full_rescan: bool = False
    onboarding: bool = False
    enable_sync: bool = False
    request_id: str | None = Field(default=None, min_length=1, max_length=256)
    source_roots: dict[str, str] = Field(default_factory=dict)
    source_candidate_revisions: dict[str, StrictInt] | None = None
    import_upload_id: UUID | None = None


ONBOARDING_LARGE_HISTORY_USER_TURNS = 200
ONBOARDING_HISTORY_WINDOW = timedelta(days=3)


class ConnectSourcesResponse(BaseModel):
    accepted_source_ids: tuple[str, ...]
    failed_sources: dict[str, str]
    jobs: tuple[ImportJob, ...]
    request_id: str | None = None


SdkMemoryKind = Literal["chat", "experience", "fact"]
SdkMemoryVariant = Literal["working", "coding"]


class SdkMemoryUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Chat: the memory text. Experience: the solution. Fact: the statement.
    content: str = Field(min_length=1, max_length=200_000)
    # Optional namespace, as listed on the memory (`kind`, `variant`).
    kind: SdkMemoryKind | None = None
    variant: SdkMemoryVariant | None = None
    # Experiences only: also replace the task.
    task: str | None = Field(default=None, min_length=1, max_length=200_000)
    #: Whose memory this is; another user's id answers 404.
    user_id: UserId = DEFAULT_USER_ID


class SdkUserProfileResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The user whose profile this is: the `user_id` asked for, else `local`.
    user_id: str
    request_id: str
    profile: dict[str, object] | None
    elapsed_ms: float = Field(ge=0)


class SdkMemoryLibraryPage(HySdkMemoryPage):
    """`GET /api/v2/memory-library`: one SDK page and the user it was read for."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: str

    @classmethod
    def of(cls, page: HySdkMemoryPage, user_id: str) -> "SdkMemoryLibraryPage":
        return cls(**dict(page), user_id=user_id)


class SdkProfileVersionsResponse(HySdkProfileVersions):
    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: str


class SdkRawMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: user / assistant (tool and system if the SDK kept them); "" if unnamed.
    role: str
    content: str


class SdkRawItem(HySdkRawRecord):
    """One SDK raw turn as `/api/v2/raw` lists it.

    The SDK record's fields, plus what tracing an import needs: the turn's
    messages with their roles (parsed from the SDK's `[role]: text` lines),
    a `sha256:` hash of the stored content computed by Core (the SDK keeps
    none), and `created`, when the SDK stored it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    messages: tuple[SdkRawMessage, ...] = ()
    #: The one role of a single-message turn; None for a mixed turn.
    role: str | None = None
    content_hash: str
    created: datetime | None = None
    turn_id: str | None = Field(default=None, min_length=1, max_length=96)
    turn_index: int | None = Field(default=None, ge=0)
    turn_count: int | None = Field(default=None, ge=1)

    @classmethod
    def of(cls, record: HySdkRawRecord, turn: RawDisplayTurn | None = None) -> "SdkRawItem":
        content = turn.content if turn is not None else record.content
        raw_messages = turn.messages if turn is not None else raw_turn_messages(record.content)
        messages = tuple(SdkRawMessage(role=role, content=text) for role, text in raw_messages)
        roles = {message.role for message in messages}
        return cls(
            **record.model_dump(exclude={"content"}),
            content=content,
            messages=messages,
            role=next(iter(roles)) if len(roles) == 1 and "" not in roles else None,
            content_hash="sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest(),
            created=(
                datetime.fromtimestamp(record.gmt_created, tz=UTC)
                if record.gmt_created is not None
                else None
            ),
            turn_id=turn.turn_id if turn is not None else None,
            turn_index=turn.turn_index if turn is not None else None,
            turn_count=turn.turn_count if turn is not None else None,
        )


class SdkRawPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The user whose raw turns these are.
    user_id: str
    items: tuple[SdkRawItem, ...] = ()
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=500)
    unit: Literal["raw", "turn"] = "raw"
    next_cursor: str | None = None
    has_more: bool = False
    total_exact: bool = True
    #: Set when a time filter was applied in Core. For storage `raw`, `total`
    #: remains the SDK count; for product `turn`, it is the filtered turn count.
    time_filtered: bool = False


class SdkRawGraphItem(BaseModel):
    """Small, non-authoritative projection used to draw the bounded raw map."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    turn_id: str = Field(min_length=1, max_length=96)
    raw_id: str = Field(min_length=1)
    label: str = Field(max_length=240)
    source: str
    session_id: str
    memory_at: float | None = None
    gmt_created: float | None = None


class SdkRawGraphPage(BaseModel):
    """One bounded page from the raw-turn catalog."""

    model_config = ConfigDict(extra="forbid")

    user_id: str
    items: tuple[SdkRawGraphItem, ...] = ()
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=300)
    has_more: bool = False


class SdkLayerCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The user whose memories were counted.
    user_id: str
    #: Every SDK layer, zero when empty.
    layers: dict[str, int]
    total: int = Field(ge=0)
    #: The statuses counted, or None for the defaults (active; raw: active+shadow).
    statuses: tuple[str, ...] | None = None


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
    #: R10: the SDK mode and System 2 state. Optional so older readers and
    #: test doubles stay valid; Core always sends it.
    pipeline: "PipelineStatus | None" = None
    #: The running embedding switch or merge, `{job_id, kind, state, percent,
    #: eta_s}`; None when none runs. Model writes wait while it is set.
    embedding_switch: dict[str, object] | None = None
    #: Jobs held back because their source is off or its grant was revoked.
    #: Not in `pending` or the queue numbers: they run again, unchanged, once
    #: the source is turned back on. Nothing is cancelled or deleted.
    suspended: int = 0
    #: Until when model work is paused because the provider kept failing
    #: (bad key, wrong URL, model missing, provider down); None when it is
    #: not. Ends early on success or when the model configuration changes.
    llm_backoff_until: datetime | None = None
    #: The last provider failure behind the pause, as a stable code
    #: (`model_auth_failed`, `model_not_found`, `model_unreachable`, ...).
    llm_backoff_error: str | None = None
    #: The durable write drain, the queue's only consumer: `running`,
    #: `restarting` (it exited on an error and comes back after a pause) or
    #: `stopped`; None before startup recovery has started it.
    write_drain: Literal["running", "restarting", "stopped"] | None = None
    #: Why the drain last exited unexpectedly, as a stable code; None if never.
    write_drain_error: str | None = None


class MemoryModeState(BaseModel):
    """`GET /api/v1/settings/memory-mode`."""

    model_config = ConfigDict(extra="forbid")

    #: What SDK clients are built with now.
    mode: MemoryMode
    #: The saved setting (default pro).
    configured_mode: MemoryMode
    #: True once a person chose a mode, including explicitly choosing pro.
    explicitly_configured: bool
    #: `ONE_MEMORY_GRAPH_STORE` (dev) forces ultra over the setting.
    forced_by_env: bool
    modes: tuple[MemoryMode, ...] = MEMORY_MODES
    graph_build: GraphBuildState
    #: The ultra graph's own state: opening / ready / rebuilt after a
    #: quarantine (`graph_open_guard`).
    graph_store: GraphStoreStatus = Field(default_factory=GraphStoreStatus)


class MemoryModeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: MemoryMode


MemoryModeEffect = Literal[
    "earlier_raw_not_extracted",
    "higher_mode_memories_hidden",
    "existing_memories_added_to_graph",
    "evolve_stopped",
    "lite_search_stops",
    "graph_backfill_already_running",
]


class MemoryModeChange(MemoryModeState):
    """`PUT /api/v1/settings/memory-mode`: the new state and what it changes."""

    previous_mode: MemoryMode
    effects: tuple[MemoryModeEffect, ...] = ()


MemoryModeBlocker = Literal[
    "active_model_required",
    "embedding_maintenance_in_progress",
    "memory_lifecycle_busy",
    "graph_store_unavailable",
]
MemoryModeWarning = Literal["forced_by_env", "effective_no_change"]


class MemoryModePlan(BaseModel):
    """Shared validation result used by plan and PUT."""

    model_config = ConfigDict(extra="forbid")

    current: MemoryMode
    target: MemoryMode
    effective_target: MemoryMode
    capabilities: HomeProjectionCapabilities
    effects: tuple[MemoryModeEffect, ...] = ()
    warnings: tuple[MemoryModeWarning, ...] = ()
    blockers: tuple[MemoryModeBlocker, ...] = ()


class EvolveScheduleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: StrictBool | None = None
    time: str | None = Field(default=None, pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")


class EvolveRequest(BaseModel):
    """`POST /api/v2/evolve`."""

    model_config = ConfigDict(extra="forbid")

    user_id: UserId = DEFAULT_USER_ID
    #: Default `all-apps`, the agent MemHub writes every memory under.
    agent_id: str | None = Field(default=None, min_length=1, max_length=128)
    max_batches: int | None = Field(default=None, ge=1, le=1_000)


class EvolveOverview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: MemoryMode
    schedule: EvolveSchedule
    users: dict[str, UserEvolveState]
    running: tuple[EvolveRunReceipt, ...] = ()


class PipelineStatus(BaseModel):
    """`pipeline` in `/api/v1/memory-processing` and `memhub status --json`."""

    model_config = ConfigDict(extra="forbid")

    mode: MemoryMode
    configured_mode: MemoryMode
    forced_by_env: bool
    evolve: EvolveOverview
    #: Newest finished run across users, for a one-line status.
    last_evolve_at: datetime | None = None
    graph_build: GraphBuildState
    #: The ultra graph's own state (`graph_open_guard`): `opening` while the
    #: startup step runs, `rebuilding_after_quarantine` after a graph that
    #: kept taking Core down was renamed aside, else `ready` / `not_used`.
    graph_store: GraphStoreStatus = Field(default_factory=GraphStoreStatus)
    #: The SDK features (`memory_features`) whose value differs from the SDK
    #: default; empty while every feature is at its default. A parameter
    #: (`intention_dedup_threshold`) appears as its number.
    features: dict[MemorySettingName, SettingValue] = Field(default_factory=dict)
    #: The display language the App last reported (`ui_language`); null =
    #: none set (the App sends the language it shows, even following the system).
    ui_language: UiLanguage | None = None
    #: The SDK prompt language: `auto` (the SDK detects zh/en) or a code.
    sdk_language: str = SDK_LANGUAGE_AUTO


class UiLanguageState(BaseModel):
    """`GET /api/v1/settings/ui-language`."""

    model_config = ConfigDict(extra="forbid")

    #: What the App reported; null = none set (CLI/API-only, or `system`).
    ui_language: UiLanguage | None
    #: What SDK clients are built with: `auto` (no language set: the SDK
    #: detects zh/en, as before) or the code passed as `MemoryConfig.language`.
    sdk_language: str
    languages: tuple[UiLanguage, ...] = UI_LANGUAGES


class UiLanguageUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: A supported code, or null for "follow the system" (SDK auto-detect).
    ui_language: UiLanguage | None


class UiLanguageChange(UiLanguageState):
    """`PUT /api/v1/settings/ui-language`."""

    #: Whether the SDK language changed, so the SDK clients were rebuilt.
    rebuilt: bool


class MemoryFeatureState(BaseModel):
    """One feature in `GET /api/v1/settings/memory-features`."""

    model_config = ConfigDict(extra="forbid")

    name: MemoryFeatureName
    #: What clients are built with: `configured`, else `default`.
    enabled: bool
    #: What a person set; null = not set, the SDK default applies.
    configured: bool | None
    #: The SDK default (its code default, or its own env var when set).
    default: bool
    #: Whether it acts in the current mode; `reason` says why not.
    active: bool
    reason: FeatureReason | None
    side: FeatureSide
    requires_mode: Literal["pro", "ultra"]
    requires_feature: MemoryFeatureName | None
    cost: FeatureCost
    #: The `MemoryConfig` field it sets, and the SDK's own env var for it.
    sdk_field: str
    env: str
    description: str


class MemoryParameterState(BaseModel):
    """One numeric setting in `GET /api/v1/settings/memory-features`."""

    model_config = ConfigDict(extra="forbid")

    name: MemoryParameterName
    #: What clients are built with: `configured`, else `default`. An integer
    #: parameter (`agent_max_tokens`) is an int throughout.
    value: int | float
    #: What a person set; null = not set, the SDK default applies.
    configured: int | float | None
    #: The SDK default (its code default, or its own env var when set).
    default: int | float
    #: The range a `PUT` accepts, inclusive.
    minimum: int | float
    maximum: int | float
    #: Whether it acts: when `requires_feature` acts, or, without one, when
    #: the mode reaches `requires_mode`.
    active: bool
    reason: FeatureReason | None
    requires_feature: MemoryFeatureName | None
    requires_mode: Literal["pro", "ultra"] = "pro"
    sdk_field: str
    env: str
    description: str


class MemoryFeaturesState(BaseModel):
    """`GET /api/v1/settings/memory-features`."""

    model_config = ConfigDict(extra="forbid")

    mode: MemoryMode
    features: tuple[MemoryFeatureState, ...]
    parameters: tuple[MemoryParameterState, ...] = ()
    #: Same as `pipeline.features`: the values that differ from the defaults.
    non_default: dict[MemorySettingName, SettingValue]


class MemoryFeaturesChange(MemoryFeaturesState):
    """`PUT /api/v1/settings/memory-features`: the new state and what changed."""

    #: Features and parameters whose value changed; empty = nothing rebuilt.
    changed: tuple[MemorySettingName, ...] = ()


def memory_mode_effects(
    previous: str,
    new: str,
    *,
    graph_backfill: bool | None = None,
) -> tuple[MemoryModeEffect, ...]:
    """What a mode switch changes; the SDK migrates nothing on its own.

    `graph_backfill`: True when this switch started the one-time backfill,
    False when one was already running, None when not asked (no switch).
    """
    if previous == new:
        return ()
    rank = {"lite": 0, "pro": 1, "ultra": 2}
    effects: list[MemoryModeEffect] = []
    if previous == "lite":
        effects.extend(("earlier_raw_not_extracted", "lite_search_stops"))
    if rank[new] < rank[previous]:
        effects.append("higher_mode_memories_hidden")
    if previous == "ultra":
        effects.append("evolve_stopped")
    if new == "ultra" and graph_backfill is True:
        effects.append("existing_memories_added_to_graph")
    if new == "ultra" and graph_backfill is False:
        effects.append("graph_backfill_already_running")
    return tuple(effects)


MemoryProcessingSummary.model_rebuild()


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


class AskMemoryRequest(SdkSearchFilters):
    """Ask over one user's memories.

    The `SdkSearchFilters` fields (floors, rerank, source/session/agent/time
    and layer filters) reach the retrieval search. Scene, reader and search
    mode stay fixed: the answer cites chat memories read back by id.
    """

    model_config = ConfigDict(extra="forbid", frozen=False)

    question: str = Field(min_length=1, max_length=2_000)
    scope: str = Field(default="all-apps", min_length=1, max_length=512)
    # Host-injected project directory, same boundary as recall. Optional so
    # the web UI and older callers keep working unchanged.
    workspace_path: str | None = Field(default=None, max_length=4_096)
    #: Whose memories answer. Omitted, it is `local`, the App's library.
    user_id: UserId = DEFAULT_USER_ID


class SdkHomeSummary(BaseModel):
    schema_version: Literal[2] = 2
    #: The user the card was read for.
    user_id: str
    memory_count: int
    latest: HySdkMemoryRecord | None
    observed_at: datetime


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
    #: The user whose memories were mapped.
    user_id: str
    total_memories: int
    projected_memories: int
    nodes: tuple[MemoryGraphNode, ...]
    edges: tuple[MemoryGraphEdge, ...]
    hy_graph_available: bool = True


class SourceGrantView(BaseModel):
    """One `(user_id, source_id)` grant, as `GET /api/v1/source-grants` lists it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: str
    source_id: str
    root_label: str
    root_path: str
    authorized: bool
    revoked_at: datetime | None
    sync_enabled: bool


class SourceGrantList(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    grants: tuple[SourceGrantView, ...]


class SdkMigrationRequired(RuntimeError):
    """A serving Core was started on a home that is not on SDK authority."""

    code = "sdk_migration_required"

    def __init__(self) -> None:
        super().__init__(self.code)


def _record_startup_authority(
    data_dir: Path,
    database_path: Path,
    authority: MemoryAuthority,
    *,
    serving: bool,
) -> None:
    from one_memory.sdk_migration_log import authority_snapshot, write_migration_log

    refused = serving and authority is not MemoryAuthority.SDK
    write_migration_log(
        data_dir,
        writer="core-startup",
        event="core.refused_legacy" if refused else "core.authority_read",
        outcome="refused" if refused else "succeeded",
        code=SdkMigrationRequired.code if refused else None,
        details={"serving": serving, **authority_snapshot(database_path)},
    )


def create_app(
    settings: Settings | None = None,
    *,
    manage_runtime: bool = False,
    source_catalog: dict[str, SourceRegistration] | None = None,
    secret_store: SecretStore | None = None,
) -> FastAPI:
    runtime_settings = settings or load_settings()

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
            app.state.sdk_client_handle = None
            bootstrap_fresh_management_schema(database_path)
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
            # SDK authority may need its bridge before the rest of startup
            # selects recall targets. Model/secret management is control-plane
            # state, so initialize it before authority selection.
            app.state.secret_store = secret_store or KeyringSecretStore()
            app.state.model_profile_store = ModelProfileStore(database_path)
            # The live embedding space, pinned before any SDK client opens.
            # A home this build has not seen registers the space it already
            # uses, under the name it already has (`EmbeddingSpaceStore.bootstrap`).
            app.state.embedding_spaces = EmbeddingSpaceStore(database_path)
            active_identity = app.state.model_profile_store.active_identity()
            app.state.embedding_spaces.bootstrap(
                app.state.model_profile_store.snapshot(*active_identity)
                if active_identity is not None
                else None
            )
            app.state.sdk_data_plane_migrations = SdkDataPlaneMigrationStore(database_path)
            app.state.memory_authority = app.state.sdk_data_plane_migrations.authority()
            _record_startup_authority(
                runtime_settings.data_dir,
                database_path,
                app.state.memory_authority,
                serving=manage_runtime,
            )
            if manage_runtime and app.state.memory_authority is not MemoryAuthority.SDK:
                # No legacy serving, ever: a home that did not reach SDK
                # authority is refused rather than served in a second shape.
                # Only the maintenance Core (restore) may open such a home.
                raise SdkMigrationRequired()
            if app.state.memory_authority is MemoryAuthority.SDK:
                # The metadata repair after a cutover reads every bundle: it is
                # recovery work, so it runs after readiness (`recover_after_ready`)
                # while the SDK's stores are held offline (see there).
                app.state.memory_governance_store = RetiredGovernanceStore(database_path)
                app.state.evidence_store = RetiredEvidenceStore(database_path)
            else:
                app.state.memory_governance_store = MemoryGovernanceStore(
                    database_path, prepared=prepared
                )
                # Hold the rebuild until every startup write below has finished.
                app.state.memory_governance_store.start_content_candidate_backfill(
                    hold_until_startup_release=True
                )
                app.state.evidence_store = EvidenceStore(database_path)
            from one_memory.ui_session import UISessionStore

            app.state.ui_sessions = UISessionStore(instance_id=runtime_settings.instance_id)
            # Taken before anything else holds the store, so nothing HY drives
            # can take it: the person's writes come through the API alone.
            app.state.person_writes = app.state.memory_governance_store.claim_person_writes("api")
            app.state.memory_write_queue_path = database_path
            app.state.memory_write_evidence_store = (
                app.state.evidence_store
                if app.state.memory_authority is MemoryAuthority.LEGACY
                else None
            )
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
                evidence=app.state.memory_write_evidence_store,
                sdk_authority=app.state.memory_authority is MemoryAuthority.SDK,
            )
            app.state.memory_write_queue = memory_write_queue
            # Builds through 1.0.2 parked pre-invocation jobs behind the
            # retired model-content consent bit. The embedded SDK is trusted
            # now, so make those durable jobs claimable on the first startup.
            await asyncio.to_thread(
                memory_write_queue.readmit_parked,
                states=("waiting_for_permission",),
            )
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
            if app.state.memory_authority is MemoryAuthority.SDK:
                await asyncio.to_thread(app.state.evidence_store.release_completed_sdk_staging)
            sdk_handle = _sdk_client_handle(app)
            app.state.recall_target = SdkRecallTarget(
                semantic_search=lambda query: _search_sdk_memories(
                    sdk_handle,
                    query,
                    user_id="local",
                    agent_id="*",
                ),
                get_memory=lambda memory_id: _get_sdk_memory(sdk_handle, memory_id),
            )
            app.state.import_job_store = ImportJobStore(database_path)
            app.state.onboarding_store = OnboardingStore(database_path)
            app.state.history_import_gate_released = False
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
            app.state.sedimentation_lock = asyncio.Lock()
            app.state.memory_mode_lock = asyncio.Lock()
            _follow_workbuddy_home(app, runtime_settings.source_home)
            app.state.source_detection = {}

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
            app.state.memory_task_store = (
                RetiredMemoryTaskStore(database_path)
                if app.state.memory_authority is MemoryAuthority.SDK
                else MemoryTaskStore(database_path)
            )
            # Derived, unversioned: created if absent, and the version ledger
            # is left alone so an older package can still open this library.
            if app.state.memory_authority is MemoryAuthority.LEGACY:
                try:
                    await asyncio.to_thread(
                        memory_visibility.startup,
                        database_path,
                    )
                except sqlite3.Error as error:
                    app.state.memory_visibility_error = str(error)
            else:
                # A home an earlier build migrated still carries the grant
                # triggers whose body reads a dropped table (#233).
                try:
                    await asyncio.to_thread(
                        memory_visibility.drop_stale_triggers,
                        database_path,
                    )
                except sqlite3.Error as error:
                    app.state.memory_visibility_error = str(error)
            model_request_budget = (
                ModelRequestBudget(
                    database_path,
                    request_limit=runtime_settings.model_request_limit,
                    body_byte_limit=runtime_settings.model_request_body_byte_limit,
                    output_token_limit=runtime_settings.model_request_output_token_limit,
                )
                if runtime_settings.model_request_limit is not None
                else None
            )
            app.state.model_request_budget = model_request_budget
            app.state.model_request_audit_store = ModelRequestAuditStore(database_path)
            #: One pause for every model request while the provider keeps
            #: failing (`model_backoff`); reset when the configuration changes.
            app.state.model_backoff = ModelBackoff()
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
            app.state.sdk_write_coordinator = SdkWriteCoordinator(
                profiles=app.state.model_profile_store,
                secrets=app.state.secret_store,
                data_dir=runtime_settings.data_dir,
                # Outermost, so a request the pause refuses never went out and
                # leaves no audit row behind.
                transport_factory=lambda profile: (
                    lambda: BackedOffModelTransport(
                        app.state.model_backoff,
                        profile_id=f"{profile.profile_id}:{profile.revision}",
                        inner=AuditedModelTransport(
                            app.state.model_request_audit_store,
                            profile_id=profile.profile_id,
                            profile_revision=profile.revision,
                            provider_kind=str(profile.provider_kind),
                            model=profile.model,
                            inner=(
                                BudgetedModelTransport(model_request_budget)
                                if model_request_budget is not None
                                else None
                            ),
                        ),
                    )
                ),
                sedimentation_enabled=lambda: _config_store(app).sedimentation_enabled(),
                memory_mode=lambda: _memory_mode(app),
                memory_features=lambda: _memory_features(app),
                spaces=app.state.embedding_spaces,
                writes_open=lambda: _embedding_writes_open(app),
                sdk_language=lambda: _sdk_language(app),
            )
            app.state.sdk_memory_runtime = SdkMemoryRuntime(
                coordinator=app.state.sdk_write_coordinator,
                read_handle=_sdk_client_handle(app),
                write_queue=memory_write_queue,
                extraction_settings=app.state.extraction_settings_store,
                data_dir=runtime_settings.data_dir,
                memory_mode=lambda: _memory_mode(app),
                writes_open=lambda: _embedding_writes_open(app),
                # The claim gate's own source question, so what is counted as
                # waiting is what the drain would claim (#245).
                source_active=lambda source_key: _source_writes_on(app, source_key),
            )
            app.state.evolve_state_store = EvolveStateStore(runtime_settings.data_dir)
            app.state.evolve_service = EvolveService(
                coordinator=app.state.sdk_write_coordinator,
                state=app.state.evolve_state_store,
                memory_mode=lambda: _memory_mode(app),
            )
            app.state.evolve_scheduler = EvolveScheduler(
                service=app.state.evolve_service,
                schedule=lambda: _config_store(app).evolve_schedule(),
                users_with_memories=lambda: _users_with_memories(app),
                lifecycle_lock=app.state.memory_mode_lock,
                lifecycle_open=lambda: (
                    _embedding_writes_open(app) and not app.state.memory_graph_backfill.running
                ),
            )
            app.state.memory_graph_backfill = MemoryGraphBackfill(
                coordinator=app.state.sdk_write_coordinator,
                state=app.state.evolve_state_store,
                lifecycle_open=lambda: _embedding_writes_open(app),
            )
            # C2 owns the single production budget (handle 5s + reserve 250ms,
            # total 5.25s, lease 10s, renew 1s, claim 250ms). The composition
            # root only consumes it: no local formula, and the 250ms claim
            # budget is independent of the ordinary 5000ms writer wait and the
            # response-tail 250ms.
            drain_budget = production_drain_budget()

            async def reconcile_unknown_writes() -> None:
                # A write whose worker never learned how it ended (a restart,
                # a lapsed lease, the work ceiling) goes back into the queue
                # under a new SDK request id; the re-run first asks the SDK
                # whether the old one landed (`execute_job`). Nothing stays
                # `unknown` waiting for a person.
                await in_the_write_queue(
                    memory_write_queue.recover_unknown,
                    in_flight=sdk_write_in_flight,
                )

            async def admit_hy_invocation(
                source_id: str,
                requires_generation: bool,
                mark_invoked: Callable[[], Awaitable[bool]],
            ) -> bool:
                return await _admit_pipeline_invocation(
                    app,
                    source_id,
                    requires_generation,
                    mark_invoked,
                )

            memory_write_drain = DurableWriteDrain(
                memory_write_queue,
                execute=lambda job_id: execute_job(
                    job_id,
                    queue=memory_write_queue,
                    supervisor=app.state.sdk_write_coordinator,
                    evidence=app.state.evidence_store,
                    source_writes_permitted=lambda source_id: _source_writes_on(app, source_id),
                    admit_hy_invocation=admit_hy_invocation,
                    model_failures=app.state.model_backoff.attempt,
                ),
                execute_owned=lambda job_id, owner: execute_job(
                    job_id,
                    queue=memory_write_queue,
                    supervisor=app.state.sdk_write_coordinator,
                    evidence=app.state.evidence_store,
                    owner=owner,
                    source_writes_permitted=lambda source_id: _source_writes_on(app, source_id),
                    admit_hy_invocation=admit_hy_invocation,
                    model_failures=app.state.model_backoff.attempt,
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
                source_writes_permitted=lambda source_id: _sedimentation_claim_enabled(
                    app, source_id
                ),
                # The claim gate has to be the same question the write gate
                # asks, not a second answer to it. It used to be
                # `sync_enabled`, the per-app toggle, and `one-memory-manual` is
                # not a connected app: it has no entry in the configuration
                # document, so that predicate answers False for it while the
                # write gate admits it by its grant. Every manual note's job was
                # therefore skipped on every drain tick — the note stayed
                # "saved, waiting" with no task behind it, forever.
                source_claim_enabled=lambda source_id: _sedimentation_claim_enabled(app, source_id),
                model_claim_allowance=lambda: _model_claim_allowance(app),
            )
            app.state.memory_write_drain = memory_write_drain

            async def embedding_writes_drained() -> bool:
                """No model write runs: none past its claim, none holding a client."""
                await asyncio.to_thread(memory_write_queue.reclaim_expired)
                running, _ = await asyncio.to_thread(app.state.sdk_memory_runtime.active_depths)
                in_use = cast(SdkWriteCoordinator, app.state.sdk_write_coordinator).in_use()
                return running == 0 and in_use == 0

            async def quiesce_sdk_clients() -> Callable[[], None]:
                """Close every SDK client and hold the stores, for the graph re-embed."""
                deadline = time.monotonic() + 120
                while True:
                    await _read_handle(app).close_idle()
                    await app.state.sdk_write_coordinator.close_idle()
                    if hold_stores_offline(runtime_settings.data_dir):
                        return lambda: release_stores(runtime_settings.data_dir)
                    if time.monotonic() >= deadline:
                        raise RuntimeError("embedding_switch_clients_busy")
                    await asyncio.sleep(0.2)

            async def after_embedding_commit(job: EmbeddingSwitchJob) -> None:
                from one_memory.hy_graph_admin import forget_graph_widths

                forget_graph_widths()
                _read_handle(app).health.forget()
                if job.kind == "switch" and job.target_profile_id is not None:
                    profile = await asyncio.to_thread(
                        _model_profile_store(app).require,
                        job.target_profile_id,
                        _secret_store(app),
                    )
                    await asyncio.to_thread(
                        app.state.memory_task_store.rebind_unstarted_profile, profile
                    )
                await asyncio.to_thread(memory_write_queue.readmit_model_profile_waiters)
                _reset_model_backoff(app)
                _resume_sedimentation(app)
                _start_backfill(app)

            from one_memory.embedding_switch import default_reembed_backend

            app.state.embedding_switch = EmbeddingSwitchService(
                database_path=database_path,
                data_dir=runtime_settings.data_dir,
                profiles=app.state.model_profile_store,
                spaces=app.state.embedding_spaces,
                secrets=app.state.secret_store,
                backend=default_reembed_backend(runtime_settings.data_dir),
                drain_idle=embedding_writes_drained,
                on_commit=after_embedding_commit,
                quiesce_clients=quiesce_sdk_clients,
                # Only ultra keeps a graph; its width follows the space.
                graph_enabled=lambda: _memory_mode(app) == "ultra",
                worker_enabled=runtime_settings.embedding_switch_worker,
                drain_timeout_s=runtime_settings.embedding_switch_drain_timeout_seconds,
            )
            if app.state.memory_authority is not MemoryAuthority.SDK:
                # Only an SDK-authority home has SDK spaces to scan and merge.
                app.state.embedding_switch.release_scan_hold()
            # Started by `recover_after_ready`: a running drain claims jobs and
            # holds the single writer, which the rest of startup still needs.
            app.state.memory_write_drain_task = None
            app.state.import_job_pruning = None
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
                live = live_sync_arrival.get()
                conversation = await asyncio.to_thread(
                    app.state.evidence_store.list_conversation, record.evidence_id
                )
                # Legacy authority. The turn rule groups the whole conversation
                # from its start, so a batch's records and key never change:
                # history (end of file) writes it all, live writes complete
                # batches and leaves the rest to catch-up (`_run_backfill`).
                batches = turn_batches(conversation, flush=not live).batches
                for batch in _batches_settled_by(batches, record):
                    await asyncio.to_thread(
                        app.state.memory_write_intake.submit_records,
                        batch,
                        priority="live" if live else "normal",
                    )

            app.state.memory_addition_store = MemoryAdditionStore(
                app.state.evidence_store,
                (
                    LegacyMemoryAdditionReader(
                        app.state.memory_task_store,
                        app.state.memory_governance_store,
                    )
                    if app.state.memory_authority is MemoryAuthority.LEGACY
                    else None
                ),
                app.state.memory_write_intake,
                sdk_authority=app.state.memory_authority is MemoryAuthority.SDK,
                write_queue=memory_write_queue,
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
                    and app.state.memory_authority is MemoryAuthority.LEGACY
                    else None
                ),
                service_levels=app.state.service_levels,
                sdk_queue=memory_write_queue,
                sdk_authority=lambda: app.state.memory_authority is MemoryAuthority.SDK,
            )
            app.state.import_job_store.replace_never_published_snapshot_failures()
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
            ) + tuple(
                lane
                for lane in _other_user_lanes(
                    app.state.source_catalog, app.state.source_grant_store
                )
                if app.state.source_catalog[split_lane(lane)[1]].capabilities.live_sync
                is CapabilityState.IMPLEMENTED
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
            app.state.source_activation_tasks = {}
            app.state.live_sync_supervisor.set_watched_sources(
                watch_roots if watching and runtime_settings.live_sync_enabled else ()
            )
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
            app.state.memory_backfill = None
            app.state.memory_visibility_task = None
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
            # Readiness is "Core answers": the stores above are open and
            # /health, recall and the library work. Everything below picks up
            # work an earlier run left unfinished -- the post-cutover metadata
            # repair, the durable write queue, interrupted imports, live sync,
            # source reconcile. On a library with pending work that took longer
            # than the Runtime's readiness budget, so the Runtime killed a
            # healthy Core and the next one began the same recovery again. It
            # now runs only after readiness, one step at a time, and a failed
            # step is reported without taking readiness or the other steps
            # with it.
            app.state.recovery = "pending"
            app.state.recovery_failed_steps = ()

            async def recover_after_ready() -> None:
                app.state.recovery = "running"
                recovery_started = time.monotonic()
                failed: list[str] = []

                async def step(name: str, action: Callable[[], Awaitable[None]]) -> None:
                    step_started = time.monotonic()
                    try:
                        await action()
                    except asyncio.CancelledError:
                        raise
                    except Exception as error:
                        failed.append(name)
                        app.state.recovery_failed_steps = tuple(failed)
                        # Where it failed, as `file:line:function` frames of
                        # the exception chain, never its message: a bare
                        # `error_type=KeyError` named no location.
                        logger.warning(
                            "core_recovery_step_failed step=%s error_type=%s chain=%s",
                            name,
                            type(error).__name__,
                            error_chain_text(error),
                        )
                        diagnostic_event(
                            level="warn",
                            component="core.recovery",
                            event="core.recovery_step_failed",
                            outcome="failed",
                            error_type=type(error).__name__,
                            safe_stack=error_frames(error) or None,
                            duration_ms=elapsed_ms(step_started),
                            details={"phase": name},
                        )

                async def repair_sdk_metadata() -> None:
                    # `sdk-migrate` repairs right after its cutover and writes
                    # the receipt, so this normally finds it and returns. It
                    # stays for homes whose repair failed before that (no
                    # receipt). It opens its own Chroma client on the SDK's
                    # vector store, and Chroma's shared System is not safe
                    # beside another client: a first library request that
                    # opened the SDK client meanwhile lost it (KeyError).
                    # The stores were held offline before readiness, so any
                    # SDK client waits for this step.
                    from one_memory.hy_sdk_migration import (
                        repair_sdk_metadata_from_bundle,
                    )

                    if not app.state.sdk_stores_held:
                        logger.warning("sdk_metadata_repair_deferred reason=sdk_client_open")
                        return
                    # The hold is this step's from here; shutdown leaves it.
                    app.state.sdk_stores_held = False
                    work = asyncio.ensure_future(
                        asyncio.to_thread(
                            repair_sdk_metadata_from_bundle,
                            data_dir=runtime_settings.data_dir,
                            database_path=database_path,
                        )
                    )
                    # Released when the thread ends, not when this coroutine
                    # is cancelled: a cancelled await leaves the thread
                    # writing Chroma.
                    work.add_done_callback(lambda _work: release_stores(runtime_settings.data_dir))
                    repair = await asyncio.shield(work)
                    if repair.vector_rows or repair.coding_rows:
                        logger.info(
                            "sdk_metadata_repair_completed mapped=%d vector=%d coding=%d",
                            repair.mapped,
                            repair.vector_rows,
                            repair.coding_rows,
                        )

                async def resume_embedding_spaces() -> None:
                    try:
                        await app.state.embedding_switch.recover()
                    finally:
                        app.state.embedding_switch.start_retention()

                async def start_write_drain() -> None:
                    # Supervised: an unexpected exit is reported and the drain
                    # restarts, instead of stranding every queued write.
                    app.state.memory_write_drain_task = watched_task(
                        asyncio.get_running_loop(),
                        lambda: memory_write_drain.run_supervised(),
                        name="one-memory-durable-write-drain",
                    )

                async def start_import_job_pruning() -> None:
                    app.state.import_job_pruning = watched_task(
                        asyncio.get_running_loop(),
                        lambda: _prune_finished_import_jobs(app.state.import_job_store),
                        name="one-memory-import-job-pruning",
                    )

                async def resume_graph_backfill() -> None:
                    if (
                        app.state.graph_open_outcome == "quarantined"
                        and _memory_mode(app) == "ultra"
                    ):
                        # The quarantined graph is derived data: rebuild it
                        # from every user's memories, the normal ultra path.
                        users = await asyncio.to_thread(_known_user_ids, app)
                        app.state.memory_graph_backfill.start(users)
                        return
                    saved = app.state.evolve_state_store.graph_build()
                    if saved.state != "running":
                        return
                    if _memory_mode(app) != "ultra":
                        app.state.evolve_state_store.set_graph_build(GraphBuildState())
                        return
                    users = await asyncio.to_thread(_known_user_ids, app)
                    app.state.memory_graph_backfill.recover(users)

                async def start_evolve_schedule() -> None:
                    # Idle outside ultra: each tick asks the mode first.
                    app.state.evolve_scheduler.start()

                async def replay_pending_dispatch() -> None:
                    # A file's Evidence and its pending-dispatch row commit atomically,
                    # but the callback that turns them into a task runs after the
                    # transaction. A crash in that window leaves durable Evidence with
                    # no in-memory timer left to notice it; drain those rows once on
                    # startup through the same permission/debounce path as live work.
                    if (
                        runtime_settings.automatic_memory_processing
                        and app.state.memory_authority is MemoryAuthority.LEGACY
                    ):
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

                async def resume_imports() -> None:
                    await app.state.import_supervisor.resume_interrupted_off_loop()

                async def start_live_sync() -> None:
                    if runtime_settings.live_sync_enabled:
                        app.state.live_sync_supervisor.start()
                        if app.state.live_sync_watch is not None:
                            app.state.live_sync_watch.start(prime=True)

                async def start_legacy_catch_up() -> None:
                    _start_backfill(app)
                    _start_visibility_recompute(app)

                # Yield once first so the HTTP server finishes binding before
                # any of this asks the loop for time.
                await asyncio.sleep(0)
                try:
                    sdk = app.state.memory_authority is MemoryAuthority.SDK
                    if sdk:
                        # Before the drain: the repair restores metadata the
                        # queued writes are read back against.
                        await step("sdk_metadata_repair", repair_sdk_metadata)
                    # Before the drain too: resume an unfinished embedding
                    # switch (an App update may have stopped it), or scan for
                    # stray spaces and start merging them. Until this decides,
                    # the write gate holds every model write back; reads are
                    # served from the live space throughout.
                    if sdk:
                        await step("embedding_spaces", resume_embedding_spaces)
                        await step("graph_backfill", resume_graph_backfill)
                    await step("write_drain", start_write_drain)
                    if sdk and _memory_mode(app) == "lite":
                        # A previous pro run may have parked jobs while model
                        # sedimentation was off. Lite needs no generation model:
                        # readmit them in the background so startup can finish
                        # while raw conversations begin appearing.
                        _resume_sedimentation(app)
                    if sdk:
                        await step("evolve_schedule", start_evolve_schedule)
                    else:
                        await step("pending_dispatch_replay", replay_pending_dispatch)
                    await step("import_job_pruning", start_import_job_pruning)
                    await step("import_resume", resume_imports)
                    await step("live_sync", start_live_sync)
                    await step("source_reconcile", _reconcile_source_sync_from_config)
                    if not sdk:
                        await step("legacy_catch_up", start_legacy_catch_up)
                finally:
                    # Startup writes are done. Only now let the derived-content
                    # rebuild take the single write checkout: it is a long
                    # background job, and on a large library it would otherwise
                    # hold the writer while the recovery above still needs it
                    # (ISSUE-CONTENT-BACKFILL-BLOCKS-STARTUP).
                    app.state.memory_governance_store.release_content_backfill_startup_gate()
                app.state.recovery = "failed" if failed else "done"
                diagnostic_event(
                    level="warn" if failed else "info",
                    component="core.recovery",
                    event="core.recovery_finished",
                    outcome="failed" if failed else "succeeded",
                    duration_ms=elapsed_ms(recovery_started),
                    details={"count": len(failed)},
                )

            # The ultra graph's first open, as its own startup step before
            # readiness (`graph_open_guard`): a graph that freezes or kills
            # Core while opening used to do it after readiness, inside the
            # first SDK client, and the Runtime relaunched into the same file
            # forever. Here the Runtime sees a declared startup phase, and a
            # graph that took two starts down is quarantined and rebuilt.
            app.state.graph_open_guard = GraphOpenGuard(
                runtime_settings.data_dir,
                instance_id=runtime_settings.instance_id,
            )
            app.state.graph_open_outcome = await _open_graph_store_at_startup(app)

            app.state.recovery_task = None
            app.state.sdk_stores_held = False
            try:
                if app.state.memory_authority is MemoryAuthority.SDK:
                    # Before the first request can open an SDK client; the
                    # metadata repair step releases it.
                    app.state.sdk_stores_held = hold_stores_offline(runtime_settings.data_dir)
                diagnostic_event(
                    level="info",
                    component="core.lifecycle",
                    event="core.ready",
                    outcome="succeeded",
                    instance_id=runtime_settings.instance_id,
                    duration_ms=elapsed_ms(lifecycle_started),
                )
                app.state.recovery_task = watched_task(
                    asyncio.get_running_loop(),
                    recover_after_ready,
                    name="one-memory-post-ready-recovery",
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
                # First: recovery may be about to start what is stopped below.
                if app.state.recovery_task is not None:
                    app.state.recovery_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await app.state.recovery_task
                # Cancelled before it got that far, recovery never let the
                # rebuild go; a held rebuild would hold shutdown below too.
                app.state.memory_governance_store.release_content_backfill_startup_gate()
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
                if app.state.memory_backfill is not None:
                    app.state.memory_backfill.cancel()
                # Awaited, not just cancelled: they hold database connections,
                # and shutdown releases the data directory's lock.
                if app.state.memory_backfill is not None:
                    with suppress(asyncio.CancelledError):
                        await app.state.memory_backfill
                if app.state.memory_authority is MemoryAuthority.LEGACY:
                    await asyncio.to_thread(
                        app.state.memory_governance_store.wait_content_candidate_backfill,
                        5.0,
                    )
                source_activation_tasks = tuple(app.state.source_activation_tasks.values())
                for task in source_activation_tasks:
                    task.cancel()
                if source_activation_tasks:
                    await asyncio.gather(*source_activation_tasks, return_exceptions=True)
                if app.state.live_sync_watch is not None:
                    await app.state.live_sync_watch.shutdown()
                await app.state.live_sync_supervisor.shutdown()
                if app.state.sdk_stores_held:
                    # Recovery never reached the metadata repair step.
                    release_stores(runtime_settings.data_dir)
                    app.state.sdk_stores_held = False
                if app.state.import_job_pruning is not None:
                    app.state.import_job_pruning.cancel()
                    with suppress(asyncio.CancelledError):
                        await app.state.import_job_pruning
                await app.state.import_supervisor.shutdown()
                app.state.memory_write_drain.stop()
                if app.state.memory_write_drain_task is not None:
                    with suppress(asyncio.CancelledError):
                        await app.state.memory_write_drain_task
                await app.state.evolve_scheduler.shutdown()
                await app.state.evolve_service.shutdown()
                await app.state.memory_graph_backfill.shutdown()
                # The job stays where it is; the next start resumes it.
                await app.state.embedding_switch.shutdown()
                await app.state.sdk_memory_runtime.shutdown()
                await app.state.sdk_write_coordinator.close()
                if app.state.sdk_client_handle is not None:
                    await app.state.sdk_client_handle.close()
                    app.state.sdk_client_handle = None
                close_all_shared_runtimes()
                # Last: what has just been shut down was using it.
                shutdown_bulk_pool()
                log_main_database_stats("shutdown")
                # Leave an empty WAL behind: everything that wrote is stopped
                # by now. Busy (another process reading) is fine; the next
                # start's idle pass truncates it.
                await asyncio.to_thread(checkpoint_idle_main_databases)
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

    app = FastAPI(
        title="MemHub Core",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.memory_mode = lambda: _memory_mode(app)
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
    # Memory reads and writes refuse a query parameter they do not declare: an
    # ignored `user_id` would read as isolation that never happened.
    strict = [*protected, Depends(forbid_unknown_query_params)]
    app.include_router(memory_add_router(strict))
    app.include_router(file_import_router(protected))
    app.include_router(sdk_context_router(strict))

    # A4-01: scan-root configuration, so the CLI's `path` command has something
    # to read instead of refusing.
    from one_memory.scan_root_routes import scan_root_router

    app.include_router(scan_root_router(protected))
    # A3-01: the unified write entry. Mounted alongside the others so every
    # write path is reachable through one door rather than per-caller routes.
    from one_memory.memory_server.registry import WriteRegistry
    from one_memory.memory_server.routes import sdk_router as sdk_memory_write_router

    if getattr(app.state, "memory_write_registry", None) is None:
        app.state.memory_write_registry = WriteRegistry()
    app.include_router(sdk_memory_write_router(strict))
    from one_memory.call_event_routes import call_event_router

    app.include_router(call_event_router(protected))
    from one_memory.embedding_switch_routes import embedding_switch_router

    app.include_router(embedding_switch_router(protected))
    from one_memory.live_capture import live_capture_router

    def file_polled(request: Request, source_id: str) -> bool:
        """Whether live sync reads this source's own files, so the hook's copy is redundant."""
        registration = getattr(request.app.state, "source_catalog", {}).get(source_id)
        return (
            runtime_settings.live_sync_enabled
            and registration is not None
            and registration.capabilities.live_sync is CapabilityState.IMPLEMENTED
        )

    # Host hooks hand their turn over here instead of opening the database.
    app.include_router(live_capture_router(protected, file_polled=file_polled))

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
            "/api/v2/context/search",
            "/api/v2/memories/ask",
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
                    details={**diagnostic_details, "status_code": response.status_code},
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

    @app.exception_handler(EmbeddingUnavailableError)
    async def embedding_unavailable(_: Request, _error: EmbeddingUnavailableError) -> JSONResponse:
        # No lexical fallback: a search that cannot embed its query fails,
        # and says why, so the App can show it (top-right toast).
        return JSONResponse(status_code=503, content={"detail": EMBEDDING_UNAVAILABLE})

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

    @app.get("/api/v1/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        backfill = cast("asyncio.Task[None] | None", getattr(app.state, "memory_backfill", None))
        error = cast(str | None, getattr(app.state, "memory_backfill_error", None))
        status = _backfill_state(backfill, error)
        governance = getattr(app.state, "memory_governance_store", None)
        supervisor = getattr(app.state, "sdk_memory_runtime", None)
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
            recovery=getattr(app.state, "recovery", "pending"),
            recovery_failed_steps=getattr(app.state, "recovery_failed_steps", ()),
            embedding_switch=_embedding_switch_summary(app),
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

    @app.get("/api/v1/diagnostics/model-usage", dependencies=protected)
    def diagnostic_model_usage(
        since: datetime | None = Query(default=None),  # noqa: B008
        request_id: str | None = Query(  # noqa: B008
            default=None, min_length=1, max_length=512
        ),
        limit: int = Query(default=200, ge=1, le=2_000),  # noqa: B008
    ) -> dict[str, object]:
        """Return content-free precipitation usage for provider reconciliation."""

        normalized_since = (
            (since if since.tzinfo is not None else since.replace(tzinfo=UTC))
            if since is not None
            else None
        )
        report = app.state.model_request_audit_store.report(
            since=normalized_since.astimezone(UTC).isoformat()
            if normalized_since is not None
            else None,
            request_id=request_id,
            limit=limit,
        )
        return {"schema_version": 1, **report}

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

    @app.api_route(
        "/api/v1/onboarding/progress",
        methods=["PATCH", "PUT"],
        response_model=OnboardingState,
        dependencies=protected,
    )
    async def update_onboarding_progress(
        update: OnboardingProgressUpdate,
    ) -> OnboardingState:
        """Persist only resumable onboarding choices; secrets are never accepted."""
        try:
            return await asyncio.to_thread(_onboarding_store(app).update_progress, update)
        except OnboardingCompletedError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

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
        return await sources_for(DEFAULT_USER_ID)

    async def sources_for(user_id: str) -> list[SourceResponse]:
        """One user's sources. `local`'s are the App's; another user sees Hermes only."""
        detection = _detection(app) if user_id == DEFAULT_USER_ID else {}
        # Read in threads. The desktop polls this every few seconds, and both
        # reads below are synchronous SQLite — the totals across every source,
        # then a grant receipt per source. Made on the loop, as they were,
        # they blocked the whole process for as long as they took: the
        # watchdog caught this endpoint holding the loop for 5.5s.
        #
        # The totals are also not counted again for each ask: counting is a
        # pass over every row, and answering this took up to 6.70s during a
        # catch-up, nearly all of it the same count repeated.
        totals_by_source = await app.state.source_totals.by_source(user_id=user_id)
        # Status polling must not trigger history discovery: detect can walk an
        # entire host tree and recheck the grant in SQLite for every file, with
        # concurrent polls duplicating that work. Only the explicit detect
        # endpoint populates this diagnostic; None means not yet checked.
        # Authorization and root identity are still read afresh below.

        def describe() -> list[SourceResponse]:
            grant_store = _grant_store(app)
            config_store = _config_store(app)
            responses = []
            for source_id, registration in _catalog(app).items():
                if user_id != DEFAULT_USER_ID and source_id not in MULTI_USER_SOURCES:
                    continue
                if user_id != DEFAULT_USER_ID:
                    # Another user's Hermes is the profile root they were
                    # granted, not the App's candidate.
                    registration = _registration_at_grant(grant_store, registration, user_id)
                responses.append(
                    _source_response(
                        registration,
                        grant_store.receipt(user_id=user_id, source_id=source_id),
                        _lane_sync_enabled(config_store, lane_key(user_id, source_id)),
                        detection.get(source_id),
                        runtime_settings.source_home,
                        totals_by_source.get(source_id, (0, 0)),
                        candidate_change_reason=(
                            app.state.source_candidate_store.change_reason(registration)
                            if user_id == DEFAULT_USER_ID
                            else "multi_user_grant"
                        ),
                        authorization_state=grant_store.authorization_state(
                            user_id=user_id,
                            source_id=source_id,
                            candidate=registration.candidate.path,
                        ),
                    )
                )
            return responses

        return await asyncio.to_thread(describe)

    @app.get(
        "/api/v2/sources",
        response_model=list[SourceResponse],
        dependencies=strict,
    )
    async def list_sources_v2(
        user_id: str = Query(default=DEFAULT_USER_ID, pattern=USER_ID_PATTERN),
    ) -> list[SourceResponse]:
        """`GET /api/v1/sources` for one user: `local` (the default) reads the App's."""
        return await sources_for(user_id)

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
        await _refuse_overlapping_root(source_id, registration.candidate.path, DEFAULT_USER_ID)
        root_changed = prior is not None and prior.root_id != request.expected_root_id
        if root_changed:
            source_jobs = await asyncio.to_thread(
                partial(
                    _job_store(app).find_jobs,
                    source_id=source_id,
                    statuses=_UNFINISHED_IMPORT_STATUSES,
                )
            )
            if any(job.status is ImportStatus.RUNNING for job in source_jobs):
                raise HTTPException(status_code=409, detail="source_import_busy")
            for job in source_jobs:
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
            try:
                await _prepare_source_resume(source_id)
            except BaseException as error:
                raise _sync_refused("sync_prepare_failed") from error
        else:
            try:
                await _live_sync(app).stop_source(source_id)
            except BaseException as error:
                raise _sync_refused("sync_prepare_failed") from error
        try:
            await asyncio.to_thread(_config_store(app).set_sync, source_id, request.enabled)
        except BaseException as error:
            if request.enabled:
                await _live_sync(app).stop_source(source_id)
            else:
                await _prepare_source_resume(source_id)
                await _activate_source_resume(source_id)
            await _refresh_live_watch(app)
            raise _sync_refused("sync_config_write_failed") from error
        try:
            if request.enabled:
                try:
                    await _activate_source_resume(source_id)
                except BaseException as error:
                    raise _sync_refused("sync_resume_failed") from error
            try:
                await _refresh_live_watch(app)
            except BaseException as error:
                raise _sync_refused("sync_watcher_failed") from error
        except BaseException:
            # Rolls back the desired state and settles the watcher exactly as
            # before; the only change is that the raise above now carries a
            # code saying which stage failed.
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
        """Prepare catch-up work before committing the new desired state.

        `source_id` is a lane key, so another user's lane prepares that
        user's own cursors and history job.
        """
        await _live_sync(app).initialize_source(source_id, baseline_missing_files=True)
        user_id, bare_source_id = split_lane(source_id)
        active_history = await asyncio.to_thread(
            partial(
                _job_store(app).latest_job,
                user_id=user_id,
                source_id=bare_source_id,
                operation="history",
                statuses=_UNFINISHED_IMPORT_STATUSES,
            )
        )
        if active_history is not None:
            return
        await asyncio.to_thread(
            _job_store(app).create,
            user_id=user_id,
            source_id=bare_source_id,
            operation="history",
        )

    async def _activate_source_resume(source_id: str) -> None:
        """Run work prepared for a source after config.json has committed."""
        user_id, bare_source_id = split_lane(source_id)
        try:
            for job in await asyncio.to_thread(
                partial(
                    _job_store(app).find_jobs,
                    user_id=user_id,
                    source_id=bare_source_id,
                    statuses=(
                        ImportStatus.PENDING,
                        ImportStatus.PAUSED,
                        ImportStatus.INTERRUPTED,
                        ImportStatus.FAILED,
                    ),
                )
            ):
                if job.status is ImportStatus.PENDING:
                    await _supervisor(app).enqueue_off_loop(job.job_id, job=job)
                else:
                    await _supervisor(app).resume_off_loop(job.job_id)
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
    async def revoke_source(
        source_id: str,
        user_id: str = Query(default=DEFAULT_USER_ID, pattern=USER_ID_PATTERN),
    ) -> SourceGrantReceipt:
        _require_registration(app, source_id)
        if user_id != DEFAULT_USER_ID:
            return await _revoke_other_user_grant(source_id, user_id)
        receipt = await asyncio.to_thread(
            partial(_grant_store(app).receipt, user_id="local", source_id=source_id)
        )
        if receipt is None:
            raise HTTPException(status_code=404, detail="grant_not_found")
        for job in await asyncio.to_thread(
            partial(
                _job_store(app).find_jobs,
                source_id=source_id,
                statuses=_UNFINISHED_IMPORT_STATUSES,
            )
        ):
            await _supervisor(app).cancel(job.job_id)
        # Revocation is the user's node for "stop using this source" (D73):
        # park unfinished extraction instead of leaving it to fail later, so
        # re-authorizing resumes the same work.
        await asyncio.to_thread(
            app.state.memory_task_store.park_source_for_permission,
            source_id=source_id,
            error_code="hy_memory_source_revoked",
        )
        await _live_sync(app).stop_source(source_id)
        _supervisor(app).unregister_adapter(source_id)
        _detection(app).pop(source_id, None)
        receipt = await asyncio.to_thread(
            partial(_grant_store(app).revoke, user_id="local", source_id=source_id)
        )
        await asyncio.to_thread(_config_store(app).set_sync, source_id, False)
        await _refresh_live_watch(app)
        return receipt

    async def _revoke_other_user_grant(source_id: str, user_id: str) -> SourceGrantReceipt:
        """Stop reading another user's root. The App's switch is left alone."""
        if source_id not in MULTI_USER_SOURCES:
            raise HTTPException(status_code=422, detail="user_id_requires_multi_user_source")
        lane = lane_key(user_id, source_id)
        receipt = await asyncio.to_thread(
            partial(_grant_store(app).receipt, user_id=user_id, source_id=source_id)
        )
        if receipt is None:
            raise HTTPException(status_code=404, detail="grant_not_found")
        for job in await asyncio.to_thread(
            partial(
                _job_store(app).find_jobs,
                user_id=user_id,
                source_id=source_id,
                statuses=_UNFINISHED_IMPORT_STATUSES,
            )
        ):
            await _supervisor(app).cancel(job.job_id)
        await _live_sync(app).stop_source(lane)
        _supervisor(app).unregister_adapter(lane)
        receipt = await asyncio.to_thread(
            partial(_grant_store(app).revoke, user_id=user_id, source_id=source_id)
        )
        await _refresh_live_watch(app)
        return receipt

    @app.get(
        "/api/v1/source-grants",
        response_model=SourceGrantList,
        dependencies=protected,
    )
    async def list_source_grants() -> SourceGrantList:
        """Every grant of every user. `/api/v1/sources` stays `local`'s, as the App reads it."""

        def describe() -> SourceGrantList:
            config_store = _config_store(app)
            views = []
            for receipt, root_path in _grant_store(app).grants():
                lane = lane_key(receipt.user_id, receipt.source_id)
                authorized = receipt.revoked_at is None and receipt.revision > 0
                views.append(
                    SourceGrantView(
                        user_id=receipt.user_id,
                        source_id=receipt.source_id,
                        root_label=receipt.root_label,
                        root_path=_display_candidate_path(
                            Path(root_path), runtime_settings.source_home
                        ),
                        authorized=authorized,
                        revoked_at=receipt.revoked_at,
                        sync_enabled=authorized and _lane_sync_enabled(config_store, lane),
                    )
                )
            return SourceGrantList(grants=tuple(views))

        return await asyncio.to_thread(describe)

    @app.post(
        "/api/v1/import-jobs",
        response_model=ImportJob,
        dependencies=protected,
    )
    async def create_import_job(request: CreateImportJobRequest) -> ImportJob:
        return await create_job_on(request.source_id, request.operation, DEFAULT_USER_ID)

    async def create_job_on(
        source_id: str, operation: Literal["history", "sync"], user_id: str
    ) -> ImportJob:
        """Start an import on one user's lane of a source (`local`: the bare id)."""
        _require_registration(app, source_id)
        _require_multi_user_source(source_id, user_id)
        lane = lane_key(user_id, source_id)
        if lane not in _supervisor(app).adapters:
            raise HTTPException(status_code=409, detail="source_not_authorized")
        try:
            job = await asyncio.to_thread(
                _supervisor(app).create_job,
                lane,
                operation=operation,
            )
        except SourceAccessError as error:
            raise HTTPException(status_code=409, detail=error.code) from None
        await _supervisor(app).enqueue_off_loop(job.job_id, job=job)
        return job

    @app.post(
        "/api/v2/import-jobs",
        response_model=ImportJob,
        dependencies=strict,
    )
    async def create_import_job_v2(request: CreateImportJobV2Request) -> ImportJob:
        """`POST /api/v1/import-jobs` on one user's lane (Hermes only for another user)."""
        return await create_job_on(request.source_id, request.operation, request.user_id)

    async def _activate_connected_sources(
        request_id: str,
        source_ids: tuple[str, ...],
        *,
        enable_sync: bool,
    ) -> None:
        """Apply one committed connection intent without holding its HTTP response."""
        try:
            for source_id in source_ids:
                if enable_sync:
                    await _live_sync(app).initialize_source(
                        source_id,
                        baseline_missing_files=True,
                    )
                else:
                    await _live_sync(app).stop_source(source_id)
            if enable_sync:
                for source_id in source_ids:
                    await _activate_source_resume(source_id)
            await _refresh_live_watch(app)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            diagnostic_event(
                level="error",
                component="source.connection",
                event="source.connection.activation_failed",
                outcome="failed",
                **diagnostic_exception(error),
                details={"request_id": request_id, "source_ids": list(source_ids)},
            )
            raise

    def _schedule_source_activation(
        request_id: str,
        source_ids: tuple[str, ...],
        *,
        enable_sync: bool,
    ) -> None:
        tasks = app.state.source_activation_tasks
        existing = tasks.get(request_id)
        if existing is not None and not existing.done():
            return
        task = watched_task(
            asyncio.get_running_loop(),
            lambda: _activate_connected_sources(
                request_id,
                source_ids,
                enable_sync=enable_sync,
            ),
            component="source.connection",
            name=f"source-connection-{request_id}",
        )
        if task is None:
            return
        tasks[request_id] = task

        def forget(completed: asyncio.Future[None]) -> None:
            if tasks.get(request_id) is completed:
                tasks.pop(request_id, None)

        task.add_done_callback(forget)

    @app.post(
        "/api/v1/sources/connect",
        response_model=ConnectSourcesResponse,
        dependencies=protected,
    )
    async def connect_sources(
        request: ConnectSourcesRequest,
    ) -> ConnectSourcesResponse:
        if request.user_id != DEFAULT_USER_ID or request.root is not None:
            return await _connect_other_root(request)
        if request.onboarding:
            app.state.history_import_gate_released = False
        source_ids = tuple(dict.fromkeys(request.source_ids))
        registrations = [_require_registration(app, source_id) for source_id in source_ids]
        if request.request_id is None:
            raise HTTPException(status_code=409, detail="source_confirmation_required")
        for registration in registrations:
            if registration.descriptor.source_id in MULTI_USER_SOURCES:
                try:
                    resolved = await asyncio.to_thread(
                        app.state.source_candidate_store.resolve, registration
                    )
                except SourceCandidateError:
                    # The connect below reports the missing candidate itself.
                    continue
                await _refuse_overlapping_root(
                    registration.descriptor.source_id,
                    resolved.candidate.path,
                    DEFAULT_USER_ID,
                )
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
        existing_intent = await asyncio.to_thread(
            app.state.source_connection_store.get,
            request.request_id,
        )
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
        if existing_intent is not None:
            return ConnectSourcesResponse(**result.model_dump())
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
                configured.append(job.source_id)
        if configured:
            await asyncio.to_thread(
                _config_store(app).set_sync_many,
                {source_id: request.enable_sync for source_id in configured},
            )
            if request.enable_sync:
                for job in result.jobs:
                    if job.source_id in configured and job.status is ImportStatus.PENDING:
                        await _supervisor(app).enqueue_off_loop(job.job_id, job=job)
            _schedule_source_activation(
                request.request_id,
                tuple(configured),
                enable_sync=request.enable_sync,
            )
        return ConnectSourcesResponse(**result.model_dump())

    async def _refuse_overlapping_root(source_id: str, root: Path, user_id: str) -> None:
        """Refuse a Hermes root that would read another user's files.

        422 `hermes_root_is_profiles_container` for `.../profiles` itself;
        409 `hermes_root_overlaps_other_user` for a root equal to, holding or
        inside another user's active grant, unless one is the other's
        `profiles/<name>` (the scan never enters `profiles/`).
        """
        if source_id not in MULTI_USER_SOURCES:
            return
        if hermes_root_is_profiles_container(root):
            raise HTTPException(status_code=422, detail=HERMES_ROOT_IS_PROFILES_CONTAINER)

        def conflict_with_others() -> str | None:
            try:
                mine = resolved_source_root(root)
            except SourceAccessError:
                # Unreadable or missing: the grant that follows refuses it
                # with its own code.
                return None
            others = [
                Path(path)
                for receipt, path in _grant_store(app).grants()
                if receipt.source_id == source_id
                and receipt.user_id != user_id
                and receipt.revoked_at is None
            ]
            return hermes_root_conflict(mine, others)

        conflict = await asyncio.to_thread(conflict_with_others)
        if conflict is not None:
            raise HTTPException(
                status_code=422 if conflict == HERMES_ROOT_IS_PROFILES_CONTAINER else 409,
                detail=conflict,
            )

    async def _connect_other_root(request: ConnectSourcesRequest) -> ConnectSourcesResponse:
        """Grant one Hermes root to a user: `~/.hermes/profiles/<name>` for its own user.

        Only Hermes holds more than one person's history (one profile per
        person), so a non-`local` user or a caller-chosen root is refused for
        every other source. The grant is `(user_id, source_id)`; its cursors,
        jobs and live sync follow that key.
        """
        source_ids = tuple(dict.fromkeys(request.source_ids))
        if any(source_id not in MULTI_USER_SOURCES for source_id in source_ids):
            raise HTTPException(status_code=422, detail="user_id_requires_multi_user_source")
        if request.root is not None and len(source_ids) != 1:
            raise HTTPException(status_code=422, detail="source_root_requires_one_source")
        if request.user_id != DEFAULT_USER_ID and request.root is None:
            # The user is the root that is read: another user must name theirs,
            # or they would be handed `local`'s history.
            raise HTTPException(status_code=422, detail="source_root_required_for_user")
        if request.onboarding or request.import_upload_id is not None:
            raise HTTPException(status_code=422, detail="user_id_unsupported_for_request")
        if request.request_id is None:
            raise HTTPException(status_code=409, detail="source_confirmation_required")
        registrations = []
        source_roots = dict(request.source_roots)
        for source_id in source_ids:
            registration = _require_registration(app, source_id)
            if request.root is not None:
                root = Path(request.root)
                if not root.is_absolute():
                    raise HTTPException(status_code=422, detail="source_root_must_be_absolute")
                await _refuse_overlapping_root(source_id, root, request.user_id)
                # Named outright by the caller, like an environment override:
                # no saved candidate may replace it, and naming it confirms it.
                registration = replace(
                    registration,
                    candidate=SourceRootCandidate(
                        path=root, kind=RootCandidateKind.ENVIRONMENT_OVERRIDE
                    ),
                )
                try:
                    source_roots[source_id] = await asyncio.to_thread(source_root_identity, root)
                except SourceAccessError as error:
                    raise HTTPException(status_code=409, detail=error.code) from None
            registrations.append(registration)
        existing_intent = await asyncio.to_thread(
            partial(
                app.state.source_connection_store.get,
                request.request_id,
                user_id=request.user_id,
            )
        )
        try:
            result = await asyncio.to_thread(
                partial(
                    app.state.source_connection_store.connect,
                    request_id=request.request_id,
                    registrations=tuple(registrations),
                    source_roots=source_roots,
                    confirm_overrides=(
                        request.confirm_environment_overrides or request.root is not None
                    ),
                    full_rescan=request.full_rescan,
                    user_id=request.user_id,
                )
            )
        except ConnectionIntentError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        if existing_intent is not None:
            return ConnectSourcesResponse(**result.model_dump())
        for job in result.jobs:
            registration = _require_registration(app, job.source_id)
            grant = await asyncio.to_thread(
                partial(
                    _grant_store(app).authorized_root,
                    user_id=job.user_id,
                    source_id=job.source_id,
                )
            )
            if grant is None:
                continue
            lane = lane_key(job.user_id, job.source_id)
            _supervisor(app).register_adapter(
                registration.create_adapter(grant), user_id=job.user_id
            )
            _detection(app).pop(lane, None)
            if job.user_id != DEFAULT_USER_ID:
                if registration.capabilities.live_sync is CapabilityState.IMPLEMENTED:
                    _live_sync(app).ensure_source(lane)
                # Another user's grant syncs while it stands (see
                # `_lane_sync_enabled`); the App's switch is not touched.
                await _prepare_source_resume(lane)
                await _activate_source_resume(lane)
            elif request.enable_sync:
                await _prepare_source_resume(lane)
                await asyncio.to_thread(_config_store(app).set_sync, job.source_id, True)
                await _activate_source_resume(lane)
        await _refresh_live_watch(app)
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
    def list_import_jobs(
        user_id: str = Query(default=DEFAULT_USER_ID, pattern=USER_ID_PATTERN),
    ) -> list[ImportJob]:
        # One user's jobs: the App reads `local`'s and must not show another
        # user's Hermes import as its own. Every one of them, oldest first, as
        # before; finished live syncs past the newest twenty per source are
        # pruned (`prune_finished_sync_jobs`), so this list stays short.
        return _job_store(app).find_jobs(user_id=user_id)

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
        return await sync_lane(source_id, DEFAULT_USER_ID)

    async def sync_lane(source_id: str, user_id: str) -> ImportJob:
        _require_registration(app, source_id)
        _require_multi_user_source(source_id, user_id)
        # Asked for by hand, so it reports a job even when nothing was new;
        # `live_sync_unavailable` keeps meaning the source cannot be synced.
        job = await _live_sync(app).trigger_now(lane_key(user_id, source_id))
        if job is None:
            raise HTTPException(status_code=409, detail="live_sync_unavailable")
        return job

    @app.post(
        "/api/v2/sources/{source_id}/sync",
        response_model=ImportJob,
        dependencies=strict,
    )
    async def trigger_live_sync_v2(
        source_id: str,
        request: SourceSyncNowRequest | None = None,
    ) -> ImportJob:
        """`POST /api/v1/sources/{id}/sync` on one user's lane; the body is optional."""
        return await sync_lane(source_id, request.user_id if request else DEFAULT_USER_ID)

    @app.get(
        "/api/v2/import-jobs",
        response_model=list[ImportJob],
        dependencies=strict,
    )
    def list_import_jobs_v2(
        user_id: str = Query(default=DEFAULT_USER_ID, pattern=USER_ID_PATTERN),
    ) -> list[ImportJob]:
        return list_import_jobs(user_id=user_id)

    @app.get(
        "/api/v2/import-jobs/{job_id}",
        response_model=ImportJob,
        dependencies=strict,
    )
    def get_import_job_v2(
        job_id: str,
        user_id: str = Query(default=DEFAULT_USER_ID, pattern=USER_ID_PATTERN),
    ) -> ImportJob:
        """One job; another user's job answers exactly like a missing one."""
        job = _require_job(app, job_id)
        if job.user_id != user_id:
            raise HTTPException(status_code=404, detail="job_not_found")
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
        await asyncio.to_thread(_require_job, app, job_id)
        return await _translate_transition(_supervisor(app).pause(job_id))

    @app.post(
        "/api/v1/import-jobs/{job_id}/resume",
        response_model=ImportJob,
        dependencies=protected,
    )
    async def resume_import_job(job_id: str) -> ImportJob:
        job = await asyncio.to_thread(_require_job, app, job_id)
        if job.status in {ImportStatus.COMPLETED, ImportStatus.CANCELLED}:
            raise HTTPException(status_code=409, detail="job_not_resumable")
        try:
            return await _supervisor(app).resume_off_loop(job_id)
        except InvalidJobTransitionError:
            raise HTTPException(409, "invalid_job_transition") from None

    @app.post(
        "/api/v1/import-jobs/{job_id}/cancel",
        response_model=ImportJob,
        dependencies=protected,
    )
    async def cancel_import_job(job_id: str) -> ImportJob:
        await asyncio.to_thread(_require_job, app, job_id)
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
            created = _model_profile_store(app).create(request, _secret_store(app))
        except EmbeddingBaseUrlRequiredError:
            raise HTTPException(422, "embedding_base_url_required") from None
        except ModelCreationOperationConflictError:
            raise HTTPException(409, "model_creation_operation_conflict") from None
        except ModelCreationOperationLookupError:
            raise HTTPException(503, "model_creation_operation_unavailable") from None
        except SecretStoreUnavailableError as error:
            raise HTTPException(
                status_code=503,
                detail=_secret_store_detail(error),
            ) from None
        _reset_model_backoff(app)
        return created

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
        _refuse_embedding_edit_during_switch(app, profile_id, request)
        try:
            updated = _model_profile_store(app).update(
                profile_id,
                request,
                _secret_store(app),
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="model_profile_not_found") from None
        except ModelProfileConflictError:
            raise HTTPException(status_code=409, detail="model_profile_conflict") from None
        except EmbeddingBaseUrlRequiredError:
            raise HTTPException(status_code=422, detail="embedding_base_url_required") from None
        except SecretStoreUnavailableError as error:
            raise HTTPException(
                status_code=503,
                detail=_secret_store_detail(error),
            ) from None
        # A saved key, URL or model is what the paused provider was waiting
        # for: try it now, not after the rest of the pause.
        _reset_model_backoff(app)
        return updated

    @app.post(
        "/api/v1/model-profiles/{profile_id}/activate",
        response_model=None,
        dependencies=protected,
    )
    async def activate_model_profile(profile_id: str) -> Any:
        # Asynchronous for the sake of what follows the activation: scheduling
        # work needs the event loop this coroutine runs on, and a synchronous
        # path operation is handed to a worker thread that has none. Activating
        # with any evidence already imported — the ordinary order, since
        # importing comes first — used to fail here outright.
        #
        # Activation never changes the embedding space by itself. A profile
        # with the live space's model and width (a generation-only change, or
        # another endpoint for the same model) activates as before. Another
        # embedding on a home with no vector at all moves the space in the
        # same transaction. Anything else answers 409 `embedding_switch_required`
        # with the plan: `POST /api/v1/embedding-switches` carries it out.
        from one_memory.embedding_switch import EmbeddingSwitchError
        from one_memory.embedding_switch_routes import refusal

        switch = _embedding_switch(app)
        try:
            # The refusals activation always made come first, with their codes.
            await asyncio.to_thread(_store_activation_preflight, app, profile_id)
            plan = await switch.plan(profile_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="model_profile_not_found") from None
        except ModelProfileUnavailableError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        except EmbeddingSwitchError as error:
            if error.code == "model_profile_not_found":
                raise HTTPException(status_code=404, detail="model_profile_not_found") from None
            return refusal(error.code, plan=error.plan)
        if "embedding_switch_running" in plan.blockers:
            return refusal("embedding_switch_running", plan=plan)
        try:
            if plan.needed:
                return refusal("embedding_switch_required", plan=plan)
            if plan.reason == "empty_home":
                await asyncio.to_thread(switch.activate_empty_home, profile_id)
                profile = await asyncio.to_thread(
                    _model_profile_store(app).require, profile_id, _secret_store(app)
                )
            else:
                profile = await asyncio.to_thread(
                    _model_profile_store(app).activate,
                    profile_id,
                    _secret_store(app),
                )
        except KeyError:
            raise HTTPException(status_code=404, detail="model_profile_not_found") from None
        except ModelProfileUnavailableError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        except ModelProfileConflictError:
            raise HTTPException(status_code=409, detail="model_profile_conflict") from None
        except EmbeddingSwitchError as error:
            return refusal(error.code)
        # D75: work queued for the previous model continues with this one.
        # Only tasks that never reached the model are re-bound.
        await asyncio.to_thread(app.state.memory_task_store.rebind_unstarted_profile, profile)
        _reset_model_backoff(app)
        # A missing profile is an event-driven wait, not a provider outage.
        # Release those durable rows only now that activation has changed the
        # condition; the ordinary drain poll will claim them without a restart.
        await asyncio.to_thread(app.state.memory_write_queue.readmit_model_profile_waiters)
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

    @app.get(
        "/api/v2/home-projection",
        response_model=HomeProjectionV1,
        dependencies=strict,
    )
    async def home_projection(
        user_id: str = Query(default=DEFAULT_USER_ID, pattern=USER_ID_PATTERN),
    ) -> HomeProjectionV1:
        """The mode-neutral homepage read model owned by Core."""
        try:
            mode = _memory_mode(app)
            handle = _sdk_client_handle(app)
            if isinstance(handle, ActiveProfileReadHandle):
                client = await handle.client_for_mode(mode)
            else:
                client = await handle.client()
            return await read_home_projection(
                client,
                user_id=user_id,
                mode=mode,
            )
        except Exception as error:
            raise _sdk_http_error(error, "home_projection_read_failed") from None

    @app.get(
        "/api/v2/home-summary",
        response_model=SdkHomeSummary,
        dependencies=strict,
    )
    async def sdk_home_summary(
        user_id: str = Query(default=DEFAULT_USER_ID, pattern=USER_ID_PATTERN),
    ) -> SdkHomeSummary:
        """The home card: the memory page's count and its newest memory.

        The count is `memory_stats` total (chat + working + coding
        experiences, facts not counted), the same set the memory page lists
        with scene="all". Without a scene `list_memories` counts chat only,
        which showed 55 on the home card against 94 on the memory page.
        """
        try:
            client = await _sdk_client_handle(app).client()
            stats = await asyncio.to_thread(
                client.memory_stats,
                user_id=user_id,
                statuses=["active"],
            )
            raw = await asyncio.to_thread(
                client.list_memories,
                user_id=user_id,
                scene="all",
                statuses=["active"],
                limit=1,
                offset=0,
                order="desc",
            )
            page, skipped = HySdkMemoryPage.from_sdk_skipping_invalid(raw, limit=1, offset=0)
            _log_skipped_sdk_memories(skipped)
        except Exception as error:
            raise _sdk_http_error(error, "hy_memory_list_failed") from None
        total = stats.get("total") if isinstance(stats, dict) else None
        return SdkHomeSummary(
            user_id=user_id,
            memory_count=total if isinstance(total, int) and total >= 0 else page.total,
            latest=page.memories[0] if page.memories else None,
            observed_at=datetime.now(UTC),
        )

    @app.get(
        "/api/v2/memory-library",
        response_model=SdkMemoryLibraryPage,
        dependencies=strict,
    )
    async def sdk_memory_library(
        statuses: list[str] = Query(default=["active"]),  # noqa: B008
        source: str | None = Query(default=None, min_length=1),
        query: str | None = Query(default=None, max_length=512),
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=LIBRARY_PAGE_SIZE, ge=1, le=500),
        window_id: str | None = Query(default=None, min_length=1, max_length=64),
        scene: Literal["all", "chat", "working", "coding"] = Query(default="all"),
        user_id: str = Query(default=DEFAULT_USER_ID, pattern=USER_ID_PATTERN),
        layers: list[str] | None = Query(default=None),  # noqa: B008
        layer: list[str] | None = Query(default=None),  # noqa: B008
        exclude_sources: list[str] | None = Query(default=None),  # noqa: B008
        session_ids: list[str] | None = Query(default=None),  # noqa: B008
        agent_ids: list[str] | None = Query(default=None),  # noqa: B008
    ) -> SdkMemoryLibraryPage:
        """One page of memories across chat, working and coding.

        Without `query`: `list_memories(scene="all")`, newest update first;
        `total` is the SDK's count across the three scenes. `scene` narrows
        the page to one scene: chat lists the chat store, working/coding list
        that scene's experiences (the SDK lists experiences only, all active).

        With `query`: keyword search over one SDK relevance window (see
        `_library_search_page`). The first request (no `window_id`) searches
        and returns a `window_id`; later pages pass it back with their offset
        and are cut from the same matches, so pages never overlap or skip. A
        `window_id` that expired or was searched with other filters searches
        again and answers page 1 with `window_reset: true`.

        `layers` (or `layer`), `statuses`, `exclude_sources`, `session_ids`
        and `agent_ids` may each repeat or be comma separated. Layers exist in
        the chat store only, so a layer filter lists the chat store (graph
        layers included) and refuses `scene=working|coding`. `l1_raw` belongs
        to `/api/v2/raw`.
        """
        statuses = _library_values(statuses, MEMORY_STATUSES, "memory_status_unknown")
        chosen_layers = _library_values(
            [*(layers or ()), *(layer or ())], MEMORY_LAYERS, "memory_layer_unknown"
        )
        if chosen_layers and scene in {"working", "coding"}:
            raise HTTPException(status_code=422, detail="memory_layers_require_chat_scene")
        filters = {
            name: values
            for name, values in (
                ("layers", chosen_layers),
                ("exclude_sources", split_csv(exclude_sources)),
                ("session_ids", split_csv(session_ids)),
                ("agent_ids", split_csv(agent_ids)),
            )
            if values
        }
        try:
            client = await _sdk_client_handle(app).client()
            search_query = (query or "").strip()
            if search_query:
                if set(statuses) != {"active"}:
                    raise HTTPException(
                        status_code=422,
                        detail="sdk_search_supports_active_status_only",
                    )
                searched = await _library_search_page(
                    app,
                    client,
                    query=search_query,
                    source=source,
                    statuses=list(statuses),
                    offset=offset,
                    limit=limit,
                    window_id=window_id,
                    scene=scene,
                    user_id=user_id,
                    filters=filters,
                )
                return SdkMemoryLibraryPage.of(searched, user_id)
            raw = await asyncio.to_thread(
                client.list_memories,
                user_id=user_id,
                # Layers are chat-store rows: `scene="all"` would still add
                # every working/coding experience to a layer-filtered page.
                scene=None if chosen_layers else scene,
                statuses=list(statuses),
                include_sources=[source] if source else None,
                offset=offset,
                limit=limit,
                order="desc",
                **filters,
            )
            page, skipped = HySdkMemoryPage.from_sdk_skipping_invalid(
                raw, limit=limit, offset=offset
            )
            _log_skipped_sdk_memories(skipped)
            return SdkMemoryLibraryPage.of(page, user_id)
        except HTTPException:
            raise
        except Exception as error:
            raise _sdk_http_error(error, "hy_memory_list_failed") from None

    @app.get(
        "/api/v2/memory-library/layers",
        response_model=SdkLayerCounts,
        dependencies=strict,
    )
    async def sdk_memory_layers(
        statuses: list[str] | None = Query(default=None),  # noqa: B008
        user_id: str = Query(default=DEFAULT_USER_ID, pattern=USER_ID_PATTERN),
    ) -> SdkLayerCounts:
        """How many memories each SDK layer holds for one user.

        One `list_memories(layers=[layer], limit=1)` per layer, read for its
        total only. Without `statuses` every layer counts its active rows,
        and `l1_raw` counts active and shadow raws: pro shadows a raw once
        it is extracted, and both are the user's kept conversations.
        """
        chosen = _library_values(statuses or [], MEMORY_STATUSES, "memory_status_unknown")
        try:
            client = await _sdk_client_handle(app).client()

            def count(layer: str) -> int:
                layer_statuses = chosen or (
                    raw_list_filters()["statuses"] if layer == "l1_raw" else ["active"]
                )
                raw = client.list_memories(
                    user_id=user_id,
                    layers=[layer],
                    statuses=layer_statuses,
                    limit=1,
                    offset=0,
                    order="desc",
                )
                return _list_total(raw)

            counts = await asyncio.to_thread(
                lambda: {layer: count(layer) for layer in MEMORY_LAYERS}
            )
        except HTTPException:
            raise
        except Exception as error:
            raise _sdk_http_error(error, "hy_memory_list_failed") from None
        return SdkLayerCounts(
            user_id=user_id,
            layers=counts,
            total=sum(counts.values()),
            statuses=tuple(chosen) if chosen else None,
        )

    @app.get(
        "/api/v2/memories/graph",
        response_model=MemoryGraphResponse,
        dependencies=strict,
    )
    async def sdk_memory_graph(
        limit: int = Query(default=160, ge=1, le=300),
        offset: int = Query(default=0, ge=0),
        source: str | None = Query(default=None, min_length=1),
        user_id: str = Query(default=DEFAULT_USER_ID, pattern=USER_ID_PATTERN),
        layers: list[str] | None = Query(default=None),  # noqa: B008
        session_ids: list[str] | None = Query(default=None),  # noqa: B008
        exclude_sources: list[str] | None = Query(default=None),  # noqa: B008
    ) -> MemoryGraphResponse:
        """The memory map on SDK authority: SDK memories plus the Kuzu overlay.

        Memories come from the public client; only the overlay reads Kuzu
        (`hy_graph_projection`, the visualization exception). Shape matches
        the legacy map so the same view draws both.

        `layers` maps the chat store's chosen layers only (as on the library);
        `session_ids` and `exclude_sources` narrow the listed memories. Each
        may repeat or be comma separated. The overlay is not filtered.
        """
        chosen_layers = _library_values(layers or [], MEMORY_LAYERS, "memory_layer_unknown")
        filters = {
            name: values
            for name, values in (
                ("layers", chosen_layers),
                ("session_ids", split_csv(session_ids)),
                ("exclude_sources", split_csv(exclude_sources)),
            )
            if values
        }
        try:
            client = await _sdk_client_handle(app).client()
            # The map visualizes the active source scope, independently of the
            # left list's 20-row pagination window.
            raw = await asyncio.to_thread(
                client.list_memories,
                user_id=user_id,
                scene=None if chosen_layers else "all",
                statuses=["active"],
                include_sources=[source] if source else None,
                limit=limit,
                offset=offset,
                order="desc",
                **filters,
            )
            page, skipped = HySdkMemoryPage.from_sdk_skipping_invalid(
                raw, limit=limit, offset=offset
            )
            _log_skipped_sdk_memories(skipped)
        except HTTPException:
            raise
        except Exception as error:
            raise _sdk_http_error(error, "hy_memory_list_failed") from None
        vdb = raw.get("vdb") if isinstance(raw, dict) else None
        total = vdb.get("total") if isinstance(vdb, dict) else None
        memories = [memory for memory in page.memories if memory.layer != "l1_raw"]
        catalog = await asyncio.to_thread(_source_catalog, app)
        nodes: dict[str, MemoryGraphNode] = {}
        edges: list[MemoryGraphEdge] = []
        sessions: dict[str, list[str]] = {}
        for memory in memories:
            memory_node_id = f"memory:{memory.memory_id}"
            nodes[memory_node_id] = MemoryGraphNode(
                node_id=memory_node_id,
                node_type="memory",
                label=memory.content,
                memory_id=memory.memory_id,
                kind=memory.layer,
                status=memory.status,
            )
            # The SDK fills in "default_session" when a writer gave none (the
            # cutover backfill did so for every legacy memory); it names no
            # conversation, so it must not chain those memories together.
            if memory.session_id and memory.session_id != "default_session":
                sessions.setdefault(memory.session_id, []).append(memory_node_id)
            if memory.source and memory.source != "default":
                source_node_id = f"source:{memory.source}"
                registration = catalog.get(memory.source)
                nodes[source_node_id] = MemoryGraphNode(
                    node_id=source_node_id,
                    node_type="source",
                    label=(registration.descriptor.display_name if registration else memory.source),
                    source_id=memory.source,
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
        for _session_id, node_ids in sorted(sessions.items()):
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
        hy_graph = None
        # Aggregate nodes carry no per-source provenance yet, so a revoked
        # source withholds the overlay, as on the legacy map.
        if not await asyncio.to_thread(_read_access(app).has_revoked_sources):
            try:
                hy_graph = await _sdk_memory_runtime(app).graph_snapshot(
                    limit=limit, user_id=user_id
                )
            except HySdkError:
                hy_graph = None
        if hy_graph is not None:
            for node in hy_graph.nodes:
                node_id = f"memory:{node.node_id}"
                if node_id in nodes:
                    continue
                node_id = f"hy:{node.node_id}"
                nodes.setdefault(
                    node_id,
                    MemoryGraphNode(
                        node_id=node_id,
                        node_type="topic" if node.layer == "TOPIC" else "schema",
                        label=node.content,
                        confidence=node.confidence,
                    ),
                )
            for edge in hy_graph.edges:
                source_node_id, target_node_id = (
                    f"memory:{edge.source_node_id}"
                    if f"memory:{edge.source_node_id}" in nodes
                    else f"hy:{edge.source_node_id}",
                    f"memory:{edge.target_node_id}"
                    if f"memory:{edge.target_node_id}" in nodes
                    else f"hy:{edge.target_node_id}",
                )
                if source_node_id not in nodes or target_node_id not in nodes:
                    continue
                edges.append(
                    MemoryGraphEdge(
                        edge_id=f"{source_node_id}->{target_node_id}:{edge.relation}",
                        source_node_id=source_node_id,
                        target_node_id=target_node_id,
                        relation=edge.relation,
                        weight=edge.weight,
                        origin="hy_relation",
                    )
                )
        return MemoryGraphResponse(
            user_id=user_id,
            total_memories=total if isinstance(total, int) else len(memories),
            projected_memories=len(memories),
            nodes=tuple(nodes.values()),
            edges=tuple(edges),
            hy_graph_available=hy_graph is not None and hy_graph.consulted,
        )

    @app.get(
        "/api/v2/user-profile",
        response_model=SdkUserProfileResponse,
        dependencies=strict,
    )
    async def sdk_user_profile(
        user_id: str = Query(default=DEFAULT_USER_ID, pattern=USER_ID_PATTERN),
    ) -> SdkUserProfileResponse:
        try:
            client = await _sdk_client_handle(app).client()
            value = await asyncio.to_thread(client.get_user_profile, user_id=user_id)
            profile = HySdkUserProfile.from_sdk(value)
        except Exception as error:
            raise _sdk_http_error(error, "hy_memory_profile_read_failed") from None
        return SdkUserProfileResponse(user_id=user_id, **profile.model_dump())

    @app.get(
        "/api/v2/user-profile/versions",
        response_model=SdkProfileVersionsResponse,
        dependencies=strict,
    )
    async def sdk_user_profile_versions(
        user_id: str = Query(default=DEFAULT_USER_ID, pattern=USER_ID_PATTERN),
    ) -> SdkProfileVersionsResponse:
        """The long-term profile's L0 version chains (`get_user_basic_info`).

        Each chain is one L0 head with its versions latest first, so a value
        that replaced a better one can be found and deleted by id.
        """
        try:
            client = await _sdk_client_handle(app).client()
            value = await asyncio.to_thread(client.get_user_basic_info, user_id=user_id)
            versions = HySdkProfileVersions.from_sdk(value)
        except Exception as error:
            raise _sdk_http_error(error, "hy_memory_profile_read_failed") from None
        return SdkProfileVersionsResponse(user_id=user_id, **versions.model_dump())

    @app.get(
        "/api/v2/memories/{memory_id}",
        response_model=HySdkMemoryRecord,
        dependencies=strict,
    )
    async def get_sdk_memory(
        memory_id: str,
        kind: SdkMemoryKind | None = None,
        variant: SdkMemoryVariant | None = None,
        user_id: str = Query(default=DEFAULT_USER_ID, pattern=USER_ID_PATTERN),
    ) -> HySdkMemoryRecord:
        _check_memory_namespace(kind, variant)
        try:
            memory = await _get_sdk_memory(
                _sdk_client_handle(app), memory_id, kind=kind, variant=variant
            )
            # The SDK's `get` takes no user: another user's memory reads as
            # missing, never as existing.
            if memory is not None and not owns(memory.user_id, user_id):
                memory = None
        except AmbiguousMemoryIdError:
            raise HTTPException(status_code=409, detail="memory_id_ambiguous") from None
        except Exception as error:
            raise _sdk_http_error(error, "hy_memory_get_failed") from None
        if memory is None:
            raise HTTPException(status_code=404, detail="memory_not_found")
        return memory

    @app.put(
        "/api/v2/memories/{memory_id}",
        response_model=HySdkMemoryRecord,
        dependencies=strict,
    )
    async def update_sdk_memory(
        memory_id: str,
        request: SdkMemoryUpdateRequest,
    ) -> HySdkMemoryRecord:
        _check_memory_namespace(request.kind, request.variant)
        if request.task is not None and request.kind in {"chat", "fact"}:
            raise HTTPException(status_code=422, detail="memory_task_requires_experience")
        namespace = _namespace_kwargs(request.kind, request.variant)
        _refuse_write_during_embedding_switch(app)
        try:
            async with app.state.sdk_write_coordinator.use_client() as client:
                # `update` takes no user: read the memory first and refuse
                # another user's id exactly as a missing one.
                current = await asyncio.to_thread(client.get, memory_id, **namespace)
                if current is None or not owns(
                    HySdkMemoryRecord.from_sdk(current).user_id, request.user_id
                ):
                    raise HTTPException(status_code=404, detail="memory_not_found")
                update_kwargs: dict[str, object] = dict(namespace)
                if request.task is not None:
                    update_kwargs["task"] = request.task
                result = await asyncio.to_thread(
                    client.update, memory_id, request.content, **update_kwargs
                )
                if not isinstance(result, dict):
                    raise HySdkError("hy_memory_update_rejected")
                if result.get("success") is not True:
                    if "not found" in str(result.get("error_message") or ""):
                        raise HTTPException(status_code=404, detail="memory_not_found")
                    raise HySdkError("hy_memory_update_rejected")
                # The SDK names the namespace it wrote; read that one back.
                if result.get("kind") in {"experience", "fact"}:
                    namespace = {"kind": result["kind"]}
                    if result.get("variant") in {"working", "coding"}:
                        namespace["variant"] = result["variant"]
                raw = await asyncio.to_thread(client.get, memory_id, **namespace)
            memory = None if raw is None else HySdkMemoryRecord.from_sdk(raw)
        except HTTPException:
            raise
        except AmbiguousMemoryIdError:
            raise HTTPException(status_code=409, detail="memory_id_ambiguous") from None
        except ValueError as error:
            if request.task is None:
                raise _sdk_http_error(error, "hy_memory_update_failed") from None
            # The SDK refuses `task` for anything but an experience.
            raise HTTPException(status_code=422, detail="memory_task_requires_experience") from None
        except Exception as error:
            raise _sdk_http_error(error, "hy_memory_update_failed") from None
        if memory is None:
            raise HTTPException(status_code=404, detail="memory_not_found")
        return memory

    @app.delete(
        "/api/v2/memories/{memory_id}",
        status_code=204,
        dependencies=strict,
    )
    async def delete_sdk_memory(
        memory_id: str,
        kind: SdkMemoryKind | None = None,
        variant: SdkMemoryVariant | None = None,
        user_id: str = Query(default=DEFAULT_USER_ID, pattern=USER_ID_PATTERN),
    ) -> Response:
        _check_memory_namespace(kind, variant)
        _refuse_write_during_embedding_switch(app)
        try:
            async with app.state.sdk_write_coordinator.use_client() as client:
                # `delete` takes no user. Another user's memory is answered
                # exactly like an id that does not exist: 404
                # `memory_not_found`, nothing touched, so the status cannot
                # tell that it exists. Callers read 404 as "already gone".
                current = await asyncio.to_thread(
                    client.get, memory_id, **_namespace_kwargs(kind, variant)
                )
                if current is None or not owns(
                    HySdkMemoryRecord.from_sdk(current).user_id, user_id
                ):
                    raise HTTPException(status_code=404, detail="memory_not_found")
                result = await asyncio.to_thread(
                    client.delete, memory_id, **_namespace_kwargs(kind, variant)
                )
            if not isinstance(result, dict):
                raise HySdkError("hy_memory_delete_rejected")
            error_code = result.get("error_code")
            if error_code or result.get("partial") is True:
                raise HySdkError(str(error_code or "hy_memory_delete_incomplete"))
            if result.get("success") is not True:
                # Read a moment ago, found by nobody now (a second delete won
                # the race): the same answer as an id that never existed.
                if result.get("deleted_count") == 0:
                    raise HTTPException(status_code=404, detail="memory_not_found")
                raise HySdkError("hy_memory_delete_rejected")
        except HTTPException:
            raise
        except AmbiguousMemoryIdError:
            raise HTTPException(status_code=409, detail="memory_id_ambiguous") from None
        except Exception as error:
            raise _sdk_http_error(error, "hy_memory_delete_failed") from None
        return Response(status_code=204)

    raw_turn_cache: dict[
        tuple[object, ...],
        tuple[
            int,
            tuple[tuple[str, str | None], ...],
            tuple[tuple[HySdkRawRecord, RawDisplayTurn], ...],
        ],
    ] = {}
    raw_turn_cache_locks: dict[tuple[object, ...], asyncio.Lock] = {}

    def graph_items(
        records: Iterable[HySdkRawRecord],
    ) -> tuple[SdkRawGraphItem, ...]:
        items: list[SdkRawGraphItem] = []
        for record in records:
            for turn in reversed(raw_display_turns(record)):
                label = re.sub(r"\s+", " ", turn.content).strip()[:240]
                items.append(
                    SdkRawGraphItem(
                        turn_id=turn.turn_id,
                        raw_id=record.raw_id,
                        label=label or record.raw_id,
                        source=record.source,
                        session_id=record.session_id,
                        memory_at=record.memory_at,
                        gmt_created=record.gmt_created,
                    )
                )
        return tuple(items)

    @app.get(
        "/api/v2/raw",
        response_model=SdkRawPage,
        dependencies=strict,
    )
    async def sdk_raw_library(
        source: list[str] | None = Query(default=None),  # noqa: B008
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
        unit: Literal["raw", "turn"] = Query(default="raw"),
        paging: Literal["offset", "cursor"] = Query(default="offset"),
        cursor: str | None = Query(default=None, max_length=160),
        user_id: str = Query(default=DEFAULT_USER_ID, pattern=USER_ID_PATTERN),
        exclude_sources: list[str] | None = Query(default=None),  # noqa: B008
        session_id: list[str] | None = Query(default=None),  # noqa: B008
        statuses: list[str] | None = Query(default=None),  # noqa: B008
        after: float | None = Query(default=None, ge=0),
        before: float | None = Query(default=None, ge=0),
        order: Literal["desc", "asc"] = Query(default="desc"),
    ) -> SdkRawPage:
        """One page of a user's SDK raw turns: what was imported, as stored.

        The SDK lists raw only when asked for `layers=["l1_raw"]`, and only
        active rows unless told otherwise. Pro shadows a raw once extraction
        succeeds, so a raw page reads active and shadow (`statuses` narrows
        it). `unit=turn` projects storage batches into stable product turns;
        the first read builds an append-only in-memory index and later polls
        only probe the latest SDK page unless it changed. `source` and
        `session_id` (a conversation) may repeat or be comma
        separated. `after`/`before` (unix seconds, against the conversation
        time, else the stored time) have no SDK list filter: they cut this
        page in Core and mark it `time_filtered`.
        """
        chosen_statuses = (
            _library_values(statuses or [], MEMORY_STATUSES, "memory_status_unknown")
            or raw_list_filters()["statuses"]
        )
        if paging == "cursor" and unit != "turn":
            raise HTTPException(status_code=422, detail="raw_cursor_requires_turn_unit")
        if cursor is not None and paging != "cursor":
            raise HTTPException(status_code=422, detail="raw_cursor_requires_cursor_paging")
        if paging == "cursor" and (offset or after is not None or before is not None):
            raise HTTPException(status_code=422, detail="raw_cursor_filter_unsupported")
        raw_cursor = (0, 0, 0, 0, "", "")
        if cursor:
            parts = cursor.split(":")
            if (
                len(parts) != 7
                or parts[0] != "turn"
                or any(re.fullmatch(r"[0-9]+", part) is None for part in parts[1:5])
                or re.fullmatch(r"(?:p?[0-9a-f]{16})?", parts[5]) is None
                or re.fullmatch(r"[0-9a-f]{16}", parts[6]) is None
            ):
                raise HTTPException(status_code=422, detail="raw_cursor_invalid")
            raw_cursor = (
                int(parts[1]),
                int(parts[2]),
                int(parts[3]),
                int(parts[4]),
                parts[5],
                parts[6],
            )
            if any(value > 100_000_000 for value in raw_cursor[:4]):
                raise HTTPException(status_code=422, detail="raw_cursor_invalid")
        sources = split_csv(source)
        excluded = split_csv(exclude_sources)
        sessions = split_csv(session_id)
        cursor_scope = hashlib.sha256(
            json.dumps(
                {
                    "user_id": user_id,
                    "statuses": sorted(chosen_statuses),
                    "sources": sorted(sources),
                    "excluded": sorted(excluded),
                    "sessions": sorted(sessions),
                    "order": order,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()[:16]
        if cursor and raw_cursor[5] != cursor_scope:
            raise HTTPException(status_code=422, detail="raw_cursor_scope_mismatch")
        try:
            client = await _sdk_client_handle(app).client()
            query = {
                "user_id": user_id,
                **raw_list_filters(chosen_statuses),
                "include_sources": sources or None,
                "exclude_sources": excluded or None,
                "session_ids": sessions or None,
                "order": order if unit == "raw" or paging == "cursor" else "desc",
            }
            if unit == "turn" and paging == "cursor":
                (
                    raw_offset,
                    turn_offset,
                    emitted,
                    previous_total,
                    expected_raw,
                    _scope,
                ) = raw_cursor
                value = await asyncio.to_thread(
                    client.list_memories,
                    **query,
                    offset=raw_offset,
                    limit=min(limit + 1, 500),
                )
                raw_page = HySdkRawPage.from_memory_list(
                    value, limit=min(limit + 1, 500), offset=raw_offset
                )

                def raw_anchor(record: HySdkRawRecord) -> str:
                    return hashlib.sha256(
                        f"{record.raw_id}\0{record.content}".encode()
                    ).hexdigest()[:16]

                if cursor:
                    if raw_page.total < previous_total:
                        raise HTTPException(status_code=409, detail="raw_cursor_stale")
                    observed_total = raw_page.total
                    added = raw_page.total - previous_total
                    if added and order == "desc":
                        raw_offset += added
                        value = await asyncio.to_thread(
                            client.list_memories,
                            **query,
                            offset=raw_offset,
                            limit=min(limit + 1, 500),
                        )
                        raw_page = HySdkRawPage.from_memory_list(
                            value,
                            limit=min(limit + 1, 500),
                            offset=raw_offset,
                        )
                        if raw_page.total != observed_total:
                            raise HTTPException(status_code=409, detail="raw_cursor_stale")

                    if expected_raw.startswith("p"):
                        if raw_offset == 0:
                            raise HTTPException(status_code=409, detail="raw_cursor_stale")
                        previous_value = await asyncio.to_thread(
                            client.list_memories,
                            **query,
                            offset=raw_offset - 1,
                            limit=1,
                        )
                        previous_page = HySdkRawPage.from_memory_list(
                            previous_value, limit=1, offset=raw_offset - 1
                        )
                        valid_anchor = (
                            previous_page.total == raw_page.total
                            and bool(previous_page.raw)
                            and raw_anchor(previous_page.raw[0]) == expected_raw[1:]
                        )
                    else:
                        valid_anchor = (
                            bool(raw_page.raw) and raw_anchor(raw_page.raw[0]) == expected_raw
                        )
                    if expected_raw and not valid_anchor:
                        raise HTTPException(status_code=409, detail="raw_cursor_stale")
                selected_turns: list[tuple[HySdkRawRecord, RawDisplayTurn]] = []
                next_cursor: str | None = None
                for record_index, record in enumerate(raw_page.raw):
                    turns = raw_display_turns(record)
                    ordered_turns = tuple(reversed(turns)) if order == "desc" else turns
                    start = turn_offset if record_index == 0 else 0
                    for index in range(start, len(ordered_turns)):
                        selected_turns.append((record, ordered_turns[index]))
                        if len(selected_turns) < limit:
                            continue
                        absolute_raw = raw_offset + record_index
                        next_raw = absolute_raw
                        next_turn = index + 1
                        if next_turn >= len(ordered_turns):
                            next_raw += 1
                            next_turn = 0
                        if next_raw < raw_page.total:
                            if next_turn:
                                anchor_record = record
                            else:
                                next_index = record_index + 1
                                anchor_record = (
                                    raw_page.raw[next_index]
                                    if next_index < len(raw_page.raw)
                                    else None
                                )
                            anchor = (
                                raw_anchor(anchor_record)
                                if anchor_record is not None
                                else f"p{raw_anchor(record)}"
                            )
                            next_cursor = (
                                f"turn:{next_raw}:{next_turn}:"
                                f"{emitted + len(selected_turns)}:{raw_page.total}:"
                                f"{anchor}:{cursor_scope}"
                            )
                        break
                    if len(selected_turns) >= limit:
                        break
                if next_cursor is None and raw_offset + len(raw_page.raw) < raw_page.total:
                    if not raw_page.raw:
                        raise HTTPException(status_code=409, detail="raw_cursor_stale")
                    next_raw = raw_offset + len(raw_page.raw)
                    previous_anchor = raw_anchor(raw_page.raw[-1])
                    next_cursor = (
                        f"turn:{next_raw}:0:{emitted + len(selected_turns)}:"
                        f"{raw_page.total}:p{previous_anchor}:{cursor_scope}"
                    )
                return SdkRawPage(
                    user_id=user_id,
                    items=tuple(SdkRawItem.of(record, turn) for record, turn in selected_turns),
                    # Cursor consumers do not use a precise total. This lower
                    # bound preserves the legacy numeric field without forcing
                    # a full scan merely to count variable-size turn batches.
                    total=emitted + len(selected_turns) + (1 if next_cursor else 0),
                    offset=emitted,
                    limit=limit,
                    unit="turn",
                    next_cursor=next_cursor,
                    has_more=next_cursor is not None,
                    total_exact=False,
                )
            if unit == "raw":
                value = await asyncio.to_thread(
                    client.list_memories,
                    **query,
                    offset=offset,
                    limit=limit,
                )
                page = HySdkRawPage.from_memory_list(value, limit=limit, offset=offset)
                records = page.raw
            else:
                cache_key = (
                    user_id,
                    tuple(sorted(chosen_statuses)),
                    tuple(sorted(sources)),
                    tuple(sorted(excluded)),
                    tuple(sorted(sessions)),
                )
                if len(raw_turn_cache_locks) > 64:
                    for stale_key, stale_lock in tuple(raw_turn_cache_locks.items()):
                        if stale_key not in raw_turn_cache and not stale_lock.locked():
                            raw_turn_cache_locks.pop(stale_key, None)
                lock = raw_turn_cache_locks.setdefault(cache_key, asyncio.Lock())
                async with lock:
                    for _attempt in range(3):
                        value = await asyncio.to_thread(
                            client.list_memories,
                            **query,
                            offset=0,
                            limit=500,
                        )
                        first_page = HySdkRawPage.from_memory_list(value, limit=500, offset=0)
                        signature = tuple(
                            (record.raw_id, record.status) for record in first_page.raw
                        )
                        cached = raw_turn_cache.get(cache_key)
                        if (
                            cached is not None
                            and cached[0] == first_page.total
                            and cached[1] == signature
                        ):
                            projected_turns = cached[2]
                            break
                        added = (
                            None
                            if cached is None
                            else raw_prefix_added(cached[1], signature, cached[0], first_page.total)
                        )
                        if cached is not None and added is not None:
                            # A live session only puts new raws on top: project
                            # those and keep the rest, instead of re-listing the
                            # whole raw layer on every poll.
                            projected_turns = (
                                tuple(
                                    (record, turn)
                                    for record in first_page.raw[:added]
                                    for turn in reversed(raw_display_turns(record))
                                )
                                + cached[2]
                            )
                            raw_turn_cache[cache_key] = (
                                first_page.total,
                                signature,
                                projected_turns,
                            )
                            break

                        records_list = list(first_page.raw)
                        raw_offset = len(first_page.raw)
                        raw_total = first_page.total
                        while first_page.raw and raw_offset < raw_total:
                            value = await asyncio.to_thread(
                                client.list_memories,
                                **query,
                                offset=raw_offset,
                                limit=500,
                            )
                            raw_page = HySdkRawPage.from_memory_list(
                                value, limit=500, offset=raw_offset
                            )
                            records_list.extend(raw_page.raw)
                            raw_offset += len(raw_page.raw)
                            if not raw_page.raw:
                                break

                        verify_value = await asyncio.to_thread(
                            client.list_memories,
                            **query,
                            offset=0,
                            limit=500,
                        )
                        verify = HySdkRawPage.from_memory_list(verify_value, limit=500, offset=0)
                        verify_signature = tuple(
                            (record.raw_id, record.status) for record in verify.raw
                        )
                        if verify.total != first_page.total or verify_signature != signature:
                            continue
                        projected_turns = tuple(
                            (record, turn)
                            for record in records_list
                            for turn in reversed(raw_display_turns(record))
                        )
                        raw_turn_cache[cache_key] = (
                            first_page.total,
                            signature,
                            projected_turns,
                        )
                        while len(raw_turn_cache) > 32:
                            oldest = next(iter(raw_turn_cache))
                            raw_turn_cache.pop(oldest)
                            old_lock = raw_turn_cache_locks.get(oldest)
                            if old_lock is not None and not old_lock.locked():
                                raw_turn_cache_locks.pop(oldest, None)
                        break
                    else:
                        raise RuntimeError("hy_memory_raw_snapshot_busy")
                if cache_key not in raw_turn_cache and not lock.locked():
                    raw_turn_cache_locks.pop(cache_key, None)
        except HTTPException:
            raise
        except Exception as error:
            raise _sdk_http_error(error, "hy_memory_raw_list_failed") from None
        time_filtered = after is not None or before is not None
        if unit == "turn":
            turns = projected_turns
            if time_filtered:
                turns = tuple(
                    (record, turn)
                    for record, turn in turns
                    if (at := record.memory_at or record.gmt_created) is not None
                    and (after is None or at >= after)
                    and (before is None or at < before)
                )
            if order == "asc":
                turns = tuple(reversed(turns))
            selected = turns[offset : offset + limit]
            return SdkRawPage(
                user_id=user_id,
                items=tuple(SdkRawItem.of(record, turn) for record, turn in selected),
                total=len(turns),
                offset=offset,
                limit=limit,
                unit="turn",
                time_filtered=time_filtered,
            )
        if time_filtered:
            records = tuple(
                record
                for record in records
                if (at := record.memory_at or record.gmt_created) is not None
                and (after is None or at >= after)
                and (before is None or at < before)
            )
        return SdkRawPage(
            user_id=user_id,
            items=tuple(SdkRawItem.of(record) for record in records),
            total=page.total,
            offset=offset,
            limit=limit,
            unit="raw",
            time_filtered=time_filtered,
        )

    @app.get(
        "/api/v2/raw/graph",
        response_model=SdkRawGraphPage,
        dependencies=strict,
    )
    async def sdk_raw_graph(
        source: list[str] | None = Query(default=None),  # noqa: B008
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=300, ge=1, le=300),
        user_id: str = Query(default=DEFAULT_USER_ID, pattern=USER_ID_PATTERN),
        exclude_sources: list[str] | None = Query(default=None),  # noqa: B008
        session_id: list[str] | None = Query(default=None),  # noqa: B008
        statuses: list[str] | None = Query(default=None),  # noqa: B008
    ) -> SdkRawGraphPage:
        """Bounded lightweight raw graph, independent of list pagination."""
        chosen_statuses = (
            _library_values(
                statuses or [],
                MEMORY_STATUSES,
                "memory_status_unknown",
            )
            or raw_list_filters()["statuses"]
        )
        sources = split_csv(source)
        excluded = split_csv(exclude_sources)
        sessions = split_csv(session_id)
        if offset:
            raise HTTPException(
                status_code=422,
                detail="raw_graph_is_single_page",
            )
        query: dict[str, object] = {
            "user_id": user_id,
            **raw_list_filters(chosen_statuses),
            "include_sources": sources or None,
            "exclude_sources": excluded or None,
            "session_ids": sessions or None,
            "order": "desc",
        }
        try:
            client = await _sdk_client_handle(app).client()
            value = await asyncio.to_thread(
                client.list_memories,
                **query,
                offset=0,
                limit=limit,
            )
            page = HySdkRawPage.from_memory_list(
                value,
                limit=limit,
                offset=0,
            )
        except HTTPException:
            raise
        except Exception as error:
            raise _sdk_http_error(
                error,
                "hy_memory_raw_graph_failed",
            ) from None
        projected = graph_items(page.raw)
        selected = projected[:limit]
        total = max(page.total, len(projected))
        return SdkRawGraphPage(
            user_id=user_id,
            items=selected,
            total=total,
            offset=0,
            limit=limit,
            has_more=page.total > len(page.raw) or len(projected) > limit,
        )

    @app.post(
        "/api/v2/memories/ask",
        response_model=SdkMemoryAnswer,
        dependencies=strict,
    )
    async def ask_sdk_memories(
        request: AskMemoryRequest,
        http_request: Request,
        response: Response,
    ) -> SdkMemoryAnswer:
        started = time.monotonic()
        access = external_access(http_request.headers)
        await _require_external_recall_scope(
            app,
            http_request,
            request.scope,
            request.workspace_path,
            "om_ask",
        )
        try:
            answer = await _sdk_request_answerer(app).answer(
                request.question, user_id=request.user_id, **_ask_filters(request)
            )
        except MemoryAnswerError as error:
            raise HTTPException(status_code=503, detail=str(error)) from None
        answer = answer.model_copy(update={"user_id": request.user_id})
        if request.question != "__om_probe__" and access is not None:
            from one_memory.call_event_recorder import ask_call_event, try_record

            try_record(
                http_request,
                lambda store: store.record(
                    ask_call_event(answer, access, (time.monotonic() - started) * 1000)
                ),
                response,
            )
        return answer

    @app.post(
        "/api/v2/memories/ask/stream",
        dependencies=strict,
    )
    async def ask_sdk_memories_stream(
        request: AskMemoryRequest,
        http_request: Request,
    ) -> StreamingResponse:
        await _require_external_recall_scope(
            app,
            http_request,
            request.scope,
            request.workspace_path,
            "om_ask",
        )
        answerer = _sdk_request_answerer(app)
        started = time.monotonic()
        access = external_access(http_request.headers)

        async def events() -> AsyncIterator[str]:
            from one_memory.call_event_recorder import ask_call_event, try_record

            final: dict[str, object] | None = None
            try:
                async for event in answerer.answer_stream(
                    request.question, user_id=request.user_id, **_ask_filters(request)
                ):
                    if event.get("type") == "final":
                        final = event
                    elif event.get("type") == "recall":
                        # The first event names whose memories answer.
                        event = {**event, "user_id": request.user_id}
                    yield _sse(_serialize_ask_event(event))
            except MemoryAnswerError:
                yield _sse({"type": "error", "code": "memory_answer_failed"})
                return
            if final is not None and request.question != "__om_probe__" and access is not None:
                answer = SdkMemoryAnswer.model_validate(
                    {
                        "answer": final.get("answer", ""),
                        "memories": final.get("memories", ()),
                        "mode": final.get("mode", "empty"),
                    }
                )
                try_record(
                    http_request,
                    lambda store: store.record(
                        ask_call_event(answer, access, (time.monotonic() - started) * 1000)
                    ),
                )

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.post(
        "/api/v1/recall",
        response_model=RecallReceipt,
        dependencies=strict,
    )
    async def recall_memories(
        request: RecallRequest,
        http_request: Request,
    ) -> RecallReceipt:
        from one_memory.call_event_recorder import record_legacy_result
        from one_memory.recall_fallback import (
            recall_total_timeout_seconds,
            recall_with_fallback,
        )

        started = time.monotonic()
        await _require_external_recall_scope(
            app, http_request, request.scope, request.workspace_path
        )
        # A caller's pass-through search options need the SDK target, which
        # carries them; without any, recall is the call it always was.
        options = request if request.search_overrides() else None
        target = (
            _recall_target(app)
            if request.user_id == DEFAULT_USER_ID and options is None
            else _sdk_recall_target(app, request.user_id, options)
        )
        raw_options = {"filters": options} if options is not None else {}
        access = external_access(http_request.headers)
        try:
            # The whole answer, not only the search: the checks before and
            # after it used to wait for a thread with no limit at all, so a
            # pool held by stuck writes left recall hanging while /health
            # still answered (#231). Late is a clear 504, not silence.
            receipt = await asyncio.wait_for(
                recall_with_fallback(
                    request,
                    model_ready=lambda: _recall_model_ready(app),
                    recall_memories=target.recall_async,
                    has_memories=lambda: _library_has_memories(app, request.user_id),
                    raw_sources=lambda: _recall_raw_sources(app, request, access, started),
                    search_raw=lambda query, limit, sources: _search_sdk_raw(
                        app,
                        query,
                        limit=limit,
                        sources=sources,
                        user_id=request.user_id,
                        **raw_options,
                    ),
                ),
                timeout=recall_total_timeout_seconds(request.timeout_ms),
            )
        except TimeoutError:
            logger.warning(
                "recall_timed_out timeout_ms=%d elapsed_ms=%.0f",
                request.timeout_ms,
                (time.monotonic() - started) * 1000,
            )
            raise HTTPException(status_code=504, detail="recall_timeout") from None
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
        return receipt.model_copy(update={"user_id": request.user_id})

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
        supervisor = app.state.sdk_memory_runtime
        depths = supervisor.queue_depths()
        active_queue, in_flight_count = supervisor.active_depths()
        planned, current = supervisor.manual_batch_progress()
        sdk_counts = (
            supervisor.status_counts() if app.state.memory_authority is MemoryAuthority.SDK else {}
        )
        # On the SDK plane `in_flight_count` is the number of `running` rows, so
        # the legacy `running - in_flight` difference is always zero there and
        # reported a healthy queue whatever was stuck. `stranded_depths` counts
        # the rows a worker no longer holds, from the same read as `running`.
        sdk_stranded = (
            supervisor.stranded_depths()[1]
            if app.state.memory_authority is MemoryAuthority.SDK
            else 0
        )
        # `running` counts rows the store still marks running, which includes
        # work a worker has already given up on. Reporting that as "what is
        # running" is how a stalled queue looks busy, so the strand is given
        # its own number.
        running_rows = counts.get(MemoryTaskStatus.RUNNING, 0)
        backoff = cast(ModelBackoff | None, getattr(app.state, "model_backoff", None))
        backoff_state = backoff.state() if backoff is not None else None
        drain = getattr(app.state, "memory_write_drain", None)
        return MemoryProcessingSummary(
            pending=(
                sdk_counts.get("pending", 0)
                if sdk_counts
                else counts.get(MemoryTaskStatus.PENDING, 0)
            ),
            fresh_pending=depths.fresh,
            backlog_pending=depths.backlog,
            outside_window_pending=counts.get(
                MemoryTaskStatus.DEFERRED_OUTSIDE_WINDOW,
                0,
            ),
            recent_completions=depths.recent_completions,
            running=sdk_counts.get("running", running_rows),
            stranded_running=(
                sdk_stranded
                if sdk_counts
                else max(0, running_rows - in_flight_count)
            ),
            unexecuted_policy_failures=store.unexecuted_policy_failures(),
            waiting_for_model=sdk_counts.get(
                "waiting_for_model",
                counts.get(MemoryTaskStatus.WAITING_FOR_MODEL, 0),
            ),
            completed=sdk_counts.get(
                "completed",
                counts.get(MemoryTaskStatus.COMPLETED, 0),
            ),
            failed=sdk_counts.get("failed", counts.get(MemoryTaskStatus.FAILED, 0)),
            unknown=sdk_counts.get("unknown", counts.get(MemoryTaskStatus.UNKNOWN, 0)),
            unknown_reasons=store.unknown_error_counts(),
            blocked_stages=tuple(
                MemoryProcessingBlock(kind=kind, reason=reason, count=count)
                for kind, reason, count in store.blocked_stages()[:6]
            ),
            seconds_per_completion=store.seconds_per_completion(),
            extraction_window_preset=app.state.extraction_settings_store.get().window_preset,
            manual_extract_batches_planned=planned,
            manual_extract_batches_current=current,
            active_queue=active_queue,
            in_flight_count=in_flight_count,
            metrics=ProcessingMetrics.model_validate(store.processing_metrics()),
            pipeline=_pipeline_status(app),
            embedding_switch=_embedding_switch_summary(app),
            suspended=sdk_counts.get("suspended", 0),
            llm_backoff_until=backoff_state.until if backoff_state is not None else None,
            llm_backoff_error=(
                backoff_state.last_error
                if backoff_state is not None and backoff_state.until is not None
                else None
            ),
            write_drain=getattr(drain, "liveness", None),
            write_drain_error=getattr(drain, "last_error_code", None),
        )

    @app.get(
        "/api/v1/settings/memory-mode",
        response_model=MemoryModeState,
        dependencies=strict,
    )
    def get_memory_mode() -> MemoryModeState:
        """The SDK mode (lite / pro / ultra): configured, effective, and why."""
        return _memory_mode_state(app)

    @app.post(
        "/api/v1/settings/memory-mode/plan",
        response_model=MemoryModePlan,
        dependencies=strict,
    )
    def plan_memory_mode(request: MemoryModeUpdate) -> MemoryModePlan:
        """Validate and explain a mode change without mutating anything."""
        return _memory_mode_plan(app, request.mode)

    @app.put(
        "/api/v1/settings/memory-mode",
        response_model=MemoryModeChange,
        dependencies=strict,
    )
    async def put_memory_mode(request: MemoryModeUpdate) -> MemoryModeChange:
        """Choose the SDK mode. Nothing is migrated; `effects` says what changes.

        The mode is read when a queued job runs, not when it was queued: a
        job already done (in any mode) stays done and is never reprocessed;
        jobs still waiting, and new input, run in the new mode. A batch in
        flight finishes in the old mode. Search follows the new mode at once.
        lite → pro/ultra: what lite stored stays raw, and lite's raw-only
        search stops. Down to a lower mode: memories the higher mode made stay
        stored but are not shown. → ultra: every user's existing facts are
        added to the graph once, in the background (`graph_build`). No raw is
        ever deleted, in any mode. See docs/contracts/memory-mode-evolve-v1.md.
        """
        store = _config_store(app)
        async with app.state.memory_mode_lock:
            plan = _memory_mode_plan(app, request.mode)
            if plan.blockers:
                raise HTTPException(status_code=409, detail=plan.blockers[0])
            previous_effective = plan.current
            previous = store.memory_mode()
            if previous != request.mode or not store.memory_mode_explicitly_configured():
                await asyncio.to_thread(store.set_memory_mode, request.mode)
            effective = _memory_mode(app)
            backfill: bool | None = None
            if effective != previous_effective:
                # Reads rebuild on their next request (`ActiveProfileReadHandle`).
                await app.state.sdk_write_coordinator.retire_stale_modes()
                if effective == "ultra":
                    backfill = not app.state.memory_graph_backfill.running
                    if backfill:
                        users = await asyncio.to_thread(_known_user_ids, app)
                        app.state.memory_graph_backfill.start(users)
                diagnostic_event(
                    level="info",
                    component="memory.mode",
                    event="memory_mode.changed",
                    outcome="succeeded",
                    details={"mode": effective, "previous_mode": previous_effective},
                )
        if effective == "lite" and effective != previous_effective:
            _resume_sedimentation(app)
        return MemoryModeChange(
            **_memory_mode_state(app).model_dump(),
            previous_mode=previous_effective,
            effects=memory_mode_effects(previous_effective, effective, graph_backfill=backfill),
        )

    @app.get(
        "/api/v1/settings/memory-features",
        response_model=MemoryFeaturesState,
        dependencies=strict,
    )
    def get_memory_features() -> MemoryFeaturesState:
        """The SDK retrieval and extraction features: value, default, active in this mode."""
        return _memory_features_state(app)

    @app.put(
        "/api/v1/settings/memory-features",
        response_model=MemoryFeaturesChange,
        dependencies=strict,
    )
    async def put_memory_features(request: MemoryFeatures) -> MemoryFeaturesChange:
        """Turn SDK features on or off; one global setting, not per user.

        A key sets that feature, null resets it to the SDK default, an absent
        key is left as it is; an unknown key, a non-boolean feature or an
        `intention_dedup_threshold` outside its range is 422. A
        change rebuilds the SDK clients like a mode change: a write in flight
        finishes with the old features, the next job and search use the new
        ones. Nothing stored is migrated and no collection is renamed. See
        docs/contracts/memory-features-v1.md.
        """
        store = _config_store(app)
        async with app.state.memory_mode_lock:
            defaults = await asyncio.to_thread(sdk_feature_defaults)
            previous = _memory_features(app)
            updated = store.memory_features().merged(request)
            if updated != store.memory_features():
                await asyncio.to_thread(store.set_memory_features, updated)
            current = _memory_features(app)
            before = effective_features(previous, defaults)
            after = effective_features(current, defaults)
            changed = tuple(name for name in SETTING_NAMES if before[name] != after[name])
            if current != previous:
                # Even a reset to the default value rebuilds: the client then
                # carries no override at all, byte for byte the default config.
                await app.state.sdk_write_coordinator.retire_stale_handles()
                # Reads rebuild on their next request (`ActiveProfileReadHandle`).
                diagnostic_event(
                    level="info",
                    component="memory.features",
                    event="memory_features.changed",
                    outcome="succeeded",
                    details={"changed": list(changed), "set": [name for name, _ in current]},
                )
        return MemoryFeaturesChange(
            **_memory_features_state(app, defaults).model_dump(),
            changed=changed,
        )

    @app.get(
        "/api/v1/settings/ui-language",
        response_model=UiLanguageState,
        dependencies=strict,
    )
    def get_ui_language() -> UiLanguageState:
        """The App's display language and the SDK prompt language it implies."""
        return _ui_language_state(app)

    @app.put(
        "/api/v1/settings/ui-language",
        response_model=UiLanguageChange,
        dependencies=strict,
    )
    async def put_ui_language(request: UiLanguageUpdate) -> UiLanguageChange:
        """Record the App's display language; one global setting, not per user.

        Every code (zh-Hans and en included) is passed as
        `MemoryConfig.language`; only null passes no language, so the SDK
        keeps detecting zh/en as before. A change of that SDK language rebuilds the
        SDK clients like a feature change: a write in flight finishes in the
        old language. Nothing stored is migrated and no collection is renamed.
        """
        store = _config_store(app)
        async with app.state.memory_mode_lock:
            previous = _sdk_language(app)
            if store.ui_language() != request.ui_language:
                await asyncio.to_thread(store.set_ui_language, request.ui_language)
            current = _sdk_language(app)
            rebuilt = current != previous
            if rebuilt:
                coordinator = getattr(app.state, "sdk_write_coordinator", None)
                if coordinator is not None:
                    await coordinator.retire_stale_handles()
                # Reads rebuild on their next request (`ActiveProfileReadHandle`).
                diagnostic_event(
                    level="info",
                    component="memory.language",
                    event="sdk_language.changed",
                    outcome="succeeded",
                    details={
                        "sdk_language": current or SDK_LANGUAGE_AUTO,
                        "previous": previous or SDK_LANGUAGE_AUTO,
                    },
                )
        return UiLanguageChange(**_ui_language_state(app).model_dump(), rebuilt=rebuilt)

    @app.get(
        "/api/v1/settings/evolve-schedule",
        response_model=EvolveSchedule,
        dependencies=strict,
    )
    def get_evolve_schedule() -> EvolveSchedule:
        return _config_store(app).evolve_schedule()

    @app.put(
        "/api/v1/settings/evolve-schedule",
        response_model=EvolveSchedule,
        dependencies=strict,
    )
    async def put_evolve_schedule(request: EvolveScheduleUpdate) -> EvolveSchedule:
        """Turn the nightly evolve on or off, or move it (local `HH:MM`)."""
        store = _config_store(app)
        current = store.evolve_schedule()
        updated = EvolveSchedule(
            enabled=current.enabled if request.enabled is None else request.enabled,
            time=current.time if request.time is None else request.time,
        )
        await asyncio.to_thread(store.set_evolve_schedule, updated)
        return updated

    @app.post(
        "/api/v2/evolve",
        response_model=EvolveRunReceipt,
        status_code=202,
        dependencies=strict,
    )
    async def start_evolve(request: EvolveRequest) -> EvolveRunReceipt:
        """Run System 2 for one user, in the background; read the run by its id.

        Ultra only (409 `memory_mode_not_ultra`). One run per user: asking
        while one runs answers that run. The run waits for the user's write
        in flight and holds their next writes until it ends, and repeats
        while the SDK says there is more to do (up to 20 rounds).
        """
        async with app.state.memory_mode_lock:
            _refuse_pipeline_change_during_embedding_maintenance(app)
            if app.state.memory_graph_backfill.running:
                raise HTTPException(status_code=409, detail="memory_lifecycle_busy")
            try:
                return _evolve_service(app).start(
                    request.user_id,
                    agent_id=request.agent_id,
                    max_batches=request.max_batches,
                )
            except EvolveModeError:
                raise HTTPException(status_code=409, detail="memory_mode_not_ultra") from None

    @app.get(
        "/api/v2/evolve",
        response_model=EvolveOverview,
        dependencies=strict,
    )
    def evolve_overview(
        user_id: str | None = Query(default=None, pattern=USER_ID_PATTERN),
    ) -> EvolveOverview:
        """Last run per user and the runs in progress; one user's with `user_id`."""
        overview = _evolve_overview(app)
        if user_id is None:
            return overview
        return overview.model_copy(
            update={
                "users": {key: value for key, value in overview.users.items() if key == user_id},
                "running": tuple(run for run in overview.running if run.user_id == user_id),
            }
        )

    @app.get(
        "/api/v2/evolve/{run_id}",
        response_model=EvolveRunReceipt,
        dependencies=strict,
    )
    def get_evolve_run(
        run_id: str,
        user_id: str = Query(default=DEFAULT_USER_ID, pattern=USER_ID_PATTERN),
    ) -> EvolveRunReceipt:
        """One run; another user's run answers exactly like a missing one."""
        receipt = _evolve_service(app).get(run_id)
        if receipt is None or receipt.user_id != user_id:
            raise HTTPException(status_code=404, detail="evolve_run_not_found")
        return receipt

    @app.get(
        "/api/v1/graph-width",
        response_model=GraphWidthStatus,
        dependencies=protected,
    )
    async def get_graph_width() -> GraphWidthStatus:
        """Report the mismatch. Never writes: this is polled by the app."""
        try:
            mismatch = await _sdk_memory_runtime(app).graph_width_mismatch()
        except HySdkError as error:
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
        supervisor = _sdk_memory_runtime(app)
        _refuse_write_during_embedding_switch(app)
        try:
            outcome = await supervisor.decide_graph_width(request.decision)
        except HySdkError as error:
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
        return app.state.extraction_settings_store.get()

    @app.put(
        "/api/v1/extraction-settings",
        response_model=ExtractionSettings,
        dependencies=protected,
    )
    def put_extraction_settings(request: ExtractionWindowUpdate) -> ExtractionSettings:
        return app.state.extraction_settings_store.set_window_preset(request.window_preset)

    @app.get(
        "/api/v1/sedimentation",
        response_model=SedimentationState,
        dependencies=protected,
    )
    def get_sedimentation() -> SedimentationState:
        """Whether the model may still turn conversations into memories."""
        return SedimentationState(enabled=_config_store(app).sedimentation_enabled())

    @app.put(
        "/api/v1/sedimentation",
        response_model=SedimentationState,
        dependencies=protected,
    )
    async def put_sedimentation(request: SedimentationRequest) -> SedimentationState:
        """Start or stop model-driven sedimentation, keeping the configuration.

        Stopping is the user's cost control: nothing further goes to the model
        and already queued work remains pending rather than failing. Starting
        again lets the normal claim tick continue it; this route never broadly
        re-admits permission/model parked jobs. Importing conversations is not
        affected either way.
        """
        store = _config_store(app)
        async with app.state.sedimentation_lock:
            previous = store.sedimentation_enabled()
            if previous is not request.enabled:
                await asyncio.to_thread(store.set_sedimentation_enabled, request.enabled)
                diagnostic_event(
                    level="info",
                    component="memory.sedimentation",
                    event="memory_sedimentation.toggled",
                    outcome="succeeded",
                    details={"enabled": request.enabled},
                )
        return SedimentationState(enabled=store.sedimentation_enabled())

    @app.post(
        "/api/v1/memory-processing/manual-extract",
        response_model=ManualExtractReceipt,
        dependencies=protected,
    )
    def manual_extract(request: ManualExtractRequest) -> ManualExtractReceipt:
        supervisor = _sdk_memory_runtime(app)
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
    known = await in_sdk_read(_evidence_store(app).known_workspaces, user_id="local")
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


def _ask_filters(request: AskMemoryRequest) -> dict[str, SdkSearchFilters]:
    """The answerer's `filters` argument, only when the caller set any."""
    return {"filters": request} if request.search_overrides() else {}


def _sdk_request_answerer(app: FastAPI) -> SdkMemoryAnswerer:
    return SdkMemoryAnswerer(
        client_handle=_sdk_client_handle(app),
        profiles=_model_profile_store(app),
        secrets=_secret_store(app),
        authorize_generation=lambda profile, question, grounding: _authorize_sdk_answer_generation(
            app,
            profile,
            question,
            grounding,
        ),
    )


async def _authorize_sdk_answer_generation(
    app: FastAPI,
    profile: ModelProfile,
    question: str,
    grounding: tuple[SdkAskMemory, ...],
) -> Callable[[], Awaitable[None]]:
    if not question.strip() or not grounding:
        raise MemoryAnswerError("memory_answer_generation_not_authorized")

    async def recheck() -> None:
        identity = await asyncio.to_thread(_model_profile_store(app).active_identity)
        if identity != (profile.profile_id, profile.revision):
            raise MemoryAnswerError("memory_answer_generation_not_authorized")
        handle = _sdk_client_handle(app)
        for memory in grounding:
            current = await _get_sdk_memory(handle, memory.memory_id)
            if current is None:
                raise MemoryAnswerError("memory_answer_generation_not_authorized")
            projected = SdkAskMemory(
                memory_id=current.memory_id,
                content=current.content,
                layer=current.layer,
                status=current.status,
                source=current.source,
                gmt_created=current.gmt_created,
            )
            if projected != memory:
                raise MemoryAnswerError("memory_answer_generation_not_authorized")

    await recheck()
    return recheck


class _LazyASGIApp:
    """Compatibility ASGI app that is built only when a server imports it.

    `create_app()` must not run at import time. The managed `serve()` entry
    imports `create_app` from this module before it can construct its own app
    with `manage_runtime=True`; an eager module-level app was therefore built
    first with the default `manage_runtime=False`, doing startup work for an
    app that never serves.
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


def _sync_refused(code: str) -> HTTPException:
    """A sync refusal that says which stage refused.

    Turning the switch on takes four steps: authorize, prepare, write, resume.
    The UI used to be told only that the switch had sprung back, and every
    stage needs a different fix, so each one gets its own code.
    """
    return HTTPException(status_code=409, detail=code)


def _lane_sync_enabled(config_store: ConfigStore, lane: str) -> bool:
    """Whether a lane may sync, before its grant is asked.

    `local` keeps the App's per-source switch in the configuration document,
    which stays global. Another user's Hermes grant syncs for as long as it
    stands: connecting it is the consent, revoking it stops it, and it never
    turns the App's switch on or off.
    """
    user_id, source_id = split_lane(lane)
    if user_id == DEFAULT_USER_ID:
        return config_store.sync_enabled(source_id)
    return source_id in MULTI_USER_SOURCES


def _other_user_lanes(
    catalog: dict[str, SourceRegistration],
    grant_store: SourceGrantStore,
) -> tuple[str, ...]:
    """Lanes of every unrevoked grant held by a user other than `local`."""
    return tuple(
        lane_key(receipt.user_id, receipt.source_id)
        for receipt, _root in grant_store.grants()
        if receipt.user_id != DEFAULT_USER_ID
        and receipt.revoked_at is None
        and receipt.source_id in MULTI_USER_SOURCES
        and receipt.source_id in catalog
    )


def _known_user_ids(app: FastAPI) -> tuple[str, ...]:
    """Every user whose data Core keeps, from Core's own records.

    The SDK has no "list users": `local`, every user holding a grant (a
    Hermes profile), and every user whose turns or notes passed the write
    queue (`POST /api/v2/memories` or `/api/v2/memory-writes` with a user).
    """
    users = [DEFAULT_USER_ID]
    for receipt, _root in _grant_store(app).grants():
        if receipt.user_id not in users:
            users.append(receipt.user_id)
    queue = getattr(app.state, "memory_write_queue", None)
    if queue is not None:
        with main_database(queue.database_path).read() as database:
            for (source_key,) in database.execute(
                "SELECT DISTINCT source_key FROM memory_write_jobs"
            ):
                user_id = split_lane(str(source_key))[0]
                if user_id not in users:
                    users.append(user_id)
    return tuple(users)


async def _users_with_memories(app: FastAPI) -> tuple[str, ...]:
    """Known users the SDK holds any memory for: the nightly evolve's users."""
    users = await asyncio.to_thread(_known_user_ids, app)
    client = await _sdk_client_handle(app).client()
    found: list[str] = []
    for user_id in users:
        try:
            stats = await asyncio.to_thread(client.memory_stats, user_id=user_id)
        except Exception:
            continue
        scenes = stats.get("scenes") if isinstance(stats, dict) else None
        total = stats.get("total") if isinstance(stats, dict) else None
        facts = sum(
            int(scene.get("facts") or 0)
            for scene in (scenes or {}).values()
            if isinstance(scene, dict)
        )
        if (isinstance(total, int) and total > 0) or facts > 0:
            found.append(user_id)
    return tuple(found)


def _granted_roots(
    source_ids: Iterable[str],
    grant_store: SourceGrantStore,
    config_store: ConfigStore,
) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for lane in source_ids:
        if not _lane_sync_enabled(config_store, lane):
            continue
        user_id, source_id = split_lane(lane)
        try:
            grant = grant_store.authorized_root(user_id=user_id, source_id=source_id)
        except SourceAccessError:
            continue
        if grant is not None:
            roots[lane] = grant.root
    return roots


def _active_adapters(
    catalog: dict[str, SourceRegistration],
    grant_store: SourceGrantStore,
) -> dict[str, SourceAdapter]:
    adapters: dict[str, SourceAdapter] = {}
    for lane in _other_user_lanes(catalog, grant_store):
        user_id, source_id = split_lane(lane)
        try:
            grant = grant_store.authorized_root(user_id=user_id, source_id=source_id)
            if grant is not None:
                adapters[lane] = catalog[source_id].create_adapter(grant)
        except SourceAccessError:
            continue
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


def _registration_at_grant(
    grant_store: SourceGrantStore,
    registration: SourceRegistration,
    user_id: str,
) -> SourceRegistration:
    """The registration with its candidate at `user_id`'s granted root, if any."""
    for receipt, root_path in grant_store.grants():
        if receipt.user_id == user_id and receipt.source_id == registration.descriptor.source_id:
            return replace(
                registration,
                candidate=replace(
                    registration.candidate,
                    path=Path(root_path),
                    kind=RootCandidateKind.USER_SELECTED,
                ),
            )
    return registration


def _source_response(
    registration: SourceRegistration,
    receipt: SourceGrantReceipt | None,
    sync_enabled: bool,
    detection: SourceInstallState | None,
    home: Path,
    totals: tuple[int, int],
    *,
    candidate_change_reason: str | None = None,
    authorization_state: SourceAuthorizationState = SourceAuthorizationState.OK,
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


def _require_multi_user_source(source_id: str, user_id: str) -> None:
    """Refuse another user's lane of a source that holds `local`'s roots only."""
    if user_id != DEFAULT_USER_ID and source_id not in MULTI_USER_SOURCES:
        raise HTTPException(status_code=422, detail="user_id_requires_multi_user_source")


def _diagnostic_request_event(method: str, request_path: str) -> str | None:
    if method == "GET" or request_path in {"/api/v1/health", "/api/v1/runtime/identity"}:
        return None
    families = (
        ("/api/v1/import", "import.request"),
        ("/api/v1/sources", "source.request"),
        ("/api/v2/import", "import.request"),
        ("/api/v2/sources", "source.request"),
        ("/api/v2/evolve", "memory.request"),
        ("/api/v1/model", "model.request"),
        ("/api/v1/memor", "memory.request"),
        ("/api/v2/context", "recall.request"),
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


#: Every state a job can still leave: the ones a revoke or re-root cancels.
_UNFINISHED_IMPORT_STATUSES = (
    ImportStatus.PENDING,
    ImportStatus.RUNNING,
    ImportStatus.PAUSED,
    ImportStatus.INTERRUPTED,
    ImportStatus.FAILED,
)


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
    """`source_id` is a lane key: another user's lane asks that user's grant."""
    if not _lane_sync_enabled(_config_store(app), source_id):
        return False
    user_id, bare_source_id = split_lane(source_id)
    try:
        return (
            _grant_store(app).authorized_root(user_id=user_id, source_id=bare_source_id) is not None
        )
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


def _recall_target(app: FastAPI) -> GovernedRecallTarget | SdkRecallTarget:
    return cast(GovernedRecallTarget | SdkRecallTarget, app.state.recall_target)


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
    """Return one shared cutoff when any selected harness has large, old history.

    The cutoff is decided before any history job is created, then persisted on
    every accepted job. This is deliberately global: independently limiting
    only the large source would leave different applications on incompatible
    timelines.
    """
    if not request.onboarding:
        return None
    anchor = now or datetime.now(UTC)
    cutoff = anchor - ONBOARDING_HISTORY_WINDOW
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
            if await _has_large_old_history(adapter, cutoff=cutoff):
                return cutoff
        except (OSError, SourceAccessError):
            # A supported source whose metadata count cannot be completed is
            # treated conservatively. The later connection still owns the
            # precise authorization error, while onboarding never falls back
            # to an unbounded history import.
            return cutoff
    return None


async def _has_large_old_history(adapter: SourceAdapter, *, cutoff: datetime) -> bool:
    """Probe one authorized Harness with the same parsers the import will use.

    Stop as soon as both policy facts are proven: more than 200 genuine person
    turns and at least one record older than the three-day cutoff. Counting
    parsed Evidence instead of transcript files prevents one giant coding
    session from bypassing the limit, while reusing the adapters keeps host
    envelopes and tool-shaped user records out of the count.
    """

    user_turns = 0
    has_old_data = False
    async for event in adapter.scan(ScanOptions()):
        if isinstance(event, ScanFailure):
            # A partial probe must never turn into an unbounded first import.
            return True
        if not isinstance(event, ScannedEvidence):
            continue
        observed = event.evidence.created_at
        if observed is None and event.cursor.observed_modified_ns > 0:
            observed = datetime.fromtimestamp(
                event.cursor.observed_modified_ns / 1_000_000_000,
                tz=UTC,
            )
        if observed is not None:
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=UTC)
            has_old_data = has_old_data or observed.astimezone(UTC) < cutoff
        if event.evidence.role == "user":
            user_turns += 1
        if user_turns > ONBOARDING_LARGE_HISTORY_USER_TURNS and has_old_data:
            return True
    return False


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


def _secret_store(app: FastAPI) -> SecretStore:
    return cast(SecretStore, app.state.secret_store)


def _secret_store_detail(error: SecretStoreUnavailableError) -> str:
    """Why the store failed, as the code the frontend branches on.

    "Click Allow", "this is slow, try again", and "there is no store here" are
    three different instructions; sending one string for all of them is why a
    user whose Keychain needs one click was told the store was broken.
    """
    return str(getattr(error, "code", None) or SecretStoreErrorCode.STORE_UNAVAILABLE)


_LEGACY_DELETION_SYNC_IDLE_SECONDS = 60.0


def _memory_store(app: FastAPI) -> MemoryGovernanceStore:
    return cast(MemoryGovernanceStore, app.state.memory_governance_store)


def _read_access(app: FastAPI) -> SourceReadAccess:
    return cast(SourceReadAccess, app.state.source_read_access)


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


#: How often finished live syncs are pruned while the Core runs, and how many
#: bounded batches one such pass may take (`prune_finished_sync_jobs`).
IMPORT_JOB_PRUNE_SECONDS = 600.0
IMPORT_JOB_PRUNE_PASS_BATCHES = 10


async def _prune_finished_import_jobs(
    store: ImportJobStore,
    *,
    interval_seconds: float = IMPORT_JOB_PRUNE_SECONDS,
    pass_batches: int = IMPORT_JOB_PRUNE_PASS_BATCHES,
) -> None:
    """Keep `import_jobs` bounded: at startup down to the keep count, then on a timer.

    Every batch is its own short write transaction, made off the loop, with a
    pause between batches so the writer's line keeps moving. The first pass
    runs until nothing is left, which on a home that grew for weeks is the
    one long pass; after it each pass is at most `pass_batches` batches.
    A busy database skips the rest of a pass rather than failing it.
    """
    first = True
    while True:
        removed_total = 0
        batches = 0
        while first or batches < pass_batches:
            try:
                removed = await in_the_background(store.prune_finished_sync_jobs)
            except sqlite3.OperationalError as error:
                if "locked" not in str(error).lower() and "busy" not in str(error).lower():
                    raise
                logger.info("import_jobs_prune_deferred error_type=%s", type(error).__name__)
                break
            batches += 1
            removed_total += removed
            if removed < PRUNE_BATCH_ROWS:
                break
            await asyncio.sleep(0.05)
        if removed_total:
            logger.info(
                "import_jobs_pruned removed=%d batches=%d startup=%s",
                removed_total,
                batches,
                first,
            )
        first = False
        await asyncio.sleep(interval_seconds)


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
            level="debug",
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
    if app.state.memory_authority is MemoryAuthority.SDK:
        app.state.memory_backfill = None
        return
    running = cast("asyncio.Task[None] | None", getattr(app.state, "memory_backfill", None))
    if running is not None and not running.done():
        running.cancel()
    app.state.memory_backfill = watched_task(
        asyncio.get_running_loop(),
        lambda: _run_backfill(app),
        name="one-memory-memory-backfill",
    )


def _resume_sedimentation(app: FastAPI) -> None:
    """Pick the work parked by a stop back up, off the caller's clock.

    Turning the switch back on is a decision, not a job the caller waits for:
    the choice is already stored, and what follows it is maintenance over every
    unfinished task followed by a scan of the whole queue. On a real backlog
    that runs for many minutes, so awaiting it here left the PUT unanswered
    until the client gave up — the renderer showed "启用未能确认" for a switch
    that had in fact been saved, and the switch only appeared to turn on once
    the follow-up read came back. Measured: one toggle, twenty-plus minutes,
    no response, client timeout at thirty seconds.

    Repeated toggles reuse the running walk; it reads the switch as it goes, so
    a stop part-way through is honoured on the next batch instead of needing a
    cancel.
    """
    running = cast("asyncio.Task[None] | None", getattr(app.state, "sedimentation_resume", None))
    if running is not None and not running.done():
        app.state.sedimentation_resume_again = True
        return

    async def resume_until_stable() -> None:
        while True:
            app.state.sedimentation_resume_again = False
            await _sdk_memory_runtime(app).resume_allowed()
            if not app.state.sedimentation_resume_again:
                return

    app.state.sedimentation_resume = watched_task(
        asyncio.get_running_loop(),
        resume_until_stable,
        component="memory.sedimentation",
        name="one-memory-sedimentation-resume",
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
    if app.state.memory_authority is MemoryAuthority.SDK or memory_visibility.mode() == "exact":
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
        details={"phase": phase},
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
                # Catch-up is history: the last turn and a short batch are written.
                for batch in turn_batches(conversation, flush=True).batches:
                    if not any(item.evidence_id in evidence_ids for item in batch):
                        continue
                    await asyncio.to_thread(intake.submit_records, batch, priority="normal")
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


def _batches_settled_by(
    batches: tuple[tuple[EvidenceRecord, ...], ...],
    record: EvidenceRecord,
) -> list[tuple[EvidenceRecord, ...]]:
    """The batches one arriving record can have made writable.

    Its own batch, and the last two: a user message completes the turn
    before it, which can close that turn's batch and, at the 8000-character
    cut, the batch before it. Submitting is idempotent, so an older batch
    named again is only a hit.
    """
    chosen = [batch for batch in batches if record in batch]
    chosen.extend(batch for batch in batches[-2:] if batch not in chosen)
    return chosen


def _sdk_memory_runtime(app: FastAPI) -> SdkMemoryRuntime:
    return cast(
        SdkMemoryRuntime,
        app.state.sdk_memory_runtime,
    )


async def _recall_model_ready(app: FastAPI) -> bool:
    """A generation model is configured and usable, so memories can exist.

    In lite nothing is extracted whatever model is configured: recall reads
    the raw conversations.
    """
    if _memory_mode(app) == "lite":
        return False
    try:
        profile = await in_sdk_read(_model_profile_store(app).active, _secret_store(app))
    except Exception:
        return False
    return profile is not None and bool(profile.capabilities.model_available)


def _sdk_recall_target(
    app: FastAPI,
    user_id: str,
    options: SdkSearchFilters | None = None,
) -> SdkRecallTarget:
    """Recall over one user's SDK memories; `get` results are kept only if theirs.

    With a caller's pass-through `options`, the search carries them and a
    working/coding hit is read back from its own namespace.
    """
    handle = _sdk_client_handle(app)
    namespaces: dict[str, dict[str, str]] = {}

    async def owned(memory_id: str) -> HySdkMemoryRecord | None:
        memory = await _get_sdk_memory(handle, memory_id, **namespaces.get(memory_id, {}))
        return memory if memory is not None and owns(memory.user_id, user_id) else None

    async def search(query: str) -> tuple[object, ...]:
        hits = await _search_sdk_memories(
            handle, query, user_id=user_id, agent_id="*", options=options
        )
        for hit in hits:
            if isinstance(hit, SemanticMemoryHit) and hit.variant is not None:
                namespaces[hit.memory_id] = {"kind": hit.kind, "variant": hit.variant}
        return hits

    return SdkRecallTarget(semantic_search=search, get_memory=owned)


async def _library_has_memories(app: FastAPI, user_id: str = DEFAULT_USER_ID) -> bool:
    """Any active memory at all. Asked only after an empty memory search."""
    try:
        client = await _sdk_client_handle(app).client()
        raw = await in_sdk_read(
            client.list_memories,
            user_id=user_id,
            statuses=["active"],
            limit=1,
            offset=0,
            order="desc",
        )
        page, _ = HySdkMemoryPage.from_sdk_skipping_invalid(raw, limit=1, offset=0)
    except Exception:
        # Unknown is not "empty": keep the memory answer rather than guess.
        return True
    return page.total > 0


async def _recall_raw_sources(
    app: FastAPI,
    request: RecallRequest,
    access: ExternalMemoryAccess | None,
    started: float,
) -> frozenset[str] | None:
    """The raw sources this caller may read: the `/context` raw-lane rule.

    `om_conversations` plus `all-apps` reads every connected, unrevoked source.
    A trusted local caller reads every source without an external grant.
    """
    if access is None:
        return None
    from one_memory.context_access import resolve_context_authority
    from one_memory.context_models import ContextSearchRequest

    query = ContextSearchRequest(
        query=request.query,
        kinds=("raw",),
        limit=request.limit,
        character_budget=request.character_budget,
        timeout_ms=request.timeout_ms,
    )
    try:
        authority = await in_sdk_read(
            resolve_context_authority,
            query,
            access,
            _evidence_store(app).database_path,
            deadline=started + request.timeout_ms / 1_000,
        )
    except (HTTPException, TimeoutError):
        return frozenset()
    if not authority.raw_allowed:
        return frozenset()
    return frozenset(authority.raw_sources or ())


async def _search_sdk_raw(
    app: FastAPI,
    query: str,
    *,
    limit: int,
    sources: frozenset[str] | None,
    user_id: str = DEFAULT_USER_ID,
    filters: SdkSearchFilters | None = None,
) -> tuple[HySdkRawHit, ...]:
    """The imported conversations nearest the query: the SDK raw layer.

    `filters` are a caller's pass-through options: its floor and row filters
    apply, and its `include_sources` only narrow `sources`, never widen them.
    """
    from one_memory.context_routes import raw_lane_overrides, raw_lane_statuses

    include = sorted(sources) if sources else None
    if filters is not None and filters.include_sources is not None:
        include = [
            source
            for source in dict.fromkeys(filters.include_sources)
            if sources is None or source in sources
        ]
        if not include:
            return ()
    client = await _sdk_client_handle(app).client()
    raw = await in_sdk_read(
        client.search,
        query,
        **{
            "user_ids": [user_id],
            "agent_ids": [],
            "include_sources": include,
            "limit": limit,
            "min_score": 0.0,
            "schema_min_score": 0.0,
            "schema_limit": 0,
            "intention_limit": 0,
            "scene": "chat",
            "reader": HY_MEMORY_READER,
            "layers": ["l1_raw"],
            # Lite recall is plain RAG over every raw, shadow included.
            **raw_lane_statuses(lite=app.state.memory_mode() == "lite"),
            **(raw_lane_overrides(filters) if filters is not None else {}),
        },
    )
    hits = parse_sdk_raw_search(raw)
    if filters is None:
        return hits
    return tuple(hit for hit in hits if filters.passes_floor("raw", hit.score))


def _sdk_client_handle(app: FastAPI) -> SdkClientHandle:
    handle = cast(SdkClientHandle | None, app.state.sdk_client_handle)
    if handle is None:
        # Reads open the embedding space writes fill: the active profile's,
        # the built-in one only when none is active (#242). A profile, a
        # memory-mode or a feature change rebuilds the client.
        handle = ActiveProfileReadHandle(
            profiles=_model_profile_store(app),
            secrets=_secret_store(app),
            data_dir=cast(Settings, app.state.settings).data_dir,
            memory_mode=lambda: _memory_mode(app),
            features=lambda: _memory_features(app),
            spaces=getattr(app.state, "embedding_spaces", None),
            language=lambda: _sdk_language(app),
        )
        app.state.sdk_client_handle = handle
    return handle


def _memory_mode(app: FastAPI) -> MemoryMode:
    """The mode SDK clients are built with: the setting, or ultra forced by env."""
    store = getattr(app.state, "config_store", None)
    configured = store.memory_mode() if store is not None else "pro"
    return cast(MemoryMode, effective_memory_mode(configured))


def _memory_mode_state(app: FastAPI) -> MemoryModeState:
    mode = _memory_mode(app)
    store = _config_store(app)
    return MemoryModeState(
        mode=mode,
        configured_mode=store.memory_mode(),
        explicitly_configured=store.memory_mode_explicitly_configured(),
        forced_by_env=graph_store_enabled(),
        graph_build=app.state.evolve_state_store.graph_build(),
        graph_store=_graph_store_status(app),
    )


def _graph_store_status(app: FastAPI) -> GraphStoreStatus:
    guard = cast(GraphOpenGuard | None, getattr(app.state, "graph_open_guard", None))
    if guard is None:
        return GraphStoreStatus()
    backfill = getattr(app.state, "memory_graph_backfill", None)
    running: bool | None = bool(backfill is not None and backfill.running) or (
        app.state.evolve_state_store.graph_build().state == "running"
    )
    if getattr(app.state, "recovery", None) not in {"done", "failed"}:
        # The rebuild is started by post-ready recovery; until that has run,
        # "not running" does not mean "finished".
        running = None
    return guard.status(rebuild_running=running)


async def _open_graph_store_at_startup(app: FastAPI) -> GraphOpenOutcome:
    """Run the guarded first open of the live ultra graph; see `graph_open_guard`."""
    guard = cast(GraphOpenGuard, app.state.graph_open_guard)
    if app.state.memory_authority is not MemoryAuthority.SDK or _memory_mode(app) != "ultra":
        guard.mark_not_used()
        return "skipped"
    live = app.state.embedding_spaces.live()
    graph_path = live_graph_path(
        cast(Settings, app.state.settings).data_dir,
        live.graph_dir if live is not None else None,
    )
    switch = getattr(app.state, "embedding_switch", None)
    switch_in_progress = bool(switch is not None and switch.jobs.open_job() is not None)
    return await asyncio.to_thread(
        guard.open_at_startup, graph_path, switch_in_progress=switch_in_progress
    )


def _memory_mode_plan(app: FastAPI, target: MemoryMode) -> MemoryModePlan:
    """One validator for previews and mutations, in stable blocker order."""
    current = _memory_mode(app)
    effective_target = cast(MemoryMode, effective_memory_mode(target))
    blockers: list[MemoryModeBlocker] = []
    if not _embedding_writes_open(app):
        blockers.append("embedding_maintenance_in_progress")
    if (
        effective_target != current
        and effective_target in {"pro", "ultra"}
        and _model_profile_store(app).active_identity() is None
    ):
        blockers.append("active_model_required")
    if effective_target != current and (
        _evolve_service(app).running() or app.state.memory_graph_backfill.running
    ):
        blockers.append("memory_lifecycle_busy")
    if (
        effective_target != current
        and effective_target == "ultra"
        and importlib.util.find_spec("kuzu") is None
    ):
        blockers.append("graph_store_unavailable")

    warnings: list[MemoryModeWarning] = []
    if graph_store_enabled():
        warnings.append("forced_by_env")
    if effective_target == current:
        warnings.append("effective_no_change")

    graph_backfill: bool | None = None
    if effective_target == "ultra" and current != "ultra":
        graph_backfill = not app.state.memory_graph_backfill.running
    effects = memory_mode_effects(
        current,
        effective_target,
        graph_backfill=graph_backfill,
    )
    return MemoryModePlan(
        current=current,
        target=target,
        effective_target=effective_target,
        capabilities=home_capabilities(effective_target),
        effects=() if blockers else effects,
        warnings=tuple(warnings),
        blockers=tuple(blockers),
    )


def _evolve_service(app: FastAPI) -> EvolveService:
    return cast(EvolveService, app.state.evolve_service)


def _evolve_overview(app: FastAPI) -> EvolveOverview:
    document = app.state.evolve_state_store.document()
    return EvolveOverview(
        mode=_memory_mode(app),
        schedule=_config_store(app).evolve_schedule(),
        users=document.users,
        running=app.state.evolve_service.running(),
    )


def _memory_features(app: FastAPI) -> FeatureOverrides:
    """The SDK features a person set, as the key every SDK client is built with."""
    store = getattr(app.state, "config_store", None)
    return store.memory_features().overrides() if store is not None else ()


def _sdk_language(app: FastAPI) -> str | None:
    """The SDK prompt language every client is built with; None = auto-detect."""
    store = getattr(app.state, "config_store", None)
    return sdk_language(store.ui_language()) if store is not None else None


def _ui_language_state(app: FastAPI) -> UiLanguageState:
    store = getattr(app.state, "config_store", None)
    return UiLanguageState(
        ui_language=store.ui_language() if store is not None else None,
        sdk_language=_sdk_language(app) or SDK_LANGUAGE_AUTO,
    )


def _memory_features_state(
    app: FastAPI, defaults: dict[str, SettingValue] | None = None
) -> MemoryFeaturesState:
    defaults = sdk_feature_defaults() if defaults is None else defaults
    overrides = _memory_features(app)
    configured = dict(overrides)
    effective = effective_features(overrides, defaults)
    mode = _memory_mode(app)
    features = []
    for spec in FEATURES:
        reason = feature_activity(spec.name, effective, mode)
        features.append(
            MemoryFeatureState(
                name=spec.name,
                enabled=bool(effective[spec.name]),
                configured=cast(bool | None, configured.get(spec.name)),
                default=bool(defaults[spec.name]),
                active=reason is None,
                reason=reason,
                side=spec.side,
                requires_mode=spec.requires_mode,
                requires_feature=spec.requires_feature,
                cost=spec.cost,
                sdk_field=spec.sdk_field,
                env=spec.env,
                description=spec.description,
            )
        )
    parameters = []
    for parameter in PARAMETERS:
        reason = feature_activity(parameter.name, effective, mode)
        parameters.append(
            MemoryParameterState(
                name=parameter.name,
                value=parameter_value(parameter.name, effective[parameter.name]),
                configured=configured.get(parameter.name),
                default=parameter_value(parameter.name, defaults[parameter.name]),
                minimum=parameter_value(parameter.name, parameter.minimum),
                maximum=parameter_value(parameter.name, parameter.maximum),
                active=reason is None,
                reason=reason,
                requires_feature=parameter.requires_feature,
                requires_mode=parameter.requires_mode,
                sdk_field=parameter.sdk_field,
                env=parameter.env,
                description=parameter.description,
            )
        )
    return MemoryFeaturesState(
        mode=mode,
        features=tuple(features),
        parameters=tuple(parameters),
        non_default=cast(
            dict[MemorySettingName, SettingValue], non_default_features(overrides, defaults)
        ),
    )


def _pipeline_status(app: FastAPI) -> PipelineStatus | None:
    """R10's `pipeline`, or None before the evolve state exists (legacy tests)."""
    if getattr(app.state, "evolve_state_store", None) is None:
        return None
    state = _memory_mode_state(app)
    overview = _evolve_overview(app)
    finished = [
        user.last_run_at for user in overview.users.values() if user.last_run_at is not None
    ]
    return PipelineStatus(
        mode=state.mode,
        configured_mode=state.configured_mode,
        forced_by_env=state.forced_by_env,
        evolve=overview,
        last_evolve_at=max(finished, default=None),
        graph_build=state.graph_build,
        graph_store=state.graph_store,
        features=cast(
            dict[MemorySettingName, SettingValue],
            non_default_features(_memory_features(app), sdk_feature_defaults()),
        ),
        **_ui_language_state(app).model_dump(exclude={"languages"}),
    )


def _read_handle(app: FastAPI) -> ActiveProfileReadHandle:
    return cast(ActiveProfileReadHandle, _sdk_client_handle(app))


def _log_skipped_sdk_memories(count: int) -> None:
    if count:
        logger.warning("sdk_memory_invalid_rows_skipped count=%d", count)


# `client.get` funnels into the SDK's single loop thread, so more workers only
# queue there while holding the default executor every other request needs.
_SDK_GET_CONCURRENCY = 8


async def _get_sdk_rows(client: Any, hits: list[LibraryHit]) -> list[object]:
    gate = asyncio.Semaphore(_SDK_GET_CONCURRENCY)

    async def one(hit: LibraryHit) -> object:
        async with gate:
            # Name the namespace the search found the row in, so an id that
            # also exists elsewhere cannot make the read ambiguous.
            return await asyncio.to_thread(
                client.get, hit.memory_id, **_namespace_kwargs(hit.kind, hit.variant)
            )

    return list(await asyncio.gather(*(one(hit) for hit in hits)))


async def _get_sdk_memory(
    handle: SdkClientHandle,
    memory_id: str,
    *,
    kind: str | None = None,
    variant: str | None = None,
) -> HySdkMemoryRecord | None:
    client = await handle.client()
    raw = await in_sdk_read(client.get, memory_id, **_namespace_kwargs(kind, variant))
    return None if raw is None else HySdkMemoryRecord.from_sdk(raw)


def _namespace_kwargs(kind: str | None, variant: str | None) -> dict[str, str]:
    """The SDK's kind/variant arguments, only the ones the caller gave."""
    namespace: dict[str, str] = {}
    if kind is not None:
        namespace["kind"] = kind
    if variant is not None:
        namespace["variant"] = variant
    return namespace


def _check_memory_namespace(kind: str | None, variant: str | None) -> None:
    if kind == "chat" and variant is not None:
        raise HTTPException(status_code=422, detail="memory_variant_requires_productivity_kind")


# Keyword search reads one SDK relevance window this large, once per window.
LIBRARY_SEARCH_WINDOW = 200
# The memory panel's page size; the API still accepts 1..500 for the CLI.
LIBRARY_PAGE_SIZE = 20


def _library_search_windows(app: FastAPI) -> LibrarySearchWindows:
    windows = getattr(app.state, "library_search_windows", None)
    if windows is None:
        windows = LibrarySearchWindows()
        app.state.library_search_windows = windows
    return cast(LibrarySearchWindows, windows)


def _library_values(values: list[str], allowed: tuple[str, ...], code: str) -> list[str]:
    """Repeated or comma-separated query values, each one the SDK knows."""
    chosen = split_csv(values)
    if any(value not in allowed for value in chosen):
        raise HTTPException(status_code=422, detail=code)
    return chosen


def _list_total(raw: object) -> int:
    """A chat-store `list_memories` reply's total: its vector and graph buckets."""
    if not isinstance(raw, dict):
        raise HySdkReadError("hy_memory_list_shape_unknown")
    total = 0
    for name in ("vdb", "graph"):
        bucket = raw.get(name)
        if bucket is None:
            continue
        value = bucket.get("total") if isinstance(bucket, dict) else None
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise HySdkReadError("hy_memory_list_shape_unknown")
        total += value
    return total


async def _library_search_page(
    app: FastAPI,
    client: Any,
    *,
    query: str,
    source: str | None,
    statuses: list[str],
    offset: int,
    limit: int,
    window_id: str | None,
    scene: str = "all",
    user_id: str = DEFAULT_USER_ID,
    filters: dict[str, list[str]] | None = None,
) -> HySdkMemoryPage:
    """One page of a keyword search, cut from a cached result window.

    SDK search has no count-only contract and, on the local store, no keyword
    channel. The search asks once for a bounded relevance window across chat,
    working and coding (no reranking, no score floor), keeps the rows that
    contain every word, and caches those matches under a `window_id`. Every
    page of that search is a slice of the same matches, read fresh from the
    SDK so edits and deletes show. The whole library is never listed in Core.

    `filters` (layers, exclude_sources, session_ids, agent_ids) reach the SDK
    search and are part of the window's identity. A layer filter keeps chat
    matches only: the SDK applies layers to the chat lane alone.
    """
    windows = _library_search_windows(app)
    filters = filters or {}
    # The user is part of the window's identity: one user's `window_id` must
    # never cut a page from another user's matches.
    key = window_key(query, source, statuses, scene, user_id, filters)
    window = windows.get(window_id, key) if window_id else None
    window_reset = window_id is not None and window is None
    if window is None:
        fetch = LIBRARY_SEARCH_WINDOW
        search_raw = await asyncio.to_thread(
            client.search,
            query,
            scene="all",
            rerank=False,
            user_ids=[user_id],
            agent_ids=filters.get("agent_ids", []),
            include_sources=[source] if source else None,
            limit=fetch,
            min_score=0.0,
            working_fact_min_score=0.0,
            working_experience_min_score=0.0,
            schema_min_score=0.0,
            schema_limit=fetch,
            # The library lists stored memories; proactive intentions are a
            # recall-time lane the list never shows.
            intention_limit=0,
            reader=HY_MEMORY_READER,
            **{name: values for name, values in filters.items() if name != "agent_ids"},
        )
        hits, held = library_keyword_hits(search_raw, query)
        if "layers" in filters:
            hits = tuple(hit for hit in hits if hit.kind == "chat")
        if scene != "all":
            # One scene: keep that scene's matches from the same fused window.
            hits = tuple(
                hit
                for hit in hits
                if (hit.kind == "chat" if scene == "chat" else hit.variant == scene)
            )
        window = windows.put(key, hits, fetch if held >= fetch else None)
        if window_reset:
            offset = 0
    rows = await _get_sdk_rows(client, list(window.hits[offset : offset + limit]))
    memories: list[HySdkMemoryRecord] = []
    skipped = 0
    for row in rows:
        if row is None:
            continue
        try:
            record = HySdkMemoryRecord.from_sdk(row)
        except HySdkReadError:
            skipped += 1
            continue
        if record.status == "active" and owns(record.user_id, user_id):
            memories.append(record)
    _log_skipped_sdk_memories(skipped)
    return HySdkMemoryPage(
        memories=tuple(memories),
        total=len(window.hits),
        limit=limit,
        offset=offset,
        search_window=window.search_window,
        window_id=window.window_id,
        window_reset=window_reset,
    )


async def _search_sdk_memories(
    handle: SdkClientHandle,
    query: str,
    *,
    user_id: str,
    agent_id: str,
    limit: int = 12,
    options: SdkSearchFilters | None = None,
) -> tuple[object, ...]:
    client = await handle.client()
    overrides = options.search_overrides() if options is not None else {}
    raw = await in_sdk_read(
        client.search,
        query,
        **{
            "user_ids": [user_id],
            "agent_ids": [] if agent_id == "*" else [agent_id],
            "limit": limit,
            "min_score": sdk_relevance_floor(),
            "schema_min_score": sdk_relevance_floor(),
            "schema_limit": limit,
            "intention_limit": limit,
            "scene": "normal",
            "reader": HY_MEMORY_READER,
            **overrides,
        },
    )
    if options is None or not overrides:
        return parse_sdk_search(raw, error_code="hy_memory_search_shape_unknown")
    return tuple(
        hit
        for hit in parse_sdk_lane_search(raw, error_code="hy_memory_search_shape_unknown")
        if options.passes_floor(cast(SearchLane, hit.lane), hit.score)
    )


def _sdk_http_error(error: Exception, fallback: str) -> HTTPException:
    detail = error.code if isinstance(error, HySdkError) else fallback
    return HTTPException(status_code=503, detail=detail)


def _evidence_store(app: FastAPI) -> EvidenceStore:
    return cast(EvidenceStore, app.state.evidence_store)


def _source_writes_on(app: FastAPI, source_id: str) -> bool:
    """Whether this source's conversations may become memories.

    A hand-added memory is allowed unless its grant was explicitly revoked;
    a missing grant row is allowed. The SDK add path writes no grant row, so
    requiring one left every SDK-authority manual add unclaimable: Pending
    forever with attempts=0 while later jobs ran. Intake, claim and execute
    all ask this one question.
    """
    if split_lane(source_id)[1] == "one-memory-manual":
        # One switch for hand-added memories, whoever adds them: it is a
        # management setting, and management stays global.
        receipt = _grant_store(app).receipt(user_id="local", source_id="one-memory-manual")
        return receipt is None or receipt.revoked_at is None
    return _source_processing_enabled(app, source_id)


def _sedimentation_claim_enabled(app: FastAPI, source_id: str) -> bool:
    """Claim queued model work only when policy, source and model all admit it.

    lite calls no model, so it needs no active generation profile: the
    built-in local embedder stores the raw turns. A user evolve is running
    for waits until it is done.
    """

    return (
        _pipeline_writes_enabled(app)
        and _embedding_writes_open(app)
        and (_memory_mode(app) == "lite" or _model_profile_store(app).active_identity() is not None)
        and _history_import_gate_released(app)
        and _source_writes_on(app, source_id)
        and not _user_evolving(app, split_lane(source_id)[0])
    )


async def _admit_pipeline_invocation(
    app: FastAPI,
    source_id: str,
    requires_generation: bool,
    mark_invoked: Callable[[], Awaitable[bool]],
) -> bool:
    """Serialize the final source and pipeline checks with the durable mark."""

    source_lock = app.state.source_permission_locks.setdefault(source_id, asyncio.Lock())
    async with app.state.sedimentation_lock, source_lock:
        if requires_generation and not _config_store(app).sedimentation_enabled():
            raise RuntimeError("model_sedimentation_disabled")
        if not _embedding_writes_open(app):
            raise RuntimeError("embedding_switch_in_progress")
        if not _source_writes_on(app, source_id):
            raise RuntimeError("model_content_source_disabled")
        return await mark_invoked()


def _pipeline_writes_enabled(app: FastAPI) -> bool:
    """Raw-only lite writes run even when model sedimentation is paused."""

    return _memory_mode(app) == "lite" or _config_store(app).sedimentation_enabled()


def _model_claim_allowance(app: FastAPI) -> int | None:
    """A failed generation model never pauses raw-only lite ingestion."""

    backoff = cast(ModelBackoff, app.state.model_backoff)
    if _memory_mode(app) == "lite":
        return backoff.allowance(kind="embedding")
    return backoff.allowance()


def _user_evolving(app: FastAPI, user_id: str) -> bool:
    """Whether evolve holds this user's writes right now."""
    coordinator = getattr(app.state, "sdk_write_coordinator", None)
    evolving = getattr(coordinator, "evolving", None)
    return bool(callable(evolving) and evolving(user_id))


def _reset_model_backoff(app: FastAPI) -> None:
    """The user changed the model configuration: end any provider pause now."""
    backoff = getattr(app.state, "model_backoff", None)
    if isinstance(backoff, ModelBackoff):
        backoff.reset()


def _embedding_switch(app: FastAPI) -> EmbeddingSwitchService:
    return cast(EmbeddingSwitchService, app.state.embedding_switch)


def _embedding_writes_open(app: FastAPI) -> bool:
    """False while an embedding switch or merge runs, or the startup scan has not decided.

    Intake is not affected: conversations keep being queued, and sediment
    into the live space once the gate opens again.
    """
    switch = getattr(app.state, "embedding_switch", None)
    return switch is None or switch.writes_open()


def _embedding_switch_summary(app: FastAPI) -> dict[str, object] | None:
    switch = getattr(app.state, "embedding_switch", None)
    if switch is None:
        return None
    try:
        return cast(EmbeddingSwitchService, switch).summary()
    except sqlite3.Error:
        return None


def _refuse_write_during_embedding_switch(app: FastAPI) -> None:
    """Direct edits and deletes wait for the switch: 409, never a write into the old space."""
    if not _embedding_writes_open(app):
        raise HTTPException(status_code=409, detail="embedding_switch_in_progress")


def _refuse_pipeline_change_during_embedding_maintenance(app: FastAPI) -> None:
    """Keep mode/evolve lifecycle changes out of embedding maintenance."""
    if not _embedding_writes_open(app):
        raise HTTPException(status_code=409, detail="embedding_maintenance_in_progress")


_EMBEDDING_PATCH_FIELDS = frozenset(
    {"embedding_model", "embedding_provider_kind", "embedding_base_url", "embedding_dimensions"}
)


def _refuse_embedding_edit_during_switch(
    app: FastAPI, profile_id: str, patch: ModelProfilePatch
) -> None:
    """The profiles a running switch reads from or commits to keep their embedding."""
    if not (_EMBEDDING_PATCH_FIELDS & patch.model_fields_set):
        return
    switch = getattr(app.state, "embedding_switch", None)
    if switch is None:
        return
    row = cast(EmbeddingSwitchService, switch).jobs.open_job()
    if row is None:
        return
    active = _model_profile_store(app).active_identity()
    if profile_id == row["target_profile_id"] or (active is not None and active[0] == profile_id):
        raise HTTPException(status_code=409, detail="embedding_switch_running")


def _store_activation_preflight(app: FastAPI, profile_id: str) -> None:
    """The checks `ModelProfileStore.activate` makes, for the empty-home path."""
    profile = _model_profile_store(app).require(profile_id, _secret_store(app))
    if profile.provider_kind in {ProviderKind.HY_DEFAULT, ProviderKind.ANTHROPIC}:
        raise ModelProfileUnavailableError("model_provider_not_supported")
    if not profile.embedding_model:
        raise ModelProfileUnavailableError("embedding_model_required")
    capabilities = profile.capabilities
    if not (
        capabilities.endpoint_reachable
        and capabilities.model_available
        and capabilities.embedding_available
    ):
        raise ModelProfileUnavailableError("model_profile_probe_required")


def _history_import_gate_released(app: FastAPI) -> bool:
    """Hold first sedimentation until every accepted history import is terminal."""

    if getattr(app.state, "history_import_gate_released", False):
        return True
    onboarding = getattr(app.state, "onboarding_store", None)
    jobs = getattr(app.state, "import_job_store", None)
    if onboarding is None or jobs is None:
        return True
    state = onboarding.get()
    if state.status != "importing" or not state.selected_source_ids:
        app.state.history_import_gate_released = True
        return True
    selected = set(state.selected_source_ids)
    # Asked inside the drain's claim, per candidate: history jobs of `local`
    # only, in SQL, never the whole table.
    matching = [
        job
        for job in jobs.find_jobs(user_id=DEFAULT_USER_ID, operation="history")
        if job.source_id in selected
        and (state.started_at is None or job.created_at >= state.started_at)
    ]
    if {job.source_id for job in matching} != selected:
        return False
    active = {
        ImportStatus.PENDING,
        ImportStatus.RUNNING,
        ImportStatus.PAUSED,
        ImportStatus.INTERRUPTED,
    }
    released = all(job.status not in active for job in matching)
    if released:
        app.state.history_import_gate_released = True
    return released


def _model_prober(app: FastAPI) -> ModelProber:
    return cast(ModelProber, app.state.model_prober)


def _display_candidate_path(path: Path, home: Path) -> str:
    try:
        return f"~/{path.expanduser().relative_to(home.expanduser()).as_posix()}"
    except ValueError:
        return str(path.expanduser())
