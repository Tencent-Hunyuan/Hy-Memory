// @vitest-environment jsdom
import type { SourceResponse } from "@one-memory/contracts";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "./gui-language";
import { MemorySpace } from "./MemorySpace";
import { queryClient, resetClient } from "./query-client";

it("translates memory filters and detail affordances live without refetching", async () => {
  setup();
  delete window.oneMemory;
  localStorage.setItem(LANGUAGE_STORAGE_KEY, "es");
  render(
    <LanguageProvider>
      <MemorySpace simplified />
    </LanguageProvider>,
  );
  await screen.findByRole("heading", { name: "Memorias", level: 1 });
  await screen.findAllByText("已保存的记忆");
  expect(
    screen.getByRole("textbox", { name: "Filtrar memorias del mapa" }),
  ).toBeTruthy();
  expect(screen.getByRole("button", { name: "Buscar" })).toBeTruthy();
  const calls = reads.sdkLibrary.mock.calls.length;
  act(() => {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, "zh-Hans");
    window.dispatchEvent(
      new StorageEvent("storage", { key: LANGUAGE_STORAGE_KEY }),
    );
  });
  expect(screen.getByRole("heading", { name: "记忆", level: 1 })).toBeTruthy();
  expect(screen.getByRole("button", { name: "搜索" })).toBeTruthy();
  expect(reads.sdkLibrary).toHaveBeenCalledTimes(calls);
  localStorage.clear();
});

it("localiza cola y estimación sin alterar las memorias", async () => {
  setup();
  delete window.oneMemory;
  reads.processing.mockResolvedValue({
    running: 1,
    pending: 2,
    fresh_pending: 1,
    backlog_pending: 1,
    in_flight_count: 1,
    active_queue: 1,
    seconds_per_completion: 60,
    completed: 0,
    blocked_stages: [],
  });
  localStorage.setItem(LANGUAGE_STORAGE_KEY, "es");
  render(
    <LanguageProvider>
      <MemorySpace />
    </LanguageProvider>,
  );
  expect(
    await screen.findByText(
      /1 en procesamiento · 1 recientes pendientes · 1 del historial pendientes/,
    ),
  ).toBeTruthy();
  expect(screen.getByText(/aprox. 2 minutos/)).toBeTruthy();
  localStorage.clear();
});

const { reads } = vi.hoisted(() => ({
  reads: {
    sdkLibrary: vi.fn(),
    sdkGraph: vi.fn(),
    processing: vi.fn(),
    profiles: vi.fn(),
    raw: vi.fn(),
    rawGraph: vi.fn(),
    rawSearch: vi.fn(),
    evolve: vi.fn(),
  },
}));
vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  getSdkMemoryLibrary: reads.sdkLibrary,
  getSdkMemoryGraph: reads.sdkGraph,
  getMemoryProcessing: reads.processing,
  getModelProfiles: reads.profiles,
  getSdkRaw: reads.raw,
  getSdkRawGraph: reads.rawGraph,
  searchSdkRaw: reads.rawSearch,
  getEvolveOverview: reads.evolve,
}));
vi.mock("./GraphWidthPrompt", () => ({ GraphWidthPrompt: () => null }));
vi.mock("./MemoryAddComposer", () => ({
  MemoryAddComposer: ({
    onPendingChange,
    rawOnly,
  }: {
    onPendingChange?(addition: {
      evidenceId: string;
      text: string;
      state: string;
    }): void;
    rawOnly?: boolean;
  }) => (
    <button
      type="button"
      onClick={() =>
        onPendingChange?.({
          evidenceId: "addition-1",
          text: "项目周会统一安排在周三上午。",
          state: "waiting_for_model",
        })
      }
    >
      {rawOnly ? "手动添加" : "补充记忆"}
    </button>
  ),
}));
vi.mock("./MemoryGraph3D", () => ({
  MemoryGraph3D: ({ graph }: { graph?: { nodes?: unknown[] } | null }) => (
    <div
      data-testid="graph-owner"
      data-graph-nodes={graph?.nodes?.length ?? 0}
    />
  ),
}));
afterEach(() => {
  cleanup();
  resetClient();
  vi.useRealTimers();
  vi.clearAllMocks();
});
it("translates the model-required prompt without configuring a model", async () => {
  setup();
  reads.processing.mockResolvedValue({
    running: 0,
    pending: 0,
    completed: 0,
    blocked_stages: [
      { kind: "extract", reason: "waiting_for_model", count: 4 },
    ],
  });
  delete window.oneMemory;
  localStorage.setItem(LANGUAGE_STORAGE_KEY, "es");
  const configure = vi.fn();
  render(
    <LanguageProvider>
      <MemorySpace simplified onConfigureModel={configure} />
    </LanguageProvider>,
  );
  expect(
    await screen.findByRole("status", {
      name: "Generación de memorias detenida",
    }),
  ).toHaveProperty(
    "textContent",
    "Aún no se generan memorias: Esperando un modelo disponible (4 pendientes)Configurar modelo de generación",
  );
  expect(configure).not.toHaveBeenCalled();
  localStorage.clear();
});

