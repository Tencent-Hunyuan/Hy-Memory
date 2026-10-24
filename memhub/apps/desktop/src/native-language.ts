import type { LanguageSnapshot } from "@one-memory/i18n";

/** Shared committed GUI language for native menus, tray, and dialogs. */
export interface NativeLanguageSource {
  read(): LanguageSnapshot;
  subscribe(listener: (snapshot: LanguageSnapshot) => void): () => void;
}
