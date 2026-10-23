import { lstat } from "node:fs/promises";
import path from "node:path";
import type { DiagnosticEventV1 } from "@one-memory/runtime-control";
import {
  diagnosticLogFiles,
  readDiagnosticEvents,
} from "@one-memory/runtime-control";

export interface LogQuery {
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
  errorsOnly: boolean;
  tail: number;
  follow: boolean;
}

const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/;
const NAME = /^[a-z][a-z0-9]*(?:[._-][a-z0-9]+){0,15}$/;
const SAFE_DETAIL_KEYS = new Set([
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
]);

export function parseLogQuery(arguments_: readonly string[]): LogQuery | null {
  if (arguments_.filter((item) => item === "--json").length > 1) return null;
  const values: Partial<LogQuery> = { errorsOnly: false, follow: false };
  for (let index = 0; index < arguments_.length; index += 1) {
    const flag = arguments_[index];
    if (flag === "--json") continue;
    if (flag === "--errors-only") {
      if (values.errorsOnly) return null;
      values.errorsOnly = true;
      continue;
    }
    if (flag === "--follow") {
      if (values.follow) return null;
      values.follow = true;
      continue;
    }
    const next = arguments_[index + 1];
    if (!next || next.startsWith("--")) return null;
    index += 1;
    if (flag === "--since") {
      if (values.since) return null;
      values.since = parseSince(next) ?? undefined;
      if (!values.since) return null;
    } else if (flag === "--tail") {
      const count = Number(next);
      if (
        values.tail ||
        !Number.isSafeInteger(count) ||
        count < 1 ||
        count > 5_000
      )
        return null;
      values.tail = count;
    } else if (
      flag === "--session" ||
      flag === "--trace" ||
      flag === "--incident"
    ) {
      if (!isUuid(next)) return null;
      const key = {
        "--session": "sessionId",
        "--trace": "traceId",
        "--incident": "incidentId",
      }[flag] as "sessionId" | "traceId" | "incidentId";
      if (values[key]) return null;
      values[key] = next;
    } else if (
      flag === "--operation" ||
      flag === "--request" ||
      flag === "--job" ||
      flag === "--task"
    ) {
      if (!ID.test(next)) return null;
      const key = {
        "--operation": "operationId",
        "--request": "requestId",
        "--job": "jobId",
        "--task": "taskId",
      }[flag] as "operationId" | "requestId" | "jobId" | "taskId";
      if (values[key]) return null;
      values[key] = next;
    } else if (flag === "--component") {
      if (values.component || !NAME.test(next)) return null;
      values.component = next;
    } else if (flag === "--event") {
      if (values.event || !NAME.test(next)) return null;
      values.event = next;
    } else return null;
  }
  return {
    ...values,
    errorsOnly: values.errorsOnly ?? false,
    follow: values.follow ?? false,
    tail: values.tail ?? 200,
  };
}

export async function readLogReport(
  dataDir: string,
  query: LogQuery,
): Promise<{
  events: DiagnosticEventV1[];
  invalid_lines: number;
  failure?: "log_directory_unreadable";
}> {
  // `readDiagnosticEvents` treats an unreadable logs path as "no events". That
  // makes a broken install indistinguishable from a quiet one, so detect the
  // specific case here and let the caller report it.
  const logsDirectory = path.join(path.resolve(dataDir), "logs");
  const info = await lstat(logsDirectory).catch(() => null);
  if (info !== null && (!info.isDirectory() || info.isSymbolicLink())) {
    return {
      events: [],
      invalid_lines: 0,
      failure: "log_directory_unreadable",
    };
  }
  if (
    info !== null &&
    (await diagnosticLogFiles(dataDir).catch(() => null)) === null
  ) {
    return {
      events: [],
      invalid_lines: 0,
      failure: "log_directory_unreadable",
    };
  }
  const explicit =
    query.since ||
    query.sessionId ||
    query.traceId ||
    query.incidentId ||
    query.operationId ||
    query.requestId ||
    query.jobId ||
    query.taskId ||
    query.component ||
    query.event ||
    query.errorsOnly;
  const first = await readDiagnosticEvents(dataDir, {
    ...query,
    requestId: query.requestId,
    jobId: query.jobId,
    taskId: query.taskId,
    tail: explicit ? query.tail : 5_000,
  });
  if (explicit || first.events.length === 0) return first;
  const latestSession = first.events.at(-1)?.session_id;
  const latestInstance = [...first.events]
    .reverse()
    .find(
      (event) => event.session_id === latestSession && event.instance_id,
    )?.instance_id;
  return {
    ...first,
    events: first.events
      .filter(
        (event) =>
          event.session_id === latestSession ||
          (latestInstance !== undefined &&
            event.instance_id === latestInstance),
      )
      .slice(-query.tail),
  };
}