it("keeps the memory snapshot visible during Evolve and refreshes when it ends", async () => {
  setup();
  reads.processing.mockResolvedValue(modeProcessing("ultra"));
  reads.evolve.mockResolvedValue({
    mode: "ultra",
    schedule: { enabled: true, time: "03:00" },
    users: {},
    running: [
      {
        run_id: "run-1",
        user_id: "local",
        state: "running",
      },
    ],
  });
  render(<MemorySpace simplified />);

  expect((await screen.findAllByText("已保存的记忆")).length).toBeGreaterThan(
    0,
  );
  expect(await screen.findByText("正在整合记忆…")).toBeTruthy();
  const readsBeforeCompletion = reads.sdkLibrary.mock.calls.length;

  act(() => {
    queryClient.setQueryData(["evolve", "local"], {
      mode: "ultra",
      schedule: { enabled: true, time: "03:00" },
      users: {},
      running: [],
    });
  });

  await waitFor(() =>
    expect(reads.sdkLibrary.mock.calls.length).toBeGreaterThan(
      readsBeforeCompletion,
    ),
  );
});

it("keeps the last successful graph when its Evolve refresh fails", async () => {
  setup();
  reads.processing.mockResolvedValue(modeProcessing("ultra"));
  reads.evolve.mockResolvedValue({
    mode: "ultra",
    schedule: { enabled: true, time: "03:00" },
    users: {},
    running: [{ run_id: "run-1", user_id: "local", state: "running" }],
  });
  const firstSource = {
    source_id: "cursor",
    display_name: "Cursor",
    authorized: true,
    sync_enabled: true,
    imported_messages: 1,
    imported_conversations: 1,
  } as SourceResponse;
  const view = render(<MemorySpace simplified sources={[firstSource]} />);

  await waitFor(() =>
    expect(screen.getByTestId("graph-owner").dataset.graphNodes).toBe("1"),
  );
  reads.sdkGraph.mockRejectedValueOnce(new Error("evolve write lock"));

  view.rerender(
    <MemorySpace
      simplified
      sources={[
        firstSource,
        {
          ...firstSource,
          source_id: "codex",
          display_name: "Codex",
        },
      ]}
    />,
  );

  await waitFor(() => expect(reads.sdkGraph).toHaveBeenCalledTimes(2));
  expect(screen.getByTestId("graph-owner").dataset.graphNodes).toBe("1");
});

