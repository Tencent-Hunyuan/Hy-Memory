import { expect, it, vi } from "vitest";
import { runStartupSdkMigration } from "./startup-sdk-migration";

const core = { command: "/core/one-memory", args: [] };

it("runs bundle backfill and cutover inside the startup maintenance fence", async () => {
  const calls: string[][] = [];
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
    return action();
  };

  const result = await runStartupSdkMigration({
    dataDir: "/data",
    core,
    runtimeBuild: "build-13",
    execute,
    fence,
  });

  expect(result).toEqual({ state: "cutover" });
  expect(fenceCalls).toEqual(["/data"]);
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
  });

  expect(result).toEqual({
    state: "deferred",
    code: "sdk_migration_status_unavailable",
  });
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
  });

  expect(result).toEqual({ state: "deferred", code: "quarantine" });
  expect(execute.mock.calls.map((call) => call[1][0])).not.toContain(
    "sdk-cutover",
  );
});
