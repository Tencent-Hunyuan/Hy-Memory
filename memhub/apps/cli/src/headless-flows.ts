import { randomUUID } from "node:crypto";
import type { ImportJob, ModelProfile } from "@one-memory/contracts";
import {
  buildModelProfileCreateRequest,
  buildSourceImportRequest,
  ModelProfileInputError,
  parseModelProfileReceipt,
  SourceImportInputError,
} from "@one-memory/operations";
import type { CoreReader, ReadOutcome } from "./core-client.js";
import type { CoreOperations } from "./core-operations.js";
import {
  validateImportJob,
  validateModelProfiles,
  validateSourceConnectionCandidates,
} from "./core-responses.js";
import { PRODUCT } from "./version.js";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const SOURCE_ID = /^[a-z0-9][a-z0-9-]{0,127}$/;
const PROVIDERS = [
  "hy_default",
  "openai_compatible",
  "anthropic",
  "ollama",
  "local_onnx",
] as const;

export type FlowDocument =
  | {
      schema_version: 1;
      product: string;
      state: "committed" | "recovered" | "partial";
      operation_id: string;
      receipt: Record<string, unknown>;
    }
  | {
      schema_version: 1;
      product: string;
      state: "rejected" | "unknown" | "invalid" | "not_found";
      operation_id?: string;
      code: string;
      /** Present when a batch was refused source-by-source, so the caller can
       * see exactly which source failed and why. */
      receipt?: Record<string, unknown>;
    };

/** Classify a source-import receipt by what was actually accepted.
 *
 * A 2xx from `/sources/connect` is not success: the endpoint accepts the
 * request and can still refuse every source (`failed_sources` non-empty,
 * `accepted_source_ids` empty). Reporting `committed` there told the user a
 * read-off source had been imported when nothing happened (C8 P2, H18r2).
 *   - all accepted                     -> committed
 *   - some accepted, some failed       -> partial
 *   - none accepted but some failed     -> rejected (carries the reasons)
 *   - a fresh all-busy refusal          -> rejected/source_import_all_busy
 *
 * `recovered` is never produced here: this classifier handles a fresh POST.
 * Only `readSourceReceipt` on the same request ID can prove idempotent
 * recovery.
 */
function classifySourceImport(
  operationId: string,
  receipt: Record<string, unknown>,
): FlowDocument {
  const accepted = Array.isArray(receipt.accepted_source_ids)
    ? (receipt.accepted_source_ids as unknown[])
    : [];
  const failed = receipt.failed_sources as Record<string, unknown> | undefined;
  const failedCount = failed ? Object.keys(failed).length : 0;
  const onlyBusy =
    accepted.length === 0 &&
    failedCount > 0 &&
    Object.values(failed ?? {}).every((code) => code === "source_import_busy");
  if (accepted.length === 0 && failedCount > 0) {
    return {
      schema_version: 1,
      product: PRODUCT,
      state: "rejected",
      operation_id: operationId,
      code: onlyBusy ? "source_import_all_busy" : "source_import_all_rejected",
      receipt,
    };
  }
  if (accepted.length > 0 && failedCount > 0) {
    return {
      schema_version: 1,
      product: PRODUCT,
      state: "partial",
      operation_id: operationId,
      receipt,
    };
  }
  return committed(operationId, receipt);
}

/**
 * Connect sources and import their history in one direct request.
 *
 * Reads the current candidates, refuses a source whose location comes from an
 * environment override unless the caller confirmed overrides, and posts one
 * `/sources/connect` (grant + history import + live sync). `requestId` is the
 * idempotency key: re-running with the same id reads the first receipt.
 */
