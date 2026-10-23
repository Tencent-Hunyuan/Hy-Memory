import { randomUUID } from "node:crypto";
import {
  appendFileSync,
  chmodSync,
  lstatSync,
  mkdirSync,
  renameSync,
  rmSync,
  statSync,
} from "node:fs";
import {
  appendFile,
  chmod,
  lstat,
  mkdir,
  readdir,
  readFile,
  rename,
  rm,
  stat,
  statfs,
  writeFile,
} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { strToU8, zipSync } from "fflate";

export const DIAGNOSTIC_SCHEMA_VERSION = 1 as const;
export const DIAGNOSTIC_MAX_FILE_BYTES = 4 * 1024 * 1024;
export const DIAGNOSTIC_MAX_TOTAL_BYTES = 128 * 1024 * 1024;
export const DIAGNOSTIC_MAX_AGE_MS = 14 * 24 * 60 * 60 * 1000;

export type DiagnosticLevel = "info" | "warn" | "error";
export type DiagnosticService =
  | "runtime"
  | "core"
  | "host-bridge"
  | "desktop"
  | "cli";

export interface DiagnosticEventV1 {
  schema_version: 1;
  timestamp: string;
  level: DiagnosticLevel;
  service: DiagnosticService;
  component: string;
  event: string;
  outcome: string;
  session_id: string;
  trace_id?: string;
  incident_id?: string;
  instance_id?: string;
  operation_id?: string;
  request_id?: string;
  job_id?: string;
  task_id?: string;
  /** Package identity, so a four-level chain can be attributed to a build.
   *  Optional: an ad-hoc source run has no package and must not invent one. */
  build_id?: string;
  package_id?: string;
  round?: string;
  duration_ms?: number;
  state_before?: string;
  state_after?: string;
  error_code?: string;
  error_type?: string;
  safe_stack?: string[];
  details?: Record<string, string | number | boolean | null>;
}

export interface DiagnosticInput
  extends Omit<
    DiagnosticEventV1,
    "schema_version" | "timestamp" | "service" | "session_id"
  > {
  timestamp?: string;
}

export interface DiagnosticLoggerOptions {
  dataDir: string;
  service: DiagnosticService;
  sessionId?: string;
  now?: () => Date;
  maxFileBytes?: number;
}

export interface DiagnosticSink {
  log(input: DiagnosticInput): Promise<void>;
}

const NAME =
  /^diagnostic-(runtime|core|host-bridge|desktop|cli)\.jsonl(?:\.\d+)?$/;
const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/;
const EVENT_NAME = /^[a-z][a-z0-9]*(?:[._-][a-z0-9]+){0,15}$/;
const DETAIL_KEYS = new Set([
  "action",
  "attempt",
  "count",
  "method",
  "mode",
  "phase",
  "platform",
  "reason",
  "result_count",
  "source_id",
  "spawned",
  "status",
  "version",
  "parent_session_id",
  "invalid_lines",
  "truncated",
  "reader_port",
  "runtime_state",
  "core_state",
  "broker_state",
  "instance_id",
  "open_file_count",
  "memory_bytes",
  "thread_count",
  "database_error_count",
  "file_count",
  "sqlite_file_count",
  "byte_file_count",
  "total_bytes",
  "copied_bytes",
  "bytes_per_second",
  "stored_count",
  "revised_count",
  "skipped_count",
  "error_count",
  "restart_streak",
  "pending_count",
  "prepared_count",
  "queue_depth",
  "in_flight_count",
  "call_count",
  "created_count",
  "updated_count",
  "superseded_count",
  "deleted_count",
  "operation_kind",
  "file_kind",
  // Bounded values the Core supervisor reports when a start fails. They are
  // numbers or short enums, not free text, so they are safe to allow explicitly.
  "attempts",
  "delay_ms",
  "budget_ms",
  "last_probe",
  "port",
  "exit_code",
  "signal",
]);
const EVENT_KEYS = new Set([
  "schema_version",
  "timestamp",
  "level",
  "service",
  "component",
  "event",
  "outcome",
  "session_id",
  "trace_id",
  "incident_id",
  "instance_id",
  "operation_id",
  "request_id",
  "job_id",
  "task_id",
  "build_id",
  "package_id",
  "round",
  "duration_ms",
  "state_before",
  "state_after",
  "error_code",
  "error_type",
  "safe_stack",
  "details",
]);

