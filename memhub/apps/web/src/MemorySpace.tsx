import type {
  ExtractionWindowPreset,
  GovernedMemory,
  HomeProjectionCapabilities,
  HostAskMemoryResponse,
  ImportJob,
  MemoryDetail,
  MemoryGraphResponse,
  MemoryLibraryPage,
  MemoryProcessingSummary,
  SdkAskMemory,
  SourceResponse,
} from "@one-memory/contracts";
import { isMessageKey } from "@one-memory/i18n";
import {
  ChevronDown,
  LayoutList,
  MessageCircle,
  Network,
  TextSearch,
} from "lucide-react";
import {
  type FormEvent,
  type SyntheticEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  addMemory,
  apiErrorCode,
  askMemoriesStream,
  deleteSdkMemory,
  getMemoryProcessing,
  getModelProfiles,
  getSdkMemory,
  getSdkMemoryGraph,
  getSdkMemoryLibrary,
  getSdkRaw,
  getSdkRawGraph,
  manualExtract,
  searchSdkRaw,
  setExtractionWindow,
  updateSdkMemory,
} from "./api";
import { localEvolveRunning, useEvolveState } from "./evolve-state";
import { GraphWidthPrompt } from "./GraphWidthPrompt";
import { useGuiLanguage } from "./gui-language";
import { MemoryDeleteDialog } from "./MemoryDeleteDialog";
import { MemoryMap } from "./MemoryMap";
import { useOnboardingTranslation } from "./onboarding-language";
import { subscribePoll } from "./poll-scheduler";
import { SdkMemoryLibrary } from "./SdkMemoryLibrary";
import { SdkRawHistory } from "./SdkRawHistory";
import {
  SDK_MEMORY_PAGE_SIZE,
  sdkDetail,
  sdkEditableText,
  sdkLibraryPage,
  sdkNamespace,
} from "./sdk-memory-map";
import {
  type RawMapRecord,
  rawDetail,
  rawGraphProjection,
  rawGraphRecord,
  rawLibraryPage,
  rawRecordId,
  rawSearchPage,
  SDK_RAW_PAGE_SIZE,
} from "./sdk-raw-map";
import { sourceNameOr, UNATTRIBUTED_SOURCE_ID } from "./source-label";
import { traceUiAction } from "./ui-trace";
import { isDefiniteRefusal, writeTraceStatus } from "./write-outcome";
import "./r6-memory.css";
import { MemoryAddComposer, type PendingMemoryAdd } from "./MemoryAddComposer";
import type { MemoryArrival } from "./memory-arrival-state";
import {
  blockReasonLabel,
  extractionWindowPresetLabel,
  llmBackoffReasonLabel,
  modelBlock,
  splitBlockedStages,
  taskKindLabel,
} from "./memory-processing-labels";

