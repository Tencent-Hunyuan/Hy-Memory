import type { SourceResponse } from "@one-memory/contracts";

/**
 * Source ids MemHub writes itself. They never arrive in the source list, so a
 * memory carrying one used to be labelled with the raw internal id.
 */
const INTERNAL_SOURCE_NAMES: Record<string, string> = {
  "one-memory-manual": "手动添加",
  "memory-content-export": "导入的已有记忆",
};

/**
 * The source the SDK records when a writer named none. The legacy-to-SDK
 * backfill wrote every legacy memory this way, so it is a real, filterable
 * bucket ("未识别来源"), not a source still being checked.
 */
export const UNATTRIBUTED_SOURCE_ID = "default";

type NamedSource = Pick<SourceResponse, "source_id" | "display_name">;

/** The name to show for a source id, falling back to the id itself. */
export function sourceName(
  sources: readonly NamedSource[],
  sourceId: string | null | undefined,
): string | null {
  if (!sourceId) return null;
  return (
    sources.find((source) => source.source_id === sourceId)?.display_name ??
    INTERNAL_SOURCE_NAMES[sourceId] ??
    sourceId
  );
}

/** The name to show for a source id, with a placeholder when there is none. */
export function sourceNameOr(
  sources: readonly NamedSource[],
  sourceId: string | null | undefined,
  fallback: string,
): string {
  return sourceName(sources, sourceId) ?? fallback;
}