/** Package identity from the environment the shared spawn point populates.
 *
 * Only fields that are actually present are returned, so a source run writes no
 * identity rather than a fabricated one.
 */
function buildIdentityFromEnvironment(
  environment: NodeJS.ProcessEnv = process.env,
): Pick<DiagnosticEventV1, "build_id" | "package_id" | "round"> {
  const identity: Pick<DiagnosticEventV1, "build_id" | "package_id" | "round"> =
    {};
  const buildId = environment.ONE_MEMORY_BUILD_ID?.trim();
  const packageId = environment.ONE_MEMORY_PACKAGE_ID?.trim();
  const round = environment.ONE_MEMORY_ROUND?.trim();
  if (buildId) identity.build_id = buildId;
  if (packageId) identity.package_id = packageId;
  if (round) identity.round = round;
  return identity;
}

export class DiagnosticLogger {
  readonly sessionId: string;
  private readonly options: DiagnosticLoggerOptions;
  private tail: Promise<void> = Promise.resolve();

  constructor(options: DiagnosticLoggerOptions) {
    this.options = options;
    this.sessionId = canonicalUuid(options.sessionId) ?? randomUUID();
  }

  log(input: DiagnosticInput): Promise<void> {
    let event: DiagnosticEventV1;
    try {
      event = diagnosticEvent({
        // A package build stamps its identity into the environment at the shared
        // spawn point; copy it onto every event so one chain can be attributed to
        // one package. A source run leaves them unset and writes nothing.
        ...buildIdentityFromEnvironment(),
        ...input,
        service: this.options.service,
        session_id: this.sessionId,
        timestamp:
          input.timestamp ??
          (this.options.now ?? (() => new Date()))().toISOString(),
      });
    } catch {
      // Diagnostics are observational. A malformed event must be dropped rather
      // than turn a logging mistake into a product outage. The pure validator
      // stays strict so tests and export paths can still reject unsafe records.
      return this.tail;
    }
    this.tail = this.tail.then(() => this.write(event)).catch(() => undefined);
    return this.tail;
  }

  async markIncident(input: {
    component: string;
    traceId?: string;
    instanceId?: string;
    status?: string;
  }): Promise<{ incident_id: string; session_id: string; timestamp: string }> {
    const incidentId = randomUUID();
    const timestamp = (this.options.now ?? (() => new Date()))().toISOString();
    await this.log({
      timestamp,
      level: "info",
      component: input.component,
      event: "incident.marked",
      outcome: "recorded",
      incident_id: incidentId,
      ...(input.traceId ? { trace_id: input.traceId } : {}),
      ...(input.instanceId ? { instance_id: input.instanceId } : {}),
      details: { status: input.status ?? "unknown" },
    });
    return { incident_id: incidentId, session_id: this.sessionId, timestamp };
  }

  private async write(event: DiagnosticEventV1): Promise<void> {
    const directory = await ensureDiagnosticDirectory(this.options.dataDir);
    await pruneDiagnosticLogs(
      this.options.dataDir,
      new Date(event.timestamp).getTime(),
    );
    const target = path.join(
      directory,
      `diagnostic-${this.options.service}.jsonl`,
    );
    const line = `${JSON.stringify(event)}\n`;
    const current = await stat(target).catch(() => null);
    if (
      current &&
      current.size + Buffer.byteLength(line) >
        (this.options.maxFileBytes ?? DIAGNOSTIC_MAX_FILE_BYTES)
    ) {
      await rotate(target);
    }
    await appendFile(target, line, { encoding: "utf8", mode: 0o600 });
    await chmod(target, 0o600);
  }
}

