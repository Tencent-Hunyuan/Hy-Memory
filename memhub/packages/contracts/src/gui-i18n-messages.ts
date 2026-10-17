import {
  guideChineseMessages,
  guideSpanishMessages,
} from "./gui-i18n-guide.js";
import {
  healthChineseMessages,
  healthSpanishMessages,
} from "./gui-i18n-health.js";
import {
  onboardingChineseMessages,
  onboardingSpanishMessages,
} from "./gui-i18n-onboarding.js";
import {
  recoveryChineseMessages,
  recoverySpanishMessages,
} from "./gui-i18n-recovery.js";
import {
  settingsChineseMessages,
  settingsSpanishMessages,
} from "./gui-i18n-settings.js";

/** The complete Chinese GUI catalog defines the supported message keys. */
export const chineseMessages = {
  ...guideChineseMessages,
  ...healthChineseMessages,
  ...recoveryChineseMessages,
  ...settingsChineseMessages,
  ...onboardingChineseMessages,
  settings: "设置",
  languageTitle: "界面语言",
  languageLabel: "语言",
  languageSystem: "跟随系统",
  languageChinese: "简体中文",
  languageSpanish: "西班牙语",
  languageDescription: "选择界面语言。此设置不会更改你的对话或记忆内容。",
  languageSaveError: "无法保存语言设置，请重试。",
  languageLoadError: "无法加载语言设置，请重试。",
  nativeErrorTitle: "MemHub 无法启动",
} as const;

/** Spanish may be partial; untranslated messages use the Chinese catalog. */
export const spanishMessages: Partial<
  Record<keyof typeof chineseMessages, string>
> = {
  ...guideSpanishMessages,
  ...healthSpanishMessages,
  ...recoverySpanishMessages,
  ...settingsSpanishMessages,
  ...onboardingSpanishMessages,
  settings: "Configuración",
  languageTitle: "Idioma de la interfaz",
  languageLabel: "Idioma",
  languageSystem: "Usar el idioma del sistema",
  languageChinese: "Chino simplificado",
  languageSpanish: "Español",
  languageDescription:
    "Elige el idioma de la interfaz. Esta opción no modifica tus conversaciones ni tus memorias.",
  languageSaveError: "No se pudo guardar el idioma. Inténtalo de nuevo.",
  languageLoadError: "No se pudo cargar el idioma. Inténtalo de nuevo.",
  nativeErrorTitle: "MemHub no pudo iniciarse",
};