function setup() {
  reads.evolve.mockResolvedValue({
    mode: "ultra",
    schedule: { enabled: true, time: "03:00" },
    users: {},
    running: [],
  });
  const memories = [
    {
      memory_id: "m1",
      content: "已保存的记忆",
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
      gmt_created: 1_761_000_000,
    },
  ];
  reads.sdkLibrary.mockResolvedValue({
    memories,
    total: memories.length,
    offset: 0,
    limit: 100,
  });
  reads.sdkGraph.mockResolvedValue({
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
    hy_graph_available: false,
  });
  reads.processing.mockResolvedValue({
    running: 0,
    pending: 0,
    blocked_stages: [],
    completed: 1,
  });
  reads.profiles.mockResolvedValue([]);
  reads.raw.mockResolvedValue({
    items: [],
    total: 0,
    limit: 100,
    offset: 0,
  });
  reads.rawGraph.mockResolvedValue({
    user_id: "local",
    items: [
      {
        turn_id: "raw-1",
        raw_id: "raw-1",
        label: "原始对话内容",
        source: "cursor",
        session_id: "session-1",
        memory_at: null,
        gmt_created: 1_761_000_000,
      },
    ],
    total: 1,
    limit: 300,
    offset: 0,
    has_more: false,
  });
  reads.rawSearch.mockResolvedValue([]);
}

function modeProcessing(mode: "lite" | "pro" | "ultra") {
  return {
    running: 0,
    pending: 0,
    blocked_stages: [],
    completed: 1,
    pipeline: {
      mode,
      configured_mode: mode,
      forced_by_env: false,
      evolve: {
        mode,
        schedule: { enabled: true, time: "03:00" },
        users: {},
        running: [],
      },
      last_evolve_at: null,
      graph_build: {
        state: "idle",
        started_at: null,
        finished_at: null,
        users: {},
      },
      features: {},
    },
  };
}

it("shows raw conversations instead of advanced memory in lite", async () => {
  setup();
  reads.processing.mockResolvedValue({
    running: 0,
    pending: 0,
    blocked_stages: [],
    completed: 1,
    pipeline: {
      mode: "lite",
      configured_mode: "lite",
      forced_by_env: false,
      evolve: {
        mode: "lite",
        schedule: { enabled: true, time: "03:00" },
        users: {},
        running: [],
      },
      last_evolve_at: null,
      graph_build: {
        state: "idle",
        started_at: null,
        finished_at: null,
        users: {},
      },
      features: {},
    },
  });
  reads.raw.mockResolvedValue({
    items: [
      {
        raw_id: "raw-1",
        content: "原始对话内容",
        user_id: "local",
        agent_id: "all-apps",
        source: "cursor",
        session_id: "session-1",
        memory_at: null,
        gmt_created: 1_761_000_000,
        custom: {},
        messages: [{ role: "user", content: "原始对话内容" }],
      },
    ],
    total: 1,
    limit: 20,
    offset: 0,
  });

  render(<MemorySpace simplified />);

  expect(await screen.findAllByText("原始对话内容")).toHaveLength(2);
  expect(screen.getByText("1 条原始对话")).toBeTruthy();
  expect(screen.queryByText("找到 1 条")).toBeNull();
  expect(
    screen.getByRole("heading", { level: 2, name: "来源归属" }),
  ).toBeTruthy();
  expect(
    document.querySelector(".core-memory-map")?.hasAttribute("hidden"),
  ).toBe(false);
  expect(screen.getByTestId("graph-owner")).toBeTruthy();
  expect(screen.getByRole("textbox", { name: "搜索原始对话" })).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "手动添加" }));
  expect(
    screen.getByText(/正在保存… 项目周会统一安排在周三上午。/),
  ).toBeTruthy();
  await userEvent.click(screen.getByRole("combobox", { name: "来源" }));
  expect(screen.getByRole("option", { name: "手动添加" })).toBeTruthy();
  expect(reads.sdkLibrary).not.toHaveBeenCalled();
  expect(reads.sdkGraph).not.toHaveBeenCalled();
  expect(reads.raw).toHaveBeenCalledWith(
    expect.objectContaining({
      source: undefined,
      offset: 0,
      limit: 20,
      signal: expect.any(AbortSignal),
    }),
  );
  expect(document.querySelector(".memory-mode-label")?.textContent).toBe(
    "lite",
  );
  expect(document.querySelector(".memory-mode-notice")).toBeNull();
});