/** Persist one fatal startup event before Electron can terminate the process. */
export function writeDiagnosticEventSync(
  options: DiagnosticLoggerOptions,
  input: DiagnosticInput,
): void {
  const event = diagnosticEvent({
    ...buildIdentityFromEnvironment(),
    ...input,
    service: options.service,
    session_id: canonicalUuid(options.sessionId) ?? randomUUID(),
    timestamp:
      input.timestamp ?? (options.now ?? (() => new Date()))().toISOString(),
  });
  const directory = diagnosticDirectory(options.dataDir);
  mkdirSync(directory, { recursive: true, mode: 0o700 });
  const directoryInfo = lstatSync(directory);
  if (!directoryInfo.isDirectory() || directoryInfo.isSymbolicLink())
    fail("diagnostic_directory_unsafe");
  chmodSync(directory, 0o700);
  const target = path.join(directory, `diagnostic-${options.service}.jsonl`);
  const line = `${JSON.stringify(event)}\n`;
  let current: ReturnType<typeof statSync> | null = null;
  try {
    current = statSync(target);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
  }
  if (
    current &&
    current.size + Buffer.byteLength(line) >
      (options.maxFileBytes ?? DIAGNOSTIC_MAX_FILE_BYTES)
  ) {
    rotateSync(target);
  }
  appendFileSync(target, line, { encoding: "utf8", mode: 0o600 });
  chmodSync(target, 0o600);
}

export function diagnosticEvent(
  value: Omit<DiagnosticEventV1, "schema_version">,
): DiagnosticEventV1 {
  for (const key of Object.keys(value)) {
    if (!EVENT_KEYS.has(key)) fail("diagnostic_field_invalid");
  }
  const timestamp = new Date(value.timestamp);
  if (Number.isNaN(timestamp.getTime())) fail("diagnostic_timestamp_invalid");
  if (!EVENT_NAME.test(value.component) || !EVENT_NAME.test(value.event))
    fail("diagnostic_event_invalid");
  if (!(["info", "warn", "error"] as const).includes(value.level))
    fail("diagnostic_level_invalid");
  if (
    !(["runtime", "core", "host-bridge", "desktop", "cli"] as const).includes(
      value.service,
    )
  )
    fail("diagnostic_service_invalid");
  if (!IDENTIFIER.test(value.outcome)) fail("diagnostic_outcome_invalid");
  if (!canonicalUuid(value.session_id)) fail("diagnostic_session_invalid");
  for (const key of [
    "trace_id",
    "incident_id",
    "instance_id",
    "operation_id",
    "request_id",
    "job_id",
    "task_id",
    "build_id",
    "package_id",
    "round",
  ] as const) {
    const item = value[key];
    if (item !== undefined && !IDENTIFIER.test(item))
      fail("diagnostic_identifier_invalid");
  }
  if (
    value.duration_ms !== undefined &&
    (!Number.isSafeInteger(value.duration_ms) || value.duration_ms < 0)
  )
    fail("diagnostic_duration_invalid");
  const details = sanitizeDetails(value.details);
  return Object.freeze({
    schema_version: DIAGNOSTIC_SCHEMA_VERSION,
    ...value,
    timestamp: timestamp.toISOString(),
    ...(details ? { details } : {}),
    ...(value.safe_stack ? { safe_stack: safeStack(value.safe_stack) } : {}),
  });
}

