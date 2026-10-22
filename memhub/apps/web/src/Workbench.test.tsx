// @vitest-environment jsdom

import type {
  AskMemoryResponse,
  GovernedMemory,
  MemoryDetail,
  MemoryGraphNode,
  MemoryGraphResponse,
  MemoryProcessingSummary,
  SourceResponse,
} from "@one-memory/contracts";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Workbench } from "./Workbench";

const getConversation = vi.fn();

vi.mock("./api", () => ({
  getConversation: (...args: unknown[]) => getConversation(...args),
}));

// The graph draws with WebGL, which jsdom has none of. The test doubles it
// with something that can be looked for, because what matters here is whether
// a graph is on screen at all — the complaint that started this was a page
// with no graph on it.
vi.mock("./MemoryGraph3D", () => ({
  MemoryGraph3D: ({
    graph,
    onSelectNode,
  }: {
    graph: MemoryGraphResponse;
    onSelectNode(node: MemoryGraphNode): void;
  }) => (
    <div data-testid="graph-3d" data-nodes={graph.nodes.length}>
      {graph.nodes.map((node) => (
        // Clickable, so a test can select a node the way a person does and
        // reach the panel that reads out the lines hanging off it.
        <button
          key={node.node_id}
          type="button"
          onClick={() => onSelectNode(node)}
        >
          {node.label}
        </button>
      ))}
    </div>
  ),
}));

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

function source(
  sourceId: string,
  overrides: Partial<SourceResponse> = {},
): SourceResponse {
  return {
    source_id: sourceId,
    display_name: sourceId === "cursor" ? "Cursor" : sourceId,
    storage_kinds: ["jsonl"],
    available: true,
    authorized: true,
    detected: true,
    root_label: null,
    candidate_path: `~/.${sourceId}`,
    candidate_kind: "default",
    imported_messages: 40,
    imported_conversations: 12,
    capabilities: {
      historical_import: "implemented",
      incremental_import: "implemented",
      live_sync: "not_implemented",
      recall: "not_implemented",
      installer: "not_implemented",
      verification: "real_replayed",
    },
    sync_completeness: "additions_only",
    ...overrides,
  } as SourceResponse;
}

const processing = {
  pending: 0,
  running: 0,
  fresh_pending: 0,
  backlog_pending: 0,
  recent_completions: [],
  waiting_for_model: 0,
  completed: 0,
  failed: 0,
  blocked_stages: [],
  outside_window_pending: 0,
  extraction_window_preset: "3m",
  manual_extract_batches_planned: 0,
  manual_extract_batches_current: 0,
  active_queue: 0,
  in_flight_count: 0,
  seconds_per_completion: null,
} as MemoryProcessingSummary;

const detail: MemoryDetail = {
  memory: memory("memory-1", { content: "统一用 axios 封装" }),
  evidence: [
    {
      evidence_id: "ev-old",
      available: true,
      source_id: "cursor",
      role: "user",
      written_by: "user",
      created_at: "2026-07-01T10:00:00Z",
      verbatim: "七月说的那句",
      extraction_state: "extracted",
    },
    {
      evidence_id: "ev-new",
      available: true,
      source_id: "claude-code",
      role: "user",
      written_by: "user",
      created_at: "2026-08-20T10:00:00Z",
      verbatim: "八月说的那句",
      extraction_state: "extracted",
    },
  ],
  history: [
    {
      memory_id: "memory-1",
      revision: 1,
      action: "create",
      status: "confirmed",
      content: "统一用 axios 封装",
      scopes: ["all-apps"],
      created_at: "2026-09-01T00:00:00Z",
    },
  ],
  index_sync: null,
} as MemoryDetail;

const EMPTY_GRAPH = {
  total_memories: 0,
  projected_memories: 0,
  hy_graph_available: false,
  nodes: [],
  edges: [],
} as MemoryGraphResponse;

