import type { GuiLocale, LanguagePreference } from "@one-memory/i18n";
import type { NativeLanguageSource } from "./native-language";

/** What Core is told: a code, or null for "follow the system". */
export function coreUiLanguage(
  preference: LanguagePreference,
): GuiLocale | null {
  return preference === "system" ? null : preference;
}

/**
 * Keep Core's `ui_language` setting on the display language a person chose.
 *
 * Core builds every SDK client with it: zh-Hans and "follow the system" pass
 * no language (the SDK keeps detecting zh/en), any other choice sets the SDK
 * prompt language. Sent once when installed, so a choice made before this
 * existed reaches Core, then on every committed change; a value Core already
 * has is not sent again. Best effort: a failed send is reported and retried
 * with the next change or launch, and never blocks the GUI.
 */
export function installCoreLanguageSync(options: {
  language: NativeLanguageSource;
  send(uiLanguage: GuiLocale | null): Promise<void>;
  reportError?(error: unknown): void;
}): () => void {
  let closed = false;
  let sent: GuiLocale | null | undefined;
  let queue: Promise<void> = Promise.resolve();
  const push = () => {
    queue = queue.then(async () => {
      if (closed) return;
      const value = coreUiLanguage(options.language.read().preference);
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
