import { mkdir, mkdtemp, readFile, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";
import { withRuntimeMaintenanceFence } from "./runtime-maintenance.js";
import {
  readInstalledRuntimeSource,
  runSdkMigration,
} from "./sdk-migration.js";
import {
  freezeCapture,
  readSdkMigrationBlock,
  SDK_MIGRATION_CAPTURE_FENCE,
  thawCapture,
} from "./sdk-migration-log.js";
import {
  disableRuntimeService,
  enableRuntimeService,
  runtimeServiceEnabled,
} from "./service-ownership.js";

const core = { command: "/core/one-memory", args: [] };
const noFence = async <T>(
  _dataDir: string,
  action: () => Promise<T>,
): Promise<T> => action();

function statusThen(
  migrate: { exitCode: number; document: Record<string, unknown> },
  status: Record<string, unknown> = { authority: "legacy", state: "required" },
) {
  return vi.fn(async (_core, args: string[]) =>
    args[0] === "sdk-migration-status"
      ? { exitCode: 0, document: status }
      : migrate,
  );
}

it("runs the one migration command inside the runtime maintenance fence", async () => {
  const ordering: string[] = [];
  const execute = vi.fn(async (_core, args: string[]) => {
    ordering.push(args[0] ?? "");
    return args[0] === "sdk-migration-status"
      ? { exitCode: 0, document: { authority: "legacy", state: "required" } }
      : { exitCode: 0, document: { state: "cutover" } };
  });
  const fence = async <T>(
    dataDir: string,
    action: () => Promise<T>,
  ): Promise<T> => {
    ordering.push(`fence:start:${dataDir}`);
    const result = await action();
    ordering.push("fence:end");
    return result;
  };

  const result = await runSdkMigration({
    dataDir: "/data",
    core,
    runtimeBuild: "20",
    execute,
    fence,
  });

  expect(result).toEqual({ state: "cutover" });
  expect(ordering).toEqual([
    "sdk-migration-status",
    "fence:start:/data",
    "sdk-migrate",
    "fence:end",
  ]);
  expect(execute.mock.calls[1]?.[1]).toEqual([
    "sdk-migrate",
    "--runtime-build",
    "20",
    "--yes",
    "--json",
  ]);
});

it("does not stop Runtime when nothing needs migrating", async () => {
  let fenced = false;
  const result = await runSdkMigration({
    dataDir: "/data",
    core,
    runtimeBuild: "20",
    execute: statusThen(
      { exitCode: 0, document: {} },
      { authority: "sdk", state: "not_required" },
    ),
    fence: async (_dataDir, action) => {
      fenced = true;
      return action();
    },
  });

  expect(result).toEqual({ state: "not_required" });
  expect(fenced).toBe(false);
});

it("reports a repair of an already migrated home as complete, not a second cutover", async () => {
  const result = await runSdkMigration({
    dataDir: "/data",
    core,
    runtimeBuild: "20",
    execute: statusThen(
      { exitCode: 0, document: { state: "complete" } },
      { authority: "sdk", state: "required" },
    ),
    fence: noFence,
  });

  expect(result).toEqual({ state: "complete" });
});

it("reports an unreadable migration status as deferred", async () => {
  const result = await runSdkMigration({
    dataDir: "/data",
    core,
    runtimeBuild: "20",
    execute: vi.fn(async () => ({ exitCode: 1, document: {} })),
    fence: noFence,
  });

  expect(result).toEqual({
    state: "deferred",
    code: "sdk_migration_status_unavailable",
  });
});

it("cycles the runtime when the status is unreadable so no pre-upgrade Runtime keeps serving", async () => {
  const fenced: string[] = [];
  const result = await runSdkMigration({
    dataDir: "/data",
    core,
    runtimeBuild: "20",
    execute: vi.fn(async () => ({ exitCode: 1, document: {} })),
    fence: async <T>(dataDir: string, action: () => Promise<T>) => {
      fenced.push(dataDir);
      return action();
    },
  });

  expect(fenced).toEqual(["/data"]);
  expect(result.state).toBe("deferred");
});

it("passes the migration failure code to the startup dialog", async () => {
  const result = await runSdkMigration({
    dataDir: "/data",
    core,
    runtimeBuild: "20",
    execute: statusThen({
      exitCode: 1,
      document: {
        state: "failed",
        code: "sdk_migration_disk_space_insufficient",
      },
    }),
    fence: noFence,
  });

  expect(result).toEqual({
    state: "deferred",
    code: "sdk_migration_disk_space_insufficient",
  });
});

it("never reports success for a result it does not recognise", async () => {
  const result = await runSdkMigration({
    dataDir: "/data",
    core,
    runtimeBuild: "20",
    execute: statusThen({ exitCode: 0, document: { state: "running" } }),
    fence: noFence,
  });

  expect(result).toEqual({
    state: "deferred",
    code: "sdk_migration_incomplete",
  });
});

it("names a Core that could not be run instead of throwing", async () => {
  const result = await runSdkMigration({
    dataDir: "/data",
    core,
    runtimeBuild: "20",
    execute: vi.fn(async (_core, args: string[]) => {
      if (args[0] === "sdk-migration-status") {
        return {
          exitCode: 0,
          document: { authority: "legacy", state: "required" },
        };
      }
      throw new Error("sdk_migration_response_invalid");
    }),
    fence: noFence,
  });

  expect(result).toEqual({
    state: "deferred",
    code: "sdk_migration_response_invalid",
  });
});

it("streams Core's progress lines to the caller", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "sdk-migrate-"));
  const fake = path.join(directory, "core.sh");
  await writeFile(
    fake,
    [
      "#!/bin/sh",
      'if [ "$1" = "sdk-migration-status" ]; then',
      '  echo \'{"authority":"legacy","state":"required"}\'; exit 0',
      "fi",
      'echo \'{"state":"running","step":"bundle"}\'',
      'echo \'{"state":"running","step":"cutover"}\'',
      'echo \'{"state":"cutover"}\'',
    ].join("\n"),
    { mode: 0o755 },
  );
  const steps: string[] = [];

  const result = await runSdkMigration({
    dataDir: directory,
    core: { command: fake },
    runtimeBuild: "20",
    fence: noFence,
    onProgress: (step) => steps.push(step),
  });

  expect(result).toEqual({ state: "cutover" });
  expect(steps).toEqual(["bundle", "cutover"]);
});

