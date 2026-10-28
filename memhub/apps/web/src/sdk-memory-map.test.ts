import type { SdkMemoryRecord } from "@one-memory/contracts";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { projectPagedSourceMap } from "./memory-map-model";
import {
  sdkDetail,
  sdkEditableText,
  sdkLibraryPage,
  sdkMemoryRef,
  sdkNamespace,
} from "./sdk-memory-map";

const NOW = Date.UTC(2026, 9, 25) / 1_000;

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW * 1_000);
});
afterEach(() => vi.useRealTimers());

const record = (extra: Partial<SdkMemoryRecord>): SdkMemoryRecord => ({
  memory_id: "m",
  content: "memory",
  layer: "l2_fact",
  status: "active",
  is_latest: true,
  supersedes: [],
  superseded_by: [],
  tags: [],
  owner: null,
  source_raw_ids: [],
  user_id: "local",
  agent_id: "all-apps",
  session_id: "s1",
  source: "cursor",
  memory_at: null,
  gmt_created: NOW - 86_400,
  ...extra,
});

it("keeps the SDK's no-source bucket as a source the list and map can name", () => {
  const page = sdkLibraryPage({
    memories: [record({ memory_id: "d", source: "default" })],
    total: 1,
    limit: 100,
    offset: 0,
  });
  expect(page.source_ids_by_memory).toEqual({ d: ["default"] });
  expect(sdkDetail(record({ source: "default" })).source_id).toBe("default");

  const drawn = projectPagedSourceMap(null, page, [], {
    default: "未识别来源",
  });
  expect(
    drawn.nodes.find((node) => node.node_id === "source:default")?.label,
  ).toBe("未识别来源");
});

it("does not present an impossible conversation time as when it happened", () => {
  const future = Date.UTC(2254, 0, 13) / 1_000;
  const page = sdkLibraryPage({
    memories: [record({ memory_id: "f", memory_at: future })],
    total: 1,
    limit: 100,
    offset: 0,
  });
  expect(page.source_time_by_memory?.f).toEqual({
    at: new Date((NOW - 86_400) * 1_000).toISOString(),
    basis: "import",
  });
  expect(sdkDetail(record({ memory_at: future })).sdk_memory_at).toBeNull();

  const real = NOW - 3 * 86_400;
  expect(
    sdkLibraryPage({
      memories: [record({ memory_id: "r", memory_at: real })],
      total: 1,
      limit: 100,
      offset: 0,
    }).source_time_by_memory?.r.basis,
  ).toBe("conversation");
});

it("carries a full search window through to the list", () => {
  expect(
    sdkLibraryPage({
      memories: [],
      total: 0,
      limit: 100,
      offset: 0,
      search_window: 500,
    }).search_window,
  ).toBe(500);
});

it("carries each memory's scene and namespace, and edits an experience's solution", () => {
  const experience = record({
    memory_id: "e",
    content: "Fix tests\nPin seed",
    layer: "experience",
    scene: "working",
    kind: "experience",
    variant: "working",
    task: "Fix tests",
    solution: "Pin seed",
    created_at: NOW - 86_400,
    updated_at: NOW - 3_600,
  });
  const page = sdkLibraryPage({
    memories: [record({ memory_id: "c" }), experience],
    total: 2,
    limit: 20,
    offset: 0,
    window_id: "w1",
    window_reset: true,
  });
  expect(page.sdk_memory_refs).toEqual({
    c: { scene: "chat", kind: "chat", variant: null },
    e: { scene: "working", kind: "experience", variant: "working" },
  });
  expect(page.window_id).toBe("w1");
  expect(page.window_reset).toBe(true);
  // Stored without a conversation time: it shows when it was written, not
  // an "import".
  expect(page.source_time_by_memory?.e).toEqual({
    at: new Date((NOW - 86_400) * 1_000).toISOString(),
    basis: "write",
  });
  expect(page.items[1]).toMatchObject({
    kind: "working",
    updated_at: new Date((NOW - 3_600) * 1_000).toISOString(),
  });

  const detail = sdkDetail(experience);
  expect(detail.sdk_ref).toEqual({
    scene: "working",
    kind: "experience",
    variant: "working",
  });
  expect(sdkNamespace(detail.sdk_ref)).toEqual({
    kind: "experience",
    variant: "working",
  });
  expect(sdkNamespace(sdkMemoryRef(record({})))).toBeUndefined();
  expect(sdkEditableText(experience)).toBe("Pin seed");
  expect(sdkEditableText(record({}))).toBe("memory");
});

it("shows an experience's conversation time when the SDK stored one", () => {
  const at = NOW - 2 * 86_400;
  const experience = record({
    memory_id: "e",
    layer: "experience",
    scene: "coding",
    kind: "experience",
    variant: "coding",
    memory_at: at,
    created_at: NOW - 3_600,
  });
  const page = sdkLibraryPage({
    memories: [experience],
    total: 1,
    limit: 20,
    offset: 0,
  });
  expect(page.source_time_by_memory?.e).toEqual({
    at: new Date(at * 1_000).toISOString(),
    basis: "conversation",
  });
  expect(sdkDetail(experience).sdk_memory_at).toBe(
    new Date(at * 1_000).toISOString(),
  );
});
