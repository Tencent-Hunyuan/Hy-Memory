import type { ChildProcess, SpawnOptions } from "node:child_process";
import { EventEmitter } from "node:events";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterAll, describe, expect, it, vi } from "vitest";

import { CoreSupervisor, PORT_IN_USE_EXIT } from "./core-supervisor.js";

const roots: string[] = [];

afterAll(async () => {
  const fs = await import("node:fs");
  await Promise.all(
    roots.map((root) => fs.promises.rm(root, { recursive: true, force: true })),
  );
});

interface RecordedEvent {
  event: string;
  error_code?: string;
}

async function home(): Promise<string> {
  const root = await mkdtemp(path.join(tmpdir(), "om-port-refusal-"));
  roots.push(root);
  return path.join(root, "data");
}

/** A Core child that exits with `code` after printing `stderrText`. */
function refusingCore(code: number, stderrText: string) {
  const child = new EventEmitter() as ChildProcess;
  child.kill = vi.fn(() => true);
  const stderr = new EventEmitter();
  (child as unknown as Record<string, unknown>).stderr = stderr;
  const spawnProcess = vi.fn(
    (_command: string, _args: readonly string[], _options: SpawnOptions) => {
      setImmediate(() => {
        stderr.emit("data", Buffer.from(stderrText, "utf8"));
        child.emit("exit", code, null);
      });
      return child;
    },
  );
  return { child, spawnProcess };
}

async function startSupervisor(
  dataDir: string,
  spawnProcess: ReturnType<typeof refusingCore>["spawnProcess"],
) {
  const events: RecordedEvent[] = [];
  const diagnostics = {
    log: vi.fn(async (event: RecordedEvent) => {
      events.push(event);
    }),
  };
  const supervisor = new CoreSupervisor({
    launch: { command: "one-memory", args: ["serve"] },
    dataDir,
    instanceId: "owned",
    port: 9401,
    attempts: 5,
    delayMs: 5,
    spawnProcess,
    probeHealth: async () => {
      throw new Error("not ready");
    },
    diagnostics,
  });
  return { supervisor, events };
}

describe("Core refusing a bind (exit 75)", () => {
  it("reports the Core's own code, not a crash (#377)", async () => {
    const dataDir = await home();
    const { spawnProcess } = refusingCore(
      PORT_IN_USE_EXIT,
      "INFO: some log lines\ncore_port_in_use\n",
    );
    const { supervisor, events } = await startSupervisor(dataDir, spawnProcess);
    await expect(supervisor.start()).rejects.toThrow("core_port_in_use");
    expect(events).toContainEqual(
      expect.objectContaining({
        event: "core.start_failed",
        error_code: "core_port_in_use",
      }),
    );
  });

  it("prefers the more specific by-runtime code from the Core's stderr", async () => {
    const dataDir = await home();
    const { spawnProcess } = refusingCore(
      PORT_IN_USE_EXIT,
      "core_port_in_use_by_runtime\n",
    );
    const { supervisor, events } = await startSupervisor(dataDir, spawnProcess);
    await expect(supervisor.start()).rejects.toThrow(
      "core_port_in_use_by_runtime",
    );
    expect(events).toContainEqual(
      expect.objectContaining({
        event: "core.start_failed",
        error_code: "core_port_in_use_by_runtime",
      }),
    );
  });

  it("falls back to the generic code when the Core printed none", async () => {
    const dataDir = await home();
    const { spawnProcess } = refusingCore(PORT_IN_USE_EXIT, "");
    const { supervisor } = await startSupervisor(dataDir, spawnProcess);
    await expect(supervisor.start()).rejects.toThrow("core_port_in_use");
  });

  it("does not swallow other exit statuses", async () => {
    const dataDir = await home();
    const { spawnProcess } = refusingCore(1, "boom\n");
    const { supervisor, events } = await startSupervisor(dataDir, spawnProcess);
    await expect(supervisor.start()).rejects.toThrow(
      "exited before becoming ready",
    );
    expect(events).toContainEqual(
      expect.objectContaining({
        event: "core.start_failed",
        error_code: "core_exited_before_ready",
      }),
    );
  });
});
