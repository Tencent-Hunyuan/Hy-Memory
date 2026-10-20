// @vitest-environment jsdom

import type {
  ConversationSearchResponse,
  ConversationTranscript,
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
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ConversationSearch } from "./ConversationSearch";
import { setUiLanguage } from "./ui-language";

const {
  searchConversations,
  getConversation,
  getConversationBrowser,
  getConversationMemories,
} = vi.hoisted(() => ({
  searchConversations: vi.fn(),
  getConversation: vi.fn(),
  getConversationBrowser: vi.fn(),
  getConversationMemories: vi.fn(),
}));

vi.mock("./api", () => ({
  apiErrorCode: (reason: unknown) =>
    reason instanceof Error ? reason.message : null,
  searchConversations,
  getConversation,
  getConversationBrowser,
  getConversationMemories,
}));

const found: ConversationSearchResponse = {
  hits: [
    {
      evidence_id: "evidence-1",
      conversation_id: "conversation-1",
      source_id: "cursor",
      workspace: "/Users/k/one-memory",
      project: "one-memory",
      created_at: "2026-09-03T01:17:00Z",
      excerpt: "昨天晚上重新打了一个包，抽取终于跑通了",
    },
  ],
  empty_reason: null,
  imported_messages: 6740,
};

const transcript: ConversationTranscript = {
  conversation_id: "conversation-1",
  source_id: "cursor",
  messages: [
    {
      evidence_id: "evidence-1",
      role: "user",
      written_by: "person",
      created_at: "2026-09-03T01:17:00Z",
      verbatim: "昨天晚上重新打了一个包，抽取终于跑通了",
    },
    {
      evidence_id: "evidence-2",
      role: "assistant",
      written_by: "agent",
      created_at: "2026-09-03T01:18:00Z",
      verbatim: "那就先把包发出去",
    },
  ],
};

const scrollIntoView = vi.fn();

beforeEach(() => {
  searchConversations.mockReset();
  getConversation.mockReset();
  getConversationBrowser.mockReset().mockResolvedValue({
    items: [],
    total: 0,
    offset: 0,
    limit: 50,
  });
  getConversationMemories.mockReset().mockResolvedValue({ items: [] });
  searchConversations.mockResolvedValue(found);
  getConversation.mockResolvedValue(transcript);
  scrollIntoView.mockReset();
  // jsdom has no layout, so the component's scroll call would throw here.
  Element.prototype.scrollIntoView = scrollIntoView;
});

