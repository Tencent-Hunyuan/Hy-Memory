import {
  CANDIDATE_KINDS,
  type CandidateKind,
  type GovernedMemory,
  type HomeSummary,
  type ImportIssue,
  type ImportJob,
  type LiveSyncHealth,
  type MemoryDetail,
  type MemoryEvidenceExcerpt,
  type MemoryLibraryPage,
  type MemoryProcessingSummary,
  type ModelProfile,
  type SourceResponse,
} from "@one-memory/contracts";
/** The only memory states a response may carry, and the only ones a filter may ask for. */
export const MEMORY_STATUSES = ["confirmed", "deleted", "superseded"] as const;

/**
 * HTTP 200 is not a contract. Everything here re-checks the shape and the
 * bounds of a Core response before any of it is projected into output, so a
 * malformed or misbound answer is reported as invalid instead of being printed
 * as a smaller truth. Returning null always means "do not show this".
 */

const ID_MAX = 128;
const LABEL_MAX = 256;
const TEXT_MAX = 100_000;
const LIST_MAX = 500;
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
const MEMORY_KINDS = [
  "chat",
  "working",
  "coding",
  "schema",
  "intention",
] as const;
const MEMORY_ORIGINS = ["extracted", "answered", "agent", "manual"] as const;
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
const MEMORY_TASK_KINDS = ["extract", "system2", "ultra", "sweeper"] as const;
const MEMORY_TASK_STATUSES = [
  "pending",
  "running",
  "waiting_for_model",
  "completed",
  "failed",
  "cancelled",
  "deferred_outside_window",
] as const;
const CAPABILITY_STATES = ["not_implemented", "implemented"] as const;
const VERIFICATION_STATES = ["fixture_only", "real_replayed"] as const;
const SYNC_COMPLETENESS = [
  "full_dynamic",
  "additions_only",
  "one_time_snapshot",
] as const;
const LOCATION_STATES = [
  "found",
  "not_found",
  "inaccessible",
  "invalid",
] as const;
const LIVE_SYNC_MODES = ["polling", "watch_and_polling"] as const;

export interface ValidatedMemoryTask {
  task_id: string;
  kind: (typeof MEMORY_TASK_KINDS)[number];
  status: (typeof MEMORY_TASK_STATUSES)[number];
  attempts: number;
  last_error_code: string | null;
  created_at: string;
  updated_at: string;
}

