export type CallResultState =
  | "grounded"
  | "empty"
  | "partial"
  | "timed_out"
  | "unavailable"
  | "denied";

export type CallResultTool =
  | "om_recall"
  | "om_conversations"
  | "om_ask"
  | "om_matters";

export type CallResultClassification =
  | "timed_out"
  | "unavailable"
  | "denied"
  | "empty"
  | "raw-only"
  | "memory-only"
  | "mixed"
  | "unclassified";

export interface CallResultLike {
  state: CallResultState;
  tool: CallResultTool;
  raw_count: number;
  memory_count: number;
}

/**
 * Classify the observable result of a call without consulting the configured
 * memory mode. Failure states always win; successful and partial calls are
 * classified solely by the planes that actually returned content.
 */
export function classifyCallResult(
  result: CallResultLike,
): CallResultClassification {
  if (
    result.state === "timed_out" ||
    result.state === "unavailable" ||
    result.state === "denied" ||
    result.state === "empty"
  ) {
    return result.state;
  }
  const hasRaw = result.raw_count > 0;
  const hasMemory = result.memory_count > 0;
  if (hasRaw && hasMemory) return "mixed";
  if (hasRaw) return "raw-only";
  if (hasMemory) return "memory-only";
  // In particular, om_matters may complete without returning either plane.
  return "unclassified";
}
