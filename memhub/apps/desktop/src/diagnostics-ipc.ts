import { lstat, open, readdir } from "node:fs/promises";
import path from "node:path";
import {
  type DiagnosticEventV1,
  type DiagnosticLogger,
  diagnosticEvent,
  diagnosticZipFilename,
  exportDiagnosticZip,
  inspectBackgroundHealth,
  projectDiagnosticEventForSafeOutput,
  recordDiagnosticMarker,
} from "@one-memory/runtime-control";
import type { BrowserWindow, IpcMainInvokeEvent } from "electron";

const MARK = "one-memory:mark-diagnostic-incident";
const EXPORT = "one-memory:export-diagnostics";
const UI_TRACE = "one-memory:log-ui-trace";
const DEBUG_LOGS = "one-memory:get-debug-logs";
const DEBUG_LOG_NAME =
  /^diagnostic-(runtime|core|host-bridge|desktop|cli)\.jsonl(?:\.\d+)?$/;
const SERVICE_LOG_NAMES = [
  "service.log",
  "service-error.log",
  "core.log",
] as const;

interface ServiceLogCursor {
  name: (typeof SERVICE_LOG_NAMES)[number];
  files: Array<{ device: number; inode: number; offset: number }>;
}

interface PipelineLogCursor {
  relativePath: string;
  device: number;
  inode: number;
  offset: number;
}

export interface DebugLogCursor {
  diagnosticLogs: Array<{ device: number; inode: number; offset: number }>;
  serviceLogs: ServiceLogCursor[];
  pipelineLogs: PipelineLogCursor[];
}

export interface DebugLogEntry {
  id: string;
  timestamp: string;
  level: "debug" | "info" | "warn" | "error";
  service: "runtime" | "core" | "host-bridge" | "desktop" | "cli" | "hy-memory";
  component: string;
  event: string;
  outcome: string;
  duration_ms?: number;
  error_code?: string;
  error_type?: string;
  trace_id?: string;
  details?: Record<string, string | number | boolean | null>;
  message?: string;
  source: "diagnostic" | "stdout" | "stderr" | "pipeline";
}

export interface DebugLogSnapshot {
  started_at: string;
  read_at: string;
  entries: DebugLogEntry[];
  truncated: boolean;
}

/** The renderer's two ends of the four-step chain.
 *
 * Values are re-validated here rather than trusted: the renderer is the least
 * privileged caller, and a trace is only useful if every line is well formed.
 * `component` stays `renderer.ui` (the logger's service is `desktop`), so no
 * shared enum has to grow a new member.
 */
const UI_ACTIONS = new Set([
  "click",
  "toggle",
  "submit",
  "type",
  "expand",
  "navigate",
  "retry",
  "cancel",
  "confirm",
  "open",
  "close",
]);
const UI_PAGES = new Set([
  "sources",
  "memory",
  "applications",
  "calls",
  "settings",
  "health",
  "matters",
  "onboarding",
]);
const UI_PHASES = new Set(["action", "result"]);
const UI_OUTCOMES = new Set(["succeeded", "failed", "rejected", "unknown"]);
const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const TRACE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/;

/** Shape the renderer's trace into one diagnostic event.
 *
 * Exported so the mapping is testable on its own, and so main.ts does not
 * re-derive it: the two ends must render identically or a chain cannot be read
 * in order.
 */
export function uiTraceDiagnosticInput(input: {
  action: string;
  page: string;
  phase: string;
  status?: string;
  reason?: string;
  count?: number;
  duration_ms?: number;
  trace_id?: string;
}) {
  return {
    level: "info" as const,
    component: "renderer.ui",
    event: input.phase === "action" ? "ui.action" : "ui.result",
    outcome: input.phase === "action" ? "started" : (input.status ?? "unknown"),
    ...(input.trace_id ? { trace_id: input.trace_id } : {}),
    details: {
      action: input.action,
      phase: input.phase,
      // `page` is not in the shared detail whitelist; `mode` is, and is the
      // closest "which surface" field. A BLOCK asks C0 to add `page`.
      mode: input.page,
      ...(input.status ? { status: input.status } : {}),
      ...(input.reason ? { reason: input.reason } : {}),
      ...(input.count === undefined ? {} : { count: input.count }),
    },
    ...(input.duration_ms === undefined
      ? {}
      : { duration_ms: input.duration_ms }),
  };
}

