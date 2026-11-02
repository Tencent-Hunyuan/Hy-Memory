import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";

import {
  stopAllRuntimeProcesses,
  waitForRuntimeProcess,
} from "./runtime-launcher.js";

async function writeRuntime(
  dataDir: string,
  runtime: {
    instance: string;
    servicePid: number;
    corePid: number;
    build?: Record<string, unknown>;
  },
) {
  await mkdir(dataDir, { recursive: true });
  await writeFile(
    path.join(dataDir, "runtime.json"),
    JSON.stringify({
      schema_version: 1,
      service: "one-memory-core",
      instance_id: runtime.instance,
      pid: runtime.corePid,
      parent_pid: runtime.servicePid,
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
      instance_id: runtime.instance,
      pid: runtime.servicePid,
      core_port: 8765,
      ...runtime.build,
      started_at: "now",
    }),
    { mode: 0o600 },
  );
}

async function writeLease(dataDir: string, pid: number) {
  await mkdir(path.join(dataDir, "runtime-service.lock"), { recursive: true });
  await writeFile(
    path.join(dataDir, "runtime-service.lock", "owner.json"),
    JSON.stringify({ pid, created_at: "now", boot_id: null }),
  );
}

async function removeRuntime(dataDir: string) {
  for (const name of ["runtime.json", "runtime-service.json", "runtime-token"])
    await rm(path.join(dataDir, name), { force: true });
  await rm(path.join(dataDir, "runtime-service.lock"), {
    recursive: true,
    force: true,
  });
}

const identityFor = (live: Map<number, string>) =>
  async function probeIdentity() {
    // The identity endpoint answers for whichever Runtime holds the receipts.
    const instance = [...live.values()].at(-1) ?? "none";
    return {
      status: "ok" as const,
      service: "one-memory-core" as const,
      version: "1.0.0",
      instance_id: instance,
    };
  };

describe("stopping every Runtime of a data dir", () => {
  it("stops the App's own child and an externally started successor", async () => {
    const dataDir = await mkdtemp(path.join(tmpdir(), "runtime-stop-all-"));
    // pid -> instance; core pids are always alive while their service is.
    const live = new Map<number, string>([[4321, "app-child"]]);
    await writeRuntime(dataDir, {
      instance: "app-child",
      servicePid: 4321,
      corePid: 1234,
    });
    await writeLease(dataDir, 4321);
    const signalProcess = vi.fn(async (pid: number) => {
      live.delete(pid);
      await removeRuntime(dataDir);
      if (pid === 4321) {
        // `memhub start` was waiting on the lease and takes over at once.
        live.set(5555, "cli-started");
        await writeRuntime(dataDir, {
          instance: "cli-started",
          servicePid: 5555,
          corePid: 5556,
        });
        await writeLease(dataDir, 5555);
      }
    });

    const result = await stopAllRuntimeProcesses({
      dataDir,
      timeoutMs: 2_000,
      processIsAlive: (pid) =>
        live.has(pid) ||
        (pid === 1234 && live.has(4321)) ||
        (pid === 5556 && live.has(5555)),
      signalProcess: (pid) => void signalProcess(pid),
      probeIdentity: identityFor(live),
    });

    expect(result).toEqual({ stopped: [4321, 5555], unverified: [] });
    expect(signalProcess.mock.calls.map(([pid]) => pid)).toEqual([4321, 5555]);
  });

  it("never signals a live lease owner it cannot verify as a MemHub Runtime", async () => {
    const dataDir = await mkdtemp(path.join(tmpdir(), "runtime-stop-all-"));
    // A lease left by a crashed Runtime whose pid the OS gave to another
    // program: no receipt, no identity endpoint, just a live number.
    await writeLease(dataDir, 7777);
    const signalProcess = vi.fn();

    const result = await stopAllRuntimeProcesses({
      dataDir,
      processIsAlive: (pid) => pid === 7777,
      signalProcess,
      probeIdentity: async () => {
        throw new Error("no endpoint");
      },
    });

    expect(result).toEqual({ stopped: [], unverified: [7777] });
    expect(signalProcess).not.toHaveBeenCalled();
  });

  it("refuses to signal a receipt pid whose identity does not match", async () => {
    const dataDir = await mkdtemp(path.join(tmpdir(), "runtime-stop-all-"));
    await writeRuntime(dataDir, {
      instance: "runtime",
      servicePid: 4321,
      corePid: 1234,
    });
    const signalProcess = vi.fn();

    await expect(
      stopAllRuntimeProcesses({
        dataDir,
        processIsAlive: () => true,
        signalProcess,
        probeIdentity: async () => ({
          status: "ok",
          service: "one-memory-core",
          version: "1.0.0",
          instance_id: "someone-else",
        }),
      }),
    ).rejects.toThrow("runtime_identity_mismatch");
    expect(signalProcess).not.toHaveBeenCalled();
  });

  it("reports nothing to stop for an empty data dir", async () => {
    const dataDir = await mkdtemp(path.join(tmpdir(), "runtime-stop-all-"));
    await expect(
      stopAllRuntimeProcesses({
        dataDir,
        processIsAlive: () => false,
        signalProcess: vi.fn(),
      }),
    ).resolves.toEqual({ stopped: [], unverified: [] });
  });
});

describe("the build a running Runtime publishes", () => {
  const probeIdentity = async () => ({
    status: "ok" as const,
    service: "one-memory-core" as const,
    version: "1.0.0",
    instance_id: "runtime",
  });

  it("is read from its receipt", async () => {
    const dataDir = await mkdtemp(path.join(tmpdir(), "runtime-build-"));
    await writeRuntime(dataDir, {
      instance: "runtime",
      servicePid: 4321,
      corePid: 1234,
      build: { build_number: 38, git_commit: "abcdef1" },
    });

    const attachment = await waitForRuntimeProcess({
      dataDir,
      attempts: 1,
      processIsAlive: () => true,
      probeIdentity,
    });

    expect(attachment.servicePid).toBe(4321);
    expect(attachment.runtimeBuild).toEqual({
      buildNumber: 38,
      commit: "abcdef1",
    });
  });

  it("is absent for a receipt from a Runtime older than the field", async () => {
    const dataDir = await mkdtemp(path.join(tmpdir(), "runtime-build-"));
    await writeRuntime(dataDir, {
      instance: "runtime",
      servicePid: 4321,
      corePid: 1234,
    });

    const attachment = await waitForRuntimeProcess({
      dataDir,
      attempts: 1,
      processIsAlive: () => true,
      probeIdentity,
    });

    expect(attachment.runtimeBuild).toBeUndefined();
  });

  it("reads a malformed build as unknown, not as a broken receipt", async () => {
    const dataDir = await mkdtemp(path.join(tmpdir(), "runtime-build-"));
    await writeRuntime(dataDir, {
      instance: "runtime",
      servicePid: 4321,
      corePid: 1234,
      build: { build_number: "38", git_commit: "not a commit" },
    });

    const attachment = await waitForRuntimeProcess({
      dataDir,
      attempts: 1,
      processIsAlive: () => true,
      probeIdentity,
    });

    expect(attachment.runtimeBuild).toEqual({
      buildNumber: null,
      commit: null,
    });
  });
});
