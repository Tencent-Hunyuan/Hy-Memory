// @vitest-environment jsdom
import type {
  GovernedMemory,
  MemoryDetail,
  MemoryGraphNode,
  MemoryGraphResponse,
  MemoryProcessingSummary,
  SourceImportReceipt as Receipt,
  SourceResponse,
} from "@one-memory/contracts";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  DIALOGUE_PANE_VISIBLE,
  hitToDialogueItem,
  MemoryMap,
} from "./MemoryMap";
import type { MemoryArrival } from "./memory-arrival-state";

const {
  conversation,
  recentConversations,
  searchConversations,
  sourceReceipt,
} = vi.hoisted(() => ({
  conversation: vi.fn(),
  recentConversations: vi.fn(),
  searchConversations: vi.fn(),
  sourceReceipt: vi.fn(),
}));
vi.mock("./api", () => ({
  getConversation: conversation,
  getRecentConversations: recentConversations,
  searchConversations,
  getSourceImportReceipt: sourceReceipt,
}));
vi.mock("./MemoryGraph3D", () => ({
  MemoryGraph3D: ({
    graph,
    selectedNodeId,
    onSelectNode,
  }: {
    graph: MemoryGraphResponse;
    selectedNodeId: string | null;
    onSelectNode(node: MemoryGraphNode): void;
  }) => (
    <div data-testid="canvas" data-selected={selectedNodeId}>
      {graph.nodes.map((n) => (
        <button
          key={n.node_id}
          type="button"
          aria-label={`图中选择 ${n.label}`}
          onClick={() => onSelectNode(n)}
        >
          {n.label}
        </button>
      ))}
    </div>
  ),
}));
beforeEach(() => {
  recentConversations.mockResolvedValue({
    items: [],
    total: 0,
    offset: 0,
    limit: 30,
  });
  searchConversations.mockResolvedValue({ hits: [], empty_reason: "no_match" });
});
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
  vi.unstubAllGlobals();
});
const memories = ["第一条", "第二条"].map((content, i) => ({
  memory_id: `m${i}`,
  content,
  tags: ["产品"],
  evidence_ids: ["shared"],
  status: "confirmed",
  origin: "extracted",
  revision: 1,
  scopes: ["all-apps"],
})) as GovernedMemory[];
const graph = {
  total_memories: 2,
  nodes: [
    ...memories.map((m) => ({
      node_id: `memory:${m.memory_id}`,
      memory_id: m.memory_id,
      node_type: "memory",
      label: m.content,
    })),
    {
      node_id: "source:codex",
      node_type: "source",
      source_id: "codex",
      label: "Codex",
    },
  ],
  edges: memories.map((m) => ({
    edge_id: m.memory_id,
    source_node_id: `memory:${m.memory_id}`,
    target_node_id: "source:codex",
    relation: "evidence_source",
    origin: "provenance",
  })),
} as MemoryGraphResponse;

it("drops the progress button on the empty memory page and leaves it once memories exist", () => {
  const view = render(<Host simplified items={[]} />);
  expect(
    screen.getByRole("heading", { name: "还没有已生成的记忆" }),
  ).toBeTruthy();
  expect(screen.queryByRole("button", { name: "查看进度" })).toBeNull();
  expect(screen.getByRole("button", { name: "返回应用" })).toBeTruthy();
  view.rerender(<Host simplified />);
  expect(
    screen.queryByRole("heading", { name: "还没有已生成的记忆" }),
  ).toBeNull();
  expect(screen.getByRole("heading", { name: "记忆详情" })).toBeTruthy();
});

it("OBS-22: the graph inspector has no editing entry and never says changes cannot be saved", async () => {
  const page = {
    items: memories,
    total: memories.length,
    offset: 0,
    limit: 100,
    source_ids_by_memory: { m0: ["codex"], m1: ["codex"] },
  };
  render(
    <Host
      simplified
      libraryView={{
        query: "",
        source: "",
        page,
        loading: true,
        failed: false,
        stale: false,
        onQuery: vi.fn(),
        onSource: vi.fn(),
        resetPage: vi.fn(),
        onPage: vi.fn(),
        onRetry: vi.fn(),
      }}
    />,
  );
  await act(async () => {});
  // The detail pane is open and the page is being re-read, which used to be
  // the exact condition that printed the warning.
  expect(screen.getByText("共同原文")).toBeTruthy();
  expect(screen.queryByText(/暂不能保存更改/)).toBeNull();
});

it("OBS-19: Enter in the memory filter re-reads, exactly like the refresh button", async () => {
  const onRetry = vi.fn();
  const page = {
    items: memories,
    total: memories.length,
    offset: 0,
    limit: 100,
    source_ids_by_memory: { m0: ["codex"], m1: ["codex"] },
  };
  render(
    <Host
      simplified
      libraryView={{
        query: "",
        source: "",
        page,
        loading: false,
        failed: false,
        stale: false,
        onQuery: vi.fn(),
        onSource: vi.fn(),
        resetPage: vi.fn(),
        onPage: vi.fn(),
        onRetry,
      }}
    />,
  );
  await act(async () => {});
  await userEvent.click(screen.getByRole("button", { name: "刷新地图记忆" }));
  expect(onRetry).toHaveBeenCalledTimes(1);
  await userEvent.type(
    screen.getByRole("textbox", { name: "筛选地图记忆" }),
    "第一{Enter}",
  );
  expect(onRetry).toHaveBeenCalledTimes(2);
});

