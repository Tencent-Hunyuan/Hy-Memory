/**
 * Model-profile writes the Web console had but the CLI did not:
 * activate (`POST /model-profiles/{id}/activate`) and edit
 * (`PATCH /model-profiles/{id}`).
 *
 * Core owns the semantics. Both run directly: activation is reversible (activate
 * another profile) and an edit is guarded by `expected_revision`, the
 * compare-and-swap Core requires, mirroring the Web console's
 * `ModelProfilePatch`.
 */

import type { ModelProfile } from "@one-memory/contracts";
import {
  buildModelProfileUpdateRequest,
  ModelProfileInputError,
  parseModelProfileReceipt,
} from "@one-memory/operations";
import type { CoreOperations, CorePatchOperations } from "./core-operations.js";
import { PRODUCT } from "./version.js";

const PROFILE_ID = /^[A-Za-z0-9][\w.-]{0,127}$/;

export type ModelWriteDocument =
  | {
      schema_version: 1;
      product: string;
      state: "committed";
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

function invalid(code: string): ModelWriteDocument {
  return { schema_version: 1, product: PRODUCT, state: "invalid", code };
}

function committed(
  operationId: string,
  profile: ModelProfile,
): ModelWriteDocument {
  return {
    schema_version: 1,
    product: PRODUCT,
    state: "committed",
    operation_id: operationId,
    receipt: projectProfile(profile),
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

/** Make `profileId` the active profile. Activation has no request body, so
 * there is no revision for Core to check. */
export async function activateModelProfile(
  profileId: string,
  operations: CoreOperations,
): Promise<ModelWriteDocument> {
  if (!PROFILE_ID.test(profileId)) {
    return invalid("model_profile_activate_input_invalid");
  }
  const result = await operations.post<unknown>(
    `/api/v1/model-profiles/${encodeURIComponent(profileId)}/activate`,
    {},
  );
  return modelProfileResult(profileId, result);
}

/** Apply a bounded patch (carrying `expected_revision`) to one profile; the
 * key, when given, comes from a controlled file and never from the patch. */
export async function editModelProfile(
  profileId: string,
  patch: unknown,
  apiKey: string | undefined,
  operations: CorePatchOperations,
): Promise<ModelWriteDocument> {
  if (
    !PROFILE_ID.test(profileId) ||
    patch === null ||
    typeof patch !== "object" ||
    Array.isArray(patch) ||
    "api_key" in patch
  ) {
    return invalid("model_profile_edit_input_invalid");
  }
  // The shared builder owns patch validation (allow-list, expected_revision
  // CAS, field bounds, api_key/clear_api_key exclusivity).
  let request: ReturnType<typeof buildModelProfileUpdateRequest>;
  try {
    request = buildModelProfileUpdateRequest(profileId, {
      ...(patch as Record<string, unknown>),
      ...(apiKey === undefined ? {} : { api_key: apiKey }),
    });
  } catch (error) {
    if (error instanceof ModelProfileInputError) {
      return invalid("model_profile_edit_input_invalid");
    }
    throw error;
  }
  const result = await operations.patch<unknown>(request.path, request.body);
  return modelProfileResult(profileId, result);
}

function modelProfileResult(
  operationId: string,
  result:
    | Awaited<ReturnType<CoreOperations["post"]>>
    | Awaited<ReturnType<CorePatchOperations["patch"]>>,
): ModelWriteDocument {
  if (result.state === "rejected") {
    return {
      schema_version: 1,
      product: PRODUCT,
      state: "rejected",
      operation_id: operationId,
      code: result.code,
    };
  }
  if (result.state === "unknown") {
    return {
      schema_version: 1,
      product: PRODUCT,
      state: "unknown",
      operation_id: operationId,
      code: result.code,
    };
  }
  // The shared parser is the single judge of a model-profile receipt; null
  // means a malformed body, which must be `unknown`, never a committed write.
  const profile = parseModelProfileReceipt(result.data);
  if (!profile) {
    return {
      schema_version: 1,
      product: PRODUCT,
      state: "unknown",
      operation_id: operationId,
      code: "model_profile_receipt_invalid",
    };
  }
  return committed(operationId, profile);
}

export function modelWriteExitCode(document: ModelWriteDocument): number {
  return document.state === "committed" ? 0 : 1;
}

export function renderModelWriteText(document: ModelWriteDocument): string {
  if (document.state === "committed") {
    return `已保存模型配置：${document.receipt.profile_id}\nrevision=${document.receipt.revision}\n`;
  }
  return `模型写操作未完成：${document.state}（${document.code}）\n`;
}
