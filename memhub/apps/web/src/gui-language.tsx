import {
  isLanguagePreference,
  isLanguageSnapshot,
  type LanguagePreference,
  type LanguageSnapshot,
  plainCount,
  resolveGuiLocale,
  translate,
  translateCount,
} from "@one-memory/contracts/gui-i18n";
import {
  createContext,
  type ReactNode,
  useContext,
  useEffect,
  useState,
  useSyncExternalStore,
} from "react";

export const LANGUAGE_STORAGE_KEY = "one-memory.gui-language";
type LanguageError = "languageLoadError" | "languageSaveError" | null;
type State = LanguageSnapshot & { error: LanguageError };
const initial: State = {
  preference: "system",
  locale: "zh-CN",
  revision: 0,
  error: null,
};

function createLanguageStore() {
  let state = initial;
  let revision = -1;
  let generation = 0;
  let writeRequest = 0;
  let bridge: Window["oneMemory"];
  const listeners = new Set<() => void>();
  const publish = (next: State) => {
    state = next;
    for (const listener of listeners) listener();
  };
  const accept = (snapshot: unknown) => {
    if (!isLanguageSnapshot(snapshot))
      throw new Error("Invalid language snapshot");
    if (snapshot.revision < revision) return;
    if (
      snapshot.revision === revision &&
      (snapshot.preference !== state.preference ||
        snapshot.locale !== state.locale)
    )
      return;
    revision = snapshot.revision;
    publish({ ...snapshot, error: null });
  };
  const readBrowser = () => {
    try {
      const stored = window.localStorage.getItem(LANGUAGE_STORAGE_KEY);
      const preference = isLanguagePreference(stored) ? stored : "system";
      publish({
        preference,
        locale: resolveGuiLocale(preference, navigator.language),
        revision: state.revision + 1,
        error: null,
      });
    } catch {
      publish({ ...state, error: "languageLoadError" });
    }
  };
  return {
    getSnapshot: () => state,
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    start() {
      const current = ++generation;
      bridge = window.oneMemory;
      if (bridge) {
        let unsubscribe: (() => void) | undefined;
        const beforeRead = revision;
        try {
          if (
            !bridge.onLanguagePreferenceChanged ||
            !bridge.getLanguagePreference
          )
            throw new Error("Language bridge unavailable");
          unsubscribe = bridge.onLanguagePreferenceChanged((snapshot) => {
            if (generation !== current) return;
            try {
              accept(snapshot);
            } catch {
              publish({ ...state, error: "languageLoadError" });
            }
          });
          void bridge
            .getLanguagePreference()
            .then((snapshot) => {
              if (generation === current) accept(snapshot);
            })
            .catch(() => {
              if (generation === current && revision === beforeRead)
                publish({ ...state, error: "languageLoadError" });
            });
        } catch {
          publish({ ...state, error: "languageLoadError" });
        }
        return () => {
          generation++;
          unsubscribe?.();
        };
      }
      const storage = (event: StorageEvent) => {
        if (event.key !== null && event.key !== LANGUAGE_STORAGE_KEY) return;
        try {
          if (
            event.storageArea !== null &&
            event.storageArea !== window.localStorage
          )
            return;
          readBrowser();
        } catch {
          publish({ ...state, error: "languageLoadError" });
        }
      };
      const languageChange = () => {
        if (state.preference === "system")
          publish({
            ...state,
            locale: resolveGuiLocale("system", navigator.language),
          });
      };
      window.addEventListener("storage", storage);
      window.addEventListener("languagechange", languageChange);
      readBrowser();
      return () => {
        generation++;
        window.removeEventListener("storage", storage);
        window.removeEventListener("languagechange", languageChange);
      };
    },
    async setPreference(preference: LanguagePreference) {
      const current = generation;
      const request = ++writeRequest;
      try {
        if (!isLanguagePreference(preference))
          throw new Error("Invalid language preference");
        if (bridge) {
          if (!bridge.setLanguagePreference)
            throw new Error("Language bridge unavailable");
          const snapshot = await bridge.setLanguagePreference(preference);
          if (generation === current) accept(snapshot);
        } else {
          window.localStorage.setItem(LANGUAGE_STORAGE_KEY, preference);
          publish({
            preference,
            locale: resolveGuiLocale(preference, navigator.language),
            revision: state.revision + 1,
            error: null,
          });
        }
      } catch {
        if (generation === current && request === writeRequest)
          publish({ ...state, error: "languageSaveError" });
      }
    },
  };
}

type LanguageContextValue = State & {
  setPreference(preference: LanguagePreference): Promise<void>;
};
// Standalone components retain the historical Chinese UI without a provider.
const LanguageContext = createContext<LanguageContextValue>({
  ...initial,
  setPreference: async () => {},
});

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [store] = useState(createLanguageStore);
  const state = useSyncExternalStore(
    store.subscribe,
    store.getSnapshot,
    store.getSnapshot,
  );
  useEffect(() => store.start(), [store]);
  useEffect(() => {
    document.documentElement.lang = state.locale;
  }, [state.locale]);
  return (
    <LanguageContext.Provider
      value={{ ...state, setPreference: store.setPreference }}
    >
      {children}
    </LanguageContext.Provider>
  );
}

export function useGuiLanguage() {
  const language = useContext(LanguageContext);
  return {
    ...language,
    t: (
      key: Parameters<typeof translate>[1],
      params?: Parameters<typeof translate>[2],
    ) => translate(language.locale, key, params),
    /** Count-aware `t`: picks the Spanish `.one` variant and groups digits. */
    tn: (
      key: Parameters<typeof translate>[1],
      count: number,
      params?: Parameters<typeof translate>[2],
    ) =>
      translateCount(language.locale, key, count, {
        ...params,
        count: new Intl.NumberFormat(language.locale).format(count),
      }),
    /** `tn` for copy whose Chinese has always shown the bare number. */
    tnPlain: (
      key: Parameters<typeof translate>[1],
      count: number,
      params?: Parameters<typeof translate>[2],
    ) =>
      translateCount(language.locale, key, count, {
        ...params,
        count: plainCount(language.locale, count),
      }),
  };
}