it("OBS-15: switching memory shows a read in progress instead of the previous memory", async () => {
  const user = userEvent.setup();
  // The read is deliberately never answered: what matters is what is on
  // screen while it is open.
  render(<Host simplified deferMemoryId="m1" />);
  const list = screen.getByRole("list", { name: "地图记忆列表" });
  expect(screen.getByText("共同原文")).toBeTruthy();
  await user.click(within(list).getByRole("button", { name: /第二条/ }));
  // The first memory's evidence must not still be presented as if it
  // belonged to the selection that is now the second one.
  expect(screen.queryByText("共同原文")).toBeNull();
  expect(screen.getByRole("status").textContent).toContain("正在读取这条记忆");
});

it("OBS-12: the graph legend stays with the graph instead of sitting under a stage without one", () => {
  // While the first page is still being read there is no graph yet: the
  // legend used to sit there anyway, describing nothing.
  const view = render(<Host simplified items={[]} loaded={false} />);
  expect(screen.queryByText("连线表示来源归属")).toBeNull();
  view.rerender(<Host simplified />);
  expect(screen.getByText("连线表示来源归属")).toBeTruthy();
});

it("labels the right-hand empty state as memory details", () => {
  render(<Host simplified />);
  expect(screen.getByRole("heading", { name: "记忆详情" })).toBeTruthy();
  expect(screen.queryByText("原始证据")).toBeNull();
});

it("opens the newest listed memory in the detail pane and keeps it closed after dismiss", async () => {
  const user = userEvent.setup();
  render(<Host simplified />);
  const list = screen.getByRole("list", { name: "地图记忆列表" });
  const first = within(list).getByRole("button", { name: /第一条/ });
  expect(first.getAttribute("aria-pressed")).toBe("true");
  expect(screen.getByText("共同原文")).toBeTruthy();
  expect(screen.getByText(/原对话/)).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "关闭记忆详情" }));
  expect(screen.queryByText("共同原文")).toBeNull();
});

it("OBS-14: lists what the conversation settled instead of asking to settle first", async () => {
  // A conversation that already produced memories still showed the
  // "settle memories first" placeholder in the detail pane, which reads as
  // "nothing happened" on exactly the conversations where something did.
  const user = userEvent.setup();
  render(<Host simplified />);
  await user.click(screen.getByRole("button", { name: "关闭记忆详情" }));
  expect(screen.queryByText("沉淀记忆后查看详情")).toBeNull();
  expect(screen.getByText("已沉淀 2 条记忆")).toBeTruthy();
  const settled = screen.getByRole("list", { name: "已沉淀的记忆" });
  expect(
    within(settled)
      .getAllByRole("listitem")
      .map((item) => item.textContent),
  ).toEqual(["第一条", "第二条"]);
});

