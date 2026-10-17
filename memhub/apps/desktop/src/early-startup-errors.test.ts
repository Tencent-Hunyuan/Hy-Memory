import { EventEmitter } from "node:events";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { installEarlyStartupErrorHandler } from "./early-startup-errors";
import { stableRuntimeUserMessage } from "./stable-runtime";

const roots: string[] = [];

afterEach(async () => {
  await Promise.all(
    roots.splice(0).map((root) => rm(root, { recursive: true })),
  );
});

describe("early desktop startup errors", () => {
  it.each(["es", "throws"] as const)(
    "uses best-effort language (%s) without changing safe error codes",
    async (language) => {
      const dataDir = await mkdtemp(
        path.join(os.tmpdir(), "desktop-early-language-"),
      );
      roots.push(dataDir);
      const processEvents = new EventEmitter();
      const showError = vi.fn();
      const exit = vi.fn();
      const dispose = installEarlyStartupErrorHandler({
        processEvents,
        dataDir,
        stderr: { write: vi.fn(() => true) },
        showError,
        exit,
        readLocale: () => {
          if (language === "throws") throw new Error("unavailable");
          return "es";
        },
      });
      processEvents.emit(
        "uncaughtException",
        new Error("synthetic_safe_failure"),
      );
      expect(showError).toHaveBeenCalledExactlyOnceWith(
        language === "es" ? "MemHub no pudo iniciarse" : "MemHub 无法启动",
        stableRuntimeUserMessage(new Error("synthetic_safe_failure")),
      );
      expect(exit).toHaveBeenCalledExactlyOnceWith(1);
      dispose();
    },
  );

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

  it.each(["es", undefined] as const)(
    "uses a safe title for initialized locale %s and retains the technical code",
    async (locale) => {
      const dataDir = await mkdtemp(
        path.join(os.tmpdir(), "desktop-early-locale-"),
      );
      roots.push(dataDir);
      const processEvents = new EventEmitter();
      const showError = vi.fn();
      const exit = vi.fn();
      const dispose = installEarlyStartupErrorHandler({
        processEvents,
        dataDir,
        stderr: { write: vi.fn(() => true) },
        showError,
        exit,
        readLocale: () => locale,
      });
      processEvents.emit(
        "uncaughtException",
        new Error("desktop_startup_failed"),
      );
      expect(showError).toHaveBeenCalledExactlyOnceWith(
        locale === "es" ? "MemHub no pudo iniciarse" : "MemHub 无法启动",
        stableRuntimeUserMessage(new Error("desktop_startup_failed")),
      );
      expect(exit).toHaveBeenCalledExactlyOnceWith(1);
      dispose();
    },
  );

  it("writes stderr and diagnostics before showing the fatal dialog", async () => {
    const dataDir = await mkdtemp(
      path.join(os.tmpdir(), "desktop-early-error-"),
    );
    roots.push(dataDir);
    const processEvents = new EventEmitter();
    const order: string[] = [];
    const dispose = installEarlyStartupErrorHandler({
      processEvents,
      dataDir,
      stderr: { write: vi.fn(() => (order.push("stderr"), true)) },
      showError: vi.fn(() => void order.push("dialog")),
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
    expect(log).toContain('"error_code":"desktop_test_real_home_mismatch"');
    expect(log).toContain('"safe_stack":["apps/desktop/src/main.ts:80:1"]');
    dispose();
  });
});
