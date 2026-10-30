import type {
  GovernedMemory,
  MemoryGraphEdge,
  MemoryGraphNode,
  MemoryGraphResponse,
  MemoryLibraryPage,
  SourceResponse,
} from "@one-memory/contracts";

export type MapAxis = "topic" | "source";
export type MapGroup = {
  key: string;
  label: string;
  memoryIds: string[];
  sourceCount: number;
};

/** The current authorized page owns provenance; the global graph may be truncated.
 * Callers must pass null while page authority is stale. Catalog metadata names
 * sources and can veto revoked ones, but never invents evidence membership.
 */
export function projectPagedSourceMap(
  graph: MemoryGraphResponse | null,
  page: MemoryLibraryPage | null,
  sources: SourceResponse[],
  /** Names for source ids that are not in the catalog (e.g. 未识别来源). */
  labels: Record<string, string> = {},
): MemoryGraphResponse {
  const nodes = new Map<string, MemoryGraphNode>();
  const edges = new Map<string, MemoryGraphEdge>();
  const catalog = new Map(sources.map((source) => [source.source_id, source]));
  const selected = new Set<string>();
  const membership = new Map<string, string[]>();
  for (const memory of page?.items ?? []) {
    if (memory.status !== "confirmed" || memory.deleted_at) continue;
    const mapping = page?.source_ids_by_memory;
    const ids =
      mapping && Object.hasOwn(mapping, memory.memory_id)
        ? mapping[memory.memory_id]
        : undefined;
    const verifiedIds =
      Array.isArray(ids) &&
      ids.every(
        (id) => typeof id === "string" && id.length > 0 && id.length <= 128,
      )
        ? [...new Set(ids)].sort()
        : [];
    if (verifiedIds.some((id) => catalog.get(id)?.authorized === false))
      continue;
    const nodeId = `memory:${memory.memory_id}`;
    selected.add(nodeId);
    membership.set(nodeId, verifiedIds);
    nodes.set(nodeId, {
      node_id: nodeId,
      node_type: "memory",
      memory_id: memory.memory_id,
      source_id: null,
      label: memory.content,
      kind: memory.kind,
      status: memory.status,
      confidence: null,
    });
  }

  const existingNodes = new Map(
    graph?.nodes.map((node) => [node.node_id, node]),
  );
  // Preserve only non-source relationships touching a current-page memory.
  // Source provenance below replaces, rather than unions with, the old graph.
  for (const edge of graph?.edges ?? []) {
    const ends = [edge.source_node_id, edge.target_node_id];
    if (edge.origin === "provenance" || !ends.some((id) => selected.has(id)))
      continue;
    const related = ends.map((id) => existingNodes.get(id));
    if (
      related.some(
        (node) =>
          !node ||
          node.node_type === "source" ||
          (node.node_type === "memory" && !selected.has(node.node_id)),
      )
    )
      continue;
    for (const node of related) {
      if (node && !selected.has(node.node_id))
        nodes.set(node.node_id, { ...node });
    }
    edges.set(edge.edge_id, { ...edge });
  }
  for (const [memoryNodeId, ids] of membership) {
    for (const id of ids) {
      const sourceNodeId = `source:${id}`;
      nodes.set(sourceNodeId, {
        node_id: sourceNodeId,
        node_type: "source",
        memory_id: null,
        source_id: id,
        label: catalog.get(id)?.display_name || labels[id] || id,
        kind: null,
        status: null,
        confidence: null,
      });
      const edgeId = `provenance:${JSON.stringify([memoryNodeId, sourceNodeId])}`;
      edges.set(edgeId, {
        edge_id: edgeId,
        source_node_id: sourceNodeId,
        target_node_id: memoryNodeId,
        relation: "evidence_source",
        origin: "provenance",
        weight: null,
      });
    }
  }
  return {
    total_memories: page?.total ?? 0,
    projected_memories: selected.size,
    hy_graph_available: page !== null && (graph?.hy_graph_available ?? false),
    nodes: [...nodes.values()],
    edges: [...edges.values()],
  };
}

