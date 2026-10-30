import { randomUUID } from "node:crypto";
import {
  appendFileSync,
  chmodSync,
  constants,
  lstatSync,
  mkdirSync,
  renameSync,
  rmSync,
  statSync,
} from "node:fs";
import {
  appendFile,
  chmod,
  link,
  lstat,
  mkdir,
  open,
  readdir,
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

export type DiagnosticLevel = "debug" | "info" | "warn" | "error";
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
const CODE_FRAME =
  /^(?:(?:apps|packages|scripts)\/(?:[A-Za-z0-9_-]+\/)*[A-Za-z0-9_.-]+:\d+:\d+|one_memory\/(?:[A-Za-z0-9_-]+\/)*[A-Za-z0-9_.-]+:\d+:[A-Za-z_][A-Za-z0-9_]*|[A-Za-z0-9_][A-Za-z0-9_.-]*\.py:\d+:[A-Za-z_][A-Za-z0-9_]*)$/;
const THREAD_STACK = /^thread\|(.+)$/;
const SHORT_CREDENTIAL_TOKEN =
  /(^|[^A-Za-z0-9])([A-Za-z0-9]{24,47})(?:[_-])?(?=$|[^A-Za-z0-9])/g;
const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/;
const EVENT_NAME = /^[a-z][a-z0-9]*(?:[._-][a-z0-9]+){0,15}$/;
const DETAIL_KEY = /^[A-Za-z][A-Za-z0-9_.-]{0,63}$/;
const DANGEROUS_DETAIL_KEYS = new Set([
  "__proto__",
  "prototype",
  "constructor",
]);
const MAX_DETAIL_KEYS = 32;
const EXPORT_SECRET_MARKER =
  /(?:^|[._:-])(?:authorization|credential|password|prompt|query|secret|token)(?:$|[._:-])/i;
const EXPORTED_STRING_DETAIL_KEYS = new Set([
  "action",
  "method",
  "mode",
  "phase",
  "platform",
  "reason",
  "source_id",
  "status",
  "version",
  "parent_session_id",
  "runtime_state",
  "core_state",
  "broker_state",
  "instance_id",
  "operation_kind",
  "file_kind",
  "last_probe",
  "signal",
]);
const EXPORTED_NUMBER_DETAIL_KEYS = new Set([
  "attempt",
  "count",
  "status_code",
  "result_count",
  "invalid_lines",
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
  "attempts",
  "delay_ms",
  "budget_ms",
  "port",
  "exit_code",
]);
const EXPORTED_BOOLEAN_DETAIL_KEYS = new Set(["spawned", "truncated"]);
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
  try {
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
  } catch {
    // A synchronous fatal-path diagnostic must never replace the business error.
  }
}

