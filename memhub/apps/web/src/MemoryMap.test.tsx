// @vitest-environment jsdom
import type {
  GovernedMemory,
  MemoryDetail,
  MemoryGraphNode,
  MemoryGraphResponse,
  MemoryProcessingSummary,
  SourceResponse,
} from "@one-memory/contracts";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "./gui-language";

it("traduce los controles del mapa en español sin cambiar etiquetas importadas", () => {
  localStorage.setItem(LANGUAGE_STORAGE_KEY, "es");
  const { container } = render(
    <LanguageProvider>
      <Host simplified />
    </LanguageProvider>,
  );
  expect(screen.getByLabelText("Filtrar memorias del mapa")).toBeTruthy();
  expect(container.textContent).not.toContain("全部记忆");
  expect(container.textContent).not.toContain("当前已加载");
  expect(container.textContent).not.toContain("来源视角");
  expect(container.textContent).not.toContain("段原文");
  localStorage.removeItem(LANGUAGE_STORAGE_KEY);
});

import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { MemoryMap } from "./MemoryMap";
import type { MemoryArrival } from "./memory-arrival-state";

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
  expect(screen.getByRole("heading", { name: "第一条" })).toBeTruthy();
  expect(screen.queryByText(/暂不能保存更改/)).toBeNull();
});

it("OBS-19: Enter in the memory filter submits it; submitting it again re-reads", async () => {
  const onRetry = vi.fn();
  const onQuery = vi.fn();
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
        onQuery,
        onSource: vi.fn(),
        resetPage: vi.fn(),
        onPage: vi.fn(),
        onRetry,
      }}
    />,
  );
  await act(async () => {});
  expect(screen.queryByRole("button", { name: "刷新地图记忆" })).toBeNull();
  // Typing alone does not search; Enter submits the words.
  const input = screen.getByRole("textbox", { name: "筛选地图记忆" });
  await userEvent.type(input, "第一");
  expect(onQuery).not.toHaveBeenCalled();
  await userEvent.type(input, "{Enter}");
  expect(onQuery).toHaveBeenCalledWith("第一");
  // Submitting the currently applied query re-reads without a separate row.
  await userEvent.clear(input);
  await userEvent.type(input, "{Enter}");
  expect(onRetry).toHaveBeenCalledOnce();
});

it("searches only when 搜索 is pressed, not on every keystroke", async () => {
  const onQuery = vi.fn();
  const resetPage = vi.fn();
  render(
    <Host
      simplified
      libraryView={{
        ...pagedView({ items: memories, total: 2, offset: 0, limit: 100 }),
        onQuery,
        resetPage,
      }}
    />,
  );
  expect(screen.getByRole("combobox", { name: "来源" })).toBeTruthy();
  await userEvent.type(
    screen.getByRole("textbox", { name: "筛选地图记忆" }),
    "版本号",
  );
  expect(onQuery).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "搜索" }));
  expect(onQuery).toHaveBeenCalledWith("版本号");
  expect(resetPage).toHaveBeenCalled();
});

it("clearing a submitted keyword immediately shows every memory from the selected source", async () => {
  const onQuery = vi.fn();
  const onSource = vi.fn();
  const resetPage = vi.fn();
  render(
    <Host
      simplified
      libraryView={{
        ...pagedView({ items: [], total: 0, offset: 0, limit: 100 }),
        query: "github",
        source: "workbuddy",
        onQuery,
        onSource,
        resetPage,
      }}
    />,
  );

  const input = screen.getByRole<HTMLInputElement>("textbox", {
    name: "筛选地图记忆",
  });
  expect(input.value).toBe("github");
  await userEvent.clear(input);

  expect(onQuery).toHaveBeenCalledExactlyOnceWith("");
  expect(resetPage).toHaveBeenCalledOnce();
  expect(onSource).not.toHaveBeenCalled();
});