type MemoryMode = "map" | "ask" | "library" | "history";
type Translate = ReturnType<typeof useGuiLanguage>["t"];
type TranslateCount = ReturnType<typeof useGuiLanguage>["tn"];
type AskMemory = GovernedMemory | SdkAskMemory;
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
}) {
  const { locale, t, tn } = useGuiLanguage();
  const { text } = useOnboardingTranslation();
  const evolveState = useEvolveState(active || preload);
  const evolveRunning = localEvolveRunning(evolveState.data);
  const evolveUncertain = evolveState.isError && evolveRunning;
  const memoryText = (text: string) => {
    const key = `memory:${text}`;
    return isMessageKey(key) ? t(key) : text;
  };
  const [detail, setDetail] = useState<MemoryDetail | null>(null);
  const [selectedRaw, setSelectedRaw] = useState<RawMapRecord | null>(null);
  const selectedRawRef = useRef<RawMapRecord | null>(null);
  selectedRawRef.current = selectedRaw;
  const rawRecords = useRef(new Map<string, RawMapRecord>());
  const rawRecordsScope = useRef("");
  const [detailLoad, setDetailLoad] = useState<{
    memoryId: string;
    status: "loading" | "failed";
    error?: string;
  } | null>(null);
  const [query, setQuery] = useState("");
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
  const stale = useRef(sourceSnapshotStale);
  stale.current = sourceSnapshotStale;
  const shouldPoll = !simplified || active || preload;
  function blockInactiveInteraction(event: SyntheticEvent) {
    // An inactive surface swallows every event. The old carve-out that let a
    // blocking dialog through existed only for the removed teaching mode.
    if (!active) {
      event.preventDefault();
      event.stopPropagation();
    }
  }
  const sourceKey = JSON.stringify(
    sources.map((source) => [
      source.source_id,
      source.authorized,
      source.grant_revision,
    ]),
  );
  const sourcesRef = useRef(sources);
  sourcesRef.current = sources;
  const management = simplified && displayedMode === "library";
  useEffect(() => {
    viewActive.current = active;
    return () => {
      viewActive.current = false;
    };
  }, [active]);
  const [answer, setAnswer] = useState<HostAskMemoryResponse | null>(null);
  const [streaming, setStreaming] = useState<{
    text: string;
    memories: AskMemory[];
  } | null>(null);
  // The SDK library is no longer where a memory opens from the map; the id is
  // kept so the panel still has a focus target for its own tests.
  const [sdkFocusedMemoryId] = useState<string | null>(null);
  // The graph covers the active source scope (up to its visual cap), while the
  // list pages through that scope. Keep source+query with the value so a
  // filter change cannot paint the previous filter's graph during the new read.
  const [sdkGraph, setSdkGraph] = useState<{
    scope: string;
    status: "ready" | "failed";
    value: MemoryGraphResponse | null;
  } | null>(null);
  // Bumping this re-reads the page and graph after an edit or a delete.
  const [sdkMapRevision, setSdkMapRevision] = useState(0);
  const bumpSdkMap = useCallback(() => setSdkMapRevision((v) => v + 1), []);
  const previousEvolveRunning = useRef(evolveRunning);
  useEffect(() => {
    const previous = previousEvolveRunning.current;
    previousEvolveRunning.current = evolveRunning;
    if (previous && !evolveRunning) bumpSdkMap();
  }, [bumpSdkMap, evolveRunning]);
  const [sdkLibrary, setSdkLibrary] = useState<{
    page: MemoryLibraryPage | null;
    loading: boolean;
    failed: boolean;
    /** The filter the shown page was read with, for the count above it. */
    settledSource: string;
    settledQuery: string;
    settledMode: "lite" | "pro" | "ultra" | null;
  }>({
    page: null,
    loading: false,
    failed: false,
    settledSource: "",
    settledQuery: "",
    settledMode: null,
  });
  const [sdkLibraryQuery, setSdkLibraryQuery] = useState("");
  const [sdkLibrarySource, setSdkLibrarySource] = useState("");
  const [sdkLibraryOffset, setSdkLibraryOffset] = useState(0);
  const [sdkRawCursor, setSdkRawCursor] = useState<string | null>(null);
  const [processing, setProcessing] = useState<MemoryProcessingSummary | null>(
    null,
  );
  const [processingFailed, setProcessingFailed] = useState(false);
  const [processingResolved, setProcessingResolved] = useState(false);
  const pipelineMode =
    processing?.pipeline?.mode ??
    (processingResolved && !processingFailed ? "pro" : null);
  const lite = pipelineMode === "lite";
  const modeCapabilities: HomeProjectionCapabilities | null = pipelineMode
    ? {
        content_plane: lite ? "raw" : "memory",
        generation_required: !lite,
        graph_enabled: pipelineMode === "ultra",
        evolve_available: pipelineMode === "ultra",
      }
    : null;
  const refreshSdkMap = useCallback(() => {
    if (lite) setSdkRawCursor(null);
    bumpSdkMap();
  }, [bumpSdkMap, lite]);
  const requestedQuery = sdkLibraryQuery.trim();
  const sdkPageSettled =
    sdkLibrary.settledMode === pipelineMode &&
    sdkLibrary.settledSource === sdkLibrarySource &&
    sdkLibrary.settledQuery.trim() === requestedQuery &&
    sdkLibrary.page?.offset === sdkLibraryOffset;
  const sdkPage = sdkPageSettled ? sdkLibrary.page : null;
  const sdkGraphRequestedScope = JSON.stringify([
    pipelineMode,
    sdkLibrarySource,
  ]);
  const sdkGraphSettled = sdkGraph?.scope === sdkGraphRequestedScope;
  const visibleSdkGraph =
    sdkGraph?.scope === sdkGraphRequestedScope ? sdkGraph.value : null;
  const sdkGraphFailed =
    sdkGraph?.scope === sdkGraphRequestedScope &&
    sdkGraph.status === "failed" &&
    sdkGraph.value === null;
  // A keyword search pages through one server-side result window. The id is
  // kept for the query and source it was searched with; a new query or
  // source starts without one, so the server searches afresh.
  const sdkSearchWindow = useRef<{ key: string; id: string | null }>({
    key: "",
    id: null,
  });
  // The page the list shows, for the namespace of a memory being opened.
  const sdkPageRef = useRef<MemoryLibraryPage | null>(null);
  sdkPageRef.current = sdkPage;
  const sdkMapVisible =
    simplified && active && displayedMode === "map" && pipelineMode !== null;
  useEffect(() => {
    if (!sdkMapVisible) return;
    // A write bumps the revision; reading it here re-runs the read at once
    // instead of waiting for the 30s poll.
    void sdkMapRevision;
    let cancelled = false;
    let controller: AbortController | null = null;
    const source = sdkLibrarySource || undefined;
    const query = sdkLibraryQuery.trim() || undefined;
    const windowKey = JSON.stringify([pipelineMode, query ?? "", source ?? ""]);
    if (sdkSearchWindow.current.key !== windowKey)
      sdkSearchWindow.current = { key: windowKey, id: null };
    const read = async (first: boolean) => {
      controller?.abort();
      const request = new AbortController();
      controller = request;
      if (first) setSdkLibrary((current) => ({ ...current, loading: true }));
      try {
        const rememberRaw = (items: RawMapRecord[]) => {
          const next =
            sdkRawCursor && rawRecordsScope.current === windowKey
              ? new Map(rawRecords.current)
              : new Map<string, RawMapRecord>();
          if (selectedRawRef.current)
            next.set(
              rawRecordId(selectedRawRef.current),
              selectedRawRef.current,
            );
          for (const item of items) next.set(rawRecordId(item), item);
          rawRecords.current = next;
          rawRecordsScope.current = windowKey;
        };
        const page = lite
          ? query
            ? await searchSdkRaw(query, {
                source,
                signal: request.signal,
              }).then((items) => {
                rememberRaw(items);
                return rawSearchPage(items);
              })
            : await getSdkRaw({
                source,
                offset: 0,
                limit: SDK_RAW_PAGE_SIZE,
                paging: "cursor",
                cursor: sdkRawCursor,
                signal: request.signal,
              }).then((rawPage) => {
                rememberRaw(rawPage.items);
                return rawLibraryPage(rawPage);
              })
          : await getSdkMemoryLibrary(
              {
                statuses: ["active"],
                source,
                query,
                offset: sdkLibraryOffset,
                limit: SDK_MEMORY_PAGE_SIZE,
                windowId: query ? sdkSearchWindow.current.id : null,
              },
              request.signal,
            ).then(sdkLibraryPage);
        if (cancelled) return;
        if (!lite && sdkSearchWindow.current.key === windowKey)
          sdkSearchWindow.current = {
            key: windowKey,
            id: page.window_id ?? null,
          };
        // The window expired: the server searched again and answered page 1.
        if (page.window_reset && sdkLibraryOffset !== 0) setSdkLibraryOffset(0);
        setSdkLibrary((current) => {
          const currentPage = current.page;
          const append =
            lite &&
            sdkRawCursor !== null &&
            currentPage !== null &&
            current.settledSource === sdkLibrarySource &&
            current.settledQuery === sdkLibraryQuery &&
            current.settledMode === pipelineMode;
          if (!append)
            return {
              page,
              loading: false,
              failed: false,
              settledSource: sdkLibrarySource,
              settledQuery: sdkLibraryQuery,
              settledMode: pipelineMode,
            };
          const items = [...currentPage.items];
          const known = new Set(items.map((item) => item.memory_id));
          for (const item of page.items)
            if (!known.has(item.memory_id)) {
              known.add(item.memory_id);
              items.push(item);
            }
          return {
            page: {
              ...page,
              items,
              total: items.length + (page.has_more ? 1 : 0),
              offset: 0,
              source_ids_by_memory: {
                ...currentPage.source_ids_by_memory,
                ...page.source_ids_by_memory,
              },
              source_time_by_memory: {
                ...currentPage.source_time_by_memory,
                ...page.source_time_by_memory,
              },
            },
            loading: false,
            failed: false,
            settledSource: sdkLibrarySource,
            settledQuery: sdkLibraryQuery,
            settledMode: pipelineMode,
          };
        });
      } catch (reason) {
        if (cancelled || request.signal.aborted) return;
        if (
          lite &&
          sdkRawCursor !== null &&
          apiErrorCode(reason) === "raw_cursor_stale"
        ) {
          setSdkRawCursor(null);
          return;
        }
        setSdkLibrary((current) => ({
          ...current,
          loading: false,
          failed: true,
        }));
      }
    };
    void read(true);
    const timer =
      lite && sdkRawCursor !== null
        ? undefined
        : setInterval(() => void read(false), 30_000);
    return () => {
      cancelled = true;
      if (timer !== undefined) clearInterval(timer);
      controller?.abort();
    };
  }, [
    sdkMapVisible,
    sdkMapRevision,
    sdkLibraryOffset,
    sdkLibraryQuery,
    sdkLibrarySource,
    sdkRawCursor,
    lite,
    pipelineMode,
  ]);
  const rawGraphRecords = useRef(new Map<string, RawMapRecord>());
  useEffect(() => {
    if (!sdkMapVisible) return;
    void sdkMapRevision;
    void sourceKey;
    const scope = sdkGraphRequestedScope;
    const source = sdkLibrarySource || undefined;
    const request = new AbortController();
    let cancelled = false;
    let reading = false;
    const readGraph = async () => {
      if (reading) return;
      reading = true;
      try {
        if (!lite) {
          const value = await getSdkMemoryGraph(
            { source, offset: 0, limit: 300 },
            request.signal,
          );
          if (cancelled) return;
          setSdkGraph({ scope, status: "ready", value });
          return;
        }
        const page = await getSdkRawGraph({
          source,
          offset: 0,
          limit: 300,
          signal: request.signal,
        });
        if (cancelled) return;
        rawGraphRecords.current.clear();
        for (const item of page.items) {
          const record = rawGraphRecord(item);
          rawGraphRecords.current.set(rawRecordId(record), record);
        }
        setSdkGraph({
          scope,
          status: "ready",
          value: rawGraphProjection(page.items, page.total, sourcesRef.current),
        });
      } catch {
        if (cancelled || request.signal.aborted) return;
        setSdkGraph((current) => ({
          scope,
          status: "failed",
          value: current?.scope === scope ? current.value : null,
        }));
      } finally {
        reading = false;
      }
    };
    void readGraph();
    const timer = setInterval(() => void readGraph(), 30_000);
    return () => {
      cancelled = true;
      clearInterval(timer);
      request.abort();
    };
  }, [
    lite,
    sdkGraphRequestedScope,
    sdkLibrarySource,
    sdkMapRevision,
    sdkMapVisible,
    sourceKey,
  ]);
  const displayedSdkPage = sdkPage;
  const sdkFilterLabel = [
    sdkLibrary.settledSource
      ? `${t("memory:来源")} ${
          sdkLibrary.settledSource === UNATTRIBUTED_SOURCE_ID
            ? t("memory:未识别来源")
            : sourceNameOr(
                sources,
                sdkLibrary.settledSource,
                sdkLibrary.settledSource,
              )
        }`
      : "",
    sdkLibrary.settledQuery.trim()
      ? t("memory:关键词“{query}”", {
          query: sdkLibrary.settledQuery.trim(),
        })
      : "",
  ]
    .filter(Boolean)
    .join(" · ");
  const [draft, setDraft] = useState("");
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
  const [pendingAdd, setPendingAdd] = useState<PendingMemoryAdd | null>(null);
  const [checkingSetup, setCheckingSetup] = useState(false);
  // biome-ignore lint/correctness/useExhaustiveDependencies: Source authority changes invalidate an in-flight setup snapshot.
  useEffect(() => {
    if (!active && (simplified || !onArrivalChange)) return;
    if (sourceSnapshotStale) {
      setCheckingSetup(false);
      return;
    }
    let stopped = false;
    let request = 0;
    async function inspectSetup() {
      const version = ++request;
      setCheckingSetup(true);
      await Promise.allSettled([getModelProfiles()]);
      if (stopped || version !== request) return;
      setCheckingSetup(false);
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
  // The memory count is not read here: the map reads its own page, and this
  // summary only ever reported a count for the governed library.
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
    memoryCount: undefined,
    capabilities: modeCapabilities,
    blockedStages: processing?.blocked_stages,
    backlogPending: processing?.backlog_pending,
    phase: sourceSnapshotStale ? "unavailable" : "checking",
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
        if (!stopped) {
          setProcessing(next);
          setProcessingFailed(false);
          setProcessingResolved(true);
        }
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
  const rawDetailController = useRef<AbortController | null>(null);
  const answerEpoch = useRef(0);
  const answerMemories = useRef<AskMemory[]>([]);
  const closeDetail = useCallback(() => {
    detailEpoch.current++;
    rawDetailController.current?.abort();
    rawDetailController.current = null;
    setDetail(null);
    setSelectedRaw(null);
    setDetailLoad(null);
  }, []);
  const previousPipelineMode = useRef(pipelineMode);
  useEffect(() => {
    if (pipelineMode === null || previousPipelineMode.current === pipelineMode)
      return;
    previousPipelineMode.current = pipelineMode;
    rawRecords.current.clear();
    sdkSearchWindow.current = { key: "", id: null };
    setSdkLibraryOffset(0);
    setSdkRawCursor(null);
    setSdkLibraryQuery("");
    setSdkGraph(null);
    setSdkLibrary((current) => ({
      ...current,
      page: null,
      loading: true,
      failed: false,
      settledQuery: "",
      settledMode: null,
    }));
    closeDetail();
  }, [pipelineMode, closeDetail]);

  const previousSourceKey = useRef(sourceKey);
  useEffect(() => {
    void sourceSnapshotStale;
    if (simplified && previousSourceKey.current !== sourceKey) {
      closeDetail();
      deleteAnswer.current?.(false);
      deleteAnswer.current = null;
      setDeleteCount(0);
    }
    previousSourceKey.current = sourceKey;
  }, [simplified, sourceSnapshotStale, sourceKey, closeDetail]);

  useEffect(() => {
    if (!shouldPoll) return;
    let cancelled = false;
    /** Consume the shared processing read rather than issuing its own. Both
     * this subscriber and the background arrival poll read the same endpoint,
     * so one `fetch` per tick is the whole point of sharing the key. */
    const consumeProcessing = (value: unknown) => {
      if (!cancelled) {
        setProcessing(value as MemoryProcessingSummary);
        setProcessingFailed(false);
        setProcessingResolved(true);
      }
    };
    // `subscribePoll` only schedules ticks; the first read still has to happen
    // on mount. Do it once through the shared fetch so mount does not double-read
    // with the background arrival subscriber.
    void getMemoryProcessing()
      .then(consumeProcessing)
      .catch(() => {
        if (!cancelled) {
          setProcessingFailed(true);
          setProcessingResolved(true);
        }
      });
    // The processing poll shares one read with the background arrival poll on
    // the same key (3s wins over 12s).
    const stopProcessing = subscribePoll({
      key: "processing",
      intervalMs: 3_000,
      fetch: getMemoryProcessing,
      onValue: consumeProcessing,
    });
    return () => {
      cancelled = true;
      stopProcessing();
      detailEpoch.current++;
      rawDetailController.current?.abort();
      rawDetailController.current = null;
      answerEpoch.current++;
      deleteAnswer.current?.(false);
      deleteAnswer.current = null;
    };
  }, [shouldPoll]);

  async function ask(question: string, scope = "all-apps") {
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
          const memories: AskMemory[] = [...event.memories];
          answerMemories.current = memories;
          setStreaming((current) => ({
            text: current?.text ?? "",
            memories,
          }));
        } else if (event.type === "delta") {
          setStreaming((current) => ({
            text: (current?.text ?? "") + event.text,
            memories: current?.memories ?? [],
          }));
        } else if (event.type === "final") {
          const memories: AskMemory[] = [...event.memories];
          answerMemories.current = memories;
          setAnswer(
            "schema_version" in event
              ? {
                  schema_version: 2,
                  answer: event.answer,
                  memories: event.memories,
                  mode: event.mode,
                }
              : {
                  answer: event.answer,
                  memories: event.memories,
                  mode: event.mode,
                },
          );
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
    // Saving an answer adds a memory, so it needs the same ui.action /
    // ui.result pair as the composer's save.
    const trace = traceUiAction("memory", "submit");
    try {
      await addMemory(
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
          ? "answer_memory_add_failed"
          : "answer_memory_add_result_unknown",
      });
      throw reason;
    }
  }

  async function open(memoryId: string) {
    if (simplified && (!viewActive.current || stale.current)) return;
    const epoch = ++detailEpoch.current;
    rawDetailController.current?.abort();
    rawDetailController.current = null;
    setError(null);
    setDetail(null);
    setSelectedRaw(null);
    if (lite) {
      let record =
        rawRecords.current.get(memoryId) ??
        rawGraphRecords.current.get(memoryId);
      if (!record) {
        setDetailLoad({
          memoryId,
          status: "failed",
          error: t("sdk.raw.loadFailed"),
        });
        return;
      }
      if ("kind" in record) {
        setDetailLoad({ memoryId, status: "loading" });
        const request = new AbortController();
        rawDetailController.current = request;
        try {
          let offset = 0;
          while (true) {
            const page = await getSdkRaw({
              source: record.source || undefined,
              sessionId: record.session_id || undefined,
              offset,
              limit: 500,
              signal: request.signal,
            });
            const full = page.items.find(
              (item) => rawRecordId(item) === memoryId,
            );
            if (full) {
              record = full;
              rawRecords.current.set(memoryId, full);
              break;
            }
            offset += page.items.length;
            if (!page.items.length || offset >= page.total)
              throw new Error("raw_not_found");
          }
        } catch {
          if (epoch === detailEpoch.current) {
            rawDetailController.current = null;
            setDetailLoad({
              memoryId,
              status: "failed",
              error: t("sdk.raw.loadFailed"),
            });
          }
          return;
        }
        if (epoch !== detailEpoch.current) return;
        rawDetailController.current = null;
      }
      setSelectedRaw(record);
      setDetail(rawDetail(record));
      setDetailLoad(null);
      return;
    }
    setDetailLoad({ memoryId, status: "loading" });
    try {
      // The inspector reads the memory through v2 and renders it in place.
      // Staying here keeps the map's list, graph and detail where the design
      // put them.
      const record = await getSdkMemory(
        memoryId,
        sdkNamespace(sdkPageRef.current?.sdk_memory_refs?.[memoryId]),
      );
      const result = sdkDetail(record);
      if (epoch !== detailEpoch.current || result.memory.memory_id !== memoryId)
        return;
      setDetail(result);
      setDetailLoad(null);
      setDraft(sdkEditableText(record));
    } catch (reason) {
      if (epoch === detailEpoch.current) {
        setError(message(reason, t));
        setDetailLoad({
          memoryId,
          status: "failed",
          error: message(reason, t),
        });
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

  async function remove() {
    if (!detail) return;
    const epoch = detailEpoch.current;
    if (!(await confirmDelete(1))) return;
    if (simplified && (!viewActive.current || epoch !== detailEpoch.current))
      return;
    setBusy(true);
    setError(null);
    // An SDK memory is removed outright: there is no revision to undo.
    const trace = traceUiAction("memory", "confirm");
    try {
      await deleteSdkMemory(
        detail.memory.memory_id,
        trace.traceId,
        sdkNamespace(detail.sdk_ref),
      );
      trace.done("succeeded");
      closeDetail();
      refreshSdkMap();
    } catch (reason) {
      trace.done(writeTraceStatus(reason), {
        reason: "memory_action_failed",
      });
      setError(message(reason, t));
    } finally {
      setBusy(false);
    }
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!detail) return;
    setBusy(true);
    setError(null);
    // A rewrite replaces the SDK row instead of adding a revision, so there
    // is no revision to send and nothing to reconcile afterwards.
    const trace = traceUiAction("memory", "submit");
    try {
      const saved = await updateSdkMemory(
        detail.memory.memory_id,
        draft,
        trace.traceId,
        sdkNamespace(detail.sdk_ref),
      );
      trace.done("succeeded");
      setDetail(sdkDetail(saved));
      setDraft(sdkEditableText(saved));
      refreshSdkMap();
    } catch (reason) {
      trace.done(writeTraceStatus(reason), {
        reason: isDefiniteRefusal(reason)
          ? "memory_correct_failed"
          : "memory_correct_result_unknown",
      });
      setError(message(reason, t));
    } finally {
      setBusy(false);
    }
  }

  // What the reader owes attention, and what the pipeline is already handling
  // on its own. The second half is counted, never listed: an internal guard
  // that clears itself on the next pass is not something to act on. See
  // `worthNamingBlock`. An unknown outcome is in the second half too: Core
  // settles it by itself within seconds.
  const blocked = processing
    ? splitBlockedStages(processing.blocked_stages)
    : { named: [], retrying: 0 };
  const processingNeedsAttention = blocked.named.length > 0;

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
          <div className="memory-title-line">
            <h1 id="memory-title">
              {simplified ? t("memory:记忆") : t("memory:记忆空间")}
            </h1>
            {simplified && pipelineMode ? (
              <span className="memory-mode-label">{pipelineMode}</span>
            ) : null}
          </div>
          <span className="memory-loaded-count">
            {simplified && !management
              ? pipelineMode === null
                ? t("memory:记忆暂时无法读取")
                : lite
                  ? displayedSdkPage
                    ? displayedSdkPage.total_exact === false
                      ? t("memory:原始对话")
                      : sdkLibrary.settledQuery.trim()
                        ? tn("sdk.raw.found", displayedSdkPage.total)
                        : tn("sdk.raw.total", displayedSdkPage.total)
                    : sdkLibrary.failed
                      ? null
                      : t("memory:正在读取…")
                  : sdkPage
                    ? tn(
                        "memory:已沉淀 {count} 条{filter} · 当前展示 {shown} 条",
                        sdkPage.total,
                        {
                          filter: sdkFilterLabel
                            ? t("memory:（{filter}）", {
                                filter: sdkFilterLabel,
                              })
                            : "",
                          shown: new Intl.NumberFormat(locale).format(
                            sdkPage.items.length,
                          ),
                        },
                      )
                    : // The list below says a failed read once, with its retry.
                      sdkLibrary.failed
                      ? null
                      : t("memory:正在读取…")
              : null}
          </span>
        </div>
        <MemoryAddComposer
          suspended={!active}
          rawOnly={lite}
          onPendingChange={setPendingAdd}
          onSaved={() => {
            // A finished addition is new page content: re-read now rather than
            // leave it for the next poll.
            refreshSdkMap();
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
            aria-label={t("memory:记忆空间模式")}
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
                ["map", t("memorySpace.tabs.workbench"), Network],
                ["library", t("memory:记忆库"), LayoutList],
                ["ask", t("memory:问记忆"), MessageCircle],
                ["history", t("memory:历史对话"), TextSearch],
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
      {evolveRunning ? (
        <p className="memory-evolve-status" role="status">
          {text(evolveUncertain ? "正在核对整合状态…" : "正在整合记忆…")}
        </p>
      ) : null}
      {processing && (!simplified || managementOpen) ? (
        <details className="memory-processing-details">
          <summary>
            <span
              className={processingNeedsAttention ? "status-attention" : ""}
            >
              {processingNeedsAttention
                ? t("memory:整理需要处理")
                : processing.running || processing.pending
                  ? t("memory:正在整理记忆")
                  : t("memory:整理状态")}
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
                ? `${tn("memory:{count} 处理中", processing.running)} · ${queueWaiting(processing, t, tn)}${queueEta(processing, tn)}`
                : tn("memory:{count} 次整理已完成", processing.completed)}
              {processing.outside_window_pending ? (
                <span className="pipeline-outside-window">
                  {tn(
                    "memory: · {count} 条在窗口外（原文可搜）",
                    processing.outside_window_pending,
                  )}
                </span>
              ) : null}
              {processing.manual_extract_batches_planned ? (
                <span className="pipeline-manual-batches">
                  {t("memory: · 已规划 {planned} 批，当前第 {current} 批", {
                    planned: new Intl.NumberFormat(locale).format(
                      processing.manual_extract_batches_planned,
                    ),
                    current: new Intl.NumberFormat(locale).format(
                      processing.manual_extract_batches_current,
                    ),
                  })}
                </span>
              ) : null}
              {blocked.named.length ? (
                <span className="pipeline-blocked">
                  {blocked.named
                    .map((block) =>
                      tn(
                        "memory:{kind}卡在 {reason}（{count} 条）",
                        block.count,
                        {
                          kind: memoryText(taskKindLabel(block.kind)),
                          reason: memoryText(blockReasonLabel(block.reason)),
                        },
                      ),
                    )
                    .join(" · ")}
                </span>
              ) : null}
              {blocked.retrying ? (
                <span className="pipeline-retrying">
                  {` · ${tn(
                    "memory:{count} 条这次没能整理成功，正在重试",
                    blocked.retrying,
                  )}`}
                </span>
              ) : null}
            </p>
            <ExtractionWindowControls
              processing={processing}
              busy={busy}
              onBusy={setBusy}
              onError={setError}
              onRefresh={async () => {
                try {
                  setProcessing(await getMemoryProcessing());
                } catch (reason) {
                  setError(message(reason, t));
                }
              }}
            />
          </div>
        </details>
      ) : null}

      {!lite ? (
        <>
          <ModelBackoffBanner
            processing={processing}
            onConfigureModel={onConfigureModel}
          />
          <ModelPrompt
            processing={processing}
            hasMemories={(sdkPage?.total ?? 0) > 0}
            onConfigureModel={onConfigureModel}
          />
        </>
      ) : null}
      {simplified && pendingAdd ? (
        <p className="memory-pending-add" role="status">
          <span>
            {lite
              ? t(
                  pendingAdd.state === "failed"
                    ? "memory:原话已保存，整理未完成"
                    : "memory:正在保存…",
                )
              : t("memory:正在等待整理：")}
            {lite ? " " : null}
            {pendingAdd.text}
          </span>
          {!lite && pendingAdd.state === "waiting_for_model" ? (
            <button type="button" onClick={onConfigureModel}>
              {t("memory:配置整理模型")}
            </button>
          ) : null}
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
          <SdkRawHistory active={active && displayedMode === "history"} />
        </div>
      )}

      <form
        className="memory-query-bar"
        data-tour-id="memory-query-bar"
        hidden={displayedMode !== "ask"}
        onSubmit={async (event) => {
          event.preventDefault();
          if (busy || !query.trim()) return;
          setError(null);
          setBusy(true);
          try {
            await ask(query.trim());
          } catch (reason) {
            setError(message(reason, t));
          } finally {
            setBusy(false);
          }
        }}
      >
        <label htmlFor="memory-query">{t("memory:向已确认记忆提问")}</label>
        <div>
          <input
            id="memory-query"
            name="memory-query"
            autoComplete="off"
            value={query}
            disabled={busy}
            placeholder={t("memory:例如：我对真实数据有什么要求？")}
            onChange={(event) => setQuery(event.target.value)}
          />
          <button type="submit" disabled={busy || !query.trim()}>
            {busy ? t("memory:处理中…") : t("memory:根据记忆回答")}
          </button>
        </div>
      </form>

      {/* Native hiding retains the WebGL owner; its visibility observer pauses drawing. */}
      <MemoryMap
        key={simplified ? (pipelineMode ?? "unknown") : "full"}
        id="memory-mode-panel-map"
        simplified={simplified}
        contentKind={lite ? "raw" : "memory"}
        modeCapabilities={modeCapabilities}
        rawRecord={selectedRaw}
        libraryView={
          simplified
            ? {
                query: sdkLibraryQuery,
                source: sdkLibrarySource,
                page: displayedSdkPage,
                loading: sdkLibrary.loading,
                failed: sdkLibrary.failed && !evolveRunning,
                graphLoading: !sdkGraphSettled,
                graphFailed: sdkGraphFailed && !evolveRunning,
                stale: false,
                settledSource: sdkLibrary.settledSource,
                settledQuery: sdkLibrary.settledQuery,
                requestedOffset: sdkLibraryOffset,
                cursorPaging: lite && !sdkLibraryQuery.trim(),
                hasMore: !!sdkPage?.has_more && !!sdkPage?.next_cursor,
                unattributedSource: UNATTRIBUTED_SOURCE_ID,
                onQuery: setSdkLibraryQuery,
                onSource: setSdkLibrarySource,
                resetPage: () => {
                  setSdkLibraryOffset(0);
                  setSdkRawCursor(null);
                },
                onPage: (offset) => setSdkLibraryOffset(offset),
                onMore: () => {
                  if (sdkPage?.next_cursor)
                    setSdkRawCursor(sdkPage.next_cursor);
                },
                onRetry: refreshSdkMap,
              }
            : undefined
        }
        hidden={
          !active ||
          displayedMode !== "map" ||
          (simplified && pipelineMode === null)
        }
        memories={displayedSdkPage?.items ?? []}
        graph={visibleSdkGraph}
        sources={sources}
        processing={processing}
        arrival={memoryArrival}
        onViewHistory={simplified ? onConnect : () => setMode("history")}
        loaded={sdkPage !== null}
        detail={detail}
        detailLoad={detailLoad}
        overlayBlocked={
          !active ||
          deleteCount > 0 ||
          // Only the paged map waits for its page; the three-column surface
          // never reads one.
          (simplified && sdkPage === null)
        }
        draft={draft}
        busy={busy}
        answer={answer}
        streaming={streaming}
        onDraft={setDraft}
        onOpenMemory={(memoryId) => void open(memoryId)}
        onCloseDetail={closeDetail}
        onSave={save}
        onDelete={() => void remove()}
        requestIds={requestIds}
        onAsk={(fullQuestion) => {
          setError(null);
          void ask(fullQuestion, "*").catch((reason) =>
            setError(message(reason, t)),
          );
        }}
        onSaveAnswer={(saved) => {
          setError(null);
          void saveAnswer(saved).catch((reason) =>
            setError(message(reason, t)),
          );
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
                  ? t("memory:模型综合")
                  : answer.mode === "extractive"
                    ? t("memory:原文摘录")
                    : t("memory:没有命中")
                : streaming?.text
                  ? t("memory:正在生成")
                  : t("memory:正在检索记忆")}
              {tn(
                "memory: · 基于 {count} 条已确认记忆",
                (answer ?? streaming)?.memories.length ?? 0,
              )}
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
                  <button
                    type="button"
                    onClick={() => void open(memory.memory_id)}
                  >
                    {memory.content}
                  </button>
                </li>
              ))}
            </ol>
          </article>
        ) : (
          <p className="ask-intro">
            {t(
              "memory:回答只使用已确认记忆。每个答案都可以回到对应记忆和来源原话。",
            )}
          </p>
        )}
        <MemoryInspector
          detail={detail}
          busy={busy}
          draft={draft}
          onDraft={setDraft}
          onClose={closeDetail}
          onSave={save}
          onDelete={remove}
          onOpen={open}
        />
      </div>

      <div
        id="memory-mode-panel-library"
        className="memory-library-layout"
        hidden={displayedMode !== "library"}
      >
        {!simplified || (pipelineMode !== null && !lite) ? (
          <SdkMemoryLibrary
            active={active}
            focusedMemoryId={sdkFocusedMemoryId}
          />
        ) : null}
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
  const { t, tn } = useGuiLanguage();
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
    <p
      className="model-prompt"
      role="status"
      aria-label={t("memory:记忆未在生成")}
    >
      <span>
        {tn(
          "memory:还没有开始生成记忆：{reason}（{count} 条待处理）",
          blocked.count,
          {
            reason: t(
              `memory:${blockReasonLabel(blocked.reason)}` as Parameters<
                typeof t
              >[0],
            ),
          },
        )}
      </span>
      <button type="button" onClick={onConfigureModel}>
        {t("memory:去配置生成模型")}
      </button>
    </p>
  );
}

