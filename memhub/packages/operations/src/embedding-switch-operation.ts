import type {
  EmbeddingSpace,
  EmbeddingSpaceDeleteReceipt,
  EmbeddingSpaceState,
  EmbeddingSpaceSummary,
  EmbeddingSpaces,
  EmbeddingSwitchCurrent,
  EmbeddingSwitchJob,
  EmbeddingSwitchJobState,
  EmbeddingSwitchPlan,
  EmbeddingSwitchPlannedSource,
  EmbeddingSwitchProgress,
  EmbeddingSwitchSummary,
  ScannedEmbeddingSpace,
} from "@one-memory/contracts";

const CODE = /^[a-z0-9_.:-]{1,128}$/;
const PLAN_REASONS = [
  "embedding_identity_changed",
  "same_embedding",
  "empty_home",
] as const;
const JOB_STATES: readonly EmbeddingSwitchJobState[] = [
  "preparing",
  "reembedding",
  "verifying",
  "committing",
  "done",
  "failed",
  "rolled_back",
];
const PROGRESS_PHASES: readonly EmbeddingSwitchProgress["phase"][] = [
  "prepare",
  "vectors",
  "graph",
  "verify",
  "commit",
  "rollback",
  "done",
];
const SPACE_STATES: readonly EmbeddingSpaceState[] = [
  "live",
  "building",
  "stray",
  "retired",
  "dropped",
];

export class EmbeddingSwitchInputError extends Error {}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function text(value: unknown, max = 256): value is string {
  return typeof value === "string" && value.length > 0 && value.length <= max;
}

function optionalText(value: unknown, max = 256): value is string | null {
  return value === null || text(value, max);
}

function count(value: unknown): value is number {
  return Number.isSafeInteger(value) && (value as number) >= 0;
}

function nullableCount(value: unknown): value is number | null {
  return value === null || count(value);
}

function finite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function nullableFinite(value: unknown): value is number | null {
  return value === null || finite(value);
}

function timestamp(value: unknown): value is string {
  return text(value, 128) && Number.isFinite(Date.parse(value));
}

function nullableTimestamp(value: unknown): value is string | null {
  return value === null || timestamp(value);
}

function parseSpaceSummary(value: unknown): EmbeddingSpaceSummary | null {
  const item = record(value);
  if (
    !item ||
    !optionalText(item.space_id, 128) ||
    !optionalText(item.model) ||
    !nullableCount(item.dims) ||
    typeof item.collection_base !== "string" ||
    item.collection_base.length > 2_048 ||
    !nullableCount(item.vectors)
  )
    return null;
  return item as unknown as EmbeddingSpaceSummary;
}

function parsePlannedSource(
  value: unknown,
): EmbeddingSwitchPlannedSource | null {
  const item = record(value);
  if (
    !item ||
    !optionalText(item.space_id, 128) ||
    typeof item.collection_base !== "string" ||
    item.collection_base.length > 2_048 ||
    !nullableCount(item.dims) ||
    !count(item.vectors) ||
    !nullableCount(item.unmerged)
  )
    return null;
  return item as unknown as EmbeddingSwitchPlannedSource;
}

/** Bounded projection of the CLI validator, extended to the full v1 plan. */
export function parseEmbeddingSwitchPlan(
  value: unknown,
): EmbeddingSwitchPlan | null {
  const plan = record(value);
  if (!plan) return null;
  const source = parseSpaceSummary(plan.source);
  const target = parseSpaceSummary(plan.target);
  const sources = Array.isArray(plan.sources)
    ? plan.sources.map(parsePlannedSource)
    : null;
  if (
    typeof plan.needed !== "boolean" ||
    !PLAN_REASONS.includes(plan.reason as (typeof PLAN_REASONS)[number]) ||
    !text(plan.plan_token, 128) ||
    !source ||
    !target ||
    !text(plan.target_profile_id, 128) ||
    !count(plan.target_revision) ||
    plan.target_revision < 1 ||
    !text(plan.target_provider_kind, 64) ||
    typeof plan.target_local !== "boolean" ||
    !sources ||
    sources.some((item) => item === null) ||
    !count(plan.total_vectors) ||
    !nullableCount(plan.users) ||
    !nullableCount(plan.graph_vectors) ||
    !nullableCount(plan.estimate_s) ||
    !nullableCount(plan.requests) ||
    !count(plan.disk_bytes_needed) ||
    !nullableCount(plan.disk_bytes_free) ||
    !count(plan.retention_days) ||
    !Array.isArray(plan.blockers) ||
    !plan.blockers.every(
      (item): item is string => typeof item === "string" && CODE.test(item),
    )
  )
    return null;
  return {
    ...(plan as unknown as EmbeddingSwitchPlan),
    source,
    target,
    sources: sources as EmbeddingSwitchPlannedSource[],
  };
}

