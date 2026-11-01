// @vitest-environment jsdom

import type { LanguageSnapshot } from "@one-memory/i18n";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  LANGUAGE_STORAGE_KEY,
  LanguageProvider,
  useGuiLanguage,
} from "./gui-language";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
function Probe() {
  const language = useGuiLanguage();
  return (
    <>
      <output>
        {language.locale}:{language.preference}
      </output>
      <input aria-label="draft" defaultValue="draft" />
      <button onClick={() => void language.setPreference("es")} type="button">
        Spanish
      </button>
      {language.error && <p role="alert">{language.error}</p>}
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
function desktop(get: () => Promise<LanguageSnapshot>) {
  const listeners = new Set<(value: LanguageSnapshot) => void>();
  const unsubscribe = vi.fn();
  const bridge = {
    getLanguagePreference: vi.fn(() => {
      expect(listeners.size).toBe(1);
      return get();
    }),
    setLanguagePreference: vi
      .fn()
      .mockResolvedValue({ preference: "es", locale: "es", revision: 2 }),
    onLanguagePreferenceChanged: vi.fn(
      (listener: (value: LanguageSnapshot) => void) => {
        listeners.add(listener);
        return () => {
          listeners.delete(listener);
          unsubscribe();
        };
      },
    ),
  };
  window.oneMemory = bridge as unknown as NonNullable<Window["oneMemory"]>;
  return { bridge, listeners, unsubscribe };
}
beforeEach(() => {
  localStorage.clear();
  delete window.oneMemory;
  vi.spyOn(navigator, "language", "get").mockReturnValue("zh-Hans");
});
afterEach(() => {
  cleanup();
  delete window.oneMemory;
  vi.restoreAllMocks();
  localStorage.clear();
});

it("persists before publishing and retains form state without remounting", async () => {
  const user = userEvent.setup();
  mount();
  await user.type(screen.getByRole("textbox"), " preserved");
  await user.click(screen.getByRole("button"));
  expect(localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe("es");
  expect(screen.getByText("es:es")).toBeTruthy();
  expect(document.documentElement.lang).toBe("es");
  expect((screen.getByRole("textbox") as HTMLInputElement).value).toBe(
    "draft preserved",
  );
  cleanup();
  mount();
  expect(screen.getByText("es:es")).toBeTruthy();
});
it("keeps confirmed language and exposes storage write errors", async () => {
  mount();
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("private storage");
  });
  await userEvent.click(screen.getByRole("button"));
  expect(screen.getByText("zh-Hans:system")).toBeTruthy();
  expect(screen.getByRole("alert").textContent).toBe("languageSaveError");
});
it("exposes browser read errors without crashing", () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("denied");
  });
  mount();
  expect(screen.getByRole("alert").textContent).toBe("languageLoadError");
});
it("validates browser storage and synchronizes tabs and principal navigator language", () => {
  localStorage.setItem(LANGUAGE_STORAGE_KEY, "invalid");
  mount();
  expect(screen.getByText("zh-Hans:system")).toBeTruthy();
  act(() => {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, "es");
    window.dispatchEvent(
      new StorageEvent("storage", {
        key: LANGUAGE_STORAGE_KEY,
        storageArea: localStorage,
      }),
    );
  });
  expect(screen.getByText("es:es")).toBeTruthy();
  act(() => {
    localStorage.removeItem(LANGUAGE_STORAGE_KEY);
    window.dispatchEvent(
      new StorageEvent("storage", { key: null, storageArea: localStorage }),
    );
  });
  vi.spyOn(navigator, "language", "get").mockReturnValue("es-MX");
  act(() => window.dispatchEvent(new Event("languagechange")));
  expect(screen.getByText("es:system")).toBeTruthy();
});
it("subscribes before reads, rejects stale snapshots and cleans StrictMode subscriptions", async () => {
  const reads = [deferred<LanguageSnapshot>(), deferred<LanguageSnapshot>()];
  let index = 0;
  const { listeners, unsubscribe } = desktop(() => reads[index++].promise);
  const view = mount();
  expect(listeners.size).toBe(1);
  act(() => {
    for (const listener of listeners)
      listener({ preference: "es", locale: "es", revision: 5 });
  });
  await act(async () => {
    for (const read of reads)
      read.resolve({ preference: "zh-Hans", locale: "zh-Hans", revision: 1 });
  });
  expect(screen.getByText("es:es")).toBeTruthy();
  view.unmount();
  expect(listeners.size).toBe(0);
  expect(unsubscribe).toHaveBeenCalledTimes(2);
});
it("never accesses browser persistence or browser locale in desktop mode and exposes read failures", async () => {
  const { listeners } = desktop(() => Promise.reject(new Error("unavailable")));
  const read = vi.spyOn(Storage.prototype, "getItem");
  const write = vi.spyOn(Storage.prototype, "setItem");
  mount();
  await waitFor(() =>
    expect(screen.getByRole("alert").textContent).toBe("languageLoadError"),
  );
  vi.spyOn(navigator, "language", "get").mockReturnValue("es");
  act(() => window.dispatchEvent(new Event("languagechange")));
  expect(screen.getByText("zh-Hans:system")).toBeTruthy();
  act(() => {
    for (const listener of listeners)
      listener({ preference: "system", locale: "es", revision: 3 });
  });
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.getByText("es:system")).toBeTruthy();
  expect(read).not.toHaveBeenCalled();
  expect(write).not.toHaveBeenCalled();
});
it("does not surface an obsolete write failure after a newer save succeeds", async () => {
  const { bridge } = desktop(async () => ({
    preference: "system",
    locale: "zh-Hans",
    revision: 0,
  }));
  const first = deferred<LanguageSnapshot>();
  bridge.setLanguagePreference
    .mockReturnValueOnce(first.promise)
    .mockResolvedValueOnce({ preference: "es", locale: "es", revision: 2 });
  mount();
  await act(async () => {});
  await userEvent.click(screen.getByRole("button"));
  await userEvent.click(screen.getByRole("button"));
  expect(screen.getByText("es:es")).toBeTruthy();
  await act(async () => first.reject(new Error("obsolete failure")));
  expect(screen.queryByRole("alert")).toBeNull();
});

