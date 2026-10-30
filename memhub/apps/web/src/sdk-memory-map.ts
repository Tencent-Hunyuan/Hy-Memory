import type {
  GovernedMemory,
  MemoryDetail,
  MemoryKind,
  MemoryLibraryPage,
  SdkMemoryPage,
  SdkMemoryRecord,
  SdkMemoryRef,
} from "@one-memory/contracts";

/** The SDK memory panel's page size (the API still accepts 1..500). */
export const SDK_MEMORY_PAGE_SIZE = 20;

/** Scene, kind and namespace of an SDK memory. Records from a Core before
 * the all-scene library carry none of these and were all chat. */
export function sdkMemoryRef(record: SdkMemoryRecord): SdkMemoryRef {
  const kind = record.kind ?? "chat";
  if (kind === "chat") return { scene: "chat", kind, variant: null };
  const variant =
    record.variant ?? (record.scene === "coding" ? "coding" : "working");
  return { scene: variant, kind, variant };
}

/** What get/update/delete name for a memory: nothing for chat, so a chat
 * memory is addressed exactly as before; kind+variant for the rest. */
export function sdkNamespace(
  ref: SdkMemoryRef | undefined,
):
  | { kind: SdkMemoryRef["kind"]; variant: SdkMemoryRef["variant"] }
  | undefined {
  return ref && ref.kind !== "chat"
    ? { kind: ref.kind, variant: ref.variant }
    : undefined;
}

/** The text an edit replaces: an experience's solution, otherwise content. */
export function sdkEditableText(record: SdkMemoryRecord): string {
  return record.kind === "experience" && typeof record.solution === "string"
    ? record.solution
    : record.content;
}

function kindOf(record: SdkMemoryRecord): MemoryKind {
  const scene = sdkMemoryRef(record).scene;
  if (scene !== "chat") return scene;
  const layer = record.layer;
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

const EARLIEST_PLAUSIBLE = Date.UTC(2000, 0, 1) / 1_000;

/** A conversation time that can be true: not before 2000, not tomorrow or later.
 *
 * A historical Cursor bug stored a file hash as the message time (years like
 * 2254). Such a value is not shown as when the conversation happened; the
 * import time, or "time unknown", says what is actually known.
 */
function plausible(seconds: number | null | undefined): number | null {
  return typeof seconds === "number" &&
    Number.isFinite(seconds) &&
    seconds >= EARLIEST_PLAUSIBLE &&
    seconds <= Date.now() / 1_000 + 86_400
    ? seconds
    : null;
}

/** The SDK writes "default" when no source was given; keep it as that bucket. */
function sourceOf(record: SdkMemoryRecord): string {
  return record.source || "default";
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
  const created = stamp(
    record.gmt_created ?? record.created_at ?? record.memory_at,
  );
  const updated = stamp(record.updated_at) || created;
  return {
    memory_id: record.memory_id,
    user_id: record.user_id,
    kind: kindOf(record),
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
    updated_at: updated,
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
    source_id: sourceOf(record),
    sdk_memory_at: stamp(plausible(record.memory_at)) || null,
    sdk_created_at: stamp(record.gmt_created ?? record.created_at) || null,
    sdk_ref: sdkMemoryRef(record),
    sdk_task: record.kind === "experience" ? (record.task ?? null) : null,
    sdk_solution:
      record.kind === "experience" ? (record.solution ?? null) : null,
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
    { at: string; basis: "conversation" | "import" | "write" }
  > = {};
  const sourceIds: Record<string, string[]> = {};
  const refs: Record<string, SdkMemoryRef> = {};
  for (const record of page.memories) {
    sourceIds[record.memory_id] = [sourceOf(record)];
    refs[record.memory_id] = sdkMemoryRef(record);
    // An experience or fact stored before hy-memory 2.0.7 has no
    // conversation time. It shows when it was written, labelled as such,
    // rather than calling its creation an import.
    const chat = refs[record.memory_id].kind === "chat";
    const conversation = plausible(record.memory_at);
    const at =
      conversation ?? plausible(chat ? record.gmt_created : record.created_at);
    if (at === null) continue;
    sourceTime[record.memory_id] = {
      at: new Date(at * 1_000).toISOString(),
      basis: conversation !== null ? "conversation" : chat ? "import" : "write",
    };
  }
  return {
    items: page.memories.map(sdkMapMemory),
    total: page.total,
    offset: page.offset,
    limit: page.limit,
    source_ids_by_memory: sourceIds,
    source_time_by_memory: sourceTime,
    search_window: page.search_window ?? null,
    window_id: page.window_id ?? null,
    window_reset: page.window_reset ?? false,
    sdk_memory_refs: refs,
  };
}
