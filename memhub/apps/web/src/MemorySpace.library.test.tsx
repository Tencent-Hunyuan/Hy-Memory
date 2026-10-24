// @vitest-environment jsdom
import type {
  GovernedMemory,
  MemoryDetail,
  MemoryLibraryPage,
  MemoryLibraryQuery,
  SourceResponse,
} from "@one-memory/contracts";
import {
  act,
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
  getMemoryAuthority: async () => ({
    authority: "legacy",
    migration_id: null,
  }),
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
  MemoryMap: ({
    memories,
    hidden,
  }: {
    memories: GovernedMemory[];
    hidden: boolean;
  }) => (
    <div hidden={hidden} data-testid="map-items">
      {memories.map((m) => m.memory_id).join(",")}
    </div>
  ),
}));
vi.mock("./MemoryRecallScope", () => ({
  MemoryRecallScope: () => null,
  memoryProvenanceLabel: () => "原话",
}));
const memory = (
  id: string,
  status: GovernedMemory["status"] = "confirmed",
): GovernedMemory => ({
  memory_id: id,
  user_id: "local",
  content: id,
  kind: "chat",
  status,
  origin: "extracted",
  revision: 3,
  scopes: ["all-apps"],
  tags: [],
  evidence_ids: [],
  created_at: "2026-09-19T00:00:00Z",
  updated_at: "2026-09-19T00:00:00Z",
  deleted_at: null,
});
const page = (
  items = [memory("全库条目")],
  total = 201,
  offset = 0,
): MemoryLibraryPage => ({ items, total, offset, limit: 100 });
function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}
beforeEach(() => {
  vi.resetAllMocks();
  api.memories.mockResolvedValue([memory("map-only")]);
  api.mapLibrary.mockResolvedValue(page([memory("map-only")], 1));
  api.library.mockImplementation(async (q) =>
    page([memory(`全库${q.offset}`)], 201, q.offset),
  );
  api.detail.mockImplementation(async (id) => ({
    memory: memory(id),
    evidence: [],
    history: [],
    index_sync: null,
  }));
  api.mutate.mockImplementation(async (id, action) => ({
    ...memory(id),
    status: action === "delete" ? "deleted" : "confirmed",
    revision: 4,
  }));
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});
async function manage(props = {}) {
  const view = render(<MemorySpace simplified {...props} />);
  await waitFor(() =>
    expect(screen.getByTestId("map-items").textContent).toBe("map-only"),
  );
  expect(api.library).not.toHaveBeenCalled();
  expect(api.mapLibrary).toHaveBeenCalledWith(
    { query: "", active_only: true, offset: 0, limit: 100 },
    expect.any(AbortSignal),
  );
  fireEvent(window, new Event("one-memory:manage"));
  await screen.findByText("共 201 条");
  return view;
}
it("queries server totals and pages without replacing the map collection; submitted search and clear reset offset", async () => {
  await manage();
  expect(api.library).toHaveBeenLastCalledWith(
    { status: "confirmed", query: "", offset: 0, limit: 100 },
    expect.any(AbortSignal),
  );
  fireEvent.click(screen.getByRole("checkbox", { name: "选择：全库0" }));
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  await screen.findByRole("checkbox", { name: "选择：全库100" });
  expect(screen.queryByRole("group", { name: "批量操作" })).toBeNull();
  const input = screen.getByLabelText("搜索记忆库");
  const calls = api.library.mock.calls.length;
  fireEvent.change(input, { target: { value: " exact % " } });
  expect(api.library).toHaveBeenCalledTimes(calls);
  fireEvent.click(screen.getByRole("button", { name: "搜索" }));
  await waitFor(() =>
    expect(api.library).toHaveBeenLastCalledWith(
      { status: "confirmed", query: " exact % ", offset: 0, limit: 100 },
      expect.any(AbortSignal),
    ),
  );
  fireEvent.click(screen.getByRole("button", { name: "已被取代" }));
  await waitFor(() =>
    expect(api.library.mock.lastCall?.[0]).toMatchObject({
      status: "superseded",
      query: " exact % ",
      offset: 0,
    }),
  );
  fireEvent.click(screen.getByRole("button", { name: "清除搜索" }));
  await waitFor(() =>
    expect(api.library.mock.lastCall?.[0]).toMatchObject({
      status: "superseded",
      query: "",
      offset: 0,
    }),
  );
  expect(screen.getByTestId("map-items")).toHaveProperty("hidden", true);
  expect(screen.getByTestId("map-items").textContent).toBe("");
  fireEvent(window, new Event("one-memory:manage"));
  await waitFor(() =>
    expect(screen.getByTestId("map-items").textContent).toBe("map-only"),
  );
  expect(api.mapLibrary.mock.lastCall?.[0]).toEqual({
    query: "",
    active_only: true,
    offset: 0,
    limit: 100,
  });
  expect(api.memories.mock.calls.every((c) => !c[0])).toBe(true);
});
it("keeps off-page detail and draft through page changes and legacy map refresh", async () => {
  vi.useFakeTimers();
  const view = render(<MemorySpace simplified />);
  await act(async () => {});
  fireEvent(window, new Event("one-memory:manage"));
  await act(async () => {});
  fireEvent.click(screen.getByRole("button", { name: /全库0/ }));
  await act(async () => {});
  fireEvent.change(screen.getByRole("textbox", { name: "记忆内容" }), {
    target: { value: "未保存草稿" },
  });
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  await act(async () => {
    vi.advanceTimersByTime(10000);
  });
  expect(
    (screen.getByRole("textbox", { name: "记忆内容" }) as HTMLTextAreaElement)
      .value,
  ).toBe("未保存草稿");
  view.unmount();
});
it("aborts superseded/left-page reads, ignores late success, and distinguishes failure from empty", async () => {
  await manage();
  const late = deferred<MemoryLibraryPage>();
  api.library.mockReturnValueOnce(late.promise);
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  const signal = api.library.mock.lastCall?.[1];
  api.library.mockRejectedValueOnce(new Error("offline"));
  fireEvent.click(screen.getByRole("button", { name: "已被取代" }));
  await screen.findByText("记忆库读取失败，请重试。");
  expect(signal.aborted).toBe(true);
  await act(async () => late.resolve(page([memory("迟到私有数据")])));
  expect(screen.queryByText("迟到私有数据")).toBeNull();
  expect(screen.queryByText("共 0 条")).toBeNull();
  expect(screen.queryByRole("checkbox")).toBeNull();
  const leave = deferred<MemoryLibraryPage>();
  api.library.mockReturnValueOnce(leave.promise);
  fireEvent.click(screen.getByRole("button", { name: "重新读取记忆库" }));
  const leaveSignal = api.library.mock.lastCall?.[1];
  fireEvent(window, new Event("one-memory:manage"));
  expect(leaveSignal.aborted).toBe(true);
});
it("clears invalid source pages and pending delete confirmation cannot write after revocation", async () => {
  const source = {
    source_id: "cursor",
    authorized: true,
    grant_revision: 1,
  } as SourceResponse;
  const view = await manage({ sources: [source] });
  fireEvent.click(screen.getByRole("checkbox", { name: "选择：全库0" }));
  fireEvent.click(screen.getByRole("button", { name: "删除所选 1 条" }));
  view.rerender(
    <MemorySpace
      simplified
      sources={[{ ...source, authorized: false, grant_revision: 2 }]}
      sourceSnapshotStale
    />,
  );
  expect(screen.queryByRole("checkbox")).toBeNull();
  const dialog = screen.queryByRole("dialog");
  if (dialog)
    fireEvent.click(within(dialog).getByRole("button", { name: /删除/ }));
  await act(async () => {});
  expect(api.mutate).not.toHaveBeenCalled();
});
it("refreshes mutation at the same query/page and recovers when the last page becomes empty", async () => {
  await manage();
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  await screen.findByRole("checkbox", { name: "选择：全库100" });
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  await screen.findByRole("checkbox", { name: "选择：全库200" });
  fireEvent.click(screen.getByRole("checkbox", { name: "选择：全库200" }));
  api.library.mockImplementation(async (q) =>
    page(q.offset >= 200 ? [] : [memory("剩余记忆")], 200, q.offset),
  );
  fireEvent.click(screen.getByRole("button", { name: "删除所选 1 条" }));
  fireEvent.click(
    within(screen.getByRole("dialog")).getByRole("button", { name: /删除/ }),
  );
  await screen.findByRole("checkbox", { name: "选择：剩余记忆" });
  expect(api.mutate).toHaveBeenCalledWith(
    "全库200",
    "delete",
    3,
    expect.any(String),
    expect.any(String),
  );
  expect(api.library.mock.calls.slice(-2).map((c) => c[0].offset)).toEqual([
    200, 100,
  ]);
  expect(screen.getByText("共 200 条")).toBeTruthy();
});

