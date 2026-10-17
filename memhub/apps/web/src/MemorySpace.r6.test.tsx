// @vitest-environment jsdom
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { DIALOGUE_PANE_VISIBLE } from "./MemoryMap";
import { MemorySpace } from "./MemorySpace";

const { reads } = vi.hoisted(() => ({
  reads: {
    memories: vi.fn(),
    graph: vi.fn(),
    processing: vi.fn(),
    profiles: vi.fn(),
    library: vi.fn(),
    mapLibrary: vi.fn(),
    recent: vi.fn(),
    conversation: vi.fn(),
  },
}));
vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  getMemories: reads.memories,
  getMemoryGraph: reads.graph,
  getMemoryProcessing: reads.processing,
  getModelProfiles: reads.profiles,
  getMemoryLibrary: (query: { active_only?: boolean }, signal?: AbortSignal) =>
    query.active_only === true
      ? reads.mapLibrary(query, signal)
      : reads.library(query, signal),
  getRecentConversations: reads.recent,
  getConversation: reads.conversation,
}));
vi.mock("./GraphWidthPrompt", () => ({ GraphWidthPrompt: () => null }));
vi.mock("./MemoryNoteComposer", () => ({
  MemoryNoteComposer: ({
    onPendingChange,
  }: {
    onPendingChange?(note: {
      evidenceId: string;
      text: string;
      state: string;
    }): void;
  }) => (
    <button
      type="button"
      onClick={() =>
        onPendingChange?.({
          evidenceId: "note-1",
          text: "项目周会统一安排在周三上午。",
          state: "waiting_for_model",
        })
      }
    >
      补充记忆
    </button>
  ),
}));
vi.mock("./MemoryGraph3D", () => ({
  MemoryGraph3D: () => <div data-testid="graph-owner" />,
}));
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
});
function setup() {
  const items = [
    {
      memory_id: "m1",
      content: "已保存的记忆",
      status: "confirmed",
      kind: "chat",
      origin: "extracted",
      tags: [],
      scopes: ["all-apps"],
      evidence_ids: ["e1"],
      revision: 1,
    },
  ];
  reads.memories.mockResolvedValue(items);
  reads.graph.mockResolvedValue({
    nodes: [
      {
        node_id: "memory:m1",
        node_type: "memory",
        memory_id: "m1",
        label: "已保存的记忆",
      },
    ],
    edges: [],
    total_memories: 1,
    projected_memories: 1,
  });
  reads.processing.mockResolvedValue({
    running: 0,
    pending: 0,
    blocked_stages: [],
    completed: 1,
  });
  reads.profiles.mockResolvedValue([]);
  reads.library.mockResolvedValue({
    items: [],
    total: 0,
    offset: 0,
    limit: 100,
  });
  reads.mapLibrary.mockResolvedValue({
    items,
    total: items.length,
    offset: 0,
    limit: 100,
  });
  reads.recent.mockResolvedValue({
    items: [
      {
        evidence_id: "e-live",
        conversation_id: "c1",
        source_id: "cursor",
        role: "user",
        written_by: "person",
        created_at: null,
        imported_at: "2026-09-23T06:47:00Z",
        excerpt: "刚同步的原话",
        truncated: false,
      },
    ],
    total: 1,
    offset: 0,
    limit: 30,
  });
  reads.conversation.mockResolvedValue({
    conversation_id: "c1",
    source_id: "cursor",
    messages: [
      {
        evidence_id: "e-live",
        role: "user",
        written_by: "person",
        created_at: null,
        verbatim: "刚同步的原话全文",
      },
    ],
  });
}

it("keeps a saved note visible while it waits for model processing", async () => {
  setup();
  render(<MemorySpace simplified onConfigureModel={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "补充记忆" }));
  expect(
    screen.getByText(/原话已保存，正在等待整理：项目周会统一安排在周三上午。/),
  ).toBeTruthy();
  expect(screen.getByRole("button", { name: "配置整理模型" })).toBeTruthy();
  expect(screen.queryByText("还没有已生成的记忆")).toBeNull();
});
// D96: the memory page no longer has the raw-dialogue tab; runs again if it comes back.
it.skipIf(!DIALOGUE_PANE_VISIBLE)(
  "lists imported dialogue from the memory sidebar and opens the original",
  async () => {
    setup();
    render(<MemorySpace simplified />);
    await screen.findByText("已保存的记忆");
    fireEvent.click(screen.getByRole("tab", { name: "原始对话" }));
    expect(await screen.findByText("刚同步的原话")).toBeTruthy();
    expect(await screen.findByText("刚同步的原话全文")).toBeTruthy();
    expect(screen.getByText("刚导入的原话，还不是沉淀后的记忆")).toBeTruthy();
    expect(screen.queryByTestId("graph-owner")).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: "记忆" }));
    expect(await screen.findByTestId("graph-owner")).toBeTruthy();
  },
);
it("hides a recoverable graph read failure on the three-column surface", async () => {
  setup();
  reads.graph.mockRejectedValue(new Error("graph offline"));
  render(<MemorySpace simplified />);
  // Two copies now: the library list, and the detail pane naming what the
  // conversation settled (OBS-14). Waiting on one copy used to be enough.
  await screen.findAllByText("已保存的记忆");
  expect(screen.queryByText(/记忆图谱读取失败/)).toBeNull();
  expect(screen.queryByRole("alert")).toBeNull();
});
it("shows the three-column surface without four modes or a management button", async () => {
  setup();
  render(<MemorySpace simplified />);
  await screen.findByTestId("graph-owner");
  expect(reads.mapLibrary).toHaveBeenCalledWith(
    { query: "", active_only: true, offset: 0, limit: 100 },
    expect.any(AbortSignal),
  );
  expect(reads.library).not.toHaveBeenCalled();
  expect(screen.queryByRole("tablist", { name: "记忆空间模式" })).toBeNull();
  expect(screen.queryByLabelText("向记忆提问")).toBeNull();
  expect(screen.queryByRole("button", { name: "管理记忆" })).toBeNull();
  fireEvent(window, new Event("one-memory:manage"));
  expect(screen.getByRole("navigation", { name: "记忆治理状态" })).toBeTruthy();
  await waitFor(() =>
    expect(reads.library).toHaveBeenCalledWith(
      { query: "", status: "confirmed", offset: 0, limit: 100 },
      expect.any(AbortSignal),
    ),
  );
  fireEvent(window, new Event("one-memory:manage"));
  expect(await screen.findByTestId("graph-owner")).toBeTruthy();
});
it("stops all page polling and disposes graph while inactive, then resumes", async () => {
  setup();
  vi.useFakeTimers();
  const view = render(<MemorySpace simplified />);
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
  expect(reads.memories).toHaveBeenCalled();
  expect(screen.getByTestId("graph-owner")).toBeTruthy();
  view.rerender(<MemorySpace simplified active={false} />);
  const counts = Object.values(reads).map((fn) => fn.mock.calls.length);
  await act(async () => {
    vi.advanceTimersByTime(30000);
    await Promise.resolve();
  });
  expect(Object.values(reads).map((fn) => fn.mock.calls.length)).toEqual(
    counts,
  );
  expect(screen.queryByTestId("graph-owner")).toBeNull();
  view.rerender(<MemorySpace simplified />);
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
  expect(reads.memories.mock.calls.length).toBeGreaterThan(counts[0]);
});
