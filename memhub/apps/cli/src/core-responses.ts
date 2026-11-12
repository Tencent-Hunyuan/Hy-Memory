import type {
  HomeProjectionV1,
  ImportIssue,
  ImportJob,
  LiveSyncHealth,
  MemoryProcessingSummary,
  ModelProfile,
  SourceResponse,
} from "@one-memory/contracts";
/** SDK memory states `memhub memories list --status` may ask for. */
export const MEMORY_STATUSES = [
  "active",
  "superseded",
  "shadow",
  "negated",
  "conflicted",
  "archived",
] as const;

/**
 * HTTP 200 is not a contract. Everything here re-checks the shape and the
 * bounds of a Core response before any of it is projected into output, so a
 * malformed or misbound answer is reported as invalid instead of being printed
 * as a smaller truth. Returning null always means "do not show this".
 */

const ID_MAX = 128;
const LABEL_MAX = 256;
const LIST_MAX = 500;
const USER_ID = /^[A-Za-z0-9._-]{1,64}$/;
const ISO_TIME =
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/;
const LIVE_SYNC_STATES = [
  "idle",
  "running",
  "degraded",
  "unavailable",
] as const;
const PROVIDER_KINDS = [
  "hy_default",
  "openai_compatible",
  "anthropic",
  "ollama",
  "local_onnx",
] as const;
const CREDENTIAL_STATES = ["available", "missing", "unavailable"] as const;
const IMPORT_STATUSES = [
  "pending",
  "running",
  "paused",
  "interrupted",
  "completed",
  "failed",
  "cancelled",
] as const;
const SCAN_PHASES = ["discover", "read", "emit", "done"] as const;
const CAPABILITY_STATES = ["not_implemented", "implemented"] as const;
const VERIFICATION_STATES = ["fixture_only", "real_replayed"] as const;
const SYNC_COMPLETENESS = [
  "full_dynamic",
  "additions_only",
  "one_time_snapshot",
] as const;
const CANDIDATE_KINDS = [
  "default",
  "environment_override",
  "user_selected",
] as const;
const LOCATION_STATES = [
  "found",
  "not_found",
  "inaccessible",
  "invalid",
] as const;
const LIVE_SYNC_MODES = ["polling", "watch_and_polling"] as const;

export interface SourceConnectionCandidate {
  source_id: string;
  display_name: string;
  candidate_kind: (typeof CANDIDATE_KINDS)[number];
  candidate_root_id: string;
  candidate_revision?: number;
  requires_override_confirmation: boolean;
}