/** Core's circuit breaker paused model work because the provider keeps
 * failing (bad key, wrong URL, missing model, provider down).
 *
 * Nothing is lost and nothing needs retrying by hand: the queued work stays
 * pending and Core keeps probing on its own, so this only says what is wrong
 * and where to fix it. It is driven by the polled summary alone and goes away
 * on the first read that no longer carries `llm_backoff_error`. */
function ModelBackoffBanner({
  processing,
  onConfigureModel,
}: {
  processing: MemoryProcessingSummary | null;
  onConfigureModel(): void;
}) {
  const { t, tn } = useGuiLanguage();
  const code = processing?.llm_backoff_error;
  if (!processing || !code) return null;
  return (
    <p
      className="model-prompt"
      role="status"
      aria-label={t("memory:模型暂停处理")}
    >
      <span>
        {tn(
          "memory:模型配置有问题（{reason}），记忆暂停处理，已有 {count} 条在排队",
          processing.pending,
          {
            reason: t(
              `memory:${llmBackoffReasonLabel(code)}` as Parameters<
                typeof t
              >[0],
            ),
          },
        )}
      </span>
      <button type="button" onClick={onConfigureModel}>
        {t("memory:配置整理模型")}
      </button>
    </p>
  );
}

function MemoryInspector({
  detail,
  busy,
  draft,
  onDraft,
  onClose,
  onSave,
  onDelete,
  onOpen,
}: {
  detail: MemoryDetail | null;
  busy: boolean;
  draft: string;
  onDraft(value: string): void;
  onClose(): void;
  onSave(event: FormEvent<HTMLFormElement>): Promise<void>;
  onDelete(): Promise<void>;
  onOpen(memoryId: string): Promise<void>;
}) {
  const { t } = useGuiLanguage();
  if (!detail) {
    return (
      <aside
        className="memory-inspector memory-inspector-empty"
        data-tour-id="memory-original-pane"
      >
        <p>{t("memory:选择一条记忆")}</p>
        <small>{t("memoryInspector.emptyHint")}</small>
      </aside>
    );
  }
  return (
    <aside
      className="memory-inspector"
      aria-label={t("memory:记忆详情")}
      data-tour-id="memory-original-pane"
    >
      <button
        type="button"
        className="drawer-close"
        aria-label={t("memory:关闭记忆详情")}
        onClick={onClose}
      >
        ×
      </button>
      <p className="status-label">
        {kindLabel(detail.memory.kind, t)} ·{" "}
        {statusLabel(detail.memory.status, t)}
      </p>
      <p className="map-origin">{localizedMemoryProvenance(detail, t)}</p>
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
              {t("memory:查看替代它的记忆")}
            </button>
          ))}
          {(detail.memory.supersedes ?? []).map((memoryId) => (
            <button
              key={`of:${memoryId}`}
              type="button"
              className="quiet"
              onClick={() => void onOpen(memoryId)}
            >
              {t("memory:查看它取代的旧记忆")}
            </button>
          ))}
        </p>
      ) : null}
      {detail.memory.status === "confirmed" ? (
        <form onSubmit={onSave}>
          <label>
            {t("memory:记忆内容")}
            <textarea
              name="memory-content"
              autoComplete="off"
              required
              value={draft}
              onChange={(event) => onDraft(event.target.value)}
            />
          </label>
          <button type="submit" disabled={busy}>
            {t("memory:保存纠正")}
          </button>
        </form>
      ) : null}
      <div className="source-actions">
        {detail.memory.status !== "deleted" ? (
          <button
            type="button"
            className="quiet"
            disabled={busy}
            onClick={() => void onDelete()}
          >
            {t("memory:删除")}
          </button>
        ) : null}
      </div>
    </aside>
  );
}

