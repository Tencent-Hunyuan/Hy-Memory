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
  it("says an upgrade may take a while instead of leaving a silent wait", () => {
    expect(startupScreenHtml()).toContain(
      "首次启动或升级记忆数据可能需要几分钟，完成后会自动进入应用。",
    );
    expect(startupScreenHtml("en")).toContain(
      "The first launch or a memory-data upgrade can take a few minutes.",
    );
  });
  it("keeps a draggable strip so the waiting window can be moved", () => {
    const html = startupScreenHtml();
    // CSS alone does not move a frameless window: the region has to be a real
    // element in the document, and it has to be the top strip.
    expect(html).toContain('<div class="window-drag-region"');
    expect(html).toContain("-webkit-app-region: drag");
  });
  it("is served from the app origin, not from the web bundle", () => {
    expect(STARTUP_SCREEN_URL).toBe("one-memory://app/__startup");
  });
});
