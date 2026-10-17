// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it } from "vitest";
import { LanguageProvider } from "./gui-language";
import { R6ColdStartFrame } from "./R6ColdStartFrame";

afterEach(() => {
  cleanup();
  localStorage.clear();
});

function declaration(file: string, selector: string): string {
  const css = readFileSync(join(process.cwd(), "src", file), "utf8");
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return css.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`))?.[1] ?? "";
}

it("lets multiline headings grow above the separator in normal flow", () => {
  const head = declaration("onboarding-r6.css", ".onboarding-r6-head");
  expect(head).toMatch(/flex:\s*0 0 auto/);
  expect(head).toMatch(/display:\s*flex/);
  expect(head).toMatch(/flex-wrap:\s*wrap/);
  expect(
    declaration("onboarding-r6.css", ".onboarding-r6-head .r6-stepper"),
  ).not.toMatch(/position:\s*absolute/);
});

it("allows step labels to wrap in separate flexible tracks", () => {
  const list = declaration("r6-controls.css", ".r6-stepper ol");
  const step = declaration("r6-controls.css", ".r6-stepper li");
  expect(list).toMatch(
    /grid-template-columns:\s*repeat\(3, minmax\(0, 1fr\)\)/,
  );
  expect(step).toMatch(/white-space:\s*normal/);
  expect(step).toMatch(/height:\s*auto/);
  expect(step).not.toMatch(/width:\s*84px/);
});

it("sizes the import action to its label and wraps footer content", () => {
  const submit = declaration("onboarding-r6.css", ".onboarding-r6-submit");
  expect(submit).toMatch(/width:\s*auto/);
  expect(submit).toMatch(/min-width:\s*138px/);
  expect(declaration("onboarding-r6.css", ".onboarding-r6-foot")).toMatch(
    /flex-wrap:\s*wrap/,
  );
});

it("keeps progress cards content-sized and the step-two footer in normal flow", () => {
  const card = declaration(
    "r6-import-progress.css",
    ".r6-import-progress-card",
  );
  expect(card).toMatch(/min-height:\s*218px/);
  expect(card).not.toMatch(/(?:^|;)\s*height:/);
  expect(
    declaration(
      "first-import-r6.css",
      ".first-import-r6-main > .r6-import-progress",
    ),
  ).toMatch(/flex:\s*0 0 auto/);
  expect(declaration("first-import-r6.css", ".first-import-r6-layout")).toMatch(
    /height:\s*auto/,
  );
  expect(
    declaration("first-import-r6.css", ".first-import-r6-footer-note"),
  ).not.toMatch(/max-height|overflow/);
  expect(declaration("onboarding-r6.css", ".onboarding-r6-body")).toMatch(
    /overflow-y:\s*auto/,
  );
});

it("removes the duplicate native drag overlay above the language selector", () => {
  expect(
    declaration(
      "onboarding-r6.css",
      ".desktop-frame:has(.onboarding-r6) > .window-drag-region",
    ),
  ).toMatch(/display:\s*none/);
  expect(
    declaration(
      "onboarding-r6.css",
      ".onboarding-r6-titlebar .r6-language-selector--compact",
    ),
  ).toMatch(/-webkit-app-region:\s*no-drag/);
});

it("keeps selection status outside decorative fades and readable when disabled", () => {
  expect(
    declaration("onboarding-r6.css", ".onboarding-r6-list-area::after"),
  ).toBe("");
  expect(
    declaration(
      "onboarding-r6.css",
      ".onboarding-r6-list-status .onboarding-r6-quiet:disabled",
    ),
  ).toMatch(/opacity:\s*1/);
});

it("keeps the native selector keyboard-focusable and switches both languages", async () => {
  render(
    <LanguageProvider>
      <R6ColdStartFrame
        step={1}
        title="Fixture"
        eyebrow="Fixture"
        footer={null}
      >
        <p>Fixture body</p>
      </R6ColdStartFrame>
    </LanguageProvider>,
  );
  const user = userEvent.setup();
  const selector = screen.getByRole("combobox");
  await user.tab();
  expect(document.activeElement).toBe(selector);
  await user.selectOptions(selector, "es");
  expect(document.documentElement.lang).toBe("es");
  expect(screen.getByRole("combobox").getAttribute("id")).toBe(selector.id);
  await user.selectOptions(selector, "zh-CN");
  expect(document.documentElement.lang).toBe("zh-CN");
  expect(screen.getByRole("combobox", { name: "界面语言" })).toBe(selector);
});

it.each([1, 2, 3] as const)(
  "offers a compact language selector on step %s",
  (step) => {
    render(
      <LanguageProvider>
        <R6ColdStartFrame
          step={step}
          title="Fixture"
          eyebrow="Fixture"
          footer={null}
        >
          <p>Fixture body</p>
        </R6ColdStartFrame>
      </LanguageProvider>,
    );
    const selector = screen.getByRole("combobox", { name: "界面语言" });
    expect(selector.closest(".r6-language-selector--compact")).toBeTruthy();
    expect(
      screen.getByRole("option", { name: "简体中文" }).getAttribute("lang"),
    ).toBe("zh-CN");
    expect(
      screen.getByRole("option", { name: "Español" }).getAttribute("lang"),
    ).toBe("es");
    expect(screen.queryByRole("heading", { name: "界面语言" })).toBeNull();
    expect(selector.getAttribute("aria-describedby")).toBeNull();
  },
);
