import type {
  HostHomeSummary,
  LiveSyncHealth,
  MemoryProcessingSummary,
  SourceResponse,
} from "@one-memory/contracts";
import { type GuiLocale, isMessageKey } from "@one-memory/i18n";
import { ArrowLeft, ArrowRight } from "lucide-react";
import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { getHomeSummary, getLiveSyncHealth, getMemoryProcessing } from "./api";
import { AppMark } from "./app-mark";
import {
  type CallEvent,
  callDetailHeadline,
  callHostName,
  callRowDetail,
  getCallHistory,
} from "./call-history-client";
import { hostMatchesSource, sameConnectionHost } from "./connection-hosts";
import { readConnectionPreferences } from "./connection-preferences";
import {
  autoReconcile,
  hostApplyPending,
  retryAutoReconcile,
  useAutoConnect,
} from "./connection-reconcile";
import { useGuiLanguage } from "./gui-language";
import { HomeCardBoundary } from "./HomeCardBoundary";
import {
  hostBridgeAvailable,
  hostConnectionApproved,
  hostConnectionUnknown,
  useRecallHosts,
} from "./host-state";
import { subscribePoll } from "./poll-scheduler";
import { R6Button } from "./R6Controls";
import { prefetchDataRuntime } from "./runtime-state";
import { refreshSedimentation, useSedimentation } from "./sedimentation-state";
import type { useImportSources } from "./use-import-sources";
import "./applications-home.css";

type HomeViewState = {
  expanded: boolean;
  homeScroll: number;
  allScroll: number;
  focus: string | null;
};

// The import controller outlives detail routes; a new App owner gets a fresh view.
// Keep only presentation state, never API data, DOM nodes or running animations.
/**
 * D96 第 4 点：调用记录详情先不对外。首页“最近调用”照常显示，只隐藏进入
 * 记录页的入口；`CallHistory.tsx` 和接口不动。不能靠不传 `onCalls` 来隐藏：
 * 首页用它是否存在来决定要不要拉最近调用。
 */
export const CALL_HISTORY_ENTRY_VISIBLE = false;

const retainedViews = new WeakMap<object, HomeViewState>();

type Translate = ReturnType<typeof useGuiLanguage>["t"];

function clockParts(value: string, locale: GuiLocale) {
  const ms = Date.parse(value);
  if (!Number.isFinite(ms)) return null;
  const date = new Date(ms);
  const now = new Date();
  return {
    clock: new Intl.DateTimeFormat(locale, {
      hour: "2-digit",
      minute: "2-digit",
    }).format(date),
    today:
      date.getFullYear() === now.getFullYear() &&
      date.getMonth() === now.getMonth() &&
      date.getDate() === now.getDate(),
  };
}

function stamped(value: string, locale: GuiLocale, t: Translate, suffix = "") {
  const parts = clockParts(value, locale);
  if (!parts) return `${t("apps.unknownTime")}${suffix}`;
  return `${parts.today ? t("apps.today") : ""}${parts.clock}${suffix}`;
}

type ActivityChoice = "call" | "update";

function sourceMatchesHost(sourceId: string, hostId: string | null) {
  if (!hostId) return false;
  return hostMatchesSource(sourceId, hostId);
}

function successfulCall(item: CallEvent) {
  return item.state === "grounded" || item.state === "empty";
}

function attributedSuccessfulCall(item: CallEvent) {
  return item.host_id !== null && successfulCall(item);
}

function callAttention(events: CallEvent[]) {
  const hosts = new Set<string>();
  for (const event of events) {
    const host = event.host_id;
    if (!host || hosts.has(host)) continue;
    hosts.add(host);
    if (event.state === "unavailable") {
      return { kind: "unavailable" as const, host };
    }
    let consecutiveTimeouts = 0;
    for (const candidate of events) {
      if (candidate.host_id !== host) continue;
      if (candidate.state !== "timed_out") break;
      consecutiveTimeouts += 1;
    }
    if (consecutiveTimeouts >= 3) {
      return { kind: "timeouts" as const, host };
    }
  }
  return null;
}

function sourceActivity(
  source: SourceResponse,
  events: CallEvent[],
  locale: GuiLocale,
  t: Translate,
  callHistoryUnavailable = false,
): string {
  if (!source.authorized) return t("apps.unlinked");
  const latest = events.find(
    (item) =>
      successfulCall(item) && sourceMatchesHost(source.source_id, item.host_id),
  );
  if (!latest) return "";
  const time = stamped(latest.occurred_at, locale, t);
  const prefix = callHistoryUnavailable ? t("apps.lastRead") : "";
  return `${prefix}${callDetailHeadline(latest, source.display_name, locale)} · ${time}`;
}