function Host({
  simplified = false,
  date = null,
  available = true,
  hidden = false,
  overlayBlocked = false,
  items = memories,
  arrival,
  sources = [],
  processing = null,
  loaded = true,
  deferMemoryId,
  onViewHistory,
  onConfigureModel = () => {},
  libraryView,
}: {
  simplified?: boolean;
  date?: string | null;
  available?: boolean;
  hidden?: boolean;
  overlayBlocked?: boolean;
  items?: GovernedMemory[];
  arrival?: MemoryArrival;
  sources?: SourceResponse[];
  processing?: MemoryProcessingSummary | null;
  loaded?: boolean;
  deferMemoryId?: string;
  onViewHistory?(): void;
  onConfigureModel?(): void;
  libraryView?: Parameters<typeof MemoryMap>[0]["libraryView"];
} = {}) {
  const [detail, setDetail] = useState<MemoryDetail | null>(null);
  return (
    <MemoryMap
      simplified={simplified}
      libraryView={libraryView}
      hidden={hidden}
      overlayBlocked={overlayBlocked}
      memories={items}
      graph={graph}
      sources={sources}
      arrival={arrival}
      onViewHistory={onViewHistory}
      loaded={loaded}
      processing={processing}
      detail={
        detail
          ? {
              ...detail,
              evidence: detail.evidence.map((item) => ({ ...item, available })),
            }
          : null
      }
      draft={detail?.memory.content ?? ""}
      scopes="all-apps"
      busy={false}
      answer={null}
      streaming={null}
      requestIds={{ current: new Map() }}
      onOpenMemory={(id) => {
        // Lets a test leave one read unanswered.
        if (deferMemoryId && id === deferMemoryId) return;
        setDetail({
          memory: items.find((m) => m.memory_id === id) ?? items[0],
          evidence: [
            {
              evidence_id: "shared",
              available: true,
              source_id: "codex",
              written_by: "person",
              role: "user",
              created_at: date,
              verbatim: "共同原文",
            },
          ],
          history: [],
          index_sync: null,
        });
      }}
      onCloseDetail={() => setDetail(null)}
      onAsk={() => {}}
      onDraft={() => {}}
      onScopes={() => {}}
      onSave={() => {}}
      onAction={() => {}}
      onSyncIndex={() => {}}
      onSaveAnswer={() => {}}
      onBuildRelations={() => {}}
      onConnect={() => {}}
      onConfigureModel={onConfigureModel}
    />
  );
}
it("does not present an unverified empty page as zero memories", () => {
  const page = {
    items: [],
    total: 0,
    offset: 0,
    limit: 100,
    source_ids_by_memory: {},
  };
  const base = {
    query: "",
    source: "",
    page,
    loading: false,
    failed: false,
    stale: false,
    onQuery: vi.fn(),
    onSource: vi.fn(),
    resetPage: vi.fn(),
    onPage: vi.fn(),
    onRetry: vi.fn(),
  };
  const view = render(
    <Host simplified items={[]} libraryView={{ ...base, loading: true }} />,
  );
  expect(screen.getByText("正在读取…")).toBeTruthy();
  expect(screen.queryByText(/0 条/)).toBeNull();
  expect(screen.queryByText("还没有已生成的记忆")).toBeNull();

  view.rerender(
    <Host simplified items={[]} libraryView={{ ...base, failed: true }} />,
  );
  expect(screen.getByText("记忆读取失败，请重试。")).toBeTruthy();
  expect(screen.queryByText(/0 条/)).toBeNull();
  expect(screen.queryByText("还没有已生成的记忆")).toBeNull();

  view.rerender(
    <Host simplified items={[]} libraryView={{ ...base, stale: true }} />,
  );
  expect(screen.getByText("正在核对来源…")).toBeTruthy();
  expect(screen.queryByText(/0 条/)).toBeNull();
  expect(screen.queryByText("还没有已生成的记忆")).toBeNull();
});
it("starts with receipt-backed source grouping, keeping topics secondary", () => {
  render(<Host />);
  expect(
    screen.getByRole("button", { name: "来源" }).getAttribute("aria-pressed"),
  ).toBe("true");
  expect(
    screen.getByRole("button", { name: "主题" }).getAttribute("aria-pressed"),
  ).toBe("false");
  expect(screen.getByRole("button", { name: "Codex2" })).toBeDefined();
});

function pagedView(
  page: Parameters<typeof MemoryMap>[0]["libraryView"] extends infer V
    ? V extends { page: infer P }
      ? P
      : never
    : never,
) {
  return {
    query: "",
    source: "",
    page,
    loading: false,
    failed: false,
    stale: false,
    onQuery: vi.fn(),
    onSource: vi.fn(),
    resetPage: vi.fn(),
    onPage: vi.fn(),
    onRetry: vi.fn(),
  };
}

it("D96: the memory page has no raw-dialogue tab and never loads the dialogue list", async () => {
  render(<Host simplified />);
  expect(screen.queryByRole("tab", { name: "原始对话" })).toBeNull();
  expect(screen.queryByRole("tablist", { name: "记忆或原始对话" })).toBeNull();
  expect(screen.getByRole("list", { name: "地图记忆列表" })).toBeTruthy();
  await act(async () => {});
  expect(recentConversations).not.toHaveBeenCalled();
});

it("D96: lists a memory by when its conversation happened, not when it was generated", () => {
  const today = new Date().toISOString();
  const thisYear = new Date().getFullYear();
  const items = ["第一条", "第二条", "第三条"].map((content, i) => ({
    ...memories[0],
    memory_id: `t${i}`,
    content,
    updated_at: today,
  })) as GovernedMemory[];
  render(
    <Host
      simplified
      items={items}
      libraryView={pagedView({
        items,
        total: 519,
        offset: 100,
        limit: 100,
        source_ids_by_memory: { t0: [], t1: [], t2: [] },
        source_time_by_memory: {
          t0: { at: `${thisYear}-10-02T10:54:00`, basis: "conversation" },
          t1: { at: `${thisYear}-09-30T08:00:00`, basis: "import" },
        },
      })}
    />,
  );
  const list = screen.getByRole("list", { name: "地图记忆列表" });
  expect(
    within(list).getByRole("button", { name: /第一条/ }).textContent,
  ).toContain("对话于 10月2日");
  expect(
    within(list).getByRole("button", { name: /第二条/ }).textContent,
  ).toContain("导入于 9月30日");
  // D99: the generated day keeps its own line, so a memory made today from an
  // old conversation still reads as new rather than looking misplaced.
  expect(
    within(list).getByRole("button", { name: /第一条/ }).textContent,
  ).toContain("沉淀于 今天");
  // Nothing cited: keep the generation date.
  expect(
    within(list).getByRole("button", { name: /第三条/ }).textContent,
  ).toContain("今天");
  // The pager says how much there is, so it reads as a pager.
  expect(
    within(screen.getByRole("navigation", { name: "地图记忆分页" })).getByText(
      /共 519 条，第 2 \/ 6 页/,
    ),
  ).toBeTruthy();
});

