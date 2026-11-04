import { describe, expect, it } from "vitest";

import {
  CORE_SERVICE,
  type HomeProjectionV1,
  type RecallReceipt,
} from "./index";

describe("Core contract", () => {
  it("has one stable service identity", () => {
    expect(CORE_SERVICE).toBe("one-memory-core");
  });

  it("types a raw-mode recall receipt: excerpts, not memories", () => {
    // Compile-time check as much as a runtime one: this literal must satisfy
    // the receipt type Core sends when it falls back to raw conversations.
    const receipt: RecallReceipt = {
      state: "grounded",
      query: "deploy plan",
      scope: "all-apps",
      mode: "raw",
      fallback_reason: "model_unavailable",
      excluded_sources: ["codex"],
      hits: [
        {
          memory_id: "raw-1",
          content: "we agreed to deploy on Friday",
          content_format: "raw",
          kind: "raw",
          source: "cursor",
          session_id: "session-1",
          scopes: [],
          evidence_ids: [],
          updated_at: "2026-10-27T00:00:00Z",
          score: 0.5,
        },
      ],
      used_characters: 29,
      truncated: false,
    };
    expect(receipt.mode).toBe("raw");
    expect(receipt.hits[0].evidence_ids).toEqual([]);
  });

  it("types the discriminated home projection without raw payload fields", () => {
    const projection: HomeProjectionV1 = {
      projection_version: 1,
      user_id: "local",
      effective_mode: "lite",
      capabilities: {
        content_plane: "raw",
        generation_required: false,
        graph_enabled: false,
        evolve_available: false,
      },
      visible_count: 1,
      display_state: "excerpt",
      latest: {
        type: "raw",
        raw_id: "raw-1",
        session_id: "session-1",
        display_state: "excerpt",
        time: null,
        time_basis: null,
        excerpt: "ship Friday",
        truncated: false,
        source_id: "cursor",
      },
      observed_at: "2026-11-03T00:00:00Z",
    };
    expect(projection.latest?.type).toBe("raw");
  });
});
