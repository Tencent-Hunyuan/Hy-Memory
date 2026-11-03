// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

const { getSdkRaw, searchSdkRaw } = vi.hoisted(() => ({
  getSdkRaw: vi.fn(),
  searchSdkRaw: vi.fn(),
}));
vi.mock("./api", () => ({ getSdkRaw, searchSdkRaw }));

import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "./gui-language";
import { SdkRawHistory } from "./SdkRawHistory";

afterEach(() => {
  cleanup();
  localStorage.clear();
});

it("localizes controls without translating original conversation fields", async () => {
  localStorage.setItem(LANGUAGE_STORAGE_KEY, "es");
  getSdkRaw.mockResolvedValue({
    items: [
      {
        raw_id: "raw-es",
        content: "原始内容 sin traducir",
        user_id: "local",
        agent_id: "one-memory",
        session_id: "sesión-original",
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
  render(
    <LanguageProvider>
      <SdkRawHistory />
    </LanguageProvider>,
  );
  expect(
    await screen.findByLabelText("Buscar conversaciones originales"),
  ).toBeTruthy();
  expect(screen.getByText("原始内容 sin traducir")).toBeTruthy();
  expect(screen.getByText(/sesión-original/)).toBeTruthy();
});

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

it("does not call a zero-result search an empty conversation history", async () => {
  getSdkRaw.mockResolvedValue({
    items: [
      {
        raw_id: "raw-existing",
        content: "已有原始对话",
        session_id: "session-existing",
        source: "cursor",
        memory_at: null,
      },
    ],
    total: 1,
    offset: 0,
    limit: 100,
  });
  searchSdkRaw.mockResolvedValue([]);
  render(<SdkRawHistory />);

  await userEvent.type(screen.getByLabelText("搜索原始对话"), "没有命中");
  await userEvent.click(screen.getByRole("button", { name: "搜索" }));

  expect(await screen.findByText("找到 0 条")).toBeTruthy();
  expect(
    screen.queryByText("还没有原始对话。连接来源后，对话会显示在这里。"),
  ).toBeNull();
});
