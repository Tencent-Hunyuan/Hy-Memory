/**
 * `memhub graph-width`: the user's decision when the embedding width changed
 * and the memory graph no longer matches it (`POST /api/v1/graph-width`).
 *
 * `keep_existing` and `export_first` change nothing irreversible and run
 * directly. `rebuild_with_new` archives the current graph and rebuilds it, so
 * the CLI asks for `--yes` before sending it.
 */

import { parseGraphWidthDecisionReceipt } from "@one-memory/operations";
import type { CoreOperations } from "./core-operations.js";
import { PRODUCT } from "./version.js";

export const GRAPH_WIDTH_DECISIONS = [
  "keep_existing",
  "rebuild_with_new",
  "export_first",
] as const;
export type GraphWidthDecision = (typeof GRAPH_WIDTH_DECISIONS)[number];

/** The one decision that cannot be taken back. */
export function graphWidthDecisionIsDestructive(
  decision: GraphWidthDecision,
): boolean {
  return decision === "rebuild_with_new";
}

export type GraphWidthDocument =
  | {
      schema_version: 1;
      product: string;
      command: "graph-width";
      state: "committed";
      decision: GraphWidthDecision;
      receipt: Record<string, unknown>;
    }
  | {
      schema_version: 1;
      product: string;
      command: "graph-width";
      state: "rejected" | "unknown";
      decision: GraphWidthDecision;
      code: string;
    };

export async function decideGraphWidth(
  decision: GraphWidthDecision,
  operations: CoreOperations,
): Promise<GraphWidthDocument> {
  const base = {
    schema_version: 1 as const,
    product: PRODUCT,
    command: "graph-width" as const,
    decision,
  };
  const result = await operations.post<unknown>("/api/v1/graph-width", {
    decision,
  });
  if (result.state !== "committed") {
    return { ...base, state: result.state, code: result.code };
  }
  const receipt = parseGraphWidthDecisionReceipt(result.data);
  if (receipt === null) {
    return { ...base, state: "unknown", code: "graph_width_receipt_invalid" };
  }
  return {
    ...base,
    state: "committed",
    receipt: {
      archived_path: receipt.archived_path ?? null,
      exported_path: receipt.exported_path ?? null,
      mismatch: receipt.mismatch ?? null,
    },
  };
}

export function graphWidthExitCode(document: GraphWidthDocument): number {
  return document.state === "committed" ? 0 : 1;
}

export function renderGraphWidthText(document: GraphWidthDocument): string {
  if (document.state === "committed") {
    const lines = [`已执行图谱宽度决定：${document.decision}`];
    if (document.receipt.exported_path) {
      lines.push(`导出：${document.receipt.exported_path}`);
    }
    if (document.receipt.archived_path) {
      lines.push(`旧图谱已归档：${document.receipt.archived_path}`);
    }
    return `${lines.join("\n")}\n`;
  }
  return `图谱宽度决定未完成：${document.state}（${document.code}）\n`;
}
