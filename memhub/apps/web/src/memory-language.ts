import {
  type GuiLocale,
  isMessageKey,
  plainCount,
  translate,
  translateCount,
} from "@one-memory/i18n";
import { useGuiLanguage } from "./gui-language";

type Params = Record<string, string | number>;

/**
 * Adapter over the shared "memory:" catalog. Only explicit interface copy is
 * passed in; unknown text (imported content, names, codes) returns unchanged.
 * `plural` selects the Spanish `.one` variant; callers format shown numbers.
 */
export function memoryText(
  locale: GuiLocale,
  message: string,
  params?: Params,
  plural?: number,
): string {
  const key = `memory:${message}`;
  if (!isMessageKey(key)) return message;
  return plural === undefined
    ? translate(locale, key, params)
    : translateCount(locale, key, plural, params);
}
export function useMemoryLanguage() {
  const { locale } = useGuiLanguage();
  return {
    locale,
    text: (message: string, params?: Params, plural?: number) =>
      memoryText(locale, message, params, plural),
    count: (value: number) => new Intl.NumberFormat(locale).format(value),
    plain: (value: number) => plainCount(locale, value),
  };
}
