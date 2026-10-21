import { constants, type Stats } from "node:fs";
import { lstat, open } from "node:fs/promises";
import path from "node:path";
import type {
  AskMemoryRequest,
  AskMemoryResponse,
  ContextSearchRequest,
  ConversationSearchResponse,
  HostContextSearchResponse,
  MatterContext,
  RecallReceipt,
  RecallRequest,
} from "@one-memory/contracts";
import axios from "axios";
import {
  HOST_CONTEXT_TIMEOUT_MS,
  isContextResponseForRequest,
  isContextSearchRequest,
} from "./context-protocol.js";
import { readDiscovery } from "./discovery.js";

export type HostCredentialProvider = () => string | Promise<string>;

export interface BrokerClientOptions {
  /** Trusted transport integration only; never populated from tool arguments. */
  credentialProvider?: HostCredentialProvider;
  signal?: AbortSignal;
}

export interface CallEventDeliveryRequest {
  request_id: string;
  delivery: "delivered" | "not_delivered";
  host_session_id: string;
  injection_elapsed_ms: number;
}

export interface CallEventDeliveryResponse {
  delivery: "delivered" | "not_delivered";
  adoption: "unknown";
}

export interface DiagnosticTraceResponse {
  schema_version: 1;
  state: "disabled" | "not_found" | "ok";
  events: Array<Record<string, unknown>>;
}

export async function diagnosticTraceThroughBroker(
  discoveryPath: string,
  request: { request_id?: string; trace_id?: string },
  options?: BrokerClientOptions,
): Promise<DiagnosticTraceResponse> {
  return postThroughBroker(
    discoveryPath,
    "diagnostics/trace",
    request,
    5_000,
    options,
  );
}

export interface BoundaryPeekResponse {
  hookSpecificOutput?: {
    hookEventName: string;
    additionalContext: string;
  };
  one_memory_ack?: string;
}

export function boundaryPeekThroughBroker(
  discoveryPath: string,
  request: unknown,
  timeout: number,
  options?: BrokerClientOptions,
): Promise<BoundaryPeekResponse> {
  return postThroughBroker(
    discoveryPath,
    "boundary/peek",
    request,
    timeout,
    options,
  );
}

export function boundaryAckThroughBroker(
  discoveryPath: string,
  token: string,
  timeout: number,
  options?: BrokerClientOptions,
): Promise<{ acknowledged: boolean }> {
  return postThroughBroker(
    discoveryPath,
    "boundary/ack",
    { token },
    timeout,
    options,
  );
}

export type BrokerClientErrorCode =
  | "host_credential_unavailable"
  | "host_authorization_required"
  | "host_tool_not_granted"
  | "host_scope_denied"
  | "context_upgrade_required"
  | "context_invalid_request"
  | "context_invalid_response"
  | "context_unavailable"
  | "context_cancelled"
  | "context_timeout"
  | "broker_request_failed"
  | "broker_unavailable";

const CLIENT_MESSAGES: Record<BrokerClientErrorCode, string> = {
  context_cancelled:
    "MemHub context request was cancelled; no late context was released.",
  context_timeout: "MemHub context request timed out; coverage is unknown.",
  context_upgrade_required:
    "MemHub context API requires an updated runtime. Do not retry through legacy tools.",
  context_invalid_request: "MemHub context request is invalid.",
  context_invalid_response:
    "MemHub context response is invalid or incompatible.",
  context_unavailable:
    "MemHub is running, but context search is temporarily unavailable.",
  host_credential_unavailable:
    "MemHub host credential is unavailable. Re-link this installation in MemHub.",
  host_authorization_required:
    "MemHub host authorization is required or has changed. Review this installation in MemHub.",
  host_tool_not_granted:
    "MemHub: this tool is not included in the current installation. The connection itself is up.",
  host_scope_denied:
    "MemHub: this recall scope is not available to external tools. Use an explicitly approved scope.",
  // Two different failures used to share one message, and the shared one was
  // the alarming one: a request the broker answered was reported as MemHub not
  // running, which sends someone to restart something that is already up.
  // `broker_unavailable` now means only what it says: nothing answered.
  broker_request_failed:
    "MemHub is running, but this request failed. Check MemHub for the reason.",
  broker_unavailable: "MemHub is not running or its broker is unavailable",
};

export class BrokerClientError extends Error {
  readonly code: BrokerClientErrorCode;

  constructor(code: BrokerClientErrorCode) {
    super(CLIENT_MESSAGES[code]);
    this.name = "BrokerClientError";
    this.code = code;
  }
}

