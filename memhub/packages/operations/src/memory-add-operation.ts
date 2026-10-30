/** Memory-add contract: the `POST /api/v2/memories` body, its receipt check and
 * the addition status path.
 *
 * Core takes exactly `request_id`, `text`, `scope`; the CLI and the UI both
 * hard-code `scope: "all-apps"`, so keeping the body here means both surfaces
 * send the same three fields. A receipt is validated before it becomes state —
 * the evidence id must be a UUID and the state must be one of the known
 * states — so a malformed 200 is never rendered as a real addition.
 *
 * Dependency-free (contracts types only) so it can move into a shared package.
 */
import type { MemoryAddInput, MemoryAddReceipt } from "@one-memory/contracts";

export class MemoryAddInputError extends Error {}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const ADD_STATES = [
  "waiting_for_model",
  "waiting_for_permission",
  "pending",
  "running",
  "failed",
  "ready",
  "no_memories",
] as const;

export interface MemoryAddRequest {
  path: "/api/v2/memories";
  body: { request_id: string; text: string; scope: "all-apps" };
}

/** The exact three-field body Core accepts; scope is fixed, not caller-supplied. */
export function buildMemoryAddRequest(input: MemoryAddInput): MemoryAddRequest {
  if (typeof input.request_id !== "string" || !UUID.test(input.request_id))
    throw new MemoryAddInputError("request_id_invalid");
  if (typeof input.text !== "string" || input.text.trim().length === 0)
    throw new MemoryAddInputError("text_required");
  return {
    path: "/api/v2/memories",
    body: { request_id: input.request_id, text: input.text, scope: "all-apps" },
  };
}

/** Where the status of one addition is read, keyed by its request id. */
export function memoryAddStatusPath(requestId: string): string {
  if (typeof requestId !== "string" || !UUID.test(requestId))
    throw new MemoryAddInputError("request_id_invalid");
  return `/api/v2/memories/additions/${requestId}`;
}

/**
 * Validate a memory-add receipt before it becomes UI state.
 *
 * Returns null for a malformed body; callers must treat that as an unknown
 * result rather than a saved memory.
 */
export function parseMemoryAddReceipt(value: unknown): MemoryAddReceipt | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const receipt = value as Record<string, unknown>;
  if (
    typeof receipt.evidence_id !== "string" ||
    !UUID.test(receipt.evidence_id) ||
    typeof receipt.state !== "string" ||
    !(ADD_STATES as readonly string[]).includes(receipt.state) ||
    receipt.scope !== "all-apps"
  )
    return null;
  if (
    !Array.isArray(receipt.memory_ids) ||
    receipt.memory_ids.some((id) => typeof id !== "string")
  )
    return null;
  return value as MemoryAddReceipt;
}
