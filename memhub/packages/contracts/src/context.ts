/** Wire contract validated against Core context_models.py / OpenAPI schema v1. */
export type ContextKind = "raw" | "memory";
export type ContextLimit =
  | "count_budget"
  | "character_budget"
  | "timeout_budget"
  | "metadata_budget";
export type ContextReason =
  | "not_requested"
  | "permission_required"
  | "source_revoked"
  | "read_unavailable"
  | "timeout_budget"
  | "evidence_changed"
  | "memory_changed"
  | "lexical_only";

export interface ContextSearchRequest {
  query: string;
  kinds?: ContextKind[];
  /** Narrows raw results only; never grants a source or changes memory scope. */
  raw_source_ids?: string[] | null;
  limit?: number;
  character_budget?: number;
  timeout_ms?: number;
}

export interface ContextMessage {
  evidence_id: string;
  revision: number;
  role: string;
  written_by: string;
  created_at: string | null;
  text: string;
  truncated: boolean;
}

export interface RawContextItem {
  kind: "raw";
  source_id: string;
  conversation_id: string;
  anchor_evidence_id: string;
  messages: ContextMessage[];
  truncated: boolean;
}

export interface MemoryContextItem {
  kind: "memory";
  memory_id: string;
  revision: number;
  content: string;
  memory_kind: string;
  updated_at: string;
  evidence_ids: string[];
  matched_by: readonly ("lexical" | "governed_semantic")[];
  truncated: boolean;
}

export type ContextItem = RawContextItem | MemoryContextItem;

export interface ContextPath {
  status:
    | "ok"
    | "empty"
    | "not_requested"
    | "not_authorized"
    | "unavailable"
    | "degraded";
  reason: ContextReason | null;
  elapsed_ms: number;
  returned_count: number;
  truncated: boolean;
  truncated_by: ContextLimit[];
}

export interface ContextSearchResponse {
  schema_version: 1;
  request_id: string;
  state: "grounded" | "empty" | "partial" | "unavailable";
  paths: { raw: ContextPath; memory: ContextPath };
  items: ContextItem[];
  used_characters: number;
  truncated: boolean;
  total_ms: number;
  adoption: "unknown";
}

export interface SdkRawContextItem {
  kind: "raw";
  raw_id: string;
  content: string;
  source: string;
  session_id: string;
  memory_at: string | null;
  score: number;
  truncated: boolean;
}

export interface SdkMemoryContextItem {
  kind: "memory";
  memory_id: string;
  content: string;
  layer: string;
  status: string;
  source: string | null;
  updated_at: string | null;
  score: number;
  truncated: boolean;
}

export type SdkContextItem = SdkRawContextItem | SdkMemoryContextItem;

export interface SdkContextSearchResponse {
  schema_version: 2;
  request_id: string;
  state: "grounded" | "empty" | "partial" | "unavailable";
  items: SdkContextItem[];
  used_characters: number;
  truncated: boolean;
  total_ms: number;
}

/** Native Host Bridge wire response. Schemas stay distinct and are never adapted. */
export type HostContextSearchResponse =
  | ContextSearchResponse
  | SdkContextSearchResponse;
