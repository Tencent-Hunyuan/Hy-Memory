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

it("uses content-driven shell, discovery and mode geometry", () => {
  const stage = declaration("onboarding-r6.css", ".onboarding-r6-stage");
  const shell = declaration("onboarding-r6.css", ".onboarding-r6-shell");
  const discovery = declaration(
    "onboarding-r6.css",
    ".onboarding-r6-discovery",
  );
  const heading = declaration(
    "onboarding-r6.css",
    ".onboarding-r6-list-heading",
  );
  const mode = declaration("onboarding-r6.css", ".onboarding-r6-mode");

  expect(stage).toMatch(/min-height:\s*0/);
  expect(stage).not.toMatch(/min-height:\s*852px/);
  expect(shell).toMatch(/height:\s*100%/);
  expect(shell).not.toMatch(/(?:^|;)\s*height:\s*820px/);
  expect(discovery).toMatch(/height:\s*100%/);
  expect(heading).toMatch(/position:\s*static/);
  expect(mode).toMatch(/width:\s*100%/);
});

it("does not reserve an empty eyebrow row", () => {
  render(
    <LanguageProvider>
      <R6ColdStartFrame step={1} title="Fixture" footer={null}>
        <p>Fixture body</p>
      </R6ColdStartFrame>
    </LanguageProvider>,
  );
  expect(screen.getByRole("heading", { name: "Fixture" })).toBeTruthy();
  expect(screen.queryByText("你的记忆，从这里接上")).toBeNull();
});

it("keeps the v1.0.4 step-two split layout and progress geometry", () => {
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
  ).toMatch(/flex:\s*0 0 218px/);
  const layout = declaration("first-import-r6.css", ".first-import-r6-layout");
  expect(layout).toMatch(
    /grid-template-columns:\s*minmax\(0,\s*738px\)\s+278px/,
  );
  expect(layout).toMatch(/height:\s*560\.2px/);
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

it("keeps long language names on one line in the fixed-size selectors", () => {
  // "Utiliser la langue du système", "Systemsprache verwenden" and
  // "Bahasa Indonesia" are wider than the 144px title-bar button.
  for (const [file, selector] of [
    [
      "onboarding-r6.css",
      ".onboarding-r6-titlebar .r6-language-option-menu .om-option-menu-button",
    ],
    [
      "r6-settings.css",
      ".r6-settings .r6-language-option-menu .om-option-menu-button",
    ],
  ] as const) {
    const rule = declaration(file, selector);
    expect(rule, selector).toMatch(/white-space:\s*nowrap/);
    expect(rule, selector).toMatch(/text-overflow:\s*ellipsis/);
    expect(rule, selector).toMatch(/overflow:\s*hidden/);
  }
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

it("keeps the product language menu keyboard-focusable and switches both languages", async () => {
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
  await user.click(selector);
  await user.click(screen.getByRole("option", { name: "Español" }));
  expect(document.documentElement.lang).toBe("es");
  expect(screen.getByRole("combobox").getAttribute("id")).toBe(selector.id);
  await user.click(selector);
  await user.click(screen.getByRole("option", { name: "简体中文" }));
  expect(document.documentElement.lang).toBe("zh-Hans");
  expect(screen.getByRole("combobox", { name: "界面语言" })).toBe(selector);
});

it.each([1, 2, 3] as const)(
  "offers a compact language selector on step %s",
  async (step) => {
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
    await userEvent.click(selector);
    expect(
      screen.getByRole("option", { name: "简体中文" }).getAttribute("lang"),
    ).toBe("zh-Hans");
    expect(
      screen.getByRole("option", { name: "Español" }).getAttribute("lang"),
    ).toBe("es");
    expect(screen.queryByRole("heading", { name: "界面语言" })).toBeNull();
    expect(selector.getAttribute("aria-describedby")).toBeNull();
  },
);
