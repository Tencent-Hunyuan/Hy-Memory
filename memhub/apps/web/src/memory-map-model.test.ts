import type {
  GovernedMemory,
  MemoryGraphResponse,
  MemoryLibraryPage,
  SourceResponse,
} from "@one-memory/contracts";
import { describe, expect, it } from "vitest";
import {
  mapGroups,
  omitRevokedSourceGraph,
  projectMap,
  projectPagedSourceMap,
} from "./memory-map-model";

const memories = [
  { memory_id: "a", content: "Alpha", tags: ["设计"], status: "confirmed" },
  {
    memory_id: "b",
    content: "Beta",
    tags: ["设计", "测试"],
    status: "confirmed",
  },
  { memory_id: "c", content: "Gamma", tags: ["测试"], status: "confirmed" },
] as GovernedMemory[];
const graph = {
  total_memories: 3,
  projected_memories: 3,
  hy_graph_available: false,
  nodes: [
    ...memories.map((m) => ({
      node_id: `memory:${m.memory_id}`,
      node_type: "memory",
      memory_id: m.memory_id,
      label: m.content,
    })),
    {
      node_id: "source:codex",
      node_type: "source",
      source_id: "codex",
      label: "Codex",
    },
    {
      node_id: "source:cursor",
      node_type: "source",
      source_id: "cursor",
      label: "Cursor",
    },
  ],
  edges: [
    {
      edge_id: "a",
      source_node_id: "source:codex",
      target_node_id: "memory:a",
      origin: "provenance",
      relation: "evidence_source",
    },
    {
      edge_id: "b",
      source_node_id: "memory:b",
      target_node_id: "source:codex",
      origin: "provenance",
      relation: "evidence_source",
    },
    {
      edge_id: "c",
      source_node_id: "source:cursor",
      target_node_id: "memory:c",
      origin: "provenance",
      relation: "evidence_source",
    },
  ],
} as MemoryGraphResponse;

describe("memory map membership", () => {
  it("counts only actual provenance rather than every imported source", () => {
    expect(
      mapGroups(memories, graph, "source").map((g) => [g.key, g.memoryIds]),
    ).toEqual([
      ["source:codex", ["a", "b"]],
      ["source:cursor", ["c"]],
    ]);
    expect(
      mapGroups(memories, graph, "topic").find((g) => g.label === "设计")
        ?.sourceCount,
    ).toBe(1);
  });
  it("topic membership is many-to-many and does not infer a matter", () => {
    expect(
      mapGroups(memories, graph, "topic").find((g) => g.label === "测试")
        ?.memoryIds,
    ).toEqual(["b", "c"]);
  });
  it("filters to selected memories and their actual related nodes only", () => {
    const result = projectMap(graph, memories, "source", ["a", "b"]);
    expect(result.nodes.map((n) => n.node_id)).not.toContain("source:cursor");
    expect(result.nodes.map((n) => n.node_id)).not.toContain("memory:c");
    expect(result.edges).toHaveLength(2);
  });
  it("removes revoked source subgraphs while retaining authorized ones", () => {
    const result = omitRevokedSourceGraph(graph, [
      { source_id: "codex", authorized: false },
      { source_id: "cursor", authorized: true },
    ] as SourceResponse[]);
    expect(result?.nodes.map((node) => node.node_id)).toEqual([
      "memory:c",
      "source:cursor",
    ]);
    expect(result?.edges.map((edge) => edge.edge_id)).toEqual(["c"]);
    expect(result?.projected_memories).toBe(1);
  });
  it("builds topic edges only from actual tags, without creating new facts", () => {
    const result = projectMap(graph, memories, "topic", ["a", "b"]);
    expect(result.nodes.filter((n) => n.node_type === "memory")).toHaveLength(
      2,
    );
    expect(result.nodes.filter((n) => n.node_type === "source")).toHaveLength(
      0,
    );
    expect(result.edges.filter((e) => e.origin === "shared_tag")).toHaveLength(
      3,
    );
    expect(projectMap(graph, memories, "source", []).nodes).toEqual([]);
  });
});

