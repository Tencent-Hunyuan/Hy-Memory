import type {
  GovernedMemory,
  MemoryDetail,
  MemoryLibraryPage,
  SdkRawContextItem,
  SdkRawPage,
  SdkRawRecord,
} from "@one-memory/contracts";

export type RawMapRecord = SdkRawRecord | SdkRawContextItem;

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
    { at: string; basis: "conversation" | "import" }
  > = {};
  for (const record of records) {
    sourceIds[record.raw_id] = [sourceOf(record)];
    const conversationAt = isoTime(record.memory_at);
    const importedAt =
      "gmt_created" in record ? isoTime(record.gmt_created) : "";
    if (conversationAt)
      sourceTime[record.raw_id] = {
        at: conversationAt,
        basis: "conversation",
      };
    else if (importedAt)
      sourceTime[record.raw_id] = { at: importedAt, basis: "import" };
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

export function rawSearchPage(items: SdkRawContextItem[]): MemoryLibraryPage {
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
    sdk_memory_at: rawRecordTime(record),
    sdk_created_at:
      "gmt_created" in record ? isoTime(record.gmt_created) || null : null,
  };
}
