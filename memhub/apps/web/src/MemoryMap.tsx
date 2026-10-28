import type {
  GovernedMemory,
  HostAskMemoryResponse,
  MemoryDetail,
  MemoryGraphNode,
  MemoryGraphResponse,
  MemoryLibraryPage,
  MemoryProcessingSummary,
  SdkAskMemory,
  SdkMemoryScene,
  SourceResponse,
} from "@one-memory/contracts";
import type { GuiLocale } from "@one-memory/i18n";
import {
  ArrowUp,
  ChevronLeft,
  ChevronRight,
  FileText,
  Network,
  Search,
  X,
} from "lucide-react";
import {
  type FormEvent,
  type RefObject,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { HandoffBar } from "./HandoffBar";
import { MemoryEvidencePanel } from "./MemoryEvidencePanel";
import { MemoryGraph3D } from "./MemoryGraph3D";
import { MemoryArrivalPanel } from "./memory-arrival";
import {
  type MemoryArrival,
  resolveMemoryArrival,
} from "./memory-arrival-state";
import { memoryText, useMemoryLanguage } from "./memory-language";
import {
  type MapAxis,
  mapGroups,
  projectMap,
  projectPagedSourceMap,
} from "./memory-map-model";
import { OptionMenu } from "./OptionMenu";
import { R6Button } from "./R6Controls";
import { SelectedMemoryDetail } from "./SelectedMemoryDetail";
import { sourceNameOr } from "./source-label";
import { useMediaPreference } from "./use-media-preference";
import { Answer, NodeDetail, type SavedAnswer } from "./Workbench";

type Props = {
  memories: GovernedMemory[];
  graph: MemoryGraphResponse | null;
  sources: SourceResponse[];
  processing: MemoryProcessingSummary | null;
  loaded: boolean;
  detail: MemoryDetail | null;
  detailLoad?: {
    memoryId: string;
    status: "loading" | "failed";
    error?: string;
  } | null;
  overlayBlocked?: boolean;
  draft: string;
  busy: boolean;
  answer: HostAskMemoryResponse | null;
  streaming: {
    text: string;
    memories: (GovernedMemory | SdkAskMemory)[];
  } | null;
  onDraft(next: string): void;
  onOpenMemory(memoryId: string): void;
  onCloseDetail(): void;
  onSave(event: FormEvent<HTMLFormElement>): void;
  onDelete(): void;
  onAsk(question: string): void;
  onSaveAnswer(draft: SavedAnswer): void;
  onConnect(): void;
  onConfigureModel(): void;
  /** Cleared whenever the draft changes, so a retried correction never
   * reuses the request id prepared for text the user has since replaced. */
  requestIds: RefObject<Map<string, string>>;
  simplified?: boolean;
  hidden?: boolean;
  id?: string;
  arrival?: MemoryArrival;
  onViewHistory?(): void;
  libraryView?: {
    query: string;
    source: string;
    page: MemoryLibraryPage | null;
    loading: boolean;
    failed: boolean;
    /** The graph for the current source/query scope has not arrived yet. */
    graphLoading?: boolean;
    graphFailed?: boolean;
    stale: boolean;
    settledSource?: string;
    settledQuery?: string;
    /** The source id the SDK uses for "no source given" (shown as 未识别来源). */
    unattributedSource?: string;
    onQuery(value: string): void;
    onSource(value: string): void;
    resetPage(): void;
    onPage(offset: number): void;
    onRetry(): void;
  };
};

function dayStamp(
  value: string | null | undefined,
  locale: GuiLocale = "zh-CN",
) {
  if (!value || !Number.isFinite(Date.parse(value)))
    return memoryText(locale, "时间未知");
  const date = new Date(value);
  const now = new Date();
  if (
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate()
  )
    return memoryText(locale, "今天");
  return date.toLocaleDateString(locale);
}

/** "10月2日" / "2 oct", with the year only when it is not this year. */
function monthDay(value: string, locale: GuiLocale): string | null {
  if (!Number.isFinite(Date.parse(value))) return null;
  const date = new Date(value);
  return new Intl.DateTimeFormat(locale, {
    day: "numeric",
    month: "short",
    ...(date.getFullYear() !== new Date().getFullYear()
      ? { year: "numeric" }
      : {}),
  }).format(date);
}

/** D96: when the cited conversation happened, not when the memory was generated. */
function sourceStamp(
  time: { at: string; basis: "conversation" | "import" | "write" } | undefined,
  fallback: string | null | undefined,
  locale: GuiLocale = "zh-CN",
): string {
  const day = time ? monthDay(time.at, locale) : null;
  if (time && day)
    return memoryText(
      locale,
      time.basis === "conversation"
        ? "对话于 {day}"
        : time.basis === "write"
          ? "写入于 {day}"
          : "导入于 {day}",
      { day },
    );
  return dayStamp(fallback, locale);
}

/** The list's type badge for an SDK memory's scene. */
const SCENE_LABELS: Record<SdkMemoryScene, string> = {
  chat: "对话",
  working: "工作",
  coding: "编码",
};

export function MemoryMap(props: Props) {
  const { locale, text, count, plain } = useMemoryLanguage();
  const {
    memories,
    graph: baseGraph,
    sources,
    detail,
    loaded,
    onOpenMemory,
    onCloseDetail,
    onAsk,
    busy,
    hidden = false,
    id,
  } = props;
  const library = props.libraryView;
  const serverPaged = library !== undefined;
  const unattributedLabel = text("未识别来源");
  const graph = useMemo(
    () =>
      serverPaged
        ? projectPagedSourceMap(
            baseGraph,
            library?.page ?? null,
            sources,
            library?.unattributedSource
              ? { [library.unattributedSource]: unattributedLabel }
              : undefined,
          )
        : baseGraph,
    [
      baseGraph,
      serverPaged,
      library?.page,
      library?.unattributedSource,
      sources,
      unattributedLabel,
    ],
  );
  const refreshing = !!library?.page && library.loading;
  const overlaysSuspended = hidden || !!props.overlayBlocked;
  const [axis, setAxis] = useState<MapAxis>("source");
  const [groupKey, setGroupKey] = useState("");
  const [search, setSearch] = useState("");
  // On a server-paged list, typing only edits the words; the search runs when
  // the reader submits it (the 搜索 button or Enter), not on every keystroke.
  const [searchDraft, setSearchDraft] = useState(library?.query ?? "");
  const submittedQuery = library?.query;
  useEffect(() => {
    if (submittedQuery !== undefined) setSearchDraft(submittedQuery);
  }, [submittedQuery]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [groupNode, setGroupNode] = useState<MemoryGraphNode | null>(null);
  const [pendingSourceSelection, setPendingSourceSelection] = useState<
    string | null
  >(null);
  const [question, setQuestion] = useState("");
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [filterNotice, setFilterNotice] = useState("");
  const [arrivedIds, setArrivedIds] = useState<string[]>([]);
  const seenMemoryIds = useRef<Set<string> | null>(null);
  const compact = useMediaPreference("(max-width: 1100px)");
  const composing = useRef(false);
  const pane = useRef<HTMLElement>(null);
  const memoryList = useRef<HTMLUListElement>(null);
  const inspectorBody = useRef<HTMLDivElement>(null);
  const openedDefault = useRef(false);
  const scrollSelection = useRef(false);
  const controlledSource = library
    ? library.source
      ? `source:${library.source}`
      : ""
    : groupKey;
  const active = useMemo(
    () => memories.filter((m) => m.status !== "deleted"),
    [memories],
  );
  const arrival = resolveMemoryArrival({
    arrival: props.arrival,
    processing: props.processing,
    sources,
    loaded,
  });
  useEffect(() => {
    if (!loaded || library) return;
    const ids = active.map((memory) => memory.memory_id);
    if (!seenMemoryIds.current) {
      seenMemoryIds.current = new Set(ids);
      return;
    }
    const added = ids.filter((id) => !seenMemoryIds.current?.has(id));
    for (const id of added) seenMemoryIds.current.add(id);
    const currentIds = new Set(ids);
    setArrivedIds((previous) => {
      const retained = previous.filter((id) => currentIds.has(id));
      return !added.length && retained.length === previous.length
        ? previous
        : [...retained, ...added];
    });
  }, [active, loaded, library]);
  const arrived = new Set(arrivedIds);
  const arrivalCount = active.filter((memory) =>
    arrived.has(memory.memory_id),
  ).length;
  const groups = useMemo(
    () => mapGroups(active, graph, axis),
    [active, graph, axis],
  );
  const group = groups.find((g) => g.key === groupKey);
  const sourceGroups = useMemo(
    () => mapGroups(active, graph, "source"),
    [active, graph],
  );
  function sourceLabel(memoryId: string): string {
    if (library) {
      const ids = library.page?.source_ids_by_memory?.[memoryId];
      return ids?.length
        ? ids
            .map((id) =>
              id === library.unattributedSource
                ? text("未识别来源")
                : sourceNameOr(sources, id, text("来源待核对")),
            )
            .join("、")
        : text("来源待核对");
    }
    return (
      sourceGroups
        .filter((entry) => entry.memoryIds.includes(memoryId))
        .map((entry) => entry.label)
        .join(text("列表分隔符")) || text("来源待核对")
    );
  }
  const filtered = useMemo(
    () =>
      active.filter(
        (m) =>
          (serverPaged
            ? axis === "source" ||
              !groupKey ||
              group?.memoryIds.includes(m.memory_id)
            : !group || group.memoryIds.includes(m.memory_id)) &&
          (serverPaged ||
            !search ||
            `${m.content} ${m.tags.join(" ")}`
              .toLocaleLowerCase()
              .includes(search.toLocaleLowerCase())),
      ),
    [active, group, search, serverPaged, axis, groupKey],
  );
  const projection = useMemo(() => {
    // Until the new scope's graph arrives (or after it failed), drawing the
    // previous scope's graph would show the wrong source.
    if (serverPaged && (library?.graphLoading || library?.graphFailed))
      return {
        total_memories: library.page?.total ?? 0,
        projected_memories: 0,
        hy_graph_available: false,
        nodes: [],
        edges: [],
      };
    // Source filtering owns the graph scope, while pagination owns only the
    // left-hand list window. Projecting the graph back down to the current
    // 20-row page made "All applications" look like whichever source happened
    // to dominate that page. Keyword search is intentionally page-scoped.
    if (serverPaged && !library?.query) {
      return (
        baseGraph ??
        projectMap(
          graph,
          active,
          axis,
          filtered.map((m) => m.memory_id),
        )
      );
    }
    const result = projectMap(
      graph,
      active,
      axis,
      filtered.map((m) => m.memory_id),
    );
    if (active.length || serverPaged) return result;
    return {
      ...result,
      nodes: sources
        .filter(
          (s) =>
            s.authorized &&
            s.imported_conversations > 0 &&
            (!groupKey || groupKey === `source:${s.source_id}`) &&
            (!search ||
              `${s.display_name} ${s.source_id}`
                .toLocaleLowerCase()
                .includes(search.toLocaleLowerCase())),
        )
        .map((s) => ({
          node_id: `source:${s.source_id}`,
          node_type: "source" as const,
          source_id: s.source_id,
          memory_id: null,
          label: `${s.display_name} · ${s.imported_conversations} 段`,
          status: null,
          kind: null,
          confidence: null,
        })),
    };
  }, [
    baseGraph,
    graph,
    axis,
    filtered,
    active,
    groupKey,
    search,
    sources,
    serverPaged,
    library?.query,
    library?.graphLoading,
    library?.graphFailed,
    library?.page?.total,
  ]);
  const visibleGroupNode = groupNode
    ? (projection.nodes.find(
        (node) =>
          node.node_id === groupNode.node_id &&
          (node.node_type !== "source" ||
            sources.some(
              (source) =>
                source.source_id === node.source_id && source.authorized,
            )),
      ) ?? null)
    : null;
  const selectedSource =
    visibleGroupNode?.node_type === "source"
      ? sources.find(
          (source) =>
            source.source_id === visibleGroupNode.source_id &&
            source.authorized,
        )
      : undefined;
  useEffect(() => {
    // Source revocation also clears a suspended preview, without closing shared memory detail.
    if (groupNode && !visibleGroupNode) {
      setGroupNode(null);
      setPendingSourceSelection(null);
      setInspectorOpen(false);
    }
  }, [groupNode, visibleGroupNode]);
  const requestedId = selectedId ?? detail?.memory.memory_id ?? null;
  const activeId = active.some((memory) => memory.memory_id === requestedId)
    ? requestedId
    : null;
  const inspectorIdentity = activeId
    ? `memory:${activeId}`
    : selectedSource
      ? `source:${selectedSource.source_id}`
      : (visibleGroupNode?.node_id ?? "empty");
  const visibleDetail =
    detail &&
    (!activeId || activeId === detail.memory.memory_id) &&
    filtered.some((m) => m.memory_id === detail.memory.memory_id)
      ? detail
      : null;
  const lastDetail = useRef(visibleDetail);
  if (visibleDetail) lastDetail.current = visibleDetail;
  const inspectorDetail =
    visibleDetail ??
    (activeId && lastDetail.current ? lastDetail.current : null);
  const pick = useCallback(
    (memoryId: string, scroll = true) => {
      if (!active.some((memory) => memory.memory_id === memoryId)) return;
      setPendingSourceSelection(null);
      scrollSelection.current = scroll;
      if (!filtered.some((memory) => memory.memory_id === memoryId)) {
        setGroupKey("");
        setSearch("");
        setFilterNotice("已清除筛选，定位到关联记忆。");
      }
      setGroupNode(null);
      setSelectedId(memoryId);
      setInspectorOpen(true);
      onOpenMemory(memoryId);
    },
    [active, filtered, onOpenMemory],
  );
  useEffect(() => {
    if (
      !library ||
      !pendingSourceSelection ||
      overlaysSuspended ||
      library.loading ||
      library.failed ||
      !selectedSource ||
      selectedSource.source_id !== pendingSourceSelection ||
      library.source !== pendingSourceSelection ||
      (library.settledSource ?? library.source) !== pendingSourceSelection ||
      (library.settledQuery ?? library.query).trim() ||
      library.page?.offset !== 0
    )
      return;
    setPendingSourceSelection(null);
    const first = active[0];
    if (first) pick(first.memory_id, false);
  }, [
    active,
    library,
    overlaysSuspended,
    pendingSourceSelection,
    pick,
    selectedSource,
  ]);
  useEffect(() => {
    if (!props.simplified || openedDefault.current || overlaysSuspended) return;
    if (library?.loading && filtered.length === 0) return;
    if (selectedId || detail) return;
    const newest = filtered[0];
    if (!newest) return;
    openedDefault.current = true;
    pick(newest.memory_id, false);
  }, [
    props.simplified,
    overlaysSuspended,
    library?.loading,
    filtered,
    selectedId,
    detail,
    pick,
  ]);
  useEffect(() => {
    if (!activeId || !scrollSelection.current) return;
    scrollSelection.current = false;
    const container = pane.current;
    const item = Array.from(
      pane.current?.querySelectorAll<HTMLElement>("[data-memory-id]") ?? [],
    ).find((node) => node.dataset.memoryId === activeId);
    if (!container || !item) return;
    const containerBounds = container.getBoundingClientRect();
    const itemBounds = item.getBoundingClientRect();
    if (itemBounds.top < containerBounds.top) {
      container.scrollTop -= containerBounds.top - itemBounds.top;
    } else if (itemBounds.bottom > containerBounds.bottom) {
      container.scrollTop += itemBounds.bottom - containerBounds.bottom;
    }
  }, [activeId]);
  useLayoutEffect(() => {
    // Detail content owns its own scrollport. A newly selected memory must
    // start at its heading instead of inheriting the previous inspector view.
    if (inspectorIdentity === "empty") return;
    if (inspectorBody.current) inspectorBody.current.scrollTop = 0;
  }, [inspectorIdentity]);
  const settledPageOffset = library?.page?.offset;
  useLayoutEffect(() => {
    // A settled page is a new reading surface, not a continuation of the
    // previous page's scroll position.
    if (settledPageOffset === undefined) return;
    if (memoryList.current) memoryList.current.scrollTop = 0;
  }, [settledPageOffset]);

  const clear = useCallback(() => {
    setSelectedId(null);
    setGroupNode(null);
    setPendingSourceSelection(null);
    setInspectorOpen(false);
    onCloseDetail();
  }, [onCloseDetail]);
  useEffect(() => {
    // A background map does not own the shared inspector in another mode.
    if (overlaysSuspended || library?.loading) return;
    if (
      requestedId &&
      !active.some((memory) => memory.memory_id === requestedId)
    ) {
      clear();
      return;
    }
    if (!activeId || filtered.some((memory) => memory.memory_id === activeId))
      return;
    clear();
    setFilterNotice("所选记忆不在当前筛选内，已取消选择。");
  }, [
    activeId,
    requestedId,
    active,
    filtered,
    clear,
    overlaysSuspended,
    library?.loading,
  ]);
  function closeInspector() {
    if (!compact) {
      clear();
      return;
    }
    setPendingSourceSelection(null);
    setInspectorOpen(false);
  }
  function chooseGroup(key: string) {
    setGroupNode(null);
    if (library) {
      clear();
      // A source picked on the map sets this too; keep both on the one choice.
      setGroupKey(key);
      library.onSource(key.startsWith("source:") ? key.slice(7) : "");
      library.resetPage();
      return;
    }
    setGroupKey(key);
  }
  function pickNode(node: MemoryGraphNode) {
    if (node.memory_id) {
      pick(node.memory_id);
      return;
    }
    clear();
    setGroupNode(node);
    setInspectorOpen(true);
    if (node.node_type === "source" && node.source_id) {
      setAxis("source");
      setGroupKey(`source:${node.source_id}`);
      if (library) {
        setPendingSourceSelection(node.source_id);
        library.onQuery("");
        library.onSource(node.source_id);
        library.resetPage();
      } else {
        const first = sourceGroups
          .find((entry) => entry.key === `source:${node.source_id}`)
          ?.memoryIds.find((memoryId) =>
            active.some((memory) => memory.memory_id === memoryId),
          );
        if (first) pick(first, false);
      }
    }
    if (node.node_type === "topic") {
      if (props.simplified) return;
      setAxis("topic");
      setGroupKey(`topic:${node.label}`);
    }
  }
  const label =
    group?.label ??
    sources.find(
      (source) =>
        source.authorized && groupKey === `source:${source.source_id}`,
    )?.display_name ??
    (library?.unattributedSource &&
    groupKey === `source:${library.unattributedSource}`
      ? text("未识别来源")
      : null) ??
    text("全部记忆");
  if (
    props.simplified &&
    (!library ||
      (!library.loading &&
        !library.failed &&
        !library.stale &&
        !library.query &&
        !library.source &&
        library.page?.total === 0)) &&
    loaded &&
    !active.length
  ) {
    const running = !!props.processing?.running;
    const queued = !!props.processing?.pending;
    return (
      <section className="selected-memory-empty" hidden={hidden}>
        <div>
          <h2>
            {running
              ? text("已开始处理所选对话")
              : queued
                ? text("处理请求已受理")
                : text("还没有已生成的记忆")}
          </h2>
          <p>
            {running || queued
              ? text("模型连接已通过。首批记忆生成后，会逐步出现在记忆列表里。")
              : arrival.importedConversations > 0
                ? text("已导入 {count} 段对话，整理成记忆后会出现在这里。", {
                    count: count(arrival.importedConversations),
                  })
                : arrival.importedMessages > 0
                  ? text("历史已保存在本机，记忆就绪后会出现在这里。")
                  : text("连接应用并导入历史后，可以开始沉淀记忆。")}
            {(running || queued) && (
              <small>
                {text(
                  "当前已生成 0 条记忆。请求已受理不等于已经完成，原始对话、模型处理任务和记忆数量分别统计。",
                )}
              </small>
            )}
          </p>
          <div>
            <R6Button onClick={props.onConnect}>{text("返回应用")}</R6Button>
          </div>
        </div>
        {running && (
          <span className="selected-memory-loading" aria-hidden="true" />
        )}
      </section>
    );
  }
  return (
    <section
      id={id}
      className="core-memory-map"
      aria-label={text("记忆工作台")}
      hidden={hidden}
      inert={hidden}
      onKeyDown={(e) => {
        if (overlaysSuspended || e.key !== "Escape" || e.defaultPrevented)
          return;
        if (
          (e.target as HTMLElement).closest(
            '[role="dialog"], input, textarea, select',
          )
        )
          return;
        clear();
      }}
    >
      <nav className="map-library" aria-label={text("来源与主题")} ref={pane}>
        {props.simplified ? (
          <div className="selected-source-filter">
            <label htmlFor="selected-source-filter">{text("来源")}</label>
            <OptionMenu
              id="selected-source-filter"
              ariaLabel={text("来源")}
              className="om-option-menu--plain"
              value={controlledSource}
              options={[
                { value: "", label: text("全部应用") },
                ...(library?.source &&
                library.source !== library.unattributedSource &&
                !sources.some(
                  (source) =>
                    source.source_id === library.source && source.authorized,
                )
                  ? [
                      {
                        value: `source:${library.source}`,
                        label: text("所选来源待核对"),
                      },
                    ]
                  : []),
                ...(library
                  ? [
                      ...sources
                        .filter((source) => source.authorized)
                        .map((source) => ({
                          value: `source:${source.source_id}`,
                          label: source.display_name,
                        })),
                      ...(library.unattributedSource
                        ? [
                            {
                              value: `source:${library.unattributedSource}`,
                              label: text("未识别来源"),
                            },
                          ]
                        : []),
                    ]
                  : groups.map((group) => ({
                      value: group.key,
                      label: group.label,
                    }))),
              ]}
              onChange={chooseGroup}
            />
          </div>
        ) : (
          <fieldset className="map-axis" aria-label={text("地图组织方式")}>
            {(
              [
                ["source", "来源"],
                ["topic", "主题"],
              ] as const
            ).map(([value, title]) => (
              <button
                type="button"
                key={value}
                aria-pressed={axis === value}
                onClick={() => {
                  setGroupNode(null);
                  setAxis(value);
                  setGroupKey("");
                }}
              >
                {text(title)}
              </button>
            ))}
          </fieldset>
        )}
        {/* biome-ignore lint/a11y/useSemanticElements: the search form is the flex row .map-search styles; a <search> wrapper would add a box around it. */}
        <form
          className="map-search"
          role="search"
          onSubmit={(e) => {
            e.preventDefault();
            if (!library) return;
            const words = searchDraft.trim();
            setFilterNotice("");
            clear();
            if (words === library.query.trim()) {
              library.onRetry();
              return;
            }
            library.onQuery(words);
            library.resetPage();
          }}
        >
          <Search size={16} aria-hidden="true" />
          <input
            aria-label={text("筛选地图记忆")}
            name="map-memory-search"
            autoComplete="off"
            placeholder={
              props.simplified ? text("搜索记忆") : text("筛选记忆…")
            }
            value={library ? searchDraft : search}
            maxLength={library ? 512 : undefined}
            onKeyDown={(e) => {
              // Enter that confirms an IME candidate is not a search.
              if (e.key === "Enter" && e.nativeEvent.isComposing)
                e.preventDefault();
            }}
            onChange={(e) => {
              if (library) {
                const next = e.target.value;
                setSearchDraft(next);
                // An empty search box means "no keyword filter". Keeping the
                // last submitted query active after the reader removed its
                // text made the source picker look empty even when that app
                // had memories.
                if (!next && library.query.trim()) {
                  setFilterNotice("");
                  clear();
                  library.onQuery("");
                  library.resetPage();
                }
                return;
              }
              setFilterNotice("");
              setSearch(e.target.value);
            }}
          />
          {library ? (
            <button
              type="submit"
              className="map-search-submit"
              disabled={library.loading || hidden}
            >
              {text("搜索")}
            </button>
          ) : null}
        </form>
        {library && (library.loading || library.stale || library.failed) ? (
          <p className="map-list-status" aria-live="polite">
            {library.failed
              ? text("读取失败")
              : library.stale
                ? text("正在核对来源…")
                : refreshing && library.page?.total !== 0
                  ? text("正在核对…")
                  : text("正在读取…")}
          </p>
        ) : null}
        {!props.simplified && (
          <div className="map-groups">
            <button
              type="button"
              className={!groupKey ? "selected" : ""}
              aria-pressed={!groupKey}
              onClick={() => chooseGroup("")}
            >
              {text("全部记忆")} <span>{active.length}</span>
            </button>
            {groups.map((g) => (
              <button
                type="button"
                key={g.key}
                className={g.key === groupKey ? "selected" : ""}
                aria-pressed={g.key === groupKey}
                onClick={() => chooseGroup(g.key)}
                title={g.label}
              >
                <span>{g.label}</span>
                <span>{g.memoryIds.length}</span>
              </button>
            ))}
          </div>
        )}
        {!props.simplified && (
          <div className="map-list-heading">
            <span>{label}</span>
            <span>{text("{count} 条", { count: filtered.length })}</span>
          </div>
        )}
        {arrivalCount > 0 && (
          <div className="map-arrival-notice">
            <span role="status">
              {text(
                "新增 {count} 条记忆",
                { count: plain(arrivalCount) },
                arrivalCount,
              )}
            </span>
            <button
              type="button"
              title={text("关闭新增记忆提示")}
              aria-label={text("关闭新增记忆提示")}
              onClick={() => setArrivedIds([])}
            >
              <X size={15} aria-hidden="true" />
            </button>
          </div>
        )}
        {filterNotice && (
          <p className="map-filter-notice" role="status">
            {text(filterNotice)}
          </p>
        )}
        <ul
          ref={memoryList}
          className="map-memory-list"
          aria-label={text("地图记忆列表")}
          id="memory-pane-memories"
        >
          {filtered.map((m) => (
            <li key={m.memory_id} data-memory-id={m.memory_id}>
              <button
                type="button"
                className="map-memory-item"
                aria-pressed={activeId === m.memory_id}
                onClick={() => pick(m.memory_id)}
              >
                <span className={`map-state ${m.status}`} aria-hidden="true" />
                <span className="map-memory-content">{m.content}</span>
                {library?.page?.sdk_memory_refs?.[m.memory_id] ? (
                  <span
                    className="map-memory-scene"
                    data-scene={library.page.sdk_memory_refs[m.memory_id].scene}
                  >
                    {text(
                      SCENE_LABELS[
                        library.page.sdk_memory_refs[m.memory_id].scene
                      ],
                    )}
                  </span>
                ) : null}
                {m.tags.length ? (
                  <span className="map-memory-tags wb-tags">
                    {m.tags.map((tag) => (
                      <span key={tag} className="wb-tag">
                        {tag}
                      </span>
                    ))}
                  </span>
                ) : null}
                <small>
                  {props.simplified ? (
                    <>
                      {`${sourceLabel(m.memory_id)} · ${sourceStamp(
                        library?.page?.source_time_by_memory?.[m.memory_id],
                        m.updated_at,
                        locale,
                      )}`}
                      {/* D99: the conversation date alone would hide that this
                          one was made today, so the generated day stays too. */}
                      <span className="map-memory-made">
                        {text("沉淀于 ")}
                        {dayStamp(m.updated_at, locale)}
                      </span>
                    </>
                  ) : (
                    text("已可用 · {count} 段原文", {
                      count: m.evidence_ids.length,
                    })
                  )}
                </small>
              </button>
            </li>
          ))}
        </ul>
        {library?.failed && (
          <div role="alert">
            <p>{text("记忆读取失败，请重试。")}</p>
            <R6Button onClick={library.onRetry}>
              {text("重新读取地图记忆")}
            </R6Button>
          </div>
        )}
        {library?.page &&
          !library.loading &&
          !library.failed &&
          !library.stale &&
          !filtered.length && (
            <p className="map-empty">
              {library.query
                ? text("没有匹配的记忆")
                : library.source
                  ? text("这个来源下还没有记忆")
                  : groupKey
                    ? text("本页没有匹配的主题记忆")
                    : text("还没有已生成的记忆")}
            </p>
          )}
        {library?.page?.search_window &&
          !library.loading &&
          !library.failed && (
            <p className="map-empty">
              {text("只在最相关的 {count} 条记忆里查找关键词", {
                count: count(library.page.search_window),
              })}
            </p>
          )}
        {library?.page &&
          (!library.loading || library.page.total > 0) &&
          !library.failed &&
          !library.stale && (
            <nav
              aria-label={text("地图记忆分页")}
              aria-busy={library.loading || undefined}
              className="map-pagination"
            >
              <button
                type="button"
                className="quiet map-tool"
                aria-label={text("上一页记忆")}
                title={text("上一页记忆")}
                disabled={library.loading || library.page.offset === 0}
                onClick={() => {
                  if (axis === "topic") setGroupKey("");
                  if (library.page)
                    library.onPage(library.page.offset - library.page.limit);
                }}
              >
                <ChevronLeft size={16} aria-hidden="true" />
              </button>
              <span>
                {text(
                  "共 {count} 条，第 {page} / {pages} 页",
                  {
                    count: count(library.page.total),
                    page:
                      library.page.total === 0
                        ? 0
                        : Math.floor(library.page.offset / library.page.limit) +
                          1,
                    pages: Math.ceil(library.page.total / library.page.limit),
                  },
                  library.page.total,
                )}
              </span>
              <button
                type="button"
                className="quiet map-tool"
                aria-label={text("下一页记忆")}
                title={text("下一页记忆")}
                disabled={
                  library.loading ||
                  library.page.offset + library.page.limit >= library.page.total
                }
                onClick={() => {
                  if (axis === "topic") setGroupKey("");
                  if (library.page)
                    library.onPage(library.page.offset + library.page.limit);
                }}
              >
                <ChevronRight size={16} aria-hidden="true" />
              </button>
            </nav>
          )}
        {!library && loaded && !filtered.length && (search || groupKey) && (
          <p className="map-empty">{text("没有匹配的记忆")}</p>
        )}
        {(!props.simplified || (!library && !active.length)) && (
          <MemoryArrivalPanel
            showDetails={false}
            arrival={arrival}
            hasMemories={active.length > 0}
            suspended={overlaysSuspended}
            onViewHistory={props.onViewHistory}
            onConfigureModel={props.onConfigureModel}
            onConnect={props.onConnect}
          />
        )}
      </nav>
      <section className="map-stage" aria-label={text("记忆关系图")}>
        <header className="map-stage-heading">
          <div>
            <span className="map-kicker" hidden={props.simplified}>
              {text(axis === "topic" ? "主题视角（非事项）" : "来源视角")}
            </span>
            <h2>{props.simplified ? text("记忆关联") : label}</h2>
            {props.simplified && (
              <p className="selected-graph-subtitle">
                {projection.nodes.length
                  ? text("来源归属")
                  : text("可在沉淀记忆后查看视图")}
              </p>
            )}
          </div>
          {compact && (activeId || selectedSource) && !inspectorOpen && (
            <button
              type="button"
              className="quiet map-tool"
              aria-label={text("记忆详情")}
              title={text("查看原文")}
              onClick={() => {
                setInspectorOpen(true);
                if (selectedSource && library)
                  setPendingSourceSelection(selectedSource.source_id);
              }}
            >
              <FileText size={17} />
            </button>
          )}
        </header>
        <div className="map-canvas" data-tour-id="memory-map-canvas">
          {projection.nodes.length && (!props.simplified || !hidden) ? (
            <MemoryGraph3D
              graph={projection}
              selectedNodeId={
                visibleGroupNode?.node_id ??
                (activeId ? `memory:${activeId}` : null)
              }
              onSelectNode={pickNode}
            />
          ) : (
            <div className="map-empty-stage">
              <Network size={32} strokeWidth={1} />
              <p
                {...(library?.graphFailed || library?.graphLoading
                  ? { role: "status", "aria-live": "polite" as const }
                  : {})}
              >
                {library?.stale
                  ? text("可在沉淀记忆后查看视图")
                  : library?.graphFailed || library?.failed
                    ? text("记忆暂时无法读取")
                    : library?.graphLoading
                      ? text("正在读取记忆地图…")
                      : library?.page &&
                          (library.query || library.source || groupKey)
                        ? text("当前筛选没有可显示的记忆")
                        : !loaded
                          ? text("正在读取记忆…")
                          : active.length
                            ? text("当前筛选没有可显示的记忆")
                            : arrival.importedMessages > 0
                              ? text("已保存的对话还未生成记忆")
                              : text("还没有可显示的记忆")}
              </p>
              {library?.graphFailed ? (
                <R6Button onClick={library.onRetry}>
                  {text("重新读取地图记忆")}
                </R6Button>
              ) : null}
            </div>
          )}
        </div>
        <footer className="map-meta">
          {/* The legend explains the graph. Keeping it while the stage shows
              something else left it dangling under a view it cannot describe. */}
          {!props.simplified && (
            <div className="map-legend">
              <span data-kind="memory">{text("记忆")}</span>
              <span data-kind="topic">{text("主题")}</span>
              <span data-kind="source">{text("来源")}</span>
              <span data-kind="schema">{text("归纳")}</span>
            </div>
          )}
          {props.simplified ? (
            projection.nodes.length ? (
              <span>{text("连线表示来源归属")}</span>
            ) : null
          ) : (
            <span>
              {text("{count} 条记忆 · 当前已加载", { count: filtered.length })}
            </span>
          )}
        </footer>
        {!props.simplified && (
          <form
            className="map-question"
            onSubmit={(e) => {
              e.preventDefault();
              if (!composing.current && question.trim() && !props.streaming) {
                onAsk(question.trim());
                setQuestion("");
              }
            }}
          >
            <input
              aria-label={text("向记忆提问")}
              name="map-question"
              autoComplete="off"
              placeholder={text("询问全部已授权可用记忆…")}
              value={question}
              onCompositionStart={() => {
                composing.current = true;
              }}
              onCompositionEnd={() => {
                composing.current = false;
              }}
              onKeyDown={(event) => {
                if (
                  event.key === "Enter" &&
                  (event.nativeEvent.isComposing || composing.current)
                )
                  event.preventDefault();
              }}
              onChange={(e) => setQuestion(e.target.value)}
            />
            <button
              type="submit"
              title={text("发送问题")}
              aria-label={text("发送问题")}
              disabled={!question.trim() || !!props.streaming}
            >
              <ArrowUp size={17} />
            </button>
          </form>
        )}
        <div hidden={overlaysSuspended}>
          {!props.simplified && (props.answer || props.streaming) && (
            <Answer
              answer={props.answer}
              streaming={props.streaming}
              onOpenMemory={pick}
              onSaveAnswer={props.onSaveAnswer}
              asPage={false}
              suspended={overlaysSuspended}
            />
          )}
          {!props.simplified && (
            <HandoffBar
              suspended={overlaysSuspended}
              memories={filtered.filter(
                (memory) => memory.memory_id === activeId,
              )}
              matterLabel={label}
            />
          )}
        </div>
      </section>
      {/* Keep inspector drafts mounted; suspension releases its modal effects. */}
      <MemoryEvidencePanel
        open={inspectorOpen && !!(activeId || visibleGroupNode)}
        suspended={overlaysSuspended}
        scrollResetKey={activeId ?? visibleGroupNode?.node_id ?? "empty"}
        onClose={closeInspector}
      >
        <header className="map-inspector-heading">
          <h2>
            {inspectorDetail
              ? text("记忆详情")
              : selectedSource
                ? text("记忆详情")
                : visibleGroupNode
                  ? text("关联节点")
                  : text("记忆详情")}
          </h2>
          {(activeId || visibleGroupNode) && (
            <button
              type="button"
              className="quiet map-tool"
              aria-label={text("关闭记忆详情")}
              title={text("关闭详情")}
              onClick={closeInspector}
            >
              <X size={17} />
            </button>
          )}
        </header>
        <div className="map-inspector-body" ref={inspectorBody}>
          {selectedSource ? (
            <div className="map-inspector-empty map-inspector-loading">
              {library?.failed &&
              library.source === selectedSource.source_id ? (
                <>
                  <p role="alert">{text("记忆暂时无法读取")}</p>
                  <button
                    type="button"
                    className="secondary"
                    onClick={library.onRetry}
                  >
                    {text("重试")}
                  </button>
                </>
              ) : pendingSourceSelection || library?.loading ? (
                <>
                  <div className="map-detail-skeleton" aria-hidden="true">
                    <i />
                    <i />
                    <i />
                    <i />
                  </div>
                  <p role="status">{text("正在读取记忆详情…")}</p>
                </>
              ) : (
                <p>{text("还没有已生成的记忆")}</p>
              )}
            </div>
          ) : props.simplified && inspectorDetail ? (
            <div
              aria-busy={
                refreshing ||
                inspectorDetail.memory.memory_id !== activeId ||
                undefined
              }
              onClickCapture={(event) => {
                // React portal events follow this owner too, including editor saves.
                if (
                  refreshing &&
                  (event.target as Element).closest("button, summary")
                ) {
                  event.preventDefault();
                  event.stopPropagation();
                }
              }}
              onSubmitCapture={(event) => {
                if (refreshing) {
                  event.preventDefault();
                  event.stopPropagation();
                }
              }}
            >
              {/* The pane used to keep the previously read memory on screen
                until the new one arrived, which read as "nothing happened"
                for two or three seconds. A read is not a value: show that a
                read is happening instead. */}
              {activeId && inspectorDetail.memory.memory_id !== activeId ? (
                <div className="map-detail-loading">
                  <div className="map-detail-skeleton" aria-hidden="true">
                    <i />
                    <i />
                    <i />
                    <i />
                  </div>
                  <p role="status">{text("正在读取这条记忆…")}</p>
                </div>
              ) : (
                <SelectedMemoryDetail
                  detail={inspectorDetail}
                  sources={sources}
                  conversationAt={
                    library?.page?.source_time_by_memory?.[
                      inspectorDetail.memory.memory_id
                    ] ?? null
                  }
                />
              )}
            </div>
          ) : inspectorDetail || visibleGroupNode ? (
            <NodeDetail
              key={
                inspectorDetail?.memory.memory_id ||
                visibleGroupNode?.node_id ||
                "empty"
              }
              compact
              node={visibleGroupNode}
              graph={projection}
              detail={inspectorDetail}
              draft={props.draft}
              busy={busy}
              matterLabel={label}
              onDraft={props.onDraft}
              onSave={props.onSave}
              onDelete={props.onDelete}
              requestIds={props.requestIds}
              onClose={closeInspector}
            />
          ) : (
            <div
              className={`map-inspector-empty${activeId ? " map-inspector-loading" : ""}`}
            >
              {activeId ? (
                <div className="map-detail-skeleton" aria-hidden="true">
                  <i />
                  <i />
                  <i />
                  <i />
                </div>
              ) : (
                <FileText size={28} strokeWidth={1} />
              )}
              {props.detailLoad?.memoryId === activeId &&
              props.detailLoad.status === "failed" ? (
                <>
                  <p role="alert">
                    {text("这条记忆的原文暂时无法读取，选择已保留。")}
                  </p>
                  <button
                    type="button"
                    className="secondary"
                    onClick={() => activeId && onOpenMemory(activeId)}
                  >
                    {text("重试读取原文")}
                  </button>
                </>
              ) : props.simplified && !activeId && filtered.length ? (
                // Nothing is selected, but this conversation has already
                // settled: naming what came out of it beats a placeholder that
                // asks for the settle that already happened. Only the
                // single-column surface needs it — the three-column one already
                // lists the same memories in the pane beside this one.
                <>
                  <p>
                    {text(
                      "已沉淀 {count} 条记忆",
                      { count: plain(filtered.length) },
                      filtered.length,
                    )}
                  </p>
                  <ul
                    className="map-settled-list"
                    aria-label={text("已沉淀的记忆")}
                  >
                    {filtered.map((m) => (
                      <li key={m.memory_id}>{m.content}</li>
                    ))}
                  </ul>
                </>
              ) : (
                <p>
                  {text(activeId ? "正在读取记忆详情…" : "沉淀记忆后查看详情")}
                </p>
              )}
            </div>
          )}
        </div>
      </MemoryEvidencePanel>
    </section>
  );
}
