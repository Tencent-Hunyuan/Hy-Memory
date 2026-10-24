import { spawn } from "node:child_process";
import { mkdir, rename, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { withRuntimeMaintenanceFence } from "@one-memory/runtime-control";

const MIGRATION_PREFIX = "sdk-authority-v2";
const MAX_OUTPUT_BYTES = 1024 * 1024;
const CAPTURE_FENCE = ".sdk-migration-capture-fence.json";

export interface StartupMigrationResult {
  state: "not_required" | "cutover" | "deferred";
  code?: string;
}

export function requireCompletedStartupSdkMigration(
  result: StartupMigrationResult,
): void {
  if (result.state === "deferred") {
    throw new Error(result.code ?? "sdk_migration_incomplete");
  }
}

interface CommandResult {
  exitCode: number;
  document: Record<string, unknown>;
}

interface CoreCommand {
  command: string;
  args?: string[];
  cwd?: string;
  env?: NodeJS.ProcessEnv;
}

interface StartupMigrationOptions {
  dataDir: string;
  core: CoreCommand;
  runtimeBuild: string;
  execute?: (
    launch: CoreCommand,
    args: string[],
    dataDir: string,
  ) => Promise<CommandResult>;
  fence?: <T>(dataDir: string, action: () => Promise<T>) => Promise<T>;
  captureFence?: <T>(dataDir: string, action: () => Promise<T>) => Promise<T>;
}

export async function runStartupSdkMigration(
  options: StartupMigrationOptions,
): Promise<StartupMigrationResult> {
  const execute = options.execute ?? executeCoreCommand;
  const fence = options.fence ?? withRuntimeMaintenanceFence;
  const captureFence = options.captureFence ?? withSdkMigrationCaptureFence;
  const status = await execute(
    options.core,
    ["sdk-migration-status", "--json"],
    options.dataDir,
  );
  if (status.exitCode !== 0) {
    return { state: "deferred", code: "sdk_migration_status_unavailable" };
  }
  if (
    status.document.authority === "sdk" &&
    status.document.state === "cleanup_required"
  ) {
    return captureFence(options.dataDir, () =>
      fence(options.dataDir, async () => {
        const cleanup = await execute(
          options.core,
          ["sdk-cleanup", "--yes", "--json"],
          options.dataDir,
        );
        return cleanup.exitCode === 0 && cleanup.document.state === "clean"
          ? { state: "cutover" }
          : deferred(cleanup);
      }),
    );
  }
  if (
    status.document.authority === "sdk" ||
    status.document.state !== "required"
  ) {
    return { state: "not_required" };
  }
  const generation = status.document.generation;
  if (typeof generation !== "string" || !/^[a-f0-9]{16}$/.test(generation)) {
    return { state: "deferred", code: "sdk_migration_generation_invalid" };
  }
  const previousMigrationId = status.document.migration_id;
  const previousBundleId = status.document.bundle_id;
  const canResume =
    typeof previousMigrationId === "string" &&
    previousMigrationId.startsWith(`${MIGRATION_PREFIX}-`) &&
    typeof previousBundleId === "string" &&
    previousBundleId === `${previousMigrationId}-pre-cutover`;
  const migrationId = canResume
    ? previousMigrationId
    : `${MIGRATION_PREFIX}-${generation}`;
  const bundleId = canResume ? previousBundleId : `${migrationId}-pre-cutover`;
  return captureFence(options.dataDir, () =>
    fence(options.dataDir, async () => {
      const bundle = await execute(
        options.core,
        [
          "migration-bundle",
          "--bundle-id",
          bundleId,
          "--runtime-build",
          options.runtimeBuild,
          "--yes",
          "--json",
        ],
        options.dataDir,
      );
      if (bundle.exitCode !== 0) return deferred(bundle);
      const backfill = await execute(
        options.core,
        [
          "sdk-migrate",
          "--migration-id",
          migrationId,
          "--bundle-id",
          bundleId,
          "--yes",
          "--json",
        ],
        options.dataDir,
      );
      if (backfill.exitCode !== 0 || backfill.document.state !== "ready") {
        return deferred(backfill);
      }
      const cutover = await execute(
        options.core,
        [
          "sdk-cutover",
          "--migration-id",
          migrationId,
          "--bundle-id",
          bundleId,
          "--yes",
          "--json",
        ],
        options.dataDir,
      );
      if (cutover.exitCode !== 0 || cutover.document.state !== "cutover") {
        return deferred(cutover);
      }
      return { state: "cutover" };
    }),
  );
}

export async function withSdkMigrationCaptureFence<T>(
  dataDir: string,
  action: () => Promise<T>,
): Promise<T> {
  await mkdir(dataDir, { recursive: true, mode: 0o700 });
  const marker = path.join(dataDir, CAPTURE_FENCE);
  const temporary = `${marker}.${process.pid}.tmp`;
  await writeFile(
    temporary,
    `${JSON.stringify({ schema_version: 1, pid: process.pid })}\n`,
    { encoding: "utf8", mode: 0o600 },
  );
  await rename(temporary, marker);
  try {
    return await action();
  } finally {
    await rm(marker, { force: true });
    await rm(temporary, { force: true });
  }
}

function deferred(result: CommandResult): StartupMigrationResult {
  return {
    state: "deferred",
    code:
      typeof result.document.code === "string"
        ? result.document.code
        : "sdk_migration_incomplete",
  };
}

async function executeCoreCommand(
  launch: CoreCommand,
  args: string[],
  dataDir: string,
): Promise<CommandResult> {
  return new Promise((resolve, reject) => {
    const child = spawn(launch.command, [...(launch.args ?? []), ...args], {
      cwd: launch.cwd,
      env: { ...process.env, ...launch.env, ONE_MEMORY_DATA_DIR: dataDir },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderrBytes = 0;
    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => {
      stdout += chunk;
      if (Buffer.byteLength(stdout) > MAX_OUTPUT_BYTES) {
        child.kill("SIGKILL");
        reject(new Error("sdk_migration_output_too_large"));
      }
    });
    child.stderr.on("data", (chunk: Buffer) => {
      stderrBytes += chunk.length;
      if (stderrBytes > MAX_OUTPUT_BYTES) {
        child.kill("SIGKILL");
        reject(new Error("sdk_migration_error_output_too_large"));
      }
    });
    child.once("error", reject);
    child.once("close", (code) => {
      try {
        const line = stdout.trim().split("\n").at(-1) ?? "{}";
        const document = JSON.parse(line) as unknown;
        if (
          !document ||
          typeof document !== "object" ||
          Array.isArray(document)
        ) {
          throw new Error("sdk_migration_response_invalid");
        }
        resolve({
          exitCode: code ?? 1,
          document: document as Record<string, unknown>,
        });
      } catch {
        reject(new Error("sdk_migration_response_invalid"));
      }
    });
  });
}
