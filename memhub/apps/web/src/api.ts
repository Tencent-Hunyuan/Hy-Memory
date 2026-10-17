import type {
  Matter,
  MatterContext,
  MatterEvidence,
  MatterPatch,
  MatterRun,
} from "@one-memory/contracts";
import {
  type AskMemoryEvent,
  type AskMemoryResponse,
  CORE_SERVICE,
  type ConnectSourcesResponse,
  type ConversationBrowserPage,
  type ConversationMemories,
  type ConversationSearchResponse,
  type ConversationTranscript,
  type DetectionResponse,
  type ExtractionSettings,
  type ExtractionWindowPreset,
  type GovernedMemory,
  type GraphWidthDecision,
  type GraphWidthDecisionReceipt,
  type GraphWidthStatus,
  type HealthResponse,
  type HomeSummary,
  type ImportIssue,
  type ImportJob,
  type LiveSyncHealth,
  type ManualExtractReceipt,
  type MemoryDetail,
  type MemoryGraphResponse,
  type MemoryIndexSync,
  type MemoryLibraryPage,
  type MemoryLibraryQuery,
  type MemoryNoteInput,
  type MemoryNoteReceipt,
  type MemoryOrigin,
  type MemoryProcessingSummary,
  type ModelCreationOperation,
  type ModelProbeOperationReceipt,
  type ModelProbeResult,
  type ModelProfile,
  type ModelProfileInput,
  type ModelProfilePatch,
  type OnboardingState,
  type RecentConversationsPage,
  type SavedMemoryNoteEvidence,
  type SourceDirectoryCandidate,
  type SourceImportReceipt,
  type SourceResponse,
  type SourceSyncState,
} from "@one-memory/contracts";
// The write contracts (request normalisation + receipt parsing) live in one
// shared package so the CLI judges the same body with the same code. What
// stays here is the transport adapter: axios wiring, recovery clients, and the
// UI-facing error mapping.
import {
  buildMemoryActionRequest,
  buildMemoryCorrectRequest,
  buildMemoryCreateRequest,
  buildMemoryNoteSaveRequest,
  buildMemoryScopesRequest,
  buildModelProfileCreateRequest,
  buildModelProfileUpdateRequest,
  buildSourceImportRequest,
  buildSourceSyncRequest,
  classifyModelRemove,
  hasSourceSyncTarget,
  normalizeManualExtractEvidence,
  parseExtractionSettingsReceipt,
  parseGraphWidthDecisionReceipt,
  parseManualExtractReceipt,
  parseMemoryNoteReceipt,
  parseMemoryReceipt,
  parseModelProfileReceipt,
  SourceSyncInputError,
} from "@one-memory/operations";
import axios from "axios";
import {
  type FileUploadTransport,
  parseFileImportReceipt,
  reconcileFileUpload,
  recoverableFileUpload,
} from "./file-upload-client";
import { recoverableProbe } from "./model-probe-client";

export const DEFAULT_CORE_URL = "http://127.0.0.1:8765";

/** The base URL the renderer should talk to.
 *
 * The renderer never decides the Core port itself. The desktop main process
 * owns that decision: for a non-default install root it derives a stable,
 * distinct port (`service-plan.ts` hashes the root into 9200-9899 and writes it
 * into the LaunchAgent plist as `ONE_MEMORY_CORE_PORT`), and it exposes the
 * resolved port through `getApiBaseUrl`. So when a desktop bridge is present,
 * its value is the only authority: `VITE_CORE_URL` must be ignored, or an App
 * installed to a non-default root would talk to whatever the daily 8765 Core
 * happens to be. Without a bridge (a browser tab against `pnpm dev`), the
 * configured URL is used, falling back to the fixed local default.
 *
 * A port value in the *renderer* environment is deliberately not consulted: the
 * renderer cannot verify the install root, so honouring it here would let
 * anything that can set a renderer env var redirect Core traffic.
 */
export function resolveCoreBaseUrl(
  bridgeBaseUrl: string | null | undefined,
  configuredUrl: string | null | undefined,
): string {
  if (typeof bridgeBaseUrl === "string" && bridgeBaseUrl.length > 0)
    return bridgeBaseUrl;
  if (typeof configuredUrl === "string" && configuredUrl.length > 0)
    return configuredUrl;
  return DEFAULT_CORE_URL;
}

export async function getCallHistoryRequest(params: {
  limit: number;
  offset: number;
  host_id?: string;
}): Promise<unknown> {
  return (
    await client.get("/api/v1/call-events", {
      params,
      maxContentLength: 2 * 1024 * 1024,
    })
  ).data;
}
export async function clearCallEventsRequest(
  eventIds: string[],
  traceId?: string,
): Promise<unknown> {
  return (
    await client.post(
      "/api/v1/call-events/clear",
      {
        event_ids: eventIds,
        user_confirmed: true,
      },
      traceId ? { headers: { "X-One-Memory-Trace-Id": traceId } } : undefined,
    )
  ).data;
}
export async function clearAllCallEventsRequest(
  traceId?: string,
): Promise<unknown> {
  return (
    await client.post(
      "/api/v1/call-events/clear-all",
      {
        user_confirmed: true,
      },
      traceId ? { headers: { "X-One-Memory-Trace-Id": traceId } } : undefined,
    )
  ).data;
}
export async function getCallReferencesRequest(
  eventId: string,
): Promise<unknown> {
  return (
    await client.get(
      `/api/v1/call-events/${encodeURIComponent(eventId)}/references`,
      { maxContentLength: 128 * 1024 },
    )
  ).data;
}

export type { HealthResponse };

export async function saveMemoryNote(
  note: MemoryNoteInput,
  traceId?: string,
): Promise<MemoryNoteReceipt> {
  const request = buildMemoryNoteSaveRequest(note);
  const response = await client.post<MemoryNoteReceipt>(
    request.path,
    request.body,
    traceId ? { headers: { "X-One-Memory-Trace-Id": traceId } } : undefined,
  );
  const receipt = parseMemoryNoteReceipt(response.data);
  if (receipt === null) throw new Error("memory_note_receipt_invalid");
  return receipt;
}

export async function getMemoryNotes(): Promise<MemoryNoteReceipt[]> {
  const response = await client.get<unknown>("/api/v1/memory-notes");
  if (!Array.isArray(response.data))
    throw new Error("memory_note_receipt_invalid");
  const notes = response.data.map(parseMemoryNoteReceipt);
  if (notes.some((note) => note === null))
    throw new Error("memory_note_receipt_invalid");
  return notes as MemoryNoteReceipt[];
}

