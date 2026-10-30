import type { ChildProcess, SpawnOptions } from "node:child_process";
import { EventEmitter } from "node:events";
import { mkdtemp, readFile, stat, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";
import {
  CoreSupervisor,
  SDK_MIGRATION_REQUIRED_EXIT,
} from "./core-supervisor.js";
import {
  gateServeOnSdkMigration,
  holdUntilSdkMigrated,
} from "./runtime-cli.js";
import { waitForRuntimeProcess } from "./runtime-launcher.js";
import { inspectRuntimeStatus } from "./runtime-status.js";
import {
  runSdkMigration,
  type SdkMigrationOptions,
  type SdkMigrationResult,
} from "./sdk-migration.js";
import {
  clearSdkMigrationBlock,
  readSdkMigrationBlock,
  recordSdkMigrationBlock,
  sdkMigrationLogPath,
  writeSdkMigrationLog,
} from "./sdk-migration-log.js";
import {
  enableRuntimeService,
  runtimeServiceEnabled,
} from "./service-ownership.js";

const core = { command: "/core/one-memory", args: [] };
const noFence = async <T>(
  _dataDir: string,
  action: () => Promise<T>,
): Promise<T> => action();

async function home(): Promise<string> {
  return mkdtemp(path.join(os.tmpdir(), "memhub-migration-log-"));
}

async function lines(dataDir: string): Promise<Record<string, unknown>[]> {
  const text = await readFile(sdkMigrationLogPath(dataDir), "utf8");
  return text
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line) as Record<string, unknown>);
}

describe("a frozen home's Runtime service", () => {
  const config = (dataDir: string) => ({
    dataDir,
    coreCommand: "/runtime/core/one-memory",
    buildIdentity: { buildNumber: "22" },
  });

  it("exits without migrating and keeps its marker off when started on a frozen home", async () => {
    const dataDir = await home();
    await enableRuntimeService(dataDir);
    await recordSdkMigrationBlock(
      dataDir,
      "sdk_migration_memory_unreadable",
      "desktop",
      { frozen: { service_was_enabled: true } },
    );
    const migrate = vi.fn();

    await expect(
      holdUntilSdkMigrated(config(dataDir), {
        stopped: new Promise(() => undefined),
        migrate,
      }),
    ).resolves.toBe("stop");

    expect(migrate).not.toHaveBeenCalled();
    expect(await runtimeServiceEnabled(dataDir)).toBe(false);
    expect(
      (await lines(dataDir)).find(
        (line) => line.event === "service.frozen_exit",
      ),
    ).toMatchObject({
      writer: "runtime-service",
      outcome: "refused",
      code: "sdk_migration_memory_unreadable",
    });
  });

  it("exits instead of idling when its own migration fails and freezes the home", async () => {
    const dataDir = await home();
    await enableRuntimeService(dataDir);
    const migrate = vi.fn(async (options: SdkMigrationOptions) =>
      runSdkMigration({
        ...options,
        execute: vi.fn(async (_core, args: string[]) =>
          args[0] === "sdk-migration-status"
            ? {
                exitCode: 0,
                document: { authority: "legacy", state: "required" },
              }
            : {
                exitCode: 1,
                document: {
                  state: "failed",
                  code: "sdk_migration_memory_unreadable",
                  data_restored: true,
                },
              },
        ),
      }),
    );

    await expect(
      holdUntilSdkMigrated(config(dataDir), {
        stopped: new Promise(() => undefined),
        migrate,
        pollIntervalMs: 5,
      }),
    ).resolves.toBe("stop");

    expect(migrate).toHaveBeenCalledTimes(1);
    // launchd/systemd will not start it again to retry on its own.
    expect(await runtimeServiceEnabled(dataDir)).toBe(false);
    expect(await readSdkMigrationBlock(dataDir)).toMatchObject({
      caller: "runtime-service",
      frozen: { service_was_enabled: true },
      data_restored: true,
    });
  });
});

