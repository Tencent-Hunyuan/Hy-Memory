import {
  isMessageKey,
  type MessageKey,
  translate,
} from "@one-memory/i18n";
import { useGuiLanguage } from "./gui-language";

/** Translate presentation copy at render time, retaining external names and unknown errors. */
export function useOnboardingTranslation() {
  const { t, tn, tnPlain, locale } = useGuiLanguage();
  return {
    locale,
    text: (message: string) => {
      if (message.startsWith("操作没成功："))
        return translate(locale, "onboardingModelOperationFailed", {
          code: message.slice("操作没成功：".length),
        });
      return isMessageKey(message) ? t(message) : message;
    },
    t: (key: MessageKey, params?: Record<string, string | number>) =>
      translate(locale, key, params),
    tn,
    tnPlain,
  };
}
