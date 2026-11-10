// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { LanguageProvider } from "./gui-language";
import { OptionMenu } from "./OptionMenu";

afterEach(cleanup);

it("shows a suffix without requiring description copy", async () => {
  const user = userEvent.setup();
  render(
    <LanguageProvider>
      <OptionMenu
        ariaLabel="记忆模式"
        value="pro"
        options={[
          { value: "lite", label: "基础记忆", suffix: "Lite" },
          { value: "pro", label: "智能记忆", suffix: "Pro" },
        ]}
        onChange={vi.fn()}
      />
    </LanguageProvider>,
  );

  const control = screen.getByRole("combobox", { name: "记忆模式" });
  expect(control.textContent).toContain("智能记忆 · Pro");
  await user.click(control);
  expect(screen.getByRole("option", { name: "基础记忆 · Lite" })).toBeTruthy();
});

it("keeps option descriptions available to assistive technology", async () => {
  const user = userEvent.setup();
  render(
    <LanguageProvider>
      <OptionMenu
        ariaLabel="记忆模式"
        value="pro"
        options={[
          {
            value: "lite",
            label: "基础记忆",
            description: "保存原始会话",
          },
          {
            value: "pro",
            label: "智能记忆",
            suffix: "Pro",
            description: "调用模型整理记忆",
          },
        ]}
        onChange={vi.fn()}
      />
    </LanguageProvider>,
  );

  await user.click(screen.getByRole("combobox", { name: "记忆模式" }));
  const option = screen.getByRole("option", { name: "智能记忆 · Pro" });
  const descriptionId = option.getAttribute("aria-describedby");
  expect(descriptionId).toBeTruthy();
  expect(document.getElementById(descriptionId ?? "")?.textContent).toBe(
    "调用模型整理记忆",
  );
  expect(
    screen
      .getByRole("option", { name: /基础记忆.*保存原始会话/ })
      .getAttribute("aria-describedby"),
  ).toBeNull();
});
