import { chineseMessages, spanishMessages } from "./gui-i18n-messages.js";

export type LanguagePreference = "system" | "zh-CN" | "es";
export type GuiLocale = "zh-CN" | "es";
export type MessageKey = keyof typeof chineseMessages;

export interface LanguageSnapshot {
  preference: LanguagePreference;
  locale: GuiLocale;
  revision: number;
}

/** Check the exact preference values accepted across GUI process boundaries. */
export function isLanguagePreference(
  value: unknown,
): value is LanguagePreference {
  return value === "system" || value === "zh-CN" || value === "es";
}

/** Validate a resolved language snapshot and its nonnegative safe revision. */
export function isLanguageSnapshot(value: unknown): value is LanguageSnapshot {
  if (typeof value !== "object" || value === null || Array.isArray(value))
    return false;
  const snapshot = value as Record<string, unknown>;
  return (
    isLanguagePreference(snapshot.preference) &&
    (snapshot.locale === "zh-CN" || snapshot.locale === "es") &&
    (snapshot.preference === "system" ||
      snapshot.preference === snapshot.locale) &&
    typeof snapshot.revision === "number" &&
    Number.isSafeInteger(snapshot.revision) &&
    snapshot.revision >= 0
  );
}

/** Resolve an explicitly supplied primary system language without reading global state. */
export function resolveGuiLocale(
  preference: LanguagePreference,
  systemLanguage?: string,
): GuiLocale {
  if (preference !== "system") return preference;
  if (!systemLanguage) return "zh-CN";
  try {
    const [canonicalLanguage] = Intl.getCanonicalLocales(systemLanguage);
    return canonicalLanguage?.split("-")[0] === "es" ? "es" : "zh-CN";
  } catch {
    return "zh-CN";
  }
}

/**
 * Return plain text, falling back to Chinese for untranslated Spanish messages.
 * Replace own {name} parameters once; missing parameters remain unchanged.
 * This is not HTML: consumers must render the result as text, never innerHTML.
 */
export function translate(
  locale: GuiLocale,
  key: MessageKey,
  params?: Record<string, string | number>,
): string {
  const message =
    (locale === "es" ? spanishMessages[key] : undefined) ??
    chineseMessages[key];
  if (!params) return message;
  return message.replace(
    /\{([A-Za-z_][A-Za-z0-9_]*)\}/g,
    (placeholder, name: string) =>
      Object.hasOwn(params, name) ? String(params[name]) : placeholder,
  );
}
