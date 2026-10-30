// @vitest-environment jsdom
import type {
  SourceImportReceipt as Receipt,
  SourceResponse,
} from "@one-memory/contracts";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SourceImportReceipt } from "./SourceImportReceipt";

const getReceipt = vi.fn();
vi.mock("./api", () => ({
  getSourceImportReceipt: (...args: unknown[]) => getReceipt(...args),
}));
const source = {
  source_id: "cursor",
  display_name: "Cursor",
  authorized: true,
  imported_messages: 5,
} as SourceResponse;
const receipt: Receipt = {
  source_id: "cursor",
  measured_at: "2026-09-13T00:00:00Z",
  messages: 5,
  conversations: 2,
  person_messages: 2,
  agent_messages: 1,
  tool_messages: 1,
  unknown_author_messages: 1,
  unknown_time_messages: 4,
  examples: [
    {
      evidence_id: "e1",
      role: "user",
      written_by: "person",
      created_at: null,
      excerpt: "预算<5，不能>5。",
      truncated: true,
    },
  ],
};
beforeEach(() => {
  vi.resetAllMocks();
  getReceipt.mockResolvedValue(receipt);
});
afterEach(cleanup);

describe("source import receipt", () => {
  it("reads only after opening and shows measured authors and unknown message times", async () => {
    const user = userEvent.setup();
    render(<SourceImportReceipt source={source} />);
    expect(getReceipt).not.toHaveBeenCalled();
    await user.click(screen.getByText("查看已读内容"));
    expect(await screen.findByText("预算<5，不能>5。")).toBeTruthy();
    expect(
      screen.getByText(/4 条记录没有原始时间，未用导入时间代替/),
    ).toBeTruthy();
    expect(screen.getByText("原始消息").nextElementSibling?.textContent).toBe(
      "5",
    );
    expect(screen.getByText("作者未知").nextElementSibling?.textContent).toBe(
      "1",
    );
    expect(screen.getByText("用户 · 时间未知")).toBeTruthy();
    expect(screen.getByText(/仅展示前 600/)).toBeTruthy();
  });
  it("offers no full-conversation read: the SDK data plane has no by-id raw read", async () => {
    const user = userEvent.setup();
    render(<SourceImportReceipt source={source} />);
    await user.click(screen.getByText("查看已读内容"));
    await screen.findByText("预算<5，不能>5。");
    expect(screen.queryByRole("button", { name: "查看完整对话" })).toBeNull();
  });

  it("drops a previous success while refresh is pending and on failure", async () => {
    const user = userEvent.setup();
    render(<SourceImportReceipt source={source} />);
    await user.click(screen.getByText("查看已读内容"));
    await screen.findByText("预算<5，不能>5。");
    getReceipt.mockRejectedValue(new Error());
    await user.click(screen.getByRole("button", { name: "刷新已读内容" }));
    expect(screen.queryByText("预算<5，不能>5。")).toBeNull();
    expect(await screen.findByRole("alert")).toBeTruthy();
    getReceipt.mockResolvedValue(receipt);
    await user.click(screen.getByRole("button", { name: "刷新已读内容" }));
    expect(await screen.findByText("预算<5，不能>5。")).toBeTruthy();
  });
  it("revocation unmounts visible evidence and discards late responses", async () => {
    let resolve: (value: Receipt) => void = () => undefined;
    getReceipt.mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    const user = userEvent.setup();
    const view = render(<SourceImportReceipt source={source} />);
    await user.click(screen.getByText("查看已读内容"));
    await waitFor(() => expect(getReceipt).toHaveBeenCalled());
    view.rerender(
      <SourceImportReceipt source={{ ...source, authorized: false }} />,
    );
    await act(async () => resolve(receipt));
    expect(screen.queryByText("查看已读内容")).toBeNull();
    expect(screen.queryByText("预算<5，不能>5。")).toBeNull();
  });

  it("rejects source-mismatched responses", async () => {
    getReceipt.mockResolvedValue({ ...receipt, source_id: "another-source" });
    const user = userEvent.setup();
    render(<SourceImportReceipt source={source} />);
    await user.click(screen.getByText("查看已读内容"));
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.queryByText("预算<5，不能>5。")).toBeNull();
  });
});
