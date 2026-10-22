import axios from "axios";

const WRITE_PATHS = [
  "/api/v1/sources/connect",
  "/api/v1/model-profiles",
] as const;
const MEMORY_WRITE_PATH =
  /^\/api\/v1\/memories(?:\/[A-Za-z0-9][\w.-]{0,127}\/(?:correct|scopes|actions\/delete))?$/;
const SDK_MEMORY_PATH = /^\/api\/v2\/memories\/[A-Za-z0-9][\w.-]{0,127}$/;
const MODEL_PROBE_PATH =
  /^\/api\/v1\/model-profiles\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/probe$/;
const MODEL_PROFILE_PATH =
  /^\/api\/v1\/model-profiles\/[A-Za-z0-9][\w.-]{0,127}$/;
const MODEL_PROFILE_ACTIVATE =
  /^\/api\/v1\/model-profiles\/[A-Za-z0-9][\w.-]{0,127}\/activate$/;
const SOURCE_SYNC_PATH =
  /^\/api\/v1\/sources\/[A-Za-z0-9][\w.-]{0,127}\/sync-permission$/;
const SOURCE_AUTHORIZE_PATH =
  /^\/api\/v1\/sources\/[A-Za-z0-9][\w.-]{0,127}\/authorize$/;
const SOURCE_REVOKE_PATH =
  /^\/api\/v1\/sources\/[A-Za-z0-9][\w.-]{0,127}\/revoke$/;
const SOURCE_IMPORT_JOB_PATH = "/api/v1/import-jobs";
const IMPORT_JOB_ACTION_PATH =
  /^\/api\/v1\/import-jobs\/[A-Za-z0-9][\w.-]{0,127}\/(?:pause|resume|cancel)$/;
const CALL_EVENTS_CLEAR_PATH = "/api/v1/call-events/clear";
const CALL_EVENTS_CLEAR_ALL_PATH = "/api/v1/call-events/clear-all";
const IMPORT_FILE_COMMIT =
  /^\/api\/v1\/import-files\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/commit$/;
const IMPORT_FILE =
  /^\/api\/v1\/import-files\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const MEMORY_NOTES_PATH = "/api/v1/memory-notes";
const MEMORY_NOTE_PROCESS =
  /^\/api\/v1\/memory-notes\/[A-Za-z0-9][\w.:-]{0,127}\/process$/;
const EXTRACTION_SETTINGS_PATH = "/api/v1/extraction-settings";
const GRAPH_WIDTH_PATH = "/api/v1/graph-width";
const MANUAL_EXTRACT_PATH = "/api/v1/memory-processing/manual-extract";
const ONBOARDING_COMPLETE_PATH = "/api/v1/onboarding/complete";
const MAX_BYTES = 4 * 1024 * 1024;

export type OperationOutcome<T> =
  | { state: "committed"; data: T }
  | { state: "rejected"; code: string; http_status: number }
  | { state: "unknown"; code: string };

export interface CoreOperations {
  post<T>(
    path: string,
    body: Record<string, unknown>,
    signal?: AbortSignal,
  ): Promise<OperationOutcome<T>>;
}

export interface CoreDeleteOperations {
  delete<T>(path: string, signal?: AbortSignal): Promise<OperationOutcome<T>>;
}

export interface CorePutOperations {
  put<T>(
    path: string,
    body: Record<string, unknown>,
    signal?: AbortSignal,
  ): Promise<OperationOutcome<T>>;
}

export interface CorePatchOperations {
  patch<T>(
    path: string,
    body: Record<string, unknown>,
    signal?: AbortSignal,
  ): Promise<OperationOutcome<T>>;
}

export interface CoreUploadOperations {
  uploadImportFile<T>(
    input: {
      bytes: Uint8Array;
      filename: string;
      inputKind: "raw_conversation" | "memory_content";
      operationId: string;
    },
    signal?: AbortSignal,
  ): Promise<OperationOutcome<T>>;
}

