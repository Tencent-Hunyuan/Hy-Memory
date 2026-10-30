import { describe, expect, it, vi } from "vitest";

import type { CoreReader } from "./core-client.js";
import type { CoreOperations } from "./core-operations.js";
import {
  modelProbeExitCode,
  readModelProbeReceipt,
  startModelProbe,
} from "./model-probe-flow.js";

const PROFILE_ID = "22222222-2222-4222-8222-222222222222";

const PROFILE = {
  profile_id: PROFILE_ID,
  name: "Synthetic",
  provider_kind: "openai_compatible",
  base_url: "https://example.invalid/v1",
  model: "synthetic-model",
  embedding_model: null,
  embedding_provider_kind: null,
  embedding_base_url: null,
  embedding_dimensions: null,
  has_api_key: true,
  credential_state: "available",
  timeout_seconds: 30,
  max_concurrency: 1,
  context_window: null,
  disable_thinking: true,
  capabilities: {
    endpoint_reachable: false,
    model_available: false,
    structured_output: false,
    embedding_available: false,
  },
  revision: 3,
  is_active: false,
  created_at: "2026-09-19T00:00:00Z",
  updated_at: "2026-09-19T00:00:00Z",
};

function notFound(operationId: string) {
  return {
    schema_version: 1,
    operation_id: operationId,
    profile_id: null,
    expected_profile_revision: null,
    state: "not_found",
    result: null,
    code: null,
    stale: null,
  };
}

function completed(operationId: string) {
  return {
    schema_version: 1,
    operation_id: operationId,
    profile_id: PROFILE_ID,
    expected_profile_revision: 3,
    state: "completed",
    result: {
      profile_id: PROFILE_ID,
      profile_revision: 3,
      input_profile_revision: 3,
      stages: [
        {
          stage: "authentication",
          ok: false,
          code: "missing_api_key",
          latency_ms: 0,
          embedding_dimensions: null,
        },
      ],
      capabilities: {
        endpoint_reachable: false,
        model_available: false,
        structured_output: false,
        embedding_available: false,
      },
    },
    code: null,
    stale: false,
  };
}

const OPERATION_ID = "33333333-3333-4333-8333-333333333333";

/** Reader that answers the profile list, then the queued operation reads. */
function readerWith(...operationReads: unknown[]) {
  const reader = vi.fn(async (path: string) => {
    if (path === "/api/v1/model-profiles") {
      return { state: "ok", data: [PROFILE] };
    }
    return operationReads.shift() ?? { state: "not_found", code: "http_404" };
  });
  return reader;
}

describe("model probe operation flow", () => {
  it("probes the exact current profile revision once support is proven", async () => {
    const reader = readerWith({ state: "ok", data: notFound(OPERATION_ID) });
    const post = vi
      .fn()
      .mockResolvedValue({ state: "committed", data: completed(OPERATION_ID) });

    const result = await startModelProbe(
      reader as unknown as CoreReader,
      { post } as CoreOperations,
      PROFILE_ID,
      OPERATION_ID,
    );

    expect(result.state).toBe("completed");
    expect(reader).toHaveBeenCalledWith(
      `/api/v1/model-probe-operations/${OPERATION_ID}`,
    );
    expect(post).toHaveBeenCalledWith(
      `/api/v1/model-profiles/${PROFILE_ID}/probe`,
      { operation_id: OPERATION_ID, expected_profile_revision: 3 },
    );
  });

  it("generates its own operation id and treats a pending probe as submitted", async () => {
    const post = vi.fn(
      async (_path: string, body: Record<string, unknown>) => ({
        state: "committed",
        data: {
          ...notFound(String(body.operation_id)),
          profile_id: PROFILE_ID,
          expected_profile_revision: 3,
          state: "pending",
          stale: false,
        },
      }),
    );
    const reader = vi.fn(async (path: string) =>
      path === "/api/v1/model-profiles"
        ? { state: "ok", data: [PROFILE] }
        : {
            state: "ok",
            data: notFound(path.split("/").at(-1) ?? ""),
          },
    );
    const result = await startModelProbe(
      reader as unknown as CoreReader,
      { post } as unknown as CoreOperations,
      PROFILE_ID,
    );
    expect(result.state).toBe("pending");
    expect(result.operation_id).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/,
    );
    expect(modelProbeExitCode(result)).toBe(0);
  });

  it("refuses an unknown profile without posting", async () => {
    const post = vi.fn();
    const result = await startModelProbe(
      readerWith() as unknown as CoreReader,
      { post } as CoreOperations,
      "44444444-4444-4444-8444-444444444444",
      OPERATION_ID,
    );
    expect(result).toMatchObject({
      state: "not_found",
      code: "model_profile_not_found",
    });
    expect(post).not.toHaveBeenCalled();
  });

  it("does not post when the Core lacks the exact operation contract", async () => {
    const post = vi.fn();
    const result = await startModelProbe(
      readerWith({
        state: "not_found",
        code: "http_404",
      }) as unknown as CoreReader,
      { post } as CoreOperations,
      PROFILE_ID,
      OPERATION_ID,
    );
    expect(result).toMatchObject({
      state: "disabled",
      code: "model_probe_operations_unsupported",
    });
    expect(post).not.toHaveBeenCalled();
  });

  it("does not post when a legacy Core returns a non-operation body", async () => {
    const post = vi.fn();
    const result = await startModelProbe(
      readerWith({
        state: "ok",
        data: { detail: "legacy_route_shape" },
      }) as unknown as CoreReader,
      { post } as CoreOperations,
      PROFILE_ID,
      OPERATION_ID,
    );
    expect(result).toMatchObject({
      state: "disabled",
      code: "model_probe_operations_unsupported",
    });
    expect(post).not.toHaveBeenCalled();
  });

  it("reconciles an unknown POST by GET and never treats not_found as replay permission", async () => {
    const reader = readerWith(
      { state: "ok", data: notFound(OPERATION_ID) },
      { state: "ok", data: notFound(OPERATION_ID) },
    );
    const post = vi.fn().mockResolvedValue({
      state: "unknown",
      code: "transport_result_unknown",
    });
    const result = await startModelProbe(
      reader as unknown as CoreReader,
      { post } as CoreOperations,
      PROFILE_ID,
      OPERATION_ID,
    );
    expect(result).toMatchObject({
      state: "unknown",
      code: "model_probe_receipt_not_found_after_attempt",
    });
    expect(post).toHaveBeenCalledTimes(1);
  });

  it("strictly rejects a receipt whose result revision is not bound to the input", async () => {
    const operationId = "11111111-1111-4111-8111-111111111111";
    const malformed = completed(operationId);
    malformed.result.profile_revision = 8;
    const result = await readModelProbeReceipt(
      vi.fn().mockResolvedValue({
        state: "ok",
        data: malformed,
      }) as CoreReader,
      operationId,
    );
    expect(result).toMatchObject({
      state: "unknown",
      code: "model_probe_receipt_invalid",
    });
  });
});
