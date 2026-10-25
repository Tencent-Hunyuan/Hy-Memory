import type {
  GovernedMemory,
  MemoryDetail,
  MemoryKind,
  MemoryLibraryPage,
  SdkMemoryPage,
  SdkMemoryRecord,
} from "@one-memory/contracts";

function kindOf(layer: string): MemoryKind {
  if (layer === "l6_schema") return "schema";
  if (layer === "l7_intention") return "intention";
  if (layer.startsWith("coding")) return "coding";
  if (layer.startsWith("working")) return "working";
  return "chat";
}

function stamp(seconds: number | null | undefined): string {
  return typeof seconds === "number" && Number.isFinite(seconds)
    ? new Date(seconds * 1_000).toISOString()
    : "";
}

/** An SDK memory in the shape the memory map draws.
 *
 * The map groups and labels by id, content, kind, status and tags only, so the
 * governed-only fields are inert placeholders here. Opening, editing and
 * deleting still go through the SDK (v2) endpoints — `sdkDetail()` below wraps
 * the same projection for the map's inspector, which is why the inspector can
 * stay where the design put it instead of sending the reader to another view.
 */
export function sdkMapMemory(record: SdkMemoryRecord): GovernedMemory {
  const created = stamp(record.gmt_created ?? record.memory_at);
  return {
    memory_id: record.memory_id,
    user_id: record.user_id,
    kind: kindOf(record.layer),
    status:
      record.status === "active"
        ? "confirmed"
        : record.status === "superseded"
          ? "superseded"
          : "deleted",
    content: record.content,
    scopes: [],
    evidence_ids: [],
    origin: "extracted",
    tags: [...record.tags],
    supersedes: [...record.supersedes],
    superseded_by: [...record.superseded_by],
    // GovernedMemory still requires a revision for the shared map model. SDK
    // has no revision contract; the inspector recognises sdk_status and never
    // renders this placeholder.
    revision: 0,
    created_at: created,
    updated_at: created,
    deleted_at: null,
  };
}

/** One SDK memory as the map's inspector reads it.
 *
 * The inspector was built for a governed detail. SDK-native memory has no
 * product revision or "original evidence" section; source_raw_ids remain an
 * internal SDK provenance relation and are deliberately not projected as UI
 * evidence.
 */
export function sdkDetail(record: SdkMemoryRecord): MemoryDetail {
  return {
    memory: sdkMapMemory(record),
    evidence: [],
    history: [],
    index_sync: null,
    sdk_status: record.status,
    source_id: record.source,
    sdk_memory_at: stamp(record.memory_at) || null,
    sdk_created_at: stamp(record.gmt_created) || null,
  };
}

/** One SDK page as the map's left list reads it.
 *
 * The list asks a library page for its rows, its total and — per row — when
 * the cited conversation happened. The SDK page carries the row itself, so the
 * time is taken from the memory: `memory_at` is the moment the conversation
 * happened, `gmt_created` only the moment it was imported. Naming which one it
 * is keeps "对话于" from silently becoming "沉淀于".
 */
export function sdkLibraryPage(page: SdkMemoryPage): MemoryLibraryPage {
  const sourceTime: Record<
    string,
    { at: string; basis: "conversation" | "import" }
  > = {};
  const sourceIds: Record<string, string[]> = {};
  for (const record of page.memories) {
    if (record.source) sourceIds[record.memory_id] = [record.source];
    const at = record.memory_at ?? record.gmt_created;
    if (typeof at !== "number" || !Number.isFinite(at)) continue;
    sourceTime[record.memory_id] = {
      at: new Date(at * 1_000).toISOString(),
      basis: record.memory_at ? "conversation" : "import",
    };
  }
  return {
    items: page.memories.map(sdkMapMemory),
    total: page.total,
    offset: page.offset,
    limit: page.limit,
    source_ids_by_memory: sourceIds,
    source_time_by_memory: sourceTime,
  };
}