function mount(props: Partial<Parameters<typeof Workbench>[0]> = {}) {
  const handlers = {
    onDraft: vi.fn(),
    onOpenMemory: vi.fn(),
    onCloseDetail: vi.fn(),
    onSave: vi.fn(),
    onAction: vi.fn(),
    onSyncIndex: vi.fn(),
    onAsk: vi.fn(),
    onBuildRelations: vi.fn(),
    onConnect: vi.fn(),
    onConfigureModel: vi.fn(),
    onSaveAnswer: vi.fn(),
  };
  const view = render(
    <Workbench
      memories={[memory("memory-1", { content: "统一用 axios 封装" })]}
      graph={{
        total_memories: 1,
        projected_memories: 1,
        hy_graph_available: false,
        nodes: [
          {
            node_id: "memory:memory-1",
            node_type: "memory",
            label: "统一用 axios 封装",
            memory_id: "memory-1",
            source_id: null,
            kind: "chat",
            status: "confirmed",
            confidence: null,
          },
        ],
        edges: [],
      }}
      sources={[source("cursor"), source("claude-code")]}
      processing={processing}
      loaded
      detail={null}
      draft=""
      busy={false}
      answer={null}
      streaming={null}
      requestIds={{ current: new Map() }}
      {...handlers}
      {...props}
    />,
  );
  return { ...handlers, view };
}

it("选中的图节点把记忆库与详情联到一起（图 → 列表 → 原文）", async () => {
  // The goal requires the graph, the memory list and the original text to be
  // linked in both directions. The list→graph direction is covered elsewhere
  // (the graph receives `selectedNodeId`); this covers graph→list→detail, which
  // had no test even though the wiring exists in `Workbench.tsx`.
  const user = userEvent.setup();
  const { onOpenMemory } = mount();
  // The mock renders one clickable button per node, which is how a person
  // selects a node without WebGL. The same label also appears in the memory
  // list, so scope the query to the graph.
  const graph = screen.getByTestId("graph-3d");
  await user.click(
    within(graph).getByRole("button", { name: "统一用 axios 封装" }),
  );
  expect(onOpenMemory).toHaveBeenCalledWith("memory-1");
});

it("展开一段原话后再选图节点，展开态被收起，不把旧原话挂在新节点下", async () => {
  // Graph selection must reset the evidence panel's *expanded* state. Leaving
  // it open would show one memory's raw conversation under a different node's
  // label, reading as if that conversation belonged to the newly selected
  // memory. The verbatim excerpt is always listed, so the assertion is on the
  // expanded body (`data-open`), which is what the reset actually controls.
  const user = userEvent.setup();
  mount({
    detail: {
      memory: memory("memory-1", { content: "统一用 axios 封装" }),
      evidence: [
        {
          evidence_id: "ev-old",
          available: true,
          source_id: "cursor",
          role: "user",
          written_by: "user",
          created_at: "2026-07-01T10:00:00Z",
          verbatim: "上一条记忆的原文",
          extraction_state: "extracted",
        },
      ],
      history: [],
      index_sync: null,
    } as never,
  });
  // Open the evidence card.
  await user.click(screen.getByRole("button", { name: /上一条记忆的原文/ }));
  expect(
    document.querySelector('[data-open="true"]'),
    "the evidence card should start expanded",
  ).not.toBeNull();
  // Selecting a graph node must collapse it.
  const graph = screen.getByTestId("graph-3d");
  await user.click(
    within(graph).getByRole("button", { name: "统一用 axios 封装" }),
  );
  expect(
    document.querySelector('[data-open="true"]'),
    "graph selection must collapse the previously opened evidence",
  ).toBeNull();
});

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  cleanup();
});