it("loads one bounded raw graph page without claiming an exact turn total", async () => {
  setup();
  reads.processing.mockResolvedValue(modeProcessing("lite"));
  reads.rawGraph.mockResolvedValue({
    user_id: "local",
    items: Array.from({ length: 300 }, (_, index) => ({
      turn_id: `turn-${index}`,
      raw_id: `raw-${index}`,
      label: `原始会话 ${index}`,
      source: "cursor",
      session_id: `session-${index}`,
      memory_at: null,
      gmt_created: 1_761_000_000,
    })),
    total: 1_600,
    limit: 300,
    offset: 0,
    has_more: true,
  });

  render(<MemorySpace simplified />);

  await waitFor(() => expect(reads.rawGraph).toHaveBeenCalledTimes(1));
  expect(reads.rawGraph).toHaveBeenCalledWith({
    source: undefined,
    offset: 0,
    limit: 300,
    signal: expect.any(AbortSignal),
  });
  expect(screen.getByTestId("graph-owner").dataset.graphNodes).toBe("301");
  expect(document.querySelector(".memory-loaded-count")?.textContent).not.toBe(
    "1,600 条原始对话",
  );
  expect(screen.queryByText("1,600 / 1,600")).toBeNull();
});

it("keeps the last successful Lite graph when a background refresh fails", async () => {
  setup();
  reads.processing.mockResolvedValue(modeProcessing("lite"));
  const source = {
    source_id: "cursor",
    display_name: "Cursor",
    authorized: true,
    imported_messages: 1,
    imported_conversations: 1,
  } as SourceResponse;
  const view = render(<MemorySpace simplified sources={[source]} />);

  await waitFor(() =>
    expect(screen.getByTestId("graph-owner").dataset.graphNodes).toBe("2"),
  );
  reads.rawGraph.mockRejectedValueOnce(new Error("refresh failed"));
  view.rerender(
    <MemorySpace
      simplified
      sources={[
        source,
        {
          ...source,
          source_id: "codex",
          display_name: "Codex",
        },
      ]}
    />,
  );

  await waitFor(() => expect(reads.rawGraph).toHaveBeenCalledTimes(2));
  expect(screen.getByTestId("graph-owner").dataset.graphNodes).toBe("2");
  expect(screen.queryByText("原始对话暂时无法读取。")).toBeNull();
});

it("does not request the raw graph for a keyword search", async () => {
  setup();
  reads.processing.mockResolvedValue(modeProcessing("lite"));
  reads.rawSearch.mockResolvedValue([]);
  const user = userEvent.setup();
  render(<MemorySpace simplified />);

  await waitFor(() => expect(reads.rawGraph).toHaveBeenCalledTimes(1));
  await user.type(
    screen.getByRole("textbox", { name: "搜索原始对话" }),
    "发布计划",
  );
  await user.click(screen.getByRole("button", { name: "搜索" }));
  await waitFor(() => expect(reads.rawSearch).toHaveBeenCalledTimes(1));
  expect(reads.rawGraph).toHaveBeenCalledTimes(1);
});

