import { classifyModelRemove } from "@one-memory/operations";
import axios from "axios";

/**
 * Only paths proven side-effect free by reading the Core handlers belong here.
 * Legacy recall can schedule a reindex and source detect walks the host tree,
 * so neither is a status read.
 */
const READ_ONLY_PATHS = [
  "/api/v1/call-events",
  "/api/v1/home-summary",
  "/api/v1/import-jobs",
  "/api/v1/live-sync",
  "/api/v1/memory-library",
  "/api/v1/memory-processing",
  "/api/v1/memory-tasks",
  "/api/v1/model-profiles",
  "/api/v1/sources",
] as const;

/** Contract (C0, H25): the one-time loopback code exchange for `memhub ui`.
 * It is a POST because it mints a short-lived code; the CLI sends the bearer
 * token only to Core, never to the browser. */
const UI_SESSION_PATH = "/api/v1/ui/session";

export function createUiSessionRequest(attachment: {
  host: string;
  port: number;
  apiToken: string;
  timeoutMs?: number;
}): () => Promise<
  { state: "ok"; data: unknown } | { state: "unavailable"; code: string }
> {
  const client = axios.create({
    baseURL: `http://${attachment.host}:${attachment.port}`,
    headers: { Authorization: `Bearer ${attachment.apiToken}` },
    timeout: attachment.timeoutMs ?? 5_000,
    proxy: false,
    maxRedirects: 0,
    maxContentLength: MAX_RESPONSE_BYTES,
    // The exchange POST carries only a tiny `{}` body; `maxBodyLength: 0`
    // (used by the GET-only reader) means "no body allowed", which axios
    // rejects with ERR_BAD_REQUEST.
    maxBodyLength: 32 * 1024,
    validateStatus: () => true,
  });
  return async () => {
    let response: { status: number; data: unknown };
    try {
      response = await client.post(UI_SESSION_PATH, {});
    } catch {
      return { state: "unavailable" as const, code: "transport_failed" };
    }
    if (response.status === 200) {
      return { state: "ok" as const, data: response.data };
    }
    if (response.status === 503) {
      return {
        state: "unavailable" as const,
        code: "ui_web_assets_unavailable",
      };
    }
    return { state: "unavailable" as const, code: `http_${response.status}` };
  };
}

const MEMORY_DETAIL = /^\/api\/v1\/memories\/[A-Za-z0-9][\w.-]{0,127}$/;
const IMPORT_JOB =
  /^\/api\/v1\/import-jobs\/[A-Za-z0-9][\w.-]{0,127}(?:\/issues)?$/;
const OPERATION_RECEIPT =
  /^\/api\/v1\/(?:source-connections|model-profile-creations|model-probe-operations)\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const MEMORY_MUTATION_RECEIPT =
  /^\/api\/v1\/memory-mutations\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
/**
 * Core registers these static routes before `/{memory_id}`, so a memory whose
 * id happens to be one of them would reach a different handler: `graph`
 * initializes the HY bridge, `ask` answers questions. Neither is a detail read.
 */
export const RESERVED_MEMORY_SEGMENTS = ["ask", "graph"] as const;

/** A response larger than this is refused before it is buffered. */
const MAX_RESPONSE_BYTES = 4 * 1024 * 1024;

export type ReadOutcome<T> =
  | { state: "ok"; data: T }
  | { state: "denied" | "unavailable" | "not_found"; code: string };

export type CoreReader = <T>(
  path: string,
  params?: Record<string, string | number>,
  options?: { signal?: AbortSignal },
) => Promise<ReadOutcome<T>>;

export type CoreContextQuery = <T>(
  body: object,
  options?: { signal?: AbortSignal },
) => Promise<ReadOutcome<T>>;

