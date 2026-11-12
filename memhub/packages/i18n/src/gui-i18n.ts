import i18next from "i18next";

import { englishMessages } from "./gui-i18n-english.js";
import { chineseMessages, spanishMessages } from "./gui-i18n-messages.js";
import { germanMessages } from "./locales/gui-i18n-de.js";
import { frenchMessages } from "./locales/gui-i18n-fr.js";
import { indonesianMessages } from "./locales/gui-i18n-id.js";
import { japaneseMessages } from "./locales/gui-i18n-ja.js";
import { koreanMessages } from "./locales/gui-i18n-ko.js";
import { portugueseMessages } from "./locales/gui-i18n-pt.js";
import { russianMessages } from "./locales/gui-i18n-ru.js";
import { traditionalChineseMessages } from "./locales/gui-i18n-zh-hant.js";

/** Display languages, in the order the language selector lists them. */
export const SUPPORTED_LOCALES = [
  "en",
  "zh-Hans",
  "zh-Hant",
  "ja",
  "es",
  "de",
  "pt",
  "fr",
  "ko",
  "ru",
  "id",
] as const;

export type GuiLocale = (typeof SUPPORTED_LOCALES)[number];

/** Each language by its own name, whatever the current display language. */
export const LOCALE_NAMES: Record<GuiLocale, string> = {
  en: "English",
  "zh-Hans": "简体中文",
  "zh-Hant": "繁體中文",
  ja: "日本語",
  es: "Español",
  de: "Deutsch",
  pt: "Português",
  fr: "Français",
  ko: "한국어",
  ru: "Русский",
  id: "Bahasa Indonesia",
};

/** Codes earlier builds stored, and the code each one is now. */
export const LEGACY_LOCALES: Readonly<Record<string, GuiLocale>> = {
  "zh-CN": "zh-Hans",
};
export type LanguagePreference = "system" | GuiLocale;
export type MessageKey = keyof typeof chineseMessages;

export interface LanguageSnapshot {
  preference: LanguagePreference;
  locale: GuiLocale;
  revision: number;
}

const resources = {
  en: { translation: englishMessages },
  "zh-Hans": { translation: chineseMessages },
  "zh-Hant": { translation: traditionalChineseMessages },
  ja: { translation: japaneseMessages },
  es: { translation: spanishMessages },
  de: { translation: germanMessages },
  pt: { translation: portugueseMessages },
  fr: { translation: frenchMessages },
  ko: { translation: koreanMessages },
  ru: { translation: russianMessages },
  id: { translation: indonesianMessages },
} as const;

/** Every catalog by locale; English and the new locales are complete. */
export const guiMessages: Readonly<
  Record<GuiLocale, Partial<Record<MessageKey, string>>>
> = Object.fromEntries(
  Object.entries(resources).map(([locale, value]) => [
    locale,
    value.translation,
  ]),
) as Record<GuiLocale, Partial<Record<MessageKey, string>>>;

const translator = i18next.createInstance();
void translator.init({
  resources,
  lng: "en",
  fallbackLng: ["en", "zh-Hans"],
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

/**
 * A stored preference in today's codes: legacy codes (zh-CN) are mapped, so
 * a person who chose a language before keeps it; anything else is null.
 */
export function normalizeLanguagePreference(
  value: unknown,
): LanguagePreference | null {
  if (typeof value === "string" && Object.hasOwn(LEGACY_LOCALES, value))
    return LEGACY_LOCALES[value] ?? null;
  return isLanguagePreference(value) ? value : null;
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
      if (parts.includes("Hans")) return "zh-Hans";
      const traditional =
        parts.includes("Hant") ||
        parts.some((part) => ["TW", "HK", "MO"].includes(part));
      return traditional ? "zh-Hant" : "zh-Hans";
    }
    return isGuiLocale(language) ? language : "en";
  } catch {
    return "en";
  }
}

export function isMessageKey(value: string): value is MessageKey {
  return (
    Object.hasOwn(chineseMessages, value) || dynamicMessage(value) !== null
  );
}

function dynamicMessage(
  value: string,
): { key: MessageKey; params: Record<string, string> } | null {
  const patterns: readonly [RegExp, MessageKey, string][] = [
    [
      /^每天 (.+) 自动运行 evolve，设备休眠错过后会补跑。$/,
      "每天 {time} 自动运行 evolve，设备休眠错过后会补跑。",
      "time",
    ],
    [/^上次整合：(.+)$/, "上次整合：{time}", "time"],
    [/^切换到 (lite|pro|ultra)？$/, "切换到 {mode}？", "mode"],
  ];
  for (const [pattern, key, param] of patterns) {
    const match = pattern.exec(value);
    if (match?.[1]) return { key, params: { [param]: match[1] } };
  }
  return null;
}

export function translate(
  locale: GuiLocale,
  key: MessageKey,
  params?: Record<string, string | number>,
): string {
  const dynamic = dynamicMessage(key);
  const selected = dynamic?.key ?? key;
  return translator.getFixedT(locale)(selected, {
    ...dynamic?.params,
    ...params,
    defaultValue: chineseMessages[selected],
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
  return locale === "zh-Hans"
    ? value
    : new Intl.NumberFormat(locale).format(value);
}
