/** Shared, fail-closed discovery for a standalone One Memory Runtime. */
import { lstat, readFile } from "node:fs/promises";
import path from "node:path";
import type { HealthResponse } from "@one-memory/contracts";

export type RuntimeIdentity = HealthResponse;

export interface RuntimeAttachment {
  instanceId: string;
  apiToken: string;
  host: "127.0.0.1";
  port: number;
  servicePid: number | null;
  /** Sidecar (Broker) state published by the outer Runtime's receipt. `failed`
   * means the Runtime is alive but a capability is gone -> callers report
   * `degraded`, never `healthy`. Null/absent when the receipt predates the
   * field (an older Runtime), which is treated as "unknown", not "healthy". */
  sidecarState?: "running" | "failed" | "stopped" | null;
  health: RuntimeIdentity;
}

export interface RuntimeAttachmentOptions {
  dataDir: string;
  processIsAlive(pid: number): boolean;
  probeIdentity(port: number, apiToken: string): Promise<RuntimeIdentity>;
  platform?: NodeJS.Platform;
  currentUid?: number;
  requireServiceReceipt?: boolean;
}

export class RuntimeDiscoveryError extends Error {
  readonly code:
    | "runtime_receipt_invalid"
    | "runtime_receipt_unavailable"
    | "runtime_service_receipt_invalid"
    | "runtime_service_receipt_unavailable"
    | "runtime_service_not_ready"
    | "runtime_token_unavailable"
    | "runtime_identity_unavailable"
    | "runtime_identity_mismatch";

  constructor(code: RuntimeDiscoveryError["code"], options?: ErrorOptions) {
    super(code, options);
    this.name = "RuntimeDiscoveryError";
    this.code = code;
  }
}

interface RuntimeReceipt {
  schemaVersion?: number;
  service?: string;
  instanceId?: string;
  pid: number;
  parentPid: number | null;
  host?: string;
  port?: number;
  startedAt: string;
}

interface RuntimeServiceReceipt {
  instanceId: string;
  pid: number;
  status: "starting" | "running" | "stopping";
  corePort: number | null;
  sidecarState: "running" | "failed" | "stopped" | null;
}

/** The Core port a *running* managed Runtime published, or null when there is
 * no usable receipt.
 *
 * `runtime-service.json.core_port` is the one authority for which port the
 * Core actually bound — it is written after the bind succeeds, from the URL the
 * Runtime itself reached. Callers that would otherwise guess (the desktop
 * bridge, the renderer's base URL) read this instead of assuming a fixed 8765,
 * so a non-default install root cannot be pointed at the daily Core.
 */
export async function readPublishedCorePort(
  dataDir: string,
): Promise<number | null> {
  const receipt = await readRuntimeServiceReceipt(dataDir).catch(() => null);
  if (receipt === null || receipt.status !== "running") return null;
  return receipt.corePort;
}

/**
 * Return a verified standalone Runtime, or null when no attachable standalone
 * instance is present. A live v1 instance that cannot be authenticated throws:
 * callers must not race it with another writer.
 */
export async function discoverRuntimeAttachment(
  options: RuntimeAttachmentOptions,
): Promise<RuntimeAttachment | null> {
  const receipt = await readRuntimeReceipt(options.dataDir);
  if (receipt === null || !options.processIsAlive(receipt.pid)) return null;
  // A parent-owned Core is another desktop's child or a recoverable orphan,
  // not the standalone Runtime this contract is allowed to attach to.
  let service: RuntimeServiceReceipt | null = null;
  if (receipt.parentPid !== null || options.requireServiceReceipt) {
    service = await readRuntimeServiceReceipt(options.dataDir);
    if (service === null) {
      // A live Core is not evidence that another Runtime may safely be started.
      // The launcher may wait for this receipt only after its own spawn.
      if (options.requireServiceReceipt) {
        throw new RuntimeDiscoveryError("runtime_service_not_ready");
      }
      return null;
    }
    if (
      !options.processIsAlive(service.pid) ||
      (receipt.parentPid !== null && service.pid !== receipt.parentPid) ||
      service.instanceId !== receipt.instanceId
    ) {
      throw new RuntimeDiscoveryError("runtime_service_receipt_invalid");
    }
    if (service.status !== "running" || service.corePort !== receipt.port) {
      throw new RuntimeDiscoveryError("runtime_service_not_ready");
    }
  }
  // Legacy receipts stay on the caller's existing ownership/conflict path.
  if (receipt.schemaVersion !== 1 && receipt.service === undefined) return null;
  if (!isAttachableReceipt(receipt)) {
    throw new RuntimeDiscoveryError("runtime_receipt_invalid");
  }
  const token = await readRuntimeToken(options);
  if (token === null) {
    throw new RuntimeDiscoveryError("runtime_token_unavailable");
  }
  let health: RuntimeIdentity;
  try {
    health = await options.probeIdentity(receipt.port, token);
  } catch (error) {
    throw new RuntimeDiscoveryError("runtime_identity_unavailable", {
      cause: error,
    });
  }
  if (!isRuntimeIdentity(health, receipt.instanceId)) {
    throw new RuntimeDiscoveryError("runtime_identity_mismatch");
  }
  return Object.freeze({
    instanceId: receipt.instanceId,
    apiToken: token,
    host: "127.0.0.1",
    port: receipt.port,
    servicePid: service?.pid ?? null,
    sidecarState: service?.sidecarState ?? null,
    health: Object.freeze({ ...health }),
  });
}

