// @vitest-environment jsdom
import type {
  GovernedMemory,
  MemoryGraphResponse,
  SdkMemoryRecord,
} from "@one-memory/contracts";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MemorySpace } from "./MemorySpace";
import { sdkMapMemory } from "./sdk-memory-map";

const api = vi.hoisted(() => ({
  sdkLibrary: vi.fn(),
  sdkGraph: vi.fn(),
  sdkMemory: vi.fn(),
  updateSdkMemory: vi.fn(),
  deleteSdkMemory: vi.fn(),
}));
// Only the SDK (v2) reads and writes exist: a call to anything else fails the
// test with "is not a function" instead of passing silently.
vi.mock("./api", () => ({
  getSdkMemoryLibrary: api.sdkLibrary,
  getSdkMemoryGraph: api.sdkGraph,
  getSdkMemory: api.sdkMemory,
  updateSdkMemory: api.updateSdkMemory,
  deleteSdkMemory: api.deleteSdkMemory,
  getMemoryProcessing: async () => null,
  getModelProfiles: async () => [],
  apiErrorCode: () => null,
}));
vi.mock("./GraphWidthPrompt", () => ({ GraphWidthPrompt: () => null }));
vi.mock("./MemoryAddComposer", () => ({ MemoryAddComposer: () => null }));
vi.mock("./MemoryMap", () => ({
  MemoryMap: ({
    hidden,
    memories,
    graph,
    loaded,
    busy,
    detail,
    onOpenMemory,
    onSave,
    onDelete,
    libraryView,
  }: {
    hidden: boolean;
    memories: GovernedMemory[];
    graph: MemoryGraphResponse | null;
    loaded: boolean;
    busy: boolean;
    detail: { memory: { memory_id: string } } | null;
    onOpenMemory(id: string): void;
    onSave(event: { preventDefault(): void }): void;
    onDelete(): void;
    libraryView?: {
      page: {
        source_ids_by_memory?: Record<string, string[]>;
        offset: number;
      } | null;
      graphFailed?: boolean;
      stale: boolean;
      unattributedSource?: string;
      onSource(value: string): void;
      onQuery(value: string): void;
      onPage(offset: number): void;
      resetPage(): void;
    };
  }) => (
    <div hidden={hidden} data-testid="map">
      <span data-testid="map-memories">
        {memories.map((m) => `${m.memory_id}:${m.status}`).join(",")}
      </span>
      <span data-testid="map-nodes">{graph?.nodes.length ?? "none"}</span>
      <span data-testid="map-loaded">{String(loaded)}</span>
      <span data-testid="map-sources">
        {memories
          .map((m) =>
            (libraryView?.page?.source_ids_by_memory?.[m.memory_id] ?? []).join(
              "+",
            ),
          )
          .join(",")}
      </span>
      <span data-testid="map-unattributed">
        {libraryView?.unattributedSource ?? ""}
      </span>
      <span data-testid="map-busy">{String(busy)}</span>
      <span data-testid="map-detail">{detail?.memory.memory_id ?? ""}</span>
      <span data-testid="map-offset">{libraryView?.page?.offset ?? ""}</span>
      <span data-testid="map-stale">{String(libraryView?.stale)}</span>
      <span data-testid="map-graph-failed">
        {String(libraryView?.graphFailed)}
      </span>
      <button type="button" onClick={() => onOpenMemory("e1")}>
        open e1
      </button>
      <button
        type="button"
        onClick={() => {
          libraryView?.onQuery("deploy");
          libraryView?.resetPage();
        }}
      >
        search deploy
      </button>
      <button
        type="button"
        onClick={() => {
          libraryView?.onQuery("other");
          libraryView?.resetPage();
        }}
      >
        search other
      </button>
      <button type="button" onClick={() => libraryView?.onPage(20)}>
        page 2
      </button>
      <button type="button" onClick={() => onOpenMemory("a")}>
        open a
      </button>
      <button type="button" onClick={() => onSave({ preventDefault() {} })}>
        save
      </button>
      <button type="button" onClick={onDelete}>
        delete
      </button>
      <button
        type="button"
        onClick={() => {
          libraryView?.onSource("workbuddy");
          libraryView?.resetPage();
        }}
      >
        pick workbuddy
      </button>
      <button
        type="button"
        onClick={() => {
          libraryView?.onSource("");
          libraryView?.resetPage();
        }}
      >
        pick all
      </button>
    </div>
  ),
}));
vi.mock("./SdkMemoryLibrary", () => ({
  SdkMemoryLibrary: ({
    focusedMemoryId,
  }: {
    focusedMemoryId: string | null;
  }) => <div data-testid="sdk-library">{focusedMemoryId ?? ""}</div>,
}));