function message(reason: unknown, t: Translate): string {
  const code =
    reason instanceof Error &&
    reason.message === "memory_answer_context_changed"
      ? reason.message
      : apiErrorCode(reason);
  const labels: Record<string, string> = {
    memory_answer_context_changed:
      "memory:记忆内容或使用权限已变化，回答已停止。请重新提问。",
    memory_source_revoked: "memory:这条记忆的来源授权已撤回，暂时不能读取。",
    memory_revision_conflict: "memory:记忆已被更新，请刷新后重试。",
    memory_not_correctable: "memory:已删除的记忆不能修改。",
    memory_deleted: "memory:这条记忆已经删除。",
    memory_superseded: "memory:这条记忆已被更新的记忆取代，只能查看或删除。",
    memory_nothing_to_undo: "memory:没有更早的修改可以撤销。",
    memory_evidence_not_found: "memory:来源原话已不存在，无法建立这条记忆。",
    memory_request_conflict: "memory:这次操作与已完成的请求不一致。",
    request_validation_failed: "memory:输入内容不符合要求。",
    local_store_unavailable: "memory:本地记忆库正忙，请稍后重试。",
    embedding_unavailable:
      "memory:搜索服务暂时不可用，无法检索记忆。请到设置中检查记忆搜索配置。",
    embedding_switch_in_progress:
      "memory:正在更新记忆索引，完成后才能修改或删除记忆。搜索仍可使用。",
    embedding_space_profile_missing:
      "memory:当前搜索模型配置已不存在，请到设置中重新配置记忆搜索。",
  };
  if (code && labels[code]) return t(labels[code] as Parameters<Translate>[0]);
  // No code at all means the request never reached the core — usually because
  // it is still starting. Saying only "failed" left the one case that resolves
  // itself looking like the ones that do not.
  if (code === null) return t("memory:还没连上本地 Core，正在重试…");
  return t("memory.operationFailed", { code });
}