export interface DiagnosticIpcOptions {
  ipc: {
    handle(
      name: string,
      handler: (
        event: IpcMainInvokeEvent,
        ...args: unknown[]
      ) => Promise<unknown>,
    ): unknown;
    removeHandler(name: string): void;
  };
  dataDir: string;
  productVersion: string;
  buildNumber?: string;
  commit?: string;
  coreSha256?: string;
  home: string;
  downloadsDirectory: string;
  crashReportsDirectory: string;
  logger: DiagnosticLogger;
  startedAt: number;
  debugLogCursor: DebugLogCursor;
  requireTrustedWindow(event: IpcMainInvokeEvent): BrowserWindow;
  /** Writes the renderer's ui-action / ui-result lines. */
  logUiTrace?: (input: {
    action: string;
    page: string;
    phase: string;
    status?: string;
    reason?: string;
    count?: number;
    duration_ms?: number;
    trace_id?: string;
  }) => Promise<void>;
  revealItem(target: string): void;
  getSummary?(): Promise<Record<string, unknown>>;
}

type DiagnosticExportOptions = Omit<
  DiagnosticIpcOptions,
  "ipc" | "requireTrustedWindow" | "logUiTrace" | "startedAt" | "debugLogCursor"
>;

export function resolveDiagnosticBuildIdentity(options: {
  environmentBuild?: string;
  environmentCommit?: string;
  embeddedBuild?: string;
  embeddedCommit?: string;
  productVersion: string;
}): { buildNumber: string; commit?: string } {
  const clean = (value: string | undefined) => {
    const candidate = value?.trim();
    return candidate && candidate !== "unknown" ? candidate : undefined;
  };
  const buildNumber =
    clean(options.environmentBuild) ??
    clean(options.embeddedBuild) ??
    options.productVersion;
  const commit =
    clean(options.environmentCommit) ?? clean(options.embeddedCommit);
  return { buildNumber, ...(commit === undefined ? {} : { commit }) };
}

export async function exportDesktopDiagnostics(
  options: DiagnosticExportOptions,
) {
  const build = options.buildNumber ?? options.productVersion;
  const target = path.join(
    options.downloadsDirectory,
    diagnosticZipFilename(build),
  );
  const health = await inspectBackgroundHealth({
    dataDir: options.dataDir,
  }).catch(() => null);
  const result = await exportDiagnosticZip({
    dataDir: options.dataDir,
    target,
    productVersion: build,
    ...(options.commit === undefined ? {} : { commit: options.commit }),
    ...(options.coreSha256 === undefined
      ? {}
      : { coreSha256: options.coreSha256 }),
    home: options.home,
    crashReportsDir: options.crashReportsDirectory,
    summary: {
      ...(await options.getSummary?.().catch(() => ({}))),
      background: health
        ? {
            status: "exported",
            runtime_state: health.state,
            core_state: health.core,
            broker_state: health.broker,
            ...(health.instanceId ? { instance_id: health.instanceId } : {}),
          }
        : { status: "unavailable" },
    },
  });
  await options.logger.log({
    level: "info",
    component: "desktop.diagnostics",
    event: "diagnostics.exported",
    outcome: "succeeded",
    details: { count: result.file_count },
  });
  options.revealItem(target);
  return {
    state: "exported" as const,
    filename: path.basename(target),
    path: target,
    file_count: result.file_count,
  };
}