const record = (memory_id: string): SdkMemoryRecord => ({
  memory_id,
  content: `memory ${memory_id}`,
  layer: "l2_fact",
  status: "active",
  is_latest: true,
  supersedes: [],
  superseded_by: [],
  tags: ["工作"],
  owner: null,
  source_raw_ids: [],
  user_id: "local",
  agent_id: "all-apps",
  session_id: "s1",
  source: "cursor",
  memory_at: null,
  gmt_created: 1_761_000_000,
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function sdkAuthority() {
  api.sdkLibrary.mockResolvedValue({
    memories: [record("a"), record("b")],
    total: 3018,
    limit: 300,
    offset: 0,
  });
  api.sdkGraph.mockResolvedValue({
    total_memories: 3018,
    projected_memories: 2,
    nodes: [
      { node_id: "memory:a", node_type: "memory", label: "a", memory_id: "a" },
      { node_id: "memory:b", node_type: "memory", label: "b", memory_id: "b" },
      { node_id: "source:cursor", node_type: "source", label: "Cursor" },
    ],
    edges: [],
    hy_graph_available: true,
  });
}

it("draws the map from SDK memories on SDK authority, without legacy reads", async () => {
  sdkAuthority();

  render(<MemorySpace simplified active />);

  await waitFor(() =>
    expect(screen.getByTestId("map-loaded").textContent).toBe("true"),
  );
  expect(screen.getByTestId("map").hidden).toBe(false);
  expect(screen.getByTestId("map-memories").textContent).toBe(
    "a:confirmed,b:confirmed",
  );
  expect(screen.getByTestId("map-nodes").textContent).toBe("3");
  expect(await screen.findByText(/3,018/)).toBeTruthy();
  expect(api.sdkLibrary).toHaveBeenCalledWith(
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
  expect(api.sdkGraph).toHaveBeenCalledWith(
    { source: undefined, offset: 0, limit: 300 },
    expect.any(AbortSignal),
  );
});

it("keeps a graph read failure explicit instead of presenting the list page as the graph", async () => {
  sdkAuthority();
  api.sdkGraph.mockRejectedValue(new Error("graph unavailable"));
  render(<MemorySpace simplified active />);

  await waitFor(() =>
    expect(screen.getByTestId("map-loaded").textContent).toBe("true"),
  );
  expect(screen.getByTestId("map-graph-failed").textContent).toBe("true");
  expect(screen.getByTestId("map-nodes").textContent).toBe("none");
});

it("keeps rows, sources, count and graph on the one filtered page", async () => {
  sdkAuthority();
  render(<MemorySpace simplified active />);
  await waitFor(() =>
    expect(screen.getByTestId("map-loaded").textContent).toBe("true"),
  );
  api.sdkLibrary.mockResolvedValue({
    memories: [
      { ...record("w1"), source: "workbuddy" },
      { ...record("w2"), source: "default" },
    ],
    total: 566,
    limit: 100,
    offset: 0,
  });

  fireEvent.click(screen.getByText("pick workbuddy"));

  await waitFor(() =>
    expect(screen.getByTestId("map-memories").textContent).toBe(
      "w1:confirmed,w2:confirmed",
    ),
  );
  expect(screen.getByTestId("map-sources").textContent).toBe(
    "workbuddy,default",
  );
  expect(screen.getByTestId("map-unattributed").textContent).toBe("default");
  expect(api.sdkLibrary).toHaveBeenLastCalledWith(
    expect.objectContaining({ source: "workbuddy", offset: 0 }),
    expect.any(AbortSignal),
  );
  expect(api.sdkGraph).toHaveBeenLastCalledWith(
    { source: "workbuddy", offset: 0, limit: 300 },
    expect.any(AbortSignal),
  );
  expect(await screen.findByText(/566/)).toBeTruthy();
});

it("hides the previous source graph until the all-applications graph settles", async () => {
  sdkAuthority();
  render(<MemorySpace simplified active />);
  await waitFor(() =>
    expect(screen.getByTestId("map-nodes").textContent).toBe("3"),
  );

  const workbuddyPage = {
    memories: [{ ...record("w1"), source: "workbuddy" }],
    total: 1,
    limit: 20,
    offset: 0,
  };
  const workbuddyGraph: MemoryGraphResponse = {
    total_memories: 1,
    projected_memories: 1,
    nodes: [
      {
        node_id: "memory:w1",
        node_type: "memory",
        label: "w1",
        memory_id: "w1",
        source_id: null,
        kind: null,
        status: "active",
        confidence: null,
      },
      {
        node_id: "source:workbuddy",
        node_type: "source",
        label: "WorkBuddy",
        source_id: "workbuddy",
        memory_id: null,
        kind: null,
        status: null,
        confidence: null,
      },
    ],
    edges: [],
    hy_graph_available: false,
  };
  api.sdkLibrary.mockResolvedValue(workbuddyPage);
  api.sdkGraph.mockResolvedValue(workbuddyGraph);
  fireEvent.click(screen.getByText("pick workbuddy"));
  await waitFor(() =>
    expect(screen.getByTestId("map-nodes").textContent).toBe("2"),
  );

  const allPage = {
    memories: [record("a"), { ...record("w1"), source: "workbuddy" }],
    total: 2,
    limit: 20,
    offset: 0,
  };
  const allGraph: MemoryGraphResponse = {
    ...workbuddyGraph,
    total_memories: 2,
    projected_memories: 2,
    nodes: [
      ...workbuddyGraph.nodes,
      {
        node_id: "memory:a",
        node_type: "memory",
        label: "a",
        memory_id: "a",
        source_id: null,
        kind: null,
        status: "active",
        confidence: null,
      },
      {
        node_id: "source:cursor",
        node_type: "source",
        label: "Cursor",
        source_id: "cursor",
        memory_id: null,
        kind: null,
        status: null,
        confidence: null,
      },
    ],
  };
  let releasePage: (value: typeof allPage) => void = () => {};
  let releaseGraph: (value: MemoryGraphResponse) => void = () => {};
  api.sdkLibrary.mockReturnValueOnce(
    new Promise((resolve) => {
      releasePage = resolve;
    }),
  );
  api.sdkGraph.mockReturnValueOnce(
    new Promise((resolve) => {
      releaseGraph = resolve;
    }),
  );

  fireEvent.click(screen.getByText("pick all"));
  await waitFor(() =>
    expect(screen.getByTestId("map-stale").textContent).toBe("true"),
  );
  expect(screen.getByTestId("map-nodes").textContent).toBe("none");

  await act(async () => {
    releasePage(allPage);
    releaseGraph(allGraph);
  });
  await waitFor(() =>
    expect(screen.getByTestId("map-nodes").textContent).toBe("4"),
  );
  expect(screen.getByTestId("map-stale").textContent).toBe("false");

  const searchPage = {
    memories: [record("search-result")],
    total: 1,
    limit: 20,
    offset: 0,
  };
  let releaseSearch: (value: typeof searchPage) => void = () => {};
  api.sdkLibrary.mockReturnValueOnce(
    new Promise((resolve) => {
      releaseSearch = resolve;
    }),
  );
  const graphCalls = api.sdkGraph.mock.calls.length;
  fireEvent.click(screen.getByText("search deploy"));
  await waitFor(() =>
    expect(screen.getByTestId("map-stale").textContent).toBe("true"),
  );
  expect(screen.getByTestId("map-nodes").textContent).toBe("none");
  expect(api.sdkGraph).toHaveBeenCalledTimes(graphCalls);

  await act(async () => releaseSearch(searchPage));
  await waitFor(() =>
    expect(screen.getByTestId("map-stale").textContent).toBe("false"),
  );
});

it("opens an SDK memory in place instead of switching away from the map", async () => {
  sdkAuthority();
  api.sdkMemory.mockResolvedValue(record("a"));

  render(<MemorySpace simplified active />);
  await waitFor(() =>
    expect(screen.getByTestId("map-loaded").textContent).toBe("true"),
  );
  fireEvent.click(screen.getByText("open a"));

  await waitFor(() =>
    expect(api.sdkMemory).toHaveBeenCalledWith("a", undefined),
  );
  // The inspector stays on the map; the reader is never moved to a second list.
  expect(screen.getByTestId("map").hidden).toBe(false);
  expect(screen.getByTestId("sdk-library").textContent).toBe("");
});

it("re-reads the page on the next tick when Core is not ready yet", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    sdkAuthority();
    api.sdkLibrary.mockRejectedValueOnce(new Error("core_not_ready"));

    render(<MemorySpace simplified active />);
    await waitFor(() => expect(api.sdkLibrary).toHaveBeenCalledTimes(1));
    expect(screen.getByTestId("map-loaded").textContent).toBe("false");
    await vi.advanceTimersByTimeAsync(30_000);

    await waitFor(() =>
      expect(screen.getByTestId("map-loaded").textContent).toBe("true"),
    );
  } finally {
    vi.useRealTimers();
  }
});

it("saves and deletes an opened memory through v2 without a stale-read block", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    sdkAuthority();
    api.sdkMemory.mockResolvedValue(record("a"));
    api.updateSdkMemory.mockResolvedValue({
      ...record("a"),
      content: "rewritten",
    });
    api.deleteSdkMemory.mockResolvedValue(undefined);

    render(<MemorySpace simplified active />);
    await waitFor(() =>
      expect(screen.getByTestId("map-loaded").textContent).toBe("true"),
    );
    fireEvent.click(screen.getByText("open a"));
    await waitFor(() =>
      expect(screen.getByTestId("map-detail").textContent).toBe("a"),
    );
    // The governed detail used to be re-verified every 10s through v1; on SDK
    // that read was refused and left the detail blocked as busy.
    await vi.advanceTimersByTimeAsync(25_000);
    expect(screen.getByTestId("map-busy").textContent).toBe("false");

    fireEvent.click(screen.getByText("save"));
    await waitFor(() =>
      expect(api.updateSdkMemory).toHaveBeenCalledWith(
        "a",
        "memory a",
        expect.any(String),
        undefined,
      ),
    );
    await waitFor(() =>
      expect(screen.getByTestId("map-busy").textContent).toBe("false"),
    );

    fireEvent.click(screen.getByText("delete"));
    fireEvent.click(await screen.findByRole("button", { name: "确认删除" }));
    await waitFor(() =>
      expect(api.deleteSdkMemory).toHaveBeenCalledWith(
        "a",
        expect.any(String),
        undefined,
      ),
    );
    await waitFor(() =>
      expect(screen.getByTestId("map-detail").textContent).toBe(""),
    );
  } finally {
    vi.useRealTimers();
  }
});

