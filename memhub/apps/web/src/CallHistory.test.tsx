// @vitest-environment jsdom

import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { CallHistory } from "./CallHistory";
import type {
  CallEvent,
  CallHistoryPage,
  ResolvedCallReference,
} from "./call-history-client";
import { setUiLanguage } from "./ui-language";

const { getHistory, clearEvents, clearAllEvents, getReferences } = vi.hoisted(
  () => ({
    getHistory: vi.fn(),
    clearEvents: vi.fn(),
    clearAllEvents: vi.fn(),
    getReferences: vi.fn(),
  }),
);
const getConversation = vi.hoisted(() => vi.fn());
vi.mock("./api", () => ({
  getConversation,
}));
vi.mock("./call-history-client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./call-history-client")>()),
  getCallHistory: getHistory,
  clearCallEvents: clearEvents,
  clearAllCallEvents: clearAllEvents,
  getCallReferences: getReferences,
}));

function event(id = "event-1", patch: Partial<CallEvent> = {}): CallEvent {
  return {
    event_id: id,
    occurred_at: "2026-09-20T11:03:37.197+08:00",
    host_id: "cursor",
    installation_id: "install-1",
    tool: "om_recall",
    state: "grounded",
    stage: "core_result",
    delivery: "unknown",
    adoption: "unknown",
    host_session_id: null,
    injection_elapsed_ms: null,
    elapsed_ms: 26.8,
    raw_count: 2,
    memory_count: 1,
    references: [
      { kind: "raw", id: "raw-1", revision: 3, source_id: "source-1" },
      { kind: "memory", id: "memory-1", revision: 2, source_id: null },
    ],
    ...patch,
  };
}