describe("三栏都在，各自装该装的东西", () => {
  it("左栏按主题分组，点一个主题就只剩它的记忆", async () => {
    // 原来这条点的是「个人画像」——按 scope 分的组。真实库里 scope 是
    // workspace:v1:<sha256>，那一列全是 hash，所以这条轴换成了主题。
    const user = userEvent.setup();
    mount({
      memories: [
        memory("a", { content: "甲记忆", tags: ["前端", "只此一次"] }),
        memory("b", { content: "乙记忆", tags: ["前端"] }),
        memory("c", { content: "丙记忆", tags: ["排期"] }),
        memory("d", { content: "丁记忆", tags: ["排期"] }),
      ],
    });

    // 默认「全部」，四条都在
    expect(screen.getByRole("button", { name: /甲记忆/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /丙记忆/ })).toBeTruthy();

    await user.click(screen.getByRole("button", { name: /^前端/ }));

    expect(screen.getByRole("button", { name: /甲记忆/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /乙记忆/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /丙记忆/ })).toBeNull();
  });

  it("左栏不出现整串授权 hash", async () => {
    // 正向对照：上一条只证明主题在，不证明 hash 不在。用户看到的正是
    // 「全部」下面九行 v1:0027abad… —— 那才是她说的「完全就不是事项」。
    mount({
      memories: [
        memory("a", {
          scopes: ["workspace:v1:0027abad1745330f1b57671635e0e9b3"],
          tags: ["前端"],
        }),
        memory("b", {
          scopes: ["workspace:v1:daf248ff48ea3e6baf35cc7f1fb46d74"],
          tags: ["前端"],
        }),
      ],
    });

    const column = screen.getByRole("navigation", { name: "主题" });
    expect(column.textContent).toContain("前端");
    expect(column.textContent).not.toContain("v1:");
    expect(column.textContent).not.toContain("0027abad");
  });

  it("右栏按时间倒序，并写出每段原话出自哪个 App", async () => {
    mount({ detail });

    const cards = screen.getAllByText(/月说的那句/);
    expect(cards[0].textContent).toContain("八月");
    expect(cards[1].textContent).toContain("七月");
    expect(screen.getByText("Cursor")).toBeTruthy();
    expect(screen.getByText("claude-code")).toBeTruthy();
  });

  it("点一段原话可以展开看完整对话", async () => {
    getConversation.mockResolvedValue({
      conversation_id: "conv-1",
      source_id: "cursor",
      messages: [
        {
          evidence_id: "ev-new",
          role: "user",
          written_by: "user",
          created_at: "2026-08-20T10:00:00Z",
          verbatim: "八月说的那句",
        },
        {
          evidence_id: "ev-reply",
          role: "assistant",
          written_by: "assistant",
          created_at: "2026-08-20T10:01:00Z",
          verbatim: "助手当时的回答",
        },
      ],
    });
    const user = userEvent.setup();
    mount({ detail });

    await user.click(screen.getByRole("button", { name: /八月说的那句/ }));
    await user.click(screen.getByRole("button", { name: "看完整对话" }));

    expect(await screen.findByText("助手当时的回答")).toBeTruthy();
    expect(getConversation).toHaveBeenCalledWith("ev-new");
  });

  it("没选中记忆时右栏说清楚该怎么让它有内容", () => {
    mount();

    expect(screen.getByText(/点图上任意一个圈/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "看完整对话" })).toBeNull();
  });
});

