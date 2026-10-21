import {
  isUiLanguage,
  translate,
  type UiLanguage,
  type UiMessage,
} from "@one-memory/contracts";
import { useSyncExternalStore } from "react";

const storageKey = "memhub.ui-language";
const listeners = new Set<() => void>();
let language: UiLanguage = readWebLanguage();
let revision = 0;

function readWebLanguage(): UiLanguage {
  try {
    if (typeof window === "undefined" || window.oneMemory) return "zh-CN";
    const saved = window.localStorage.getItem(storageKey);
    return isUiLanguage(saved) ? saved : "zh-CN";
  } catch {
    return "zh-CN";
  }
}

function publish(next: UiLanguage): void {
  revision++;
  if (typeof document !== "undefined") document.documentElement.lang = next;
  if (language === next) return;
  language = next;
  for (const listener of [...listeners]) listener();
}

export function getUiLanguage(): UiLanguage {
  return language;
}

export function useUiLanguage(): UiLanguage {
  return useSyncExternalStore(subscribe, getUiLanguage, () => "zh-CN");
}

export function useTranslate() {
  const selected = useUiLanguage();
  return (key: UiMessage, values?: Readonly<Record<string, string | number>>) =>
    translate(selected, key, values);
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export async function setUiLanguage(next: UiLanguage): Promise<void> {
  if (!isUiLanguage(next)) throw new Error("invalid_ui_language");
  const bridge = typeof window === "undefined" ? undefined : window.oneMemory;
  if (bridge) {
    if (!bridge.setUiLanguage) throw new Error("ui_language_unavailable");
    const saved: unknown = await bridge.setUiLanguage(next);
    if (!isUiLanguage(saved)) throw new Error("invalid_ui_language");
    publish(saved);
  } else {
    window.localStorage.setItem(storageKey, next);
    publish(next);
  }
}

export function startUiLanguage(): () => void {
  const bridge = window.oneMemory;
  if (!bridge) {
    publish(readWebLanguage());
    const onStorage = (event: StorageEvent) => {
      if (event.key === storageKey) publish(readWebLanguage());
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }
  let active = true;
  const beforeRead = revision;
  const unsubscribe = bridge.onUiLanguageChanged?.((next) => {
    if (active && isUiLanguage(next)) publish(next);
  });
  if (bridge.getUiLanguage) {
    void Promise.resolve()
      .then(() => bridge.getUiLanguage?.())
      .then((value) => {
        if (active && revision === beforeRead && isUiLanguage(value))
          publish(value);
      })
      .catch(() => {});
  }
  return () => {
    active = false;
    unsubscribe?.();
  };
}
