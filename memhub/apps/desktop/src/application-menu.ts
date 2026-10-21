import { translate } from "@one-memory/contracts/gui-i18n";
import type { Menu, MenuItemConstructorOptions } from "electron";
import type { NativeLanguageSource } from "./native-language";

export function installApplicationMenu(options: {
  productName: string;
  language?: NativeLanguageSource;
  buildMenu(template: MenuItemConstructorOptions[]): Menu;
  setMenu(menu: Menu): void;
  exportLogs(): Promise<void>;
  reportError(message: string): void;
}): () => void {
  const runExport = async () => {
    try {
      await options.exportLogs();
    } catch {
      options.reportError(
        translate(
          options.language?.read().locale ?? "zh-CN",
          "nativeExportError",
        ),
      );
    }
  };
  const refresh = () => {
    const locale = options.language?.read().locale ?? "zh-CN";
    options.setMenu(
      options.buildMenu([
        {
          label: options.productName,
          submenu: [{ role: "about" }, { type: "separator" }, { role: "quit" }],
        },
        { role: "editMenu" },
        { role: "windowMenu" },
        {
          label: translate(locale, "nativeHelp"),
          submenu: [
            {
              label: translate(locale, "nativeExportLogs"),
              click: runExport,
            },
          ],
        },
      ]),
    );
  };
  refresh();
  return options.language?.subscribe(refresh) ?? (() => {});
}