export function validateSources(value: unknown): SourceResponse[] | null {
  return list(value, (item) => {
    const source = record(item);
    const sourceId = source && text(source.source_id, ID_MAX);
    const displayName = source && text(source.display_name, LABEL_MAX);
    const storageKinds = source && identifiers(source.storage_kinds);
    const capabilities = source && record(source.capabilities);
    const grantRevision = source && count(source.grant_revision);
    // `stringOrNull` is a type guard, not a getter: it reports the shape but
    // does not hand back the value. Read the string explicitly, and treat a
    // present-but-malformed value as its own failure rather than as absent.
    let rootId: string | null | undefined;
    if (source !== null && source !== undefined && "root_id" in source) {
      rootId = stringOrNull(source.root_id, LABEL_MAX)
        ? (source.root_id as string | null)
        : undefined;
    }
    if (
      !source ||
      sourceId === null ||
      displayName === null ||
      storageKinds === null ||
      typeof source.available !== "boolean" ||
      typeof source.authorized !== "boolean" ||
      !booleanOrNull(source.detected) ||
      !stringOrNull(source.root_label, LABEL_MAX) ||
      !oneOf(source.candidate_kind, CANDIDATE_KINDS) ||
      count(source.imported_conversations) === null ||
      count(source.imported_messages) === null ||
      !capabilities ||
      !oneOf(capabilities.historical_import, CAPABILITY_STATES) ||
      !oneOf(capabilities.incremental_import, CAPABILITY_STATES) ||
      !oneOf(capabilities.live_sync, CAPABILITY_STATES) ||
      !oneOf(capabilities.recall, CAPABILITY_STATES) ||
      !oneOf(capabilities.installer, CAPABILITY_STATES) ||
      !oneOf(capabilities.verification, VERIFICATION_STATES) ||
      !oneOf(source.sync_completeness, SYNC_COMPLETENESS) ||
      typeof source.sync_enabled !== "boolean" ||
      grantRevision === null ||
      ("root_id" in source && rootId === undefined) ||
      (source.location_state !== undefined &&
        !oneOf(source.location_state, LOCATION_STATES))
    ) {
      return null;
    }
    return {
      source_id: sourceId,
      display_name: displayName,
      storage_kinds: storageKinds,
      available: source.available,
      authorized: source.authorized,
      detected: source.detected,
      root_label: source.root_label,
      candidate_path: "",
      candidate_kind: source.candidate_kind,
      imported_messages: source.imported_messages,
      imported_conversations: source.imported_conversations,
      capabilities: {
        historical_import: capabilities.historical_import,
        incremental_import: capabilities.incremental_import,
        live_sync: capabilities.live_sync,
        recall: capabilities.recall,
        installer: capabilities.installer,
        verification: capabilities.verification,
      },
      sync_completeness: source.sync_completeness,
      sync_enabled: source.sync_enabled,
      ...(typeof source.location_state === "string"
        ? { location_state: source.location_state }
        : {}),
      grant_revision: grantRevision,
      root_id: rootId,
    } as unknown as SourceResponse;
  });
}

export function validateSourceConnectionCandidates(
  value: unknown,
): SourceConnectionCandidate[] | null {
  if (!Array.isArray(value) || value.length > LIST_MAX) return null;
  const candidates: SourceConnectionCandidate[] = [];
  const seen = new Set<string>();
  for (const item of value) {
    const source = record(item);
    if (!source) return null;
    const sourceId = text(source.source_id, ID_MAX);
    const displayName = text(source.display_name, LABEL_MAX);
    const rootId =
      source.candidate_root_id === null
        ? null
        : text(source.candidate_root_id, LABEL_MAX);
    const revision = count(source.candidate_revision);
    if (
      sourceId === null ||
      displayName === null ||
      seen.has(sourceId) ||
      !oneOf(source.candidate_kind, CANDIDATE_KINDS) ||
      typeof source.available !== "boolean" ||
      !oneOf(source.location_state, LOCATION_STATES) ||
      revision === null ||
      (source.candidate_root_id !== null && rootId === null)
    ) {
      return null;
    }
    seen.add(sourceId);
    // The catalog includes supported-but-not-installed sources. They are valid
    // status rows, not connection candidates. Preserve strict identity checks
    // for every selected, found source instead of rejecting the whole catalog.
    if (source.available === true && source.location_state === "found") {
      if (rootId === null) return null;
      candidates.push({
        source_id: sourceId,
        display_name: displayName,
        candidate_kind: source.candidate_kind,
        candidate_root_id: rootId,
        candidate_revision: revision,
        requires_override_confirmation: source.candidate_kind !== "default",
      });
    }
  }
  return candidates;
}

export function validateLiveSync(value: unknown): LiveSyncHealth[] | null {
  return list(value, (item) => {
    const health = record(item);
    const sourceId = health && text(health.source_id, ID_MAX);
    if (
      !health ||
      sourceId === null ||
      !oneOf(health.mode, LIVE_SYNC_MODES) ||
      !oneOf(health.state, LIVE_SYNC_STATES) ||
      !stringOrNull(health.last_job_id, ID_MAX) ||
      !stringOrNull(health.last_attempt_at, LABEL_MAX) ||
      !stringOrNull(health.last_success_at, LABEL_MAX) ||
      (health.last_error_code !== undefined &&
        health.last_error_code !== null &&
        text(health.last_error_code, LABEL_MAX) === null)
    ) {
      return null;
    }
    return {
      source_id: sourceId,
      mode: health.mode,
      state: health.state,
      last_job_id: health.last_job_id ?? null,
      last_attempt_at: health.last_attempt_at ?? null,
      last_success_at: health.last_success_at ?? null,
      last_error_code: health.last_error_code ?? null,
    } as LiveSyncHealth;
  });
}

