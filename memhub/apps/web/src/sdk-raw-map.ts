import type {
  GovernedMemory,
  MemoryDetail,
  MemoryGraphResponse,
  MemoryLibraryPage,
  SdkRawContextItem,
  SdkRawGraphItem,
  SdkRawPage,
  SdkRawRecord,
  SourceResponse,
} from "@one-memory/contracts";

export type RawMapRecord = SdkRawRecord | SdkRawContextItem;
export const SDK_RAW_PAGE_SIZE = 20;

export function rawRecordId(record: RawMapRecord): string {
  return record.turn_id || record.raw_id;
}

const EARLIEST_PLAUSIBLE = Date.UTC(2000, 0, 1);
const LATEST_PLAUSIBLE = () => Date.now() + 86_400_000;

function isoTime(value: number | string | null | undefined): string {
  const milliseconds =
    typeof value === "number"
      ? value * 1_000
      : typeof value === "string"
        ? Date.parse(value)
        : Number.NaN;
  if (
    Number.isFinite(milliseconds) &&
    milliseconds >= EARLIEST_PLAUSIBLE &&
    milliseconds <= LATEST_PLAUSIBLE()
  )
    return new Date(milliseconds).toISOString();
  return "";
}

export function rawRecordTime(record: RawMapRecord): string | null {
  return (
    isoTime(record.memory_at) ||
    ("gmt_created" in record ? isoTime(record.gmt_created) : "") ||
    null
  );
}

function sourceOf(record: RawMapRecord): string {
  return record.source || "default";
}

export function rawMapMemory(record: RawMapRecord): GovernedMemory {
  const created = rawRecordTime(record) ?? "";
  return {
    memory_id: rawRecordId(record),
    user_id: "user_id" in record ? record.user_id : "local",
    kind: "chat",
    status: "confirmed",
    content: record.content,
    scopes: [],
    evidence_ids: [],
    // GovernedMemory is only the map's shared projection. Raw remains raw;
    // this inert value is never shown or written back.
    origin: "extracted",
    tags: [],
    supersedes: [],
    superseded_by: [],
    revision: 0,
    created_at: created,
    updated_at: created,
    deleted_at: null,
  };
}

function pageFor(
  records: RawMapRecord[],
  total: number,
  offset: number,
  limit: number,
  cursor?: {
    next_cursor?: string | null;
    has_more?: boolean;
    total_exact?: boolean;
  },
): MemoryLibraryPage {
  const sourceIds: Record<string, string[]> = {};
  const sourceTime: Record<
    string,
    { at: string; basis: "conversation" | "import" }
  > = {};
  for (const record of records) {
    const recordId = rawRecordId(record);
    sourceIds[recordId] = [sourceOf(record)];
    const conversationAt = isoTime(record.memory_at);
    const importedAt =
      "gmt_created" in record ? isoTime(record.gmt_created) : "";
    if (conversationAt)
      sourceTime[recordId] = {
        at: conversationAt,
        basis: "conversation",
      };
    else if (importedAt)
      sourceTime[recordId] = { at: importedAt, basis: "import" };
  }
  return {
    items: records.map(rawMapMemory),
    total,
    offset,
    limit,
    ...cursor,
    source_ids_by_memory: sourceIds,
    source_time_by_memory: sourceTime,
    search_window: null,
    window_id: null,
    window_reset: false,
    sdk_memory_refs: {},
  };
}

export function rawLibraryPage(page: SdkRawPage): MemoryLibraryPage {
  return pageFor(page.items, page.total, page.offset, page.limit, {
    next_cursor: page.next_cursor,
    has_more: page.has_more,
    total_exact: page.total_exact,
  });
}

export function rawSearchPage(items: SdkRawContextItem[]): MemoryLibraryPage {
  return pageFor(items, items.length, 0, Math.max(items.length, 1));
}

export function rawGraphRecord(item: SdkRawGraphItem): SdkRawContextItem {
  return {
    kind: "raw",
    raw_id: item.raw_id,
    turn_id: item.turn_id,
    content: item.label,
    source: item.source,
    session_id: item.session_id,
    memory_at:
      typeof item.memory_at === "number"
        ? new Date(item.memory_at * 1_000).toISOString()
        : null,
    score: 0,
    truncated: true,
  };
}

export function rawGraphProjection(
  items: SdkRawGraphItem[],
  total: number,
  sources: SourceResponse[],
): MemoryGraphResponse {
  const labels = new Map(
    sources.map((source) => [source.source_id, source.display_name]),
  );
  const nodes = [];
  const edges = [];
  const sourceIds = new Set<string>();
  for (const item of items) {
    const sourceId = item.source || "default";
    if (!sourceIds.has(sourceId)) {
      sourceIds.add(sourceId);
      nodes.push({
        node_id: `source:${sourceId}`,
        node_type: "source" as const,
        memory_id: null,
        source_id: sourceId,
        label: labels.get(sourceId) ?? sourceId,
        kind: null,
        status: null,
        confidence: null,
      });
    }
    const nodeId = `memory:${item.turn_id}`;
    nodes.push({
      node_id: nodeId,
      node_type: "memory" as const,
      memory_id: item.turn_id,
      source_id: null,
      label: item.label,
      kind: "chat",
      status: "confirmed",
      confidence: null,
    });
    edges.push({
      edge_id: `source:${sourceId}->${nodeId}`,
      source_node_id: `source:${sourceId}`,
      target_node_id: nodeId,
      relation: "evidence_source",
      origin: "provenance" as const,
      weight: null,
    });
  }
  return {
    total_memories: total,
    projected_memories: items.length,
    hy_graph_available: false,
    nodes,
    edges,
  };
}

export function rawDetail(record: RawMapRecord): MemoryDetail {
  const memory = rawMapMemory(record);
  return {
    memory,
    evidence: [],
    history: [],
    index_sync: null,
    source_id: sourceOf(record),
    sdk_memory_at: rawRecordTime(record),
    sdk_created_at:
      "gmt_created" in record ? isoTime(record.gmt_created) || null : null,
  };
}
