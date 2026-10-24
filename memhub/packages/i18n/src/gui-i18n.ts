import i18next from "i18next";

import { englishMessages } from "./gui-i18n-english.js";
import { chineseMessages, spanishMessages } from "./gui-i18n-messages.js";

export const SUPPORTED_LOCALES = ["zh-CN", "en", "es"] as const;

export type GuiLocale = (typeof SUPPORTED_LOCALES)[number];
export type LanguagePreference = "system" | GuiLocale;
export type MessageKey = keyof typeof chineseMessages;

export interface LanguageSnapshot {
  preference: LanguagePreference;
  locale: GuiLocale;
  revision: number;
}

const resources = {
  "zh-CN": { translation: chineseMessages },
  en: { translation: englishMessages },
  es: { translation: spanishMessages },
} as const;

const translator = i18next.createInstance();
void translator.init({
  resources,
  lng: "en",
  fallbackLng: ["en", "zh-CN"],
  supportedLngs: [...SUPPORTED_LOCALES],
  initImmediate: false,
  keySeparator: false,
  nsSeparator: false,
  pluralSeparator: ".",
  returnNull: false,
  showSupportNotice: false,
  interpolation: {
    escapeValue: false,
    prefix: "{",
    suffix: "}",
  },
});

export function isGuiLocale(value: unknown): value is GuiLocale {
  return (
    typeof value === "string" &&
    (SUPPORTED_LOCALES as readonly string[]).includes(value)
  );
}

export function isLanguagePreference(
  value: unknown,
): value is LanguagePreference {
  return value === "system" || isGuiLocale(value);
}

export function isLanguageSnapshot(value: unknown): value is LanguageSnapshot {
  if (typeof value !== "object" || value === null || Array.isArray(value))
    return false;
  const snapshot = value as Record<string, unknown>;
  return (
    isLanguagePreference(snapshot.preference) &&
    isGuiLocale(snapshot.locale) &&
    (snapshot.preference === "system" ||
      snapshot.preference === snapshot.locale) &&
    typeof snapshot.revision === "number" &&
    Number.isSafeInteger(snapshot.revision) &&
    snapshot.revision >= 0
  );
}

export function resolveGuiLocale(
  preference: LanguagePreference,
  systemLanguage?: string,
): GuiLocale {
  if (preference !== "system") return preference;
  if (!systemLanguage) return "en";
  try {
    const [canonicalLanguage] = Intl.getCanonicalLocales(systemLanguage);
    const language = canonicalLanguage?.split("-")[0]?.toLowerCase();
    if (language === "zh") {
      const parts = canonicalLanguage?.split("-") ?? [];
      const unsupportedTraditional =
        parts.includes("Hant") ||
        parts.some((part) => ["TW", "HK", "MO"].includes(part));
      return unsupportedTraditional ? "en" : "zh-CN";
    }
    if (language === "es") return "es";
    if (language === "en") return "en";
    return "en";
  } catch {
    return "en";
  }
}

export function isMessageKey(value: string): value is MessageKey {
  return Object.hasOwn(chineseMessages, value);
}

export function translate(
  locale: GuiLocale,
  key: MessageKey,
  params?: Record<string, string | number>,
): string {
  return translator.getFixedT(locale)(key, {
    ...params,
    defaultValue: chineseMessages[key],
  });
}

export function translateCount(
  locale: GuiLocale,
  key: MessageKey,
  count: number,
  params?: Record<string, string | number>,
): string {
  const category = new Intl.PluralRules(locale).select(count);
  const variant = `${key}.${category}`;
  const selected = isMessageKey(variant) ? variant : key;
  return translate(locale, selected, {
    ...params,
    count: params?.count ?? new Intl.NumberFormat(locale).format(count),
  });
}

export function plainCount(locale: GuiLocale, value: number): string | number {
  return locale === "zh-CN"
    ? value
    : new Intl.NumberFormat(locale).format(value);
}
