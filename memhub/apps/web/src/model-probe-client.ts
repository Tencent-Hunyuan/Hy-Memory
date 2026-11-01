import type {
  ModelProbeOperationReceipt,
  ModelProbeOperationRequest,
  ModelProbeResult,
  ProbeCode,
} from "@one-memory/contracts";
import {
  clearPendingModelProbe,
  type PendingModelProbe,
  readPendingModelProbe,
  savePendingModelProbe,
} from "./pending-model-probe";

export interface ModelProbeTransport {
  lookup(operationId: string): Promise<ModelProbeOperationReceipt>;
  probe(
    profileId: string,
    request: ModelProbeOperationRequest,
  ): Promise<ModelProbeOperationReceipt>;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const CODES: readonly ProbeCode[] = [
  "ok",
  "hy_default_unavailable",
  "missing_api_key",
  "unreachable",
  "unauthorized",
  "rate_limited",
  "insufficient_balance",
  "model_not_found",
  "model_list_unsupported",
  "invalid_response",
  "structured_output_unsupported",
  "embedding_model_required",
  "embedding_unavailable",
];
let inFlight = false;

function fail(code = "model_probe_operation_unavailable"): never {
  throw new Error(code);
}
function uuid(value: unknown): value is string {
  return typeof value === "string" && value.length === 36 && UUID.test(value);
}
function revision(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 1;
}
function object(
  value: unknown,
  required: readonly string[],
  optional: readonly string[] = [],
): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) fail();
  const keys = Reflect.ownKeys(value);
  if (
    keys.some(
      (key) =>
        typeof key !== "string" || ![...required, ...optional].includes(key),
    ) ||
    required.some((key) => !Object.hasOwn(value, key))
  )
    fail();
  const result: Record<string, unknown> = {};
  for (const key of keys) {
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    if (!descriptor || !("value" in descriptor)) fail();
    result[key as string] = descriptor.value;
  }
  return result;
}

/** Bind the tested input to the only revision change a probe can make. */
export function modelProbeMatchesRevision(
  result: ModelProbeResult | null | undefined,
  profileId: string,
  inputRevision: number,
): boolean {
  return (
    result?.profile_id === profileId &&
    // Older receipts can match the same revision, but cannot justify an upgrade.
    (result.input_profile_revision ?? result.profile_revision) ===
      inputRevision &&
    revision(result.profile_revision) &&
    (result.profile_revision === inputRevision ||
      result.profile_revision === inputRevision + 1)
  );
}

function validateResult(
  value: unknown,
  pending: PendingModelProbe,
): ModelProbeResult {
  const result = object(value, [
    "profile_id",
    "profile_revision",
    "input_profile_revision",
    "stages",
    "capabilities",
  ]);
  if (result.input_profile_revision !== pending.inputRevision) fail();
  const capabilities = object(result.capabilities, [
    "endpoint_reachable",
    "model_available",
    "structured_output",
    "embedding_available",
  ]);
  if (Object.values(capabilities).some((value) => typeof value !== "boolean"))
    fail();
  if (
    !Array.isArray(result.stages) ||
    result.stages.length < 1 ||
    result.stages.length > 4
  )
    fail();
  const stages = result.stages.map((value) => {
    const stage = object(
      value,
      ["stage", "ok", "code", "latency_ms"],
      ["embedding_dimensions"],
    );
    if (
      typeof stage.stage !== "string" ||
      ![
        "endpoint",
        "provider",
        "authentication",
        "model",
        "structured_output",
        "embedding",
      ].includes(stage.stage) ||
      typeof stage.ok !== "boolean" ||
      !CODES.includes(stage.code as ProbeCode) ||
      typeof stage.latency_ms !== "number" ||
      !Number.isSafeInteger(stage.latency_ms) ||
      stage.latency_ms < 0 ||
      (stage.embedding_dimensions !== undefined &&
        stage.embedding_dimensions !== null &&
        !revision(stage.embedding_dimensions))
    )
      fail();
    return stage;
  });
  // Project validated data, never expose extra fields from a transport response.
  const validated = {
    ...result,
    stages,
    capabilities,
  } as unknown as ModelProbeResult;
  if (
    !modelProbeMatchesRevision(
      validated,
      pending.profileId,
      pending.inputRevision,
    )
  )
    fail();
  return validated;
}

function receipt(value: unknown, operationId: string): Record<string, unknown> {
  const item = object(value, [
    "schema_version",
    "operation_id",
    "profile_id",
    "expected_profile_revision",
    "state",
    "result",
    "code",
    "stale",
  ]);
  if (
    item.schema_version !== 1 ||
    item.operation_id !== operationId ||
    !uuid(item.operation_id) ||
    !["pending", "completed", "failed", "not_found"].includes(
      item.state as string,
    )
  )
    fail();
  if (item.state === "not_found") {
    if (
      item.profile_id !== null ||
      item.expected_profile_revision !== null ||
      item.result !== null ||
      item.code !== null ||
      item.stale !== null
    )
      fail();
  } else if (
    !uuid(item.profile_id) ||
    !revision(item.expected_profile_revision) ||
    typeof item.stale !== "boolean"
  )
    fail();
  return item;
}

function consume(value: unknown, pending: PendingModelProbe): ModelProbeResult {
  const item = receipt(value, pending.operationId);
  if (item.state === "not_found") fail("model_probe_operation_pending");
  if (
    item.profile_id !== pending.profileId ||
    item.expected_profile_revision !== pending.inputRevision
  )
    fail();
  if (item.state === "pending") {
    if (item.result !== null || item.code !== null) fail();
    fail("model_probe_operation_pending");
  }
  if (item.state === "failed") {
    if (
      item.result !== null ||
      !["model_probe_profile_changed", "model_probe_execution_failed"].includes(
        item.code as string,
      )
    )
      fail();
    clearPendingModelProbe(pending.operationId);
    fail("model_probe_operation_stale");
  }
  if (item.code !== null) fail();
  const result = validateResult(item.result, pending);
  clearPendingModelProbe(pending.operationId);
  if (item.stale) fail("model_probe_operation_stale");
  return result;
}

/** One local operation at a time; persisted unknown operations are GET-only.
 * This module neither activates models nor infers retry permission from not_found.
 */
export async function recoverableProbe(
  profileId: string,
  inputRevision: number,
  transport: ModelProbeTransport,
): Promise<ModelProbeResult> {
  if (inFlight) fail("model_probe_in_flight");
  inFlight = true;
  try {
    const previous = readPendingModelProbe();
    if (!uuid(profileId) || !revision(inputRevision))
      fail("model_probe_input_invalid");
    if (previous) {
      if (previous.profileId !== profileId)
        fail("model_probe_other_profile_pending");
      let result: unknown;
      try {
        result = await transport.lookup(previous.operationId);
      } catch {
        return fail("model_probe_operation_pending");
      }
      return consume(result, previous);
    }
    let operationId: string;
    try {
      operationId = crypto.randomUUID();
      if (!uuid(operationId)) fail();
      const capability = receipt(
        await transport.lookup(operationId),
        operationId,
      );
      if (capability.state !== "not_found") fail();
    } catch {
      return fail("model_probe_capability_unavailable");
    }
    const pending = { operationId, profileId, inputRevision };
    savePendingModelProbe(pending);
    let result: unknown;
    try {
      result = await transport.probe(profileId, {
        operation_id: operationId,
        expected_profile_revision: inputRevision,
      });
    } catch {
      return fail("model_probe_operation_pending");
    }
    return consume(result, pending);
  } finally {
    inFlight = false;
  }
}
