import { spawn } from "node:child_process";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { withRuntimeMaintenanceFence } from "./runtime-maintenance.js";
import {
  clearSdkMigrationBlock,
  freezeCapture,
  migrationErrorCode,
  readSdkMigrationBlock,
  recordSdkMigrationBlock,
  type SdkMigrationCaller,
  type SdkMigrationLogEntry,
  type SdkMigrationSource,
  thawCapture,
  writeSdkMigrationLog,
} from "./sdk-migration-log.js";
import {
  disableRuntimeService,
  enableRuntimeService,
  runtimeServiceEnabled,
} from "./service-ownership.js";

const MAX_OUTPUT_BYTES = 1024 * 1024;

export interface SdkMigrationResult {
  /** `complete`: already on SDK authority; leftovers were repaired. */
  state: "not_required" | "cutover" | "complete" | "deferred";
  code?: string;
  /** Deferred only: Core put the data back as it was before the upgrade. */
  dataRestored?: boolean;
}

export interface SdkMigrationCommandResult {
  exitCode: number;
  document: Record<string, unknown>;
  /** Size of Core's stderr. Its text can carry content, so only the size is logged. */
  stderrBytes?: number;
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
  /** Who is migrating; stamped on every line of the migration log. */
  caller?: SdkMigrationCaller;
  /**
   * The Runtime this upgrade replaced, read from its install manifest before
   * `current` moved. Unknown (`null`) falls back to what an earlier failed
   * attempt recorded in the block note; it is never guessed.
   */
  sourceRuntime?: SdkMigrationSource | null;
}

/** Build and version of an installed Runtime, from its `manifest.json`. */
export async function readInstalledRuntimeSource(
  installRoot: string,
  target: string | null | undefined,
): Promise<SdkMigrationSource | null> {
  if (!target) return null;
  const directory = path.resolve(installRoot, target);
  if (
    path.dirname(directory) !== path.join(path.resolve(installRoot), "versions")
  )
    return null;
  try {
    const manifest = JSON.parse(
      await readFile(path.join(directory, "manifest.json"), "utf8"),
    ) as { version?: unknown; build_number?: unknown };
    const version =
      typeof manifest.version === "string" &&
      // Packaged App runtimes are versioned by a tree digest, which says
      // nothing to a person; only a release version is reported.
      /^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]{1,40})?$/.test(manifest.version)
        ? manifest.version
        : null;
    const build =
      typeof manifest.build_number === "number" &&
      Number.isSafeInteger(manifest.build_number) &&
      manifest.build_number > 0
        ? manifest.build_number
        : null;
    return version === null && build === null
      ? null
      : { version, build_number: build };
  } catch {
    return null;
  }
}

/**
 * Bring a home to SDK memory authority before a Runtime serves it.
 *
 * Core's `sdk-migrate` does the whole migration. A failure before SDK
 * authority is committed puts the data back as it was before the upgrade; the
 * caller (Desktop startup, CLI install/upgrade, the Runtime service) only
 * keeps the Runtime stopped, and its service from restarting, while it runs.
 *
 * Every decision is appended to `<dataDir>/logs/sdk-migration.log`, and Core
 * writes its own phase and failure lines to the same file, so one attachment
 * explains an upgrade that did not finish.
 *
 * A deferred result freezes the home until the next attempt: the block note
 * (read by `memhub status` and Desktop) records the freeze, the Runtime
 * service marker stays off so launchd/systemd do not start a Runtime that
 * would retry on its own, and host capture stays fenced so nothing writes
 * into the restored data. A successful attempt lifts all of it and turns the
 * service back on when it was on before the freeze.
 */
