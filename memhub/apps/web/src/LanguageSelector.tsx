import { useState } from "react";
import { setUiLanguage, useTranslate, useUiLanguage } from "./ui-language";

export function LanguageSelector() {
  const t = useTranslate();
  const language = useUiLanguage();
  const [error, setError] = useState(false);
  return (
    <div className="ui-language-selector">
      <label>
        {t("语言")}
        <select
          aria-label={t("语言")}
          value={language}
          onChange={(event) => {
            setError(false);
            void setUiLanguage(
              event.currentTarget.value as "zh-CN" | "en",
            ).catch(() => setError(true));
          }}
        >
          <option value="zh-CN">简体中文</option>
          <option value="en">English</option>
        </select>
      </label>
      {error && <p role="alert">{t("语言未能保存，请重试。")}</p>}
    </div>
  );
}
