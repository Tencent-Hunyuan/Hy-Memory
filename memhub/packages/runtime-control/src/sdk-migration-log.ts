import {
  appendFile,
  chmod,
  mkdir,
  readFile,
  rename,
  rm,
  stat,
  writeFile,
} from "node:fs/promises";
import path from "node:path";

/**
 * The one log support asks for when an upgrade did not reach SDK authority.
 *
 * `<dataDir>/logs/sdk-migration.log` is JSON Lines shared with Core's
 * `one_memory/sdk_migration_log.py`, which writes the same line shape. It never
 * carries user content: detail values are numbers, booleans and short
 * identifier-shaped strings; anything else is dropped. Writing never throws —
 * logging must not be the reason a migration or a startup fails.
 */

export type SdkMigrationCaller =
  | "desktop"
  | "desktop-background"
  | "cli-install"
  | "cli-upgrade"
  | "cli-start"
  | "cli-restart"
  | "runtime-service";

export const SDK_MIGRATION_LOG_NAME = "sdk-migration.log";
const MAX_BYTES = 4 * 1024 * 1024;
const SAFE_TEXT = /^[A-Za-z0-9_.:/@+-]{0,160}$/;
const MAX_ITEMS = 32;
// Preserve invocation order within this process, including fire-and-forget
// progress followed by an awaited terminal event. Homes remain independent.
const pendingWrites = new Map<string, Promise<void>>();

export function sdkMigrationLogPath(dataDir: string): string {
  return path.join(dataDir, "logs", SDK_MIGRATION_LOG_NAME);
}

export interface SdkMigrationLogEntry {
  writer: SdkMigrationCaller;
  event: string;
  outcome: "started" | "succeeded" | "failed" | "deferred" | "refused";
  code?: string;
  migrationId?: string;
  runtimeBuild?: string;
  details?: Record<string, unknown>;
}

function safe(value: unknown, depth = 0): unknown {
  if (value === null || value === undefined) return null;
  if (typeof value === "boolean") return value;
  if (typeof value === "number") {
    return Number.isFinite(value) ? Math.round(value * 1000) / 1000 : null;
  }
  if (typeof value === "string") {
    return SAFE_TEXT.test(value) ? value : "<redacted>";
  }
  if (depth >= 4) return "<nested>";
  if (Array.isArray(value)) {
    const items = value
      .slice(0, MAX_ITEMS)
      .map((item) => safe(item, depth + 1));
    if (value.length > MAX_ITEMS) items.push(`<+${value.length - MAX_ITEMS}>`);
    return items;
  }
  if (typeof value === "object") {
    const result: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value).slice(0, MAX_ITEMS)) {
      if (SAFE_TEXT.test(key)) result[key] = safe(item, depth + 1);
    }
    return result;
  }
  return "<unsupported>";
}

/** A stable code from an error, never its free-text message. */
export function migrationErrorCode(error: unknown, fallback: string): string {
  const message = error instanceof Error ? error.message : "";
  return /^[a-z][a-z0-9_]{0,95}$/.test(message) ? message : fallback;
}

export async function writeSdkMigrationLog(
  dataDir: string,
  entry: SdkMigrationLogEntry,
): Promise<void> {
  try {
    const line = safe({
      schema_version: 1,
      ts: new Date().toISOString(),
      pid: process.pid,
      writer: entry.writer,
      event: entry.event,
      outcome: entry.outcome,
      ...(entry.code === undefined ? {} : { code: entry.code }),
      ...(entry.migrationId === undefined
        ? {}
        : { migration_id: entry.migrationId }),
      ...(entry.runtimeBuild === undefined
        ? {}
        : { runtime_build: entry.runtimeBuild }),
      platform: process.platform,
      ...(entry.details === undefined ? {} : { details: entry.details }),
    });
    const target = path.resolve(sdkMigrationLogPath(dataDir));
    const previous = pendingWrites.get(target) ?? Promise.resolve();
    const pending = previous
      .catch(() => undefined)
      .then(async () => {
        await mkdir(path.dirname(target), { recursive: true, mode: 0o700 });
        const size = await stat(target).then(
          (info) => info.size,
          () => 0,
        );
        if (size > MAX_BYTES) await rename(target, `${target}.1`);
        await appendFile(target, `${JSON.stringify(line)}\n`, {
          encoding: "utf8",
          mode: 0o600,
        });
      });
    pendingWrites.set(target, pending);
    try {
      await pending;
    } finally {
      if (pendingWrites.get(target) === pending) pendingWrites.delete(target);
    }
  } catch {
    // Never let the log decide whether a migration or a startup happens.
  }
}

/**
 * A durable note that this home is blocked on an unfinished migration.
 *
 * Written when a migration is deferred and removed once one succeeds, so a
 * separate process (`memhub status`, the Desktop dialog after a background
 * launch) reports the real cause instead of a generic "not running".
 */
export interface SdkMigrationBlock {
  code: string;
  caller: SdkMigrationCaller;
  at: string;
  /**
   * The home is frozen after a failed migration: the Runtime service marker
   * is off, the service exits instead of idling and host capture is fenced.
   * The next foreground migration (Desktop launch, `memhub install`,
   * `upgrade`, `start`/`restart`) lifts it, and re-enables the service when it
   * was enabled before the freeze.
   */
  frozen?: { service_was_enabled: boolean };
  /** Core put the data back exactly as it was before the upgrade. */
  data_restored?: boolean;
  /** Why that restore did not happen, when Core tried and failed. */
  restore_code?: string;
  /** The Runtime the home was upgraded from, when it is known. */
  source?: SdkMigrationSource;
}

