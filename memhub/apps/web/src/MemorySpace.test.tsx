// @vitest-environment jsdom

import type { GovernedMemory, MemoryDetail } from "@one-memory/contracts";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MemorySpace } from "./MemorySpace";
import { pollNow } from "./poll-scheduler";

// Text queries include hidden retained views by default; assertions target the visible owner.
const visibleText = { selector: ":not([hidden] *)" };

vi.mock("./MemoryGraph3D", () => ({
  MemoryGraph3D: ({ selectedNodeId }: { selectedNodeId: string | null }) => (
    <div data-testid="memory-graph" data-selected={selectedNodeId} />
  ),
}));

const memory: GovernedMemory = {
  memory_id: "memory-1",
  user_id: "local",
  kind: "chat",
  status: "confirmed",
  content: "Initial memory",
  scopes: ["all-apps"],
  evidence_ids: ["evidence-1"],
  origin: "extracted",
  tags: [],
  revision: 1,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
  deleted_at: null,
};

const detail: MemoryDetail = {
  memory,
  evidence: [
    {
      evidence_id: "evidence-1",
      available: true,
      source_id: "cursor",
      role: "user",
      written_by: "person",
      created_at: null,
      verbatim: "Original words",
    },
  ],
  history: [
    {
      memory_id: "memory-1",
      revision: 1,
      action: "create",
      status: "confirmed",
      content: "Initial memory",
      scopes: ["all-apps"],
      created_at: "2026-09-01T00:00:00Z",
    },
  ],
  index_sync: null,
};

const {
  apiErrorCode,
  getMemories,
  getMemoryAuthority,
  getMemory,
  getMemoryGraph,
  getMemoryProcessing,
  askMemoriesStream,
  mutateMemory,
  correctMemory,
  syncMemoryIndex,
  setExtractionWindow,
  manualExtract,
  retryUnknownMemoryProcessing,
  getGraphWidth,
  decideGraphWidth,
  getModelProfiles,
  createMemory,
  digestMemoryGraph,
  searchConversations,
  getConversation,
  getConversationBrowser,
  getConversationMemories,
  getMemoryNotes,
  saveMemoryNote,
  processMemoryNote,
} = vi.hoisted(() => ({
  apiErrorCode: vi.fn(),
  getMemories: vi.fn(),
  getMemoryAuthority: vi.fn(),
  getMemory: vi.fn(),
  getMemoryGraph: vi.fn(),
  getMemoryProcessing: vi.fn(),
  askMemoriesStream: vi.fn(),
  mutateMemory: vi.fn(),
  correctMemory: vi.fn(),
  syncMemoryIndex: vi.fn(),
  setExtractionWindow: vi.fn(),
  manualExtract: vi.fn(),
  retryUnknownMemoryProcessing: vi.fn(),
  getGraphWidth: vi.fn(),
  decideGraphWidth: vi.fn(),
  getModelProfiles: vi.fn(),
  createMemory: vi.fn(),
  digestMemoryGraph: vi.fn(),
  searchConversations: vi.fn(),
  getConversation: vi.fn(),
  getConversationBrowser: vi.fn(),
  getConversationMemories: vi.fn(),
  getMemoryNotes: vi.fn(),
  saveMemoryNote: vi.fn(),
  processMemoryNote: vi.fn(),
}));

const LIBRARY_REFRESH_MS = 10_000;

