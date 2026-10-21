import { createHash, randomUUID, timingSafeEqual } from "node:crypto";
import type { GovernedMemory } from "@one-memory/contracts";
import {
  buildMemoryActionRequest,
  buildMemoryCorrectRequest,
  buildMemoryCreateRequest,
  parseMemoryReceipt,
} from "@one-memory/operations";
import type { CoreReader } from "./core-client.js";
import type { CoreOperations } from "./core-operations.js";
import { validateMemoryDetail } from "./core-responses.js";
import { isMemoryId } from "./memory-report.js";
import { PRODUCT } from "./version.js";

const MEMORY_KINDS = [
  "chat",
  "working",
  "coding",
  "schema",
  "intention",
] as const;
const OPERATIONS = ["add", "correct", "delete"] as const;
const MEMORY_ORIGINS = ["extracted", "answered", "agent", "manual"] as const;
const MUTATION_RECEIPT_KEYS = new Set([
  "schema_version",
  "request_id",
  "state",
  "operation",
  "memory_id",
  "result_revision",
  "result_status",
  "memory_state",
  "migrated_from",
]);
const CONTENT_MAX_CHARACTERS = 100_000;
const ID = /^[A-Za-z0-9][\w.-]{0,127}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

interface PlanBody {
  schema_version: 1;
  kind: "memory_mutation";
  operation: (typeof OPERATIONS)[number];
  request_id: string;
  memory_id?: string;
  expected_revision?: number;
  expected_status?: string;
  expected_origin?: string;
  memory_kind?: (typeof MEMORY_KINDS)[number];
  scopes?: string[];
  evidence_ids?: string[];
  content_sha256?: string;
  content_characters?: number;
  current?: {
    status: string;
    origin: string;
    revision: number;
    scopes: string[];
    evidence: Array<{
      evidence_id: string;
      available: boolean;
      source_id?: string;
      written_by?: string;
    }>;
  };
}

export type MemoryMutationPlan = PlanBody & { digest: string };

export type MemoryFlowDocument =
  | {
      schema_version: 1;
      product: string;
      state: "preview";
      plan: MemoryMutationPlan;
    }
  | {
      schema_version: 1;
      product: string;
      state: "committed" | "recovered";
      operation_id: string;
      receipt: Record<string, unknown>;
    }
  | {
      schema_version: 1;
      product: string;
      state: "rejected" | "unknown" | "invalid";
      operation_id?: string;
      code: string;
    };

export function parseMemoryContent(bytes: Buffer): string | null {
  if (bytes.byteLength > CONTENT_MAX_CHARACTERS * 4 + 2) return null;
  let decoded: string;
  try {
    decoded = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    return null;
  }
  const content = decoded.replace(/\r?\n$/, "");
  return content.trim().length > 0 &&
    [...content].length <= CONTENT_MAX_CHARACTERS &&
    ![...content].some((character) => {
      const code = character.charCodeAt(0);
      return code <= 8 || (code >= 11 && code <= 31) || code === 127;
    })
    ? content
    : null;
}

export function planMemoryAdd(input: {
  content: string;
  memoryKind: string;
  scopes: string[];
  evidenceIds: string[];
}): MemoryFlowDocument {
  if (
    !validContent(input.content) ||
    !oneOf(input.memoryKind, MEMORY_KINDS) ||
    !validScopes(input.scopes) ||
    !validIds(input.evidenceIds, 500)
  ) {
    return invalid("memory_add_input_invalid");
  }
  return preview(
    withDigest({
      schema_version: 1,
      kind: "memory_mutation",
      operation: "add",
      request_id: randomUUID(),
      memory_kind: input.memoryKind,
      scopes: input.scopes,
      evidence_ids: input.evidenceIds,
      ...contentIdentity(input.content),
    }),
  );
}