function page(
  items = [event()],
  patch: Partial<CallHistoryPage> = {},
): CallHistoryPage {
  return {
    schema_version: 1,
    available: true,
    recording_error: false,
    total: items.length,
    offset: 0,
    limit: 20,
    items,
    retention_days: 30,
    max_events: 10000,
    ...patch,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

function mount() {
  return render(<CallHistory onBack={vi.fn()} />);
}
function refresh() {
  fireEvent.click(screen.getByRole("button", { name: "刷新记录" }));
}
function row(id: string) {
  return document.querySelector(`[data-event-id="${id}"]`);
}
async function loaded(id = "event-1") {
  await waitFor(() => expect(row(id)).not.toBeNull());
  return row(id) as HTMLElement;
}
async function openClear() {
  fireEvent.click(screen.getByRole("button", { name: "清除本页记录" }));
  return screen.findByRole("dialog");
}

const originalShowModal = Object.getOwnPropertyDescriptor(
  HTMLDialogElement.prototype,
  "showModal",
);
beforeEach(() => {
  getHistory.mockReset().mockResolvedValue(page());
  clearEvents.mockReset().mockResolvedValue(undefined);
  clearAllEvents.mockReset().mockResolvedValue(undefined);
  getConversation.mockReset().mockResolvedValue({
    conversation_id: "c1",
    source_id: "source-1",
    messages: [{ verbatim: "current permitted original" }],
  });
  getReferences.mockReset().mockImplementation(async (call: CallEvent) =>
    call.references.map((reference) => ({
      ...reference,
      available: false,
      title: null,
      reason: "unavailable",
    })),
  );
  Object.defineProperty(HTMLDialogElement.prototype, "showModal", {
    configurable: true,
    value: function (this: HTMLDialogElement) {
      this.setAttribute("open", "");
    },
  });
});
afterEach(async () => {
  cleanup();
  await setUiLanguage("zh-CN");
  window.localStorage.removeItem("memhub.ui-language");
  vi.restoreAllMocks();
  if (originalShowModal)
    Object.defineProperty(
      HTMLDialogElement.prototype,
      "showModal",
      originalShowModal,
    );
  else Reflect.deleteProperty(HTMLDialogElement.prototype, "showModal");
});

it("switches usage history, details and actions to English without changing original content", async () => {
  const title = "用户原始对话标题";
  const original = "用户原始对话内容";
  getReferences.mockImplementation(async (call: CallEvent) =>
    call.references.map((reference) => ({
      ...reference,
      available: true,
      title,
      reason: null,
    })),
  );
  getConversation.mockResolvedValue({
    messages: [{ evidence_id: "raw-1", verbatim: original }],
  });
  mount();
  await loaded();
  await act(async () => setUiLanguage("en"));
  expect(screen.getByRole("heading", { name: "Usage history" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Refresh history" })).toBeTruthy();
  expect(screen.getByText("Cursor found 3 related items")).toBeTruthy();
  fireEvent.click(row("event-1") as HTMLElement);
  expect(
    screen.getByRole("heading", { name: "This memory lookup" }),
  ).toBeTruthy();
  expect(screen.getByRole("article", { name: "Call details" })).toBeTruthy();
  expect(screen.getByText(/Found 2 original passages/)).toBeTruthy();
  expect(screen.getAllByText("Lookup succeeded")).toHaveLength(2);
  expect(
    (await screen.findAllByRole("heading", { level: 4 })).every((heading) =>
      heading.textContent?.includes(title),
    ),
  ).toBe(true);
  fireEvent.click(
    screen.getByRole("button", { name: "View original conversation" }),
  );
  expect(await screen.findByText(original)).toBeTruthy();
  expect(screen.queryByText("用户原始对话内容", { exact: false })).toBeTruthy();
});

it("retranslates an existing clear failure without echoing an error code", async () => {
  clearEvents.mockRejectedValue(new Error("call_history_disabled"));
  mount();
  await loaded();
  await openClear();
  fireEvent.click(screen.getByRole("button", { name: "确认清除" }));
  expect(
    await screen.findByText(
      "清除结果未确认，请核对重新读取的记录。未自动重试清除。",
    ),
  ).toBeTruthy();
  await act(async () => setUiLanguage("en"));
  expect(
    screen.getByText(
      "Could not confirm the result. Check the reloaded history; clearing was not retried automatically.",
    ),
  ).toBeTruthy();
  expect(document.body.textContent).not.toContain("call_history_disabled");
});

it("loads a bounded first page and returns to applications", async () => {
  const pending = deferred<CallHistoryPage>();
  getHistory.mockReturnValueOnce(pending.promise);
  const onBack = vi.fn();
  render(<CallHistory onBack={onBack} />);
  expect(screen.getByRole("status").textContent).toContain("正在读取");
  expect(getHistory).toHaveBeenCalledWith({ limit: 20, offset: 0 });
  await act(async () => pending.resolve(page()));
  expect(await loaded()).toBeTruthy();
  expect(screen.getByText("保留 30 天，最多 10,000 条记录")).toBeTruthy();
  expect(
    (screen.getByRole("button", { name: "上一页" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  expect(
    (screen.getByRole("button", { name: "下一页" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "返回应用" }));
  expect(onBack).toHaveBeenCalledOnce();
});

it("renders an empty page without inventing a successful call", async () => {
  getHistory.mockResolvedValue(page([]));
  mount();
  await screen.findByText("暂无使用记录");
  expect(screen.queryByText(/Core已生成结果|已采用/)).toBeNull();
  expect(
    (screen.getByRole("button", { name: "清除本页记录" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
});

it.each(["reject", "unavailable"])(
  "does not render unavailable data as an empty or successful result: %s",
  async (mode) => {
    if (mode === "reject")
      getHistory.mockRejectedValue(new Error("sensitive response body"));
    else getHistory.mockResolvedValue(page([event()], { available: false }));
    mount();
    await screen.findByRole("alert");
    expect(screen.queryByText("暂无使用记录")).toBeNull();
    expect(
      screen.queryByText(/记忆召回|om_recall|sensitive response body/),
    ).toBeNull();
    getHistory.mockResolvedValue(page());
    refresh();
    await loaded();
  },
);

it("hides the previous page during refresh and after failure", async () => {
  mount();
  await loaded();
  const pending = deferred<CallHistoryPage>();
  getHistory.mockReturnValueOnce(pending.promise);
  refresh();
  expect(row("event-1")).toBeNull();
  expect(screen.queryByRole("navigation", { name: "记录分页" })).toBeNull();
  await act(async () => pending.reject(new Error("offline")));
  await screen.findByRole("alert");
  expect(screen.queryByText(/Core已生成结果/)).toBeNull();
});

it("ignores late refresh responses when a newer request has completed", async () => {
  const old = deferred<CallHistoryPage>();
  getHistory.mockReturnValueOnce(old.promise);
  mount();
  getHistory.mockResolvedValueOnce(page([event("new")]));
  refresh();
  await loaded("new");
  await act(async () => old.resolve(page([event("old")])));
  expect(row("old")).toBeNull();
  expect(await loaded("new")).toBeTruthy();
});

it("paginates in bounded pages and resets the offset on host filtering", async () => {
  const first = page(
    Array.from({ length: 20 }, (_, index) => event(`first-${index}`)),
    { total: 21 },
  );
  getHistory
    .mockResolvedValueOnce(first)
    .mockResolvedValueOnce(page([event("last")], { offset: 20, total: 21 }));
  mount();
  await loaded("first-0");
  expect(
    within(screen.getByRole("list", { name: "调用记录" })).getAllByRole(
      "listitem",
    ),
  ).toHaveLength(20);
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  await loaded("last");
  expect(getHistory).toHaveBeenLastCalledWith({ limit: 20, offset: 20 });
  expect(row("first-0")).toBeNull();
  expect(
    (screen.getByRole("button", { name: "下一页" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  getHistory.mockResolvedValueOnce(first);
  fireEvent.click(screen.getByRole("button", { name: "上一页" }));
  await loaded("first-0");
  expect(getHistory).toHaveBeenLastCalledWith({ limit: 20, offset: 0 });
  getHistory.mockResolvedValueOnce(page([event("filtered")]));
  fireEvent.change(screen.getByRole("combobox", { name: "按应用筛选" }), {
    target: { value: "cursor" },
  });
  await loaded("filtered");
  expect(getHistory).toHaveBeenLastCalledWith({
    limit: 20,
    offset: 0,
    hostId: "cursor",
  });
});

it("ignores an older host response and allows returning to the unfiltered list", async () => {
  getHistory.mockResolvedValueOnce(
    page([event(), event("other", { host_id: "codex" })]),
  );
  mount();
  await loaded();
  const old = deferred<CallHistoryPage>();
  getHistory
    .mockReturnValueOnce(old.promise)
    .mockResolvedValueOnce(page([event("codex", { host_id: "codex" })]));
  const filter = screen.getByRole("combobox");
  fireEvent.change(filter, { target: { value: "cursor" } });
  fireEvent.change(filter, { target: { value: "codex" } });
  await loaded("codex");
  await act(async () => old.resolve(page([event("old")])));
  expect(row("old")).toBeNull();
  fireEvent.change(filter, { target: { value: "" } });
  await loaded();
  expect(getHistory).toHaveBeenLastCalledWith({ limit: 20, offset: 0 });
});

it("shows exact identity, timestamp, result counts and metadata-only references", async () => {
  mount();
  fireEvent.click(await loaded());
  const detail = screen.getByRole("article", { name: "调用详情" });
  const exactTime = within(detail).getByText(
    new Date(event().occurred_at).toLocaleString("zh-CN", { hour12: false }),
  );
  expect(exactTime.getAttribute("datetime")).toBe(event().occurred_at);
  expect(exactTime.textContent).not.toContain("T11:03:37");
  expect(within(detail).getByText("查记忆")).toBeTruthy();
  expect(within(detail).queryByText("om_recall")).toBeNull();
  // The metadata table must not leak the raw Core state enum next to the
  // The humanized state must not leak the raw Core enum.
  expect(within(detail).queryByText("grounded")).toBeNull();
  expect(within(detail).getAllByText("查询成功").length).toBeGreaterThan(0);
  expect(
    within(detail).getByText("找到原文 2 段 · 记忆 1 条 · 用时 0.0 秒"),
  ).toBeTruthy();
  expect(
    within(detail).getByText((value) =>
      value.includes("Cursor 查到 3 条相关内容"),
    ),
  ).toBeTruthy();
  expect(within(detail).getAllByText("source-1").length).toBeGreaterThan(0);
  expect(within(detail).queryByText(/第 3 版|第 2 版/)).toBeNull();
  expect(within(detail).queryByRole("link")).toBeNull();
  expect(within(detail).queryByText(/调用成功|已经采用/)).toBeNull();
  expect(within(detail).queryByText(/送达未确认|采用未确认|Core/)).toBeNull();
  expect(document.activeElement).toBe(
    screen.getByRole("heading", { name: "这次查记忆" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "返回记录" }));
  expect(document.activeElement).toBe(await loaded());
});
it("shows delivered delivery without claiming adoption", async () => {
  getHistory.mockResolvedValue(
    page([
      event("delivered", {
        delivery: "delivered",
        host_session_id: "session-1",
        injection_elapsed_ms: 31.4,
      }),
    ]),
  );
  mount();
  fireEvent.click(await loaded("delivered"));
  const detail = screen.getByRole("article", { name: "调用详情" });
  expect(within(detail).getAllByText("已提供给 Cursor").length).toBeGreaterThan(
    0,
  );
  expect(within(detail).queryByText(/已采用/)).toBeNull();
});
it("does not claim delivery for a call Core never completed", async () => {
  getHistory.mockResolvedValue(
    page([
      event("unavailable", {
        state: "unavailable",
        delivery: "unknown",
        raw_count: 0,
        memory_count: 0,
        references: [],
      }),
    ]),
  );
  mount();
  fireEvent.click(
    await screen.findByRole("button", {
      name: "Cursor 暂时无法查记忆",
    }),
  );
  const detail = screen.getByRole("article", { name: "调用详情" });
  expect(within(detail).queryByText(/已返回给/)).toBeNull();
  expect(
    within(detail).getByText("One Memory 暂时不可用，这次回答没有带上记忆。"),
  ).toBeTruthy();
});
it("does not restate an undelivered result as returned", async () => {
  getHistory.mockResolvedValue(
    page([event("not-delivered", { delivery: "not_delivered" })]),
  );
  mount();
  fireEvent.click(await loaded("not-delivered"));
  const detail = screen.getByRole("article", { name: "调用详情" });
  expect(within(detail).getAllByText("没有提供给应用")).toHaveLength(1);
  expect(within(detail).queryByText(/已返回给/)).toBeNull();
});
it("shows delivery state in the list without turning it into adoption", async () => {
  getHistory.mockResolvedValue(
    page([
      event("delivered-list", { delivery: "delivered" }),
      event("not-delivered-list", { delivery: "not_delivered" }),
      event("unknown-list"),
    ]),
  );
  mount();
  await loaded("delivered-list");
  const list = screen.getByRole("list", { name: "调用记录" });
  expect(within(list).getByText("已提供给 Cursor")).toBeTruthy();
  expect(within(list).getByText("没有提供给应用")).toBeTruthy();
  expect(within(list).getByText("查询成功")).toBeTruthy();
  expect(within(list).queryByText(/已采用/)).toBeNull();
});

it("keeps detail bound to event ID across refresh reordering, then removes missing details", async () => {
  getHistory.mockResolvedValueOnce(page([event("one"), event("two")]));
  mount();
  fireEvent.click(await loaded("two"));
  getHistory.mockResolvedValueOnce(
    page([event("two", { memory_count: 7 }), event("one")]),
  );
  refresh();
  expect(screen.queryByRole("article")).toBeNull();
  await screen.findByText("找到原文 2 段 · 记忆 7 条 · 用时 0.0 秒");
  expect(screen.getByRole("article").textContent).not.toContain("记忆 1 条");
  getHistory.mockResolvedValueOnce(page([event("one")]));
  refresh();
  await screen.findByText("这条记录已不在当前页，请返回记录查看。");
  expect(screen.queryByRole("article")).toBeNull();
});

it.each([
  ["grounded", "查询成功", "直接调用（诊断） 查到 0 条相关内容"],
  ["empty", "查询成功，没有找到相关记忆", "直接调用（诊断） 没有查到相关记忆"],
  [
    "partial",
    "查询成功，部分内容还在整理中",
    "直接调用（诊断） 查到 0 条相关内容",
  ],
  [
    "timed_out",
    "查询超时，没有提供记忆",
    "直接调用（诊断） 这次没来得及查到记忆",
  ],
  ["unavailable", "One Memory 暂时不可用", "直接调用（诊断） 暂时无法查记忆"],
  ["denied", "当前未授权查询", "直接调用（诊断） 未获准查记忆"],
] as const)(
  "renders the %s state without claiming external adoption",
  async (state, label, headline) => {
    getHistory.mockResolvedValue(
      page([
        event("state", {
          state,
          host_id: null,
          installation_id: null,
          references: [],
          raw_count: 0,
          memory_count: 0,
        }),
      ]),
    );
    mount();
    fireEvent.click(
      await screen.findByRole("button", {
        name: headline,
      }),
    );
    expect(
      screen.getByRole("heading", {
        name: /^未记录 · 09\/20 \d{2}:03$/,
      }),
    ).toBeTruthy();
    expect(screen.getByText("没有找到相关内容")).toBeTruthy();
    expect(screen.queryByText(/已经采用|调用成功/)).toBeNull();
    // The humanized state label appears both as the stage line and in the
    // 记录详情 table; the raw enum must appear nowhere.
    expect(screen.getAllByText(new RegExp(label)).length).toBeGreaterThan(0);
    expect(screen.queryByText(state)).toBeNull();
  },
);

it("does not call an unavailable call returned", async () => {
  getHistory.mockResolvedValue(
    page([
      event("unavailable-call", {
        state: "unavailable",
        raw_count: 0,
        memory_count: 0,
        references: [],
      }),
    ]),
  );
  mount();
  fireEvent.click(
    await screen.findByRole("button", {
      name: "Cursor 暂时无法查记忆",
    }),
  );
  const detail = screen.getByRole("article", { name: "调用详情" });
  expect(within(detail).queryByText(/· 已返回/)).toBeNull();
  expect(
    within(detail).getAllByText(/One Memory 暂时不可用/).length,
  ).toBeGreaterThan(0);
  expect(within(detail).queryByText(/采用未确认|Core/)).toBeNull();
});

it.each(["om_recall", "om_conversations", "om_ask", "om_matters"] as const)(
  "renders %s as 查记忆 instead of the API name",
  async (tool) => {
    getHistory.mockResolvedValue(page([event("tool", { tool })]));
    mount();
    expect(
      await screen.findByRole("button", {
        name: "Cursor 查到 3 条相关内容",
      }),
    ).toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: "Cursor 查到 3 条相关内容" }),
    );
    expect(
      within(screen.getByRole("article")).getByText("查记忆"),
    ).toBeTruthy();
    expect(screen.queryByText(tool)).toBeNull();
  },
);

it("warns when recording is incomplete without hiding readable historical metadata", async () => {
  getHistory.mockResolvedValue(page([event()], { recording_error: true }));
  mount();
  await loaded();
  expect(screen.getByRole("alert").textContent).toContain("列表可能不全");
});

it("requires a distinct confirmation for clearing all retained records", async () => {
  getHistory.mockResolvedValueOnce(page([event()], { total: 21, offset: 0 }));
  mount();
  await loaded();
  fireEvent.click(screen.getByRole("button", { name: "清空全部记录" }));
  const dialog = await screen.findByRole("dialog");
  expect(
    within(dialog).getByRole("heading", { name: "清空全部使用记录？" }),
  ).toBeTruthy();
  expect(dialog.textContent).toContain("不限于本页或当前筛选");
  fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
  expect(clearAllEvents).not.toHaveBeenCalled();
  expect(clearEvents).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "清空全部记录" }));
  getHistory.mockResolvedValueOnce(page([], { total: 0 }));
  fireEvent.click(
    within(await screen.findByRole("dialog")).getByRole("button", {
      name: "确认清空全部",
    }),
  );
  await screen.findByText("暂无使用记录");
  // A clear is a real write: it now carries a trace id so the ui.action /
  // ui.result lines join Core's kernel-call/return in one log.
  expect(clearAllEvents).toHaveBeenCalledTimes(1);
  expect(clearAllEvents.mock.calls[0][0]).toEqual(expect.any(String));
  expect(clearEvents).not.toHaveBeenCalled();
  expect(getHistory).toHaveBeenLastCalledWith({ limit: 20, offset: 0 });
});

it("requires confirmation and cancelling or Escape sends no clear request", async () => {
  mount();
  await loaded();
  let dialog = await openClear();
  expect(
    within(dialog).getByRole("heading", { name: "清除本页 1 条记录？" }),
  ).toBeTruthy();
  fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
  expect(clearEvents).not.toHaveBeenCalled();
  dialog = await openClear();
  fireEvent(dialog, new Event("cancel", { bubbles: true, cancelable: true }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(clearEvents).not.toHaveBeenCalled();
});

it("clears only the confirmed current-page IDs, suppresses double submit, and rereads", async () => {
  getHistory
    .mockResolvedValueOnce(page([event("first")], { total: 21 }))
    .mockResolvedValueOnce(page([event("last")], { offset: 20, total: 21 }));
  mount();
  await loaded("first");
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  await loaded("last");
  const pending = deferred<void>();
  clearEvents.mockReturnValueOnce(pending.promise);
  const dialog = await openClear();
  const confirm = within(dialog).getByRole("button", { name: "确认清除" });
  fireEvent.click(confirm);
  fireEvent.click(confirm);
  expect(clearEvents).toHaveBeenCalledTimes(1);
  expect(clearEvents.mock.calls[0][0]).toEqual(["last"]);
  expect(clearEvents.mock.calls[0][1]).toEqual(expect.any(String));
  expect(row("last")).toBeNull();
  expect(
    (within(dialog).getByRole("button", { name: "取消" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  getHistory.mockResolvedValueOnce(
    page([event("new-arrival")], { offset: 20, total: 21 }),
  );
  await act(async () => pending.resolve());
  await loaded("new-arrival");
  expect(getHistory).toHaveBeenLastCalledWith({ limit: 20, offset: 20 });
  expect(clearEvents).toHaveBeenCalledTimes(1);
  expect(screen.getByText("已清除所选记录。")).toBeTruthy();
});

it("rereads after an unknown clear outcome without replaying the deletion", async () => {
  mount();
  await loaded();
  clearEvents.mockRejectedValueOnce(new Error("private body"));
  getHistory.mockResolvedValueOnce(page([event("retained")]));
  const dialog = await openClear();
  fireEvent.click(within(dialog).getByRole("button", { name: "确认清除" }));
  await loaded("retained");
  expect(screen.getByText(/清除结果未确认/)).toBeTruthy();
  expect(screen.queryByText(/private body|已清除所选记录/)).toBeNull();
  refresh();
  await loaded();
  expect(clearEvents).toHaveBeenCalledTimes(1);
  expect(clearEvents.mock.calls[0][0]).toEqual(["event-1"]);
});

it("keeps read failure and unknown deletion visible together", async () => {
  mount();
  await loaded();
  clearEvents.mockRejectedValueOnce(new Error("lost response"));
  getHistory.mockRejectedValueOnce(new Error("offline"));
  const dialog = await openClear();
  fireEvent.click(within(dialog).getByRole("button", { name: "确认清除" }));
  await screen.findByText("记录读取失败，请刷新重试。");
  expect(screen.getByText(/清除结果未确认/)).toBeTruthy();
  expect(screen.queryByText("暂无使用记录")).toBeNull();
  expect(clearEvents).toHaveBeenCalledTimes(1);
});

it("reports a refused clear as a definite failure, not as unconfirmed", async () => {
  // Core answers `409 call_history_disabled` when the store is disabled: that
  // is a refusal, so nothing was deleted. The old code logged `unknown` and
  // said "未确认" for this case, contradicting a definite Core answer.
  mount();
  await loaded();
  clearEvents.mockRejectedValueOnce(
    Object.assign(new Error("refused"), {
      response: { status: 409, data: { detail: "call_history_disabled" } },
    }),
  );
  getHistory.mockResolvedValueOnce(page([event("retained")]));
  const dialog = await openClear();
  fireEvent.click(within(dialog).getByRole("button", { name: "确认清除" }));
  expect(
    await screen.findByText(/未能清除所选记录，当前记录保持不变/),
  ).toBeTruthy();
  expect(screen.queryByText(/清除结果未确认/)).toBeNull();
  // The records are still there and are never auto-deleted twice.
  expect(clearEvents).toHaveBeenCalledTimes(1);
});

it("reports a refused clear-all as a definite failure", async () => {
  mount();
  await loaded();
  clearAllEvents.mockRejectedValueOnce(
    Object.assign(new Error("refused"), {
      response: { status: 409, data: { detail: "call_history_disabled" } },
    }),
  );
  getHistory.mockResolvedValueOnce(page([event("retained")]));
  fireEvent.click(screen.getByRole("button", { name: "清空全部记录" }));
  fireEvent.click(
    within(await screen.findByRole("dialog")).getByRole("button", {
      name: "确认清空全部",
    }),
  );
  expect(
    await screen.findByText(/未能清空使用记录，当前记录保持不变/),
  ).toBeTruthy();
  expect(screen.queryByText(/清除结果未确认/)).toBeNull();
  expect(clearAllEvents).toHaveBeenCalledTimes(1);
});

it("ignores late reads after unmount and never persists metadata locally", async () => {
  const storage = vi.spyOn(Storage.prototype, "setItem");
  const old = deferred<CallHistoryPage>();
  getHistory.mockReturnValueOnce(old.promise);
  const view = mount();
  view.unmount();
  mount();
  await loaded();
  await act(async () => old.resolve(page([event("old")])));
  expect(row("old")).toBeNull();
  expect(storage).not.toHaveBeenCalled();
});

it("does not issue recovery reads after unmount during deletion", async () => {
  const view = mount();
  await loaded();
  const pending = deferred<void>();
  clearEvents.mockReturnValueOnce(pending.promise);
  const dialog = await openClear();
  fireEvent.click(within(dialog).getByRole("button", { name: "确认清除" }));
  view.unmount();
  await act(async () => pending.reject(new Error("lost response")));
  await waitFor(() => expect(getHistory).toHaveBeenCalledTimes(1));
  expect(clearEvents).toHaveBeenCalledTimes(1);
});

it("can return from a page emptied by retention or clearing", async () => {
  getHistory
    .mockResolvedValueOnce(page([event()], { total: 21 }))
    .mockResolvedValueOnce(page([], { offset: 20, total: 20 }));
  mount();
  await loaded();
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  await screen.findByText("本页暂无记录");
  expect(
    (screen.getByRole("button", { name: "下一页" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  expect(
    (screen.getByRole("button", { name: "上一页" }) as HTMLButtonElement)
      .disabled,
  ).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "上一页" }));
  await loaded();
  expect(getHistory).toHaveBeenLastCalledWith({ limit: 20, offset: 0 });
});

it("offers host filters even when that host is absent from the current page", async () => {
  mount();
  await loaded();
  getHistory.mockResolvedValueOnce(page([]));
  fireEvent.change(screen.getByRole("combobox"), {
    target: { value: "workbuddy" },
  });
  await screen.findByText("暂无使用记录");
  expect(getHistory).toHaveBeenLastCalledWith({
    limit: 20,
    offset: 0,
    hostId: "workbuddy",
  });
});

it("preserves the filter and offset after an unknown clear result", async () => {
  mount();
  await loaded();
  getHistory
    .mockResolvedValueOnce(page([event()], { total: 21 }))
    .mockResolvedValueOnce(page([event("last")], { total: 21, offset: 20 }));
  fireEvent.change(screen.getByRole("combobox"), {
    target: { value: "cursor" },
  });
  await loaded();
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  await loaded("last");
  clearEvents.mockRejectedValueOnce(new Error("unknown"));
  getHistory.mockResolvedValueOnce(
    page([event("last")], { total: 21, offset: 20 }),
  );
  const dialog = await openClear();
  fireEvent.click(within(dialog).getByRole("button", { name: "确认清除" }));
  await screen.findByText(/清除结果未确认/);
  await loaded("last");
  expect(getHistory).toHaveBeenLastCalledWith({
    limit: 20,
    offset: 20,
    hostId: "cursor",
  });
  expect(clearEvents).toHaveBeenCalledTimes(1);
  expect(clearEvents.mock.calls[0][0]).toEqual(["last"]);
});

function resolved(call: CallEvent, title: string): ResolvedCallReference[] {
  return call.references.map((reference) => ({
    ...reference,
    available: true,
    title: `${title}-${reference.kind}`,
    reason: null,
  }));
}

it("resolves titles only on detail selection and shows explicit loading without invented content", async () => {
  const call = event();
  const pending = deferred<ResolvedCallReference[]>();
  getReferences.mockReturnValueOnce(pending.promise);
  const storage = vi.spyOn(Storage.prototype, "setItem");
  mount();
  const row = await loaded();
  expect(getReferences).not.toHaveBeenCalled();
  fireEvent.click(row);
  expect(getReferences).toHaveBeenCalledExactlyOnceWith(call);
  expect(screen.getByText("正在读取相关内容")).toBeTruthy();
  expect(screen.queryByText("原文 · permission-checked-raw")).toBeNull();
  await act(async () => pending.resolve(resolved(call, "permission-checked")));
  expect(screen.getByText("原文 · permission-checked-raw")).toBeTruthy();
  expect(screen.getByText("记忆 · permission-checked-memory")).toBeTruthy();
  expect(screen.getByText("source-1")).toBeTruthy();
  expect(screen.queryByRole("link")).toBeNull();
  expect(screen.getByRole("button", { name: "查看原文" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "查看记忆" })).toBeTruthy();
  expect(storage).not.toHaveBeenCalled();
  expect(call.references[0]).not.toHaveProperty("title");
});

it("opens currently permitted memory or evidence and hides withdrawn titles", async () => {
  const onOpenMemory = vi.fn();
  const onOpenEvidence = vi.fn();
  getReferences.mockResolvedValueOnce(resolved(event(), "permission-checked"));
  render(
    <CallHistory
      onBack={vi.fn()}
      onOpenMemory={onOpenMemory}
      onOpenEvidence={onOpenEvidence}
    />,
  );
  fireEvent.click(await loaded());
  await screen.findByText("原文 · permission-checked-raw");
  fireEvent.click(screen.getByRole("button", { name: "查看原文" }));
  fireEvent.click(screen.getByRole("button", { name: "查看记忆" }));
  expect(onOpenEvidence).toHaveBeenCalledExactlyOnceWith("raw-1");
  expect(onOpenMemory).toHaveBeenCalledExactlyOnceWith("memory-1");
  getReferences.mockResolvedValueOnce([
    {
      ...event().references[0],
      available: false,
      title: null,
      reason: "changed",
    },
    {
      ...event().references[1],
      available: false,
      title: null,
      reason: "unavailable",
    },
  ]);
  refresh();
  await screen.findByText("原文 · 版本已变更");
  expect(
    screen.queryByRole("button", { name: /查看记忆|查看原文/ }),
  ).toBeNull();
});

it("drops a late reference response after selecting a different event", async () => {
  const first = event("one");
  const second = event("two");
  getHistory.mockResolvedValue(page([first, second]));
  const old = deferred<ResolvedCallReference[]>();
  getReferences
    .mockReturnValueOnce(old.promise)
    .mockResolvedValueOnce(resolved(second, "current"));
  mount();
  fireEvent.click(await loaded("one"));
  fireEvent.click(screen.getByRole("button", { name: "返回记录" }));
  fireEvent.click(await loaded("two"));
  await screen.findByText("原文 · current-raw");
  await act(async () => old.resolve(resolved(first, "old-private-title")));
  expect(screen.queryByText(/old-private-title/)).toBeNull();
  expect(screen.getByText("原文 · current-raw")).toBeTruthy();
  expect(getReferences).toHaveBeenNthCalledWith(2, second);
});

it("removes titles immediately on refresh and keeps them hidden when rechecking fails", async () => {
  getReferences.mockResolvedValueOnce(resolved(event(), "previous-title"));
  mount();
  fireEvent.click(await loaded());
  await screen.findByText("原文 · previous-title-raw");
  const pending = deferred<ResolvedCallReference[]>();
  getReferences.mockReturnValueOnce(pending.promise);
  refresh();
  expect(screen.queryByText(/previous-title/)).toBeNull();
  await screen.findByText("正在读取相关内容");
  expect(screen.queryByText(/previous-title/)).toBeNull();
  await waitFor(() => expect(getReferences).toHaveBeenCalledTimes(2));
  expect(getReferences.mock.results[1].value).toBe(pending.promise);
  await act(async () => pending.reject(new Error("private error content")));
  await screen.findByText("相关内容暂时无法读取，请刷新重试。");
  expect(screen.queryByText(/previous-title|private error content/)).toBeNull();
  expect(screen.getByText("原文 · 不可用")).toBeTruthy();
  getReferences.mockResolvedValueOnce(resolved(event(), "fresh-title"));
  refresh();
  await screen.findByText("原文 · fresh-title-raw");
});

it("does not restore a pre-refresh title after a newer reference recheck fails", async () => {
  const pending = deferred<ResolvedCallReference[]>();
  getReferences
    .mockReturnValueOnce(pending.promise)
    .mockRejectedValueOnce(new Error("offline"));
  mount();
  fireEvent.click(await loaded());
  refresh();
  await screen.findByText("相关内容暂时无法读取，请刷新重试。");
  await act(async () => pending.resolve(resolved(event(), "stale-title")));
  expect(screen.queryByText(/stale-title/)).toBeNull();
  expect(screen.getByText("相关内容暂时无法读取，请刷新重试。")).toBeTruthy();
});

it("shows changed or unavailable references without retaining previously permitted titles", async () => {
  getReferences.mockResolvedValueOnce(resolved(event(), "revoked-title"));
  mount();
  fireEvent.click(await loaded());
  await screen.findByText("记忆 · revoked-title-memory");
  getReferences.mockResolvedValueOnce([
    {
      ...event().references[0],
      available: false,
      title: null,
      reason: "changed",
    },
    {
      ...event().references[1],
      available: false,
      title: null,
      reason: "unavailable",
    },
  ]);
  refresh();
  await screen.findByText("原文 · 版本已变更");
  expect(screen.getByText("记忆 · 不可用")).toBeTruthy();
  expect(screen.queryByText(/revoked-title/)).toBeNull();
  expect(screen.queryByRole("link")).toBeNull();
});

it("never renders a title from an unavailable reference even if a malformed mock includes one", async () => {
  getReferences.mockResolvedValue(
    event().references.map((reference) => ({
      ...reference,
      available: false,
      title: "forbidden-title",
      reason: "unavailable",
    })),
  );
  mount();
  fireEvent.click(await loaded());
  await screen.findByText("原文 · 不可用");
  expect(screen.queryByText(/forbidden-title/)).toBeNull();
});

it("drops late reference content after leaving detail or unmounting", async () => {
  const pending = deferred<ResolvedCallReference[]>();
  getReferences.mockReturnValueOnce(pending.promise);
  const view = mount();
  fireEvent.click(await loaded());
  fireEvent.click(screen.getByRole("button", { name: "返回记录" }));
  await act(async () => pending.resolve(resolved(event(), "late-title")));
  expect(screen.queryByText(/late-title/)).toBeNull();
  const unmounted = deferred<ResolvedCallReference[]>();
  getReferences.mockReturnValueOnce(unmounted.promise);
  fireEvent.click(await loaded());
  view.unmount();
  mount();
  await loaded();
  await act(async () =>
    unmounted.resolve(resolved(event(), "unmounted-title")),
  );
  expect(screen.queryByText(/unmounted-title/)).toBeNull();
});

it("does not resolve references for calls with no references", async () => {
  getHistory.mockResolvedValue(page([event("empty", { references: [] })]));
  mount();
  fireEvent.click(await loaded("empty"));
  expect(screen.getByText("没有找到相关内容")).toBeTruthy();
  expect(getReferences).not.toHaveBeenCalled();
});