export async function readDiagnosticEvents(
  dataDir: string,
  filter: {
    since?: Date;
    sessionId?: string;
    traceId?: string;
    incidentId?: string;
    operationId?: string;
    requestId?: string;
    jobId?: string;
    taskId?: string;
    component?: string;
    event?: string;
    errorsOnly?: boolean;
    tail?: number;
  } = {},
): Promise<{ events: DiagnosticEventV1[]; invalid_lines: number }> {
  const files = await diagnosticLogFiles(dataDir);
  let events: DiagnosticEventV1[] = [];
  let invalidLines = 0;
  for (const file of files) {
    const content = await readFile(file, "utf8");
    for (const line of content.split("\n")) {
      if (!line) continue;
      try {
        const item = parseDiagnosticEvent(JSON.parse(line));
        events.push(item);
      } catch {
        invalidLines += 1;
      }
    }
  }
  events.sort((a, b) => compareDiagnosticTimestamps(a.timestamp, b.timestamp));
  if (filter.incidentId) {
    const marker = events.find(
      (item) => item.incident_id === filter.incidentId,
    );
    if (!marker) events = [];
    else {
      const markedAt = new Date(marker.timestamp).getTime();
      events = events.filter(
        (item) =>
          (item.session_id === marker.session_id ||
            (marker.instance_id !== undefined &&
              item.instance_id === marker.instance_id)) &&
          new Date(item.timestamp).getTime() >= markedAt - 15 * 60_000 &&
          new Date(item.timestamp).getTime() <= markedAt + 5 * 60_000,
      );
    }
  }
  events = events.filter((item) => {
    if (filter.since && new Date(item.timestamp) < filter.since) return false;
    if (filter.sessionId && item.session_id !== filter.sessionId) return false;
    if (filter.traceId && item.trace_id !== filter.traceId) return false;
    if (filter.operationId && item.operation_id !== filter.operationId)
      return false;
    if (filter.requestId && item.request_id !== filter.requestId) return false;
    if (filter.jobId && item.job_id !== filter.jobId) return false;
    if (filter.taskId && item.task_id !== filter.taskId) return false;
    if (filter.component && item.component !== filter.component) return false;
    if (filter.event && item.event !== filter.event) return false;
    return !(filter.errorsOnly && item.level !== "error");
  });
  const tail = Math.min(Math.max(filter.tail ?? 200, 1), 5_000);
  return { events: events.slice(-tail), invalid_lines: invalidLines };
}

export async function exportDiagnosticBundle(options: {
  dataDir: string;
  target: string;
  productVersion: string;
  status?: Record<string, string | number | boolean | null>;
}): Promise<{ event_count: number; invalid_lines: number }> {
  const result = await readDiagnosticEvents(options.dataDir, { tail: 5_000 });
  const payload = {
    schema_version: 1,
    exported_at: new Date().toISOString(),
    product_version: cleanText(options.productVersion, 128),
    platform: process.platform,
    arch: process.arch,
    status: sanitizeDetails(options.status),
    event_count: result.events.length,
    invalid_lines: result.invalid_lines,
    events: result.events,
  };
  const directory = path.dirname(options.target);
  await mkdir(directory, { recursive: true });
  const temporary = path.join(
    directory,
    `.one-memory-diagnostics-${randomUUID()}.tmp`,
  );
  try {
    await appendFile(temporary, `${JSON.stringify(payload, null, 2)}\n`, {
      encoding: "utf8",
      mode: 0o600,
      flag: "wx",
    });
    await chmod(temporary, 0o600);
    await rename(temporary, options.target);
    await chmod(options.target, 0o600);
  } catch (error) {
    await rm(temporary, { force: true }).catch(() => undefined);
    throw error;
  }
  return {
    event_count: result.events.length,
    invalid_lines: result.invalid_lines,
  };
}

const SUPPORT_LOG =
  /^(?:diagnostic-(?:runtime|core|host-bridge|desktop|cli)\.jsonl|core\.log|service-error\.log|service\.log|recall-inject\.jsonl)(?:\.\d+)?$/;
