// @vitest-environment jsdom

import { translate } from "@one-memory/contracts/gui-i18n";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "./gui-language";
import { LanguageSelector } from "./LanguageSelector";

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.restoreAllMocks();
});

it.each(["zh-CN", "es"] as const)(
  "hides the compact label while preserving its accessible name in %s",
  (locale) => {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, locale);
    render(
      <LanguageProvider>
        <LanguageSelector compact />
      </LanguageProvider>,
    );
    const name = translate(locale, "languageTitle");
    const selector = screen.getByRole("combobox", { name });
    const label = screen.getByText(name) as HTMLLabelElement;
    expect(label.hidden).toBe(true);
    expect(label.htmlFor).toBe(selector.id);
    expect(screen.getByRole("region", { name })).toBeTruthy();
    expect(screen.getByRole("option", { name: "Español" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "简体中文" })).toBeTruthy();
  },
);

it.each(["zh-CN", "es"] as const)(
  "keeps the normal settings label visible and accessible in %s",
  (locale) => {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, locale);
    render(
      <LanguageProvider>
        <LanguageSelector />
      </LanguageProvider>,
    );
    const name = translate(locale, "languageLabel");
    const selector = screen.getByRole("combobox", { name });
    const label = screen.getByText(name) as HTMLLabelElement;
    expect(label.hidden).toBe(false);
    expect(label.htmlFor).toBe(selector.id);
    expect(selector.getAttribute("aria-label")).toBeNull();
    expect(
      screen.getByRole("heading", { name: translate(locale, "languageTitle") }),
    ).toBeTruthy();
    expect(
      screen.getByText(translate(locale, "languageDescription")),
    ).toBeTruthy();
  },
);

it("offers an accessible selector, persists and updates its label", async () => {
  render(
    <LanguageProvider>
      <LanguageSelector />
    </LanguageProvider>,
  );
  const selector = screen.getByRole("combobox", {
    name: translate("zh-CN", "languageLabel"),
  });
  expect(screen.getAllByRole("option")).toHaveLength(3);
  expect(selector.getAttribute("aria-describedby")).toBeTruthy();
  await userEvent.selectOptions(selector, "es");
  expect(localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe("es");
  expect(
    screen.getByRole("combobox", { name: translate("es", "languageLabel") }),
  ).toBe(selector);
  expect(document.documentElement.lang).toBe("es");
});

it("announces storage errors without replacing the confirmed choice", async () => {
  render(
    <LanguageProvider>
      <LanguageSelector />
    </LanguageProvider>,
  );
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("denied");
  });
  await userEvent.selectOptions(screen.getByRole("combobox"), "es");
  expect(screen.getByRole("alert").textContent).toBe(
    translate("zh-CN", "languageSaveError"),
  );
  expect((screen.getByRole("combobox") as HTMLSelectElement).value).toBe(
    "system",
  );
});
