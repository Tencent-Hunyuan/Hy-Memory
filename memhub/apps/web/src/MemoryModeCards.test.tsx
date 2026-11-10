// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { LanguageProvider } from "./gui-language";
import { MemoryModeCards } from "./MemoryModeCards";

afterEach(cleanup);

it("shows every mode feature and reveals Ultra scheduling after selection", async () => {
  const onChange = vi.fn();
  const user = userEvent.setup();
  const view = (value: "lite" | "pro" | "ultra") => (
    <LanguageProvider>
      <MemoryModeCards
        aria-label="记忆模式"
        value={value}
        layout="rows"
        onChange={onChange}
        ultraDetail={
          <div>
            <strong>每天自动整合</strong>
            <small>默认 03:00，可在设置中修改。</small>
          </div>
        }
      />
    </LanguageProvider>
  );
  const { rerender } = render(view("pro"));

  expect(screen.getByRole("radiogroup", { name: "记忆模式" })).toBeTruthy();
  expect(screen.getAllByRole("radio")).toHaveLength(3);
  expect(screen.getByText("不调用生成模型")).toBeTruthy();
  expect(screen.getByText("沉淀高级记忆")).toBeTruthy();
  expect(screen.getByText("增强关联召回")).toBeTruthy();
  expect(screen.getByText("· Pro").className).toContain("memory-mode-tier");
  expect(screen.queryByText("每天自动整合")).toBeNull();

  await user.click(screen.getByRole("radio", { name: /深度记忆/ }));
  expect(onChange).toHaveBeenCalledWith("ultra", expect.any(HTMLInputElement));

  rerender(view("ultra"));
  expect(screen.getByText("每天自动整合")).toBeTruthy();
});

it("preserves per-mode disabled behavior", () => {
  render(
    <LanguageProvider>
      <MemoryModeCards
        aria-label="记忆模式"
        value="lite"
        disabled={(mode) => mode !== "lite"}
        onChange={vi.fn()}
      />
    </LanguageProvider>,
  );

  expect(screen.getByRole("radio", { name: /基础记忆/ })).toHaveProperty(
    "disabled",
    false,
  );
  expect(screen.getByRole("radio", { name: /智能记忆/ })).toHaveProperty(
    "disabled",
    true,
  );
  expect(screen.getByRole("radio", { name: /深度记忆/ })).toHaveProperty(
    "disabled",
    true,
  );
});
