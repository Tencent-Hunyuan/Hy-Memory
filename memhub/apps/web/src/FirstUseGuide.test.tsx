// @vitest-environment jsdom
import type { MemoryDetail, SourceResponse } from "@one-memory/contracts";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FirstUseGuide, FirstUseInvitation } from "./FirstUseGuide";
import { setUiLanguage } from "./ui-language";

const getMemories = vi.fn(),
  getMemory = vi.fn(),
  getSources = vi.fn(),
  copyText = vi.fn(),
  openHandoff = vi.fn();
vi.mock("./api", () => ({
  getMemories: (...args: unknown[]) => getMemories(...args),
  getMemory: (...args: unknown[]) => getMemory(...args),
  getSources: () => getSources(),
}));
vi.mock("./copy-text", () => ({ copyText: (text: string) => copyText(text) }));
const source = {
  source_id: "cursor",
  display_name: "Cursor",
  authorized: true,
} as SourceResponse;
const detail: MemoryDetail = {
  memory: {
    memory_id: "m1",
    user_id: "local",
    content: "周会避开周五",
    kind: "chat",
    status: "confirmed",
    origin: "extracted",
    scopes: ["all-apps"],
    tags: ["项目周会"],
    evidence_ids: ["e1"],
    revision: 1,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    deleted_at: null,
  },
  evidence: [
    {
      evidence_id: "e1",
      source_id: "cursor",
      written_by: "agent",
      role: "assistant",
      created_at: null,
      verbatim: "建议把周会安排在其他工作日。",
      available: true,
    },
  ],
  history: [],
  index_sync: null,
};
const second: MemoryDetail = {
  ...detail,
  memory: {
    ...detail.memory,
    memory_id: "m2",
    content: "第二条背景",
    evidence_ids: ["e2"],
  },
  evidence: [{ ...detail.evidence[0], evidence_id: "e2", source_id: "codex" }],
};
beforeEach(() => {
  vi.resetAllMocks();
  getMemories.mockResolvedValue([detail.memory, second.memory]);
  getMemory.mockImplementation(async (id: string) =>
    id === "m1" ? detail : second,
  );
  getSources.mockResolvedValue([source]);
  copyText.mockResolvedValue(undefined);
  openHandoff.mockResolvedValue({ opened: true });
  window.oneMemory = {
    setUiLanguage: vi.fn(async (next: "zh-CN" | "en") => next),
    openHandoff,
    getRecallHosts: vi.fn().mockResolvedValue([
      {
        hostId: "cursor",
        label: "Cursor",
        present: true,
        installed: true,
        handoff: "open",
        selfCheck: { state: "wired" },
      },
      {
        hostId: "codex",
        label: "Codex",
        present: true,
        installed: false,
        handoff: "copy",
        selfCheck: null,
      },
    ]),
  } as unknown as Window["oneMemory"];
});
afterEach(async () => {
  cleanup();
  delete window.oneMemory;
  await setUiLanguage("zh-CN");
});
function show() {
  return render(
    <FirstUseGuide sources={[source]} onClose={vi.fn()} onSources={vi.fn()} />,
  );
}
async function ready() {
  await waitFor(() =>
    expect(
      (screen.getByLabelText("开场白完整预览") as HTMLTextAreaElement).value,
    ).toContain("周五"),
  );
}

