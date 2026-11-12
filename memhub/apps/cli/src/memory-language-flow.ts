/**
 * `memhub settings language [<code>|system]`.
 *
 * The display language the App reports to the Runtime (`ui_language`), and
 * the SDK prompt language it implies. Every code, zh-Hans and en included,
 * makes extraction and the other SDK prompts use that language; "system"
 * (no language set) passes none, so the SDK keeps detecting Chinese or
 * English as before. The App sends the language it shows, so it is only
 * unset on an install without the App. A change applies at once: a write in flight
 * finishes in the old language, the next ones use the new one. The App sends
 * its own choice again whenever it changes there.
 */

import type { CoreReader } from "./core-client.js";
import type { CorePutOperations } from "./core-operations.js";
import { PRODUCT } from "./version.js";

export const UI_LANGUAGES = [
  "en",
  "zh-Hans",
  "zh-Hant",
  "ja",
  "es",
  "de",
  "pt",
  "fr",
  "ko",
  "ru",
  "id",
] as const;
export type UiLanguage = (typeof UI_LANGUAGES)[number];

/** Codes earlier builds used, and the code each one is now. */
const LEGACY: Record<string, UiLanguage> = { "zh-CN": "zh-Hans" };

const NAMES: Record<UiLanguage, string> = {
  en: "English",
  "zh-Hans": "简体中文",
  "zh-Hant": "繁體中文",
  ja: "日本語",
  es: "Español",
  de: "Deutsch",
  pt: "Português",
  fr: "Français",
  ko: "한국어",
  ru: "Русский",
  id: "Bahasa Indonesia",
};

export type LanguageCommand =
  | { kind: "show" }
  | { kind: "set"; language: UiLanguage | null };

function isUiLanguage(value: string): value is UiLanguage {
  return (UI_LANGUAGES as readonly string[]).includes(value);
}

/** Arguments after `settings language`; null = usage. */
export function parseLanguageCommand(
  values: readonly string[],
): LanguageCommand | null {
  const rest = values.filter((value) => value !== "--json");
  if (rest.length === 0) return { kind: "show" };
  if (rest.length !== 1) return null;
  const raw = rest[0] ?? "";
  const value: string = Object.hasOwn(LEGACY, raw) ? LEGACY[raw] : raw;
  if (value === "system") return { kind: "set", language: null };
  return isUiLanguage(value) ? { kind: "set", language: value } : null;
}

export interface LanguageState {
  ui_language: UiLanguage | null;
  sdk_language: string;
  rebuilt?: boolean;
}

function languageState(value: unknown): LanguageState | null {
  if (!value || typeof value !== "object") return null;
  const item = value as Record<string, unknown>;
  if (
    !(item.ui_language === null || isUiLanguage(String(item.ui_language))) ||
    typeof item.sdk_language !== "string"
  ) {
    return null;
  }
  return {
    ui_language: item.ui_language as UiLanguage | null,
    sdk_language: item.sdk_language,
    ...(typeof item.rebuilt === "boolean" ? { rebuilt: item.rebuilt } : {}),
  };
}

export type LanguageDocument = {
  schema_version: 1;
  product: string;
  command: "settings language";
} & (
  | { state: "ok" | "unchanged"; language: LanguageState }
  | { state: "committed"; language: LanguageState; rebuilt: boolean }
  | { state: "rejected" | "unknown" | "unavailable"; code: string }
);

const BASE = {
  schema_version: 1 as const,
  product: PRODUCT,
  command: "settings language" as const,
};

const PATH = "/api/v1/settings/ui-language";

export async function runLanguageCommand(
  command: LanguageCommand,
  reader: CoreReader,
  operations: CorePutOperations,
): Promise<LanguageDocument> {
  const read = await reader<unknown>(PATH);
  if (read.state !== "ok") {
    return { ...BASE, state: "unavailable", code: read.code };
  }
  const current = languageState(read.data);
  if (current === null) {
    return { ...BASE, state: "unavailable", code: "response_invalid" };
  }
  if (command.kind === "show") {
    return { ...BASE, state: "ok", language: current };
  }
  if (current.ui_language === command.language) {
    return { ...BASE, state: "unchanged", language: current };
  }
  const result = await operations.put<unknown>(PATH, {
    ui_language: command.language,
  });
  if (result.state !== "committed") {
    return { ...BASE, state: result.state, code: result.code };
  }
  const changed = languageState(result.data);
  if (changed === null) {
    return { ...BASE, state: "unknown", code: "response_invalid" };
  }
  return {
    ...BASE,
    state: "committed",
    language: changed,
    rebuilt: changed.rebuilt === true,
  };
}

export function languageExitCode(document: LanguageDocument): number {
  return ["ok", "unchanged", "committed"].includes(document.state) ? 0 : 1;
}

function sdkText(sdkLanguage: string): string {
  return sdkLanguage === "auto"
    ? "auto（SDK 自动识别中文或英文）"
    : `${sdkLanguage}（提取等 SDK 提示使用该语言）`;
}

export function renderLanguageText(document: LanguageDocument): string {
  if ("code" in document) {
    return `界面语言未能${document.state === "unavailable" ? "读取" : "修改"}：${document.code}\n`;
  }
  const state = document.language;
  const lines = [
    `界面语言：${
      state.ui_language === null
        ? "未设置（SDK 自动识别）"
        : `${state.ui_language}（${NAMES[state.ui_language]}）`
    }`,
    `SDK 提示语言：${sdkText(state.sdk_language)}`,
  ];
  if (document.state === "ok") {
    lines.push(
      `可选：${UI_LANGUAGES.join("、")}、system。任一语言（包括 zh-Hans、en）都会让 SDK 使用该语言；system 不指定语言，SDK 自动识别中文或英文。`,
    );
  }
  if (document.state === "unchanged") lines.push("没有需要改动的设置。");
  if (document.state === "committed") {
    lines.push(
      document.rebuilt
        ? "已更新。正在进行的写入按原语言完成，之后的写入和检索使用新语言。"
        : "已保存，SDK 提示语言没有变化。",
    );
  }
  return `${lines.join("\n")}\n`;
}