const CRASH_REPORT = /^MemHub.*\.(?:ips|crash)$/i;
const PRIVATE_FIELD =
  /(?:authorization|api[_-]?key|token|secret|credential|password)|^(?:prompt|query|content|messages?|memory|memory_text|raw_text)$/i;

export interface DiagnosticMarker {
  timestamp: string;
  description: string;
}

const MARKER_FILE = "problem-markers.jsonl";

export async function recordDiagnosticMarker(
  dataDir: string,
  marker: DiagnosticMarker,
  home = os.homedir(),
): Promise<void> {
  const timestamp = new Date(marker.timestamp);
  if (Number.isNaN(timestamp.getTime())) fail("diagnostic_timestamp_invalid");
  const directory = await ensureDiagnosticDirectory(dataDir);
  const safe = {
    timestamp: timestamp.toISOString(),
    description: redactString(cleanText(marker.description, 500), home),
  };
  await appendFile(
    path.join(directory, MARKER_FILE),
    `${JSON.stringify(safe)}\n`,
    {
      encoding: "utf8",
      mode: 0o600,
    },
  );
}

async function readDiagnosticMarkers(
  dataDir: string,
  home: string,
): Promise<DiagnosticMarker[]> {
  const raw = await readFile(
    path.join(diagnosticDirectory(dataDir), MARKER_FILE),
    "utf8",
  ).catch(() => "");
  return raw
    .split("\n")
    .flatMap((line) => {
      if (!line) return [];
      try {
        const value = JSON.parse(line) as Record<string, unknown>;
        if (
          typeof value.timestamp !== "string" ||
          Number.isNaN(new Date(value.timestamp).getTime()) ||
          typeof value.description !== "string"
        )
          return [];
        return [
          {
            timestamp: new Date(value.timestamp).toISOString(),
            description: redactString(cleanText(value.description, 500), home),
          },
        ];
      } catch {
        return [];
      }
    })
    .slice(-50);
}

export function diagnosticZipFilename(build: string, now = new Date()): string {
  const two = (value: number) => String(value).padStart(2, "0");
  const stamp = `${now.getFullYear()}${two(now.getMonth() + 1)}${two(now.getDate())}-${two(now.getHours())}${two(now.getMinutes())}`;
  const safeBuild = build.trim().replace(/[^0-9A-Za-z._-]+/g, "-") || "unknown";
  return `MemHub-日志-${safeBuild}-${stamp}.zip`;
}