it("failed mutation refresh is not empty or writable, and retries preserve unknown-operation request IDs", async () => {
  await manage();
  fireEvent.click(screen.getByRole("checkbox", { name: "选择：全库0" }));
  api.mutate.mockRejectedValueOnce(new Error("response lost"));
  api.library.mockRejectedValueOnce(new Error("offline"));
  fireEvent.click(screen.getByRole("button", { name: "删除所选 1 条" }));
  fireEvent.click(
    within(screen.getByRole("dialog")).getByRole("button", { name: /删除/ }),
  );
  await screen.findByText("记忆库读取失败，请重试。");
  const request = api.mutate.mock.calls[0];
  expect(request).toEqual([
    "全库0",
    "delete",
    3,
    expect.any(String),
    expect.any(String),
  ]);
  expect(screen.queryByRole("checkbox")).toBeNull();
  expect(screen.queryByRole("button", { name: "删除所选 1 条" })).toBeNull();
  expect(screen.queryByText("共 0 条")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "重新读取记忆库" }));
  await screen.findByRole("button", { name: "重试失败的 1 条" });
  fireEvent.click(screen.getByRole("button", { name: "重试失败的 1 条" }));
  fireEvent.click(
    within(await screen.findByRole("dialog")).getByRole("button", {
      name: /删除/,
    }),
  );
  await waitFor(() => expect(api.mutate).toHaveBeenCalledTimes(2));
  // Each attempt is its own traced interaction, so the trace id differs; the
  // intent that must be preserved is the idempotent request id and the CAS
  // revision. Compare everything except the fresh trace id.
  expect(api.mutate.mock.calls[1].slice(0, 4)).toEqual(request.slice(0, 4));
  expect(api.mutate.mock.calls[1][4]).toEqual(expect.any(String));
});

