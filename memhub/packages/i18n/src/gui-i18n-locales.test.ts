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