it("pages and filters the raw conversation data plane in lite", async () => {
  setup();
  reads.processing.mockResolvedValue(modeProcessing("lite"));
  reads.raw.mockImplementation(async (options = {}) => {
    const cursor = "cursor" in options ? options.cursor : null;
    const source = "source" in options ? options.source : undefined;
    const rawId = `${source ?? "all"}-${cursor ? "more" : "first"}`;
    return {
      items: [
        {
          raw_id: rawId,
          content: `原始会话 ${rawId}`,
          user_id: "local",
          agent_id: "all-apps",
          source: source ?? "cursor",
          session_id: `session-${rawId}`,
          memory_at: null,
          gmt_created: 1_761_000_000,
          custom: {},
          messages: [{ role: "user", content: `原始会话 ${rawId}` }],
        },
      ],
      total: cursor ? 2 : 1,
      limit: 20,
      offset: cursor ? 1 : 0,
      next_cursor: !source && !cursor ? "turn:1:0:1" : null,
      has_more: !source && !cursor,
      total_exact: false,
    };
  });
  const user = userEvent.setup();
  render(
    <MemorySpace
      simplified
      sources={
        [
          {
            source_id: "cursor",
            display_name: "Cursor",
            authorized: true,
          },
        ] as SourceResponse[]
      }
    />,
  );

  expect(await screen.findAllByText("原始会话 all-first")).toHaveLength(2);
  await waitFor(() => expect(reads.rawGraph).toHaveBeenCalledTimes(1));
  const graphCalls = reads.rawGraph.mock.calls.length;
  await user.click(screen.getByRole("button", { name: "加载更多" }));
  expect(await screen.findAllByText("原始会话 all-more")).toHaveLength(1);
  expect(screen.getAllByText("原始会话 all-first")).toHaveLength(2);
  expect(reads.rawGraph).toHaveBeenCalledTimes(graphCalls);
  expect(reads.raw).toHaveBeenLastCalledWith(
    expect.objectContaining({
      source: undefined,
      cursor: "turn:1:0:1",
      paging: "cursor",
      limit: 20,
    }),
  );

  await user.click(screen.getByRole("combobox", { name: "来源" }));
  await user.click(screen.getByRole("option", { name: "Cursor" }));
  expect(await screen.findAllByText("原始会话 cursor-first")).toHaveLength(2);
  expect(reads.raw).toHaveBeenLastCalledWith(
    expect.objectContaining({
      source: "cursor",
      cursor: null,
      paging: "cursor",
      limit: 20,
    }),
  );

  await user.click(screen.getByRole("combobox", { name: "来源" }));
  await user.click(screen.getByRole("option", { name: "手动添加" }));
  expect(
    await screen.findAllByText("原始会话 one-memory-manual-first"),
  ).toHaveLength(2);
  expect(reads.raw).toHaveBeenLastCalledWith(
    expect.objectContaining({
      source: "one-memory-manual",
      offset: 0,
      limit: 20,
      paging: "cursor",
      cursor: null,
    }),
  );
});

it("selects and renders one turn when two turns share a raw storage batch", async () => {
  setup();
  reads.processing.mockResolvedValue(modeProcessing("lite"));
  reads.raw.mockResolvedValue({
    items: [
      {
        raw_id: "raw-batch",
        turn_id: "raw-turn:batch:0",
        turn_index: 0,
        turn_count: 2,
        content: "[user]: 第一问\n[assistant]: 第一答",
        user_id: "local",
        agent_id: "all-apps",
        source: "cursor",
        session_id: "session-batch",
        memory_at: null,
        gmt_created: 1_761_000_000,
        custom: {},
        messages: [
          { role: "user", content: "第一问" },
          { role: "assistant", content: "第一答" },
        ],
      },
      {
        raw_id: "raw-batch",
        turn_id: "raw-turn:batch:1",
        turn_index: 1,
        turn_count: 2,
        content: "[user]: 第二问\n[assistant]: 第二答",
        user_id: "local",
        agent_id: "all-apps",
        source: "cursor",
        session_id: "session-batch",
        memory_at: null,
        gmt_created: 1_761_000_000,
        custom: {},
        messages: [
          { role: "user", content: "第二问" },
          { role: "assistant", content: "第二答" },
        ],
      },
    ],
    total: 2,
    limit: 20,
    offset: 0,
    unit: "turn",
  });
  const user = userEvent.setup();
  render(<MemorySpace simplified />);

  await user.click(await screen.findByRole("button", { name: /第二问/ }));

  await waitFor(() => {
    const detail = document.querySelector(".map-dialogue-reading");
    expect(detail?.textContent).toContain("第二问");
    expect(detail?.textContent).toContain("第二答");
    expect(detail?.textContent).not.toContain("第一问");
    expect(detail?.textContent).not.toContain("第一答");
  });
});