function parseProgress(value: unknown): EmbeddingSwitchProgress | null {
  const progress = record(value);
  if (
    !progress ||
    !PROGRESS_PHASES.includes(
      progress.phase as EmbeddingSwitchProgress["phase"],
    ) ||
    !count(progress.done) ||
    !count(progress.total) ||
    !finite(progress.percent) ||
    progress.percent < 0 ||
    progress.percent > 100 ||
    !count(progress.embedded) ||
    !count(progress.reused) ||
    !count(progress.kept) ||
    !optionalText(progress.role, 128) ||
    !count(progress.source_index) ||
    !count(progress.sources) ||
    !nullableFinite(progress.rate_per_s) ||
    (typeof progress.rate_per_s === "number" && progress.rate_per_s < 0) ||
    !nullableCount(progress.eta_s) ||
    !nullableTimestamp(progress.updated_at)
  )
    return null;
  return progress as unknown as EmbeddingSwitchProgress;
}

export function parseEmbeddingSwitchJob(
  value: unknown,
): EmbeddingSwitchJob | null {
  const job = record(value);
  if (!job) return null;
  const source = parseSpaceSummary(job.source);
  const target = parseSpaceSummary(job.target);
  const progress = parseProgress(job.progress);
  if (
    !text(job.job_id, 128) ||
    (job.kind !== "switch" && job.kind !== "merge") ||
    !JOB_STATES.includes(job.state as EmbeddingSwitchJobState) ||
    !source ||
    !target ||
    !Array.isArray(job.stray_space_ids) ||
    !job.stray_space_ids.every((item) => text(item, 128)) ||
    !optionalText(job.target_profile_id, 128) ||
    !nullableCount(job.target_revision) ||
    !["app", "cli", "startup"].includes(String(job.requested_by)) ||
    !progress ||
    typeof job.cancel_requested !== "boolean" ||
    !optionalText(job.error_code, 128) ||
    (typeof job.error_code === "string" && !CODE.test(job.error_code)) ||
    !count(job.attempt) ||
    job.attempt < 1 ||
    !timestamp(job.created_at) ||
    !timestamp(job.updated_at) ||
    !nullableTimestamp(job.finished_at)
  )
    return null;
  return {
    ...(job as unknown as EmbeddingSwitchJob),
    source,
    target,
    progress,
  };
}

export function parseEmbeddingSwitchCurrent(
  value: unknown,
): EmbeddingSwitchCurrent | null {
  const current = record(value);
  if (
    current?.schema_version !== 1 ||
    !["idle", "scanning", "running"].includes(String(current.phase)) ||
    typeof current.writes_paused !== "boolean" ||
    !["pending", "scanning", "done", "failed", "unavailable"].includes(
      String(current.scan_state),
    ) ||
    !optionalText(current.scan_error, 128)
  )
    return null;
  const job =
    current.job === null ? null : parseEmbeddingSwitchJob(current.job);
  const lastFinished =
    current.last_finished === null
      ? null
      : parseEmbeddingSwitchJob(current.last_finished);
  if (
    (current.job !== null && job === null) ||
    (current.last_finished !== null && lastFinished === null)
  )
    return null;
  return {
    ...(current as unknown as EmbeddingSwitchCurrent),
    job,
    last_finished: lastFinished,
  };
}

function parseEmbeddingSpace(value: unknown): EmbeddingSpace | null {
  const space = record(value);
  if (
    !space ||
    !text(space.space_id, 128) ||
    !SPACE_STATES.includes(space.state as EmbeddingSpaceState) ||
    !optionalText(space.model) ||
    !nullableCount(space.dims) ||
    !nullableCount(space.name_dims) ||
    typeof space.collection_base !== "string" ||
    space.collection_base.length > 2_048 ||
    !["legacy", "v2", "discovered"].includes(String(space.naming)) ||
    !optionalText(space.graph_dir, 2_048) ||
    !optionalText(space.provider_kind, 64) ||
    !optionalText(space.profile_id, 128) ||
    !nullableCount(space.profile_revision) ||
    !nullableCount(space.vectors) ||
    !timestamp(space.created_at) ||
    !nullableTimestamp(space.live_since) ||
    !nullableTimestamp(space.retired_at) ||
    !nullableTimestamp(space.keep_until) ||
    !nullableTimestamp(space.dropped_at)
  )
    return null;
  return space as unknown as EmbeddingSpace;
}

function parseScannedSpace(value: unknown): ScannedEmbeddingSpace | null {
  const space = record(value);
  const counts = record(space?.counts);
  if (
    !space ||
    typeof space.collection_base !== "string" ||
    space.collection_base.length > 2_048 ||
    !nullableCount(space.name_dims) ||
    !nullableCount(space.width) ||
    !count(space.vectors) ||
    !counts ||
    !Object.values(counts).every(count) ||
    !optionalText(space.space_id, 128) ||
    ![...SPACE_STATES, "unregistered"].includes(
      space.state as EmbeddingSpaceState | "unregistered",
    )
  )
    return null;
  return space as unknown as ScannedEmbeddingSpace;
}

