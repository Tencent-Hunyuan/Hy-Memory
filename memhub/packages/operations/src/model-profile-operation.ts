/** Model-profile creation: the field contract both surfaces must satisfy.
 *
 * The CLI validates a create request in `headless-flows.ts:479-519` before
 * sending it: it rejects `api_key` and `creation_operation_id` inside the
 * reviewable config, allows exactly 13 named fields, and bounds each one. The
 * Web previously posted its `ModelProfileInput` straight through, so a caller
 * could send an extra key (or an out-of-range value) and only find out if Core
 * happened to reject it — with an error the UI then had to guess at.
 *
 * This module mirrors the CLI's allow-list and bounds so both surfaces either
 * accept the same body or fail before the request. The credential is passed
 * **separately** (`api_key`) exactly as the CLI does, never mixed into config.
 *
 * Dependency-free (contracts types only) so it can move into a shared package.
 */
import type {
  ModelProfile,
  ModelProfileInput,
  ProviderKind,
} from "@one-memory/contracts";

export class ModelProfileInputError extends Error {}

const PROVIDERS: readonly ProviderKind[] = [
  "hy_default",
  "openai_compatible",
  "anthropic",
  "ollama",
  "local_onnx",
];

const CREDENTIAL_STATES = ["available", "missing", "unavailable"] as const;

/** Matches the CLI's `text()`: a bounded string, empty allowed. */
function boundedValue(value: unknown, max: number): value is string {
  return typeof value === "string" && value.length <= max;
}

/** The reviewable config fields; credentials are never part of this set. */
const CONFIG_KEYS = [
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
  "output_token_parameter",
] as const;

const OUTPUT_TOKEN_PARAMETERS = ["max_tokens", "max_completion_tokens"];

function optionalOutputTokenParameter(value: unknown): boolean {
  return (
    value === undefined ||
    (typeof value === "string" && OUTPUT_TOKEN_PARAMETERS.includes(value))
  );
}

function text(value: unknown, max: number): value is string {
  return typeof value === "string" && value.length >= 1 && value.length <= max;
}

function optionalText(value: unknown, max: number): boolean {
  return value === undefined || value === null || text(value, max);
}

function optionalInteger(value: unknown, min: number, max: number): boolean {
  return (
    value === undefined ||
    value === null ||
    (typeof value === "number" &&
      Number.isSafeInteger(value) &&
      value >= min &&
      value <= max)
  );
}

export interface ModelProfileCreateRequest {
  path: "/api/v1/model-profiles";
  body: Record<string, unknown>;
}

export interface ModelProfileUpdateRequest {
  path: string;
  body: Record<string, unknown>;
}

/** The patch fields Core accepts; the CLI's `PATCH_KEYS` is the reference. */
const PATCH_KEYS = [
  "expected_revision",
  "name",
  "base_url",
  "model",
  "embedding_model",
  "embedding_provider_kind",
  "embedding_base_url",
  "embedding_dimensions",
  "api_key",
  "clear_api_key",
  "timeout_seconds",
  "max_concurrency",
  "context_window",
  "disable_thinking",
  "output_token_parameter",
] as const;

function optionalChainText(value: unknown, max: number): boolean {
  return value === undefined || boundedValue(value, max);
}

function nullableText(value: unknown, max: number): boolean {
  return value === undefined || value === null || boundedValue(value, max);
}

function optionalChainInteger(
  value: unknown,
  min: number,
  max: number,
): boolean {
  return (
    value === undefined ||
    value === null ||
    (typeof value === "number" &&
      Number.isSafeInteger(value) &&
      value >= min &&
      value <= max)
  );
}

/**
 * Validate and normalise a model-profile patch, mirroring the CLI's
 * `model-write-flows.ts` `validatePatch`.
 *
 * The Web posted `ModelProfilePatch` straight to `PATCH /model-profiles/{id}`,
 * so an extra key or an out-of-range value was only discovered if Core happened
 * to reject it. Both surfaces must judge the patch identically: `expected_revision`
 * is a required positive integer (the CAS guard), the allow-list is closed, and
 * `api_key` and `clear_api_key` are mutually exclusive.
 */
