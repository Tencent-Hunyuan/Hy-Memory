/** Shared transport contract for Core's embedding-space lifecycle. */

export type EmbeddingSwitchKind = "switch" | "merge";
export type EmbeddingSwitchJobState =
  | "preparing"
  | "reembedding"
  | "verifying"
  | "committing"
  | "done"
  | "failed"
  | "rolled_back";
export type EmbeddingSwitchProgressPhase =
  | "prepare"
  | "vectors"
  | "graph"
  | "verify"
  | "commit"
  | "rollback"
  | "done";

export interface EmbeddingSpaceSummary {
  space_id: string | null;
  model: string | null;
  dims: number | null;
  collection_base: string;
  vectors: number | null;
}

export interface EmbeddingSwitchPlannedSource {
  space_id: string | null;
  collection_base: string;
  dims: number | null;
  vectors: number;
  unmerged: number | null;
}

export interface EmbeddingSwitchPlan {
  needed: boolean;
  reason: "embedding_identity_changed" | "same_embedding" | "empty_home";
  plan_token: string;
  source: EmbeddingSpaceSummary;
  target: EmbeddingSpaceSummary;
  target_profile_id: string;
  target_revision: number;
  target_provider_kind: string;
  target_local: boolean;
  sources: EmbeddingSwitchPlannedSource[];
  total_vectors: number;
  users: number | null;
  graph_vectors: number | null;
  estimate_s: number | null;
  requests: number | null;
  disk_bytes_needed: number;
  disk_bytes_free: number | null;
  retention_days: number;
  blockers: string[];
}

export interface EmbeddingSwitchProgress {
  phase: EmbeddingSwitchProgressPhase;
  done: number;
  total: number;
  percent: number;
  embedded: number;
  reused: number;
  kept: number;
  role: string | null;
  source_index: number;
  sources: number;
  rate_per_s: number | null;
  eta_s: number | null;
  updated_at: string | null;
}

export interface EmbeddingSwitchJob {
  job_id: string;
  kind: EmbeddingSwitchKind;
  state: EmbeddingSwitchJobState;
  source: EmbeddingSpaceSummary;
  target: EmbeddingSpaceSummary;
  stray_space_ids: string[];
  target_profile_id: string | null;
  target_revision: number | null;
  requested_by: "app" | "cli" | "startup";
  progress: EmbeddingSwitchProgress;
  cancel_requested: boolean;
  error_code: string | null;
  attempt: number;
  created_at: string;
  updated_at: string;
  finished_at: string | null;
}

export interface EmbeddingSwitchCurrent {
  schema_version: 1;
  phase: "idle" | "scanning" | "running";
  writes_paused: boolean;
  scan_state: "pending" | "scanning" | "done" | "failed" | "unavailable";
  scan_error: string | null;
  job: EmbeddingSwitchJob | null;
  last_finished: EmbeddingSwitchJob | null;
}

export type EmbeddingSpaceState =
  | "live"
  | "building"
  | "stray"
  | "retired"
  | "dropped";

export interface EmbeddingSpace {
  space_id: string;
  state: EmbeddingSpaceState;
  model: string | null;
  dims: number | null;
  name_dims: number | null;
  collection_base: string;
  naming: "legacy" | "v2" | "discovered";
  graph_dir: string | null;
  provider_kind: string | null;
  profile_id: string | null;
  profile_revision: number | null;
  vectors: number | null;
  created_at: string;
  live_since: string | null;
  retired_at: string | null;
  keep_until: string | null;
  dropped_at: string | null;
}

export interface ScannedEmbeddingSpace {
  collection_base: string;
  name_dims: number | null;
  width: number | null;
  vectors: number;
  counts: Record<string, number>;
  space_id: string | null;
  state: EmbeddingSpaceState | "unregistered";
}

export interface EmbeddingSpaces {
  schema_version: 1;
  live_space_id: string | null;
  spaces: EmbeddingSpace[];
  scanned: ScannedEmbeddingSpace[] | null;
  scan_error: string | null;
}

export interface EmbeddingSpaceDeleteReceipt {
  schema_version: 1;
  space: EmbeddingSpace;
}

/** Compact projection included by health and memory-processing responses. */
export interface EmbeddingSwitchSummary {
  job_id?: string;
  kind: EmbeddingSwitchKind;
  state: EmbeddingSwitchJobState | "scanning";
  percent: number;
  eta_s: number | null;
}
