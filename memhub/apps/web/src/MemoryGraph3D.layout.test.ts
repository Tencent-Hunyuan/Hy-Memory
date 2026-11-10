import type {
  MemoryGraphEdge,
  MemoryGraphNode,
  MemoryGraphResponse,
} from "@one-memory/contracts";
import * as THREE from "three";
import { describe, expect, it } from "vitest";
import { createRelationLayout, isGraphLayoutCacheValid } from "./MemoryGraph3D";

const NODE_TYPES: MemoryGraphNode["node_type"][] = [
  "memory",
  "source",
  "schema",
  "topic",
];

function makeGraph(nodeCount: number): MemoryGraphResponse {
  const nodes: MemoryGraphNode[] = Array.from(
    { length: nodeCount },
    (_, index) => ({
      node_id: `node-${index}`,
      node_type: NODE_TYPES[index % NODE_TYPES.length],
      label: `节点 ${index}`,
      memory_id: index % 2 === 0 ? `mem-${index}` : null,
      source_id: index % 3 === 0 ? `src-${index}` : null,
      kind: null,
      status: index % 5 === 0 ? "candidate" : "active",
      confidence: 0.8,
    }),
  );
  const edges: MemoryGraphEdge[] = [];
  for (let index = 1; index < nodeCount; index += 1) {
    edges.push({
      edge_id: `edge-${index}`,
      source_node_id: `node-${Math.floor(index / 3)}`,
      target_node_id: `node-${index}`,
      // 两种 relation 的目标弹簧长度不同，必须都覆盖
      relation: index % 4 === 0 ? "evidence_source" : "derived_from",
      weight: null,
      origin: "provenance",
    });
  }
  return {
    total_memories: nodeCount,
    projected_memories: nodeCount,
    nodes,
    edges,
    hy_graph_available: true,
  };
}

/**
 * 重构前的原始算法，逐字复刻自改动前的 relationLayout：
 * 每次迭代新建 force Map、每对节点 clone 一个 Vector3。
 * 用它当基准，验证分帧重构没有改变布局语义。
 */
function legacyLayout(graph: MemoryGraphResponse): Map<string, THREE.Vector3> {
  const positions = new Map(
    graph.nodes.map((node, index) => {
      const angle = seededUnit(node.node_id, 0) * Math.PI * 2;
      const elevation = (seededUnit(node.node_id, 1) - 0.5) * Math.PI;
      const radius = 3.2 + (index % 5) * 0.24;
      return [
        node.node_id,
        new THREE.Vector3(
          Math.cos(angle) * Math.cos(elevation) * radius,
          Math.sin(elevation) * radius,
          Math.sin(angle) * Math.cos(elevation) * radius,
        ),
      ] as const;
    }),
  );

  for (let iteration = 0; iteration < 90; iteration += 1) {
    const force = new Map(
      graph.nodes.map((node) => [node.node_id, new THREE.Vector3()]),
    );
    for (let left = 0; left < graph.nodes.length; left += 1) {
      for (let right = left + 1; right < graph.nodes.length; right += 1) {
        const a = graph.nodes[left];
        const b = graph.nodes[right];
        const aPosition = positions.get(a.node_id);
        const bPosition = positions.get(b.node_id);
        const aForce = force.get(a.node_id);
        const bForce = force.get(b.node_id);
        if (!aPosition || !bPosition || !aForce || !bForce) continue;
        const delta = aPosition.clone().sub(bPosition);
        const distanceSq = Math.max(0.12, delta.lengthSq());
        const repulsion = delta.normalize().multiplyScalar(0.035 / distanceSq);
        aForce.add(repulsion);
        bForce.sub(repulsion);
      }
    }
    for (const edge of graph.edges) {
      const a = positions.get(edge.source_node_id);
      const b = positions.get(edge.target_node_id);
      if (!a || !b) continue;
      const delta = b.clone().sub(a);
      const distance = delta.length();
      const target = edge.relation === "evidence_source" ? 2.5 : 1.35;
      const spring = delta
        .normalize()
        .multiplyScalar((distance - target) * 0.018);
      force.get(edge.source_node_id)?.add(spring);
      force.get(edge.target_node_id)?.sub(spring);
    }
    for (const node of graph.nodes) {
      const position = positions.get(node.node_id);
      const nodeForce = force.get(node.node_id);
      if (position && nodeForce) {
        position.add(nodeForce).multiplyScalar(0.995);
      }
    }
  }
  return positions;
}

function seededUnit(value: string, salt: number): number {
  let hash = 2166136261 + salt;
  for (const character of value) {
    hash ^= character.charCodeAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0) / 4294967295;
}

function snapshot(positions: Map<string, THREE.Vector3>): [string, number[]][] {
  return [...positions.entries()]
    .map(
      ([id, vector]) =>
        [id, [vector.x, vector.y, vector.z]] as [string, number[]],
    )
    .sort((left, right) => left[0].localeCompare(right[0]));
}

