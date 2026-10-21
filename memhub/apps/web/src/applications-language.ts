import {
  type GuiLocale,
  isMessageKey,
  type MessageKey,
  translate,
  translateCount,
} from "@one-memory/contracts/gui-i18n";
import { useGuiLanguage } from "./gui-language";

// Presentation-only adapter over the shared "apps." catalog: never translate
// imported content, names or error codes.
type Namespaced = Extract<MessageKey, `apps.${string}`>;
export type ApplicationsMessage = Namespaced extends `apps.${infer Key}`
  ? Key extends `${string}.one`
    ? never
    : Key
  : never;

export function applicationsText(
  locale: GuiLocale,
  key: ApplicationsMessage,
  params?: Record<string, string | number>,
): string {
  const full = `apps.${key}` as const;
  if (!isMessageKey(full)) return key;
  return typeof params?.count === "number"
    ? translateCount(locale, full, params.count, params)
    : translate(locale, full, params);
}
export function useApplicationsLanguage() {
  const { locale } = useGuiLanguage();
  return {
    locale,
    a: (key: ApplicationsMessage, params?: Record<string, string | number>) =>
      applicationsText(locale, key, params),
  };
}
