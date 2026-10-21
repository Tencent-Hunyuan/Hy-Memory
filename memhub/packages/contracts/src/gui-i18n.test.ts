import { describe, expect, expectTypeOf, it, vi } from "vitest";

import {
  type GuiLocale,
  isLanguagePreference,
  isLanguageSnapshot,
  type LanguagePreference,
  type LanguageSnapshot,
  type MessageKey,
  resolveGuiLocale,
  translate,
} from "./gui-i18n.js";

const messageKeys = [
  "settings",
  "languageTitle",
  "languageLabel",
  "languageSystem",
  "languageChinese",
  "languageSpanish",
  "languageDescription",
  "languageSaveError",
  "languageLoadError",
  "nativeErrorTitle",
] as const satisfies readonly MessageKey[];

describe("GUI language contracts", () => {
  it("pins the language preference and snapshot types", () => {
    expectTypeOf<LanguagePreference>().toEqualTypeOf<
      "system" | "zh-CN" | "es"
    >();
    expectTypeOf<GuiLocale>().toEqualTypeOf<"zh-CN" | "es">();
    expectTypeOf<LanguageSnapshot>().toEqualTypeOf<{
      preference: LanguagePreference;
      locale: GuiLocale;
      revision: number;
    }>();
  });

  it.each(["system", "zh-CN", "es"])("accepts preference %s", (value) => {
    expect(isLanguagePreference(value)).toBe(true);
  });

  it.each([undefined, null, "", "en", "ES", "es-MX", {}, [], 1, true])(
    "rejects invalid preferences: %j",
    (value) => expect(isLanguagePreference(value)).toBe(false),
  );

  it("validates snapshots and nonnegative safe integer revisions", () => {
    expect(
      isLanguageSnapshot({ preference: "system", locale: "es", revision: 0 }),
    ).toBe(true);
    expect(
      isLanguageSnapshot({
        preference: "zh-CN",
        locale: "zh-CN",
        revision: Number.MAX_SAFE_INTEGER,
      }),
    ).toBe(true);
    for (const value of [
      undefined,
      null,
      [],
      {},
      { preference: "en", locale: "es", revision: 0 },
      { preference: "es", locale: "zh-CN", revision: 0 },
      { preference: "zh-CN", locale: "es", revision: 0 },
      { preference: "system", locale: "system", revision: 0 },
      { preference: "system", locale: "es" },
      ...[-1, 0.5, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1, "0"].map(
        (revision) => ({ preference: "system", locale: "es", revision }),
      ),
    ]) {
      expect(isLanguageSnapshot(value)).toBe(false);
    }
  });
});

describe("GUI locale resolution", () => {
  it.each([
    "es",
    "es-ES",
    "es-MX",
    "ES-ar",
    "es-419",
    "es-Latn-ES",
    "es-u-nu-latn",
  ])("resolves Spanish primary language %s", (language) =>
    expect(resolveGuiLocale("system", language)).toBe("es"),
  );

  it.each([
    undefined,
    "",
    "en",
    "zh",
    "zh-TW",
    "fr-ES",
    "en-US-u-rg-eszzzz",
    "es_MX",
    "es--ES",
    "es-",
    " es ",
    "es,en",
    "not-a-locale!",
  ])("falls back to Chinese for %j", (language) =>
    expect(resolveGuiLocale("system", language)).toBe("zh-CN"),
  );

  it("prioritizes explicit preferences over the system language", () => {
    expect(resolveGuiLocale("es", "zh-CN")).toBe("es");
    expect(resolveGuiLocale("zh-CN", "es-MX")).toBe("zh-CN");
    expect(resolveGuiLocale("es", "invalid_locale")).toBe("es");
  });
});

describe("GUI messages", () => {
  it.each(messageKeys)("provides Chinese and Spanish text for %s", (key) => {
    expect(translate("zh-CN", key).length).toBeGreaterThan(0);
    expect(translate("es", key).length).toBeGreaterThan(0);
    expect(translate("es", key)).not.toBe(translate("zh-CN", key));
  });

  it("provides stable settings and language names", () => {
    expect(translate("zh-CN", "settings")).toBe("设置");
    expect(translate("es", "settings")).toBe("Configuración");
    expect(translate("zh-CN", "nativeErrorTitle")).toBe("MemHub 无法启动");
    expect(translate("es", "nativeErrorTitle")).toBe(
      "MemHub no pudo iniciarse",
    );
    expect(translate("es", "languageSpanish")).toBe("Español");
  });

  it("falls back to Chinese with a partial Spanish catalog and interpolates plain text once", async () => {
    vi.resetModules();
    vi.doMock("./gui-i18n-messages.js", () => ({
      chineseMessages: { settings: "中文 {name} {count} {missing} {toString}" },
      spanishMessages: {},
    }));
    try {
      const { translate: partialTranslate } = await import("./gui-i18n.js");
      expect(partialTranslate("es", "settings")).toBe(
        "中文 {name} {count} {missing} {toString}",
      );
      expect(
        partialTranslate("es", "settings", {
          name: "<img src=x onerror=alert(1)>$&{count}",
          count: 2,
        }),
      ).toBe(
        "中文 <img src=x onerror=alert(1)>$&{count} 2 {missing} {toString}",
      );
      expect(
        partialTranslate("zh-CN", "settings", { name: "", count: 0 }),
      ).toBe("中文  0 {missing} {toString}");
    } finally {
      vi.doUnmock("./gui-i18n-messages.js");
      vi.resetModules();
    }
  });
});
