// @vitest-environment jsdom

import { translate } from "@one-memory/i18n";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "./gui-language";
import { LanguageSelector } from "./LanguageSelector";
import { optionMenuPosition } from "./OptionMenu";

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.restoreAllMocks();
});

it.each(["zh-CN", "en", "es"] as const)(
  "hides the compact label while preserving its accessible name in %s",
  async (locale) => {
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
    await userEvent.click(selector);
    expect(screen.getByRole("option", { name: "Español" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "English" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "简体中文" })).toBeTruthy();
  },
);

it.each(["zh-CN", "en", "es"] as const)(
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

it.each(["system", "zh-CN", "en", "es"] as const)(
  "keeps one stable settings row for the %s preference",
  async (preference) => {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, preference);
    render(
      <LanguageProvider>
        <LanguageSelector />
      </LanguageProvider>,
    );
    const selector = screen.getByRole("combobox") as HTMLButtonElement;
    const row = selector.closest(".r6-language-setting-row");
    expect(selector.value).toBe(preference);
    expect(row).toBeTruthy();
    expect(row?.querySelector("label")?.hidden).toBe(false);
    await userEvent.click(selector);
    expect(
      screen
        .getByRole("option", {
          name: /简体中文|Simplified Chinese|Chino simplificado/,
        })
        .getAttribute("lang"),
    ).toBe("zh-CN");
    expect(
      screen
        .getByRole("option", { name: /英语|English|Inglés/ })
        .getAttribute("lang"),
    ).toBe("en");
    expect(
      screen
        .getByRole("option", { name: /西班牙语|Spanish|Español/ })
        .getAttribute("lang"),
    ).toBe("es");
  },
);

it("offers an accessible selector, persists and updates its label", async () => {
  localStorage.setItem(LANGUAGE_STORAGE_KEY, "zh-CN");
  render(
    <LanguageProvider>
      <LanguageSelector />
    </LanguageProvider>,
  );
  const selector = screen.getByRole("combobox", {
    name: translate("zh-CN", "languageLabel"),
  });
  expect(selector.getAttribute("aria-describedby")).toBeTruthy();
  await userEvent.click(selector);
  expect(screen.getAllByRole("option")).toHaveLength(4);
  await userEvent.click(screen.getByRole("option", { name: "西班牙语" }));
  expect(localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe("es");
  expect(
    screen.getByRole("combobox", { name: translate("es", "languageLabel") }),
  ).toBe(selector);
  expect(document.documentElement.lang).toBe("es");
});

it("supports keyboard selection without using the system select menu", async () => {
  localStorage.setItem(LANGUAGE_STORAGE_KEY, "system");
  render(
    <LanguageProvider>
      <LanguageSelector />
    </LanguageProvider>,
  );
  const selector = screen.getByRole("combobox");
  selector.focus();
  await userEvent.keyboard("{Enter}");
  expect(selector.getAttribute("aria-expanded")).toBe("true");
  await userEvent.tab();
  expect(selector.getAttribute("aria-expanded")).toBe("false");
  selector.focus();
  await userEvent.keyboard("{Enter}");
  await userEvent.keyboard("{Escape}");
  expect(selector.getAttribute("aria-expanded")).toBe("false");
  await userEvent.keyboard(" ");
  expect(selector.getAttribute("aria-expanded")).toBe("true");
  await userEvent.keyboard("{Escape}");
  await userEvent.keyboard("{ArrowDown}{ArrowDown}{Enter}");
  expect(localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe("zh-CN");
  expect(selector.getAttribute("aria-expanded")).toBe("false");
});

it("announces storage errors without replacing the confirmed choice", async () => {
  localStorage.setItem(LANGUAGE_STORAGE_KEY, "zh-CN");
  render(
    <LanguageProvider>
      <LanguageSelector />
    </LanguageProvider>,
  );
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("denied");
  });
  await userEvent.click(screen.getByRole("combobox"));
  await userEvent.click(screen.getByRole("option", { name: "西班牙语" }));
  expect(screen.getByRole("alert").textContent).toBe(
    translate("zh-CN", "languageSaveError"),
  );
  expect((screen.getByRole("combobox") as HTMLButtonElement).value).toBe(
    "zh-CN",
  );
});