it("source revision changes invalidate page/detail immediately and abort stale reads", async () => {
  const source = {
    source_id: "cursor",
    authorized: true,
    grant_revision: 1,
  } as SourceResponse;
  const view = await manage({ sources: [source] });
  fireEvent.click(screen.getByRole("button", { name: /全库0/ }));
  await screen.findByRole("textbox", { name: "记忆内容" });
  const late = deferred<MemoryLibraryPage>();
  api.library.mockReturnValueOnce(late.promise);
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  const signal = api.library.mock.lastCall?.[1];
  api.library.mockResolvedValueOnce(page([memory("授权变化后")], 1));
  view.rerender(
    <MemorySpace simplified sources={[{ ...source, grant_revision: 2 }]} />,
  );
  expect(signal.aborted).toBe(true);
  expect(screen.queryByRole("textbox", { name: "记忆内容" })).toBeNull();
  await screen.findByRole("checkbox", { name: "选择：授权变化后" });
  await act(async () => late.resolve(page([memory("旧来源回包")], 201, 100)));
  expect(screen.queryByText("旧来源回包")).toBeNull();
  expect(api.library.mock.lastCall?.[0].offset).toBe(0);
});

it("stops remaining batch writes when source authority changes during the first request", async () => {
  api.library.mockResolvedValue(page([memory("一"), memory("二")], 201));
  const view = await manage();
  fireEvent.click(screen.getByRole("checkbox", { name: "选择：一" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "选择：二" }));
  const write = deferred<GovernedMemory>();
  api.mutate.mockReturnValueOnce(write.promise);
  fireEvent.click(screen.getByRole("button", { name: "删除所选 2 条" }));
  fireEvent.click(
    within(screen.getByRole("dialog")).getByRole("button", { name: /删除/ }),
  );
  await waitFor(() => expect(api.mutate).toHaveBeenCalledTimes(1));
  view.rerender(<MemorySpace simplified sourceSnapshotStale />);
  await act(async () =>
    write.resolve({ ...memory("一"), status: "deleted", revision: 4 }),
  );
  expect(api.mutate).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("checkbox")).toBeNull();
});

