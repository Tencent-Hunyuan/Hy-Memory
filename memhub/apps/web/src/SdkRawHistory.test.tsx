// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

const { getSdkRaw, searchSdkRaw } = vi.hoisted(() => ({
  getSdkRaw: vi.fn(),
  searchSdkRaw: vi.fn(),
}));
vi.mock("./api", () => ({ getSdkRaw, searchSdkRaw }));

import { SdkRawHistory } from "./SdkRawHistory";

afterEach(cleanup);

it("lists and searches SDK raw without legacy conversation APIs", async () => {
  getSdkRaw.mockResolvedValue({
    items: [
      {
        raw_id: "raw-1",
        content: "最近原话",
        user_id: "local",
        agent_id: "one-memory",
        session_id: "session-1",
        source: "cursor",
        memory_at: null,
        gmt_created: null,
        custom: {},
      },
    ],
    total: 1,
    offset: 0,
    limit: 100,
  });
  searchSdkRaw.mockResolvedValue([
    {
      kind: "raw",
      raw_id: "raw-2",
      content: "搜索结果",
      source: "cursor",
      session_id: "session-2",
      memory_at: null,
      score: 0.9,
      truncated: false,
    },
  ]);
  render(<SdkRawHistory />);
  expect(await screen.findByText("最近原话")).toBeTruthy();
  await userEvent.type(screen.getByLabelText("搜索原始对话"), "查询");
  await userEvent.click(screen.getByRole("button", { name: "搜索" }));
  expect(await screen.findByText("搜索结果")).toBeTruthy();
  expect(searchSdkRaw).toHaveBeenCalledWith("查询");
});
