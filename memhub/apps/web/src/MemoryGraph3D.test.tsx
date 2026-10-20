// @vitest-environment jsdom

import type {
  MemoryGraphNode,
  MemoryGraphResponse,
} from "@one-memory/contracts";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { MemoryGraph3D } from "./MemoryGraph3D";
import { setUiLanguage } from "./ui-language";

afterEach(cleanup);

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
      memory_id: null,
      source_id: null,
      kind: null,
      status: "active",
      confidence: 0.8,
    }),
  );
  return {
    total_memories: nodeCount,
    projected_memories: nodeCount,
    nodes,
    edges: nodes.slice(1).map((node, index) => ({
      edge_id: `edge-${index}`,
      source_node_id: "node-0",
      target_node_id: node.node_id,
      relation: "derived_from",
      weight: null,
      origin: "provenance" as const,
    })),
    hy_graph_available: true,
  };
}

describe("MemoryGraph3D", () => {
  it("图谱降级提示随语言切换，节点标签保持原样", async () => {
    render(
      <MemoryGraph3D
        graph={makeGraph(1)}
        selectedNodeId={null}
        onSelectNode={() => {}}
      />,
    );
    try {
      await act(() => setUiLanguage("en"));
      expect(
        screen.getByText(
          "3D view is unavailable on this device. Showing the connections as a list.",
        ),
      ).toBeTruthy();
      expect(screen.getByRole("button", { name: "节点 0" })).toBeTruthy();
    } finally {
      await act(() => setUiLanguage("zh-CN"));
    }
  });
  it("没有 WebGL 时给出可访问降级提示", () => {
    render(
      <MemoryGraph3D
        graph={makeGraph(8)}
        selectedNodeId={null}
        onSelectNode={() => {}}
      />,
    );
    expect(screen.getByText(/这台设备打不开 3D 视图/)).toBeDefined();
  });

  // 场景装配（布局分帧、入场动画、按需渲染、选中态走 ref 而非 effect 依赖）
  // 在这里没有守卫，而且不是遗漏：jsdom 无 WebGL，effect 在建 renderer 时就抛并
  // 提前返回，装配代码一行都不会执行。恢复配方见 OPEN-ITEMS A-31。
  // 不要在这里补一条断言组件没被卸载的用例：那条测过，加不加修复都绿。
});