describe("图永远不空", () => {
  it("图查询成功但返回 0 个节点时，用已经导入过的来源画出骨架", () => {
    // 这才是新装一台机器的真实状态：抽取还没跑，图接口正常返回一个空图。
    // 原来这里喂的是 graph: null（只在请求失败时出现），于是骨架在唯一
    // 需要它的那一屏永远不会出现 —— 真机跑一遍才发现。
    mount({ memories: [], graph: EMPTY_GRAPH });

    expect(screen.getByTestId("graph-3d").getAttribute("data-nodes")).toBe("2");
    expect(screen.getByText("Cursor · 12 段")).toBeTruthy();
  });

  it("图接口彻底失败时同样有骨架，不是一片空白", () => {
    mount({ memories: [], graph: null });

    expect(screen.getByTestId("graph-3d").getAttribute("data-nodes")).toBe("2");
  });

  it("但什么都没导入过时不画假的圈，而是说去授权", async () => {
    // 反向守卫的正面对照就是上一条：同样没有记忆，只是来源没导入过，
    // 骨架就必须消失。两条一起才能证明这里画的是真数据。
    const user = userEvent.setup();
    const { onConnect } = mount({
      memories: [],
      graph: EMPTY_GRAPH,
      sources: [source("cursor", { imported_conversations: 0 })],
    });

    expect(screen.queryByTestId("graph-3d")).toBeNull();
    await user.click(screen.getByRole("button", { name: "去授权一个来源" }));
    expect(onConnect).toHaveBeenCalled();
  });

  it("没授权的来源不算数", () => {
    mount({
      memories: [],
      graph: EMPTY_GRAPH,
      sources: [source("cursor", { authorized: false })],
    });

    expect(screen.queryByTestId("graph-3d")).toBeNull();
  });

  it("还没读完时说正在读，不说你没有记忆", () => {
    mount({ memories: [], graph: EMPTY_GRAPH, sources: [], loaded: false });

    expect(screen.getByText("正在读取记忆…")).toBeTruthy();
    expect(screen.queryByText(/去授权一个来源/)).toBeNull();
  });
});

describe("发现条", () => {
  it("点「在图上看」会打开这条发现依据的记忆", async () => {
    const user = userEvent.setup();
    const { onOpenMemory } = mount({
      memories: [
        memory("a", { content: "统一用 axios 封装", evidence_ids: ["ev-1"] }),
        memory("b", { content: "不用 fetch 裸调", evidence_ids: ["ev-1"] }),
      ],
    });

    expect(screen.getByText(/是从同一句原话里抽出来的/)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "在图上看" }));

    expect(onOpenMemory).toHaveBeenCalledWith("a");
  });

  it("没有任何发现时整条不出现，不留一个空壳", () => {
    mount({ memories: [memory("a")] });

    expect(screen.queryByText("我替你看出这几件事")).toBeNull();
  });
});

