import type {
  GovernedMemory,
  MemoryDetail,
  MemoryLibraryPage,
  SdkRawContextItem,
  SdkRawPage,
  SdkRawRecord,
} from "@one-memory/contracts";

export type RawMapRecord = SdkRawRecord | SdkRawContextItem;

function isoTime(value: number | string | null | undefined): string {
  if (typeof value === "number" && Number.isFinite(value))
    return new Date(value * 1_000).toISOString();
  if (typeof value === "string" && Number.isFinite(Date.parse(value)))
    return new Date(value).toISOString();
  return "";
}

function sourceOf(record: RawMapRecord): string {
  return record.source || "default";
}

export function rawMapMemory(record: RawMapRecord): GovernedMemory {
  const created =
    isoTime(record.memory_at) ||
    ("gmt_created" in record ? isoTime(record.gmt_created) : "");
  return {
    memory_id: record.raw_id,
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
): MemoryLibraryPage {
  const sourceIds: Record<string, string[]> = {};
  const sourceTime: Record<
    string,
    { at: string; basis: "conversation" }
  > = {};
  for (const record of records) {
    sourceIds[record.raw_id] = [sourceOf(record)];
    const at = isoTime(record.memory_at);
    if (at) sourceTime[record.raw_id] = { at, basis: "conversation" };
  }
  return {
    items: records.map(rawMapMemory),
    total,
    offset,
    limit,
    source_ids_by_memory: sourceIds,
    source_time_by_memory: sourceTime,
    search_window: null,
    window_id: null,
    window_reset: false,
    sdk_memory_refs: {},
  };
}

export function rawLibraryPage(page: SdkRawPage): MemoryLibraryPage {
  return pageFor(page.items, page.total, page.offset, page.limit);
}

export function rawSearchPage(
  items: SdkRawContextItem[],
): MemoryLibraryPage {
  return pageFor(items, items.length, 0, Math.max(items.length, 1));
}

export function rawDetail(record: RawMapRecord): MemoryDetail {
  const memory = rawMapMemory(record);
  return {
    memory,
    evidence: [],
    history: [],
    index_sync: null,
    source_id: sourceOf(record),
    sdk_memory_at: isoTime(record.memory_at) || null,
    sdk_created_at:
      "gmt_created" in record ? isoTime(record.gmt_created) || null : null,
  };
}
