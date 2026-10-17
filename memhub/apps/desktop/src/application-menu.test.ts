import type { LanguageSnapshot } from "@one-memory/contracts/gui-i18n";
import { expect, it, vi } from "vitest";

import { installApplicationMenu } from "./application-menu";

it("rebuilds translated labels from committed language updates without exporting", () => {
  let change: (() => void) | undefined;
  const snapshot: LanguageSnapshot = {
    preference: "es",
    locale: "es",
    revision: 1,
  };
  const unsubscribe = vi.fn();
  const read = vi.fn(() => snapshot);
  const buildMenu = vi.fn(() => ({}) as never);
  const dispose = installApplicationMenu({
    productName: "MemHub",
    buildMenu,
    setMenu: vi.fn(),
    exportLogs: async () => {},
    reportError: vi.fn(),
    language: {
      read,
      subscribe: (listener) => {
        change = () => listener(snapshot);
        return unsubscribe;
      },
    },
  });
  expect(buildMenu).toHaveBeenLastCalledWith(
    expect.arrayContaining([expect.objectContaining({ label: "Ayuda" })]),
  );
  snapshot.locale = "zh-CN";
  change?.();
  expect(read).toHaveBeenCalledTimes(2);
  expect(buildMenu).toHaveBeenCalledTimes(2);
  expect(buildMenu).toHaveBeenLastCalledWith(
    expect.arrayContaining([expect.objectContaining({ label: "帮助" })]),
  );
  dispose();
  expect(unsubscribe).toHaveBeenCalledTimes(1);
});

it("exports logs from Help without requiring a renderer window", async () => {
  const exportLogs = vi.fn(async () => undefined);
  let template: Array<Record<string, unknown>> = [];
  installApplicationMenu({
    productName: "MemHub",
    buildMenu: (value) => {
      template = value as Array<Record<string, unknown>>;
      return {} as never;
    },
    setMenu: vi.fn(),
    exportLogs,
    reportError: vi.fn(),
  });
  const help = template.find((item) => item.label === "帮助");
  expect(help).toBeDefined();
  const submenu = help?.submenu as Array<Record<string, unknown>> | undefined;
  const exportItem = submenu?.find(
    (item) => item.label === "导出日志（发给开发排查）",
  );
  expect(exportItem).toBeDefined();
  const click = exportItem?.click;
  if (typeof click !== "function") throw new Error("missing export click");
  await (click as () => Promise<void>)();
  expect(exportLogs).toHaveBeenCalledOnce();
});