it("D96: with conversations imported and no memory yet, says how many were imported", () => {
  render(
    <Host
      simplified
      items={[]}
      arrival={{
        importing: false,
        importedMessages: 40,
        importedConversations: 12,
        phase: "idle",
      }}
    />,
  );
  expect(screen.getByText(/已导入 12 段对话/)).toBeTruthy();
});

it.skipIf(!DIALOGUE_PANE_VISIBLE)(
  "lets keyboard arrows move between the memory and original-dialogue tabs",
  async () => {
    const user = userEvent.setup();
    render(<Host simplified />);
    const memoriesTab = screen.getByRole("tab", { name: "记忆" });
    const dialoguesTab = screen.getByRole("tab", { name: "原始对话" });
    expect(memoriesTab.getAttribute("aria-selected")).toBe("true");
    expect(dialoguesTab.getAttribute("aria-selected")).toBe("false");
    memoriesTab.focus();
    await user.keyboard("{ArrowRight}");
    expect(dialoguesTab.getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(dialoguesTab);
    await user.keyboard("{ArrowLeft}");
    expect(memoriesTab.getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(memoriesTab);
  },
);

it.skipIf(!DIALOGUE_PANE_VISIBLE)(
  "searches all conversations through the backend in simplified mode",
  async () => {
    conversation.mockResolvedValueOnce({
      conversation_id: "conversation-1",
      source_id: "workbuddy",
      messages: [],
    });
    searchConversations.mockResolvedValue({
      hits: [
        {
          evidence_id: "hit-1",
          source_id: "workbuddy",
          conversation_id: "conversation-1",
          excerpt: "红眼航班要靠过道",
          imported_at: "2026-10-04T10:00:00Z",
          written_by: "person",
          truncated: false,
        },
      ],
      empty_reason: null,
      imported_messages: 1,
    });
    render(<Host simplified />);
    await userEvent.click(screen.getByRole("tab", { name: "原始对话" }));
    await userEvent.type(
      screen.getByRole("textbox", { name: "筛选地图记忆" }),
      "红眼",
    );

    expect(await screen.findByText("红眼航班要靠过道")).toBeTruthy();
    expect(searchConversations).toHaveBeenLastCalledWith("红眼", 100);
  },
);

it("simplifies the surface without a question form and disposes the hidden graph owner", () => {
  const view = render(<Host simplified />);
  expect(screen.queryByRole("textbox", { name: "向记忆提问" })).toBeNull();
  expect(screen.getByTestId("canvas")).toBeTruthy();
  view.rerender(<Host simplified hidden />);
  expect(screen.queryByTestId("canvas")).toBeNull();
  view.rerender(<Host simplified />);
  expect(screen.getByTestId("canvas")).toBeTruthy();
});

it("explains missing setup during ongoing raw import without pretending to generate", async () => {
  const configure = vi.fn(),
    history = vi.fn();
  render(
    <Host
      items={[]}
      arrival={{
        importing: true,
        importedMessages: 29155,
        importedConversations: 400,
        phase: "model-required",
      }}
      onConfigureModel={configure}
      onViewHistory={history}
    />,
  );
  expect(
    screen.getByRole("region", { name: "记忆整理状态" }).textContent,
  ).toContain("尚未设置整理模型");
  expect(screen.getByText(/29,155 条消息/)).toBeDefined();
  expect(screen.getByText("原文仍在导入")).toBeDefined();
  expect(screen.queryByText("暂无已整理的记忆")).toBeNull();
  expect(document.querySelector(".memory-arrival-spinner")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "设置记忆整理" }));
  await userEvent.click(
    screen.getByRole("button", { name: "查看已保存的对话" }),
  );
  expect(configure).toHaveBeenCalledOnce();
  expect(history).toHaveBeenCalledOnce();
});

it("adds real memories without replacing graph, current detail, or question draft", async () => {
  const view = render(<Host />);
  const canvas = screen.getByTestId("canvas");
  await userEvent.click(
    screen.getByRole("button", { name: "图中选择 第一条" }),
  );
  await userEvent.type(
    screen.getByRole("textbox", { name: "向记忆提问" }),
    "保留这个问题",
  );
  const added = { ...memories[0], memory_id: "m2", content: "第三条" };
  view.rerender(<Host items={[...memories, added]} />);
  expect(screen.getByTestId("canvas")).toBe(canvas);
  expect(canvas.dataset.selected).toBe("memory:m0");
  expect(
    (screen.getByRole("textbox", { name: "向记忆提问" }) as HTMLInputElement)
      .value,
  ).toBe("保留这个问题");
  expect(screen.getByRole("button", { name: "共同原文" })).toBeDefined();
  expect(screen.getByText("新增 1 条记忆")).toBeDefined();
  await userEvent.click(
    screen.getByRole("button", { name: "关闭新增记忆提示" }),
  );
  view.rerender(<Host items={[added, ...memories]} />);
  expect(screen.queryByText("新增 1 条记忆")).toBeNull();
});

