export interface Citation {
  evidence_id?: string | null;
  raw_id?: string | null;
  item_index?: number | null;
  quote: string;
  revision: number;
  source_id: string;
  written_by: string;
  created_at: string | null;
}

export interface MatterClaim {
  memory_refs: MemoryRef[];
  state: "current" | "historical" | "superseded" | "unresolved";
  change_note: string;
  statement: string;
  kind:
    | "goal"
    | "decision"
    | "constraint"
    | "proposal"
    | "progress"
    | "open_question"
    | "experience";
  scope: string;
  citations: Citation[];
  basis: "human_statement" | "agent_report" | "mixed" | "unknown";
  verification: "citation_checked" | "model_reviewed";
}

export interface Matter {
  source_engine: "hy-memory" | "legacy_direct";
  content_review: "needs_review";
  matter_id: string;
  title: string;
  purpose: string;
  boundary: string;
  anchors: string[];
  claims: MatterClaim[];
  tags: string[];
  uncertainties: string[];
  version: number;
  scope_confirmed: boolean;
  agent_access: boolean;
  availability:
    | "ready"
    | "needs_update"
    | "source_unavailable"
    | "evidence_changed"
    | "memory_changed"
    | "archived";
  model: string;
  updated_at: string;
  source_count: number;
  evidence_count: number;
}

export interface MatterPatch {
  expected_version: number;
  title?: string;
  boundary?: string;
  scope_confirmed?: boolean;
  agent_access?: boolean;
}

export interface MatterContext {
  matter_id: string;
  version: number;
  availability: string;
  text: string;
  instruction_policy: "reference_only";
  truncated: boolean;
}

export interface MatterEvidence {
  evidence_id: string;
  source_id: string;
  conversation_id: string;
  role: string;
  written_by: string;
  created_at: string | null;
  observed_at: string;
  revision: number;
  content_hash: string;
  project: string | null;
  workspace: string | null;
  text: string;
}

export interface MatterRun {
  import_job_ids: string[];
  collection_mode: "source_import" | "existing_evidence";
  coverage_complete: boolean;
  deferred_conversations: {
    source_id: string;
    conversation_id: string;
    records: number;
    characters: number;
    reason: "character_budget" | "conversation_limit";
  }[];
  hy_input_records: number;
  hy_excluded_records: number;
  governed_memories: number;
  reviewed_memories: number;
  reference_only_memories: number;
  recovered_evidence_links: number;
  required_memory_coverage_complete: boolean;
  failed_phase: string | null;
  validation_issues: string[];
  extraction_engine: "hy-memory" | "legacy_direct";
  extraction_task_ids: string[];
  synthesis_profile_id: string | null;
  synthesis_profile_revision: number | null;
  run_id: string;
  status: "running" | "completed" | "failed" | "interrupted";
  phase: string;
  completed_batches: number;
  total_batches: number;
  selected_records: number;
  omitted_records: number;
  rejected_claims: number;
  matter_ids: string[];
  error: string | null;
  elapsed_seconds: number;
  observations: string[];
}

export interface MemoryRef {
  origin: string;
  memory_id: string;
  revision?: number | null;
  status: "confirmed" | "active";
  content_hash: string;
  task_id?: string | null;
  hy_memory_id?: string | null;
  engine: "hy-memory";
  engine_version?: string | null;
  profile_id?: string | null;
  profile_revision?: number | null;
  model?: string | null;
  authority?: "legacy" | "sdk";
}
