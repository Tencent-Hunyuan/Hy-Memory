import type {
  HomeSummary,
  LiveSyncHealth,
  MemoryProcessingSummary,
  RecentConversationItem,
  SourceResponse,
} from "@one-memory/contracts";
import { ArrowLeft, ArrowRight } from "lucide-react";
import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import {
  getHomeSummary,
  getLiveSyncHealth,
  getMemoryProcessing,
  getRecentConversations,
} from "./api";
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

// 2026-10-12: Codex 侧曾把 MCP 调试帧（如 `[write_stdout] {"chars":…`）存成
// 对话摘要，首页“最近更新”会原样露出机器文本。展示层拦掉这类帧，宁可退回
// 显示记忆或空态，也不把协议帧当动态内容。
const DEBUG_FRAME_PREFIX = /^\s*\[(?:write_stdout|write_stderr|read_request)\b/;
function presentableDialogue(text: string): boolean {
  return !DEBUG_FRAME_PREFIX.test(text);
}

const retainedViews = new WeakMap<object, HomeViewState>();

function clockParts(value: string) {
  const ms = Date.parse(value);
  if (!Number.isFinite(ms)) return null;
  const date = new Date(ms);
  const now = new Date();
  return {
    clock: new Intl.DateTimeFormat("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
    }).format(date),
    today:
      date.getFullYear() === now.getFullYear() &&
      date.getMonth() === now.getMonth() &&
      date.getDate() === now.getDate(),
  };
}

function stamped(value: string, suffix = "") {
  const parts = clockParts(value);
  if (!parts) return `时间未知${suffix}`;
  return `${parts.today ? "今天 " : ""}${parts.clock}${suffix}`;
}

function memoryAction(action: string | undefined) {
  if (action === "create") return "新增记忆";
  if (action === "correct" || action === "restore") return "更新记忆";
  return "记忆已保存";
}

type ActivityChoice = "call" | "update";

function sourceMatchesHost(sourceId: string, hostId: string | null) {
  if (!hostId) return false;
  return hostMatchesSource(sourceId, hostId);
}

function successfulCall(item: CallEvent) {
  return item.state === "grounded" || item.state === "empty";
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
  callHistoryUnavailable = false,
): string {
  if (!source.authorized) return "尚未关联";
  const latest = events.find(
    (item) =>
      successfulCall(item) && sourceMatchesHost(source.source_id, item.host_id),
  );
  if (!latest) return "";
  const time = stamped(latest.occurred_at);
  const prefix = callHistoryUnavailable ? "上次读取：" : "";
  return `${prefix}${callDetailHeadline(latest, source.display_name)} · ${time}`;
}

