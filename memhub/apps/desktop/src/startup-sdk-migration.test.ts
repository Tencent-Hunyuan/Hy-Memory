import { access, mkdtemp, readFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { expect, it, vi } from "vitest";
import {
  requireCompletedStartupSdkMigration,
  runStartupSdkMigration,
  withSdkMigrationCaptureFence,
} from "./startup-sdk-migration";

const core = { command: "/core/one-memory", args: [] };
const captureFence = async <T>(
  _dataDir: string,
  action: () => Promise<T>,
): Promise<T> => action();

it("fences hook capture only while startup migration is running", async () => {
  const dataDir = await mkdtemp(
    path.join(os.tmpdir(), "memhub-sdk-migration-"),
  );
  const marker = path.join(dataDir, ".sdk-migration-capture-fence.json");

  await withSdkMigrationCaptureFence(dataDir, async () => {
    const document = JSON.parse(await readFile(marker, "utf8")) as {
      pid: number;
    };
    expect(document.pid).toBe(process.pid);
  });

  await expect(access(marker)).rejects.toThrow();
});

it("runs bundle backfill and cutover inside the startup maintenance fence", async () => {
  const calls: string[][] = [];
  const ordering: string[] = [];
  const execute = vi.fn(async (_core, args: string[]) => {
    calls.push(args);
    const command = args[0];
    return {
      exitCode: 0,
      document:
        command === "sdk-migration-status"
          ? {
              authority: "legacy",
              state: "required",
              generation: "0123456789abcdef",
            }
          : command === "sdk-migrate"
            ? { state: "ready" }
            : command === "sdk-cutover"
              ? { state: "cutover" }
              : { state: "created" },
    };
  });
  const fenceCalls: string[] = [];
  const fence = async <T>(
    dataDir: string,
    action: () => Promise<T>,
  ): Promise<T> => {
    fenceCalls.push(dataDir);
    ordering.push("runtime-fence:start");
    const result = await action();
    ordering.push("runtime-fence:end");
    return result;
  };
  const orderedCaptureFence = async <T>(
    _dataDir: string,
    action: () => Promise<T>,
  ): Promise<T> => {
    ordering.push("capture-fence:start");
    const result = await action();
    ordering.push("capture-fence:end");
    return result;
  };

  const result = await runStartupSdkMigration({
    dataDir: "/data",
    core,
    runtimeBuild: "build-13",
    execute,
    fence,
    captureFence: orderedCaptureFence,
  });

  expect(result).toEqual({ state: "cutover" });
  expect(fenceCalls).toEqual(["/data"]);
  expect(ordering).toEqual([
    "capture-fence:start",
    "runtime-fence:start",
    "runtime-fence:end",
    "capture-fence:end",
  ]);
  expect(calls.map((args) => args[0])).toEqual([
    "sdk-migration-status",
    "migration-bundle",
    "sdk-migrate",
    "sdk-cutover",
  ]);
});

it("does not stop Runtime when authority is already SDK or the home is empty", async () => {
  for (const document of [
    { authority: "sdk", state: "complete" },
    { authority: "legacy", state: "empty" },
  ]) {
    let fenced = false;
    const fence = async <T>(
      _dataDir: string,
      action: () => Promise<T>,
    ): Promise<T> => {
      fenced = true;
      return action();
    };
    const result = await runStartupSdkMigration({
      dataDir: "/data",
      core,
      runtimeBuild: "build-13",
      execute: vi.fn(async () => ({ exitCode: 0, document })),
      fence,
      captureFence,
    });
    expect(result).toEqual({ state: "not_required" });
    expect(fenced).toBe(false);
  }
});

it("reports an unreadable migration status as deferred", async () => {
  const result = await runStartupSdkMigration({
    dataDir: "/data",
    core,
    runtimeBuild: "build-13",
    execute: vi.fn(async () => ({ exitCode: 1, document: {} })),
    fence: async (_dataDir, action) => action(),
    captureFence,
  });

  expect(result).toEqual({
    state: "deferred",
    code: "sdk_migration_status_unavailable",
  });
  expect(() => requireCompletedStartupSdkMigration(result)).toThrow(
    "sdk_migration_status_unavailable",
  );
});

it("resumes legacy table cleanup under the maintenance fence", async () => {
  const calls: string[] = [];
  const result = await runStartupSdkMigration({
    dataDir: "/data",
    core,
    runtimeBuild: "build-13",
    execute: vi.fn(async (_core, args: string[]) => {
      calls.push(args[0]);
      return args[0] === "sdk-migration-status"
        ? {
            exitCode: 0,
            document: {
              authority: "sdk",
              state: "cleanup_required",
            },
          }
        : { exitCode: 0, document: { state: "clean" } };
    }),
    fence: async (_dataDir, action) => action(),
    captureFence,
  });

  expect(result).toEqual({ state: "cutover" });
  expect(calls).toEqual(["sdk-migration-status", "sdk-cleanup"]);
});

it("keeps legacy authority when backfill is blocked", async () => {
  const execute = vi.fn(async (_core, args: string[]) => {
    const command = args[0];
    return {
      exitCode: command === "sdk-migrate" ? 4 : 0,
      document:
        command === "sdk-migration-status"
          ? {
              authority: "legacy",
              state: "required",
              generation: "0123456789abcdef",
            }
          : command === "sdk-migrate"
            ? { state: "blocked", code: "quarantine" }
            : { state: "created" },
    };
  });

  const result = await runStartupSdkMigration({
    dataDir: "/data",
    core,
    runtimeBuild: "build-13",
    execute,
    fence: async (_dataDir, action) => action(),
    captureFence,
  });

  expect(result).toEqual({ state: "deferred", code: "quarantine" });
  expect(execute.mock.calls.map((call) => call[1][0])).not.toContain(
    "sdk-cutover",
  );
});

it("reuses one v2 migration identity after a blocked startup", async () => {
  const calls: string[][] = [];
  const migrationId = "sdk-authority-v2-0123456789abcdef";
  const bundleId = `${migrationId}-pre-cutover`;
  const execute = vi.fn(async (_core, args: string[]) => {
    calls.push(args);
    return args[0] === "sdk-migration-status"
      ? {
          exitCode: 0,
          document: {
            authority: "legacy",
            state: "required",
            generation: "ffffffffffffffff",
            migration_id: migrationId,
            bundle_id: bundleId,
          },
        }
      : args[0] === "sdk-migrate"
        ? { exitCode: 4, document: { state: "blocked", code: "quarantine" } }
        : { exitCode: 0, document: { state: "reused" } };
  });

  await runStartupSdkMigration({
    dataDir: "/data",
    core,
    runtimeBuild: "build-17",
    execute,
    fence: async (_dataDir, action) => action(),
    captureFence,
  });

  expect(calls.find((args) => args[0] === "migration-bundle")).toContain(
    bundleId,
  );
  expect(calls.find((args) => args[0] === "sdk-migrate")).toContain(
    migrationId,
  );
});