describe("记忆可以改也可以删", () => {
  it("选中一条记忆后，改写和删除都在手边", async () => {
    const user = userEvent.setup();
    const { onAction } = mount({
      detail,
      draft: "统一用 axios 封装",
    });

    expect(screen.getByLabelText("记忆内容")).toBeTruthy();
    expect(screen.getByRole("button", { name: "保存纠正" })).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "删除" }));
    expect(onAction).toHaveBeenCalledWith("delete");
  });

  it("改动文字会作废预备好的请求 id，避免用旧 id 提交新内容", async () => {
    const user = userEvent.setup();
    const requestIds = { current: new Map([["memory-1:correct:1", "old"]]) };
    mount({
      detail,
      draft: "统一用 axios 封装",
      requestIds,
    });

    await user.type(screen.getByLabelText("记忆内容"), "改");

    expect(requestIds.current.size).toBe(0);
  });

  it("已删除的记忆不再摆一个删除按钮", () => {
    mount({
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

describe("提问", () => {
  it("问题送出去的是用户打的那句", async () => {
    const user = userEvent.setup();
    const { onAsk } = mount();

    await user.type(
      screen.getByLabelText("就着这个事项提问"),
      "我对真实数据有什么要求",
    );
    await user.click(screen.getByRole("button", { name: "问" }));

    await waitFor(() =>
      expect(onAsk).toHaveBeenCalledWith("我对真实数据有什么要求"),
    );
  });

  it("记忆产出即可用，不给一个「还没确认」的芯片", () => {
    mount({ memories: [memory("a")] });

    expect(screen.queryByRole("button", { name: "有哪些还没确认" })).toBeNull();
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

describe("问出来的东西留得下来", () => {
  it("存下来带着它依据的那几条记忆的出处", async () => {
    const user = userEvent.setup();
    const { onSaveAnswer } = mount({ answer: ANSWER });

    await user.click(screen.getByRole("button", { name: "存为记忆" }));

    await waitFor(() =>
      expect(onSaveAnswer).toHaveBeenCalledWith({
        content: "综合起来，你在前端一律走 axios 封装。",
        evidence_ids: ["ev-a", "ev-b", "ev-c"],
      }),
    );
  });

  it("没命中任何记忆的回答存不下来", () => {
    mount({
      answer: {
        answer: "没有找到相关记忆。",
        memories: [],
        mode: "empty",
      } as AskMemoryResponse,
    });

    expect(screen.queryByRole("button", { name: "存为记忆" })).toBeNull();
  });

  it("还在流式输出的时候不给存，存下来的得是完整那一份", () => {
    mount({
      answer: null,
      streaming: { text: "综合起", memories: [memory("m1")] },
    });

    expect(screen.queryByRole("button", { name: "存为记忆" })).toBeNull();
  });

  it("答案长到不像一条记忆时，指去事项页而不是硬塞进库", () => {
    mount({
      answer: { ...ANSWER, answer: "很".repeat(601) } as AskMemoryResponse,
    });

    expect(screen.queryByRole("button", { name: "存为记忆" })).toBeNull();
    expect(screen.getByText(/太长/)).toBeTruthy();
  });
});

describe("主题标签", () => {
  it("记忆行下面挂着它的标签", () => {
    mount({ memories: [memory("a", { tags: ["排期", "工作习惯"] })] });

    expect(screen.getByText("排期")).toBeTruthy();
    expect(screen.getByText("工作习惯")).toBeTruthy();
  });

  it("只用过一次的标签被标出来，用过两次的不标", () => {
    mount({
      memories: [
        memory("a", { tags: ["排期", "只此一次"] }),
        memory("b", { tags: ["排期"] }),
      ],
    });

    expect(screen.getByText("只此一次").getAttribute("data-once")).toBe("true");
    // 两条记忆都挂着「排期」，所以它作为标签本来就有两个节点 —— 这正是它
    // 不该被标成「只用过一次」的原因。只数标签，不数左栏那一行主题标题：
    // 「排期」现在同时是一行主题，getAllByText 会把它一起数进来。
    const shared = screen
      .getAllByText("排期")
      .filter((node) => node.className === "wb-tag");
    expect(shared).toHaveLength(2);
    for (const node of shared) {
      expect(node.getAttribute("data-once")).toBeNull();
    }
  });

  it("顶上报的是两个数，不是一句警告", () => {
    mount({
      memories: [
        memory("a", { tags: ["排期", "只此一次"] }),
        memory("b", { tags: ["排期"] }),
      ],
    });

    // 说的必须和看到的一致：成形的主题才立行，只用过一次的没立行，
    // 所以这句要把两个数分开说，而不是报一个「2 个主题标签」——
    // 那会让人数着一行却读到 2。
    expect(
      screen.getByText("1 个主题成形，另有 1 个标签只出现过一次"),
    ).toBeTruthy();
  });

  it("一个标签都没有的时候不占地方", () => {
    mount({ memories: [memory("a")] });

    expect(screen.queryByText(/个主题标签/)).toBeNull();
  });
});

describe("事项综述", () => {
  it("问题里带上这个事项的名字", async () => {
    const user = userEvent.setup();
    // 原来这里喂的假 scope 是 workspace:v1:one-memory —— 恰好可读，
    // 于是这条测试永远发现不了真 hash 不可读。改成按主题选。
    const { onAsk } = mount({
      memories: [
        memory("a", { tags: ["one-memory"] }),
        memory("b", { tags: ["one-memory"] }),
      ],
    });
    await user.click(screen.getByRole("button", { name: /^one-memory/ }));

    await user.click(screen.getByRole("button", { name: "读这个事项的综述" }));

    await waitFor(() =>
      expect(onAsk).toHaveBeenCalledWith(expect.stringContaining("one-memory")),
    );
  });

  it("综述不给存成记忆，它按定义是每次重建的", async () => {
    const user = userEvent.setup();
    mount({ answer: ANSWER });

    await user.click(screen.getByRole("button", { name: "读这个事项的综述" }));

    expect(screen.queryByRole("button", { name: "存为记忆" })).toBeNull();
    expect(screen.getByText(/每次都会重新生成/)).toBeTruthy();
  });

  it("换回普通提问之后，存下来又可以了", async () => {
    const user = userEvent.setup();
    mount({ answer: ANSWER, memories: [memory("a")] });
    await user.click(screen.getByRole("button", { name: "读这个事项的综述" }));

    await user.click(
      screen.getByRole("button", { name: "这里最近有什么变化" }),
    );

    expect(screen.getByRole("button", { name: "存为记忆" })).toBeTruthy();
  });

  it("这个事项里一条记忆都没有时，没有综述可读", () => {
    mount({ memories: [] });

    expect(
      screen.queryByRole("button", { name: "读这个事项的综述" }),
    ).toBeNull();
  });
});

describe("按主题聚类", () => {
  afterEach(cleanup);

  it("共用主题的线在图上有自己的说法和自己的计数", () => {
    // 记忆很少的时候，图上唯一挂着多条记忆的东西就只剩来源 App，看起来像是
    // 按 App 聚类 —— 而这恰恰是它最不该表达的东西。主题这条轴补上之前，
    // 我们从 HY 接回来的标签只在左栏当文字，没有进图。
    mount({
      graph: {
        total_memories: 2,
        projected_memories: 2,
        hy_graph_available: false,
        nodes: [
          { node_id: "topic:axios", node_type: "topic", label: "axios" },
          { node_id: "memory:a", node_type: "memory", label: "统一走 axios" },
          { node_id: "memory:b", node_type: "memory", label: "测试先行" },
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

    // 回执按来源分档计数。多出来的这一档如果没被认出来，
    // counts[edge.origin] += 1 会算出 NaN，整行回执静默坏掉。
    const receipt = screen.getByText(/原话来源/).textContent ?? "";
    expect(receipt).not.toContain("NaN");
    expect(receipt).toContain("同一个主题 2 条");
  });

  it("没有共用主题时回执里不出现这一档", () => {
    // 正向对照：没有这条，上一条可以被「无条件把这句话拼进回执」骗过。
    // 喂的是一张有边但没有主题边的图 —— 空图走的是骨架分支，压根没有回执行，
    // 那样断言「不含」会因为整行不存在而恒真，又是一条假守卫。
    mount({
      graph: {
        total_memories: 1,
        projected_memories: 1,
        hy_graph_available: false,
        nodes: [
          { node_id: "source:cursor", node_type: "source", label: "Cursor" },
          { node_id: "memory:a", node_type: "memory", label: "统一走 axios" },
        ],
        edges: [
          {
            edge_id: "e1",
            source_node_id: "source:cursor",
            target_node_id: "memory:a",
            relation: "evidence_source",
            origin: "provenance",
          },
        ],
      } as MemoryGraphResponse,
    });

    const receipt = screen.getByText(/原话来源/).textContent ?? "";
    expect(receipt).not.toContain("同一个主题");
  });

  it("点开一个主题，它挂着的记忆和这条线的来头都说得出来", async () => {
    // 走到详情面板才碰得到 edgeOriginLabel。它是一条 if 链、以「原话来源」
    // 兜底，所以漏掉一种来源不会报错，只会把主题的线说成是原话来源。
    const user = userEvent.setup();
    mount({
      graph: {
        total_memories: 2,
        projected_memories: 2,
        hy_graph_available: false,
        nodes: [
          { node_id: "topic:axios", node_type: "topic", label: "axios" },
          { node_id: "memory:a", node_type: "memory", label: "统一走 axios" },
          { node_id: "memory:b", node_type: "memory", label: "测试先行" },
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

    await user.click(screen.getByRole("button", { name: "axios" }));

    const panel = screen.getByLabelText("这个节点");
    expect(panel.textContent).toContain("主题");
    expect(panel.textContent).toContain("统一走 axios");
    expect(panel.textContent).toContain("同一个主题");
    expect(panel.textContent).not.toContain("原话来源");
  });
});
