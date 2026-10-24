// @vitest-environment jsdom
import type {
  GovernedMemory,
  MemoryGraphResponse,
  SdkMemoryRecord,
} from "@one-memory/contracts";
import {
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
  authority: vi.fn(),
  legacyLibrary: vi.fn(),
  legacyGraph: vi.fn(),
  sdkLibrary: vi.fn(),
  sdkGraph: vi.fn(),
  sdkMemory: vi.fn(),
}));
vi.mock("./api", () => ({
  getMemoryAuthority: api.authority,
  getMemoryLibrary: api.legacyLibrary,
  getMemories: async () => [],
  getMemoryGraph: api.legacyGraph,
  getSdkMemoryLibrary: api.sdkLibrary,
  getSdkMemoryGraph: api.sdkGraph,
  getSdkMemory: api.sdkMemory,
  updateSdkMemory: vi.fn(),
  deleteSdkMemory: vi.fn(),
  getMemoryProcessing: async () => null,
  getModelProfiles: async () => [],
  apiErrorCode: () => null,
}));
vi.mock("./GraphWidthPrompt", () => ({ GraphWidthPrompt: () => null }));
vi.mock("./MemoryNoteComposer", () => ({ MemoryNoteComposer: () => null }));
vi.mock("./MemoryMap", () => ({
  MemoryMap: ({
    hidden,
    memories,
    graph,
    loaded,
    onOpenMemory,
  }: {
    hidden: boolean;
    memories: GovernedMemory[];
    graph: MemoryGraphResponse | null;
    loaded: boolean;
    onOpenMemory(id: string): void;
  }) => (
    <div hidden={hidden} data-testid="map">
      <span data-testid="map-memories">
        {memories.map((m) => `${m.memory_id}:${m.status}`).join(",")}
      </span>
      <span data-testid="map-nodes">{graph?.nodes.length ?? "none"}</span>
      <span data-testid="map-loaded">{String(loaded)}</span>
      <button type="button" onClick={() => onOpenMemory("a")}>
        open a
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
vi.mock("./MemoryRecallScope", () => ({
  MemoryRecallScope: () => null,
  memoryProvenanceLabel: () => "原话",
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
  api.authority.mockResolvedValue({ authority: "sdk", migration_id: "m" });
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
    { statuses: ["active"], offset: 0, limit: 300 },
    expect.any(AbortSignal),
  );
  expect(api.legacyLibrary).not.toHaveBeenCalled();
  expect(api.legacyGraph).not.toHaveBeenCalled();
});

it("opens an SDK memory in place instead of switching away from the map", async () => {
  sdkAuthority();
  api.sdkMemory.mockResolvedValue(record("a"));

  render(<MemorySpace simplified active />);
  await waitFor(() =>
    expect(screen.getByTestId("map-loaded").textContent).toBe("true"),
  );
  fireEvent.click(screen.getByText("open a"));

  await waitFor(() => expect(api.sdkMemory).toHaveBeenCalledWith("a"));
  // The inspector stays on the map; the reader is never moved to a second list.
  expect(screen.getByTestId("map").hidden).toBe(false);
  expect(screen.getByTestId("sdk-library").textContent).toBe("");
});

it("keeps asking for the authority until Core answers", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    sdkAuthority();
    api.authority
      .mockRejectedValueOnce(new Error("core_not_ready"))
      .mockRejectedValueOnce(new Error("core_not_ready"));

    render(<MemorySpace simplified active />);
    await vi.advanceTimersByTimeAsync(2_000);

    await waitFor(() => expect(api.authority).toHaveBeenCalledTimes(3));
    await waitFor(() =>
      expect(screen.getByTestId("map-loaded").textContent).toBe("true"),
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