describe("a failed migration freezes the home until the next attempt", () => {
  async function homeWithService(enabled: boolean): Promise<string> {
    const dataDir = await mkdtemp(path.join(os.tmpdir(), "sdk-freeze-"));
    if (enabled) await enableRuntimeService(dataDir);
    return dataDir;
  }
  // The real fence, with only the Runtime stop faked.
  const fence = <T>(dataDir: string, action: () => Promise<T>) =>
    withRuntimeMaintenanceFence(dataDir, action, {
      serviceEnabled: runtimeServiceEnabled,
      disableService: disableRuntimeService,
      enableService: enableRuntimeService,
      stopRuntime: async () => "stopped" as const,
    });
  const failed = (document: Record<string, unknown> = {}) =>
    statusThen({
      exitCode: 1,
      document: {
        state: "failed",
        code: "sdk_migration_memory_unreadable",
        ...document,
      },
    });
  const fenceFile = (dataDir: string) =>
    path.join(dataDir, SDK_MIGRATION_CAPTURE_FENCE);

  it("keeps the service off, fences capture and records the freeze", async () => {
    const dataDir = await homeWithService(true);

    const result = await runSdkMigration({
      dataDir,
      core,
      runtimeBuild: "22",
      execute: failed({ data_restored: true }),
      fence,
      sourceRuntime: { version: "1.0.4", build_number: 104 },
    });

    expect(result).toEqual({
      state: "deferred",
      code: "sdk_migration_memory_unreadable",
      dataRestored: true,
    });
    // The fence did not turn the service back on.
    expect(await runtimeServiceEnabled(dataDir)).toBe(false);
    expect(
      JSON.parse(await readFile(fenceFile(dataDir), "utf8")),
    ).toMatchObject({ frozen: true });
    expect(await readSdkMigrationBlock(dataDir)).toMatchObject({
      code: "sdk_migration_memory_unreadable",
      frozen: { service_was_enabled: true },
      data_restored: true,
      source: { version: "1.0.4", build_number: 104 },
    });
  });

  it("lifts the freeze and turns the service back on after a successful retry", async () => {
    const dataDir = await homeWithService(true);
    await runSdkMigration({
      dataDir,
      core,
      runtimeBuild: "22",
      execute: failed(),
      fence,
      sourceRuntime: { version: "1.0.4", build_number: 104 },
    });
    const retry = statusThen({ exitCode: 0, document: { state: "cutover" } });

    const result = await runSdkMigration({
      dataDir,
      core,
      runtimeBuild: "23",
      execute: retry,
      fence,
    });

    expect(result).toEqual({ state: "cutover" });
    expect(await readSdkMigrationBlock(dataDir)).toBeNull();
    expect(await runtimeServiceEnabled(dataDir)).toBe(true);
    await expect(readFile(fenceFile(dataDir), "utf8")).rejects.toThrow();
    // The retry still names the version the home was upgraded from.
    const call = retry.mock.calls[1]?.[0] as
      | { env?: NodeJS.ProcessEnv }
      | undefined;
    const env = call?.env;
    expect(env?.ONE_MEMORY_MIGRATION_SOURCE_VERSION).toBe("1.0.4");
    expect(env?.ONE_MEMORY_MIGRATION_SOURCE_BUILD).toBe("104");
    const log = await readFile(
      path.join(dataDir, "logs", "sdk-migration.log"),
      "utf8",
    );
    expect(log).toContain('"event":"migration.unfrozen"');
    expect(log).toContain('"source_version":"1.0.4"');
  });

  it("does not turn on a service that was off before the freeze", async () => {
    const dataDir = await homeWithService(false);
    await runSdkMigration({
      dataDir,
      core,
      runtimeBuild: "22",
      execute: failed(),
      fence,
    });

    await runSdkMigration({
      dataDir,
      core,
      runtimeBuild: "23",
      execute: statusThen({ exitCode: 0, document: { state: "cutover" } }),
      fence,
    });

    expect(await runtimeServiceEnabled(dataDir)).toBe(false);
    expect(await readSdkMigrationBlock(dataDir)).toBeNull();
  });

  it("a second failure keeps what the first freeze recorded", async () => {
    const dataDir = await homeWithService(true);
    await runSdkMigration({
      dataDir,
      core,
      runtimeBuild: "22",
      execute: failed(),
      fence,
      sourceRuntime: { version: "1.0.4", build_number: 104 },
    });

    await runSdkMigration({
      dataDir,
      core,
      runtimeBuild: "23",
      execute: failed(),
      fence,
    });

    expect(await runtimeServiceEnabled(dataDir)).toBe(false);
    expect(await readSdkMigrationBlock(dataDir)).toMatchObject({
      frozen: { service_was_enabled: true },
      source: { version: "1.0.4", build_number: 104 },
    });
  });

  it("an unreadable status freezes inside the fence", async () => {
    const dataDir = await homeWithService(true);

    const result = await runSdkMigration({
      dataDir,
      core,
      runtimeBuild: "22",
      execute: vi.fn(async () => ({ exitCode: 1, document: {} })),
      fence,
    });

    expect(result.state).toBe("deferred");
    expect(await runtimeServiceEnabled(dataDir)).toBe(false);
    expect((await readSdkMigrationBlock(dataDir))?.frozen).toEqual({
      service_was_enabled: true,
    });
  });

  it("thawing never lifts the live fence of a running migration", async () => {
    const dataDir = await homeWithService(false);
    await writeFile(
      fenceFile(dataDir),
      JSON.stringify({ schema_version: 1, pid: process.pid }),
    );
    await thawCapture(dataDir);
    expect(JSON.parse(await readFile(fenceFile(dataDir), "utf8"))).toEqual({
      schema_version: 1,
      pid: process.pid,
    });
    await freezeCapture(dataDir);
    await thawCapture(dataDir);
    await expect(readFile(fenceFile(dataDir), "utf8")).rejects.toThrow();
  });
});

describe("the version an upgrade replaced", () => {
  it("reads a release version and build from the previous install manifest", async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), "sdk-source-"));
    await mkdir(path.join(root, "versions", "old"), { recursive: true });
    await writeFile(
      path.join(root, "versions", "old", "manifest.json"),
      JSON.stringify({ version: "1.0.4", build_number: 104 }),
    );
    await mkdir(path.join(root, "versions", "app"), { recursive: true });
    await writeFile(
      path.join(root, "versions", "app", "manifest.json"),
      JSON.stringify({ version: "ab".repeat(32), build_number: 99 }),
    );

    await expect(
      readInstalledRuntimeSource(root, "versions/old"),
    ).resolves.toEqual({ version: "1.0.4", build_number: 104 });
    // A packaged App runtime is versioned by a digest: only its build is told.
    await expect(
      readInstalledRuntimeSource(root, "versions/app"),
    ).resolves.toEqual({ version: null, build_number: 99 });
    await expect(readInstalledRuntimeSource(root, null)).resolves.toBeNull();
    await expect(
      readInstalledRuntimeSource(root, "../outside"),
    ).resolves.toBeNull();
    await expect(
      readInstalledRuntimeSource(root, "versions/missing"),
    ).resolves.toBeNull();
  });
});