afterEach(async () => {
  cleanup();
  await setUiLanguage("zh-CN");
  window.localStorage.removeItem("memhub.ui-language");
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

const secondHit = {
  ...found.hits[0],
  evidence_id: "evidence-b",
  conversation_id: "conversation-b",
  source_id: "workbuddy",
  excerpt: "第二段命中摘要",
};
const secondTranscript: ConversationTranscript = {
  conversation_id: "conversation-b",
  source_id: "workbuddy",
  messages: [
    {
      evidence_id: "evidence-b",
      role: "user",
      written_by: "person",
      created_at: null,
      verbatim: "B 的完整原文",
    },
  ],
};

function hitArticle(excerpt: string) {
  const article = screen.getByText(excerpt).closest("article");
  if (!article) throw new Error("missing hit article");
  return article;
}

async function openA(user: ReturnType<typeof userEvent.setup>) {
  await user.click(
    within(hitArticle(found.hits[0].excerpt)).getByRole("button", {
      name: "查看原文",
    }),
  );
}

async function openB(user: ReturnType<typeof userEvent.setup>) {
  await user.click(
    within(hitArticle(secondHit.excerpt)).getByRole("button", {
      name: "查看原文",
    }),
  );
}

async function search(
  text = "抽取终于",
  props: Parameters<typeof ConversationSearch>[0] = {},
) {
  const user = userEvent.setup();
  render(<ConversationSearch {...props} />);
  await user.type(screen.getByLabelText("搜索历史对话"), text);
  await user.click(screen.getByRole("button", { name: "搜索" }));
  return user;
}

it("switches conversation controls to English without translating original messages", async () => {
  const user = userEvent.setup();
  render(<ConversationSearch />);
  await user.type(
    screen.getByRole("textbox", { name: "搜索历史对话" }),
    "打包",
  );
  await user.click(screen.getByRole("button", { name: "搜索" }));
  await screen.findByText(found.hits[0].excerpt);
  await openA(user);
  expect(await screen.findByText(transcript.messages[1].verbatim)).toBeTruthy();

  await act(async () => setUiLanguage("en"));
  expect(
    screen.getByRole("textbox", { name: "Search conversation history" }),
  ).toBeTruthy();
  expect(
    screen.getByRole("region", { name: "Conversation search results" }),
  ).toBeTruthy();
  expect(screen.getByText(transcript.messages[1].verbatim)).toBeTruthy();
  expect(screen.getAllByText(found.hits[0].excerpt)).toHaveLength(2);
  expect(screen.getByText("Original user message")).toBeTruthy();
  expect(
    screen.getByRole("button", { name: "Close original conversation" }),
  ).toBeTruthy();
});

it("translates an existing search error without exposing its internal code", async () => {
  const user = userEvent.setup();
  searchConversations.mockRejectedValueOnce(
    new Error("conversation_search_unavailable"),
  );
  render(<ConversationSearch />);
  await user.type(
    screen.getByRole("textbox", { name: "搜索历史对话" }),
    "打包",
  );
  await user.click(screen.getByRole("button", { name: "搜索" }));
  expect((await screen.findByRole("alert")).textContent).toContain(
    "搜索索引不可用",
  );
  await act(async () => setUiLanguage("en"));
  expect(screen.getByRole("alert").textContent).toContain(
    "search index is unavailable",
  );
  expect(screen.getByRole("alert").textContent).not.toContain(
    "conversation_search_unavailable",
  );
});

it("lists recent conversations before the user searches", async () => {
  getConversationBrowser.mockResolvedValue({
    items: [
      {
        evidence_id: "recent-1",
        conversation_id: "conversation-recent",
        source_id: "workbuddy",
        workspace: null,
        project: "One Memory",
        title: "最近的一段真实对话",
        latest_at: "2026-10-04T08:00:00Z",
        turn_count: 3,
        memory_count: 2,
      },
    ],
    total: 1,
    offset: 0,
    limit: 50,
  });

  render(<ConversationSearch />);

  expect(await screen.findByText("最近的一段真实对话")).toBeTruthy();
  expect(searchConversations).not.toHaveBeenCalled();
});

it("shows memories associated with the selected conversation", async () => {
  const onOpenMemory = vi.fn();
  getConversationMemories.mockResolvedValue({
    items: [
      {
        memory_id: "memory-1",
        user_id: "local",
        kind: "chat",
        status: "confirmed",
        content: "发布前需要确认签名和公证。",
        scopes: ["all-apps"],
        origin: "hy",
        tags: [],
        revision: 1,
        created_at: "2026-10-04T08:00:00Z",
        updated_at: "2026-10-04T08:00:00Z",
        deleted_at: null,
        undo_cursor_revision: null,
      },
    ],
  });
  const user = await search("抽取终于", { onOpenMemory });
  await openA(user);

  await user.click(
    await screen.findByRole("button", { name: /发布前需要确认签名和公证/ }),
  );
  expect(onOpenMemory).toHaveBeenCalledWith("memory-1");
});

it("filters the conversation list by source", async () => {
  getConversationBrowser.mockResolvedValueOnce({
    items: [
      {
        evidence_id: "cursor-1",
        conversation_id: "c1",
        source_id: "cursor",
        workspace: null,
        project: null,
        title: "Cursor 会话",
        latest_at: "2026-10-04T08:00:00Z",
        turn_count: 2,
        memory_count: 0,
      },
      {
        evidence_id: "workbuddy-1",
        conversation_id: "w1",
        source_id: "workbuddy",
        workspace: null,
        project: null,
        title: "WorkBuddy 会话",
        latest_at: "2026-10-04T08:01:00Z",
        turn_count: 3,
        memory_count: 1,
      },
    ],
    total: 2,
    offset: 0,
    limit: 50,
  });
  getConversationBrowser.mockResolvedValueOnce({
    items: [],
    total: 0,
    offset: 0,
    limit: 50,
  });
  const user = userEvent.setup();
  render(<ConversationSearch />);
  await screen.findByText("Cursor 会话");

  await user.selectOptions(screen.getByLabelText("应用"), "cursor");

  await waitFor(() =>
    expect(getConversationBrowser).toHaveBeenLastCalledWith({
      sourceId: "cursor",
    }),
  );
});

it("loads older complete turns into the middle column", async () => {
  getConversation.mockResolvedValueOnce({
    ...transcript,
    total_turns: 25,
    offset: 0,
    limit: 20,
    has_more: true,
  });
  getConversation.mockResolvedValueOnce({
    ...transcript,
    messages: [
      {
        ...transcript.messages[0],
        evidence_id: "older-user",
        verbatim: "更早的一轮",
        turn_index: 1,
      },
    ],
    total_turns: 25,
    offset: 20,
    limit: 20,
    has_more: false,
  });
  const user = await search();
  await openA(user);
  await user.click(
    await screen.findByRole("button", { name: "加载更早的对话" }),
  );

  expect(await screen.findByText("更早的一轮")).toBeTruthy();
  expect(getConversation).toHaveBeenLastCalledWith("evidence-1", {
    offset: 20,
    limit: 20,
  });
});

it("opens a search hit on the page containing its matching turn", async () => {
  searchConversations.mockResolvedValue({
    ...found,
    hits: [{ ...found.hits[0], turn_offset: 40 }],
  });
  const user = await search();
  await openA(user);

  await waitFor(() =>
    expect(getConversation).toHaveBeenCalledWith("evidence-1", {
      offset: 40,
      limit: 20,
    }),
  );
});

it("以原始对话的名义给出结果，不冒充记忆", async () => {
  await search();

  await waitFor(() => {
    expect(
      screen.getByText("昨天晚上重新打了一个包，抽取终于跑通了"),
    ).toBeTruthy();
  });
  // The wording carries the whole distinction: these are the user's own
  // sentences, not anything the product concluded about them.
  expect(screen.getByRole("region", { name: "历史对话搜索结果" })).toBeTruthy();
  expect(screen.getByText(/原始对话/)).toBeTruthy();
  expect(screen.queryByText(/已生成记忆/)).toBeNull();
});

it("检索正常但没有匹配时，说没有找到相关对话", async () => {
  searchConversations.mockResolvedValue({
    hits: [],
    empty_reason: "no_matches",
    imported_messages: 6740,
  });

  await search("量子纠缠");

  await waitFor(() => {
    expect(screen.getByText("没有找到相关对话")).toBeTruthy();
  });
});

it("还没导入任何历史时，说先导入历史", async () => {
  searchConversations.mockResolvedValue({
    hits: [],
    empty_reason: "nothing_imported",
    imported_messages: 0,
  });

  await search();

  await waitFor(() => {
    expect(screen.getByText("先导入历史，然后就能搜了")).toBeTruthy();
  });
  expect(screen.queryByText("没有找到相关对话")).toBeNull();
});

it("检索本身坏掉时明确报错，不伪装成没有找到", async () => {
  // The shape last night's packaging bug took: a core that could not search
  // at all was indistinguishable, on screen, from a user who had never said
  // the thing they were looking for.
  searchConversations.mockRejectedValue(
    new Error("conversation_search_unavailable"),
  );

  await search();

  await waitFor(() => {
    expect(screen.getByRole("alert")).toBeTruthy();
  });
  expect(screen.getByRole("alert").textContent).toContain("搜索索引不可用");
  expect(screen.queryByText("没有找到相关对话")).toBeNull();
});

it("点开一条结果就地展开它的原文", async () => {
  const user = await search();

  await waitFor(() => {
    expect(screen.getByRole("button", { name: /查看原文/ })).toBeTruthy();
  });
  await user.click(screen.getByRole("button", { name: /查看原文/ }));

  await waitFor(() => {
    expect(screen.getByText("那就先把包发出去")).toBeTruthy();
  });
  expect(getConversation).toHaveBeenCalledWith("evidence-1");
});

it("explains model configuration before generating memory", () => {
  render(<ConversationSearch />);
  expect(screen.getByText(/设置模型后，才会开始生成记忆/)).toBeTruthy();
  expect(screen.queryByText(/配好生成模型之后/)).toBeNull();
});

it("shows every original author and timestamp without inferring person from role=user", async () => {
  const verbatim = "  保留空格\n第二行 <原文>\n\n最后一行  ";
  getConversation.mockResolvedValue({
    ...transcript,
    messages: [
      { ...transcript.messages[0], verbatim },
      transcript.messages[1],
      {
        evidence_id: "unknown",
        role: "user",
        written_by: "unknown",
        created_at: null,
        verbatim: "用户角色但作者未知",
      },
      {
        evidence_id: "machine",
        role: "user",
        written_by: "agent",
        created_at: "not-a-time",
        verbatim: "用户角色的机器提醒",
      },
      {
        evidence_id: "tool",
        role: "tool",
        written_by: "tool",
        created_at: null,
        verbatim: "工具原始记录",
      },
    ],
  });
  const user = await search();
  await openA(user);
  const region = await screen.findByRole("region", { name: "完整原始对话" });
  const items = within(region).getAllByRole("listitem");
  expect(items).toHaveLength(1);
  expect(items[0].querySelector("p")?.textContent).toBe(verbatim);
  expect(within(items[0]).getByText("用户原话")).toBeTruthy();
  expect(items[0].querySelector("time")?.dateTime).toBe(
    transcript.messages[0].created_at,
  );
  expect(within(items[0]).getAllByText("助手原话")).toHaveLength(2);
  expect(within(items[0]).getByText("作者未知")).toBeTruthy();
  expect(within(items[0]).getByText("工具记录")).toBeTruthy();
  expect(within(region).getAllByText("时间未知")).toHaveLength(2);
  expect(region.textContent).not.toContain("not-a-time");
});

it.each([null, "invalid-time"])(
  "shows unknown original hit time for %s",
  async (created_at) => {
    searchConversations.mockResolvedValue({
      ...found,
      hits: [{ ...found.hits[0], created_at }],
    });
    await search();
    expect(
      within(hitArticle(found.hits[0].excerpt)).getByText("时间未知"),
    ).toBeTruthy();
  },
);

it("A resolving after B cannot display A's original text under B", async () => {
  const a = deferred<ConversationTranscript>(),
    b = deferred<ConversationTranscript>();
  searchConversations.mockResolvedValue({
    ...found,
    hits: [found.hits[0], secondHit],
  });
  getConversation.mockImplementation((id) =>
    id === "evidence-1" ? a.promise : b.promise,
  );
  const user = await search();
  await openA(user);
  await openB(user);
  await act(async () => b.resolve(secondTranscript));
  expect(screen.getByText("B 的完整原文")).toBeTruthy();
  await act(async () => a.resolve(transcript));
  expect(screen.getByText("B 的完整原文")).toBeTruthy();
  expect(screen.queryByText("那就先把包发出去")).toBeNull();
});

it("an obsolete A error never replaces B's success", async () => {
  const a = deferred<ConversationTranscript>();
  searchConversations.mockResolvedValue({
    ...found,
    hits: [found.hits[0], secondHit],
  });
  getConversation.mockImplementation((id) =>
    id === "evidence-1" ? a.promise : Promise.resolve(secondTranscript),
  );
  const user = await search();
  await openA(user);
  await openB(user);
  await screen.findByText("B 的完整原文");
  await act(async () => a.reject(new Error("source_not_authorized")));
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.getByText("B 的完整原文")).toBeTruthy();
});

it("collapse and reopen invalidates a late response for the same hit", async () => {
  const first = deferred<ConversationTranscript>(),
    second = deferred<ConversationTranscript>();
  getConversation
    .mockReturnValueOnce(first.promise)
    .mockReturnValueOnce(second.promise);
  const user = await search();
  await openA(user);
  await user.click(screen.getByRole("button", { name: "收起原文" }));
  await openA(user);
  await act(async () => first.resolve(transcript));
  expect(screen.queryByText("那就先把包发出去")).toBeNull();
  expect(screen.getByText("正在读取原文…")).toBeTruthy();
  await act(async () =>
    second.resolve({
      ...transcript,
      messages: [{ ...transcript.messages[0], verbatim: "新读取的原文" }],
    }),
  );
  expect(screen.getByText("新读取的原文")).toBeTruthy();
});

it("collapsing discards a late rejected read without leaving an error or loading state", async () => {
  const pending = deferred<ConversationTranscript>();
  getConversation.mockReturnValue(pending.promise);
  const user = await search();
  await openA(user);
  await user.click(screen.getByRole("button", { name: "收起原文" }));
  await act(async () => pending.reject(new Error("offline")));
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.queryByText("正在读取原文…")).toBeNull();
  expect(
    screen
      .getByRole("button", { name: "查看原文" })
      .getAttribute("aria-expanded"),
  ).toBe("false");
});

