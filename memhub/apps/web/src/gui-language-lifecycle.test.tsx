// @vitest-environment jsdom

import type { LanguageSnapshot } from "@one-memory/contracts/gui-i18n";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  LANGUAGE_STORAGE_KEY,
  LanguageProvider,
  useGuiLanguage,
} from "./gui-language";

function Probe() {
  const { locale, preference, error, setPreference } = useGuiLanguage();
  return (
    <>
      <output>
        {locale}:{preference}
      </output>
      <button type="button" onClick={() => void setPreference("es")}>
        Spanish
      </button>
      {error && <p role="alert">{error}</p>}
    </>
  );
}
function mount() {
  return render(
    <StrictMode>
      <LanguageProvider>
        <Probe />
      </LanguageProvider>
    </StrictMode>,
  );
}
beforeEach(() => {
  localStorage.clear();
  delete window.oneMemory;
});
afterEach(() => {
  cleanup();
  delete window.oneMemory;
  vi.restoreAllMocks();
  localStorage.clear();
});

it("removes browser listeners on StrictMode cleanup and final unmount", () => {
  const add = vi.spyOn(window, "addEventListener");
  const remove = vi.spyOn(window, "removeEventListener");
  const view = mount();
  view.unmount();
  for (const type of ["storage", "languagechange"]) {
    const callbacks = add.mock.calls
      .filter(([name]) => name === type)
      .map(([, callback]) => callback);
    expect(callbacks).toHaveLength(2);
    for (const callback of callbacks)
      expect(remove).toHaveBeenCalledWith(type, callback);
  }
});
it("ignores unrelated storage and language changes for explicit preferences", async () => {
  localStorage.setItem(LANGUAGE_STORAGE_KEY, "zh-CN");
  mount();
  vi.spyOn(navigator, "language", "get").mockReturnValue("es");
  act(() => {
    window.dispatchEvent(new Event("languagechange"));
    window.dispatchEvent(
      new StorageEvent("storage", { key: "unrelated", newValue: "es" }),
    );
    window.dispatchEvent(
      new StorageEvent("storage", {
        key: LANGUAGE_STORAGE_KEY,
        storageArea: sessionStorage,
      }),
    );
  });
  expect(screen.getByText("zh-CN:zh-CN")).toBeTruthy();
});
it("shows errors for unavailable desktop bridges without using browser fallback", async () => {
  localStorage.setItem(LANGUAGE_STORAGE_KEY, "es");
  window.oneMemory = {} as NonNullable<Window["oneMemory"]>;
  const read = vi.spyOn(Storage.prototype, "getItem");
  const write = vi.spyOn(Storage.prototype, "setItem");
  mount();
  expect(screen.getByRole("alert").textContent).toBe("languageLoadError");
  await userEvent.click(screen.getByRole("button"));
  expect(screen.getByRole("alert").textContent).toBe("languageSaveError");
  expect(screen.getByText("zh-CN:system")).toBeTruthy();
  expect(read).not.toHaveBeenCalled();
  expect(write).not.toHaveBeenCalled();
});
it("does not surface a stale read rejection after an authoritative event", async () => {
  let rejectRead!: (reason?: unknown) => void;
  let listener!: (value: LanguageSnapshot) => void;
  const pending = new Promise<LanguageSnapshot>((_, reject) => {
    rejectRead = reject;
  });
  window.oneMemory = {
    getLanguagePreference: () => pending,
    onLanguagePreferenceChanged: (next: typeof listener) => {
      listener = next;
      return () => {};
    },
  } as NonNullable<Window["oneMemory"]>;
  mount();
  await act(async () => {
    listener({ preference: "es", locale: "es", revision: 3 });
    rejectRead(new Error("late failure"));
  });
  expect(screen.getByText("es:es")).toBeTruthy();
  expect(screen.queryByRole("alert")).toBeNull();
});
it("validates native replies and keeps the last confirmed snapshot when saving fails", async () => {
  const set = vi.fn().mockRejectedValue(new Error("denied"));
  let listener!: (value: LanguageSnapshot) => void;
  window.oneMemory = {
    getLanguagePreference: async () => ({
      preference: "bad",
      locale: "es",
      revision: 0,
    }),
    setLanguagePreference: set,
    onLanguagePreferenceChanged: (next: typeof listener) => {
      listener = next;
      return () => {};
    },
  } as unknown as NonNullable<Window["oneMemory"]>;
  mount();
  await waitFor(() =>
    expect(screen.getByRole("alert").textContent).toBe("languageLoadError"),
  );
  act(() => listener({ preference: "system", locale: "zh-CN", revision: 2 }));
  await userEvent.click(screen.getByRole("button"));
  expect(set).toHaveBeenCalledWith("es");
  expect(screen.getByRole("alert").textContent).toBe("languageSaveError");
  expect(screen.getByText("zh-CN:system")).toBeTruthy();
  act(() => listener({ preference: "system", locale: "zh-CN", revision: 2 }));
  expect(screen.queryByRole("alert")).toBeNull();
});