it("projects SDK records into the fields the map groups by", () => {
  const coding = sdkMapMemory({ ...record("c"), layer: "coding_experience" });
  expect(coding).toMatchObject({
    memory_id: "c",
    kind: "coding",
    status: "confirmed",
    tags: ["工作"],
    deleted_at: null,
  });
  expect(sdkMapMemory({ ...record("s"), layer: "l6_schema" }).kind).toBe(
    "schema",
  );
  expect(sdkMapMemory({ ...record("x"), status: "superseded" }).status).toBe(
    "superseded",
  );
});

const node = (
  node_id: string,
  node_type: MemoryGraphResponse["nodes"][number]["node_type"],
  extra: Partial<MemoryGraphResponse["nodes"][number]> = {},
): MemoryGraphResponse["nodes"][number] => ({
  node_id,
  node_type,
  label: node_id,
  memory_id: null,
  source_id: null,
  kind: null,
  status: null,
  confidence: null,
  ...extra,
});
const provenance = (
  from: string,
  to: string,
): MemoryGraphResponse["edges"][number] => ({
  edge_id: `${from}->${to}`,
  source_node_id: from,
  target_node_id: to,
  relation: "evidence_source",
  weight: null,
  origin: "provenance",
});

it("groups and draws SDK memories by source with the real map model", async () => {
  const { mapGroups, projectMap } = await import("./memory-map-model");
  const memories = [record("a"), record("b")].map(sdkMapMemory);
  const graph: MemoryGraphResponse = {
    total_memories: 2,
    projected_memories: 2,
    nodes: [
      node("memory:a", "memory", { memory_id: "a" }),
      node("memory:b", "memory", { memory_id: "b" }),
      node("source:cursor", "source", { source_id: "cursor", label: "Cursor" }),
      node("hy:t1", "topic"),
    ],
    edges: [
      provenance("source:cursor", "memory:a"),
      provenance("source:cursor", "memory:b"),
    ],
    hy_graph_available: true,
  };

  const groups = mapGroups(memories, graph, "source");
  expect(groups.map((group) => [group.key, group.memoryIds])).toEqual([
    ["source:cursor", ["a", "b"]],
  ]);
  const drawn = projectMap(graph, memories, "source", ["a", "b"]);
  expect(
    drawn.nodes
      .filter((node) => node.node_type === "memory")
      .map((n) => n.memory_id),
  ).toEqual(["a", "b"]);
  expect(drawn.edges.length).toBeGreaterThan(0);
});

