import type { HealthResponse, OnboardingState } from "@one-memory/contracts";
import {
  ArrowLeft,
  Layers3,
  Network,
  PanelsTopLeft,
  Settings,
} from "lucide-react";
import type { ReactNode } from "react";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  Activity as ViewActivity,
} from "react";
import { ApplicationDetail } from "./ApplicationDetail";
import {
  ApplicationsHome,
  CALL_HISTORY_ENTRY_VISIBLE,
} from "./ApplicationsHome";
import { getOnboarding } from "./api";
import { useApplicationsLanguage } from "./applications-language";
import { CallHistory } from "./CallHistory";
import { DebugLogPanel } from "./DebugLogPanel";
import { DesktopFrame } from "./DesktopFrame";
import { useGuiLanguage } from "./gui-language";
import { HealthPanel } from "./HealthPanel";
import { ImportProgressStrip } from "./ImportProgressStrip";
import { MemorySpace } from "./MemorySpace";
import type { MemoryArrival } from "./memory-arrival-state";
import { OnboardingFlow } from "./OnboardingFlow";
import { R6Button, R6LocalStorage } from "./R6Controls";
import { R6Settings } from "./R6Settings";
import { prefetchDataRuntime } from "./runtime-state";
import "./styles.css";
import "./core-visual.css";
import "./r6-shell.css";
import "./r6-memory.css";
import "./selected-memory.css";
import { useImportSources } from "./use-import-sources";

type View = "memory" | "sources" | "health" | "settings" | "calls";
export const PRIMARY_VIEWS = ["sources", "memory"] as const;
const rendererStartedAt = Date.now();

export function App() {
  return (
    <DesktopFrame>
      <AppContent />
      <DebugLogPanel />
    </DesktopFrame>
  );
}