export async function connectSourcesWithImport(
  reader: CoreReader,
  operations: CoreOperations,
  input: {
    sourceIds: readonly string[];
    confirmOverrides: boolean;
    requestId?: string;
  },
): Promise<FlowDocument> {
  const sourceIds = input.sourceIds;
  if (
    sourceIds.length < 1 ||
    sourceIds.length > 32 ||
    new Set(sourceIds).size !== sourceIds.length ||
    sourceIds.some((id) => !SOURCE_ID.test(id))
  ) {
    return invalid("invalid_source_selection");
  }
  const requestId = input.requestId ?? randomUUID();
  if (!UUID.test(requestId)) return invalid("invalid_request_id");
  const outcome = await reader<unknown>("/api/v1/sources");
  if (outcome.state !== "ok") return fromReadFailure(outcome);
  const candidates = validateSourceConnectionCandidates(outcome.data);
  if (!candidates) return invalid("source_response_invalid");
  const selected = sourceIds.map((id) =>
    candidates.filter((candidate) => candidate.source_id === id),
  );
  if (selected.some((matches) => matches.length !== 1)) {
    return invalid("source_unavailable");
  }
  const items = selected.map(([candidate]) => candidate);
  const needsOverride = items
    .filter((item) => item.requires_override_confirmation)
    .map((item) => item.source_id);
  if (needsOverride.length > 0 && !input.confirmOverrides) {
    return {
      schema_version: 1,
      product: PRODUCT,
      state: "rejected",
      operation_id: requestId,
      code: "environment_override_confirmation_required",
      receipt: { override_required_source_ids: needsOverride },
    };
  }
  // The shared builder owns the connect body's field set and coalescing, so
  // the CLI and the Web console send the same request by construction.
  let request: ReturnType<typeof buildSourceImportRequest>;
  try {
    request = buildSourceImportRequest({
      sourceIds: [...sourceIds],
      sourceRoots: Object.fromEntries(
        items.map((item) => [item.source_id, item.candidate_root_id]),
      ),
      sourceCandidateRevisions: Object.fromEntries(
        items.flatMap((item) =>
          item.candidate_revision === undefined
            ? []
            : [[item.source_id, item.candidate_revision]],
        ),
      ),
      confirmEnvironmentOverrides: needsOverride.length > 0,
      fullRescan: false,
      onboarding: true,
      enableSync: true,
      requestId,
    });
  } catch (error) {
    if (error instanceof SourceImportInputError) {
      return invalid("invalid_source_selection");
    }
    throw error;
  }
  const result = await operations.post<unknown>(request.path, request.body);
  if (result.state === "rejected") {
    return rejected(requestId, result.code);
  }
  if (result.state === "committed") {
    const receipt = validateSourceReceipt(result.data, requestId);
    if (!receipt) return unknown(requestId, "source_receipt_invalid");
    return classifySourceImport(requestId, receipt);
  }
  const recovered = await readSourceReceipt(reader, requestId);
  if (recovered.state === "unknown" || recovered.state === "not_found") {
    return unknown(requestId, result.code);
  }
  // A recovered receipt must be classified the same way as a fresh one: an
  // all-rejected or all-busy replay is still not success. Only a terminal
  // accepted receipt is promoted to `recovered`.
  return recovered.state === "committed"
    ? { ...recovered, state: "recovered" }
    : recovered;
}

export async function readSourceReceipt(
  reader: CoreReader,
  requestId: string,
): Promise<FlowDocument> {
  if (!UUID.test(requestId)) return invalid("invalid_request_id");
  const result = await reader<unknown>(
    `/api/v1/source-connections/${requestId}`,
  );
  if (result.state === "not_found") {
    return {
      schema_version: 1,
      product: PRODUCT,
      state: "not_found",
      operation_id: requestId,
      code: result.code,
    };
  }
  if (result.state !== "ok") return unknown(requestId, result.code);
  const receipt = validateSourceReceipt(result.data, requestId);
  if (!receipt) return unknown(requestId, "source_receipt_invalid");
  const accepted = receipt.accepted_source_ids as unknown[];
  const failed = receipt.failed_sources as Record<string, unknown>;
  // Recovery is only justified by the same request ID proving a terminal,
  // accepted write. An empty/empty pending receipt must stay unknown until
  // the job reaches a terminal state; a nonempty failure keeps its refusal.
  if (accepted.length === 0 && Object.keys(failed).length === 0) {
    return unknown(requestId, "source_import_pending");
  }
  if (accepted.length === 0 || Object.keys(failed).length > 0) {
    return classifySourceImport(requestId, receipt);
  }
  return committed(requestId, receipt);
}

/**
 * Create a model profile directly from a bounded config (never containing the
 * key) plus an optional key read from a controlled file.
 *
 * `operationId` is Core's creation idempotency key; the receipt can be read
 * later with `readModelReceipt`.
 */
export async function createModelProfile(
  value: unknown,
  apiKey: string | undefined,
  reader: CoreReader,
  operations: CoreOperations,
  operationId: string = randomUUID(),
): Promise<FlowDocument> {
  const config = validateModelConfig(value);
  if (!config) return invalid("model_config_invalid");
  if (!UUID.test(operationId)) return invalid("invalid_operation_id");
  // The shared create builder owns the body's allow-list, bounds and the
  // creation_operation_id/api_key handling, so the CLI and the Web console
  // send the same create request.
  let request: ReturnType<typeof buildModelProfileCreateRequest>;
  try {
    request = buildModelProfileCreateRequest(
      {
        ...config,
        ...(apiKey === undefined ? {} : { api_key: apiKey }),
      } as Parameters<typeof buildModelProfileCreateRequest>[0],
      operationId,
    );
  } catch (error) {
    if (error instanceof ModelProfileInputError) {
      return invalid("model_config_invalid");
    }
    throw error;
  }
  const result = await operations.post<unknown>(request.path, request.body);
  if (result.state === "rejected") {
    return rejected(operationId, result.code);
  }
  if (result.state === "committed") {
    const profile = validateModelProfile(result.data);
    return profile
      ? committed(operationId, projectProfile(profile))
      : unknown(operationId, "model_receipt_invalid");
  }
  const recovered = await readModelReceipt(reader, operationId);
  return recovered.state === "committed"
    ? { ...recovered, state: "recovered" }
    : unknown(operationId, result.code);
}

