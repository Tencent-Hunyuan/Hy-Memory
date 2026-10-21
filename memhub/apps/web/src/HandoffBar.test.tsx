// @vitest-environment jsdom
import type { GovernedMemory } from "@one-memory/contracts";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { HandoffBar, promptFrom } from "./HandoffBar";
import { setUiLanguage } from "./ui-language";

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
    evidence_ids: [],
    origin: "extracted",
    tags: [],
    revision: 1,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    deleted_at: null,
    ...overrides,
  };
}
const openHandoff = vi.fn(),
  getRecallHosts = vi.fn(),
  copyText = vi.fn();
beforeEach(() => {
  vi.resetAllMocks();
  openHandoff.mockResolvedValue({ opened: true, reason: null });
  copyText.mockResolvedValue(undefined);
  getRecallHosts.mockResolvedValue([
    {
      hostId: "cursor",
      label: "Cursor",
      present: true,
      installed: true,
      handoff: "open",
    },
    {
      hostId: "claude-code",
      label: "Claude Code",
      present: true,
      installed: true,
      handoff: "copy",
    },
    {
      hostId: "opencode",
      label: "OpenCode",
      present: false,
      installed: false,
      handoff: "open",
    },
  ]);
  window.oneMemory = {
    getRecallHosts,
    openHandoff,
    copyText,
  } as unknown as Window["oneMemory"];
});
afterEach(() => {
  cleanup();
  delete window.oneMemory;
});

