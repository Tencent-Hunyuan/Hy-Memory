import type { ChildProcess } from "node:child_process";
import { EventEmitter } from "node:events";
import { writeFileSync } from "node:fs";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { HealthResponse } from "@one-memory/contracts";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CoreSupervisor, readCoreStartupPhase } from "./core-supervisor.js";
import type { DiagnosticInput } from "./diagnostic-log.js";

const OWNED: HealthResponse = {
  status: "ok",
  service: "one-memory-core",
  version: "0.0.0",
  instance_id: "owned",
};

function fakeChild(): ChildProcess {
  const child = new EventEmitter() as ChildProcess;
  child.kill = vi.fn(() => true);
  return child;
}

/** A child that exits when it is told to stop, as a real process does. */
function killableChild(): ChildProcess {
  const child = new EventEmitter() as ChildProcess;
  child.kill = vi.fn((signal?: NodeJS.Signals | number) => {
    queueMicrotask(() =>
      child.emit("exit", null, (signal as NodeJS.Signals) ?? "SIGTERM"),
    );
    return true;
  });
  return child;
}

const directories: string[] = [];

async function dataDir(): Promise<string> {
  const directory = await mkdtemp(path.join(os.tmpdir(), "om-startup-phase-"));
  directories.push(directory);
  return directory;
}

/** What Core's `graph_open_guard` writes before the guarded graph open. */
async function declarePhase(
  directory: string,
  budgetMs: number,
  instanceId = "owned",
): Promise<void> {
  await writeFile(
    path.join(directory, "core-startup.json"),
    JSON.stringify({
      schema_version: 1,
      pid: null,
      instance_id: instanceId,
      phase: "graph_open",
      started_at: new Date().toISOString(),
      budget_ms: budgetMs,
    }),
  );
}

/** The same receipt, written before `spawn` returns, as Core's is before the
 *  probe starts: no race against the readiness budget. */
function declarePhaseNow(directory: string, budgetMs: number): void {
  writeFileSync(
    path.join(directory, "core-startup.json"),
    JSON.stringify({
      schema_version: 1,
      pid: null,
      instance_id: "owned",
      phase: "graph_open",
      started_at: new Date().toISOString(),
      budget_ms: budgetMs,
    }),
  );
}

async function clearPhase(directory: string): Promise<void> {
  await rm(path.join(directory, "core-startup.json"), { force: true });
}