export async function readModelReceipt(
  reader: CoreReader,
  operationId: string,
): Promise<FlowDocument> {
  if (!UUID.test(operationId)) return invalid("invalid_operation_id");
  const result = await reader<unknown>(
    `/api/v1/model-profile-creations/${operationId}`,
  );
  if (result.state !== "ok") {
    return result.state === "not_found"
      ? {
          schema_version: 1,
          product: PRODUCT,
          state: "not_found",
          operation_id: operationId,
          code: result.code,
        }
      : unknown(operationId, result.code);
  }
  const receipt = validateModelOperation(result.data, operationId);
  if (!receipt) return unknown(operationId, "model_operation_receipt_invalid");
  if (receipt.state !== "committed") {
    return unknown(operationId, `model_operation_${receipt.state}`);
  }
  return committed(operationId, receipt);
}

export function parseApiKey(bytes: Buffer | null): string | undefined {
  if (!bytes) return undefined;
  const text = bytes.toString("utf8");
  if (text.includes("\uFFFD")) return undefined;
  const value = text.replace(/\r?\n$/, "");
  if (
    value.length < 1 ||
    value.length > 16_384 ||
    [...value].some((character) => {
      const code = character.charCodeAt(0);
      return code <= 31 || code === 127;
    })
  ) {
    return undefined;
  }
  return value;
}

export function renderFlowText(document: FlowDocument): string {
  const lines = [
    `操作状态：${document.state}`,
    `操作 ID：${document.operation_id ?? "无"}`,
    "code" in document ? `代码：${document.code}` : "已返回有界回执。",
  ];
  // A partial success or an all-rejected batch must name each source that was
  // not accepted; a bare "partial" leaves the user with nothing to act on.
  if (
    (document.state === "partial" || document.state === "rejected") &&
    "receipt" in document &&
    document.receipt
  ) {
    const failed = document.receipt.failed_sources as
      | Record<string, unknown>
      | undefined;
    if (failed) {
      for (const [sourceId, reason] of Object.entries(failed)) {
        lines.push(`  未接入 ${sourceId}：${String(reason)}`);
      }
    }
    const accepted = document.receipt.accepted_source_ids;
    if (document.state === "partial" && Array.isArray(accepted)) {
      lines.push(`  已接入：${accepted.join("、")}`);
    }
    const overrides = document.receipt.override_required_source_ids;
    if (Array.isArray(overrides)) {
      lines.push(
        `  这些来源的目录来自环境变量覆盖，需要确认：${overrides.join("、")}`,
        "  确认目录无误后加 --confirm-overrides 重新运行。",
      );
    }
  }
  lines.push("");
  return lines.join("\n");
}

export function flowExitCode(document: FlowDocument): number {
  return ["committed", "recovered"].includes(document.state) ? 0 : 1;
}

function validateModelConfig(value: unknown): Record<string, unknown> | null {
  const config = record(value);
  if (!config || "api_key" in config || "creation_operation_id" in config) {
    return null;
  }
  const allowed = new Set([
    "name",
    "provider_kind",
    "base_url",
    "model",
    "embedding_model",
    "embedding_provider_kind",
    "embedding_base_url",
    "embedding_dimensions",
    "timeout_seconds",
    "max_concurrency",
    "context_window",
    "disable_thinking",
  ]);
  if (Object.keys(config).some((key) => !allowed.has(key))) return null;
  if (
    !boundedText(config.name, 128) ||
    !PROVIDERS.includes(config.provider_kind as (typeof PROVIDERS)[number]) ||
    !boundedText(config.model, 256) ||
    !optionalText(config.base_url, 2_048) ||
    !optionalText(config.embedding_model, 256) ||
    !optionalProvider(config.embedding_provider_kind) ||
    !optionalText(config.embedding_base_url, 2_048) ||
    !optionalInteger(config.embedding_dimensions, 1, 1_000_000) ||
    !optionalInteger(config.timeout_seconds, 1, 600) ||
    !optionalInteger(config.max_concurrency, 1, 32) ||
    !optionalInteger(config.context_window, 1_024, 10_000_000) ||
    (config.disable_thinking !== undefined &&
      typeof config.disable_thinking !== "boolean")
  ) {
    return null;
  }
  return Object.fromEntries(
    Object.entries(config).map(([key, item]) => [key, item]),
  );
}

