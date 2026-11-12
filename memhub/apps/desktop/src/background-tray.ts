import path from "node:path";
import { type MessageKey, translate } from "@one-memory/i18n";
import type {
  App,
  Menu,
  MenuItemConstructorOptions,
  NativeImage,
  Tray,
} from "electron";
import type { LoginPreference } from "./login-preference";
import type { NativeLanguageSource } from "./native-language";

export type RuntimeHosting = "background-service" | "app-hosted" | "unknown";

export const LOGIN_SERVICE_NAME = "com.tencent.onememory.runtime.plist";

interface TrayOptions {
  app: Pick<App, "isPackaged" | "quit">;
  platform: NodeJS.Platform;
  resourcesPath: string;
  appPath: string;
  createImage(file: string): NativeImage;
  createTray(image: NativeImage): Tray;
  buildMenu(template: MenuItemConstructorOptions[]): Menu;
  open(): void;
  reportError(message: string): void;
  readLoginPreference(): Promise<LoginPreference>;
  setLoginPreference(enabled: boolean): Promise<LoginPreference>;
  /** Who keeps the Runtime alive, re-read on every menu refresh: the
   * background service (quit leaves it running) or this App (quit stops it). */
  readRuntimeHosting?(): Promise<RuntimeHosting>;
  allowLoginStartup?: boolean;
  productName?: string;
  language?: NativeLanguageSource;
}

export function installBackgroundTray(options: TrayOptions): () => void {
  if (options.platform !== "darwin") return () => {};
  const { app } = options;
  const iconPath = app.isPackaged
    ? path.join(options.resourcesPath, "desktop", "icon.png")
    : path.join(options.appPath, "assets", "icon.png");
  const image = options.createImage(iconPath);
  if (image.isEmpty()) throw new Error("desktop_tray_icon_unavailable");
  const tray = options.createTray(image.resize({ width: 18, height: 18 }));
  let disposed = false;
  const productName = options.productName ?? "MemHub";
  tray.setToolTip(productName);
  let generation = 0;
  const refresh = async () => {
    if (disposed) return;
    const locale = options.language?.read().locale ?? "zh-Hans";
    const t = (key: MessageKey) =>
      translate(locale, key, { product: productName });
    const current = ++generation;
    let checked = false;
    let enabled = app.isPackaged && options.allowLoginStartup !== false;
    let label = t("nativeLogin");
    if (options.allowLoginStartup === false) {
      label += t("nativeTrialUnavailable");
    } else if (app.isPackaged) {
      try {
        const state = await options.readLoginPreference();
        if (disposed || current !== generation) return;
        checked = state.enabled;
        enabled = state.supported;
        if (state.status === "managed-by-cli") label += t("nativeCliManaged");
      } catch {
        label += t("nativeStatusUnavailable");
        enabled = false;
      }
    } else {
      label += t("nativeDevelopmentUnavailable");
    }
    let hosting: RuntimeHosting = "unknown";
    try {
      hosting = (await options.readRuntimeHosting?.()) ?? "unknown";
    } catch {
      hosting = "unknown";
    }
    if (disposed || current !== generation) return;
    tray.setContextMenu(
      options.buildMenu([
        { label: t("nativeOpen"), click: options.open },
        {
          label,
          type: "checkbox",
          checked,
          enabled,
          click: (item) => {
            if (disposed || !app.isPackaged || !enabled) return;
            void options
              .setLoginPreference(item.checked)
              .catch(() =>
                options.reportError(
                  translate(
                    options.language?.read().locale ?? "zh-Hans",
                    "nativeLoginError",
                  ),
                ),
              )
              .finally(() => void refresh());
          },
        },
        { type: "separator" },
        {
          // Unknown keeps the plain label: quit then stops nothing.
          label: t(
            hosting === "app-hosted" ? "nativeQuitStopsService" : "nativeQuit",
          ),
          click: () => {
            if (!disposed) app.quit();
          },
        },
      ]),
    );
  };
  const unsubscribeLanguage = options.language?.subscribe(() => void refresh());
  try {
    void refresh();
    // Refresh before the native menu opens, including changes made in Settings.
    tray.on("mouse-down", () => void refresh());
    tray.on("right-click", () => void refresh());
  } catch (error) {
    tray.destroy();
    throw error;
  }
  return () => {
    if (disposed) return;
    disposed = true;
    unsubscribeLanguage?.();
    tray.removeAllListeners("mouse-down");
    tray.removeAllListeners("right-click");
    tray.destroy();
  };
}
