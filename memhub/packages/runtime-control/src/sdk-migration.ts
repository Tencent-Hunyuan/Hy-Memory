import { spawn } from "node:child_process";
import { withRuntimeMaintenanceFence } from "./runtime-maintenance.js";

const MAX_OUTPUT_BYTES = 1024 * 1024;

export interface SdkMigrationResult {
  state: "not_required" | "cutover" | "deferred";
  code?: string;
}

export interface SdkMigrationCommandResult {
  exitCode: number;
  document: Record<string, unknown>;
}

export interface CoreCommand {
  command: string;
  args?: string[];
  cwd?: string;
  env?: NodeJS.ProcessEnv;
}

export interface SdkMigrationOptions {
  dataDir: string;
  core: CoreCommand;
  runtimeBuild: string;
  execute?: (
    launch: CoreCommand,
    args: string[],
    dataDir: string,
    onProgress?: (step: string) => void,
  ) => Promise<SdkMigrationCommandResult>;
  fence?: <T>(dataDir: string, action: () => Promise<T>) => Promise<T>;
  /** Called with `bundle`, `prepare`, `cutover`, `cleanup` as Core reaches them. */
  onProgress?: (step: string) => void;
}

/**
 * Bring a home to SDK memory authority before a Runtime serves it.
 *
 * Core's `sdk-migrate` does the whole migration and resumes after any failure;
 * the caller (Desktop startup, CLI install/upgrade) only keeps the Runtime
 * stopped, and its service from restarting, while it runs.
 */
export async function runSdkMigration(
  options: SdkMigrationOptions,
): Promise<SdkMigrationResult> {
  const execute = options.execute ?? executeCoreCommand;
  const fence = options.fence ?? withRuntimeMaintenanceFence;
  const status = await execute(
    options.core,
    ["sdk-migration-status", "--json"],
    options.dataDir,
  ).catch(() => null);
  if (status === null || status.exitCode !== 0) {
    return { state: "deferred", code: "sdk_migration_status_unavailable" };
  }
  if (status.document.state !== "required") {
    return { state: "not_required" };
  }
  return fence(options.dataDir, async () => {
    let migrated: SdkMigrationCommandResult;
    try {
      migrated = await execute(
        options.core,
        [
          "sdk-migrate",
          "--runtime-build",
          options.runtimeBuild,
          "--yes",
          "--json",
        ],
        options.dataDir,
        options.onProgress,
      );
    } catch (error) {
      const code = error instanceof Error ? error.message : "";
      return {
        state: "deferred",
        code: /^[a-z][a-z0-9_]{0,95}$/.test(code)
          ? code
          : "sdk_migration_response_invalid",
      };
    }
    if (migrated.exitCode !== 0) return deferred(migrated);
    const state = migrated.document.state;
    if (state === "cutover" || state === "complete")
      return { state: "cutover" };
    if (state === "not_required") return { state: "not_required" };
    return deferred(migrated);
  });
}

function deferred(result: SdkMigrationCommandResult): SdkMigrationResult {
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
  onProgress?: (step: string) => void,
): Promise<SdkMigrationCommandResult> {
  return new Promise((resolve, reject) => {
    const child = spawn(launch.command, [...(launch.args ?? []), ...args], {
      cwd: launch.cwd,
      env: { ...process.env, ...launch.env, ONE_MEMORY_DATA_DIR: dataDir },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let reported = 0;
    let stderrBytes = 0;
    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => {
      stdout += chunk;
      const complete = stdout.lastIndexOf("\n");
      if (onProgress && complete > reported) {
        for (const line of stdout.slice(reported, complete).split("\n")) {
          const step = progressStep(line);
          if (step !== null) onProgress(step);
        }
        reported = complete + 1;
      }
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

function progressStep(line: string): string | null {
  try {
    const document = JSON.parse(line) as { state?: unknown; step?: unknown };
    return document.state === "running" && typeof document.step === "string"
      ? document.step
      : null;
  } catch {
    return null;
  }
}
