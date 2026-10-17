import type { DebugLogEntry, DebugLogLevel } from "./debug-log-types";

const entries: DebugLogEntry[] = [];
let snapshot: readonly DebugLogEntry[] = entries;
const listeners = new Set<() => void>();
let installed = false;
let sequence = 0;

export function installRendererDebugLogCapture(): () => void {
  if (installed) return () => undefined;
  installed = true;
  const originals = {
    warn: console.warn,
    error: console.error,
  };
  // Successful renderer chatter is already represented by structured UI
  // diagnostics. Mirroring console.log/info/debug duplicated it without trace
  // identity and made the panel less useful. Console capture is now only the
  // actionable fallback for warnings and failures.
  const methods = ["warn", "error"] as const;
  for (const method of methods) {
    console[method] = (...values: unknown[]) => {
      originals[method](...values);
      recordRendererDebugLog({
        level: method,
        event: `console.${method}`,
        outcome: method === "error" ? "failed" : "observed",
        message: values.map(debugValue).join(" "),
      });
    };
  }
  const onError = (event: ErrorEvent) => {
    recordRendererDebugLog({
      level: "error",
      event: "window.error",
      outcome: "failed",
      message: event.message || "Unhandled renderer error",
      error_type: event.error instanceof Error ? event.error.name : undefined,
    });
  };
  const onRejection = (event: PromiseRejectionEvent) => {
    recordRendererDebugLog({
      level: "error",
      event: "window.unhandled_rejection",
      outcome: "failed",
      message: debugValue(event.reason),
      error_type: event.reason instanceof Error ? event.reason.name : undefined,
    });
  };
  window.addEventListener("error", onError);
  window.addEventListener("unhandledrejection", onRejection);
  return () => {
    for (const method of methods) console[method] = originals[method];
    window.removeEventListener("error", onError);
    window.removeEventListener("unhandledrejection", onRejection);
    installed = false;
  };
}

export function recordRendererDebugLog(input: {
  level: DebugLogLevel;
  event: string;
  outcome: string;
  message: string;
  error_type?: string;
}): void {
  sequence += 1;
  entries.push({
    id: `renderer:${sequence}`,
    timestamp: new Date().toISOString(),
    level: input.level,
    service: "renderer",
    component: "renderer.console",
    event: input.event,
    outcome: input.outcome,
    message: input.message.slice(0, 8_000),
    source: "console",
    ...(input.error_type ? { error_type: input.error_type } : {}),
  });
  snapshot = entries.slice();
  for (const listener of listeners) listener();
}

export function rendererDebugLogs(): readonly DebugLogEntry[] {
  return snapshot;
}

export function subscribeRendererDebugLogs(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function resetRendererDebugLogsForTest(): void {
  entries.length = 0;
  sequence = 0;
  snapshot = entries;
}

function debugValue(value: unknown): string {
  if (typeof value === "string") return value;
  if (value instanceof Error) return `${value.name}: ${value.message}`;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}
