import { expect, it, vi } from "vitest";

import { installApplicationMenu } from "./application-menu";

it("rebuilds native menu labels in English without changing diagnostic actions", async () => {
  let template: Array<Record<string, unknown>> = [];
  const exportLogs = vi.fn(async () => undefined);
  installApplicationMenu({
    productName: "MemHub",
    language: "en",
    buildMenu: (value) => {
      template = value as Array<Record<string, unknown>>;
      return {} as never;
    },
    setMenu: vi.fn(),
    exportLogs,
    reportError: vi.fn(),
  });
  expect(template.map((item) => item.label)).toEqual([
    "MemHub",
    "Edit",
    "Window",
    "Help",
  ]);
  const help = template[3]?.submenu as Array<Record<string, unknown>>;
  expect(help[0]?.label).toBe("Export logs for support");
  const click = help[0]?.click;
  if (typeof click !== "function") throw new Error("missing export click");
  await (click as () => Promise<void>)();
  expect(exportLogs).toHaveBeenCalledOnce();
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
