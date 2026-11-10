/**
 * Managed backup and restore contract (docs/contracts/backup-v2.md).
 *
 * A backup (`POST /api/v1/backup`) is a v2 directory with the memories: the
 * management database, every SDK file of the home, and on a Qdrant server an
 * export of the memories through the SDK. `GET /api/v1/backup/estimate`
 * reports its size first. Restore replaces the management database and the
 * memories. Core refuses it while a Runtime-owned Core is live, accepts only
 * managed sources under the One Memory Home `backups/` directory, verifies
 * the counts the manifest recorded and rolls back on any mismatch. An old
 * backup (one `.sqlite3` file) restores the management database only.
 */

export interface BackupRequest {
  target: string;
}

export interface BackupBackend {
  kind: "local" | "remote_graph" | "remote_vector";
  vector: string;
  graph: string;
  vector_prefix: string | null;
}

export interface BackupEstimate {
  /** The directory a backup would be written to, when `target` was given. */
  target?: string | null;
  bytes: number;
  files: number;
  local_bytes: number;
  export_bytes: number;
  free_bytes: number | null;
  memories_included: true;
  backend: BackupBackend;
}

export interface BackupResult {
  target: string;
  bytes: number;
  files: number;
  schema_version: 2;
  memories_included: true;
  /** How long SDK writes were paused, and how long the file copy took. */
  pause_ms: number;
  copy_ms: number;
  backend: BackupBackend;
  users: string[];
  counts: Record<string, unknown>;
}

export type BackupFailureCode =
  | "backup_target_outside_data_dir"
  | "backup_target_exists"
  | "backup_target_invalid"
  | "backup_in_progress"
  | "backup_embedding_maintenance_in_progress"
  | "backup_lifecycle_busy"
  | "backup_writes_busy"
  | "backup_clients_busy"
  | "backup_disk_space_insufficient"
  | "backup_sdk_unavailable"
  | "backup_database_missing"
  | "backup_sdk_root_symlink";

export interface BackupRestoreRequest {
  source: string;
}

export interface BackupRestoreVerification {
  vector_store: "verified" | "skipped";
  layers: "verified" | "skipped";
  layers_reason: string | null;
  graph:
    | "verified"
    | "quarantined"
    | "absent"
    | "rebuild_scheduled"
    | "skipped";
  mismatches: string[];
}

export interface BackupRestoreResult {
  state: "restored";
  restored_bytes: number;
  previous_backup_path: string;
  source_schema_version: number;
  /** 1: an old backup (management database only); 2: memories included. */
  backup_schema_version?: 1 | 2;
  memories_included?: boolean;
  restored_files?: number | null;
  backend?: BackupBackend | null;
  verification?: BackupRestoreVerification | null;
  graph_rebuild_scheduled?: boolean;
  duration_ms?: number | null;
  /** `management_database_only_no_memories` for an old backup. */
  note?: string | null;
}

export type BackupRestoreFailureCode =
  | "backup_restore_runtime_running"
  | "backup_restore_source_outside_home"
  | "backup_restore_source_invalid"
  | "backup_restore_source_too_new"
  | "backup_restore_protective_backup_failed"
  | "backup_restore_replace_failed"
  | "backup_restore_manifest_invalid"
  | "backup_restore_verify_failed"
  | "backup_restore_backend_mismatch"
  | "backup_restore_remote_sdk_unsupported"
  | "backup_restore_counts_mismatch"
  | "backup_restore_disk_space_insufficient"
  | "backup_restore_stage_failed"
  | "backup_restore_pending"
  | "backup_restore_rollback_failed";