export async function getMemoryNoteOperation(
  requestId: string,
): Promise<MemoryNoteReceipt> {
  const response = await client.get<unknown>(
    `/api/v1/memory-notes/operations/${encodeURIComponent(requestId)}`,
  );
  const receipt = parseMemoryNoteReceipt(response.data);
  if (receipt === null) throw new Error("memory_note_receipt_invalid");
  return receipt;
}

export async function getMemoryNoteEvidence(
  id: string,
): Promise<SavedMemoryNoteEvidence> {
  return (
    await client.get<SavedMemoryNoteEvidence>(
      `/api/v1/memory-notes/${encodeURIComponent(id)}/evidence`,
    )
  ).data;
}

export async function processMemoryNote(
  id: string,
  traceId?: string,
): Promise<MemoryNoteReceipt> {
  const response = await client.post<MemoryNoteReceipt>(
    `/api/v1/memory-notes/${encodeURIComponent(id)}/process`,
    {},
    // Starting processing is a write; carry the trace id so the ui.action /
    // ui.result pair joins the kernel legs in one chain.
    traceId ? { headers: { "X-One-Memory-Trace-Id": traceId } } : undefined,
  );
  const receipt = parseMemoryNoteReceipt(response.data);
  if (receipt === null) throw new Error("memory_note_receipt_invalid");
  return receipt;
}

export async function getMatters(): Promise<Matter[]> {
  return (await client.get<Matter[]>("/api/v1/matters")).data;
}
export async function getHistoryFile(
  id: string,
): Promise<import("@one-memory/contracts").FileImportReceipt> {
  const response = await client.get<unknown>(
    `/api/v1/import-files/${encodeURIComponent(id)}`,
  );
  const receipt = parseFileImportReceipt(response.data, id);
  if (receipt === null) throw new Error("file_import_receipt_invalid");
  return receipt;
}
export async function uploadHistoryFile(
  file: File,
  inputKind: "raw_conversation" | "memory_content" = "raw_conversation",
  traceId?: string,
): Promise<import("@one-memory/contracts").FileImportReceipt> {
  return recoverableFileUpload(
    file,
    inputKind,
    historyUploadTransport,
    traceId,
  );
}
const historyUploadTransport: FileUploadTransport = {
  lookup: async (id) =>
    (
      await client.get<
        import("@one-memory/contracts").FileImportOperationReceipt
      >(`/api/v1/import-files/operations/${encodeURIComponent(id)}`)
    ).data,
  upload: async (file, inputKind, id, traceId) =>
    (
      await client.post<
        import("@one-memory/contracts").FileImportOperationReceipt
      >(
        `/api/v1/import-files?filename=${encodeURIComponent(file.name)}&input_kind=${inputKind}&operation_id=${encodeURIComponent(id)}`,
        file,
        {
          headers: {
            "Content-Type": "application/octet-stream",
            ...(traceId ? { "X-One-Memory-Trace-Id": traceId } : {}),
          },
          timeout: 120000,
        },
      )
    ).data,
  readPreview: getHistoryFile,
};
export async function reconcileHistoryUpload() {
  return reconcileFileUpload(historyUploadTransport);
}
export async function commitHistoryFile(
  id: string,
  traceId?: string,
): Promise<import("@one-memory/contracts").FileImportReceipt> {
  return (
    await client.post<import("@one-memory/contracts").FileImportReceipt>(
      `/api/v1/import-files/${encodeURIComponent(id)}/commit`,
      { confirmed: true },
      traceId ? { headers: { "X-One-Memory-Trace-Id": traceId } } : undefined,
    )
  ).data;
}
export async function discardHistoryFile(
  id: string,
  traceId?: string,
): Promise<void> {
  await client.delete(`/api/v1/import-files/${encodeURIComponent(id)}`, {
    ...(traceId ? { headers: { "X-One-Memory-Trace-Id": traceId } } : {}),
  });
}

export async function getMatterRuns(): Promise<MatterRun[]> {
  return (await client.get<MatterRun[]>("/api/v1/matters/runs")).data;
}
export async function discoverMatters(traceId?: string): Promise<MatterRun> {
  return (
    await client.post<MatterRun>(
      "/api/v1/matters/discover",
      {},
      traceId ? { headers: { "X-One-Memory-Trace-Id": traceId } } : undefined,
    )
  ).data;
}
export async function patchMatter(
  id: string,
  patch: MatterPatch,
  traceId?: string,
): Promise<Matter> {
  return (
    await client.patch<Matter>(
      `/api/v1/matters/${encodeURIComponent(id)}`,
      patch,
      traceId ? { headers: { "X-One-Memory-Trace-Id": traceId } } : undefined,
    )
  ).data;
}
export async function getMatterEvidence(
  id: string,
  evidence: string,
): Promise<MatterEvidence> {
  return (
    await client.get<MatterEvidence>(
      `/api/v1/matters/${encodeURIComponent(id)}/evidence/${encodeURIComponent(evidence)}`,
    )
  ).data;
}
export async function getMatterContext(id: string): Promise<MatterContext> {
  return (
    await client.get<MatterContext>(
      `/api/v1/matters/${encodeURIComponent(id)}/context`,
    )
  ).data;
}
export async function getMatterHistory(id: string): Promise<Matter[]> {
  return (
    await client.get<Matter[]>(
      `/api/v1/matters/${encodeURIComponent(id)}/history`,
    )
  ).data;
}
export async function addMatterNote(
  id: string,
  text: string,
  expected_version: number,
  traceId?: string,
): Promise<MatterEvidence> {
  return (
    await client.post<MatterEvidence>(
      `/api/v1/matters/${encodeURIComponent(id)}/notes`,
      { text, expected_version },
      traceId ? { headers: { "X-One-Memory-Trace-Id": traceId } } : undefined,
    )
  ).data;
}
export async function refreshMatter(
  id: string,
  traceId?: string,
): Promise<MatterRun> {
  return (
    await client.post<MatterRun>(
      `/api/v1/matters/${encodeURIComponent(id)}/refresh`,
      {},
      traceId ? { headers: { "X-One-Memory-Trace-Id": traceId } } : undefined,
    )
  ).data;
}

const client = axios.create({
  baseURL: import.meta.env.VITE_CORE_URL ?? DEFAULT_CORE_URL,
  // The core shares a process with the ingest pipeline, and while that is
  // catching up even /health — which does no work — has been measured at 3.5s,
  // and a library read at 20.7s. Five seconds was tight enough that a real
  // library timed out on every attempt and read as empty.
  timeout: 30_000,
});

