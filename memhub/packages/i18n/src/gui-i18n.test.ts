import { describe, expect, expectTypeOf, it } from "vitest";

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
  "languageEnglish",
  "languageSpanish",
  "languageDescription",
  "languageSaveError",
  "languageLoadError",
  "nativeErrorTitle",
] as const satisfies readonly MessageKey[];

describe("GUI language contracts", () => {
  it("pins the language preference and snapshot types", () => {
    expectTypeOf<LanguagePreference>().toEqualTypeOf<
      "system" | "zh-CN" | "en" | "es"
    >();
    expectTypeOf<GuiLocale>().toEqualTypeOf<"zh-CN" | "en" | "es">();
    expectTypeOf<LanguageSnapshot>().toEqualTypeOf<{
      preference: LanguagePreference;
      locale: GuiLocale;
      revision: number;
    }>();
  });

  it.each(["system", "zh-CN", "en", "es"])("accepts preference %s", (value) => {
    expect(isLanguagePreference(value)).toBe(true);
  });

  it.each([undefined, null, "", "EN", "ES", "es-MX", {}, [], 1, true])(
    "rejects invalid preferences: %j",
    (value) => expect(isLanguagePreference(value)).toBe(false),
  );

  it("validates snapshots and nonnegative safe integer revisions", () => {
    expect(
      isLanguageSnapshot({ preference: "system", locale: "es", revision: 0 }),
    ).toBe(true);
    expect(
      isLanguageSnapshot({
        preference: "en",
        locale: "en",
        revision: 1,
      }),
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

  it.each(["en", "en-US", "en-GB"])(
    "resolves English primary language %s",
    (language) => expect(resolveGuiLocale("system", language)).toBe("en"),
  );

  it.each(["zh", "zh-CN", "zh-SG", "zh-Hans"])(
    "resolves Simplified Chinese family %s",
    (language) => expect(resolveGuiLocale("system", language)).toBe("zh-CN"),
  );

  it.each([
    undefined,
    "",
    "zh-TW",
    "fr-ES",
    "en-US-u-rg-eszzzz",
    "es_MX",
    "es--ES",
    "es-",
    " es ",
    "es,en",
    "not-a-locale!",
  ])("falls back to English for %j", (language) =>
    expect(resolveGuiLocale("system", language)).toBe("en"),
  );

  it("prioritizes explicit preferences over the system language", () => {
    expect(resolveGuiLocale("es", "zh-CN")).toBe("es");
    expect(resolveGuiLocale("zh-CN", "es-MX")).toBe("zh-CN");
    expect(resolveGuiLocale("en", "es-MX")).toBe("en");
    expect(resolveGuiLocale("es", "invalid_locale")).toBe("es");
  });
});

describe("GUI messages", () => {
  it.each(messageKeys)(
    "provides Chinese, English and Spanish text for %s",
    (key) => {
      expect(translate("zh-CN", key).length).toBeGreaterThan(0);
      expect(translate("en", key).length).toBeGreaterThan(0);
      expect(translate("es", key).length).toBeGreaterThan(0);
      expect(translate("en", key)).not.toBe(translate("zh-CN", key));
      expect(translate("es", key)).not.toBe(translate("zh-CN", key));
    },
  );

  it("provides stable settings and language names", () => {
    expect(translate("zh-CN", "settings")).toBe("设置");
    expect(translate("en", "settings")).toBe("Settings");
    expect(translate("es", "settings")).toBe("Configuración");
    expect(translate("zh-CN", "nativeErrorTitle")).toBe("MemHub 无法启动");
    expect(translate("es", "nativeErrorTitle")).toBe(
      "MemHub no pudo iniciarse",
    );
    expect(translate("es", "languageSpanish")).toBe("Español");
    expect(translate("en", "languageEnglish")).toBe("English");
  });

  it("interpolates plain text once without interpreting substituted values", () => {
    expect(
      translate("en", "calls.detail.delivered", {
        host: "<img src=x onerror=alert(1)>$&{count}",
      }),
    ).toBe("Provided to <img src=x onerror=alert(1)>$&{count}");
  });
});
