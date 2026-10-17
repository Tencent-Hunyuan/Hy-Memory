import {
  Bug,
  ChevronDown,
  ChevronRight,
  CirclePause,
  CirclePlay,
  RefreshCw,
  Search,
  X,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import {
  type DiagnosticLogLevel,
  getDiagnosticLogLevel,
  setDiagnosticLogLevel,
} from "./api";
import type {
  DebugLogEntry,
  DebugLogLevel,
  DebugLogSnapshot,
} from "./debug-log-types";
import { useGuiLanguage } from "./gui-language";
import {
  rendererDebugLogs,
  subscribeRendererDebugLogs,
} from "./renderer-debug-log";
import "./debug-log-panel.css";

const LEVELS: Array<{ value: DebugLogLevel | "all"; label: string }> = [
  { value: "all", label: "全部" },
  { value: "debug", label: "Debug" },
  { value: "info", label: "Info" },
  { value: "warn", label: "Warn" },
  { value: "error", label: "Error" },
];
const SERVICE_LABELS: Record<DebugLogEntry["service"], string> = {
  renderer: "Renderer",
  desktop: "Desktop",
  runtime: "Runtime",
  core: "Core",
  "host-bridge": "Host Bridge",
  cli: "CLI",
  "hy-memory": "HY Memory",
};

export function DebugLogPanel() {
  const { t, locale } = useGuiLanguage();
  const supported = typeof window.oneMemory?.getDebugLogs === "function";
  const [open, setOpen] = useState(false);
  const [live, setLive] = useState(true);
  const [query, setQuery] = useState("");
  const [level, setLevel] = useState<DebugLogLevel | "all">("all");
  const [captureLevel, setCaptureLevel] = useState<DiagnosticLogLevel>("info");
  const [capturePending, setCapturePending] = useState(false);
  const [service, setService] = useState<DebugLogEntry["service"] | "all">(
    "all",
  );
  const [snapshot, setSnapshot] = useState<DebugLogSnapshot | null>(null);
  const [readState, setReadState] = useState<"idle" | "loading" | "error">(
    "idle",
  );
  const snapshotRef = useRef<DebugLogSnapshot | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const listRef = useRef<HTMLDivElement>(null);
  const rendererEntries = useSyncExternalStore(
    subscribeRendererDebugLogs,
    rendererDebugLogs,
    rendererDebugLogs,
  );

  const refresh = useCallback(async () => {
    const read = window.oneMemory?.getDebugLogs;
    if (!read) return;
    setReadState((state) => (snapshotRef.current === null ? "loading" : state));
    try {
      const next = await read();
      snapshotRef.current = next;
      setSnapshot(next);
      setReadState("idle");
    } catch {
      setReadState("error");
    }
  }, []);

  useEffect(() => {
    if (!open || !supported) return;
    void refresh();
    void getDiagnosticLogLevel()
      .then(setCaptureLevel)
      .catch(() => undefined);
    if (!live) return;
    const timer = window.setInterval(() => void refresh(), 1_000);
    return () => window.clearInterval(timer);
  }, [live, open, refresh, supported]);

  const entries = useMemo(() => {
    const byId = new Map<string, DebugLogEntry>();
    for (const entry of snapshot?.entries ?? []) byId.set(entry.id, entry);
    for (const entry of rendererEntries) byId.set(entry.id, entry);
    return [...byId.values()].sort((left, right) =>
      left.timestamp === right.timestamp
        ? left.id.localeCompare(right.id)
        : left.timestamp.localeCompare(right.timestamp),
    );
  }, [rendererEntries, snapshot]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return entries.filter((entry) => {
      if (level !== "all" && entry.level !== level) return false;
      if (service !== "all" && entry.service !== service) return false;
      return !needle || searchableEntry(entry).includes(needle);
    });
  }, [entries, level, query, service]);

  useEffect(() => {
    if (filtered.length === 0) return;
    if (!open || !live || !listRef.current) return;
    listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [filtered.length, live, open]);

  const toggleExpanded = (id: string) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  if (!supported) return null;
  return (
    <div className={`debug-log-root${open ? " is-open" : ""}`}>
      {open ? (
        <aside className="debug-log-panel" aria-label={t("debugTitle")}>
          <header className="debug-log-header">
            <div>
              <strong>{t("debugTitle")}</strong>
              <span>
                {t("debugCount", {
                  count: entries.length.toLocaleString(locale),
                })}
              </span>
            </div>
            <div className="debug-log-header-actions">
              <button
                type="button"
                className="debug-icon-button"
                title={live ? t("debugPause") : t("debugResume")}
                aria-label={live ? t("debugPause") : t("debugResume")}
                onClick={() => setLive((value) => !value)}
              >
                {live ? <CirclePause size={17} /> : <CirclePlay size={17} />}
              </button>
              <button
                type="button"
                className="debug-icon-button"
                title={t("debugRefresh")}
                aria-label={t("debugRefresh")}
                onClick={() => void refresh()}
              >
                <RefreshCw
                  size={17}
                  className={readState === "loading" ? "spin" : ""}
                />
              </button>
              <button
                type="button"
                className="debug-icon-button"
                title={t("debugClose")}
                aria-label={t("debugClose")}
                onClick={() => setOpen(false)}
              >
                <X size={18} />
              </button>
            </div>
          </header>
          <div className="debug-log-toolbar">
            <label className="debug-search">
              <Search size={15} aria-hidden="true" />
              <input
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={t("debugSearchPlaceholder")}
                aria-label={t("debugSearch")}
              />
            </label>
            <select
              aria-label={t("debugSource")}
              value={service}
              onChange={(event) =>
                setService(event.target.value as typeof service)
              }
            >
              <option value="all">{t("debugAllSources")}</option>
              {Object.entries(SERVICE_LABELS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
            <label className="debug-capture-level">
              <span>{t("debugCapture")}</span>
              <select
                aria-label={t("debugCapture")}
                value={captureLevel}
                disabled={capturePending}
                onChange={(event) => {
                  const next = event.target.value as DiagnosticLogLevel;
                  setCapturePending(true);
                  void setDiagnosticLogLevel(next)
                    .then(setCaptureLevel)
                    .then(() => refresh())
                    .catch(() => setReadState("error"))
                    .finally(() => setCapturePending(false));
                }}
              >
                <option value="error">Error</option>
                <option value="warning">Warning</option>
                <option value="info">Info</option>
                <option value="debug">Debug</option>
              </select>
            </label>
          </div>
          <fieldset className="debug-levels">
            <legend>{t("debugDisplay")}</legend>
            {LEVELS.map((option) => (
              <button
                type="button"
                key={option.value}
                aria-pressed={level === option.value}
                onClick={() => setLevel(option.value)}
              >
                {option.value === "all" ? t("debugAll") : option.label}
              </button>
            ))}
          </fieldset>
          <div className="debug-log-status" aria-live="polite">
            <span>
              {t("debugMatches", {
                count: filtered.length.toLocaleString(locale),
              })}
            </span>
            <span>{live ? t("debugLive") : t("debugPaused")}</span>
            {snapshot?.truncated ? <span>{t("debugTruncated")}</span> : null}
            {readState === "error" ? (
              <span className="debug-read-error">{t("debugReadError")}</span>
            ) : null}
          </div>
          <div className="debug-log-list" ref={listRef}>
            {filtered.length ? (
              filtered.map((entry) => {
                const isExpanded = expanded.has(entry.id);
                return (
                  <article
                    className={`debug-log-row level-${entry.level}`}
                    key={entry.id}
                  >
                    <button
                      type="button"
                      className="debug-log-summary"
                      onClick={() => toggleExpanded(entry.id)}
                      aria-expanded={isExpanded}
                    >
                      {isExpanded ? (
                        <ChevronDown size={14} />
                      ) : (
                        <ChevronRight size={14} />
                      )}
                      <time>{logTime(entry.timestamp, locale)}</time>
                      <span className="debug-log-level">{entry.level}</span>
                      <span className="debug-log-service">
                        {SERVICE_LABELS[entry.service]}
                      </span>
                      <span className="debug-log-event">
                        {entry.message || `${entry.component} · ${entry.event}`}
                      </span>
                      <span className="debug-log-outcome">{entry.outcome}</span>
                    </button>
                    {isExpanded ? (
                      <pre>{JSON.stringify(entry, null, 2)}</pre>
                    ) : null}
                  </article>
                );
              })
            ) : (
              <p className="debug-log-empty">
                {readState === "loading" ? t("debugLoading") : t("debugEmpty")}
              </p>
            )}
          </div>
        </aside>
      ) : null}
      <button
        type="button"
        className="debug-fab"
        aria-label={open ? t("debugOpened") : t("debugOpen")}
        title={t("debugOpen")}
        onClick={() => setOpen(true)}
      >
        <Bug size={20} aria-hidden="true" />
        {entries.some((entry) => entry.level === "error") ? (
          <span className="debug-error-dot" aria-hidden="true" />
        ) : null}
      </button>
    </div>
  );
}

function searchableEntry(entry: DebugLogEntry): string {
  return JSON.stringify(entry).toLocaleLowerCase();
}

function logTime(timestamp: string, locale: string): string {
  const date = new Date(timestamp);
  return Number.isNaN(date.getTime())
    ? timestamp
    : date.toLocaleTimeString(locale, { hour12: false });
}
