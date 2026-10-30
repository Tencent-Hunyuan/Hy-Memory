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
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "./gui-language";
import { MemorySpace } from "./MemorySpace";

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
    localStorage.setItem(LANGUAGE_STORAGE_KEY, "zh-CN");
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
  },
}));
vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  getSdkMemoryLibrary: reads.sdkLibrary,
  getSdkMemoryGraph: reads.sdkGraph,
  getMemoryProcessing: reads.processing,
  getModelProfiles: reads.profiles,
}));
vi.mock("./GraphWidthPrompt", () => ({ GraphWidthPrompt: () => null }));
vi.mock("./MemoryAddComposer", () => ({
  MemoryAddComposer: ({
    onPendingChange,
  }: {
    onPendingChange?(addition: {
      evidenceId: string;
      text: string;
      state: string;
    }): void;
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

function setup() {
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
}

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
