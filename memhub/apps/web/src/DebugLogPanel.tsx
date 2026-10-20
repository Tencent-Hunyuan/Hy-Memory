import {
  Bug,
  ChevronDown,
  ChevronRight,
  CirclePause,
  CirclePlay,
  Maximize2,
  Minimize2,
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
import { ModelUsageView } from "./ModelUsageView";
import {
  rendererDebugLogs,
  subscribeRendererDebugLogs,
} from "./renderer-debug-log";
import { useTranslate } from "./ui-language";
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
  const t = useTranslate();
  const supported = typeof window.oneMemory?.getDebugLogs === "function";
  const [open, setOpen] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [tab, setTab] = useState<"logs" | "usage">("logs");
  const [live, setLive] = useState(true);
  const [usageRefreshToken, setUsageRefreshToken] = useState(0);
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
  const followLatestRef = useRef(true);
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
    if (!open || !supported || tab !== "logs") return;
    void refresh();
    void getDiagnosticLogLevel()
      .then(setCaptureLevel)
      .catch(() => undefined);
    if (!live) return;
    const timer = window.setInterval(() => void refresh(), 1_000);
    return () => window.clearInterval(timer);
  }, [live, open, refresh, supported, tab]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || !fullscreen) return;
      event.preventDefault();
      setFullscreen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [fullscreen, open]);

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
    if (
      !open ||
      tab !== "logs" ||
      !live ||
      !followLatestRef.current ||
      !listRef.current
    )
      return;
    listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [filtered.length, live, open, tab]);

  const handleListScroll = () => {
    const list = listRef.current;
    if (!list) return;
    followLatestRef.current =
      list.scrollHeight - list.scrollTop - list.clientHeight <= 24;
  };

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
        <aside
          className={`debug-log-panel${fullscreen ? " is-fullscreen" : ""}`}
          aria-label={t("Debug 日志")}
        >
          <header className="debug-log-header">
            <div>
              <strong>{t("诊断")}</strong>
              <span>
                {tab === "logs"
                  ? t("{count} 条日志，本次启动", { count: entries.length })
                  : t("记忆沉淀模型用量")}
              </span>
            </div>
            <div className="debug-log-header-actions">
              <button
                type="button"
                className="debug-icon-button"
                title={live ? t("暂停实时刷新") : t("继续实时刷新")}
                aria-label={live ? t("暂停实时刷新") : t("继续实时刷新")}
                onClick={() => setLive((value) => !value)}
              >
                {live ? <CirclePause size={17} /> : <CirclePlay size={17} />}
              </button>
              <button
                type="button"
                className="debug-icon-button"
                title={t("立即刷新")}
                aria-label={t("立即刷新")}
                onClick={() => {
                  if (tab === "logs") void refresh();
                  else setUsageRefreshToken((value) => value + 1);
                }}
              >
                <RefreshCw
                  size={17}
                  className={readState === "loading" ? "spin" : ""}
                />
              </button>
              <button
                type="button"
                className="debug-icon-button"
                title={fullscreen ? t("退出全屏") : t("全屏显示")}
                aria-label={
                  fullscreen ? t("退出全屏显示日志") : t("全屏显示日志")
                }
                onClick={() => setFullscreen((value) => !value)}
              >
                {fullscreen ? <Minimize2 size={17} /> : <Maximize2 size={17} />}
              </button>
              <button
                type="button"
                className="debug-icon-button"
                title={t("关闭 Debug 日志")}
                aria-label={t("关闭 Debug 日志")}
                onClick={() => {
                  setFullscreen(false);
                  setOpen(false);
                }}
              >
                <X size={18} />
              </button>
            </div>
          </header>
          <div
            className="debug-panel-tabs"
            role="tablist"
            aria-label={t("诊断页面")}
          >
            <button
              type="button"
              role="tab"
              aria-selected={tab === "logs"}
              onClick={() => setTab("logs")}
            >
              {t("日志")}
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === "usage"}
              onClick={() => setTab("usage")}
            >
              {t("模型用量")}
            </button>
          </div>
          {tab === "logs" ? (
            <div className="debug-log-content">
              <div className="debug-log-toolbar">
                <label className="debug-search">
                  <Search size={15} aria-hidden="true" />
                  <input
                    type="search"
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder={t("搜索事件、组件、错误码或内容")}
                    aria-label={t("搜索日志")}
                  />
                </label>
                <select
                  aria-label={t("日志来源")}
                  value={service}
                  onChange={(event) =>
                    setService(event.target.value as typeof service)
                  }
                >
                  <option value="all">{t("全部来源")}</option>
                  {Object.entries(SERVICE_LABELS).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
                <label className="debug-capture-level">
                  <span>{t("采集级别")}</span>
                  <select
                    aria-label={t("采集级别")}
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
                <legend>{t("显示级别")}</legend>
                {LEVELS.map((option) => (
                  <button
                    type="button"
                    key={option.value}
                    aria-pressed={level === option.value}
                    onClick={() => setLevel(option.value)}
                  >
                    {option.value === "all" ? t("全部") : option.label}
                  </button>
                ))}
              </fieldset>
              <div className="debug-log-status" aria-live="polite">
                <span>{t("{count} 条匹配", { count: filtered.length })}</span>
                <span>{live ? t("实时刷新") : t("已暂停")}</span>
                {snapshot?.truncated ? (
                  <span>{t("日志过多，已截断")}</span>
                ) : null}
                {readState === "error" ? (
                  <span className="debug-read-error">{t("读取失败")}</span>
                ) : null}
              </div>
              <div
                className="debug-log-list"
                ref={listRef}
                onScroll={handleListScroll}
              >
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
                          <time>{logTime(entry.timestamp)}</time>
                          <span className="debug-log-level">{entry.level}</span>
                          <span className="debug-log-service">
                            {SERVICE_LABELS[entry.service]}
                          </span>
                          <span className="debug-log-event">
                            {entry.message ||
                              `${entry.component} · ${entry.event}`}
                          </span>
                          <span className="debug-log-outcome">
                            {entry.outcome}
                          </span>
                        </button>
                        {isExpanded ? (
                          <pre>{JSON.stringify(entry, null, 2)}</pre>
                        ) : null}
                      </article>
                    );
                  })
                ) : (
                  <p className="debug-log-empty">
                    {readState === "loading"
                      ? t("正在读取日志…")
                      : t("没有匹配的日志")}
                  </p>
                )}
              </div>
            </div>
          ) : (
            <ModelUsageView live={live} refreshToken={usageRefreshToken} />
          )}
        </aside>
      ) : null}
      <button
        type="button"
        className="debug-fab"
        aria-label={open ? t("Debug 日志已打开") : t("打开 Debug 日志")}
        title={t("打开 Debug 日志")}
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

function logTime(timestamp: string): string {
  const date = new Date(timestamp);
  return Number.isNaN(date.getTime())
    ? timestamp
    : date.toLocaleTimeString("zh-CN", { hour12: false });
}