function sourceSyncActivity(
  sourceId: string,
  sync: LiveSyncHealth[],
  locale: GuiLocale,
  t: Translate,
): string {
  const current = sync.find((item) => item.source_id === sourceId);
  return current?.last_success_at
    ? t("apps.recentSync", {
        time: stamped(current.last_success_at, locale, t),
      })
    : "";
}

function SourceMark({ source }: { source: SourceResponse }) {
  return (
    <AppMark
      id={source.source_id}
      label={source.display_name}
      fallbackClassName="r6-app-initial"
    />
  );
}

export function ApplicationsHome({
  imports,
  onManage,
  onMemories,
  onCalls,
  active = true,
}: {
  imports: ReturnType<typeof useImportSources>;
  onManage(sourceId?: string): void;
  onMemories(memoryId?: string): void;
  onSettings?(): void;
  onCalls?(): void;
  active?: boolean;
}) {
  const { locale, t, tn } = useGuiLanguage();
  const hostQuery = useRecallHosts({ poll: active });
  const hostBridge = hostBridgeAvailable();
  const hosts = hostQuery.data ?? [];
  const hostState =
    hostQuery.data !== undefined && (hostBridge || hosts.length)
      ? "ready"
      : !hostBridge || hostQuery.isError
        ? "unavailable"
        : "loading";
  // A failed poll keeps the last answer on screen; the page must say it is old.
  const hostStale = hostQuery.isError && hostQuery.data !== undefined;
  const auto = useAutoConnect();
  const sedimentation = useSedimentation();
  const [summary, setSummary] = useState<HostHomeSummary | null>(null);
  const [processing, setProcessing] = useState<MemoryProcessingSummary | null>(
    null,
  );
  const [syncHealth, setSyncHealth] = useState<LiveSyncHealth[]>([]);
  const [summaryFailures, setSummaryFailures] = useState(0);
  const [recentCall, setRecentCall] = useState<CallEvent | null>(null);
  const [callEvents, setCallEvents] = useState<CallEvent[]>([]);
  const [callState, setCallState] = useState<
    "loading" | "ready" | "unavailable" | "disabled" | "incomplete"
  >("loading");
  const [callRevision, setCallRevision] = useState(0);
  const [activityView, setActivityView] = useState<ActivityChoice | null>(null);
  const [restartingBackground, setRestartingBackground] = useState(false);
  useEffect(() => {
    void callRevision;
    if (!active || !onCalls) return;
    let cancelled = false;
    let flight = false;
    const refresh = async () => {
      if (flight) return;
      flight = true;
      try {
        const result = await getCallHistory({ limit: 20, offset: 0 });
        if (cancelled) return;
        setCallEvents(result.items);
        // The home card promises an application identity. Direct diagnostic
        // calls have no host and remain available in call history, but cannot
        // truthfully supply the icon and name required by this surface.
        setRecentCall(result.items.find(attributedSuccessfulCall) ?? null);
        setCallState(
          !result.available
            ? "disabled"
            : result.recording_error
              ? "incomplete"
              : "ready",
        );
      } catch {
        if (!cancelled) setCallState("unavailable");
      } finally {
        flight = false;
      }
    };
    void refresh();
    const stop = subscribePoll({
      key: "calls",
      intervalMs: 10_000,
      run: refresh,
    });
    return () => {
      cancelled = true;
      stop();
    };
  }, [active, onCalls, callRevision]);
  useEffect(() => {
    if (active) void prefetchDataRuntime();
  }, [active]);
  // Re-evaluated on every change; the apply itself only starts where the
  // preference and the real state differ and nothing was tried before.
  // biome-ignore lint/correctness/useExhaustiveDependencies: hostQuery.data is the trigger
  useEffect(() => {
    if (!active) return;
    void autoReconcile({
      sources: imports.sources,
      setSync: imports.setSync,
      refresh: imports.refresh,
    });
  }, [
    active,
    hostQuery.data,
    imports.refresh,
    imports.setSync,
    imports.sources,
  ]);
  const [retained] = useState(() => {
    const owner = imports.refresh ?? imports;
    let state = retainedViews.get(owner);
    if (!state) {
      state = { expanded: false, homeScroll: 0, allScroll: 0, focus: null };
      retainedViews.set(owner, state);
    }
    return state;
  });
  const [expanded, setExpanded] = useState(retained.expanded);
  const [phase, setPhase] = useState("idle");
  const [revision, setRevision] = useState(0);
  const expandButton = useRef<HTMLButtonElement>(null);
  const backButton = useRef<HTMLButtonElement>(null);
  const root = useRef<HTMLElement>(null);
  const overview = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLElement>(null);
  const panel = useRef<HTMLElement>(null);
  const listId = useId();
  const transition = useRef<(next: boolean) => void>(() => {});
  const viewState = useRef(retained);
  const restorePending = useRef<(() => void) | null>(null);
  const touchStartY = useRef<number | null>(null);

  useLayoutEffect(() => {
    const home = root.current;
    const top = overview.current;
    const appsPanel = panel.current;
    const appList = list.current;
    if (!home || !top || !appsPanel || !appList) return;
    const saved = viewState.current;
    setExpanded(saved.expanded);
    setPhase("idle");
    if (!active) return;

    let scrollOwner = home.parentElement;
    while (
      scrollOwner &&
      !/(auto|scroll)/.test(getComputedStyle(scrollOwner).overflowY)
    ) {
      scrollOwner = scrollOwner.parentElement;
    }
    const scroller = scrollOwner ?? document.scrollingElement;
    let layoutExpanded = saved.expanded;
    let desired = saved.expanded;
    let generation = 0;
    let stopped = false;
    let animating = false;
    let animations: Animation[] = [];
    const media = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    const reduced = () =>
      media?.matches ||
      document.documentElement.dataset.nativeReduceMotion === "true";
    const cancel = () => {
      for (const animation of animations) animation.cancel();
      animations = [];
    };
    const animate = (
      element: HTMLElement,
      frames: Keyframe[],
      duration: number,
      easing = "cubic-bezier(.22,1,.36,1)",
    ) => {
      const animation = element.animate(frames, { duration, easing });
      animations.push(animation);
      return animation.finished.catch(() => {});
    };
    const rows = () =>
      Array.from(appList.querySelectorAll<HTMLElement>(".r6-home-row-content"));
    const restoreScroll = () => {
      if (scroller) scroller.scrollTop = desired ? 0 : saved.homeScroll;
      appList.scrollTop = desired ? saved.allScroll : 0;
    };
    const focusDestination = () => {
      const focused = document.activeElement;
      // Respect focus moved by the user to another control while motion was running.
      if (
        focused === document.body ||
        focused === expandButton.current ||
        focused === backButton.current ||
        focused === home
      ) {
        (desired ? backButton.current : expandButton.current)?.focus({
          preventScroll: true,
        });
      }
    };
    const commit = (next: boolean, nextPhase: string) => {
      layoutExpanded = next;
      flushSync(() => {
        setExpanded(next);
        setPhase(nextPhase);
      });
      restoreScroll();
    };
    const settle = (focus: boolean) => {
      ++generation;
      cancel();
      animating = false;
      commit(desired, "idle");
      if (focus) focusDestination();
    };
    const move = async (next: boolean) => {
      if (stopped || desired === next) return;
      const ticket = ++generation;
      const interrupted = animating;
      desired = next;
      saved.expanded = next;
      if (next && !layoutExpanded && !interrupted)
        saved.homeScroll = scroller?.scrollTop ?? 0;
      if (!next && layoutExpanded) saved.allScroll = appList.scrollTop;
      if (reduced() || !appsPanel.animate || document.hidden) {
        settle(true);
        return;
      }
      animating = true;
      if (next && !layoutExpanded && !interrupted) {
        cancel();
        flushSync(() => setPhase("overview-exit"));
        await animate(
          top,
          [
            { opacity: 1, transform: "translateY(0)" },
            { opacity: 0, transform: "translateY(-8px)" },
          ],
          90,
          "cubic-bezier(.4,0,.8,1)",
        );
        if (stopped || ticket !== generation) return;
      }
      // Read the current animated geometry before cancellation so reversal starts here.
      const before = appsPanel.getBoundingClientRect();
      const listBefore = appList.getBoundingClientRect();
      const rowBefore = rows().map((row) => row.getBoundingClientRect());
      cancel();
      commit(next, next ? "expanding" : "collapsing");
      const after = appsPanel.getBoundingClientRect();
      const delta = before.top - after.top;
      const rowAfter = rows().map((row) => row.getBoundingClientRect());
      const jobs = [
        animate(
          appsPanel,
          [
            {
              transform: `translateY(${delta}px)`,
              height: `${before.height}px`,
            },
            { transform: "translateY(0)", height: `${after.height}px` },
          ],
          190,
        ),
      ];
      rows().forEach((row, index) => {
        const previous = rowBefore[index];
        const current = rowAfter[index];
        const wasVisible =
          previous?.height &&
          previous.bottom > listBefore.top &&
          previous.top < listBefore.bottom;
        const shift = wasVisible ? previous.top - current.top - delta : 8;
        jobs.push(
          animate(
            row,
            [
              {
                transform: `translateY(${shift}px)`,
                opacity: wasVisible ? 1 : 0,
              },
              { transform: "translateY(0)", opacity: 1 },
            ],
            190,
          ),
        );
      });
      await Promise.all(jobs);
      if (stopped || ticket !== generation) return;
      cancel();
      if (!next) {
        flushSync(() => setPhase("overview-enter"));
        await animate(
          top,
          [
            { opacity: 0, transform: "translateY(-8px)" },
            { opacity: 1, transform: "translateY(0)" },
          ],
          100,
        );
        if (stopped || ticket !== generation) return;
      }
      cancel();
      animating = false;
      setPhase("idle");
      focusDestination();
    };
    transition.current = (next) => {
      void move(next);
    };
    const preferenceChanged = () => {
      if (animating && reduced()) settle(true);
    };
    const resized = () => {
      if (animating) settle(false);
    };
    const visibilityChanged = () => {
      if (document.hidden && animating) settle(false);
    };
    const keyDown = (event: KeyboardEvent) => {
      if (
        event.key === "Escape" &&
        desired &&
        !event.defaultPrevented &&
        event.target instanceof Node &&
        (home.contains(event.target) ||
          (animating && event.target === document.body))
      ) {
        event.preventDefault();
        void move(false);
      }
    };
    const rememberHomeScroll = () => {
      if (!desired && !animating && scroller)
        saved.homeScroll = scroller.scrollTop;
    };
    const observer = new MutationObserver(preferenceChanged);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-native-reduce-motion"],
    });
    media?.addEventListener("change", preferenceChanged);
    window.addEventListener("resize", resized);
    document.addEventListener("visibilitychange", visibilityChanged);
    document.addEventListener("keydown", keyDown);
    scroller?.addEventListener("scroll", rememberHomeScroll);
    restorePending.current = () => {
      // Activity can hide during the exit stage, before expanded state is committed.
      if (home.dataset.expanded !== String(saved.expanded)) return;
      restorePending.current = null;
      restoreScroll();
      if (!saved.focus) return;
      const destination = Array.from(
        home.querySelectorAll<HTMLElement>("[data-home-focus]"),
      ).find(
        (element) =>
          element.dataset.homeFocus === saved.focus &&
          !element.closest("[hidden]"),
      );
      (
        destination ?? (desired ? backButton.current : expandButton.current)
      )?.focus({ preventScroll: true });
    };
    return () => {
      stopped = true;
      ++generation;
      cancel();
      transition.current = () => {};
      restorePending.current = null;
      observer.disconnect();
      media?.removeEventListener("change", preferenceChanged);
      window.removeEventListener("resize", resized);
      document.removeEventListener("visibilitychange", visibilityChanged);
      document.removeEventListener("keydown", keyDown);
      scroller?.removeEventListener("scroll", rememberHomeScroll);
    };
  }, [active]);
  useLayoutEffect(() => {
    restorePending.current?.();
  });
  const stale = imports.sourceSnapshot?.stale === true;
  const sourceVersion = imports.sources
    .map((s) => `${s.source_id}:${s.authorized}:${s.grant_revision}`)
    .join("|");
  const requestKey = `${sourceVersion}:${revision}`;
  // requestKey retriggers this read when the source snapshot or a manual retry changes.
  // biome-ignore lint/correctness/useExhaustiveDependencies: requestKey is the refresh token
  useEffect(() => {
    if (!active) return;
    let stopped = false,
      busy = false;
    async function refresh() {
      if (busy || document.hidden) return;
      busy = true;
      const [home, state, sync] = await Promise.allSettled([
        getHomeSummary(),
        getMemoryProcessing(),
        getLiveSyncHealth(),
        refreshSedimentation(),
      ]);
      busy = false;
      if (stopped) return;
      if (home.status === "fulfilled") {
        setSummaryFailures(0);
        // stale=true 表示这轮读取超时，数字不代表现状：保留上一次的概览，
        // 等 10 秒后的下一轮轮询补上，绝不清零也不报错。
        if (!("stale" in home.value) || !home.value.stale)
          setSummary(home.value);
      } else {
        setSummaryFailures((count) => count + 1);
      }
      if (state.status === "fulfilled") setProcessing(state.value);
      if (sync.status === "fulfilled") setSyncHealth(sync.value);
    }
    void refresh();
    const stop = subscribePoll({
      key: "applications",
      intervalMs: 10_000,
      run: refresh,
    });
    return () => {
      stopped = true;
      stop();
    };
  }, [active, requestKey]);
  const visible = summary;
  const latest = visible?.latest;
  const summaryUnavailable = visible === null && summaryFailures >= 2;
  const hasCall =
    recentCall !== null &&
    (callState === "ready" || callState === "incomplete");
  const hasMemory = latest !== null && latest !== undefined;
  const updateKind: "memory" | null = hasMemory ? "memory" : null;
  const availableActivities: ActivityChoice[] = [
    ...(hasCall ? (["call"] as const) : []),
    ...(updateKind ? (["update"] as const) : []),
  ];
  const pinned =
    activityView !== null && availableActivities.includes(activityView)
      ? activityView
      : null;
  const historyImporting = imports.jobs.some(
    (job) => job.status === "running" || job.status === "pending",
  );
  const recentCallSourceId = (recentCall?.references ?? []).find(
    (reference) => reference.source_id,
  )?.source_id;
  const recentCallSource = imports.sources.find(
    (source) => source.source_id === recentCallSourceId,
  );
  const recentCallHost = imports.sources.find((source) =>
    sourceMatchesHost(source.source_id, recentCall?.host_id ?? null),
  );
  const recentCallHostName = recentCall
    ? (recentCallHost?.display_name ?? callHostName(recentCall.host_id, locale))
    : null;
  const attention = callAttention(callEvents);
  const attentionHost = attention ? callHostName(attention.host, locale) : null;
  const activity = summaryUnavailable
    ? "unavailable"
    : visible === null
      ? "loading"
      : pinned === "call"
        ? "call"
        : pinned === "update"
          ? updateKind
          : updateKind
            ? updateKind
            : hasCall
              ? "call"
              : callState === "unavailable"
                ? "unavailable"
                : historyImporting
                  ? "importing"
                  : callState === "loading"
                    ? recentCall !== null
                      ? "call"
                      : "loading"
                    : processing?.running
                      ? "first-running"
                      : processing?.pending || processing?.waiting_for_model
                        ? "first-queued"
                        : "empty";
  const apps = imports.sources.filter(
    (s) => s.authorized || s.detected === true || s.location_state === "found",
  );
  const stateLabel =
    sedimentation?.enabled === true
      ? t("apps.running")
      : sedimentation?.enabled === false
        ? t("apps.paused")
        : t("apps.checkingStatus");
  const stateDetail =
    sedimentation?.enabled === false
      ? t("apps.stoppedNew")
      : sedimentation?.enabled === true
        ? t("apps.organizingNew")
        : t("apps.checkingProcessing");
  const latestUpdatedAt =
    latest === null || latest === undefined
      ? null
      : "revision" in latest
        ? latest.updated_at
        : latest.gmt_created === null
          ? null
          : new Date(latest.gmt_created * 1000).toISOString();
  const latestClock = latestUpdatedAt
    ? clockParts(latestUpdatedAt, locale)
    : null;
  const latestSourceIds =
    latest === null || latest === undefined
      ? []
      : "revision" in latest
        ? latest.source_ids
        : latest.source
          ? [latest.source]
          : [];
  const latestSourceNames = latestSourceIds.map(
    (id) =>
      imports.sources.find((source) => source.source_id === id)?.display_name ??
      id,
  );
  const time = latestClock?.clock ?? (latest ? t("apps.unknownTime") : null);
  const checked = visible ? clockParts(visible.observed_at, locale) : null;
  // A failed read leaves the last health in place on purpose: clearing it would
  // drop the instance it names, and that instance is what keeps memory writes
  // and backfill receipts attached to the run that produced them. So the card
  // needs its own answer to "is this still true", rather than reading one off
  // an object that is being kept precisely because it may be out of date.
  const connected = Boolean(imports.health) && !imports.healthStale;
  const admitted = hosts.filter(hostConnectionApproved).length;
  const unchecked = hosts.filter(hostConnectionUnknown);
  const hostReadFailed = hostBridge && hostState === "unavailable";
  const preferences = readConnectionPreferences();
  const accessPending =
    hostState === "loading" ||
    (!hostBridge && hostState !== "ready") ||
    (admitted === 0 && hostApplyPending(auto, preferences.hostSourceIds));
  const accessText = accessPending
    ? t("apps.checkingNow")
    : hostReadFailed
      ? t("apps.unchecked")
      : !unchecked.length
        ? tn("apps.connectedCount", admitted)
        : admitted
          ? tn("apps.mixedCount", admitted, {
              pending: new Intl.NumberFormat(locale).format(unchecked.length),
            })
          : tn("apps.pendingCount", unchecked.length);
  const accessUnknownText = accessPending
    ? null
    : hostReadFailed
      ? t("apps.unknownAccess")
      : unchecked.length
        ? t("apps.unknownPermissions", {
            names: unchecked.map((host) => host.label).join(", "),
          })
        : null;
  async function restartBackground() {
    if (restartingBackground) return;
    setRestartingBackground(true);
    try {
      if (!window.oneMemory?.restartBackground)
        throw new Error("runtime_restart_unavailable");
      await window.oneMemory.restartBackground();
      setRevision((value) => value + 1);
      setCallRevision((value) => value + 1);
    } catch {
      setCallRevision((value) => value + 1);
    } finally {
      setRestartingBackground(false);
    }
  }
  const nameOf = (sourceId: string) =>
    imports.sources.find((source) =>
      sameConnectionHost(source.source_id, sourceId),
    )?.display_name ?? sourceId;
  const autoFailures = auto.failures.filter((failure) =>
    failure.kind === "host"
      ? preferences.hostSourceIds.some((id) =>
          sameConnectionHost(id, failure.sourceId),
        ) &&
        !hosts.some(
          (host) =>
            hostConnectionApproved(host) &&
            hostMatchesSource(failure.sourceId, host.hostId),
        )
      : preferences.syncSourceIds.includes(failure.sourceId) &&
        !imports.sources.some(
          (source) =>
            source.source_id === failure.sourceId && source.sync_enabled,
        ),
  );
  const localizeFailureReason = (reason: string | null | undefined) =>
    reason && isMessageKey(reason) ? t(reason) : (reason ?? "");
  const autoFailureText = autoFailures
    .map((failure) =>
      failure.kind === "host"
        ? t("apps.autoHostFailed", {
            name: nameOf(failure.sourceId),
            reason: failure.reason
              ? t("apps.failureReason", {
                  reason: localizeFailureReason(failure.reason),
                })
              : "",
          })
        : t("apps.autoSyncFailed", {
            name: nameOf(failure.sourceId),
            reason: localizeFailureReason(failure.reason),
          }),
    )
    .join("");
  const authorizedSyncSources = imports.sources.filter(
    (source) => source.authorized && source.sync_enabled,
  );
  const wantsSync = preferences.syncSourceIds.length > 0;
  const syncApplyFailed = autoFailures.some(
    (failure) => failure.kind === "sync",
  );
  const syncState = authorizedSyncSources.length
    ? tn("apps.enabledCount", authorizedSyncSources.length)
    : syncApplyFailed
      ? t("apps.enableFailed")
      : wantsSync || !visible
        ? t("apps.checkingState")
        : t("apps.notEnabled");
  function toggleAll(next: boolean) {
    transition.current(next);
  }
  return (
    <section
      className="r6-home"
      ref={root}
      aria-label={t("apps.home")}
      data-expanded={expanded}
      data-phase={phase}
      onFocusCapture={(event) => {
        viewState.current.focus =
          (event.target as HTMLElement).dataset.homeFocus ?? null;
      }}
    >
      <header className="r6-home-heading">
        <h1>{expanded ? t("apps.allApps") : t("apps.apps")}</h1>
      </header>
      <button
        type="button"
        ref={backButton}
        data-home-focus="back"
        className="r6-home-back r6-button"
        hidden={!expanded}
        data-variant="quiet"
        onClick={() => toggleAll(false)}
      >
        <span className="r6-button-label">
          <ArrowLeft size={16} aria-hidden="true" />
          {t("apps.back")}
        </span>
      </button>
      <div
        className="r6-home-top"
        ref={overview}
        hidden={expanded}
        inert={expanded || phase !== "idle"}
      >
        <HomeCardBoundary label={t("apps.overview")}>
          <section className="r6-home-overview" aria-label={t("apps.overview")}>
            <header>
              <h2>{t("apps.overview")}</h2>
            </header>
            <div className="r6-home-metrics">
              <div>
                <p>{t("apps.memories")}</p>
                <p className="r6-home-figure">
                  <strong>
                    {new Intl.NumberFormat(locale).format(
                      visible?.memory_count ?? 0,
                    )}
                  </strong>
                  {t("apps.unit") ? <i>{t("apps.unit")}</i> : null}
                </p>
                <small {...(visible ? {} : { role: "status" })}>
                  {visible
                    ? t("apps.scope")
                    : summaryUnavailable
                      ? t("apps.countWillUpdate")
                      : summaryFailures
                        ? t("apps.startingRetry")
                        : t("apps.readingCount")}
                </small>
              </div>
              <div>
                <p>{t("apps.processing")}</p>
                <strong className="r6-home-word">{stateLabel}</strong>
                <small>{stateDetail}</small>
              </div>
              <div>
                <p>{t("apps.recentUpdate")}</p>
                <strong className={time ? "r6-home-time" : "r6-home-word"}>
                  {time ??
                    (visible
                      ? t("apps.notGenerated")
                      : t("apps.checkingRecent"))}
                </strong>
                <small>
                  {time
                    ? `${latestClock?.today ? `${t("apps.today")}· ` : ""}${t("apps.saved")}`
                    : visible
                      ? t("apps.separate")
                      : t("apps.recentWillUpdate")}
                </small>
              </div>
            </div>
            <div className="r6-home-activity">
              {availableActivities.length > 1 &&
              (activity === "call" || activity === "memory") ? (
                <div className="r6-home-activity-tabs" role="tablist">
                  {(["update", "call"] as const).map((item) => (
                    <button
                      type="button"
                      role="tab"
                      key={item}
                      aria-selected={
                        item === "call"
                          ? activity === "call"
                          : activity === "memory"
                      }
                      onClick={() => setActivityView(item)}
                    >
                      {item === "call"
                        ? t("apps.recentCall")
                        : t("apps.recentUpdate")}
                    </button>
                  ))}
                </div>
              ) : (
                <p>
                  {activity === "unavailable"
                    ? t("apps.activityUnavailable")
                    : activity === "call"
                      ? t("apps.recentCall")
                      : activity === "memory"
                        ? t("apps.recentUpdate")
                        : t("apps.firstBatch")}
                </p>
              )}
              <div>
                {activity === "call" && recentCall ? (
                  <div className="r6-home-call-direction" aria-hidden="true">
                    {recentCallSource ? (
                      <>
                        <AppMark id={recentCallSource.source_id} size={26} />
                        <ArrowRight size={22} />
                      </>
                    ) : null}
                    <AppMark
                      id={
                        recentCallHost?.source_id ??
                        recentCall.host_id ??
                        "unknown"
                      }
                      size={26}
                    />
                  </div>
                ) : activity === "memory" && latestSourceIds[0] ? (
                  (() => {
                    const source = imports.sources.find(
                      (item) => item.source_id === latestSourceIds[0],
                    );
                    return source ? <SourceMark source={source} /> : null;
                  })()
                ) : null}
                <section>
                  <h3>
                    {activity === "unavailable"
                      ? callState === "unavailable"
                        ? t("apps.callsUnavailable")
                        : t("apps.memoryUnavailable")
                      : activity === "call" && recentCall
                        ? callDetailHeadline(
                            recentCall,
                            recentCallHostName ?? undefined,
                            locale,
                          )
                        : activity === "memory" && latest
                          ? `${latest.content}${"truncated" in latest && latest.truncated ? "…" : ""}`
                          : activity === "importing"
                            ? t("apps.importing")
                            : activity === "first-running"
                              ? t("apps.firstRunning")
                              : activity === "first-queued"
                                ? t("apps.firstQueued")
                                : activity === "loading"
                                  ? t("apps.readingActivity")
                                  : t("apps.emptyMemories")}
                  </h3>
                  <small>
                    {activity === "unavailable"
                      ? callState === "unavailable"
                        ? t("apps.callReadFailure")
                        : t("apps.memoryReadFailure")
                      : activity === "call" && recentCall
                        ? `${recentCallHostName} · ${stamped(recentCall.occurred_at, locale, t)} · ${callRowDetail(recentCall, locale)}${callState === "incomplete" ? t("apps.incompleteCalls") : ""}`
                        : activity === "memory" && latest
                          ? [time, latestSourceNames.join(", ")]
                              .filter(Boolean)
                              .join(" · ")
                          : activity === "importing"
                            ? t("apps.savingConversations")
                            : activity === "loading"
                              ? t("apps.recentWillUpdate")
                              : t("apps.waitingMemories")}
                  </small>
                </section>
                {(activity === "unavailable" ||
                  (activity === "call" && CALL_HISTORY_ENTRY_VISIBLE)) && (
                  <button
                    type="button"
                    className="r6-home-activity-link"
                    onClick={
                      activity === "unavailable"
                        ? () => {
                            setRevision((n) => n + 1);
                            setCallRevision((n) => n + 1);
                          }
                        : activity === "call"
                          ? onCalls
                          : () => onMemories(latest?.memory_id)
                    }
                  >
                    {activity === "unavailable"
                      ? t("apps.reread")
                      : activity === "call"
                        ? t("apps.viewCalls")
                        : t("apps.viewMemory")}
                    {activity === "unavailable" ? null : (
                      <ArrowRight size={14} aria-hidden="true" />
                    )}
                  </button>
                )}
              </div>
            </div>
          </section>
        </HomeCardBoundary>
        {attention ? (
          <section className="r6-home-auto-failure" role="alert">
            <p>
              {attention.kind === "unavailable"
                ? t("apps.backgroundDown")
                : t("apps.timeoutWarning", { host: attentionHost ?? "" })}
            </p>
            {attention.kind === "unavailable" || CALL_HISTORY_ENTRY_VISIBLE ? (
              <R6Button
                variant="quiet"
                pending={
                  attention.kind === "unavailable" && restartingBackground
                }
                onClick={
                  attention.kind === "unavailable"
                    ? () => void restartBackground()
                    : onCalls
                }
              >
                {attention.kind === "unavailable"
                  ? t("apps.restart")
                  : t("apps.viewCalls")}
              </R6Button>
            ) : null}
          </section>
        ) : null}
        <HomeCardBoundary label={t("apps.runtime")}>
          <aside className="r6-home-runtime">
            <h2>{t("apps.runtime")}</h2>
            <h3>
              {connected
                ? t("apps.backgroundRunning")
                : imports.healthStale
                  ? t("apps.connectionUnchecked")
                  : t("apps.disconnected")}
            </h3>
            <p>{t("apps.authorizedOnly")}</p>
            <dl>
              <div>
                <dt>{t("apps.sync")}</dt>
                <dd>{syncState}</dd>
              </div>
              <div>
                <dt>{t("apps.organizing")}</dt>
                <dd>{stateLabel}</dd>
              </div>
              <div>
                <dt>{t("apps.access")}</dt>
                <dd>{accessText}</dd>
              </div>
            </dl>
            {accessUnknownText ? (
              <div
                role="status"
                aria-label={t("apps.checkingAccess")}
                className="r6-home-auto-failure"
              >
                <p>{accessUnknownText}</p>
                <button
                  type="button"
                  className="r6-home-activity-link"
                  disabled={hostQuery.isFetching}
                  onClick={() => void hostQuery.refetch()}
                >
                  {hostQuery.isFetching
                    ? t("apps.checking")
                    : t("apps.recheck")}
                </button>
              </div>
            ) : null}
            {hostStale ? (
              <p
                role="status"
                aria-label={t("apps.accessState")}
                className="r6-home-auto-failure"
              >
                {t("apps.staleAccess")}
              </p>
            ) : null}
            {autoFailureText ? (
              <div role="alert" className="r6-home-auto-failure">
                <p>{`${autoFailureText}${t("apps.noAutoRetry")}`}</p>
                <button
                  type="button"
                  className="r6-home-activity-link"
                  disabled={auto.running.length > 0}
                  onClick={() =>
                    void retryAutoReconcile({
                      sources: imports.sources,
                      setSync: imports.setSync,
                      refresh: imports.refresh,
                    })
                  }
                >
                  {t("apps.reconnect")}
                </button>
              </div>
            ) : null}
            <p className="r6-home-checked">
              {!checked
                ? t("apps.checkedAt", { time: t("apps.unchecked") })
                : t("apps.checkedAt", {
                    time: `${checked.today ? t("apps.today") : ""}${checked.clock}`,
                  })}
            </p>
          </aside>
        </HomeCardBoundary>
      </div>
      <section
        className="r6-home-apps"
        ref={panel}
        aria-label={t("apps.myApps")}
        onWheel={(event) => {
          if (!expanded && apps.length > 3 && event.deltaY > 10)
            toggleAll(true);
          else if (
            expanded &&
            event.deltaY < -10 &&
            (list.current?.scrollTop ?? 0) <= 0
          )
            toggleAll(false);
        }}
        onTouchStart={(event) => {
          touchStartY.current = event.touches[0]?.clientY ?? null;
        }}
        onTouchEnd={(event) => {
          const start = touchStartY.current;
          touchStartY.current = null;
          if (start === null) return;
          const end = event.changedTouches[0]?.clientY;
          if (end === undefined) return;
          const distance = start - end;
          if (!expanded && apps.length > 3 && distance > 24) toggleAll(true);
          else if (
            expanded &&
            distance < -24 &&
            (list.current?.scrollTop ?? 0) <= 0
          )
            toggleAll(false);
        }}
      >
        <header>
          <h2>
            {expanded ? tn("apps.appCount", apps.length) : t("apps.myApps")}
          </h2>
          {apps.length > 3 ? (
            <button
              type="button"
              ref={expandButton}
              data-home-focus="all"
              hidden={expanded}
              aria-expanded={expanded}
              aria-controls={listId}
              className="r6-home-expand"
              onClick={() => toggleAll(true)}
            >
              {tn("apps.viewAll", apps.length)}
            </button>
          ) : null}
        </header>
        <section
          className="r6-home-list"
          ref={list}
          id={listId}
          aria-label={t("apps.list")}
          tabIndex={expanded ? 0 : -1}
          onScroll={(event) => {
            if (expanded)
              viewState.current.allScroll = event.currentTarget.scrollTop;
          }}
        >
          {(expanded ? apps : apps.slice(0, 3)).map((source) => {
            const activity =
              sourceActivity(
                source,
                callEvents,
                locale,
                t,
                callState === "unavailable",
              ) || sourceSyncActivity(source.source_id, syncHealth, locale, t);
            const host = hosts.find((item) =>
              sourceMatchesHost(source.source_id, item.hostId),
            );
            return (
              <div className="r6-home-app-row" key={source.source_id}>
                <div className="r6-home-row-content">
                  <SourceMark source={source} />
                  <section>
                    <h3>{source.display_name}</h3>
                    <p>
                      {source.sync_enabled
                        ? t("apps.syncEnabled")
                        : host?.installed &&
                            host.permission?.state === "approved"
                          ? t("apps.recallAllowed")
                          : source.authorized
                            ? t("apps.historyAllowed")
                            : t("apps.connectionPending")}
                    </p>
                  </section>
                  {activity ? (
                    <p className="r6-home-app-meta">{activity}</p>
                  ) : null}
                  <R6Button
                    variant="quiet"
                    data-home-focus={`source:${source.source_id}`}
                    onClick={() => onManage(source.source_id)}
                  >
                    {t("apps.details")}
                  </R6Button>
                </div>
              </div>
            );
          })}
          {!apps.length ? (
            <p className="r6-home-empty">
              {stale ? t("apps.listUnavailable") : t("apps.emptyApps")}
            </p>
          ) : null}
        </section>
      </section>
    </section>
  );
}