it("announces the first arriving batch but not an initial populated snapshot", () => {
  const view = render(<Host items={[]} />);
  expect(screen.queryByText(/新增 .* 条记忆/)).toBeNull();
  view.rerender(<Host />);
  expect(screen.getByText("新增 2 条记忆")).toBeDefined();
  view.unmount();
  render(<Host />);
  expect(screen.queryByText(/新增 .* 条记忆/)).toBeNull();
});

it("does not reannounce removed and restored IDs as new memories", () => {
  const view = render(<Host items={[]} />);
  view.rerender(<Host />);
  expect(screen.getByText("新增 2 条记忆")).toBeDefined();
  view.rerender(<Host items={[]} />);
  expect(screen.queryByText(/新增 .* 条记忆/)).toBeNull();
  view.rerender(<Host />);
  expect(screen.queryByText(/新增 .* 条记忆/)).toBeNull();
});
it("keeps graph, list and reverse evidence selections linked", async () => {
  render(<Host />);
  await userEvent.click(
    within(screen.getByRole("list", { name: "地图记忆列表" })).getByRole(
      "button",
      { name: /第一条/ },
    ),
  );
  expect(screen.getByTestId("canvas").dataset.selected).toBe("memory:m0");
  await userEvent.click(screen.getByRole("button", { name: "共同原文" }));
  await userEvent.click(
    screen.getByRole("button", { name: "定位关联记忆：第二条" }),
  );
  expect(screen.getByTestId("canvas").dataset.selected).toBe("memory:m1");
  await userEvent.click(
    screen.getByRole("button", { name: "图中选择 第一条" }),
  );
  expect(
    within(screen.getByRole("list", { name: "地图记忆列表" }))
      .getByRole("button", { name: /第一条/ })
      .getAttribute("aria-pressed"),
  ).toBe("true");
});
it("does not reopen a previous transcript after switching memory", async () => {
  let finish: (value: unknown) => void = () => {};
  conversation.mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  render(<Host />);
  await userEvent.click(
    screen.getByRole("button", { name: "图中选择 第一条" }),
  );
  await userEvent.click(screen.getByRole("button", { name: "共同原文" }));
  await userEvent.click(screen.getByRole("button", { name: "看完整对话" }));
  await userEvent.click(
    screen.getByRole("button", { name: "图中选择 第二条" }),
  );
  await act(async () =>
    finish({
      messages: [
        { evidence_id: "old", verbatim: "过期原文", written_by: "person" },
      ],
    }),
  );
  expect(screen.queryByText("过期原文")).toBeNull();
});

it("MEM-MAP-13: closing a handoff does not clear the background selection", async () => {
  render(<Host />);
  await userEvent.click(
    screen.getByRole("button", { name: "图中选择 第一条" }),
  );
  await userEvent.click(screen.getByRole("button", { name: "带出所选记忆" }));
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.getByTestId("canvas").dataset.selected).toBe("memory:m0");
});

it("MEM-EVID-02: reopening evidence clears its previous request error", async () => {
  conversation.mockRejectedValue(new Error("unavailable"));
  render(<Host />);
  await userEvent.click(
    screen.getByRole("button", { name: "图中选择 第一条" }),
  );
  const quote = screen.getByRole("button", { name: "共同原文" });
  await userEvent.click(quote);
  await userEvent.click(screen.getByRole("button", { name: "看完整对话" }));
  expect(await screen.findByRole("alert")).toBeDefined();
  await userEvent.click(quote);
  await userEvent.click(quote);
  expect(screen.queryByRole("alert")).toBeNull();
});

it("MEM-EVID-01: invalid evidence time stays unknown", async () => {
  render(<Host date="not-a-date" />);
  await userEvent.click(
    screen.getByRole("button", { name: "图中选择 第一条" }),
  );
  expect(screen.queryByText("Invalid Date")).toBeNull();
  expect(screen.getByText("时间未知")).toBeDefined();
});

it("GRF-08: filtering keeps a still-visible selection and clears an excluded one", async () => {
  render(<Host />);
  await userEvent.click(
    screen.getByRole("button", { name: "图中选择 第一条" }),
  );
  const search = screen.getByRole("textbox", { name: "筛选地图记忆" });
  await userEvent.type(search, "第一");
  expect(screen.getByTestId("canvas").dataset.selected).toBe("memory:m0");
  await userEvent.clear(search);
  await userEvent.type(search, "第二");
  expect(screen.getByTestId("canvas").dataset.selected ?? "").toBe("");
  expect(screen.getByRole("status").textContent).toContain("不在当前筛选");
});