export async function runSdkMigration(
  options: SdkMigrationOptions,
): Promise<SdkMigrationResult> {
  const caller = options.caller ?? "desktop";
  const log = (entry: Omit<SdkMigrationLogEntry, "writer" | "runtimeBuild">) =>
    writeSdkMigrationLog(options.dataDir, {
      writer: caller,
      runtimeBuild: options.runtimeBuild,
      ...entry,
    });
  const started = Date.now();
  const prior = await readSdkMigrationBlock(options.dataDir);
  // Whether the service ran before this home was (first) frozen: a freeze
  // turned the marker off, so the marker alone no longer says.
  const serviceWasEnabled =
    prior?.frozen?.service_was_enabled === true ||
    (await runtimeServiceEnabled(options.dataDir).catch(() => false));
  const source = options.sourceRuntime ?? prior?.source ?? null;
  const core: CoreCommand = {
    ...options.core,
    env: {
      ...options.core.env,
      // Core stamps these on its own lines in the shared log.
      ONE_MEMORY_MIGRATION_CALLER: caller,
      ONE_MEMORY_MIGRATION_RUNTIME_BUILD: options.runtimeBuild,
      ...(source?.version
        ? { ONE_MEMORY_MIGRATION_SOURCE_VERSION: source.version }
        : {}),
      ...(source?.build_number
        ? { ONE_MEMORY_MIGRATION_SOURCE_BUILD: String(source.build_number) }
        : {}),
    },
  };
  const finish = async (
    result: SdkMigrationResult,
    details: Record<string, unknown> = {},
    restoreCode?: string,
  ): Promise<SdkMigrationResult> => {
    await log({
      event: "migration.finished",
      outcome: result.state === "deferred" ? "deferred" : "succeeded",
      ...(result.code === undefined ? {} : { code: result.code }),
      details: {
        state: result.state,
        duration_ms: Date.now() - started,
        ...(result.state === "deferred"
          ? {
              data_restored: result.dataRestored === true,
              frozen: true,
              service_was_enabled: serviceWasEnabled,
            }
          : {}),
        ...details,
      },
    });
    if (result.state === "deferred") {
      // Marker first: from here launchd/systemd do not start a Runtime.
      await disableRuntimeService(options.dataDir).catch(() => undefined);
      await freezeCapture(options.dataDir);
      await recordSdkMigrationBlock(
        options.dataDir,
        result.code ?? "sdk_migration_incomplete",
        caller,
        {
          frozen: { service_was_enabled: serviceWasEnabled },
          data_restored: result.dataRestored === true,
          ...(restoreCode === undefined ? {} : { restore_code: restoreCode }),
          source,
        },
      );
    } else {
      await clearSdkMigrationBlock(options.dataDir);
      await thawCapture(options.dataDir);
      if (prior?.frozen !== undefined) {
        if (serviceWasEnabled) {
          await enableRuntimeService(options.dataDir).catch(() => undefined);
        }
        await log({
          event: "migration.unfrozen",
          outcome: "succeeded",
          details: { service_enabled: serviceWasEnabled },
        });
      }
    }
    return result;
  };
  const execute = options.execute ?? executeCoreCommand;
  const fence = options.fence ?? withRuntimeMaintenanceFence;
  await log({
    event: "migration.invoked",
    outcome: "started",
    details: {
      source_version: source?.version ?? "unknown",
      source_build: source?.build_number ?? "unknown",
      frozen: prior?.frozen !== undefined,
    },
  });
  let statusFailure = "";
  const status = await execute(
    core,
    ["sdk-migration-status", "--json"],
    options.dataDir,
  ).catch((error: unknown) => {
    statusFailure = migrationErrorCode(error, "sdk_migration_status_failed");
    return null;
  });
  await log({
    event: "migration.status_probed",
    outcome: status !== null && status.exitCode === 0 ? "succeeded" : "failed",
    details: {
      exit_code: status?.exitCode ?? null,
      stderr_bytes: status?.stderrBytes ?? null,
      authority: status?.document.authority ?? null,
      state: status?.document.state ?? null,
      ...(statusFailure ? { reason: statusFailure } : {}),
    },
  });
  if (status === null || status.exitCode !== 0) {
    // An unreadable status still must not leave a pre-upgrade Runtime serving
    // the home: stop it, and freeze inside the fence so the fence does not
    // turn the service back on.
    const unavailable: SdkMigrationResult = {
      state: "deferred",
      code: "sdk_migration_status_unavailable",
    };
    return fence(options.dataDir, () => finish(unavailable)).catch(() =>
      finish(unavailable),
    );
  }
  if (status.document.state !== "required") {
    return finish({ state: "not_required" });
  }
  return fence(options.dataDir, async () => {
    await log({ event: "migration.started", outcome: "started" });
    let migrated: SdkMigrationCommandResult;
    try {
      migrated = await execute(
        core,
        [
          "sdk-migrate",
          "--runtime-build",
          options.runtimeBuild,
          "--yes",
          "--json",
        ],
        options.dataDir,
        (step) => {
          void log({
            event: "migration.progress",
            outcome: "started",
            details: { step },
          });
          options.onProgress?.(step);
        },
      );
    } catch (error) {
      return finish(
        {
          state: "deferred",
          code: migrationErrorCode(error, "sdk_migration_response_invalid"),
        },
        { stage: "sdk_migrate_spawn" },
      );
    }
    const restoreCode =
      typeof migrated.document.restore_code === "string" &&
      /^[a-z][a-z0-9_]{0,95}$/.test(migrated.document.restore_code)
        ? migrated.document.restore_code
        : undefined;
    const childDetails = {
      exit_code: migrated.exitCode,
      stderr_bytes: migrated.stderrBytes ?? null,
      document_state: migrated.document.state ?? null,
      document_code: migrated.document.code ?? null,
      migration_id: migrated.document.migration_id ?? null,
      ...(restoreCode === undefined ? {} : { restore_code: restoreCode }),
    };
    if (migrated.exitCode !== 0)
      return finish(deferred(migrated), childDetails, restoreCode);
    const state = migrated.document.state;
    if (state === "cutover" || state === "complete") {
      return finish({ state }, childDetails);
    }
    if (state === "not_required") {
      return finish({ state: "not_required" }, childDetails);
    }
    return finish(deferred(migrated), childDetails, restoreCode);
  });
}

function deferred(result: SdkMigrationCommandResult): SdkMigrationResult {
  return {
    state: "deferred",
    code:
      typeof result.document.code === "string"
        ? result.document.code
        : "sdk_migration_incomplete",
    ...(result.document.data_restored === true ? { dataRestored: true } : {}),
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
          stderrBytes,
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
