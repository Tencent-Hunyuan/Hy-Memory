// @vitest-environment jsdom
import type { ImportJob, SourceResponse } from "@one-memory/contracts";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { FirstImportReview } from "./FirstImportReview";
import { setUiLanguage } from "./ui-language";

const api = vi.hoisted(() => ({ receipt: vi.fn(), conversation: vi.fn() }));
vi.mock("./api", () => ({
  getSourceImportReceipt: api.receipt,
  getConversation: api.conversation,
}));
const source = (id: string): SourceResponse => ({
  source_id: id,
  display_name: id,
  authorized: true,
  available: true,
  detected: true,
  storage_kinds: ["jsonl"],
  candidate_kind: "default",
  candidate_path: `/isolated/${id}`,
  root_label: id,
  root_id: id,
  grant_revision: 1,
  imported_messages: 7,
  imported_conversations: 1,
  sync_enabled: false,
  sync_completeness: "additions_only",
  capabilities: {
    historical_import: "implemented",
    incremental_import: "implemented",
    live_sync: "implemented",
    recall: "implemented",
    installer: "implemented",
    verification: "fixture_only",
  },
});
const job = (
  id: string,
  status: ImportJob["status"] = "running",
  operation: ImportJob["operation"] = "history",
): ImportJob => ({
  job_id: id,
  source_id: id,
  user_id: "local",
  operation,
  status,
  phase: "read",
  checkpoint: null,
  discovered: 0,
  discovered_targets: 0,
  emitted: 7,
  stored: 7,
  revised: 0,
  merged: 0,
  skipped: 0,
  errors: 0,
  created_at: "2026-09-19T00:00:00Z",
  updated_at: "2026-09-19T00:00:01Z",
});
afterEach(async () => {
  cleanup();
  await setUiLanguage("zh-CN");
  vi.useRealTimers();
  vi.resetAllMocks();
});
beforeEach(() =>
  api.receipt.mockImplementation(async (source_id: string) => ({
    source_id,
    query_examples: [],
    examples: [],
  })),
);

it("uses step2 frame and withholds unqualified pure-user feed from old receipts", async () => {
  const view = render(
    <FirstImportReview
      sourceIds={["a"]}
      sources={[source("a")]}
      jobs={[job("a")]}
      onContinue={vi.fn()}
    />,
  );
  expect(view.container.querySelector(".onboarding-r6")).toBeTruthy();
  expect(
    screen
      .getByRole("navigation", { name: "首次设置进度" })
      .querySelector('[aria-current="step"]')?.textContent,
  ).toContain("连接应用");
  expect(
    await screen.findByText("后台将持续为你导入，可以放心进入下一步"),
  ).toBeTruthy();
  expect(screen.queryByText("持续导入中...")).toBeNull();
  expect(api.receipt).not.toHaveBeenCalled();
  expect(screen.getByText("已保存 7 条消息")).toBeTruthy();
  expect(
    screen.getByRole("heading", { name: "正在导入你的会话" }),
  ).toBeTruthy();
  expect(
    screen.getByText("后台将持续为你导入，可以放心进入下一步"),
  ).toBeTruthy();
});

it("switches first-import copy without changing connection selections or continuation payload", async () => {
  const update = vi.fn();
  const next = vi.fn();
  render(
    <FirstImportReview
      sourceIds={["a"]}
      sources={[{ ...source("a"), display_name: "Original Source" }]}
      jobs={[job("a")]}
      connectionDraft={{ hostSourceIds: [] }}
      onConnectionDraftChange={update}
      onContinue={next}
    />,
  );
  await act(() => setUiLanguage("en"));
  expect(
    screen.getByRole("heading", {
      name: "Conversations are coming in. Memories start here",
    }),
  ).toBeTruthy();
  expect(screen.getByText("Original Source")).toBeTruthy();
  expect(screen.getByText("7 messages saved")).toBeTruthy();
  fireEvent.click(
    screen.getByRole("checkbox", { name: "Connect apps in one step" }),
  );
  expect(update).toHaveBeenCalledWith({ hostSourceIds: ["a"] });
  fireEvent.click(
    screen.getByRole("button", {
      name: "Configure a model to create memories",
    }),
  );
  expect(next).toHaveBeenCalledWith("settings");
  await act(() => setUiLanguage("zh-CN"));
  expect(
    screen.getByRole("heading", { name: "对话正在汇入，记忆从这里开始" }),
  ).toBeTruthy();
});