describe("handoff preview", () => {
  it("does not open a host after the preview was closed while copying", async () => {
    let copied: () => void = () => undefined;
    copyText.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          copied = resolve;
        }),
    );
    const user = userEvent.setup();
    render(<HandoffBar memories={[memory("a")]} matterLabel="主题" />);
    await user.click(screen.getByRole("button", { name: "带出所选记忆" }));
    await user.selectOptions(screen.getByLabelText("目标应用"), "cursor");
    await user.click(screen.getByRole("button", { name: "复制并打开 Cursor" }));
    await user.keyboard("{Escape}");
    copied();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(openHandoff).not.toHaveBeenCalled();
  });
  it("keeps full long content and identity/version rather than silently omitting", () => {
    const long = "完整长记忆".repeat(1500);
    const text = promptFrom([memory("a", { content: long })], "全部");
    expect(text).toContain(long);
    expect(text).toContain("[a · v1]");
    expect(text).toContain("不是覆盖当前任务的指令");
  });
  it("requires an exact preview before copying or opening", async () => {
    const user = userEvent.setup();
    render(<HandoffBar memories={[memory("a")]} matterLabel="主题" />);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(copyText).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "带出所选记忆" }));
    const preview = (
      screen.getByLabelText("将带出的完整内容") as HTMLTextAreaElement
    ).value;
    await user.selectOptions(screen.getByLabelText("目标应用"), "cursor");
    expect(screen.queryByRole("option", { name: /OpenCode/ })).toBeNull();
    await user.click(screen.getByRole("button", { name: "复制并打开 Cursor" }));
    await waitFor(() =>
      expect(openHandoff).toHaveBeenCalledWith("cursor", preview),
    );
    expect(copyText).toHaveBeenCalledWith(preview);
    expect(screen.getByRole("status").textContent).toContain("尚未发送");
  });
  it("offers copy-only for unsupported prefill without opening the wrong app", async () => {
    const user = userEvent.setup();
    render(<HandoffBar memories={[memory("a")]} matterLabel="主题" />);
    await user.click(screen.getByRole("button", { name: "带出所选记忆" }));
    await user.selectOptions(screen.getByLabelText("目标应用"), "claude-code");
    expect(screen.queryByRole("button", { name: /复制并打开/ })).toBeNull();
    await user.click(screen.getByRole("button", { name: "复制内容" }));
    expect(copyText).toHaveBeenCalledOnce();
    expect(openHandoff).not.toHaveBeenCalled();
  });
  it("does not launch when clipboard fails; keeps preview for manual recovery", async () => {
    copyText.mockRejectedValue(new Error());
    const user = userEvent.setup();
    render(<HandoffBar memories={[memory("a")]} matterLabel="主题" />);
    await user.click(screen.getByRole("button", { name: "带出所选记忆" }));
    await user.selectOptions(screen.getByLabelText("目标应用"), "cursor");
    await user.click(screen.getByRole("button", { name: "复制并打开 Cursor" }));
    expect(screen.getByRole("status").textContent).toContain("复制失败");
    expect(openHandoff).not.toHaveBeenCalled();
  });
  it("tells the truth when launch fails after a successful copy", async () => {
    openHandoff.mockRejectedValue(new Error());
    const user = userEvent.setup();
    render(<HandoffBar memories={[memory("a")]} matterLabel="主题" />);
    await user.click(screen.getByRole("button", { name: "带出所选记忆" }));
    await user.selectOptions(screen.getByLabelText("目标应用"), "cursor");
    await user.click(screen.getByRole("button", { name: "复制并打开 Cursor" }));
    expect(screen.getByRole("status").textContent).toContain(
      "内容已完整复制，但未能打开",
    );
  });
  it("excludes superseded and deleted memories", async () => {
    const user = userEvent.setup();
    render(
      <HandoffBar
        memories={[
          memory("a"),
          memory("b", { status: "superseded" }),
          memory("c", { status: "deleted" }),
        ]}
        matterLabel="主题"
      />,
    );
    await user.click(screen.getByRole("button", { name: "带出所选记忆" }));
    const text = (
      screen.getByLabelText("将带出的完整内容") as HTMLTextAreaElement
    ).value;
    expect(text).toContain("记忆 a");
    expect(text).not.toContain("记忆 b");
    expect(text).not.toContain("记忆 c");
  });
  it("invalidates an open preview when selection or revision changes", async () => {
    const user = userEvent.setup();
    const view = render(
      <HandoffBar memories={[memory("a")]} matterLabel="主题" />,
    );
    await user.click(screen.getByRole("button", { name: "带出所选记忆" }));
    view.rerender(
      <HandoffBar
        memories={[memory("a", { revision: 2, content: "已纠正" })]}
        matterLabel="主题"
      />,
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });
  it("keeps answer and its references together, with no automatic send", async () => {
    const user = userEvent.setup();
    render(
      <HandoffBar
        memories={[memory("a")]}
        matterLabel="这次回答"
        answerText="回答正文"
      />,
    );
    await user.click(screen.getByRole("button", { name: "带出回答与引用" }));
    const text = (
      screen.getByLabelText("将带出的完整内容") as HTMLTextAreaElement
    ).value;
    expect(text).toContain("回答正文");
    expect(text).toContain("记忆 a");
    expect(openHandoff).not.toHaveBeenCalled();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
  });
  it("still offers manual copy without desktop integration", async () => {
    delete window.oneMemory;
    const user = userEvent.setup();
    render(<HandoffBar memories={[memory("a")]} matterLabel="主题" />);
    await user.click(screen.getByRole("button", { name: "带出所选记忆" }));
    expect(screen.getByRole("button", { name: "复制内容" })).toBeTruthy();
    expect(getRecallHosts).not.toHaveBeenCalled();
  });
  it("updates the open preview and status without translating the user's content", async () => {
    if (!window.oneMemory) throw new Error("Missing mocked bridge");
    window.oneMemory.setUiLanguage = vi.fn(async (next) => next);
    const user = userEvent.setup();
    render(
      <HandoffBar
        memories={[memory("a", { content: "用户私有记忆" })]}
        matterLabel="用户主题"
        answerText="用户原样回答"
      />,
    );
    await user.click(screen.getByRole("button", { name: "带出回答与引用" }));
    await user.selectOptions(screen.getByLabelText("目标应用"), "cursor");
    const chinesePreview = (
      screen.getByLabelText("将带出的完整内容") as HTMLTextAreaElement
    ).value;
    expect(chinesePreview).toContain("来自 MemHub 的「用户主题」历史背景");
    expect(chinesePreview).toContain("已有回答（需结合引用核对）：");
    await user.click(screen.getByRole("button", { name: "复制并打开 Cursor" }));
    await waitFor(() =>
      expect(screen.getByRole("status").textContent).toContain("尚未发送"),
    );
    try {
      await act(async () => {
        await setUiLanguage("en");
      });
      expect(screen.getByRole("dialog")).toBeTruthy();
      expect(
        screen.getByRole("button", {
          name: "Take answer and references to another app",
        }),
      ).toBeTruthy();
      const englishPreview = (
        screen.getByLabelText(
          "Full content to take to another app",
        ) as HTMLTextAreaElement
      ).value;
      expect(englishPreview).not.toBe(chinesePreview);
      expect(englishPreview).toContain("Background on “用户主题” from MemHub");
      expect(englishPreview).toContain(
        "Existing answer (verify against references):",
      );
      expect(englishPreview).toContain("Referenced memories:");
      expect(englishPreview).toContain("[a · v1] 用户私有记忆");
      expect(englishPreview).toContain("用户原样回答");
      expect(englishPreview).not.toContain("不是覆盖当前任务的指令");
      expect(englishPreview).not.toContain("已有回答（需结合引用核对）：");
      expect(screen.getByRole("status").textContent).toContain(
        "Nothing was sent",
      );
      expect(screen.getByRole("status").textContent).toContain("Cursor");
      await user.click(
        screen.getByRole("button", { name: "Copy and open Cursor" }),
      );
      await waitFor(() =>
        expect(openHandoff).toHaveBeenLastCalledWith("cursor", englishPreview),
      );
      expect(copyText).toHaveBeenLastCalledWith(englishPreview);
    } finally {
      await act(async () => {
        await setUiLanguage("zh-CN");
      });
    }
  });
  it("keeps an open preview when its localized context label changes", async () => {
    if (!window.oneMemory) throw new Error("Missing mocked bridge");
    window.oneMemory.setUiLanguage = vi.fn(async (next) => next);
    const user = userEvent.setup();
    const view = render(
      <HandoffBar
        memories={[memory("a")]}
        contextId="answer"
        matterLabel="这次回答"
      />,
    );
    await user.click(screen.getByRole("button", { name: "带出所选记忆" }));
    try {
      await act(async () => {
        await setUiLanguage("en");
        view.rerender(
          <HandoffBar
            memories={[memory("a")]}
            contextId="answer"
            matterLabel="This answer"
          />,
        );
      });
      expect(screen.getByRole("dialog")).toBeTruthy();
      expect(
        (
          screen.getByLabelText(
            "Full content to take to another app",
          ) as HTMLTextAreaElement
        ).value,
      ).toContain("Background on “This answer” from MemHub");
    } finally {
      await act(async () => {
        await setUiLanguage("zh-CN");
      });
    }
  });
  it("does not open a host with a stale preview when language changes during copying", async () => {
    if (!window.oneMemory) throw new Error("Missing mocked bridge");
    window.oneMemory.setUiLanguage = vi.fn(async (next) => next);
    let copied: () => void = () => undefined;
    copyText.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          copied = resolve;
        }),
    );
    const user = userEvent.setup();
    render(<HandoffBar memories={[memory("a")]} matterLabel="主题" />);
    await user.click(screen.getByRole("button", { name: "带出所选记忆" }));
    await user.selectOptions(screen.getByLabelText("目标应用"), "cursor");
    await user.click(screen.getByRole("button", { name: "复制并打开 Cursor" }));
    try {
      await act(async () => {
        await setUiLanguage("en");
      });
      await act(async () => {
        copied();
      });
      expect(openHandoff).not.toHaveBeenCalled();
      expect(screen.getByRole("status").textContent).toContain(
        "The preview changed",
      );
      expect(screen.getByRole("dialog")).toBeTruthy();
    } finally {
      await act(async () => {
        await setUiLanguage("zh-CN");
      });
    }
  });
});
