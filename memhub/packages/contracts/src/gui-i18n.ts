import { connectionSpanishMessages } from "./gui-i18n-connection.js";
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

/** Tell catalog keys from arbitrary text (names, paths, codes, imported text). */
export function isMessageKey(value: string): value is MessageKey {
  return Object.hasOwn(chineseMessages, value);
}

/**
 * Translate a message whose catalog key depends on a Spanish count: use the
 * `${key}.one` variant when it exists and count is exactly 1.
 */
export function translateCount(
  locale: GuiLocale,
  key: MessageKey,
  count: number,
  params?: Record<string, string | number>,
): string {
  const one = `${key}.one`;
  const chosen = count === 1 && isMessageKey(one) ? one : key;
  return translate(locale, chosen, params);
}

/**
 * A count for copy whose Chinese text historically shows the bare number:
 * Spanish groups digits, Chinese stays byte-for-byte as before.
 */
export function plainCount(locale: GuiLocale, value: number): string | number {
  return locale === "es" ? new Intl.NumberFormat(locale).format(value) : value;
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
  return params ? interpolate(message, params) : message;
}

/** Single pass: substituted values are never scanned again for placeholders. */
function interpolate(
  message: string,
  params: Record<string, string | number>,
): string {
  return message.replace(
    /\{([A-Za-z_][A-Za-z0-9_]*)\}/g,
    (placeholder, name: string) =>
      Object.hasOwn(params, name) ? String(params[name]) : placeholder,
  );
}

/** Installation material from the desktop bridge; snippets are opaque. */
export interface ConnectionMaterial {
  mcp: string;
  shell: string | null;
  /** Chinese prompt rendered by host-bridge; returned unchanged for zh-CN. */
  prompt: string;
}

/**
 * Connection prompt in the interface language. Chinese is the bridge's own
 * prompt, byte for byte. Spanish fills the catalog template; `mcp` and `shell`
 * are inserted once and verbatim, never parsed, escaped or translated.
 */
export function connectionPrompt(
  locale: GuiLocale,
  material: ConnectionMaterial,
): string {
  if (locale !== "es") return material.prompt;
  const shellBlock =
    material.shell === null || material.shell === ""
      ? ""
      : interpolate(connectionSpanishMessages["connection.promptShell"], {
          shell: material.shell,
        });
  return interpolate(connectionSpanishMessages["connection.prompt"], {
    mcp: material.mcp,
    shellBlock,
  });
}