it("new search removes old hits immediately and invalidates pending original-text reads", async () => {
  const reading = deferred<ConversationTranscript>(),
    searching = deferred<ConversationSearchResponse>();
  getConversation.mockReturnValue(reading.promise);
  const user = await search();
  await openA(user);
  searchConversations.mockReturnValueOnce(searching.promise);
  await user.clear(screen.getByLabelText("搜索历史对话"));
  await user.type(screen.getByLabelText("搜索历史对话"), "第二次");
  await user.click(screen.getByRole("button", { name: "搜索" }));
  expect(screen.queryByText(found.hits[0].excerpt)).toBeNull();
  await act(async () => reading.resolve(transcript));
  await act(async () => searching.resolve({ ...found, hits: [secondHit] }));
  expect(screen.queryByText("那就先把包发出去")).toBeNull();
  expect(screen.getByText(secondHit.excerpt)).toBeTruthy();
});

it("a later search wins over an older success or failure while duplicate submits stay single-flight", async () => {
  const a = deferred<ConversationSearchResponse>(),
    b = deferred<ConversationSearchResponse>();
  searchConversations
    .mockReturnValueOnce(a.promise)
    .mockReturnValueOnce(b.promise);
  const view = render(<ConversationSearch />);
  const input = screen.getByLabelText("搜索历史对话");
  const form = view.container.querySelector("form");
  if (!form) throw new Error("missing search form");
  fireEvent.change(input, { target: { value: "first" } });
  act(() => {
    fireEvent.submit(form);
    fireEvent.submit(form);
  });
  expect(searchConversations).toHaveBeenCalledTimes(1);
  fireEvent.change(input, { target: { value: "second" } });
  fireEvent.submit(form);
  await act(async () => b.resolve({ ...found, hits: [secondHit] }));
  await act(async () => a.reject(new Error("conversation_search_unavailable")));
  expect(screen.getByText(secondHit.excerpt)).toBeTruthy();
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.queryByRole("button", { name: "搜索中…" })).toBeNull();
});