export async function planExistingMemoryMutation(
  reader: CoreReader,
  input: {
    operation: Exclude<(typeof OPERATIONS)[number], "add">;
    memoryId: string;
    content?: string;
  },
): Promise<MemoryFlowDocument> {
  if (
    !isMemoryId(input.memoryId) ||
    (input.operation === "correct" && input.content === undefined) ||
    (input.content !== undefined && !validContent(input.content))
  ) {
    return invalid("memory_plan_input_invalid");
  }
  const outcome = await reader<unknown>(`/api/v1/memories/${input.memoryId}`);
  if (outcome.state !== "ok") {
    return invalid(`memory_${outcome.state}:${outcome.code}`);
  }
  const detail = validateMemoryDetail(outcome.data, input.memoryId);
  if (!detail) return invalid("memory_response_invalid");
  const memory = detail.memory;
  if (memory.status !== "confirmed") {
    return invalid("memory_operation_not_allowed");
  }
  const body: PlanBody = {
    schema_version: 1,
    kind: "memory_mutation",
    operation: input.operation,
    request_id: randomUUID(),
    memory_id: memory.memory_id,
    expected_revision: memory.revision,
    expected_status: memory.status,
    expected_origin: memory.origin,
    ...(input.content ? contentIdentity(input.content) : {}),
    current: {
      status: memory.status,
      origin: memory.origin,
      revision: memory.revision,
      scopes: [...memory.scopes],
      evidence: detail.evidence.map((item) => ({
        evidence_id: item.evidence_id,
        available: item.available,
        ...(item.source_id ? { source_id: item.source_id } : {}),
        ...(item.written_by ? { written_by: item.written_by } : {}),
      })),
    },
  };
  return preview(withDigest(body));
}

export function inspectMemoryPlan(
  value: unknown,
): { operationId: string; digest: string; needsContent: boolean } | null {
  const plan = validatePlan(value);
  return plan
    ? {
        operationId: plan.request_id,
        digest: plan.digest,
        needsContent: plan.operation === "add" || plan.operation === "correct",
      }
    : null;
}

export function memoryPlanAcceptsContent(
  value: unknown,
  content: string | undefined,
): boolean {
  const plan = validatePlan(value);
  if (!plan) return false;
  if (plan.operation !== "add" && plan.operation !== "correct") {
    return content === undefined;
  }
  if (content === undefined) return false;
  const identity = contentIdentity(content);
  return (
    identity.content_sha256 === plan.content_sha256 &&
    identity.content_characters === plan.content_characters
  );
}

export async function executeMemoryMutation(
  value: unknown,
  content: string | undefined,
  operations: CoreOperations,
  reader?: CoreReader,
): Promise<MemoryFlowDocument> {
  const plan = validatePlan(value);
  if (!plan) return invalid("memory_plan_invalid");
  if (!memoryPlanAcceptsContent(plan, content)) {
    return invalid("memory_content_mismatch");
  }
  const { path, body } = operationRequest(plan, content);
  const outcome = await operations.post<unknown>(path, body);
  if (outcome.state === "rejected") {
    return {
      schema_version: 1,
      product: PRODUCT,
      state: "rejected",
      operation_id: plan.request_id,
      code: outcome.code,
    };
  }
  if (outcome.state === "unknown") {
    return reader
      ? readMemoryMutationReceipt(reader, plan)
      : pending(plan.request_id, outcome.code);
  }
  const memory = validateWriteResponse(outcome.data, plan);
  if (!memory || !matchesReceipt(memory, plan, content)) {
    return reader
      ? readMemoryMutationReceipt(reader, plan)
      : pending(plan.request_id, "memory_receipt_invalid");
  }
  return {
    schema_version: 1,
    product: PRODUCT,
    state: "committed",
    operation_id: plan.request_id,
    receipt: {
      memory_id: memory.memory_id,
      status: memory.status,
      origin: memory.origin,
      revision: memory.revision,
      scopes: memory.scopes,
      evidence_ids: memory.evidence_ids,
      content_sha256: contentIdentity(memory.content).content_sha256,
      content_characters: [...memory.content].length,
      created_at: memory.created_at,
      updated_at: memory.updated_at,
    },
  };
}

/**
 * Validate the write response through the shared `parseMemoryReceipt`, which
 * owns the GovernedMemory shape and the revision/status binding Core must
 * satisfy. The CLI keeps only its own plan binding (`matchesReceipt`, which
 * also checks `origin`, `kind` and the echoed content), so the two surfaces
 * judge a write response identically and no shape rule is duplicated here.
 *
 * `add` creates the first revision and expects `confirmed`; the shared parser
 * derives that from `expectedRevision: 0`. Every other operation advances the
 * revision the caller acted against and expects `confirmed` (correct/scope)
 * or `deleted` (delete).
 */
