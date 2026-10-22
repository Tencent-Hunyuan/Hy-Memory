import { EventEmitter } from "node:events";
import type { LanguageSnapshot } from "@one-memory/i18n";
import type {
  App,
  Menu,
  MenuItem,
  MenuItemConstructorOptions,
  NativeImage,
  Tray,
} from "electron";
import { describe, expect, it, vi } from "vitest";
import { installBackgroundTray } from "./background-tray";
import type { LoginPreference } from "./login-preference";

async function flush() {
  await new Promise((resolve) => setTimeout(resolve, 0));
}

function fixture(
  packaged = true,
  platform: NodeJS.Platform = "darwin",
  trial = false,
) {
  let state: LoginPreference = {
    supported: true,
    enabled: false,
    status: "disabled",
  };
  let changed: (() => void) | undefined;
  const languageSnapshot: LanguageSnapshot = {
    preference: "zh-CN",
    locale: "zh-CN",
    revision: 1,
  };
  const app = { isPackaged: packaged, quit: vi.fn() };
  const tray = Object.assign(new EventEmitter(), {
    setToolTip: vi.fn(),
    setContextMenu: vi.fn(),
    destroy: vi.fn(),
  });
  const image = { isEmpty: () => false, resize: vi.fn() };
  image.resize.mockReturnValue(image);
  const menus: MenuItemConstructorOptions[][] = [];
  const readLoginPreference = vi.fn(async () => state);
  const setLoginPreference = vi.fn(async (enabled: boolean) => {
    state = {
      supported: true,
      enabled,
      status: enabled ? "enabled" : "disabled",
    };
    return state;
  });
  const open = vi.fn();
  const reportError = vi.fn();
  const install = () =>
    installBackgroundTray({
      app: app as unknown as App,
      platform,
      language: {
        read: () => languageSnapshot,
        subscribe: (listener) => {
          changed = () => listener(languageSnapshot);
          return () => {
            changed = undefined;
          };
        },
      },
      resourcesPath: "/bundle/Contents/Resources",
      appPath: "/repo/apps/desktop",
      createImage: vi.fn(() => image as unknown as NativeImage),
      createTray: vi.fn(() => tray as unknown as Tray),
      buildMenu: (template) => {
        menus.push(template);
        return {} as Menu;
      },
      open,
      reportError,
      readLoginPreference,
      setLoginPreference,
      ...(trial
        ? { allowLoginStartup: false, productName: "One Memory Trial" }
        : {}),
    });
  const menu = () => menus.at(-1) ?? [];
  const click = (index: number, checked = false) =>
    menu()[index]?.click?.({ checked } as MenuItem, null as never, {} as never);
  return {
    app,
    tray,
    image,
    menus,
    readLoginPreference,
    setLoginPreference,
    open,
    reportError,
    install,
    menu,
    click,
    changeLanguage(locale: LanguageSnapshot["locale"]) {
      languageSnapshot.locale = locale;
      changed?.();
    },
    setState(value: LoginPreference) {
      state = value;
    },
  };
}

describe("native macOS background tray", () => {
  it("rebuilds Spanish and Chinese labels without changing login or stopping services", async () => {
    const f = fixture();
    f.install();
    await flush();
    f.changeLanguage("es");
    await flush();
    expect(f.menu()[0]?.label).toBe("Abrir MemHub");
    expect(f.menu()[1]?.label).toBe("Iniciar MemHub al iniciar sesión");
    expect(f.menu()[3]?.label).toBe(
      "Salir de MemHub (el servicio sigue en segundo plano)",
    );
    expect(f.setLoginPreference).not.toHaveBeenCalled();
    f.click(0);
    expect(f.open).toHaveBeenCalledOnce();
    f.changeLanguage("zh-CN");
    await flush();
    expect(f.menu()[0]?.label).toBe("打开 MemHub");
    f.click(3);
    expect(f.app.quit).toHaveBeenCalledOnce();
  });
  it("reads and changes the unified Runtime login preference", async () => {
    const f = fixture();
    const dispose = f.install();
    await flush();

    expect(f.menu()[1]).toMatchObject({
      type: "checkbox",
      checked: false,
      enabled: true,
    });
    f.click(1, true);
    await flush();
    expect(f.setLoginPreference).toHaveBeenCalledExactlyOnceWith(true);
    expect(f.menu()[1]).toMatchObject({ checked: true, enabled: true });

    f.tray.emit("right-click");
    await flush();
    expect(f.readLoginPreference).toHaveBeenCalled();
    dispose();
  });

  it("keeps a CLI-owned service visible and read-only", async () => {
    const f = fixture();
    f.setState({
      supported: false,
      enabled: true,
      status: "managed-by-cli",
    });
    f.install();
    await flush();

    expect(f.menu()[1]).toMatchObject({ checked: true, enabled: false });
    expect(f.menu()[1]?.label).toContain("由 CLI 管理");
    f.click(1, false);
    expect(f.setLoginPreference).not.toHaveBeenCalled();
  });

  it("quits only the UI and states that the service continues", async () => {
    const f = fixture();
    const dispose = f.install();
    await flush();
    expect(f.menu()[3]?.label).toContain("后台服务继续运行");
    f.click(0);
    expect(f.open).toHaveBeenCalledOnce();
    f.click(3);
    expect(f.app.quit).toHaveBeenCalledOnce();
    dispose();
    dispose();
    expect(f.tray.destroy).toHaveBeenCalledOnce();
    expect(f.tray.eventNames()).toEqual([]);
  });

  it("blocks login changes for trial, development and non-macOS builds", async () => {
    for (const f of [
      fixture(true, "darwin", true),
      fixture(false, "darwin"),
      fixture(true, "linux"),
    ]) {
      const dispose = f.install();
      await flush();
      if (f.menus.length) {
        expect(f.menu()[1]).toMatchObject({ enabled: false, checked: false });
        f.click(1, true);
      }
      expect(f.setLoginPreference).not.toHaveBeenCalled();
      dispose();
    }
  });

  it("reports failed changes and restores the confirmed state", async () => {
    const f = fixture();
    f.setLoginPreference.mockRejectedValueOnce(new Error("denied"));
    f.install();
    await flush();
    f.click(1, true);
    await flush();
    expect(f.reportError).toHaveBeenCalledOnce();
    expect(f.menu()[1]).toMatchObject({ checked: false });
  });

  it("rejects a missing tray icon", () => {
    const f = fixture();
    f.image.isEmpty = () => true;
    expect(f.install).toThrow("desktop_tray_icon_unavailable");
  });
});
