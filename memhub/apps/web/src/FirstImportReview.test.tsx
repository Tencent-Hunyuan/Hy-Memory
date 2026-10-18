// @vitest-environment jsdom
import type {
  ImportJob,
  SourceImportReceipt,
  SourceResponse,
} from "@one-memory/contracts";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  FirstImportReview,
  type FirstImportReviewProps,
} from "./FirstImportReview";

const api = vi.hoisted(() => ({ receipt: vi.fn(), conversation: vi.fn() }));
vi.mock("./api", () => ({
  getSourceImportReceipt: api.receipt,
  getConversation: api.conversation,
}));
const source: SourceResponse = {
  source_id: "cursor",
  display_name: "Cursor",
  authorized: true,
  available: true,
  detected: true,
  storage_kinds: ["jsonl"],
  candidate_kind: "default",
  candidate_path: "/isolated/cursor",
  root_label: "cursor",
  root_id: "root-one",
  grant_revision: 1,
  imported_messages: 7,
  imported_conversations: 2,
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
};
const job: ImportJob = {
  job_id: "accepted-job",
  source_id: "cursor",
  user_id: "local",
  operation: "history",
  status: "running",
  phase: "read",
  checkpoint: "byte:20",
  discovered: 100,
  discovered_targets: 4,
  emitted: 7,
  stored: 7,
  revised: 0,
  merged: 0,
  skipped: 0,
  errors: 0,
  created_at: "2026-09-14T00:00:00Z",
  updated_at: "2026-09-14T00:00:01Z",
};
const query = {
  evidence_id: "person",
  evidence_revision: 2,
  role: "user",
  written_by: "person",
  created_at: "2026-09-13T12:10:00Z",
  excerpt: "真实用户原文样例",
  truncated: false,
  query_start: 0,
  query_end: 8,
  projection_version: 1,
};
const receipt: SourceImportReceipt = {
  source_id: "cursor",
  measured_at: "2026-09-14T00:00:02Z",
  messages: 7,
  conversations: 2,
  person_messages: 3,
  agent_messages: 3,
  tool_messages: 0,
  unknown_author_messages: 1,
  unknown_time_messages: 2,
  query_examples: [query],
  examples: [
    {
      evidence_id: "agent",
      role: "assistant",
      written_by: "agent",
      created_at: null,
      excerpt: "助手声称任务已完成",
      truncated: false,
    },
  ],
};
function setup(change: Partial<FirstImportReviewProps> = {}) {
  let props: FirstImportReviewProps = {
    sourceIds: ["cursor"],
    sources: [source],
    jobs: [job],
    onContinue: vi.fn(),
    ...change,
  };
  const view = render(<FirstImportReview {...props} />);
  return {
    ...view,
    props,
    update(change: Partial<FirstImportReviewProps>) {
      props = { ...props, ...change };
      view.rerender(<FirstImportReview {...props} />);
    },
  };
}
beforeEach(() => {
  vi.resetAllMocks();
  api.receipt.mockResolvedValue(receipt);
  api.conversation.mockResolvedValue({
    source_id: "cursor",
    conversation_id: "conversation",
    messages: [
      {
        evidence_id: "person",
        role: "user",
        written_by: "person",
        created_at: query.created_at,
        verbatim: query.excerpt,
      },
      {
        evidence_id: "agent",
        role: "assistant",
        written_by: "agent",
        created_at: null,
        verbatim: "助手在原始会话中的回复",
      },
    ],
  });
});
afterEach(cleanup);

