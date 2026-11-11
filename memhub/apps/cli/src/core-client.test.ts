import { beforeEach, describe, expect, it, vi } from "vitest";

import { submitMemoryWrite } from "./core-client.js";

const post = vi.hoisted(() => vi.fn());

vi.mock("axios", () => ({ default: { post } }));

const ATTACHMENT = {
  host: "127.0.0.1",
  port: 8765,
  apiToken: "synthetic-runtime-token",
};

const REQUEST = {
  session: "session-1",
  source: "claude-code",
  request_id: "request-1",
  messages: [{ role: "user" as const, content: "hello" }],
};

function respond(status: number, data: unknown) {
  post.mockResolvedValue({ status, data });
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("a 5xx write keeps the refusal Core named", () => {
  it("surfaces secure_storage_unavailable", async () => {
    respond(503, { detail: "secure_storage_unavailable" });

    const outcome = await submitMemoryWrite(ATTACHMENT, REQUEST);

    expect(outcome).toEqual({
      state: "unknown",
      code: "secure_storage_unavailable_result_unknown",
    });
  });

  it("falls back to the bare status when the body names nothing", async () => {
    respond(500, {});

    const outcome = await submitMemoryWrite(ATTACHMENT, REQUEST);

    expect(outcome).toEqual({
      state: "unknown",
      code: "http_500_result_unknown",
    });
  });

  it("never promotes a bind address to a code", async () => {
    respond(503, { detail: "127.0.0.1:8765" });

    const outcome = await submitMemoryWrite(ATTACHMENT, REQUEST);

    expect(outcome).toEqual({
      state: "unknown",
      code: "http_503_result_unknown",
    });
  });

  it("stays unknown, so the same request id is still the only safe retry", async () => {
    respond(503, { detail: "secure_storage_unavailable" });

    const outcome = await submitMemoryWrite(ATTACHMENT, REQUEST);

    expect(outcome.state).toBe("unknown");
  });
});

describe("other write outcomes are unchanged", () => {
  it("accepts a 2xx", async () => {
    respond(200, { request_id: "request-1", state: "pending" });

    const outcome = await submitMemoryWrite(ATTACHMENT, REQUEST);

    expect(outcome.state).toBe("accepted");
  });

  it("rejects a 4xx with the detail code", async () => {
    respond(409, { detail: "memory_write_conflict" });

    const outcome = await submitMemoryWrite(ATTACHMENT, REQUEST);

    expect(outcome).toEqual({
      state: "rejected",
      code: "memory_write_conflict",
      http_status: 409,
    });
  });

  it("reports a transport failure as unknown", async () => {
    post.mockRejectedValue(new Error("ECONNREFUSED"));

    const outcome = await submitMemoryWrite(ATTACHMENT, REQUEST);

    expect(outcome).toEqual({
      state: "unknown",
      code: "transport_result_unknown",
    });
  });
});
