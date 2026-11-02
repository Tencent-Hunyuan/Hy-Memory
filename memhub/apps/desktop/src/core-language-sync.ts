import type { GuiLocale, LanguageSnapshot } from "@one-memory/i18n";
import type { NativeLanguageSource } from "./native-language";

/**
 * What Core is told: the language the GUI shows. "Follow the system" sends
 * the language it resolved to, so the memories match the interface a person
 * sees; the App never leaves the SDK to detect zh/en (#243).
 */
export function coreUiLanguage(snapshot: LanguageSnapshot): GuiLocale {
  return snapshot.locale;
}

/**
 * Keep Core's `ui_language` setting on the display language the GUI shows.
 *
 * Core builds every SDK client with it and passes it, zh-Hans included, as
 * the SDK prompt language. Sent once when installed, so a choice made before
 * this existed, or the system language followed at this launch, reaches
 * Core; then on every committed change. A value Core already has is not sent
 * again. Best effort: a failed send is reported and retried with the next
 * change or launch, and never blocks the GUI.
 */
export function installCoreLanguageSync(options: {
  language: NativeLanguageSource;
  send(uiLanguage: GuiLocale): Promise<void>;
  reportError?(error: unknown): void;
}): () => void {
  let closed = false;
  let sent: GuiLocale | undefined;
  let queue: Promise<void> = Promise.resolve();
  const push = () => {
    queue = queue.then(async () => {
      if (closed) return;
      const value = coreUiLanguage(options.language.read());
      if (value === sent) return;
      try {
        await options.send(value);
        sent = value;
      } catch (error) {
        options.reportError?.(error);
      }
    });
  };
  const unsubscribe = options.language.subscribe(push);
  push();
  return () => {
    closed = true;
    unsubscribe();
  };
}
