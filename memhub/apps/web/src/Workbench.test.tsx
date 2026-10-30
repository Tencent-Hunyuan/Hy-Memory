// @vitest-environment jsdom

import type {
  AskMemoryResponse,
  GovernedMemory,
  MemoryDetail,
  MemoryGraphNode,
  MemoryGraphResponse,
} from "@one-memory/contracts";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "./gui-language";
import { OptionMenu } from "./OptionMenu";
import { Answer, NodeDetail } from "./Workbench";

it("localiza y mantiene cerrado el menú auxiliar sin opciones", async () => {
  window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "es");
  render(
    <LanguageProvider>
      <OptionMenu value="" options={[]} onChange={vi.fn()} />
    </LanguageProvider>,
  );
  const menu = screen.getByRole("combobox", {
    name: "Selecciona una opción",
  });
  menu.focus();
  await userEvent.keyboard("{Enter}{ArrowDown}");
  expect(menu.getAttribute("aria-expanded")).toBe("false");
  expect(menu.getAttribute("aria-activedescendant")).toBeNull();
  window.localStorage.removeItem(LANGUAGE_STORAGE_KEY);
});

it("traduce la respuesta visible sin traducir la respuesta del modelo", () => {
  window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "es");
  render(
    <LanguageProvider>
      <Answer
        answer={null}
        streaming={{ text: "Respuesta 中文 opaca", memories: [] }}
        onOpenMemory={vi.fn()}
        onSaveAnswer={vi.fn()}
        asPage={false}
      />
    </LanguageProvider>,
  );
  expect(screen.getByText("Respuesta 中文 opaca")).toBeTruthy();
  expect(screen.getByText("Se recuperaron 0 memorias")).toBeTruthy();
  window.localStorage.removeItem(LANGUAGE_STORAGE_KEY);
});

afterEach(cleanup);

function memory(
  id: string,
  overrides: Partial<GovernedMemory> = {},
): GovernedMemory {
  return {
    memory_id: id,
    user_id: "local",
    kind: "chat",
    status: "confirmed",
    content: `记忆 ${id}`,
    scopes: ["all-apps"],
    evidence_ids: [`ev-${id}`],
    origin: "extracted",
    tags: [],
    revision: 1,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    deleted_at: null,
    ...overrides,
  };
}

const detail: MemoryDetail = {
  memory: memory("memory-1", { content: "统一用 axios 封装" }),
  evidence: [],
  history: [],
  index_sync: null,
} as MemoryDetail;

function nodeDetail(props: Partial<Parameters<typeof NodeDetail>[0]> = {}) {
  const handlers = {
    onDraft: vi.fn(),
    onSave: vi.fn(),
    onDelete: vi.fn(),
    onClose: vi.fn(),
  };
  render(
    <NodeDetail
      node={null}
      graph={null}
      detail={null}
      draft=""
      busy={false}
      matterLabel="全部"
      requestIds={{ current: new Map() }}
      {...handlers}
      {...props}
    />,
  );
  return handlers;
}

describe("记忆可以改也可以删", () => {
  it("选中一条记忆后，改写和删除都在手边", async () => {
    const user = userEvent.setup();
    const { onDelete } = nodeDetail({ detail, draft: "统一用 axios 封装" });

    expect(screen.getByLabelText("记忆内容")).toBeTruthy();
    expect(screen.getByRole("button", { name: "保存纠正" })).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "删除" }));
    expect(onDelete).toHaveBeenCalledTimes(1);
  });

  it("没有撤销，也没有索引同步：SDK 记忆没有版本可退", () => {
    nodeDetail({
      detail: {
        ...detail,
        history: [
          { ...detail.memory, revision: 1, action: "create" },
          { ...detail.memory, revision: 2, action: "correct" },
        ],
      } as unknown as MemoryDetail,
      draft: "统一用 axios 封装",
    });

    expect(screen.queryByRole("button", { name: "撤销上次修改" })).toBeNull();
    expect(screen.queryByRole("button", { name: /重试同步/ })).toBeNull();
  });

  it("改动文字会作废预备好的请求 id，避免用旧 id 提交新内容", async () => {
    const user = userEvent.setup();
    const requestIds = { current: new Map([["memory-1:correct:1", "old"]]) };
    nodeDetail({ detail, draft: "统一用 axios 封装", requestIds });

    await user.type(screen.getByLabelText("记忆内容"), "改");

    expect(requestIds.current.size).toBe(0);
  });

  it("已删除的记忆不再摆一个删除按钮", () => {
    nodeDetail({
      detail: {
        ...detail,
        memory: { ...detail.memory, status: "deleted" },
      } as MemoryDetail,
      draft: "统一用 axios 封装",
    });

    expect(screen.queryByRole("button", { name: "删除" })).toBeNull();
    expect(screen.queryByLabelText("记忆内容")).toBeNull();
  });
});