/** Installer-owned POSIX file: raw omhg_v1_ token with an optional final LF.
 * Dedicated immediate parent must be owner-only; ancestors/ACLs are installer
 * responsibilities. No JSON, no automatic repair, no credential caching. Same
 * OS-account adversaries are outside this boundary. Windows needs ACL support.
 * The environment is deliberately read only by executable entry points. */
export async function readHostCredentialFile(
  targetPath: string,
): Promise<string> {
  try {
    if (
      process.platform === "win32" ||
      typeof process.getuid !== "function" ||
      typeof targetPath !== "string" ||
      !path.isAbsolute(targetPath) ||
      targetPath.includes("\0")
    ) {
      throw new BrokerClientError("host_credential_unavailable");
    }
    checkPrivateCredentialNode(await lstat(path.dirname(targetPath)), true);
    checkPrivateCredentialNode(await lstat(targetPath), false);
    const handle = await open(
      targetPath,
      constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK,
    );
    try {
      const info = await handle.stat();
      checkPrivateCredentialNode(info, false);
      if (info.size < 51 || info.size > 52)
        throw new BrokerClientError("host_credential_unavailable");
      // Bounded even if a faulty writer grows the file after stat.
      const buffer = Buffer.alloc(53);
      let bytes = 0;
      while (bytes < buffer.length) {
        const result = await handle.read(
          buffer,
          bytes,
          buffer.length - bytes,
          bytes,
        );
        if (result.bytesRead === 0) break;
        bytes += result.bytesRead;
      }
      const raw = buffer.subarray(0, bytes).toString("utf8");
      const secret =
        raw.length === 52 && raw.endsWith("\n") ? raw.slice(0, -1) : raw;
      if (!isHostSecret(secret))
        throw new BrokerClientError("host_credential_unavailable");
      return secret;
    } finally {
      await handle.close();
    }
  } catch {
    // Filesystem/parser/provider errors can contain paths and secret bytes.
    throw new BrokerClientError("host_credential_unavailable");
  }
}

function checkPrivateCredentialNode(info: Stats, directory: boolean): void {
  if (
    (directory ? !info.isDirectory() : !info.isFile() || info.nlink !== 1) ||
    info.uid !== process.getuid?.() ||
    (info.mode & 0o7777) !== (directory ? 0o700 : 0o600)
  ) {
    throw new BrokerClientError("host_credential_unavailable");
  }
}

function isHostSecret(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length === 51 &&
    /^omhg_v1_[A-Za-z0-9_-]{43}$/.test(value)
  );
}

/** Which read tools this installation's grant allows.
 *
 * Null means the grant could not be read, and is not a denial: the grant is
 * enforced on every call, so an unreadable grant must leave the caller's list
 * as it was rather than empty it. Every failure returns null for that reason,
 * including a broker that is not there at all. */
export async function grantedToolsThroughBroker(
  discoveryPath: string,
  options: BrokerClientOptions = {},
): Promise<readonly string[] | null> {
  try {
    const headers = await abortable(credentialHeaders(options), options.signal);
    options.signal?.throwIfAborted();
    const { baseUrl, instanceId } = await abortable(
      brokerBase(discoveryPath),
      options.signal,
    );
    options.signal?.throwIfAborted();
    await assertBrokerIdentity(baseUrl, instanceId, options.signal);
    const response = await axios.get<{ allowed_tools?: unknown }>(
      `${baseUrl}/granted-tools`,
      {
        headers,
        timeout: 5_000,
        proxy: false,
        maxRedirects: 0,
        signal: options.signal,
      },
    );
    const allowed = response.data?.allowed_tools;
    if (!Array.isArray(allowed)) return null;
    return allowed.filter((tool): tool is string => typeof tool === "string");
  } catch {
    return null;
  }
}

export async function mattersThroughBroker(
  discoveryPath: string,
  request: { query: string },
  options?: BrokerClientOptions,
): Promise<MatterContext[]> {
  return postThroughBroker(discoveryPath, "matters", request, 15_000, options);
}

/** A write accepted by the unified Memory Server entry.
 *
 * `request_id` is the durable handle a caller keeps: it can be queried later
 * with `memoryWriteStatusThroughBroker` instead of retrying the write. Retrying
 * is unsafe because HY has no idempotency key (A0-03).
 */
export interface MemoryWriteReceipt {
  request_id: string;
  state: string;
  created_at: string;
  updated_at: string;
  detail?: string;
}

/** A3-02: submit a write through the same durable entry every entry uses.
 *
 * The MCP tool is one of several doors (HTTP, scanner, hooks); all of them land
 * in the same queue. Nothing here calls HY directly.
 */
export async function memoryWriteThroughBroker(
  discoveryPath: string,
  request: {
    internal_session_id: string;
    first_round: number;
    last_round: number;
    evidence_ids: string[];
    priority?: "live" | "normal";
    idempotency_key?: string;
  },
  options?: BrokerClientOptions,
): Promise<MemoryWriteReceipt> {
  return postThroughBroker(
    discoveryPath,
    "memory-writes",
    request,
    15_000,
    options,
  );
}