export function buildModelProfileUpdateRequest(
  profileId: string,
  patch: unknown,
): ModelProfileUpdateRequest {
  if (
    typeof profileId !== "string" ||
    !boundedValue(profileId, 128) ||
    !profileId
  )
    throw new ModelProfileInputError("profile_id_invalid");
  if (typeof patch !== "object" || patch === null || Array.isArray(patch))
    throw new ModelProfileInputError("patch_invalid");
  const value = patch as Record<string, unknown>;
  for (const key of Object.keys(value)) {
    if (!(PATCH_KEYS as readonly string[]).includes(key))
      throw new ModelProfileInputError(`unknown_field:${key}`);
  }
  if (
    !Number.isSafeInteger(value.expected_revision) ||
    (value.expected_revision as number) < 1
  )
    throw new ModelProfileInputError("expected_revision_invalid");
  if (
    value.name !== undefined &&
    !(
      typeof value.name === "string" &&
      value.name.length >= 1 &&
      value.name.length <= 128
    )
  )
    throw new ModelProfileInputError("name_invalid");
  if (!nullableText(value.base_url, 2_048))
    throw new ModelProfileInputError("base_url_invalid");
  if (
    value.model !== undefined &&
    !(
      typeof value.model === "string" &&
      value.model.length >= 1 &&
      value.model.length <= 256
    )
  )
    throw new ModelProfileInputError("model_invalid");
  if (!nullableText(value.embedding_model, 256))
    throw new ModelProfileInputError("embedding_model_invalid");
  if (
    value.embedding_provider_kind !== undefined &&
    value.embedding_provider_kind !== null &&
    !PROVIDERS.includes(value.embedding_provider_kind as ProviderKind)
  )
    throw new ModelProfileInputError("embedding_provider_kind_invalid");
  if (!nullableText(value.embedding_base_url, 2_048))
    throw new ModelProfileInputError("embedding_base_url_invalid");
  if (!optionalChainInteger(value.embedding_dimensions, 1, 1_000_000))
    throw new ModelProfileInputError("embedding_dimensions_invalid");
  if (
    value.clear_api_key !== undefined &&
    typeof value.clear_api_key !== "boolean"
  )
    throw new ModelProfileInputError("clear_api_key_invalid");
  if (!optionalChainInteger(value.timeout_seconds, 1, 600))
    throw new ModelProfileInputError("timeout_seconds_invalid");
  if (!optionalChainInteger(value.max_concurrency, 1, 32))
    throw new ModelProfileInputError("max_concurrency_invalid");
  if (!optionalChainInteger(value.context_window, 1_024, 10_000_000))
    throw new ModelProfileInputError("context_window_invalid");
  if (
    value.disable_thinking !== undefined &&
    typeof value.disable_thinking !== "boolean"
  )
    throw new ModelProfileInputError("disable_thinking_invalid");
  if (!optionalOutputTokenParameter(value.output_token_parameter))
    throw new ModelProfileInputError("output_token_parameter_invalid");
  if (value.clear_api_key === true && value.api_key !== undefined)
    throw new ModelProfileInputError("api_key_and_clear_conflict");
  if (!optionalChainText(value.api_key, 16_384))
    throw new ModelProfileInputError("api_key_invalid");
  return {
    path: `/api/v1/model-profiles/${encodeURIComponent(profileId)}`,
    body: { ...value },
  };
}

/**
 * Build the create body, or throw with the specific field that is wrong.
 *
 * `creation_operation_id` is added here (the caller supplies its durable id),
 * and `api_key` is forwarded only when the caller resolved one, matching the
 * CLI's `{...config, creation_operation_id, [api_key]}` shape.
 */
