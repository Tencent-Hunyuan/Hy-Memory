import type {
  RuntimeServiceState,
  StopAllRuntimesResult,
} from "@one-memory/runtime-control";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { shutdownRuntime } from "./runtime-shutdown";

const { stopAllRuntimeProcesses } = vi.hoisted(() => ({
  stopAllRuntimeProcesses: vi.fn<
    (options: { dataDir: string }) => Promise<StopAllRuntimesResult>
  >(async () => ({ stopped: [4321], unverified: [] })),
}));

vi.mock("@one-memory/runtime-control", () => ({ stopAllRuntimeProcesses }));

const stopMock = vi.mocked(stopAllRuntimeProcesses);

const enabled: RuntimeServiceState = {
  state: "enabled",
  reason: "service-enabled",
  jobPid: 4321,
};
const disabled = (
  reason: RuntimeServiceState["reason"] = "service-enable-marker-absent",
): RuntimeServiceState => ({ state: "disabled", reason, jobPid: null });
const unknown: RuntimeServiceState = {
  state: "unknown",
  reason: "service-state-timeout",
  jobPid: null,
};

beforeEach(() => {
  stopMock.mockReset();
  stopMock.mockResolvedValue({ stopped: [4321], unverified: [] });
});

describe("App quit with the background service enabled", () => {
  it("never stops the Runtime", async () => {
    const stopSupervisor = vi.fn(async () => {});
    const events: string[] = [];

    const result = await shutdownRuntime({
      dataDir: "/tmp/runtime-data",
      standalone: true,
      queryServiceState: async () => enabled,
      stopSupervisor,
      onDiagnostic: (event) => {
        events.push(event.detail);
      },
    });

    expect(result).toEqual({
      runtime: "preserved",
      reason: "background-service-enabled",
    });
    expect(stopSupervisor).toHaveBeenCalledOnce();
    expect(stopMock).not.toHaveBeenCalled();
    expect(events).toEqual(["runtime-preserved:background-service-enabled"]);
  });

  it("does not stop a transient Runtime this App started before the service was enabled", async () => {
    // Startup fell back to a transient child (service launchctl-disabled) and
    // the user re-enabled the service later. Nothing startup saw is an input
    // here: quit asks the service manager again and preserves the Runtime.
    const queryServiceState = vi.fn(async () => enabled);

    const result = await shutdownRuntime({
      dataDir: "/tmp/runtime-data",
      standalone: true,
      queryServiceState,
      stopSupervisor: async () => {},
    });

    expect(queryServiceState).toHaveBeenCalledOnce();
    expect(result.runtime).toBe("preserved");
    expect(stopMock).not.toHaveBeenCalled();
  });
});

describe("App quit with the background service disabled", () => {
  it("stops every Runtime of the data dir: the App's child and an external one", async () => {
    stopMock.mockResolvedValue({ stopped: [1111, 2222], unverified: [] });

    const result = await shutdownRuntime({
      dataDir: "/tmp/runtime-data",
      standalone: true,
      queryServiceState: async () => disabled(),
      stopSupervisor: async () => {},
    });

    expect(result).toEqual({ runtime: "stopped" });
    expect(stopMock).toHaveBeenCalledWith({ dataDir: "/tmp/runtime-data" });
  });

  it.each([
    "service-not-installed",
    "service-disabled-by-launchctl",
    "service-enable-marker-absent",
  ] as const)("treats %s as disabled", async (reason) => {
    const result = await shutdownRuntime({
      dataDir: "/tmp/runtime-data",
      standalone: true,
      queryServiceState: async () => disabled(reason),
    });

    expect(result).toEqual({ runtime: "stopped" });
    expect(stopMock).toHaveBeenCalledOnce();
  });

  it("reports a live lease owner it could not verify instead of signalling it", async () => {
    stopMock.mockResolvedValue({ stopped: [], unverified: [9999] });
    const events: string[] = [];

    const result = await shutdownRuntime({
      dataDir: "/tmp/runtime-data",
      standalone: true,
      queryServiceState: async () => disabled(),
      onDiagnostic: (event) => {
        events.push(`${event.outcome}:${event.detail}`);
      },
    });

    expect(result).toEqual({
      runtime: "failed",
      detail: "unverified-runtime-left-running",
    });
    expect(events).toEqual(["degraded:unverified-runtime-left-running"]);
  });

  it("tears the supervisor down before it stops the process", async () => {
    const order: string[] = [];
    stopMock.mockImplementation(async () => {
      order.push("process");
      return { stopped: [4321], unverified: [] };
    });

    await shutdownRuntime({
      dataDir: "/tmp/runtime-data",
      standalone: true,
      queryServiceState: async () => {
        order.push("query");
        return disabled();
      },
      stopSupervisor: async () => {
        order.push("supervisor");
      },
    });

    expect(order).toEqual(["supervisor", "query", "process"]);
  });

  it("reports not-running when nothing serves the data dir", async () => {
    stopMock.mockResolvedValue({ stopped: [], unverified: [] });

    const result = await shutdownRuntime({
      dataDir: "/tmp/runtime-data",
      standalone: true,
      queryServiceState: async () => disabled(),
    });

    expect(result).toEqual({ runtime: "not-running" });
  });

  it("reports a failure to stop the Runtime without throwing", async () => {
    stopMock.mockRejectedValue(new Error("Timed out waiting"));
    const events: string[] = [];

    const result = await shutdownRuntime({
      dataDir: "/tmp/runtime-data",
      standalone: true,
      queryServiceState: async () => disabled(),
      onDiagnostic: async (event) => {
        events.push(event.outcome);
      },
    });

    expect(result).toEqual({ runtime: "failed", detail: "Timed out waiting" });
    expect(events).toEqual(["degraded"]);
  });

  it("still stops the Runtime when supervisor teardown fails", async () => {
    const result = await shutdownRuntime({
      dataDir: "/tmp/runtime-data",
      standalone: true,
      queryServiceState: async () => disabled(),
      stopSupervisor: async () => {
        throw new Error("supervisor exploded");
      },
    });

    expect(stopMock).toHaveBeenCalledOnce();
    expect(result).toEqual({
      runtime: "failed",
      detail: "supervisor exploded",
    });
  });
});