describe("page-receipted source projection", () => {
  const sources = [
    { source_id: "codex", display_name: "Codex", authorized: true },
    { source_id: "cursor", display_name: "Cursor", authorized: true },
  ] as SourceResponse[];
  const page = (
    overrides: Partial<MemoryLibraryPage> = {},
  ): MemoryLibraryPage => ({
    items: memories,
    total: 501,
    offset: 100,
    limit: 100,
    source_ids_by_memory: { a: ["cursor"], b: ["codex", "cursor"], c: [] },
    ...overrides,
  });

  it("adds source nodes and provenance for off-graph page memories using the receipt", () => {
    const result = projectPagedSourceMap(null, page(), sources);
    expect(result.total_memories).toBe(501);
    expect(result.projected_memories).toBe(3);
    expect(
      result.nodes.find((node) => node.node_id === "source:cursor")?.label,
    ).toBe("Cursor");
    expect(
      [...mapGroups(memories, result, "source")].map((group) => [
        group.key,
        group.memoryIds,
      ]),
    ).toEqual([
      ["source:cursor", ["a", "b"]],
      ["source:codex", ["b"]],
    ]);
    expect(result.edges).toHaveLength(3);
    expect(
      result.edges.every(
        (edge) =>
          edge.origin === "provenance" &&
          edge.relation === "evidence_source" &&
          edge.weight === null,
      ),
    ).toBe(true);
  });

  it("replaces stale provenance instead of combining conflicting graph guesses", () => {
    const before = JSON.stringify({ graph, memories });
    const result = projectPagedSourceMap(graph, page(), sources);
    expect(
      mapGroups(memories, result, "source").find(
        (group) => group.key === "source:codex",
      )?.memoryIds,
    ).toEqual(["b"]);
    expect(
      result.nodes.find((node) => node.node_id === "memory:c"),
    ).toBeTruthy();
    expect(
      result.edges.some((edge) =>
        [edge.source_node_id, edge.target_node_id].includes("memory:c"),
      ),
    ).toBe(false);
    expect(JSON.stringify({ graph, memories })).toBe(before);
  });

  it("keeps only current confirmed memories and does not project off-page mapping entries", () => {
    const result = projectPagedSourceMap(
      graph,
      page({
        items: [
          memories[1],
          { ...memories[0], status: "deleted" },
          { ...memories[2], status: "superseded" },
        ],
        source_ids_by_memory: {
          a: ["cursor"],
          b: ["codex"],
          c: ["cursor"],
          other: ["cursor"],
        },
      }),
      sources,
    );
    expect(
      result.nodes
        .filter((node) => node.node_type === "memory")
        .map((node) => node.memory_id),
    ).toEqual(["b"]);
    expect(result.projected_memories).toBe(1);
    expect(result.edges).toHaveLength(1);
  });

  it("does not keep any part of mixed-source memory after an explicit source revocation", () => {
    const result = projectPagedSourceMap(
      graph,
      page(),
      sources.map((source) => ({
        ...source,
        authorized: source.source_id !== "cursor",
      })),
    );
    expect(
      result.nodes
        .filter((node) => node.node_type === "memory")
        .map((node) => node.memory_id),
    ).toEqual(["c"]);
    expect(result.nodes.some((node) => node.node_type === "source")).toBe(
      false,
    );
    expect(result.edges).toEqual([]);
  });

  it("missing receipt fields never use old graph membership or tag labels", () => {
    const result = projectPagedSourceMap(
      graph,
      page({ source_ids_by_memory: undefined }),
      sources,
    );
    expect(
      result.nodes.filter((node) => node.node_type === "memory"),
    ).toHaveLength(3);
    expect(result.nodes.some((node) => node.node_type === "source")).toBe(
      false,
    );
    expect(result.edges).toEqual([]);
    const partial = projectPagedSourceMap(
      graph,
      page({ source_ids_by_memory: { b: ["codex"] } }),
      sources,
    );
    expect(partial.edges).toHaveLength(1);
  });

  it("uses the receipted source ID when the current source catalog lacks a display name", () => {
    const result = projectPagedSourceMap(
      null,
      page({
        source_ids_by_memory: { a: ["historical-source"], b: [], c: [] },
      }),
      sources,
    );
    expect(
      result.nodes.find((node) => node.source_id === "historical-source")
        ?.label,
    ).toBe("historical-source");
  });

  it("preserves current-page topic relationships and the existing topic projection", () => {
    const topics = projectMap(graph, memories, "topic", ["a", "b", "c"]);
    const result = projectPagedSourceMap(topics, page(), sources);
    expect(result.edges.filter((edge) => edge.origin === "shared_tag")).toEqual(
      topics.edges,
    );
    const topicView = projectMap(result, memories, "topic", ["a", "b"]);
    expect(topicView.nodes.some((node) => node.node_type === "source")).toBe(
      false,
    );
    expect(
      topicView.edges.filter((edge) => edge.origin === "shared_tag"),
    ).toHaveLength(3);
  });

  it("is deterministic and does not duplicate nodes or edges on repeated projection", () => {
    const first = projectPagedSourceMap(graph, page(), sources);
    expect(projectPagedSourceMap(first, page(), sources)).toEqual(first);
    expect(new Set(first.nodes.map((node) => node.node_id)).size).toBe(
      first.nodes.length,
    );
    expect(new Set(first.edges.map((edge) => edge.edge_id)).size).toBe(
      first.edges.length,
    );
  });

  it("null page clears graph body and old sources rather than retaining an earlier page", () => {
    const result = projectPagedSourceMap(graph, null, sources);
    expect(result.nodes).toEqual([]);
    expect(result.edges).toEqual([]);
    expect(result.projected_memories).toBe(0);
  });

  it("never builds a source edge from malformed or inherited metadata", () => {
    const inherited = Object.create({ a: ["codex"] });
    inherited.b = ["cursor"];
    inherited.c = [null];
    const result = projectPagedSourceMap(
      graph,
      page({ source_ids_by_memory: inherited }),
      sources,
    );
    expect(result.edges).toHaveLength(1);
    expect(result.edges[0].target_node_id).toBe("memory:b");
    expect(result.edges[0].source_node_id).toBe("source:cursor");
  });

  it("deduplicates receipted source IDs and excludes tombstoned active records", () => {
    const result = projectPagedSourceMap(
      null,
      page({
        items: [
          memories[0],
          { ...memories[1], deleted_at: "2026-09-20T00:00:00Z" },
        ],
        source_ids_by_memory: { a: ["codex", "codex"], b: ["cursor"] },
      }),
      sources,
    );
    expect(result.projected_memories).toBe(1);
    expect(result.edges).toHaveLength(1);
    expect(result.nodes.map((node) => node.node_id)).toEqual([
      "memory:a",
      "source:codex",
    ]);
  });
});