it("OBS-15: switching memory shows a read in progress instead of the previous memory", async () => {
  const user = userEvent.setup();
  // The read is deliberately never answered: what matters is what is on
  // screen while it is open.
  render(<Host simplified deferMemoryId="m1" />);
  const list = screen.getByRole("list", { name: "地图记忆列表" });
  expect(screen.getByRole("heading", { name: "第一条" })).toBeTruthy();
  await user.click(within(list).getByRole("button", { name: /第二条/ }));
  // The first memory's evidence must not still be presented as if it
  // belonged to the selection that is now the second one.
  expect(screen.queryByRole("heading", { name: "第一条" })).toBeNull();
  expect(screen.getByRole("status").textContent).toContain("正在读取这条记忆");
});

it("shows a stable loading surface when the selected memory is read again", async () => {
  const view = render(<Host simplified />);
  await act(async () => {});
  expect(screen.getByRole("heading", { name: "第一条" })).toBeTruthy();
  view.rerender(<Host simplified deferMemoryId="m0" />);
  await userEvent.click(
    within(screen.getByRole("list", { name: "地图记忆列表" })).getByRole(
      "button",
      { name: /第一条/ },
    ),
  );
  expect(screen.queryByRole("heading", { name: "第一条" })).toBeNull();
  expect(screen.getByRole("status").textContent).toContain("正在读取这条记忆");
  expect(document.querySelector(".map-detail-loading")).toBeTruthy();
});

it("shows retry instead of stale content when the selected memory reread fails", async () => {
  const view = render(<Host simplified />);
  await act(async () => {});
  expect(screen.getByRole("heading", { name: "第一条" })).toBeTruthy();
  view.rerender(<Host simplified failMemoryId="m0" />);
  await userEvent.click(
    within(screen.getByRole("list", { name: "地图记忆列表" })).getByRole(
      "button",
      { name: /第一条/ },
    ),
  );
  expect(screen.queryByRole("heading", { name: "第一条" })).toBeNull();
  expect(screen.getByRole("alert").textContent).toContain(
    "这条记忆的原文暂时无法读取",
  );
  expect(screen.getByRole("button", { name: "重试读取原文" })).toBeTruthy();
});