export async function readDebugLogSnapshot(
  dataDir: string,
  startedAt: number,
  cursor: DebugLogCursor = {
    diagnosticLogs: [],
    serviceLogs: [],
    pipelineLogs: [],
  },
): Promise<DebugLogSnapshot> {
  const started = Number.isFinite(startedAt) ? startedAt : Date.now();
  const directory = path.join(dataDir, "logs");
  const directoryInfo = await lstat(directory).catch(() => null);
  const standardLogsAvailable = Boolean(
    directoryInfo?.isDirectory() && !directoryInfo.isSymbolicLink(),
  );
  const names = standardLogsAvailable
    ? (await readdir(directory)).filter((name) => DEBUG_LOG_NAME.test(name))
    : [];
  const entries: DebugLogEntry[] = [];
  const diagnosticBaseline = new Map(
    cursor.diagnosticLogs.map((file) => [
      `${file.device}:${file.inode}`,
      file.offset,
    ]),
  );
  for (const name of names) {
    const target = path.join(directory, name);
    const info = await safeRegularFile(target);
    if (info === null) continue;
    const offset = diagnosticBaseline.get(`${info.dev}:${info.ino}`) ?? 0;
    if (info.size <= offset) continue;
    const content = await readFileFromOffset(target, offset).catch(() => "");
    let lineNumber = 0;
    for (const line of content.split("\n")) {
      lineNumber += 1;
      if (!line.trim()) continue;
      const entry = debugLogEntry(line, name, lineNumber, started);
      if (entry) entries.push(entry);
    }
  }
  if (standardLogsAvailable) {
    for (const serviceCursor of cursor.serviceLogs) {
      entries.push(
        ...(await readServiceLogEntries(directory, serviceCursor, started)),
      );
    }
  }
  entries.push(...(await readPipelineLogEntries(dataDir, cursor, started)));
  entries.sort((left, right) =>
    left.timestamp === right.timestamp
      ? left.id.localeCompare(right.id)
      : left.timestamp.localeCompare(right.timestamp),
  );
  return {
    started_at: new Date(started).toISOString(),
    read_at: new Date().toISOString(),
    entries,
    truncated: false,
  };
}

export async function captureDebugLogCursor(
  dataDir: string,
): Promise<DebugLogCursor> {
  const directory = path.join(dataDir, "logs");
  const names = await readdir(directory).catch(() => []);
  const pipelineRoot = path.join(dataDir, "hy-memory", "governed-v2", "logs");
  return {
    diagnosticLogs: (
      await Promise.all(
        names
          .filter((name) => DEBUG_LOG_NAME.test(name))
          .map(async (name) => {
            const info = await safeRegularFile(path.join(directory, name));
            return info
              ? { device: info.dev, inode: info.ino, offset: info.size }
              : null;
          }),
      )
    ).filter((value) => value !== null),
    serviceLogs: await Promise.all(
      SERVICE_LOG_NAMES.map(async (name): Promise<ServiceLogCursor> => {
        return {
          name,
          files: (
            await Promise.all(
              [3, 2, 1, 0].map(async (suffix) => {
                const info = await safeRegularFile(
                  path.join(
                    directory,
                    suffix === 0 ? name : `${name}.${suffix}`,
                  ),
                );
                return info
                  ? { device: info.dev, inode: info.ino, offset: info.size }
                  : null;
              }),
            )
          ).filter((value) => value !== null),
        };
      }),
    ),
    pipelineLogs: await pipelineLogFiles(pipelineRoot),
  };
}

function debugLogEntry(
  line: string,
  filename: string,
  lineNumber: number,
  startedAt: number,
): DebugLogEntry | null {
  let value: Record<string, unknown>;
  try {
    value = JSON.parse(line) as Record<string, unknown>;
  } catch {
    return null;
  }
  const timestamp = typeof value.timestamp === "string" ? value.timestamp : "";
  const timestampMs = Date.parse(timestamp);
  if (!Number.isFinite(timestampMs) || timestampMs < startedAt) return null;
  let projected: DiagnosticEventV1;
  try {
    projected = projectDiagnosticEventForSafeOutput(
      diagnosticEvent({
        timestamp,
        level: value.level as never,
        service: value.service as never,
        component: value.component as never,
        event: value.event as never,
        outcome: value.outcome as never,
        session_id: value.session_id as never,
        ...(typeof value.trace_id === "string"
          ? { trace_id: value.trace_id }
          : {}),
        ...(typeof value.duration_ms === "number"
          ? { duration_ms: value.duration_ms }
          : {}),
        ...(typeof value.error_code === "string"
          ? { error_code: value.error_code }
          : {}),
        ...(typeof value.error_type === "string"
          ? { error_type: value.error_type }
          : {}),
        ...(value.details && typeof value.details === "object"
          ? { details: value.details as never }
          : {}),
      }),
    );
  } catch {
    return null;
  }
  return {
    id: `${filename}:${lineNumber}:${timestamp}`,
    timestamp: projected.timestamp,
    level: projected.level,
    service: projected.service,
    component: projected.component,
    event: projected.event,
    outcome: projected.outcome,
    ...(projected.duration_ms === undefined
      ? {}
      : { duration_ms: projected.duration_ms }),
    ...(projected.error_code === undefined
      ? {}
      : { error_code: projected.error_code }),
    ...(projected.error_type === undefined
      ? {}
      : { error_type: projected.error_type }),
    ...(projected.trace_id === undefined
      ? {}
      : { trace_id: projected.trace_id }),
    ...(projected.details === undefined ? {} : { details: projected.details }),
    source: "diagnostic",
  };
}

