import type {
  AskMemoryResponse,
  ExtractionWindowPreset,
  GovernedMemory,
  ImportJob,
  MemoryDetail,
  MemoryGraphResponse,
  MemoryProcessingSummary,
  SourceResponse,
} from "@one-memory/contracts";
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  LayoutList,
  MessageCircle,
  Network,
  TextSearch,
} from "lucide-react";
import {
  type FormEvent,
  type RefObject,
  type SyntheticEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  apiErrorCode,
  askMemoriesStream,
  correctMemory,
  digestMemoryGraph,
  getMemories,
  getMemory,
  getMemoryGraph,
  getMemoryProcessing,
  getModelProfiles,
  manualExtract,
  mutateMemory,
  retryUnknownMemoryProcessing,
  saveMemoryNote,
  setExtractionWindow,
  syncMemoryIndex,
} from "./api";
import { ConversationSearch } from "./ConversationSearch";
import { clockStamp } from "./clock-stamp";
import { GraphWidthPrompt } from "./GraphWidthPrompt";
import { MemoryDeleteDialog } from "./MemoryDeleteDialog";
import { MemoryMap } from "./MemoryMap";
import { subscribePoll } from "./poll-scheduler";
import { R6Button } from "./R6Controls";
import { sourceNameOr } from "./source-label";
import { traceUiAction } from "./ui-trace";
import { useMemoryLibrary } from "./use-memory-library";
import { isDefiniteRefusal, writeTraceStatus } from "./write-outcome";
import "./r6-memory.css";
import { indexSyncPresentation } from "./index-sync-notice";
import {
  MemoryNoteComposer,
  type PendingMemoryNote,
} from "./MemoryNoteComposer";
import { memoryProvenanceLabel } from "./MemoryRecallScope";
import type { MemoryArrival } from "./memory-arrival-state";
import {
  blockReasonLabel,
  extractionStateLabel,
  extractionWindowPresetLabel,
  memoryHistoryActionLabel,
  modelBlock,
  retryingBlockLabel,
  splitBlockedStages,
  taskKindLabel,
} from "./memory-processing-labels";

type MemoryMode = "map" | "ask" | "library" | "history";
type MemoryStatusFilter = "confirmed" | "deleted" | "superseded";
type BulkAction = "delete";
type BulkTarget = {
  memoryId: string;
  revision: number;
  action: BulkAction;
  intent: string;
  requestId: string;
};
type BulkFailure = BulkTarget & { error: string };
const NO_SOURCES: SourceResponse[] = [];

