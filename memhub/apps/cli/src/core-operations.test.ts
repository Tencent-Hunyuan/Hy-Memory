import { beforeEach, describe, expect, it, vi } from "vitest";

import { createCoreOperations } from "./core-operations.js";

const transport = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
}));

vi.mock("axios", () => ({
  default: { create: vi.fn(() => transport) },
}));

const ATTACHMENT = {
  host: "127.0.0.1" as const,
  port: 8765,
  apiToken: "synthetic-runtime-token",
};

/** Every mutation verb carries the same 4xx/5xx classifier; check them all.
 *
 * Each verb only accepts the paths its own allowlist names, so the cases use
 * one legal path per verb rather than sharing a single one. */
const VERBS = [
  ["post", "/api/v1/model-profiles"],
  ["put", "/api/v2/memories/mem-1"],
  ["patch", "/api/v1/model-profiles/profile-1"],
  ["delete", "/api/v1/model-profiles/profile-1"],
] as const;

function operations() {
  return createCoreOperations(ATTACHMENT);
}

function respond(status: number, data: unknown) {
  for (const [verb] of VERBS) {
    transport[verb].mockResolvedValue({ status, data });
  }
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("5xx keeps the refusal Core named", () => {
  it.each(VERBS)(
    "%s surfaces secure_storage_unavailable instead of a bare status",
    async (verb, path) => {
      respond(503, { detail: "secure_storage_unavailable" });

      const outcome = await operations()[verb](path, {});

      expect(outcome).toEqual({
        state: "unknown",
        code: "secure_storage_unavailable_result_unknown",
      });
    },
  );

  it("falls back to the bare status when the body names nothing", async () => {
    respond(500, { detail: "  not a code  " });

    const outcome = await operations().post("/api/v1/model-profiles", {});

    expect(outcome).toEqual({
      state: "unknown",
      code: "http_500_result_unknown",
    });
  });

  it("falls back when the body is not an object", async () => {
    respond(502, "Bad Gateway");

    const outcome = await operations().post("/api/v1/model-profiles", {});

    expect(outcome).toEqual({
      state: "unknown",
      code: "http_502_result_unknown",
    });
  });

  it.each([
    "127.0.0.1:8765",
    "localhost:8765",
    "C:\\Users\\someone\\token",
    "http://example.test/path",
  ])("never promotes an address or path to a code: %s", async (detail) => {
    respond(503, { detail });

    const outcome = await operations().post("/api/v1/model-profiles", {});

    expect(outcome).toEqual({
      state: "unknown",
      code: "http_503_result_unknown",
    });
  });

  it("rejects a detail longer than 128 characters", async () => {
    respond(503, { detail: `a_${"b".repeat(200)}` });

    const outcome = await operations().post("/api/v1/model-profiles", {});

    expect(outcome).toEqual({
      state: "unknown",
      code: "http_503_result_unknown",
    });
  });

  it("still reports state unknown, so a write is never read as accepted", async () => {
    respond(503, { detail: "secure_storage_unavailable" });

    const outcome = await operations().post("/api/v1/model-profiles", {});

    expect(outcome.state).toBe("unknown");
  });
});

describe("4xx behaviour is unchanged", () => {
  it("keeps the detail code for a rejected request", async () => {
    respond(409, { detail: "model_creation_operation_conflict" });

    const outcome = await operations().post("/api/v1/model-profiles", {});

    expect(outcome).toEqual({
      state: "rejected",
      code: "model_creation_operation_conflict",
      http_status: 409,
      body: { detail: "model_creation_operation_conflict" },
    });
  });

  it("keeps the embedding-switch 503 as a rejection", async () => {
    respond(503, { detail: "sdk_reembed_unavailable" });

    const outcome = await operations().post("/api/v1/embedding-switches", {});

    expect(outcome).toMatchObject({
      state: "rejected",
      code: "sdk_reembed_unavailable",
    });
  });
});