export function validateImportJobs(value: unknown): ImportJob[] | null {
  return list(value, validateImportJob);
}

export function validateImportJob(value: unknown): ImportJob | null {
  const job = record(value);
  if (!job) return null;
  const jobId = text(job.job_id, ID_MAX);
  const sourceId = text(job.source_id, ID_MAX);
  if (
    jobId === null ||
    sourceId === null ||
    (job.operation !== undefined &&
      !oneOf(job.operation, ["history", "sync"] as const)) ||
    !oneOf(job.status, IMPORT_STATUSES) ||
    (job.phase !== null && !oneOf(job.phase, SCAN_PHASES)) ||
    !stringOrNull(job.checkpoint, LABEL_MAX) ||
    text(job.created_at, LABEL_MAX) === null ||
    text(job.updated_at, LABEL_MAX) === null
  ) {
    return null;
  }
  for (const key of [
    "discovered",
    "discovered_targets",
    "emitted",
    "stored",
    "revised",
    "merged",
    "skipped",
    "errors",
  ]) {
    if (count(job[key]) === null) return null;
  }
  return {
    job_id: jobId,
    user_id: "local",
    source_id: sourceId,
    operation: job.operation ?? "history",
    status: job.status,
    phase: job.phase,
    checkpoint: job.checkpoint,
    discovered: job.discovered,
    discovered_targets: job.discovered_targets,
    emitted: job.emitted,
    stored: job.stored,
    revised: job.revised,
    merged: job.merged,
    skipped: job.skipped,
    errors: job.errors,
    created_at: job.created_at,
    updated_at: job.updated_at,
  } as ImportJob;
}

export function validateImportIssues(
  value: unknown,
  jobId: string,
): ImportIssue[] | null {
  return list(value, (item) => {
    const issue = record(item);
    if (!issue) return null;
    const returnedJobId = text(issue.job_id, ID_MAX);
    if (
      issue.conversation_id !== null &&
      text(issue.conversation_id, ID_MAX) === null
    ) {
      return null;
    }
    const conversationId = issue.conversation_id as string | null;
    const reason = text(issue.reason, LABEL_MAX);
    const createdAt = text(issue.created_at, LABEL_MAX);
    const issueId = count(issue.issue_id);
    if (
      returnedJobId !== jobId ||
      reason === null ||
      createdAt === null ||
      issueId === null ||
      !oneOf(issue.kind, ["error", "skip"] as const)
    ) {
      return null;
    }
    return {
      issue_id: issueId,
      job_id: returnedJobId,
      kind: issue.kind,
      conversation_id: conversationId,
      reason,
      created_at: createdAt,
    };
  });
}

export function validateProcessing(
  value: unknown,
): MemoryProcessingSummary | null {
  const summary = record(value);
  if (!summary) return null;
  for (const key of [
    "pending",
    "fresh_pending",
    "backlog_pending",
    "outside_window_pending",
    "running",
    "waiting_for_model",
    "completed",
    "failed",
  ]) {
    if (count(summary[key]) === null) return null;
  }
  const blocked = summary.blocked_stages;
  if (blocked !== undefined && blocked !== null) {
    const stages = list(blocked, (item) => {
      const stage = record(item);
      return stage &&
        text(stage.kind, LABEL_MAX) !== null &&
        text(stage.reason, LABEL_MAX) !== null &&
        count(stage.count) !== null
        ? stage
        : null;
    });
    if (stages === null) return null;
  }
  return summary as unknown as MemoryProcessingSummary;
}

