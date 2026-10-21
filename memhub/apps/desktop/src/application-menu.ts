import { translate, type UiLanguage } from "@one-memory/contracts";
import type { Menu, MenuItemConstructorOptions } from "electron";

export function installApplicationMenu(options: {
  productName: string;
  language?: UiLanguage;
  buildMenu(template: MenuItemConstructorOptions[]): Menu;
  setMenu(menu: Menu): void;
  exportLogs(): Promise<void>;
  reportError(message: string): void;
}): void {
  const language = options.language ?? "zh-CN";
  const t = (key: Parameters<typeof translate>[1]) => translate(language, key);
  const runExport = async () => {
    try {
      await options.exportLogs();
    } catch {
      options.reportError(t("日志未能导出，请稍后重试。"));
    }
  };
  options.setMenu(
    options.buildMenu([
      {
        label: options.productName,
        submenu: [
          {
            role: "about",
            label:
              language === "en"
                ? `About ${options.productName}`
                : `关于 ${options.productName}`,
          },
          { type: "separator" },
          {
            role: "quit",
            label:
              language === "en"
                ? `Quit ${options.productName}`
                : `退出 ${options.productName}`,
          },
        ],
      },
      {
        label: language === "en" ? "Edit" : "编辑",
        submenu: [
          { role: "undo", label: language === "en" ? "Undo" : "撤销" },
          { role: "redo", label: language === "en" ? "Redo" : "重做" },
          { type: "separator" },
          { role: "cut", label: language === "en" ? "Cut" : "剪切" },
          { role: "copy", label: language === "en" ? "Copy" : "复制" },
          { role: "paste", label: language === "en" ? "Paste" : "粘贴" },
          {
            role: "selectAll",
            label: language === "en" ? "Select All" : "全选",
          },
        ],
      },
      {
        label: language === "en" ? "Window" : "窗口",
        submenu: [
          {
            role: "minimize",
            label: language === "en" ? "Minimize" : "最小化",
          },
          { role: "zoom", label: language === "en" ? "Zoom" : "缩放" },
          { role: "close", label: language === "en" ? "Close" : "关闭" },
          {
            role: "front",
            label: language === "en" ? "Bring All to Front" : "全部置于最前",
          },
        ],
      },
      {
        label: t("帮助"),
        submenu: [
          {
            label: t("导出日志（发给开发排查）"),
            click: runExport,
          },
        ],
      },
    ]),
  );
}