export function buildModelProfileCreateRequest(
  input: ModelProfileInput,
  creationOperationId: string,
): ModelProfileCreateRequest {
  if (
    typeof creationOperationId !== "string" ||
    creationOperationId.length !== 36
  )
    throw new ModelProfileInputError("creation_operation_id_invalid");
  const config: Record<string, unknown> = {};
  const source = input as unknown as Record<string, unknown>;
  for (const key of CONFIG_KEYS) {
    const value = source[key];
    if (value !== undefined) config[key] = value;
  }
  // The CLI refuses a body with any key outside its allow-list; silently
  // dropping a stray key would let the UI accept an input the CLI rejects.
  for (const key of Object.keys(source)) {
    if ((CONFIG_KEYS as readonly string[]).includes(key)) continue;
    if (key === "api_key" || key === "creation_operation_id") continue;
    throw new ModelProfileInputError(`unknown_field:${key}`);
  }
  if ("creation_operation_id" in source)
    throw new ModelProfileInputError("creation_operation_id_not_allowed");
  if (!text(config.name, 128)) throw new ModelProfileInputError("name_invalid");
  if (!PROVIDERS.includes(config.provider_kind as ProviderKind))
    throw new ModelProfileInputError("provider_kind_invalid");
  if (!text(config.model, 256))
    throw new ModelProfileInputError("model_invalid");
  if (!optionalText(config.base_url, 2_048))
    throw new ModelProfileInputError("base_url_invalid");
  if (!optionalText(config.embedding_model, 256))
    throw new ModelProfileInputError("embedding_model_invalid");
  if (
    config.embedding_provider_kind !== undefined &&
    config.embedding_provider_kind !== null &&
    !PROVIDERS.includes(config.embedding_provider_kind as ProviderKind)
  )
    throw new ModelProfileInputError("embedding_provider_kind_invalid");
  if (!optionalText(config.embedding_base_url, 2_048))
    throw new ModelProfileInputError("embedding_base_url_invalid");
  if (!optionalInteger(config.embedding_dimensions, 1, 1_000_000))
    throw new ModelProfileInputError("embedding_dimensions_invalid");
  if (!optionalInteger(config.timeout_seconds, 1, 600))
    throw new ModelProfileInputError("timeout_seconds_invalid");
  if (!optionalInteger(config.max_concurrency, 1, 32))
    throw new ModelProfileInputError("max_concurrency_invalid");
  if (!optionalInteger(config.context_window, 1_024, 10_000_000))
    throw new ModelProfileInputError("context_window_invalid");
  if (
    config.disable_thinking !== undefined &&
    typeof config.disable_thinking !== "boolean"
  )
    throw new ModelProfileInputError("disable_thinking_invalid");
  if (!optionalOutputTokenParameter(config.output_token_parameter))
    throw new ModelProfileInputError("output_token_parameter_invalid");
  return {
    path: "/api/v1/model-profiles",
    body: {
      ...config,
      creation_operation_id: creationOperationId,
      ...(input.api_key === undefined ? {} : { api_key: input.api_key }),
    },
  };
}

/**
 * Validate a model-profile write receipt before it becomes UI state.
 *
 * A create, activate or edit returns the resulting `ModelProfile` on 2xx. The
 * CLI refuses to trust that body blindly: `headless-flows.ts` validates it with
 * `validateModelProfile` (an allow-listed `ModelProfile` plus a bounded
 * `profile_id`, an integer `revision` and `revision >= 1`) and, on failure,
 * reports `model_receipt_invalid` / `model_profile_receipt_invalid` rather than
 * a committed write. The Web previously stored `response.data` straight into
 * `profiles` state via `mergeProfile`, so a malformed 200 became an "active"
 * profile the user could then trust.
 *
 * Returns null for a malformed body; callers must treat that as an unknown
 * result, not a saved profile. This mirrors the CLI's field rules exactly so a
 * body Core gives the CLI is judged the same way here.
 */
export function parseModelProfileReceipt(value: unknown): ModelProfile | null {
  if (typeof value !== "object" || value === null || Array.isArray(value))
    return null;
  const profile = value as Record<string, unknown>;
  // The subset `core-responses.ts` `validateModelProfiles` checks, in the same
  // order, plus the two extras `validateModelProfile` adds on top.
  if (
    !boundedValue(profile.name, 256) ||
    !PROVIDERS.includes(profile.provider_kind as ProviderKind) ||
    !boundedValue(profile.model, 256) ||
    typeof profile.has_api_key !== "boolean" ||
    typeof profile.is_active !== "boolean" ||
    (profile.credential_state !== undefined &&
      profile.credential_state !== null &&
      !(CREDENTIAL_STATES as readonly unknown[]).includes(
        profile.credential_state,
      )) ||
    !boundedValue(profile.profile_id, 128) ||
    profile.profile_id.length < 1 ||
    !Number.isSafeInteger(profile.revision) ||
    (profile.revision as number) < 1
  )
    return null;
  return profile as unknown as ModelProfile;
}