/** Build and version of the Runtime an upgrade replaced, from its manifest. */
export interface SdkMigrationSource {
  version: string | null;
  build_number: number | null;
}

export interface SdkMigrationBlockDetails {
  frozen?: { service_was_enabled: boolean };
  data_restored?: boolean;
  restore_code?: string;
  source?: SdkMigrationSource | null;
}

const BLOCK_NAME = "sdk-migration-blocked.json";

function blockPath(dataDir: string): string {
  return path.join(dataDir, BLOCK_NAME);
}

export async function recordSdkMigrationBlock(
  dataDir: string,
  code: string,
  caller: SdkMigrationCaller,
  details: SdkMigrationBlockDetails = {},
): Promise<void> {
  try {
    await mkdir(dataDir, { recursive: true, mode: 0o700 });
    const target = blockPath(dataDir);
    const temporary = `${target}.${process.pid}.tmp`;
    await writeFile(
      temporary,
      `${JSON.stringify({
        schema_version: 1,
        code,
        caller,
        at: new Date().toISOString(),
        ...(details.frozen === undefined ? {} : { frozen: details.frozen }),
        ...(details.data_restored === undefined
          ? {}
          : { data_restored: details.data_restored }),
        ...(details.restore_code === undefined
          ? {}
          : { restore_code: details.restore_code }),
        ...(details.source ? { source: details.source } : {}),
      })}\n`,
      { encoding: "utf8", mode: 0o600 },
    );
    await chmod(temporary, 0o600);
    await rename(temporary, target);
  } catch {
    // Best effort, like the log.
  }
}

export async function clearSdkMigrationBlock(dataDir: string): Promise<void> {
  await rm(blockPath(dataDir), { force: true }).catch(() => undefined);
}

const SAFE_CODE = /^[a-z][a-z0-9_]{0,95}$/;
const SAFE_VERSION = /^[0-9A-Za-z][0-9A-Za-z.+-]{0,63}$/;

function sourceFrom(value: unknown): SdkMigrationSource | undefined {
  if (!value || typeof value !== "object") return undefined;
  const { version, build_number } = value as Record<string, unknown>;
  const safeVersion =
    typeof version === "string" && SAFE_VERSION.test(version) ? version : null;
  const safeBuild =
    typeof build_number === "number" &&
    Number.isSafeInteger(build_number) &&
    build_number > 0
      ? build_number
      : null;
  return safeVersion === null && safeBuild === null
    ? undefined
    : { version: safeVersion, build_number: safeBuild };
}

export async function readSdkMigrationBlock(
  dataDir: string,
): Promise<SdkMigrationBlock | null> {
  try {
    const value = JSON.parse(
      await readFile(blockPath(dataDir), "utf8"),
    ) as Partial<SdkMigrationBlock>;
    if (
      typeof value.code !== "string" ||
      !SAFE_CODE.test(value.code) ||
      typeof value.caller !== "string" ||
      typeof value.at !== "string"
    ) {
      return null;
    }
    const block: SdkMigrationBlock = {
      code: value.code,
      caller: value.caller,
      at: value.at,
    };
    if (
      value.frozen &&
      typeof value.frozen === "object" &&
      typeof value.frozen.service_was_enabled === "boolean"
    ) {
      block.frozen = { service_was_enabled: value.frozen.service_was_enabled };
    }
    if (typeof value.data_restored === "boolean") {
      block.data_restored = value.data_restored;
    }
    if (
      typeof value.restore_code === "string" &&
      SAFE_CODE.test(value.restore_code)
    ) {
      block.restore_code = value.restore_code;
    }
    const source = sourceFrom(value.source);
    if (source !== undefined) block.source = source;
    return block;
  } catch {
    return null;
  }
}

/**
 * `<dataDir>/.sdk-migration-capture-fence.json`, shared with Core: while it is
 * live, host capture hooks (`one-memory capture <host>`) return without
 * writing. Core holds it with its pid during `sdk-migrate`; a frozen home
 * holds it without one until the next migration lifts it.
 */
export const SDK_MIGRATION_CAPTURE_FENCE = ".sdk-migration-capture-fence.json";

export async function freezeCapture(dataDir: string): Promise<void> {
  try {
    const target = path.join(dataDir, SDK_MIGRATION_CAPTURE_FENCE);
    const temporary = `${target}.${process.pid}.tmp`;
    await writeFile(
      temporary,
      `${JSON.stringify({ schema_version: 1, frozen: true, reason: "sdk_migration_failed" })}\n`,
      { encoding: "utf8", mode: 0o600 },
    );
    await chmod(temporary, 0o600);
    await rename(temporary, target);
  } catch {
    // Core leaves the same fence on its own failures.
  }
}

/** Lift a frozen capture fence; a live one (a running migration) is kept. */
export async function thawCapture(dataDir: string): Promise<void> {
  const target = path.join(dataDir, SDK_MIGRATION_CAPTURE_FENCE);
  try {
    const value = JSON.parse(await readFile(target, "utf8")) as {
      frozen?: unknown;
    };
    if (value.frozen === true) await rm(target, { force: true });
  } catch {
    // Absent or unreadable: Core treats an unreadable fence as stale.
  }
}