describe("createRelationLayout", () => {
  it("rejects runaway cached coordinates for the current graph", () => {
    const nodes = [{ node_id: "existing" }, { node_id: "new" }];
    const cache = new Map([
      ["existing", new THREE.Vector3(1, 2, 3)],
      ["unrelated", new THREE.Vector3(Number.NaN, 0, 0)],
    ]);
    expect(isGraphLayoutCacheValid(nodes, cache)).toBe(true);
    cache.set("existing", new THREE.Vector3(1e57, 0, 0));
    expect(isGraphLayoutCacheValid(nodes, cache)).toBe(false);
    cache.set("existing", new THREE.Vector3(Number.NaN, 0, 0));
    expect(isGraphLayoutCacheValid(nodes, cache)).toBe(false);
  });
  it("分帧推进与一次算完结果完全一致", () => {
    const graph = makeGraph(14);

    const stepped = createRelationLayout(graph);
    let guard = 0;
    while (stepped.step()) {
      guard += 1;
      if (guard > 500) throw new Error("step 未在预期迭代数内收尾");
    }

    const settled = createRelationLayout(graph);
    settled.settle();

    expect(snapshot(stepped.positions)).toEqual(snapshot(settled.positions));
    expect(stepped.done()).toBe(true);
    expect(settled.done()).toBe(true);
  });

  it("与重构前的原算法逐坐标一致，布局语义未变", () => {
    const graph = makeGraph(14);
    const next = createRelationLayout(graph);
    next.settle();
    const legacy = legacyLayout(graph);

    const a = snapshot(next.positions);
    const b = snapshot(legacy);
    expect(a).toHaveLength(b.length);
    for (let index = 0; index < a.length; index += 1) {
      expect(a[index][0]).toBe(b[index][0]);
      for (let axis = 0; axis < 3; axis += 1) {
        // 完全相同的运算顺序，允许的只有浮点表示误差
        expect(a[index][1][axis]).toBeCloseTo(b[index][1][axis], 10);
      }
    }
  });

  it("同一张图布局可复现，不会每次乱跳", () => {
    const graph = makeGraph(11);
    const first = createRelationLayout(graph);
    first.settle();
    const second = createRelationLayout(graph);
    second.settle();
    expect(snapshot(first.positions)).toEqual(snapshot(second.positions));
  });

  it("step 在任意中途打断时坐标都是有限值", () => {
    const graph = makeGraph(20);
    const layout = createRelationLayout(graph);
    for (let taken = 0; taken < 30; taken += 1) {
      layout.step();
      for (const vector of layout.positions.values()) {
        expect(Number.isFinite(vector.x)).toBe(true);
        expect(Number.isFinite(vector.y)).toBe(true);
        expect(Number.isFinite(vector.z)).toBe(true);
      }
    }
  });

  it("收敛后节点不会飞出可视范围，也不会互相重合", () => {
    const graph = makeGraph(16);
    const layout = createRelationLayout(graph);
    layout.settle();
    const vectors = [...layout.positions.values()];
    for (const vector of vectors) {
      expect(vector.length()).toBeLessThan(20);
    }
    let minimum = Number.POSITIVE_INFINITY;
    for (let left = 0; left < vectors.length; left += 1) {
      for (let right = left + 1; right < vectors.length; right += 1) {
        minimum = Math.min(minimum, vectors[left].distanceTo(vectors[right]));
      }
    }
    expect(minimum).toBeGreaterThan(0.05);
  });

  it.each([80, 160, 300])(
    "keeps a source with %i nodes inside the graph",
    (nodeCount) => {
      const graph = makeGraph(nodeCount);
      graph.edges = graph.nodes.slice(1).map((node, index) => ({
        edge_id: `source-edge-${index}`,
        source_node_id: graph.nodes[0].node_id,
        target_node_id: node.node_id,
        relation: "evidence_source",
        weight: null,
        origin: "provenance",
      }));
      const layout = createRelationLayout(graph);
      while (!layout.done()) {
        layout.step();
        for (const position of layout.positions.values()) {
          expect(Number.isFinite(position.length())).toBe(true);
          expect(position.length()).toBeLessThan(12);
        }
      }
    },
  );

  it("空图与单节点图不抛错", () => {
    const empty = createRelationLayout(makeGraph(0));
    empty.settle();
    expect(empty.positions.size).toBe(0);

    const single = createRelationLayout(makeGraph(1));
    single.settle();
    expect(single.positions.size).toBe(1);
    expect(single.done()).toBe(true);
  });

  it("边指向不存在的节点时跳过而不抛错", () => {
    const graph = makeGraph(6);
    graph.edges.push({
      edge_id: "dangling",
      source_node_id: "node-0",
      target_node_id: "missing-node",
      relation: "derived_from",
      weight: null,
      origin: "provenance",
    });
    const layout = createRelationLayout(graph);
    expect(() => layout.settle()).not.toThrow();
    expect(layout.positions.size).toBe(6);
  });
});
