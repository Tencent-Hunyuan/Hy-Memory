import { isLanguagePreference } from "@one-memory/contracts/gui-i18n";
import { useId, useState } from "react";
import { useGuiLanguage } from "./gui-language";

export function LanguageSelector({ compact = false }: { compact?: boolean }) {
  const id = useId();
  const { preference, setPreference, t, error } = useGuiLanguage();
  const [pending, setPending] = useState(false);
  return (
    <section
      aria-labelledby={compact ? `${id}-label` : `${id}-title`}
      className={
        compact ? "r6-language-selector--compact" : "r6-language-setting"
      }
    >
      {!compact && <h2 id={`${id}-title`}>{t("languageTitle")}</h2>}
      <div className={compact ? "r6-language-selector-row" : "r6-setting-row"}>
        <label id={`${id}-label`} htmlFor={`${id}-select`} hidden={compact}>
          {t(compact ? "languageTitle" : "languageLabel")}
        </label>
        <select
          id={`${id}-select`}
          aria-label={compact ? t("languageTitle") : undefined}
          aria-describedby={compact ? undefined : `${id}-description`}
          value={preference}
          disabled={pending}
          onChange={async (event) => {
            const value = event.currentTarget.value;
            if (!isLanguagePreference(value)) return;
            setPending(true);
            try {
              await setPreference(value);
            } finally {
              setPending(false);
            }
          }}
        >
          <option value="system">{t("languageSystem")}</option>
          <option value="zh-CN" lang={compact ? "zh-CN" : undefined}>
            {compact ? "简体中文" : t("languageChinese")}
          </option>
          <option value="es" lang={compact ? "es" : undefined}>
            {compact ? "Español" : t("languageSpanish")}
          </option>
        </select>
      </div>
      {!compact && <p id={`${id}-description`}>{t("languageDescription")}</p>}
      {error && (
        <p role="alert" className="r6-settings-error">
          {t(error)}
        </p>
      )}
    </section>
  );
}
