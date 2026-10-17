import { type MessageKey, translate } from "@one-memory/contracts/gui-i18n";
import { useGuiLanguage } from "./gui-language";

/** Translate presentation copy at render time, retaining external names and unknown errors. */
export function useOnboardingTranslation() {
  const { t, locale } = useGuiLanguage();
  return {
    locale,
    text: (message: string) => {
      if (message.startsWith("操作没成功："))
        return translate(locale, "onboardingModelOperationFailed", {
          code: message.slice("操作没成功：".length),
        });
      const translated = t(message as MessageKey);
      return typeof translated === "string" ? translated : message;
    },
    t: (key: MessageKey, params?: Record<string, string | number>) =>
      translate(locale, key, params),
  };
}
