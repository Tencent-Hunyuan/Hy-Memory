import { randomUUID } from "node:crypto";
import type { CoreReader, ReadOutcome } from "./core-client.js";
import type { CoreOperations } from "./core-operations.js";
import { validateModelProfiles } from "./core-responses.js";
import { PRODUCT } from "./version.js";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const RECEIPT_KEYS = new Set([
  "schema_version",
  "operation_id",
  "profile_id",
  "expected_profile_revision",
  "state",
  "result",
  "code",
  "stale",
]);
const RESULT_KEYS = new Set([
  "profile_id",
  "profile_revision",
  "input_profile_revision",
  "stages",
  "capabilities",
]);
const STAGE_KEYS = new Set([
  "stage",
  "ok",
  "code",
  "latency_ms",
  "embedding_dimensions",
]);
const CAPABILITY_KEYS = new Set([
  "endpoint_reachable",
  "model_available",
  "structured_output",
  "embedding_available",
]);
const STAGES = new Set([
  "endpoint",
  "provider",
  "authentication",
  "model",
  "structured_output",
  "embedding",
]);
const CODES = new Set([
  "ok",
  "hy_default_unavailable",
  "missing_api_key",
  "unreachable",
  "unauthorized",
  "rate_limited",
  "model_not_found",
  "model_list_unsupported",
  "invalid_response",
  "structured_output_unsupported",
  "embedding_model_required",
  "embedding_unavailable",
]);

export type ModelProbeFlowDocument =
  | {
      schema_version: 1;
      product: string;
      state: "completed" | "pending" | "failed";
      operation_id: string;
      receipt: Record<string, unknown>;
    }
  | {
      schema_version: 1;
      product: string;
      state: "disabled" | "rejected" | "unknown" | "invalid" | "not_found";
      operation_id?: string;
      code: string;
    };

/**
 * Start one capability probe of a model profile (one real model request).
 *
 * Reads the profile's current revision so Core can refuse a probe of a
 * profile that changed meanwhile, checks that this Core tracks probe
 * operations (an old Core would start an untracked request), then submits it.
 * The probe runs asynchronously: the answer is usually `pending`, and
 * `models receipt <operation-id>` reads the result.
 */
export async function startModelProbe(
  reader: CoreReader,
  operations: CoreOperations,
  profileId: string,
  operationId: string = randomUUID(),
): Promise<ModelProbeFlowDocument> {
  if (!UUID.test(profileId)) return failure("invalid", "invalid_profile_id");
  if (!UUID.test(operationId)) {
    return failure("invalid", "invalid_probe_operation_id");
  }
  const outcome = await reader<unknown>("/api/v1/model-profiles");
  if (outcome.state !== "ok") return fromReadFailure(outcome);
  const profiles = validateModelProfiles(outcome.data);
  if (!profiles) return failure("invalid", "model_profiles_response_invalid");
  const matches = profiles.filter(
    (profile) => profile.profile_id === profileId,
  );
  if (matches.length !== 1)
    return failure("not_found", "model_profile_not_found");
  const revision = matches[0].revision;
  if (!Number.isSafeInteger(revision) || revision < 1) {
    return failure("invalid", "model_profile_revision_invalid");
  }

  // A 404 or a legacy/malformed body is not support. Some old Core versions
  // ignore the operation body and start an untracked external model request.
  const support = await reader<unknown>(operationPath(operationId));
  if (support.state !== "ok") {
    return failure(
      "disabled",
      "model_probe_operations_unsupported",
      operationId,
    );
  }
  const supportReceipt = validateReceipt(support.data, operationId);
  if (supportReceipt?.state !== "not_found") {
    return failure(
      "disabled",
      "model_probe_operations_unsupported",
      operationId,
    );
  }

  const posted = await operations.post<unknown>(
    `/api/v1/model-profiles/${profileId}/probe`,
    { operation_id: operationId, expected_profile_revision: revision },
  );
  if (posted.state === "rejected") {
    return failure("rejected", posted.code, operationId);
  }
  if (posted.state === "committed") {
    return receiptDocument(posted.data, operationId, profileId, revision);
  }
  const reconciled = await readModelProbeReceipt(reader, operationId);
  return reconciled.state === "not_found"
    ? failure(
        "unknown",
        "model_probe_receipt_not_found_after_attempt",
        operationId,
      )
    : reconciled;
}

export async function readModelProbeReceipt(
  reader: CoreReader,
  operationId: string,
): Promise<ModelProbeFlowDocument> {
  if (!UUID.test(operationId)) {
    return failure("invalid", "invalid_probe_operation_id");
  }
  const outcome = await reader<unknown>(operationPath(operationId));
  if (outcome.state !== "ok") {
    return outcome.state === "not_found"
      ? failure("not_found", outcome.code, operationId)
      : failure("unknown", outcome.code, operationId);
  }
  return receiptDocument(outcome.data, operationId);
}

export function modelProbeExitCode(document: ModelProbeFlowDocument): number {
  // `pending` is a submitted probe still running: the submit succeeded.
  return ["completed", "pending"].includes(document.state) ? 0 : 1;
}

export function renderModelProbeText(document: ModelProbeFlowDocument): string {
  const lines = [
    `探测状态：${document.state}`,
    `操作 ID：${document.operation_id ?? "无"}`,
    "code" in document ? `代码：${document.code}` : "已返回有界操作回执。",
  ];
  if (document.state === "pending" && document.operation_id) {
    lines.push(
      `探测在后台进行；用 memhub models receipt ${document.operation_id} 查看结果。`,
    );
  }
  lines.push("");
  return lines.join("\n");
}

