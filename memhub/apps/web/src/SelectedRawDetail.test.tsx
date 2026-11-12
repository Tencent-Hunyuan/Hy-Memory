// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it } from "vitest";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "./gui-language";
import { SelectedRawDetail } from "./SelectedRawDetail";

beforeEach(() => localStorage.setItem(LANGUAGE_STORAGE_KEY, "zh-Hans"));
afterEach(() => {
  cleanup();
  localStorage.clear();
});

it("shows every role and preserves a long original message in the detail pane", () => {
  const long = "原文不能截断。".repeat(500);
  render(
    <LanguageProvider>
      <SelectedRawDetail
        record={{
          raw_id: "raw-long",
          content: `[user]: ${long}\n[assistant]: 已收到`,
          user_id: "local",
          agent_id: "all-apps",
          session_id: "session-1",
          source: "cursor",
          memory_at: 1_793_707_200,
          gmt_created: 1_793_707_210,
          custom: {},
          messages: [
            { role: "user", content: long },
            { role: "assistant", content: "已收到" },
            { role: "tool", content: "3 tests passed" },
            { role: "unknown", content: "来源角色缺失" },
          ],
        }}
        sources={[]}
      />
    </LanguageProvider>,
  );

  expect(screen.getByText("你")).toBeTruthy();
  expect(screen.getByText("助手")).toBeTruthy();
  expect(screen.getByText("工具")).toBeTruthy();
  expect(screen.getByText("作者待核对")).toBeTruthy();
  expect(screen.getByText(long)).toHaveProperty("textContent", long);
  expect(screen.getByText("已收到")).toBeTruthy();
  expect(screen.queryByText("原始对话")).toBeNull();
  expect(screen.getByRole("article", { name: "原始对话" })).toBeTruthy();
});
