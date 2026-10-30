import { describe, expect, it } from "vitest";
import {
  buildMemoryAddRequest,
  MemoryAddInputError,
  memoryAddStatusPath,
  parseMemoryAddReceipt,
} from "./memory-add-operation";

const ID = "11111111-2222-4333-8444-555555555555";

describe("memory add request", () => {
  it("sends exactly request_id, text and the fixed scope", () => {
    expect(
      buildMemoryAddRequest({
        request_id: ID,
        text: "raw memory",
        scope: "all-apps",
      }),
    ).toEqual({
      path: "/api/v2/memories",
      body: { request_id: ID, text: "raw memory", scope: "all-apps" },
    });
  });

  it("refuses a bad request id or empty text", () => {
    expect(() =>
      buildMemoryAddRequest({
        request_id: "nope",
        text: "x",
        scope: "all-apps",
      }),
    ).toThrow("request_id_invalid");
    expect(() =>
      buildMemoryAddRequest({
        request_id: ID,
        text: "   ",
        scope: "all-apps",
      }),
    ).toThrow(MemoryAddInputError);
  });
});

describe("memory add status path", () => {
  it("addresses the addition by its request id", () => {
    expect(memoryAddStatusPath(ID)).toBe(`/api/v2/memories/additions/${ID}`);
  });

  it("refuses a non-UUID request id", () => {
    for (const value of ["nope", "", `${ID}/../x`]) {
      expect(() => memoryAddStatusPath(value)).toThrow(MemoryAddInputError);
      expect(() => memoryAddStatusPath(value)).toThrow("request_id_invalid");
    }
  });
});

describe("memory add receipt validation", () => {
  function receipt(patch: Record<string, unknown> = {}) {
    return {
      evidence_id: ID,
      text: "raw memory",
      scope: "all-apps",
      created_at: "2026-09-13T00:00:00+00:00",
      state: "pending",
      task_id: null,
      memory_ids: [],
      ...patch,
    };
  }

  it("accepts a well-formed receipt", () => {
    expect(parseMemoryAddReceipt(receipt())?.state).toBe("pending");
  });

  it("refuses a non-UUID evidence id or an unknown state", () => {
    expect(parseMemoryAddReceipt(receipt({ evidence_id: "nope" }))).toBeNull();
    expect(parseMemoryAddReceipt(receipt({ state: "invented" }))).toBeNull();
  });

  it("refuses a wrong scope or malformed memory_ids", () => {
    expect(parseMemoryAddReceipt(receipt({ scope: "private" }))).toBeNull();
    expect(parseMemoryAddReceipt(receipt({ memory_ids: [1] }))).toBeNull();
    expect(
      parseMemoryAddReceipt(receipt({ memory_ids: undefined })),
    ).toBeNull();
  });

  it("refuses a non-object body", () => {
    for (const value of [null, [], "ok", 7]) {
      expect(parseMemoryAddReceipt(value)).toBeNull();
    }
  });
});
