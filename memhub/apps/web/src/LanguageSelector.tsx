import { isLanguagePreference } from "@one-memory/i18n";
import { useId, useState } from "react";
import { useGuiLanguage } from "./gui-language";
import { OptionMenu } from "./OptionMenu";

export function LanguageSelector({ compact = false }: { compact?: boolean }) {
  const id = useId();
  const { preference, setPreference, t, error } = useGuiLanguage();
  const [pending, setPending] = useState(false);
  const options = [
    { value: "system", label: t("languageSystem") },
    {
      value: "zh-CN",
      label: compact ? "简体中文" : t("languageChinese"),
      lang: "zh-CN",
    },
    {
      value: "en",
      label: compact ? "English" : t("languageEnglish"),
      lang: "en",
    },
    {
      value: "es",
      label: compact ? "Español" : t("languageSpanish"),
      lang: "es",
    },
  ] as const;
  return (
    <section
      aria-labelledby={compact ? `${id}-label` : `${id}-title`}
      className={
        compact ? "r6-language-selector--compact" : "r6-language-setting"
      }
    >
      {!compact && (
        <h2 id={`${id}-title`} className="r6-language-setting-title">
          {t("languageTitle")}
        </h2>
      )}
      <div
        className={
          compact ? "r6-language-selector-row" : "r6-language-setting-row"
        }
      >
        <label id={`${id}-label`} htmlFor={`${id}-select`} hidden={compact}>
          {t(compact ? "languageTitle" : "languageLabel")}
        </label>
        <OptionMenu
          id={`${id}-select`}
          ariaLabel={compact ? t("languageTitle") : undefined}
          labelledBy={compact ? undefined : `${id}-label`}
          describedBy={compact ? undefined : `${id}-description`}
          value={preference}
          disabled={pending}
          options={options}
          className="r6-language-option-menu"
          onChange={async (value) => {
            if (!isLanguagePreference(value)) return;
            setPending(true);
            try {
              await setPreference(value);
            } finally {
              setPending(false);
            }
          }}
        />
      </div>
      {!compact && (
        <p id={`${id}-description`} className="r6-language-setting-description">
          {t("languageDescription")}
        </p>
      )}
      {error && (
        <p role="alert" className="r6-settings-error">
          {t(error)}
        </p>
      )}
    </section>
  );
}