function kindLabel(kind: string, t: Translate) {
  const labels: Record<string, Parameters<Translate>[0]> = {
    chat: "memory:对话",
    working: "memory:项目记忆",
    coding: "memory:编码偏好",
    schema: "memory:长期模式",
    intention: "memory:意图",
  };
  return labels[kind] ? t(labels[kind]) : kind;
}

function statusLabel(status: string, t: Translate) {
  const labels: Record<string, Parameters<Translate>[0]> = {
    confirmed: "memory:已确认",
    deleted: "memory:已删除",
    superseded: "memory:已被取代",
  };
  return labels[status] ? t(labels[status]) : status;
}

function localizedMemoryProvenance(detail: MemoryDetail, t: Translate): string {
  const evidence = detail.evidence.filter((item) => item.available);
  if (
    evidence.length &&
    evidence.every((item) => item.source_id === "memory-content-export")
  ) {
    const authors = new Set(
      evidence.map((item) => item.written_by ?? "unknown"),
    );
    const author =
      authors.size !== 1
        ? t("provenance.multiple")
        : authors.has("person")
          ? t("provenance.person")
          : authors.has("agent")
            ? t("provenance.agent")
            : t("provenance.unknown");
    return t("provenance.imported", { author });
  }
  return detail.memory.origin === "manual"
    ? t("provenance.manual")
    : detail.memory.origin === "answered"
      ? t("provenance.answered")
      : detail.memory.origin === "agent"
        ? t("provenance.generated")
        : t("provenance.conversation");
}