function validateSourceReceipt(
  value: unknown,
  requestId: string,
): Record<string, unknown> | null {
  const receipt = record(value);
  if (
    !receipt ||
    receipt.request_id !== requestId ||
    !Array.isArray(receipt.accepted_source_ids) ||
    receipt.accepted_source_ids.some(
      (id) => typeof id !== "string" || !SOURCE_ID.test(id),
    )
  ) {
    return null;
  }
  const failed = record(receipt.failed_sources);
  if (
    !failed ||
    Object.entries(failed).some(
      ([key, code]) =>
        !SOURCE_ID.test(key) ||
        typeof code !== "string" ||
        !/^[a-z0-9_.:-]{1,128}$/.test(code),
    ) ||
    !Array.isArray(receipt.jobs) ||
    receipt.jobs.length > 32
  ) {
    return null;
  }
  const jobs: ImportJob[] = [];
  for (const item of receipt.jobs) {
    const job = validateImportJob(item);
    if (!job || !receipt.accepted_source_ids.includes(job.source_id))
      return null;
    jobs.push(job);
  }
  return {
    request_id: requestId,
    accepted_source_ids: [...receipt.accepted_source_ids],
    failed_sources: { ...failed },
    jobs: jobs.map(projectJob),
  };
}

/** Delegate to the shared operations package so model-profile receipts are
 * judged identically on both surfaces (null = malformed, caller reports
 * `unknown`/`model_receipt_invalid`). */
function validateModelProfile(value: unknown): ModelProfile | null {
  return parseModelProfileReceipt(value);
}

function validateModelOperation(
  value: unknown,
  operationId: string,
): Record<string, unknown> | null {
  const receipt = record(value);
  if (
    !receipt ||
    receipt.operation_id !== operationId ||
    !["committed", "pending", "deleted", "not_found"].includes(
      asString(receipt.state),
    ) ||
    (receipt.profile_id !== null && !boundedText(receipt.profile_id, 128)) ||
    !(receipt.created_revision === null || receipt.created_revision === 1)
  ) {
    return null;
  }
  return {
    operation_id: operationId,
    state: receipt.state,
    profile_id: receipt.profile_id,
    created_revision: receipt.created_revision,
  };
}

function projectProfile(profile: ModelProfile): Record<string, unknown> {
  return {
    profile_id: profile.profile_id,
    name: profile.name,
    provider_kind: profile.provider_kind,
    model: profile.model,
    embedding_model: profile.embedding_model,
    credential_state: profile.credential_state,
    revision: profile.revision,
    is_active: profile.is_active,
  };
}

function projectJob(job: ImportJob): Record<string, unknown> {
  return {
    job_id: job.job_id,
    source_id: job.source_id,
    operation: job.operation ?? "history",
    status: job.status,
    phase: job.phase,
    discovered: job.discovered,
    stored: job.stored,
    errors: job.errors,
  };
}

function committed(
  operationId: string,
  receipt: Record<string, unknown>,
): FlowDocument {
  return {
    schema_version: 1,
    product: PRODUCT,
    state: "committed",
    operation_id: operationId,
    receipt,
  };
}

function rejected(operationId: string, code: string): FlowDocument {
  return {
    schema_version: 1,
    product: PRODUCT,
    state: "rejected",
    operation_id: operationId,
    code,
  };
}

function unknown(operationId: string, code: string): FlowDocument {
  return {
    schema_version: 1,
    product: PRODUCT,
    state: "unknown",
    operation_id: operationId,
    code,
  };
}

function invalid(code: string): FlowDocument {
  return {
    schema_version: 1,
    product: PRODUCT,
    state: "invalid",
    code,
  };
}

function fromReadFailure(
  outcome: Exclude<ReadOutcome<unknown>, { state: "ok" }>,
): FlowDocument {
  return {
    schema_version: 1,
    product: PRODUCT,
    state: outcome.state === "not_found" ? "not_found" : "unknown",
    code: outcome.code,
  };
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function boundedText(value: unknown, max: number): value is string {
  return typeof value === "string" && value.length >= 1 && value.length <= max;
}

function optionalText(value: unknown, max: number): boolean {
  return value === undefined || value === null || boundedText(value, max);
}

function optionalProvider(value: unknown): boolean {
  return (
    value === undefined ||
    value === null ||
    PROVIDERS.includes(value as (typeof PROVIDERS)[number])
  );
}

function optionalInteger(
  value: unknown,
  minimum: number,
  maximum: number,
): boolean {
  return (
    value === undefined ||
    value === null ||
    (Number.isSafeInteger(value) &&
      (value as number) >= minimum &&
      (value as number) <= maximum)
  );
}
