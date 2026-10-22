import { describe, expect, it } from "vitest";
import { STARTUP_SCREEN_URL, startupScreenHtml } from "./startup-screen";

describe("startup screen", () => {
  it("is a self-contained page that asks the main process for nothing", () => {
    const html = startupScreenHtml();
    expect(html).toContain("MemHub");
    // It renders before `initialize` has registered a single handler, so any
    // bridge call here would fail and put the placeholder in an error state
    // for the whole of a cold start.
    expect(html).not.toContain("oneMemory");
    expect(html).not.toContain("ipcRenderer");
  });
  it("speaks the interface language and keeps Chinese by default", () => {
    expect(startupScreenHtml()).toContain('<html lang="zh-CN">');
    expect(startupScreenHtml()).toContain("正在启动，正在准备记忆服务…");
    const spanish = startupScreenHtml("es");
    expect(spanish).toContain('<html lang="es">');
    expect(spanish).toContain(
      "Iniciando y preparando el servicio de memorias…",
    );
    expect(spanish).not.toMatch(/[一-鿿]/);
  });
  it("is served from the app origin, not from the web bundle", () => {
    expect(STARTUP_SCREEN_URL).toBe("one-memory://app/__startup");
  });
});