export interface SourceConnectionCandidate {
  source_id: string;
  display_name: string;
  candidate_kind: CandidateKind;
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

export function validateMemoryTasks(
  value: unknown,
): ValidatedMemoryTask[] | null {
  return list(value, (item) => {
    const task = record(item);
    if (!task) return null;
    const taskId = text(task.task_id, ID_MAX);
    if (
      task.last_error_code !== null &&
      text(task.last_error_code, LABEL_MAX) === null
    ) {
      return null;
    }
    const error = task.last_error_code as string | null;
    const createdAt = text(task.created_at, LABEL_MAX);
    const updatedAt = text(task.updated_at, LABEL_MAX);
    const attempts = count(task.attempts);
    if (
      taskId === null ||
      createdAt === null ||
      updatedAt === null ||
      attempts === null ||
      !oneOf(task.kind, MEMORY_TASK_KINDS) ||
      !oneOf(task.status, MEMORY_TASK_STATUSES)
    ) {
      return null;
    }
    return {
      task_id: taskId,
      kind: task.kind,
      status: task.status,
      attempts,
      last_error_code: error,
      created_at: createdAt,
      updated_at: updatedAt,
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

export function validateHomeSummary(value: unknown): HomeSummary | null {
  const summary = record(value);
  if (
    !summary ||
    count(summary.memory_count) === null ||
    text(summary.observed_at, LABEL_MAX) === null
  ) {
    return null;
  }
  return summary as unknown as HomeSummary;
}

/** The page must be the page that was asked for, not merely a page. */
export function validateMemoryPage(
  value: unknown,
  request: { offset: number; limit: number },
): MemoryLibraryPage | null {
  const page = record(value);
  if (!page) return null;
  const total = count(page.total);
  if (
    total === null ||
    page.offset !== request.offset ||
    page.limit !== request.limit
  ) {
    return null;
  }
  const items = list(page.items, validateGovernedMemory);
  if (items === null || items.length > request.limit) return null;
  if (items.length > 0 && total < request.offset + items.length) return null;
  return { items, total, offset: page.offset, limit: page.limit };
}

export function validateMemoryDetail(
  value: unknown,
  memoryId: string,
): MemoryDetail | null {
  const detail = record(value);
  if (!detail) return null;
  const memory = validateGovernedMemory(detail.memory);
  if (memory === null || memory.memory_id !== memoryId) return null;
  const cited = new Set(memory.evidence_ids);
  const returned = new Set<string>();
  const evidence = list(detail.evidence ?? [], (item) => {
    const excerpt = record(item);
    if (!excerpt) return null;
    const evidenceId = text(excerpt.evidence_id, ID_MAX);
    // Core builds this list from the memory's own citations; anything else is
    // another record's excerpt and must not be shown under this memory.
    if (
      evidenceId === null ||
      !cited.has(evidenceId) ||
      returned.has(evidenceId) ||
      typeof excerpt.available !== "boolean"
    ) {
      return null;
    }
    returned.add(evidenceId);
    const available = excerpt.available === true;
    const verbatim = available ? text(excerpt.verbatim ?? "", TEXT_MAX) : "";
    if (verbatim === null) return null;
    return {
      evidence_id: evidenceId,
      available,
      source_id: optional(excerpt.source_id, ID_MAX),
      written_by: optional(excerpt.written_by, LABEL_MAX),
      created_at: optional(excerpt.created_at, LABEL_MAX),
      // An excerpt the user cannot read has no text, whatever the server sent.
      verbatim,
    } as unknown as MemoryEvidenceExcerpt;
  });
  if (evidence === null || evidence.length !== memory.evidence_ids.length) {
    return null;
  }
  const history = list(detail.history ?? [], (item) => {
    const version = record(item);
    if (
      !version ||
      typeof version.revision !== "number" ||
      !Number.isSafeInteger(version.revision) ||
      version.revision < 1 ||
      text(version.action, LABEL_MAX) === null ||
      !oneOf(version.status, MEMORY_STATUSES) ||
      text(version.content, TEXT_MAX) === null ||
      identifiers(version.scopes) === null ||
      text(version.created_at, LABEL_MAX) === null
    ) {
      return null;
    }
    return {
      revision: version.revision,
      action: version.action,
      status: version.status,
      content: version.content,
      scopes: identifiers(version.scopes) as string[],
      created_at: version.created_at,
    };
  });
  if (history === null) return null;
  return {
    memory,
    evidence,
    history,
    index_sync: null,
  } as unknown as MemoryDetail;
}

export function validateGovernedMemory(value: unknown): GovernedMemory | null {
  const memory = record(value);
  if (
    !memory ||
    !text(memory.memory_id, ID_MAX) ||
    !text(memory.user_id, LABEL_MAX) ||
    !text(memory.content, TEXT_MAX) ||
    !oneOf(memory.kind, MEMORY_KINDS) ||
    !oneOf(memory.origin, MEMORY_ORIGINS) ||
    typeof memory.revision !== "number" ||
    !Number.isSafeInteger(memory.revision) ||
    memory.revision < 1 ||
    !oneOf(memory.status, MEMORY_STATUSES) ||
    !text(memory.created_at, LABEL_MAX) ||
    !text(memory.updated_at, LABEL_MAX)
  ) {
    return null;
  }
  const scopes = identifiers(memory.scopes);
  const evidenceIds = identifiers(memory.evidence_ids);
  const tags = identifiers(memory.tags ?? []);
  if (
    scopes === null ||
    scopes.length < 1 ||
    evidenceIds === null ||
    evidenceIds.length < 1 ||
    tags === null ||
    tags.length > 32
  ) {
    return null;
  }
  return {
    ...memory,
    scopes,
    evidence_ids: evidenceIds,
    tags,
  } as unknown as GovernedMemory;
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

function text(value: unknown, max: number): string | null {
  return typeof value === "string" && value.length <= max ? value : null;
}

function optional(value: unknown, max: number): string | undefined {
  return typeof value === "string" && value.length <= max ? value : undefined;
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