it("MEM-MAP-16: IME confirmation does not submit a question", () => {
  render(<Host />);
  const input = screen.getByRole("textbox", { name: "向记忆提问" });
  fireEvent.change(input, { target: { value: "当前背景" } });
  fireEvent.compositionStart(input);
  const form = input.closest("form");
  if (!form) throw new Error("Question form is missing");
  fireEvent.submit(form);
  expect((input as HTMLInputElement).value).toBe("当前背景");
  expect(screen.getByPlaceholderText("询问全部已授权可用记忆…")).toBeDefined();
});

it("MEM-EVID-05: an availability change without a memory revision clears an open transcript", async () => {
  conversation.mockResolvedValue({
    messages: [
      {
        evidence_id: "original",
        written_by: "person",
        verbatim: "完整敏感原文",
      },
    ],
  });
  const view = render(<Host />);
  await userEvent.click(
    screen.getByRole("button", { name: "图中选择 第一条" }),
  );
  await userEvent.click(screen.getByRole("button", { name: "共同原文" }));
  await userEvent.click(screen.getByRole("button", { name: "看完整对话" }));
  expect(await screen.findByText("完整敏感原文")).toBeDefined();
  view.rerender(<Host available={false} />);
  expect(screen.queryByText("完整敏感原文")).toBeNull();
  expect(screen.queryByRole("button", { name: "看完整对话" })).toBeNull();
});

it.each(["hidden", "overlayBlocked"] as const)(
  "TOUR: %s suspends compact evidence effects without clearing the graph or detail",
  async (flag) => {
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: query.includes("max-width"),
      addEventListener() {},
      removeEventListener() {},
    }));
    const view = render(
      <>
        <button type="button">Other route</button>
        <Host />
      </>,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "图中选择 第一条" }),
    );
    const panel = screen.getByRole("dialog", { name: "记忆与原文" });
    const canvas = screen.getByTestId("canvas");
    const other = screen.getByRole("button", { name: "Other route" });
    expect(other.inert).toBe(true);
    view.rerender(
      <>
        <button type="button">Other route</button>
        <Host {...{ [flag]: true }} />
      </>,
    );
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(other.inert).not.toBe(true);
    other.focus();
    const escapeEvent = new KeyboardEvent("keydown", {
      key: "Escape",
      bubbles: true,
      cancelable: true,
    });
    other.dispatchEvent(escapeEvent);
    expect(escapeEvent.defaultPrevented).toBe(false);
    expect(document.activeElement).toBe(other);
    expect(screen.getByTestId("canvas")).toBe(canvas);
    expect(canvas.dataset.selected).toBe("memory:m0");
    view.rerender(
      <>
        <button type="button">Other route</button>
        <Host />
      </>,
    );
    expect(screen.getByRole("dialog", { name: "记忆与原文" })).toBe(panel);
    expect(
      within(panel).getByRole("button", { name: "共同原文" }),
    ).toBeTruthy();
  },
);

it("TOUR: an inactive handoff cannot intercept Escape and its draft owner survives", async () => {
  const view = render(<Host />);
  await userEvent.click(
    screen.getByRole("button", { name: "图中选择 第一条" }),
  );
  await userEvent.click(screen.getByRole("button", { name: "带出所选记忆" }));
  const dialog = screen.getByRole("dialog");
  view.rerender(<Host hidden />);
  expect(screen.queryByRole("dialog")).toBeNull();
  const escapeEvent = new KeyboardEvent("keydown", {
    key: "Escape",
    bubbles: true,
    cancelable: true,
  });
  document.dispatchEvent(escapeEvent);
  expect(escapeEvent.defaultPrevented).toBe(false);
  view.rerender(<Host />);
  expect(screen.getByRole("dialog")).toBe(dialog);
  expect(screen.getByTestId("canvas").dataset.selected).toBe("memory:m0");
});

const importedSources = [
  {
    source_id: "codex",
    display_name: "Codex",
    authorized: true,
    imported_messages: 5,
    imported_conversations: 2,
  },
  {
    source_id: "cursor-local",
    display_name: "Cursor",
    authorized: true,
    imported_messages: 2,
    imported_conversations: 1,
  },
] as SourceResponse[];
const importedReceipt: Receipt = {
  source_id: "codex",
  measured_at: "2026-09-14T00:00:00Z",
  messages: 5,
  conversations: 2,
  person_messages: 2,
  agent_messages: 2,
  tool_messages: 0,
  unknown_author_messages: 1,
  unknown_time_messages: 1,
  examples: [
    {
      evidence_id: "raw-codex",
      role: "user",
      written_by: "person",
      created_at: null,
      excerpt: "合成来源原文，不是治理记忆。",
      truncated: false,
    },
  ],
};
const importedConversation = {
  source_id: "codex",
  conversation_id: "conversation-codex",
  messages: [
    {
      evidence_id: "raw-codex",
      role: "user",
      written_by: "person",
      created_at: null,
      verbatim: "合成来源完整对话。",
    },
  ],
};
async function openSourceReceipt() {
  await userEvent.click(
    screen.getByRole("button", { name: "图中选择 Codex · 2 段" }),
  );
  await userEvent.click(screen.getByText("查看已读内容"));
  await waitFor(() => expect(sourceReceipt).toHaveBeenCalledWith("codex"));
}

