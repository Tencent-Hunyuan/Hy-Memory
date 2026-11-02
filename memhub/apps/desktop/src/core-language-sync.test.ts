import type { GuiLocale, LanguageSnapshot } from "@one-memory/i18n";
import { describe, expect, it, vi } from "vitest";
import { coreUiLanguage, installCoreLanguageSync } from "./core-language-sync";

function source(initial: LanguageSnapshot) {
  let snapshot = initial;
  const listeners = new Set<(value: LanguageSnapshot) => void>();
  return {
    read: () => snapshot,
    subscribe(listener: (value: LanguageSnapshot) => void) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    change(next: LanguageSnapshot) {
      snapshot = next;
      for (const listener of listeners) listener(next);
    },
  };
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("Core language sync", () => {
  it("tells Core the language the GUI shows, system included", () => {
    const shown = (
      preference: LanguageSnapshot["preference"],
      locale: GuiLocale,
    ) => coreUiLanguage({ preference, locale, revision: 0 });
    expect(shown("system", "zh-Hans")).toBe("zh-Hans");
    expect(shown("system", "en")).toBe("en");
    expect(shown("zh-Hans", "zh-Hans")).toBe("zh-Hans");
    expect(shown("en", "en")).toBe("en");
    expect(shown("ja", "ja")).toBe("ja");
  });

  it("sends the stored choice once at launch, then every change", async () => {
    const language = source({ preference: "es", locale: "es", revision: 0 });
    const send = vi.fn(async () => {});
    const dispose = installCoreLanguageSync({ language, send });
    await settle();
    expect(send.mock.calls).toEqual([["es"]]);
    language.change({ preference: "ja", locale: "ja", revision: 1 });
    await settle();
    language.change({ preference: "system", locale: "en", revision: 2 });
    await settle();
    expect(send.mock.calls).toEqual([["es"], ["ja"], ["en"]]);
    dispose();
    language.change({ preference: "de", locale: "de", revision: 3 });
    await settle();
    expect(send).toHaveBeenCalledTimes(3);
  });

  it("an untouched install sends the system language the GUI shows", async () => {
    const language = source({
      preference: "system",
      locale: "zh-Hans",
      revision: 0,
    });
    const send = vi.fn(async () => {});
    installCoreLanguageSync({ language, send });
    await settle();
    expect(send.mock.calls).toEqual([["zh-Hans"]]);
    // Choosing the language already shown changes nothing Core sees.
    language.change({ preference: "zh-Hans", locale: "zh-Hans", revision: 1 });
    await settle();
    expect(send).toHaveBeenCalledTimes(1);
  });

  it("reports a failed send and retries it with the next change", async () => {
    const language = source({ preference: "fr", locale: "fr", revision: 0 });
    const send = vi
      .fn<(value: string) => Promise<void>>()
      .mockRejectedValueOnce(new Error("core_unavailable"))
      .mockResolvedValue(undefined);
    const reportError = vi.fn();
    installCoreLanguageSync({ language, send, reportError });
    await settle();
    expect(reportError).toHaveBeenCalledTimes(1);
    language.change({ preference: "fr", locale: "fr", revision: 1 });
    await settle();
    expect(send.mock.calls).toEqual([["fr"], ["fr"]]);
  });
});