/** Reader process base URL, set by the desktop bridge when available.
 *
 * The Reader is a separate read-only process. When its port is known and the
 * process is healthy, read routes are served from it (lower latency, no GIL
 * contention with the Core's background work). Falls back to Core on any
 * failure within 1.5 seconds, and does not retry Reader for 30 seconds after.
 */
let readerBaseUrl: string | null = null;
let readerCircuitOpenUntil = 0;

export function setReaderBaseUrl(url: string | null): void {
  readerBaseUrl = url;
}

function readerAvailable(): boolean {
  return readerBaseUrl !== null && Date.now() >= readerCircuitOpenUntil;
}

function openReaderCircuit(): void {
  readerCircuitOpenUntil = Date.now() + 30_000;
}

const READER_TIMEOUT_MS = 1_500;

const READER_PATHS = new Set([
  "/api/v1/home-summary",
  "/api/v1/memories",
  "/api/v1/memory-library",
  "/api/v1/conversations/search",
  "/api/v1/memories/graph",
]);

const READER_POST_PATHS = new Set(["/api/v1/recall", "/api/v1/context/search"]);

async function tryReader<T>(
  method: "get" | "post",
  url: string,
  config: Record<string, unknown> | undefined,
  fallback: () => Promise<T>,
): Promise<T> {
  if (
    !readerAvailable() ||
    !(READER_PATHS.has(url) || READER_POST_PATHS.has(url))
  )
    return fallback();
  try {
    const response = await axios.request<T>({
      method,
      url: `${readerBaseUrl}${url}`,
      timeout: READER_TIMEOUT_MS,
      ...(method === "post"
        ? { data: config?.data ?? config?.body }
        : { params: config?.params }),
      headers: (config?.headers as Record<string, string>) ?? {},
    });
    return response.data;
  } catch {
    openReaderCircuit();
    return fallback();
  }
}

client.interceptors.request.use(async (config) => {
  // A traced interaction sets its own id so the ui-action line, this request
  // and Core's kernel-call/return lines all share one trace. Untraced calls
  // (the large majority, and every poll) mint a fresh one as before.
  if (!config.headers["X-One-Memory-Trace-Id"])
    config.headers["X-One-Memory-Trace-Id"] = createTraceId();
  const desktopBridge =
    typeof window === "undefined" ? undefined : window.oneMemory;
  if (desktopBridge) {
    const [baseURL, token] = await Promise.all([
      desktopBridge.getApiBaseUrl(),
      desktopBridge.getApiToken(),
    ]);
    // The bridge owns the real port (non-default install roots included); never
    // let the build-time `VITE_CORE_URL` override it.
    config.baseURL = resolveCoreBaseUrl(baseURL, null);
    config.headers.Authorization = `Bearer ${token}`;
  } else if (import.meta.env.DEV && import.meta.env.VITE_CORE_API_TOKEN) {
    config.headers.Authorization = `Bearer ${import.meta.env.VITE_CORE_API_TOKEN}`;
  }
  return config;
});