function AppContent() {
  const { t } = useGuiLanguage();
  const { a } = useApplicationsLanguage();
  const [onboarding, setOnboarding] = useState<OnboardingState | null>(null);
  const [startupRead, setStartupRead] = useState<"loading" | "ready" | "error">(
    "loading",
  );
  const startupFlight = useRef<Promise<OnboardingState> | null>(null);
  const startupEpoch = useRef(0);
  const startupAlive = useRef(false);
  const [detailSourceId, setDetailSourceId] = useState<string | null>(null);
  const [memoryArrival, setMemoryArrival] = useState<
    MemoryArrival | undefined
  >();
  const [focusMemoryId, setFocusMemoryId] = useState<string | null>(null);
  const imports = useImportSources({
    automaticSync:
      onboarding?.status === "completed" &&
      import.meta.env.VITE_AUTOMATIC_SYNC !== "false",
  });
  useEffect(() => {
    void prefetchDataRuntime();
    void window.oneMemory?.logUiTrace?.({
      action: "open",
      page: "onboarding",
      phase: "result",
      status: "succeeded",
      reason: "first_render_ready",
      duration_ms: Date.now() - rendererStartedAt,
    });
  }, []);
  useEffect(() => {
    if (onboarding?.status === "completed") void prefetchDataRuntime();
  }, [onboarding?.status]);
  const [view, setView] = useState<View>(() =>
    viewFromUrl(window.location.search),
  );
  const displayedView = view;
  const content = useRef<HTMLElement>(null);
  const viewScroll = useRef(new Map<View, number>());
  const detailSource = imports.sources.find(
    (source) => source.source_id === detailSourceId,
  );
  const [visited, setVisited] = useState<Set<View>>(() => new Set([view]));
  const rememberCurrentScroll = useCallback(() => {
    if (content.current)
      viewScroll.current.set(displayedView, content.current.scrollTop);
  }, [displayedView]);
  useEffect(() => {
    setVisited((previous) =>
      previous.has(displayedView)
        ? previous
        : new Set([...previous, displayedView]),
    );
  }, [displayedView]);
  useLayoutEffect(() => {
    const element = content.current;
    if (!element) return;
    element.scrollTop = viewScroll.current.get(displayedView) ?? 0;
    const reduceMotion =
      document.documentElement.dataset.nativeReduceMotion === "true" ||
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const animation =
      reduceMotion || typeof element.animate !== "function"
        ? null
        : element.animate([{ opacity: 0.84 }, { opacity: 1 }], {
            duration: 160,
            easing: "cubic-bezier(0.16, 1, 0.3, 1)",
          });
    return () => {
      animation?.cancel();
    };
  }, [displayedView]);
  const readStartup = useCallback(() => {
    const epoch = ++startupEpoch.current;
    setStartupRead("loading");
    // Share the physical read across effect replay; only its latest subscriber
    // may update this mounted App owner.
    let request = startupFlight.current;
    if (!request) {
      request = Promise.resolve()
        .then(getOnboarding)
        .finally(() => {
          if (startupFlight.current === request) startupFlight.current = null;
        });
      startupFlight.current = request;
    }
    void request.then(
      (state) => {
        if (!startupAlive.current || epoch !== startupEpoch.current) return;
        setOnboarding(state);
        setStartupRead("ready");
      },
      () => {
        if (startupAlive.current && epoch === startupEpoch.current)
          setStartupRead("error");
      },
    );
  }, []);
  useEffect(() => {
    startupAlive.current = true;
    readStartup();
    return () => {
      startupAlive.current = false;
      startupEpoch.current++;
    };
  }, [readStartup]);
  // OBS-17: the first read fails while no Core session exists yet. Landing back
  // on the first screen must not leave it parked on that error until someone
  // reloads by hand: read again when a session appears or when the page comes
  // back from the sign-in that created it.
  const sessionId = imports.health?.instance_id ?? null;
  const recoveredSession = useRef<string | null>(null);
  useEffect(() => {
    if (startupRead !== "error") return;
    if (sessionId) {
      if (recoveredSession.current === sessionId) return;
      recoveredSession.current = sessionId;
      readStartup();
      return;
    }
    const recover = () => readStartup();
    window.addEventListener("focus", recover);
    document.addEventListener("visibilitychange", recover);
    return () => {
      window.removeEventListener("focus", recover);
      document.removeEventListener("visibilitychange", recover);
    };
  }, [startupRead, sessionId, readStartup]);

  useEffect(() => {
    const update = () => {
      rememberCurrentScroll();
      setView(viewFromUrl(window.location.search));
    };
    window.addEventListener("popstate", update);
    return () => window.removeEventListener("popstate", update);
  }, [rememberCurrentScroll]);

  const navigate = (next: View) => {
    rememberCurrentScroll();
    const url = new URL(window.location.href);
    if (next === "sources") url.searchParams.delete("view");
    else url.searchParams.set("view", next);
    window.history.pushState({}, "", url);
    setView(next);
  };

  if (!onboarding || imports.loading) {
    return (
      <main className="onboarding-shell">
        {startupRead === "error" ? (
          <div className="boot-status">
            <p className="error-banner" role="alert">
              {t("startupReadError")}
            </p>
            <R6Button
              onClick={() => {
                if (!startupFlight.current) readStartup();
              }}
            >
              {t("startupRetry")}
            </R6Button>
          </div>
        ) : (
          <p className="boot-status" aria-live="polite">
            {t("startupOpening")}
          </p>
        )}
      </main>
    );
  }

  if (onboarding.status !== "completed") {
    return (
      <OnboardingFlow
        state={onboarding}
        imports={imports}
        onFinish={(destination) => {
          setOnboarding({
            ...onboarding,
            status: "completed",
            completed_at: new Date().toISOString(),
          });
          navigate(destination);
        }}
      />
    );
  }

  return (
    <div
      className={`app-shell r6-shell${window.oneMemory ? " desktop-shell" : ""}`}
    >
      {/* Mark + "MemHub" + the "个人资料空间" caption were removed on
          2026-09-20 at the user's request. The source still shows them
          (139:6770 / 139:6771); this is a product decision, not a gap. The bar
          stays because it is the window drag region and holds the traffic-light
          inset, so its height is now declared in CSS rather than inferred from
          the text it used to contain. */}
      <header className="r6-titlebar" />
      <a className="skip-link" href="#main-content">
        {a("skip")}
      </a>
      <nav className="app-rail" aria-label={a("navigation")}>
        <div className="brand-mark" title="MemHub">
          <Layers3 size={21} strokeWidth={1.6} aria-hidden="true" />
          <span>MemHub</span>
        </div>
        <div className="rail-primary">
          {PRIMARY_VIEWS.map((destination) => (
            <RailButton
              key={destination}
              active={displayedView === destination}
              tourId={destination === "sources" ? "nav-sources" : undefined}
              label={a(destination === "memory" ? "navMemories" : "apps")}
              icon={
                destination === "memory" ? (
                  <Network size={19} />
                ) : (
                  <PanelsTopLeft size={19} />
                )
              }
              badge={
                destination === "sources"
                  ? imports.jobs.filter((job) =>
                      ["pending", "running"].includes(job.status),
                    ).length
                  : undefined
              }
              onClick={() => navigate(destination)}
            />
          ))}
        </div>
        <div className="rail-utility">
          <RailButton
            active={displayedView === "settings" || displayedView === "health"}
            tourId="nav-settings"
            label={t("settings")}
            icon={<Settings size={18} />}
            onClick={() => navigate("settings")}
          />
          <p className="rail-core" title={a("localHint")}>
            <span aria-hidden="true" />
            {a("local")}
          </p>
        </div>
      </nav>
      <main
        className="app-content"
        id="main-content"
        tabIndex={-1}
        ref={content}
        data-view={displayedView}
      >
        {/* First run hands the user in here while the import is still going.
         * Without this the work would be invisible from every screen except
         * the one they were no longer required to sit on. */}
        {displayedView !== "memory" && displayedView !== "sources" && (
          <ImportProgressStrip
            userFacing
            jobs={imports.jobs}
            sources={imports.sources}
            backfilling={imports.health?.memory_backfill === "running"}
            onOpenSources={() => navigate("sources")}
            sourceSnapshot={imports.sourceSnapshot}
            onRefresh={imports.refresh}
            arrival={memoryArrival}
            onConfigureModel={() => navigate("settings")}
            onJobAction={imports.changeJob}
            showSourceLink={displayedView !== "settings"}
            showProgressDetails={displayedView !== "settings"}
          />
        )}
        {(visited.has("memory") || displayedView === "memory") && (
          <div
            className="memory-workspace-host"
            hidden={displayedView !== "memory"}
          >
            <MemorySpace
              simplified
              active={displayedView === "memory"}
              preload={false}
              sources={imports.sources}
              importJobs={imports.jobs}
              sourceSnapshotStale={imports.sourceSnapshot?.stale === true}
              focusMemoryId={focusMemoryId}
              onFocusMemoryConsumed={() => setFocusMemoryId(null)}
              onConnect={() => navigate("sources")}
              onConfigureModel={() => navigate("settings")}
              onArrivalChange={setMemoryArrival}
            />
          </div>
        )}
        {visited.has("sources") || displayedView === "sources" ? (
          <ViewActivity
            mode={displayedView === "sources" ? "visible" : "hidden"}
          >
            <div hidden={displayedView !== "sources"} className="retained-view">
              {detailSource ? (
                <ApplicationDetail
                  key={detailSource.source_id}
                  source={detailSource}
                  sources={imports.sources}
                  stale={imports.sourceSnapshot?.stale === true}
                  syncWriteConfirmed={imports.sourceSyncWriteConfirmed?.has(
                    detailSource.source_id,
                  )}
                  setSync={imports.setSync}
                  refresh={imports.refresh}
                  onBack={() => setDetailSourceId(null)}
                />
              ) : (
                <ApplicationsHome
                  imports={imports}
                  active={displayedView === "sources"}
                  onManage={(sourceId) => {
                    if (sourceId) setDetailSourceId(sourceId);
                  }}
                  onMemories={(memoryId) => {
                    if (memoryId) setFocusMemoryId(memoryId);
                    navigate("memory");
                  }}
                  onCalls={() => navigate("calls")}
                />
              )}
            </div>
          </ViewActivity>
        ) : null}
        {displayedView === "calls" ? (
          <CallHistory
            onBack={() => navigate("sources")}
            onOpenMemory={(id) => {
              setFocusMemoryId(id);
              navigate("memory");
            }}
          />
        ) : null}
        {displayedView === "health" ? (
          <>
            <button
              type="button"
              className="quiet settings-back"
              onClick={() => navigate("settings")}
            >
              <ArrowLeft size={16} />
              {t("settings")}
            </button>
            <HealthPanel imports={imports} />
          </>
        ) : null}
        {visited.has("settings") || displayedView === "settings" ? (
          <section
            className="settings-view"
            hidden={displayedView !== "settings"}
          >
            <R6Settings
              active={displayedView === "settings"}
              onDiagnostics={() => navigate("health")}
            />
          </section>
        ) : null}
      </main>
      <footer className="r6-shell-footer">
        <R6LocalStorage />
      </footer>
    </div>
  );
}