export function createCoreReader(attachment: {
  host: string;
  port: number;
  apiToken: string;
  timeoutMs?: number;
}): CoreReader {
  const client = axios.create({
    baseURL: `http://${attachment.host}:${attachment.port}`,
    headers: { Authorization: `Bearer ${attachment.apiToken}` },
    timeout: attachment.timeoutMs ?? 5_000,
    proxy: false,
    maxRedirects: 0,
    maxContentLength: MAX_RESPONSE_BYTES,
    maxBodyLength: 0,
    validateStatus: () => true,
  });
  return async <T>(
    path: string,
    params?: Record<string, string | number>,
    options?: { signal?: AbortSignal },
  ) => {
    if (!isReadOnlyPath(path)) {
      throw new Error("core_read_path_not_allowed");
    }
    let response: { status: number; data: unknown };
    try {
      const config =
        params || options?.signal
          ? {
              ...(params ? { params } : {}),
              ...(options?.signal ? { signal: options.signal } : {}),
            }
          : undefined;
      response = await client.get(path, config);
    } catch {
      return { state: "unavailable" as const, code: "transport_failed" };
    }
    if (response.status === 200) {
      return { state: "ok" as const, data: response.data as T };
    }
    if (response.status === 401 || response.status === 403) {
      return { state: "denied" as const, code: `http_${response.status}` };
    }
    if (response.status === 404) {
      return { state: "not_found" as const, code: "http_404" };
    }
    return { state: "unavailable" as const, code: `http_${response.status}` };
  };
}

/**
 * Context search is semantically read-only but intentionally uses POST so the
 * private query and filters are carried in a bounded body, never in a URL.
 */
export function createCoreContextQuery(attachment: {
  host: string;
  port: number;
  apiToken: string;
  timeoutMs?: number;
}): CoreContextQuery {
  const client = axios.create({
    baseURL: `http://${attachment.host}:${attachment.port}`,
    headers: { Authorization: `Bearer ${attachment.apiToken}` },
    timeout: attachment.timeoutMs ?? 30_000,
    proxy: false,
    maxRedirects: 0,
    maxContentLength: MAX_RESPONSE_BYTES,
    maxBodyLength: 32 * 1024,
    validateStatus: () => true,
  });
  return async <T>(body: object, options?: { signal?: AbortSignal }) => {
    let response: { status: number; data: unknown };
    try {
      response = await client.post(
        "/api/v1/context/search",
        body,
        options?.signal ? { signal: options.signal } : undefined,
      );
    } catch {
      return { state: "unavailable" as const, code: "transport_failed" };
    }
    if (response.status === 200) {
      return { state: "ok" as const, data: response.data as T };
    }
    if (response.status === 401 || response.status === 403) {
      return { state: "denied" as const, code: `http_${response.status}` };
    }
    if (response.status === 404) {
      return { state: "not_found" as const, code: "http_404" };
    }
    return { state: "unavailable" as const, code: `http_${response.status}` };
  };
}

export function isReadOnlyPath(path: string): boolean {
  if ((READ_ONLY_PATHS as readonly string[]).includes(path)) return true;
  if (IMPORT_JOB.test(path)) return true;
  if (OPERATION_RECEIPT.test(path)) return true;
  if (MEMORY_MUTATION_RECEIPT.test(path)) return true;
  if (!MEMORY_DETAIL.test(path)) return false;
  const segment = path.slice("/api/v1/memories/".length);
  return !(RESERVED_MEMORY_SEGMENTS as readonly string[]).includes(segment);
}

/** A write accepted by the unified Memory Server entry.
 *
 * `request_id` is what the caller keeps and queries later. Retrying is unsafe:
 * HY has no idempotency key, so a resubmit duplicates the memory.
 */
export interface MemoryWriteReceipt {
  request_id: string;
  state: string;
  created_at: string;
  updated_at: string;
  detail?: string;
}

/** A6: submit a write through the durable entry every entry shares.
 *
 * The CLI is one of several doors (MCP, scanner, hooks); all land in the same
 * queue. Nothing here calls HY directly.
 */
/** The attachment the CLI gets from Runtime discovery.
 *
 * Named here rather than importing `RuntimeAttachment`, so this module stays
 * usable in tests that fake discovery without importing runtime-control.
 */
export interface RuntimeAttachmentLike {
  readonly host: string;
  readonly port: number;
  readonly apiToken: string;
}

function baseUrlOf(attachment: RuntimeAttachmentLike): string {
  return `http://${attachment.host}:${attachment.port}`;
}

export async function submitMemoryWrite(
  attachment: RuntimeAttachmentLike,
  request: {
    session: string;
    first_round: number;
    last_round: number;
    evidence_ids: readonly string[];
    priority?: "live" | "normal";
  },
): Promise<MemoryWriteReceipt> {
  const response = await axios.post<MemoryWriteReceipt>(
    `${baseUrlOf(attachment)}/api/v1/memory-writes`,
    {
      internal_session_id: request.session,
      first_round: request.first_round,
      last_round: request.last_round,
      evidence_ids: [...request.evidence_ids],
      ...(request.priority === undefined ? {} : { priority: request.priority }),
    },
    {
      headers: { Authorization: `Bearer ${attachment.apiToken}` },
      timeout: 15_000,
      proxy: false,
      maxRedirects: 0,
      maxContentLength: MAX_RESPONSE_BYTES,
    },
  );
  return response.data;
}

