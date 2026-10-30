/** Memory-processing writes: extraction window, graph width, manual extract.
 *
 * These three are the same class of gap as the model-profile and
 * continuous-policy receipts: a 2xx body is not trusted until it parses
 * (the CLI's `graph-width-flow.ts` reports `graph_width_receipt_invalid`
 * instead of a committed write). The Web returned
 * `response.data` straight into component state.
 *
 * The graph-width case is the one that can lie: `GraphWidthPrompt` treats an
 * absent `mismatch` as "resolved" (`if (receipt.mismatch) return; setSettled`),
 * so a malformed 200 with no `mismatch` key would close the prompt as if the
 * width had been settled. Validating here means a body without the key is an
 * unknown result, never a false "resolved".
 *
 * Dependency-free (contracts types only) so it can move into a shared package.
 */
import type {
  ExtractionSettings,
  ExtractionWindowPreset,
  GraphWidthDecisionReceipt,
  GraphWidthMismatch,
  ManualExtractReceipt,
} from "@one-memory/contracts";

export class MemoryProcessingInputError extends Error {}

const PRESETS: readonly ExtractionWindowPreset[] = ["3m", "6m", "1y", "all"];
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

/** Validate the manual-extract evidence list exactly as the CLI plan does. */
export function normalizeManualExtractEvidence(
  evidenceIds: readonly string[],
): string[] {
  const ids = [...new Set(evidenceIds)];
  if (ids.length !== evidenceIds.length || !ids.every((id) => UUID.test(id)))
    throw new MemoryProcessingInputError("manual_extract_evidence_ids_invalid");
  return ids;
}

/**
 * The extraction-settings receipt must echo the preset that was requested;
 * echoing a different one means the response belongs to another write.
 */
export function parseExtractionSettingsReceipt(
  value: unknown,
  requested: ExtractionWindowPreset,
): ExtractionSettings | null {
  if (typeof value !== "object" || value === null || Array.isArray(value))
    return null;
  const receipt = value as Record<string, unknown>;
  return receipt.window_preset === requested &&
    (PRESETS as readonly unknown[]).includes(receipt.window_preset)
    ? (receipt as unknown as ExtractionSettings)
    : null;
}

/**
 * The graph-width decision receipt must carry a `mismatch` key; `null` means
 * the decision resolved it. The check is deliberately the same one the CLI
 * makes (`Object.hasOwn(receipt, "mismatch")`) rather than a stricter shape
 * test, so both surfaces judge the same body identically. The two paths are
 * coerced to `string | null` exactly as the CLI does.
 */
export function parseGraphWidthDecisionReceipt(
  value: unknown,
): GraphWidthDecisionReceipt | null {
  if (typeof value !== "object" || value === null || Array.isArray(value))
    return null;
  const receipt = value as Record<string, unknown>;
  if (!Object.hasOwn(receipt, "mismatch")) return null;
  return {
    archived_path:
      typeof receipt.archived_path === "string" ? receipt.archived_path : null,
    exported_path:
      typeof receipt.exported_path === "string" ? receipt.exported_path : null,
    mismatch: (receipt.mismatch ?? null) as GraphWidthMismatch | null,
  } as GraphWidthDecisionReceipt;
}

/**
 * All three counters must be safe integers; a missing one is not a zero. The
 * check is exactly the CLI's (`Number.isSafeInteger`, no lower bound) so both
 * surfaces judge the same body identically.
 */
export function parseManualExtractReceipt(
  value: unknown,
): ManualExtractReceipt | null {
  if (typeof value !== "object" || value === null || Array.isArray(value))
    return null;
  const receipt = value as Record<string, unknown>;
  for (const key of ["promoted", "batches_planned", "batches_current"]) {
    if (!Number.isSafeInteger(receipt[key])) return null;
  }
  return receipt as unknown as ManualExtractReceipt;
}