it("shows true job counts and the standing import note, never excerpts or percentages", () => {
  setup();
  expect(
    screen.getByRole("heading", { name: "对话正在汇入，记忆从这里开始" }),
  ).toBeTruthy();
  expect(
    screen.getByRole("heading", { name: "正在导入你的会话" }),
  ).toBeTruthy();
  expect(
    screen.getByText("后台将持续为你导入，可以放心进入下一步"),
  ).toBeTruthy();
  expect(screen.getByText("已保存 7 条消息")).toBeTruthy();
  expect(screen.queryByText(query.excerpt)).toBeNull();
  expect(screen.queryByText("助手声称任务已完成")).toBeNull();
  expect(screen.queryByRole("button", { name: "查看原文" })).toBeNull();
  expect(screen.queryByRole("progressbar")).toBeNull();
  expect(document.body.textContent).not.toContain("100%");
  expect(api.receipt).not.toHaveBeenCalled();
  expect(api.conversation).not.toHaveBeenCalled();
});
it("explains that one large harness limits every first-import source to three days", () => {
  setup({
    jobs: [{ ...job, history_after: "2026-10-13T05:00:00Z" }],
  });
  expect(screen.getByText(/本次所有应用仅导入最近 3 天/).textContent).toContain(
    "本次所有应用仅导入最近 3 天",
  );
});
it("continues to step3 while sample GET is pending without calling completion APIs", () => {
  api.receipt.mockReturnValue(new Promise(() => {}));
  const { props } = setup();
  fireEvent.click(screen.getByRole("button", { name: "配置模型，沉淀记忆" }));
  expect(props.onContinue).toHaveBeenCalledWith("settings");
  expect(screen.queryByText(/^已生成.*记忆/)).toBeNull();
});
it.each([
  ["pending", "已扫描 100 条，正在筛选"],
  ["paused", "已扫描 100 条，尚未保存"],
  ["interrupted", "已扫描 100 条，尚未保存"],
  ["failed", "已扫描 100 条，尚未保存"],
  ["cancelled", "已扫描 100 条，尚未保存"],
  ["completed", "已扫描 100 条，本次没有可保存的对话"],
] as const)(
  "shows %s without mutating jobs or fetching missing content",
  (status, expected) => {
    setup({
      jobs: [{ ...job, status, stored: 0 }],
      sources: [{ ...source, imported_messages: 0 }],
    });
    expect(screen.getByText(expected)).toBeTruthy();
    expect(api.receipt).not.toHaveBeenCalled();
  },
);
it("does not manufacture zero when task receipt is absent", () => {
  setup({ jobs: [] });
  expect(screen.getByText("正在导入")).toBeTruthy();
  expect(screen.queryByText("等待任务回执")).toBeNull();
  expect(screen.queryByText("已保存 0 条消息")).toBeNull();
  expect(api.receipt).not.toHaveBeenCalled();
});
it("retains accepted progress and routes partial failures without leaking raw errors", () => {
  const { props } = setup({
    failedSources: { workbuddy: "sensitive server message" },
  });
  expect(screen.getByText(/workbuddy 未能开始导入/)).toBeTruthy();
  expect(screen.queryByText(/sensitive/)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "查看未导入的应用" }));
  expect(props.onContinue).toHaveBeenCalledWith("sources");
});
it("keeps the saved count when authority or freshness changes, without a conversation list", () => {
  const view = setup();
  view.update({ sources: [{ ...source, authorized: false }] });
  expect(screen.getByText("读取授权不可用")).toBeTruthy();
  expect(screen.getByText("已保存 7 条消息")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "查看原文" })).toBeNull();
  view.update({
    sources: [source],
    sourceSnapshot: { confirmedAt: null, stale: true },
  });
  expect(screen.queryByText("状态待核对")).toBeNull();
  expect(view.container.querySelector('[data-running="true"]')).toBeTruthy();
  expect(screen.getByText("已保存 7 条消息")).toBeTruthy();
  expect(
    screen.getAllByText("正在重新核对来源；已保存数量仍按任务回执显示。"),
  ).toHaveLength(1);
  view.update({
    sourceSnapshot: { confirmedAt: "2026-09-14T00:00:02Z", stale: false },
    jobs: [{ ...job, stored: 20 }],
  });
  expect(screen.getByText("已保存 20 条消息")).toBeTruthy();
  expect(screen.queryByText(query.excerpt)).toBeNull();
  expect(api.receipt).not.toHaveBeenCalled();
  expect(api.conversation).not.toHaveBeenCalled();
});