/** Ask Runtime to scan one source now, returning the import job it started.
 *
 * Returns null when the source cannot be synced (`live_sync_unavailable`, HTTP
 * 409). That is a property of the source, not a transport failure, so the
 * caller reports it as "not synced" rather than "the command failed".
 */
export async function submitScan(
  attachment: RuntimeAttachmentLike,
  sourceId: string,
): Promise<ScanJob | null> {
  const response = await axios.post<ScanJob>(
    `${baseUrlOf(attachment)}/api/v1/sources/${encodeURIComponent(sourceId)}/sync`,
    undefined,
    {
      headers: { Authorization: `Bearer ${attachment.apiToken}` },
      timeout: 15_000,
      proxy: false,
      maxRedirects: 0,
      maxContentLength: MAX_RESPONSE_BYTES,
      validateStatus: () => true,
    },
  );
  if (response.status === 409) return null;
  if (response.status >= 400) {
    throw new Error(`scan_rejected http_${response.status}`);
  }
  return response.data;
}

/** The import job a scan starts. Only the fields the CLI reports. */
export interface ScanJob {
  readonly job_id: string;
  readonly source_id: string;
  readonly status: string;
}

/** Ask Runtime to copy its committed database to `target`.
 *
 * The server decides the final path; the CLI reports what it was told, rather
 * than assuming its own argument survived unchanged.
 */
export async function submitBackup(
  attachment: RuntimeAttachmentLike,
  target: string,
): Promise<BackupResult> {
  const response = await axios.post<BackupResult>(
    `${baseUrlOf(attachment)}/api/v1/backup`,
    { target },
    {
      headers: { Authorization: `Bearer ${attachment.apiToken}` },
      timeout: 120_000,
      proxy: false,
      maxRedirects: 0,
      maxContentLength: MAX_RESPONSE_BYTES,
      validateStatus: () => true,
    },
  );
  if (response.status === 422) {
    throw new Error("backup_target_outside_data_dir");
  }
  if (response.status >= 400) {
    throw new Error(`backup_rejected http_${response.status}`);
  }
  return response.data;
}

export interface BackupResult {
  readonly target: string;
  readonly bytes: number;
}

/** Ask Runtime to delete a model profile.
 *
 * Returns false when the server refused: 404 for an unknown id, 409 for the
 * profile that is currently active. Those are properties of the profile, not
 * transport failures, so the caller distinguishes them rather than throwing.
 */
export async function deleteModelProfile(
  attachment: RuntimeAttachmentLike,
  profileId: string,
): Promise<boolean> {
  const response = await axios.delete(
    `${baseUrlOf(attachment)}/api/v1/model-profiles/${encodeURIComponent(profileId)}`,
    {
      headers: { Authorization: `Bearer ${attachment.apiToken}` },
      timeout: 15_000,
      proxy: false,
      maxRedirects: 0,
      maxContentLength: MAX_RESPONSE_BYTES,
      validateStatus: () => true,
    },
  );
  // The shared classifier is the single judge of "refused because of the
  // profile itself" (404 unknown id / 409 still active) versus a transport
  // failure, preferring Core's stable detail over the bare status. Only the
  // former is a `false`; anything else that is not 2xx must throw rather than
  // look like "already gone".
  const detail = (response.data as { detail?: unknown } | undefined)?.detail;
  const classified = classifyModelRemove(response.status, detail);
  if (classified.state === "refused") return false;
  if (classified.state === "unknown") {
    throw new Error(`model_remove_rejected http_${response.status}`);
  }
  return true;
}

/** Read back a write's outcome, or null when the id was never accepted.
 *
 * Unknown must be distinguishable from failed: "never accepted" and "accepted
 * and failed" lead the caller to different actions.
 */
export async function readMemoryWrite(
  attachment: RuntimeAttachmentLike,
  requestId: string,
): Promise<MemoryWriteReceipt | null> {
  try {
    const response = await axios.get<MemoryWriteReceipt>(
      `${baseUrlOf(attachment)}/api/v1/memory-writes/${encodeURIComponent(requestId)}`,
      {
        headers: { Authorization: `Bearer ${attachment.apiToken}` },
        timeout: 10_000,
        proxy: false,
        maxRedirects: 0,
        maxContentLength: MAX_RESPONSE_BYTES,
      },
    );
    return response.data;
  } catch (error) {
    if (axios.isAxiosError(error) && error.response?.status === 404)
      return null;
    throw error;
  }
}

