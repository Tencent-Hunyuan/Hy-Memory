/** `memhub backup restore`: drive Core's managed restore from a stopped Runtime.
 *
 * Contract: docs/contracts/c0-ui-and-restore-contract.md (C0, H25) and Core
 * commit `a8ade00`. Restore replaces the business database, so Core refuses it
 * from a Runtime-owning process (`backup_restore_runtime_running`). The CLI
 * therefore starts a *non-owning* maintenance Core (`one-memory serve
 * --maintenance`) against the same data dir, calls
 * `POST /api/v1/backup/restore`, and then tears that process down. Core keeps
 * the safety envelope (managed `backups/` source, protective copy, atomic
 * replace, stable failure codes); the CLI only adds preview + explicit confirm.
 * A v2 backup (docs/contracts/backup-v2.md) restores the memories too, and
 * Core verifies their counts before the restore stands.
 */

export interface RestoreResult {
  state: "restored";
  restoredBytes: number;
  previousBackupPath: string;
  sourceSchemaVersion: number;
  /** 1: an old backup (management database only); 2: memories included.
   * A Core before 1.0.7 answers without it: that is a 1. */
  backupSchemaVersion?: 1 | 2;
  memoriesIncluded?: boolean;
  restoredFiles?: number | null;
  verification?: Record<string, unknown> | null;
  graphRebuildScheduled?: boolean;
  note?: string | null;
}

/** How long the CLI waits for Core's restore answer. */
export const RESTORE_TIMEOUT_MS = 60 * 60_000;

export type RestoreOutcome =
  | { state: "restored" }
  | { state: "rejected"; code: string };

const CONTRACT_CODES = new Set([
  "backup_restore_runtime_running",
  "backup_restore_source_outside_home",
  "backup_restore_source_invalid",
  "backup_restore_source_too_new",
  "backup_restore_protective_backup_failed",
  "backup_restore_replace_failed",
  "backup_restore_manifest_invalid",
  "backup_restore_verify_failed",
  "backup_restore_backend_mismatch",
  "backup_restore_remote_sdk_unsupported",
  "backup_restore_counts_mismatch",
  "backup_restore_disk_space_insufficient",
  "backup_restore_stage_failed",
  "backup_restore_pending",
  "backup_restore_rollback_failed",
  "backup_in_progress",
  "backup_lifecycle_busy",
  "backup_writes_busy",
  "backup_clients_busy",
]);

/** Validate the HTTP result. Anything malformed returns null so the caller
 * reports a failure rather than a false "restored". */
export function normalizeRestoreResult(value: unknown): RestoreResult | null {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  const record = value as Record<string, unknown>;
  if (record.state !== "restored") return null;
  if (
    typeof record.restored_bytes !== "number" ||
    !Number.isInteger(record.restored_bytes) ||
    record.restored_bytes < 0
  ) {
    return null;
  }
  if (
    typeof record.previous_backup_path !== "string" ||
    record.previous_backup_path === ""
  ) {
    return null;
  }
  if (
    typeof record.source_schema_version !== "number" ||
    !Number.isInteger(record.source_schema_version) ||
    record.source_schema_version < 0
  ) {
    return null;
  }
  const version = record.backup_schema_version === 2 ? 2 : 1;
  return {
    state: "restored",
    restoredBytes: record.restored_bytes,
    previousBackupPath: record.previous_backup_path,
    sourceSchemaVersion: record.source_schema_version,
    backupSchemaVersion: version,
    // Only a v2 answer that says so counts as memories restored.
    memoriesIncluded: version === 2 && record.memories_included === true,
    restoredFiles:
      typeof record.restored_files === "number" ? record.restored_files : null,
    verification:
      record.verification !== null &&
      typeof record.verification === "object" &&
      !Array.isArray(record.verification)
        ? (record.verification as Record<string, unknown>)
        : null,
    graphRebuildScheduled: record.graph_rebuild_scheduled === true,
    note: typeof record.note === "string" ? record.note : null,
  };
}

