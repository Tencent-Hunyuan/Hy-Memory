import {
  appendFileSync,
  closeSync,
  fstatSync,
  mkdirSync,
  openSync,
  readFileSync,
  readSync,
} from "node:fs";
import path from "node:path";

/**
 * What a person sees when an upgrade did not reach SDK authority.
 *
 * MemHub does not run on a home whose migration did not finish: one data shape
 * per release, no legacy fallback. The app therefore stops, and this dialog is
 * the only thing between the failure and a useful report — it names the code
 * and hands the person the one file support needs (`sdk-migration.log`), with
 * a GitHub issue already filled in from that log.
 */

export const MIGRATION_ISSUE_URL =
  "https://github.com/Tencent-Hunyuan/Hy-Memory/issues/new";

const CODE = /^[a-z][a-z0-9_]{0,95}$/;
const STEP = /^[a-z][a-z0-9_.]{0,79}$/;
const VERSION = /^[0-9A-Za-z][0-9A-Za-z.+-]{0,63}$/;
/** Generic startup codes that hide a migration block recorded by the service. */
const MASKING_CODES = new Set([
  "desktop_start_failed",
  "runtime_service_not_ready",
]);
/** Only the tail of the log is read: the last attempt is at its end. */
const LOG_TAIL_BYTES = 256 * 1024;
/** GitHub rejects very long `issues/new` URLs; stay well below its limit. */
export const MAX_ISSUE_URL_LENGTH = 6000;

export function migrationLogPath(dataDir: string): string {
  return path.join(dataDir, "logs", "sdk-migration.log");
}

interface BlockNote {
  code: string;
  dataRestored: boolean | null;
}

function readBlock(dataDir: string): BlockNote | null {
  try {
    const value = JSON.parse(
      readFileSync(path.join(dataDir, "sdk-migration-blocked.json"), "utf8"),
    ) as { code?: unknown; data_restored?: unknown };
    return typeof value.code === "string" && CODE.test(value.code)
      ? {
          code: value.code,
          dataRestored:
            typeof value.data_restored === "boolean"
              ? value.data_restored
              : null,
        }
      : null;
  } catch {
    return null;
  }
}

/**
 * The migration code behind a startup failure, or null when it was not one.
 *
 * Desktop's own migration throws the code directly. After a login launch the
 * service migrates instead, and Desktop only sees "not ready": the block note
 * the service left says why.
 */
export function migrationFailureCode(
  error: unknown,
  dataDir: string | null,
): string | null {
  const message = error instanceof Error ? error.message : "";
  if (CODE.test(message) && message.startsWith("sdk_migration_")) {
    return message;
  }
  if (dataDir === null) return null;
  const blocked = readBlock(dataDir)?.code ?? null;
  if (blocked === null) return null;
  // Desktop's own migration threw the code the note records (for example
  // `sdk_data_plane_write_job_input_missing`).
  if (message === blocked) return blocked;
  return MASKING_CODES.has(message) ||
    !CODE.test(message) ||
    message.startsWith("core_")
    ? blocked
    : null;
}

/** What the issue says about the failed upgrade; every field is optional. */
export interface MigrationIssueFacts {
  code: string;
  fromVersion?: string | null;
  fromBuild?: number | null;
  toVersion?: string | null;
  toBuild?: number | null;
  step?: string | null;
  subStep?: string | null;
  dataRestored?: boolean | null;
  osName?: string | null;
  osVersion?: string | null;
  arch?: string | null;
  /** The local log path, already shown with the home directory as `~`. */
  logPath: string;
}

const UNKNOWN = "未知";

function versionText(value: string | null | undefined): string {
  return typeof value === "string" && VERSION.test(value) ? value : UNKNOWN;
}

function buildText(value: number | null | undefined): string {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0
    ? String(value)
    : UNKNOWN;
}

function stepText(
  step: string | null | undefined,
  sub: string | null | undefined,
) {
  if (typeof step !== "string" || !STEP.test(step)) return UNKNOWN;
  return typeof sub === "string" && STEP.test(sub) ? `${step} / ${sub}` : step;
}

/** The issue body: short, content-free, and honest about what is unknown. */
export function migrationIssueBody(facts: MigrationIssueFacts): string {
  const restored =
    facts.dataRestored === true
      ? "是"
      : facts.dataRestored === false
        ? "否"
        : UNKNOWN;
  const system = [facts.osName, facts.osVersion]
    .map((part) => versionText(part))
    .filter((part) => part !== UNKNOWN)
    .join(" ");
  const arch = versionText(facts.arch);
  return [
    "## 升级迁移失败",
    `- 从 MemHub ${versionText(facts.fromVersion)}（build ${buildText(facts.fromBuild)}）升级到 ${versionText(facts.toVersion)}（build ${buildText(facts.toBuild)}），迁移数据失败。`,
    `- 错误码：${CODE.test(facts.code) ? facts.code : UNKNOWN}`,
    `- 失败步骤：${stepText(facts.step, facts.subStep)}`,
    `- 数据已恢复到升级前状态：${restored}`,
    `- 系统：${system || UNKNOWN}${arch === UNKNOWN ? "" : `（${arch}）`}`,
    `- 迁移日志（本地）：${facts.logPath}`,
    "",
    "请把上面这个日志文件拖到这里作为附件，再提交。",
    "",
  ].join("\n");
}