export function createTraceId(random: () => number = Math.random): string {
  const native = globalThis.crypto?.randomUUID;
  if (native) return native.call(globalThis.crypto);
  const bytes = Array.from({ length: 16 }, () => Math.floor(random() * 256));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = bytes
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export async function getHealth(): Promise<HealthResponse> {
  const response = await client.get<unknown>("/api/v1/health");
  if (!isHealthResponse(response.data)) {
    throw new Error("Core returned an invalid health response");
  }
  return response.data;
}

export async function getSources(
  options: { signal?: AbortSignal } = {},
): Promise<SourceResponse[]> {
  const response = await client.get<unknown>("/api/v1/sources", {
    signal: options.signal,
  });
  if (!Array.isArray(response.data) || !response.data.every(isSourceResponse)) {
    throw new Error("Core returned an invalid source list");
  }
  return response.data;
}

export async function getSourceImportReceipt(
  sourceId: string,
  queryEvidenceId?: string,
): Promise<SourceImportReceipt> {
  const response = await client.get<unknown>(
    `/api/v1/sources/${encodeURIComponent(sourceId)}/import-receipt`,
    queryEvidenceId
      ? { params: { query_evidence_id: queryEvidenceId } }
      : undefined,
  );
  if (!isSourceImportReceipt(response.data))
    throw new Error("source_import_receipt_invalid");
  return response.data;
}

export async function detectSource(
  sourceId: string,
  traceId?: string,
): Promise<DetectionResponse> {
  const response = await client.post<DetectionResponse>(
    `/api/v1/sources/${encodeURIComponent(sourceId)}/detect`,
    undefined,
    traceId ? { headers: { "X-One-Memory-Trace-Id": traceId } } : undefined,
  );
  return response.data;
}

export async function setSourceSync(
  source: SourceResponse,
  enabled: boolean,
  traceId?: string,
): Promise<SourceSyncState> {
  if (!hasSourceSyncTarget(source)) {
    throw new SourceSyncInputError("source_not_authorized");
  }
  const request = buildSourceSyncRequest(source, enabled);
  const response = await client.post<SourceSyncState>(
    request.path,
    request.body,
    traceId ? { headers: { "X-One-Memory-Trace-Id": traceId } } : undefined,
  );
  if (
    response.data.source_id !== source.source_id ||
    response.data.sync_enabled !== enabled
  )
    throw Error("source_sync_response_invalid");
  return response.data;
}

export async function createImportJob(
  sourceId: string,
  operation: "history" | "sync" = "history",
  traceId?: string,
): Promise<ImportJob> {
  const response = await client.post<ImportJob>(
    "/api/v1/import-jobs",
    {
      source_id: sourceId,
      operation,
    },
    traceId ? { headers: { "X-One-Memory-Trace-Id": traceId } } : undefined,
  );
  const job = parseImportJob(response.data);
  if (job === null) throw new Error("import_job_invalid");
  return job;
}

export async function connectSources(
  sourceIds: string[],
  confirmEnvironmentOverrides: boolean,
  options: {
    fullRescan?: boolean;
    onboarding?: boolean;
    enableSync?: boolean;
    requestId?: string;
    sourceRoots?: Record<string, string>;
    sourceCandidateRevisions?: Record<string, number>;
    importUploadId?: string;
    traceId?: string;
  } = {},
): Promise<ConnectSourcesResponse> {
  // `traceId` is transport metadata, never part of the body, so it is split
  // out before the shared builder sees the options.
  const { traceId, ...input } = options;
  const request = buildSourceImportRequest({
    sourceIds,
    confirmEnvironmentOverrides,
    ...input,
  });
  const response = await client.post<ConnectSourcesResponse>(
    request.path,
    request.body,
    traceId ? { headers: { "X-One-Memory-Trace-Id": traceId } } : undefined,
  );
  return response.data;
}

export async function previewSourceDirectory(sourceId: string, path: string) {
  const response = await client.post<SourceDirectoryCandidate>(
    `/api/v1/sources/${encodeURIComponent(sourceId)}/candidate/preview`,
    { path },
  );
  return response.data;
}

export async function saveSourceDirectory(
  sourceId: string,
  input: {
    path: string;
    expected_revision: number;
    expected_root_id: string;
    user_confirmed: true;
  },
  traceId?: string,
) {
  const response = await client.put<SourceDirectoryCandidate>(
    `/api/v1/sources/${encodeURIComponent(sourceId)}/candidate`,
    input,
    traceId ? { headers: { "X-One-Memory-Trace-Id": traceId } } : undefined,
  );
  return response.data;
}

export async function getSourceConnection(
  requestId: string,
): Promise<ConnectSourcesResponse> {
  const response = await client.get<ConnectSourcesResponse>(
    `/api/v1/source-connections/${encodeURIComponent(requestId)}`,
  );
  return response.data;
}

export async function getOnboarding(): Promise<OnboardingState> {
  const response = await client.get<OnboardingState>("/api/v1/onboarding");
  return response.data;
}

export async function completeOnboarding(
  traceId?: string,
): Promise<OnboardingState> {
  const response = await client.post<OnboardingState>(
    "/api/v1/onboarding/complete",
    undefined,
    traceId ? { headers: { "X-One-Memory-Trace-Id": traceId } } : undefined,
  );
  return response.data;
}

export async function getImportJobs(): Promise<ImportJob[]> {
  const response = await client.get<ImportJob[]>("/api/v1/import-jobs");
  if (!Array.isArray(response.data)) throw new Error("import_jobs_invalid");
  const jobs = response.data.map(parseImportJob);
  if (jobs.some((job) => job === null)) throw new Error("import_jobs_invalid");
  return jobs as ImportJob[];
}

export async function getImportJob(jobId: string): Promise<ImportJob> {
  const response = await client.get<ImportJob>(
    `/api/v1/import-jobs/${encodeURIComponent(jobId)}`,
  );
  const job = parseImportJob(response.data);
  if (job === null) throw new Error("import_job_invalid");
  return job;
}

export async function getImportIssues(jobId: string): Promise<ImportIssue[]> {
  const response = await client.get<ImportIssue[]>(
    `/api/v1/import-jobs/${encodeURIComponent(jobId)}/issues`,
  );
  if (!Array.isArray(response.data) || !response.data.every(isImportIssue))
    throw new Error("import_issues_invalid");
  return response.data;
}

export async function changeImportJob(
  jobId: string,
  action: "pause" | "resume" | "cancel",
  traceId?: string,
): Promise<ImportJob> {
  const response = await client.post<ImportJob>(
    `/api/v1/import-jobs/${encodeURIComponent(jobId)}/${action}`,
    undefined,
    traceId ? { headers: { "X-One-Memory-Trace-Id": traceId } } : undefined,
  );
  const job = parseImportJob(response.data);
  if (job === null) throw new Error("import_job_invalid");
  return job;
}

export async function getModelProfiles(): Promise<ModelProfile[]> {
  const response = await client.get<ModelProfile[]>("/api/v1/model-profiles");
  if (!Array.isArray(response.data)) throw new Error("model_profiles_invalid");
  const profiles = response.data.map(parseModelProfileReceipt);
  if (profiles.some((profile) => profile === null))
    throw new Error("model_profiles_invalid");
  return profiles as ModelProfile[];
}

export async function getModelCreationOperation(
  operationId: string,
): Promise<ModelCreationOperation> {
  const response = await client.get<unknown>(
    `/api/v1/model-profile-creations/${encodeURIComponent(operationId)}`,
  );
  if (!isModelCreationOperation(response.data))
    throw new Error("model_creation_operation_invalid");
  return response.data;
}

export async function abandonModelCreationOperation(
  operationId: string,
): Promise<ModelCreationOperation> {
  const response = await client.post<unknown>(
    `/api/v1/model-profile-creations/${encodeURIComponent(operationId)}/abandon`,
  );
  if (!isModelCreationOperation(response.data))
    throw new Error("model_creation_operation_invalid");
  return response.data;
}

export async function createModelProfile(
  input: ModelProfileInput,
  traceId?: string,
): Promise<ModelProfile> {
  // `creation_operation_id` is a *transport* field, not a reviewable config
  // value: the CLI sends it as `{...config, creation_operation_id}` and the
  // shared builder rejects it inside the config. Strip it here so the builder
  // sees only config, then hand the id over through its own parameter. Passing
  // it through inside `input` made every create throw
  // `creation_operation_id_not_allowed` before a request was ever sent.
  const { creation_operation_id: creationOperationId, ...config } =
    input as ModelProfileInput & { creation_operation_id?: string };
  // The shared builder mirrors the CLI's allow-list and bounds, so the UI
  // rejects a bad body with a named field instead of posting it and hoping.
  const request = buildModelProfileCreateRequest(
    config as ModelProfileInput,
    creationOperationId ?? "",
  );
  const response = await client.post<ModelProfile>(
    request.path,
    request.body,
    traceId ? { headers: { "X-One-Memory-Trace-Id": traceId } } : undefined,
  );
  // A 2xx body is not automatically a profile: the CLI refuses to trust it
  // (`headless-flows.ts` `validateModelProfile` -> `model_receipt_invalid`),
  // so the UI validates the same receipt instead of rendering a malformed
  // body as a created, possibly active, profile.
  const receipt = parseModelProfileReceipt(response.data);
  if (receipt === null) throw new Error("model_receipt_invalid");
  return receipt;
}

export async function probeModelProfile(
  profileId: string,
  inputRevision: number,
  traceId?: string,
): Promise<ModelProbeResult> {
  return recoverableProbe(profileId, inputRevision, {
    lookup: async (operationId) =>
      (
        await client.get<ModelProbeOperationReceipt>(
          `/api/v1/model-probe-operations/${encodeURIComponent(operationId)}`,
        )
      ).data,
    probe: async (id, request) =>
      (
        await client.post<ModelProbeOperationReceipt>(
          `/api/v1/model-profiles/${encodeURIComponent(id)}/probe`,
          request,
          traceId
            ? { headers: { "X-One-Memory-Trace-Id": traceId } }
            : undefined,
        )
      ).data,
  });
}

export async function activateModelProfile(
  profileId: string,
  traceId?: string,
): Promise<ModelProfile> {
  const response = await client.post<ModelProfile>(
    `/api/v1/model-profiles/${encodeURIComponent(profileId)}/activate`,
    undefined,
    traceId ? { headers: { "X-One-Memory-Trace-Id": traceId } } : undefined,
  );
  const receipt = parseModelProfileReceipt(response.data);
  if (receipt === null) throw new Error("model_profile_receipt_invalid");
  return receipt;
}

export async function updateModelProfile(
  profileId: string,
  patch: ModelProfilePatch,
  traceId?: string,
): Promise<ModelProfile> {
  // The CLI validates the patch before PATCHing; the Web used to post the
  // object straight through, so a stray key or out-of-range value was only
  // discovered if Core happened to reject it. Both surfaces now build the
  // request through the same shared validator.
  const request = buildModelProfileUpdateRequest(profileId, patch);
  const response = await client.patch<ModelProfile>(
    request.path,
    request.body,
    traceId ? { headers: { "X-One-Memory-Trace-Id": traceId } } : undefined,
  );
  const receipt = parseModelProfileReceipt(response.data);
  if (receipt === null) throw new Error("model_profile_receipt_invalid");
  return receipt;
}

export async function deleteModelProfile(
  profileId: string,
  traceId?: string,
): Promise<void> {
  try {
    await client.delete(
      `/api/v1/model-profiles/${encodeURIComponent(profileId)}`,
      traceId ? { headers: { "X-One-Memory-Trace-Id": traceId } } : undefined,
    );
  } catch (reason) {
    const status = axios.isAxiosError(reason)
      ? reason.response?.status
      : undefined;
    if (status === undefined) throw reason;
    const outcome = classifyModelRemove(
      status,
      axios.isAxiosError(reason) ? reason.response?.data?.detail : undefined,
    );
    if (outcome.state === "refused") {
      // A refusal is a property of the profile, not a transport failure.
      // Attach the classification to the original AxiosError so apiErrorCode
      // and the existing message mapping keep working while callers can also
      // branch on `modelRemoveOutcome`.
      throw Object.assign(reason as object, {
        modelRemoveOutcome: outcome.state,
        modelRemoveCode: outcome.code,
      });
    }
    throw reason;
  }
}

export async function getHomeSummary(): Promise<HomeSummary> {
  return tryReader<HomeSummary>(
    "get",
    "/api/v1/home-summary",
    undefined,
    async () => {
      const response = await client.get<HomeSummary>("/api/v1/home-summary");
      return response.data;
    },
  );
}

export async function getLiveSyncHealth(): Promise<LiveSyncHealth[]> {
  const response = await client.get<LiveSyncHealth[]>("/api/v1/live-sync");
  if (!Array.isArray(response.data) || !response.data.every(isLiveSyncHealth))
    throw new Error("live_sync_health_invalid");
  return response.data;
}

export async function getMemoryLibrary(
  query: MemoryLibraryQuery = {},
  signal?: AbortSignal,
): Promise<MemoryLibraryPage> {
  const response = await client.get<MemoryLibraryPage>(
    "/api/v1/memory-library",
    { params: query, signal },
  );
  return response.data;
}

export async function getMemories(query = ""): Promise<GovernedMemory[]> {
  return tryReader<GovernedMemory[]>(
    "get",
    "/api/v1/memories",
    { params: query ? { query } : undefined },
    async () => {
      const response = await client.get<GovernedMemory[]>("/api/v1/memories", {
        params: query ? { query } : undefined,
      });
      return response.data;
    },
  );
}

export async function askMemories(
  question: string,
  scope = "all-apps",
): Promise<AskMemoryResponse> {
  const response = await client.post<AskMemoryResponse>(
    "/api/v1/memories/ask",
    { question, scope },
    { timeout: 120_000 },
  );
  return response.data;
}

export async function* askMemoriesStream(
  question: string,
  scope = "all-apps",
  signal?: AbortSignal,
  traceId?: string,
): AsyncGenerator<AskMemoryEvent> {
  const desktopBridge =
    typeof window === "undefined" ? undefined : window.oneMemory;
  // Same rule as the axios interceptor: a present bridge is the only authority
  // for the Core port, so the streaming path cannot drift onto the daily Core.
  const baseUrl = resolveCoreBaseUrl(
    desktopBridge ? await desktopBridge.getApiBaseUrl() : null,
    desktopBridge ? null : import.meta.env.VITE_CORE_URL,
  );
  const token = desktopBridge
    ? await desktopBridge.getApiToken()
    : import.meta.env.DEV
      ? // Same rule as the axios interceptor above: the build-time token is a
        // development convenience (browser console against a local Core), never
        // something a production bundle should carry. Without the `DEV` guard
        // this streaming path would embed the token while every axios call
        // would not.
        import.meta.env.VITE_CORE_API_TOKEN
      : undefined;
  const response = await fetch(`${baseUrl}/api/v1/memories/ask/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      // This path uses raw `fetch`, so it does not go through the axios
      // interceptor that stamps every other call. Without the header here Core
      // still logs its own request, but under a different id from the
      // renderer's `ui.action`/`ui.result`, so the four lines never group.
      ...(traceId ? { "X-One-Memory-Trace-Id": traceId } : {}),
    },
    body: JSON.stringify({ question, scope }),
    signal,
  });
  if (!response.ok || !response.body) {
    throw new Error(`ask_stream_failed_${response.status}`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // SSE frames are separated by a blank line; keep any partial tail buffered.
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const line = frame.trim();
      if (!line.startsWith("data:")) continue;
      yield JSON.parse(line.slice(5).trim()) as AskMemoryEvent;
    }
  }
}

export async function getRecentConversations(params?: {
  limit?: number;
  offset?: number;
  sourceId?: string;
  recentActivity?: boolean;
}): Promise<RecentConversationsPage> {
  const response = await client.get<RecentConversationsPage>(
    "/api/v1/conversations",
    {
      params: {
        limit: params?.limit ?? 30,
        offset: params?.offset ?? 0,
        ...(params?.sourceId ? { source_id: params.sourceId } : {}),
        ...(params?.recentActivity ? { recent_activity: true } : {}),
      },
    },
  );
  return response.data;
}

export async function searchConversations(
  query: string,
  limit = 20,
): Promise<ConversationSearchResponse> {
  return tryReader<ConversationSearchResponse>(
    "get",
    "/api/v1/conversations/search",
    { params: { query, limit } },
    async () => {
      const response = await client.get<ConversationSearchResponse>(
        "/api/v1/conversations/search",
        { params: { query, limit } },
      );
      return response.data;
    },
  );
}

export async function getConversation(
  evidenceId: string,
  params: { offset?: number; limit?: number } = {},
): Promise<ConversationTranscript> {
  const response = await client.get<ConversationTranscript>(
    `/api/v1/conversations/${encodeURIComponent(evidenceId)}`,
    { params },
  );
  return response.data;
}

export async function getConversationBrowser(params?: {
  offset?: number;
  limit?: number;
  sourceId?: string;
}): Promise<ConversationBrowserPage> {
  const response = await client.get<ConversationBrowserPage>(
    "/api/v1/conversation-browser",
    {
      params: {
        offset: params?.offset ?? 0,
        limit: params?.limit ?? 50,
        ...(params?.sourceId ? { source_id: params.sourceId } : {}),
      },
    },
  );
  return response.data;
}

export async function getConversationMemories(
  evidenceId: string,
): Promise<ConversationMemories> {
  const response = await client.get<ConversationMemories>(
    `/api/v1/conversations/${encodeURIComponent(evidenceId)}/memories`,
  );
  return response.data;
}

export async function getMemory(memoryId: string): Promise<MemoryDetail> {
  const response = await client.get<MemoryDetail>(
    `/api/v1/memories/${encodeURIComponent(memoryId)}`,
  );
  return response.data;
}

/**
 * One map request at a time; callers asking while one is in flight share it.
 *
 * D91: on Kristin's machine, three overlapping graph requests all timed out
 * and dragged the memory list from 0.4 s to 4.3 s. The map is the slowest
 * read in the app and the least urgent, so it never runs twice at once.
 */
let pendingMemoryGraph: Promise<MemoryGraphResponse> | null = null;

export async function getMemoryGraphWith(
  transport: { get: typeof client.get } = client,
): Promise<MemoryGraphResponse> {
  if (pendingMemoryGraph) return pendingMemoryGraph;
  pendingMemoryGraph = transport
    .get<MemoryGraphResponse>("/api/v1/memories/graph", {
      params: { limit: 300 },
    })
    .then((response) => response.data)
    .finally(() => {
      pendingMemoryGraph = null;
    });
  return pendingMemoryGraph;
}

export async function getMemoryGraph(): Promise<MemoryGraphResponse> {
  return tryReader<MemoryGraphResponse>(
    "get",
    "/api/v1/memories/graph",
    { params: { limit: 300 } },
    () => getMemoryGraphWith(),
  );
}

/** Test seam: forget whatever request is in flight. */
export function resetMemoryGraphCoalescingForTest(): void {
  pendingMemoryGraph = null;
}

export async function digestMemoryGraph(
  traceId?: string,
): Promise<Record<string, unknown>> {
  const response = await client.post<Record<string, unknown>>(
    "/api/v1/memories/graph/digest",
    undefined,
    {
      timeout: 15 * 60_000,
      ...(traceId ? { headers: { "X-One-Memory-Trace-Id": traceId } } : {}),
    },
  );
  return response.data;
}

export async function getMemoryProcessing(): Promise<MemoryProcessingSummary> {
  const response = await client.get<MemoryProcessingSummary>(
    "/api/v1/memory-processing",
  );
  return response.data;
}

export async function getDetailedLogging(): Promise<boolean> {
  const response = await client.get("/api/v1/diagnostics/settings");
  const value = response.data as Record<string, unknown>;
  if (value.schema_version !== 1 || typeof value.detailed_logging !== "boolean")
    throw new Error("diagnostic_settings_invalid");
  return value.detailed_logging;
}

export async function setDetailedLogging(enabled: boolean): Promise<boolean> {
  const response = await client.put("/api/v1/diagnostics/settings", {
    enabled,
  });
  const value = response.data as Record<string, unknown>;
  if (value.schema_version !== 1 || typeof value.detailed_logging !== "boolean")
    throw new Error("diagnostic_settings_invalid");
  return value.detailed_logging;
}

export type DiagnosticLogLevel = "error" | "warning" | "info" | "debug";

export async function getDiagnosticLogLevel(): Promise<DiagnosticLogLevel> {
  const response = await client.get("/api/v1/diagnostics/settings");
  const value = response.data as Record<string, unknown>;
  if (
    value.schema_version !== 1 ||
    !["error", "warning", "info", "debug"].includes(String(value.log_level))
  )
    throw new Error("diagnostic_settings_invalid");
  return value.log_level as DiagnosticLogLevel;
}

export async function setDiagnosticLogLevel(
  level: DiagnosticLogLevel,
): Promise<DiagnosticLogLevel> {
  const response = await client.put("/api/v1/diagnostics/log-level", { level });
  const value = response.data as Record<string, unknown>;
  if (
    value.schema_version !== 1 ||
    !["error", "warning", "info", "debug"].includes(String(value.log_level))
  )
    throw new Error("diagnostic_settings_invalid");
  return value.log_level as DiagnosticLogLevel;
}

export async function retryUnknownMemoryProcessing(
  traceId?: string,
): Promise<{ requeued: number }> {
  const response = await client.post<{ reconciled: number; requeued: number }>(
    "/api/v1/memory-processing/retry-unknown",
    { confirm: true },
    traceId ? { headers: { "X-One-Memory-Trace-Id": traceId } } : undefined,
  );
  return response.data;
}

export async function getExtractionSettings(): Promise<ExtractionSettings> {
  const response = await client.get<ExtractionSettings>(
    "/api/v1/extraction-settings",
  );
  return response.data;
}

export async function setExtractionWindow(
  windowPreset: ExtractionWindowPreset,
  traceId?: string,
): Promise<ExtractionSettings> {
  const response = await client.put<ExtractionSettings>(
    "/api/v1/extraction-settings",
    { window_preset: windowPreset },
    traceId ? { headers: { "X-One-Memory-Trace-Id": traceId } } : undefined,
  );
  // The CLI validates this receipt (`ui-write-flows-extended.ts`
  // `executeExtractionWindowSet` -> `ui_write_receipt_invalid`); the Web stored
  // the raw body, so a malformed 200 became a saved preset.
  const receipt = parseExtractionSettingsReceipt(response.data, windowPreset);
  if (receipt === null) throw new Error("extraction_settings_receipt_invalid");
  return receipt;
}

export async function manualExtract(
  evidenceIds: string[] = [],
  traceId?: string,
): Promise<ManualExtractReceipt> {
  // Shared with the CLI plan check; throws `manual_extract_evidence_ids_invalid`
  // rather than sending an unvalidated list.
  const evidence_ids = normalizeManualExtractEvidence(evidenceIds);
  const response = await client.post<ManualExtractReceipt>(
    "/api/v1/memory-processing/manual-extract",
    { evidence_ids },
    traceId ? { headers: { "X-One-Memory-Trace-Id": traceId } } : undefined,
  );
  const receipt = parseManualExtractReceipt(response.data);
  if (receipt === null) throw new Error("manual_extract_receipt_invalid");
  return receipt;
}

export async function getGraphWidth(): Promise<GraphWidthStatus> {
  const response = await client.get<GraphWidthStatus>("/api/v1/graph-width");
  return response.data;
}

export async function decideGraphWidth(
  decision: GraphWidthDecision,
  traceId?: string,
): Promise<GraphWidthDecisionReceipt> {
  const response = await client.post<GraphWidthDecisionReceipt>(
    "/api/v1/graph-width",
    { decision },
    traceId ? { headers: { "X-One-Memory-Trace-Id": traceId } } : undefined,
  );
  // Without the `mismatch` key `GraphWidthPrompt` would treat the decision as
  // resolved; the CLI only commits a body that carries it.
  const receipt = parseGraphWidthDecisionReceipt(response.data);
  if (receipt === null) throw new Error("graph_width_receipt_invalid");
  return receipt;
}

export async function mutateMemory(
  memoryId: string,
  action: "undo" | "delete",
  revision: number,
  requestId: string,
  traceId?: string,
): Promise<GovernedMemory> {
  const request = buildMemoryActionRequest(
    memoryId,
    action,
    revision,
    requestId,
  );
  const response = await client.post<GovernedMemory>(
    request.path,
    request.body,
    traceId ? { headers: { "X-One-Memory-Trace-Id": traceId } } : undefined,
  );
  const receipt = parseMemoryReceipt(response.data, {
    memoryId,
    expectedRevision: revision,
    expectedStatus: action === "delete" ? "deleted" : "confirmed",
  });
  if (receipt === null) throw new Error("memory_action_receipt_invalid");
  return receipt;
}

export async function correctMemory(
  memoryId: string,
  revision: number,
  content: string,
  scopes: string[] | undefined,
  requestId: string,
  traceId?: string,
): Promise<GovernedMemory> {
  const request = buildMemoryCorrectRequest(
    memoryId,
    revision,
    content,
    scopes,
    requestId,
  );
  const response = await client.post<GovernedMemory>(
    request.path,
    request.body,
    traceId ? { headers: { "X-One-Memory-Trace-Id": traceId } } : undefined,
  );
  const receipt = parseMemoryReceipt(response.data, {
    memoryId,
    expectedRevision: revision,
    expectedStatus: "confirmed",
  });
  if (receipt === null) throw new Error("memory_correct_receipt_invalid");
  return receipt;
}

export async function syncMemoryIndex(
  memoryId: string,
  traceId?: string,
): Promise<MemoryIndexSync> {
  const response = await client.post<MemoryIndexSync>(
    `/api/v1/memories/${encodeURIComponent(memoryId)}/index-sync`,
    undefined,
    traceId ? { headers: { "X-One-Memory-Trace-Id": traceId } } : undefined,
  );
  return response.data;
}

export async function setMemoryScopes(
  memoryId: string,
  revision: number,
  scopes: string[],
  requestId: string,
  traceId?: string,
): Promise<GovernedMemory> {
  const request = buildMemoryScopesRequest(
    memoryId,
    revision,
    scopes,
    requestId,
  );
  const response = await client.post<GovernedMemory>(
    request.path,
    request.body,
    traceId ? { headers: { "X-One-Memory-Trace-Id": traceId } } : undefined,
  );
  const receipt = parseMemoryReceipt(response.data, {
    memoryId,
    expectedRevision: revision,
    expectedStatus: "confirmed",
  });
  if (receipt === null) throw new Error("memory_scopes_receipt_invalid");
  return receipt;
}

export function apiErrorCode(reason: unknown): string | null {
  if (!axios.isAxiosError(reason)) return null;
  const data = reason.response?.data;
  if (!data || typeof data !== "object") return null;
  const detail = (data as Record<string, unknown>).detail;
  return typeof detail === "string" ? detail : null;
}

export function historyImportIssue(
  reason: unknown,
): { code: string; location: string; excerpt: string } | null {
  if (
    !axios.isAxiosError(reason) ||
    apiErrorCode(reason) !== "generic_import_invalid"
  )
    return null;
  const issue: unknown = reason.response?.data?.issue;
  if (!issue || typeof issue !== "object") return null;
  const value = issue as Record<string, unknown>;
  if (
    typeof value.code !== "string" ||
    typeof value.location !== "string" ||
    !/^(line|message):\d+$/.test(value.location) ||
    typeof value.excerpt !== "string"
  )
    return null;
  return {
    code: value.code,
    location: value.location,
    excerpt: value.excerpt.slice(0, 200),
  };
}

export function isHealthResponse(value: unknown): value is HealthResponse {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Record<string, unknown>;
  return (
    candidate.status === "ok" &&
    candidate.service === CORE_SERVICE &&
    typeof candidate.version === "string" &&
    typeof candidate.instance_id === "string" &&
    candidate.instance_id.length > 0 &&
    // Optional, so an older Core still reads as healthy, but not free-form:
    // an unrecognised value would otherwise show up as "已连接" and hide a
    // catch-up that had failed.
    (candidate.memory_backfill === undefined ||
      candidate.memory_backfill === "running" ||
      candidate.memory_backfill === "done" ||
      candidate.memory_backfill === "failed")
  );
}

export function isSourceResponse(value: unknown): value is SourceResponse {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.source_id === "string" &&
    typeof candidate.display_name === "string" &&
    Array.isArray(candidate.storage_kinds) &&
    candidate.storage_kinds.every((kind) => typeof kind === "string") &&
    typeof candidate.available === "boolean" &&
    typeof candidate.authorized === "boolean" &&
    (typeof candidate.detected === "boolean" || candidate.detected === null) &&
    (typeof candidate.root_label === "string" ||
      candidate.root_label === null) &&
    typeof candidate.candidate_path === "string" &&
    measuredCount(candidate.imported_messages) &&
    measuredCount(candidate.imported_conversations) &&
    (candidate.candidate_kind === "default" ||
      candidate.candidate_kind === "environment_override") &&
    // Guarded rather than defaulted: an unrecognised value must not quietly
    // read as "additions_only" and tell the user we track less than we do,
    // nor as "full_dynamic" and promise more.
    (candidate.sync_completeness === "full_dynamic" ||
      candidate.sync_completeness === "one_time_snapshot" ||
      candidate.sync_completeness === "additions_only")
  );
}

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

/** Strict transport view of Core's `ImportJob` (`extra="forbid"` there too).
 *
 * The progress strip and job actions render these counters directly and branch
 * on `status`/`phase`. A malformed response must fail before it can become a
 * negative count or an unknown state label in UI state.
 */
export function parseImportJob(value: unknown): ImportJob | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const job = value as Record<string, unknown>;
  const allowed = new Set([
    "job_id",
    "user_id",
    "source_id",
    "status",
    "phase",
    "checkpoint",
    "discovered",
    "discovered_targets",
    "emitted",
    "stored",
    "revised",
    "merged",
    "skipped",
    "errors",
    "created_at",
    "updated_at",
    "operation",
    "history_after",
  ]);
  if (
    Object.keys(job).some((key) => !allowed.has(key)) ||
    typeof job.job_id !== "string" ||
    typeof job.user_id !== "string" ||
    typeof job.source_id !== "string" ||
    !IMPORT_STATUSES.includes(job.status as (typeof IMPORT_STATUSES)[number]) ||
    !(
      job.phase === null ||
      SCAN_PHASES.includes(job.phase as (typeof SCAN_PHASES)[number])
    ) ||
    !(job.checkpoint === null || typeof job.checkpoint === "string") ||
    !measuredCount(job.discovered) ||
    !measuredCount(job.discovered_targets) ||
    !measuredCount(job.emitted) ||
    !measuredCount(job.stored) ||
    !measuredCount(job.revised) ||
    !measuredCount(job.merged) ||
    !measuredCount(job.skipped) ||
    !measuredCount(job.errors) ||
    typeof job.created_at !== "string" ||
    !Number.isFinite(Date.parse(job.created_at)) ||
    typeof job.updated_at !== "string" ||
    !Number.isFinite(Date.parse(job.updated_at)) ||
    (job.operation !== undefined &&
      job.operation !== "history" &&
      job.operation !== "sync") ||
    !(
      job.history_after === undefined ||
      job.history_after === null ||
      (typeof job.history_after === "string" &&
        Number.isFinite(Date.parse(job.history_after)))
    )
  )
    return null;
  // Keep the legacy omission meaningful: older responses did not carry
  // `operation`, and the UI correctly treated those jobs as history.
  return {
    ...job,
    operation: job.operation === undefined ? "history" : job.operation,
  } as unknown as ImportJob;
}

export function isImportIssue(value: unknown): value is ImportIssue {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const issue = value as Record<string, unknown>;
  return (
    Number.isSafeInteger(issue.issue_id) &&
    (issue.issue_id as number) >= 0 &&
    typeof issue.job_id === "string" &&
    (issue.kind === "error" || issue.kind === "skip") &&
    (issue.conversation_id === null ||
      typeof issue.conversation_id === "string") &&
    typeof issue.reason === "string" &&
    typeof issue.created_at === "string" &&
    Number.isFinite(Date.parse(issue.created_at))
  );
}

export function isLiveSyncHealth(value: unknown): value is LiveSyncHealth {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const item = value as Record<string, unknown>;
  const nullableText = (candidate: unknown) =>
    candidate === null ||
    (typeof candidate === "string" &&
      candidate.length > 0 &&
      candidate.length <= 512 &&
      !/\p{C}/u.test(candidate));
  return (
    typeof item.source_id === "string" &&
    item.source_id.length > 0 &&
    item.source_id.length <= 128 &&
    (item.mode === "polling" || item.mode === "watch_and_polling") &&
    ["idle", "running", "degraded", "unavailable"].includes(
      item.state as string,
    ) &&
    nullableText(item.last_job_id) &&
    nullableText(item.last_attempt_at) &&
    nullableText(item.last_success_at) &&
    nullableText(item.last_error_code)
  );
}

function measuredCount(value: unknown): value is number {
  return Number.isSafeInteger(value) && (value as number) >= 0;
}

function isSourceImportExample(value: unknown): boolean {
  if (!value || typeof value !== "object") return false;
  const item = value as Record<string, unknown>;
  return (
    typeof item.evidence_id === "string" &&
    typeof item.role === "string" &&
    typeof item.written_by === "string" &&
    (item.created_at === null || typeof item.created_at === "string") &&
    typeof item.excerpt === "string" &&
    typeof item.truncated === "boolean"
  );
}

export function isSourceImportReceipt(
  value: unknown,
): value is SourceImportReceipt {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const receipt = value as Record<string, unknown>;
  return (
    typeof receipt.source_id === "string" &&
    receipt.source_id.length > 0 &&
    typeof receipt.measured_at === "string" &&
    Number.isFinite(Date.parse(receipt.measured_at)) &&
    measuredCount(receipt.messages) &&
    measuredCount(receipt.conversations) &&
    measuredCount(receipt.person_messages) &&
    measuredCount(receipt.agent_messages) &&
    measuredCount(receipt.tool_messages) &&
    measuredCount(receipt.unknown_author_messages) &&
    measuredCount(receipt.unknown_time_messages) &&
    Array.isArray(receipt.examples) &&
    receipt.examples.every(isSourceImportExample) &&
    (receipt.query_examples === undefined ||
      (Array.isArray(receipt.query_examples) &&
        receipt.query_examples.every(isSourceImportExample)))
  );
}

function isModelCreationOperation(
  value: unknown,
): value is ModelCreationOperation {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const operation = value as Record<string, unknown>;
  const keys = Object.keys(operation);
  if (
    keys.length !== 4 ||
    keys.some(
      (key) =>
        !["operation_id", "state", "profile_id", "created_revision"].includes(
          key,
        ),
    )
  )
    return false;
  return (
    typeof operation.operation_id === "string" &&
    operation.operation_id.length > 0 &&
    ["committed", "pending", "deleted", "not_found"].includes(
      operation.state as string,
    ) &&
    (operation.profile_id === null ||
      (typeof operation.profile_id === "string" &&
        operation.profile_id.length > 0)) &&
    (operation.created_revision === null || operation.created_revision === 1)
  );
}

export async function createMemory(
  draft: {
    content: string;
    scopes: string[];
    evidence_ids: string[];
    origin: MemoryOrigin;
  },
  requestId: string,
): Promise<GovernedMemory> {
  const request = buildMemoryCreateRequest(draft, requestId);
  const response = await client.post<GovernedMemory>(
    request.path,
    request.body,
  );
  return response.data;
}
