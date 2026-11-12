export type HomeProjectionMode = "lite" | "pro" | "ultra";
export type HomeProjectionTimeBasis =
  | "conversation"
  | "import"
  | "write"
  | "update";

export interface HomeProjectionCapabilities {
  content_plane: "raw" | "memory";
  generation_required: boolean;
  graph_enabled: boolean;
  evolve_available: boolean;
}

interface HomeProjectionLatestBase {
  time: string | null;
  time_basis: HomeProjectionTimeBasis | null;
  excerpt: string;
  truncated: boolean;
  source_id: string;
}

export interface HomeProjectionRawLatest extends HomeProjectionLatestBase {
  type: "raw";
  raw_id: string;
  session_id: string;
  display_state: "excerpt" | "no_user_excerpt";
}

export interface HomeProjectionMemoryLatest extends HomeProjectionLatestBase {
  type: "memory";
  memory_id: string;
  scene: "chat" | "working" | "coding";
  kind: "chat" | "experience";
  variant: "working" | "coding" | null;
}

export type HomeProjectionLatest =
  | HomeProjectionRawLatest
  | HomeProjectionMemoryLatest;

export interface HomeProjectionV1 {
  projection_version: 1;
  user_id: string;
  effective_mode: HomeProjectionMode;
  capabilities: HomeProjectionCapabilities;
  visible_count: number;
  display_state:
    | "empty"
    | "excerpt"
    | "no_user_excerpt"
    | "no_displayable_memory";
  latest: HomeProjectionLatest | null;
  observed_at: string;
}
