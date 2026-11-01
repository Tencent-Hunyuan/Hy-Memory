import {
  isLanguagePreference,
  LOCALE_NAMES,
  SUPPORTED_LOCALES,
} from "@one-memory/i18n";
import { useId, useState } from "react";
import { useGuiLanguage } from "./gui-language";
import { OptionMenu } from "./OptionMenu";

export function LanguageSelector({ compact = false }: { compact?: boolean }) {
  const id = useId();
  const { preference, setPreference, t, error } = useGuiLanguage();
  const [pending, setPending] = useState(false);
  // Each language by its own name, so a person can find theirs in any UI.
  const options = [
    { value: "system", label: t("languageSystem") },
    ...SUPPORTED_LOCALES.map((locale) => ({
      value: locale,
      label: LOCALE_NAMES[locale],
      lang: locale,
    })),
  ];
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
          panelClassName={
            compact
              ? "r6-language-option-menu-panel--compact"
              : "r6-language-option-menu-panel--settings"
          }
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
