import type { ChildProcess, SpawnOptions } from "node:child_process";
import { spawn } from "node:child_process";
import { EventEmitter } from "node:events";
import { chmod, mkdir, mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";

import {
  CoreSupervisor,
  ensureRuntimeProcess,
  findAvailablePort,
  isOwnedHealth,
  processIsAlive,
  resolveCoreDataDir,
  resolveCoreLaunch,
  resolvePackagedRuntimeLaunch,
} from "./core-supervisor";

function fakeChild(): ChildProcess {
  const child = new EventEmitter() as ChildProcess;
  child.kill = vi.fn(() => {
    queueMicrotask(() => child.emit("exit", 0, null));
    return true;
  });
  return child;
}

describe("Core launch", () => {
  it("keeps the preview app away from the existing user's data", () => {
    expect(resolveCoreDataDir("One Memory Preview", "/home/person")).toBe(
      path.join("/home/person", ".one-memory-preview"),
    );
    expect(resolveCoreDataDir("One Memory", "/home/person")).toBe(
      path.join("/home/person", ".one-memory"),
    );
    expect(
      resolveCoreDataDir("One Memory Preview", "/home/person", "/test/data"),
    ).toBe("/test/data");
  });

  it("uses the workspace Core during development", () => {
    expect(
      resolveCoreLaunch({
        packaged: false,
        appPath: "/repo/apps/desktop",
        resourcesPath: "/unused",
        platform: "darwin",
      }),
    ).toEqual({
      command: "uv",
      args: [
        "run",
        "--directory",
        path.join("/repo", "apps", "core"),
        "one-memory",
      ],
      cwd: "/repo",
    });
  });

  it("uses the bundled executable in a packaged app", () => {
    expect(
      resolveCoreLaunch({
        packaged: true,
        appPath: "/unused",
        resourcesPath: "/app/resources",
        platform: "win32",
      }).command,
    ).toBe(path.join("/app/resources", "core", "one-memory.exe"));
  });

  it("starts the packaged Runtime from the bundle and points hosts at the stable install", () => {
    const launch = resolvePackagedRuntimeLaunch({
      resourcesPath: "/app/resources",
      dataDir: "/home/person/.one-memory",
      runtimeCommand: "/app/One Memory",
      home: "/home/person",
      platform: "linux",
    });
    expect(launch.command).toBe("/app/One Memory");
    expect(launch.args).toEqual([
      path.join("/app/resources", "runtime", "runtime.cjs"),
      "serve",
    ]);
    expect(launch.env).toMatchObject({
      ELECTRON_RUN_AS_NODE: "1",
      ONE_MEMORY_DATA_DIR: "/home/person/.one-memory",
      ONE_MEMORY_CORE_COMMAND: path.join(
        "/app/resources",
        "core",
        "one-memory",
      ),
      ONE_MEMORY_SYNC_HOOK_ASSETS: path.join(
        "/home/person/.local/share/one-memory/current",
        "host-bridge",
        "sync-hook",
      ),
      ONE_MEMORY_NODE_COMMAND:
        "/home/person/.local/share/one-memory/current/bin/node",
      ONE_MEMORY_MCP_ENTRY:
        "/home/person/.local/share/one-memory/current/host-bridge/mcp.cjs",
      ONE_MEMORY_RECALL_CLI:
        "/home/person/.local/share/one-memory/current/host-bridge/recall-cli.cjs",
    });
    expect(launch.env?.ONE_MEMORY_NODE_COMMAND).not.toContain(".app");
    expect(launch.env?.ONE_MEMORY_SYNC_HOOK_ASSETS).not.toContain(".app");
    expect(ensureRuntimeProcess).toBeTypeOf("function");
  });
});

describe("Core ownership", () => {
  it("allocates a usable ephemeral loopback port", async () => {
    await expect(findAvailablePort()).resolves.toBeGreaterThan(0);
  });

  it("accepts only the instance started by this desktop process", () => {
    expect(
      isOwnedHealth(
        {
          status: "ok",
          service: "one-memory-core",
          version: "0.0.0",
          instance_id: "other",
        },
        "owned",
      ),
    ).toBe(false);
  });

  it("passes an ownership token and stops only its child", async () => {
    const child = fakeChild();
    const spawnProcess = vi.fn(
      (_command: string, _args: readonly string[], _options: SpawnOptions) =>
        child,
    );
    const supervisor = new CoreSupervisor({
      launch: { command: "one-memory", args: [] },
      dataDir: "/tmp/one-memory-test",
      instanceId: "owned",
      port: 9123,
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

    await expect(supervisor.start()).resolves.toMatchObject({
      instance_id: "owned",
    });
    const spawnOptions = spawnProcess.mock.calls[0]?.[2];
    expect(spawnOptions?.env?.ONE_MEMORY_INSTANCE_ID).toBe("owned");
    expect(spawnOptions?.env?.ONE_MEMORY_PORT).toBe("9123");
    expect(supervisor.port).toBe(9123);

    await supervisor.stop();
    expect(child.kill).toHaveBeenCalledWith("SIGTERM");
  });

  it("attaches to a verified independent Core without spawning or stopping it", async () => {
    const dataDir = await mkdtemp(path.join(tmpdir(), "core-attached-"));
    await writeRuntime(dataDir, {
      schema_version: 1,
      service: "one-memory-core",
      instance_id: "cli-instance",
      pid: process.pid,
      parent_pid: null,
      host: "127.0.0.1",
      port: 9234,
      started_at: "2026-09-19T01:00:00Z",
    });
    const spawnProcess = vi.fn(() => fakeChild());
    const supervisor = new CoreSupervisor({
      launch: { command: "one-memory", args: [] },
      dataDir,
      spawnProcess,
      probeHealth: async (port, token) => {
        expect(port).toBe(9234);
        expect(token).toBe("attached-secret");
        return {
          status: "ok",
          service: "one-memory-core",
          version: "0.0.0",
          instance_id: "cli-instance",
        };
      },
    });

    await expect(supervisor.start()).resolves.toMatchObject({
      instance_id: "cli-instance",
    });
    expect(supervisor.port).toBe(9234);
    expect(supervisor.instanceId).toBe("cli-instance");
    expect(supervisor.apiToken).toBe("attached-secret");
    expect(spawnProcess).not.toHaveBeenCalled();

    await supervisor.stop();
    expect(spawnProcess).not.toHaveBeenCalled();
  });

  it("does not attach or start twice through one supervisor", async () => {
    const dataDir = await mkdtemp(path.join(tmpdir(), "core-attached-once-"));
    await writeRuntime(dataDir, {
      schema_version: 1,
      service: "one-memory-core",
      instance_id: "cli-instance",
      pid: process.pid,
      parent_pid: null,
      host: "127.0.0.1",
      port: 9234,
      started_at: "2026-09-19T01:00:00Z",
    });
    const supervisor = new CoreSupervisor({
      launch: { command: "one-memory", args: [] },
      dataDir,
      probeHealth: async () => ({
        status: "ok",
        service: "one-memory-core",
        version: "0.0.0",
        instance_id: "cli-instance",
      }),
    });

    await supervisor.start();
    await expect(supervisor.start()).rejects.toThrow("already started");
    await supervisor.stop();
  });

  it("attaches when a standalone Core wins the race with the first spawn", async () => {
    const dataDir = await mkdtemp(path.join(tmpdir(), "core-attach-race-"));
    let probes = 0;
    // The winner has finished publishing its receipt and token by the time
    // our child loses the lock. Writing them from inside the spawn raced the
    // readiness loop: on a slow runner the token was not there yet, the
    // re-read rejected the receipt, and the test saw a false conflict.
    let winner: Promise<void> = Promise.resolve();
    const spawnProcess = vi.fn(() => {
      const child = fakeChild();
      winner = writeRuntime(dataDir, {
        schema_version: 1,
        service: "one-memory-core",
        instance_id: "cli-race-winner",
        pid: process.pid,
        parent_pid: null,
        host: "127.0.0.1",
        port: 9236,
        started_at: "2026-09-19T01:00:00Z",
      }).then(() => {
        child.emit("exit", 1, null);
      });
      return child;
    });
    const supervisor = new CoreSupervisor({
      launch: { command: "one-memory", args: [] },
      dataDir,
      attempts: 2,
      delayMs: 0,
      spawnProcess,
      probeHealth: async (port, token) => {
        probes += 1;
        // Our own child never answers: it lost the race. Waiting here for the
        // winner's files keeps the readiness loop from concluding before the
        // winner exists, which is the order this test is about.
        await winner;
        if (port !== 9236 || token !== "attached-secret")
          throw new Error("not attached yet");
        return {
          status: "ok",
          service: "one-memory-core",
          version: "0.0.0",
          instance_id: "cli-race-winner",
        };
      },
    });

    await expect(supervisor.start()).resolves.toMatchObject({
      instance_id: "cli-race-winner",
    });
    expect(spawnProcess).toHaveBeenCalledTimes(1);
    expect(probes).toBeGreaterThan(0);
    await supervisor.stop();
  });

  it.each([
    ["non-loopback", { host: "0.0.0.0" }, true],
    ["mismatched instance", {}, false],
  ])(
    "refuses a live but unverified %s receipt instead of spawning another Core",
    async (_label, receiptPatch, matchingHealth) => {
      const dataDir = await mkdtemp(path.join(tmpdir(), "core-untrusted-"));
      await writeRuntime(dataDir, {
        schema_version: 1,
        service: "one-memory-core",
        instance_id: "claimed-instance",
        pid: process.pid,
        parent_pid: null,
        host: "127.0.0.1",
        port: 9235,
        started_at: "2026-09-19T01:00:00Z",
        ...receiptPatch,
      });
      const spawnProcess = vi.fn(() => fakeChild());
      const supervisor = new CoreSupervisor({
        launch: { command: "one-memory", args: [] },
        dataDir,
        spawnProcess,
        probeHealth: async () => ({
          status: "ok",
          service: "one-memory-core",
          version: "0.0.0",
          instance_id: matchingHealth ? "claimed-instance" : "other-instance",
        }),
      });

      await expect(supervisor.start()).rejects.toThrow(
        "无法验证正在运行的 MemHub Core",
      );
      expect(spawnProcess).not.toHaveBeenCalled();
    },
  );

  it("refuses an independent Core whose token file is readable by other users", async () => {
    if (process.platform === "win32") return;
    const dataDir = await mkdtemp(path.join(tmpdir(), "core-token-mode-"));
    await writeRuntime(dataDir, {
      schema_version: 1,
      service: "one-memory-core",
      instance_id: "cli-instance",
      pid: process.pid,
      parent_pid: null,
      host: "127.0.0.1",
      port: 9237,
      started_at: "2026-09-19T01:00:00Z",
    });
    await chmod(path.join(dataDir, "runtime-token"), 0o644);
    const spawnProcess = vi.fn(() => fakeChild());
    const supervisor = new CoreSupervisor({
      launch: { command: "one-memory", args: [] },
      dataDir,
      spawnProcess,
      probeHealth: async () => {
        throw new Error("must not probe with an unsafe token");
      },
    });

    await expect(supervisor.start()).rejects.toThrow(
      "无法验证正在运行的 MemHub Core",
    );
    expect(spawnProcess).not.toHaveBeenCalled();
  });

  it("surfaces spawn errors without waiting for a timeout", async () => {
    const child = fakeChild();
    const supervisor = new CoreSupervisor({
      launch: { command: "/missing/one-memory", args: [] },
      dataDir: "/tmp/one-memory-test",
      attempts: 5,
      delayMs: 0,
      spawnProcess: () => {
        queueMicrotask(() => child.emit("error", new Error("ENOENT")));
        return child;
      },
      probeHealth: async () => {
        throw new Error("not ready");
      },
    });

    await expect(supervisor.start()).rejects.toThrow(
      "MemHub Core could not start: ENOENT",
    );
  });

  it("escalates when the Core ignores graceful shutdown", async () => {
    const child = new EventEmitter() as ChildProcess;
    child.kill = vi.fn((signal) => {
      if (signal === "SIGKILL") {
        queueMicrotask(() => child.emit("exit", null, "SIGKILL"));
      }
      return true;
    });
    const supervisor = new CoreSupervisor({
      launch: { command: "one-memory", args: [] },
      dataDir: "/tmp/one-memory-test",
      instanceId: "owned",
      attempts: 1,
      delayMs: 0,
      shutdownTimeoutMs: 0,
      spawnProcess: () => child,
      probeHealth: async () => ({
        status: "ok",
        service: "one-memory-core",
        version: "0.0.0",
        instance_id: "owned",
      }),
    });
    await supervisor.start();

    await supervisor.stop();

    expect(child.kill).toHaveBeenNthCalledWith(1, "SIGTERM");
    expect(child.kill).toHaveBeenNthCalledWith(2, "SIGKILL");
  });

  it("tells Core which process it should not outlive", async () => {
    const child = fakeChild();
    const spawnProcess = vi.fn(
      (_command: string, _args: readonly string[], _options: SpawnOptions) =>
        child,
    );
    const supervisor = new CoreSupervisor({
      launch: { command: "one-memory", args: [] },
      dataDir: "/tmp/one-memory-test",
      instanceId: "owned",
      attempts: 1,
      delayMs: 0,
      spawnProcess,
      probeHealth: async () => ownedHealth,
    });

    await supervisor.start();

    // Core cannot work this out for itself: under PyInstaller its own parent
    // is the bootloader, which is alive exactly when the app is not.
    expect(spawnProcess.mock.calls[0]?.[2]?.env?.ONE_MEMORY_PARENT_PID).toBe(
      String(process.pid),
    );
  });
});

const ownedHealth = {
  status: "ok",
  service: "one-memory-core",
  version: "0.0.0",
  instance_id: "owned",
} as const;

/** A supervisor whose first launch fails the way a held lock makes it fail. */
async function supervisorBlockedOnce(dataDir: string) {
  const children: ChildProcess[] = [];
  let launches = 0;
  const supervisor = new CoreSupervisor({
    launch: { command: "one-memory", args: [] },
    dataDir,
    instanceId: "owned",
    attempts: 2,
    delayMs: 0,
    shutdownTimeoutMs: 2_000,
    spawnProcess: () => {
      launches += 1;
      const child = fakeChild();
      children.push(child);
      // Core exits at once when another Core owns the data directory.
      if (launches === 1) queueMicrotask(() => child.emit("exit", 1, null));
      return child;
    },
    probeHealth: async () => {
      if (launches < 2) throw new Error("not ready");
      return ownedHealth;
    },
  });
  return { supervisor, launches: () => launches, children };
}

async function writeReceipt(
  dataDir: string,
  receipt: Record<string, unknown>,
): Promise<void> {
  await mkdir(dataDir, { recursive: true });
  await writeFile(path.join(dataDir, "runtime.json"), JSON.stringify(receipt));
}

async function writeRuntime(
  dataDir: string,
  receipt: Record<string, unknown>,
): Promise<void> {
  await writeReceipt(dataDir, receipt);
  const token = path.join(dataDir, "runtime-token");
  await writeFile(token, "attached-secret", { mode: 0o600 });
  if (process.platform !== "win32") await chmod(token, 0o600);
}

/** A live process standing in for a Core, plus a pid that is certainly gone. */
async function liveProcess(): Promise<number> {
  const child = spawn("sleep", ["30"], { stdio: "ignore" });
  await new Promise((resolve) => child.once("spawn", resolve));
  return child.pid as number;
}

async function deadPid(): Promise<number> {
  const child = spawn("true", [], { stdio: "ignore" });
  const pid = child.pid as number;
  await new Promise((resolve) => child.once("exit", resolve));
  return pid;
}

describe("a Core left behind by a crashed app", () => {
  it("is ended so that the app can start at all", async () => {
    const dataDir = await mkdtemp(path.join(tmpdir(), "core-orphan-"));
    const orphan = await liveProcess();
    await writeReceipt(dataDir, {
      pid: orphan,
      parent_pid: await deadPid(),
      started_at: "2026-09-05T03:29:49Z",
    });
    const { supervisor, launches } = await supervisorBlockedOnce(dataDir);

    // Observed for real: an app killed with SIGKILL left a Core holding this
    // lock for 65 minutes, and every later launch died on arrival.
    await expect(supervisor.start()).resolves.toMatchObject({
      instance_id: "owned",
    });
    expect(processIsAlive(orphan)).toBe(false);
    expect(launches()).toBe(2);
  });

  it("is only ended when its launcher is provably gone", async () => {
    const dataDir = await mkdtemp(path.join(tmpdir(), "core-live-parent-"));
    const holder = await liveProcess();
    await writeReceipt(dataDir, {
      pid: holder,
      // Our own process stands in for a second app that is genuinely running.
      parent_pid: process.pid,
      started_at: "2026-09-05T03:29:49Z",
    });
    const { supervisor } = await supervisorBlockedOnce(dataDir);

    await expect(supervisor.start()).rejects.toThrow("另一个 MemHub Core");
    expect(processIsAlive(holder)).toBe(true);
    process.kill(holder, "SIGKILL");
  });

  it("leaves a Core someone started by hand alone", async () => {
    const dataDir = await mkdtemp(path.join(tmpdir(), "core-manual-"));
    const holder = await liveProcess();
    // No launcher recorded: a developer ran Core in a terminal. Killing that
    // would be taking something we were not given.
    await writeReceipt(dataDir, {
      pid: holder,
      started_at: "2026-09-05T03:29:49Z",
    });
    const { supervisor } = await supervisorBlockedOnce(dataDir);

    await expect(supervisor.start()).rejects.toThrow(`进程 ${holder}`);
    expect(processIsAlive(holder)).toBe(true);
    process.kill(holder, "SIGKILL");
  });

  it("tries once more when the receipt outlived its Core", async () => {
    const dataDir = await mkdtemp(path.join(tmpdir(), "core-stale-"));
    await writeReceipt(dataDir, {
      pid: await deadPid(),
      parent_pid: await deadPid(),
      started_at: "2026-09-05T03:29:49Z",
    });
    const { supervisor, launches } = await supervisorBlockedOnce(dataDir);

    // The lock is already free; the first failure was a race with a shutdown.
    await expect(supervisor.start()).resolves.toMatchObject({
      instance_id: "owned",
    });
    expect(launches()).toBe(2);
  });

  it("keeps the original failure when no Core holds anything", async () => {
    const dataDir = await mkdtemp(path.join(tmpdir(), "core-none-"));
    const { supervisor, launches } = await supervisorBlockedOnce(dataDir);

    await expect(supervisor.start()).rejects.toThrow(
      "MemHub Core exited before becoming ready",
    );
    expect(launches()).toBe(1);
  });
});