export interface OnboardingState {
  status: string;
  selected_source_ids: string[];
  started_at: string | null;
  completed_at: string | null;
}

/** Send onboarding back to its first-run state.
 *
 * Returns null when the endpoint is not there, so an older runtime degrades to
 * "not available" instead of failing the command. Only the completion flag
 * moves: memories, sources and settings live in other tables, which is what
 * makes this the honest way to see the cold-start screens again.
 */
export async function resetOnboarding(
  attachment: RuntimeAttachmentLike,
): Promise<OnboardingState | null> {
  try {
    const response = await axios.post<OnboardingState>(
      `${baseUrlOf(attachment)}/api/v1/onboarding/reset`,
      undefined,
      {
        headers: { Authorization: `Bearer ${attachment.apiToken}` },
        timeout: 15_000,
        proxy: false,
        maxRedirects: 0,
        maxContentLength: MAX_RESPONSE_BYTES,
      },
    );
    return response.data;
  } catch {
    return null;
  }
}

export async function readScanRoots(
  attachment: RuntimeAttachmentLike,
): Promise<{
  schema_version: number;
  revision: number;
  roots: unknown[];
} | null> {
  try {
    const response = await axios.get<{
      schema_version: number;
      revision: number;
      roots: unknown[];
    }>(`${baseUrlOf(attachment)}/api/v1/scan-roots`, {
      headers: { Authorization: `Bearer ${attachment.apiToken}` },
      timeout: 10_000,
      proxy: false,
      maxRedirects: 0,
      maxContentLength: MAX_RESPONSE_BYTES,
    });
    return response.data;
  } catch {
    return null;
  }
}

export async function addScanRoot(
  attachment: RuntimeAttachmentLike,
  body: { root: string; source?: string; recursive?: boolean },
): Promise<unknown | null> {
  try {
    const response = await axios.post<unknown>(
      `${baseUrlOf(attachment)}/api/v1/scan-roots`,
      body,
      {
        headers: { Authorization: `Bearer ${attachment.apiToken}` },
        timeout: 10_000,
        proxy: false,
        maxRedirects: 0,
        maxContentLength: MAX_RESPONSE_BYTES,
      },
    );
    return response.data;
  } catch (error) {
    return { _error: error instanceof Error ? error.message : String(error) };
  }
}

export async function removeScanRoot(
  attachment: RuntimeAttachmentLike,
  body: { root: string },
): Promise<unknown | null> {
  try {
    const response = await axios.delete<unknown>(
      `${baseUrlOf(attachment)}/api/v1/scan-roots`,
      {
        headers: { Authorization: `Bearer ${attachment.apiToken}` },
        data: body,
        timeout: 10_000,
        proxy: false,
        maxRedirects: 0,
        maxContentLength: MAX_RESPONSE_BYTES,
      },
    );
    return response.data;
  } catch (error) {
    return { _error: error instanceof Error ? error.message : String(error) };
  }
}

export async function readAdapters(
  attachment: RuntimeAttachmentLike,
): Promise<{ schema_version: number; adapters: unknown[] } | null> {
  try {
    const response = await axios.get<{
      schema_version: number;
      adapters: unknown[];
    }>(`${baseUrlOf(attachment)}/api/v1/adapters`, {
      headers: { Authorization: `Bearer ${attachment.apiToken}` },
      timeout: 10_000,
      proxy: false,
      maxRedirects: 0,
      maxContentLength: MAX_RESPONSE_BYTES,
    });
    return response.data;
  } catch {
    return null;
  }
}

export async function probeAdapter(
  attachment: RuntimeAttachmentLike,
  source: string,
): Promise<unknown | null> {
  try {
    const response = await axios.post<unknown>(
      `${baseUrlOf(attachment)}/api/v1/adapters/probe`,
      { source },
      {
        headers: { Authorization: `Bearer ${attachment.apiToken}` },
        timeout: 15_000,
        proxy: false,
        maxRedirects: 0,
        maxContentLength: MAX_RESPONSE_BYTES,
      },
    );
    return response.data;
  } catch (error) {
    return { _error: error instanceof Error ? error.message : String(error) };
  }
}