function sourceSyncActivity(sourceId: string, sync: LiveSyncHealth[]): string {
  const current = sync.find((item) => item.source_id === sourceId);
  return current?.last_success_at
    ? `最近同步 ${stamped(current.last_success_at)}`
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
  /** 保留兼容旧调用方；“查看会话”入口已按产品要求下线。 */
  onConversations?(): void;
  onSettings?(): void;
  onCalls?(): void;
  active?: boolean;
}) {
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
  const [summary, setSummary] = useState<HomeSummary | null>(null);
  const [processing, setProcessing] = useState<MemoryProcessingSummary | null>(
    null,
  );
  const [syncHealth, setSyncHealth] = useState<LiveSyncHealth[]>([]);
  const [failed, setFailed] = useState(false);
  const [recentCall, setRecentCall] = useState<CallEvent | null>(null);
  const [callEvents, setCallEvents] = useState<CallEvent[]>([]);
  const [callState, setCallState] = useState<
    "loading" | "ready" | "unavailable" | "disabled" | "incomplete"
  >("loading");
  const [callRevision, setCallRevision] = useState(0);
  const [latestDialogue, setLatestDialogue] =
    useState<RecentConversationItem | null>(null);
  const [dialogueState, setDialogueState] = useState<
    "loading" | "ready" | "unavailable"
  >("loading");
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
        setRecentCall(result.items.find(successfulCall) ?? null);
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
      const [home, state, sync, recent] = await Promise.allSettled([
        getHomeSummary(),
        getMemoryProcessing(),
        getLiveSyncHealth(),
        getRecentConversations({ limit: 1, recentActivity: true }),
      ]);
      busy = false;
      if (stopped) return;
      if (home.status === "fulfilled") {
        setFailed(false);
        // stale=true 表示这轮读取超时，数字不代表现状：保留上一次的概览，
        // 等 10 秒后的下一轮轮询补上，绝不清零也不报错。
        if (!home.value.stale) setSummary(home.value);
      } else {
        setFailed(true);
      }
      if (state.status === "fulfilled") setProcessing(state.value);
      if (sync.status === "fulfilled") setSyncHealth(sync.value);
      if (recent.status === "fulfilled") {
        setLatestDialogue(recent.value.items[0] ?? null);
        setDialogueState("ready");
      } else {
        setDialogueState("unavailable");
      }
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
  const summaryUnavailable = failed;
  const hasCall =
    recentCall !== null &&
    (callState === "ready" || callState === "incomplete");
  const hasMemory = latest !== null && latest !== undefined;
  const dialogueReady =
    dialogueState === "ready" &&
    latestDialogue !== null &&
    presentableDialogue(latestDialogue.excerpt);
  const updateKind: "memory" | "dialogue" | null = dialogueReady
    ? "dialogue"
    : hasMemory
      ? "memory"
      : null;
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
  const attention = callAttention(callEvents);
  const attentionHost = attention ? callHostName(attention.host) : null;
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
  const stateLabel = processing?.running
    ? "整理中"
    : processing?.blocked_stages?.some((stage) => stage.count > 0)
      ? "排队处理中"
      : processing?.waiting_for_model
        ? "等待模型"
        : processing?.failed
          ? "需要处理"
          : processing?.pending
            ? "准备中"
            : processing
              ? "一切就绪"
              : "待核对";
  const latestClock = latest ? clockParts(latest.updated_at) : null;
  const time = latestClock?.clock ?? (latest ? "时间未知" : null);
  const checked = visible ? clockParts(visible.observed_at) : null;
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
    ? "正在核对中"
    : hostReadFailed
      ? "待核对"
      : !unchecked.length
        ? `${admitted} 个应用已接入`
        : admitted
          ? `${admitted} 个应用已接入，${unchecked.length} 个待核对`
          : `${unchecked.length} 个应用待核对`;
  const accessUnknownText = accessPending
    ? null
    : hostReadFailed
      ? "应用接入状态暂时没有读到，无法确认有几个应用已接入。"
      : unchecked.length
        ? `${unchecked.map((host) => host.label).join("、")} 的记忆权限暂时没有读到，无法确认是否已接入。`
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
  const autoFailureText = autoFailures
    .map((failure) =>
      failure.kind === "host"
        ? `${nameOf(failure.sourceId)} 未能自动接入${failure.reason ? `：${failure.reason}` : ""}。`
        : `${nameOf(failure.sourceId)} 的持续同步未能自动开启。${failure.reason ?? ""}`,
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
    ? `${authorizedSyncSources.length} 个应用已开启`
    : syncApplyFailed
      ? "未能开启"
      : wantsSync || !visible
        ? "正在检查状态"
        : "尚未开启";
  function toggleAll(next: boolean) {
    transition.current(next);
  }
  return (
    <section
      className="r6-home"
      ref={root}
      aria-label="应用首页"
      data-expanded={expanded}
      data-phase={phase}
      onFocusCapture={(event) => {
        viewState.current.focus =
          (event.target as HTMLElement).dataset.homeFocus ?? null;
      }}
    >
      <header className="r6-home-heading">
        <h1>{expanded ? "全部应用" : "应用"}</h1>
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
          返回首页
        </span>
      </button>
      <div
        className="r6-home-top"
        ref={overview}
        hidden={expanded}
        inert={expanded || phase !== "idle"}
      >
        <HomeCardBoundary label="记忆概览">
          <section className="r6-home-overview" aria-label="记忆概览">
            <header>
              <h2>记忆概览</h2>
            </header>
            <div className="r6-home-metrics">
              <div>
                <p>已沉淀记忆</p>
                {/* The figure slot used to hold the word 读取中 while the first
                  read was in flight, which is how "大数字不可见" (D51) shows up
                  on a slow link: the biggest type on the page spends the first
                  frame saying something that is not a number. A bar of the same
                  height reserves the digits instead, and the wording moves to a
                  live region so screen readers still hear it. 暂无法读取 stays a
                  word, because that is a settled state rather than progress. */}
                {!visible && !summaryUnavailable ? (
                  <>
                    <p className="r6-home-figure">
                      <span
                        className="r6-home-figure-pending"
                        aria-hidden="true"
                      />
                    </p>
                    <small role="status">正在读取已沉淀记忆数量…</small>
                  </>
                ) : (
                  <>
                    <p className="r6-home-figure">
                      <strong className={visible ? "" : "r6-home-word"}>
                        {visible
                          ? new Intl.NumberFormat("zh-CN").format(
                              visible.memory_count,
                            )
                          : "暂无法读取"}
                      </strong>
                      {visible ? <i>条</i> : null}
                    </p>
                    <small>按应用授权范围使用</small>
                  </>
                )}
              </div>
              <div>
                <p>沉淀状态</p>
                <strong className="r6-home-word">{stateLabel}</strong>
                <small>
                  {processing?.running
                    ? "新的对话正在整理"
                    : processing?.pending
                      ? "等待队列开始处理"
                      : "以当前处理状态为准"}
                </small>
              </div>
              <div>
                <p>最近更新</p>
                <strong className={time ? "r6-home-time" : "r6-home-word"}>
                  {time ?? (visible ? "尚未生成" : "待核对")}
                </strong>
                <small>
                  {time
                    ? `${latestClock?.today ? "今天 · " : ""}记忆已保存`
                    : "历史与记忆分别处理"}
                </small>
              </div>
            </div>
            <div className="r6-home-activity">
              {availableActivities.length > 1 &&
              (activity === "call" ||
                activity === "memory" ||
                activity === "dialogue") ? (
                <div className="r6-home-activity-tabs" role="tablist">
                  {(["update", "call"] as const).map((item) => (
                    <button
                      type="button"
                      role="tab"
                      key={item}
                      aria-selected={
                        item === "call"
                          ? activity === "call"
                          : activity === "memory" || activity === "dialogue"
                      }
                      onClick={() => setActivityView(item)}
                    >
                      {item === "call" ? "最近调用" : "最近更新"}
                    </button>
                  ))}
                </div>
              ) : (
                <p>
                  {activity === "unavailable"
                    ? "动态暂不可用"
                    : activity === "call"
                      ? "最近调用"
                      : activity === "dialogue" || activity === "memory"
                        ? "最近更新"
                        : "首批沉淀"}
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
                ) : activity === "memory" && latest?.source_ids[0] ? (
                  (() => {
                    const source = imports.sources.find(
                      (item) => item.source_id === latest.source_ids[0],
                    );
                    return source ? <SourceMark source={source} /> : null;
                  })()
                ) : activity === "dialogue" && latestDialogue ? (
                  (() => {
                    const source = imports.sources.find(
                      (item) => item.source_id === latestDialogue.source_id,
                    );
                    return source ? <SourceMark source={source} /> : null;
                  })()
                ) : null}
                <section>
                  <h3>
                    {activity === "unavailable"
                      ? callState === "unavailable"
                        ? "暂时无法读取调用记录"
                        : "暂时无法读取记忆状态"
                      : activity === "call" && recentCall
                        ? callDetailHeadline(
                            recentCall,
                            recentCallHost?.display_name ??
                              callHostName(recentCall.host_id),
                          )
                        : activity === "memory" && latest
                          ? `${latest.content}${latest.truncated ? "…" : ""}`
                          : activity === "dialogue" && latestDialogue
                            ? `${latestDialogue.excerpt}${latestDialogue.truncated ? "…" : ""}`
                            : activity === "importing"
                              ? "对话持续导入中，即将沉淀你的记忆"
                              : activity === "first-running"
                                ? "正在沉淀第一批记忆"
                                : activity === "first-queued"
                                  ? "准备沉淀第一批记忆"
                                  : activity === "loading"
                                    ? "正在读取动态"
                                    : "还没有已生成的记忆"}
                  </h3>
                  <small>
                    {activity === "unavailable"
                      ? callState === "unavailable"
                        ? "已有记忆仍可查看；重试不会发起新的调用。"
                        : "读取失败不会当作没有记忆。"
                      : activity === "call" && recentCall
                        ? `${stamped(recentCall.occurred_at)} · ${callRowDetail(recentCall)}${callState === "incomplete" ? " · 有几次调用没被记下来，列表可能不全" : ""}`
                        : activity === "memory" && latest
                          ? `${time} · ${memoryAction(latest.action)} · ${latest.source_ids.map((id) => imports.sources.find((s) => s.source_id === id)?.display_name ?? id).join("、")} · 版本 ${latest.revision}`
                          : activity === "dialogue" && latestDialogue
                            ? `${clockParts(latestDialogue.created_at ?? latestDialogue.imported_at)?.clock ?? "时间未知"} ${latestDialogue.time_basis === "conversation" ? "对话时间" : "导入时间"} · ${imports.sources.find((source) => source.source_id === latestDialogue.source_id)?.display_name ?? latestDialogue.source_id}`
                            : activity === "importing"
                              ? "对话正在后台持续保存。"
                              : "历史已保存，记忆生成后会显示在这里。"}
                  </small>
                </section>
                {(activity === "unavailable" ||
                  (activity === "call" && CALL_HISTORY_ENTRY_VISIBLE) ||
                  activity === "memory") && (
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
                      ? "重新读取"
                      : activity === "call"
                        ? "查看记录"
                        : "查看记忆"}
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
                ? "后台服务没有运行，应用暂时查不到记忆"
                : `${attentionHost} 最近几次没查到记忆，后台可能正忙，稍后会自动恢复`}
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
                {attention.kind === "unavailable" ? "重新启动" : "查看记录"}
              </R6Button>
            ) : null}
          </section>
        ) : null}
        <HomeCardBoundary label="运行状态">
          <aside className="r6-home-runtime">
            <h2>运行状态</h2>
            <h3>
              {connected
                ? "后台运行中"
                : imports.healthStale
                  ? "连接状态待核对"
                  : "服务未连接"}
            </h3>
            <p>仅处理已授权范围</p>
            <dl>
              <div>
                <dt>对话同步</dt>
                <dd>{syncState}</dd>
              </div>
              <div>
                <dt>记忆整理</dt>
                <dd>{stateLabel}</dd>
              </div>
              <div>
                <dt>应用访问</dt>
                <dd>{accessText}</dd>
              </div>
            </dl>
            {accessUnknownText ? (
              <div
                role="status"
                aria-label="应用接入核对"
                className="r6-home-auto-failure"
              >
                <p>{accessUnknownText}</p>
                <button
                  type="button"
                  className="r6-home-activity-link"
                  disabled={hostQuery.isFetching}
                  onClick={() => void hostQuery.refetch()}
                >
                  {hostQuery.isFetching ? "核对中…" : "重新核对"}
                </button>
              </div>
            ) : null}
            {hostStale ? (
              <p
                role="status"
                aria-label="应用接入状态"
                className="r6-home-auto-failure"
              >
                应用接入状态暂时读取失败，当前为上次状态。
              </p>
            ) : null}
            {autoFailureText ? (
              <div role="alert" className="r6-home-auto-failure">
                <p>{`${autoFailureText}不会自动重试。`}</p>
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
                  重新接入
                </button>
              </div>
            ) : null}
            <p className="r6-home-checked">
              {!checked
                ? "上次核对：待核对"
                : `上次核对：${checked.today ? "今天 " : ""}${checked.clock}`}
            </p>
          </aside>
        </HomeCardBoundary>
      </div>
      <section
        className="r6-home-apps"
        ref={panel}
        aria-label="我的应用"
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
          <h2>{expanded ? `${apps.length} 个应用` : "我的应用"}</h2>
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
              查看全部 {apps.length} 个应用
            </button>
          ) : null}
        </header>
        <section
          className="r6-home-list"
          ref={list}
          id={listId}
          aria-label="应用列表"
          tabIndex={expanded ? 0 : -1}
          onScroll={(event) => {
            if (expanded)
              viewState.current.allScroll = event.currentTarget.scrollTop;
          }}
        >
          {(expanded ? apps : apps.slice(0, 3)).map((source) => {
            const activity =
              sourceActivity(source, callEvents, callState === "unavailable") ||
              sourceSyncActivity(source.source_id, syncHealth);
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
                        ? "持续同步已开启"
                        : host?.installed &&
                            host.permission?.state === "approved"
                          ? "允许请求已授权记忆"
                          : source.authorized
                            ? "历史读取已授权"
                            : "连接待完成"}
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
                    查看详情
                  </R6Button>
                </div>
              </div>
            );
          })}
          {!apps.length ? (
            <p className="r6-home-empty">
              {stale ? "应用列表暂时不可读取" : "还没有可显示的应用"}
            </p>
          ) : null}
        </section>
      </section>
    </section>
  );
}
