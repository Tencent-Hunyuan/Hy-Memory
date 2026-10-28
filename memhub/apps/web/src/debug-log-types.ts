export type DebugLogLevel = "debug" | "info" | "warn" | "error";

export interface DebugLogEntry {
  id: string;
  timestamp: string;
  level: DebugLogLevel;
  service: "runtime" | "core" | "host-bridge" | "desktop" | "cli" | "renderer";
  component: string;
  event: string;
  outcome: string;
  duration_ms?: number;
  error_code?: string;
  error_type?: string;
  trace_id?: string;
  details?: Record<string, string | number | boolean | null>;
  message?: string;
  source: "diagnostic" | "stdout" | "stderr" | "console";
}

export interface DebugLogSnapshot {
  started_at: string;
  read_at: string;
  entries: DebugLogEntry[];
  truncated: boolean;
}
