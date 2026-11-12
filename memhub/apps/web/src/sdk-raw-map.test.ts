import { describe, expect, it } from "vitest";
import { rawDetail, rawLibraryPage, rawSearchPage } from "./sdk-raw-map";

const record = {
  raw_id: "raw-1",
  content: "[user]: Keep the original words.",
  user_id: "local",
  agent_id: "all-apps",
  session_id: "session-1",
  source: "cursor",
  memory_at: 1_761_000_000,
  gmt_created: 1_761_000_100,
  custom: {},
  messages: [{ role: "user", content: "Keep the original words." }],
};

describe("raw map projection", () => {
  it("projects a raw page into the shared three-column library shape", () => {
    const page = rawLibraryPage({
      items: [record],
      total: 3,
      offset: 0,
      limit: 20,
    });

    expect(page.items[0]).toMatchObject({
      memory_id: "raw-1",
      content: record.content,
      status: "confirmed",
    });
    expect(page.source_ids_by_memory).toEqual({ "raw-1": ["cursor"] });
    expect(page.source_time_by_memory?.["raw-1"]?.basis).toBe("conversation");
    expect(page.total).toBe(3);
  });

  it("keeps search results read-only and page-local", () => {
    const page = rawSearchPage([
      {
        kind: "raw",
        raw_id: "raw-search",
        content: "Matched original",
        source: "claude",
        session_id: "session-2",
        memory_at: "2026-11-03T09:00:00Z",
        score: 0.8,
        truncated: false,
      },
    ]);

    expect(page).toMatchObject({ total: 1, offset: 0, limit: 1 });
    expect(page.items[0].memory_id).toBe("raw-search");
  });

  it("falls back to import time when conversation time is unavailable", () => {
    const page = rawLibraryPage({
      items: [{ ...record, memory_at: null }],
      total: 1,
      offset: 0,
      limit: 20,
    });

    expect(page.source_time_by_memory?.["raw-1"]).toEqual({
      at: new Date(record.gmt_created * 1_000).toISOString(),
      basis: "import",
    });
    expect(rawDetail({ ...record, memory_at: null }).sdk_memory_at).toBe(
      new Date(record.gmt_created * 1_000).toISOString(),
    );
  });

  it("adapts raw only for the shared selection identity", () => {
    expect(rawDetail(record)).toMatchObject({
      memory: { memory_id: "raw-1", content: record.content },
      source_id: "cursor",
      evidence: [],
      history: [],
    });
  });

  it("uses a turn identity while retaining the parent raw id", () => {
    const turn = {
      ...record,
      raw_id: "raw-batch",
      turn_id: "raw-turn:batch:1",
      turn_index: 1,
      turn_count: 2,
      content: "[user]: second question\n[assistant]: second answer",
      messages: [
        { role: "user", content: "second question" },
        { role: "assistant", content: "second answer" },
      ],
    };

    expect(
      rawLibraryPage({ items: [turn], total: 2, offset: 0, limit: 20 }),
    ).toMatchObject({
      items: [{ memory_id: "raw-turn:batch:1", content: turn.content }],
      source_ids_by_memory: { "raw-turn:batch:1": ["cursor"] },
    });
    expect(rawDetail(turn).memory.memory_id).toBe("raw-turn:batch:1");
    expect(turn.raw_id).toBe("raw-batch");
  });
});
