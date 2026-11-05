import type {
  MemoryGraphEdge,
  MemoryGraphNode,
  MemoryGraphResponse,
} from "@one-memory/contracts";
import { describe, expect, it } from "vitest";
import { createRawClusterLayout } from "./RawConversationGraph3D";

function makeRawGraph(count: number, sourceCount = 4): MemoryGraphResponse {
  const sources: MemoryGraphNode[] = Array.from(
    { length: sourceCount },
    (_, index) => ({
      node_id: `source:s-${index}`,
      node_type: "source",
      memory_id: null,
      source_id: `s-${index}`,
      label: `Source ${index}`,
      kind: null,
      status: null,
      confidence: null,
    }),
  );
  const memories: MemoryGraphNode[] = Array.from(
    { length: count },
    (_, index) => ({
      node_id: `memory:m-${index}`,
      node_type: "memory",
      memory_id: `m-${index}`,
      source_id: null,
      label: `Turn ${index}`,
      kind: "chat",
      status: "confirmed",
      confidence: null,
    }),
  );
  const edges: MemoryGraphEdge[] = memories.map((memory, index) => ({
    edge_id: `edge-${index}`,
    source_node_id: sources[index % sourceCount].node_id,
    target_node_id: memory.node_id,
    relation: "evidence_source",
    origin: "provenance",
    weight: null,
  }));
  return {
    total_memories: count,
    projected_memories: count,
    hy_graph_available: false,
    nodes: [...sources, ...memories],
    edges,
  };
}

describe("createRawClusterLayout", () => {
  it.each([1_600, 5_000])(
    "positions every node in bounded linear time at %i turns",
    (count) => {
      const graph = makeRawGraph(count);
      const started = performance.now();
      const positions = createRawClusterLayout(graph);
      const elapsed = performance.now() - started;

      expect(positions.size).toBe(graph.nodes.length);
      expect(elapsed).toBeLessThan(1_000);
      for (const position of positions.values()) {
        expect(Number.isFinite(position.x)).toBe(true);
        expect(Number.isFinite(position.y)).toBe(true);
        expect(Number.isFinite(position.z)).toBe(true);
      }
    },
  );

  it("is deterministic", () => {
    const graph = makeRawGraph(80);
    const first = createRawClusterLayout(graph);
    const second = createRawClusterLayout(graph);
    expect([...first].map(([id, value]) => [id, value.toArray()])).toEqual(
      [...second].map(([id, value]) => [id, value.toArray()]),
    );
  });
});