export function diagnosticEvent(
  value: Omit<DiagnosticEventV1, "schema_version">,
): DiagnosticEventV1 {
  for (const key of Object.keys(value)) {
    if (!EVENT_KEYS.has(key)) fail("diagnostic_field_invalid");
  }
  if (typeof value.timestamp !== "string") fail("diagnostic_timestamp_invalid");
  const timestamp = new Date(value.timestamp);
  if (Number.isNaN(timestamp.getTime())) fail("diagnostic_timestamp_invalid");
  if (
    typeof value.component !== "string" ||
    typeof value.event !== "string" ||
    !EVENT_NAME.test(value.component) ||
    !EVENT_NAME.test(value.event)
  )
    fail("diagnostic_event_invalid");
  if (!(["debug", "info", "warn", "error"] as const).includes(value.level))
    fail("diagnostic_level_invalid");
  if (
    !(["runtime", "core", "host-bridge", "desktop", "cli"] as const).includes(
      value.service,
    )
  )
    fail("diagnostic_service_invalid");
  if (typeof value.outcome !== "string" || !IDENTIFIER.test(value.outcome))
    fail("diagnostic_outcome_invalid");
  if (typeof value.session_id !== "string" || !canonicalUuid(value.session_id))
    fail("diagnostic_session_invalid");
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
    if (
      item !== undefined &&
      (typeof item !== "string" || !IDENTIFIER.test(item))
    )
      fail("diagnostic_identifier_invalid");
  }
  for (const key of [
    "state_before",
    "state_after",
    "error_code",
    "error_type",
  ] as const) {
    const item = value[key];
    if (
      item !== undefined &&
      (typeof item !== "string" || !IDENTIFIER.test(item))
    )
      fail("diagnostic_text_field_invalid");
  }
  if (
    value.duration_ms !== undefined &&
    (!Number.isSafeInteger(value.duration_ms) || value.duration_ms < 0)
  )
    fail("diagnostic_duration_invalid");
  const details = sanitizeDetails(value.details);
  const stack =
    value.safe_stack === undefined ? undefined : safeStack(value.safe_stack);
  const { details: _details, safe_stack: _safeStack, ...event } = value;
  return Object.freeze({
    schema_version: DIAGNOSTIC_SCHEMA_VERSION,
    ...event,
    timestamp: timestamp.toISOString(),
    ...(details ? { details } : {}),
    ...(stack ? { safe_stack: stack } : {}),
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
    const content = await readRegularFileNoFollow(
      file,
      "diagnostic_file_unsafe",
    );
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
  if (result.invalid_lines > 0) fail("diagnostic_export_invalid_lines");
  const events = result.events.map(projectDiagnosticEventForSafeOutput);
  const payload = {
    schema_version: 1,
    exported_at: new Date().toISOString(),
    product_version: exportProductVersion(options.productVersion),
    platform: process.platform,
    arch: process.arch,
    status: projectExportDetails(options.status),
    event_count: events.length,
    invalid_lines: 0,
    events,
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
    await publishTemporaryFile(temporary, options.target);
  } catch (error) {
    await rm(temporary, { force: true }).catch(() => undefined);
    throw error;
  }
  return {
    event_count: result.events.length,
    invalid_lines: result.invalid_lines,
  };
}

export interface DiagnosticMarker {
  timestamp: string;
  description: string;
}

const MARKER_FILE = "problem-markers.jsonl";
const MARKER_DESCRIPTION = "user_marked_incident";

export async function recordDiagnosticMarker(
  dataDir: string,
  marker: DiagnosticMarker,
  home = os.homedir(),
): Promise<void> {
  const safe = validateDiagnosticMarker(marker, home);
  const directory = await ensureDiagnosticDirectory(dataDir);
  const target = path.join(directory, MARKER_FILE);
  await refuseUnsafeExistingFile(target, "diagnostic_marker_file_unsafe");
  const handle = await open(
    target,
    constants.O_WRONLY |
      constants.O_CREAT |
      constants.O_APPEND |
      (constants.O_NOFOLLOW ?? 0),
    0o600,
  );
  try {
    const info = await handle.stat();
    if (!info.isFile()) fail("diagnostic_marker_file_unsafe");
    await handle.appendFile(`${JSON.stringify(safe)}\n`, "utf8");
    await handle.chmod(0o600);
  } finally {
    await handle.close();
  }
}

async function readDiagnosticMarkers(
  dataDir: string,
  home: string,
): Promise<DiagnosticMarker[]> {
  const target = path.join(diagnosticDirectory(dataDir), MARKER_FILE);
  let raw: string;
  try {
    raw = await readRegularFileNoFollow(
      target,
      "diagnostic_marker_file_unsafe",
    );
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return [];
    throw error;
  }
  const markers: DiagnosticMarker[] = [];
  for (const line of raw.split("\n")) {
    if (!line) continue;
    let value: unknown;
    try {
      value = JSON.parse(line);
    } catch {
      fail("diagnostic_marker_invalid");
    }
    markers.push(validateDiagnosticMarker(value, home));
  }
  return markers.slice(-50);
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
  const cutoff = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);
  const result = await readDiagnosticEvents(options.dataDir, {
    since: cutoff,
    tail: 5_000,
  });
  if (result.invalid_lines > 0) fail("diagnostic_export_invalid_lines");
  const events = result.events.map(projectDiagnosticEventForSafeOutput);
  const markers = options.markers
    ? options.markers
        .map((marker) => validateDiagnosticMarker(marker, home))
        .slice(-50)
    : await readDiagnosticMarkers(options.dataDir, home);
  const disk = await statfs(options.dataDir).catch(() => null);
  const background = projectSummaryBackground(options.summary);
  const summary = {
    schema_version: 1,
    exported_at: now.toISOString(),
    build: exportProductVersion(options.productVersion),
    commit: options.commit ? exportCommit(options.commit) : null,
    core_sha256: options.coreSha256 ? exportSha256(options.coreSha256) : null,
    system: {
      macos_version: process.platform === "darwin" ? os.release() : null,
      platform: process.platform,
      arch: process.arch,
      chip: os.cpus()[0]?.model ?? "unknown",
      memory_bytes: os.totalmem(),
      free_disk_bytes: disk ? disk.bavail * disk.bsize : null,
    },
    problem_markers: markers,
    ...(background ? { background } : {}),
  };
  const eventLines = events.map((event) => JSON.stringify(event)).join("\n");
  const archive: Record<string, Uint8Array> = {
    "diagnostics/events.jsonl": strToU8(
      eventLines.length > 0 ? `${eventLines}\n` : "",
    ),
    "summary.json": strToU8(`${JSON.stringify(summary, null, 2)}\n`),
    "README.txt": strToU8(
      [
        "MemHub diagnostics",
        "",
        "包含：最近 7 天通过安全白名单重新投影的诊断事件，以及不含正文的状态摘要。",
        "不包含：原始日志、崩溃报告、对话原文、记忆内容、API Key、token 或宿主凭证。",
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
    ),
  };
  const directory = path.dirname(options.target);
  await mkdir(directory, { recursive: true });
  const temporary = path.join(directory, `.memhub-logs-${randomUUID()}.tmp`);
  try {
    await writeFile(temporary, zipSync(archive, { level: 6 }), {
      mode: 0o600,
      flag: "wx",
    });
    await chmod(temporary, 0o600);
    await publishTemporaryFile(temporary, options.target);
  } catch (error) {
    await rm(temporary, { force: true }).catch(() => undefined);
    throw error;
  }
  return {
    filename: path.basename(options.target),
    file_count: Object.keys(archive).length,
  };
}

async function publishTemporaryFile(
  temporary: string,
  target: string,
): Promise<void> {
  // A hard link publishes the already-permissioned inode atomically and refuses
  // to overwrite an existing target. That avoids both stale-file confusion and
  // the rename-then-chmod window where a later failure leaves a false success.
  await link(temporary, target);
  await rm(temporary, { force: true }).catch(() => undefined);
}

export function projectDiagnosticEventForSafeOutput(
  event: DiagnosticEventV1,
): DiagnosticEventV1 {
  const details = projectExportDetails(event.details);
  const stack = projectSafeStack(event.safe_stack);
  const optional: Partial<DiagnosticEventV1> = {};
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
    "state_before",
    "state_after",
    "error_code",
    "error_type",
  ] as const) {
    const value = event[key];
    if (value !== undefined && isExportIdentifier(value)) optional[key] = value;
  }
  return {
    schema_version: DIAGNOSTIC_SCHEMA_VERSION,
    timestamp: event.timestamp,
    level: event.level,
    service: event.service,
    component: isExportIdentifier(event.component)
      ? event.component
      : "diagnostic.redacted",
    event: isExportIdentifier(event.event)
      ? event.event
      : "diagnostic.redacted",
    outcome: isExportIdentifier(event.outcome) ? event.outcome : "redacted",
    session_id: event.session_id,
    ...optional,
    ...(event.duration_ms === undefined ||
    !Number.isSafeInteger(event.duration_ms)
      ? {}
      : { duration_ms: event.duration_ms }),
    ...(stack === undefined ? {} : { safe_stack: stack }),
    ...(details === undefined ? {} : { details }),
  };
}

