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

  it("adapts raw only for the shared selection identity", () => {
    expect(rawDetail(record)).toMatchObject({
      memory: { memory_id: "raw-1", content: record.content },
      source_id: "cursor",
      evidence: [],
      history: [],
    });
  });
});