it("keeps the detail header outside the scrolling body and resets detail scroll on selection", async () => {
  const { container } = render(
    <Host simplified sources={[importedSources[0]]} />,
  );
  const heading = screen
    .getByRole("heading", { name: "记忆详情" })
    .closest(".map-inspector-heading");
  const body = container.querySelector<HTMLElement>(".map-inspector-body");
  expect(heading?.nextElementSibling).toBe(body);
  if (!body) throw new Error("Memory detail scroll body is missing");
  body.scrollTop = 90;
  await userEvent.click(
    within(screen.getByRole("list", { name: "地图记忆列表" })).getByRole(
      "button",
      { name: /第一条/ },
    ),
  );
  expect(body.scrollTop).toBe(0);
  body.scrollTop = 180;
  await userEvent.click(
    within(screen.getByRole("list", { name: "地图记忆列表" })).getByRole(
      "button",
      { name: /第二条/ },
    ),
  );
  expect(body.scrollTop).toBe(0);
  body.scrollTop = 120;
  await userEvent.click(screen.getByRole("button", { name: "图中选择 Codex" }));
  expect(body.scrollTop).toBe(0);
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

it("renders SDK-projected memory tags with the existing Workbench chips", () => {
  const { container } = render(<Host simplified />);
  expect(
    Array.from(
      container.querySelectorAll(".map-memory-tags .wb-tag"),
      (node) => node.textContent,
    ),
  ).toEqual(["产品", "产品"]);
});

it("opens the newest listed memory in the detail pane and keeps it closed after dismiss", async () => {
  const user = userEvent.setup();
  render(<Host simplified />);
  const list = screen.getByRole("list", { name: "地图记忆列表" });
  const first = within(list).getByRole("button", { name: /第一条/ });
  expect(first.getAttribute("aria-pressed")).toBe("true");
  expect(screen.getByRole("heading", { name: "第一条" })).toBeTruthy();
  expect(screen.queryByText(/原对话/)).toBeNull();
  await user.click(screen.getByRole("button", { name: "关闭记忆详情" }));
  expect(screen.queryByRole("heading", { name: "第一条" })).toBeNull();
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
  failMemoryId,
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
  failMemoryId?: string;
  onViewHistory?(): void;
  onConfigureModel?(): void;
  libraryView?: Parameters<typeof MemoryMap>[0]["libraryView"];
} = {}) {
  const [detail, setDetail] = useState<MemoryDetail | null>(null);
  const [detailLoad, setDetailLoad] = useState<{
    memoryId: string;
    status: "loading" | "failed";
  } | null>(null);
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
      detailLoad={detailLoad}
      draft={detail?.memory.content ?? ""}
      busy={false}
      answer={null}
      streaming={null}
      requestIds={{ current: new Map() }}
      onOpenMemory={(id) => {
        if (failMemoryId && id === failMemoryId) {
          setDetail(null);
          setDetailLoad({ memoryId: id, status: "failed" });
          return;
        }
        // Lets a test leave one read unanswered.
        if (deferMemoryId && id === deferMemoryId) {
          setDetail(null);
          setDetailLoad({ memoryId: id, status: "loading" });
          return;
        }
        setDetailLoad(null);
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
      onCloseDetail={() => {
        setDetail(null);
        setDetailLoad(null);
      }}
      onAsk={() => {}}
      onDraft={() => {}}
      onSave={() => {}}
      onDelete={() => {}}
      onSaveAnswer={() => {}}
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
});

it("D96: lists a memory by when its conversation happened, not when it was generated", () => {
  const today = new Date().toISOString();
  const thisYear = new Date().getFullYear();
  const items = ["第一条", "第二条", "第三条", "第四条"].map((content, i) => ({
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
        source_ids_by_memory: { t0: [], t1: [], t2: [], t3: [] },
        source_time_by_memory: {
          t0: { at: `${thisYear}-10-02T10:54:00`, basis: "conversation" },
          t1: { at: `${thisYear}-09-30T08:00:00`, basis: "import" },
          t3: { at: `${thisYear}-09-29T08:00:00`, basis: "write" },
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
  // An experience stored without a conversation time names its write time.
  expect(
    within(list).getByRole("button", { name: /第四条/ }).textContent,
  ).toContain("写入于 9月29日");
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
  expect(screen.getAllByText(/共 519 条/)).toHaveLength(1);
  expect(
    screen
      .getByRole("navigation", { name: "地图记忆分页" })
      .classList.contains("map-pagination"),
  ).toBe(true);
});

it("keeps pagination and current detail stable while the next page is loading", async () => {
  const onPage = vi.fn();
  const page = {
    items: memories,
    total: 202,
    offset: 0,
    limit: 100,
    source_ids_by_memory: { m0: ["codex"], m1: ["codex"] },
  };
  const libraryView = { ...pagedView(page), onPage };
  const view = render(<Host simplified libraryView={libraryView} />);
  const list = screen.getByRole("list", { name: "地图记忆列表" });
  const statusSlot = document.querySelector(".map-list-status");
  expect(statusSlot?.getAttribute("data-visible")).toBe("false");
  list.scrollTop = 160;
  const pager = screen.getByRole("navigation", { name: "地图记忆分页" });
  await userEvent.click(
    within(pager).getByRole("button", { name: "下一页记忆" }),
  );
  expect(onPage).toHaveBeenCalledWith(100);
  view.rerender(
    <Host simplified libraryView={{ ...libraryView, loading: true }} />,
  );
  expect(
    screen
      .getByRole("navigation", { name: "地图记忆分页" })
      .getAttribute("aria-busy"),
  ).toBe("true");
  expect(document.querySelector(".map-list-status")).toBe(statusSlot);
  expect(statusSlot?.getAttribute("data-visible")).toBe("true");
  expect(
    (
      screen.getByRole("button", {
        name: "下一页记忆",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  expect(screen.getByRole("heading", { name: "第一条" })).toBeTruthy();
  expect(list.scrollTop).toBe(160);

  const nextItems = memories.map((memory, index) => ({
    ...memory,
    memory_id: `next-${index}`,
    content: `下一页第 ${index + 1} 条`,
  }));
  view.rerender(
    <Host
      simplified
      items={nextItems}
      libraryView={{
        ...libraryView,
        page: {
          ...page,
          items: nextItems,
          offset: 100,
          source_ids_by_memory: {
            "next-0": ["codex"],
            "next-1": ["codex"],
          },
        },
        loading: false,
      }}
    />,
  );
  expect(list.scrollTop).toBe(0);
  expect(document.querySelector(".map-list-status")).toBe(statusSlot);
  expect(statusSlot?.getAttribute("data-visible")).toBe("false");
  expect(within(list).getByText("下一页第 1 条")).toBeTruthy();
  expect(
    await screen.findByRole("heading", { name: "下一页第 1 条" }),
  ).toBeTruthy();
  expect(
    within(list)
      .getByRole("button", { name: /下一页第 1 条/ })
      .getAttribute("aria-pressed"),
  ).toBe("true");
  expect(screen.queryByRole("heading", { name: "第一条" })).toBeNull();
});

it("keeps the page intent through a failed read and selects the first row after retry", async () => {
  const onPage = vi.fn();
  const onRetry = vi.fn();
  const page = {
    items: memories,
    total: 202,
    offset: 0,
    limit: 100,
    source_ids_by_memory: { m0: ["codex"], m1: ["codex"] },
  };
  const base = { ...pagedView(page), onPage, onRetry };
  const view = render(<Host simplified libraryView={base} />);
  await act(async () => {});
  await userEvent.click(screen.getByRole("button", { name: "下一页记忆" }));
  expect(
    within(screen.getByRole("list", { name: "地图记忆列表" }))
      .getByRole("button", { name: /第一条/ })
      .hasAttribute("disabled"),
  ).toBe(true);

  view.rerender(
    <Host
      simplified
      libraryView={{
        ...base,
        loading: false,
        failed: true,
        requestedOffset: 100,
      }}
    />,
  );
  expect(screen.getByRole("heading", { name: "第一条" })).toBeTruthy();
  await userEvent.click(
    screen.getByRole("button", { name: "重新读取地图记忆" }),
  );
  expect(onRetry).toHaveBeenCalledOnce();
  expect(
    within(screen.getByRole("list", { name: "地图记忆列表" }))
      .getByRole("button", { name: /第一条/ })
      .hasAttribute("disabled"),
  ).toBe(true);
  expect(
    (
      screen.getByRole("button", {
        name: "搜索",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);

  const nextItems = memories.map((memory, index) => ({
    ...memory,
    memory_id: `retry-${index}`,
    content: `重试页第 ${index + 1} 条`,
  }));
  view.rerender(
    <Host
      simplified
      items={nextItems}
      libraryView={{
        ...base,
        requestedOffset: 100,
        page: {
          ...page,
          items: nextItems,
          offset: 100,
          source_ids_by_memory: {
            "retry-0": ["codex"],
            "retry-1": ["codex"],
          },
        },
        loading: false,
        failed: false,
      }}
    />,
  );
  expect(
    await screen.findByRole("heading", { name: "重试页第 1 条" }),
  ).toBeTruthy();
});

it("returns to the shown page when a failed page request is cancelled by a row click", async () => {
  const onPage = vi.fn();
  const page = {
    items: memories,
    total: 202,
    offset: 0,
    limit: 100,
    source_ids_by_memory: { m0: ["codex"], m1: ["codex"] },
  };
  const base = { ...pagedView(page), onPage };
  const view = render(<Host simplified libraryView={base} />);
  await act(async () => {});
  await userEvent.click(screen.getByRole("button", { name: "下一页记忆" }));
  view.rerender(
    <Host
      simplified
      libraryView={{
        ...base,
        failed: true,
        requestedOffset: 100,
      }}
    />,
  );
  await userEvent.click(
    within(screen.getByRole("list", { name: "地图记忆列表" })).getByRole(
      "button",
      { name: /第二条/ },
    ),
  );
  expect(onPage.mock.calls).toEqual([[100], [0]]);
  expect(await screen.findByRole("heading", { name: "第二条" })).toBeTruthy();
});

it("names the SDK's no-source bucket 未识别来源, apart from a source under review", () => {
  const items = ["有来源", "无来源"].map((content, i) => ({
    ...memories[0],
    memory_id: `s${i}`,
    content,
  })) as GovernedMemory[];
  render(
    <Host
      simplified
      items={items}
      sources={[
        {
          source_id: "codex",
          display_name: "Codex",
          authorized: true,
          imported_messages: 1,
          imported_conversations: 1,
        } as SourceResponse,
      ]}
      libraryView={{
        ...pagedView({
          items,
          total: 2,
          offset: 0,
          limit: 100,
          source_ids_by_memory: { s0: ["codex"], s1: ["default"] },
          search_window: 500,
        }),
        query: "版本",
        unattributedSource: "default",
      }}
    />,
  );
  const list = screen.getByRole("list", { name: "地图记忆列表" });
  expect(
    within(list).getByRole("button", { name: /有来源/ }).textContent,
  ).toContain("Codex");
  const unattributed = within(list).getByRole("button", { name: /无来源/ });
  expect(unattributed.textContent).toContain("未识别来源");
  expect(unattributed.textContent).not.toContain("待核对");
  expect(screen.getByText("只在最相关的 500 条记忆里查找关键词")).toBeTruthy();
});

it("badges each listed memory with its scene and pages 20 at a time", () => {
  const items = ["对话一条", "工作经验", "编码事实"].map((content, i) => ({
    ...memories[0],
    memory_id: `x${i}`,
    content,
  })) as GovernedMemory[];
  const view = pagedView({
    items,
    total: 45,
    offset: 20,
    limit: 20,
    sdk_memory_refs: {
      x0: { scene: "chat", kind: "chat", variant: null },
      x1: { scene: "working", kind: "experience", variant: "working" },
      x2: { scene: "coding", kind: "fact", variant: "coding" },
    },
  });
  render(<Host simplified items={items} libraryView={view} />);
  const list = screen.getByRole("list", { name: "地图记忆列表" });
  expect(
    within(list).getByRole("button", { name: /对话一条/ }).textContent,
  ).toContain("对话");
  expect(
    within(list).getByRole("button", { name: /工作经验/ }).textContent,
  ).toContain("工作");
  expect(
    within(list).getByRole("button", { name: /编码事实/ }).textContent,
  ).toContain("编码");
  expect(screen.getByText(/第 2 \/ 3 页/)).toBeTruthy();
  // A short page (a row deleted since the search) still offers the next page.
  const next = screen.getByRole("button", { name: "下一页记忆" });
  expect((next as HTMLButtonElement).disabled).toBe(false);
  fireEvent.click(next);
  expect(view.onPage).toHaveBeenCalledWith(40);
  const previous = screen.getByRole("button", { name: "上一页记忆" });
  expect((previous as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(previous);
  expect(view.onPage).not.toHaveBeenCalledWith(0);
});

it("says a filtered source is empty rather than that nothing matched", () => {
  render(
    <Host
      simplified
      items={[]}
      libraryView={{
        ...pagedView({ items: [], total: 0, offset: 0, limit: 100 }),
        source: "default",
        unattributedSource: "default",
      }}
    />,
  );
  expect(screen.getByText("这个来源下还没有记忆")).toBeTruthy();
  expect(screen.queryByText("没有匹配的记忆")).toBeNull();
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
it("keeps graph and list selections linked", async () => {
  render(<Host />);
  await userEvent.click(
    within(screen.getByRole("list", { name: "地图记忆列表" })).getByRole(
      "button",
      { name: /第一条/ },
    ),
  );
  expect(screen.getByTestId("canvas").dataset.selected).toBe("memory:m0");
  await userEvent.click(
    screen.getByRole("button", { name: "图中选择 第二条" }),
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
  expect(screen.getByRole("heading", { name: "记忆详情" })).toBeTruthy();
  expect(screen.getByText("还没有已生成的记忆")).toBeTruthy();
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
  expect(screen.getByText("还没有已生成的记忆")).toBeTruthy();
  await userEvent.clear(search);
  await userEvent.type(search, "Cursor");
  expect(screen.queryByTestId("canvas")).toBeNull();
  expect(screen.queryByText("正在读取记忆详情…")).toBeNull();
  await userEvent.clear(search);
  expect(screen.getByTestId("canvas").dataset.selected ?? "").toBe("");
  expect(screen.queryByText("正在读取记忆详情…")).toBeNull();
});

it("selecting a source node waits for its settled page, then opens the first memory", async () => {
  const onQuery = vi.fn();
  const onSource = vi.fn();
  const resetPage = vi.fn();
  const oldPage = {
    items: memories,
    total: 2,
    offset: 0,
    limit: 20,
    source_ids_by_memory: { m0: ["codex"], m1: ["codex"] },
  };
  const view = render(
    <Host
      simplified
      sources={importedSources}
      libraryView={{
        ...pagedView(oldPage),
        query: "旧查询",
        settledQuery: "旧查询",
        onQuery,
        onSource,
        resetPage,
      }}
    />,
  );
  await act(async () => {});
  await userEvent.click(screen.getByRole("button", { name: "图中选择 Codex" }));
  expect(onQuery).toHaveBeenCalledWith("");
  expect(onSource).toHaveBeenCalledWith("codex");
  expect(resetPage).toHaveBeenCalledOnce();

  view.rerender(
    <Host
      simplified
      sources={importedSources}
      libraryView={{
        ...pagedView(oldPage),
        source: "codex",
        settledSource: "",
        loading: true,
        onQuery,
        onSource,
        resetPage,
      }}
    />,
  );
  expect(
    screen
      .getAllByRole("status")
      .some((node) => node.textContent === "正在读取记忆详情…"),
  ).toBe(true);

  const settledItems = [memories[1], memories[0]];
  view.rerender(
    <Host
      simplified
      items={settledItems}
      sources={importedSources}
      libraryView={{
        ...pagedView({
          ...oldPage,
          items: settledItems,
          source_ids_by_memory: { m1: ["codex"], m0: ["codex"] },
        }),
        source: "codex",
        settledSource: "codex",
        settledQuery: "",
        onQuery,
        onSource,
        resetPage,
      }}
    />,
  );
  expect(await screen.findByRole("heading", { name: "第二条" })).toBeTruthy();
  expect(screen.queryByText("查看已读内容")).toBeNull();
  expect(document.querySelector(".source-import-receipt")).toBeNull();
});

it("does not replace a newer memory choice when a source page settles", async () => {
  const page = {
    items: memories,
    total: 2,
    offset: 0,
    limit: 20,
    source_ids_by_memory: { m0: ["codex"], m1: ["codex"] },
  };
  const view = render(
    <Host simplified sources={importedSources} libraryView={pagedView(page)} />,
  );
  await act(async () => {});
  await userEvent.click(screen.getByRole("button", { name: "图中选择 Codex" }));
  await userEvent.click(
    screen.getByRole("button", { name: "图中选择 第二条" }),
  );
  expect(await screen.findByRole("heading", { name: "第二条" })).toBeTruthy();

  view.rerender(
    <Host
      simplified
      sources={importedSources}
      libraryView={{
        ...pagedView(page),
        source: "codex",
        settledSource: "codex",
      }}
    />,
  );
  expect(screen.getByRole("heading", { name: "第二条" })).toBeTruthy();
});

it("closing the compact source inspector cancels its pending auto-open", async () => {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query.includes("max-width"),
    addEventListener() {},
    removeEventListener() {},
  }));
  const page = {
    items: memories,
    total: 2,
    offset: 0,
    limit: 20,
    source_ids_by_memory: { m0: ["codex"], m1: ["codex"] },
  };
  const view = render(
    <Host simplified sources={importedSources} libraryView={pagedView(page)} />,
  );
  await act(async () => {});
  await userEvent.click(screen.getByRole("button", { name: "图中选择 Codex" }));
  await userEvent.click(screen.getByRole("button", { name: "关闭记忆详情" }));

  view.rerender(
    <Host
      simplified
      sources={importedSources}
      libraryView={{
        ...pagedView(page),
        source: "codex",
        settledSource: "codex",
      }}
    />,
  );
  expect(screen.queryByRole("dialog", { name: "记忆与原文" })).toBeNull();
  expect(screen.queryByRole("heading", { name: "第一条" })).toBeNull();
});

it("keeps the source selection and offers retry when its page fails", async () => {
  const onRetry = vi.fn();
  const page = {
    items: memories,
    total: 2,
    offset: 0,
    limit: 20,
    source_ids_by_memory: { m0: ["codex"], m1: ["codex"] },
  };
  const view = render(
    <Host simplified sources={importedSources} libraryView={pagedView(page)} />,
  );
  await act(async () => {});
  await userEvent.click(screen.getByRole("button", { name: "图中选择 Codex" }));
  view.rerender(
    <Host
      simplified
      sources={importedSources}
      libraryView={{
        ...pagedView(page),
        source: "codex",
        settledSource: "codex",
        failed: true,
        onRetry,
      }}
    />,
  );
  expect(
    screen
      .getAllByRole("alert")
      .some((node) => node.textContent === "记忆暂时无法读取"),
  ).toBe(true);
  await userEvent.click(screen.getByRole("button", { name: "重试" }));
  expect(onRetry).toHaveBeenCalledOnce();
});

it("drops a pending source selection when that source is revoked", async () => {
  const page = {
    items: memories,
    total: 2,
    offset: 0,
    limit: 20,
    source_ids_by_memory: { m0: ["codex"], m1: ["codex"] },
  };
  const view = render(
    <Host
      simplified
      sources={[importedSources[0]]}
      libraryView={pagedView(page)}
    />,
  );
  await act(async () => {});
  await userEvent.click(screen.getByRole("button", { name: "图中选择 Codex" }));
  expect(screen.getByTestId("canvas").dataset.selected).toBe("source:codex");

  view.rerender(
    <Host
      simplified
      sources={[{ ...importedSources[0], authorized: false }]}
      libraryView={{
        ...pagedView(page),
        source: "codex",
        settledSource: "codex",
      }}
    />,
  );
  expect(screen.queryByRole("button", { name: "图中选择 Codex" })).toBeNull();
  expect(screen.getByTestId("canvas").dataset.selected).not.toBe(
    "source:codex",
  );
  expect(screen.queryByText("正在读取记忆详情…")).toBeNull();

  view.rerender(
    <Host
      simplified
      sources={[importedSources[0]]}
      libraryView={{
        ...pagedView(page),
        source: "codex",
        settledSource: "codex",
      }}
    />,
  );
  expect(screen.getByTestId("canvas").dataset.selected).not.toBe(
    "source:codex",
  );
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
});