export function MemorySpace({
  sources = NO_SOURCES,
  importJobs = [],
  sourceSnapshotStale = false,
  onConnect = () => undefined,
  onConfigureModel = () => undefined,
  onArrivalChange,
  active = true,
  preload = false,
  simplified = false,
  focusMemoryId = null,
  onFocusMemoryConsumed,
  focusDialogue = false,
  onFocusDialogueConsumed,
}: {
  /** Needed to name an app in the evidence column and to draw the skeleton
   * graph before any memory exists. Owned by `App`, which already polls it. */
  sources?: SourceResponse[];
  importJobs?: ImportJob[];
  sourceSnapshotStale?: boolean;
  onConnect?(): void;
  onConfigureModel?(): void;
  onArrivalChange?(arrival: MemoryArrival): void;
  active?: boolean;
  preload?: boolean;
  /** Temporary presentation only; never replaces the user's chosen mode. */
  simplified?: boolean;
  focusMemoryId?: string | null;
  onFocusMemoryConsumed?(): void;
  focusDialogue?: boolean;
  onFocusDialogueConsumed?(): void;
}) {
  const [memories, setMemories] = useState<GovernedMemory[]>([]);
  const [detail, setDetail] = useState<MemoryDetail | null>(null);
  const [detailFreshness, setDetailFreshness] = useState<{
    id: string;
    state: "checking" | "verified" | "failed" | "changed";
    revision?: number;
  } | null>(null);
  const detailReadBlocked =
    simplified &&
    !!detail &&
    detailFreshness?.id === detail.memory.memory_id &&
    (detailFreshness.state === "checking" ||
      detailFreshness.state === "failed");
  const detailWriteBlocked = useRef(detailReadBlocked);
  detailWriteBlocked.current = detailReadBlocked;
  const [detailLoad, setDetailLoad] = useState<{
    memoryId: string;
    status: "loading" | "failed";
    error?: string;
  } | null>(null);
  const [query, setQuery] = useState("");
  const [askScope, setAskScope] = useState<"all-apps" | "*">("all-apps");
  const [mode, setMode] = useState<MemoryMode>("map");
  const [managementOpen, setManagementOpen] = useState(false);
  const displayedMode = simplified
    ? managementOpen
      ? "library"
      : "map"
    : mode;
  useEffect(() => {
    const openManagement = () => {
      if (simplified) setManagementOpen((value) => !value);
    };
    window.addEventListener("one-memory:manage", openManagement);
    return () =>
      window.removeEventListener("one-memory:manage", openManagement);
  }, [simplified]);
  const viewActive = useRef(active);
  viewActive.current = active;
  const shouldPoll = !simplified || active || preload;
  function blockInactiveInteraction(event: SyntheticEvent) {
    // An inactive surface swallows every event. The old carve-out that let a
    // blocking dialog through existed only for the removed teaching mode.
    if (!active) {
      event.preventDefault();
      event.stopPropagation();
    }
  }
  const [statusFilter, setStatusFilter] =
    useState<MemoryStatusFilter>("confirmed");
  const [submittedQuery, setSubmittedQuery] = useState("");
  const sourceKey = JSON.stringify(
    sources.map((source) => [
      source.source_id,
      source.authorized,
      source.grant_revision,
    ]),
  );
  const management = simplified && displayedMode === "library";
  const [mapQuery, setMapQuery] = useState("");
  const [mapSource, setMapSource] = useState("");
  const mapLibrary = useMemoryLibrary({
    enabled: simplified && (active || preload) && !management,
    stale: sourceSnapshotStale,
    sourceKey,
    query: mapQuery,
    status: "confirmed",
    source: mapSource || undefined,
    activeOnly: true,
  });
  // The count above is the count of the current filter, not of every memory.
  // Naming the filter is what keeps "已沉淀 3 条" from reading as "only three
  // exist" while a search or a source selection is on.
  // Named after the filter the page answers, never after the filter in the
  // inputs: while a new read is open the numbers are still the previous
  // filter's, and naming the new one would caption the wrong numbers.
  const settledFilterLabel = [
    mapLibrary.settledSource
      ? `来源 ${sourceNameOr(sources, mapLibrary.settledSource, mapLibrary.settledSource)}`
      : "",
    mapLibrary.settledQuery.trim()
      ? `关键词“${mapLibrary.settledQuery.trim()}”`
      : "",
  ]
    .filter(Boolean)
    .join(" · ");
  const knownMapTotal = useRef(0);
  if (
    simplified &&
    !mapQuery &&
    !mapSource &&
    mapLibrary.page &&
    !mapLibrary.failed
  ) {
    knownMapTotal.current = mapLibrary.page.total;
  }
  const library = useMemoryLibrary({
    enabled: management && active,
    stale: sourceSnapshotStale,
    sourceKey,
    query: submittedQuery,
    status: statusFilter,
  });
  const libraryGuard = useRef({
    writable: library.writable,
    scope: library.scope,
    active,
    stale: sourceSnapshotStale,
  });
  libraryGuard.current = {
    writable: library.writable,
    scope: library.scope,
    active,
    stale: sourceSnapshotStale,
  };
  useEffect(() => {
    viewActive.current = active;
    libraryGuard.current.active = active;
    return () => {
      viewActive.current = false;
      libraryGuard.current.active = false;
    };
  }, [active]);
  const [graph, setGraph] = useState<MemoryGraphResponse | null>(null);
  const [answer, setAnswer] = useState<AskMemoryResponse | null>(null);
  const [streaming, setStreaming] = useState<{
    text: string;
    memories: GovernedMemory[];
  } | null>(null);
  const [draft, setDraft] = useState("");
  const [selection, setSelection] = useState<Set<string>>(new Set());
  const bulkInFlight = useRef(false);
  const [bulkFailures, setBulkFailures] = useState(
    new Map<string, BulkFailure>(),
  );
  const [bulkResult, setBulkResult] = useState<{
    action: BulkAction | null;
    succeeded: number;
    failed: number;
  } | null>(null);
  const [deleteCount, setDeleteCount] = useState(0);
  const deleteAnswer = useRef<((answer: boolean) => void) | null>(null);
  function confirmDelete(count: number) {
    setDeleteCount(count);
    return new Promise<boolean>((resolve) => {
      deleteAnswer.current = resolve;
    });
  }
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [readError, setReadError] = useState<string | null>(null);
  /** A refresh behind an already-drawn list, not the first load. */
  const [refreshing, setRefreshing] = useState(false);
  const [processing, setProcessing] = useState<MemoryProcessingSummary | null>(
    null,
  );
  const [pendingNote, setPendingNote] = useState<PendingMemoryNote | null>(
    null,
  );
  const [arrivalSetup, setArrivalSetup] = useState<
    "checking" | "model-required" | "ready" | "unavailable"
  >("checking");
  const [checkingSetup, setCheckingSetup] = useState(false);
  const liveLoading = useRef(false);
  liveLoading.current =
    importJobs.some((job) => ["pending", "running"].includes(job.status)) ||
    !!(processing?.running || processing?.pending);
  // biome-ignore lint/correctness/useExhaustiveDependencies: Source authority changes invalidate an in-flight setup snapshot.
  useEffect(() => {
    if (!active && (simplified || !onArrivalChange)) return;
    if (sourceSnapshotStale) {
      setArrivalSetup("unavailable");
      setCheckingSetup(false);
      return;
    }
    let stopped = false;
    let request = 0;
    setArrivalSetup("checking");
    async function inspectSetup() {
      const version = ++request;
      setCheckingSetup(true);
      const profiles = await Promise.allSettled([getModelProfiles()]);
      if (stopped || version !== request) return;
      setCheckingSetup(false);
      if (profiles[0].status !== "fulfilled") {
        setArrivalSetup("unavailable");
        return;
      }
      const activeProfiles = profiles[0].value.filter((item) => item.is_active);
      const profile = activeProfiles.length === 1 ? activeProfiles[0] : null;
      if (activeProfiles.length > 1) {
        setArrivalSetup("unavailable");
        return;
      }
      if (!profile) {
        setArrivalSetup("model-required");
        return;
      }
      setArrivalSetup("ready");
    }
    void inspectSetup();
    const stop = subscribePoll({
      key: "model-profiles",
      intervalMs: 12_000,
      run: inspectSetup,
    });
    return () => {
      stopped = true;
      request++;
      stop();
    };
  }, [active, onArrivalChange, simplified, sourceKey, sourceSnapshotStale]);
  // Whether the library has ever been read. An empty list means "you have
  // none"; not having read it yet means we do not know, and saying the first
  // when we mean the second is how 483 memories came to look like zero.
  const [loaded, setLoaded] = useState(false);
  const loadedRef = useRef(loaded);
  loadedRef.current = loaded;
  /** Focus targets for the mode tablist's roving arrow-key navigation. */
  const modeTabs = useRef<
    Partial<
      Record<"map" | "library" | "ask" | "history", HTMLButtonElement | null>
    >
  >({});
  const importedSources = sources.filter((source) => source.authorized);
  const isImporting = importJobs.some((job) =>
    ["pending", "running"].includes(job.status),
  );
  const memoryArrival: MemoryArrival = {
    importing: isImporting && !sourceSnapshotStale,
    importedMessages: importedSources.reduce(
      (sum, source) => sum + source.imported_messages,
      0,
    ),
    importedConversations: importedSources.reduce(
      (sum, source) => sum + source.imported_conversations,
      0,
    ),
    refreshing: checkingSetup,
    memoryCount: loaded ? memories.length : undefined,
    blockedStages: processing?.blocked_stages,
    backlogPending: processing?.backlog_pending,
    phase: sourceSnapshotStale
      ? "unavailable"
      : !loaded
        ? "checking"
        : arrivalSetup !== "ready"
          ? arrivalSetup
          : !processing
            ? "checking"
            : processing.running > 0
              ? "processing"
              : processing.failed > 0
                ? "failed"
                : isImporting
                  ? "importing"
                  : "idle",
  };
  const arrivalStateKey = JSON.stringify(memoryArrival);
  useEffect(() => {
    onArrivalChange?.(JSON.parse(arrivalStateKey) as MemoryArrival);
  }, [arrivalStateKey, onArrivalChange]);
  useEffect(() => {
    if (active || !onArrivalChange) return;
    let stopped = false;
    const update = async () => {
      try {
        const next = await getMemoryProcessing();
        if (!stopped) setProcessing(next);
      } catch {
        // Background read failures do not invalidate the last verified state.
      }
    };
    void update();
    // The background arrival read is the same surface as the foreground
    // processing poll. Both read `getMemoryProcessing`, so the read itself is
    // shared (`fetch`) and this subscriber only consumes it (`onValue`): two
    // `run` subscribers on one key would still fire two requests per tick.
    const stop = subscribePoll({
      key: "processing",
      intervalMs: 12_000,
      fetch: getMemoryProcessing,
      onValue: (value) => {
        if (!stopped) setProcessing(value as MemoryProcessingSummary);
      },
    });
    return () => {
      stopped = true;
      stop();
    };
  }, [active, onArrivalChange]);
  const requestIds = useRef(new Map<string, string>());
  const detailEpoch = useRef(0);
  const detailRef = useRef(detail);
  detailRef.current = detail;
  const answerEpoch = useRef(0);
  const answerMemories = useRef<GovernedMemory[]>([]);
  const dataEpoch = useRef(0);
  const invalidateAnswers = useCallback((items: GovernedMemory[]) => {
    if (
      !answerMemories.current.some((memory) => {
        const current = items.find(
          (item) => item.memory_id === memory.memory_id,
        );
        return (
          !current ||
          current.revision !== memory.revision ||
          current.status !== memory.status
        );
      })
    )
      return;
    answerEpoch.current++;
    answerMemories.current = [];
    setAnswer(null);
    setStreaming(null);
  }, []);
  const closeDetail = useCallback(() => {
    detailEpoch.current++;
    setDetail(null);
    setDetailLoad(null);
    setDetailFreshness(null);
  }, []);

  const selectedDetailId = detail?.memory.memory_id;
  const detailContext = useRef({ sourceKey, displayedMode });
  detailContext.current = { sourceKey, displayedMode };
  useEffect(() => {
    if (
      !simplified ||
      !active ||
      sourceSnapshotStale ||
      !selectedDetailId ||
      busy
    )
      return;
    let stopped = false;
    let inFlight = false;
    async function verifySelected() {
      if (stopped || inFlight || document.hidden) return;
      const selected = detailRef.current;
      if (!selected || selected.memory.memory_id !== selectedDetailId) return;
      const epoch = detailEpoch.current;
      inFlight = true;
      setDetailFreshness({ id: selectedDetailId, state: "checking" });
      const valid = () =>
        !stopped &&
        viewActive.current &&
        !libraryGuard.current.stale &&
        detailContext.current.sourceKey === sourceKey &&
        detailContext.current.displayedMode === displayedMode &&
        epoch === detailEpoch.current &&
        detailRef.current?.memory.memory_id === selectedDetailId &&
        detailRef.current.memory.revision === selected.memory.revision;
      try {
        const latest = await getMemory(selectedDetailId);
        if (!valid()) return;
        if (latest.memory.memory_id !== selectedDetailId)
          throw new Error("detail_identity_mismatch");
        if (
          (latest.memory.status === "deleted" &&
            selected.memory.status !== "deleted") ||
          (latest.memory.deleted_at && !selected.memory.deleted_at)
        ) {
          closeDetail();
          setError("所选记忆已不可读取，请重新读取记忆库。");
          return;
        }
        // Refresh the read-only projection even when the memory revision did
        // not move. Index sync lives outside the memory revision: Core can
        // fail or complete a retry without changing the governed content, and
        // the inspector must show that authoritative state. The edit draft and
        // CAS base are left untouched here.
        setDetail((current) =>
          current?.memory.memory_id === selectedDetailId
            ? {
                ...current,
                index_sync: latest.index_sync,
                evidence: latest.evidence,
                history: latest.history,
              }
            : current,
        );
        // Keep the editing base and draft: silently rebasing would defeat CAS.
        setDetailFreshness({
          id: selectedDetailId,
          state:
            latest.memory.revision !== selected.memory.revision
              ? "changed"
              : "verified",
          revision: latest.memory.revision,
        });
      } catch (reason) {
        if (!valid()) return;
        const status =
          reason &&
          typeof reason === "object" &&
          "response" in reason &&
          reason.response &&
          typeof reason.response === "object" &&
          "status" in reason.response
            ? reason.response.status
            : null;
        const code = apiErrorCode(reason);
        if (
          status === 403 ||
          status === 404 ||
          code === "memory_source_revoked" ||
          code === "memory_not_found" ||
          code === "memory_deleted"
        ) {
          closeDetail();
          setError("所选记忆已不可读取，请重新读取记忆库。");
        } else setDetailFreshness({ id: selectedDetailId, state: "failed" });
      } finally {
        inFlight = false;
      }
    }
    void verifySelected();
    const stopPoll = subscribePoll({
      key: "memory-detail",
      intervalMs: 10_000,
      run: verifySelected,
    });
    const foreground = () => {
      void verifySelected();
    };
    window.addEventListener("focus", foreground);
    document.addEventListener("visibilitychange", foreground);
    return () => {
      stopped = true;
      stopPoll();
      window.removeEventListener("focus", foreground);
      document.removeEventListener("visibilitychange", foreground);
    };
  }, [
    simplified,
    active,
    sourceSnapshotStale,
    selectedDetailId,
    sourceKey,
    displayedMode,
    busy,
    closeDetail,
  ]);

  // 记忆图谱只读一次很慢（实测同一份数据 1.4s–85s，会拖死渲染进程），
  // 所以绝不放在编辑/确认的等待链路上：改动后延迟合并拉一次，失败了也不影响列表。
  const graphEpoch = useRef(0);
  const graphTimer = useRef<number | undefined>(undefined);
  const scheduleGraphRefresh = useCallback(() => {
    window.clearTimeout(graphTimer.current);
    const epoch = ++graphEpoch.current;
    graphTimer.current = window.setTimeout(() => {
      if (simplified && !viewActive.current) return;
      getMemoryGraph().then(
        (value) => {
          if (epoch !== graphEpoch.current) return;
          setGraph((old) =>
            JSON.stringify(old) === JSON.stringify(value) ? old : value,
          );
        },
        () => {
          /* Keep the last drawn map. Recoverable graph reads retry silently. */
        },
      );
    }, 1500);
  }, [simplified]);
  useEffect(
    () => () => {
      window.clearTimeout(graphTimer.current);
      graphEpoch.current++;
    },
    [],
  );

  const refresh = useCallback(
    async (search = "") => {
      if (simplified && !viewActive.current) return;
      if (simplified) {
        library.invalidate();
        await library.refresh();
        await mapLibrary.refresh();
      }
      if (simplified && !viewActive.current) return;
      const epoch = ++dataEpoch.current;
      const result = await getMemories(simplified ? "" : search);
      if (epoch !== dataEpoch.current) return;
      invalidateAnswers(result);
      setMemories((old) =>
        JSON.stringify(old) === JSON.stringify(result) ? old : result,
      );
      scheduleGraphRefresh();
    },
    [
      invalidateAnswers,
      scheduleGraphRefresh,
      simplified,
      library.invalidate,
      library.refresh,
      mapLibrary.refresh,
    ],
  );

  useEffect(() => {
    if (simplified && !library.page) return;
    const visibleIds = new Set(
      (simplified ? (library.page?.items ?? []) : memories)
        .filter((item) => item.status === statusFilter)
        .map((item) => item.memory_id),
    );
    setSelection((current) => {
      const next = new Set([...current].filter((id) => visibleIds.has(id)));
      return next.size === current.size ? current : next;
    });
  }, [memories, statusFilter, simplified, library.page]);

  const selectionPageKey = useRef(library.key);
  useEffect(() => {
    if (!simplified || selectionPageKey.current === library.key) return;
    selectionPageKey.current = library.key;
    setSelection(new Set());
  }, [simplified, library.key]);

  const previousSourceKey = useRef(sourceKey);
  useEffect(() => {
    void sourceSnapshotStale;
    if (simplified && previousSourceKey.current !== sourceKey) {
      closeDetail();
      setSelection(new Set());
      setBulkFailures(new Map());
      deleteAnswer.current?.(false);
      deleteAnswer.current = null;
      setDeleteCount(0);
    }
    previousSourceKey.current = sourceKey;
  }, [simplified, sourceSnapshotStale, sourceKey, closeDetail]);

  useEffect(() => {
    if (!simplified || !active || management || sourceSnapshotStale) return;
    const stop = subscribePoll({
      key: "map-library",
      intervalMs: 10_000,
      run: () => mapLibrary.refresh().catch(() => {}),
    });
    return stop;
  }, [simplified, active, management, sourceSnapshotStale, mapLibrary.refresh]);

  useEffect(() => {
    if (!shouldPoll) return;
    let cancelled = false;

    /** Load each panel on its own, and keep trying until the library arrives.
     *
     * This ran once, and all three together: one request failing threw away
     * the other two, and nothing ever asked again. The app starts before the
     * core is ready, so the first attempt normally fails — and the library
     * then stayed empty for the rest of the session while the header kept
     * updating from its own poll, so 483 memories looked exactly like none.
     */
    async function load() {
      const epoch = ++dataEpoch.current;
      const projectionEpoch = ++graphEpoch.current;
      // The library can arrive before the graph; render it immediately.
      // Overlapping map requests are the failure mode D91 is about, and they
      // are prevented in `getMemoryGraph` itself: callers asking while a read
      // is in flight share it. Keeping the map attached to the list refresh
      // also keeps the tests that prove a failed map never disturbs the list.
      const projection = getMemoryGraph().then(
        (value) => {
          if (cancelled || projectionEpoch !== graphEpoch.current) return;
          setGraph((old) =>
            JSON.stringify(old) === JSON.stringify(value) ? old : value,
          );
        },
        () => {
          /* Keep the last drawn map. Recoverable graph reads retry silently. */
        },
      );
      const [items] = await Promise.allSettled([getMemories()]);
      if (cancelled || epoch !== dataEpoch.current) return false;
      if (items.status === "fulfilled") {
        setMemories((old) =>
          JSON.stringify(old) === JSON.stringify(items.value)
            ? old
            : items.value,
        );
        setLoaded(true);
      }
      if (items.status === "fulfilled") invalidateAnswers(items.value);
      if (items.status === "fulfilled" && detailRef.current && !simplified) {
        const current = items.value.find(
          (m) => m.memory_id === detailRef.current?.memory.memory_id,
        );
        if (
          (!current && !simplified) ||
          (!simplified &&
            current &&
            current.revision !== detailRef.current.memory.revision) ||
          (current && current.status !== detailRef.current.memory.status)
        ) {
          detailEpoch.current++;
          setDetail(null);
        }
      }
      // The graph is a view of the library, so the library is what decides
      // whether this screen has anything to show.
      if (items.status === "rejected") {
        setReadError(message(items.reason));
        return false;
      }
      setReadError(null);
      void projection;
      return true;
    }

    /** Consume the shared processing read rather than issuing its own. Both
     * this subscriber and the background arrival poll read the same endpoint,
     * so one `fetch` per tick is the whole point of sharing the key. */
    const consumeProcessing = (value: unknown) => {
      if (!cancelled) setProcessing(value as MemoryProcessingSummary);
    };

    let attempt = 0;
    let loading = false;
    let retry: number | undefined;
    // D91: the hint only appears when a refresh is actually slow or failed.
    // Showing it on every tick turned a quiet background refresh into a
    // permanent badge on a screen that is otherwise still.
    let refreshingTimer: number | undefined;
    async function attemptLoad() {
      if (cancelled || loading) return;
      loading = true;
      if (loadedRef.current) {
        refreshingTimer = window.setTimeout(() => {
          if (!cancelled) setRefreshing(true);
        }, 2_000);
      }
      const success = await load();
      loading = false;
      window.clearTimeout(refreshingTimer);
      if (!cancelled) {
        // A failed refresh keeps the list on screen, so it says why the list
        // may be behind. A quick successful one never flashes the hint.
        setRefreshing(!success && loadedRef.current);
      }
      if (cancelled || success) return;
      attempt += 1;
      // Backs off to five seconds and stays there: the core being slow to
      // start is the common case, and giving up is what caused this.
      retry = window.setTimeout(
        attemptLoad,
        Math.min(500 * 2 ** attempt, 5_000),
      );
    }

    void attemptLoad();
    // `subscribePoll` only schedules ticks; the first read still has to happen
    // on mount. Do it once through the shared fetch so mount does not double-read
    // with the background arrival subscriber.
    void getMemoryProcessing()
      .then(consumeProcessing)
      .catch(() => {});
    // Two surfaces, two timers. The processing poll shares one read with the
    // background arrival poll on the same key (3s wins over 12s); the library
    // read is its own surface and keeps its backoff.
    const stopProcessing = subscribePoll({
      key: "processing",
      intervalMs: 3_000,
      fetch: getMemoryProcessing,
      onValue: consumeProcessing,
    });
    const stopLibrary = subscribePoll({
      key: "memory-library",
      // The list is cheap and is what the reader is looking at, so it keeps
      // its ten second cadence. The map rides along, but `getMemoryGraph`
      // coalesces, so a read still in flight is shared rather than added to
      // — three overlapping ones are what timed out and dragged the list
      // from 0.4 s to 4.3 s (D91).
      intervalMs: 10_000,
      run: attemptLoad,
    });
    return () => {
      cancelled = true;
      stopProcessing();
      stopLibrary();
      window.clearTimeout(refreshingTimer);
      detailEpoch.current++;
      answerEpoch.current++;
      dataEpoch.current++;
      graphEpoch.current++;
      window.clearTimeout(graphTimer.current);
      deleteAnswer.current?.(false);
      deleteAnswer.current = null;
      if (retry !== undefined) window.clearTimeout(retry);
    };
  }, [invalidateAnswers, shouldPoll, simplified]);

  /* Land on the workbench, including on day one.
   *
   * This used to redirect to the history tab whenever no profile was active,
   * because the map, the questions and the library were all views of
   * memories and a fresh install has none — three empty panels in a row.
   *
   * The workbench removed that premise. Before a single memory exists it
   * still draws the sources that have conversations imported, still lists the
   * matters, and still says what is holding extraction up. So the redirect is
   * now a detour around a problem that is no longer there, and it costs the
   * user the one screen that explains what this app is for. */

  async function ask(question: string, scope = askScope) {
    const epoch = ++answerEpoch.current;
    answerMemories.current = [];
    setAnswer(null);
    setStreaming({ text: "", memories: [] });
    // Asking is a model call: it sends the question out and streams the answer
    // back, so it gets the same ui.action / ui.result pair as the other model
    // interactions. The id rides on the raw-fetch SSE request (`askMemoriesStream`
    // does not go through the axios interceptor), which is what lets Core's
    // kernel legs join these two lines.
    const trace = traceUiAction("memory", "submit");
    let settled = false;
    const settle = (
      outcome: "succeeded" | "failed" | "unknown",
      reason?: string,
    ) => {
      if (settled) return;
      settled = true;
      trace.done(outcome, reason ? { reason } : undefined);
    };
    try {
      for await (const event of askMemoriesStream(
        question,
        scope,
        undefined,
        trace.traceId,
      )) {
        if (epoch !== answerEpoch.current) return;
        if (event.type === "recall") {
          answerMemories.current = event.memories;
          setStreaming((current) => ({
            text: current?.text ?? "",
            memories: event.memories,
          }));
        } else if (event.type === "delta") {
          setStreaming((current) => ({
            text: (current?.text ?? "") + event.text,
            memories: current?.memories ?? [],
          }));
        } else if (event.type === "final") {
          answerMemories.current = event.memories;
          setAnswer({
            answer: event.answer,
            memories: event.memories,
            mode: event.mode,
          });
        } else {
          throw new Error(event.code);
        }
      }
      settle("succeeded");
    } catch (reason) {
      // A newer question superseded this one: not a failure, and the trace is
      // closed here rather than left dangling.
      settle(
        epoch === answerEpoch.current ? "failed" : "unknown",
        epoch === answerEpoch.current ? "ask_failed" : "superseded",
      );
      if (epoch === answerEpoch.current) throw reason;
    } finally {
      settle("unknown", "ask_abandoned");
      if (epoch === answerEpoch.current) setStreaming(null);
    }
  }

  async function saveAnswer(draft: { content: string }) {
    // HY decides what becomes a memory; the reader choosing to keep the
    // answer is what makes it their words for HY to read.
    // Saving an answer writes a memory note, so it needs the same ui.action /
    // ui.result pair as the composer's note save.
    const trace = traceUiAction("memory", "submit");
    try {
      await saveMemoryNote(
        {
          request_id: crypto.randomUUID(),
          text: draft.content,
          scope: "all-apps",
        },
        trace.traceId,
      );
      trace.done("succeeded");
    } catch (reason) {
      trace.done(writeTraceStatus(reason), {
        reason: isDefiniteRefusal(reason)
          ? "answer_note_save_failed"
          : "answer_note_save_result_unknown",
      });
      throw reason;
    }
    await refresh(query);
  }

  function toggleSelection(memoryId: string) {
    if (busy || bulkInFlight.current || (management && !library.writable))
      return;
    setSelection((current) => {
      const next = new Set(current);
      if (!next.delete(memoryId)) next.add(memoryId);
      return next;
    });
  }

  async function open(memoryId: string) {
    if (simplified && (!viewActive.current || libraryGuard.current.stale))
      return;
    const epoch = ++detailEpoch.current;
    setError(null);
    setDetail(null);
    setDetailFreshness(null);
    setDetailLoad({ memoryId, status: "loading" });
    try {
      const result = await getMemory(memoryId);
      if (epoch !== detailEpoch.current || result.memory.memory_id !== memoryId)
        return;
      setDetail(result);
      setDetailLoad(null);
      setDraft(result.memory.content);
    } catch (reason) {
      if (epoch === detailEpoch.current) {
        setError(message(reason));
        setDetailLoad({ memoryId, status: "failed", error: message(reason) });
      }
    }
  }

  const openRef = useRef(open);
  openRef.current = open;
  const consumeFocus = useRef(onFocusMemoryConsumed);
  consumeFocus.current = onFocusMemoryConsumed;
  useEffect(() => {
    if (!focusMemoryId || !active) return;
    void openRef.current(focusMemoryId);
    consumeFocus.current?.();
  }, [focusMemoryId, active]);

  async function action(actionName: "undo" | "delete") {
    if (!detail || detailReadBlocked || (management && !library.writable))
      return;
    const scope = libraryGuard.current.scope;
    const epoch = detailEpoch.current;
    if (actionName === "delete" && !(await confirmDelete(1))) {
      return;
    }
    if (
      (simplified &&
        (!viewActive.current ||
          detailWriteBlocked.current ||
          epoch !== detailEpoch.current)) ||
      (management &&
        (!libraryGuard.current.writable ||
          scope !== libraryGuard.current.scope))
    )
      return;
    setBusy(true);
    setError(null);
    const intent = `${detail.memory.memory_id}:${actionName}:${detail.memory.revision}`;
    const requestId = requestIdFor(requestIds.current, intent);
    const trace = traceUiAction("memory", "confirm");
    let committed = false;
    try {
      const updated = await mutateMemory(
        detail.memory.memory_id,
        actionName,
        detail.memory.revision,
        requestId,
        trace.traceId,
      );
      trace.done("succeeded");
      committed = true;
      acceptMutation(updated);
      requestIds.current.delete(intent);
      await refresh(query);
      if (detailRef.current?.memory.memory_id === updated.memory_id) {
        if (updated.status === "deleted") closeDetail();
        else await open(updated.memory_id);
      }
    } catch (reason) {
      trace.done(writeTraceStatus(reason), {
        reason: isDefiniteRefusal(reason)
          ? "memory_action_failed"
          : "memory_action_result_unknown",
      });
      setError(
        committed
          ? "修改已保存，但读取最新列表失败。已停止使用旧回答，请重新读取。"
          : message(reason),
      );
    } finally {
      setBusy(false);
    }
  }

  function acceptMutation(updated: GovernedMemory) {
    if (simplified) library.invalidate();
    // Reads started before this commit must not restore its previous revision.
    dataEpoch.current++;
    graphEpoch.current++;
    invalidateAnswers(
      answerMemories.current.map((item) =>
        item.memory_id === updated.memory_id ? updated : item,
      ),
    );
    setMemories((current) =>
      current.map((item) =>
        item.memory_id === updated.memory_id &&
        item.revision <= updated.revision
          ? updated
          : item,
      ),
    );
    if (
      detailRef.current?.memory.memory_id === updated.memory_id &&
      updated.status === "deleted"
    )
      closeDetail();
  }

  /** Run one governance action across a selection, one request each.
   *
   * There is no batch endpoint, and this does not invent one: each memory
   * carries its own revision, and the revision check is what stops a stale
   * list from acting on a memory that changed underneath it. A batch call
   * would have to either carry all the revisions anyway or drop the check.
   *
   * A retry keeps the failed request's action, revision and id, independently
   * of subsequent selection changes or edits to the detail draft.
   */
  function bulkAction(actionName: BulkAction) {
    if (busy || bulkInFlight.current || (management && !library.writable))
      return;
    const targets = selectedTargets.map((target) => {
      const intent = `${target.memory_id}:${actionName}:${target.revision}`;
      const previous = bulkFailures.get(target.memory_id);
      return {
        memoryId: target.memory_id,
        revision: target.revision,
        action: actionName,
        intent,
        requestId:
          previous?.intent === intent
            ? previous.requestId
            : requestIdFor(requestIds.current, intent),
      };
    });
    void runBulk(targets);
  }

  async function runBulk(targets: BulkTarget[]) {
    if (
      busy ||
      bulkInFlight.current ||
      !targets.length ||
      (management && !library.writable)
    )
      return;
    const scope = libraryGuard.current.scope;
    bulkInFlight.current = true;
    setBusy(true);
    try {
      const deleteTargets = targets.filter(
        (target) => target.action === "delete",
      );
      if (deleteTargets.length && !(await confirmDelete(deleteTargets.length)))
        return;
      setError(null);
      setBulkResult(null);
      let succeeded = 0;
      let failed = 0;
      for (const target of targets) {
        if (
          management &&
          (!libraryGuard.current.active ||
            libraryGuard.current.stale ||
            scope !== libraryGuard.current.scope)
        )
          break;
        // A bulk action still issues one kernel request per memory, and a trace
        // id maps to exactly one request. Tracing only the batch would leave
        // every mutation's kernel legs attached to no ui.action, so each item
        // gets its own short chain.
        const trace = traceUiAction("memory", "confirm");
        try {
          const updated = await mutateMemory(
            target.memoryId,
            target.action,
            target.revision,
            target.requestId,
            trace.traceId,
          );
          // The kernel write landed; close the chain before any local UI work,
          // so a later state-update throw cannot relabel a landed write.
          trace.done("succeeded");
          acceptMutation(updated);
          // Also stop a pre-commit ask whose recall event has not arrived yet.
          answerEpoch.current++;
          answerMemories.current = [];
          setAnswer(null);
          setStreaming(null);
          requestIds.current.delete(target.intent);
          succeeded++;
          setBulkFailures((current) => {
            const next = new Map(current);
            next.delete(target.memoryId);
            return next;
          });
          setSelection((current) => {
            const next = new Set(current);
            next.delete(target.memoryId);
            return next;
          });
        } catch (reason) {
          trace.done(writeTraceStatus(reason), {
            reason: isDefiniteRefusal(reason)
              ? "bulk_memory_action_failed"
              : "bulk_memory_action_result_unknown",
          });
          failed++;
          setBulkFailures((current) =>
            new Map(current).set(target.memoryId, {
              ...target,
              error:
                apiErrorCode(reason) === null
                  ? "未收到操作结果，请重试。"
                  : message(reason),
            }),
          );
          setSelection((current) => new Set(current).add(target.memoryId));
        }
      }
      setBulkResult({
        action: targets.every((target) => target.action === targets[0].action)
          ? targets[0].action
          : null,
        succeeded,
        failed,
      });
      try {
        await refresh(query);
      } catch {
        setError(
          simplified
            ? "批量处理结果已保留，但列表刷新失败，请重新读取记忆库。"
            : "批量处理结果已保留，但列表刷新失败，稍后会自动重新读取。",
        );
      }
    } finally {
      bulkInFlight.current = false;
      setBusy(false);
    }
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!detail || detailReadBlocked || (management && !library.writable))
      return;
    setBusy(true);
    setError(null);
    const intent = `${detail.memory.memory_id}:correct:${detail.memory.revision}`;
    const requestId = requestIdFor(requestIds.current, intent);
    const trace = traceUiAction("memory", "submit");
    let committed = false;
    try {
      const updated = await correctMemory(
        detail.memory.memory_id,
        detail.memory.revision,
        draft,
        detail.memory.scopes,
        requestId,
        trace.traceId,
      );
      trace.done("succeeded");
      committed = true;
      acceptMutation(updated);
      requestIds.current.delete(intent);
      await refresh(query);
      await open(detail.memory.memory_id);
    } catch (reason) {
      trace.done(writeTraceStatus(reason), {
        reason: isDefiniteRefusal(reason)
          ? "memory_correct_failed"
          : "memory_correct_result_unknown",
      });
      setError(
        committed
          ? "纠正已保存，但读取最新列表失败。已停止使用旧回答，请重新读取。"
          : message(reason),
      );
    } finally {
      setBusy(false);
    }
  }

  async function syncIndex() {
    if (!detail || detailReadBlocked || (management && !library.writable))
      return;
    setBusy(true);
    setError(null);
    const trace = traceUiAction("memory", "retry");
    try {
      const result = await syncMemoryIndex(
        detail.memory.memory_id,
        trace.traceId,
      );
      if (result.status === "synced") trace.done("succeeded");
      else
        trace.done("failed", {
          reason: "memory_index_sync_failed",
        });
      // Reopened rather than patched from the reply: a retry that failed
      // again comes back as a pending record with a 200, so the line below
      // has to be redrawn from the memory's state either way.
      await open(detail.memory.memory_id);
    } catch (reason) {
      trace.done(writeTraceStatus(reason), {
        reason: isDefiniteRefusal(reason)
          ? "memory_index_sync_failed"
          : "memory_index_sync_result_unknown",
      });
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  }

  async function buildRelations() {
    setBusy(true);
    setError(null);
    const trace = traceUiAction("memory", "submit");
    try {
      await digestMemoryGraph(trace.traceId);
      trace.done("succeeded");
      await refresh();
    } catch (reason) {
      trace.done(writeTraceStatus(reason), {
        reason: isDefiniteRefusal(reason)
          ? "memory_graph_digest_failed"
          : "memory_graph_digest_result_unknown",
      });
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  }

  async function retryUnknown() {
    if (
      busy ||
      !window.confirm(
        "重新整理可能再次调用模型。只会重跑结果不确定的对话，是否继续？",
      )
    )
      return;
    setBusy(true);
    setError(null);
    const trace = traceUiAction("memory", "retry");
    try {
      const receipt = await retryUnknownMemoryProcessing(trace.traceId);
      trace.done("succeeded", { count: receipt.requeued });
      setProcessing(await getMemoryProcessing());
    } catch (reason) {
      trace.done(writeTraceStatus(reason), {
        reason: isDefiniteRefusal(reason)
          ? "memory_unknown_retry_rejected"
          : "memory_unknown_retry_result_unknown",
      });
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  }

  const libraryMemories = simplified
    ? (library.page?.items ?? [])
    : memories.filter((memory) => memory.status === statusFilter);
  const selectedTargets = libraryMemories.filter((memory) =>
    selection.has(memory.memory_id),
  );
  const selectedCount = selectedTargets.length;
  const visibleFailures = libraryMemories.flatMap((memory) => {
    const failure = bulkFailures.get(memory.memory_id);
    return failure ? [failure] : [];
  });
  // What the reader owes attention, and what the pipeline is already handling
  // on its own. The second half is counted, never listed: an internal guard
  // that clears itself on the next pass is not something to act on. See
  // `worthNamingBlock`.
  const blocked = processing
    ? splitBlockedStages(processing.blocked_stages)
    : { named: [], retrying: 0 };
  const processingNeedsAttention =
    blocked.named.length > 0 || (processing?.unknown ?? 0) > 0;

  return (
    <section
      className={`view memory-view memory-instrument${simplified ? " r6-memory" : ""}`}
      aria-labelledby="memory-title"
      hidden={!active}
      inert={!active}
      onClickCapture={blockInactiveInteraction}
      onChangeCapture={blockInactiveInteraction}
      onSubmitCapture={blockInactiveInteraction}
      onKeyDownCapture={blockInactiveInteraction}
    >
      <div hidden={!active}>
        {deleteCount > 0 && (
          <MemoryDeleteDialog
            count={deleteCount}
            onAnswer={(answer) => {
              setDeleteCount(0);
              deleteAnswer.current?.(answer);
              deleteAnswer.current = null;
            }}
          />
        )}
        {/* Ahead of everything else: while the widths disagree, every number
          below this line is about a pipeline that cannot run. */}
        <GraphWidthPrompt />
      </div>
      <header className="memory-instrument-header">
        <div>
          <h1 id="memory-title">{simplified ? "记忆" : "记忆空间"}</h1>
          <span className="memory-loaded-count">
            {simplified && !management
              ? mapLibrary.failed
                ? "记忆读取失败"
                : mapLibrary.stale
                  ? "正在导入历史会话，记忆将陆续出现"
                  : mapLibrary.page
                    ? `已沉淀 ${mapLibrary.page.total} 条${settledFilterLabel ? `（${settledFilterLabel}）` : ""} · 当前展示 ${mapLibrary.page.items.length} 条`
                    : "正在读取…"
              : loaded
                ? `已加载 ${memories.filter((m) => m.status !== "deleted").length} 条记忆`
                : "正在读取…"}
          </span>
        </div>
        <MemoryNoteComposer
          suspended={!active}
          onPendingChange={setPendingNote}
          onSaved={() => {
            void refresh(query).catch((reason) => setError(message(reason)));
          }}
          onOpenMemory={(id) => {
            if (!simplified) setMode("library");
            void open(id);
          }}
          onConfigureModel={onConfigureModel}
        />
        {!simplified && (
          <div
            className="memory-mode-tabs"
            role="tablist"
            aria-label="记忆空间模式"
            onKeyDown={(event) => {
              // A `role="tablist"` is expected to move selection with the arrow
              // keys (WAI-ARIA tabs pattern). Without this the four tabs were
              // reachable by Tab but could only be changed with the mouse.
              const order = ["map", "library", "ask", "history"] as const;
              const current = order.indexOf(displayedMode);
              if (current < 0) return;
              const next =
                event.key === "ArrowRight"
                  ? (current + 1) % order.length
                  : event.key === "ArrowLeft"
                    ? (current - 1 + order.length) % order.length
                    : event.key === "Home"
                      ? 0
                      : event.key === "End"
                        ? order.length - 1
                        : null;
              if (next === null) return;
              event.preventDefault();
              const target = order[next];
              setMode(target);
              setAnswer(null);
              // Move focus with the selection so the keyboard user lands on the
              // tab they just chose rather than staying on the old one. The
              // buttons are not remounted by the mode change, so focusing the
              // existing element synchronously is correct.
              modeTabs.current[target]?.focus();
            }}
          >
            {(
              [
                ["map", "工作台", Network],
                ["library", "记忆库", LayoutList],
                ["ask", "问记忆", MessageCircle],
                ["history", "历史对话", TextSearch],
              ] as const
            ).map(([value, label, Icon]) => (
              <button
                key={value}
                type="button"
                role="tab"
                data-tour-id={`memory-${value}-tab`}
                ref={(node) => {
                  modeTabs.current[value] = node;
                }}
                // Roving tabindex: exactly one tab (the selected one) is in the
                // Tab order, as the tabs pattern requires.
                tabIndex={displayedMode === value ? 0 : -1}
                aria-controls={`memory-mode-panel-${value}`}
                aria-selected={displayedMode === value}
                onClick={() => {
                  setMode(value);
                  setAnswer(null);
                }}
              >
                <Icon size={16} aria-hidden="true" />
                {label}
              </button>
            ))}
          </div>
        )}
      </header>
      {processing && (!simplified || managementOpen) ? (
        <details className="memory-processing-details">
          <summary>
            <span
              className={processingNeedsAttention ? "status-attention" : ""}
            >
              {processingNeedsAttention
                ? "整理需要处理"
                : processing.running || processing.pending
                  ? "正在整理记忆"
                  : "整理状态"}
            </span>
            <ChevronDown size={14} aria-hidden="true" />
          </summary>
          <div className="memory-processing-body">
            <p className="memory-pipeline-state" aria-live="polite">
              <span
                className={
                  processing.running ? "activity-dot active" : "activity-dot"
                }
              />
              {processing.running || processing.pending
                ? `${processing.running} 处理中 · ${queueWaiting(processing)}${queueEta(processing)}`
                : `${processing.completed} 次整理已完成`}
              {processing.outside_window_pending ? (
                <span className="pipeline-outside-window">
                  {` · ${processing.outside_window_pending} 条在窗口外（原文可搜）`}
                </span>
              ) : null}
              {processing.manual_extract_batches_planned ? (
                <span className="pipeline-manual-batches">
                  {` · 已规划 ${processing.manual_extract_batches_planned} 批，当前第 ${processing.manual_extract_batches_current} 批`}
                </span>
              ) : null}
              {blocked.named.length ? (
                <span className="pipeline-blocked">
                  {blocked.named
                    .map(
                      (block) =>
                        `${taskKindLabel(block.kind)}卡在 ${blockReasonLabel(block.reason)}（${block.count} 条）`,
                    )
                    .join(" · ")}
                </span>
              ) : null}
              {blocked.retrying ? (
                <span className="pipeline-retrying">
                  {` · ${retryingBlockLabel(blocked.retrying)}`}
                </span>
              ) : null}
            </p>
            {(processing.unknown ?? 0) > 0 ? (
              <div className="memory-processing-unknown" role="status">
                <span>{`有 ${processing.unknown} 段对话的整理结果不确定`}</span>
                <R6Button disabled={busy} onClick={() => void retryUnknown()}>
                  重新整理
                </R6Button>
              </div>
            ) : null}
            <ExtractionWindowControls
              processing={processing}
              busy={busy}
              onBusy={setBusy}
              onError={setError}
              onRefresh={async () => {
                try {
                  setProcessing(await getMemoryProcessing());
                } catch (reason) {
                  setError(message(reason));
                }
              }}
            />
          </div>
        </details>
      ) : null}

      <ModelPrompt
        processing={processing}
        hasMemories={
          simplified
            ? knownMapTotal.current > 0 || (mapLibrary.page?.total ?? 0) > 0
            : memories.length > 0
        }
        onConfigureModel={onConfigureModel}
      />
      {simplified && pendingNote ? (
        <p className="memory-pending-note" role="status">
          <span>原话已保存，正在等待整理：{pendingNote.text}</span>
          {pendingNote.state === "waiting_for_model" ? (
            <button type="button" onClick={onConfigureModel}>
              配置整理模型
            </button>
          ) : null}
        </p>
      ) : null}

      {readError && (
        <details className="memory-feedback-details">
          <summary>查看读取详情</summary>
          <p>{readError}</p>
        </details>
      )}
      {refreshing ? (
        <p className="memory-refreshing" role="status">
          正在更新…
        </p>
      ) : null}
      {error ? (
        <p className="error-banner" role="alert">
          {error}
        </p>
      ) : null}

      {!simplified && (
        <div
          id="memory-mode-panel-history"
          hidden={displayedMode !== "history"}
        >
          <ConversationSearch
            onOpenMemory={(memoryId) => {
              setMode("library");
              void open(memoryId);
            }}
          />
        </div>
      )}

      <form
        className="memory-query-bar"
        data-tour-id="memory-query-bar"
        hidden={displayedMode !== "ask" && displayedMode !== "library"}
        onSubmit={async (event) => {
          event.preventDefault();
          if (management) {
            if (busy || bulkInFlight.current) return;
            setSelection(new Set());
            if (submittedQuery === query && library.offset === 0)
              void library.refresh().catch(() => {});
            else {
              setSubmittedQuery(query);
              library.go(0);
            }
            return;
          }
          if (busy || bulkInFlight.current || !query.trim()) return;
          setError(null);
          setBusy(true);
          try {
            if (displayedMode === "ask") {
              await ask(query.trim());
            } else {
              await refresh(query);
            }
          } catch (reason) {
            setError(message(reason));
          } finally {
            setBusy(false);
          }
        }}
      >
        <label htmlFor="memory-query">
          {displayedMode === "ask" ? "向已确认记忆提问" : "搜索记忆库"}
        </label>
        <div>
          {displayedMode === "ask" ? (
            <select
              aria-label="问答记忆范围"
              value={askScope}
              onChange={(event) =>
                setAskScope(event.target.value as "all-apps" | "*")
              }
            >
              <option value="all-apps">只问个人画像</option>
              <option value="*">问全部记忆（含项目）</option>
            </select>
          ) : null}
          <input
            id="memory-query"
            name="memory-query"
            autoComplete="off"
            value={query}
            disabled={busy}
            maxLength={management ? 512 : undefined}
            placeholder={
              displayedMode === "ask"
                ? "例如：我对真实数据有什么要求？"
                : "搜索偏好、决定或经历"
            }
            onChange={(event) => {
              if (bulkInFlight.current) return;
              setQuery(event.target.value);
              if (displayedMode === "library") setSelection(new Set());
            }}
          />
          <button
            type="submit"
            disabled={busy || (!management && !query.trim())}
          >
            {busy
              ? "处理中…"
              : displayedMode === "ask"
                ? "根据记忆回答"
                : "搜索"}
          </button>
          {management && (query || submittedQuery) ? (
            <R6Button
              variant="quiet"
              disabled={busy}
              onClick={() => {
                setQuery("");
                setSubmittedQuery("");
                library.go(0);
                setSelection(new Set());
              }}
            >
              清除搜索
            </R6Button>
          ) : null}
        </div>
      </form>

      {/* Native hiding retains the WebGL owner; its visibility observer pauses drawing. */}
      <MemoryMap
        id="memory-mode-panel-map"
        simplified={simplified}
        focusDialogue={focusDialogue}
        onFocusDialogueConsumed={onFocusDialogueConsumed}
        libraryView={
          simplified
            ? {
                query: mapQuery,
                source: mapSource,
                page: mapLibrary.page,
                loading: mapLibrary.loading,
                failed: mapLibrary.failed,
                stale: sourceSnapshotStale,
                onQuery: setMapQuery,
                onSource: setMapSource,
                resetPage: () => mapLibrary.go(0),
                onPage: mapLibrary.go,
                onRetry: () => {
                  void mapLibrary.refresh().catch(() => {});
                },
              }
            : undefined
        }
        onMutationComplete={(id) => {
          void refresh(query)
            .then(() => open(id))
            .catch((reason) => setError(message(reason)));
        }}
        hidden={!active || displayedMode !== "map"}
        memories={simplified ? (mapLibrary.page?.items ?? []) : memories}
        graph={simplified && sourceSnapshotStale ? null : graph}
        sources={sources}
        processing={processing}
        arrival={memoryArrival}
        onViewHistory={simplified ? onConnect : () => setMode("history")}
        loaded={simplified ? mapLibrary.page !== null : loaded}
        detail={detail}
        detailLoad={detailLoad}
        overlayBlocked={
          !active ||
          deleteCount > 0 ||
          (simplified && (!mapLibrary.page || sourceSnapshotStale))
        }
        draft={draft}
        busy={busy || detailReadBlocked}
        answer={answer}
        streaming={streaming}
        onDraft={setDraft}
        onOpenMemory={(memoryId) => void open(memoryId)}
        onCloseDetail={closeDetail}
        onSave={save}
        onAction={(name) => void action(name)}
        onSyncIndex={() => void syncIndex()}
        onBuildRelations={() => void buildRelations()}
        requestIds={requestIds}
        onAsk={(fullQuestion) => {
          setError(null);
          void ask(fullQuestion, "*").catch((reason) =>
            setError(message(reason)),
          );
        }}
        onSaveAnswer={(saved) => {
          setError(null);
          void saveAnswer(saved).catch((reason) => setError(message(reason)));
        }}
        onConnect={onConnect}
        onConfigureModel={onConfigureModel}
      />

      <div
        id="memory-mode-panel-ask"
        className="memory-answer-layout"
        hidden={displayedMode !== "ask"}
      >
        {answer || streaming ? (
          <article className="grounded-answer">
            <p className="answer-label">
              {answer
                ? answer.mode === "generated"
                  ? "模型综合"
                  : answer.mode === "extractive"
                    ? "原文摘录"
                    : "没有命中"
                : streaming?.text
                  ? "正在生成"
                  : "正在检索记忆"}{" "}
              · 基于 {(answer ?? streaming)?.memories.length ?? 0} 条已确认记忆
            </p>
            <h2 aria-live="polite">
              {answer ? answer.answer : streaming?.text}
              {!answer && streaming?.text ? (
                <span className="answer-caret" aria-hidden="true" />
              ) : null}
            </h2>
            <ol>
              {(answer ?? streaming)?.memories.map((memory) => (
                <li key={memory.memory_id}>
                  <button type="button" onClick={() => open(memory.memory_id)}>
                    {memory.content}
                  </button>
                </li>
              ))}
            </ol>
          </article>
        ) : (
          <p className="ask-intro">
            回答只使用已确认记忆。每个答案都可以回到对应记忆和来源原话。
          </p>
        )}
        <MemoryInspector
          detail={detail}
          busy={busy}
          draft={draft}
          requestIds={requestIds}
          onDraft={setDraft}
          onClose={closeDetail}
          onSave={save}
          onAction={action}
          onSyncIndex={syncIndex}
          onConfigureModel={onConfigureModel}
          onOpen={open}
        />
      </div>

      <div
        id="memory-mode-panel-library"
        className="memory-library-layout"
        hidden={displayedMode !== "library"}
      >
        <div>
          <nav
            className="memory-status-tabs"
            data-tour-id="memory-status-tabs"
            aria-label="记忆治理状态"
          >
            {(
              [
                ["confirmed", "已可用"],
                ["superseded", "已被取代"],
                ["deleted", "已删除"],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                type="button"
                disabled={busy}
                aria-current={statusFilter === value ? "page" : undefined}
                onClick={() => {
                  if (bulkInFlight.current) return;
                  setStatusFilter(value);
                  setSelection(new Set());
                  if (!simplified) setDetail(null);
                }}
              >
                {label}{" "}
                {!simplified &&
                  memories.filter((memory) => memory.status === value).length}
              </button>
            ))}
          </nav>
          {management && (
            <>
              {library.page ? (
                <nav aria-label="记忆库分页">
                  <span aria-live="polite">共 {library.page.total} 条</span>{" "}
                  <span>
                    第{" "}
                    {Math.floor(library.page.offset / library.page.limit) + 1}{" "}
                    页
                  </span>
                  <R6Button
                    variant="quiet"
                    title="上一页"
                    aria-label="上一页"
                    disabled={busy || library.page.offset === 0}
                    onClick={() => {
                      if (library.page) {
                        setSelection(new Set());
                        library.go(library.page.offset - library.page.limit);
                      }
                    }}
                  >
                    <ChevronLeft size={18} aria-hidden="true" />
                  </R6Button>
                  <R6Button
                    variant="quiet"
                    title="下一页"
                    aria-label="下一页"
                    disabled={
                      busy ||
                      library.page.offset + library.page.limit >=
                        library.page.total
                    }
                    onClick={() => {
                      if (library.page) {
                        setSelection(new Set());
                        library.go(library.page.offset + library.page.limit);
                      }
                    }}
                  >
                    <ChevronRight size={18} aria-hidden="true" />
                  </R6Button>
                </nav>
              ) : null}
              {sourceSnapshotStale ? (
                <p role="status">来源状态待核对，记忆库暂不可读取。</p>
              ) : library.stale ? (
                <p role="status">
                  正在导入历史会话，记忆库暂时只显示一部分；导入完成后会自动补全，不需要重试。
                </p>
              ) : library.failed ? (
                <div role="alert">
                  <p>记忆库读取失败，请重试。</p>
                  <R6Button
                    onClick={() => {
                      void library.refresh().catch(() => {});
                    }}
                  >
                    重新读取记忆库
                  </R6Button>
                </div>
              ) : library.loading ? (
                <p role="status">正在读取记忆库…</p>
              ) : null}
            </>
          )}
          {bulkResult ? (
            <p role="status" aria-label="批量操作结果">
              {`已${bulkResult.action ? bulkActionLabel(bulkResult.action) : "处理"} ${bulkResult.succeeded} 条${bulkResult.failed ? `，${bulkResult.failed} 条未完成` : ""}`}
            </p>
          ) : null}
          {visibleFailures.length ? (
            <button
              type="button"
              disabled={busy}
              onClick={() => void runBulk(visibleFailures)}
            >
              {`重试失败的 ${visibleFailures.length} 条`}
            </button>
          ) : null}
          {selectedCount ? (
            <fieldset className="memory-bulk-bar" aria-label="批量操作">
              <span>{`已选 ${selectedCount} 条`}</span>
              <button
                type="button"
                className="danger"
                disabled={busy}
                onClick={() => void bulkAction("delete")}
              >
                {`删除所选 ${selectedCount} 条`}
              </button>
              <button
                type="button"
                className="secondary"
                disabled={busy}
                onClick={() => {
                  if (!bulkInFlight.current) setSelection(new Set());
                }}
              >
                取消选择
              </button>
            </fieldset>
          ) : null}
          {libraryMemories.length ? (
            <ul className="memory-ledger">
              {libraryMemories.map((memory) => {
                const failure = bulkFailures.get(memory.memory_id);
                return (
                  <li key={memory.memory_id}>
                    <input
                      type="checkbox"
                      disabled={busy}
                      aria-label={`选择：${memory.content}`}
                      checked={selection.has(memory.memory_id)}
                      onChange={() => toggleSelection(memory.memory_id)}
                    />
                    <button
                      type="button"
                      className={`memory-ledger-row memory-${memory.status}`}
                      aria-current={
                        detail?.memory.memory_id === memory.memory_id
                          ? "true"
                          : undefined
                      }
                      onClick={() => open(memory.memory_id)}
                    >
                      <span>{memory.content}</span>
                      <small>
                        {kindLabel(memory.kind)} · {statusLabel(memory.status)}{" "}
                        · {memory.evidence_ids.length} 条原话
                      </small>
                    </button>
                    {failure ? (
                      <fieldset
                        className="memory-bulk-failure"
                        aria-label={`处理失败：${memory.content}`}
                      >
                        <p>{`未能${bulkActionLabel(failure.action)}：${failure.error}`}</p>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => void runBulk([failure])}
                        >
                          {`重试${bulkActionLabel(failure.action)}`}
                        </button>
                      </fieldset>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          ) : management && !library.page ? null : !loaded && !management ? (
            // Not the same as having none, and it used to read as if it
            // were.
            <p className="memory-status-empty">正在读取记忆库…</p>
          ) : (
            <p className="memory-status-empty">
              {`没有${statusLabel(statusFilter)}的记忆。`}
            </p>
          )}
        </div>
        <MemoryInspector
          detail={detail}
          busy={busy || detailReadBlocked || (management && !library.writable)}
          draft={draft}
          requestIds={requestIds}
          onDraft={setDraft}
          onClose={() => setDetail(null)}
          onSave={save}
          onAction={action}
          onSyncIndex={syncIndex}
          onConfigureModel={onConfigureModel}
          onOpen={open}
        />
      </div>
    </section>
  );
}

/** Says, for as long as it is true, that no memory is being made and why.
 *
 * The pipeline line in the header already carries the reason, but it is one
 * clause among the queue counts and reads as telemetry. Without a model the
 * whole screen is empty, and an empty screen with a working-looking header
 * is read as "this product does not do anything".
 *
 * Only model reasons appear here, because this prompt offers exactly one
 * action. A stall for any other reason is not fixed by opening settings, and
 * sending the user there would waste the one thing they were told to do.
 */
function ModelPrompt({
  processing,
  hasMemories,
  onConfigureModel,
}: {
  processing: MemoryProcessingSummary | null;
  hasMemories: boolean;
  onConfigureModel(): void;
}) {
  const blocked = processing ? modelBlock(processing.blocked_stages) : null;
  if (!blocked) return null;
  if (
    hasMemories &&
    ["model_secret_unavailable", "secure_storage_unavailable"].includes(
      blocked.reason,
    )
  )
    return null;
  return (
    <p className="model-prompt" role="status" aria-label="记忆未在生成">
      <span>
        {`还没有开始生成记忆：${blockReasonLabel(blocked.reason)}（${blocked.count} 条待处理）`}
      </span>
      <button type="button" onClick={onConfigureModel}>
        去配置生成模型
      </button>
    </p>
  );
}

function MemoryInspector({
  detail,
  busy,
  draft,
  requestIds,
  onDraft,
  onClose,
  onSave,
  onAction,
  onSyncIndex,
  onConfigureModel,
  onOpen,
}: {
  detail: MemoryDetail | null;
  busy: boolean;
  draft: string;
  requestIds: RefObject<Map<string, string>>;
  onDraft(value: string): void;
  onClose(): void;
  onSave(event: FormEvent<HTMLFormElement>): Promise<void>;
  onAction(action: "undo" | "delete"): Promise<void>;
  onSyncIndex(): Promise<void>;
  onConfigureModel(): void;
  onOpen(memoryId: string): Promise<void>;
}) {
  if (!detail) {
    return (
      <aside
        className="memory-inspector memory-inspector-empty"
        data-tour-id="memory-original-pane"
      >
        <p>选择一条记忆</p>
        <small>查看它的来源、内容和修改历史。</small>
      </aside>
    );
  }
  return (
    <aside
      className="memory-inspector"
      aria-label="记忆详情"
      data-tour-id="memory-original-pane"
    >
      <button
        type="button"
        className="drawer-close"
        aria-label="关闭记忆详情"
        onClick={onClose}
      >
        ×
      </button>
      <p className="status-label">
        {kindLabel(detail.memory.kind)} · {statusLabel(detail.memory.status)} ·
        r{detail.memory.revision}
      </p>
      <p className="map-origin">{memoryProvenanceLabel(detail)}</p>
      {detail.memory.superseded_by?.length ||
      detail.memory.supersedes?.length ? (
        <p className="memory-supersession">
          {(detail.memory.superseded_by ?? []).map((memoryId) => (
            <button
              key={`by:${memoryId}`}
              type="button"
              className="quiet"
              onClick={() => void onOpen(memoryId)}
            >
              查看替代它的记忆
            </button>
          ))}
          {(detail.memory.supersedes ?? []).map((memoryId) => (
            <button
              key={`of:${memoryId}`}
              type="button"
              className="quiet"
              onClick={() => void onOpen(memoryId)}
            >
              查看它取代的旧记忆
            </button>
          ))}
        </p>
      ) : null}
      {detail.index_sync?.status === "pending" ? (
        // Said plainly, because the consequence is not in the word "index":
        // the memory here reads correctly and search does not, and nobody
        // would guess that from a sync badge.
        <IndexSyncNotice
          sync={detail.index_sync}
          busy={busy}
          onRetry={onSyncIndex}
          onConfigureModel={onConfigureModel}
          onRefresh={() => void open(detail.memory.memory_id)}
        />
      ) : null}
      {detail.memory.status === "confirmed" ? (
        <form onSubmit={onSave}>
          <label>
            记忆内容
            <textarea
              name="memory-content"
              autoComplete="off"
              required
              value={draft}
              onChange={(event) => {
                requestIds.current?.clear();
                onDraft(event.target.value);
              }}
            />
          </label>
          <button type="submit" disabled={busy}>
            保存纠正
          </button>
        </form>
      ) : null}
      <div className="source-actions">
        {detail.history.length > 1 &&
        detail.memory.status !== "deleted" &&
        detail.memory.status !== "superseded" ? (
          <button
            type="button"
            className="secondary"
            disabled={busy}
            onClick={() => onAction("undo")}
          >
            撤销上次修改
          </button>
        ) : null}
        {detail.memory.status !== "deleted" ? (
          <button
            type="button"
            className="quiet"
            disabled={busy}
            onClick={() => onAction("delete")}
          >
            删除
          </button>
        ) : null}
      </div>
      <h3>来源原话</h3>
      {detail.evidence.map((item) => (
        <blockquote key={item.evidence_id}>
          {item.available ? (
            <>
              {item.extraction_state &&
              item.extraction_state !== "extracted" ? (
                <small>{extractionStateLabel(item.extraction_state)}</small>
              ) : null}
              {item.verbatim}
            </>
          ) : (
            "来源原话已不可读取"
          )}
        </blockquote>
      ))}
      <h3>修改历史</h3>
      <ol>
        {detail.history.map((item) => (
          <li key={item.revision}>
            r{item.revision} · {memoryHistoryActionLabel(item.action)}
          </li>
        ))}
      </ol>
    </aside>
  );
}

function message(reason: unknown): string {
  const code =
    reason instanceof Error &&
    reason.message === "memory_answer_context_changed"
      ? reason.message
      : apiErrorCode(reason);
  const labels: Record<string, string> = {
    memory_answer_context_changed:
      "记忆内容或使用权限已变化，回答已停止。请重新提问。",
    memory_source_revoked: "这条记忆的来源授权已撤回，暂时不能读取。",
    memory_revision_conflict: "记忆已被更新，请刷新后重试。",
    memory_not_correctable: "已删除的记忆不能修改。",
    memory_deleted: "这条记忆已经删除。",
    memory_superseded: "这条记忆已被更新的记忆取代，只能查看或删除。",
    memory_nothing_to_undo: "没有更早的修改可以撤销。",
    memory_evidence_not_found: "来源原话已不存在，无法建立这条记忆。",
    memory_request_conflict: "这次操作与已完成的请求不一致。",
    request_validation_failed: "输入内容不符合要求。",
    local_store_unavailable: "本地记忆库正忙，请稍后重试。",
  };
  if (code && labels[code]) return labels[code];
  // No code at all means the request never reached the core — usually because
  // it is still starting. Saying only "failed" left the one case that resolves
  // itself looking like the ones that do not.
  if (code === null) return "还没连上本地 Core，正在重试…";
  return `记忆操作失败（${code}）`;
}

function IndexSyncNotice({
  sync,
  busy,
  onRetry,
  onConfigureModel,
  onRefresh,
}: {
  sync: NonNullable<MemoryDetail["index_sync"]>;
  busy: boolean;
  onRetry(): void;
  onConfigureModel(): void;
  onRefresh(): void;
}) {
  const { attemptedLine, explanation, action } = indexSyncPresentation(
    sync,
    clockStamp(sync.updated_at),
  );

  return (
    <p className="notice" role="status">
      搜索里还是改之前那句话。{explanation} <small>{attemptedLine}</small>
      {action === "retry" ? (
        <>
          {" "}
          <button
            type="button"
            className="quiet"
            disabled={busy}
            onClick={onRetry}
          >
            重试同步
          </button>
        </>
      ) : null}
      {action === "configure" ? (
        <>
          {" "}
          <button
            type="button"
            className="quiet"
            disabled={busy}
            onClick={onConfigureModel}
          >
            配置整理模型
          </button>
        </>
      ) : null}
      {action === "refresh" ? (
        <>
          {" "}
          <button
            type="button"
            className="quiet"
            disabled={busy}
            onClick={onRefresh}
          >
            刷新核对
          </button>
        </>
      ) : null}
    </p>
  );
}

function requestIdFor(requests: Map<string, string>, intent: string): string {
  const existing = requests.get(intent);
  if (existing) return existing;
  const requestId = crypto.randomUUID();
  requests.set(intent, requestId);
  return requestId;
}

function kindLabel(kind: string) {
  const labels: Record<string, string> = {
    chat: "对话",
    working: "项目记忆",
    coding: "编码偏好",
    schema: "长期模式",
    intention: "意图",
  };
  return labels[kind] ?? kind;
}

function bulkActionLabel(action: BulkAction): string {
  const labels = { delete: "删除" };
  return labels[action];
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    confirmed: "已确认",
    deleted: "已删除",
    superseded: "已被取代",
  };
  return labels[status] ?? status;
}

function queueWaiting(processing: MemoryProcessingSummary): string {
  const fresh = processing.fresh_pending ?? 0;
  const backlog = processing.backlog_pending ?? 0;
  const active = processing.active_queue ?? 0;
  const inFlight = processing.in_flight_count ?? 0;
  const strandedUnknown =
    processing.stranded_running === undefined && processing.running > inFlight;
  const stranded = processing.stranded_running ?? 0;
  const activeSuffix =
    inFlight || active
      ? ` · 处理中 ${inFlight} / 待派发 ${active}`
      : strandedUnknown
        ? ` · ${processing.running - inFlight} 条任务状态待核对`
        : stranded
          ? ` · ${stranded} 条任务状态异常，正在恢复`
          : "";
  if (fresh + backlog === 0) {
    return `${processing.pending} 等待${activeSuffix}`;
  }
  if (backlog === 0) {
    return `最新 ${fresh} 条待处理${activeSuffix}`;
  }
  if (fresh === 0) {
    return `历史积压 ${backlog} 条${activeSuffix}`;
  }
  return `最新 ${fresh} 条待处理 · 历史积压 ${backlog} 条${activeSuffix}`;
}

const EXTRACTION_WINDOW_PRESETS: ExtractionWindowPreset[] = [
  "3m",
  "6m",
  "1y",
  "all",
];

function ExtractionWindowControls({
  processing,
  busy,
  onBusy,
  onError,
  onRefresh,
}: {
  processing: MemoryProcessingSummary;
  busy: boolean;
  onBusy(value: boolean): void;
  onError(value: string | null): void;
  onRefresh(): Promise<void>;
}) {
  async function changeWindow(preset: ExtractionWindowPreset) {
    if (preset === processing.extraction_window_preset) return;
    onBusy(true);
    onError(null);
    const trace = traceUiAction("memory", "toggle");
    try {
      await setExtractionWindow(preset, trace.traceId);
      trace.done("succeeded");
      await onRefresh();
    } catch (reason) {
      trace.done(writeTraceStatus(reason), {
        reason: isDefiniteRefusal(reason)
          ? "extraction_window_failed"
          : "extraction_window_result_unknown",
      });
      onError(message(reason));
    } finally {
      onBusy(false);
    }
  }

  async function extractNextBatch() {
    onBusy(true);
    onError(null);
    const trace = traceUiAction("memory", "submit");
    try {
      await manualExtract([], trace.traceId);
      trace.done("succeeded");
      await onRefresh();
    } catch (reason) {
      trace.done(writeTraceStatus(reason), {
        reason: isDefiniteRefusal(reason)
          ? "manual_extract_failed"
          : "manual_extract_result_unknown",
      });
      onError(message(reason));
    } finally {
      onBusy(false);
    }
  }

  return (
    <div className="extraction-window-controls">
      <label>
        自动提炼范围
        <select
          value={processing.extraction_window_preset}
          disabled={busy}
          onChange={(event) =>
            void changeWindow(event.target.value as ExtractionWindowPreset)
          }
        >
          {EXTRACTION_WINDOW_PRESETS.map((preset) => (
            <option key={preset} value={preset}>
              {extractionWindowPresetLabel(preset)}
            </option>
          ))}
        </select>
      </label>
      {processing.outside_window_pending ? (
        <button
          type="button"
          disabled={busy}
          onClick={() => void extractNextBatch()}
        >
          手动提炼下一批
        </button>
      ) : null}
    </div>
  );
}

function queueEta(processing: MemoryProcessingSummary): string {
  // A local model handles one extraction at a time, so a long queue can mean
  // most of a day. Saying so is kinder than a bare count that looks stuck.
  const perTask = processing.seconds_per_completion;
  if (!processing.pending || !perTask || perTask <= 0) {
    return "";
  }
  const hours = (processing.pending * perTask) / 3600;
  if (hours < 1) {
    return `（约 ${Math.max(1, Math.round(hours * 60))} 分钟）`;
  }
  if (hours < 24) {
    return `（约 ${Math.round(hours)} 小时）`;
  }
  return `（约 ${Math.round(hours / 24)} 天）`;
}
