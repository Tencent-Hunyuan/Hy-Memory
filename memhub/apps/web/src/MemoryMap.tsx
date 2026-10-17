import type {
  ConversationSearchHit,
  ConversationTranscript,
  MemoryGraphNode,
  MemoryLibraryPage,
  RecentConversationItem,
} from "@one-memory/contracts";
import {
  ArrowUp,
  ChevronLeft,
  ChevronRight,
  FileText,
  Network,
  RefreshCw,
  Search,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  getConversation,
  getRecentConversations,
  searchConversations,
} from "./api";
import { HandoffBar } from "./HandoffBar";
import { MemoryEvidencePanel } from "./MemoryEvidencePanel";
import { MemoryGraph3D } from "./MemoryGraph3D";
import { MemoryArrivalPanel } from "./memory-arrival";
import {
  type MemoryArrival,
  resolveMemoryArrival,
} from "./memory-arrival-state";
import {
  type MapAxis,
  mapGroups,
  projectMap,
  projectPagedSourceMap,
} from "./memory-map-model";
import { extractionStateLabel } from "./memory-processing-labels";
import { OptionMenu } from "./OptionMenu";
import { subscribePoll } from "./poll-scheduler";
import { R6Button } from "./R6Controls";
import { SelectedMemoryDetail } from "./SelectedMemoryDetail";
import { SourceImportReceipt } from "./SourceImportReceipt";
import { sourceNameOr } from "./source-label";
import { useMediaPreference } from "./use-media-preference";
import { Answer, NodeDetail, type Workbench } from "./Workbench";

type Props = Parameters<typeof Workbench>[0] & {
  simplified?: boolean;
  hidden?: boolean;
  id?: string;
  arrival?: MemoryArrival;
  onViewHistory?(): void;
  onMutationComplete?(id: string): void;
  focusDialogue?: boolean;
  onFocusDialogueConsumed?(): void;
  libraryView?: {
    query: string;
    source: string;
    page: MemoryLibraryPage | null;
    loading: boolean;
    failed: boolean;
    stale: boolean;
    onQuery(value: string): void;
    onSource(value: string): void;
    resetPage(): void;
    onPage(offset: number): void;
    onRetry(): void;
  };
};

/** A search hit republished as a dialogue row.
 *
 * Both panes list the same shape, so a hit has to arrive carrying what a
 * recent conversation carries, including who wrote it: the author used to be
 * filled in here as the user because the endpoint never sent one, which made
 * every hit look like something the person themselves had said.
 */
export function hitToDialogueItem(
  item: ConversationSearchHit,
): RecentConversationItem {
  return {
    ...item,
    role: item.role ?? "",
    written_by: item.written_by ?? "",
    imported_at: item.created_at ?? "",
    time_basis: item.created_at ? "conversation" : "import",
    truncated: false,
  };
}

function authorLabel(writtenBy: string) {
  if (writtenBy === "person") return "你";
  if (writtenBy === "agent") return "助手";
  if (writtenBy === "tool") return "工具";
  return "未知";
}

function dayStamp(value: string | null | undefined) {
  if (!value || !Number.isFinite(Date.parse(value))) return "时间未知";
  const date = new Date(value);
  const now = new Date();
  if (
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate()
  )
    return "今天";
  return date.toLocaleDateString("zh-CN");
}

/**
 * D96: the memory page no longer offers the raw-dialogue tab. The code stays
 * (the full mode's conversation search and a memory's own "原始对话 N 段"
 * still read dialogues); flipping this brings the tab back.
 */
export const DIALOGUE_PANE_VISIBLE = false;

/** "10月2日", with the year only when it is not this year. */
function monthDay(value: string): string | null {
  if (!Number.isFinite(Date.parse(value))) return null;
  const date = new Date(value);
  const day = `${date.getMonth() + 1}月${date.getDate()}日`;
  return date.getFullYear() === new Date().getFullYear()
    ? day
    : `${date.getFullYear()}年${day}`;
}

