export const CORE_SERVICE = "one-memory-core" as const;

/**
 * The one host-id grammar.
 *
 * A host id is an opaque label for the app a recall grant belongs to. It is
 * never a path component: the permission layer keys an installation by a
 * digest of the whole id, so a colon is a label separator, not a directory
 * separator.
 *
 * One app can be configured per profile, which is the reason for the single
 * optional `:<profile>` segment — DeepSeek Harness reports
 * `deepseek-harness:<profile>` and `host list` prints those ids.
 *
 * The CLI, the permission layer and the grant store all have to read the same
 * shape: an id that `host list` prints must be an id `host install` accepts.
 * There is one definition here rather than three regexes that drift.
 */
const HOST_SEGMENT_MAX = 64;
const PROFILE_SEGMENT_MAX = 32;

/**
 * Lower-case letters, digits and hyphens; first character alphanumeric; at
 * most one `:` followed by an alphanumeric-first profile segment.
 *
 * The trailing `(?![\s\S])` is "end of input". `$` alone would also match
 * before a final newline, so `"codex\n"` would pass a pattern a caller reads
 * as an exact match.
 */
export const HOST_ID_PATTERN = new RegExp(
  `^[a-z0-9][a-z0-9-]{0,${HOST_SEGMENT_MAX - 1}}(?::[a-z0-9][a-z0-9-]{0,${PROFILE_SEGMENT_MAX - 1}})?(?![\\s\\S])`,
);

/** Longest id the grammar can produce: host + ":" + profile. */
export const HOST_ID_MAX_LENGTH = HOST_SEGMENT_MAX + 1 + PROFILE_SEGMENT_MAX;

export function isHostId(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length <= HOST_ID_MAX_LENGTH &&
    HOST_ID_PATTERN.test(value)
  );
}

export type {
  ConnectionSetupCapability,
  ConnectionSetupExecute,
  ConnectionSetupItem,
  ConnectionSetupItemState,
  ConnectionSetupJournal,
  ConnectionSetupPreview,
  ConnectionSetupResult,
  ConnectionSetupSelection,
} from "./connection-setup.js";

export interface HealthResponse {
  status: "ok";
  service: typeof CORE_SERVICE;
  version: string;
  instance_id: string;
  /**
   * Whether Core is still catching up on conversations imported before it had
   * a model to read them with. Not a degraded state: recall answers
   * throughout. Absent from cores older than this field.
   */
  memory_backfill?: "running" | "done" | "failed";
  memory_backfill_error?: string | null;
}

export type ImportStatus =
  | "pending"
  | "running"
  | "paused"
  | "interrupted"
  | "completed"
  | "failed"
  | "cancelled";

export type ScanPhase = "discover" | "read" | "emit" | "done";

export interface HostPackageCapabilities {
  historical_import: "not_implemented" | "implemented";
  incremental_import: "not_implemented" | "implemented";
  live_sync: "not_implemented" | "implemented";
  recall: "not_implemented" | "implemented";
  installer: "not_implemented" | "implemented";
  verification: "fixture_only" | "real_replayed";
}

/**
 * How much of a source's own changes a live connection can follow.
 *
 * A source we can only read forward from never tells us something was deleted
 * upstream, so the user has to be told they are getting additions only rather
 * than a copy that tracks the source.
 */
export type SyncCompleteness =
  | "full_dynamic"
  | "additions_only"
  | "one_time_snapshot";

export interface SourceImportReceipt {
  source_id: string;
  measured_at: string;
  messages: number;
  conversations: number;
  person_messages: number;
  agent_messages: number;
  tool_messages: number;
  unknown_author_messages: number;
  unknown_time_messages: number;
  /** Bounded deterministic human-query samples, not a live job event feed. */
  query_examples?: Array<{
    evidence_id: string;
    evidence_revision: number;
    role: string;
    written_by: string;
    created_at: string | null;
    excerpt: string;
    truncated: boolean;
    query_start: number;
    query_end: number;
    projection_version: number;
  }>;
  examples: Array<{
    evidence_id: string;
    role: string;
    written_by: string;
    created_at: string | null;
    excerpt: string;
    truncated: boolean;
  }>;
}

