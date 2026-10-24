/** Memory-governance operations: the exact write bodies both surfaces send.
 *
 * The CLI builds these in `memory-flows.ts` after validating a plan; the
 * renderer builds the same shapes here. Keeping the wire bodies in one
 * transport-agnostic module means a revision, request id or scope field can
 * only drift in a way the parity test sees.
 *
 * Dependencies are contracts types only so the module can move into a shared
 * package once C0 chooses the landing spot.
 */
import type {
  GovernedMemory,
  MemoryKind,
  MemoryOrigin,
  MemoryStatus,
} from "@one-memory/contracts";

export type MemoryMutationAction = "undo" | "delete";

export interface MemoryWriteRequest {
  path: string;
  body: Record<string, unknown>;
}

export class MemoryOperationInputError extends Error {}

function requireMemoryId(memoryId: string): string {
  if (
    typeof memoryId !== "string" ||
    memoryId.length < 1 ||
    memoryId.length > 128
  )
    throw new MemoryOperationInputError("memory_id_invalid");
  if (/[/\\?#]/.test(memoryId))
    throw new MemoryOperationInputError("memory_id_invalid");
  return encodeURIComponent(memoryId);
}

function requireRequestId(requestId: string): string {
  if (
    typeof requestId !== "string" ||
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(
      requestId,
    )
  )
    throw new MemoryOperationInputError("request_id_invalid");
  return requestId;
}

function requireRevision(revision: number): number {
  if (!Number.isSafeInteger(revision) || revision < 1)
    throw new MemoryOperationInputError("revision_invalid");
  return revision;
}

function requireScopes(scopes: readonly string[]): string[] {
  if (
    !Array.isArray(scopes) ||
    scopes.some((scope) => typeof scope !== "string")
  )
    throw new MemoryOperationInputError("scopes_invalid");
  return [...scopes];
}

/** `POST /api/v1/memories` — manual add, matching the CLI's `draft` body. */
export function buildMemoryCreateRequest(
  draft: {
    content: string;
    scopes: string[];
    evidence_ids: string[];
    origin: MemoryOrigin;
  },
  requestId: string,
): MemoryWriteRequest {
  if (typeof draft.content !== "string" || draft.content.trim().length === 0)
    throw new MemoryOperationInputError("content_required");
  return {
    path: "/api/v1/memories",
    body: {
      request_id: requireRequestId(requestId),
      draft: {
        user_id: "local",
        kind: "chat",
        content: draft.content,
        scopes: requireScopes(draft.scopes),
        evidence_ids: [...draft.evidence_ids],
        origin: draft.origin,
      },
    },
  };
}

/** `POST /api/v1/memories/{id}/correct` — content and optional scopes. */
export function buildMemoryCorrectRequest(
  memoryId: string,
  revision: number,
  content: string,
  scopes: string[] | undefined,
  requestId: string,
): MemoryWriteRequest {
  if (typeof content !== "string" || content.trim().length === 0)
    throw new MemoryOperationInputError("content_required");
  return {
    path: `/api/v1/memories/${requireMemoryId(memoryId)}/correct`,
    body: {
      request_id: requireRequestId(requestId),
      expected_revision: requireRevision(revision),
      content,
      ...(scopes === undefined ? {} : { scopes: requireScopes(scopes) }),
    },
  };
}

/** `POST /api/v1/memories/{id}/actions/{undo|delete}`. */
export function buildMemoryActionRequest(
  memoryId: string,
  action: MemoryMutationAction,
  revision: number,
  requestId: string,
): MemoryWriteRequest {
  if (action !== "undo" && action !== "delete")
    throw new MemoryOperationInputError("action_invalid");
  return {
    path: `/api/v1/memories/${requireMemoryId(memoryId)}/actions/${action}`,
    body: {
      request_id: requireRequestId(requestId),
      expected_revision: requireRevision(revision),
    },
  };
}

const MEMORY_KINDS: readonly MemoryKind[] = [
  "chat",
  "working",
  "coding",
  "schema",
  "intention",
];
const MEMORY_STATUSES: readonly MemoryStatus[] = [
  "confirmed",
  "deleted",
  "superseded",
];
const MEMORY_ORIGINS: readonly MemoryOrigin[] = [
  "extracted",
  "answered",
  "agent",
  "manual",
];

/**
 * Validate a mutation response before it becomes UI state.
 *
 * The CLI never trusts the raw 200 body: `matchesReceipt` in
 * `apps/cli/src/memory-flows.ts:389-423` checks the returned memory against the
 * plan (id, revision = expected+1, origin, status, content, scopes). The
 * renderer previously promoted any object returned by Core straight into
 * `setMemories`/`setDetail`, so a malformed or mis-bound 200 would be rendered
 * as truth. This returns null instead, and callers must treat that as a
 * failed/unknown write rather than a successful one.
 */
export function parseMemoryReceipt(
  value: unknown,
  expected: {
    memoryId: string;
    /** The revision the caller acted against; the receipt must be this + 1. */
    expectedRevision: number;
    /** The status the receipt must report, or null when any non-deleted
     * accepted status is fine (correct/scope keep the memory in effect). */
    expectedStatus: "deleted" | "confirmed";
  },
): GovernedMemory | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const memory = value as Record<string, unknown>;
  if (
    typeof memory.memory_id !== "string" ||
    memory.memory_id !== expected.memoryId ||
    !Number.isSafeInteger(memory.revision) ||
    memory.revision !== expected.expectedRevision + 1 ||
    typeof memory.content !== "string" ||
    typeof memory.origin !== "string" ||
    !MEMORY_ORIGINS.includes(memory.origin as MemoryOrigin) ||
    typeof memory.kind !== "string" ||
    !MEMORY_KINDS.includes(memory.kind as MemoryKind) ||
    typeof memory.status !== "string" ||
    !MEMORY_STATUSES.includes(memory.status as MemoryStatus)
  )
    return null;
  if (
    !Array.isArray(memory.scopes) ||
    memory.scopes.some((scope) => typeof scope !== "string")
  )
    return null;
  // The list panel renders `memory.evidence_ids.length`, so an absent or
  // non-array value would crash the render rather than fail closed. The CLI
  // validates this field too (`core-responses.ts` `identifiers(memory.evidence_ids)`),
  // and a receipt Core accepted must carry the ids it cites.
  if (
    !Array.isArray(memory.evidence_ids) ||
    memory.evidence_ids.some((id) => typeof id !== "string")
  )
    return null;
  // A correct/scope write must leave the memory in effect; a delete must report
  // it gone. Neither may report the other's status.
  if (memory.status !== expected.expectedStatus) return null;
  return value as GovernedMemory;
}

/** Status a mutation action must report, or null when the action is unknown. */
export function memoryReceiptStatus(
  action: "undo" | "delete" | "correct" | "scope",
): "deleted" | "confirmed" | null {
  if (action === "delete") return "deleted";
  if (action === "undo" || action === "correct" || action === "scope")
    return "confirmed";
  return null;
}