function queueWaiting(
  processing: MemoryProcessingSummary,
  t: Translate,
  tn: TranslateCount,
): string {
  const fresh = processing.fresh_pending ?? 0;
  const backlog = processing.backlog_pending ?? 0;
  const active = processing.active_queue ?? 0;
  const inFlight = processing.in_flight_count ?? 0;
  const strandedUnknown =
    processing.stranded_running === undefined && processing.running > inFlight;
  const stranded = processing.stranded_running ?? 0;
  const activeSuffix =
    inFlight || active
      ? t("memory: · 处理中 {running} / 待派发 {active}", {
          running: inFlight,
          active,
        })
      : strandedUnknown
        ? tn(
            "memory: · {count} 条任务状态待核对",
            processing.running - inFlight,
          )
        : stranded
          ? tn("memory: · {count} 条任务状态异常，正在恢复", stranded)
          : "";
  if (fresh + backlog === 0) {
    return `${tn("memory:{count} 等待", processing.pending)}${activeSuffix}`;
  }
  if (backlog === 0) {
    return `${tn("memory:最新 {count} 条待处理", fresh)}${activeSuffix}`;
  }
  if (fresh === 0) {
    return `${tn("memory:历史积压 {count} 条", backlog)}${activeSuffix}`;
  }
  return `${tn("memory:最新 {count} 条待处理", fresh)} · ${tn(
    "memory:历史积压 {count} 条",
    backlog,
  )}${activeSuffix}`;
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
  const { t } = useGuiLanguage();
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
      onError(message(reason, t));
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
      onError(message(reason, t));
    } finally {
      onBusy(false);
    }
  }

  return (
    <div className="extraction-window-controls">
      <label>
        {t("memory:自动提炼范围")}
        <select
          value={processing.extraction_window_preset}
          disabled={busy}
          onChange={(event) =>
            void changeWindow(event.target.value as ExtractionWindowPreset)
          }
        >
          {EXTRACTION_WINDOW_PRESETS.map((preset) => (
            <option key={preset} value={preset}>
              {t(
                `memory:${extractionWindowPresetLabel(preset)}` as Parameters<
                  typeof t
                >[0],
              )}
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
          {t("memory:手动提炼下一批")}
        </button>
      ) : null}
    </div>
  );
}

function queueEta(
  processing: MemoryProcessingSummary,
  tn: TranslateCount,
): string {
  // A local model handles one extraction at a time, so a long queue can mean
  // most of a day. Saying so is kinder than a bare count that looks stuck.
  const perTask = processing.seconds_per_completion;
  if (!processing.pending || !perTask || perTask <= 0) {
    return "";
  }
  const hours = (processing.pending * perTask) / 3600;
  if (hours < 1) {
    return tn(
      "memory:（约 {count} 分钟）",
      Math.max(1, Math.round(hours * 60)),
    );
  }
  if (hours < 24) {
    return tn("memory:（约 {count} 小时）", Math.round(hours));
  }
  return tn("memory:（约 {count} 天）", Math.round(hours / 24));
}