it("switches data planes without keeping rows or details from the prior mode", async () => {
  setup();
  vi.useFakeTimers();
  reads.processing.mockResolvedValue(modeProcessing("pro"));
  reads.raw.mockResolvedValue({
    items: [
      {
        raw_id: "raw-after-switch",
        content: "切换后的原始会话",
        user_id: "local",
        agent_id: "all-apps",
        source: "cursor",
        session_id: "session-after-switch",
        memory_at: null,
        gmt_created: 1_761_000_000,
        custom: {},
        messages: [{ role: "user", content: "切换后的原始会话" }],
      },
    ],
    total: 1,
    limit: 20,
    offset: 0,
  });
  render(<MemorySpace simplified />);

  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
  expect(reads.sdkLibrary).toHaveBeenCalled();
  expect(screen.getAllByText("已保存的记忆").length).toBeGreaterThan(0);

  reads.processing.mockResolvedValue(modeProcessing("lite"));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3_000);
  });
  await vi.waitFor(() => expect(reads.raw).toHaveBeenCalled(), {
    timeout: 1_000,
  });
  expect(screen.getAllByText("切换后的原始会话")).toHaveLength(2);
  expect(screen.queryByText("已保存的记忆")).toBeNull();
  expect(document.querySelector(".memory-mode-label")?.textContent).toBe(
    "lite",
  );

  const libraryCalls = reads.sdkLibrary.mock.calls.length;
  reads.processing.mockResolvedValue(modeProcessing("ultra"));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3_000);
  });
  await vi.waitFor(
    () =>
      expect(reads.sdkLibrary.mock.calls.length).toBeGreaterThan(libraryCalls),
    { timeout: 1_000 },
  );
  expect(screen.getAllByText("已保存的记忆").length).toBeGreaterThan(0);
  expect(screen.queryByText("切换后的原始会话")).toBeNull();
  expect(document.querySelector(".memory-mode-label")?.textContent).toBe(
    "ultra",
  );
});

it("opens a search hit with the complete role transcript", async () => {
  setup();
  reads.processing.mockResolvedValue({
    running: 0,
    pending: 0,
    blocked_stages: [],
    completed: 1,
    pipeline: {
      mode: "lite",
      configured_mode: "lite",
      forced_by_env: false,
      evolve: {
        mode: "lite",
        schedule: { enabled: true, time: "03:00" },
        users: {},
        running: [],
      },
      last_evolve_at: null,
      graph_build: {
        state: "idle",
        started_at: null,
        finished_at: null,
        users: {},
      },
      features: {},
    },
  });
  reads.raw.mockImplementation(async (options = {}) =>
    "sessionId" in options && options.sessionId
      ? {
          items: [
            {
              raw_id: "raw-search",
              turn_id: "raw-turn:search:0",
              turn_index: 0,
              turn_count: 2,
              content: "[user]: 第一轮问题\n[assistant]: 第一轮回答",
              user_id: "local",
              agent_id: "all-apps",
              source: "cursor",
              session_id: "session-search",
              memory_at: null,
              gmt_created: 1_761_000_000,
              custom: {},
              messages: [
                { role: "user", content: "第一轮问题" },
                { role: "assistant", content: "第一轮回答" },
              ],
            },
            {
              raw_id: "raw-search",
              turn_id: "raw-turn:search:1",
              turn_index: 1,
              turn_count: 2,
              content: "[user]: 完整问题\n[assistant]: 完整回答",
              user_id: "local",
              agent_id: "all-apps",
              source: "cursor",
              session_id: "session-search",
              memory_at: null,
              gmt_created: 1_761_000_000,
              custom: {},
              messages: [
                { role: "user", content: "完整问题" },
                { role: "assistant", content: "完整回答" },
              ],
            },
          ],
          total: 2,
          limit: 500,
          offset: 0,
        }
      : { items: [], total: 0, limit: 20, offset: 0 },
  );
  reads.rawSearch.mockResolvedValue([
    {
      kind: "raw",
      raw_id: "raw-search",
      turn_id: "raw-turn:search:1",
      turn_index: 1,
      turn_count: 2,
      content: "被截断的搜索摘要",
      source: "cursor",
      session_id: "session-search",
      memory_at: null,
      score: 0.9,
      truncated: true,
    },
  ]);
  reads.rawGraph.mockResolvedValue({
    user_id: "local",
    items: [],
    total: 1_600,
    limit: 300,
    offset: 0,
    has_more: false,
  });
  const user = userEvent.setup();
  render(<MemorySpace simplified />);

  const search = await screen.findByRole("textbox", {
    name: "搜索原始对话",
  });
  await user.type(search, "完整");
  await user.click(screen.getByRole("button", { name: "搜索" }));

  expect(await screen.findByText("完整问题")).toBeTruthy();
  expect(screen.getByText("找到 1 条")).toBeTruthy();
  expect(screen.getByText("完整回答")).toBeTruthy();
  expect(
    document.querySelector(".map-dialogue-reading")?.textContent,
  ).not.toContain("第一轮问题");
  expect(reads.raw).toHaveBeenCalledWith(
    expect.objectContaining({
      source: "cursor",
      sessionId: "session-search",
      limit: 500,
    }),
  );
});