function projectExportDetails(
  value: Record<string, unknown> | undefined,
): Record<string, string | number | boolean | null> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value))
    return undefined;
  const result: Record<string, string | number | boolean | null> = {};
  for (const [key, item] of Object.entries(value)) {
    if (
      EXPORTED_STRING_DETAIL_KEYS.has(key) &&
      typeof item === "string" &&
      isExportIdentifier(item)
    ) {
      result[key] = item;
    } else if (
      EXPORTED_NUMBER_DETAIL_KEYS.has(key) &&
      typeof item === "number" &&
      isJsonSafeNumber(item)
    ) {
      result[key] = item;
    } else if (
      EXPORTED_BOOLEAN_DETAIL_KEYS.has(key) &&
      typeof item === "boolean"
    ) {
      result[key] = item;
    }
  }
  return Object.keys(result).length > 0 ? result : undefined;
}

function validateDiagnosticMarker(
  value: unknown,
  _home: string,
): DiagnosticMarker {
  if (!value || typeof value !== "object" || Array.isArray(value))
    fail("diagnostic_marker_invalid");
  const marker = value as Record<string, unknown>;
  const keys = Object.keys(marker);
  if (
    keys.length !== 2 ||
    !keys.includes("timestamp") ||
    !keys.includes("description") ||
    typeof marker.timestamp !== "string" ||
    typeof marker.description !== "string"
  )
    fail("diagnostic_marker_invalid");
  const timestamp = new Date(marker.timestamp);
  if (Number.isNaN(timestamp.getTime())) fail("diagnostic_marker_invalid");
  return {
    timestamp: timestamp.toISOString(),
    description: redactString(MARKER_DESCRIPTION, _home),
  };
}

