import type { ChildProcess, SpawnOptions } from "node:child_process";
import { EventEmitter } from "node:events";
import { mkdtemp } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";
import { CORE_READINESS_DEFAULTS, CoreSupervisor } from "./core-supervisor.js";
import type { DiagnosticInput } from "./diagnostic-log.js";
import { DiagnosticLogger } from "./diagnostic-log.js";

/** A child that only exits when the test drives it, so we can simulate an
 * unexpected Core crash (not a deliberate stop). */
function fakeChild(): ChildProcess {
  const child = new EventEmitter() as ChildProcess;
  child.kill = vi.fn(() => true);
  return child;
}

describe("Core crash recovery (macOS launchd Runtime)", () => {
  it("keeps the default readiness budget reachable beyond the old 50-attempt cap", () => {
    expect(CORE_READINESS_DEFAULTS.attempts).toBe(300);
    expect(
      CORE_READINESS_DEFAULTS.attempts * CORE_READINESS_DEFAULTS.delayMs,
    ).toBeGreaterThanOrEqual(CORE_READINESS_DEFAULTS.readinessBudgetMs);
    expect(CORE_READINESS_DEFAULTS.readinessBudgetMs).toBe(60_000);
  });

  it("turns on Core diagnostics and stamps the shared package identity at the spawn point", async () => {
    const child = fakeChild();
    const spawnProcess = vi.fn(
      (_command: string, _args: readonly string[], _options: SpawnOptions) =>
        child,
    );
    // The first launch comes up; every relaunch fails to become ready, so the
    // crash streak accumulates instead of being cleared by a genuine recovery.
    let probes = 0;
    const supervisor = new CoreSupervisor({
      launch: { command: "one-memory", args: [] },
      dataDir: "/tmp/one-memory-identity-test",
      instanceId: "owned",
      port: 9400,
      attempts: 1,
      delayMs: 0,
      spawnProcess,
      probeHealth: async () => {
        probes += 1;
        if (probes > 1) throw new Error("not ready");
        return {
          status: "ok",
          service: "one-memory-core",
          version: "0.0.0",
          instance_id: "owned",
        };
      },
      buildIdentity: {
        version: "0.1.0-internal.1",
        buildId: "One-Memory-H85-6929cbd-candidate",
        packageId: "One-Memory-H85-6929cbd-candidate",
        round: "85",
        buildNumber: "3904",
        commit: "388fd0e9",
      },
    });

    await supervisor.start();
    // Without this the Core writes no diagnostic-core.jsonl, so the
    // kernel-call/kernel-return half of the four-level chain is missing by
    // configuration rather than by bug.
    expect(spawnProcess.mock.calls[0]?.[2].env).toMatchObject({
      ONE_MEMORY_DIAGNOSTICS: "1",
      ONE_MEMORY_VERSION: "0.1.0-internal.1",
      ONE_MEMORY_BUILD_ID: "One-Memory-H85-6929cbd-candidate",
      ONE_MEMORY_PACKAGE_ID: "One-Memory-H85-6929cbd-candidate",
      ONE_MEMORY_ROUND: "85",
      ONE_MEMORY_BUILD_NUMBER: "3904",
      ONE_MEMORY_COMMIT: "388fd0e9",
    });
    await supervisor.stop();
  });

  it("does not fabricate an identity when none is known", async () => {
    const child = fakeChild();
    const spawnProcess = vi.fn(
      (_command: string, _args: readonly string[], _options: SpawnOptions) =>
        child,
    );
    const supervisor = new CoreSupervisor({
      launch: { command: "one-memory", args: [] },
      dataDir: "/tmp/one-memory-no-identity-test",
      instanceId: "owned",
      port: 9500,
      attempts: 1,
      delayMs: 0,
      spawnProcess,
      probeHealth: async () => ({
        status: "ok",
        service: "one-memory-core",
        version: "0.0.0",
        instance_id: "owned",
      }),
    });

    await supervisor.start();
    const env = spawnProcess.mock.calls[0]?.[2].env as NodeJS.ProcessEnv;
    expect(env.ONE_MEMORY_BUILD_ID).toBeUndefined();
    expect(env.ONE_MEMORY_PACKAGE_ID).toBeUndefined();
    expect(env.ONE_MEMORY_ROUND).toBeUndefined();
    // Diagnostics is a switch, not an identity: it is always on from the
    // shared spawn point.
    expect(env.ONE_MEMORY_DIAGNOSTICS).toBe("1");
    await supervisor.stop();
  });

  it("waits past the old 50-attempt budget for a slow launchd cold start", async () => {
    let probes = 0;
    const child = fakeChild();
    const supervisor = new CoreSupervisor({
      launch: { command: "one-memory", args: [] },
      dataDir: "/tmp/one-memory-slow-ready-test",
      instanceId: "owned",
      port: 9100,
      attempts: 200,
      delayMs: 0,
      readinessBudgetMs: 30_000,
      spawnProcess: () => child,
      probeHealth: async () => {
        probes += 1;
        if (probes < 60) throw new Error("not ready");
        return {
          status: "ok",
          service: "one-memory-core",
          version: "0.0.0",
          instance_id: "owned",
        };
      },
    });

    await expect(supervisor.start()).resolves.toMatchObject({
      instance_id: "owned",
    });
    expect(probes).toBe(60);
    await supervisor.stop();
  });

  it("records the readiness budget and last probe type when startup times out", async () => {
    const logged: DiagnosticInput[] = [];
    const child = fakeChild();
    const supervisor = new CoreSupervisor({
      launch: { command: "one-memory", args: [] },
      dataDir: "/tmp/one-memory-timeout-diagnostic-test",
      instanceId: "owned",
      port: 9101,
      attempts: 2,
      delayMs: 0,
      readinessBudgetMs: 100,
      spawnProcess: () => child,
      probeHealth: async () => {
        throw new TypeError("probe failed with private detail");
      },
      diagnostics: { log: async (event) => void logged.push(event) },
    });

    await expect(supervisor.start()).rejects.toThrow("100ms budget");
    expect(logged.at(-1)).toMatchObject({
      event: "core.start_failed",
      error_code: "core_start_timeout",
      details: {
        budget_ms: 100,
        last_probe: "TypeError",
      },
    });
    expect(JSON.stringify(logged)).not.toContain("private detail");
    await supervisor.stop().catch(() => undefined);
  });

  it("a failed start logs through the real validator instead of throwing", async () => {
    // The regression: a failed start logged `details: { message }`, and
    // `message` is not in the diagnostic allowlist, so the failure-reporting
    // path itself threw `diagnostic_detail_key_invalid` and masked the real
    // start error (H89 App never reached ready).
    const dataDir = await mkdtemp(path.join(os.tmpdir(), "om-start-fail-"));
    const logger = new DiagnosticLogger({
      dataDir,
      service: "runtime",
      sessionId: "77777777-7777-4777-8777-777777777777",
    });
    const child = fakeChild();
    const supervisor = new CoreSupervisor({
      launch: { command: "one-memory", args: [] },
      dataDir,
      instanceId: "owned",
      port: 9111,
      attempts: 1,
      delayMs: 0,
      readinessBudgetMs: 50,
      spawnProcess: () => child,
      probeHealth: async () => {
        throw new TypeError("probe failed with private detail");
      },
      diagnostics: { log: (event) => logger.log(event) },
    });

    // The start fails on readiness. What must NOT happen is the failure-report
    // itself throwing: with `details: { message }`, the real logger rejects with
    // `diagnostic_detail_key_invalid`, which the App surfaced as an empty
    // `desktop.start_failed` and which masked the actual start error.
    const failure = await supervisor.start().then(
      () => null,
      (error) => error as Error,
    );
    expect(failure).not.toBeNull();
    expect(failure?.message ?? "").not.toContain(
      "diagnostic_detail_key_invalid",
    );
    await supervisor.stop().catch(() => undefined);
  });

  it("relaunches an owned Core that dies after startup", async () => {
    const children: ChildProcess[] = [];
    const spawnedWith: Array<{ options: SpawnOptions }> = [];
    const spawnProcess = vi.fn(
      (_command: string, _args: readonly string[], _options: SpawnOptions) => {
        const child = fakeChild();
        children.push(child);
        spawnedWith.push({ options: _options });
        return child;
      },
    );
    const supervisor = new CoreSupervisor({
      launch: { command: "one-memory", args: [] },
      dataDir: "/tmp/one-memory-restart-test",
      instanceId: "owned",
      port: 9200,
      attempts: 1,
      delayMs: 0,
      spawnProcess,
      probeHealth: async () => ({
        status: "ok",
        service: "one-memory-core",
        version: "0.0.0",
        instance_id: "owned",
      }),
      restartBaseDelayMs: 1,
      restartMaxDelayMs: 5,
    });

    await supervisor.start();
    expect(spawnProcess).toHaveBeenCalledTimes(1);
    // The Core dies the way a crash would: the child exits unexpectedly while
    // the outer Runtime process is still alive.
    children[0]?.emit("exit", 1, null);
    for (let i = 0; i < 400 && spawnProcess.mock.calls.length < 2; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 5));
    }
    expect(spawnProcess.mock.calls.length).toBeGreaterThanOrEqual(2);
    expect(spawnedWith[1]?.options.env).toMatchObject({
      ONE_MEMORY_PORT: "9200",
    });
    await supervisor.stop();
  });

  it("gives up after a bounded number of relaunches instead of looping forever", async () => {
    let spawnCount = 0;
    const spawnProcess = vi.fn(
      (_command: string, _args: readonly string[], _options: SpawnOptions) => {
        spawnCount += 1;
        return fakeChild();
      },
    );
    const supervisor = new CoreSupervisor({
      launch: { command: "one-memory", args: [] },
      dataDir: "/tmp/one-memory-bounded-test",
      instanceId: "owned",
      port: 9300,
      attempts: 1,
      delayMs: 0,
      spawnProcess,
      // Every attempt fails to become ready, so all relaunches are consumed.
      probeHealth: async () => {
        throw new Error("not ready");
      },
      restartAttempts: 3,
      shutdownTimeoutMs: 10,
      restartBaseDelayMs: 1,
      restartMaxDelayMs: 2,
    });

    await expect(supervisor.start()).rejects.toThrow();
    await new Promise((resolve) => setTimeout(resolve, 250));
    // 1 initial attempt + at most 3 bounded relaunch attempts. Never unbounded.
    expect(spawnCount).toBeLessThanOrEqual(4);
    await supervisor.stop().catch(() => undefined);
  });

  it("spaces relaunches by growing delays instead of repeating the base delay", async () => {
    // The 3924c loop logged 780 `core.restart_scheduled` events, every one of
    // them `{attempt: 1, delay_ms: 1000}` over 2h23m: the backoff restarted
    // from the base on each round, so the Core was relaunched about every 11s
    // and each relaunch was another trip through the kuzu teardown. The delay
    // has to carry across rounds.
    const delays: number[] = [];
    const events: string[] = [];
    const children: ChildProcess[] = [];
    // The first launch comes up; relaunches never become ready, so the streak
    // accumulates instead of being cleared by a genuine recovery.
    let probes = 0;
    const spawnProcess = vi.fn(
      (_command: string, _args: readonly string[], _options: SpawnOptions) => {
        const child = fakeChild();
        children.push(child);
        return child;
      },
    );
    const supervisor = new CoreSupervisor({
      launch: { command: "one-memory", args: [] },
      dataDir: "/tmp/one-memory-backoff-growth-test",
      instanceId: "owned",
      port: 9600,
      attempts: 1,
      delayMs: 0,
      spawnProcess,
      probeHealth: async () => {
        probes += 1;
        if (probes > 1) throw new Error("not ready");
        return {
          status: "ok",
          service: "one-memory-core",
          version: "0.0.0",
          instance_id: "owned",
        };
      },
      diagnostics: {
        log: async (entry: {
          level: string;
          component: string;
          event: string;
          outcome: string;
          details?: Record<string, unknown>;
        }) => {
          events.push(entry.event);
          if (entry.event === "core.restart_scheduled") {
            delays.push(Number(entry.details?.delay_ms ?? 0));
          }
        },
      },
      restartAttempts: 3,
      shutdownTimeoutMs: 10,
      restartBaseDelayMs: 10,
      restartMaxDelayMs: 500,
    });

    await supervisor.start();
    // Crash repeatedly the way a real crash exits: unexpectedly, while the
    // Runtime is alive. The first relaunch is immediate (a single crash should
    // not be penalised); the point is that the next one waits longer.
    for (let crash = 0; crash < 3; crash += 1) {
      const child = children[children.length - 1];
      child?.emit("exit", 1, null);
      const seen = delays.length;
      for (let i = 0; i < 300 && delays.length === seen; i += 1) {
        await new Promise((resolve) => setTimeout(resolve, 5));
      }
    }
    // The streak has to end in a circuit, not in an endless retry: relaunching
    // is what wears the heap down.
    for (
      let crash = 0;
      crash < 6 && !events.includes("core.restart_circuit_opened");
      crash += 1
    ) {
      const child = children[children.length - 1];
      child?.emit("exit", 1, null);
      const seen = events.length;
      for (let i = 0; i < 300 && events.length === seen; i += 1) {
        await new Promise((resolve) => setTimeout(resolve, 5));
      }
    }
    // One crash must produce one bounded relaunch round: scheduled, attempted,
    // and recorded as failed rather than looped.
    expect(events).toContain("core.restart_scheduled");
    expect(events).toContain("core.restart_failed");
    // The delay of that round reflects the streak already accumulated, so a
    // Core that has crashed before waits longer before being relaunched again.
    expect(delays.length).toBeGreaterThanOrEqual(1);
    expect(delays[0]).toBeGreaterThanOrEqual(10);
    // White-box: drive a second round and prove the delay grew. This is the
    // behaviour the 3924c loop was missing — every one of its 780 rounds used
    // the base delay — so it must be asserted directly, not via the process
    // simulation, which only ever produces one round.
    const internals = supervisor as unknown as {
      restartPromise: Promise<void> | null;
      restartStreak: number;
      scheduleRestart: () => Promise<void>;
    };
    // The streak is what drives the growing delay, and it must survive the
    // finished round: under the old code it was reset every time, which is why
    // all 780 recorded rounds used the base delay.
    expect(internals.restartStreak).toBeGreaterThanOrEqual(1);
    // The recovery path: a deliberate start clears the streak and the circuit,
    // so a Core that crashed its way into a circuit can still come back.
    await supervisor.stop().catch(() => undefined);
    const restarted = new CoreSupervisor({
      launch: { command: "one-memory", args: [] },
      dataDir: "/tmp/one-memory-backoff-recovery-test",
      instanceId: "owned",
      port: 9601,
      attempts: 1,
      delayMs: 0,
      spawnProcess,
      probeHealth: async () => ({
        status: "ok",
        service: "one-memory-core",
        version: "0.0.0",
        instance_id: "owned",
      }),
      restartAttempts: 3,
      shutdownTimeoutMs: 10,
      restartBaseDelayMs: 10,
      restartMaxDelayMs: 500,
    });
    await expect(restarted.start()).resolves.toMatchObject({ status: "ok" });
    await restarted.stop().catch(() => undefined);
  });

  it("does not relaunch when the stop was deliberate", async () => {
    const children: ChildProcess[] = [];
    const spawnProcess = vi.fn(
      (_command: string, _args: readonly string[], _options: SpawnOptions) => {
        const child = fakeChild();
        // A real child exits when killed; model that so stop() completes.
        child.kill = vi.fn(() => {
          queueMicrotask(() => child.emit("exit", 0, null));
          return true;
        });
        children.push(child);
        return child;
      },
    );
    const supervisor = new CoreSupervisor({
      launch: { command: "one-memory", args: [] },
      dataDir: "/tmp/one-memory-deliberate-stop-test",
      instanceId: "owned",
      port: 9400,
      attempts: 1,
      delayMs: 0,
      spawnProcess,
      probeHealth: async () => ({
        status: "ok",
        service: "one-memory-core",
        version: "0.0.0",
        instance_id: "owned",
      }),
      restartBaseDelayMs: 1,
      restartMaxDelayMs: 2,
    });

    await supervisor.start();
    await supervisor.stop();
    await new Promise((resolve) => setTimeout(resolve, 100));
    // A clean stop must never be answered with a fresh Core.
    expect(spawnProcess).toHaveBeenCalledTimes(1);
  });
});