it("does not request advanced memories before the pipeline mode is known", async () => {
  setup();
  let resolveProcessing!: (value: unknown) => void;
  reads.processing.mockReturnValueOnce(
    new Promise((resolve) => {
      resolveProcessing = resolve;
    }),
  );
  render(<MemorySpace simplified />);

  await act(async () => Promise.resolve());
  expect(reads.sdkLibrary).not.toHaveBeenCalled();
  expect(reads.sdkGraph).not.toHaveBeenCalled();

  await act(async () => {
    resolveProcessing({
      running: 0,
      pending: 0,
      blocked_stages: [],
      completed: 0,
      pipeline: {
        mode: "lite",
        configured_mode: "lite",
        forced_by_env: false,
        evolve: {
          mode: "lite",
          schedule: { enabled: true, time: "03:00" },
          users: {},
          running: [],
        },
        last_evolve_at: null,
        graph_build: {
          state: "idle",
          started_at: null,
          finished_at: null,
          users: {},
        },
        features: {},
      },
    });
  });
  await waitFor(() =>
    expect(document.querySelector(".memory-mode-label")?.textContent).toBe(
      "lite",
    ),
  );
  expect(reads.sdkLibrary).not.toHaveBeenCalled();
  expect(reads.sdkGraph).not.toHaveBeenCalled();
});

it("keeps an added memory visible while it waits for model processing", async () => {
  setup();
  render(<MemorySpace simplified onConfigureModel={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "补充记忆" }));
  expect(
    screen.getByText(/正在等待整理：项目周会统一安排在周三上午。/),
  ).toBeTruthy();
  expect(screen.queryByText(/原话已保存/)).toBeNull();
  expect(screen.getByRole("button", { name: "配置整理模型" })).toBeTruthy();
  expect(screen.queryByText("还没有已生成的记忆")).toBeNull();
});
it("hides a recoverable graph read failure on the three-column surface", async () => {
  setup();
  reads.sdkGraph.mockRejectedValue(new Error("graph offline"));
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
  expect(reads.sdkLibrary).toHaveBeenCalledWith(
    {
      statuses: ["active"],
      source: undefined,
      query: undefined,
      offset: 0,
      limit: 20,
      windowId: null,
    },
    expect.any(AbortSignal),
  );
  expect(screen.queryByRole("tablist", { name: "记忆空间模式" })).toBeNull();
  expect(screen.queryByLabelText("向记忆提问")).toBeNull();
  expect(screen.queryByRole("button", { name: "管理记忆" })).toBeNull();
  // The library is a search-free manager: the ask/search bar stays hidden.
  fireEvent(window, new Event("one-memory:manage"));
  expect(screen.queryByRole("textbox", { name: "搜索记忆库" })).toBeNull();
  await waitFor(() =>
    expect(reads.sdkLibrary).toHaveBeenCalledWith({
      statuses: ["active"],
      offset: 0,
      limit: 20,
    }),
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
  expect(reads.sdkLibrary).toHaveBeenCalled();
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
  expect(reads.sdkLibrary.mock.calls.length).toBeGreaterThan(counts[0]);
});