export async function exportDiagnosticZip(options: {
  dataDir: string;
  target: string;
  productVersion: string;
  commit?: string;
  coreSha256?: string;
  home?: string;
  crashReportsDir?: string;
  now?: Date;
  markers?: DiagnosticMarker[];
  summary?: Record<string, unknown>;
}): Promise<{ filename: string; file_count: number }> {
  const now = options.now ?? new Date();
  const home = options.home ?? os.homedir();
  const archive: Record<string, Uint8Array> = {};
  const logsDir = diagnosticDirectory(options.dataDir);
  const cutoff = now.getTime() - 7 * 24 * 60 * 60 * 1000;
  const logEntries = await readdir(logsDir, { withFileTypes: true }).catch(
    () => [],
  );
  for (const entry of logEntries) {
    if (
      !entry.isFile() ||
      entry.isSymbolicLink() ||
      !SUPPORT_LOG.test(entry.name)
    )
      continue;
    const source = path.join(logsDir, entry.name);
    const info = await lstat(source);
    if (!info.isFile() || info.isSymbolicLink() || info.mtimeMs < cutoff)
      continue;
    archive[`logs/${entry.name}`] = strToU8(
      sanitizeDiagnosticText(await readFile(source, "utf8"), home),
    );
  }
  const crashCutoff = now.getTime() - 24 * 60 * 60 * 1000;
  if (options.crashReportsDir) {
    const crashEntries = await readdir(options.crashReportsDir, {
      withFileTypes: true,
    }).catch(() => []);
    for (const entry of crashEntries) {
      if (
        !entry.isFile() ||
        entry.isSymbolicLink() ||
        !CRASH_REPORT.test(entry.name)
      )
        continue;
      const source = path.join(options.crashReportsDir, entry.name);
      const info = await lstat(source);
      if (!info.isFile() || info.isSymbolicLink() || info.mtimeMs < crashCutoff)
        continue;
      archive[`crashes/${entry.name}`] = strToU8(
        sanitizeDiagnosticText(await readFile(source, "utf8"), home),
      );
    }
  }
  const disk = await statfs(options.dataDir).catch(() => null);
  const markers = (
    options.markers ?? (await readDiagnosticMarkers(options.dataDir, home))
  ).map((marker) => ({
    timestamp: new Date(marker.timestamp).toISOString(),
    description: cleanText(marker.description, 500),
  }));
  const summary = sanitizeBundleValue(
    {
      schema_version: 1,
      exported_at: now.toISOString(),
      build: cleanText(options.productVersion, 128),
      commit: options.commit ? cleanText(options.commit, 64) : null,
      core_sha256: options.coreSha256
        ? cleanText(options.coreSha256, 128)
        : null,
      system: {
        macos_version: process.platform === "darwin" ? os.release() : null,
        platform: process.platform,
        arch: process.arch,
        chip: os.cpus()[0]?.model ?? "unknown",
        memory_bytes: os.totalmem(),
        free_disk_bytes: disk ? disk.bavail * disk.bsize : null,
      },
      problem_markers: markers,
      ...(options.summary ?? {}),
    },
    home,
  );
  archive["summary.json"] = strToU8(`${JSON.stringify(summary, null, 2)}\n`);
  archive["README.txt"] = strToU8(
    [
      "MemHub diagnostics",
      "",
      "包含：最近 7 天诊断日志、运行日志、最近 24 小时 MemHub 崩溃报告，以及不含正文的状态摘要。",
      "不包含：对话原文、记忆内容、API Key、token 或宿主凭证。",
      "本机主目录已替换为 ~。",
      ...(markers.length
        ? [
            "",
            "问题时间点：",
            ...markers.map(
              (marker) => `${marker.timestamp}  ${marker.description}`,
            ),
          ]
        : []),
      "",
    ].join("\n"),
  );
  const directory = path.dirname(options.target);
  await mkdir(directory, { recursive: true });
  const temporary = path.join(directory, `.memhub-logs-${randomUUID()}.tmp`);
  try {
    await writeFile(temporary, zipSync(archive, { level: 6 }), {
      mode: 0o600,
      flag: "wx",
    });
    await rename(temporary, options.target);
    await chmod(options.target, 0o600);
  } catch (error) {
    await rm(temporary, { force: true }).catch(() => undefined);
    throw error;
  }
  return {
    filename: path.basename(options.target),
    file_count: Object.keys(archive).length,
  };
}

function sanitizeDiagnosticText(value: string, home: string): string {
  return value
    .split("\n")
    .map((line) => {
      if (!line) return "";
      try {
        return JSON.stringify(sanitizeBundleValue(JSON.parse(line), home));
      } catch {
        return redactString(line, home)
          .replace(
            /\b(?:authorization|api[_-]?key|token|secret|credential|password)\s*[:=]\s*\S+/gi,
            (match) => match.replace(/[:=].*$/, "=[redacted]"),
          )
          .replace(
            /\b(?:content|prompt|query|message|memory_text|raw_text)\s*[:=].*$/gi,
            (match) => match.replace(/[:=].*$/, "=[redacted]"),
          );
      }
    })
    .join("\n");
}

function sanitizeBundleValue(value: unknown, home: string, key = ""): unknown {
  if (PRIVATE_FIELD.test(key)) return "[redacted]";
  if (typeof value === "string") return redactString(value, home);
  if (Array.isArray(value))
    return value.map((item) => sanitizeBundleValue(item, home));
  if (value && typeof value === "object")
    return Object.fromEntries(
      Object.entries(value).map(([childKey, child]) => [
        childKey,
        sanitizeBundleValue(child, home, childKey),
      ]),
    );
  return value;
}

