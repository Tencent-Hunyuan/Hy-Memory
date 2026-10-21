import { EventEmitter } from "node:events";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { installEarlyStartupErrorHandler } from "./early-startup-errors";

const roots: string[] = [];

afterEach(async () => {
  await Promise.all(
    roots.splice(0).map((root) => rm(root, { recursive: true })),
  );
});

describe("early desktop startup errors", () => {
  it("replaces an earlier handler on the same process emitter", () => {
    const processEvents = new EventEmitter();
    const options = {
      processEvents,
      dataDir: os.tmpdir(),
      stderr: { write: vi.fn(() => true) },
      showError: vi.fn(),
      exit: vi.fn(),
    };

    installEarlyStartupErrorHandler(options);
    const dispose = installEarlyStartupErrorHandler(options);

    expect(processEvents.listenerCount("uncaughtException")).toBe(1);
    expect(processEvents.listenerCount("unhandledRejection")).toBe(1);
    dispose();
  });

  it("writes stderr and diagnostics before showing the fatal dialog", async () => {
    const dataDir = await mkdtemp(
      path.join(os.tmpdir(), "desktop-early-error-"),
    );
    roots.push(dataDir);
    const processEvents = new EventEmitter();
    const order: string[] = [];
    const showError = vi.fn(
      (_title: string, _message: string) => void order.push("dialog"),
    );
    const dispose = installEarlyStartupErrorHandler({
      processEvents,
      dataDir,
      stderr: { write: vi.fn(() => (order.push("stderr"), true)) },
      showError,
      getLanguage: () => "en",
      exit: vi.fn(() => void order.push("exit")),
      afterDiagnosticWrite: () => order.push("diagnostic"),
    });

    processEvents.emit(
      "uncaughtException",
      Object.assign(new Error("desktop_test_real_home_mismatch"), {
        stack:
          "Error: desktop_test_real_home_mismatch\n    at boot (apps/desktop/src/main.ts:80:1)",
      }),
    );

    const log = await readFile(
      path.join(dataDir, "logs", "diagnostic-desktop.jsonl"),
      "utf8",
    );
    expect(order).toEqual(["stderr", "diagnostic", "dialog", "exit"]);
    expect(showError).toHaveBeenCalledWith(
      "MemHub could not start",
      expect.stringContaining("MemHub could not start"),
    );
    expect(showError.mock.calls[0]?.[1]).not.toContain(
      "desktop_test_real_home_mismatch",
    );
    expect(log).toContain('"error_code":"desktop_test_real_home_mismatch"');
    expect(log).toContain('"safe_stack":["apps/desktop/src/main.ts:80:1"]');
    dispose();
  });
});