it("rejects conflicting native locale and explicit preference pairs", async () => {
  desktop(async () => ({ preference: "es", locale: "zh-Hans", revision: 1 }));
  mount();
  await waitFor(() =>
    expect(screen.getByRole("alert").textContent).toBe("languageLoadError"),
  );
  expect(screen.getByText("zh-Hans:system")).toBeTruthy();
});

it("does not allow a late write reply to overwrite a newer native event", async () => {
  const { bridge, listeners } = desktop(async () => ({
    preference: "system",
    locale: "zh-Hans",
    revision: 0,
  }));
  const write = deferred<LanguageSnapshot>();
  bridge.setLanguagePreference.mockReturnValue(write.promise);
  mount();
  await act(async () => {});
  await userEvent.click(screen.getByRole("button"));
  act(() => {
    for (const listener of listeners)
      listener({ preference: "zh-Hans", locale: "zh-Hans", revision: 8 });
  });
  await act(async () =>
    write.resolve({ preference: "es", locale: "es", revision: 7 }),
  );
  expect(screen.getByText("zh-Hans:zh-Hans")).toBeTruthy();
});

it("groups counts in Chinese only where tn asks for it, never with tnPlain", () => {
  function Counts() {
    const { tn, tnPlain } = useGuiLanguage();
    return (
      <>
        <p>{tn("healthMemories", 12345)}</p>
        <p>{tnPlain("healthRetrying", 12345)}</p>
        <p>{tnPlain("healthRetrying", 1)}</p>
      </>
    );
  }
  const view = (stored: string | null) => {
    if (stored) localStorage.setItem(LANGUAGE_STORAGE_KEY, stored);
    const { container, unmount } = render(
      <LanguageProvider>
        <Counts />
      </LanguageProvider>,
    );
    const lines = [...container.querySelectorAll("p")].map(
      (p) => p.textContent,
    );
    unmount();
    localStorage.clear();
    return lines;
  };
  expect(view(null)).toEqual([
    "12,345 条记忆",
    "12345 条这次没能整理成功，正在重试",
    "1 条这次没能整理成功，正在重试",
  ]);
  expect(view("es")).toEqual([
    "12.345 memorias",
    "12.345 no se pudieron procesar esta vez; reintentando",
    "1 no se pudo procesar esta vez; reintentando",
  ]);
});

it.each([
  ["zh-CN", "zh-Hans"],
  ["es", "es"],
  ["en", "en"],
])(
  "keeps an existing browser user's stored %s choice as %s",
  (stored, preference) => {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, stored);
    mount();
    expect(screen.getByText(`${preference}:${preference}`)).toBeTruthy();
    expect(localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe(preference);
  },
);

it("tells Core the browser choice, null for the system language", async () => {
  const reportToCore = vi.fn().mockRejectedValueOnce(new Error("offline"));
  function Choose() {
    const language = useGuiLanguage();
    return (
      <>
        <output>{language.preference}</output>
        <button onClick={() => void language.setPreference("ja")} type="button">
          ja
        </button>
        <button
          onClick={() => void language.setPreference("system")}
          type="button"
        >
          system
        </button>
      </>
    );
  }
  render(
    <LanguageProvider reportToCore={reportToCore}>
      <Choose />
    </LanguageProvider>,
  );
  // Opening the browser UI tells Core nothing; only a choice does.
  expect(reportToCore).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "ja" }));
  // A failed report never undoes the saved display language.
  expect(screen.getByRole("status").textContent).toBe("ja");
  await userEvent.click(screen.getByRole("button", { name: "system" }));
  expect(reportToCore.mock.calls).toEqual([["ja"], [null]]);
});

it("leaves telling Core to the Desktop main process", async () => {
  desktop(async () => ({
    preference: "system",
    locale: "zh-Hans",
    revision: 1,
  }));
  const reportToCore = vi.fn();
  render(
    <LanguageProvider reportToCore={reportToCore}>
      <Probe />
    </LanguageProvider>,
  );
  await userEvent.click(screen.getByRole("button", { name: "Spanish" }));
  await waitFor(() => expect(screen.getByText("es:es")).toBeTruthy());
  expect(reportToCore).not.toHaveBeenCalled();
});