export function memorySources(
  graph: MemoryGraphResponse | null,
): Map<string, Set<string>> {
  const result = new Map<string, Set<string>>();
  if (!graph) return result;
  const nodes = new Map(graph.nodes.map((n) => [n.node_id, n]));
  for (const edge of graph.edges) {
    if (edge.origin !== "provenance") continue;
    const ends = [
      nodes.get(edge.source_node_id),
      nodes.get(edge.target_node_id),
    ];
    const memory = ends.find((n) => n?.node_type === "memory");
    const source = ends.find((n) => n?.node_type === "source");
    if (!memory?.memory_id || !source?.source_id) continue;
    const ids = result.get(memory.memory_id) ?? new Set<string>();
    ids.add(source.source_id);
    result.set(memory.memory_id, ids);
  }
  return result;
}

export function mapGroups(
  memories: GovernedMemory[],
  graph: MemoryGraphResponse | null,
  axis: MapAxis,
): MapGroup[] {
  const sources = memorySources(graph);
  const sourceLabels = new Map(
    graph?.nodes
      .filter((n) => n.source_id)
      .map((n) => [n.source_id, n.label]) ?? [],
  );
  const groups = new Map<string, MapGroup>();
  for (const memory of memories) {
    if (memory.status === "deleted") continue;
    const values =
      axis === "topic"
        ? new Set(memory.tags)
        : (sources.get(memory.memory_id) ?? new Set<string>());
    for (const value of values) {
      const key = `${axis}:${value}`;
      const group = groups.get(key) ?? {
        key,
        label: axis === "source" ? (sourceLabels.get(value) ?? value) : value,
        memoryIds: [],
        sourceCount: 0,
      };
      group.memoryIds.push(memory.memory_id);
      groups.set(key, group);
    }
  }
  for (const group of groups.values())
    group.sourceCount = new Set(
      group.memoryIds.flatMap((id) => [...(sources.get(id) ?? [])]),
    ).size;
  return [...groups.values()].sort(
    (a, b) =>
      b.memoryIds.length - a.memoryIds.length ||
      a.label.localeCompare(b.label, "zh"),
  );
}

export function projectMap(
  graph: MemoryGraphResponse | null,
  memories: GovernedMemory[],
  axis: MapAxis,
  memoryIds: string[],
): MemoryGraphResponse {
  const wanted = new Set(memoryIds);
  const selected = memories.filter(
    (m) => wanted.has(m.memory_id) && m.status !== "deleted",
  );
  const nodes = new Map<string, MemoryGraphNode>();
  for (const m of selected)
    nodes.set(`memory:${m.memory_id}`, {
      node_id: `memory:${m.memory_id}`,
      node_type: "memory",
      memory_id: m.memory_id,
      label: m.content,
      source_id: null,
      kind: m.kind,
      status: m.status,
      confidence: null,
    });
  const edges = [];
  for (const edge of graph?.edges ?? []) {
    const both = [edge.source_node_id, edge.target_node_id];
    const related = graph?.nodes.find(
      (n) => both.includes(n.node_id) && n.node_type !== "memory",
    );
    if (
      related &&
      both.some((id) => nodes.has(id)) &&
      (related.node_type === axis || related.node_type === "schema")
    )
      nodes.set(related.node_id, related);
  }
  for (const edge of graph?.edges ?? [])
    if (nodes.has(edge.source_node_id) && nodes.has(edge.target_node_id))
      edges.push(edge);
  if (axis === "topic") {
    for (const memory of selected)
      for (const tag of new Set(memory.tags)) {
        const key = `topic:${tag}`;
        if (!nodes.has(key))
          nodes.set(key, {
            node_id: key,
            node_type: "topic",
            label: tag,
            memory_id: null,
            source_id: null,
            kind: null,
            status: null,
            confidence: null,
          });
        if (
          !edges.some(
            (e) =>
              [e.source_node_id, e.target_node_id].includes(key) &&
              [e.source_node_id, e.target_node_id].includes(
                `memory:${memory.memory_id}`,
              ),
          )
        )
          edges.push({
            edge_id: `${key}->memory:${memory.memory_id}`,
            source_node_id: key,
            target_node_id: `memory:${memory.memory_id}`,
            relation: "shared_tag",
            origin: "shared_tag" as const,
            weight: null,
          });
      }
  }
  return {
    total_memories: graph?.total_memories ?? memories.length,
    projected_memories: selected.length,
    hy_graph_available: graph?.hy_graph_available ?? false,
    nodes: [...nodes.values()],
    edges,
  };
}
