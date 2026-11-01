import axios from "axios";

const WRITE_PATHS = [
  "/api/v1/sources/connect",
  "/api/v1/model-profiles",
] as const;
/** `POST /api/v2/memories` adds one memory (the app's 添加记忆). */
const MEMORY_ADD_PATH = "/api/v2/memories";
/** One SDK memory; delete names its namespace, and a non-default user, in
 * the query string. */
const SDK_MEMORY_PATH =
  /^\/api\/v2\/memories\/[A-Za-z0-9][\w.-]{0,127}(?:\?(?:kind=(?:chat|experience|fact)(?:&variant=(?:working|coding))?(?:&user_id=[A-Za-z0-9._-]{1,64})?|user_id=[A-Za-z0-9._-]{1,64}))?$/;
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
const GRAPH_WIDTH_PATH = "/api/v1/graph-width";
/** `memhub evolve` starts a run; `memhub settings mode <m> --yes` sets the mode. */
const EVOLVE_PATH = "/api/v2/evolve";
const MEMORY_MODE_PATH = "/api/v1/settings/memory-mode";
/** `memhub settings features set|reset` sets the SDK features. */
const MEMORY_FEATURES_PATH = "/api/v1/settings/memory-features";
/** `memhub settings language <code>` sets the display language Core uses. */
const UI_LANGUAGE_PATH = "/api/v1/settings/ui-language";
const ONBOARDING_COMPLETE_PATH = "/api/v1/onboarding/complete";
const EMBEDDING_SWITCH_PATHS = [
  "/api/v1/embedding-switches",
  "/api/v1/embedding-switches/plan",
] as const;
const EMBEDDING_SWITCH_CANCEL =
  /^\/api\/v1\/embedding-switches\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/cancel$/;
const EMBEDDING_SPACE_PATH =
  /^\/api\/v1\/embedding-spaces\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const MAX_BYTES = 4 * 1024 * 1024;

export type OperationOutcome<T> =
  | { state: "committed"; data: T }
  | {
      state: "rejected";
      code: string;
      http_status: number;
      /** Core's refusal body, when it carries more than the code (a switch
       * plan rides along with `embedding_switch_required`). */
      body?: unknown;
    }
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

export function createCoreOperations(attachment: {
  host: string;
  port: number;
  apiToken: string;
  timeoutMs?: number;
}): CoreOperations &
  CoreDeleteOperations &
  CorePutOperations &
  CorePatchOperations {
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
  return {
    async post<T>(
      path: string,
      body: Record<string, unknown>,
      signal?: AbortSignal,
    ) {
      if (
        !(WRITE_PATHS as readonly string[]).includes(path) &&
        path !== SOURCE_IMPORT_JOB_PATH &&
        path !== MEMORY_ADD_PATH &&
        !MODEL_PROBE_PATH.test(path) &&
        !SOURCE_SYNC_PATH.test(path) &&
        !IMPORT_JOB_ACTION_PATH.test(path) &&
        !SOURCE_AUTHORIZE_PATH.test(path) &&
        !SOURCE_REVOKE_PATH.test(path) &&
        path !== GRAPH_WIDTH_PATH &&
        path !== EVOLVE_PATH &&
        path !== ONBOARDING_COMPLETE_PATH &&
        !(EMBEDDING_SWITCH_PATHS as readonly string[]).includes(path) &&
        !EMBEDDING_SWITCH_CANCEL.test(path) &&
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
      if (
        (response.status >= 400 && response.status < 500) ||
        // Core answers a refusal it can name with 503 (`sdk_reembed_unavailable`)
        // or 507 (`insufficient_disk_space`) on the switch routes.
        ([503, 507].includes(response.status) &&
          (EMBEDDING_SWITCH_PATHS as readonly string[]).includes(path))
      ) {
        return {
          state: "rejected",
          code: safeErrorCode(response.data, response.status),
          http_status: response.status,
          body: response.data,
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
      if (
        path !== MEMORY_MODE_PATH &&
        path !== MEMORY_FEATURES_PATH &&
        path !== UI_LANGUAGE_PATH &&
        (!SDK_MEMORY_PATH.test(path) || path.includes("?"))
      ) {
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
      if (
        !SDK_MEMORY_PATH.test(path) &&
        !MODEL_PROFILE_PATH.test(path) &&
        !EMBEDDING_SPACE_PATH.test(path)
      ) {
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