it("read errors stop loading and expose an inline retry for the same hit", async () => {
  getConversation
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValueOnce(transcript);
  const user = await search();
  await openA(user);
  expect((await screen.findByRole("alert")).textContent).toContain(
    "原文读取未完成",
  );
  expect(screen.queryByText("正在读取原文…")).toBeNull();
  expect(screen.getByRole("button", { name: "重试读取原文" })).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "重试读取原文" }));
  expect(await screen.findByText("那就先把包发出去")).toBeTruthy();
  expect(getConversation.mock.calls).toEqual([["evidence-1"], ["evidence-1"]]);
});

it("a double retry submits one read and new failures remain retryable", async () => {
  const pending = deferred<ConversationTranscript>();
  getConversation
    .mockRejectedValueOnce(null)
    .mockReturnValueOnce(pending.promise);
  const user = await search();
  await openA(user);
  const retry = await screen.findByRole("button", { name: "重试读取原文" });
  act(() => {
    fireEvent.click(retry);
    fireEvent.click(retry);
  });
  expect(getConversation).toHaveBeenCalledTimes(2);
  await act(async () => pending.reject(new Error("again")));
  expect(screen.getByRole("button", { name: "重试读取原文" })).toBeTruthy();
  expect(screen.queryByText("正在读取原文…")).toBeNull();
});