async function readServiceLogEntries(
  directory: string,
  cursor: ServiceLogCursor,
  startedAt: number,
): Promise<DebugLogEntry[]> {
  const candidates = [3, 2, 1, 0].map((suffix) => ({
    suffix,
    path: path.join(
      directory,
      suffix === 0 ? cursor.name : `${cursor.name}.${suffix}`,
    ),
  }));
  const files = [];
  for (const candidate of candidates) {
    const info = await safeRegularFile(candidate.path);
    if (info) files.push({ ...candidate, info });
  }
  const baseline = new Map(
    cursor.files.map((file) => [`${file.device}:${file.inode}`, file.offset]),
  );
  const entries: DebugLogEntry[] = [];
  for (const file of files) {
    const offset = baseline.get(`${file.info.dev}:${file.info.ino}`) ?? 0;
    if (file.info.size <= offset) continue;
    const content = await readFileFromOffset(file.path, offset);
    let lineIndex = 0;
    for (const line of content.split("\n")) {
      lineIndex += 1;
      const message = line.trimEnd();
      if (!message) continue;
      const level = serviceLogLevel(message, cursor.name);
      // Raw stdout is an implementation trace, not a product event. Core's
      // structured diagnostics and HY pipeline logs already carry meaningful
      // successful work with request/task identity. Showing raw INFO/DEBUG
      // here duplicated those events and turned polling access logs, file
      // watchers and library initialization into thousands of fake "useful"
      // rows. Keep raw output only when it needs attention.
      if (level === "info" || level === "debug") continue;
      entries.push({
        id: `${cursor.name}:${file.info.dev}:${file.info.ino}:${offset}:${lineIndex}`,
        timestamp: logLineTimestamp(message, startedAt, file.info.mtimeMs),
        level,
        service: "core",
        component:
          cursor.name === "core.log"
            ? "core.file"
            : cursor.name === "service-error.log"
              ? "service.stderr"
              : "service.stdout",
        event: "service.output",
        outcome: level === "error" ? "failed" : "observed",
        message,
        source: cursor.name === "service-error.log" ? "stderr" : "stdout",
      });
    }
  }
  return entries;
}

async function safeRegularFile(target: string) {
  const info = await lstat(target).catch(() => null);
  return info?.isFile() && !info.isSymbolicLink() ? info : null;
}

async function readFileFromOffset(
  target: string,
  offset: number,
): Promise<string> {
  const handle = await open(target, "r");
  try {
    const info = await handle.stat();
    const length = Math.max(0, info.size - offset);
    const buffer = Buffer.alloc(length);
    await handle.read(buffer, 0, length, offset);
    return buffer.toString("utf8");
  } finally {
    await handle.close();
  }
}

function serviceLogLevel(
  message: string,
  name: (typeof SERVICE_LOG_NAMES)[number],
): DebugLogEntry["level"] {
  if (/\b(error|critical|fatal|traceback|exception)\b/i.test(message))
    return "error";
  if (/\bwarn(?:ing)?\b/i.test(message)) return "warn";
  if (/\bdebug\b/i.test(message)) return "debug";
  return name === "service-error.log" && !/^INFO\b/.test(message)
    ? "warn"
    : "info";
}

function logLineTimestamp(
  message: string,
  startedAt: number,
  fallback: number,
): string {
  const match =
    /^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})(?:[.,](\d{1,6}))?\b/.exec(
      message,
    );
  if (match) {
    const fraction = (match[3] ?? "").padEnd(3, "0").slice(0, 3);
    const parsed = Date.parse(
      `${match[1]}T${match[2]}${fraction ? `.${fraction}` : ""}`,
    );
    if (Number.isFinite(parsed))
      return new Date(Math.max(startedAt, parsed)).toISOString();
  }
  return new Date(Math.max(startedAt, fallback)).toISOString();
}