function projectSummaryBackground(
  summary: Record<string, unknown> | undefined,
): Record<string, string> | undefined {
  if (!summary || typeof summary !== "object" || Array.isArray(summary))
    return undefined;
  const candidate = summary.background;
  if (!candidate || typeof candidate !== "object" || Array.isArray(candidate))
    return undefined;
  const source = candidate as Record<string, unknown>;
  const background: Record<string, string> = {};
  for (const key of [
    "status",
    "runtime_state",
    "core_state",
    "broker_state",
    "instance_id",
  ]) {
    const value = source[key];
    if (typeof value === "string" && isExportIdentifier(value))
      background[key] = value;
  }
  return Object.keys(background).length > 0 ? background : undefined;
}

function redactString(value: string, home: string): string {
  const withoutHome = (home ? value.split(home).join("~") : value)
    .replace(/\/Users\/[^/\s"']+/g, "~")
    .replace(/\b[A-Za-z]:\\Users\\[^\\\s"']+/g, "~");
  return withoutHome
    .replace(/\bBearer\s+[^\s"']+/gi, "Bearer [redacted]")
    .replace(/\b(?:sk|omhg|ghp|github_pat)_[A-Za-z0-9._-]+\b/gi, "[redacted]")
    .replace(/\bsk-[A-Za-z0-9._-]+\b/gi, "[redacted]")
    .replace(/\bxox[baprs]-[A-Za-z0-9-]+\b/gi, "[redacted]")
    .replace(/\b(?:AKIA|ASIA)[A-Z0-9]{16}\b/g, "[redacted]")
    .replace(/\bAKID[A-Za-z0-9]{12,}\b/g, "[redacted]")
    .replace(/\bAIza[A-Za-z0-9_-]{20,}\b/g, "[redacted]")
    .replace(/\bGOCSPX-[A-Za-z0-9_-]+\b/g, "[redacted]")
    .replace(SHORT_CREDENTIAL_TOKEN, (match, prefix: string, token: string) =>
      isConservativeShortCredential(token) ? `${prefix}[redacted]` : match,
    )
    .replace(
      /\b[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b/g,
      "[redacted]",
    )
    .replace(
      /\b(?=[A-Za-z0-9._-]{48,}\b)(?=[A-Za-z0-9._-]*[A-Za-z])(?=[A-Za-z0-9._-]*\d)[A-Za-z0-9._-]+\b/g,
      "[redacted]",
    )
    .replace(
      /\b(api[_-]?key|authorization|credential|password|secret|token)\s*[:=]\s*[^\s,;"']+/gi,
      "$1=[redacted]",
    );
}

async function refuseUnsafeExistingFile(
  target: string,
  code: string,
): Promise<void> {
  try {
    const info = await lstat(target);
    if (!info.isFile() || info.isSymbolicLink()) fail(code);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
  }
}

async function readRegularFileNoFollow(
  target: string,
  code: string,
): Promise<string> {
  await refuseUnsafeExistingFile(target, code);
  const handle = await open(
    target,
    constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0),
  );
  try {
    const info = await handle.stat();
    if (!info.isFile()) fail(code);
    return await handle.readFile("utf8");
  } finally {
    await handle.close();
  }
}

export async function diagnosticLogFiles(dataDir: string): Promise<string[]> {
  const directory = diagnosticDirectory(dataDir);
  let directoryInfo: Awaited<ReturnType<typeof lstat>>;
  try {
    directoryInfo = await lstat(directory);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return [];
    throw error;
  }
  if (!directoryInfo.isDirectory() || directoryInfo.isSymbolicLink())
    fail("diagnostic_directory_unsafe");
  const entries = await readdir(directory, { withFileTypes: true });
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
    const nanoseconds = BigInt(`${fraction}000000000`.slice(0, 9));
    return (
      BigInt(Math.floor(milliseconds / 1000)) * 1_000_000_000n + nanoseconds
    );
  };
  const leftKey = key(left);
  const rightKey = key(right);
  return leftKey < rightKey ? -1 : leftKey > rightKey ? 1 : 0;
}

function containsCredentialMaterial(value: string): boolean {
  return (
    /(?:^|[^A-Za-z0-9])(?:sk-|sk_|omhg_|ghp_|github_pat_)[A-Za-z0-9._-]+/i.test(
      value,
    ) ||
    /(?:^|[^A-Za-z0-9])xox[baprs]-[A-Za-z0-9-]+/i.test(value) ||
    /(?:^|[^A-Za-z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Za-z0-9])/.test(value) ||
    /(?:^|[^A-Za-z0-9])AKID[A-Za-z0-9]{12,}/i.test(value) ||
    /(?:^|[^A-Za-z0-9])AIza[A-Za-z0-9_-]{20,}/.test(value) ||
    /(?:^|[^A-Za-z0-9])GOCSPX-[A-Za-z0-9_-]+/.test(value) ||
    hasConservativeShortCredential(value) ||
    /\b[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b/.test(
      value,
    ) ||
    /\b(?=[A-Za-z0-9._-]{48,}\b)(?=[A-Za-z0-9._-]*[A-Za-z])(?=[A-Za-z0-9._-]*\d)[A-Za-z0-9._-]+\b/.test(
      value,
    )
  );
}

function hasConservativeShortCredential(value: string): boolean {
  for (const match of value.matchAll(SHORT_CREDENTIAL_TOKEN)) {
    if (match[2] && isConservativeShortCredential(match[2])) return true;
  }
  return false;
}

function isConservativeShortCredential(value: string): boolean {
  return (
    /[A-Z]/.test(value) &&
    /[a-z]/.test(value) &&
    /\d/.test(value) &&
    !/^[A-Fa-f0-9]+$/.test(value)
  );
}

function looksLikeCredential(value: string): boolean {
  return containsCredentialMaterial(value);
}

function isExportIdentifier(value: string): boolean {
  return (
    IDENTIFIER.test(value) &&
    !EXPORT_SECRET_MARKER.test(value) &&
    !looksLikeCredential(value)
  );
}

function isJsonSafeNumber(value: number): boolean {
  return (
    Number.isFinite(value) &&
    (!Number.isInteger(value) || Number.isSafeInteger(value))
  );
}

function sanitizeDetails(
  value: Record<string, unknown> | undefined,
): Record<string, string | number | boolean | null> | undefined {
  if (value === undefined) return undefined;
  if (!value || typeof value !== "object" || Array.isArray(value))
    fail("diagnostic_details_invalid");
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null)
    fail("diagnostic_detail_key_invalid");
  const keys = Reflect.ownKeys(value);
  if (keys.length > MAX_DETAIL_KEYS) fail("diagnostic_detail_count_invalid");
  if (keys.some((key) => typeof key !== "string"))
    fail("diagnostic_detail_key_invalid");
  const result: Record<string, string | number | boolean | null> = {};
  for (const [key, item] of Object.entries(value)) {
    if (!DETAIL_KEY.test(key) || DANGEROUS_DETAIL_KEYS.has(key))
      fail("diagnostic_detail_key_invalid");
    if (item === null || typeof item === "boolean") result[key] = item;
    else if (typeof item === "number" && isJsonSafeNumber(item))
      result[key] = item;
    else if (typeof item === "string") result[key] = cleanText(item, 256);
    else fail("diagnostic_detail_value_invalid");
  }
  return Object.keys(result).length ? result : undefined;
}

function projectSafeStack(value: string[] | undefined): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const result = value
    .filter((line) => typeof line === "string" && isSafeStackLine(line))
    .slice(0, 16);
  return result.length > 0 ? result : undefined;
}

function safeStack(value: string[]): string[] {
  if (!Array.isArray(value) || value.length > 16)
    fail("diagnostic_stack_invalid");
  return value.map((line) => {
    if (typeof line !== "string" || !isSafeStackLine(line))
      fail("diagnostic_stack_invalid");
    return line;
  });
}

function isSafeStackLine(value: string): boolean {
  if (
    value.length < 1 ||
    value.length > 256 ||
    /[\0\r\n]/.test(value) ||
    containsCredentialMaterial(value)
  )
    return false;
  if (CODE_FRAME.test(value)) return true;
  const aggregate = THREAD_STACK.exec(value)?.[1];
  return (
    aggregate?.split(" > ").every((frame) => CODE_FRAME.test(frame)) === true
  );
}

function exportProductVersion(value: string): string {
  const cleaned = cleanText(value, 128);
  if (!isExportIdentifier(cleaned)) fail("diagnostic_product_version_invalid");
  return cleaned;
}

function exportCommit(value: string): string {
  const cleaned = cleanText(value, 64);
  if (!/^[0-9a-f]{7,64}$/i.test(cleaned)) fail("diagnostic_commit_invalid");
  return cleaned;
}

function exportSha256(value: string): string {
  const cleaned = cleanText(value, 64);
  if (!/^[0-9a-f]{64}$/i.test(cleaned)) fail("diagnostic_sha256_invalid");
  return cleaned;
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
