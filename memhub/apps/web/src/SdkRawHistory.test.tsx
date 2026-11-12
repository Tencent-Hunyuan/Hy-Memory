// @vitest-environment jsdom
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
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
  vi.clearAllMocks();
  vi.useRealTimers();
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

it("retries the lite history read in place after a load failure", async () => {
  getSdkRaw.mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce({
    items: [
      {
        raw_id: "raw-recovered",
        content: "恢复后的原始对话",
        session_id: "session-recovered",
        source: "cursor",
        memory_at: null,
      },
    ],
    total: 1,
    offset: 0,
    limit: 100,
  });
  render(<SdkRawHistory />);

  await userEvent.click(await screen.findByRole("button", { name: "重试" }));

  expect(await screen.findByText("恢复后的原始对话")).toBeTruthy();
  expect(getSdkRaw).toHaveBeenCalledTimes(2);
});

it("retries the failed raw search instead of reloading history", async () => {
  getSdkRaw.mockResolvedValue({
    items: [],
    total: 0,
    offset: 0,
    limit: 100,
  });
  searchSdkRaw
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValueOnce([
      {
        kind: "raw",
        raw_id: "raw-search-recovered",
        content: "重试后的搜索结果",
        source: "cursor",
        session_id: "session-search",
        memory_at: null,
        score: 0.8,
        truncated: false,
      },
    ]);
  render(<SdkRawHistory />);

  await userEvent.type(screen.getByLabelText("搜索原始对话"), "恢复搜索");
  await userEvent.click(screen.getByRole("button", { name: "搜索" }));
  await userEvent.click(await screen.findByRole("button", { name: "重试" }));

  expect(await screen.findByText("重试后的搜索结果")).toBeTruthy();
  expect(searchSdkRaw).toHaveBeenCalledTimes(2);
  expect(getSdkRaw).toHaveBeenCalledTimes(1);
});

it("does not let a history poll clear an independent search failure", async () => {
  vi.useFakeTimers();
  getSdkRaw.mockResolvedValue({
    items: [
      {
        raw_id: "raw-old",
        content: "已有历史",
        session_id: "session-old",
        source: "cursor",
        memory_at: null,
      },
    ],
    total: 1,
    offset: 0,
    limit: 100,
  });
  searchSdkRaw.mockRejectedValue(new Error("search offline"));
  render(<SdkRawHistory />);
  await act(async () => Promise.resolve());

  fireEvent.change(screen.getByLabelText("搜索原始对话"), {
    target: { value: "失败查询" },
  });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "搜索" }));
    await Promise.resolve();
  });
  expect(screen.getByRole("alert").textContent).toContain("原始对话搜索失败");

  await act(async () => {
    await vi.advanceTimersByTimeAsync(12_000);
  });
  expect(screen.getByRole("alert").textContent).toContain("原始对话搜索失败");
});

it("ignores a stale initial-history failure after a newer search succeeds", async () => {
  let rejectHistory!: (reason: Error) => void;
  getSdkRaw.mockReturnValueOnce(
    new Promise((_resolve, reject) => {
      rejectHistory = reject;
    }),
  );
  searchSdkRaw.mockResolvedValue([
    {
      kind: "raw",
      raw_id: "raw-new-search",
      content: "较新的搜索结果",
      source: "cursor",
      session_id: "session-new",
      memory_at: null,
      score: 0.9,
      truncated: false,
    },
  ]);
  render(<SdkRawHistory />);

  await userEvent.type(screen.getByLabelText("搜索原始对话"), "新查询");
  await userEvent.click(screen.getByRole("button", { name: "搜索" }));
  expect(await screen.findByText("较新的搜索结果")).toBeTruthy();
  await act(async () => rejectHistory(new Error("late history failure")));

  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.getByText("较新的搜索结果")).toBeTruthy();
});