describe("App quit when the service state cannot be determined", () => {
  it("stops nothing on a timeout and says so", async () => {
    const events: string[] = [];

    const result = await shutdownRuntime({
      dataDir: "/tmp/runtime-data",
      standalone: true,
      queryServiceState: async () => unknown,
      onDiagnostic: (event) => {
        events.push(`${event.outcome}:${event.detail}`);
      },
    });

    expect(result).toEqual({
      runtime: "preserved",
      reason: "service-state-unknown:service-state-timeout",
    });
    expect(stopMock).not.toHaveBeenCalled();
    expect(events).toEqual([
      "degraded:runtime-preserved:service-state-unknown:service-state-timeout",
    ]);
  });

  it("stops nothing when the query itself throws", async () => {
    const result = await shutdownRuntime({
      dataDir: "/tmp/runtime-data",
      standalone: true,
      queryServiceState: async () => {
        throw new Error("launchctl exploded");
      },
    });

    expect(result).toEqual({
      runtime: "preserved",
      reason: "service-state-unknown:service-state-query-failed",
    });
    expect(stopMock).not.toHaveBeenCalled();
  });
});

describe("Runtimes quit never touches", () => {
  it("leaves a Runtime whose build could not be read, even with the service disabled", async () => {
    const queryServiceState = vi.fn(async () => disabled());

    const result = await shutdownRuntime({
      dataDir: "/tmp/runtime-data",
      standalone: true,
      queryServiceState,
      preserveReason: "runtime-build-unknown",
    });

    expect(result).toEqual({
      runtime: "preserved",
      reason: "runtime-build-unknown",
    });
    expect(stopMock).not.toHaveBeenCalled();
  });

  it("does not touch a separate Runtime process in an embedded build", async () => {
    const stopSupervisor = vi.fn(async () => {});
    const queryServiceState = vi.fn(async () => disabled());

    const result = await shutdownRuntime({
      dataDir: "/tmp/runtime-data",
      standalone: false,
      queryServiceState,
      stopSupervisor,
    });

    expect(result).toEqual({ runtime: "unchanged" });
    expect(stopMock).not.toHaveBeenCalled();
    expect(queryServiceState).not.toHaveBeenCalled();
    expect(stopSupervisor).toHaveBeenCalledOnce();
  });

  it("reports a supervisor failure in an embedded build too", async () => {
    const result = await shutdownRuntime({
      dataDir: "/tmp/runtime-data",
      standalone: false,
      queryServiceState: async () => disabled(),
      stopSupervisor: async () => {
        throw new Error("supervisor exploded");
      },
    });

    expect(result).toEqual({
      runtime: "failed",
      detail: "supervisor exploded",
    });
  });
});
