import { describe, expect, expectTypeOf, it } from "vitest";

import {
  type GuiLocale,
  isLanguagePreference,
  isLanguageSnapshot,
  isMessageKey,
  type LanguagePreference,
  type LanguageSnapshot,
  LOCALE_NAMES,
  type MessageKey,
  normalizeLanguagePreference,
  resolveGuiLocale,
  SUPPORTED_LOCALES,
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
    expectTypeOf<GuiLocale>().toEqualTypeOf<
      | "en"
      | "zh-Hans"
      | "zh-Hant"
      | "ja"
      | "es"
      | "de"
      | "pt"
      | "fr"
      | "ko"
      | "ru"
      | "id"
    >();
    expectTypeOf<LanguagePreference>().toEqualTypeOf<"system" | GuiLocale>();
    expectTypeOf<LanguageSnapshot>().toEqualTypeOf<{
      preference: LanguagePreference;
      locale: GuiLocale;
      revision: number;
    }>();
  });

  it.each(["system", ...SUPPORTED_LOCALES])(
    "accepts preference %s",
    (value) => {
      expect(isLanguagePreference(value)).toBe(true);
    },
  );

  it("lists eleven languages, each by its own name", () => {
    expect(SUPPORTED_LOCALES).toHaveLength(11);
    expect(SUPPORTED_LOCALES.map((locale) => LOCALE_NAMES[locale])).toEqual([
      "English",
      "简体中文",
      "繁體中文",
      "日本語",
      "Español",
      "Deutsch",
      "Português",
      "Français",
      "한국어",
      "Русский",
      "Bahasa Indonesia",
    ]);
  });

  it("maps the legacy zh-CN preference so existing users keep their choice", () => {
    expect(isLanguagePreference("zh-CN")).toBe(false);
    expect(normalizeLanguagePreference("zh-CN")).toBe("zh-Hans");
    expect(normalizeLanguagePreference("es")).toBe("es");
    expect(normalizeLanguagePreference("en")).toBe("en");
    expect(normalizeLanguagePreference("system")).toBe("system");
    for (const value of [undefined, null, "", "zh", "EN", {}, 1])
      expect(normalizeLanguagePreference(value)).toBeNull();
  });

  it.each([undefined, null, "", "EN", "ES", "es-MX", "zh-CN", {}, [], 1, true])(
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
        preference: "zh-Hans",
        locale: "zh-Hans",
        revision: Number.MAX_SAFE_INTEGER,
      }),
    ).toBe(true);
    for (const value of [
      undefined,
      null,
      [],
      {},
      { preference: "en", locale: "es", revision: 0 },
      { preference: "es", locale: "zh-Hans", revision: 0 },
      { preference: "zh-Hans", locale: "es", revision: 0 },
      { preference: "zh-CN", locale: "zh-CN", revision: 0 },
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

  it.each(["zh", "zh-CN", "zh-SG", "zh-Hans", "zh-Hans-HK"])(
    "resolves Simplified Chinese family %s",
    (language) => expect(resolveGuiLocale("system", language)).toBe("zh-Hans"),
  );

  it.each(["zh-TW", "zh-HK", "zh-MO", "zh-Hant", "zh-Hant-TW"])(
    "resolves Traditional Chinese family %s",
    (language) => expect(resolveGuiLocale("system", language)).toBe("zh-Hant"),
  );

  it.each([
    ["ja-JP", "ja"],
    ["de-AT", "de"],
    ["pt-BR", "pt"],
    ["pt-PT", "pt"],
    ["fr-ES", "fr"],
    ["fr-CA", "fr"],
    ["ko-KR", "ko"],
    ["ru-RU", "ru"],
    ["id-ID", "id"],
    ["in-ID", "id"],
  ])("resolves %s to %s", (language, locale) =>
    expect(resolveGuiLocale("system", language)).toBe(locale),
  );

  it.each([
    undefined,
    "",
    "it-IT",
    "nl",
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
    expect(resolveGuiLocale("zh-Hans", "es-MX")).toBe("zh-Hans");
    expect(resolveGuiLocale("ja", "zh-TW")).toBe("ja");
    expect(resolveGuiLocale("en", "es-MX")).toBe("en");
    expect(resolveGuiLocale("es", "invalid_locale")).toBe("es");
  });
});

describe("GUI messages", () => {
  it.each(messageKeys)(
    "provides Chinese, English and Spanish text for %s",
    (key) => {
      expect(translate("zh-Hans", key).length).toBeGreaterThan(0);
      expect(translate("en", key).length).toBeGreaterThan(0);
      expect(translate("es", key).length).toBeGreaterThan(0);
      expect(translate("en", key)).not.toBe(translate("zh-Hans", key));
      expect(translate("es", key)).not.toBe(translate("zh-Hans", key));
    },
  );

  it("provides stable settings and language names", () => {
    expect(translate("zh-Hans", "settings")).toBe("设置");
    expect(translate("en", "settings")).toBe("Settings");
    expect(translate("es", "settings")).toBe("Configuración");
    expect(translate("zh-Hans", "nativeErrorTitle")).toBe("MemHub 无法启动");
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

  it("translates mode copy interpolated before lookup", () => {
    const cases = [
      [
        "每天 03:00 自动运行 evolve，设备休眠错过后会补跑。",
        "Run evolve daily at 03:00.",
      ],
      [
        "上次整合：11/3/2026, 4:00:00 PM",
        "Last consolidated: 11/3/2026, 4:00:00 PM",
      ],
      ["切换到 ultra？", "Choose ultra?"],
    ] as const;
    for (const [source, expected] of cases) {
      expect(isMessageKey(source)).toBe(true);
      expect(translate("en", source as MessageKey)).toContain(expected);
    }
  });
});