async function pipelineLogFiles(root: string): Promise<PipelineLogCursor[]> {
  const rootInfo = await lstat(root).catch(() => null);
  if (!rootInfo?.isDirectory() || rootInfo.isSymbolicLink()) return [];
  const files: PipelineLogCursor[] = [];
  const visit = async (directory: string): Promise<void> => {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const target = path.join(directory, entry.name);
      if (entry.isSymbolicLink()) continue;
      if (entry.isDirectory()) {
        await visit(target);
        continue;
      }
      if (!entry.isFile() || !entry.name.endsWith(".log")) continue;
      const info = await safeRegularFile(target);
      if (!info) continue;
      files.push({
        relativePath: path.relative(root, target),
        device: info.dev,
        inode: info.ino,
        offset: info.size,
      });
    }
  };
  await visit(root);
  return files;
}

async function readPipelineLogEntries(
  dataDir: string,
  cursor: DebugLogCursor,
  startedAt: number,
): Promise<DebugLogEntry[]> {
  const root = path.join(dataDir, "hy-memory", "governed-v2", "logs");
  const current = await pipelineLogFiles(root);
  const baseline = new Map(
    cursor.pipelineLogs.map((file) => [
      `${file.device}:${file.inode}`,
      file.offset,
    ]),
  );
  const entries: DebugLogEntry[] = [];
  for (const file of current) {
    const target = path.join(root, file.relativePath);
    const offset = baseline.get(`${file.device}:${file.inode}`) ?? 0;
    const info = await safeRegularFile(target);
    if (!info || info.size <= offset) continue;
    const content = await readFileFromOffset(target, offset).catch(() => "");
    let lineNumber = 0;
    for (const line of content.split("\n")) {
      lineNumber += 1;
      if (!line.trim()) continue;
      const entry = pipelineLogEntry(
        line,
        file,
        lineNumber,
        offset,
        startedAt,
        info.mtimeMs,
      );
      if (entry) entries.push(entry);
    }
  }
  return entries;
}

function pipelineLogEntry(
  line: string,
  file: PipelineLogCursor,
  lineNumber: number,
  offset: number,
  startedAt: number,
  fallbackTime: number,
): DebugLogEntry | null {
  let value: Record<string, unknown>;
  try {
    value = JSON.parse(line) as Record<string, unknown>;
  } catch {
    return null;
  }
  const step = typeof value.step === "string" ? value.step.trim() : "";
  if (!step || !/^[A-Za-z0-9_-]{1,80}$/.test(step)) return null;
  const rawTimestamp =
    typeof value.timestamp === "string" ? value.timestamp : "";
  const parsedTimestamp = Date.parse(rawTimestamp);
  const timestamp = new Date(
    Math.max(
      startedAt,
      Number.isFinite(parsedTimestamp) ? parsedTimestamp : fallbackTime,
    ),
  ).toISOString();
  const elapsed = safeFiniteNumber(value.elapsed_ms);
  const tokens = safeFiniteNumber(value.tokens);
  const requestId =
    typeof value.request_id === "string" && value.request_id.length <= 256
      ? value.request_id
      : undefined;
  return {
    id: `hy-pipeline:${file.device}:${file.inode}:${offset}:${lineNumber}`,
    timestamp,
    level: "info",
    service: "hy-memory",
    component: "hy.pipeline",
    event: `hy.pipeline.${step.toLowerCase().replaceAll("_", "-")}`,
    outcome: "observed",
    ...(elapsed === undefined ? {} : { duration_ms: elapsed }),
    ...(requestId ? { trace_id: requestId } : {}),
    details: {
      phase: step,
      ...(tokens === undefined ? {} : { count: tokens }),
    },
    message: [
      step,
      ...(tokens === undefined ? [] : [`${Math.round(tokens)} tokens`]),
      ...(elapsed === undefined ? [] : [`${Math.round(elapsed)} ms`]),
    ].join(" · "),
    source: "pipeline",
  };
}

function safeFiniteNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value)
    ? value
    : undefined;
}