describe("sdk-migration.log", () => {
  it("records every orchestrator decision of a deferred migration, content-free", async () => {
    const dataDir = await home();
    const execute = vi.fn(async (_core, args: string[], _dir, onProgress) => {
      if (args[0] === "sdk-migration-status") {
        return {
          exitCode: 0,
          document: { authority: "legacy", state: "required" },
        };
      }
      onProgress?.("bundle");
      return {
        exitCode: 1,
        stderrBytes: 812,
        document: {
          state: "failed",
          code: "sdk_migration_preflight_failed",
          // A document field that is free text must not reach the log.
          message: "我的私人记忆 contains spaces",
        },
      };
    });

    const result = await runSdkMigration({
      dataDir,
      core,
      runtimeBuild: "22",
      caller: "desktop",
      execute,
      fence: noFence,
    });

    expect(result).toEqual({
      state: "deferred",
      code: "sdk_migration_preflight_failed",
    });
    const written = await lines(dataDir);
    expect(written.map((line) => line.event)).toEqual([
      "migration.invoked",
      "migration.status_probed",
      "migration.started",
      "migration.progress",
      "migration.finished",
    ]);
    for (const line of written) {
      expect(line).toMatchObject({
        schema_version: 1,
        writer: "desktop",
        runtime_build: "22",
      });
      expect(typeof line.ts).toBe("string");
      expect(typeof line.pid).toBe("number");
    }
    expect(written.at(-1)).toMatchObject({
      outcome: "deferred",
      code: "sdk_migration_preflight_failed",
      details: {
        state: "deferred",
        exit_code: 1,
        stderr_bytes: 812,
        document_code: "sdk_migration_preflight_failed",
      },
    });
    const raw = await readFile(sdkMigrationLogPath(dataDir), "utf8");
    expect(raw).not.toContain("私人记忆");
    // Core's own lines carry the caller and build it was started with.
    expect(execute.mock.calls[1]?.[0].env).toMatchObject({
      ONE_MEMORY_MIGRATION_CALLER: "desktop",
      ONE_MEMORY_MIGRATION_RUNTIME_BUILD: "22",
    });
    expect(await readSdkMigrationBlock(dataDir)).toMatchObject({
      code: "sdk_migration_preflight_failed",
      caller: "desktop",
    });
  });

  it("clears the block note once a migration succeeds", async () => {
    const dataDir = await home();
    const execute = vi.fn(async (_core, args: string[]) =>
      args[0] === "sdk-migration-status"
        ? { exitCode: 1, document: {} }
        : { exitCode: 0, document: { state: "cutover" } },
    );
    await runSdkMigration({
      dataDir,
      core,
      runtimeBuild: "22",
      execute,
      fence: noFence,
    });
    expect(await readSdkMigrationBlock(dataDir)).toMatchObject({
      code: "sdk_migration_status_unavailable",
    });

    execute.mockImplementation(async (_core, args: string[]) =>
      args[0] === "sdk-migration-status"
        ? { exitCode: 0, document: { authority: "legacy", state: "required" } }
        : { exitCode: 0, document: { state: "cutover" } },
    );
    const result = await runSdkMigration({
      dataDir,
      core,
      runtimeBuild: "22",
      execute,
      fence: noFence,
    });
    expect(result).toEqual({ state: "cutover" });
    expect(await readSdkMigrationBlock(dataDir)).toBeNull();
  });

  it("redacts free text and rotates past its size bound", async () => {
    const dataDir = await home();
    await writeFile(
      sdkMigrationLogPath(dataDir),
      "x".repeat(4 * 1024 * 1024 + 1),
      { flag: "w" },
    ).catch(async () => {
      const { mkdir } = await import("node:fs/promises");
      await mkdir(path.dirname(sdkMigrationLogPath(dataDir)), {
        recursive: true,
      });
      await writeFile(
        sdkMigrationLogPath(dataDir),
        "x".repeat(4 * 1024 * 1024 + 1),
      );
    });
    await writeSdkMigrationLog(dataDir, {
      writer: "cli-upgrade",
      event: "migration.invoked",
      outcome: "started",
      details: { note: "a sentence with spaces", count: 3 },
    });
    const [line] = await lines(dataDir);
    expect(line?.details).toEqual({ note: "<redacted>", count: 3 });
    expect(
      (await stat(`${sdkMigrationLogPath(dataDir)}.1`)).size,
    ).toBeGreaterThan(4 * 1024 * 1024);
  });

  it("never throws when the log cannot be written", async () => {
    await expect(
      writeSdkMigrationLog("/dev/null/not-a-directory", {
        writer: "desktop",
        event: "migration.invoked",
        outcome: "started",
      }),
    ).resolves.toBeUndefined();
  });
});

