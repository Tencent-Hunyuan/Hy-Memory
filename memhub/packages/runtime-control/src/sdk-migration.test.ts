import { mkdtemp, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { expect, it, vi } from "vitest";
import { runSdkMigration } from "./sdk-migration.js";

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