it("keeps a right-edge menu inside the viewport", () => {
  expect(
    optionMenuPosition(
      { top: 8, right: 1016, bottom: 40, left: 872, width: 144 },
      160,
      1024,
      600,
    ),
  ).toMatchObject({
    top: 44,
    left: 868,
    width: 144,
    maxHeight: 280,
    placement: "below",
  });
});

it("flips the menu above when the lower edge has no room", () => {
  expect(
    optionMenuPosition(
      { top: 540, right: 500, bottom: 572, left: 356, width: 144 },
      180,
      1024,
      600,
    ),
  ).toMatchObject({
    top: 356,
    left: 356,
    width: 144,
    maxHeight: 280,
    placement: "above",
  });
});

it("limits the panel to the actual space when neither side has 280px", () => {
  expect(
    optionMenuPosition(
      { top: 40, right: 160, bottom: 60, left: 16, width: 144 },
      180,
      320,
      100,
    ),
  ).toMatchObject({
    top: 64,
    left: 16,
    width: 144,
    maxHeight: 24,
    placement: "below",
  });
});

it("portals the open menu outside clipping layout containers", async () => {
  render(
    <LanguageProvider>
      <div data-testid="clipping-parent">
        <LanguageSelector compact />
      </div>
    </LanguageProvider>,
  );
  await userEvent.click(screen.getByRole("combobox"));
  const listbox = screen.getByRole("listbox");
  expect(listbox.parentElement).toBe(document.body);
  expect(
    listbox.classList.contains("r6-language-option-menu-panel--compact"),
  ).toBe(true);
});

it("keeps the menu open while its portal scrollbar or padding is pressed", async () => {
  render(
    <LanguageProvider>
      <LanguageSelector compact />
    </LanguageProvider>,
  );
  const selector = screen.getByRole("combobox");
  await userEvent.click(selector);
  const listbox = screen.getByRole("listbox");
  fireEvent.mouseDown(listbox);
  fireEvent.blur(selector, { relatedTarget: null });
  expect(selector.getAttribute("aria-expanded")).toBe("true");
  fireEvent.mouseUp(document);
});

it("clears the portal pointer guard when the menu closes mid-press", async () => {
  render(
    <LanguageProvider>
      <LanguageSelector compact />
    </LanguageProvider>,
  );
  const selector = screen.getByRole("combobox");
  await userEvent.click(selector);
  fireEvent.mouseDown(screen.getByRole("listbox"));
  fireEvent.keyDown(document, { key: "Escape" });
  expect(selector.getAttribute("aria-expanded")).toBe("false");

  await userEvent.click(selector);
  fireEvent.blur(selector, { relatedTarget: null });
  expect(selector.getAttribute("aria-expanded")).toBe("false");
});

it("clears the portal pointer guard when the window loses focus", async () => {
  render(
    <LanguageProvider>
      <LanguageSelector compact />
    </LanguageProvider>,
  );
  const selector = screen.getByRole("combobox");
  await userEvent.click(selector);
  fireEvent.mouseDown(screen.getByRole("listbox"));
  fireEvent.blur(window);
  fireEvent.blur(selector, { relatedTarget: null });
  expect(selector.getAttribute("aria-expanded")).toBe("false");
});

it("closes the portal when its anchor scrolls completely out of view", async () => {
  render(
    <LanguageProvider>
      <LanguageSelector compact />
    </LanguageProvider>,
  );
  const selector = screen.getByRole("combobox");
  vi.spyOn(selector, "getBoundingClientRect").mockReturnValue({
    top: 20,
    right: 1168,
    bottom: 52,
    left: 1024,
    width: 144,
    height: 32,
    x: 1024,
    y: 20,
    toJSON: () => ({}),
  });
  await userEvent.click(selector);
  await waitFor(() =>
    expect(selector.getAttribute("aria-expanded")).toBe("false"),
  );
  expect(screen.queryByRole("listbox")).toBeNull();
});