export function validateModelProfiles(value: unknown): ModelProfile[] | null {
  return list(value, (item) => {
    const profile = record(item);
    if (
      !profile ||
      text(profile.name, LABEL_MAX) === null ||
      !oneOf(profile.provider_kind, PROVIDER_KINDS) ||
      text(profile.model, LABEL_MAX) === null ||
      typeof profile.has_api_key !== "boolean" ||
      typeof profile.is_active !== "boolean" ||
      (profile.credential_state !== undefined &&
        profile.credential_state !== null &&
        !oneOf(profile.credential_state, CREDENTIAL_STATES))
    ) {
      return null;
    }
    return profile as unknown as ModelProfile;
  });
}

/** `GET /api/v2/home-summary`: the SDK-authority home card. */
export interface SdkHomeSummary {
  schema_version: 2;
  memory_count: number;
  latest: { memory_id: string } | null;
  observed_at: string;
}

export function validateHomeSummary(value: unknown): SdkHomeSummary | null {
  const summary = record(value);
  if (
    !summary ||
    summary.schema_version !== 2 ||
    count(summary.memory_count) === null ||
    text(summary.observed_at, LABEL_MAX) === null
  ) {
    return null;
  }
  const latest = summary.latest;
  if (latest !== null) {
    const memory = record(latest);
    if (!memory || typeof memory.memory_id !== "string" || !memory.memory_id) {
      return null;
    }
  }
  return summary as unknown as SdkHomeSummary;
}

/** `GET /api/v2/home-projection`: the mode-aware home content plane. */
export function validateHomeProjection(
  value: unknown,
): HomeProjectionV1 | null {
  const projection = exactRecord(value, [
    "projection_version",
    "user_id",
    "effective_mode",
    "capabilities",
    "visible_count",
    "display_state",
    "latest",
    "observed_at",
  ]);
  if (!projection) return null;
  const mode = projection.effective_mode;
  const capabilities = exactRecord(projection.capabilities, [
    "content_plane",
    "generation_required",
    "graph_enabled",
    "evolve_available",
  ]);
  if (
    projection.projection_version !== 1 ||
    typeof projection.user_id !== "string" ||
    !USER_ID.test(projection.user_id) ||
    !oneOf(mode, ["lite", "pro", "ultra"] as const) ||
    !capabilities ||
    capabilities.content_plane !== (mode === "lite" ? "raw" : "memory") ||
    capabilities.generation_required !== (mode !== "lite") ||
    capabilities.graph_enabled !== (mode === "ultra") ||
    capabilities.evolve_available !== (mode === "ultra") ||
    count(projection.visible_count) === null ||
    !oneOf(projection.display_state, [
      "empty",
      "excerpt",
      "no_user_excerpt",
      "no_displayable_memory",
    ] as const) ||
    !isoTime(projection.observed_at) ||
    !validProjectionLatest(projection.latest, mode)
  ) {
    return null;
  }
  const visible = projection.visible_count as number;
  const latest = projection.latest as Record<string, unknown> | null;
  if (
    (visible === 0 &&
      (latest !== null || projection.display_state !== "empty")) ||
    (visible > 0 && projection.display_state === "empty") ||
    (latest === null &&
      ["excerpt", "no_user_excerpt"].includes(
        projection.display_state as string,
      )) ||
    (latest?.type === "raw" &&
      projection.display_state !== latest.display_state) ||
    (latest?.type === "memory" && projection.display_state !== "excerpt")
  ) {
    return null;
  }
  return projection as unknown as HomeProjectionV1;
}

