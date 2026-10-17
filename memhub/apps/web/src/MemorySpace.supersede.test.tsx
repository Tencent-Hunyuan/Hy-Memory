// @vitest-environment jsdom
import type {
  GovernedMemory,
  MemoryLibraryPage,
  MemoryLibraryQuery,
} from "@one-memory/contracts";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemorySpace } from "./MemorySpace";

const api = vi.hoisted(() => ({
  library: vi.fn(),
  mapLibrary: vi.fn(),
  memories: vi.fn(),
  detail: vi.fn(),
  mutate: vi.fn(),
  correct: vi.fn(),
}));
vi.mock("./api", () => ({
  getMemoryLibrary: (query: MemoryLibraryQuery, signal?: AbortSignal) =>
    query.active_only === true
      ? api.mapLibrary(query, signal)
      : api.library(query, signal),
  getMemories: api.memories,
  getMemory: api.detail,
  mutateMemory: api.mutate,
  correctMemory: api.correct,
  apiErrorCode: (reason: { response?: { data?: { detail?: string } } }) =>
    reason?.response?.data?.detail ?? null,
  getMemoryGraph: async () => ({
    nodes: [],
    edges: [],
    total_memories: 1,
    projected_memories: 1,
  }),
  getMemoryProcessing: async () => null,
  getModelProfiles: async () => [],
}));
vi.mock("./GraphWidthPrompt", () => ({ GraphWidthPrompt: () => null }));
vi.mock("./MemoryNoteComposer", () => ({ MemoryNoteComposer: () => null }));
vi.mock("./MemoryMap", () => ({
  MemoryMap: ({ hidden }: { hidden: boolean }) => (
    <div hidden={hidden} data-testid="map-items" />
  ),
}));
vi.mock("./MemoryRecallScope", () => ({
  MemoryRecallScope: () => null,
  memoryProvenanceLabel: () => "原话",
}));

const memory = (
  id: string,
  content: string,
  status: GovernedMemory["status"],
  links: Partial<Pick<GovernedMemory, "supersedes" | "superseded_by">> = {},
): GovernedMemory => ({
  memory_id: id,
  user_id: "local",
  content,
  kind: "chat",
  status,
  origin: "extracted",
  revision: 3,
  scopes: ["all-apps"],
  tags: [],
  evidence_ids: [],
  supersedes: [],
  superseded_by: [],
  ...links,
  created_at: "2026-09-19T00:00:00Z",
  updated_at: "2026-09-19T00:00:00Z",
  deleted_at: null,
});
const old = memory("old", "发布前做一次权限评审", "superseded", {
  superseded_by: ["new"],
});
const head = memory("new", "发布前做两次权限评审", "confirmed", {
  supersedes: ["old"],
});
const page = (items: GovernedMemory[]): MemoryLibraryPage => ({
  items,
  total: items.length,
  offset: 0,
  limit: 100,
});

beforeEach(() => {
  vi.resetAllMocks();
  api.memories.mockResolvedValue([]);
  api.mapLibrary.mockResolvedValue(page([head]));
  api.library.mockImplementation(async (query: MemoryLibraryQuery) =>
    page(query.status === "superseded" ? [old] : [head]),
  );
  api.detail.mockImplementation(async (id: string) => ({
    memory: id === "old" ? old : head,
    evidence: [],
    history: [3, 2].map((revision) => ({
      memory_id: id,
      revision,
      action: revision === 3 ? "hy_supersede" : "correct",
      status: id === "old" && revision === 3 ? "superseded" : "confirmed",
      content: id === "old" ? old.content : head.content,
      scopes: ["all-apps"],
      created_at: "2026-09-19T00:00:00Z",
    })),
    index_sync: null,
  }));
});
afterEach(cleanup);

async function openSuperseded() {
  render(<MemorySpace simplified />);
  await waitFor(() => expect(api.mapLibrary).toHaveBeenCalled());
  fireEvent(window, new Event("one-memory:manage"));
  const tab = await screen.findByRole("button", { name: "已被取代" });
  fireEvent.click(tab);
  await waitFor(() =>
    expect(api.library.mock.lastCall?.[0]).toMatchObject({
      status: "superseded",
    }),
  );
  fireEvent.click(
    await screen.findByRole("button", { name: /发布前做一次权限评审/ }),
  );
  return screen.findByRole("complementary", { name: "记忆详情" });
}

it("labels a superseded memory, jumps to its replacement, and shows what the head replaced", async () => {
  const inspector = await openSuperseded();
  expect(within(inspector).getByText(/已被取代/)).toBeTruthy();

  fireEvent.click(
    within(inspector).getByRole("button", { name: "查看替代它的记忆" }),
  );
  await waitFor(() => expect(api.detail).toHaveBeenLastCalledWith("new"));
  const current = await screen.findByRole("complementary", {
    name: "记忆详情",
  });
  await within(current).findByText(/已确认/);
  fireEvent.click(
    within(current).getByRole("button", { name: "查看它取代的旧记忆" }),
  );
  await waitFor(() => expect(api.detail).toHaveBeenLastCalledWith("old"));
});

it("offers no confirm, correction or undo on a superseded memory, but still deletes", async () => {
  const inspector = await openSuperseded();
  // Positive control: the current memory with the same history offers them.
  expect(
    within(inspector).queryByRole("button", { name: "保存纠正" }),
  ).toBeNull();
  expect(
    within(inspector).queryByRole("button", { name: "撤销上次修改" }),
  ).toBeNull();
  expect(within(inspector).queryByRole("button", { name: "确认" })).toBeNull();
  expect(within(inspector).getByRole("button", { name: "删除" })).toBeTruthy();

  fireEvent.click(
    within(inspector).getByRole("button", { name: "查看替代它的记忆" }),
  );
  const current = await screen.findByRole("complementary", {
    name: "记忆详情",
  });
  await within(current).findByRole("button", { name: "保存纠正" });
  expect(
    within(current).getByRole("button", { name: "撤销上次修改" }),
  ).toBeTruthy();
});

it("names every history step in words, including records carried over from an older version", async () => {
  const actions = [
    "legacy",
    "create",
    "correct",
    "set_scopes",
    "undo",
    "hy_reconcile",
    "hy_supersede",
    "delete",
    "from-a-future-version",
  ];
  api.detail.mockImplementation(async () => ({
    memory: old,
    evidence: [],
    history: actions.map((action, index) => ({
      memory_id: "old",
      revision: actions.length - index,
      action,
      status: "confirmed",
      content: old.content,
      scopes: ["all-apps"],
      created_at: "2026-09-19T00:00:00Z",
    })),
    index_sync: null,
  }));
  const inspector = await openSuperseded();
  const steps = within(inspector)
    .getAllByRole("listitem")
    .map((item) => item.textContent);

  expect(steps).toEqual([
    "r9 · 旧版确认记录",
    "r8 · 创建",
    "r7 · 纠正",
    "r6 · 调整使用范围",
    "r5 · 撤销修改",
    "r4 · 模型更新",
    "r3 · 被新记忆取代",
    "r2 · 删除",
    "r1 · 其他操作",
  ]);
});