/** D96: when the cited conversation happened, not when the memory was generated. */
function sourceStamp(
  time: { at: string; basis: "conversation" | "import" } | undefined,
  fallback: string | null | undefined,
): string {
  const day = time ? monthDay(time.at) : null;
  if (time && day)
    return time.basis === "conversation" ? `对话于 ${day}` : `导入于 ${day}`;
  return dayStamp(fallback);
}

export function MemoryMap(props: Props) {
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
  const graph = useMemo(
    () =>
      serverPaged
        ? projectPagedSourceMap(baseGraph, library?.page ?? null, sources)
        : baseGraph,
    [baseGraph, serverPaged, library?.page, sources],
  );
  const refreshing = !!library?.page && library.loading;
  const overlaysSuspended = hidden || !!props.overlayBlocked;
  const [axis, setAxis] = useState<MapAxis>("source");
  const [groupKey, setGroupKey] = useState("");
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [groupNode, setGroupNode] = useState<MemoryGraphNode | null>(null);
  const [question, setQuestion] = useState("");
  const [evidenceId, setEvidenceId] = useState<string | null>(null);
  const [transcript, setTranscript] = useState<ConversationTranscript | null>(
    null,
  );
  const [transcriptError, setTranscriptError] = useState("");
  const [transcriptLoading, setTranscriptLoading] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [filterNotice, setFilterNotice] = useState("");
  const [arrivedIds, setArrivedIds] = useState<string[]>([]);
  const [libraryPane, setLibraryPane] = useState<"memories" | "dialogues">(
    "memories",
  );
  const [dialoguePage, setDialoguePage] = useState<
    RecentConversationItem[] | null
  >(null);
  const [dialogueFailed, setDialogueFailed] = useState(false);
  const [selectedDialogueId, setSelectedDialogueId] = useState<string | null>(
    null,
  );
  const [dialogueFilter, setDialogueFilter] = useState("");
  const [dialogueTranscript, setDialogueTranscript] =
    useState<ConversationTranscript | null>(null);
  const [dialogueTranscriptError, setDialogueTranscriptError] = useState("");
  const consumeDialogueFocus = useRef(props.onFocusDialogueConsumed);
  consumeDialogueFocus.current = props.onFocusDialogueConsumed;
  const seenMemoryIds = useRef<Set<string> | null>(null);
  const compact = useMediaPreference("(max-width: 1100px)");
  const composing = useRef(false);
  const generation = useRef(0);
  const displayedVersion = useRef("");
  const pane = useRef<HTMLElement>(null);
  const memoryPaneTabs = useRef<Record<string, HTMLButtonElement | null>>({});
  const openedDefault = useRef(false);
  const scrollSelection = useRef(false);
  useEffect(() => {
    if (!props.focusDialogue) return;
    if (DIALOGUE_PANE_VISIBLE) setLibraryPane("dialogues");
    consumeDialogueFocus.current?.();
  }, [props.focusDialogue]);
  useEffect(() => {
    if (libraryPane !== "dialogues" || hidden) return;
    let stopped = false;
    let busy = false;
    const sourceId = library?.source || undefined;
    async function load() {
      if (busy || document.hidden) return;
      busy = true;
      try {
        const query = dialogueFilter.trim();
        const page = query
          ? await searchConversations(query, 100)
          : await getRecentConversations({ limit: 30, sourceId });
        if (stopped) return;
        const items = query
          ? "hits" in page
            ? page.hits
                .filter((item) => !sourceId || item.source_id === sourceId)
                .map(hitToDialogueItem)
            : []
          : "items" in page
            ? page.items
            : [];
        setDialoguePage(items);
        setDialogueFailed(false);
        setSelectedDialogueId((current) =>
          current && items.some((item) => item.evidence_id === current)
            ? current
            : (items[0]?.evidence_id ?? null),
        );
      } catch {
        if (!stopped) setDialogueFailed(true);
      } finally {
        busy = false;
      }
    }
    void load();
    const stop = subscribePoll({
      // This surface is the dialogue list (`getRecentConversations`), not the
      // governed-memory library that `memory-library` keys elsewhere.
      key: "conversations",
      intervalMs: 10_000,
      run: load,
    });
    return () => {
      stopped = true;
      stop();
    };
  }, [libraryPane, hidden, library?.source, dialogueFilter]);
  useEffect(() => {
    if (libraryPane !== "dialogues" || !selectedDialogueId || hidden) return;
    let stopped = false;
    setDialogueTranscript(null);
    setDialogueTranscriptError("");
    void getConversation(selectedDialogueId).then(
      (value) => {
        if (!stopped) setDialogueTranscript(value);
      },
      () => {
        if (!stopped) setDialogueTranscriptError("原文暂时无法读取");
      },
    );
    return () => {
      stopped = true;
    };
  }, [libraryPane, selectedDialogueId, hidden]);
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
        ? ids.map((id) => sourceNameOr(sources, id, "来源待核对")).join("、")
        : "来源待核对";
    }
    return (
      sourceGroups
        .filter((entry) => entry.memoryIds.includes(memoryId))
        .map((entry) => entry.label)
        .join("、") || "来源待核对"
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
  }, [graph, axis, filtered, active, groupKey, search, sources, serverPaged]);
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
      setInspectorOpen(false);
    }
  }, [groupNode, visibleGroupNode]);
  const requestedId = selectedId ?? detail?.memory.memory_id ?? null;
  const activeId = active.some((memory) => memory.memory_id === requestedId)
    ? requestedId
    : null;
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
  const version = visibleDetail
    ? `${visibleDetail.memory.memory_id}:${visibleDetail.memory.revision}:${visibleDetail.memory.status}:${visibleDetail.evidence.map((item) => `${item.evidence_id}:${item.available}`).join(",")}`
    : "";
  const pick = useCallback(
    (memoryId: string, scroll = true) => {
      if (!active.some((memory) => memory.memory_id === memoryId)) return;
      scrollSelection.current = scroll;
      if (!filtered.some((memory) => memory.memory_id === memoryId)) {
        setGroupKey("");
        setSearch("");
        setFilterNotice("已清除筛选，定位到关联记忆。");
      }
      generation.current++;
      setGroupNode(null);
      setSelectedId(memoryId);
      setTranscript(null);
      setEvidenceId(null);
      setTranscriptError("");
      setTranscriptLoading(false);
      setInspectorOpen(true);
      onOpenMemory(memoryId);
    },
    [active, filtered, onOpenMemory],
  );
  useEffect(() => {
    if (displayedVersion.current === version) return;
    displayedVersion.current = version;
    generation.current++;
    setTranscript(null);
    setEvidenceId(null);
    setTranscriptError("");
    setTranscriptLoading(false);
  }, [version]);
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

  const clear = useCallback(() => {
    generation.current++;
    setSelectedId(null);
    setGroupNode(null);
    setTranscript(null);
    setEvidenceId(null);
    setTranscriptError("");
    setTranscriptLoading(false);
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
    generation.current++;
    setTranscript(null);
    setTranscriptError("");
    setTranscriptLoading(false);
    setInspectorOpen(false);
  }
  function chooseGroup(key: string) {
    setGroupNode(null);
    if (library) {
      clear();
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
        library.onSource(node.source_id);
        library.resetPage();
      }
    }
    if (node.node_type === "topic") {
      if (props.simplified) return;
      setAxis("topic");
      setGroupKey(`topic:${node.label}`);
    }
  }
  async function openConversation(id: string) {
    if (transcriptLoading) return;
    const turn = ++generation.current;
    setTranscriptError("");
    setTranscript(null);
    setTranscriptLoading(true);
    try {
      const value = await getConversation(id);
      if (turn === generation.current) setTranscript(value);
    } catch {
      if (turn === generation.current)
        setTranscriptError("暂时无法读取这段对话，已保留当前记忆。");
    } finally {
      if (turn === generation.current) setTranscriptLoading(false);
    }
  }
  const evidence = [...(visibleDetail?.evidence ?? [])].sort(
    (a, b) =>
      (Date.parse(b.created_at ?? "") || 0) -
      (Date.parse(a.created_at ?? "") || 0),
  );
  const label =
    group?.label ??
    sources.find(
      (source) =>
        source.authorized && groupKey === `source:${source.source_id}`,
    )?.display_name ??
    "全部记忆";
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
              ? "已开始处理所选对话"
              : queued
                ? "处理请求已受理"
                : "还没有已生成的记忆"}
          </h2>
          <p>
            {running || queued
              ? "模型连接已通过。首批记忆生成后，会逐步出现在记忆列表里。"
              : arrival.importedConversations > 0
                ? `已导入 ${arrival.importedConversations.toLocaleString()} 段对话，整理成记忆后会出现在这里。`
                : arrival.importedMessages > 0
                  ? "历史已保存在本机，记忆就绪后会出现在这里。"
                  : "连接应用并导入历史后，可以开始沉淀记忆。"}
            {(running || queued) && (
              <small>
                当前已生成 0
                条记忆。请求已受理不等于已经完成，原始对话、模型处理任务和记忆数量分别统计。
              </small>
            )}
          </p>
          <div>
            <R6Button onClick={props.onConnect}>返回应用</R6Button>
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
      aria-label="记忆工作台"
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
      <nav className="map-library" aria-label="来源与主题" ref={pane}>
        {props.simplified && DIALOGUE_PANE_VISIBLE ? (
          <div
            className="map-library-pane"
            role="tablist"
            aria-label="记忆或原始对话"
            onKeyDown={(event) => {
              const order = ["memories", "dialogues"] as const;
              const current = order.indexOf(libraryPane);
              const target =
                event.key === "Home"
                  ? order[0]
                  : event.key === "End"
                    ? order[order.length - 1]
                    : event.key === "ArrowRight"
                      ? order[(current + 1) % order.length]
                      : event.key === "ArrowLeft"
                        ? order[(current - 1 + order.length) % order.length]
                        : null;
              if (!target) return;
              event.preventDefault();
              setLibraryPane(target);
              memoryPaneTabs.current[target]?.focus();
            }}
          >
            {(
              [
                ["memories", "记忆"],
                ["dialogues", "原始对话"],
              ] as const
            ).map(([value, title]) => (
              <button
                type="button"
                role="tab"
                key={value}
                ref={(node) => {
                  memoryPaneTabs.current[value] = node;
                }}
                aria-selected={libraryPane === value}
                aria-controls={`memory-pane-${value}`}
                onClick={() => setLibraryPane(value)}
              >
                {title}
              </button>
            ))}
          </div>
        ) : null}
        {props.simplified ? (
          <div className="selected-source-filter">
            <label htmlFor="selected-source-filter">来源</label>
            <OptionMenu
              id="selected-source-filter"
              className="om-option-menu--plain"
              value={controlledSource}
              options={[
                { value: "", label: "全部应用" },
                ...(library?.source &&
                !sources.some(
                  (source) =>
                    source.source_id === library.source && source.authorized,
                )
                  ? [
                      {
                        value: `source:${library.source}`,
                        label: "所选来源待核对",
                      },
                    ]
                  : []),
                ...(library
                  ? sources
                      .filter((source) => source.authorized)
                      .map((source) => ({
                        value: `source:${source.source_id}`,
                        label: source.display_name,
                      }))
                  : groups.map((group) => ({
                      value: group.key,
                      label: group.label,
                    }))),
              ]}
              onChange={chooseGroup}
            />
          </div>
        ) : (
          <fieldset className="map-axis" aria-label="地图组织方式">
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
                {title}
              </button>
            ))}
          </fieldset>
        )}
        <div className="map-search">
          <Search size={16} aria-hidden="true" />
          <input
            aria-label="筛选地图记忆"
            name="map-memory-search"
            autoComplete="off"
            placeholder={
              libraryPane === "dialogues"
                ? "搜索原始对话"
                : props.simplified
                  ? "搜索记忆"
                  : "筛选记忆…"
            }
            value={
              libraryPane === "dialogues"
                ? dialogueFilter
                : (library?.query ?? search)
            }
            maxLength={library ? 512 : undefined}
            onKeyDown={(e) => {
              // The refresh button next to this list re-reads the page;
              // Enter has to do the same thing, not sit there doing nothing.
              if (e.key !== "Enter" || e.nativeEvent.isComposing) return;
              e.preventDefault();
              library?.onRetry();
            }}
            onChange={(e) => {
              if (libraryPane === "dialogues") {
                setDialogueFilter(e.target.value);
                return;
              }
              setFilterNotice("");
              setSearch(e.target.value);
              if (library) {
                clear();
                library.onQuery(e.target.value);
                library.resetPage();
              }
            }}
          />
        </div>
        {library && libraryPane === "memories" && (
          <div className="map-list-heading" aria-live="polite">
            <span>
              {library.stale
                ? library.page && library.page.total > 0
                  ? `共 ${library.page.total} 条 · 正在核对来源…`
                  : "正在核对来源…"
                : library.failed
                  ? "读取失败"
                  : library.loading && library.page?.total === 0
                    ? "正在读取…"
                    : library.page
                      ? `共 ${library.page.total} 条${refreshing ? " · 正在核对…" : ""}`
                      : "正在读取…"}
            </span>
            {!library.stale && (
              <button
                type="button"
                className="quiet map-tool"
                aria-label="刷新地图记忆"
                title="刷新地图记忆"
                disabled={library.loading || hidden}
                onClick={library.onRetry}
              >
                <RefreshCw size={16} aria-hidden="true" />
              </button>
            )}
          </div>
        )}
        {!props.simplified && (
          <div className="map-groups">
            <button
              type="button"
              className={!groupKey ? "selected" : ""}
              aria-pressed={!groupKey}
              onClick={() => chooseGroup("")}
            >
              全部记忆 <span>{active.length}</span>
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
            <span>{filtered.length} 条</span>
          </div>
        )}
        {arrivalCount > 0 && (
          <div className="map-arrival-notice">
            <span role="status">新增 {arrivalCount} 条记忆</span>
            <button
              type="button"
              title="关闭新增记忆提示"
              aria-label="关闭新增记忆提示"
              onClick={() => setArrivedIds([])}
            >
              <X size={15} aria-hidden="true" />
            </button>
          </div>
        )}
        {filterNotice && (
          <p className="map-filter-notice" role="status">
            {filterNotice}
          </p>
        )}
        {libraryPane === "dialogues" ? (
          <>
            <div className="map-list-heading" aria-live="polite">
              <span>
                {dialogueFailed
                  ? "读取失败"
                  : dialoguePage
                    ? `最近 ${dialoguePage.length} 条`
                    : "正在读取…"}
              </span>
            </div>
            <ul
              className="map-memory-list"
              id="memory-pane-dialogues"
              aria-label="原始对话"
            >
              {(dialoguePage ?? []).map((item) => (
                <li key={item.evidence_id}>
                  <button
                    type="button"
                    className="map-memory-item"
                    aria-pressed={selectedDialogueId === item.evidence_id}
                    onClick={() => setSelectedDialogueId(item.evidence_id)}
                  >
                    <span>
                      {item.excerpt}
                      {item.truncated ? "…" : ""}
                    </span>
                    <small>
                      {sourceNameOr(sources, item.source_id, "来源待核对")} ·{" "}
                      {dayStamp(item.imported_at)} ·{" "}
                      {authorLabel(item.written_by)}
                    </small>
                  </button>
                </li>
              ))}
            </ul>
            {dialogueFailed ? <p role="alert">原始对话暂时无法读取</p> : null}
            {dialoguePage && !dialoguePage.length ? (
              <p className="map-empty">
                {dialogueFilter.trim()
                  ? "没有匹配的原始对话"
                  : "还没有导入的原始对话"}
              </p>
            ) : null}
          </>
        ) : null}
        <ul
          className="map-memory-list"
          aria-label="地图记忆列表"
          id="memory-pane-memories"
          hidden={libraryPane === "dialogues"}
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
                <span>{m.content}</span>
                <small>
                  {props.simplified ? (
                    <>
                      {`${sourceLabel(m.memory_id)} · ${sourceStamp(
                        library?.page?.source_time_by_memory?.[m.memory_id],
                        m.updated_at,
                      )}`}
                      {/* D99: the conversation date alone would hide that this
                          one was made today, so the generated day stays too. */}
                      <span className="map-memory-made">
                        沉淀于 {dayStamp(m.updated_at)}
                      </span>
                    </>
                  ) : (
                    <>已可用 · {m.evidence_ids.length} 段原文</>
                  )}
                </small>
              </button>
            </li>
          ))}
        </ul>
        {libraryPane === "memories" && library?.failed && (
          <div role="alert">
            <p>记忆读取失败，请重试。</p>
            <R6Button onClick={library.onRetry}>重新读取地图记忆</R6Button>
          </div>
        )}
        {libraryPane === "memories" &&
          library?.page &&
          !library.loading &&
          !library.failed &&
          !library.stale &&
          !filtered.length && (
            <p className="map-empty">
              {library.query || library.source
                ? "没有匹配的记忆"
                : groupKey
                  ? "本页没有匹配的主题记忆"
                  : "还没有已生成的记忆"}
            </p>
          )}
        {libraryPane === "memories" &&
          library?.page &&
          !library.loading &&
          !library.failed &&
          !library.stale && (
            <nav aria-label="地图记忆分页" className="map-list-heading">
              <button
                type="button"
                className="quiet map-tool"
                aria-label="上一页记忆"
                title="上一页记忆"
                disabled={library.page.offset === 0}
                onClick={() => {
                  clear();
                  if (axis === "topic") setGroupKey("");
                  if (library.page)
                    library.onPage(library.page.offset - library.page.limit);
                }}
              >
                <ChevronLeft size={16} aria-hidden="true" />
              </button>
              <span>
                共 {library.page.total.toLocaleString()} 条，第{" "}
                {library.page.total === 0
                  ? 0
                  : Math.floor(library.page.offset / library.page.limit) +
                    1}{" "}
                / {Math.ceil(library.page.total / library.page.limit)} 页
              </span>
              <button
                type="button"
                className="quiet map-tool"
                aria-label="下一页记忆"
                title="下一页记忆"
                disabled={
                  library.page.offset + library.page.items.length >=
                  library.page.total
                }
                onClick={() => {
                  clear();
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
          <p className="map-empty">没有匹配的记忆</p>
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
      <section className="map-stage" aria-label="记忆关系图">
        <header className="map-stage-heading">
          <div>
            <span className="map-kicker" hidden={props.simplified}>
              {axis === "topic" ? "主题视角（非事项）" : "来源视角"}
            </span>
            <h2>
              {libraryPane === "dialogues"
                ? "原始对话"
                : props.simplified
                  ? "记忆关联"
                  : label}
            </h2>
            {props.simplified && (
              <p className="selected-graph-subtitle">
                {libraryPane === "dialogues"
                  ? "刚导入的原话，还不是沉淀后的记忆"
                  : projection.nodes.length
                    ? "来源归属"
                    : "可在沉淀记忆后查看视图"}
              </p>
            )}
          </div>
          {compact && (activeId || selectedSource) && !inspectorOpen && (
            <button
              type="button"
              className="quiet map-tool"
              aria-label={
                selectedSource ? "查看选中来源原文" : "查看选中记忆原文"
              }
              title="查看原文"
              onClick={() => setInspectorOpen(true)}
            >
              <FileText size={17} />
            </button>
          )}
          {!props.simplified && (
            <button
              type="button"
              className="quiet map-tool"
              title="整理已有关系"
              aria-label="整理已有关系"
              disabled={busy || !graph?.nodes.length}
              onClick={props.onBuildRelations}
            >
              <Network size={17} />
            </button>
          )}
        </header>
        <div className="map-canvas" data-tour-id="memory-map-canvas">
          {libraryPane === "dialogues" ? (
            <div className="map-dialogue-reading">
              {dialogueTranscriptError ? (
                <p role="alert">{dialogueTranscriptError}</p>
              ) : dialogueTranscript ? (
                <ol className="map-transcript">
                  {dialogueTranscript.messages.map((message) => (
                    <li key={message.evidence_id}>
                      <small>{authorLabel(message.written_by)}</small>
                      <p>{message.verbatim}</p>
                    </li>
                  ))}
                </ol>
              ) : selectedDialogueId ? (
                <p>正在读取原文…</p>
              ) : (
                <p>选择一条原始对话</p>
              )}
            </div>
          ) : projection.nodes.length && (!props.simplified || !hidden) ? (
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
              <p>
                {library?.stale
                  ? "可在沉淀记忆后查看视图"
                  : library?.failed
                    ? "记忆暂时无法读取"
                    : library?.page &&
                        (library.query || library.source || groupKey)
                      ? "当前筛选没有可显示的记忆"
                      : !loaded
                        ? "正在读取记忆…"
                        : active.length
                          ? "当前筛选没有可显示的记忆"
                          : arrival.importedMessages > 0
                            ? "已保存的对话还未生成记忆"
                            : "还没有可显示的记忆"}
              </p>
            </div>
          )}
        </div>
        <footer className="map-meta">
          {/* The legend explains the graph. Keeping it while the stage shows
              something else left it dangling under a view it cannot describe. */}
          {!props.simplified && libraryPane === "memories" && (
            <div className="map-legend">
              <span data-kind="memory">记忆</span>
              <span data-kind="topic">主题</span>
              <span data-kind="source">来源</span>
              <span data-kind="schema">归纳</span>
            </div>
          )}
          {props.simplified ? (
            projection.nodes.length ? (
              <span>连线表示来源归属</span>
            ) : null
          ) : (
            <span>{filtered.length} 条记忆 · 当前已加载</span>
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
              aria-label="向记忆提问"
              name="map-question"
              autoComplete="off"
              placeholder="询问全部已授权可用记忆…"
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
              title="发送问题"
              aria-label="发送问题"
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
        onClose={closeInspector}
      >
        <header className="map-inspector-heading">
          <h2>
            {inspectorDetail
              ? "记忆详情"
              : selectedSource
                ? "来源原文"
                : visibleGroupNode
                  ? "关联节点"
                  : "记忆详情"}
          </h2>
          {(activeId || visibleGroupNode) && (
            <button
              type="button"
              className="quiet map-tool"
              aria-label="关闭记忆详情"
              title="关闭详情"
              onClick={closeInspector}
            >
              <X size={17} />
            </button>
          )}
        </header>
        {selectedSource ? (
          inspectorOpen &&
          !overlaysSuspended && (
            <SourceImportReceipt
              key={selectedSource.source_id}
              source={selectedSource}
            />
          )
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
                <p role="status">正在读取这条记忆…</p>
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
                suspended={overlaysSuspended}
                onSaveScopes={props.onSaveScopes}
                onChanged={(id) => props.onMutationComplete?.(id)}
                hideActions
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
            scopes={props.scopes}
            busy={busy}
            matterLabel={label}
            onDraft={props.onDraft}
            onScopes={props.onScopes}
            onSave={props.onSave}
            onSaveScopes={props.onSaveScopes}
            onAction={props.onAction}
            onSyncIndex={props.onSyncIndex}
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
                <p role="alert">这条记忆的原文暂时无法读取，选择已保留。</p>
                <button
                  type="button"
                  className="secondary"
                  onClick={() => activeId && onOpenMemory(activeId)}
                >
                  重试读取原文
                </button>
              </>
            ) : props.simplified && !activeId && filtered.length ? (
              // Nothing is selected, but this conversation has already
              // settled: naming what came out of it beats a placeholder that
              // asks for the settle that already happened. Only the
              // single-column surface needs it — the three-column one already
              // lists the same memories in the pane beside this one.
              <>
                <p>{`已沉淀 ${filtered.length} 条记忆`}</p>
                <ul className="map-settled-list" aria-label="已沉淀的记忆">
                  {filtered.map((m) => (
                    <li key={m.memory_id}>{m.content}</li>
                  ))}
                </ul>
              </>
            ) : (
              <p>{activeId ? "正在读取记忆详情…" : "沉淀记忆后查看详情"}</p>
            )}
          </div>
        )}
        {!props.simplified && evidence.length > 0 && (
          <h3 className="map-section-title">
            原始对话 <span>{evidence.length}</span>
          </h3>
        )}
        {!props.simplified &&
          evidence.map((item) => (
            <article
              key={item.evidence_id}
              className="map-evidence"
              data-evidence-id={item.evidence_id}
            >
              <div className="map-evidence-meta">
                <strong>
                  {sourceNameOr(sources, item.source_id, "未知来源")}
                </strong>
                <span>{author(item.written_by)}</span>
              </div>
              <time>
                {item.created_at && Number.isFinite(Date.parse(item.created_at))
                  ? new Date(item.created_at).toLocaleString("zh-CN", {
                      year: "numeric",
                      month: "2-digit",
                      day: "2-digit",
                      hour: "2-digit",
                      minute: "2-digit",
                    })
                  : "时间未知"}
              </time>
              <button
                type="button"
                className="map-quote"
                disabled={!item.available}
                aria-expanded={evidenceId === item.evidence_id}
                aria-controls={`evidence-body-${item.evidence_id}`}
                onClick={() => {
                  generation.current++;
                  setTranscript(null);
                  setTranscriptError("");
                  setTranscriptLoading(false);
                  setEvidenceId(
                    evidenceId === item.evidence_id ? null : item.evidence_id,
                  );
                }}
              >
                {item.available ? item.verbatim : "来源原文已不可读取"}
              </button>
              {item.extraction_state &&
                item.extraction_state !== "extracted" && (
                  <small>{extractionStateLabel(item.extraction_state)}</small>
                )}
              {item.available && evidenceId === item.evidence_id && (
                <div
                  className="map-evidence-expanded"
                  id={`evidence-body-${item.evidence_id}`}
                >
                  <p className="map-section-title">关联记忆</p>
                  {active
                    .filter((m) => m.evidence_ids.includes(item.evidence_id))
                    .map((m) => (
                      <button
                        className="quiet map-related"
                        type="button"
                        key={m.memory_id}
                        aria-label={`定位关联记忆：${m.content}`}
                        onClick={() => pick(m.memory_id)}
                      >
                        {m.content}
                        <ChevronRight size={14} />
                      </button>
                    ))}
                  <button
                    type="button"
                    className="quiet"
                    disabled={transcriptLoading}
                    onClick={() => void openConversation(item.evidence_id)}
                  >
                    {transcriptLoading
                      ? "正在读取完整对话…"
                      : transcriptError
                        ? "重试读取完整对话"
                        : "看完整对话"}
                  </button>
                  {transcriptError && <p role="alert">{transcriptError}</p>}
                  {transcript && (
                    <ol className="map-transcript">
                      {transcript.messages.map((m) => (
                        <li key={m.evidence_id}>
                          <small>{author(m.written_by)}</small>
                          <p>{m.verbatim}</p>
                        </li>
                      ))}
                    </ol>
                  )}
                </div>
              )}
            </article>
          ))}
        {!props.simplified && !!visibleDetail?.history.length && (
          <details className="map-history">
            <summary>修改历史 · {visibleDetail.history.length} 个版本</summary>
            {visibleDetail.history.map((h) => (
              <article key={h.revision}>
                <small>第 {h.revision} 版</small>
                <p>{h.content}</p>
              </article>
            ))}
          </details>
        )}
      </MemoryEvidencePanel>
    </section>
  );
}

function author(who: string | null) {
  return who === "person" || who === "user"
    ? "你"
    : who === "agent" || who === "assistant"
      ? "Agent"
      : who === "tool"
        ? "工具"
        : "作者待核对";
}