it.each([
  "source_not_authorized",
  "conversation_not_found",
  "invalid_local_token",
])(
  "%s clears cached excerpts and original text, then allows a fresh search",
  async (code) => {
    searchConversations.mockResolvedValue({
      ...found,
      hits: [found.hits[0], { ...secondHit, source_id: "cursor" }],
    });
    const user = await search();
    await openA(user);
    await screen.findByText("那就先把包发出去");
    getConversation.mockRejectedValueOnce(new Error(code));
    await openB(user);
    await screen.findByRole("alert");
    expect(screen.queryByText(found.hits[0].excerpt)).toBeNull();
    expect(screen.queryByText(secondHit.excerpt)).toBeNull();
    expect(screen.queryByText("那就先把包发出去")).toBeNull();
    expect(screen.queryByText("正在读取原文…")).toBeNull();
    await user.click(screen.getByRole("button", { name: "重新搜索" }));
    expect(searchConversations).toHaveBeenCalledTimes(2);
  },
);

it("search permission rejection clears all prior original contents", async () => {
  const user = await search();
  await openA(user);
  await screen.findByText("那就先把包发出去");
  searchConversations.mockRejectedValueOnce(new Error("source_not_authorized"));
  await user.click(screen.getByRole("button", { name: "搜索" }));
  await screen.findByRole("alert");
  expect(screen.queryByText(found.hits[0].excerpt)).toBeNull();
  expect(screen.queryByText("那就先把包发出去")).toBeNull();
});

