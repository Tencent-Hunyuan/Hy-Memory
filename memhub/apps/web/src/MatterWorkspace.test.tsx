// @vitest-environment jsdom
import type {
  Matter,
  MatterContext,
  MemoryDetail,
} from "@one-memory/contracts";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentProps } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MatterWorkspace } from "./MatterWorkspace";
import type { MemoryGraph3D } from "./MemoryGraph3D";
import { setUiLanguage } from "./ui-language";

const api = vi.hoisted(() => ({
  getMatters: vi.fn(),
  getMatterRuns: vi.fn(),
  patchMatter: vi.fn(),
  getMatterEvidence: vi.fn(),
  getMatterContext: vi.fn(),
  getMatterHistory: vi.fn(),
  addMatterNote: vi.fn(),
  discoverMatters: vi.fn(),
  refreshMatter: vi.fn(),
  getMemories: vi.fn(),
  getMemoryGraph: vi.fn(),
  getMemory: vi.fn(),
}));
vi.mock("./api", () => api);
vi.mock("./MemoryGraph3D", () => ({
  MemoryGraph3D: ({
    graph,
    selectedNodeId,
    onSelectNode,
  }: ComponentProps<typeof MemoryGraph3D>) => (
    <div data-testid="matter-map" data-selected={selectedNodeId}>
      {graph.nodes.map((node) => (
        <button
          type="button"
          key={node.node_id}
          onClick={() => onSelectNode(node)}
        >
          {`图节点 ${node.label}`}
        </button>
      ))}
    </div>
  ),
}));

const matter: Matter = {
  source_engine: "legacy_direct",
  content_review: "needs_review",
  matter_id: "m1",
  title: "Atlas 产品开发",
  purpose: "发布可用产品",
  boundary: "产品设计和开发",
  anchors: ["e1"],
  tags: ["产品"],
  uncertainties: ["Windows 尚未验收"],
  version: 1,
  scope_confirmed: false,
  agent_access: false,
  availability: "ready",
  model: "test-model",
  updated_at: "2026-09-11T00:00:00Z",
  source_count: 1,
  evidence_count: 1,
  claims: [
    {
      statement: "发布目标为 9 月 23 日",
      memory_refs: [],
      state: "current",
      change_note: "",
      kind: "decision",
      scope: "开源发布",
      basis: "human_statement",
      verification: "model_reviewed",
      citations: [
        {
          evidence_id: "e1",
          quote: "计划 9 月 23 日开源",
          revision: 1,
          source_id: "cursor",
          written_by: "person",
          created_at: null,
        },
      ],
    },
  ],
};

beforeEach(() => {
  vi.resetAllMocks();
  api.getMatters.mockResolvedValue([matter]);
  api.getMatterRuns.mockResolvedValue([]);
  api.getMemories.mockResolvedValue([]);
  api.getMemoryGraph.mockResolvedValue(null);
  api.getMatterHistory.mockResolvedValue([matter]);
  api.getMatterEvidence.mockResolvedValue({
    evidence_id: "e1",
    source_id: "cursor",
    written_by: "person",
    role: "user",
    created_at: null,
    observed_at: "2026-09-11T00:00:00Z",
    text: "我们计划 9 月 23 日开源，并做好验收。",
  });
  api.patchMatter.mockResolvedValue({
    ...matter,
    version: 2,
    scope_confirmed: true,
  });
});
afterEach(cleanup);

