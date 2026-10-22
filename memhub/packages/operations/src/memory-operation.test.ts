import { describe, expect, it } from "vitest";
import {
  buildMemoryActionRequest,
  buildMemoryCorrectRequest,
  buildMemoryCreateRequest,
  MemoryOperationInputError,
  parseMemoryReceipt,
} from "./memory-operation";

const REQUEST = "11111111-2222-4333-8444-555555555555";

describe("memory operation requests", () => {
  it("builds the CLI's create body shape", () => {
    expect(
      buildMemoryCreateRequest(
        {
          content: "remember this",
          scopes: ["all-apps"],
          evidence_ids: ["ev-1"],
          origin: "manual",
        },
        REQUEST,
      ),
    ).toStrictEqual({
      path: "/api/v1/memories",
      body: {
        request_id: REQUEST,
        draft: {
          user_id: "local",
          kind: "chat",
          content: "remember this",
          scopes: ["all-apps"],
          evidence_ids: ["ev-1"],
          origin: "manual",
        },
      },
    });
  });

  it("builds the CLI's correct body, omitting scopes when unchanged", () => {
    expect(
      buildMemoryCorrectRequest("m1", 4, "fixed", undefined, REQUEST).body,
    ).toStrictEqual({
      request_id: REQUEST,
      expected_revision: 4,
      content: "fixed",
    });
    expect(
      buildMemoryCorrectRequest("m1", 4, "fixed", ["s"], REQUEST).body,
    ).toStrictEqual({
      request_id: REQUEST,
      expected_revision: 4,
      content: "fixed",
      scopes: ["s"],
    });
  });

  it("builds the CLI's action bodies", () => {
    expect(
      buildMemoryActionRequest("m1", "delete", 3, REQUEST).body,
    ).toStrictEqual({ request_id: REQUEST, expected_revision: 3 });
    expect(buildMemoryActionRequest("m1", "undo", 3, REQUEST).path).toBe(
      "/api/v1/memories/m1/actions/undo",
    );
  });

  it("fails closed on an invalid id, revision, request id or action", () => {
    expect(() =>
      buildMemoryActionRequest("bad/id", "undo", 1, REQUEST),
    ).toThrow(MemoryOperationInputError);
    expect(() => buildMemoryActionRequest("m1", "undo", 0, REQUEST)).toThrow(
      "revision_invalid",
    );
    expect(() => buildMemoryActionRequest("m1", "undo", 1, "nope")).toThrow(
      "request_id_invalid",
    );
    expect(() =>
      buildMemoryActionRequest("m1", "explode" as "undo", 1, REQUEST),
    ).toThrow("action_invalid");
  });
});

function memory(patch: Record<string, unknown> = {}) {
  return {
    memory_id: "m1",
    user_id: "local",
    kind: "chat",
    status: "confirmed",
    content: "kept",
    scopes: ["all-apps"],
    evidence_ids: ["ev-1"],
    origin: "manual",
    tags: [],
    revision: 3,
    created_at: "2026-09-13T00:00:00+00:00",
    updated_at: "2026-09-13T00:00:00+00:00",
    deleted_at: null,
    ...patch,
  };
}

describe("memory receipt validation", () => {
  it("accepts a correct receipt that stays in effect and advances one revision", () => {
    expect(
      parseMemoryReceipt(
        memory({ status: "confirmed", revision: 3, content: "fixed" }),
        {
          memoryId: "m1",
          expectedRevision: 2,
          expectedStatus: "confirmed",
        },
      )?.content,
    ).toBe("fixed");
  });

  it("refuses a correct receipt that reports the memory gone or stale", () => {
    for (const patch of [
      { status: "deleted" },
      { revision: 2 },
      { memory_id: "other" },
    ]) {
      expect(
        parseMemoryReceipt(memory({ status: "confirmed", ...patch }), {
          memoryId: "m1",
          expectedRevision: 2,
          expectedStatus: "confirmed",
        }),
      ).toBeNull();
    }
  });

  it("accepts a delete receipt bound to the request", () => {
    expect(
      parseMemoryReceipt(memory({ status: "deleted", revision: 3 }), {
        memoryId: "m1",
        expectedRevision: 2,
        expectedStatus: "deleted",
      })?.status,
    ).toBe("deleted");
  });

  it("accepts an undo receipt that is no longer deleted", () => {
    expect(
      parseMemoryReceipt(memory({ status: "confirmed", revision: 4 }), {
        memoryId: "m1",
        expectedRevision: 3,
        expectedStatus: "confirmed",
      })?.status,
    ).toBe("confirmed");
  });

  it("refuses a receipt for a different id or a non-advancing revision", () => {
    for (const patch of [
      { memory_id: "other" },
      { revision: 2 },
      { revision: 5 },
    ]) {
      expect(
        parseMemoryReceipt(memory({ status: "deleted", ...patch }), {
          memoryId: "m1",
          expectedRevision: 2,
          expectedStatus: "deleted",
        }),
      ).toBeNull();
    }
  });

  it("refuses a status that contradicts the requested action", () => {
    expect(
      parseMemoryReceipt(memory({ status: "confirmed", revision: 3 }), {
        memoryId: "m1",
        expectedRevision: 2,
        expectedStatus: "deleted",
      }),
    ).toBeNull();
    expect(
      parseMemoryReceipt(memory({ status: "deleted", revision: 3 }), {
        memoryId: "m1",
        expectedRevision: 2,
        expectedStatus: "confirmed",
      }),
    ).toBeNull();
  });

  it("refuses a malformed or unknown-enum body instead of promoting it", () => {
    for (const value of [
      null,
      [],
      "ok",
      memory({ status: "active" }),
      memory({ origin: "invented" }),
      memory({ kind: "unknown-kind" }),
      memory({ scopes: [1] }),
    ]) {
      expect(
        parseMemoryReceipt(value, {
          memoryId: "m1",
          expectedRevision: 2,
          expectedStatus: "deleted",
        }),
      ).toBeNull();
    }
  });

  it("refuses a receipt whose evidence_ids would crash the list render", () => {
    // The list panel renders `memory.evidence_ids.length`, so an absent or
    // non-array value must be an unknown receipt, not a render crash. Every
    // other field stays valid (revision 3 == 2 + 1, status `deleted`) so the
    // assertion isolates the evidence_ids check.
    for (const evidence_ids of [undefined, null, "ev-1", [1], {}]) {
      expect(
        parseMemoryReceipt(memory({ evidence_ids, status: "deleted" }), {
          memoryId: "m1",
          expectedRevision: 2,
          expectedStatus: "deleted",
        }),
      ).toBeNull();
    }
    // Control: the same body with a well-formed list is accepted, so the test
    // fails for the field and not because the fixture drifted.
    expect(
      parseMemoryReceipt(
        memory({ evidence_ids: ["ev-1"], status: "deleted" }),
        {
          memoryId: "m1",
          expectedRevision: 2,
          expectedStatus: "deleted",
        },
      ),
    ).not.toBeNull();
  });
});