/** Read back the outcome of an accepted write, or null when the id is unknown.
 *
 * Unknown must be distinguishable from failed: "never accepted" and "accepted
 * and failed" lead a caller to different actions.
 */
export async function memoryWriteStatusThroughBroker(
  discoveryPath: string,
  requestId: string,
  options?: BrokerClientOptions,
): Promise<MemoryWriteReceipt | null> {
  return getThroughBroker(
    discoveryPath,
    `memory-writes/${encodeURIComponent(requestId)}`,
    10_000,
    options,
  );
}

async function brokerBase(
  discoveryPath: string,
): Promise<{ baseUrl: string; instanceId: string }> {
  const discovery = await readDiscovery(discoveryPath);
  if (discovery.status !== "running" || discovery.port === null) {
    throw new Error("MemHub is not running");
  }
  return {
    baseUrl: `http://${discovery.host}:${discovery.port}`,
    instanceId: discovery.instance_id,
  };
}

async function assertBrokerIdentity(
  baseUrl: string,
  instanceId: string,
  signal?: AbortSignal,
): Promise<void> {
  const health = await axios.get<{
    status: string;
    service: string;
    instance_id: string;
  }>(`${baseUrl}/health`, {
    timeout: 1_000,
    proxy: false,
    maxRedirects: 0,
    signal,
  });
  if (
    health.data.status !== "ok" ||
    health.data.service !== "one-memory-recall-broker" ||
    health.data.instance_id !== instanceId
  ) {
    throw new Error("broker identity mismatch");
  }
}

export async function recallThroughBroker(
  discoveryPath: string,
  request: RecallRequest,
  options?: BrokerClientOptions,
): Promise<RecallReceipt> {
  return postThroughBroker(
    discoveryPath,
    "recall",
    { ...request, timeout_ms: request.timeout_ms ?? HOST_CONTEXT_TIMEOUT_MS },
    20_000,
    options,
  );
}

export async function contextThroughBroker(
  discoveryPath: string,
  asked: ContextSearchRequest,
  options?: BrokerClientOptions,
): Promise<HostContextSearchResponse> {
  if (!isContextSearchRequest(asked))
    throw new BrokerClientError("context_invalid_request");
  const request = {
    ...asked,
    timeout_ms: asked.timeout_ms ?? HOST_CONTEXT_TIMEOUT_MS,
  };
  const deadline = AbortSignal.timeout(request.timeout_ms + 2000);
  const signal = options?.signal
    ? AbortSignal.any([options.signal, deadline])
    : deadline;
  try {
    signal.throwIfAborted();
    const response = await postThroughBroker<unknown>(
      discoveryPath,
      "context",
      request,
      request.timeout_ms + 2000,
      { ...options, signal },
    );
    signal.throwIfAborted();
    if (!isContextResponseForRequest(response, request))
      throw new BrokerClientError("context_invalid_response");
    return response;
  } catch (error) {
    if (signal.aborted)
      throw new BrokerClientError(
        options?.signal?.aborted ? "context_cancelled" : "context_timeout",
      );
    throw error;
  }
}

/** Report what the host process actually delivered, without claiming adoption. */
export async function reportCallEventDeliveryThroughBroker(
  discoveryPath: string,
  request: CallEventDeliveryRequest,
  options?: BrokerClientOptions,
): Promise<CallEventDeliveryResponse> {
  return postThroughBroker(
    discoveryPath,
    "call-events/delivery",
    request,
    5_000,
    options,
  );
}

export async function askThroughBroker(
  discoveryPath: string,
  request: AskMemoryRequest,
  options?: BrokerClientOptions,
): Promise<AskMemoryResponse> {
  return postThroughBroker(discoveryPath, "ask", request, 125_000, options);
}

/** Search the original conversations across every imported app.
 *
 * Separate from recall on purpose. Recall answers "what does MemHub know
 * about me"; this answers "what did I actually say over there", which is what
 * an agent needs to pick a piece of work back up. */
export async function searchConversationsThroughBroker(
  discoveryPath: string,
  request: { query: string; limit?: number },
  options?: BrokerClientOptions,
): Promise<ConversationSearchResponse> {
  return postThroughBroker(
    discoveryPath,
    "conversations",
    request,
    15_000,
    options,
  );
}

