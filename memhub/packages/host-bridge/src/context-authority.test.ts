import type { ContextItem, ContextSearchResponse } from "@one-memory/contracts";
import { describe, expect, it } from "vitest";
import { isContextResponseAuthorized } from "./context-protocol.js";
import type { VerifiedHostIdentity } from "./host-grants.js";

function identity(
  overrides: Partial<VerifiedHostIdentity> = {},
): VerifiedHostIdentity {
  return {
    installationId: "11111111-1111-4111-8111-111111111111",
    hostId: "cursor",
    revision: 1,
    status: "approved",
    allowedTools: ["om_conversations", "om_recall"],
    allowedScopes: ["all-apps", "source:codex"],
    ...overrides,
  } as VerifiedHostIdentity;
}

function response(items: ContextItem[]): ContextSearchResponse {
  return {
    schema_version: 1,
    request_id: "22222222-2222-4222-8222-222222222222",
    state: "grounded",
    paths: {
      raw: {
        status: "ok",
        reason: null,
        elapsed_ms: 1,
        returned_count: items.length,
        truncated: false,
        truncated_by: [],
      },
      memory: {
        status: "not_requested",
        reason: null,
        elapsed_ms: 0,
        returned_count: 0,
        truncated: false,
        truncated_by: [],
      },
    },
    items,
    used_characters: 10,
    truncated: false,
    total_ms: 1,
    adoption: "unknown",
  };
}

function raw(sourceId: string): ContextItem {
  return {
    kind: "raw",
    source_id: sourceId,
    conversation_id: "conv-1",
    anchor_evidence_id: "ev-1",
    messages: [],
    truncated: false,
  };
}

function memory(): ContextItem {
  return {
    kind: "memory",
    memory_id: "m1",
    revision: 1,
    content: "c",
    memory_kind: "chat",
    updated_at: "2026-01-01T00:00:00Z",
    evidence_ids: [],
    matched_by: ["lexical"],
    truncated: false,
  };
}

describe("context response authority", () => {
  it("accepts a raw item from a source the host was granted", () => {
    // The write side stores under `source:<id>`, so a read that did not check
    // the same scope would return rows from sources this host never granted.
    expect(
      isContextResponseAuthorized(response([raw("codex")]), identity()),
    ).toBe(true);
  });

  it("rejects a raw item from a source the host was not granted", () => {
    // This is the symmetry rule: granted for one source, shown another's rows
    // is a silent leak rather than an error anyone would notice.
    expect(
      isContextResponseAuthorized(response([raw("cursor")]), identity()),
    ).toBe(false);
  });

  it("rejects raw when the conversations tool is missing", () => {
    expect(
      isContextResponseAuthorized(
        response([raw("codex")]),
        identity({ allowedTools: ["om_recall"] }),
      ),
    ).toBe(false);
  });

  it("rejects raw when all-apps is missing even if the exact source is granted", () => {
    // The two are independent by design: a precise source grant alone is not
    // meant to open every conversation.
    expect(
      isContextResponseAuthorized(
        response([raw("codex")]),
        identity({
          allowedScopes: ["source:codex"],
        }),
      ),
    ).toBe(false);
  });

  it("rejects every item when any one of them is out of scope", () => {
    // Not "filter the bad one out": a response that mixed in one foreign row
    // has failed its contract, and passing the rest would hide that.
    expect(
      isContextResponseAuthorized(
        response([raw("codex"), raw("cursor")]),
        identity(),
      ),
    ).toBe(false);
  });

  it("checks memory items against memory authority, not source scopes", () => {
    expect(isContextResponseAuthorized(response([memory()]), identity())).toBe(
      true,
    );
    expect(
      isContextResponseAuthorized(
        response([memory()]),
        identity({ allowedTools: ["om_conversations"] }),
      ),
    ).toBe(false);
  });

  it("accepts an empty response", () => {
    // No items is not unauthorized; empty and denied are different things.
    expect(isContextResponseAuthorized(response([]), identity())).toBe(true);
  });
});