async function until(check: () => boolean, ms = 3_000): Promise<void> {
  const deadline = Date.now() + ms;
  while (!check() && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
}

afterEach(async () => {
  await Promise.all(
    directories
      .splice(0)
      .map((directory) => rm(directory, { recursive: true, force: true })),
  );
});

describe("Core startup phase (the ultra graph's guarded first open)", () => {
  it("reads the declared phase and ignores a malformed receipt", async () => {
    const directory = await dataDir();
    expect(await readCoreStartupPhase(directory)).toBeNull();
    await declarePhase(directory, 180_000);
    expect(await readCoreStartupPhase(directory)).toMatchObject({
      phase: "graph_open",
      budgetMs: 180_000,
      instanceId: "owned",
    });
    await writeFile(path.join(directory, "core-startup.json"), "{not json");
    expect(await readCoreStartupPhase(directory)).toBeNull();
  });

  it("waits past the readiness budget while Core declares a slow startup phase", async () => {
    const directory = await dataDir();
    const logged: DiagnosticInput[] = [];
    let ready = false;
    const spawnProcess = vi.fn(() => {
      // The guarded probe starts right after spawn and outlasts the plain
      // readiness budget, then finishes and Core binds.
      declarePhaseNow(directory, 5_000);
      setTimeout(() => {
        void clearPhase(directory).then(() => {
          ready = true;
        });
      }, 250);
      return fakeChild();
    });
    const supervisor = new CoreSupervisor({
      launch: { command: "one-memory", args: [] },
      dataDir: directory,
      instanceId: "owned",
      port: 9701,
      shutdownTimeoutMs: 20,
      attempts: 5,
      delayMs: 10,
      readinessBudgetMs: 50,
      spawnProcess,
      probeHealth: async () => {
        if (!ready) throw new Error("not listening yet");
        return OWNED;
      },
      diagnostics: { log: async (event) => void logged.push(event) },
      livenessIntervalMs: 0,
    });

    await expect(supervisor.start()).resolves.toMatchObject({
      instance_id: "owned",
    });
    // Starting, not hung: one spawn, no kill, no relaunch.
    expect(spawnProcess).toHaveBeenCalledTimes(1);
    expect(logged.map((event) => event.event)).toContain(
      "core.startup_phase_waiting",
    );
    await supervisor.stop();
  });

  it("still times out a Core that declares no startup phase", async () => {
    const directory = await dataDir();
    const supervisor = new CoreSupervisor({
      launch: { command: "one-memory", args: [] },
      dataDir: directory,
      instanceId: "owned",
      port: 9702,
      shutdownTimeoutMs: 20,
      attempts: 5,
      delayMs: 10,
      readinessBudgetMs: 50,
      spawnProcess: () => fakeChild(),
      probeHealth: async () => {
        throw new Error("not listening");
      },
    });
    await expect(supervisor.start()).rejects.toThrow("50ms budget");
  });

  it("ignores a receipt left by an earlier Core", async () => {
    const directory = await dataDir();
    await writeFile(
      path.join(directory, "core-startup.json"),
      JSON.stringify({
        pid: null,
        instance_id: "owned",
        phase: "graph_open",
        started_at: new Date(Date.now() - 60_000).toISOString(),
        budget_ms: 180_000,
      }),
    );
    const supervisor = new CoreSupervisor({
      launch: { command: "one-memory", args: [] },
      dataDir: directory,
      instanceId: "owned",
      port: 9703,
      shutdownTimeoutMs: 20,
      attempts: 5,
      delayMs: 10,
      readinessBudgetMs: 50,
      spawnProcess: () => fakeChild(),
      probeHealth: async () => {
        throw new Error("not listening");
      },
    });
    await expect(supervisor.start()).rejects.toThrow("50ms budget");
  });

  it("stops a phase that outlives its budget and respawns, so Core can quarantine", async () => {
    const directory = await dataDir();
    const logged: DiagnosticInput[] = [];
    let spawns = 0;
    const spawnProcess = vi.fn(() => {
      spawns += 1;
      // The first two Cores hang inside the graph open; the third one (which
      // in Core quarantines the graph) comes straight up.
      if (spawns <= 2) declarePhaseNow(directory, 80);
      else void clearPhase(directory);
      return killableChild();
    });
    const supervisor = new CoreSupervisor({
      launch: { command: "one-memory", args: [] },
      dataDir: directory,
      instanceId: "owned",
      port: 9704,
      attempts: 1_000,
      delayMs: 10,
      readinessBudgetMs: 30,
      shutdownTimeoutMs: 50,
      restartBaseDelayMs: 1,
      restartMaxDelayMs: 5,
      spawnProcess,
      probeHealth: async () => {
        if (spawns <= 2) throw new Error("GIL held");
        return OWNED;
      },
      diagnostics: { log: async (event) => void logged.push(event) },
      livenessIntervalMs: 0,
    });

    await expect(supervisor.start()).resolves.toMatchObject({
      instance_id: "owned",
    });
    expect(spawnProcess).toHaveBeenCalledTimes(3);
    const events = logged.map((event) => event.event);
    expect(
      events.filter((event) => event === "core.startup_phase_retry"),
    ).toHaveLength(2);
    expect(logged).toContainEqual(
      expect.objectContaining({ error_code: "core_startup_phase_timeout" }),
    );
    await supervisor.stop();
  });

  it("respawns a Core killed inside its startup phase, a bounded number of times", async () => {
    const directory = await dataDir();
    const logged: DiagnosticInput[] = [];
    const spawnProcess = vi.fn(() => {
      const child = fakeChild();
      // Killed by the OS while opening the graph: the receipt stays behind.
      declarePhaseNow(directory, 180_000);
      setTimeout(() => child.emit("exit", null, "SIGKILL"), 20);
      return child;
    });
    const supervisor = new CoreSupervisor({
      launch: { command: "one-memory", args: [] },
      dataDir: directory,
      instanceId: "owned",
      port: 9705,
      shutdownTimeoutMs: 20,
      attempts: 1_000,
      delayMs: 5,
      readinessBudgetMs: 10_000,
      restartBaseDelayMs: 1,
      restartMaxDelayMs: 5,
      startupPhaseRetries: 2,
      spawnProcess,
      probeHealth: async () => {
        throw new Error("GIL held");
      },
      diagnostics: { log: async (event) => void logged.push(event) },
    });

    await expect(supervisor.start()).rejects.toThrow("SIGKILL");
    // One start and two retries: never an endless loop.
    expect(spawnProcess).toHaveBeenCalledTimes(3);
    expect(logged).toContainEqual(
      expect.objectContaining({
        error_code: "core_died_in_startup_phase",
        details: expect.objectContaining({ phase: "graph_open" }),
      }),
    );
  });
});

describe("Core after readiness", () => {
  it("restarts a Core that is alive but stopped answering after ready", async () => {
    const directory = await dataDir();
    const logged: DiagnosticInput[] = [];
    const spawnProcess = vi.fn(() => killableChild());
    let answering = true;
    const supervisor = new CoreSupervisor({
      launch: { command: "one-memory", args: [] },
      dataDir: directory,
      instanceId: "owned",
      port: 9706,
      attempts: 50,
      delayMs: 5,
      shutdownTimeoutMs: 20,
      restartBaseDelayMs: 1,
      restartMaxDelayMs: 5,
      livenessIntervalMs: 10,
      livenessTimeoutMs: 60,
      spawnProcess,
      probeHealth: async () => {
        if (!answering) throw new Error("GIL held");
        return OWNED;
      },
      diagnostics: { log: async (event) => void logged.push(event) },
    });

    await supervisor.start();
    answering = false;
    await until(() =>
      logged.some((event) => event.event === "core.unresponsive"),
    );
    expect(logged).toContainEqual(
      expect.objectContaining({
        event: "core.unresponsive",
        error_code: "core_unresponsive",
      }),
    );
    answering = true;
    await until(() => spawnProcess.mock.calls.length >= 2);
    expect(spawnProcess.mock.calls.length).toBeGreaterThanOrEqual(2);
    await supervisor.stop();
  });

  it("rides out a stall shorter than the liveness window", async () => {
    // The field's worst loop stall was 44 s against a 90 s window; scaled
    // down here: silent for ~half the window, then answering again.
    const directory = await dataDir();
    const logged: DiagnosticInput[] = [];
    const spawnProcess = vi.fn(() => killableChild());
    let answering = true;
    const supervisor = new CoreSupervisor({
      launch: { command: "one-memory", args: [] },
      dataDir: directory,
      instanceId: "owned",
      port: 9710,
      shutdownTimeoutMs: 20,
      attempts: 5,
      delayMs: 5,
      livenessIntervalMs: 10,
      livenessTimeoutMs: 200,
      spawnProcess,
      probeHealth: async () => {
        if (!answering) throw new Error("loop stalled");
        return OWNED;
      },
      diagnostics: { log: async (event) => void logged.push(event) },
    });
    await supervisor.start();
    for (let stall = 0; stall < 3; stall += 1) {
      answering = false;
      await new Promise((resolve) => setTimeout(resolve, 100));
      answering = true;
      await new Promise((resolve) => setTimeout(resolve, 40));
    }
    expect(spawnProcess).toHaveBeenCalledTimes(1);
    expect(logged.map((event) => event.event)).not.toContain(
      "core.unresponsive",
    );
    await supervisor.stop();
  });

  it("does not restart a ready Core that keeps answering", async () => {
    const directory = await dataDir();
    const spawnProcess = vi.fn(() => killableChild());
    const supervisor = new CoreSupervisor({
      launch: { command: "one-memory", args: [] },
      dataDir: directory,
      instanceId: "owned",
      port: 9707,
      shutdownTimeoutMs: 20,
      attempts: 5,
      delayMs: 5,
      livenessIntervalMs: 10,
      livenessTimeoutMs: 40,
      spawnProcess,
      probeHealth: async () => OWNED,
    });
    await supervisor.start();
    await new Promise((resolve) => setTimeout(resolve, 150));
    expect(spawnProcess).toHaveBeenCalledTimes(1);
    await supervisor.stop();
  });

  it("grows the backoff for a Core that comes up and dies again, then opens the circuit", async () => {
    // The field loop: every relaunch reached `core.ready` and died ~26 s
    // later, and readiness reset the streak, so every round waited the base
    // delay. A Core must now stay up `restartStableMs` to be forgiven.
    const directory = await dataDir();
    const delays: number[] = [];
    const events: string[] = [];
    const children: ChildProcess[] = [];
    const spawnProcess = vi.fn(() => {
      const child = fakeChild();
      children.push(child);
      return child;
    });
    const supervisor = new CoreSupervisor({
      launch: { command: "one-memory", args: [] },
      dataDir: directory,
      instanceId: "owned",
      port: 9708,
      shutdownTimeoutMs: 20,
      attempts: 5,
      delayMs: 1,
      restartAttempts: 4,
      restartBaseDelayMs: 2,
      restartMaxDelayMs: 1_000,
      restartStableMs: 60_000,
      livenessIntervalMs: 0,
      spawnProcess,
      probeHealth: async () => OWNED,
      diagnostics: {
        log: async (event) => {
          events.push(event.event);
          if (event.event === "core.restart_scheduled") {
            delays.push(Number(event.details?.delay_ms ?? 0));
          }
        },
      },
    });

    await supervisor.start();
    for (let crash = 0; crash < 6; crash += 1) {
      if (events.includes("core.restart_circuit_opened")) break;
      const spawned = spawnProcess.mock.calls.length;
      children.at(-1)?.emit("exit", null, "SIGKILL");
      await until(
        () =>
          spawnProcess.mock.calls.length > spawned ||
          events.includes("core.restart_circuit_opened"),
      );
    }
    expect(delays.length).toBeGreaterThanOrEqual(3);
    for (let index = 1; index < delays.length; index += 1) {
      expect(delays[index]).toBeGreaterThan(delays[index - 1] ?? 0);
    }
    expect(events).toContain("core.restart_circuit_opened");
    const spawnsAtCircuit = spawnProcess.mock.calls.length;
    children.at(-1)?.emit("exit", null, "SIGKILL");
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(spawnProcess.mock.calls.length).toBe(spawnsAtCircuit);
    expect(events).toContain("core.restart_skipped_circuit_open");
    await supervisor.stop();
  });

  it("forgives the streak once the Core stayed up long enough", async () => {
    const directory = await dataDir();
    const delays: number[] = [];
    const children: ChildProcess[] = [];
    const spawnProcess = vi.fn(() => {
      const child = fakeChild();
      children.push(child);
      return child;
    });
    const supervisor = new CoreSupervisor({
      launch: { command: "one-memory", args: [] },
      dataDir: directory,
      instanceId: "owned",
      port: 9709,
      shutdownTimeoutMs: 20,
      attempts: 5,
      delayMs: 1,
      restartBaseDelayMs: 2,
      restartMaxDelayMs: 1_000,
      restartStableMs: 30,
      livenessIntervalMs: 0,
      spawnProcess,
      probeHealth: async () => OWNED,
      diagnostics: {
        log: async (event) => {
          if (event.event === "core.restart_scheduled") {
            delays.push(Number(event.details?.delay_ms ?? 0));
          }
        },
      },
    });
    await supervisor.start();
    for (let crash = 0; crash < 3; crash += 1) {
      // Stable first: a crash after a long healthy run is not a loop.
      await new Promise((resolve) => setTimeout(resolve, 60));
      const spawned = spawnProcess.mock.calls.length;
      children.at(-1)?.emit("exit", 1, null);
      await until(() => spawnProcess.mock.calls.length > spawned);
    }
    expect(delays).toEqual([2, 2, 2]);
    await supervisor.stop();
  });
});