it("unmount aborts a live query and a late response has no continuation", async () => {
  const view = await manage();
  const late = deferred<MemoryLibraryPage>();
  api.library.mockReturnValueOnce(late.promise);
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  const signal = api.library.mock.lastCall?.[1];
  const calls = api.library.mock.calls.length;
  view.unmount();
  expect(signal.aborted).toBe(true);
  await act(async () => late.resolve(page([], 0, 100)));
  expect(api.library).toHaveBeenCalledTimes(calls);
});

it("unmount during a batch stops remaining writes and post-mutation reads", async () => {
  api.library.mockResolvedValue(page([memory("一"), memory("二")], 201));
  const view = await manage();
  fireEvent.click(screen.getByRole("checkbox", { name: "选择：一" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "选择：二" }));
  const write = deferred<GovernedMemory>();
  api.mutate.mockReturnValueOnce(write.promise);
  fireEvent.click(screen.getByRole("button", { name: "删除所选 2 条" }));
  fireEvent.click(
    within(screen.getByRole("dialog")).getByRole("button", { name: /删除/ }),
  );
  await waitFor(() => expect(api.mutate).toHaveBeenCalledTimes(1));
  const reads =
    api.memories.mock.calls.length +
    api.library.mock.calls.length +
    api.mapLibrary.mock.calls.length;
  view.unmount();
  await act(async () =>
    write.resolve({ ...memory("一"), status: "deleted", revision: 4 }),
  );
  expect(api.mutate).toHaveBeenCalledTimes(1);
  expect(
    api.memories.mock.calls.length +
      api.library.mock.calls.length +
      api.mapLibrary.mock.calls.length,
  ).toBe(reads);
});

it("correction keeps submitted filter and page, passes CAS, and refreshes the exact page", async () => {
  await manage();
  fireEvent.change(screen.getByLabelText("搜索记忆库"), {
    target: { value: "原词" },
  });
  fireEvent.click(screen.getByRole("button", { name: "搜索" }));
  await waitFor(() =>
    expect(api.library.mock.lastCall?.[0].query).toBe("原词"),
  );
  await screen.findByRole("checkbox", { name: "选择：全库0" });
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  await screen.findByRole("checkbox", { name: "选择：全库100" });
  fireEvent.click(screen.getByRole("button", { name: /全库100/ }));
  await screen.findByRole("textbox", { name: "记忆内容" });
  fireEvent.change(screen.getByRole("textbox", { name: "记忆内容" }), {
    target: { value: "新词" },
  });
  api.correct.mockResolvedValueOnce({
    ...memory("全库100"),
    content: "新词",
    revision: 4,
  });
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "保存纠正" })).toHaveProperty(
      "disabled",
      false,
    ),
  );
  fireEvent.click(screen.getByRole("button", { name: "保存纠正" }));
  await waitFor(() =>
    expect(api.correct).toHaveBeenCalledWith(
      "全库100",
      3,
      "新词",
      ["all-apps"],
      expect.any(String),
      expect.any(String),
    ),
  );
  await waitFor(() =>
    expect(api.library.mock.lastCall?.[0]).toEqual({
      query: "原词",
      status: "confirmed",
      offset: 100,
      limit: 100,
    }),
  );
  expect(screen.getByTestId("map-items")).toHaveProperty("hidden", true);
  expect(screen.getByTestId("map-items").textContent).toBe("");
  expect(
    api.mapLibrary.mock.calls.every(
      ([query]) =>
        query.active_only === true &&
        query.query === "" &&
        query.status === undefined,
    ),
  ).toBe(true);
});