it("REV09: selecting an imported source retains the canvas and only its authorized node", async () => {
  render(<Host items={[]} sources={importedSources} />);
  const canvas = screen.getByTestId("canvas");
  expect(within(canvas).getAllByRole("button")).toHaveLength(2);
  await userEvent.click(
    screen.getByRole("button", { name: "图中选择 Codex · 2 段" }),
  );
  expect(screen.getByTestId("canvas")).toBe(canvas);
  expect(canvas.dataset.selected).toBe("source:codex");
  expect(within(canvas).getAllByRole("button")).toHaveLength(1);
  expect(screen.getByRole("heading", { name: "Codex" })).toBeTruthy();
  expect(
    screen.getByRole("list", { name: "地图记忆列表" }).children,
  ).toHaveLength(0);
  expect(sourceReceipt).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: /全部记忆/ }));
  expect(within(canvas).getAllByRole("button")).toHaveLength(2);
  expect(canvas.dataset.selected ?? "").toBe("");
});

it("REV09: fallback search matches source names and IDs without bypassing the selected source", async () => {
  render(<Host items={[]} sources={importedSources} />);
  const search = screen.getByRole("textbox", { name: "筛选地图记忆" });
  await userEvent.type(search, "LOCAL");
  expect(
    screen.getByRole("button", { name: "图中选择 Cursor · 1 段" }),
  ).toBeTruthy();
  expect(
    screen.queryByRole("button", { name: "图中选择 Codex · 2 段" }),
  ).toBeNull();
  await userEvent.clear(search);
  await userEvent.click(
    screen.getByRole("button", { name: "图中选择 Codex · 2 段" }),
  );
  await userEvent.type(search, "cOdEx");
  expect(screen.getByTestId("canvas").dataset.selected).toBe("source:codex");
  expect(screen.getByText("查看已读内容")).toBeTruthy();
  await userEvent.clear(search);
  await userEvent.type(search, "Cursor");
  expect(screen.queryByTestId("canvas")).toBeNull();
  expect(screen.queryByText("查看已读内容")).toBeNull();
  await userEvent.clear(search);
  expect(screen.getByTestId("canvas").dataset.selected ?? "").toBe("");
  expect(screen.queryByText("查看已读内容")).toBeNull();
});

it("REV09: source selection uses the actual permission-checked receipt and original conversation APIs", async () => {
  sourceReceipt.mockResolvedValue(importedReceipt);
  conversation.mockResolvedValue(importedConversation);
  render(<Host items={[]} sources={importedSources} />);
  await openSourceReceipt();
  expect(
    await screen.findByText(importedReceipt.examples[0].excerpt),
  ).toBeTruthy();
  expect(screen.getByText("用户 · 时间未知")).toBeTruthy();
  expect(screen.getByText("原始消息").nextElementSibling?.textContent).toBe(
    "5",
  );
  expect(conversation).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "查看完整对话" }));
  expect(await screen.findByText("合成来源完整对话。")).toBeTruthy();
  expect(conversation).toHaveBeenCalledExactlyOnceWith("raw-codex");
});

it.each(["revoked", "removed", "revoked-hidden"])(
  "REV09: %s source drops fallback, preview and a late receipt without reopening on reauthorization",
  async (state) => {
    let finish: (value: Receipt) => void = () => {};
    sourceReceipt.mockImplementation(
      () =>
        new Promise<Receipt>((resolve) => {
          finish = resolve;
        }),
    );
    const view = render(<Host items={[]} sources={[importedSources[0]]} />);
    await openSourceReceipt();
    view.rerender(
      <Host
        items={[]}
        hidden={state === "revoked-hidden"}
        sources={
          state === "removed"
            ? []
            : [{ ...importedSources[0], authorized: false }]
        }
      />,
    );
    expect(screen.queryByTestId("canvas")).toBeNull();
    expect(document.querySelector(".source-import-receipt")).toBeNull();
    await act(async () => finish(importedReceipt));
    view.rerender(<Host items={[]} sources={[importedSources[0]]} />);
    expect(screen.getByTestId("canvas").dataset.selected ?? "").toBe("");
    expect(screen.queryByText(importedReceipt.examples[0].excerpt)).toBeNull();
    expect(screen.queryByText("查看已读内容")).toBeNull();
    expect(sourceReceipt).toHaveBeenCalledTimes(1);
  },
);