export function parseEmbeddingSpaces(value: unknown): EmbeddingSpaces | null {
  const body = record(value);
  if (
    body?.schema_version !== 1 ||
    !optionalText(body.live_space_id, 128) ||
    !Array.isArray(body.spaces) ||
    !(body.scanned === null || Array.isArray(body.scanned)) ||
    !optionalText(body.scan_error, 128)
  )
    return null;
  const spaces = body.spaces.map(parseEmbeddingSpace);
  const scanned =
    body.scanned === null ? null : body.scanned.map(parseScannedSpace);
  if (
    spaces.some((space) => space === null) ||
    scanned?.some((space) => space === null)
  )
    return null;
  return {
    ...(body as unknown as EmbeddingSpaces),
    spaces: spaces as EmbeddingSpace[],
    scanned: scanned as ScannedEmbeddingSpace[] | null,
  };
}

export function parseEmbeddingSpaceDeleteReceipt(
  value: unknown,
): EmbeddingSpaceDeleteReceipt | null {
  const receipt = record(value);
  const space = parseEmbeddingSpace(receipt?.space);
  return receipt?.schema_version === 1 && space
    ? { schema_version: 1, space }
    : null;
}

export function parseEmbeddingSwitchSummary(
  value: unknown,
): EmbeddingSwitchSummary | null {
  const summary = record(value);
  if (
    !summary ||
    (summary.job_id !== undefined && !text(summary.job_id, 128)) ||
    (summary.kind !== "switch" && summary.kind !== "merge") ||
    ![...JOB_STATES, "scanning"].includes(
      summary.state as EmbeddingSwitchJobState | "scanning",
    ) ||
    !finite(summary.percent) ||
    summary.percent < 0 ||
    summary.percent > 100 ||
    !nullableCount(summary.eta_s)
  )
    return null;
  return summary as unknown as EmbeddingSwitchSummary;
}

export interface EmbeddingSwitchRefusal {
  code: string;
  plan: EmbeddingSwitchPlan | null;
}

export function parseEmbeddingSwitchRefusal(
  value: unknown,
): EmbeddingSwitchRefusal | null {
  const refusal = record(value);
  if (
    !refusal ||
    typeof refusal.detail !== "string" ||
    !CODE.test(refusal.detail)
  )
    return null;
  const plan =
    refusal.plan === undefined ? null : parseEmbeddingSwitchPlan(refusal.plan);
  if (refusal.plan !== undefined && plan === null) return null;
  return { code: refusal.detail, plan };
}

export interface EmbeddingSwitchPlanRequest {
  path: "/api/v1/embedding-switches/plan";
  body: { profile_id: string; expected_revision?: number };
}

export interface EmbeddingSwitchStartInput {
  profile_id: string;
  expected_revision: number;
  plan_token: string;
  requested_by: "app" | "cli";
}

export interface EmbeddingSwitchStartRequest {
  path: "/api/v1/embedding-switches";
  body: EmbeddingSwitchStartInput;
}

function identifier(value: unknown, field: string): asserts value is string {
  if (!text(value, 128))
    throw new EmbeddingSwitchInputError(`${field}_invalid`);
}

function revision(value: unknown): asserts value is number {
  if (!count(value) || value < 1)
    throw new EmbeddingSwitchInputError("expected_revision_invalid");
}

export function buildEmbeddingSwitchPlanRequest(
  profileId: string,
  expectedRevision?: number,
): EmbeddingSwitchPlanRequest {
  identifier(profileId, "profile_id");
  if (expectedRevision !== undefined) revision(expectedRevision);
  return {
    path: "/api/v1/embedding-switches/plan",
    body: {
      profile_id: profileId,
      ...(expectedRevision === undefined
        ? {}
        : { expected_revision: expectedRevision }),
    },
  };
}

export function buildEmbeddingSwitchStartRequest(
  input: EmbeddingSwitchStartInput,
): EmbeddingSwitchStartRequest {
  identifier(input.profile_id, "profile_id");
  revision(input.expected_revision);
  identifier(input.plan_token, "plan_token");
  if (input.requested_by !== "app" && input.requested_by !== "cli")
    throw new EmbeddingSwitchInputError("requested_by_invalid");
  return { path: "/api/v1/embedding-switches", body: { ...input } };
}

export const EMBEDDING_SWITCH_CURRENT_PATH =
  "/api/v1/embedding-switches/current" as const;
export const EMBEDDING_SPACES_PATH = "/api/v1/embedding-spaces" as const;

export function embeddingSwitchCancelPath(jobId: string): string {
  identifier(jobId, "job_id");
  return `/api/v1/embedding-switches/${encodeURIComponent(jobId)}/cancel`;
}

export function retiredEmbeddingSpacePath(spaceId: string): string {
  identifier(spaceId, "space_id");
  return `/api/v1/embedding-spaces/${encodeURIComponent(spaceId)}`;
}