it("only successful empty pages show zero and disabled pagination", async () => {
  await manage();
  api.library.mockResolvedValueOnce(page([], 0));
  fireEvent.change(screen.getByLabelText("搜索记忆库"), {
    target: { value: "无匹配" },
  });
  fireEvent.click(screen.getByRole("button", { name: "搜索" }));
  await screen.findByText("共 0 条");
  expect(
    (screen.getByRole("button", { name: "上一页" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  expect(
    (screen.getByRole("button", { name: "下一页" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  expect(screen.queryByRole("checkbox")).toBeNull();
});

it("active=false aborts management reads and return reloads rather than accepting a late page", async () => {
  const view = await manage();
  const late = deferred<MemoryLibraryPage>();
  api.library.mockReturnValueOnce(late.promise);
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  const signal = api.library.mock.lastCall?.[1];
  view.rerender(<MemorySpace simplified active={false} />);
  expect(signal.aborted).toBe(true);
  await act(async () => late.resolve(page([memory("迟到页")], 201, 100)));
  api.library.mockResolvedValueOnce(page([memory("重新读取页")], 201, 100));
  view.rerender(<MemorySpace simplified />);
  await screen.findByRole("checkbox", { name: "选择：重新读取页" });
  expect(screen.queryByText("迟到页")).toBeNull();
});

async function selectedOffPage() {
  vi.useFakeTimers();
  const view = render(<MemorySpace simplified />);
  await act(async () => {});
  fireEvent(window, new Event("one-memory:manage"));
  await act(async () => {});
  fireEvent.click(screen.getByRole("button", { name: /全库0/ }));
  await act(async () => {});
  fireEvent.change(screen.getByRole("textbox", { name: "记忆内容" }), {
    target: { value: "本地未保存草稿" },
  });
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  await act(async () => {});
  return view;
}
const advanceDetailCheck = async () => {
  await act(async () => {
    vi.advanceTimersByTime(10000);
  });
};

it.each(["deleted", "403", "404"])(
  "authoritatively hides off-page selected detail on external %s",
  async (kind) => {
    await selectedOffPage();
    const reads = api.detail.mock.calls.length;
    if (kind === "deleted")
      api.detail.mockResolvedValue({
        memory: {
          ...memory("全库0", "deleted"),
          deleted_at: "2026-09-19T01:00:00Z",
        },
        evidence: [],
        history: [],
      });
    else
      api.detail.mockRejectedValue({
        response: {
          status: Number(kind),
          data: {
            detail:
              kind === "403" ? "memory_source_revoked" : "memory_not_found",
          },
        },
      });
    await advanceDetailCheck();
    expect(api.detail.mock.calls.length).toBeGreaterThan(reads);
    expect(api.detail.mock.lastCall?.[0]).toBe("全库0");
    expect(screen.queryByRole("textbox", { name: "记忆内容" })).toBeNull();
    expect(screen.getByRole("alert").textContent).toContain("已不可读取");
  },
);

it("external revision preserves draft and original CAS with a visible conflict, without keystroke reads", async () => {
  await selectedOffPage();
  const reads = api.detail.mock.calls.length;
  fireEvent.change(screen.getByRole("textbox", { name: "记忆内容" }), {
    target: { value: "继续写草稿" },
  });
  expect(api.detail).toHaveBeenCalledTimes(reads);
  api.detail.mockResolvedValue({
    memory: { ...memory("全库0"), content: "其他客户端新版本", revision: 4 },
    evidence: [],
    history: [],
  });
  await advanceDetailCheck();
  expect(
    (screen.getByRole("textbox", { name: "记忆内容" }) as HTMLTextAreaElement)
      .value,
  ).toBe("继续写草稿");
  expect(screen.queryByRole("status", { name: "详情新鲜度" })).toBeNull();
  expect(screen.queryByText(/第 4 版/)).toBeNull();
  api.correct.mockRejectedValueOnce({
    response: { status: 409, data: { detail: "memory_revision_conflict" } },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存纠正" }));
  await act(async () => {});
  expect(api.correct).toHaveBeenCalledWith(
    "全库0",
    3,
    "继续写草稿",
    ["all-apps"],
    expect.any(String),
    expect.any(String),
  );
  expect(screen.getByRole("alert").textContent).toContain("记忆已被更新");
});

it("does not show the in-flight verification line on the memory page", async () => {
  await selectedOffPage();
  api.detail.mockReturnValue(new Promise(() => {}));
  await advanceDetailCheck();
  expect(screen.queryByText("正在核验所选记忆…")).toBeNull();
  expect(screen.queryByText(/核验失败/)).toBeNull();
  expect(screen.queryByRole("status", { name: "详情新鲜度" })).toBeNull();
});

it("failed authoritative read is not deletion or freshness and disables writes until verified", async () => {
  await selectedOffPage();
  api.detail.mockRejectedValue(new Error("offline"));
  await advanceDetailCheck();
  expect(
    (screen.getByRole("textbox", { name: "记忆内容" }) as HTMLTextAreaElement)
      .value,
  ).toBe("本地未保存草稿");
  expect(screen.queryByText(/核验失败/)).toBeNull();
  expect(screen.queryByRole("status", { name: "详情新鲜度" })).toBeNull();
  expect(
    (screen.getByRole("button", { name: "保存纠正" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  const form = screen
    .getByRole("textbox", { name: "记忆内容" })
    .closest("form");
  expect(form).not.toBeNull();
  if (form) fireEvent.submit(form);
  expect(api.correct).not.toHaveBeenCalled();
  api.detail.mockResolvedValue({
    memory: memory("全库0"),
    evidence: [],
    history: [],
  });
  await advanceDetailCheck();
  expect(
    (screen.getByRole("button", { name: "保存纠正" }) as HTMLButtonElement)
      .disabled,
  ).toBe(false);
});

it.each(["selection", "inactive", "revoked", "unmount"])(
  "late authoritative response cannot overwrite after %s",
  async (mode) => {
    const view = await selectedOffPage();
    const pending = deferred<MemoryDetail>();
    const initialReads = api.detail.mock.calls.length;
    api.detail.mockReturnValueOnce(pending.promise);
    await advanceDetailCheck();
    const reads = api.detail.mock.calls.length;
    expect(reads).toBe(initialReads + 1);
    if (mode === "selection") {
      fireEvent.click(screen.getByRole("button", { name: /全库100/ }));
      await act(async () => {});
    } else if (mode === "inactive")
      view.rerender(<MemorySpace simplified active={false} />);
    else if (mode === "revoked")
      view.rerender(<MemorySpace simplified sourceSnapshotStale />);
    else view.unmount();
    await act(async () =>
      pending.resolve({
        memory: memory("全库0", "deleted"),
        evidence: [],
        history: [],
        index_sync: null,
      }),
    );
    expect(screen.queryByText(/已不可读取/)).toBeNull();
    if (mode === "selection")
      expect(
        (
          screen.getByRole("textbox", {
            name: "记忆内容",
          }) as HTMLTextAreaElement
        ).value,
      ).toBe("全库100");
    else {
      await advanceDetailCheck();
      expect(api.detail).toHaveBeenCalledTimes(reads);
    }
  },
);

it("external removal while delete confirmation is open cannot send the old intent", async () => {
  await selectedOffPage();
  fireEvent.click(screen.getByRole("button", { name: "删除" }));
  api.detail.mockResolvedValue({
    memory: memory("全库0", "deleted"),
    evidence: [],
    history: [],
  });
  await advanceDetailCheck();
  expect(screen.queryByRole("textbox", { name: "记忆内容" })).toBeNull();
  fireEvent.click(
    within(screen.getByRole("dialog")).getByRole("button", { name: /删除/ }),
  );
  await act(async () => {});
  expect(api.mutate).not.toHaveBeenCalled();
});