/** Map a Core detail code to the CLI vocabulary. Unknown codes stay visible as
 * a generic failure, never as success. */
export function restoreFailure(code: string): { code: string } {
  return { code: CONTRACT_CODES.has(code) ? code : "backup_restore_failed" };
}

export function restoreExitCode(outcome: RestoreOutcome): number {
  return outcome.state === "restored" ? 0 : 1;
}

export function restoreIsDestructive(): true {
  return true;
}

/** The loopback call itself, factored out so it can be exercised against a
 * real socket in tests. */
export async function callRestore(
  options: {
    host: string;
    port: number;
    apiToken: string;
  },
  source: string,
  request: typeof defaultPost = defaultPost,
): Promise<
  | { state: "restored"; result: RestoreResult }
  | { state: "rejected"; code: string }
> {
  const response = await request(
    `http://${options.host}:${options.port}/api/v1/backup/restore`,
    { source },
    {
      Authorization: `Bearer ${options.apiToken}`,
    },
  );
  if (response.status === 200) {
    const result = normalizeRestoreResult(response.data);
    return result === null
      ? { state: "rejected", code: "backup_restore_failed" }
      : { state: "restored", result };
  }
  if (response.status === 409) {
    // Runtime still owns the data dir. Distinguish it from transport errors.
    return { state: "rejected", code: "backup_restore_runtime_running" };
  }
  if (response.status === 404) {
    // Core predates the endpoint.
    return { state: "rejected", code: "backup_restore_contract_unavailable" };
  }
  const detail = (response.data as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") {
    return { state: "rejected", ...restoreFailure(detail) };
  }
  return { state: "rejected", code: "backup_restore_failed" };
}

type Post = (
  url: string,
  body: unknown,
  headers: Record<string, string>,
) => Promise<{ status: number; data: unknown }>;

type Get = (
  url: string,
  headers: Record<string, string>,
) => Promise<{ status: number; data: unknown }>;

/** Verify that the process answering on the maintenance port is the Core
 * instance this CLI just launched. The health call carries no Authorization;
 * the bearer is only sent after the exact instance id matches. */
export async function verifyMaintenanceCoreIdentity(
  options: { host: string; port: number; instanceId: string },
  request: Get = defaultGet,
): Promise<boolean> {
  let response: { status: number; data: unknown };
  try {
    response = await request(
      `http://${options.host}:${options.port}/api/v1/health`,
      {},
    );
  } catch {
    return false;
  }
  if (response.status !== 200) return false;
  if (
    response.data === null ||
    typeof response.data !== "object" ||
    Array.isArray(response.data)
  ) {
    return false;
  }
  const body = response.data as Record<string, unknown>;
  return (
    body.status === "ok" &&
    body.service === "one-memory-core" &&
    body.instance_id === options.instanceId
  );
}

async function defaultGet(
  url: string,
  headers: Record<string, string>,
): Promise<{ status: number; data: unknown }> {
  const { default: axios } = await import("axios");
  const response = await axios.get(url, {
    headers,
    timeout: 2_000,
    proxy: false,
    maxRedirects: 0,
    maxContentLength: 4 * 1024 * 1024,
    validateStatus: () => true,
  });
  return { status: response.status, data: response.data };
}

async function defaultPost(
  url: string,
  body: unknown,
  headers: Record<string, string>,
): Promise<{ status: number; data: unknown }> {
  const { default: axios } = await import("axios");
  const response = await axios.post(url, body, {
    headers,
    // A v2 restore stages and verifies every SDK file of the backup: a
    // large library takes minutes, not seconds.
    timeout: RESTORE_TIMEOUT_MS,
    proxy: false,
    maxRedirects: 0,
    maxContentLength: 4 * 1024 * 1024,
    maxBodyLength: 64 * 1024,
    validateStatus: () => true,
  });
  return { status: response.status, data: response.data };
}