export function migrationIssueUrl(code: string, body?: string): string {
  const title = `[升级迁移失败 / migration failed] ${code}`;
  const plain = `${MIGRATION_ISSUE_URL}?${new URLSearchParams({ title }).toString()}`;
  if (body === undefined) return plain;
  let text = body;
  for (;;) {
    const url = `${MIGRATION_ISSUE_URL}?${new URLSearchParams({ title, body: text }).toString()}`;
    if (url.length <= MAX_ISSUE_URL_LENGTH) return url;
    if (text.length === 0) return plain;
    // Cut whole characters until it fits; the log attachment carries the rest.
    text = Array.from(text)
      .slice(0, Math.floor(Array.from(text).length * 0.8))
      .join("");
  }
}

/** `/Users/name/.one-memory/...` as `~/.one-memory/...`: never the user name. */
export function displayPath(target: string, home: string | null): string {
  if (home) {
    const normalized = path.resolve(home);
    const resolved = path.resolve(target);
    if (resolved === normalized) return "~";
    if (resolved.startsWith(`${normalized}${path.sep}`)) {
      return `~${path.sep}${path.relative(normalized, resolved)}`;
    }
  }
  return target;
}

export interface MigrationLogFacts {
  fromVersion: string | null;
  fromBuild: number | null;
  step: string | null;
  subStep: string | null;
  code: string | null;
  dataRestored: boolean | null;
}

function readTail(file: string): string {
  const descriptor = openSync(file, "r");
  try {
    const size = fstatSync(descriptor).size;
    const length = Math.min(size, LOG_TAIL_BYTES);
    const buffer = Buffer.alloc(length);
    readSync(descriptor, buffer, 0, length, size - length);
    return buffer.toString("utf8");
  } finally {
    closeSync(descriptor);
  }
}

function sourceBuild(value: unknown): number | null {
  const number = typeof value === "string" ? Number(value) : value;
  return typeof number === "number" &&
    Number.isSafeInteger(number) &&
    number > 0
    ? number
    : null;
}

/**
 * What the migration log says about the last failed attempt.
 *
 * The version upgraded from is the latest one an invocation recorded; the
 * failing step is the last `step.failed` Core wrote; the restore outcome is
 * the last `restore.finished`/`restore.failed`. Anything absent stays null.
 */
export function readMigrationLogFacts(dataDir: string): MigrationLogFacts {
  const facts: MigrationLogFacts = {
    fromVersion: null,
    fromBuild: null,
    step: null,
    subStep: null,
    code: null,
    dataRestored: null,
  };
  let text: string;
  try {
    text = readTail(migrationLogPath(dataDir));
  } catch {
    return facts;
  }
  for (const line of text.split("\n")) {
    let entry: {
      event?: unknown;
      code?: unknown;
      details?: Record<string, unknown>;
    };
    try {
      entry = JSON.parse(line) as typeof entry;
    } catch {
      continue;
    }
    const details =
      entry.details && typeof entry.details === "object" ? entry.details : {};
    if (
      entry.event === "migrate.invoked" ||
      entry.event === "migration.invoked"
    ) {
      // Each invocation is a new attempt; the version upgraded from stays.
      facts.step = null;
      facts.subStep = null;
      facts.dataRestored = null;
      const version = details.source_version;
      const build = sourceBuild(details.source_build);
      if (
        typeof version === "string" &&
        VERSION.test(version) &&
        version !== "unknown"
      ) {
        facts.fromVersion = version;
      }
      if (build !== null) facts.fromBuild = build;
    } else if (entry.event === "step.failed") {
      facts.step = typeof details.step === "string" ? details.step : null;
      facts.subStep =
        typeof details.sub_step === "string" ? details.sub_step : null;
      if (typeof entry.code === "string" && CODE.test(entry.code)) {
        facts.code = entry.code;
      }
    } else if (entry.event === "migrate.failed") {
      if (typeof entry.code === "string" && CODE.test(entry.code)) {
        facts.code = entry.code;
      }
    } else if (entry.event === "restore.finished") {
      facts.dataRestored = true;
    } else if (entry.event === "restore.failed") {
      facts.dataRestored = false;
    }
  }
  return facts;
}