describe("Runtime service gate", () => {
  it("migrates as the runtime service without the maintenance fence", async () => {
    const migrate = vi.fn(
      async (_options: SdkMigrationOptions): Promise<SdkMigrationResult> => ({
        state: "deferred",
        code: "sdk_migration_preflight_failed",
      }),
    );
    const result = await gateServeOnSdkMigration(
      {
        dataDir: "/data",
        coreCommand: "/runtime/core/one-memory",
        buildIdentity: { buildNumber: "22" },
      },
      migrate,
    );
    expect(result.state).toBe("deferred");
    const options = migrate.mock.calls[0]?.[0];
    expect(options).toMatchObject({
      dataDir: "/data",
      core: { command: "/runtime/core/one-memory" },
      runtimeBuild: "22",
      caller: "runtime-service",
    });
    // The fence would stop the Runtime, i.e. this very process.
    const order: string[] = [];
    await options?.fence?.("/data", async () => {
      order.push("action");
    });
    expect(order).toEqual(["action"]);
  });

  it("never lets the service spawn Core while the migration is deferred", async () => {
    const dataDir = await home();
    const config = {
      dataDir,
      coreCommand: "/runtime/core/one-memory",
      buildIdentity: { buildNumber: "22" },
    };
    const results: SdkMigrationResult[] = [
      { state: "deferred", code: "sdk_migration_preflight_failed" },
      { state: "cutover" },
    ];
    const migrate = vi.fn(async (options: SdkMigrationOptions) => {
      const result = results.shift() ?? { state: "not_required" as const };
      if (result.state === "deferred") {
        await recordSdkMigrationBlock(
          options.dataDir,
          result.code ?? "",
          "runtime-service",
        );
      }
      return result;
    });
    let settled: "serve" | "stop" | null = null;
    const held = holdUntilSdkMigrated(config, {
      stopped: new Promise(() => undefined),
      migrate,
      pollIntervalMs: 5,
    }).then((value) => {
      settled = value;
      return value;
    });
    // Deferred: the service idles without Core and records why.
    await vi.waitFor(async () =>
      expect(
        (await lines(dataDir)).some(
          (line) => line.event === "service.core_refused",
        ),
      ).toBe(true),
    );
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(settled).toBeNull();
    expect(migrate).toHaveBeenCalledTimes(1);
    const refused = (await lines(dataDir)).find(
      (line) => line.event === "service.core_refused",
    );
    expect(refused).toMatchObject({
      writer: "runtime-service",
      outcome: "refused",
      code: "sdk_migration_preflight_failed",
    });
    // Another migrator (Desktop, `memhub upgrade`) succeeds and clears the
    // note: the gate runs again, and only now may Core start.
    await clearSdkMigrationBlock(dataDir);
    await expect(held).resolves.toBe("serve");
    expect(migrate).toHaveBeenCalledTimes(2);
  });

  it("stops while idle without ever serving", async () => {
    const dataDir = await home();
    let stop: () => void = () => undefined;
    const stopped = new Promise<void>((resolve) => {
      stop = resolve;
    });
    const migrate = vi.fn(
      async (): Promise<SdkMigrationResult> => ({
        state: "deferred",
        code: "sdk_migration_status_unavailable",
      }),
    );
    await recordSdkMigrationBlock(
      dataDir,
      "sdk_migration_status_unavailable",
      "runtime-service",
    );
    const held = holdUntilSdkMigrated(
      { dataDir, coreCommand: "/core" },
      { stopped, migrate, pollIntervalMs: 5 },
    );
    await vi.waitFor(() => expect(migrate).toHaveBeenCalled());
    stop();
    await expect(held).resolves.toBe("stop");
  });

  it("a launcher waiting on the service learns the migration code at once", async () => {
    const dataDir = await home();
    const waiting = waitForRuntimeProcess({
      dataDir,
      attempts: 1_000,
      delayMs: 5,
      processIsAlive: () => false,
    });
    await new Promise((resolve) => setTimeout(resolve, 20));
    await recordSdkMigrationBlock(
      dataDir,
      "sdk_migration_preflight_failed",
      "runtime-service",
    );
    await expect(waiting).rejects.toThrow("sdk_migration_preflight_failed");
  });

  it("status names the unfinished migration instead of a generic stop", async () => {
    const dataDir = await home();
    const execute = vi.fn(async () => ({ exitCode: 1, document: {} }));
    await runSdkMigration({
      dataDir,
      core,
      runtimeBuild: "22",
      caller: "runtime-service",
      execute,
      fence: noFence,
    });
    const status = await inspectRuntimeStatus({ dataDir });
    expect(status).toEqual({
      state: "stopped",
      code: "sdk_migration_status_unavailable",
    });
  });
});

describe("Core refusing a legacy home", () => {
  it("reports Core's refusal as sdk_migration_required, not a crash", async () => {
    const child = new EventEmitter() as ChildProcess;
    child.kill = vi.fn(() => true);
    const spawnProcess = vi.fn(
      (_command: string, _args: readonly string[], _options: SpawnOptions) => {
        setImmediate(() =>
          child.emit("exit", SDK_MIGRATION_REQUIRED_EXIT, null),
        );
        return child;
      },
    );
    const dataDir = await home();
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
    });
    await expect(supervisor.start()).rejects.toThrow("sdk_migration_required");
    // `status`/`doctor` name it, and the log ties it to the spawn.
    expect(await readSdkMigrationBlock(dataDir)).toMatchObject({
      code: "sdk_migration_required",
    });
    expect((await lines(dataDir)).at(-1)).toMatchObject({
      writer: "runtime-service",
      event: "core.refused_legacy",
      code: "sdk_migration_required",
    });
  });
});