export function createCoreOperations(attachment: {
  host: string;
  port: number;
  apiToken: string;
  timeoutMs?: number;
}): CoreOperations &
  CoreDeleteOperations &
  CorePutOperations &
  CorePatchOperations &
  CoreUploadOperations {
  const client = axios.create({
    baseURL: `http://${attachment.host}:${attachment.port}`,
    headers: { Authorization: `Bearer ${attachment.apiToken}` },
    timeout: attachment.timeoutMs ?? 10_000,
    proxy: false,
    maxRedirects: 0,
    maxContentLength: MAX_BYTES,
    maxBodyLength: 128 * 1024,
    validateStatus: () => true,
  });
  // Uploads carry up to 128 MiB of raw octet-stream, so they need a separate
  // body limit and a deadline that leaves room for parsing on the Core side.
  const uploadClient = axios.create({
    baseURL: `http://${attachment.host}:${attachment.port}`,
    headers: { Authorization: `Bearer ${attachment.apiToken}` },
    timeout: 120_000,
    proxy: false,
    maxRedirects: 0,
    maxContentLength: MAX_BYTES,
    maxBodyLength: 128 * 1024 * 1024,
    validateStatus: () => true,
  });
  return {
    async post<T>(
      path: string,
      body: Record<string, unknown>,
      signal?: AbortSignal,
    ) {
      if (
        !(WRITE_PATHS as readonly string[]).includes(path) &&
        path !== SOURCE_IMPORT_JOB_PATH &&
        !MEMORY_WRITE_PATH.test(path) &&
        !MODEL_PROBE_PATH.test(path) &&
        !SOURCE_SYNC_PATH.test(path) &&
        !IMPORT_JOB_ACTION_PATH.test(path) &&
        !SOURCE_AUTHORIZE_PATH.test(path) &&
        !SOURCE_REVOKE_PATH.test(path) &&
        path !== CALL_EVENTS_CLEAR_PATH &&
        path !== CALL_EVENTS_CLEAR_ALL_PATH &&
        !IMPORT_FILE_COMMIT.test(path) &&
        path !== MEMORY_NOTES_PATH &&
        !MEMORY_NOTE_PROCESS.test(path) &&
        path !== GRAPH_WIDTH_PATH &&
        path !== MANUAL_EXTRACT_PATH &&
        path !== ONBOARDING_COMPLETE_PATH &&
        !MODEL_PROFILE_ACTIVATE.test(path)
      ) {
        throw new Error("core_operation_path_not_allowed");
      }
      let response: { status: number; data: unknown };
      try {
        response = await client.post(
          path,
          body,
          signal ? { signal } : undefined,
        );
      } catch {
        return { state: "unknown", code: "transport_result_unknown" };
      }
      if (response.status >= 200 && response.status < 300) {
        return { state: "committed", data: response.data as T };
      }
      if (response.status >= 400 && response.status < 500) {
        return {
          state: "rejected",
          code: safeErrorCode(response.data, response.status),
          http_status: response.status,
        };
      }
      return {
        state: "unknown",
        code: `http_${response.status}_result_unknown`,
      };
    },
    async put<T>(
      path: string,
      body: Record<string, unknown>,
      signal?: AbortSignal,
    ) {
      if (path !== EXTRACTION_SETTINGS_PATH && !SDK_MEMORY_PATH.test(path)) {
        throw new Error("core_operation_path_not_allowed");
      }
      let response: { status: number; data: unknown };
      try {
        response = await client.put(
          path,
          body,
          signal ? { signal } : undefined,
        );
      } catch {
        return { state: "unknown", code: "transport_result_unknown" };
      }
      if (response.status >= 200 && response.status < 300) {
        return { state: "committed", data: response.data as T };
      }
      if (response.status >= 400 && response.status < 500) {
        return {
          state: "rejected",
          code: safeErrorCode(response.data, response.status),
          http_status: response.status,
        };
      }
      return {
        state: "unknown",
        code: `http_${response.status}_result_unknown`,
      };
    },
    async patch<T>(
      path: string,
      body: Record<string, unknown>,
      signal?: AbortSignal,
    ) {
      if (!MODEL_PROFILE_PATH.test(path)) {
        throw new Error("core_operation_path_not_allowed");
      }
      let response: { status: number; data: unknown };
      try {
        response = await client.patch(
          path,
          body,
          signal ? { signal } : undefined,
        );
      } catch {
        return { state: "unknown", code: "transport_result_unknown" };
      }
      if (response.status >= 200 && response.status < 300) {
        return { state: "committed", data: response.data as T };
      }
      if (response.status >= 400 && response.status < 500) {
        return {
          state: "rejected",
          code: safeErrorCode(response.data, response.status),
          http_status: response.status,
        };
      }
      return {
        state: "unknown",
        code: `http_${response.status}_result_unknown`,
      };
    },
    async delete<T>(path: string, signal?: AbortSignal) {
      if (!IMPORT_FILE.test(path) && !SDK_MEMORY_PATH.test(path)) {
        throw new Error("core_operation_path_not_allowed");
      }
      let response: { status: number; data: unknown };
      try {
        response = await client.delete(path, signal ? { signal } : undefined);
      } catch {
        return { state: "unknown", code: "transport_result_unknown" };
      }
      if (response.status >= 200 && response.status < 300) {
        return { state: "committed", data: response.data as T };
      }
      if (response.status >= 400 && response.status < 500) {
        return {
          state: "rejected",
          code: safeErrorCode(response.data, response.status),
          http_status: response.status,
        };
      }
      return {
        state: "unknown",
        code: `http_${response.status}_result_unknown`,
      };
    },
    async uploadImportFile<T>(
      input: {
        bytes: Uint8Array;
        filename: string;
        inputKind: "raw_conversation" | "memory_content";
        operationId: string;
      },
      signal?: AbortSignal,
    ) {
      const query = new URLSearchParams({
        filename: input.filename,
        input_kind: input.inputKind,
        operation_id: input.operationId,
      });
      let response: { status: number; data: unknown };
      try {
        response = await uploadClient.post(
          `/api/v1/import-files?${query.toString()}`,
          input.bytes,
          {
            headers: {
              "Content-Type": "application/octet-stream",
              "Content-Length": String(input.bytes.byteLength),
            },
            ...(signal ? { signal } : {}),
          },
        );
      } catch {
        return { state: "unknown", code: "transport_result_unknown" };
      }
      if (response.status >= 200 && response.status < 300) {
        return { state: "committed", data: response.data as T };
      }
      if (response.status >= 400 && response.status < 500) {
        return {
          state: "rejected",
          code: safeErrorCode(response.data, response.status),
          http_status: response.status,
        };
      }
      return {
        state: "unknown",
        code: `http_${response.status}_result_unknown`,
      };
    },
  };
}

function safeErrorCode(value: unknown, status: number): string {
  if (
    value &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    typeof (value as Record<string, unknown>).detail === "string"
  ) {
    const detail = (value as { detail: string }).detail;
    if (/^[a-z0-9_.:-]{1,128}$/.test(detail)) return detail;
  }
  return `http_${status}`;
}