function validateWriteResponse(
  value: unknown,
  plan: MemoryMutationPlan,
): GovernedMemory | null {
  const expectedStatus = plan.operation === "delete" ? "deleted" : "confirmed";
  // For `add` there is no id or prior revision to bind yet, so the parser's
  // own receipt is the only source for both; the plan binding that follows is
  // what proves the response belongs to this request.
  const memoryId =
    plan.operation === "add"
      ? String((value as { memory_id?: unknown } | null)?.memory_id ?? "")
      : (plan.memory_id as string);
  if (memoryId === "") return null;
  return parseMemoryReceipt(value, {
    memoryId,
    expectedRevision:
      plan.operation === "add" ? 0 : (plan.expected_revision as number),
    expectedStatus,
  });
}

export async function readMemoryMutationReceipt(
  reader: CoreReader,
  value: unknown,
): Promise<MemoryFlowDocument> {
  const plan = validatePlan(value);
  if (!plan) return invalid("memory_plan_invalid");
  const outcome = await reader<unknown>(
    `/api/v1/memory-mutations/${encodeURIComponent(plan.request_id)}`,
  );
  if (outcome.state !== "ok") {
    return pending(plan.request_id, `memory_mutation_receipt_${outcome.code}`);
  }
  const receipt = validateMutationReceipt(outcome.data, plan);
  if (receipt === "not_found") {
    return pending(
      plan.request_id,
      "memory_mutation_receipt_not_found_after_attempt",
    );
  }
  if (!receipt) {
    return pending(plan.request_id, "memory_mutation_receipt_invalid");
  }
  return {
    schema_version: 1,
    product: PRODUCT,
    state: "recovered",
    operation_id: plan.request_id,
    receipt,
  };
}

export function memoryFlowExitCode(document: MemoryFlowDocument): number {
  return ["preview", "committed", "recovered"].includes(document.state) ? 0 : 1;
}

export function renderMemoryFlowText(document: MemoryFlowDocument): string {
  if (document.state === "preview") {
    const plan = document.plan;
    const lines = [
      `记忆操作预览：${plan.operation}`,
      `request_id：${plan.request_id}`,
    ];
    if (plan.memory_id) lines.push(`memory_id：${plan.memory_id}`);
    if (plan.expected_revision) {
      lines.push(
        `当前版本：${plan.expected_revision}  状态：${plan.expected_status}  来源方式：${plan.expected_origin}`,
      );
    }
    if (plan.scopes) lines.push(`使用范围：${plan.scopes.join(",")}`);
    if (plan.current) {
      for (const evidence of plan.current.evidence) {
        lines.push(
          `证据 ${evidence.evidence_id}：source=${evidence.source_id ?? "unknown"} author=${evidence.written_by ?? "unknown"} available=${evidence.available}`,
        );
      }
    }
    if (plan.content_characters !== undefined) {
      lines.push(
        `正文：${plan.content_characters} 字符，sha256=${plan.content_sha256}`,
      );
    }
    lines.push("尚未写入；保存 JSON 计划后用 memories apply --confirm 执行。");
    return `${lines.join("\n")}\n`;
  }
  if (document.state === "committed" || document.state === "recovered") {
    return `记忆操作${document.state === "recovered" ? "已从治理回执恢复" : "已提交"}：${document.operation_id}\n${JSON.stringify(document.receipt)}\n`;
  }
  return `记忆操作未完成：${document.state}（${"code" in document ? document.code : "memory_operation_state_invalid"}）${document.operation_id ? ` request_id=${document.operation_id}` : ""}\n`;
}

function operationRequest(
  plan: MemoryMutationPlan,
  content: string | undefined,
): { path: string; body: Record<string, unknown> } {
  if (plan.operation === "add") {
    // The shared create builder is the Web console's manual-add body, which
    // always creates `chat` (the Web has no kind picker). The CLI exposes
    // `--kind`, so it builds through the same builder and then sets the kind,
    // keeping every other field (user_id/scope/evidence/origin) shared.
    const request = buildMemoryCreateRequest(
      {
        content: content as string,
        scopes: plan.scopes ?? [],
        evidence_ids: plan.evidence_ids ?? [],
        origin: "manual",
      },
      plan.request_id,
    );
    const draft = request.body.draft as Record<string, unknown>;
    draft.kind = plan.memory_kind;
    return { path: request.path, body: request.body };
  }
  if (plan.operation === "correct") {
    return buildMemoryCorrectRequest(
      plan.memory_id as string,
      plan.expected_revision as number,
      content as string,
      undefined,
      plan.request_id,
    );
  }
  return buildMemoryActionRequest(
    plan.memory_id as string,
    plan.operation,
    plan.expected_revision as number,
    plan.request_id,
  );
}