export function viewFromUrl(search: string): View {
  const value = new URLSearchParams(search).get("view");
  return value === "sources" ||
    value === "health" ||
    value === "settings" ||
    // D96: the call-history page has no way in for now; a stale link lands home.
    (value === "calls" && CALL_HISTORY_ENTRY_VISIBLE) ||
    value === "memory"
    ? value
    : "sources";
}

export function coreStatus(health: HealthResponse | null): string {
  if (!health) return "连接 Core…";
  // After a large import there is a stretch where Core is connected and
  // working through conversations it has not read yet. Saying only "已连接"
  // there reads as "nothing is happening", and the memories are not in yet.
  if (health.memory_backfill === "running") return "本地 Core 正在整理历史对话";
  if (health.memory_backfill === "failed") return "历史对话整理未完成";
  return "本地 Core 已连接";
}

function RailButton({
  active,
  label,
  icon,
  badge,
  tourId,
  onClick,
}: {
  active: boolean;
  label: string;
  icon: ReactNode;
  badge?: number;
  tourId?: string;
  onClick(): void;
}) {
  return (
    <button
      type="button"
      className={`rail-button${active ? " active" : ""}`}
      aria-current={active ? "page" : undefined}
      data-tour-id={tourId}
      title={label}
      onClick={onClick}
    >
      <span className="rail-icon" aria-hidden="true">
        {icon}
      </span>
      <span>{label}</span>
      {badge ? <span className="rail-badge">{badge}</span> : null}
    </button>
  );
}