/** Why a source cannot be read right now.
 *
 * `authorized: false` alone could not be acted on: a grant that was never
 * made, one that was revoked, and a history directory that moved all need a
 * different thing from the user, and used to get the same advice. Absent on a
 * Core that does not report it, which then means "not measured".
 */
export type SourceAuthorizationState =
  | "ok"
  | "missing_grant"
  | "revoked_grant"
  | "source_identity_changed"
  | "source_root_unavailable"
  | "candidate_changed";

export interface SourceResponse {
  source_id: string;
  display_name: string;
  storage_kinds: string[];
  available: boolean;
  authorized: boolean;
  authorization_state?: SourceAuthorizationState;
  detected: boolean | null;
  root_label: string | null;
  candidate_path: string;
  candidate_kind: "default" | "environment_override" | "user_selected";
  candidate_revision?: number;
  candidate_change_allowed?: boolean;
  candidate_change_reason?: string | null;
  imported_messages: number;
  imported_conversations: number;
  capabilities: HostPackageCapabilities;
  sync_completeness: SyncCompleteness;
  grant_revision?: number;
  root_id?: string | null;
  sync_enabled: boolean;
  location_state?: "found" | "not_found" | "inaccessible" | "invalid";
  candidate_root_id?: string | null;
  /** How many sessions the detect walk found at this source's root.
   *
   * Present only on cores that report it. Absent or null means "not measured"
   * rather than zero, so the UI must not print a number it was not given.
   */
  session_count?: number | null;
}

export interface SourceDirectoryCandidate {
  source_id: string;
  path: string;
  root_id: string;
  candidate_revision: number;
}

export interface SourceGrantReceipt {
  user_id: string;
  source_id: string;
  root_id: string;
  root_label: string;
  candidate_kind: "default" | "environment_override" | "user_selected";
  granted_at: string;
  revoked_at: string | null;
  revision: number;
}

export interface SourceSyncRequest {
  enabled: boolean;
}

export interface SourceSyncState {
  source_id: string;
  sync_enabled: boolean;
}

/** Whether the configured model may read locally stored conversation content. */
export interface ModelContentPermission {
  allowed: boolean;
}

export interface DetectionResponse {
  source_id: string;
  detected: boolean;
}

export interface ConnectSourcesResponse {
  accepted_source_ids: string[];
  failed_sources: Record<string, string>;
  jobs: ImportJob[];
  request_id?: string | null;
}

export interface OnboardingState {
  status: "not_started" | "importing" | "completed";
  selected_source_ids: string[];
  started_at: string | null;
  completed_at: string | null;
}

export interface ImportJob {
  job_id: string;
  user_id: string;
  source_id: string;
  /** Present in current Core responses; older versions imply history. */
  operation?: "history" | "sync";
  status: ImportStatus;
  phase: ScanPhase | null;
  checkpoint: string | null;
  discovered: number;
  discovered_targets: number;
  emitted: number;
  stored: number;
  revised: number;
  merged: number;
  skipped: number;
  errors: number;
  created_at: string;
  updated_at: string;
  /** Shared lower bound for a volume-limited first import; absent means full history. */
  history_after?: string | null;
}

export interface ImportIssue {
  issue_id: number;
  job_id: string;
  kind: "error" | "skip";
  conversation_id: string | null;
  reason: string;
  created_at: string;
}

export type ProviderKind =
  | "hy_default"
  | "openai_compatible"
  | "anthropic"
  | "ollama"
  | "local_onnx";

export interface ModelCapabilities {
  endpoint_reachable: boolean;
  model_available: boolean;
  structured_output: boolean;
  embedding_available: boolean;
}

