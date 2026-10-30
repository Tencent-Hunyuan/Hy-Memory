import path from "node:path";
import axios from "axios";
import {
  inspectRecallBrokerStatus,
  type RecallBrokerStatus,
} from "./broker-status.js";
import type { RuntimeIdentity } from "./discovery.js";
import { inspectRuntimeStatus, type RuntimeStatus } from "./runtime-status.js";

export interface BackgroundHealth {
  state: "healthy" | "stopped" | "degraded" | "untrusted" | "unknown";
  code: string;
  core: RuntimeStatus["state"] | "unknown";
  broker: RuntimeStatus["state"] | "not_checked";
  instanceId?: string;
}

type RuntimeInspector = (
  options: Parameters<typeof inspectRuntimeStatus>[0],
) => Promise<RuntimeStatus>;
type BrokerInspector = (options: {
  dataDir: string;
  expectedInstanceId: string;
}) => Promise<RecallBrokerStatus>;

/** Observe only. A healthy snapshot is not login consent or OS registration.
 * Never start/repair services, return tokens or follow an arbitrary host URL.
 */
export async function inspectBackgroundHealth(options: {
  dataDir: string;
  inspectRuntime?: RuntimeInspector;
  inspectBroker?: BrokerInspector;
}): Promise<BackgroundHealth> {
  if (!path.isAbsolute(options.dataDir) || /[\0\r\n]/.test(options.dataDir)) {
    throw Error("background_data_directory_invalid");
  }
  const inspectCore = options.inspectRuntime ?? inspectRuntimeStatus;
  const inspectBroker = options.inspectBroker ?? inspectRecallBrokerStatus;
  const coreOptions = {
    dataDir: options.dataDir,
    requireServiceReceipt: true,
    probeIdentity,
  };
  try {
    const first = await inspectCore(coreOptions);
    if (first.state !== "running") {
      return {
        state: first.state === "unhealthy" ? "degraded" : first.state,
        code: "background_runtime_not_healthy",
        core: first.state,
        broker: "not_checked",
      };
    }
    if (!first.instanceId) {
      return {
        state: "untrusted",
        code: "background_runtime_identity_missing",
        core: "untrusted",
        broker: "not_checked",
      };
    }
    // The outer Runtime captured a sidecar (Broker) fault. That is the exact
    // contract case "outer alive but Broker failed -> degraded": report it
    // without probing, and never let a stale/absent Broker receipt read as
    // healthy just because the Core still answers.
    if (first.sidecar === "failed") {
      return {
        state: "degraded",
        code: "background_broker_failed",
        core: "running",
        broker: "unhealthy",
      };
    }
    const broker = await inspectBroker({
      dataDir: options.dataDir,
      expectedInstanceId: first.instanceId,
    });
    const current = await inspectCore(coreOptions);
    if (
      current.state !== "running" ||
      current.instanceId !== first.instanceId ||
      current.port !== first.port
    ) {
      return {
        state: "unknown",
        code: "background_identity_changed",
        core: current.state,
        broker: "not_checked",
      };
    }
    if (broker.state === "running" && broker.instanceId !== first.instanceId) {
      return {
        state: "untrusted",
        code: "background_broker_identity_mismatch",
        core: "running",
        broker: "untrusted",
      };
    }
    const state =
      broker.state === "running"
        ? "healthy"
        : broker.state === "untrusted"
          ? "untrusted"
          : "degraded";
    return {
      state,
      code:
        state === "healthy"
          ? "background_verified"
          : "background_broker_not_healthy",
      core: "running",
      broker: broker.state,
      ...(state === "healthy" ? { instanceId: first.instanceId } : {}),
    };
  } catch {
    return {
      state: "unknown",
      code: "background_inspection_unavailable",
      core: "unknown",
      broker: "not_checked",
    };
  }
}

async function probeIdentity(
  port: number,
  apiToken: string,
): Promise<RuntimeIdentity> {
  return (
    await axios.get<RuntimeIdentity>(
      `http://127.0.0.1:${port}/api/v1/runtime/identity`,
      {
        headers: { Authorization: `Bearer ${apiToken}` },
        timeout: 1000,
        proxy: false,
        maxRedirects: 0,
        maxContentLength: 16384,
      },
    )
  ).data;
}