describe("first-use guide", () => {
  it("switches the fixed context prompt without changing the topic, memory or original evidence", async () => {
    getMemory.mockResolvedValue({
      ...detail,
      evidence: [{ ...detail.evidence[0], created_at: "2026-09-01T12:00:00Z" }],
    });
    const { container } = render(
      <FirstUseGuide
        sources={[{ ...source, imported_messages: 1234 }]}
        onClose={vi.fn()}
        onSources={vi.fn()}
      />,
    );
    await ready();
    const draft = (
      screen.getByLabelText("开场白完整预览") as HTMLTextAreaElement
    ).value;
    expect(draft).toContain("我在新会话里接着之前的工作继续。");
    await act(async () => {
      await setUiLanguage("en");
    });
    expect(
      screen.getByRole("heading", {
        name: "Pick up where your last conversation left off",
      }),
    ).toBeTruthy();
    expect(screen.getByText(/1,234 original messages saved/)).toBeTruthy();
    expect(screen.getByText("建议把周会安排在其他工作日。")).toBeTruthy();
    expect(
      container.querySelector(".first-use-evidence header span:last-child")
        ?.textContent,
    ).toContain(new Date("2026-09-01T12:00:00Z").toLocaleString("en"));
    const englishDraft = (
      screen.getByLabelText("Full opening preview") as HTMLTextAreaElement
    ).value;
    expect(englishDraft).not.toBe(draft);
    expect(englishDraft).toContain(
      "I am continuing earlier work in a new conversation.",
    );
    expect(englishDraft).toContain("Background on “项目周会” from MemHub");
    expect(englishDraft).toContain("周会避开周五");
    expect(englishDraft).toContain("Scope (not expanded): all-apps");
    expect(englishDraft).not.toContain("我在新会话里");
    expect(copyText).not.toHaveBeenCalled();
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Copy opening" }));
    await waitFor(() => expect(copyText).toHaveBeenCalledWith(englishDraft));
  });
  it("clears an old preview if the current memory read is denied", async () => {
    const user = userEvent.setup();
    show();
    await ready();
    getMemory.mockRejectedValue(new Error("access revoked"));
    await user.click(screen.getByRole("button", { name: "复制开场白" }));
    expect(copyText).not.toHaveBeenCalled();
    expect(screen.getByLabelText("开场白完整预览")).toHaveProperty("value", "");
  });
  it("opens without a cloud request, auto copy or launch; keeps author and unknown time", async () => {
    show();
    await ready();
    expect(screen.getByText(/Agent · 时间未知/)).toBeTruthy();
    expect(screen.getByText(/系统整理，需你核对/)).toBeTruthy();
    expect(copyText).not.toHaveBeenCalled();
    expect(openHandoff).not.toHaveBeenCalled();
  });
  it("keeps all three scenarios optional and filters destinations", async () => {
    const user = userEvent.setup();
    show();
    await ready();
    expect(screen.queryByRole("option", { name: "Codex" })).toBeNull();
    await user.click(screen.getByRole("radio", { name: /换个应用继续/ }));
    expect(screen.getByRole("option", { name: "Codex" })).toBeTruthy();
    expect(screen.queryByRole("option", { name: "Cursor" })).toBeNull();
    await user.click(screen.getByRole("radio", { name: /让新工具了解我/ }));
    expect(screen.getByRole("option", { name: "Cursor" })).toBeTruthy();
    expect(
      (screen.getByLabelText("开场白完整预览") as HTMLTextAreaElement).value,
    ).toContain("不代表完整个人画像");
  });
  it("only enables query for a connected destination, and excludes the answer", async () => {
    const user = userEvent.setup();
    show();
    await ready();
    expect(screen.getByRole("radio", { name: "让应用查询" })).toHaveProperty(
      "disabled",
      true,
    );
    await user.selectOptions(screen.getByLabelText("在哪个应用试用"), "cursor");
    await user.click(screen.getByRole("radio", { name: "让应用查询" }));
    expect(
      (screen.getByLabelText("开场白完整预览") as HTMLTextAreaElement).value,
    ).not.toContain("周五");
    await user.click(screen.getByRole("button", { name: "复制开场白" }));
    expect(copyText.mock.calls[0][0]).not.toContain("周五");
    expect(screen.getByText(/尚无本次外部调用回执/)).toBeTruthy();
  });
  it("keeps an edited query topic when switching languages and copies only the English query", async () => {
    const user = userEvent.setup();
    show();
    await ready();
    await user.selectOptions(screen.getByLabelText("在哪个应用试用"), "cursor");
    await user.click(screen.getByRole("radio", { name: "让应用查询" }));
    await user.clear(screen.getByLabelText("要接续的主题"));
    await user.type(screen.getByLabelText("要接续的主题"), "用户自定主题");
    const chineseDraft = (
      screen.getByLabelText("开场白完整预览") as HTMLTextAreaElement
    ).value;
    expect(chineseDraft).toContain("与「用户自定主题」相关");
    await act(async () => {
      await setUiLanguage("en");
    });
    expect(screen.getByLabelText("Topic to continue")).toHaveProperty(
      "value",
      "用户自定主题",
    );
    const englishDraft = (
      screen.getByLabelText("Full opening preview") as HTMLTextAreaElement
    ).value;
    expect(englishDraft).toContain("related to “用户自定主题”");
    expect(englishDraft).toContain("sources and citations actually returned");
    expect(englishDraft).not.toContain("请先用 MemHub");
    expect(englishDraft).not.toContain("周会避开周五");
    expect(englishDraft).not.toContain("建议把周会安排在其他工作日。");
    await user.click(screen.getByRole("button", { name: "Copy opening" }));
    await waitFor(() => expect(copyText).toHaveBeenCalledWith(englishDraft));
    expect(openHandoff).not.toHaveBeenCalled();
    await act(async () => {
      await setUiLanguage("zh-CN");
    });
    expect(screen.getByLabelText("要接续的主题")).toHaveProperty(
      "value",
      "用户自定主题",
    );
    expect(screen.getByLabelText("开场白完整预览")).toHaveProperty(
      "value",
      chineseDraft,
    );
  });
  it("keeps the new draft copyable when language changes during an old recheck", async () => {
    const user = userEvent.setup();
    show();
    await ready();
    let finishOld: (data: MemoryDetail) => void = () => undefined;
    getMemory.mockImplementationOnce(
      () =>
        new Promise<MemoryDetail>((resolve) => {
          finishOld = resolve;
        }),
    );
    await user.click(screen.getByRole("button", { name: "复制开场白" }));
    await act(async () => {
      await setUiLanguage("en");
    });
    const englishDraft = (
      screen.getByLabelText("Full opening preview") as HTMLTextAreaElement
    ).value;
    expect(englishDraft).toContain("Scope (not expanded)");
    expect(screen.getByRole("button", { name: "Copy opening" })).toHaveProperty(
      "disabled",
      false,
    );
    await act(async () => finishOld(detail));
    expect(copyText).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Copy opening" }));
    await waitFor(() => expect(copyText).toHaveBeenCalledWith(englishDraft));
  });
  it("does not turn copy or user feedback into an automatic adoption pass", async () => {
    const user = userEvent.setup();
    show();
    await ready();
    await user.click(screen.getByRole("button", { name: "复制开场白" }));
    expect(getMemory).toHaveBeenCalledTimes(2);
    expect(getSources).toHaveBeenCalledOnce();
    expect(screen.getByRole("status").textContent).toContain("尚未发送");
    await user.click(screen.getByRole("button", { name: "我已核对结果" }));
    expect(screen.getByText(/这不是系统自动验收记录/)).toBeTruthy();
  });
  it.each(["revision", "revocation", "evidence"] as const)(
    "rechecks %s before copying",
    async (kind) => {
      const user = userEvent.setup();
      show();
      await ready();
      if (kind === "revision")
        getMemory.mockResolvedValue({
          ...detail,
          memory: { ...detail.memory, revision: 2 },
        });
      if (kind === "revocation")
        getSources.mockResolvedValue([{ ...source, authorized: false }]);
      if (kind === "evidence")
        getMemory.mockResolvedValue({
          ...detail,
          evidence: [{ ...detail.evidence[0], verbatim: "新的原文" }],
        });
      await user.click(screen.getByRole("button", { name: "复制开场白" }));
      expect(copyText).not.toHaveBeenCalled();
      expect(openHandoff).not.toHaveBeenCalled();
      expect(screen.getByRole("alert").textContent).toContain("授权已变化");
      expect(screen.getByLabelText("开场白完整预览")).toHaveProperty(
        "value",
        "",
      );
    },
  );
  it("ignores a late detail response after selecting another memory", async () => {
    let first: (data: MemoryDetail) => void = () => undefined;
    getMemory.mockImplementation((id: string) =>
      id === "m1"
        ? new Promise((resolve) => {
            first = resolve;
          })
        : Promise.resolve(second),
    );
    const user = userEvent.setup();
    show();
    await screen.findByLabelText("选择一条记忆");
    await user.selectOptions(screen.getByLabelText("选择一条记忆"), "m2");
    await waitFor(() =>
      expect(
        (screen.getByLabelText("开场白完整预览") as HTMLTextAreaElement).value,
      ).toContain("第二条背景"),
    );
    await act(async () => first(detail));
    expect(
      (screen.getByLabelText("开场白完整预览") as HTMLTextAreaElement).value,
    ).not.toContain("周五");
  });
  it("does not launch after unmount while the pre-copy check is pending", async () => {
    let resolve: (data: MemoryDetail) => void = () => undefined;
    const user = userEvent.setup();
    const view = show();
    await ready();
    getMemory.mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    await user.selectOptions(screen.getByLabelText("在哪个应用试用"), "cursor");
    await user.click(screen.getByRole("button", { name: "复制并打开 Cursor" }));
    view.unmount();
    await act(async () => resolve(detail));
    expect(copyText).not.toHaveBeenCalled();
    expect(openHandoff).not.toHaveBeenCalled();
  });
  it("does not launch or claim success when the clipboard fails", async () => {
    copyText.mockRejectedValue(new Error("denied"));
    const user = userEvent.setup();
    show();
    await ready();
    await user.selectOptions(screen.getByLabelText("在哪个应用试用"), "cursor");
    await user.click(screen.getByRole("button", { name: "复制并打开 Cursor" }));
    expect(openHandoff).not.toHaveBeenCalled();
    expect(screen.getByRole("status").textContent).toContain("未能核对");
    expect(screen.queryByRole("button", { name: "我已核对结果" })).toBeNull();
  });
  it("keeps the empty state truthful and retryable", async () => {
    getMemories.mockResolvedValue([]);
    show();
    expect(await screen.findByText("先留下一条真正有用的背景")).toBeTruthy();
    expect(screen.queryByLabelText("开场白完整预览")).toBeNull();
    expect(screen.getByRole("button", { name: "查看导入与连接" })).toBeTruthy();
  });
  it("refuses missing evidence without replacing it with invented content", async () => {
    getMemory.mockResolvedValue({ ...detail, evidence: [] });
    show();
    await screen.findByText(/原文或当前授权不可用/);
    expect(screen.getByRole("button", { name: "复制开场白" })).toHaveProperty(
      "disabled",
      true,
    );
  });
  it("dismisses the invitation without changing jobs, memory or local storage", async () => {
    const user = userEvent.setup(),
      onDismiss = vi.fn(),
      onOpen = vi.fn();
    render(<FirstUseInvitation onDismiss={onDismiss} onOpen={onOpen} />);
    await user.click(screen.getByRole("button", { name: "关闭上手邀请" }));
    expect(onDismiss).toHaveBeenCalledOnce();
    expect(onOpen).not.toHaveBeenCalled();
    expect(getMemories).not.toHaveBeenCalled();
  });
});