it("REV09: revocation clears an already visible original and rejects a later conversation response", async () => {
  sourceReceipt.mockResolvedValue(importedReceipt);
  conversation.mockResolvedValueOnce(importedConversation);
  let finish: (value: unknown) => void = () => {};
  const view = render(<Host items={[]} sources={[importedSources[0]]} />);
  await openSourceReceipt();
  await userEvent.click(
    await screen.findByRole("button", { name: "查看完整对话" }),
  );
  expect(await screen.findByText("合成来源完整对话。")).toBeTruthy();
  conversation.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  await userEvent.click(screen.getByRole("button", { name: "查看完整对话" }));
  expect(conversation).toHaveBeenCalledTimes(2);
  view.rerender(
    <Host
      items={[]}
      sources={[{ ...importedSources[0], authorized: false }]}
    />,
  );
  expect(document.querySelector(".source-import-receipt")).toBeNull();
  await act(async () => finish(importedConversation));
  view.rerender(<Host items={[]} sources={[importedSources[0]]} />);
  expect(screen.queryByText("合成来源完整对话。")).toBeNull();
  expect(screen.queryByText(importedReceipt.examples[0].excerpt)).toBeNull();
});

it("REV09: changing source discards the previous source's pending original", async () => {
  sourceReceipt.mockResolvedValue(importedReceipt);
  let finish: (value: unknown) => void = () => {};
  conversation.mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  render(<Host items={[]} sources={importedSources} />);
  await openSourceReceipt();
  await userEvent.click(
    await screen.findByRole("button", { name: "查看完整对话" }),
  );
  await userEvent.click(screen.getByRole("button", { name: /全部记忆/ }));
  await userEvent.click(
    screen.getByRole("button", { name: "图中选择 Cursor · 1 段" }),
  );
  await act(async () => finish(importedConversation));
  expect(screen.queryByText("合成来源完整对话。")).toBeNull();
  expect(screen.queryByText(importedReceipt.examples[0].excerpt)).toBeNull();
  expect(screen.getByTestId("canvas").dataset.selected).toBe(
    "source:cursor-local",
  );
  expect(sourceReceipt).toHaveBeenCalledTimes(1);
});

it("REV09: compact preview close discards a pending original but keeps source selection for reopening", async () => {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query.includes("max-width"),
    addEventListener() {},
    removeEventListener() {},
  }));
  sourceReceipt.mockResolvedValue(importedReceipt);
  let finish: (value: unknown) => void = () => {};
  conversation.mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  render(<Host items={[]} sources={importedSources} />);
  await openSourceReceipt();
  await userEvent.click(
    await screen.findByRole("button", { name: "查看完整对话" }),
  );
  await userEvent.click(screen.getByRole("button", { name: "关闭记忆详情" }));
  await act(async () => finish(importedConversation));
  expect(document.querySelector(".source-import-receipt")).toBeNull();
  expect(screen.getByTestId("canvas").dataset.selected).toBe("source:codex");
  await userEvent.click(
    screen.getByRole("button", { name: "查看选中来源原文" }),
  );
  expect(screen.getByRole("dialog", { name: "记忆与原文" })).toBeTruthy();
  expect(screen.queryByText("合成来源完整对话。")).toBeNull();
  expect(screen.getByText("查看已读内容")).toBeTruthy();
});

it("REV09: first governed memories replace fallback without remounting the graph or breaking memory IDs", async () => {
  const view = render(<Host items={[]} sources={importedSources} />);
  const canvas = screen.getByTestId("canvas");
  await userEvent.click(
    screen.getByRole("button", { name: "图中选择 Codex · 2 段" }),
  );
  view.rerender(<Host sources={importedSources} />);
  expect(screen.getByTestId("canvas")).toBe(canvas);
  expect(canvas.dataset.selected).toBe("source:codex");
  await userEvent.click(
    screen.getByRole("button", { name: "图中选择 第一条" }),
  );
  expect(canvas.dataset.selected).toBe("memory:m0");
  expect(screen.queryByText("查看已读内容")).toBeNull();
  expect(screen.getByRole("button", { name: "共同原文" })).toBeTruthy();
});

it.each([
  { name: "empty", sources: [] },
  {
    name: "unauthorized",
    sources: [{ ...importedSources[0], authorized: false }],
  },
  {
    name: "not imported",
    sources: [
      {
        ...importedSources[0],
        imported_conversations: 0,
        imported_messages: 0,
      },
    ],
  },
])("REV09: $name sources mean no fallback nodes or preview", ({ sources }) => {
  render(<Host items={[]} sources={sources} />);
  expect(screen.queryByTestId("canvas")).toBeNull();
  expect(document.querySelector(".source-import-receipt")).toBeNull();
  expect(sourceReceipt).not.toHaveBeenCalled();
});

const searchHit = {
  evidence_id: "hit-1",
  conversation_id: "conversation-1",
  source_id: "workbuddy",
  workspace: null,
  project: null,
  created_at: "2026-10-04T10:00:00Z",
  excerpt: "红眼航班要靠过道",
  role: "assistant",
  written_by: "agent",
};

it("BUG-02: a search hit listed as a dialogue stays with who wrote it", () => {
  expect(hitToDialogueItem(searchHit)).toMatchObject({
    role: "assistant",
    written_by: "agent",
  });
});

it("BUG-02: a search hit with no time of its own falls back to its import", () => {
  expect(hitToDialogueItem({ ...searchHit, created_at: null })).toMatchObject({
    imported_at: "",
    time_basis: "import",
  });
});