it("does not render saved excerpts or a transcript while import is still running", () => {
  api.receipt.mockClear();
  api.conversation.mockClear();
  render(
    <FirstImportReview
      sourceIds={["a"]}
      sources={[source("a")]}
      jobs={[job("a")]}
      onContinue={vi.fn()}
    />,
  );
  expect(screen.queryByText("还没导完也能看到的问题")).toBeNull();
  expect(screen.queryByRole("button", { name: "查看原文" })).toBeNull();
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(api.receipt).not.toHaveBeenCalled();
  expect(api.conversation).not.toHaveBeenCalled();
});

it("pauses only accepted history jobs, retains partial receipts and never touches sync or another source", async () => {
  const change = vi.fn().mockImplementation(async (id: string) => {
    if (id === "b") throw Error("secret");
    return { ...job(id), status: "paused", updated_at: "2026-09-19T00:00:02Z" };
  });
  render(
    <FirstImportReview
      sourceIds={["a", "b"]}
      sources={[source("a"), source("b")]}
      jobs={[
        job("a"),
        job("b"),
        { ...job("sync", "running", "sync"), source_id: "a" },
        job("outside"),
      ]}
      onChangeJob={change}
      onContinue={vi.fn()}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "暂停导入" }));
  await waitFor(() => expect(change).toHaveBeenCalledTimes(2));
  expect(change.mock.calls).toEqual([
    ["a", "pause"],
    ["b", "pause"],
  ]);
  expect(await screen.findByText(/已确认 1 项，1 项未确认/)).toBeTruthy();
  expect(screen.queryByText(/secret/)).toBeNull();
});

it("stops remaining batch commands after cancellation without pretending the in-flight command was undone", async () => {
  let resolve!: (v: ImportJob) => void;
  const change = vi.fn().mockReturnValue(
    new Promise((r) => {
      resolve = r;
    }),
  );
  render(
    <FirstImportReview
      sourceIds={["a", "b"]}
      sources={[source("a"), source("b")]}
      jobs={[job("a"), job("b")]}
      onChangeJob={change}
      onContinue={vi.fn()}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "暂停导入" }));
  fireEvent.click(screen.getByRole("button", { name: "停止后续操作" }));
  await act(async () => resolve({ ...job("a"), status: "paused" }));
  expect(change).toHaveBeenCalledOnce();
  expect(screen.getByText(/已停止后续操作/)).toBeTruthy();
});

it("does not offer resume for a cancelled job and still permits safe pause when stale", () => {
  const change = vi.fn();
  const view = render(
    <FirstImportReview
      sourceIds={["a"]}
      sources={[source("a")]}
      jobs={[job("a", "cancelled")]}
      onChangeJob={change}
      onContinue={vi.fn()}
    />,
  );
  expect(screen.queryByRole("button", { name: "继续导入" })).toBeNull();
  view.rerender(
    <FirstImportReview
      sourceIds={["a"]}
      sources={[source("a")]}
      jobs={[job("a")]}
      sourceSnapshot={{ confirmedAt: null, stale: true }}
      onChangeJob={change}
      onContinue={vi.fn()}
    />,
  );
  expect(
    screen.getByRole<HTMLButtonElement>("button", { name: "暂停导入" })
      .disabled,
  ).toBe(false);
  expect(change).not.toHaveBeenCalled();
});

it("does not present unwired connection choices as actionable", () => {
  render(
    <FirstImportReview
      sourceIds={["a"]}
      sources={[source("a")]}
      jobs={[job("a")]}
      onContinue={vi.fn()}
    />,
  );
  for (const input of screen.getAllByRole<HTMLInputElement>("checkbox"))
    expect(input.disabled).toBe(true);
  expect(screen.getByText(/暂时无法连接应用/)).toBeTruthy();
});