it.each([
  { ...transcript, source_id: "other" },
  { ...transcript, conversation_id: "other" },
  {
    ...transcript,
    messages: [{ ...transcript.messages[1], evidence_id: "not-requested" }],
  },
])(
  "rejects a transcript with a mismatched response identity",
  async (response) => {
    getConversation.mockResolvedValue(response);
    const user = await search();
    await openA(user);
    expect((await screen.findByRole("alert")).textContent).toContain(
      "原文读取未完成",
    );
    expect(screen.queryByText("那就先把包发出去")).toBeNull();
    expect(screen.queryByText("正在读取原文…")).toBeNull();
  },
);

it("点开结果后滚动到命中的那条消息并标出命中的词", async () => {
  const user = await search();
  await openA(user);

  const marked = await screen.findByText("抽取终于", { selector: "mark" });
  const hit = marked.closest("section[data-match]");
  expect(hit?.getAttribute("data-match")).toBe("true");
  expect(hit?.textContent).toContain("昨天晚上重新打了一个包");
  expect(scrollIntoView).toHaveBeenCalledTimes(1);
  expect(scrollIntoView.mock.instances[0]).toBe(hit);
  // The rest of the transcript is shown as plain text, not as a hit.
  expect(
    screen.queryByText("那就先把包发出去", { selector: "mark" }),
  ).toBeNull();
});

it("unmount invalidates both search and original requests without another API call", async () => {
  const reading = deferred<ConversationTranscript>();
  getConversation.mockReturnValue(reading.promise);
  const user = await search();
  await openA(user);
  cleanup();
  await act(async () => reading.reject(new Error("source_not_authorized")));
  expect(document.body.textContent).toBe("");
  const searching = deferred<ConversationSearchResponse>();
  searchConversations.mockReturnValueOnce(searching.promise);
  const view = render(<ConversationSearch />);
  fireEvent.change(screen.getByLabelText("搜索历史对话"), {
    target: { value: "late" },
  });
  const form = view.container.querySelector("form");
  if (!form) throw new Error("missing search form");
  fireEvent.submit(form);
  view.unmount();
  await act(async () => searching.resolve(found));
  expect(screen.queryByText(found.hits[0].excerpt)).toBeNull();
  expect(getConversation).toHaveBeenCalledTimes(1);
});

describe("BUG-06: a turn that is still waiting for its answer", () => {
  it("says so on the turn, and that it is left out of extraction", async () => {
    getConversation.mockResolvedValueOnce({
      ...transcript,
      trailing_turn_unanswered: true,
    });
    const user = await search();
    await openA(user);
    expect(
      await screen.findByText("这一回合还没等到回答，暂不参与记忆整理。"),
    ).toBeTruthy();
  });

  it("says nothing when the turn got its answer", async () => {
    const user = await search();
    await openA(user);
    await screen.findByText("那就先把包发出去");
    expect(
      screen.queryByText("这一回合还没等到回答，暂不参与记忆整理。"),
    ).toBeNull();
  });
});
