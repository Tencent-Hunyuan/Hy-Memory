import { describe, expect, it } from "vitest";
import {
  guiMessages,
  type MessageKey,
  SUPPORTED_LOCALES,
  translate,
} from "./gui-i18n.js";
import { chineseMessages } from "./gui-i18n-messages.js";
import { japaneseMessages } from "./locales/gui-i18n-ja.js";

const placeholders = (text: string) =>
  [...text.matchAll(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g)].map((m) => m[1]).sort();

/** Copy that is deliberately empty: Spanish shows no counter word. */
const INTENTIONALLY_EMPTY: Partial<Record<string, readonly string[]>> = {
  es: ["apps.unit"],
};

/** Locale-independent interpolation-only values that cannot be translated. */
const ENGLISH_EQUIVALENT_ALLOWED = new Set<MessageKey>([
  "embeddingSwitch.impact.memoryCount",
  "embeddingSwitch.rollback.reason",
]);

const keys = Object.keys(chineseMessages) as MessageKey[];
const rawSavingStatusKey = "apps.rawSavingStatus" satisfies MessageKey;
const rawSavingCompactKey = "apps.rawSavingCompact" satisfies MessageKey;
const recentEmptyCompactKey = "apps.recentEmptyCompact" satisfies MessageKey;

const rawSavingStatusTranslations = {
  en: "Saving status",
  "zh-Hans": "保存状态",
  "zh-Hant": "儲存狀態",
  ja: "保存状態",
  es: "Estado de guardado",
  de: "Speicherstatus",
  pt: "Status de salvamento",
  fr: "État de l’enregistrement",
  ko: "저장 상태",
  ru: "Статус сохранения",
  id: "Status penyimpanan",
} as const satisfies Record<(typeof SUPPORTED_LOCALES)[number], string>;

const compactHomeTranslations = {
  en: ["Saving", "No updates"],
  "zh-Hans": ["正在保存", "暂无更新"],
  "zh-Hant": ["儲存中", "暫無更新"],
  ja: ["保存中", "更新なし"],
  es: ["Guardando", "Sin novedades"],
  de: ["Wird gespeichert", "Keine Updates"],
  pt: ["Salvando", "Sem atualizações"],
  fr: ["Enregistrement", "Aucune mise à jour"],
  ko: ["저장 중", "업데이트 없음"],
  ru: ["Сохранение", "Нет обновлений"],
  id: ["Menyimpan", "Belum ada pembaruan"],
} as const satisfies Record<
  (typeof SUPPORTED_LOCALES)[number],
  readonly [string, string]
>;

describe.each(SUPPORTED_LOCALES)("%s catalog", (locale) => {
  const catalog = guiMessages[locale];

  it("has every key and no other", () => {
    expect(Object.keys(catalog).sort()).toEqual([...keys].sort());
  });

  it("keeps every placeholder exactly", () => {
    for (const key of keys) {
      const translated = placeholders(catalog[key] ?? "");
      const expected = placeholders(chineseMessages[key]);
      if (key.endsWith(".one")) {
        // A singular may drop {count} ("Voir l’application"), never invent one.
        for (const name of translated) expect(expected, key).toContain(name);
      } else {
        expect(translated, `${locale} ${key}`).toEqual(expected);
      }
    }
  });

  it("has no empty string", () => {
    const allowed = INTENTIONALLY_EMPTY[locale] ?? [];
    for (const key of keys) {
      if (allowed.includes(key)) continue;
      expect(catalog[key]?.trim(), `${locale} ${key}`).toBeTruthy();
    }
  });

  it("keeps product names untranslated", () => {
    for (const key of keys) {
      const english = guiMessages.en[key] ?? "";
      for (const name of ["MemHub", "MCP", "GitHub"]) {
        if (english.includes(name) && chineseMessages[key].includes(name))
          expect(catalog[key], `${locale} ${key}`).toContain(name);
      }
    }
  });

  it("translates through the shared translator", () => {
    expect(translate(locale, "settings")).toBe(catalog.settings);
  });
});