function matchesReceipt(
  memory: GovernedMemory,
  plan: MemoryMutationPlan,
  content: string | undefined,
): boolean {
  if (plan.operation === "add") {
    return (
      memory.status === "confirmed" &&
      memory.origin === "manual" &&
      memory.revision === 1 &&
      memory.kind === plan.memory_kind &&
      same(memory.scopes, plan.scopes ?? []) &&
      same(memory.evidence_ids, plan.evidence_ids ?? []) &&
      memory.content === content
    );
  }
  if (
    memory.memory_id !== plan.memory_id ||
    memory.revision !== (plan.expected_revision as number) + 1 ||
    memory.origin !== plan.expected_origin
  ) {
    return false;
  }
  if (plan.operation === "delete" && memory.status !== "deleted") return false;
  if (plan.operation === "correct" && memory.status !== plan.expected_status) {
    return false;
  }
  if (plan.operation === "correct" && memory.content !== content) return false;
  const expectedScopes =
    plan.scopes ?? (plan.current?.scopes as string[] | undefined);
  return expectedScopes ? same(memory.scopes, expectedScopes) : true;
}

function validateMutationReceipt(
  value: unknown,
  plan: MemoryMutationPlan,
): Record<string, unknown> | "not_found" | null {
  const receipt = record(value);
  if (
    !receipt ||
    Object.keys(receipt).length !== MUTATION_RECEIPT_KEYS.size ||
    Object.keys(receipt).some((key) => !MUTATION_RECEIPT_KEYS.has(key)) ||
    receipt.schema_version !== 2 ||
    receipt.request_id !== plan.request_id
  ) {
    return null;
  }
  if (receipt.state === "not_found") {
    return [
      receipt.operation,
      receipt.memory_id,
      receipt.result_revision,
      receipt.result_status,
      receipt.memory_state,
      receipt.migrated_from,
    ].every((item) => item === null)
      ? "not_found"
      : null;
  }
  const expectedOperation =
    plan.operation === "add" ? "create" : plan.operation;
  const expectedRevision =
    plan.operation === "add" ? 1 : (plan.expected_revision as number) + 1;
  const expectedStatus = plan.operation === "delete" ? "deleted" : "confirmed";
  // A migrated receipt records an operation from before v2; no plan made
  // today can have produced it.
  if (
    receipt.state !== "committed" ||
    receipt.migrated_from !== null ||
    receipt.operation !== expectedOperation ||
    typeof receipt.memory_id !== "string" ||
    !isMemoryId(receipt.memory_id) ||
    (plan.memory_id !== undefined && receipt.memory_id !== plan.memory_id) ||
    receipt.result_revision !== expectedRevision ||
    receipt.result_status !== expectedStatus ||
    !["present", "deleted"].includes(String(receipt.memory_state))
  ) {
    return null;
  }
  return {
    schema_version: 2,
    request_id: plan.request_id,
    state: "committed",
    operation: expectedOperation,
    memory_id: receipt.memory_id,
    result_revision: expectedRevision,
    result_status: expectedStatus,
    memory_state: receipt.memory_state,
    proof_scope: "governance_transaction_only",
    downstream_sync_verified: false,
    payload_fingerprint_verified: false,
  };
}

function validatePlan(value: unknown): MemoryMutationPlan | null {
  const plan = record(value);
  if (
    plan?.schema_version !== 1 ||
    plan.kind !== "memory_mutation" ||
    !oneOf(plan.operation, OPERATIONS) ||
    typeof plan.request_id !== "string" ||
    !UUID.test(plan.request_id) ||
    typeof plan.digest !== "string"
  ) {
    return null;
  }
  const body = { ...plan };
  delete body.digest;
  const expected = withDigest(body as unknown as PlanBody);
  if (!safeEqual(expected.digest, plan.digest)) return null;
  if (plan.operation === "add") {
    if (
      !oneOf(plan.memory_kind, MEMORY_KINDS) ||
      !validScopes(plan.scopes) ||
      !validIds(plan.evidence_ids, 500) ||
      !contentFields(plan)
    ) {
      return null;
    }
  } else if (
    typeof plan.memory_id !== "string" ||
    !isMemoryId(plan.memory_id) ||
    !Number.isSafeInteger(plan.expected_revision) ||
    (plan.expected_revision as number) < 1 ||
    plan.expected_status !== "confirmed" ||
    !oneOf(plan.expected_origin, MEMORY_ORIGINS) ||
    !validCurrent(plan) ||
    (plan.operation === "correct" && !contentFields(plan))
  ) {
    return null;
  }
  return plan as unknown as MemoryMutationPlan;
}

