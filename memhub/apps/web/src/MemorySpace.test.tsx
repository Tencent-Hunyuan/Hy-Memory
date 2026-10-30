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

const _detail: MemoryDetail = {
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
  getSdkRaw,
  searchSdkRaw,
  getMemoryProcessing,
  askMemoriesStream,
  setExtractionWindow,
  manualExtract,
  retryUnknownMemoryProcessing,
  getGraphWidth,
  decideGraphWidth,
  getModelProfiles,
  getMemoryAddStatus,
  addMemory,
} = vi.hoisted(() => ({
  apiErrorCode: vi.fn(),
  getSdkRaw: vi.fn(),
  searchSdkRaw: vi.fn(),
  getMemoryProcessing: vi.fn(),
  askMemoriesStream: vi.fn(),
  setExtractionWindow: vi.fn(),
  manualExtract: vi.fn(),
  retryUnknownMemoryProcessing: vi.fn(),
  getGraphWidth: vi.fn(),
  decideGraphWidth: vi.fn(),
  getModelProfiles: vi.fn(),
  getMemoryAddStatus: vi.fn(),
  addMemory: vi.fn(),
}));

const _LIBRARY_REFRESH_MS = 10_000;

vi.mock("./api", () => ({
  apiErrorCode,
  getSdkRaw,
  searchSdkRaw,
  getMemoryProcessing,
  askMemoriesStream,
  setExtractionWindow,
  manualExtract,
  retryUnknownMemoryProcessing,
  getGraphWidth,
  decideGraphWidth,
  getModelProfiles,
  getMemoryAddStatus,
  addMemory,
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
    getSdkRaw.mockResolvedValue({
      items: [],
      total: 0,
      offset: 0,
      limit: 100,
    });
    searchSdkRaw.mockResolvedValue([]);
    apiErrorCode.mockReturnValue(null);
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
    getGraphWidth.mockResolvedValue({ mismatch: null, memory_count: 1 });
    decideGraphWidth.mockResolvedValue({
      archived_path: null,
      mismatch: null,
    });
    getModelProfiles.mockResolvedValue([
      { profile_id: "profile-1", is_active: true },
    ]);
    getMemoryAddStatus.mockRejectedValue(new Error("not found"));
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
        setExtractionWindow,
        manualExtract,
        decideGraphWidth,
        addMemory,
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
      await userEvent.type(draft, "Unsent draft");
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
      expect(draft.value).toBe("Unsent draft");
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
  });

  it("does not claim you have no memories while it is still reading", async () => {
    // The lie that made this invisible: an unread library and an empty one
    // said the same thing, so the failure looked like a fact about the user.

    render(<MemorySpace onConnect={() => {}} />);

    await waitFor(() => {
      expect(screen.getByText("正在读取记忆…")).toBeTruthy();
    });
    expect(screen.queryByText("还没有通过证据校验的记忆。")).toBeNull();
  });

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
      ),
    );
  });

  it("answers from SDK Ask v2 without governed fields", async () => {
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
      ),
    );
    expect((await screen.findAllByText("SDK 回答")).length).toBeGreaterThan(0);
    expect(screen.queryByText(/revision|evidence_ids/)).toBeNull();
  });

  it("hands a saved answer to HY as an added memory instead of writing the memory itself", async () => {
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
      expect(addMemory).toHaveBeenCalledWith(
        {
          request_id: expect.any(String),
          text: "Grounded answer",
          scope: "all-apps",
        },
        expect.any(String),
      ),
    );
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

      // The history tab is the SDK raw history: its search reads v2 context.
      expect(screen.getByRole("region", { name: "SDK 原始对话" })).toBeTruthy();
    });
  });
});