function validProjectionLatest(
  value: unknown,
  mode: "lite" | "pro" | "ultra",
): boolean {
  if (value === null) return true;
  const base = [
    "type",
    "time",
    "time_basis",
    "excerpt",
    "truncated",
    "source_id",
  ];
  const candidate = record(value);
  if (!candidate) return false;
  const keys =
    candidate.type === "raw"
      ? [...base, "raw_id", "session_id", "display_state"]
      : [...base, "memory_id", "scene", "kind", "variant"];
  const latest = exactRecord(value, keys);
  if (
    !latest ||
    !(latest.time === null || isoTime(latest.time)) ||
    ![null, "conversation", "import", "write", "update"].includes(
      latest.time_basis as null | string,
    ) ||
    text(latest.excerpt, 200) === null ||
    typeof latest.truncated !== "boolean" ||
    text(latest.source_id, ID_MAX) === null
  ) {
    return false;
  }
  if (latest.type === "raw") {
    return (
      mode === "lite" &&
      nonemptyText(latest.raw_id, ID_MAX) &&
      nonemptyText(latest.session_id, LABEL_MAX) &&
      oneOf(latest.display_state, ["excerpt", "no_user_excerpt"] as const) &&
      (latest.display_state !== "no_user_excerpt" ||
        (latest.excerpt === "" && latest.truncated === false))
    );
  }
  return (
    latest.type === "memory" &&
    mode !== "lite" &&
    nonemptyText(latest.memory_id, ID_MAX) &&
    oneOf(latest.scene, ["chat", "working", "coding"] as const) &&
    oneOf(latest.kind, ["chat", "experience"] as const) &&
    [null, "working", "coding"].includes(latest.variant as null | string) &&
    ((latest.kind === "chat" &&
      latest.scene === "chat" &&
      latest.variant === null) ||
      (latest.kind === "experience" &&
        latest.scene === latest.variant &&
        oneOf(latest.scene, ["working", "coding"] as const)))
  );
}

/** The bounded `/api/v2/raw?limit=1` envelope used only for its real total. */
export function validateRawTotal(value: unknown): number | null {
  const page = exactRecord(value, [
    "user_id",
    "items",
    "total",
    "offset",
    "limit",
    "time_filtered",
  ]);
  if (
    !page ||
    typeof page.user_id !== "string" ||
    !USER_ID.test(page.user_id) ||
    !Array.isArray(page.items) ||
    page.items.length > 1 ||
    count(page.total) === null ||
    page.offset !== 0 ||
    page.limit !== 1 ||
    page.time_filtered !== false
  ) {
    return null;
  }
  return page.total as number;
}

function identifiers(value: unknown): string[] | null {
  return list(value ?? [], (item) => text(item, ID_MAX));
}

function list<T>(
  value: unknown,
  validate: (item: unknown) => T | null,
): T[] | null {
  if (!Array.isArray(value) || value.length > LIST_MAX) return null;
  const items: T[] = [];
  for (const item of value) {
    const valid = validate(item);
    if (valid === null) return null;
    items.push(valid);
  }
  return items;
}

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function exactRecord(
  value: unknown,
  keys: readonly string[],
): Record<string, unknown> | null {
  const item = record(value);
  return item &&
    Object.keys(item).length === keys.length &&
    keys.every((key) => Object.hasOwn(item, key))
    ? item
    : null;
}

function isoTime(value: unknown): value is string {
  return (
    typeof value === "string" &&
    ISO_TIME.test(value) &&
    Number.isFinite(Date.parse(value))
  );
}

function nonemptyText(value: unknown, max: number): value is string {
  return typeof value === "string" && value.length > 0 && value.length <= max;
}

function text(value: unknown, max: number): string | null {
  return typeof value === "string" && value.length <= max ? value : null;
}

function count(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0
    ? value
    : null;
}

function booleanOrNull(value: unknown): value is boolean | null {
  return value === null || typeof value === "boolean";
}

function stringOrNull(value: unknown, max: number): value is string | null {
  return value === null || text(value, max) !== null;
}

function oneOf<T extends string>(
  value: unknown,
  allowed: readonly T[],
): value is T {
  return typeof value === "string" && allowed.includes(value as T);
}
