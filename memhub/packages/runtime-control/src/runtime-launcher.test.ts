import type { ChildProcess } from "node:child_process";
import { EventEmitter } from "node:events";
import { rmSync } from "node:fs";
import { mkdir, mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";

import {
  ensureRuntimeProcess,
  stopRuntimeProcess,
} from "./runtime-launcher.js";

function fakeChild(): ChildProcess {
  const child = new EventEmitter() as ChildProcess;
  child.unref = vi.fn(() => child);
  return child;
}

async function writeService(dataDir: string) {
  await mkdir(dataDir, { recursive: true });
  await writeFile(
    path.join(dataDir, "runtime.json"),
    JSON.stringify({
      schema_version: 1,
      service: "one-memory-core",
      instance_id: "runtime",
      pid: 1234,
      parent_pid: 4321,
      host: "127.0.0.1",
      port: 8765,
      started_at: "now",
    }),
  );
  await writeFile(path.join(dataDir, "runtime-token"), "token", {
    mode: 0o600,
  });
  await writeFile(
    path.join(dataDir, "runtime-service.json"),
    JSON.stringify({
      schema_version: 1,
      service: "one-memory-runtime",
      status: "running",
      instance_id: "runtime",
      pid: 4321,
      core_port: 8765,
      started_at: "now",
    }),
    { mode: 0o600 },
  );
}

async function writeStaleService(dataDir: string) {
  await writeFile(
    path.join(dataDir, "runtime-service.json"),
    JSON.stringify({
      schema_version: 1,
      service: "one-memory-runtime",
      status: "running",
      instance_id: "previous",
      pid: 999,
      core_port: 7000,
      started_at: "before",
    }),
    { mode: 0o600 },
  );
}

function identity(instanceId: string) {
  return {
    status: "ok",
    service: "one-memory-core",
    version: "1.0.0",
    instance_id: instanceId,
  } as const;
}

describe("ensureRuntimeProcess", () => {
  it("does not spawn when a verified Runtime service already exists", async () => {
    const dataDir = await mkdtemp(path.join(tmpdir(), "runtime-launcher-"));
    await writeService(dataDir);
    const spawnProcess = vi.fn();

    await expect(
      ensureRuntimeProcess({
        dataDir,
        launch: { command: "/app/runtime", args: ["serve"] },
        spawnProcess,
        processIsAlive: () => true,
        probeIdentity: async () => ({
          status: "ok",
          service: "one-memory-core",
          version: "1.0.0",
          instance_id: "runtime",
        }),
      }),
    ).resolves.toBe("attached");
    expect(spawnProcess).not.toHaveBeenCalled();
  });

  it("spawns detached and waits for the Runtime receipt", async () => {
    const dataDir = await mkdtemp(path.join(tmpdir(), "runtime-launcher-"));
    const child = fakeChild();
    const spawnProcess = vi.fn(() => {
      void writeService(dataDir);
      return child;
    });

    await expect(
      ensureRuntimeProcess({
        dataDir,
        launch: {
          command: "/app/electron",
          args: ["/app/runtime/runtime.cjs", "serve"],
          env: { ELECTRON_RUN_AS_NODE: "1" },
        },
        // The receipt write is intentionally asynchronous. Keep enough wall
        // clock for it even when the workspace suite runs CPU-heavy tests in
        // parallel; ten 1ms polls made this a scheduler race.
        attempts: 200,
        delayMs: 5,
        spawnProcess,
        processIsAlive: () => true,
        probeIdentity: async () => ({
          status: "ok",
          service: "one-memory-core",
          version: "1.0.0",
          instance_id: "runtime",
        }),
      }),
    ).resolves.toBe("started");
    expect(spawnProcess).toHaveBeenCalledWith(
      "/app/electron",
      ["/app/runtime/runtime.cjs", "serve"],
      expect.objectContaining({ detached: true, stdio: "ignore" }),
    );
    expect(child.unref).toHaveBeenCalledOnce();
  });

  it("keeps waiting while its own Runtime replaces a stale service receipt", async () => {
    const dataDir = await mkdtemp(path.join(tmpdir(), "runtime-launcher-"));
    await writeService(dataDir);
    await writeStaleService(dataDir);
    let spawned = false;
    let polls = 0;

    await expect(
      ensureRuntimeProcess({
        dataDir,
        launch: { command: "/app/runtime", args: ["serve"] },
        attempts: 20,
        delayMs: 1,
        spawnProcess: vi.fn(() => {
          spawned = true;
          return fakeChild();
        }),
        processIsAlive: (pid) => {
          if (pid === 999) return false;
          if (spawned && pid === 1234 && ++polls === 4)
            void writeService(dataDir);
          return spawned;
        },
        probeIdentity: async () => identity("runtime"),
      }),
    ).resolves.toBe("started");
  });

  it("still fails when its own Runtime exits behind a stale service receipt", async () => {
    const dataDir = await mkdtemp(path.join(tmpdir(), "runtime-launcher-"));
    await writeService(dataDir);
    await writeStaleService(dataDir);
    const child = fakeChild();
    let spawned = false;

    await expect(
      ensureRuntimeProcess({
        dataDir,
        launch: { command: "/app/runtime", args: ["serve"] },
        attempts: 20,
        delayMs: 1,
        spawnProcess: vi.fn(() => {
          spawned = true;
          setTimeout(() => child.emit("exit", 1, null), 0);
          return child;
        }),
        processIsAlive: (pid) => pid !== 999 && spawned,
        probeIdentity: async () => identity("runtime"),
      }),
    ).rejects.toThrow("exited before becoming ready");
  });

  it("stops only a fully verified Runtime service pid", async () => {
    const dataDir = await mkdtemp(
      path.join(tmpdir(), "runtime-launcher-stop-"),
    );
    await writeService(dataDir);
    let alive = true;
    const signalProcess = vi.fn(() => {
      alive = false;
      rmSync(path.join(dataDir, "runtime-service.json"), { force: true });
      rmSync(path.join(dataDir, "runtime.json"), { force: true });
      rmSync(path.join(dataDir, "runtime-token"), { force: true });
    });

    await expect(
      stopRuntimeProcess({
        dataDir,
        timeoutMs: 100,
        processIsAlive: (pid) => pid === 1234 || (pid === 4321 && alive),
        signalProcess,
        probeIdentity: async () => ({
          status: "ok",
          service: "one-memory-core",
          version: "1.0.0",
          instance_id: "runtime",
        }),
      }),
    ).resolves.toBe("stopped");
    expect(signalProcess).toHaveBeenCalledExactlyOnceWith(4321, "SIGTERM");
  });

  it("reports a real stop even when a receipt file is left behind", async () => {
    const dataDir = await mkdtemp(
      path.join(tmpdir(), "runtime-launcher-stop-"),
    );
    await writeService(dataDir);
    let alive = true;
    // A Runtime killed hard can exit without deleting every receipt. Waiting
    // for all three files to disappear turned that real stop into a false
    // "timed out", which is what BUG-12b / OBS-27 reported.
    const signalProcess = vi.fn(() => {
      alive = false;
      rmSync(path.join(dataDir, "runtime-service.json"), { force: true });
      rmSync(path.join(dataDir, "runtime.json"), { force: true });
    });

    await expect(
      stopRuntimeProcess({
        dataDir,
        timeoutMs: 2_000,
        processIsAlive: (pid) => pid === 1234 || (pid === 4321 && alive),
        signalProcess,
        probeIdentity: async () => ({
          status: "ok",
          service: "one-memory-core",
          version: "1.0.0",
          instance_id: "runtime",
        }),
      }),
    ).resolves.toBe("stopped");
  });

  it("does not signal an unverified Runtime", async () => {
    const dataDir = await mkdtemp(
      path.join(tmpdir(), "runtime-launcher-stop-"),
    );
    await writeService(dataDir);
    const signalProcess = vi.fn();

    await expect(
      stopRuntimeProcess({
        dataDir,
        processIsAlive: () => true,
        signalProcess,
        probeIdentity: async () => ({
          status: "ok",
          service: "one-memory-core",
          version: "1.0.0",
          instance_id: "other",
        }),
      }),
    ).rejects.toThrow("runtime_identity_mismatch");
    expect(signalProcess).not.toHaveBeenCalled();
  });
});