async function readRuntimeServiceReceipt(
  dataDir: string,
): Promise<RuntimeServiceReceipt | null> {
  let raw: string;
  try {
    raw = await readFile(path.join(dataDir, "runtime-service.json"), "utf8");
  } catch (error) {
    if (isMissingFile(error)) return null;
    throw new RuntimeDiscoveryError("runtime_service_receipt_unavailable");
  }
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    throw new RuntimeDiscoveryError("runtime_service_receipt_invalid");
  }
  if (!value || typeof value !== "object") {
    throw new RuntimeDiscoveryError("runtime_service_receipt_invalid");
  }
  // Unknown keys are ignored: a receipt written by an older Runtime may still
  // carry `reader_port`, which no longer means anything.
  const item = value as Record<string, unknown>;
  if (
    item.schema_version !== 1 ||
    item.service !== "one-memory-runtime" ||
    !["starting", "running", "stopping"].includes(String(item.status)) ||
    typeof item.instance_id !== "string" ||
    !item.instance_id ||
    typeof item.pid !== "number" ||
    !Number.isInteger(item.pid) ||
    item.pid <= 1 ||
    (item.core_port !== null &&
      (typeof item.core_port !== "number" ||
        !Number.isInteger(item.core_port) ||
        item.core_port < 1024 ||
        item.core_port > 65_535)) ||
    (item.sidecar !== undefined &&
      item.sidecar !== "running" &&
      item.sidecar !== "failed" &&
      item.sidecar !== "stopped")
  ) {
    throw new RuntimeDiscoveryError("runtime_service_receipt_invalid");
  }
  return {
    instanceId: item.instance_id,
    pid: item.pid,
    status: item.status as RuntimeServiceReceipt["status"],
    corePort: item.core_port,
    sidecarState:
      item.sidecar === undefined
        ? null
        : (item.sidecar as RuntimeServiceReceipt["sidecarState"]),
  };
}

async function readRuntimeReceipt(
  dataDir: string,
): Promise<RuntimeReceipt | null> {
  let raw: string;
  try {
    raw = await readFile(path.join(dataDir, "runtime.json"), "utf8");
  } catch (error) {
    if (isMissingFile(error)) return null;
    throw new RuntimeDiscoveryError("runtime_receipt_unavailable");
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new RuntimeDiscoveryError("runtime_receipt_invalid");
  }
  if (typeof parsed !== "object" || parsed === null) {
    throw new RuntimeDiscoveryError("runtime_receipt_invalid");
  }
  const value = parsed as Record<string, unknown>;
  if (typeof value.pid !== "number" || !Number.isInteger(value.pid)) {
    throw new RuntimeDiscoveryError("runtime_receipt_invalid");
  }
  return {
    schemaVersion:
      typeof value.schema_version === "number"
        ? value.schema_version
        : undefined,
    service: typeof value.service === "string" ? value.service : undefined,
    instanceId:
      typeof value.instance_id === "string" ? value.instance_id : undefined,
    pid: value.pid,
    parentPid: typeof value.parent_pid === "number" ? value.parent_pid : null,
    host: typeof value.host === "string" ? value.host : undefined,
    port: typeof value.port === "number" ? value.port : undefined,
    startedAt:
      typeof value.started_at === "string" ? value.started_at : "未知时间",
  };
}

async function readRuntimeToken(
  options: RuntimeAttachmentOptions,
): Promise<string | null> {
  const platform = options.platform ?? process.platform;
  if (platform === "win32") return null;
  const target = path.join(options.dataDir, "runtime-token");
  try {
    const metadata = await lstat(target);
    if (!metadata.isFile() || metadata.isSymbolicLink()) return null;
    if ((metadata.mode & 0o077) !== 0) return null;
    const currentUid =
      options.currentUid ??
      (typeof process.getuid === "function" ? process.getuid() : undefined);
    if (currentUid !== undefined && metadata.uid !== currentUid) return null;
    const token = await readFile(target, "utf8");
    return token.length > 0 && token.length <= 4096 ? token : null;
  } catch {
    return null;
  }
}

function isMissingFile(error: unknown): boolean {
  return (
    error !== null &&
    typeof error === "object" &&
    "code" in error &&
    error.code === "ENOENT"
  );
}

function isAttachableReceipt(
  receipt: RuntimeReceipt,
): receipt is RuntimeReceipt & {
  schemaVersion: 1;
  service: "one-memory-core";
  instanceId: string;
  host: "127.0.0.1";
  port: number;
} {
  return (
    receipt.schemaVersion === 1 &&
    receipt.service === "one-memory-core" &&
    typeof receipt.instanceId === "string" &&
    receipt.instanceId.length > 0 &&
    receipt.host === "127.0.0.1" &&
    Number.isInteger(receipt.port) &&
    (receipt.port ?? 0) >= 1024 &&
    (receipt.port ?? 0) <= 65_535
  );
}

function isRuntimeIdentity(
  value: RuntimeIdentity,
  instanceId: string,
): value is RuntimeIdentity {
  return (
    value?.status === "ok" &&
    value.service === "one-memory-core" &&
    typeof value.version === "string" &&
    value.instance_id === instanceId
  );
}
