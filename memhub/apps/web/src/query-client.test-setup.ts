import { afterEach, beforeEach, vi } from "vitest";
import { resetClient } from "./query-client";

beforeEach(() => {
  if (typeof navigator !== "undefined") {
    vi.spyOn(navigator, "language", "get").mockReturnValue("zh-CN");
  }
});

afterEach(() => {
  resetClient();
});