const experience = (memory_id: string): SdkMemoryRecord => ({
  ...record(memory_id),
  content: "Fix flaky tests\nPin the seed",
  layer: "experience",
  scene: "coding",
  kind: "experience",
  variant: "coding",
  task: "Fix flaky tests",
  solution: "Pin the seed",
  created_at: 1_761_000_000,
  updated_at: 1_761_000_100,
});

it("opens, edits and deletes a coding experience by its kind and variant", async () => {
  api.sdkLibrary.mockResolvedValue({
    memories: [record("a"), experience("e1")],
    total: 2,
    limit: 20,
    offset: 0,
  });
  api.sdkGraph.mockResolvedValue(null);
  api.sdkMemory.mockResolvedValue(experience("e1"));
  api.updateSdkMemory.mockResolvedValue({
    ...experience("e1"),
    solution: "Pin the seed",
  });
  api.deleteSdkMemory.mockResolvedValue(undefined);

  render(<MemorySpace simplified active />);
  await waitFor(() =>
    expect(screen.getByTestId("map-loaded").textContent).toBe("true"),
  );
  // A productivity memory has no graph node; the page still draws.
  expect(screen.getByTestId("map-memories").textContent).toBe(
    "a:confirmed,e1:confirmed",
  );
  fireEvent.click(screen.getByText("open e1"));
  await waitFor(() =>
    expect(api.sdkMemory).toHaveBeenCalledWith("e1", {
      kind: "experience",
      variant: "coding",
    }),
  );
  await waitFor(() =>
    expect(screen.getByTestId("map-detail").textContent).toBe("e1"),
  );

  // The editor holds the solution, not the joined task and solution.
  fireEvent.click(screen.getByText("save"));
  await waitFor(() =>
    expect(api.updateSdkMemory).toHaveBeenCalledWith(
      "e1",
      "Pin the seed",
      expect.any(String),
      { kind: "experience", variant: "coding" },
    ),
  );
  await waitFor(() =>
    expect(screen.getByTestId("map-busy").textContent).toBe("false"),
  );
  fireEvent.click(screen.getByText("delete"));
  fireEvent.click(await screen.findByRole("button", { name: "确认删除" }));
  await waitFor(() =>
    expect(api.deleteSdkMemory).toHaveBeenCalledWith("e1", expect.any(String), {
      kind: "experience",
      variant: "coding",
    }),
  );
});