describe("节点详情说得出每条线的来头", () => {
  const node = (id: string, type: string, label: string) =>
    ({ node_id: id, node_type: type, label }) as unknown as MemoryGraphNode;

  it("一个主题挂着的记忆和这条线的来头都说得出来", () => {
    // edgeOriginLabel 是一条 if 链、以「原话来源」兜底，所以漏掉一种来源不会
    // 报错，只会把主题的线说成是原话来源。
    nodeDetail({
      node: node("topic:axios", "topic", "axios"),
      graph: {
        total_memories: 2,
        projected_memories: 2,
        hy_graph_available: false,
        nodes: [
          node("topic:axios", "topic", "axios"),
          node("memory:a", "memory", "统一走 axios"),
          node("memory:b", "memory", "测试先行"),
        ],
        edges: [
          {
            edge_id: "e1",
            source_node_id: "topic:axios",
            target_node_id: "memory:a",
            relation: "shared_tag",
            origin: "shared_tag",
          },
          {
            edge_id: "e2",
            source_node_id: "topic:axios",
            target_node_id: "memory:b",
            relation: "shared_tag",
            origin: "shared_tag",
          },
        ],
      } as MemoryGraphResponse,
    });

    const panel = screen.getByLabelText("这个节点");
    expect(panel.textContent).toContain("主题");
    expect(panel.textContent).toContain("统一走 axios");
    expect(panel.textContent).toContain("同一个主题");
    expect(panel.textContent).not.toContain("原话来源");
  });

  it("Core 送来未知的节点类型或连线来头时，仍然说得出一个词而不是留空", () => {
    nodeDetail({
      node: node("future:x", "future_kind", "新节点"),
      graph: {
        total_memories: 1,
        projected_memories: 1,
        hy_graph_available: false,
        nodes: [
          node("future:x", "future_kind", "新节点"),
          node("memory:a", "memory", "统一走 axios"),
        ],
        edges: [
          {
            edge_id: "e1",
            source_node_id: "future:x",
            target_node_id: "memory:a",
            relation: "related_to",
            origin: "future_origin",
          },
        ],
      } as unknown as MemoryGraphResponse,
    });

    const panel = screen.getByLabelText("这个节点");
    expect(panel.querySelector(".wb-status")?.textContent).toBe("记忆");
    expect(panel.querySelector(".wb-trace-origin")?.textContent).toBe(
      "原话来源",
    );
  });
});

const ANSWER = {
  answer: "综合起来，你在前端一律走 axios 封装。",
  memories: [
    memory("m1", { evidence_ids: ["ev-a", "ev-b"] }),
    memory("m2", { evidence_ids: ["ev-b", "ev-c"] }),
  ],
  mode: "generated",
} as AskMemoryResponse;

function answer(props: Partial<Parameters<typeof Answer>[0]> = {}) {
  const onSaveAnswer = vi.fn();
  render(
    <Answer
      answer={null}
      streaming={null}
      onOpenMemory={vi.fn()}
      onSaveAnswer={onSaveAnswer}
      asPage={false}
      {...props}
    />,
  );
  return { onSaveAnswer };
}

describe("问出来的东西留得下来", () => {
  it("存下来带着它依据的那几条记忆的出处", async () => {
    const user = userEvent.setup();
    const { onSaveAnswer } = answer({ answer: ANSWER });

    await user.click(screen.getByRole("button", { name: "存为记忆" }));

    await waitFor(() =>
      expect(onSaveAnswer).toHaveBeenCalledWith({
        content: "综合起来，你在前端一律走 axios 封装。",
        evidence_ids: ["ev-a", "ev-b", "ev-c"],
      }),
    );
  });

  it("没命中任何记忆的回答存不下来", () => {
    answer({
      answer: {
        answer: "没有找到相关记忆。",
        memories: [],
        mode: "empty",
      } as AskMemoryResponse,
    });

    expect(screen.queryByRole("button", { name: "存为记忆" })).toBeNull();
  });

  it("还在流式输出的时候不给存，存下来的得是完整那一份", () => {
    answer({ streaming: { text: "综合起", memories: [memory("m1")] } });

    expect(screen.queryByRole("button", { name: "存为记忆" })).toBeNull();
  });

  it("答案长到不像一条记忆时，不硬塞进库", () => {
    answer({
      answer: { ...ANSWER, answer: "很".repeat(601) } as AskMemoryResponse,
    });

    expect(screen.queryByRole("button", { name: "存为记忆" })).toBeNull();
    expect(screen.getByText(/太长/)).toBeTruthy();
  });
});