function redactString(value: string, home: string): string {
  const withoutHome = (home ? value.split(home).join("~") : value).replace(
    /\/Users\/[^/\s"']+/g,
    "~",
  );
  return withoutHome
    .replace(/\bBearer\s+[^\s"']+/gi, "Bearer [redacted]")
    .replace(/\b(?:sk|omhg)_[A-Za-z0-9._-]+\b/g, "[redacted]")
    .replace(/\bsk-[A-Za-z0-9._-]+\b/g, "[redacted]");
}

export async function diagnosticLogFiles(dataDir: string): Promise<string[]> {
  const directory = diagnosticDirectory(dataDir);
  const directoryInfo = await lstat(directory).catch(() => null);
  if (directoryInfo?.isSymbolicLink()) return [];
  const entries = await readdir(directory, { withFileTypes: true }).catch(
    () => [],
  );
  const files: Array<{ path: string; mtime: number }> = [];
  for (const entry of entries) {
    if (!entry.isFile() || entry.isSymbolicLink() || !NAME.test(entry.name))
      continue;
    const target = path.join(directory, entry.name);
    const info = await lstat(target);
    if (!info.isFile() || info.isSymbolicLink()) continue;
    files.push({ path: target, mtime: info.mtimeMs });
  }
  return files.sort((a, b) => a.mtime - b.mtime).map((item) => item.path);
}

export async function pruneDiagnosticLogs(
  dataDir: string,
  now = Date.now(),
  maxTotalBytes = DIAGNOSTIC_MAX_TOTAL_BYTES,
): Promise<void> {
  const files = await diagnosticLogFiles(dataDir);
  const retained: Array<{ path: string; size: number; mtime: number }> = [];
  for (const file of files) {
    const info = await stat(file);
    if (now - info.mtimeMs > DIAGNOSTIC_MAX_AGE_MS)
      await rm(file, { force: true });
    else retained.push({ path: file, size: info.size, mtime: info.mtimeMs });
  }
  let total = retained.reduce((sum, item) => sum + item.size, 0);
  for (const item of retained.sort((a, b) => a.mtime - b.mtime)) {
    if (total <= maxTotalBytes) break;
    await rm(item.path, { force: true });
    total -= item.size;
  }
}

export function diagnosticError(error: unknown): {
  error_type: string;
  safe_stack?: string[];
} {
  if (!(error instanceof Error)) return { error_type: "UnknownError" };
  const stack = (error.stack ?? "")
    .split("\n")
    .flatMap((line) => {
      const match = line.match(
        /((?:apps|packages|scripts)\/[A-Za-z0-9_./-]+:\d+:\d+)/,
      );
      return match?.[1] ? [match[1]] : [];
    })
    .slice(0, 16);
  return {
    error_type: cleanErrorType(error.name),
    ...(stack.length ? { safe_stack: stack } : {}),
  };
}

function diagnosticDirectory(dataDir: string): string {
  if (!path.isAbsolute(dataDir) || /[\0\r\n]/.test(dataDir))
    fail("diagnostic_data_directory_invalid");
  return path.join(dataDir, "logs");
}

async function ensureDiagnosticDirectory(dataDir: string): Promise<string> {
  const directory = diagnosticDirectory(dataDir);
  await mkdir(directory, { recursive: true, mode: 0o700 });
  const info = await lstat(directory);
  if (!info.isDirectory() || info.isSymbolicLink())
    fail("diagnostic_directory_unsafe");
  await chmod(directory, 0o700);
  return directory;
}

async function rotate(target: string): Promise<void> {
  await rm(`${target}.3`, { force: true });
  for (let index = 2; index >= 1; index -= 1) {
    await rename(`${target}.${index}`, `${target}.${index + 1}`).catch(
      (error: NodeJS.ErrnoException) => {
        if (error.code !== "ENOENT") throw error;
      },
    );
  }
  await rename(target, `${target}.1`).catch((error: NodeJS.ErrnoException) => {
    if (error.code !== "ENOENT") throw error;
  });
}

function rotateSync(target: string): void {
  rmSync(`${target}.3`, { force: true });
  for (let index = 2; index >= 1; index -= 1) {
    try {
      renameSync(`${target}.${index}`, `${target}.${index + 1}`);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    }
  }
  try {
    renameSync(target, `${target}.1`);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
  }
}

function parseDiagnosticEvent(value: unknown): DiagnosticEventV1 {
  if (!value || typeof value !== "object" || Array.isArray(value))
    fail("diagnostic_record_invalid");
  const item = value as Record<string, unknown>;
  if (item.schema_version !== 1) fail("diagnostic_record_invalid");
  const validated = diagnosticEvent(
    item as unknown as Omit<DiagnosticEventV1, "schema_version">,
  );
  // Core emits RFC 3339 timestamps with microseconds while JS writers emit
  // milliseconds. `diagnosticEvent()` canonicalizes through `Date` and would
  // otherwise truncate the extra digits, making events within one millisecond
  // sort in file-discovery order rather than chronological order.
  if (
    typeof item.timestamp !== "string" ||
    !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$/.test(
      item.timestamp,
    )
  )
    fail("diagnostic_timestamp_invalid");
  return Object.freeze({ ...validated, timestamp: item.timestamp });
}

function compareDiagnosticTimestamps(left: string, right: string): number {
  const key = (value: string): bigint => {
    const milliseconds = Date.parse(value);
    if (!Number.isFinite(milliseconds)) fail("diagnostic_timestamp_invalid");
    const fraction = /\.(\d+)(?:Z|[+-]\d{2}:\d{2})$/.exec(value)?.[1] ?? "";
    const nanoseconds = BigInt((fraction + "000000000").slice(0, 9));
    return (
      BigInt(Math.floor(milliseconds / 1000)) * 1_000_000_000n + nanoseconds
    );
  };
  const leftKey = key(left);
  const rightKey = key(right);
  return leftKey < rightKey ? -1 : leftKey > rightKey ? 1 : 0;
}

function sanitizeDetails(
  value: Record<string, unknown> | undefined,
): Record<string, string | number | boolean | null> | undefined {
  if (!value) return undefined;
  const result: Record<string, string | number | boolean | null> = {};
  for (const [key, item] of Object.entries(value)) {
    if (!DETAIL_KEYS.has(key)) fail("diagnostic_detail_key_invalid");
    if (item === null || typeof item === "boolean") result[key] = item;
    else if (typeof item === "number" && Number.isFinite(item))
      result[key] = item;
    else if (typeof item === "string") result[key] = cleanText(item, 256);
    else fail("diagnostic_detail_value_invalid");
  }
  return Object.keys(result).length ? result : undefined;
}

function safeStack(value: string[]): string[] {
  if (!Array.isArray(value) || value.length > 16)
    fail("diagnostic_stack_invalid");
  return value.map((line) => {
    const cleaned = cleanText(line, 256);
    if (path.isAbsolute(cleaned) || cleaned.includes("file://"))
      fail("diagnostic_stack_invalid");
    return cleaned;
  });
}

function cleanText(value: string, maximum: number): string {
  if (!value || value.length > maximum || /[\0\r\n]/.test(value))
    fail("diagnostic_text_invalid");
  return value;
}

function cleanErrorType(value: string): string {
  return IDENTIFIER.test(value) ? value : "Error";
}

function canonicalUuid(value: string | undefined): string | null {
  if (!value) return null;
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(
    value,
  )
    ? value
    : null;
}

function fail(code: string): never {
  throw new Error(code);
}