it("pages a keyword search through one window and restarts when it resets", async () => {
  sdkAuthority();
  api.sdkGraph.mockResolvedValue(null);
  render(<MemorySpace simplified active />);
  await waitFor(() =>
    expect(screen.getByTestId("map-loaded").textContent).toBe("true"),
  );

  // The server keeps window "w1" until it expires, then answers any page
  // asked of it with page 1 of a fresh window "w2".
  const live = new Set(["w1"]);
  api.sdkLibrary.mockImplementation(
    async (request: { offset: number; windowId?: string | null }) => {
      const reset = !!request.windowId && !live.has(request.windowId);
      const offset = reset ? 0 : request.offset;
      const windowId = reset || live.has("w2") ? "w2" : "w1";
      if (reset) live.add("w2");
      return {
        memories: [record(`${windowId}-${offset}`)],
        total: 45,
        limit: 20,
        offset,
        window_id: windowId,
        window_reset: reset,
      };
    },
  );
  fireEvent.click(screen.getByText("search deploy"));
  await waitFor(() =>
    expect(screen.getByTestId("map-memories").textContent).toBe(
      "w1-0:confirmed",
    ),
  );
  expect(api.sdkLibrary).toHaveBeenLastCalledWith(
    expect.objectContaining({ query: "deploy", offset: 0, windowId: null }),
    expect.any(AbortSignal),
  );

  fireEvent.click(screen.getByText("page 2"));
  await waitFor(() =>
    expect(screen.getByTestId("map-memories").textContent).toBe(
      "w1-20:confirmed",
    ),
  );
  expect(api.sdkLibrary).toHaveBeenLastCalledWith(
    expect.objectContaining({ query: "deploy", offset: 20, windowId: "w1" }),
    expect.any(AbortSignal),
  );

  // Back to page 1 on the same window, then the window expires: asking for
  // page 2 again restarts at page 1 of the new window.
  fireEvent.click(screen.getByText("search deploy"));
  await waitFor(() =>
    expect(screen.getByTestId("map-offset").textContent).toBe("0"),
  );
  live.delete("w1");
  fireEvent.click(screen.getByText("page 2"));
  await waitFor(() =>
    expect(api.sdkLibrary).toHaveBeenLastCalledWith(
      expect.objectContaining({ query: "deploy", offset: 0, windowId: "w2" }),
      expect.any(AbortSignal),
    ),
  );
  await waitFor(() =>
    expect(screen.getByTestId("map-memories").textContent).toBe(
      "w2-0:confirmed",
    ),
  );
  expect(screen.getByTestId("map-offset").textContent).toBe("0");

  // A new query drops the window.
  fireEvent.click(screen.getByText("search other"));
  await waitFor(() =>
    expect(api.sdkLibrary).toHaveBeenLastCalledWith(
      expect.objectContaining({ query: "other", offset: 0, windowId: null }),
      expect.any(AbortSignal),
    ),
  );
});
