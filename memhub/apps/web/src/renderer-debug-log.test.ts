// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  installRendererDebugLogCapture,
  rendererDebugLogs,
  resetRendererDebugLogsForTest,
} from "./renderer-debug-log";

afterEach(() => resetRendererDebugLogsForTest());

it("captures only actionable console output and uncaught renderer failures", () => {
  const info = vi.spyOn(console, "info").mockImplementation(() => undefined);
  const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
  const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
  const stop = installRendererDebugLogCapture();
  console.info("routine-value", { count: 1 });
  console.warn("warning-value", { count: 2 });
  window.dispatchEvent(new ErrorEvent("error", { message: "render exploded" }));

  expect(rendererDebugLogs()).toEqual([
    expect.objectContaining({
      level: "warn",
      event: "console.warn",
      message: 'warning-value {"count":2}',
    }),
    expect.objectContaining({
      level: "error",
      event: "window.error",
      message: "render exploded",
    }),
  ]);

  stop();
  info.mockRestore();
  warn.mockRestore();
  error.mockRestore();
});