export interface ModelProfile {
  profile_id: string;
  name: string;
  provider_kind: ProviderKind;
  base_url: string | null;
  model: string;
  embedding_model: string | null;
  embedding_provider_kind: ProviderKind | null;
  embedding_base_url: string | null;
  embedding_dimensions: number | null;
  has_api_key: boolean;
  has_configured_api_key?: boolean;
  credential_state?: "available" | "missing" | "unavailable";
  timeout_seconds: number;
  max_concurrency: number;
  context_window: number | null;
  disable_thinking: boolean;
  capabilities: ModelCapabilities;
  revision: number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface ModelProfileInput {
  creation_operation_id?: string;
  name: string;
  provider_kind: ProviderKind;
  base_url?: string | null;
  model: string;
  embedding_model?: string | null;
  embedding_provider_kind?: ProviderKind | null;
  embedding_base_url?: string | null;
  embedding_dimensions?: number | null;
  api_key?: string;
  timeout_seconds?: number;
  max_concurrency?: number;
  context_window?: number | null;
  disable_thinking?: boolean;
}

export interface ModelCreationOperation {
  operation_id: string;
  state: "committed" | "pending" | "deleted" | "not_found";
  profile_id: string | null;
  created_revision: 1 | null;
}

/**
 * A partial change to a saved profile. `expected_revision` is required: the
 * core rejects a write aimed at a revision that has since moved rather than
 * silently overwriting whatever is there now.
 */
export interface ModelProfilePatch {
  expected_revision: number;
  name?: string;
  base_url?: string | null;
  model?: string;
  embedding_model?: string | null;
  embedding_provider_kind?: ProviderKind | null;
  embedding_base_url?: string | null;
  embedding_dimensions?: number | null;
  api_key?: string;
  clear_api_key?: boolean;
  timeout_seconds?: number;
  max_concurrency?: number;
  context_window?: number | null;
  disable_thinking?: boolean;
}

export type ProbeCode =
  | "ok"
  | "hy_default_unavailable"
  | "missing_api_key"
  | "unreachable"
  | "unauthorized"
  | "rate_limited"
  | "insufficient_balance"
  | "model_not_found"
  | "model_list_unsupported"
  | "invalid_response"
  | "structured_output_unsupported"
  | "embedding_model_required"
  | "embedding_unavailable";

export interface ModelProbeResult {
  profile_id: string;
  profile_revision: number;
  /** Revision read at probe admission; a measured embedding width may advance the result revision. */
  input_profile_revision?: number | null;
  stages: Array<{
    stage: string;
    ok: boolean;
    code: ProbeCode;
    latency_ms: number;
  }>;
  capabilities: ModelCapabilities;
}

export interface ModelProbeOperationRequest {
  operation_id: string;
  expected_profile_revision: number;
}

export interface ModelProbeOperationReceipt {
  schema_version: 1;
  operation_id: string;
  profile_id: string | null;
  expected_profile_revision: number | null;
  state: "pending" | "completed" | "failed" | "not_found";
  result: ModelProbeResult | null;
  code: "model_probe_profile_changed" | "model_probe_execution_failed" | null;
  stale: boolean | null;
}

// biome-ignore format: test_contract_parity.py reads this union from a single line.
export type MemoryStatus = "confirmed" | "deleted" | "superseded";
export type MemoryKind = "chat" | "working" | "coding" | "schema" | "intention";
/** Who wrote the sentence, as against `kind`, which is what it is about.
 *
 * Read by recall: a summary a model composed should not reach another app
 * looking like something the person said. */
export type MemoryOrigin = "extracted" | "answered" | "agent" | "manual";

/** Local homepage projection; not a host-specific recall permission. */
export interface HomeSummary {
  memory_count: number;
  latest: {
    memory_id: string;
    content: string;
    truncated: boolean;
    revision: number;
    action?: "create" | "correct" | "delete" | "restore" | "unknown";
    updated_at: string;
    source_ids: string[];
  } | null;
  observed_at: string;
  /** 2026-10-12: 本轮读取超时，数字不代表现状；调用方应保留上一次的数据。 */
  stale?: boolean;
}

export interface GovernedMemory {
  memory_id: string;
  user_id: string;
  kind: MemoryKind;
  status: MemoryStatus;
  content: string;
  scopes: string[];
  evidence_ids: string[];
  origin: MemoryOrigin;
  /** HY's own topic tags, free-form and possibly empty.
   *
   * Not a closed set. HY seeds its extract prompt with the tags already in
   * the library so the model reuses them, but nothing stops it minting a new
   * one, which is how every product that tried this ended up with `Goals` and
   * `goal` side by side. Read as a proposal, not a taxonomy. */
  tags: string[];
  /** Memories HY replaced with this one, and memories that replaced it.
   * A memory with `superseded_by` is kept for reading but is no longer current. */
  supersedes?: string[];
  superseded_by?: string[];
  revision: number;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
}

export type ExtractionState = "extracted" | "outside_window" | "queued";

/** Local UI projection; source permissions apply before both count and paging. */
export interface MemoryLibraryPage {
  items: GovernedMemory[];
  total: number;
  offset: number;
  limit: number;
  /** Active evidence sources for each returned memory, from the same authorized page snapshot. */
  source_ids_by_memory?: Record<string, string[]>;
  /** When each returned memory's cited conversation happened (D96). `import` when only the import time is known. */
  source_time_by_memory?: Record<
    string,
    { at: string; basis: "conversation" | "import" }
  >;
  /** True when the read budget ran out mid-page (typically during a history
   * import): a partial snapshot, not an authoritative view. Show "importing"
   * and retry; never render this state as a failure. */
  stale?: boolean;
}

export interface MemoryLibraryQuery {
  query?: string;
  status?: MemoryStatus;
  active_only?: boolean;
  source?: string;
  offset?: number;
  limit?: number;
}

export type ExtractionWindowPreset = "3m" | "6m" | "1y" | "all";

export interface MemoryEvidenceExcerpt {
  evidence_id: string;
  available: boolean;
  source_id: string | null;
  role: string | null;
  written_by: string | null;
  created_at: string | null;
  verbatim: string | null;
  extraction_state?: ExtractionState | null;
}

export interface MemoryVersion {
  memory_id: string;
  revision: number;
  action: string;
  status: MemoryStatus;
  content: string;
  scopes: string[];
  created_at: string;
}

export type MemoryIndexSyncStatus = "pending" | "synced";

export interface MemoryIndexSync {
  memory_id: string;
  status: MemoryIndexSyncStatus;
  attempts: number;
  last_error_code: string | null;
  updated_at: string;
}

export interface MemoryDetail {
  memory: GovernedMemory;
  evidence: MemoryEvidenceExcerpt[];
  history: MemoryVersion[];
  // Null for a memory nobody has rewritten: there is no index to be behind,
  // and the interface should not explain a state the user never caused.
  index_sync: MemoryIndexSync | null;
}

export type SdkMemoryStatus =
  | "active"
  | "superseded"
  | "shadow"
  | "negated"
  | "conflicted"
  | "archived";

export interface SdkMemoryRecord {
  memory_id: string;
  content: string;
  layer: string;
  status: SdkMemoryStatus;
  is_latest: boolean;
  supersedes: string[];
  superseded_by: string[];
  tags: string[];
  user_id: string;
  agent_id: string;
  session_id: string | null;
  source: string | null;
  memory_at: number | null;
  gmt_created: number | null;
}

export interface SdkMemoryPage {
  memories: SdkMemoryRecord[];
  total: number;
  limit: number;
  offset: number;
}

export interface MemoryAuthorityResponse {
  authority: "legacy" | "sdk";
  migration_id: string | null;
}

export interface SdkRawRecord {
  raw_id: string;
  content: string;
  user_id: string;
  agent_id: string;
  session_id: string;
  source: string;
  memory_at: number | null;
  gmt_created: number | null;
  custom: Record<string, unknown>;
}

export interface SdkRawPage {
  items: SdkRawRecord[];
  total: number;
  limit: number;
  offset: number;
}

export interface FileImportReceipt {
  upload_id: string;
  filename: string;
  vendor: "chatgpt" | "claude" | "generic" | "memory-content";
  content_kind?: "raw_conversation" | "memory_content";
  source_id: string;
  messages: number;
  conversations: number;
  human_messages: number;
  agent_messages: number;
  unknown_author_messages?: number;
  skipped_records: number;
  ignored_files: number;
  checksum: string;
  created_at: string;
  committed: boolean;
  preview?: Array<{
    role: string;
    written_by?: "person" | "agent" | "unknown" | "tool";
    text: string;
    created_at: string | null;
    truncated: boolean;
  }>;
}

export interface FileImportOperationReceipt {
  schema_version: 1;
  operation_id: string;
  state: "not_found" | "pending" | "completed" | "failed";
  upload_id: string | null;
  code: "import_upload_invalid" | null;
  upload_state: "staged" | "committed" | "missing" | "unknown" | null;
}

export interface AskMemoryRequest {
  question: string;
  scope?: string;
  /** Same host-injected directory as recall; never a model-supplied path. */
  workspace_path?: string;
}

export interface AskMemoryResponse {
  answer: string;
  memories: GovernedMemory[];
  mode: "generated" | "extractive" | "empty";
}

export type AskMemoryEvent =
  | { type: "recall"; memories: GovernedMemory[] }
  | { type: "delta"; text: string }
  | {
      type: "final";
      answer: string;
      memories: GovernedMemory[];
      mode: "generated" | "extractive" | "empty";
    }
  | { type: "error"; code: string };

export interface RecallRequest {
  query: string;
  scope?: string;
  limit?: number;
  character_budget?: number;
  /** Wall-clock budget for candidate gather + score (ms). */
  timeout_ms?: number;
  /** The directory the caller is working in.
   *
   * A scope string cannot be produced outside Core, so this is how a caller
   * reaches the memories from the project it is in. Naming its own directory
   * is all it can do: no other workspace becomes readable. */
  workspace_path?: string;
}

export interface RecallHit {
  memory_id: string;
  content: string;
  /** `hy-sdk` means content already includes SDK-rendered logical time/history. */
  content_format?: "plain" | "hy-sdk";
  kind: MemoryKind;
  scopes: string[];
  evidence_ids: string[];
  updated_at: string;
  score: number;
}

export type TruncationBudget =
  | "count_budget"
  | "character_budget"
  | "timeout_budget";

export interface RecallReceipt {
  state: "grounded" | "empty";
  query: string;
  scope: string;
  hits: RecallHit[];
  used_characters: number;
  truncated: boolean;
  /** Which parallel budget fired when `truncated` is true. */
  truncated_by?: TruncationBudget | null;
  /** Scored (or known surplus) candidates left unreturned. */
  unexamined_count?: number;
}

export interface LiveSyncHealth {
  source_id: string;
  // Watching is layered on top of the timer rather than replacing it, so the
  // mode names both: the timer still covers what the watch rate limit skipped.
  mode: "polling" | "watch_and_polling";
  state: "idle" | "running" | "degraded" | "unavailable";
  last_job_id: string | null;
  last_attempt_at: string | null;
  last_success_at: string | null;
  last_error_code: string | null;
}

export interface MemoryGraphNode {
  node_id: string;
  node_type: "memory" | "source" | "schema" | "topic";
  label: string;
  memory_id: string | null;
  source_id: string | null;
  kind: string | null;
  status: string | null;
  confidence: number | null;
}

export interface MemoryGraphEdge {
  edge_id: string;
  source_node_id: string;
  target_node_id: string;
  relation: string;
  weight: number | null;
  origin:
    | "provenance"
    | "same_conversation"
    /** Two memories carrying the same tag. Only drawn where a tag is shared,
     * so a topic on the map always has more than one memory under it. */
    | "shared_tag"
    | "hy_relation";
}

export interface MemoryGraphResponse {
  total_memories: number;
  projected_memories: number;
  nodes: MemoryGraphNode[];
  edges: MemoryGraphEdge[];
  hy_graph_available: boolean;
}

export interface MemoryProcessingBlock {
  kind: string;
  reason: string;
  count: number;
}

export interface MemoryProcessingSummary {
  pending: number;
  /** `pending` split by how recent the evidence is: what arrived today, and
   * the import catch-up behind it. One total reads as "stuck". */
  fresh_pending: number;
  backlog_pending: number;
  /** Evidence kept out of automatic extraction by the window. */
  outside_window_pending: number;
  /** When work last landed, newest first. */
  recent_completions: string[];
  /** Rows the task store marks `running`.
   *
   * **Not the current concurrency.** A task is marked running before anything
   * slow happens, so a worker that later gives up — a timeout, a crash between
   * steps — leaves its row here until something recovers it. This number can
   * therefore stay above zero while nothing is actually running.
   *
   * For "how much is running now" use `in_flight_count`. What this is good for
   * is the opposite question: how many look running but are not. */
  running: number;
  /** Rows marked `running` that no worker currently owns.
   *
   * The difference between `running` and `in_flight_count`, floored at zero.
   * Non-zero means work is stranded: those tasks hold their harness's slot
   * until they are recovered, and a harness is serial, so nothing else from
   * that harness can run behind them. */
  /* Optional so that existing producers and fixtures stay valid; Core always
   * sends it, and a reader that does not see it should treat it as unknown
   * rather than as zero. */
  stranded_running?: number;
  /** Failed and never attempted because the continuous policy was not in
   * force. Included in `failed`, but not a real extraction failure: nothing
   * was sent to HY. A revoked policy otherwise reads as a broken pipeline. */
  unexecuted_policy_failures?: number;
  waiting_for_model: number;
  completed: number;
  failed: number;
  /** Tasks whose HY outcome cannot be proven yet. */
  unknown?: number;
  unknown_reasons?: Record<string, number>;
  blocked_stages: MemoryProcessingBlock[];
  seconds_per_completion: number | null;
  extraction_window_preset: ExtractionWindowPreset;
  manual_extract_batches_planned: number;
  manual_extract_batches_current: number;
  /** Work admitted to the bounded scheduler window. */
  active_queue: number;
  /** Memory tasks currently owned by workers — the real concurrency.
   *
   * Workers own a task from the moment they take it until they finish, fail or
   * give up, so unlike `running` this cannot include work nobody is doing. */
  in_flight_count: number;
  metrics?: {
    raw_evidence: number;
    complete_turns: number;
    hy_calls: number;
    completed: number;
    unknown: number;
    failed: number;
    cancelled: number;
    cancelled_reasons: Record<string, number>;
    governed_memories: number;
    workbuddy: { injected: number; timed_out: number; unavailable: number };
  };
}

export interface ExtractionSettings {
  window_preset: ExtractionWindowPreset;
}

export type GraphWidthDecision =
  | "keep_existing"
  | "rebuild_with_new"
  | "export_first";

export interface GraphWidthMismatch {
  stored_width: number;
  expected_width: number;
  graph_path: string;
}

export interface GraphWidthStatus {
  mismatch: GraphWidthMismatch | null;
  memory_count: number;
}

export interface GraphWidthDecisionReceipt {
  archived_path: string | null;
  exported_path: string | null;
  mismatch: GraphWidthMismatch | null;
}

export interface ConversationSearchHit {
  evidence_id: string;
  conversation_id: string;
  source_id: string;
  workspace: string | null;
  project: string | null;
  created_at: string | null;
  excerpt: string;
  /** Who the matched row belongs to. Present for a hit on a stored row, where
   * both columns are NOT NULL on `evidence`. Absent when the list was built
   * from a conversation summary instead: a whole conversation has no single
   * author, and a client must not invent one. */
  role?: string | null;
  written_by?: string | null;
  title?: string | null;
  turn_count?: number | null;
  memory_count?: number | null;
  turn_offset?: number | null;
}

/**
 * Why a search came back empty. Null whenever there are hits; a search that
 * cannot run at all answers 503 instead, so the interface never has to guess
 * which of the three states it is looking at.
 */
export type ConversationSearchEmptyReason = "no_matches" | "nothing_imported";

export interface ConversationSearchResponse {
  hits: ConversationSearchHit[];
  empty_reason: ConversationSearchEmptyReason | null;
  imported_messages: number;
}

export interface ConversationMessage {
  evidence_id: string;
  role: string;
  written_by: string;
  created_at: string | null;
  verbatim: string;
  turn_index?: number;
  system_context?: boolean;
}

export interface ConversationTranscript {
  conversation_id: string;
  source_id: string;
  messages: ConversationMessage[];
  total_turns?: number;
  offset?: number;
  limit?: number;
  has_more?: boolean;
  /** The last turn asked something and got nothing back yet. It is imported
   * and shown, but nothing about it is settled enough to draw a memory from. */
  trailing_turn_unanswered?: boolean;
}

export interface ConversationSummary {
  evidence_id: string;
  conversation_id: string;
  source_id: string;
  workspace: string | null;
  project: string | null;
  title: string;
  latest_at: string;
  turn_count: number;
  memory_count: number;
}

export interface ConversationBrowserPage {
  items: ConversationSummary[];
  total: number;
  offset: number;
  limit: number;
}

export interface ConversationMemories {
  items: GovernedMemory[];
}

/** One imported message, newest import first. Not a governed memory. */
export interface RecentConversationItem {
  evidence_id: string;
  conversation_id: string;
  source_id: string;
  role: string;
  written_by: string;
  created_at: string | null;
  imported_at: string;
  time_basis: "conversation" | "import";
  excerpt: string;
  truncated: boolean;
}

export interface RecentConversationsPage {
  items: RecentConversationItem[];
  total: number;
  offset: number;
  limit: number;
}

export interface ManualExtractReceipt {
  promoted: number;
  batches_planned: number;
  batches_current: number;
}

export interface MemoryNoteInput {
  request_id: string;
  text: string;
  scope: "all-apps";
}

export interface MemoryNoteReceipt {
  evidence_id: string;
  text: string;
  scope: "all-apps";
  created_at: string;
  state:
    | "waiting_for_model"
    | "waiting_for_permission"
    | "pending"
    | "running"
    | "failed"
    | "ready"
    | "no_memories";
  task_id: string | null;
  memory_ids: string[];
  error: string | null;
}

export type SavedMemoryNoteEvidence = MemoryEvidenceExcerpt;
export type {
  BackupRestoreFailureCode,
  BackupRestoreRequest,
  BackupRestoreResult,
} from "./backup-restore.js";
export type {
  ContextItem,
  ContextKind,
  ContextLimit,
  ContextMessage,
  ContextPath,
  ContextReason,
  ContextSearchRequest,
  ContextSearchResponse,
  MemoryContextItem,
  RawContextItem,
} from "./context.js";
export type {
  ContextExpandRequestV2,
  ContextHintsV2,
  ContextRequestV2,
  ContextSearchRequestV2,
  EvidenceGroupV2,
  EvidenceItemV2,
  EvidenceSpanV2,
  GovernedEvidenceV2,
  ObjectReferenceV2,
  RawEvidenceV2,
  RetrievalChannel,
  RetrievalIntent,
  RetrievalLimit,
  RetrievalPathV2,
  RetrievalResponseV2,
  RetrievalTimeRange,
} from "./context-v2.js";
export type {
  Citation,
  Matter,
  MatterClaim,
  MatterContext,
  MatterEvidence,
  MatterPatch,
  MatterRun,
} from "./matters.js";
export type {
  UISessionExchange,
  UISessionFailureCode,
  UISessionLogout,
} from "./ui-session.js";
