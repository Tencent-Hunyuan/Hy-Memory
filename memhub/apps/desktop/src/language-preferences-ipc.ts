import { isLanguagePreference, type LanguageSnapshot } from "@one-memory/i18n";
import type { LanguagePreferences } from "./language-preferences";

const GET = "one-memory:get-language-preference";
const SET = "one-memory:set-language-preference";

export function installLanguagePreferencesIpc<Event>(options: {
  ipc: {
    handle(
      name: string,
      handler: (event: Event, ...args: unknown[]) => Promise<unknown>,
    ): unknown;
    removeHandler(name: string): void;
  };
  preferences: LanguagePreferences;
  requireTrustedWindow(event: Event): unknown;
  broadcast(snapshot: LanguageSnapshot): void;
}): () => void {
  let closed = false;
  const requireTrusted = (event: Event) => {
    if (closed) throw Error("language_preferences_closed");
    options.requireTrustedWindow(event);
  };
  options.ipc.handle(GET, async (event, ...args) => {
    requireTrusted(event);
    if (args.length) throw Error("invalid_language_preference_arguments");
    return options.preferences.read();
  });
  options.ipc.handle(SET, async (event, ...args) => {
    requireTrusted(event);
    if (args.length !== 1 || !isLanguagePreference(args[0]))
      throw Error("invalid_language_preference");
    const snapshot = await options.preferences.set(args[0]);
    requireTrusted(event);
    return snapshot;
  });
  const unsubscribe = options.preferences.subscribe((snapshot) => {
    if (!closed) options.broadcast(snapshot);
  });
  return () => {
    if (closed) return;
    closed = true;
    unsubscribe();
    options.ipc.removeHandler(GET);
    options.ipc.removeHandler(SET);
  };
}
