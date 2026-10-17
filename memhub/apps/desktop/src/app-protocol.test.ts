import path from "node:path";
import { describe, expect, it } from "vitest";

import {
  isStartupScreenRequest,
  resolveWebResource,
  STARTUP_SCREEN_PATH,
} from "./app-protocol";
import { STARTUP_SCREEN_URL } from "./startup-screen";

describe("packaged web protocol", () => {
  it("serves only files inside the packaged web root", () => {
    const root = path.resolve("/bundle/web");

    expect(resolveWebResource(root, "one-memory://app/")).toBe(
      path.join(root, "index.html"),
    );
    expect(resolveWebResource(root, "one-memory://app/assets/app.js")).toBe(
      path.join(root, "assets/app.js"),
    );
    expect(
      resolveWebResource(root, "one-memory://other/index.html"),
    ).toBeNull();
    expect(
      resolveWebResource(root, "one-memory://app/%2F..%2Fsecret"),
    ).toBeNull();
  });
  it("recognizes the startup route and never maps it onto a file", () => {
    const root = path.resolve("/bundle/web");
    expect(isStartupScreenRequest(STARTUP_SCREEN_URL)).toBe(true);
    expect(isStartupScreenRequest("one-memory://app/index.html")).toBe(false);
    expect(isStartupScreenRequest("one-memory://other/__startup")).toBe(false);
    // The placeholder is served from memory, so a file of that name inside the
    // web bundle must never be reachable through it.
    expect(resolveWebResource(root, STARTUP_SCREEN_URL)).toBeNull();
    expect(STARTUP_SCREEN_PATH).toBe("/__startup");
  });
});