function contentFields(plan: Record<string, unknown>): boolean {
  return (
    typeof plan.content_sha256 === "string" &&
    /^[0-9a-f]{64}$/.test(plan.content_sha256) &&
    Number.isSafeInteger(plan.content_characters) &&
    (plan.content_characters as number) >= 1 &&
    (plan.content_characters as number) <= CONTENT_MAX_CHARACTERS
  );
}

function validCurrent(plan: Record<string, unknown>): boolean {
  const current = record(plan.current);
  if (
    !current ||
    current.status !== plan.expected_status ||
    current.origin !== plan.expected_origin ||
    current.revision !== plan.expected_revision ||
    !validScopes(current.scopes) ||
    !Array.isArray(current.evidence) ||
    current.evidence.length < 1 ||
    current.evidence.length > 500
  ) {
    return false;
  }
  return current.evidence.every((value) => {
    const evidence = record(value);
    return (
      evidence !== null &&
      typeof evidence.evidence_id === "string" &&
      ID.test(evidence.evidence_id) &&
      typeof evidence.available === "boolean" &&
      (evidence.source_id === undefined ||
        (typeof evidence.source_id === "string" &&
          evidence.source_id.length <= 128)) &&
      (evidence.written_by === undefined ||
        (typeof evidence.written_by === "string" &&
          evidence.written_by.length <= 256))
    );
  });
}

function contentIdentity(content: string): {
  content_sha256: string;
  content_characters: number;
} {
  return {
    content_sha256: createHash("sha256").update(content, "utf8").digest("hex"),
    content_characters: [...content].length,
  };
}

function validContent(content: string): boolean {
  return (
    content.trim().length > 0 &&
    [...content].length <= CONTENT_MAX_CHARACTERS &&
    ![...content].some((character) => {
      const code = character.charCodeAt(0);
      return code <= 8 || (code >= 11 && code <= 31) || code === 127;
    })
  );
}

function validScopes(value: unknown): value is string[] {
  if (
    !Array.isArray(value) ||
    value.length < 1 ||
    value.length > 128 ||
    new Set(value).size !== value.length ||
    value.some(
      (scope) =>
        typeof scope !== "string" ||
        scope.trim() !== scope ||
        scope.length < 1 ||
        scope.length > 512 ||
        scope === "*",
    )
  ) {
    return false;
  }
  return !value.includes("private") || value.length === 1;
}

function validIds(value: unknown, maximum: number): value is string[] {
  return (
    Array.isArray(value) &&
    value.length >= 1 &&
    value.length <= maximum &&
    new Set(value).size === value.length &&
    value.every((item) => typeof item === "string" && ID.test(item))
  );
}

function withDigest(body: PlanBody): MemoryMutationPlan {
  return {
    ...body,
    digest: createHash("sha256")
      .update(JSON.stringify(body), "utf8")
      .digest("hex"),
  };
}

function preview(plan: MemoryMutationPlan): MemoryFlowDocument {
  return {
    schema_version: 1,
    product: PRODUCT,
    state: "preview",
    plan,
  };
}

function invalid(code: string): MemoryFlowDocument {
  return {
    schema_version: 1,
    product: PRODUCT,
    state: "invalid",
    code,
  };
}

function pending(operationId: string, code: string): MemoryFlowDocument {
  return {
    schema_version: 1,
    product: PRODUCT,
    state: "unknown",
    operation_id: operationId,
    code,
  };
}

function same(left: readonly string[], right: readonly string[]): boolean {
  return (
    left.length === right.length &&
    left.every((value, index) => value === right[index])
  );
}

function safeEqual(left: string, right: string): boolean {
  if (left.length !== right.length) return false;
  return timingSafeEqual(Buffer.from(left), Buffer.from(right));
}

function oneOf<const T extends readonly string[]>(
  value: unknown,
  values: T,
): value is T[number] {
  return typeof value === "string" && values.includes(value as T[number]);
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}
