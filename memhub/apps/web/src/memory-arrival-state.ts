import type {
  MemoryProcessingBlock,
  MemoryProcessingSummary,
  SourceResponse,
} from "@one-memory/contracts";
import { modelBlock, splitBlockedStages } from "./memory-processing-labels";

export type MemoryArrivalPhase =
  | "checking"
  | "importing"
  | "model-required"
  | "processing"
  | "failed"
  | "idle"
  | "stopped"
  | "unavailable";

/** Parent-owned setup and job snapshot; importing is independent of processing. */
export interface MemoryArrival {
  importing: boolean;
  importedMessages: number;
  importedConversations: number;
  /** processing requires confirmed running generation, not import or queued preparation. */
  phase: MemoryArrivalPhase;
  /** True only while a current status read is in flight. */
  refreshing?: boolean;
  blockedStages?: readonly MemoryProcessingBlock[];
  backlogPending?: number;
  /** Confirmed memory count, never inferred from completed processing tasks. */
  memoryCount?: number;
}

export function resolveMemoryArrival({
  arrival,
  processing,
  sources,
  loaded,
}: {
  arrival?: MemoryArrival;
  processing: MemoryProcessingSummary | null;
  sources: readonly SourceResponse[];
  loaded: boolean;
}): MemoryArrival {
  const authorized = sources.filter((source) => source.authorized);
  const importedMessages =
    arrival?.importedMessages ??
    authorized.reduce((total, source) => total + source.imported_messages, 0);
  const importedConversations =
    arrival?.importedConversations ??
    authorized.reduce(
      (total, source) => total + source.imported_conversations,
      0,
    );
  const blocks = (
    processing?.blocked_stages ??
    arrival?.blockedStages ??
    []
  ).filter((block) => block.count > 0);
  let phase: MemoryArrivalPhase = arrival?.phase ?? "idle";
  if (
    arrival &&
    ["checking", "unavailable", "stopped", "model-required"].includes(
      arrival.phase,
    )
  ) {
    return blocks.length ? { ...arrival, blockedStages: blocks } : arrival;
  }
  if (!arrival && (!loaded || (importedMessages > 0 && processing === null))) {
    phase = "checking";
  } else if (
    (processing?.running ?? 0) > 0 ||
    (processing === null && arrival?.phase === "processing")
  ) {
    // Aggregate failures can belong to other or historical tasks. Preserve
    // them in details without hiding a current, positively observed runner.
    phase = "processing";
  } else if ((processing?.waiting_for_model ?? 0) > 0 || modelBlock(blocks)) {
    phase = "model-required";
  } else if ((processing?.failed ?? 0) > 0 && !onlyRetries(blocks)) {
    // "Failed" is a prompt to go and look. Work the pipeline will pick up on
    // its own pass is not that, and asking the reader to check a failure
    // whose only answer is "wait" spends attention for nothing.
    phase = "failed";
  } else if (processing !== null) {
    phase =
      arrival?.importing || arrival?.phase === "importing"
        ? "importing"
        : "idle";
  }
  return {
    ...arrival,
    importing: arrival?.importing ?? false,
    importedMessages,
    importedConversations,
    phase,
    ...(blocks.length ? { blockedStages: blocks } : {}),
    ...((processing?.backlog_pending ?? 0) > 0
      ? { backlogPending: processing?.backlog_pending }
      : {}),
  };
}

/** Every stall here is one Core retries on its own.
 *
 * False for an empty list: a failure count with nothing to explain it is
 * still worth looking at, and this predicate only ever excuses a failure it
 * can actually account for.
 */
function onlyRetries(blocks: readonly MemoryProcessingBlock[]): boolean {
  return blocks.length > 0 && splitBlockedStages(blocks).named.length === 0;
}

export function memoryArrivalTitle(arrival: MemoryArrival): string {
  switch (arrival.phase) {
    case "checking":
    case "unavailable":
      return arrival.refreshing ? "已保存内容保留，正在核对进度" : "进度待更新";
    case "importing":
      return "正在导入对话";
    case "processing":
      return "正在沉淀记忆";
    case "model-required":
      return modelBlock(arrival.blockedStages ?? [])
        ? "待继续"
        : "尚未设置整理模型";
    case "failed":
    case "stopped":
      return "待继续";
    default:
      return arrival.importedMessages > 0 ? "待继续" : "还没有保存的对话";
  }
}