it("switches matter navigation and search while preserving matter-authored text", async () => {
  const view = render(<MatterWorkspace onOpenSources={() => {}} />);
  await screen.findByRole("heading", { name: matter.title });
  try {
    await act(async () => {
      await setUiLanguage("en");
    });
    expect(screen.getByRole("heading", { name: "Workspace" })).toBeTruthy();
    expect(
      screen.getByRole("button", { name: /Sources and connections/ }),
    ).toBeTruthy();
    expect(
      screen.getByRole("textbox", { name: "Search matters" }),
    ).toBeTruthy();
    expect(screen.getByRole("heading", { name: matter.title })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Knowledge profile" })).toBeTruthy();
    expect(
      screen.getByText("Are you still working on this matter?"),
    ).toBeTruthy();
    expect(screen.getByText(matter.purpose)).toBeTruthy();
    await userEvent.click(
      screen.getByRole("tab", { name: "Knowledge profile" }),
    );
    expect(screen.getByText("Decisions")).toBeTruthy();
    expect(screen.getByText(matter.claims[0].statement)).toBeTruthy();
    expect(screen.getByText(matter.uncertainties[0])).toBeTruthy();
    expect(api.discoverMatters).not.toHaveBeenCalled();
  } finally {
    view.unmount();
    await act(async () => {
      await setUiLanguage("zh-CN");
    });
  }
});

it("separates write verification from provider failures without automatic retries", async () => {
  api.getMatters.mockResolvedValue([]);
  api.getMatterRuns.mockResolvedValue([
    {
      run_id: "failed-run",
      status: "failed",
      phase: "整理未完成",
      error: "hy_memory_write_verification_failed",
      observations: [],
      selected_records: 3,
      hy_input_records: 0,
      omitted_records: 0,
      coverage_complete: true,
    },
  ]);
  render(<MatterWorkspace onOpenSources={() => {}} />);
  expect(
    await screen.findByText(
      /记忆内容未通过来源与作者核验，当前批次未完成，原话已保留。/,
    ),
  ).toBeTruthy();
  expect(api.discoverMatters).not.toHaveBeenCalled();
});

it("does not turn a failed matters read into zero matters", async () => {
  api.getMatters.mockRejectedValue(new Error("offline"));
  api.getMatterRuns.mockRejectedValue(new Error("offline"));
  render(<MatterWorkspace onOpenSources={() => {}} />);
  const list = await screen.findByRole("complementary", { name: "事项列表" });
  expect(list.textContent).toContain("待核对");
  expect(list.textContent).not.toContain("0");
  expect(screen.queryByRole("heading", { name: "尚未整理事项" })).toBeNull();
  expect((await screen.findByRole("alert")).textContent).toContain(
    "未能读取事项档案",
  );
});

it("does not turn a failed matters read plus a query into no matches", async () => {
  const user = userEvent.setup();
  api.getMatters.mockRejectedValue(new Error("offline"));
  api.getMatterRuns.mockRejectedValue(new Error("offline"));
  render(<MatterWorkspace onOpenSources={() => {}} />);
  await screen.findByRole("alert");
  await user.type(
    screen.getByRole("textbox", { name: "搜索事项" }),
    "不存在的",
  );
  expect(screen.queryByText("没有匹配的事项")).toBeNull();
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((complete) => {
    resolve = complete;
  });
  return { promise, resolve };
}

const context: MatterContext = {
  matter_id: matter.matter_id,
  version: matter.version,
  availability: "ready",
  instruction_policy: "reference_only",
  truncated: false,
  text: "Only the original authorized matter can show this context.",
};

it("opens source evidence and preserves unknown message time", async () => {
  render(<MatterWorkspace onOpenSources={() => {}} />);
  await screen.findByRole("heading", { name: "Atlas 产品开发" });
  await userEvent.click(screen.getByRole("tab", { name: "知识档案" }));
  await userEvent.click(screen.getByRole("button", { name: /查看原话/ }));
  expect(await screen.findByText("消息时间未知")).toBeTruthy();
  expect(document.querySelector(".evidence-verbatim")?.textContent).toBe(
    "我们计划 9 月 23 日开源，并做好验收。",
  );
  expect(document.querySelector(".evidence-verbatim mark")?.textContent).toBe(
    "计划 9 月 23 日开源",
  );
  expect(api.getMatterEvidence).toHaveBeenCalledWith("m1", "e1");
});

it("links real governed memories, map nodes and raw evidence without losing the graph", async () => {
  const item = {
    memory_id: "base-1",
    revision: 1,
    status: "confirmed",
    kind: "working",
    content: "项目周会安排在周三",
    evidence_ids: ["e1"],
    tags: [],
    scopes: ["all-apps"],
  };
  api.getMatters.mockResolvedValue([
    {
      ...matter,
      source_engine: "hy-memory",
      claims: [
        {
          ...matter.claims[0],
          memory_refs: [{ memory_id: "base-1", revision: 1 }],
        },
      ],
    },
  ]);
  api.getMemories.mockResolvedValue([item]);
  api.getMemory.mockResolvedValue({
    memory: item,
    evidence: [
      {
        evidence_id: "e1",
        available: true,
        verbatim: "周会改到周三。",
        written_by: "person",
        source_id: "codex",
        created_at: null,
      },
    ],
    history: [],
  });
  render(<MatterWorkspace onOpenSources={() => {}} />);
  const node = await screen.findByRole("button", {
    name: "图节点 项目周会安排在周三",
  });
  await userEvent.click(node);
  expect(await screen.findByText("周会改到周三。")).toBeTruthy();
  const canvas = screen.getByTestId("matter-map");
  expect(canvas.dataset.selected).toBe("memory:base-1");
  await userEvent.click(screen.getByRole("tab", { name: "知识档案" }));
  await userEvent.click(screen.getByRole("tab", { name: "记忆地图" }));
  expect(screen.getByTestId("matter-map")).toBe(canvas);
  expect(canvas.dataset.selected).toBe("memory:base-1");
  await userEvent.click(
    screen.getByRole("button", { name: "选择记忆 项目周会安排在周三" }),
  );
  expect(api.getMemory).toHaveBeenCalledWith("base-1");
});

it("confirms scope using optimistic version without confirming claims", async () => {
  render(<MatterWorkspace onOpenSources={() => {}} />);
  await userEvent.click(
    await screen.findByRole("button", { name: "确认事项范围" }),
  );
  await waitFor(() =>
    expect(api.patchMatter).toHaveBeenCalledWith(
      "m1",
      {
        expected_version: 1,
        scope_confirmed: true,
      },
      expect.any(String),
    ),
  );
});

it("shows a failed read rather than a misleading empty library", async () => {
  api.getMatters.mockRejectedValue(new Error("offline"));
  render(<MatterWorkspace onOpenSources={() => {}} />);
  expect(await screen.findByRole("alert")).toBeTruthy();
  expect(screen.queryByText("尚未整理事项")).toBeNull();
});

it("blocks stale context after new evidence", async () => {
  api.getMatters.mockResolvedValue([
    { ...matter, availability: "needs_update" },
  ]);
  render(<MatterWorkspace onOpenSources={() => {}} />);
  const button = await screen.findByRole("button", { name: "任务上下文" });
  expect((button as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText(/新资料待整理/)).toBeTruthy();
});

it.each(["source_unavailable", "needs_update", "memory_changed"] as const)(
  "closes an already open context when the matter becomes %s",
  async (availability) => {
    api.getMatterContext.mockResolvedValue(context);
    render(<MatterWorkspace onOpenSources={() => {}} />);
    await userEvent.click(
      await screen.findByRole("button", { name: "任务上下文" }),
    );
    expect(await screen.findByDisplayValue(context.text)).toBeTruthy();
    api.getMatters.mockResolvedValue([{ ...matter, availability, claims: [] }]);
    fireEvent(document, new Event("visibilitychange"));
    await waitFor(() =>
      expect(
        (
          screen.getByRole("button", {
            name: "任务上下文",
          }) as HTMLButtonElement
        ).disabled,
      ).toBe(true),
    );
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByDisplayValue(context.text)).toBeNull();
  },
);

it("does not reopen context from a request started before switching matters", async () => {
  const response = deferred<MatterContext>();
  api.getMatterContext.mockReturnValue(response.promise);
  api.getMatters.mockResolvedValue([
    matter,
    { ...matter, matter_id: "m2", title: "Cedar 计划" },
  ]);
  render(<MatterWorkspace onOpenSources={() => {}} />);
  await userEvent.click(
    await screen.findByRole("button", { name: "任务上下文" }),
  );
  await userEvent.click(screen.getByRole("button", { name: /Cedar 计划/ }));
  await screen.findByRole("heading", { name: "Cedar 计划" });
  await act(async () => {
    response.resolve(context);
  });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.queryByDisplayValue(context.text)).toBeNull();
});

it("rejects a context returned for a different revision", async () => {
  api.getMatterContext.mockResolvedValue({ ...context, version: 2 });
  render(<MatterWorkspace onOpenSources={() => {}} />);
  await userEvent.click(
    await screen.findByRole("button", { name: "任务上下文" }),
  );
  await waitFor(() => expect(api.getMatterContext).toHaveBeenCalledOnce());
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("clears history and ignores its late response after source revocation", async () => {
  const response = deferred<Matter[]>();
  api.getMatterHistory.mockReturnValue(response.promise);
  render(<MatterWorkspace onOpenSources={() => {}} />);
  await userEvent.click(await screen.findByRole("tab", { name: "版本记录" }));
  const privateHistory = { ...matter, title: "A private historical title" };
  api.getMatters.mockResolvedValue([
    { ...matter, availability: "source_unavailable", claims: [] },
  ]);
  fireEvent(document, new Event("visibilitychange"));
  await screen.findByText(/旧档案已隐藏/);
  await act(async () => {
    response.resolve([privateHistory]);
  });
  expect(screen.queryByText(privateHistory.title)).toBeNull();
});

it("clears already loaded history when its matter becomes unavailable", async () => {
  api.getMatterHistory.mockResolvedValue([
    { ...matter, title: "Private prior version" },
  ]);
  render(<MatterWorkspace onOpenSources={() => {}} />);
  await userEvent.click(await screen.findByRole("tab", { name: "版本记录" }));
  await screen.findByText("Private prior version");
  api.getMatters.mockResolvedValue([
    { ...matter, availability: "source_unavailable", claims: [] },
  ]);
  fireEvent(document, new Event("visibilitychange"));
  await screen.findByText(/旧档案已隐藏/);
  expect(screen.queryByText("Private prior version")).toBeNull();
});

it("does not restore a late context after a known revocation", async () => {
  const response = deferred<MatterContext>();
  api.getMatterContext.mockReturnValue(response.promise);
  render(<MatterWorkspace onOpenSources={() => {}} />);
  await userEvent.click(
    await screen.findByRole("button", { name: "任务上下文" }),
  );
  api.getMatters.mockResolvedValue([
    { ...matter, availability: "source_unavailable", claims: [] },
  ]);
  fireEvent(document, new Event("visibilitychange"));
  await screen.findByText(/旧档案已隐藏/);
  await act(async () => {
    response.resolve(context);
  });
  expect(screen.queryByDisplayValue(context.text)).toBeNull();
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("applies a successful source invalidation even when run-status loading fails", async () => {
  api.getMatterContext.mockResolvedValue(context);
  render(<MatterWorkspace onOpenSources={() => {}} />);
  await userEvent.click(
    await screen.findByRole("button", { name: "任务上下文" }),
  );
  await screen.findByDisplayValue(context.text);
  api.getMatters.mockResolvedValue([
    { ...matter, availability: "source_unavailable", claims: [] },
  ]);
  api.getMatterRuns.mockRejectedValue(new Error("status endpoint unavailable"));
  fireEvent(document, new Event("visibilitychange"));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(screen.queryByDisplayValue(context.text)).toBeNull();
  expect(
    (screen.getByRole("button", { name: "任务上下文" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
});

it("does not wait for a pending progress request to apply source revocation", async () => {
  api.getMatterContext.mockResolvedValue(context);
  render(<MatterWorkspace onOpenSources={() => {}} />);
  await userEvent.click(
    await screen.findByRole("button", { name: "任务上下文" }),
  );
  await screen.findByDisplayValue(context.text);
  const progress = deferred<[]>();
  api.getMatterRuns.mockReturnValue(progress.promise);
  api.getMatters.mockResolvedValue([
    { ...matter, availability: "source_unavailable", claims: [] },
  ]);
  fireEvent(document, new Event("visibilitychange"));
  try {
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(
      (screen.getByRole("button", { name: "任务上下文" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  } finally {
    await act(async () => {
      progress.resolve([]);
    });
  }
});

it.each([false, true])(
  "invalidates memory details on version change (pending: %s)",
  async (pending) => {
    const memory: MemoryDetail["memory"] = {
      memory_id: "base-1",
      user_id: "local",
      revision: 1,
      status: "confirmed",
      kind: "working",
      content: "Memory before update",
      evidence_ids: ["e1"],
      tags: [],
      scopes: ["all-apps"],
      origin: "extracted",
      created_at: "2026-09-12",
      updated_at: "2026-09-12",
      deleted_at: null,
    };
    const item = {
      ...matter,
      source_engine: "hy-memory",
      claims: [
        {
          ...matter.claims[0],
          memory_refs: [
            { memory_id: memory.memory_id, revision: memory.revision },
          ],
        },
      ],
    };
    api.getMatters.mockResolvedValue([item]);
    api.getMemories.mockResolvedValue([memory]);
    const response = deferred<MemoryDetail>();
    const detail: MemoryDetail = {
      memory,
      history: [],
      index_sync: null,
      evidence: [
        {
          evidence_id: "e1",
          available: true,
          verbatim: "Private source before update",
          written_by: "person",
          source_id: "codex",
          role: "user",
          created_at: null,
        },
      ],
    };
    api.getMemory.mockReturnValue(
      pending ? response.promise : Promise.resolve(detail),
    );
    render(<MatterWorkspace onOpenSources={() => {}} />);
    await userEvent.click(
      await screen.findByRole("button", {
        name: "选择记忆 Memory before update",
      }),
    );
    if (!pending) await screen.findByText("Private source before update");
    api.getMatters.mockResolvedValue([
      { ...item, availability: "needs_update" },
    ]);
    fireEvent(document, new Event("visibilitychange"));
    await screen.findByText(/新资料待整理/);
    if (pending)
      await act(async () => {
        response.resolve(detail);
      });
    expect(screen.queryByText("Private source before update")).toBeNull();
  },
);

it.each([
  { revision: 2 },
  { status: "deleted" },
  { memory_id: "different-memory" },
])(
  "rejects a detail that no longer matches the selected reference: %j",
  async (change) => {
    const memory = {
      memory_id: "base-1",
      revision: 1,
      status: "confirmed",
      kind: "working",
      content: "Referenced memory",
      evidence_ids: ["e1"],
      tags: [],
      scopes: ["all-apps"],
    };
    api.getMatters.mockResolvedValue([
      {
        ...matter,
        source_engine: "hy-memory",
        claims: [
          {
            ...matter.claims[0],
            memory_refs: [{ memory_id: memory.memory_id, revision: 1 }],
          },
        ],
      },
    ]);
    api.getMemories.mockResolvedValue([memory]);
    api.getMemory.mockResolvedValue({
      memory: { ...memory, ...change },
      history: [],
      index_sync: null,
      evidence: [
        {
          evidence_id: "e1",
          available: true,
          verbatim: "Mismatched source text",
        },
      ],
    });
    render(<MatterWorkspace onOpenSources={() => {}} />);
    await userEvent.click(
      await screen.findByRole("button", { name: "选择记忆 Referenced memory" }),
    );
    await screen.findByText(/这条基础记忆已变化或不可读取/);
    expect(screen.queryByText("Mismatched source text")).toBeNull();
  },
);

it("separates full input coverage from failed dossier checks", async () => {
  api.getMatterRuns.mockResolvedValue([
    {
      run_id: "r1",
      status: "failed",
      phase: "整理未完成",
      failed_phase: "整理当前结论与变化历史",
      selected_records: 77,
      hy_input_records: 77,
      omitted_records: 0,
      coverage_complete: true,
      required_memory_coverage_complete: false,
      error: "matter_synthesis_coverage_incomplete",
      observations: [],
    },
  ]);
  render(<MatterWorkspace onOpenSources={() => {}} />);
  expect(await screen.findByText(/已读取 77 条/)).toBeTruthy();
  expect(screen.getByText(/HY 输入 77 条/)).toBeTruthy();
  expect(screen.getByText(/关键记忆未完整保留/)).toBeTruthy();
  expect(screen.queryByText("档案待核对")).toBeNull();
});

it("reports deferred conversations instead of implying complete coverage", async () => {
  api.getMatterRuns.mockResolvedValue([
    {
      run_id: "r2",
      status: "failed",
      phase: "整理未完成",
      selected_records: 3,
      hy_input_records: 0,
      omitted_records: 42,
      coverage_complete: false,
      error: "matter_collection_incomplete",
      observations: [],
    },
  ]);
  render(<MatterWorkspace onOpenSources={() => {}} />);
  expect(await screen.findByText(/42 条未处理/)).toBeTruthy();
  expect(screen.getByText(/本轮读取未完整/)).toBeTruthy();
});

it("refreshes idle matters so external revocation cannot leave old claims visible", async () => {
  render(<MatterWorkspace onOpenSources={() => {}} />);
  await screen.findByRole("heading", { name: matter.title });
  api.getMatters.mockResolvedValue([
    {
      ...matter,
      availability: "source_unavailable",
      claims: [],
      title: "来源不可用",
      purpose: "",
    },
  ]);
  await waitFor(
    () =>
      expect(
        screen.getByText("来源或基础记忆已变化，旧档案已隐藏。"),
      ).toBeTruthy(),
    { timeout: 7500 },
  );
  expect(
    (screen.getByRole("button", { name: "任务上下文" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
}, 9000);