it("emits controlled connection drafts only and passes continuation without completing onboarding", () => {
  const draft = { hostSourceIds: [] },
    update = vi.fn(),
    next = vi.fn();
  render(
    <FirstImportReview
      sourceIds={["a"]}
      sources={[source("a")]}
      jobs={[job("a")]}
      connectionDraft={draft}
      onConnectionDraftChange={update}
      connectionExecutionAvailable
      onContinue={next}
    />,
  );
  fireEvent.click(screen.getByRole("checkbox", { name: "一键接入应用" }));
  expect(update).toHaveBeenCalledWith({
    hostSourceIds: ["a"],
  });
  expect(
    screen.getByRole<HTMLInputElement>("checkbox", { name: "一键接入应用" })
      .checked,
  ).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "配置模型，沉淀记忆" }));
  expect(next).toHaveBeenCalledWith("settings");
  expect(api.conversation).not.toHaveBeenCalled();
});

it("keeps profile-based Harness and paused OpenClaw out of the atomic one-click host batch", () => {
  const update = vi.fn();
  render(
    <FirstImportReview
      sourceIds={["a", "deepseek-harness", "openclaw"]}
      sources={[source("a"), source("deepseek-harness"), source("openclaw")]}
      jobs={[job("a"), job("deepseek-harness"), job("openclaw")]}
      connectionDraft={{ hostSourceIds: [] }}
      onConnectionDraftChange={update}
      connectionExecutionAvailable
      onContinue={vi.fn()}
    />,
  );
  fireEvent.click(screen.getByRole("checkbox", { name: "一键接入应用" }));
  expect(update).toHaveBeenCalledWith({
    hostSourceIds: ["a"],
  });
});

it("shows a disabled host option for a noncanonical test instance", () => {
  const update = vi.fn();
  render(
    <FirstImportReview
      sourceIds={["a"]}
      sources={[source("a")]}
      jobs={[job("a")]}
      connectionDraft={{ hostSourceIds: [] }}
      onConnectionDraftChange={update}
      connectionExecutionAvailable
      hostConnectionAvailable={false}
      connectionNote="测试实例不接入应用"
      onContinue={vi.fn()}
    />,
  );

  const host = screen.getByRole<HTMLInputElement>("checkbox", {
    name: "测试实例不接入应用",
  });
  expect(host.disabled).toBe(true);
  expect(host.checked).toBe(false);
  expect(screen.getAllByText("测试实例不接入应用").length).toBeGreaterThan(0);
});

it("does not adopt later history jobs into the initially accepted batch", async () => {
  const change = vi.fn().mockImplementation(async (id: string) => ({
    ...job(id),
    status: "paused",
  }));
  const props = {
    sourceIds: ["a"],
    sources: [source("a")],
    onChangeJob: change,
    onContinue: vi.fn(),
  };
  const view = render(<FirstImportReview {...props} jobs={[job("a")]} />);
  view.rerender(
    <FirstImportReview
      {...props}
      jobs={[
        job("a"),
        { ...job("later"), source_id: "a", created_at: "2026-09-19T00:05:00Z" },
      ]}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "暂停导入" }));
  await waitFor(() => expect(change).toHaveBeenCalledOnce());
  expect(change).toHaveBeenCalledWith("a", "pause");
});

it("stops the remainder when source authority changes during a pending command", async () => {
  let resolve!: (value: ImportJob) => void;
  const change = vi.fn().mockReturnValue(
    new Promise((r) => {
      resolve = r;
    }),
  );
  const props = {
    sourceIds: ["a", "b"],
    jobs: [job("a"), job("b")],
    onChangeJob: change,
    onContinue: vi.fn(),
  };
  const view = render(
    <FirstImportReview {...props} sources={[source("a"), source("b")]} />,
  );
  fireEvent.click(screen.getByRole("button", { name: "暂停导入" }));
  view.rerender(
    <FirstImportReview
      {...props}
      sources={[{ ...source("a"), authorized: false }, source("b")]}
    />,
  );
  await act(async () => resolve({ ...job("a"), status: "paused" }));
  expect(change).toHaveBeenCalledOnce();
  expect(screen.getByText(/已确认 0 项，1 项未确认/)).toBeTruthy();
});

it("does not accept a command receipt for another task", async () => {
  const change = vi
    .fn()
    .mockResolvedValue({ ...job("wrong"), status: "paused" });
  render(
    <FirstImportReview
      sourceIds={["a"]}
      sources={[source("a")]}
      jobs={[job("a")]}
      onChangeJob={change}
      onContinue={vi.fn()}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "暂停导入" }));
  expect(await screen.findByText(/已确认 0 项，1 项未确认/)).toBeTruthy();
  expect(screen.getByRole("button", { name: "暂停导入" })).toBeTruthy();
});

