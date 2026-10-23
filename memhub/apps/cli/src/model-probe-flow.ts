import { createHash, randomUUID } from "node:crypto";
import type { CoreReader, ReadOutcome } from "./core-client.js";
import type { CoreOperations } from "./core-operations.js";
import { validateModelProfiles } from "./core-responses.js";
import { PRODUCT } from "./version.js";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const DIGEST = /^[0-9a-f]{64}$/;
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
  "structured_output_truncated",
  "embedding_model_required",
  "embedding_unavailable",
]);

interface ModelProbePlanBody {
  schema_version: 1;
  kind: "model_profile_probe";
  operation_id: string;
  profile_id: string;
  expected_profile_revision: number;
}

export type ModelProbePlan = ModelProbePlanBody & { digest: string };

export type ModelProbeFlowDocument =
  | {
      schema_version: 1;
      product: string;
      state: "preview";
      plan: ModelProbePlan;
    }
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

export async function planModelProbe(
  reader: CoreReader,
  profileId: string,
): Promise<ModelProbeFlowDocument> {
  if (!UUID.test(profileId)) return failure("invalid", "invalid_profile_id");
  const outcome = await reader<unknown>("/api/v1/model-profiles");
  if (outcome.state !== "ok") return fromReadFailure(outcome);
  const profiles = validateModelProfiles(outcome.data);
  if (!profiles) return failure("invalid", "model_profiles_response_invalid");
  const matches = profiles.filter(
    (profile) => profile.profile_id === profileId,
  );
  if (matches.length !== 1)
    return failure("not_found", "model_profile_not_found");
  const profile = matches[0];
  if (!Number.isSafeInteger(profile.revision) || profile.revision < 1) {
    return failure("invalid", "model_profile_revision_invalid");
  }
  const plan = withDigest({
    schema_version: 1 as const,
    kind: "model_profile_probe" as const,
    operation_id: randomUUID(),
    profile_id: profileId,
    expected_profile_revision: profile.revision,
  });
  return { schema_version: 1, product: PRODUCT, state: "preview", plan };
}

export async function executeModelProbe(
  value: unknown,
  reader: CoreReader,
  operations: CoreOperations,
): Promise<ModelProbeFlowDocument> {
  const plan = validatePlan(value);
  if (!plan) return failure("invalid", "model_probe_plan_invalid");

  // A 404 or a legacy/malformed body is not support. Some old Core versions
  // ignore the operation body and start an untracked external model request.
  const support = await reader<unknown>(operationPath(plan.operation_id));
  if (support.state !== "ok") {
    return failure(
      "disabled",
      "model_probe_operations_unsupported",
      plan.operation_id,
    );
  }
  const supportReceipt = validateReceipt(support.data, plan.operation_id);
  if (supportReceipt?.state !== "not_found") {
    return failure(
      "disabled",
      "model_probe_operations_unsupported",
      plan.operation_id,
    );
  }

  const outcome = await operations.post<unknown>(
    `/api/v1/model-profiles/${plan.profile_id}/probe`,
    {
      operation_id: plan.operation_id,
      expected_profile_revision: plan.expected_profile_revision,
    },
  );
  if (outcome.state === "rejected") {
    return failure("rejected", outcome.code, plan.operation_id);
  }
  if (outcome.state === "committed") {
    return receiptDocument(
      outcome.data,
      plan.operation_id,
      plan.profile_id,
      plan.expected_profile_revision,
    );
  }
  const reconciled = await readModelProbeReceipt(reader, plan.operation_id);
  return reconciled.state === "not_found"
    ? failure(
        "unknown",
        "model_probe_receipt_not_found_after_attempt",
        plan.operation_id,
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

export function inspectModelProbePlan(value: unknown): ModelProbePlan | null {
  return validatePlan(value);
}

export function modelProbeExitCode(document: ModelProbeFlowDocument): number {
  return ["preview", "completed"].includes(document.state) ? 0 : 1;
}

export function renderModelProbeText(document: ModelProbeFlowDocument): string {
  if (document.state === "preview") {
    return [
      "预览：模型能力探测",
      `模型配置：${document.plan.profile_id}`,
      `预期版本：${document.plan.expected_profile_revision}`,
      `操作 ID：${document.plan.operation_id}`,
      `确认摘要：${document.plan.digest}`,
      "未发起模型请求。请保存 --json 输出，再用 models probe --plan-file <path> --confirm。",
      "",
    ].join("\n");
  }
  return [
    `探测状态：${document.state}`,
    `操作 ID：${document.operation_id ?? "无"}`,
    "code" in document ? `代码：${document.code}` : "已返回有界操作回执。",
    "",
  ].join("\n");
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

function validatePlan(value: unknown): ModelProbePlan | null {
  const plan = record(value);
  if (
    plan?.schema_version !== 1 ||
    plan.kind !== "model_profile_probe" ||
    typeof plan.operation_id !== "string" ||
    !UUID.test(plan.operation_id) ||
    typeof plan.profile_id !== "string" ||
    !UUID.test(plan.profile_id) ||
    !positiveInteger(plan.expected_profile_revision) ||
    typeof plan.digest !== "string" ||
    !DIGEST.test(plan.digest)
  ) {
    return null;
  }
  const expected = withDigest({
    schema_version: 1 as const,
    kind: "model_profile_probe" as const,
    operation_id: plan.operation_id,
    profile_id: plan.profile_id,
    expected_profile_revision: plan.expected_profile_revision,
  });
  return expected.digest === plan.digest ? expected : null;
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

function withDigest<T extends object>(body: T): T & { digest: string } {
  return {
    ...body,
    digest: createHash("sha256").update(canonical(body)).digest("hex"),
  };
}

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value)
      .sort()
      .map(
        (key) =>
          `${JSON.stringify(key)}:${canonical((value as Record<string, unknown>)[key])}`,
      )
      .join(",")}}`;
  }
  return JSON.stringify(value);
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