export function installDiagnosticsIpc(
  options: DiagnosticIpcOptions,
): () => void {
  options.ipc.handle(DEBUG_LOGS, async (event, ...args) => {
    if (args.length) throw new Error("diagnostic_arguments_rejected");
    options.requireTrustedWindow(event);
    return readDebugLogSnapshot(
      options.dataDir,
      options.startedAt,
      options.debugLogCursor,
    );
  });
  options.ipc.handle(UI_TRACE, async (event, ...args) => {
    // One argument, and it must be an object: a renderer that sends extra
    // positional values is not the renderer this channel was opened for.
    if (args.length !== 1 || !args[0] || typeof args[0] !== "object")
      throw new Error("ui_trace_arguments_rejected");
    options.requireTrustedWindow(event);
    const raw = args[0] as Record<string, unknown>;
    const action = raw.action;
    const page = raw.page;
    const phase = raw.phase;
    if (typeof action !== "string" || !UI_ACTIONS.has(action))
      throw new Error("ui_trace_action_rejected");
    if (typeof page !== "string" || !UI_PAGES.has(page))
      throw new Error("ui_trace_page_rejected");
    if (typeof phase !== "string" || !UI_PHASES.has(phase))
      throw new Error("ui_trace_phase_rejected");
    const status = raw.status;
    if (
      status !== undefined &&
      (typeof status !== "string" || !UI_OUTCOMES.has(status))
    )
      throw new Error("ui_trace_status_rejected");
    const reason = raw.reason;
    if (
      reason !== undefined &&
      (typeof reason !== "string" || !SAFE_ID.test(reason))
    )
      throw new Error("ui_trace_reason_rejected");
    const count = raw.count;
    if (
      count !== undefined &&
      (typeof count !== "number" || !Number.isInteger(count) || count < 0)
    )
      throw new Error("ui_trace_count_rejected");
    const duration = raw.duration_ms;
    if (
      duration !== undefined &&
      (typeof duration !== "number" ||
        !Number.isSafeInteger(duration) ||
        duration < 0)
    )
      throw new Error("ui_trace_duration_rejected");
    const traceId = raw.trace_id;
    if (
      traceId !== undefined &&
      (typeof traceId !== "string" || !TRACE_ID.test(traceId))
    )
      throw new Error("ui_trace_trace_id_rejected");
    const writer = options.logUiTrace;
    if (!writer) throw new Error("ui_trace_unavailable");
    await writer({
      action,
      page,
      phase,
      ...(status === undefined ? {} : { status }),
      ...(reason === undefined ? {} : { reason }),
      ...(count === undefined ? {} : { count }),
      ...(duration === undefined ? {} : { duration_ms: duration }),
      ...(traceId === undefined ? {} : { trace_id: traceId }),
    });
    return { state: "logged" } as const;
  });

  options.ipc.handle(MARK, async (event, ...args) => {
    if (
      args.length > 1 ||
      (args[0] !== undefined && typeof args[0] !== "string")
    )
      throw new Error("diagnostic_arguments_rejected");
    options.requireTrustedWindow(event);
    let status = "unknown";
    let instanceId: string | undefined;
    try {
      const health = await inspectBackgroundHealth({
        dataDir: options.dataDir,
      });
      status = health.state;
      instanceId = health.instanceId;
    } catch {
      status = "unavailable";
    }
    const marker = await options.logger.markIncident({
      component: "desktop.diagnostics",
      status,
      ...(instanceId ? { instanceId } : {}),
    });
    const description = typeof args[0] === "string" ? args[0].trim() : "";
    if (description)
      await recordDiagnosticMarker(
        options.dataDir,
        {
          timestamp: marker.timestamp,
          description,
        },
        options.home,
      );
    return marker;
  });

  options.ipc.handle(EXPORT, async (event, ...args) => {
    if (args.length) throw new Error("diagnostic_arguments_rejected");
    options.requireTrustedWindow(event);
    return exportDesktopDiagnostics(options);
  });

  return () => {
    options.ipc.removeHandler(DEBUG_LOGS);
    options.ipc.removeHandler(UI_TRACE);
    options.ipc.removeHandler(MARK);
    options.ipc.removeHandler(EXPORT);
  };
}