export function renderLogText(
  events: readonly DiagnosticEventV1[],
  invalidLines: number,
): string {
  const lines = events.map(redactDiagnosticEvent).map((event) => {
    const ids = [
      event.incident_id ? `incident=${event.incident_id}` : null,
      event.trace_id ? `trace=${event.trace_id}` : null,
      event.operation_id ? `operation=${event.operation_id}` : null,
      event.job_id ? `job=${event.job_id}` : null,
      event.task_id ? `task=${event.task_id}` : null,
    ].filter(Boolean);
    return `[${event.timestamp}] ${event.level.toUpperCase()} ${event.service}/${event.component} ${event.event} ${event.outcome}${ids.length ? ` ${ids.join(" ")}` : ""}`;
  });
  if (invalidLines) lines.push(`跳过 ${invalidLines} 行损坏的诊断记录。`);
  if (!lines.length) lines.push("没有匹配的诊断记录。");
  return `${lines.join("\n")}\n`;
}

/** Defence in depth for the CLI read surface. `DiagnosticLogger` already
 * allow-lists keys, but a future writer, a manually edited log, or a malformed
 * line must not turn `memhub logs` into an exfiltration path. */
export function redactDiagnosticEvent(
  event: DiagnosticEventV1,
): DiagnosticEventV1 {
  const redacted: DiagnosticEventV1 = {
    schema_version: 1,
    timestamp: event.timestamp,
    level: event.level,
    service: event.service,
    component: event.component,
    event: event.event,
    outcome: event.outcome,
    session_id: event.session_id,
    ...(event.trace_id === undefined ? {} : { trace_id: event.trace_id }),
    ...(event.incident_id === undefined
      ? {}
      : { incident_id: event.incident_id }),
    ...(event.instance_id === undefined
      ? {}
      : { instance_id: event.instance_id }),
    ...(event.operation_id === undefined
      ? {}
      : { operation_id: event.operation_id }),
    ...(event.request_id === undefined ? {} : { request_id: event.request_id }),
    ...(event.job_id === undefined ? {} : { job_id: event.job_id }),
    ...(event.task_id === undefined ? {} : { task_id: event.task_id }),
    ...(event.duration_ms === undefined
      ? {}
      : { duration_ms: event.duration_ms }),
    ...(event.state_before === undefined
      ? {}
      : { state_before: event.state_before }),
    ...(event.state_after === undefined
      ? {}
      : { state_after: event.state_after }),
    ...(event.error_code === undefined ? {} : { error_code: event.error_code }),
    ...(event.error_type === undefined ? {} : { error_type: event.error_type }),
    ...(event.safe_stack === undefined
      ? {}
      : {
          safe_stack: event.safe_stack.filter(
            (frame) =>
              !frame.startsWith("/") &&
              !/^[A-Za-z]:\\/.test(frame) &&
              !frame.includes(".."),
          ),
        }),
  };
  const details = Object.fromEntries(
    Object.entries(event.details ?? {}).filter(([key]) =>
      SAFE_DETAIL_KEYS.has(key),
    ),
  );
  if (Object.keys(details).length > 0) redacted.details = details;
  return redacted;
}

export async function followLogs(options: {
  dataDir: string;
  query: LogQuery;
  signal: AbortSignal;
  emit(event: DiagnosticEventV1): void;
  wait?: (milliseconds: number, signal: AbortSignal) => Promise<void>;
  maxEvents?: number;
}): Promise<void> {
  const seen = new Set<string>();
  let emitted = 0;
  const wait = options.wait ?? abortableWait;
  while (
    !options.signal.aborted &&
    emitted < (options.maxEvents ?? Number.POSITIVE_INFINITY)
  ) {
    const report = await readLogReport(options.dataDir, {
      ...options.query,
      tail: 5_000,
    });
    for (const event of report.events) {
      const identity = JSON.stringify(event);
      if (seen.has(identity)) continue;
      seen.add(identity);
      options.emit(event);
      emitted += 1;
      if (emitted >= (options.maxEvents ?? Number.POSITIVE_INFINITY)) return;
    }
    if (seen.size > 10_000) {
      const latest = [...seen].slice(-5_000);
      seen.clear();
      for (const item of latest) seen.add(item);
    }
    try {
      await wait(1_000, options.signal);
    } catch {
      return;
    }
  }
}

function parseSince(value: string): Date | null {
  const relative = value.match(/^(\d+)(m|h|d)$/);
  if (relative) {
    const amount = Number(relative[1]);
    if (!Number.isSafeInteger(amount) || amount < 1 || amount > 365)
      return null;
    const unit = { m: 60_000, h: 3_600_000, d: 86_400_000 }[
      relative[2] as "m" | "h" | "d"
    ];
    return new Date(Date.now() - amount * unit);
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(
    value,
  );
}

function abortableWait(
  milliseconds: number,
  signal: AbortSignal,
): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted)
      return reject(new DOMException("aborted", "AbortError"));
    const timer = setTimeout(resolve, milliseconds);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        reject(new DOMException("aborted", "AbortError"));
      },
      { once: true },
    );
  });
}