vi.mock("./api", () => ({
  apiErrorCode,
  getMemories,
  getMemoryAuthority,
  getMemory,
  getMemoryGraph,
  getMemoryProcessing,
  askMemoriesStream,
  mutateMemory,
  correctMemory,
  syncMemoryIndex,
  setExtractionWindow,
  manualExtract,
  retryUnknownMemoryProcessing,
  getGraphWidth,
  decideGraphWidth,
  getModelProfiles,
  createMemory,
  digestMemoryGraph,
  searchConversations,
  getConversation,
  getConversationBrowser,
  getConversationMemories,
  getMemoryNotes,
  saveMemoryNote,
  processMemoryNote,
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe("MemorySpace", () => {
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  beforeEach(() => {
    vi.resetAllMocks();
    getMemoryAuthority.mockResolvedValue({
      authority: "legacy",
      migration_id: null,
    });
    apiErrorCode.mockReturnValue(null);
    getMemories.mockResolvedValue([memory]);
    getMemory.mockResolvedValue(detail);
    getMemoryGraph.mockResolvedValue({
      total_memories: 1,
      projected_memories: 1,
      nodes: [
        {
          node_id: "memory:memory-1",
          node_type: "memory",
          label: "Initial memory",
          memory_id: "memory-1",
          source_id: null,
          kind: "chat",
          status: "confirmed",
        },
      ],
      edges: [],
    });
    getMemoryProcessing.mockResolvedValue({
      pending: 0,
      running: 0,
      fresh_pending: 0,
      backlog_pending: 0,
      recent_completions: [],
      waiting_for_model: 0,
      completed: 1,
      failed: 0,
      unknown: 0,
      unknown_reasons: {},
      blocked_stages: [],
      outside_window_pending: 0,
      extraction_window_preset: "3m",
      manual_extract_batches_planned: 0,
      manual_extract_batches_current: 0,
      active_queue: 0,
      in_flight_count: 0,
      seconds_per_completion: null,
    });
    retryUnknownMemoryProcessing.mockResolvedValue({ requeued: 0 });
    mutateMemory.mockResolvedValue({
      ...memory,
      status: "confirmed",
      revision: 2,
    });
    correctMemory.mockResolvedValue({
      ...memory,
      content: "Corrected",
      revision: 2,
    });
    syncMemoryIndex.mockResolvedValue({
      memory_id: "memory-1",
      status: "synced",
      attempts: 2,
      last_error_code: null,
      updated_at: "2026-09-01T00:00:00Z",
    });
    getGraphWidth.mockResolvedValue({ mismatch: null, memory_count: 1 });
    decideGraphWidth.mockResolvedValue({
      archived_path: null,
      mismatch: null,
    });
    getModelProfiles.mockResolvedValue([
      { profile_id: "profile-1", is_active: true },
    ]);
    getMemoryNotes.mockResolvedValue([]);
    searchConversations.mockResolvedValue({
      hits: [
        {
          evidence_id: "history-1",
          conversation_id: "history-conversation",
          source_id: "cursor",
          workspace: null,
          project: null,
          created_at: null,
          excerpt: "History excerpt",
        },
      ],
      empty_reason: null,
    });
    getConversation.mockResolvedValue({
      conversation_id: "history-conversation",
      source_id: "cursor",
      messages: [
        {
          evidence_id: "history-1",
          role: "user",
          written_by: "person",
          created_at: null,
          verbatim: "History original",
        },
      ],
    });
    getConversationBrowser.mockResolvedValue({
      items: [],
      total: 0,
      offset: 0,
      limit: 50,
    });
    getConversationMemories.mockResolvedValue({ items: [] });
    setExtractionWindow.mockResolvedValue({ window_preset: "1y" });
    manualExtract.mockResolvedValue({
      promoted: 25,
      batches_planned: 3,
      batches_current: 1,
    });
    askMemoriesStream.mockImplementation(async function* () {
      yield { type: "recall", memories: [memory] };
      yield { type: "delta", text: "Grounded " };
      yield { type: "delta", text: "answer" };
      yield {
        type: "final",
        answer: "Grounded answer",
        memories: [memory],
        mode: "generated",
      };
    });
  });

  describe("S1.3 teaching state preservation", () => {
    function expectNoTeachingWrites() {
      for (const write of [
        mutateMemory,
        correctMemory,
        syncMemoryIndex,
        setExtractionWindow,
        manualExtract,
        decideGraphWidth,
        createMemory,
        digestMemoryGraph,
        saveMemoryNote,
        processMemoryNote,
      ])
        expect(write).not.toHaveBeenCalled();
    }

    it("navigates the memory mode tabs with the arrow keys", async () => {
      const view = render(<MemorySpace />);
      void view;
      const mapTab = screen.getByRole("tab", { name: "工作台" });
      const libraryTab = screen.getByRole("tab", { name: "记忆库" });
      // WAI-ARIA tabs pattern: one tabstop only, the selected one.
      expect(mapTab.tabIndex).toBe(0);
      expect(libraryTab.tabIndex).toBe(-1);
      mapTab.focus();
      await userEvent.keyboard("{ArrowRight}");
      expect(libraryTab.getAttribute("aria-selected")).toBe("true");
      expect(libraryTab.tabIndex).toBe(0);
      expect(document.activeElement).toBe(libraryTab);
      await userEvent.keyboard("{ArrowLeft}");
      expect(mapTab.getAttribute("aria-selected")).toBe("true");
      expect(document.activeElement).toBe(mapTab);
      // Home/End jump to the ends of the tablist.
      await userEvent.keyboard("{End}");
      const historyTab = screen.getByRole("tab", { name: "历史对话" });
      expect(historyTab.getAttribute("aria-selected")).toBe("true");
      expect(document.activeElement).toBe(historyTab);
      await userEvent.keyboard("{Home}");
      expect(mapTab.getAttribute("aria-selected")).toBe("true");
      // Every tab points at a panel that exists.
      for (const label of ["工作台", "记忆库", "问记忆", "历史对话"]) {
        const tab = screen.getByRole("tab", { name: label });
        const controls = tab.getAttribute("aria-controls");
        expect(controls).toBeTruthy();
        expect(view.container.querySelector(`#${controls}`)).not.toBeNull();
      }
    });

    it("keeps the map DOM, selection, filters and question across every teaching view and route", async () => {
      const view = render(<MemorySpace />);
      const map = await screen.findByRole("region", { name: "记忆工作台" });
      await userEvent.type(
        within(map).getByLabelText("筛选地图记忆"),
        "Initial",
      );
      await userEvent.click(
        await within(map).findByRole("button", { name: /Initial memory/ }),
      );
      await within(map).findByText("Original words");
      await userEvent.type(
        within(map).getByLabelText("向记忆提问"),
        "Unsent map question",
      );
      await userEvent.click(within(map).getByRole("button", { name: "纠正" }));
      fireEvent.change(within(map).getByLabelText("记忆内容"), {
        target: { value: "Unsent correction" },
      });
      const canvas = screen.getByTestId("memory-graph");
      // The teaching prop is gone; the invariant to keep is that switching
      // between the real tabs must not rebuild the map or drop its state.
      for (const [label, mode] of [
        ["工作台", "map"],
        ["记忆库", "library"],
        ["问记忆", "ask"],
        ["历史对话", "history"],
        ["工作台", "map"],
      ] as const) {
        await userEvent.click(screen.getByRole("tab", { name: label }));
        expect(view.container.querySelector(".core-memory-map")).toBe(map);
        expect(screen.getByTestId("memory-graph")).toBe(canvas);
        expect(map.hidden).toBe(mode !== "map");
        expect(
          view.container
            .querySelector(`[data-tour-id="memory-${mode}-tab"]`)
            ?.getAttribute("aria-selected"),
        ).toBe("true");
      }
      view.rerender(<MemorySpace active={false} />);
      expect(screen.queryByRole("region", { name: "记忆空间" })).toBeNull();
      expect(map.hidden).toBe(true);
      view.rerender(<MemorySpace />);
      expect(
        screen
          .getByRole("tab", { name: "工作台" })
          .getAttribute("aria-selected"),
      ).toBe("true");
      expect(screen.getByTestId("memory-graph")).toBe(canvas);
      expect(canvas.dataset.selected).toBe("memory:memory-1");
      expect(
        (within(map).getByLabelText("筛选地图记忆") as HTMLInputElement).value,
      ).toBe("Initial");
      expect(
        (within(map).getByLabelText("向记忆提问") as HTMLInputElement).value,
      ).toBe("Unsent map question");
      expect(
        (within(map).getByLabelText("记忆内容") as HTMLTextAreaElement).value,
      ).toBe("Unsent correction");
      expect(getMemory).toHaveBeenCalledTimes(1);
      expect(askMemoriesStream).not.toHaveBeenCalled();
      expect(searchConversations).not.toHaveBeenCalled();
      for (const label of ["记忆库", "问记忆", "历史对话", "工作台"]) {
        await userEvent.click(screen.getByRole("tab", { name: label }));
        expect(screen.getByTestId("memory-graph")).toBe(canvas);
      }
      expect(canvas.dataset.selected).toBe("memory:memory-1");
      expect(
        (within(map).getByLabelText("向记忆提问") as HTMLInputElement).value,
      ).toBe("Unsent map question");
      expectNoTeachingWrites();
    });

    it.each(["confirmed", "superseded", "deleted"] as const)(
      "restores a %s library detail, draft, status, selection and query",
      async (status) => {
        const target = { ...memory, status };
        getMemories.mockResolvedValue([target]);
        getMemory.mockResolvedValue({ ...detail, memory: target });
        const view = render(<MemorySpace />);
        await userEvent.click(screen.getByRole("tab", { name: "记忆库" }));
        await userEvent.click(
          screen.getByRole("button", {
            name:
              status === "confirmed"
                ? /已可用/
                : status === "superseded"
                  ? /已被取代/
                  : /已删除/,
          }),
        );
        const query = screen.getByLabelText("搜索记忆库") as HTMLInputElement;
        await userEvent.type(query, "Unsubmitted library query");
        const checkbox = (await screen.findByRole("checkbox", {
          name: "选择：Initial memory",
        })) as HTMLInputElement;
        await userEvent.click(checkbox);
        await userEvent.click(
          screen.getByRole("button", { name: /^Initial memory/ }),
        );
        const inspector = await screen.findByRole("complementary", {
          name: "记忆详情",
        });
        if (status === "confirmed")
          fireEvent.change(within(inspector).getByLabelText("记忆内容"), {
            target: { value: "Library correction draft" },
          });
        // Tabs are the only way to change mode now, and a tab click clears the
        // answer and closes the detail by design. The invariant still worth
        // pinning is that suspending and resuming the surface keeps the
        // library detail, draft, selection and query.
        view.rerender(<MemorySpace active={false} />);
        view.rerender(<MemorySpace />);
        expect(
          screen
            .getByRole("tab", { name: "记忆库" })
            .getAttribute("aria-selected"),
        ).toBe("true");
        expect(screen.getByLabelText("搜索记忆库")).toBe(query);
        expect(query.value).toBe("Unsubmitted library query");
        expect(
          screen.getByRole("checkbox", { name: "选择：Initial memory" }),
        ).toBe(checkbox);
        expect(checkbox.checked).toBe(true);
        expect(screen.getByRole("complementary", { name: "记忆详情" })).toBe(
          inspector,
        );
        expect(within(inspector).getByText("Original words")).toBeTruthy();
        if (status === "confirmed")
          expect(
            (
              within(inspector).getByLabelText(
                "记忆内容",
              ) as HTMLTextAreaElement
            ).value,
          ).toBe("Library correction draft");
        expect(getMemory).toHaveBeenCalledTimes(1);
        expect(getMemories).toHaveBeenCalledTimes(1);
        expectNoTeachingWrites();
      },
    );

    it("keeps the real history owner and results without repeating searches on tour or ordinary tabs", async () => {
      const view = render(<MemorySpace />);
      expect(searchConversations).not.toHaveBeenCalled();
      await userEvent.click(screen.getByRole("tab", { name: "历史对话" }));
      const input = screen.getByLabelText("搜索历史对话") as HTMLInputElement;
      await userEvent.type(input, "History query");
      await userEvent.click(screen.getByRole("button", { name: "搜索" }));
      await userEvent.click(
        await screen.findByRole("button", { name: "查看原文" }),
      );
      const original = await screen.findByText("History original");
      await userEvent.click(screen.getByRole("tab", { name: "工作台" }));
      await userEvent.click(screen.getByRole("tab", { name: "历史对话" }));
      expect(screen.getByLabelText("搜索历史对话")).toBe(input);
      for (const [label, mode] of [
        ["工作台", "map"],
        ["问记忆", "ask"],
        ["记忆库", "library"],
        ["历史对话", "history"],
      ] as const) {
        await userEvent.click(screen.getByRole("tab", { name: label }));
        expect(
          screen
            .getByRole("tab", { selected: true })
            .getAttribute("data-tour-id"),
        ).toBe(`memory-${mode}-tab`);
      }
      view.rerender(<MemorySpace active={false} />);
      view.rerender(<MemorySpace />);
      expect(
        screen
          .getByRole("tab", { name: "历史对话" })
          .getAttribute("aria-selected"),
      ).toBe("true");
      expect(screen.getByLabelText("搜索历史对话")).toBe(input);
      expect(input.value).toBe("History query");
      expect(screen.getByText("History original")).toBe(original);
      expect(searchConversations).toHaveBeenCalledExactlyOnceWith(
        "History query",
      );
      expect(getConversation).toHaveBeenCalledExactlyOnceWith("history-1");
      expectNoTeachingWrites();
    });

    it("preserves an existing answer and scope without asking again", async () => {
      const view = render(<MemorySpace />);
      await userEvent.click(screen.getByRole("tab", { name: "问记忆" }));
      await userEvent.selectOptions(screen.getByLabelText("问答记忆范围"), "*");
      await userEvent.type(
        screen.getByLabelText("向已确认记忆提问"),
        "Question",
      );
      await userEvent.click(
        screen.getByRole("button", { name: "根据记忆回答" }),
      );
      const answer = await screen.findByRole("heading", {
        name: "Grounded answer",
      });
      // A tab click intentionally clears the answer, so the preserved state to
      // assert is across a suspend/resume of the same tab, not across tabs.
      view.rerender(<MemorySpace active={false} />);
      view.rerender(<MemorySpace />);
      expect(screen.getByRole("heading", { name: "Grounded answer" })).toBe(
        answer,
      );
      expect(
        (screen.getByLabelText("问答记忆范围") as HTMLSelectElement).value,
      ).toBe("*");
      expect(
        (screen.getByLabelText("向已确认记忆提问") as HTMLInputElement).value,
      ).toBe("Question");
      // The trace id is the 4th argument: the SSE path uses raw `fetch`, so it
      // must carry the header itself for Core's kernel legs to join the
      // renderer's ui.action/ui.result.
      expect(askMemoriesStream).toHaveBeenCalledExactlyOnceWith(
        "Question",
        "*",
        undefined,
        expect.any(String),
        "legacy",
      );
      expectNoTeachingWrites();
    });

    it("does not pass inactive events through to business controls", async () => {
      const view = render(<MemorySpace />);
      await userEvent.click(
        await screen.findByRole("button", { name: /Initial memory/ }),
      );
      await userEvent.click(screen.getByRole("button", { name: "纠正" }));
      const save = screen.getByRole("button", { name: "保存纠正" });
      const correction = screen
        .getByRole("textbox", { name: "记忆内容" })
        .closest("form");
      if (!correction) throw new Error("Correction form missing");
      // Take the surface out of active use: nothing on it may still write.
      view.rerender(<MemorySpace active={false} />);
      fireEvent.click(save);
      fireEvent.submit(correction);
      expectNoTeachingWrites();
      const queryForm = view.container.querySelector(
        '[data-tour-id="memory-query-bar"]',
      );
      if (!queryForm) throw new Error("Query form missing");
      fireEvent.submit(queryForm);
      expectNoTeachingWrites();
      view.rerender(<MemorySpace />);
      fireEvent.submit(correction);
      await waitFor(() => expect(correctMemory).toHaveBeenCalledTimes(1));
    });

    it("shows a late blocking dialog without changing the user's mode", async () => {
      const width = deferred<{
        mismatch: {
          stored_width: number;
          expected_width: number;
          graph_path: string;
        };
        memory_count: number;
      }>();
      getGraphWidth.mockReturnValue(width.promise);
      const view = render(<MemorySpace />);
      await userEvent.click(screen.getByRole("tab", { name: "问记忆" }));
      await act(async () =>
        width.resolve({
          mismatch: {
            stored_width: 768,
            expected_width: 512,
            graph_path: "/tmp/synthetic-graph",
          },
          memory_count: 1,
        }),
      );
      const dialog = await screen.findByRole("dialog", {
        name: "现有记忆库和内置模型对不上",
      });
      expectNoTeachingWrites();
      await userEvent.click(
        within(dialog).getByRole("button", { name: "就这么办" }),
      );
      expect(decideGraphWidth).toHaveBeenCalledExactlyOnceWith(
        "keep_existing",
        expect.any(String),
      );
      view.rerender(<MemorySpace />);
      // Dismissing the dialog must not move the user off the tab they chose.
      expect(
        screen
          .getByRole("tab", { name: "问记忆" })
          .getAttribute("aria-selected"),
      ).toBe("true");
    });

    it("retains an unsaved composer draft while its inactive modal is suspended", async () => {
      const view = render(<MemorySpace />);
      await userEvent.click(screen.getByRole("button", { name: "添加记忆" }));
      const draft = screen.getByLabelText("记下这件事") as HTMLTextAreaElement;
      await userEvent.type(draft, "Unsent note");
      view.rerender(<MemorySpace active={false} />);
      expect(screen.queryByRole("dialog")).toBeNull();
      const event = new KeyboardEvent("keydown", {
        key: "Escape",
        bubbles: true,
        cancelable: true,
      });
      document.dispatchEvent(event);
      expect(event.defaultPrevented).toBe(false);
      view.rerender(<MemorySpace />);
      expect(screen.getByLabelText("记下这件事")).toBe(draft);
      expect(draft.value).toBe("Unsent note");
      expectNoTeachingWrites();
    });

    it("keeps its tab anchors while loading and empty states are shown", async () => {
      const items = deferred<GovernedMemory[]>();
      getMemories.mockReturnValue(items.promise);
      getMemoryGraph.mockResolvedValue({
        nodes: [],
        edges: [],
        total_memories: 0,
      });
      const view = render(<MemorySpace />);
      expect(screen.getByText("正在读取记忆…")).toBeTruthy();
      expect(
        view.container.querySelector('[data-tour-id="memory-map-canvas"]')
          ?.className,
      ).toBe("map-canvas");
      await userEvent.click(screen.getByRole("tab", { name: "记忆库" }));
      expect(
        view.container.querySelector('[data-tour-id="memory-status-tabs"]')
          ?.tagName,
      ).toBe("NAV");
      expect(screen.getByText("正在读取记忆库…")).toBeTruthy();
      await userEvent.click(screen.getByRole("tab", { name: "问记忆" }));
      expect(
        view.container.querySelector('[data-tour-id="memory-query-bar"]')
          ?.tagName,
      ).toBe("FORM");
      await act(async () => items.resolve([]));
      view.rerender(<MemorySpace />);
      // The arrival status belongs to the workbench surface, not the ask tab.
      await userEvent.click(screen.getByRole("tab", { name: "工作台" }));
      expect(screen.getByRole("region", { name: "记忆整理状态" })).toBeTruthy();
      expect(screen.getByText("还没有保存的对话")).toBeTruthy();
      expect(screen.queryByTestId("memory-graph")).toBeNull();
      expectNoTeachingWrites();
    });
  });

  describe("未配模型的常驻提示（RL-04）", () => {
    function blockedOnModel(reason: string) {
      getMemoryProcessing.mockResolvedValue({
        pending: 0,
        running: 0,
        fresh_pending: 0,
        backlog_pending: 0,
        recent_completions: [],
        waiting_for_model: 7,
        completed: 0,
        failed: 0,
        blocked_stages: [{ kind: "extract", reason, count: 7 }],
        outside_window_pending: 0,
        extraction_window_preset: "3m",
        manual_extract_batches_planned: 0,
        manual_extract_batches_current: 0,
        active_queue: 0,
        in_flight_count: 0,
        seconds_per_completion: null,
      });
    }

    it("未配可用模型时提示常驻，并能直达配置", async () => {
      const onConfigureModel = vi.fn();
      blockedOnModel("model_profile_not_active");

      render(
        <MemorySpace
          onConnect={() => {}}
          onConfigureModel={onConfigureModel}
        />,
      );

      const prompt = await screen.findByRole("status", {
        name: "记忆未在生成",
      });
      expect(prompt.textContent).toContain("还没有启用生成模型");

      // 常驻：切到别的模式不该把它带走。
      await userEvent.click(screen.getByRole("tab", { name: "记忆库" }));
      expect(screen.getByRole("status", { name: "记忆未在生成" })).toBeTruthy();

      await userEvent.click(
        screen.getByRole("button", { name: "去配置生成模型" }),
      );
      expect(onConfigureModel).toHaveBeenCalledTimes(1);
    });

    it("提示文案走已有映射，不是写死一句", async () => {
      blockedOnModel("model_api_key_missing");

      render(<MemorySpace onConnect={() => {}} onConfigureModel={() => {}} />);

      const prompt = await screen.findByRole("status", {
        name: "记忆未在生成",
      });
      expect(prompt.textContent).toContain("缺少生成模型 API Key");
      expect(prompt.textContent).not.toContain("还没有启用生成模型");
    });

    it("模型配好之后提示消失", async () => {
      // 默认 mock 就是 blocked_stages 空、completed 1，即已在正常出记忆。
      render(<MemorySpace onConnect={() => {}} onConfigureModel={() => {}} />);

      await waitFor(() => {
        expect(screen.getByText("Initial memory", visibleText)).toBeTruthy();
      });
      expect(screen.queryByRole("status", { name: "记忆未在生成" })).toBeNull();
    });

    it("阻塞原因与模型无关时不冒充成模型问题", async () => {
      blockedOnModel("hy_memory_unavailable");

      render(<MemorySpace onConnect={() => {}} onConfigureModel={() => {}} />);

      await waitFor(() => {
        expect(screen.getByText("Initial memory", visibleText)).toBeTruthy();
      });
      expect(screen.queryByRole("status", { name: "记忆未在生成" })).toBeNull();
    });
  });

  describe("记忆库多选与批量操作（RL-27）", () => {
    const rows = [
      { ...memory, memory_id: "memory-1", content: "First", revision: 1 },
      { ...memory, memory_id: "memory-2", content: "Second", revision: 3 },
      { ...memory, memory_id: "memory-3", content: "Third", revision: 2 },
    ];

    async function openLibrary() {
      getMemories.mockResolvedValue(rows);
      mutateMemory.mockImplementation(async (id: string) => {
        const target = rows.find((item) => item.memory_id === id);
        if (!target) throw new Error("Unexpected batch target");
        return { ...target, status: "deleted", revision: target.revision + 1 };
      });
      render(<MemorySpace onConnect={() => {}} />);
      await userEvent.click(screen.getByRole("tab", { name: "记忆库" }));
      await waitFor(() => {
        expect(
          screen.getByRole("checkbox", { name: "选择：Second" }),
        ).toBeTruthy();
      });
    }

    async function confirmDeletion() {
      await userEvent.click(screen.getByRole("button", { name: "确认删除" }));
    }

    async function bulkDelete(count: number) {
      await userEvent.click(
        screen.getByRole("button", { name: `删除所选 ${count} 条` }),
      );
      await confirmDeletion();
    }

    async function select(...contents: string[]) {
      for (const content of contents) {
        await userEvent.click(
          screen.getByRole("checkbox", { name: `选择：${content}` }),
        );
      }
    }

    it("批量删除对每条各发一次请求，带各自的 revision", async () => {
      vi.spyOn(window, "confirm").mockReturnValue(true);
      await openLibrary();
      await select("First", "Second");

      await userEvent.click(
        screen.getByRole("button", { name: "删除所选 2 条" }),
      );
      await userEvent.click(screen.getByRole("button", { name: "确认删除" }));

      await waitFor(() => {
        expect(mutateMemory).toHaveBeenCalledTimes(2);
      });
      const calls = mutateMemory.mock.calls.map((call) => [
        call[0],
        call[1],
        call[2],
      ]);
      expect(calls).toContainEqual(["memory-1", "delete", 1]);
      expect(calls).toContainEqual(["memory-2", "delete", 3]);
      expect(calls.map((call) => call[0])).not.toContain("memory-3");
    });

    it("批量删除要先确认，取消则一条都不发", async () => {
      vi.spyOn(window, "confirm").mockReturnValue(false);
      await openLibrary();
      await select("First");

      await userEvent.click(
        screen.getByRole("button", { name: "删除所选 1 条" }),
      );
      await userEvent.click(screen.getByRole("button", { name: "取消" }));

      expect(mutateMemory).not.toHaveBeenCalled();
    });

    it("没选中任何一条时不提供批量操作", async () => {
      await openLibrary();

      expect(screen.queryByRole("button", { name: /删除所选/ })).toBeNull();
    });

    it("记忆库没有待确认 / 已拒绝的筛选，也没有确认 / 拒绝操作", async () => {
      await openLibrary();

      const tabs = screen.getByRole("navigation", { name: "记忆治理状态" });
      expect(
        within(tabs)
          .getAllByRole("button")
          .map((tab) => tab.textContent?.replace(/\s*\d*$/, "")),
      ).toEqual(["已可用", "已被取代", "已删除"]);

      await select("First");
      const bulk = screen.getByRole("group", { name: "批量操作" });
      expect(
        within(bulk)
          .getAllByRole("button")
          .map((button) => button.textContent),
      ).toEqual(["删除所选 1 条", "取消选择"]);

      getMemory.mockResolvedValue({ ...detail, memory: rows[0] });
      await userEvent.click(screen.getByRole("button", { name: /^First/ }));
      const inspector = await screen.findByRole("complementary", {
        name: "记忆详情",
      });
      await within(inspector).findByText("Original words");
      expect(
        within(inspector).getByRole("button", { name: "删除" }),
      ).toBeTruthy();
      expect(
        within(inspector).queryByRole("button", { name: /确认|拒绝/ }),
      ).toBeNull();
    });

    it("S1.3: changing status clears hidden targets from the next batch", async () => {
      await openLibrary();
      await select("First");
      await userEvent.click(screen.getByRole("button", { name: /已被取代/ }));
      expect(screen.queryByRole("group", { name: "批量操作" })).toBeNull();
      await userEvent.click(screen.getByRole("button", { name: /已可用/ }));
      await select("Second");
      await bulkDelete(1);
      await waitFor(() => expect(mutateMemory).toHaveBeenCalledTimes(1));
      expect(mutateMemory.mock.calls[0].slice(0, 3)).toEqual([
        "memory-2",
        "delete",
        3,
      ]);
    });

    it("S1.3: changing the search clears selection before new results arrive", async () => {
      await openLibrary();
      await select("First", "Second");
      await userEvent.type(screen.getByLabelText("搜索记忆库"), "Third");
      expect(screen.queryByRole("group", { name: "批量操作" })).toBeNull();
      expect(mutateMemory).not.toHaveBeenCalled();
    });

    it("S1.3: removed memories cannot leave a phantom selected count", async () => {
      await openLibrary();
      await select("First", "Second");
      getMemories.mockResolvedValue([rows[2]]);
      // The library surface is refreshed through the shared scheduler now;
      // one call runs exactly one tick for it.
      await act(async () => pollNow("memory-library"));
      expect(screen.queryByRole("group", { name: "批量操作" })).toBeNull();
      getMemories.mockResolvedValue(rows);
      await act(async () => pollNow("memory-library"));
      expect(
        (
          screen.getByRole("checkbox", {
            name: "选择：First",
          }) as HTMLInputElement
        ).checked,
      ).toBe(false);
    });

    it("S1.3: same-tick submits send one batch and lock its selection", async () => {
      await openLibrary();
      await select("First");
      const pending = deferred<GovernedMemory>();
      mutateMemory.mockReturnValueOnce(pending.promise);
      const remove = screen.getByRole("button", { name: "删除所选 1 条" });
      try {
        act(() => {
          remove.click();
          remove.click();
        });
        await confirmDeletion();
        expect(mutateMemory).toHaveBeenCalledTimes(1);
        expect(
          (
            screen.getByRole("checkbox", {
              name: "选择：Second",
            }) as HTMLInputElement
          ).disabled,
        ).toBe(true);
        expect(
          (
            screen.getByRole("button", {
              name: "取消选择",
            }) as HTMLButtonElement
          ).disabled,
        ).toBe(true);
        expect(
          (screen.getByLabelText("搜索记忆库") as HTMLInputElement).disabled,
        ).toBe(true);
      } finally {
        await act(async () =>
          pending.resolve({
            ...rows[0],
            status: "deleted",
            revision: 2,
          }),
        );
      }
    });

    it("S1.3: delete owns the batch lock before confirmation and cancellation releases it", async () => {
      await openLibrary();
      await select("First");
      const remove = screen.getByRole("button", { name: "删除所选 1 条" });
      act(() => {
        remove.click();
        remove.click();
      });
      try {
        expect(
          (
            screen.getByRole("button", {
              name: "删除所选 1 条",
            }) as HTMLButtonElement
          ).disabled,
        ).toBe(true);
        expect(mutateMemory).not.toHaveBeenCalled();
      } finally {
        await userEvent.click(screen.getByRole("button", { name: "取消" }));
      }
      await bulkDelete(1);
      await waitFor(() => expect(mutateMemory).toHaveBeenCalledTimes(1));
    });

    it.each(["memories", "graph"])(
      "S1.3: a failed final %s refresh releases busy without retrying saved items",
      async (panel) => {
        await openLibrary();
        await select("First", "Second");
        getMemories.mockResolvedValue([
          { ...rows[0], status: "deleted", revision: 2 },
          { ...rows[1], status: "deleted", revision: 4 },
          rows[2],
        ]);
        const reader = panel === "memories" ? getMemories : getMemoryGraph;
        reader.mockRejectedValueOnce(new Error("Final refresh unavailable"));
        await bulkDelete(2);
        await waitFor(() => expect(mutateMemory).toHaveBeenCalledTimes(2));
        await userEvent.click(
          screen.getByRole("checkbox", { name: "选择：Third" }),
        );
        await waitFor(() =>
          expect(
            (
              screen.getByRole("button", {
                name: "删除所选 1 条",
              }) as HTMLButtonElement
            ).disabled,
          ).toBe(false),
        );
        expect(
          screen.getByRole("status", { name: "批量操作结果" }).textContent,
        ).toContain("已删除 2 条");
        if (panel === "graph") {
          await waitFor(
            () => expect(getMemoryGraph.mock.calls.length).toBeGreaterThan(1),
            { timeout: 3500 },
          );
          expect(screen.queryByText(/记忆图谱读取失败/)).toBeNull();
        } else {
          expect(
            (await screen.findByRole("alert", {}, { timeout: 3500 }))
              .textContent,
          ).toContain("列表刷新失败");
        }
        expect(
          screen.queryByRole("checkbox", { name: "选择：First" }),
        ).toBeNull();
        expect(
          screen.queryByRole("checkbox", { name: "选择：Second" }),
        ).toBeNull();
        expect(mutateMemory).toHaveBeenCalledTimes(2);
      },
    );

    it("S1.3: per-item retry keeps the failed intent and never includes successful or newly selected items", async () => {
      await openLibrary();
      apiErrorCode.mockImplementation((reason: Error) => reason.message);
      mutateMemory
        .mockResolvedValueOnce({
          ...rows[0],
          status: "deleted",
          revision: 2,
        })
        .mockRejectedValueOnce(new Error("local_store_unavailable"));
      getMemories.mockResolvedValue([
        { ...rows[0], status: "deleted", revision: 2 },
        rows[1],
        rows[2],
      ]);
      await select("First", "Second");
      await bulkDelete(2);
      const failure = await screen.findByRole("group", {
        name: "处理失败：Second",
      });
      expect(failure.textContent).toContain("本地记忆库正忙");
      expect(
        screen.getByRole("status", { name: "批量操作结果" }).textContent,
      ).toContain("已删除 1 条，1 条未完成");
      expect(
        (
          screen.getByRole("checkbox", {
            name: "选择：Second",
          }) as HTMLInputElement
        ).checked,
      ).toBe(true);
      const failedCall = mutateMemory.mock.calls[1];
      await select("Third");
      await userEvent.click(
        within(failure).getByRole("button", { name: "重试删除" }),
      );
      await confirmDeletion();
      await waitFor(() => expect(mutateMemory).toHaveBeenCalledTimes(3));
      // A retry is its own traced interaction (fresh trace id); the intent that
      // must be preserved is the idempotent request id and CAS revision.
      expect(mutateMemory.mock.calls[2].slice(0, 4)).toEqual(
        failedCall.slice(0, 4),
      );
      expect(mutateMemory.mock.calls[2][4]).toEqual(expect.any(String));
      expect(
        screen.queryByRole("group", { name: "处理失败：Second" }),
      ).toBeNull();
      expect(
        (
          screen.getByRole("checkbox", {
            name: "选择：Third",
          }) as HTMLInputElement
        ).checked,
      ).toBe(true);
    });

    it.each(["memories", "graph"])(
      "S1.3: partial failure survives a %s refresh failure and only failed targets can be retried",
      async (panel) => {
        await openLibrary();
        await select("First", "Second");
        mutateMemory
          .mockResolvedValueOnce({
            ...rows[0],
            status: "deleted",
            revision: 2,
          })
          .mockRejectedValueOnce(new Error("Connection lost"));
        const reader = panel === "graph" ? getMemoryGraph : getMemories;
        reader.mockRejectedValueOnce(new Error("Refresh failed"));
        await bulkDelete(2);
        const retry = await screen.findByRole("button", {
          name: "重试失败的 1 条",
        });
        await waitFor(() =>
          expect((retry as HTMLButtonElement).disabled).toBe(false),
        );
        expect(
          screen.getByRole("group", { name: "处理失败：Second" }),
        ).toBeTruthy();
        if (panel === "graph") {
          await waitFor(
            () => expect(getMemoryGraph.mock.calls.length).toBeGreaterThan(1),
            { timeout: 3500 },
          );
          expect(screen.queryByText(/记忆图谱读取失败/)).toBeNull();
        } else {
          expect(
            (await screen.findByRole("alert", {}, { timeout: 3500 }))
              .textContent,
          ).toContain("列表刷新失败");
        }
        const failedCall = mutateMemory.mock.calls[1];
        await userEvent.click(retry);
        await confirmDeletion();
        await waitFor(() => expect(mutateMemory).toHaveBeenCalledTimes(3));
        expect(mutateMemory.mock.calls[2].slice(0, 4)).toEqual(
          failedCall.slice(0, 4),
        );
      },
    );

    it("S1.3: retry uses the original revision even if refresh returns a newer version", async () => {
      await openLibrary();
      await select("Second");
      mutateMemory.mockRejectedValueOnce(new Error("Response lost"));
      getMemories.mockResolvedValue([
        rows[0],
        { ...rows[1], revision: 4 },
        rows[2],
      ]);
      await bulkDelete(1);
      const retry = await screen.findByRole("button", { name: "重试删除" });
      const failedCall = mutateMemory.mock.calls[0];
      await userEvent.click(retry);
      await confirmDeletion();
      await waitFor(() => expect(mutateMemory).toHaveBeenCalledTimes(2));
      expect(mutateMemory.mock.calls[1].slice(0, 4)).toEqual(
        failedCall.slice(0, 4),
      );
      expect(mutateMemory.mock.calls[1][2]).toBe(3);
    });

    it("S1.3: editing a detail draft cannot replace a failed request identity", async () => {
      await openLibrary();
      getMemory.mockResolvedValue({ ...detail, memory: rows[1] });
      await select("Second");
      mutateMemory.mockRejectedValueOnce(new Error("Response lost"));
      await bulkDelete(1);
      const retry = await screen.findByRole("button", { name: "重试删除" });
      const failedCall = mutateMemory.mock.calls[0];
      await userEvent.click(screen.getByRole("button", { name: /^Second/ }));
      await userEvent.type(
        await screen.findByRole("textbox", { name: "记忆内容" }),
        " draft",
      );
      await userEvent.click(retry);
      await confirmDeletion();
      await waitFor(() => expect(mutateMemory).toHaveBeenCalledTimes(2));
      expect(mutateMemory.mock.calls[1].slice(0, 4)).toEqual(
        failedCall.slice(0, 4),
      );
      expect(correctMemory).not.toHaveBeenCalled();
    });

    it("S1.3: an idempotent receipt cannot roll back a newer visible revision when refresh fails", async () => {
      await openLibrary();
      await select("Second");
      mutateMemory.mockRejectedValueOnce(new Error("Response lost"));
      getMemories.mockResolvedValue([
        rows[0],
        { ...rows[1], content: "Second updated", revision: 5 },
        rows[2],
      ]);
      await bulkDelete(1);
      const retry = await screen.findByRole("button", { name: "重试删除" });
      mutateMemory.mockResolvedValueOnce({
        ...rows[1],
        status: "deleted",
        revision: 4,
      });
      getMemoryGraph.mockRejectedValueOnce(new Error("Graph refresh failed"));
      await userEvent.click(retry);
      await confirmDeletion();
      await waitFor(() => expect(mutateMemory).toHaveBeenCalledTimes(2));
      expect(screen.getByText("Second updated", visibleText)).toBeTruthy();
      expect(screen.queryByText("Second", visibleText)).toBeNull();
      expect(mutateMemory.mock.calls[1].slice(0, 4)).toEqual(
        mutateMemory.mock.calls[0].slice(0, 4),
      );
    });

    it("S1.3: an unknown write outcome asks for explicit retry instead of claiming an automatic retry", async () => {
      await openLibrary();
      await select("Second");
      mutateMemory.mockRejectedValueOnce(new Error("Response lost"));
      await bulkDelete(1);
      const failure = await screen.findByRole("group", {
        name: "处理失败：Second",
      });
      expect(failure.textContent).toContain("未收到操作结果，请重试");
      expect(failure.textContent).not.toContain("正在重试");
      expect(mutateMemory).toHaveBeenCalledTimes(1);
    });

    it("S1.3: revocation removes a failed row and its retry without discarding other failures", async () => {
      await openLibrary();
      await select("First", "Second");
      mutateMemory.mockRejectedValue(new Error("Response lost"));
      await bulkDelete(2);
      await screen.findByRole("button", { name: "重试失败的 2 条" });
      getMemories.mockResolvedValue([rows[1], rows[2]]);
      await act(async () => pollNow("memory-library"));
      expect(
        screen.queryByRole("group", { name: "处理失败：First" }),
      ).toBeNull();
      expect(screen.queryByText("First")).toBeNull();
      await userEvent.click(
        screen.getByRole("button", { name: "重试失败的 1 条" }),
      );
      await confirmDeletion();
      await waitFor(() => expect(mutateMemory).toHaveBeenCalledTimes(3));
      expect(mutateMemory.mock.calls[2][0]).toBe("memory-2");
    });

    it("S1.3: an older poll cannot restore a successfully processed item", async () => {
      const pollResult = deferred<GovernedMemory[]>();
      const secondMutation = deferred<GovernedMemory>();
      try {
        await openLibrary();
        await select("First", "Second");
        getMemories.mockReturnValueOnce(pollResult.promise);
        act(() => void pollNow("memory-library"));
        mutateMemory
          .mockResolvedValueOnce({
            ...rows[0],
            status: "deleted",
            revision: 2,
          })
          .mockReturnValueOnce(secondMutation.promise);
        await bulkDelete(2);
        await waitFor(() => expect(mutateMemory).toHaveBeenCalledTimes(2));
        await act(async () => pollResult.resolve(rows));
        expect(
          screen.queryByRole("checkbox", { name: "选择：First" }),
        ).toBeNull();
      } finally {
        await act(async () => {
          pollResult.resolve(rows);
          secondMutation.resolve({
            ...rows[1],
            status: "deleted",
            revision: 4,
          });
        });
      }
    });

    it("S1.3: a failing item keeps its selection while another item is committing", async () => {
      await openLibrary();
      await select("First", "Second", "Third");
      const lastMutation = deferred<GovernedMemory>();
      mutateMemory
        .mockRejectedValueOnce(new Error("Connection lost"))
        .mockResolvedValueOnce({
          ...rows[1],
          status: "deleted",
          revision: 4,
        })
        .mockReturnValueOnce(lastMutation.promise);
      try {
        await bulkDelete(3);
        await waitFor(() => expect(mutateMemory).toHaveBeenCalledTimes(3));
        expect(
          screen.getByRole("group", { name: "处理失败：First" }),
        ).toBeTruthy();
        expect(
          (
            screen.getByRole("checkbox", {
              name: "选择：First",
            }) as HTMLInputElement
          ).checked,
        ).toBe(true);
      } finally {
        await act(async () =>
          lastMutation.resolve({
            ...rows[2],
            status: "deleted",
            revision: 3,
          }),
        );
      }
    });

    it("S1.3: completing an ask cannot release a batch waiting on its mutation", async () => {
      await openLibrary();
      const stream = deferred<void>();
      const pending = deferred<GovernedMemory>();
      askMemoriesStream.mockImplementation(async function* () {
        await stream.promise;
        yield {
          type: "final",
          answer: "Answer",
          memories: [],
          mode: "generated",
        };
      });
      await userEvent.click(screen.getByRole("tab", { name: "工作台" }));
      await userEvent.type(screen.getByLabelText("向记忆提问"), "Question");
      await userEvent.click(screen.getByRole("button", { name: "发送问题" }));
      await userEvent.click(screen.getByRole("tab", { name: "记忆库" }));
      await select("First");
      mutateMemory.mockReturnValueOnce(pending.promise);
      try {
        await bulkDelete(1);
        await act(async () => stream.resolve());
        expect(
          (
            screen.getByRole("button", {
              name: "删除所选 1 条",
            }) as HTMLButtonElement
          ).disabled,
        ).toBe(true);
      } finally {
        await act(async () => {
          stream.resolve();
          pending.resolve({
            ...rows[0],
            status: "deleted",
            revision: 2,
          });
        });
      }
    });

    it("S1.3: retrying a failed deletion still requires confirmation", async () => {
      await openLibrary();
      await select("Second");
      mutateMemory.mockRejectedValueOnce(new Error("Response lost"));
      await userEvent.click(
        screen.getByRole("button", { name: "删除所选 1 条" }),
      );
      await userEvent.click(screen.getByRole("button", { name: "确认删除" }));
      const retry = await screen.findByRole("button", { name: "重试删除" });
      await userEvent.click(retry);
      expect(mutateMemory).toHaveBeenCalledTimes(1);
      await userEvent.click(screen.getByRole("button", { name: "取消" }));
      expect(
        screen.getByRole("group", { name: "处理失败：Second" }),
      ).toBeTruthy();
      await userEvent.click(retry);
      await userEvent.click(screen.getByRole("button", { name: "确认删除" }));
      await waitFor(() => expect(mutateMemory).toHaveBeenCalledTimes(2));
      expect(mutateMemory.mock.calls[1].slice(0, 4)).toEqual(
        mutateMemory.mock.calls[0].slice(0, 4),
      );
    });

    it.each([false, true])(
      "S1.3: the first saved item invalidates an old stream before later items or refresh finish (recalled=%s)",
      async (recalled) => {
        await openLibrary();
        const stream = deferred<void>();
        const secondMutation = deferred<GovernedMemory>();
        const refresh = deferred<GovernedMemory[]>();
        askMemoriesStream.mockImplementation(async function* () {
          if (recalled) {
            yield { type: "recall", memories: [rows[0]] };
            yield { type: "delta", text: "Old batch answer" };
          }
          await stream.promise;
          yield {
            type: "final",
            answer: "Late old batch answer",
            memories: [rows[0]],
            mode: "generated",
          };
        });
        await userEvent.click(screen.getByRole("tab", { name: "工作台" }));
        await userEvent.type(screen.getByLabelText("向记忆提问"), "Question");
        await userEvent.click(screen.getByRole("button", { name: "发送问题" }));
        await waitFor(() => expect(askMemoriesStream).toHaveBeenCalledTimes(1));
        await userEvent.click(screen.getByRole("tab", { name: "记忆库" }));
        await select("First", "Second");
        mutateMemory
          .mockResolvedValueOnce({
            ...rows[0],
            status: "deleted",
            revision: 2,
          })
          .mockReturnValueOnce(secondMutation.promise);
        getMemories.mockReturnValueOnce(refresh.promise);
        try {
          await bulkDelete(2);
          await waitFor(() => expect(mutateMemory).toHaveBeenCalledTimes(2));
          await userEvent.click(screen.getByRole("tab", { name: "工作台" }));
          expect(screen.queryByText("Old batch answer")).toBeNull();
          await act(async () => stream.resolve());
          expect(screen.queryByText("Late old batch answer")).toBeNull();
        } finally {
          await act(async () => {
            stream.resolve();
            secondMutation.resolve({
              ...rows[1],
              status: "deleted",
              revision: 4,
            });
            refresh.resolve(rows);
          });
        }
      },
    );
  });

  it("keeps asking until the core is up, rather than once", async () => {
    // The real sequence on every launch: the window is there before the core
    // is. This used to be tried once, so a library of 483 memories stayed
    // blank for the whole session.
    getMemories
      .mockRejectedValueOnce(new Error("core not up yet"))
      .mockResolvedValue([{ ...memory, status: "confirmed" }]);

    render(<MemorySpace onConnect={() => {}} />);
    await userEvent.click(screen.getByRole("tab", { name: "记忆库" }));

    await waitFor(() => {
      expect(screen.getByText("还没连上本地 Core，正在重试…")).toBeTruthy();
    });
    // No refresh, no navigation: the memories arrive on their own.
    await waitFor(
      () => {
        expect(screen.getByText("Initial memory", visibleText)).toBeTruthy();
      },
      { timeout: 4_000 },
    );
    expect(getMemories.mock.calls.length).toBeGreaterThan(1);
    expect(screen.queryByText("还没连上本地 Core，正在重试…")).toBeNull();
  });

  it("does not claim you have no memories while it is still reading", async () => {
    // The lie that made this invisible: an unread library and an empty one
    // said the same thing, so the failure looked like a fact about the user.
    getMemories.mockRejectedValue(new Error("core not up yet"));
    getMemoryGraph.mockRejectedValue(new Error("core not up yet"));

    render(<MemorySpace onConnect={() => {}} />);

    await waitFor(() => {
      expect(screen.getByText("正在读取记忆…")).toBeTruthy();
    });
    expect(screen.queryByText("还没有通过证据校验的记忆。")).toBeNull();
  });

  it("shows the library when only the graph is unavailable", async () => {
    // They were fetched as one, so a failing graph threw the memories away.
    getMemories.mockResolvedValue([{ ...memory, status: "confirmed" }]);
    getMemoryGraph.mockRejectedValue(new Error("graph unavailable"));

    render(<MemorySpace onConnect={() => {}} />);
    await userEvent.click(screen.getByRole("tab", { name: "记忆库" }));

    await waitFor(() => {
      expect(screen.getByText("Initial memory", visibleText)).toBeTruthy();
    });
  });

  it("shows newly available memory without waiting for a slow graph response", async () => {
    const stalled = deferred<Awaited<ReturnType<typeof getMemoryGraph>>>();
    getMemories.mockResolvedValue([{ ...memory, status: "confirmed" }]);
    getMemoryGraph.mockReturnValue(stalled.promise);
    render(<MemorySpace />);
    await userEvent.click(screen.getByRole("tab", { name: "记忆库" }));
    await waitFor(() =>
      expect(screen.getByText("Initial memory", visibleText)).toBeTruthy(),
    );
    await act(async () =>
      stalled.resolve({
        nodes: [],
        edges: [],
        total_memories: 1,
        projected_memories: 0,
      }),
    );
  });

  it("retries a failed graph read without showing it to the user", async () => {
    vi.useFakeTimers();
    getMemoryGraph.mockRejectedValueOnce(new Error("graph offline"));
    render(<MemorySpace />);
    await act(async () => {});
    expect(screen.queryByText(/记忆图谱读取失败/)).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(getMemories).toHaveBeenCalledTimes(1);
    const pending = deferred<Awaited<ReturnType<typeof getMemoryGraph>>>();
    getMemoryGraph.mockReturnValueOnce(pending.promise);
    // One library refresh cycle.
    await act(async () => vi.advanceTimersByTimeAsync(LIBRARY_REFRESH_MS));
    expect(getMemoryGraph).toHaveBeenCalledTimes(2);
    expect(getMemories).toHaveBeenCalledTimes(2);
    expect(screen.queryByText(/记忆图谱读取失败/)).toBeNull();
    await act(async () =>
      pending.resolve({
        nodes: [],
        edges: [],
        total_memories: 1,
        projected_memories: 0,
      }),
    );
    expect(screen.queryByRole("alert")).toBeNull();
    expect(mutateMemory).not.toHaveBeenCalled();
  });

  it.each(["success", "failure"])(
    "ignores an old deferred graph %s after a newer poll",
    async (outcome) => {
      vi.useFakeTimers();
      render(<MemorySpace />);
      await act(async () => {});
      fireEvent.click(screen.getByRole("tab", { name: "记忆库" }));
      fireEvent.change(screen.getByLabelText("搜索记忆库"), {
        target: { value: "Initial" },
      });
      let resolve!: (value: unknown) => void;
      let reject!: (reason: Error) => void;
      getMemoryGraph.mockReturnValueOnce(
        new Promise((done, fail) => {
          resolve = done;
          reject = fail;
        }),
      );
      const form = screen.getByLabelText("搜索记忆库").closest("form");
      expect(form).not.toBeNull();
      if (form) fireEvent.submit(form);
      await act(async () => {});
      await act(async () => vi.advanceTimersByTimeAsync(1500));
      expect(getMemoryGraph).toHaveBeenCalledTimes(2);
      // Search has finished even though its projection remains pending.
      expect(
        (screen.getByRole("button", { name: "搜索" }) as HTMLButtonElement)
          .disabled,
      ).toBe(false);
      if (outcome === "success")
        getMemoryGraph.mockRejectedValueOnce(new Error("newer graph failure"));
      await act(async () => vi.advanceTimersByTimeAsync(LIBRARY_REFRESH_MS));
      expect(getMemoryGraph).toHaveBeenCalledTimes(3);
      expect(screen.queryByText(/记忆图谱读取失败/)).toBeNull();
      expect(screen.queryByRole("alert")).toBeNull();
      await act(async () => {
        if (outcome === "success")
          resolve({
            nodes: [],
            edges: [],
            total_memories: 1,
            projected_memories: 0,
          });
        else reject(new Error("older graph failure"));
      });
      expect(screen.queryByText(/记忆图谱读取失败/)).toBeNull();
      expect(screen.queryByRole("alert")).toBeNull();
      expect(mutateMemory).not.toHaveBeenCalled();
    },
  );

  it("turns a long queue into a wait the reader can plan around", async () => {
    getMemoryProcessing.mockResolvedValue({
      pending: 677,
      fresh_pending: 0,
      backlog_pending: 677,
      recent_completions: [],
      running: 1,
      stranded_running: 0,
      waiting_for_model: 0,
      completed: 51,
      failed: 0,
      blocked_stages: [],
      outside_window_pending: 0,
      extraction_window_preset: "3m",
      manual_extract_batches_planned: 0,
      manual_extract_batches_current: 0,
      active_queue: 0,
      in_flight_count: 0,
      seconds_per_completion: 112,
    });
    render(<MemorySpace />);

    expect(
      await screen.findByText("1 处理中 · 历史积压 677 条（约 21 小时）"),
    ).toBeTruthy();
  });

  it("shows unknown results once and only retries after confirmation", async () => {
    const confirm = vi
      .spyOn(window, "confirm")
      .mockReturnValueOnce(false)
      .mockReturnValueOnce(true);
    getMemoryProcessing.mockResolvedValue({
      pending: 0,
      fresh_pending: 0,
      backlog_pending: 0,
      recent_completions: [],
      running: 0,
      stranded_running: 0,
      waiting_for_model: 0,
      completed: 43,
      failed: 2,
      unknown: 17,
      unknown_reasons: { model_content_outcome_unknown: 17 },
      blocked_stages: [],
      outside_window_pending: 0,
      extraction_window_preset: "3m",
      manual_extract_batches_planned: 0,
      manual_extract_batches_current: 0,
      active_queue: 0,
      in_flight_count: 0,
      seconds_per_completion: 20,
    });
    retryUnknownMemoryProcessing.mockResolvedValue({ requeued: 17 });
    render(<MemorySpace />);

    expect(
      await screen.findByText("有 17 段对话的整理结果不确定"),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "重新整理" }));
    expect(retryUnknownMemoryProcessing).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "重新整理" }));
    await waitFor(() =>
      expect(retryUnknownMemoryProcessing).toHaveBeenCalledOnce(),
    );
    expect(confirm).toHaveBeenCalledTimes(2);
    confirm.mockRestore();
  });

  it("splits the queue into new and catch-up rather than one total", async () => {
    // 675 as a single number reads as "stuck". Split, it says the three from
    // this morning are minutes away and the rest is the import (R-02).
    getMemoryProcessing.mockResolvedValue({
      pending: 675,
      fresh_pending: 3,
      backlog_pending: 672,
      recent_completions: ["2026-09-08T00:31:00+00:00"],
      running: 1,
      stranded_running: 0,
      waiting_for_model: 0,
      completed: 51,
      failed: 0,
      blocked_stages: [],
      outside_window_pending: 0,
      extraction_window_preset: "3m",
      manual_extract_batches_planned: 0,
      manual_extract_batches_current: 0,
      active_queue: 0,
      in_flight_count: 0,
      seconds_per_completion: 112,
    });
    render(<MemorySpace />);

    expect(
      await screen.findByText(
        "1 处理中 · 最新 3 条待处理 · 历史积压 672 条（约 21 小时）",
      ),
    ).toBeTruthy();
  });

  it("omits zero dispatch telemetry when the queue is only running", async () => {
    getMemoryProcessing.mockResolvedValue({
      pending: 2,
      fresh_pending: 2,
      backlog_pending: 0,
      recent_completions: [],
      running: 1,
      stranded_running: 0,
      waiting_for_model: 0,
      completed: 0,
      failed: 0,
      blocked_stages: [],
      outside_window_pending: 0,
      extraction_window_preset: "3m",
      manual_extract_batches_planned: 0,
      manual_extract_batches_current: 0,
      active_queue: 0,
      in_flight_count: 0,
      seconds_per_completion: 60,
    });
    render(<MemorySpace />);
    expect(
      await screen.findByText("1 处理中 · 最新 2 条待处理（约 2 分钟）"),
    ).toBeTruthy();
    expect(screen.queryByText(/待派发 0/)).toBeNull();
  });

  it("names stranded running work instead of a healthy in-flight count", async () => {
    getMemoryProcessing.mockResolvedValue({
      pending: 2,
      fresh_pending: 2,
      backlog_pending: 0,
      recent_completions: [],
      running: 2,
      stranded_running: 1,
      waiting_for_model: 0,
      completed: 0,
      failed: 0,
      blocked_stages: [],
      outside_window_pending: 0,
      extraction_window_preset: "3m",
      manual_extract_batches_planned: 0,
      manual_extract_batches_current: 0,
      active_queue: 0,
      in_flight_count: 0,
      seconds_per_completion: 60,
    });
    render(<MemorySpace />);
    expect(
      await screen.findByText(
        "2 处理中 · 最新 2 条待处理 · 1 条任务状态异常，正在恢复（约 2 分钟）",
      ),
    ).toBeTruthy();
  });

  it("does not invent a healthy zero when stranded_running is unknown", async () => {
    getMemoryProcessing.mockResolvedValue({
      pending: 2,
      fresh_pending: 2,
      backlog_pending: 0,
      recent_completions: [],
      running: 2,
      waiting_for_model: 0,
      completed: 0,
      failed: 0,
      blocked_stages: [],
      outside_window_pending: 0,
      extraction_window_preset: "3m",
      manual_extract_batches_planned: 0,
      manual_extract_batches_current: 0,
      active_queue: 0,
      in_flight_count: 0,
      seconds_per_completion: 60,
    });
    render(<MemorySpace />);

    expect(
      await screen.findByText(
        "2 处理中 · 最新 2 条待处理 · 2 条任务状态待核对（约 2 分钟）",
      ),
    ).toBeTruthy();
    expect(screen.queryByText(/任务状态异常，正在恢复/)).toBeNull();
  });

  it("names the stuck pipeline stage instead of implying there is nothing", async () => {
    getMemoryProcessing.mockResolvedValue({
      pending: 0,
      running: 0,
      waiting_for_model: 4,
      completed: 0,
      failed: 0,
      blocked_stages: [
        { kind: "extract", reason: "model_profile_not_active", count: 4 },
      ],
      outside_window_pending: 0,
      extraction_window_preset: "3m",
      manual_extract_batches_planned: 0,
      manual_extract_batches_current: 0,
      active_queue: 0,
      in_flight_count: 0,
      seconds_per_completion: null,
    });
    render(<MemorySpace />);

    expect(
      await screen.findByText("记忆抽取卡在 还没有启用生成模型（4 条）"),
    ).toBeTruthy();
  });

  it("keeps the pipeline's own retries out of the reader's way", async () => {
    getMemoryProcessing.mockResolvedValue({
      pending: 0,
      running: 1,
      waiting_for_model: 0,
      completed: 3,
      failed: 2,
      blocked_stages: [
        {
          kind: "extract",
          reason: "hy_memory_write_verification_clause_incomplete",
          count: 1,
        },
        {
          kind: "extract",
          reason: "hy_memory_write_verification_failed",
          count: 1,
        },
      ],
      outside_window_pending: 0,
      extraction_window_preset: "3m",
      manual_extract_batches_planned: 0,
      manual_extract_batches_current: 0,
      active_queue: 0,
      in_flight_count: 0,
      seconds_per_completion: null,
    });
    const { container } = render(<MemorySpace />);

    // One calm sentence, and the two stalls summed into it rather than named.
    expect(
      await screen.findByText("2 条这次没能整理成功，正在重试", {
        exact: false,
      }),
    ).toBeTruthy();
    // Neither an internal guard name nor the amber "整理需要处理" heading: the
    // pipeline is handling these itself.
    expect(container.textContent).not.toContain("hy_memory_write_verification");
    expect(screen.queryByText("整理需要处理")).toBeNull();
    expect(screen.getByText("正在整理记忆")).toBeTruthy();
  });

  it("shows recalled memories before the answer finishes streaming", async () => {
    const user = userEvent.setup();
    let releaseFinal: () => void = () => undefined;
    const finalReleased = new Promise<void>((resolve) => {
      releaseFinal = resolve;
    });
    askMemoriesStream.mockImplementation(async function* () {
      yield { type: "recall", memories: [memory] };
      yield { type: "delta", text: "Grounded" };
      await finalReleased;
      yield {
        type: "final",
        answer: "Grounded answer",
        memories: [memory],
        mode: "generated",
      };
    });
    render(<MemorySpace />);

    await user.click(await screen.findByRole("tab", { name: "问记忆" }));
    await user.type(
      screen.getByLabelText("向已确认记忆提问"),
      "我的原则是什么？",
    );
    await user.click(screen.getByRole("button", { name: "根据记忆回答" }));

    expect(await screen.findByText("正在生成", { exact: false })).toBeTruthy();
    expect(screen.getByText("Grounded", visibleText)).toBeTruthy();

    releaseFinal();

    expect(
      await screen.findByText("Grounded answer", visibleText),
    ).toBeTruthy();
    expect(
      screen.getByText("模型综合", { ...visibleText, exact: false }),
    ).toBeTruthy();
  });

  it("opens exactly one ui.action/ui.result chain for an answer and hands its id to the stream", async () => {
    // The SSE ask path uses raw `fetch`, so it bypasses the axios interceptor.
    // Without its own trace, `askMemoriesStream` sends no X-One-Memory-Trace-Id
    // and Core logs its kernel legs under a different id — the interaction shows
    // up as a half-chain (ui.action/ui.result with no kernel legs). This pins
    // both ends: one action, one matching result, and the same id on the call.
    const emitted: Array<{
      phase: string;
      status?: string;
      reason?: string;
      trace_id?: string;
    }> = [];
    (window as unknown as { oneMemory: unknown }).oneMemory = {
      logUiTrace: (input: {
        phase: string;
        status?: string;
        reason?: string;
      }) => {
        emitted.push(input);
        return Promise.resolve({ state: "logged" });
      },
    };
    askMemoriesStream.mockImplementation(async function* () {
      yield {
        type: "final",
        answer: "Grounded answer",
        memories: [memory],
        mode: "generated",
      };
    });
    try {
      render(<MemorySpace />);
      await userEvent.type(
        await screen.findByLabelText("向记忆提问"),
        "项目要求是什么？",
      );
      await userEvent.click(screen.getByRole("button", { name: "发送问题" }));
      await waitFor(() =>
        expect(emitted.filter((e) => e.phase === "result")).toHaveLength(1),
      );
      const actions = emitted.filter((e) => e.phase === "action");
      const results = emitted.filter((e) => e.phase === "result");
      expect(actions).toHaveLength(1);
      expect(results).toHaveLength(1);
      expect(actions[0].trace_id).toBe(results[0].trace_id);
      expect(results[0].status).toBe("succeeded");
      // The very same id went out on the raw-fetch request.
      expect(askMemoriesStream.mock.calls[0][3]).toBe(actions[0].trace_id);
    } finally {
      delete (window as unknown as { oneMemory?: unknown }).oneMemory;
    }
  });

  it("explains a revoked answer without calling it a Core disconnection", async () => {
    askMemoriesStream.mockImplementation(async function* () {
      yield { type: "error", code: "memory_answer_context_changed" };
    });
    render(<MemorySpace />);
    await userEvent.type(
      await screen.findByLabelText("向记忆提问"),
      "项目要求",
    );
    await userEvent.click(screen.getByRole("button", { name: "发送问题" }));
    expect(
      await screen.findByText(
        "记忆内容或使用权限已变化，回答已停止。请重新提问。",
      ),
    ).toBeTruthy();
    expect(screen.queryByText("还没连上本地 Core，正在重试…")).toBeNull();
  });

  it("opens original evidence without a confirm or reject step", async () => {
    const user = userEvent.setup();
    render(<MemorySpace />);

    await user.click(
      await screen.findByRole("button", { name: /Initial memory/ }),
    );
    expect(await screen.findByText("Original words", visibleText)).toBeTruthy();
    expect(screen.getByRole("button", { name: "纠正" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "确认" })).toBeNull();
    expect(screen.queryByRole("button", { name: "拒绝" })).toBeNull();
    expect(mutateMemory).not.toHaveBeenCalled();
  });

  it("asks across authorized project memories from the workbench", async () => {
    render(<MemorySpace />);
    await userEvent.type(
      await screen.findByLabelText("向记忆提问"),
      "项目要求是什么？",
    );
    await userEvent.click(screen.getByRole("button", { name: "发送问题" }));
    await waitFor(() =>
      // The trace id is the 4th argument (the SSE path carries its own header).
      expect(askMemoriesStream).toHaveBeenCalledWith(
        "项目要求是什么？",
        "*",
        undefined,
        expect.any(String),
        "legacy",
      ),
    );
  });

  it("uses SDK Ask v2 after authority cutover", async () => {
    getMemoryAuthority.mockResolvedValue({
      authority: "sdk",
      migration_id: "migration-1",
    });
    askMemoriesStream.mockImplementation(async function* () {
      yield {
        type: "recall",
        memories: [
          {
            memory_id: "sdk-memory-1",
            content: "SDK 原生记忆",
            layer: "l2_fact",
            status: "active",
            source: "cursor",
            gmt_created: null,
          },
        ],
      };
      yield {
        type: "final",
        schema_version: 2,
        answer: "SDK 回答",
        memories: [
          {
            memory_id: "sdk-memory-1",
            content: "SDK 原生记忆",
            layer: "l2_fact",
            status: "active",
            source: "cursor",
            gmt_created: null,
          },
        ],
        mode: "extractive",
      };
    });
    render(<MemorySpace />);
    await userEvent.type(
      await screen.findByLabelText("向记忆提问"),
      "SDK 问题",
    );
    await userEvent.click(screen.getByRole("button", { name: "发送问题" }));

    await waitFor(() =>
      expect(askMemoriesStream).toHaveBeenCalledWith(
        "SDK 问题",
        "*",
        undefined,
        expect.any(String),
        "sdk",
      ),
    );
    expect((await screen.findAllByText("SDK 回答")).length).toBeGreaterThan(0);
    expect(screen.queryByText(/revision|evidence_ids/)).toBeNull();
  });

  it("hands a saved answer to HY as a note instead of writing the memory itself", async () => {
    askMemoriesStream.mockImplementation(async function* () {
      yield {
        type: "final",
        answer: "Grounded answer",
        memories: [memory],
        mode: "generated",
      };
    });
    render(<MemorySpace />);
    await userEvent.type(
      await screen.findByLabelText("向记忆提问"),
      "项目要求是什么？",
    );
    await userEvent.click(screen.getByRole("button", { name: "发送问题" }));
    await userEvent.click(
      await screen.findByRole("button", { name: "存为记忆" }),
    );

    await waitFor(() =>
      expect(saveMemoryNote).toHaveBeenCalledWith(
        {
          request_id: expect.any(String),
          text: "Grounded answer",
          scope: "all-apps",
        },
        expect.any(String),
      ),
    );
    expect(createMemory).not.toHaveBeenCalled();
  });

  it("P0-05: local governance invalidates the answer even when the following refresh fails", async () => {
    const confirmed = { ...memory, status: "confirmed" as const };
    getMemories.mockResolvedValue([confirmed]);
    getMemory.mockResolvedValue({ ...detail, memory: confirmed });
    askMemoriesStream.mockImplementation(async function* () {
      yield {
        type: "final",
        answer: "Answer before deletion",
        memories: [confirmed],
        mode: "generated",
      };
    });
    render(<MemorySpace />);
    await userEvent.click(
      await screen.findByRole("button", { name: /Initial memory/ }),
    );
    await screen.findByText("Original words", visibleText);
    await userEvent.type(screen.getByLabelText("向记忆提问"), "查询");
    await userEvent.click(screen.getByRole("button", { name: "发送问题" }));
    await screen.findByText("Answer before deletion", visibleText);
    getMemories.mockRejectedValue(
      new Error("read failed after successful mutation"),
    );
    mutateMemory.mockResolvedValue({
      ...confirmed,
      status: "deleted",
      revision: 2,
    });
    await userEvent.click(screen.getByRole("button", { name: "删除" }));
    await userEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(mutateMemory).toHaveBeenCalled());
    expect(screen.queryByText("Answer before deletion")).toBeNull();
    expect(screen.queryByRole("button", { name: "带出回答与引用" })).toBeNull();
  });

  it.each([false, true])(
    "invalidates answers after the recalled memory changes (streaming=%s)",
    async (streaming) => {
      let release: () => void = () => {};
      const waiting = new Promise<void>((resolve) => {
        release = resolve;
      });
      askMemoriesStream.mockImplementation(async function* () {
        yield { type: "recall", memories: [memory] };
        yield { type: "delta", text: "Old answer" };
        if (streaming) await waiting;
        yield {
          type: "final",
          answer: "Old final answer",
          memories: [memory],
          mode: "generated",
        };
      });
      try {
        render(<MemorySpace />);
        await userEvent.click(
          await screen.findByRole("tab", { name: "问记忆" }),
        );
        await userEvent.type(screen.getByLabelText("向已确认记忆提问"), "查询");
        await userEvent.click(
          screen.getByRole("button", { name: "根据记忆回答" }),
        );
        expect(
          await screen.findByText(
            streaming ? "Old answer" : "Old final answer",
            visibleText,
          ),
        ).toBeTruthy();
        getMemories.mockResolvedValue([
          { ...memory, revision: 2, status: "deleted" },
        ]);
        await act(async () => pollNow("memory-library"));
        expect(screen.queryByText("Old answer")).toBeNull();
        expect(screen.queryByText("Old final answer")).toBeNull();
        await act(async () => release());
        expect(screen.queryByText("Old final answer")).toBeNull();
      } finally {
        release();
      }
    },
  );

  it("ignores a late detail response after another memory is selected", async () => {
    const second = {
      ...memory,
      memory_id: "memory-2",
      content: "Second memory",
    };
    getMemories.mockResolvedValue([memory, second]);
    let resolveFirst: (value: MemoryDetail) => void = () => {};
    getMemory.mockImplementation((id: string) =>
      id === memory.memory_id
        ? new Promise((resolve) => {
            resolveFirst = resolve;
          })
        : Promise.resolve({
            ...detail,
            memory: second,
            evidence: [{ ...detail.evidence[0], verbatim: "Second original" }],
          }),
    );
    render(<MemorySpace />);
    await userEvent.click(
      await screen.findByRole("button", { name: /Initial memory/ }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: /Second memory/ }),
    );
    expect(
      await screen.findByText("Second original", visibleText),
    ).toBeTruthy();
    await act(async () => resolveFirst(detail));
    await userEvent.click(screen.getByRole("button", { name: "纠正" }));
    expect(
      screen.getByRole("textbox", { name: "记忆内容" }).getAttribute("name"),
    ).toBe("memory-content");
    expect(
      (screen.getByRole("textbox", { name: "记忆内容" }) as HTMLTextAreaElement)
        .value,
    ).toBe("Second memory");
    expect(screen.queryByText("Original words")).toBeNull();
  });

  it("saves a correction without editing the evidence field", async () => {
    const user = userEvent.setup();
    render(<MemorySpace />);
    await user.click(
      await screen.findByRole("button", { name: /Initial memory/ }),
    );

    await user.click(await screen.findByRole("button", { name: "纠正" }));
    const content = await screen.findByRole("textbox", { name: "记忆内容" });
    await user.clear(content);
    await user.type(content, "Corrected memory");
    await user.click(screen.getByRole("button", { name: "保存纠正" }));

    await waitFor(() =>
      expect(correctMemory).toHaveBeenCalledWith(
        "memory-1",
        1,
        "Corrected memory",
        ["all-apps"],
        expect.any(String),
        expect.any(String),
      ),
    );
    expect(screen.getByText("Original words", visibleText)).toBeTruthy();
  });

  it("says search is behind, and offers to try again", async () => {
    // A correction that only landed locally leaves the user reading the new
    // text while search matches the old one. Nothing on screen said so, and
    // nothing offered to fix it.
    getMemory.mockResolvedValue({
      ...detail,
      index_sync: {
        memory_id: "memory-1",
        status: "pending",
        attempts: 1,
        last_error_code: "hy_memory_update_failed",
        updated_at: "2026-09-01T00:00:00Z",
      },
    });
    const user = userEvent.setup();
    render(<MemorySpace />);
    await user.click(
      await screen.findByRole("button", { name: /Initial memory/ }),
    );

    expect(
      await screen.findByText(/搜索里还是改之前那句话/, visibleText),
    ).toBeTruthy();
    expect(
      screen.getAllByText(/同步尝试 1 次 · 状态更新于/).length,
    ).toBeGreaterThan(0);
    await user.click(screen.getByRole("button", { name: "重试同步" }));

    await waitFor(() =>
      expect(syncMemoryIndex).toHaveBeenCalledWith(
        "memory-1",
        expect.any(String),
      ),
    );
  });

  it("does not offer a retry that cannot succeed", async () => {
    // HY has no such memory to re-embed, so every retry ends the same way.
    // The button would be a loop the user has to decide to stop.
    getMemory.mockResolvedValue({
      ...detail,
      index_sync: {
        memory_id: "memory-1",
        status: "pending",
        attempts: 4,
        last_error_code: "hy_memory_update_rejected",
        updated_at: "2026-09-01T00:00:00Z",
      },
    });
    const user = userEvent.setup();
    render(<MemorySpace />);
    await user.click(
      await screen.findByRole("button", { name: /Initial memory/ }),
    );

    expect(
      screen.getAllByText(/HY Memory 里没有这条记忆/).length,
    ).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "重试同步" })).toBeNull();
  });

  it("does not call a 200 pending index response a successful sync", async () => {
    getMemory.mockResolvedValue({
      ...detail,
      index_sync: {
        memory_id: "memory-1",
        status: "pending",
        attempts: 2,
        last_error_code: "hy_memory_update_failed",
        updated_at: "2026-09-01T00:00:00Z",
      },
    });
    syncMemoryIndex.mockResolvedValueOnce({
      memory_id: "memory-1",
      status: "pending",
      attempts: 3,
      last_error_code: "hy_memory_update_failed",
      updated_at: "2026-09-01T00:01:00Z",
    });
    render(<MemorySpace />);
    await userEvent.click(
      await screen.findByRole("button", { name: /Initial memory/ }),
    );
    await userEvent.click(
      await screen.findByRole("button", { name: "重试同步" }),
    );
    await waitFor(() =>
      expect(syncMemoryIndex).toHaveBeenCalledWith(
        "memory-1",
        expect.any(String),
      ),
    );
    expect(syncMemoryIndex.mock.calls[0][1]).toMatch(/^[0-9a-f-]{36}$/);
    await waitFor(() =>
      expect(screen.getAllByText(/同步尝试 2 次/).length).toBeGreaterThan(0),
    );
  });

  it("stays quiet about the index when nobody corrected the memory", async () => {
    // The control: a sync line on every memory would make the user account
    // for a state they never caused.
    const user = userEvent.setup();
    render(<MemorySpace />);
    await user.click(
      await screen.findByRole("button", { name: /Initial memory/ }),
    );

    expect(await screen.findByText("Original words", visibleText)).toBeTruthy();
    expect(screen.queryByText(/搜索里还是改之前那句话/)).toBeNull();
    expect(screen.queryByRole("button", { name: "重试同步" })).toBeNull();
  });

  describe("宽度不一致的三选一（A-33）", () => {
    it("记忆空间一打开就把选择摆出来，不等抽取先失败一轮", async () => {
      getGraphWidth.mockResolvedValue({
        mismatch: {
          stored_width: 768,
          expected_width: 512,
          graph_path: "/tmp/graph.kuzu",
        },
        memory_count: 427,
      });

      render(<MemorySpace />);

      expect(
        await screen.findByRole("dialog", {
          name: "现有记忆库和内置模型对不上",
        }),
      ).toBeTruthy();
    });

    it("宽度一致时不摆这个对话框", async () => {
      render(<MemorySpace />);

      await waitFor(() => {
        expect(getGraphWidth).toHaveBeenCalled();
      });
      expect(screen.queryByRole("dialog")).toBeNull();
    });
  });

  describe("抽取窗口与三态（M-21）", () => {
    function withDeferred(outside: number, extra: object = {}) {
      getMemoryProcessing.mockResolvedValue({
        pending: 3,
        running: 1,
        fresh_pending: 3,
        backlog_pending: 0,
        recent_completions: [],
        waiting_for_model: 0,
        completed: 51,
        failed: 0,
        blocked_stages: [],
        outside_window_pending: outside,
        extraction_window_preset: "3m",
        manual_extract_batches_planned: 0,
        manual_extract_batches_current: 0,
        active_queue: 0,
        in_flight_count: 0,
        seconds_per_completion: null,
        ...extra,
      });
    }

    it("窗口外的条数单独说，并讲明原文还搜得到", async () => {
      withDeferred(672);

      render(<MemorySpace />);

      expect(
        await screen.findByText(/672 条在窗口外（原文可搜）/),
      ).toBeTruthy();
    });

    it("手动提炼的批次进度按已规划 N 批 当前第 M 批显示", async () => {
      withDeferred(647, {
        manual_extract_batches_planned: 27,
        manual_extract_batches_current: 2,
        active_queue: 0,
        in_flight_count: 0,
      });

      render(<MemorySpace />);

      expect(await screen.findByText(/已规划 27 批，当前第 2 批/)).toBeTruthy();
    });

    it("切换档位把选择送到后端", async () => {
      const user = userEvent.setup();
      withDeferred(672);

      render(<MemorySpace />);

      const select = await screen.findByLabelText("自动提炼范围");
      await user.selectOptions(select, "1y");

      await waitFor(() => {
        expect(setExtractionWindow).toHaveBeenCalledWith(
          "1y",
          expect.any(String),
        );
      });
    });

    it("手动提炼一次只要一批，不是把窗口外全塞进去", async () => {
      const user = userEvent.setup();
      withDeferred(672);

      render(<MemorySpace />);

      await user.click(
        await screen.findByRole("button", { name: "手动提炼下一批" }),
      );

      await waitFor(() => {
        expect(manualExtract).toHaveBeenCalledTimes(1);
      });
      // 不带 id 就是「下一批」：批量大小由 Core 定，界面不负责一次塞完。
      expect(manualExtract).toHaveBeenCalledWith([], expect.any(String));
    });

    it("没有窗口外的条目时不摆一个提炼不动的按钮", async () => {
      withDeferred(0);

      render(<MemorySpace />);

      await screen.findByLabelText("自动提炼范围");
      expect(
        screen.queryByRole("button", { name: "手动提炼下一批" }),
      ).toBeNull();
    });

    it("原话标出窗口外，已提炼的不加多余标签", async () => {
      const user = userEvent.setup();
      getMemory.mockResolvedValue({
        ...detail,
        evidence: [
          { ...detail.evidence[0], extraction_state: "outside_window" },
          {
            ...detail.evidence[0],
            evidence_id: "evidence-2",
            verbatim: "Extracted words",
            extraction_state: "extracted",
          },
        ],
      });

      render(<MemorySpace />);
      await user.click(
        await screen.findByRole("button", { name: /Initial memory/ }),
      );

      expect(
        await screen.findByText("未提炼 · 在窗口外", visibleText),
      ).toBeTruthy();
      expect(screen.getByText("Extracted words", visibleText)).toBeTruthy();
      expect(screen.queryByText("已提炼")).toBeNull();
    });
  });

  describe("没有生成模型时的落点", () => {
    it("没有活跃档案也落在工作台，不再绕道历史对话", async () => {
      // 原来这里会跳去历史对话，理由是「地图、问答、记忆库此刻都是空的」。
      // 工作台把这个前提拿掉了：一条记忆都没有时它照样画出已导入的来源、
      // 列出事项、说清楚抽取卡在哪。绕过去反而让人错过唯一解释这个 App
      // 是干什么的那一屏。
      getModelProfiles.mockResolvedValue([
        { profile_id: "profile-1", is_active: false },
      ]);

      render(<MemorySpace />);

      expect(
        (await screen.findByRole("tab", { name: "工作台" })).getAttribute(
          "aria-selected",
        ),
      ).toBe("true");
      expect(
        screen.queryByRole("textbox", { name: "搜索历史对话" }),
      ).toBeNull();
    });

    it("历史对话仍旧一点就到", async () => {
      const user = userEvent.setup();
      getModelProfiles.mockResolvedValue([
        { profile_id: "profile-1", is_active: false },
      ]);
      render(<MemorySpace />);

      await user.click(await screen.findByRole("tab", { name: "历史对话" }));

      expect(
        screen.getByRole("textbox", { name: "搜索历史对话" }),
      ).toBeTruthy();
    });
  });
});