it("uses user-facing connection confirmation copy without implementation narration", () => {
  render(
    <FirstImportReview
      sourceIds={["a"]}
      sources={[source("a")]}
      jobs={[job("a")]}
      connectionDraft={{ hostSourceIds: [] }}
      onConnectionDraftChange={vi.fn()}
      connectionExecutionAvailable
      onContinue={vi.fn()}
    />,
  );
  expect(screen.getByRole("complementary", { name: "连接应用" })).toBeTruthy();
  expect(
    screen.getByText(
      "点击底部按钮后，后台分别接入各应用；某个应用失败不影响其他应用。",
    ),
  ).toBeTruthy();
  expect(
    screen.queryByText(/选择草稿|按各应用回执|本页任务控制尚未接通/),
  ).toBeNull();
  expect(screen.getByText(/不会自动开放应用读取权限/)).toBeTruthy();
  expect(screen.getByText(/配置模型后，才会开始生成记忆/)).toBeTruthy();
});

it.each(["paused", "failed", "cancelled", "interrupted"] as const)(
  "keeps the stable import heading while %s",
  (status) => {
    render(
      <FirstImportReview
        sourceIds={["a"]}
        sources={[source("a")]}
        jobs={[job("a", status)]}
        onContinue={vi.fn()}
      />,
    );
    expect(screen.queryByText("导入在后台继续，无需等待。")).toBeNull();
    expect(
      screen.getByRole("heading", { name: "对话正在汇入，记忆从这里开始" }),
    ).toBeTruthy();
    expect(
      screen.getByText(/已保存的对话还在/, {
        selector: ".first-import-r6-footer-note p",
      }),
    ).toBeTruthy();
  },
);

it("does not turn a failed import into a claim that there was nothing to save", () => {
  const failed = {
    ...job("a"),
    status: "failed" as const,
    discovered: 0,
    stored: 0,
    errors: 1,
  };
  render(
    <FirstImportReview
      sourceIds={["a"]}
      sources={[source("a")]}
      jobs={[failed]}
      onContinue={vi.fn()}
    />,
  );
  expect(screen.queryByText("没有可保存的历史对话")).toBeNull();
  expect(screen.getByText(/导入遇到问题/)).toBeTruthy();
  expect(screen.getByText(/1 条读取异常/)).toBeTruthy();
});

it.each(["stale", "revoked", "missing"])(
  "keeps the stable import heading for an unverified %s batch",
  (kind) => {
    render(
      <FirstImportReview
        sourceIds={["a"]}
        sources={[{ ...source("a"), authorized: kind !== "revoked" }]}
        jobs={kind === "missing" ? [] : [job("a")]}
        sourceSnapshot={{ confirmedAt: null, stale: kind === "stale" }}
        onContinue={vi.fn()}
      />,
    );
    if (kind === "missing")
      expect(screen.getByText("导入在后台继续，无需等待。")).toBeTruthy();
    else expect(screen.queryByText("导入在后台继续，无需等待。")).toBeNull();
    expect(
      screen.getByRole("heading", { name: "对话正在汇入，记忆从这里开始" }),
    ).toBeTruthy();
    expect(api.conversation).not.toHaveBeenCalled();
  },
);

it("does not open a transcript when the saved count changes", () => {
  api.receipt.mockClear();
  api.conversation.mockClear();
  const view = render(
    <FirstImportReview
      sourceIds={["a"]}
      sources={[source("a")]}
      jobs={[job("a")]}
      onContinue={vi.fn()}
    />,
  );
  view.rerender(
    <FirstImportReview
      sourceIds={["a"]}
      sources={[source("a")]}
      jobs={[{ ...job("a"), stored: 9 }]}
      onContinue={vi.fn()}
    />,
  );
  expect(screen.getByText("已保存 9 条消息")).toBeTruthy();
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.queryByRole("button", { name: "查看原文" })).toBeNull();
  expect(api.receipt).not.toHaveBeenCalled();
  expect(api.conversation).not.toHaveBeenCalled();
});