describe("applications catalog", () => {
  it("includes every application key in all 11 locales", () => {
    const applicationKeys = keys.filter((key) => key.startsWith("apps."));
    expect(applicationKeys.length).toBeGreaterThan(0);
    for (const locale of SUPPORTED_LOCALES)
      for (const key of applicationKeys)
        expect(
          Object.hasOwn(guiMessages[locale], key),
          `${locale} ${key}`,
        ).toBe(true);
  });

  it("localizes the raw saving status in all 11 locales", () => {
    for (const locale of SUPPORTED_LOCALES)
      expect(guiMessages[locale][rawSavingStatusKey]).toBe(
        rawSavingStatusTranslations[locale],
      );
  });

  it("keeps large home metric copy compact in all 11 locales", () => {
    for (const locale of SUPPORTED_LOCALES) {
      const [saving, empty] = compactHomeTranslations[locale];
      expect(guiMessages[locale][rawSavingCompactKey]).toBe(saving);
      expect(guiMessages[locale][recentEmptyCompactKey]).toBe(empty);
    }
  });
});

describe("embedding switch catalog", () => {
  const embeddingKeys = keys.filter((key) =>
    key.startsWith("embeddingSwitch."),
  );

  it("is complete in all 11 locales", () => {
    expect(embeddingKeys.length).toBeGreaterThan(0);
    for (const locale of SUPPORTED_LOCALES) {
      for (const key of embeddingKeys)
        expect(
          guiMessages[locale][key]?.trim(),
          `${locale} ${key}`,
        ).toBeTruthy();
    }
  });

  it.each(["en", "es"] as const)("%s has no Chinese leakage", (locale) => {
    for (const key of embeddingKeys)
      expect(guiMessages[locale][key], key).not.toMatch(/[一-鿿]/);
  });

  it("does not silently retain English fallback copy", () => {
    const untranslated: string[] = [];
    for (const locale of SUPPORTED_LOCALES) {
      if (locale === "en") continue;
      for (const key of embeddingKeys) {
        if (ENGLISH_EQUIVALENT_ALLOWED.has(key)) continue;
        if (guiMessages[locale][key] === guiMessages.en[key])
          untranslated.push(`${locale} ${key}`);
      }
    }
    expect(untranslated).toEqual([]);
  });
});

describe("onboarding model controller catalog", () => {
  const controllerKeys = [
    "索引更新没有完成，仍在使用原来的搜索模型。请检查连接后重试。",
    "上次模型测试结果仍待核对，请稍后重试。",
    "模型写入暂时关闭。请等待索引维护完成后重试。",
    "模型未能确认启用，请在设置中核对。",
    "已保存的模型配置暂时无法读取，不会重复创建。请重试刷新。",
  ] as const satisfies readonly MessageKey[];

  it("is complete in all 11 locales", () => {
    for (const locale of SUPPORTED_LOCALES)
      for (const key of controllerKeys)
        expect(
          Object.hasOwn(guiMessages[locale], key) &&
            Boolean(guiMessages[locale][key]?.trim()),
          `${locale} ${key}`,
        ).toBe(true);
  });

  it("has no Simplified Chinese fallback outside zh-Hans", () => {
    for (const locale of SUPPORTED_LOCALES) {
      if (locale === "zh-Hans") continue;
      for (const key of controllerKeys)
        expect(guiMessages[locale][key], `${locale} ${key}`).not.toBe(key);
    }
  });
});

describe("fallback", () => {
  it("falls back to English when a locale lacks a key", () => {
    const key = "languageTitle";
    const saved = japaneseMessages[key];
    try {
      delete (japaneseMessages as Partial<typeof japaneseMessages>)[key];
      expect(translate("ja", key)).toBe(guiMessages.en[key]);
    } finally {
      japaneseMessages[key] = saved;
    }
    expect(translate("ja", key)).toBe(saved);
  });
});
