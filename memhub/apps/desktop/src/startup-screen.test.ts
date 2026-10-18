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
  it("is served from the app origin, not from the web bundle", () => {
    expect(STARTUP_SCREEN_URL).toBe("one-memory://app/__startup");
  });
});