async function credentialHeaders(
  options: BrokerClientOptions,
): Promise<Record<string, string>> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "X-One-Memory-Client": "cursor-mcp",
  };
  if (options.credentialProvider === undefined) return headers;
  try {
    const secret = await options.credentialProvider();
    if (!isHostSecret(secret))
      throw new BrokerClientError("host_credential_unavailable");
    headers.Authorization = `Bearer ${secret}`;
    return headers;
  } catch {
    throw new BrokerClientError("host_credential_unavailable");
  }
}

async function postThroughBroker<T>(
  discoveryPath: string,
  endpoint:
    | "recall"
    | "ask"
    | "conversations"
    | "matters"
    | "context"
    | "boundary/peek"
    | "boundary/ack"
    | "call-events/delivery"
    | "memory-writes"
    | "diagnostics/trace",
  request: unknown,
  timeout: number,
  options: BrokerClientOptions = {},
): Promise<T> {
  const headers = await abortable(credentialHeaders(options), options.signal);
  try {
    options.signal?.throwIfAborted();
    const { baseUrl, instanceId } = await abortable(
      brokerBase(discoveryPath),
      options.signal,
    );
    options.signal?.throwIfAborted();
    await assertBrokerIdentity(baseUrl, instanceId, options.signal);
    options.signal?.throwIfAborted();
    const response = await axios.post<T>(`${baseUrl}/${endpoint}`, request, {
      headers,
      timeout,
      proxy: false,
      maxRedirects: 0,
      signal: options.signal,
      ...(endpoint === "context" ? { maxContentLength: 2 * 1024 * 1024 } : {}),
    });
    return response.data;
  } catch (error) {
    if (axios.isAxiosError(error)) {
      if (
        endpoint === "context" &&
        [404, 405, 501].includes(error.response?.status ?? 0)
      )
        throw new BrokerClientError("context_upgrade_required");
      if (endpoint === "context" && (error.response?.status ?? 0) >= 500)
        throw new BrokerClientError("context_unavailable");
      if (error.response?.status === 401)
        throw new BrokerClientError("host_authorization_required");
      if (error.response?.status === 403) {
        const code: unknown = error.response.data?.error;
        throw new BrokerClientError(
          code === "external_recall_scope_denied" ||
            code === "host_grant_scope_denied"
            ? "host_scope_denied"
            : code === "host_grant_tool_denied"
              ? "host_tool_not_granted"
              : "host_authorization_required",
        );
      }
      // Anything the broker actually answered means it is running, however
      // badly the request went. Only a failure to reach it says otherwise.
      if (error.response) throw new BrokerClientError("broker_request_failed");
    }
    // Axios errors include Authorization in config/request: never retain cause.
    throw new BrokerClientError("broker_unavailable");
  }
}

/** Read one resource through the broker.
 *
 * A 404 is returned as `null` rather than thrown: for a write status, "never
 * accepted" is a legitimate answer the caller must be able to distinguish from
 * "accepted and failed", and from the broker being unreachable.
 */
async function getThroughBroker<T>(
  discoveryPath: string,
  endpoint: string,
  timeout: number,
  options: BrokerClientOptions = {},
): Promise<T | null> {
  const headers = await abortable(credentialHeaders(options), options.signal);
  try {
    options.signal?.throwIfAborted();
    const { baseUrl, instanceId } = await abortable(
      brokerBase(discoveryPath),
      options.signal,
    );
    options.signal?.throwIfAborted();
    await assertBrokerIdentity(baseUrl, instanceId, options.signal);
    options.signal?.throwIfAborted();
    const response = await axios.get<T>(`${baseUrl}/${endpoint}`, {
      headers,
      timeout,
      proxy: false,
      maxRedirects: 0,
      signal: options.signal,
    });
    return response.data;
  } catch (error) {
    if (axios.isAxiosError(error)) {
      if (error.response?.status === 404) return null;
      if (error.response?.status === 401)
        throw new BrokerClientError("host_authorization_required");
      if (error.response?.status === 403)
        throw new BrokerClientError("host_authorization_required");
      if (error.response) throw new BrokerClientError("broker_request_failed");
    }
    // Axios errors include Authorization in config/request: never retain cause.
    throw new BrokerClientError("broker_unavailable");
  }
}

/** Late credential/discovery completion cannot dispatch after a cancelled wait. */
function abortable<T>(promise: Promise<T>, signal?: AbortSignal): Promise<T> {
  if (!signal) return promise;
  return new Promise<T>((resolve, reject) => {
    const onAbort = () => reject(signal.reason);
    signal.addEventListener("abort", onAbort, { once: true });
    promise.then(
      (value) => {
        signal.removeEventListener("abort", onAbort);
        resolve(value);
      },
      (error) => {
        signal.removeEventListener("abort", onAbort);
        reject(error);
      },
    );
    if (signal.aborted) {
      signal.removeEventListener("abort", onAbort);
      reject(signal.reason);
    }
  });
}
