import { readFile } from "node:fs/promises";
import path from "node:path";

import { processIsAlive } from "./core-supervisor.js";
import type { RuntimeAttachment } from "./discovery.js";
import {
  discoverRuntimeAttachment,
  RuntimeDiscoveryError,
  type RuntimeIdentity,
} from "./discovery.js";
import { readSdkMigrationBlock } from "./sdk-migration-log.js";

export type RuntimeStatusState =
  | "running"
  | "stopped"
  | "unhealthy"
  | "degraded"
  | "untrusted";

export interface RuntimeStatus {
  state: RuntimeStatusState;
  code: string;
  instanceId?: string;
  host?: string;
  port?: number;
  version?: string;
  /** Sidecar (Broker) state reported by the outer Runtime receipt. Exposed
   * separately because an alive Runtime with a failed Broker must be reported
   * as `degraded`, not `healthy`. */
  sidecar?: "running" | "failed" | "stopped";
}

/** Core/Runtime attachment status, not OS registration or Broker health.
 * Unreadable receipts and a live Core without its required service receipt
 * remain untrusted; they are never evidence that starting a replacement is safe.
 */
export async function inspectRuntimeStatus(options: {
  dataDir: string;
  processIsAlive?: (pid: number) => boolean;
  probeIdentity?: (port: number, apiToken: string) => Promise<RuntimeIdentity>;
  requireServiceReceipt?: boolean;
}): Promise<RuntimeStatus> {
  const processAlive = options.processIsAlive ?? processIsAlive;
  try {
    const attachment = await discoverRuntimeAttachment({
      dataDir: options.dataDir,
      processIsAlive: processAlive,
      probeIdentity: options.probeIdentity ?? probeUnavailable,
      requireServiceReceipt: options.requireServiceReceipt,
    });
    if (attachment) return running(attachment);
    // An unfinished upgrade is why nothing is serving; say so instead of a
    // generic "not running" or "core exited".
    const blocked = await readSdkMigrationBlock(options.dataDir);
    // `discoverRuntimeAttachment` returns null both when nothing is running and
    // when the Core child this service owns has died. Those are not the same
    // thing: a login-start service whose Core exited is still installed, still
    // registered and still holding the data directory. The product rule is
    // explicit -- an alive outer process must not be reported as healthy, and
    // "stopped" would hide a real failure. Report the degraded state instead.
    if (
      options.requireServiceReceipt &&
      (await serviceOwnerIsAlive({
        dataDir: options.dataDir,
        processIsAlive: processAlive,
      }))
    ) {
      return {
        state: "degraded",
        code: blocked?.code ?? "runtime_core_exited",
      };
    }
    return { state: "stopped", code: blocked?.code ?? "runtime_not_running" };
  } catch (error) {
    if (error instanceof RuntimeDiscoveryError) {
      return {
        state:
          error.code === "runtime_identity_unavailable"
            ? "unhealthy"
            : "untrusted",
        code: error.code,
      };
    }
    throw error;
  }
}

/** True when runtime-service.json names a live Runtime process that owns this
 * data directory. A malformed or unreadable receipt is deliberately not
 * "alive": those paths already fail closed elsewhere as untrusted.
 */
async function serviceOwnerIsAlive(options: {
  dataDir: string;
  processIsAlive(pid: number): boolean;
}): Promise<boolean> {
  let raw: string;
  try {
    raw = await readFile(
      path.join(options.dataDir, "runtime-service.json"),
      "utf8",
    );
  } catch {
    return false;
  }
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return false;
  }
  if (!value || typeof value !== "object") return false;
  const item = value as Record<string, unknown>;
  if (
    item.service !== "one-memory-runtime" ||
    typeof item.pid !== "number" ||
    !Number.isInteger(item.pid) ||
    item.pid <= 1
  ) {
    return false;
  }
  return options.processIsAlive(item.pid);
}

function running(attachment: RuntimeAttachment): RuntimeStatus {
  // The Core is reachable, so the Runtime is "running" in the transport sense.
  // A failed sidecar is surfaced alongside it; `background-health` is what
  // turns that pair into the user-visible `degraded` verdict, because only it
  // knows whether the missing capability matters for the caller.
  return {
    state: "running",
    code: "runtime_verified",
    instanceId: attachment.instanceId,
    host: attachment.host,
    port: attachment.port,
    version: attachment.health.version,
    ...(attachment.sidecarState === null
      ? {}
      : { sidecar: attachment.sidecarState }),
  };
}

async function probeUnavailable(): Promise<never> {
  throw new Error("runtime_identity_probe_required");
}
