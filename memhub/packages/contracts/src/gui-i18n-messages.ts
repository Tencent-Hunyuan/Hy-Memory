import {
  applicationsChineseMessages,
  applicationsSpanishMessages,
} from "./gui-i18n-applications.js";
import {
  callsChineseMessages,
  callsSpanishMessages,
} from "./gui-i18n-calls.js";
import {
  guideChineseMessages,
  guideSpanishMessages,
} from "./gui-i18n-guide.js";
import {
  healthChineseMessages,
  healthSpanishMessages,
} from "./gui-i18n-health.js";
import {
  mattersChineseMessages,
  mattersSpanishMessages,
} from "./gui-i18n-matters.js";
import {
  memoryChineseMessages,
  memorySpanishMessages,
} from "./gui-i18n-memory.js";
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
import {
  workbenchChineseMessages,
  workbenchSpanishMessages,
} from "./gui-i18n-workbench.js";

const languageChineseMessages = {
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
  startupPreparing: "正在启动，正在准备记忆服务…",
} as const;

const languageSpanishMessages: Record<
  keyof typeof languageChineseMessages,
  string
> = {
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
  startupPreparing: "Iniciando y preparando el servicio de memorias…",
};

type Catalog = {
  name: string;
  chinese: Readonly<Record<string, string>>;
  spanish: Readonly<Record<string, string>>;
};

/** Every domain catalog, in a fixed order; exported for collision tests. */
export const guiCatalogs: readonly Catalog[] = [
  {
    name: "matters",
    chinese: mattersChineseMessages,
    spanish: mattersSpanishMessages,
  },
  {
    name: "workbench",
    chinese: workbenchChineseMessages,
    spanish: workbenchSpanishMessages,
  },
  {
    name: "guide",
    chinese: guideChineseMessages,
    spanish: guideSpanishMessages,
  },
  {
    name: "health",
    chinese: healthChineseMessages,
    spanish: healthSpanishMessages,
  },
  {
    name: "recovery",
    chinese: recoveryChineseMessages,
    spanish: recoverySpanishMessages,
  },
  {
    name: "settings",
    chinese: settingsChineseMessages,
    spanish: settingsSpanishMessages,
  },
  {
    name: "onboarding",
    chinese: onboardingChineseMessages,
    spanish: onboardingSpanishMessages,
  },
  {
    name: "applications",
    chinese: applicationsChineseMessages,
    spanish: applicationsSpanishMessages,
  },
  {
    name: "memory",
    chinese: memoryChineseMessages,
    spanish: memorySpanishMessages,
  },
  {
    name: "calls",
    chinese: callsChineseMessages,
    spanish: callsSpanishMessages,
  },
  {
    name: "language",
    chinese: languageChineseMessages,
    spanish: languageSpanishMessages,
  },
];

/**
 * Merge catalogs without silent overrides: a key may appear in two catalogs
 * only when both its Chinese and Spanish texts are identical.
 */
export function mergeCatalogs(catalogs: readonly Catalog[]) {
  const chinese: Record<string, string> = {};
  const spanish: Record<string, string> = {};
  const owner: Record<string, string> = {};
  for (const catalog of catalogs) {
    for (const [key, value] of Object.entries(catalog.chinese)) {
      const translated = catalog.spanish[key];
      if (Object.hasOwn(owner, key)) {
        if (chinese[key] !== value || spanish[key] !== translated)
          throw new Error(
            `GUI message "${key}" differs between ${owner[key]} and ${catalog.name}`,
          );
        continue;
      }
      owner[key] = catalog.name;
      chinese[key] = value;
      if (translated !== undefined) spanish[key] = translated;
    }
  }
  return { chinese, spanish };
}

const merged = mergeCatalogs(guiCatalogs);

/** The complete Chinese GUI catalog defines the supported message keys. */
export const chineseMessages = merged.chinese as typeof guideChineseMessages &
  typeof healthChineseMessages &
  typeof recoveryChineseMessages &
  typeof settingsChineseMessages &
  typeof onboardingChineseMessages &
  typeof applicationsChineseMessages &
  typeof memoryChineseMessages &
  typeof callsChineseMessages &
  typeof languageChineseMessages &
  typeof workbenchChineseMessages &
  typeof mattersChineseMessages;

/** Spanish may be partial; untranslated messages use the Chinese catalog. */
export const spanishMessages = merged.spanish as Partial<
  Record<keyof typeof chineseMessages, string>
>;