export interface MigrationDialogText {
  title: string;
  message: string;
  detail: string;
  openLog: string;
  quit: string;
}

export function migrationDialogText(
  code: string,
  logPath: string,
  locale: string,
  dataRestored = false,
): MigrationDialogText {
  if (locale.toLowerCase().startsWith("en")) {
    return {
      title: "MemHub",
      message: "The data migration for this MemHub upgrade did not finish.",
      detail:
        "To protect your data, MemHub will not start. Please attach the migration log to a GitHub issue and send it to us. " +
        `(Error code: ${code})` +
        (dataRestored
          ? "\n\nYour data was restored to its state before the upgrade."
          : "") +
        `\n\nLog: ${logPath}`,
      openLog: "Open log",
      quit: "Quit",
    };
  }
  return {
    title: "MemHub",
    message: "MemHub 升级时数据迁移没有完成。",
    detail:
      "为保护你的数据，MemHub 不会启动。请把迁移日志附在 GitHub 反馈里发给我们。" +
      `（错误码：${code}）` +
      (dataRestored ? "\n\n记忆数据已恢复到升级前的状态。" : "") +
      `\n\n日志：${logPath}`,
    openLog: "打开日志",
    quit: "退出",
  };
}

export interface MigrationDialogDependencies {
  showMessageBoxSync(options: {
    type: "error";
    title: string;
    message: string;
    detail: string;
    buttons: string[];
    defaultId: number;
    cancelId: number;
    noLink: boolean;
  }): number;
  showItemInFolder(target: string): void;
  openExternal(url: string): Promise<void> | void;
}

/** The running app and system, for the issue body. */
export interface MigrationDialogEnvironment {
  appVersion?: string | null;
  buildNumber?: number | null;
  osName?: string | null;
  osVersion?: string | null;
  arch?: string | null;
  /** The account home, shown as `~` in the issue body. */
  home?: string | null;
}

/** Make sure the file the dialog points at exists and ends with this failure. */
function appendFinalLine(dataDir: string, code: string): void {
  try {
    const target = migrationLogPath(dataDir);
    mkdirSync(path.dirname(target), { recursive: true, mode: 0o700 });
    appendFileSync(
      target,
      `${JSON.stringify({
        schema_version: 1,
        ts: new Date().toISOString(),
        pid: process.pid,
        writer: "desktop",
        event: "desktop.migration_failure_shown",
        outcome: "refused",
        code,
        platform: process.platform,
      })}\n`,
      { encoding: "utf8", mode: 0o600 },
    );
  } catch {
    // The dialog still names the code even if the log cannot be written.
  }
}

/**
 * Show the migration failure dialog. Returns true when it handled the error;
 * the caller then quits without showing any window.
 */
export function showMigrationFailureDialog(options: {
  error: unknown;
  dataDir: string | null;
  locale: string;
  dependencies: MigrationDialogDependencies;
  environment?: MigrationDialogEnvironment;
}): boolean {
  const code = migrationFailureCode(options.error, options.dataDir);
  if (code === null || options.dataDir === null) return false;
  const block = readBlock(options.dataDir);
  const logFacts = readMigrationLogFacts(options.dataDir);
  // The log says whether this attempt restored (是) or failed to (否); a
  // failure before anything changed has no restore line and stays 未知
  // rather than a misleading 否.
  const dataRestored =
    logFacts.dataRestored ?? (block?.dataRestored === true ? true : null);
  appendFinalLine(options.dataDir, code);
  const logPath = migrationLogPath(options.dataDir);
  const text = migrationDialogText(
    code,
    logPath,
    options.locale,
    dataRestored === true,
  );
  const choice = options.dependencies.showMessageBoxSync({
    type: "error",
    title: text.title,
    message: text.message,
    detail: text.detail,
    buttons: [text.openLog, text.quit],
    defaultId: 0,
    cancelId: 1,
    noLink: true,
  });
  if (choice === 0) {
    options.dependencies.showItemInFolder(logPath);
    const environment = options.environment ?? {};
    const body = migrationIssueBody({
      code,
      fromVersion: logFacts.fromVersion,
      fromBuild: logFacts.fromBuild,
      toVersion: environment.appVersion ?? null,
      toBuild: environment.buildNumber ?? null,
      step: logFacts.step,
      subStep: logFacts.subStep,
      dataRestored,
      osName: environment.osName ?? null,
      osVersion: environment.osVersion ?? null,
      arch: environment.arch ?? null,
      logPath: displayPath(logPath, environment.home ?? null),
    });
    void Promise.resolve(
      options.dependencies.openExternal(migrationIssueUrl(code, body)),
    ).catch(() => undefined);
  }
  return true;
}