function receiptDocument(
  value: unknown,
  operationId: string,
  profileId?: string,
  expectedRevision?: number,
): ModelProbeFlowDocument {
  const receipt = validateReceipt(value, operationId);
  if (
    !receipt ||
    (profileId !== undefined && receipt.profile_id !== profileId) ||
    (expectedRevision !== undefined &&
      receipt.expected_profile_revision !== expectedRevision)
  ) {
    return failure("unknown", "model_probe_receipt_invalid", operationId);
  }
  if (receipt.state === "not_found") {
    return failure("not_found", "model_probe_operation_not_found", operationId);
  }
  const state = receipt.state;
  if (state !== "pending" && state !== "completed" && state !== "failed") {
    return failure("unknown", "model_probe_receipt_invalid", operationId);
  }
  return {
    schema_version: 1,
    product: PRODUCT,
    state,
    operation_id: operationId,
    receipt,
  };
}

function validateReceipt(
  value: unknown,
  operationId: string,
): Record<string, unknown> | null {
  const receipt = record(value);
  if (
    !receipt ||
    !exactKeys(receipt, RECEIPT_KEYS) ||
    receipt.schema_version !== 1 ||
    receipt.operation_id !== operationId ||
    !["pending", "completed", "failed", "not_found"].includes(
      String(receipt.state),
    )
  ) {
    return null;
  }
  if (receipt.state === "not_found") {
    return receipt.profile_id === null &&
      receipt.expected_profile_revision === null &&
      receipt.result === null &&
      receipt.code === null &&
      receipt.stale === null
      ? { ...receipt }
      : null;
  }
  if (
    typeof receipt.profile_id !== "string" ||
    !UUID.test(receipt.profile_id) ||
    !positiveInteger(receipt.expected_profile_revision) ||
    typeof receipt.stale !== "boolean"
  ) {
    return null;
  }
  if (receipt.state === "completed") {
    const result = validateResult(
      receipt.result,
      receipt.profile_id,
      receipt.expected_profile_revision,
    );
    return result && receipt.code === null ? { ...receipt, result } : null;
  }
  if (receipt.result !== null) return null;
  if (receipt.state === "pending") {
    return receipt.code === null ? { ...receipt } : null;
  }
  return [
    "model_probe_profile_changed",
    "model_probe_execution_failed",
  ].includes(String(receipt.code))
    ? { ...receipt }
    : null;
}

function validateResult(
  value: unknown,
  profileId: string,
  inputRevision: number,
): Record<string, unknown> | null {
  const result = record(value);
  if (
    !result ||
    !exactKeys(result, RESULT_KEYS) ||
    result.profile_id !== profileId ||
    result.input_profile_revision !== inputRevision ||
    !positiveInteger(result.profile_revision) ||
    ![inputRevision, inputRevision + 1].includes(result.profile_revision) ||
    !Array.isArray(result.stages) ||
    result.stages.length < 1 ||
    result.stages.length > 4
  ) {
    return null;
  }
  const stages: Record<string, unknown>[] = [];
  const seen = new Set<string>();
  for (const value of result.stages) {
    const stage = record(value);
    if (
      !stage ||
      !exactKeys(stage, STAGE_KEYS) ||
      typeof stage.stage !== "string" ||
      !STAGES.has(stage.stage) ||
      seen.has(stage.stage) ||
      typeof stage.ok !== "boolean" ||
      typeof stage.code !== "string" ||
      !CODES.has(stage.code) ||
      !nonnegativeInteger(stage.latency_ms) ||
      stage.latency_ms > 3_600_000 ||
      !(
        stage.embedding_dimensions === null ||
        positiveInteger(stage.embedding_dimensions)
      )
    ) {
      return null;
    }
    seen.add(stage.stage);
    stages.push({ ...stage });
  }
  const capabilities = record(result.capabilities);
  if (
    !capabilities ||
    !exactKeys(capabilities, CAPABILITY_KEYS) ||
    [...CAPABILITY_KEYS].some((key) => typeof capabilities[key] !== "boolean")
  ) {
    return null;
  }
  return { ...result, stages, capabilities: { ...capabilities } };
}

function operationPath(operationId: string): string {
  return `/api/v1/model-probe-operations/${operationId}`;
}

function fromReadFailure(
  outcome: Exclude<ReadOutcome<unknown>, { state: "ok" }>,
): ModelProbeFlowDocument {
  return failure(
    outcome.state === "not_found" ? "not_found" : "unknown",
    outcome.code,
  );
}

function failure(
  state: "disabled" | "rejected" | "unknown" | "invalid" | "not_found",
  code: string,
  operationId?: string,
): ModelProbeFlowDocument {
  return {
    schema_version: 1,
    product: PRODUCT,
    state,
    ...(operationId ? { operation_id: operationId } : {}),
    code,
  };
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function exactKeys(
  value: Record<string, unknown>,
  expected: ReadonlySet<string>,
): boolean {
  const keys = Object.keys(value);
  return (
    keys.length === expected.size && keys.every((key) => expected.has(key))
  );
}

function positiveInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && (value as number) >= 1;
}

function nonnegativeInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && (value as number) >= 0;
}
