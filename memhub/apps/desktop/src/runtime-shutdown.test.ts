import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { readPublishedRuntimeLifetime } from "../../../packages/runtime-control/src/discovery.js";

import { shutdownRuntime } from "./runtime-shutdown";

const { stopRuntimeProcess } = vi.hoisted(() => ({
  stopRuntimeProcess: vi.fn<() => Promise<"stopped" | "not-running">>(
    async () => "stopped",
  ),
}));

vi.mock("@one-memory/runtime-control", () => ({ stopRuntimeProcess }));

const stopMock = vi.mocked(stopRuntimeProcess);

beforeEach(() => {
  stopMock.mockClear();
  stopMock.mockResolvedValue("stopped");
});

describe("BUG-12: a full quit stops the standalone Runtime process", () => {
  it("detaches the UI without stopping a managed Runtime service", async () => {
    const stopSupervisor = vi.fn(async () => {});
    const events: string[] = [];

    const result = await shutdownRuntime({
      dataDir: "/tmp/runtime-data",
      standalone: true,
      preserveManagedRuntime: true,
      stopSupervisor,
      onDiagnostic: (event) => {
        events.push(event.detail);
      },
    });

    expect(result).toEqual({ runtime: "unchanged" });
    expect(stopSupervisor).toHaveBeenCalledOnce();
    expect(stopMock).not.toHaveBeenCalled();
    expect(events).toEqual(["managed-runtime-preserved"]);
  });

  it("stops the Runtime process in a packaged build", async () => {
    const stopSupervisor = vi.fn(async () => {});

    const result = await shutdownRuntime({
      dataDir: "/tmp/runtime-data",
      standalone: true,
      stopSupervisor,
    });

    expect(result).toEqual({ runtime: "stopped" });
    expect(stopMock).toHaveBeenCalledWith({ dataDir: "/tmp/runtime-data" });
    expect(stopSupervisor).toHaveBeenCalledOnce();
  });

  it("does not touch a separate Runtime process in an embedded build", async () => {
    const stopSupervisor = vi.fn(async () => {});

    const result = await shutdownRuntime({
      dataDir: "/tmp/runtime-data",
      standalone: false,
      stopSupervisor,
    });

    expect(result).toEqual({ runtime: "unchanged" });
    expect(stopMock).not.toHaveBeenCalled();
    expect(stopSupervisor).toHaveBeenCalledOnce();
  });

  it("tears the supervisor down before it stops the process", async () => {
    const order: string[] = [];
    stopMock.mockImplementation(async () => {
      order.push("process");
      return "stopped";
    });

    await shutdownRuntime({
      dataDir: "/tmp/runtime-data",
      standalone: true,
      stopSupervisor: async () => {
        order.push("supervisor");
      },
    });

    expect(order).toEqual(["supervisor", "process"]);
  });

  it("reports not-running instead of throwing when nothing is attached", async () => {
    stopMock.mockResolvedValue("not-running");

    const result = await shutdownRuntime({
      dataDir: "/tmp/runtime-data",
      standalone: true,
      stopSupervisor: async () => {},
    });

    expect(result).toEqual({ runtime: "not-running" });
  });

  it("reports a failure to stop the Runtime without throwing", async () => {
    stopMock.mockRejectedValue(new Error("Timed out waiting"));
    const events: string[] = [];

    const result = await shutdownRuntime({
      dataDir: "/tmp/runtime-data",
      standalone: true,
      stopSupervisor: async () => {},
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
      stopSupervisor: async () => {
        throw new Error("supervisor exploded");
      },
    });

    // The process is what must not be left behind; a supervisor error is not
    // an excuse to skip it.
    expect(stopMock).toHaveBeenCalledOnce();
    expect(result).toEqual({
      runtime: "failed",
      detail: "supervisor exploded",
    });
  });

  it("reports a supervisor failure in an embedded build too", async () => {
    const result = await shutdownRuntime({
      dataDir: "/tmp/runtime-data",
      standalone: false,
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

it.each(["transient", "managed", undefined] as const)(
  "a reattached %s runtime follows its persisted lifetime on quit",
  async (lifetime) => {
    const dataDir = await mkdtemp(path.join(tmpdir(), "reattached-runtime-"));
    try {
      await writeFile(
        path.join(dataDir, "runtime-service.json"),
        JSON.stringify({
          schema_version: 1,
          service: "one-memory-runtime",
          status: "running",
          instance_id: "prior-launch",
          pid: 4321,
          core_port: 8765,
          ...(lifetime === undefined ? {} : { lifetime }),
        }),
      );
      const transient =
        (await readPublishedRuntimeLifetime(dataDir)) === "transient";
      const result = await shutdownRuntime({
        dataDir,
        standalone: true,
        preserveManagedRuntime: !transient,
      });
      if (lifetime === "transient") {
        expect(result).toEqual({ runtime: "stopped" });
        expect(stopMock).toHaveBeenCalledWith({ dataDir });
      } else {
        expect(result).toEqual({ runtime: "unchanged" });
        expect(stopMock).not.toHaveBeenCalled();
      }
    } finally {
      await rm(dataDir, { recursive: true, force: true });
    }
  },
);
