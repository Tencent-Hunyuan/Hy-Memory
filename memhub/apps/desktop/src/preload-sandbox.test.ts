import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { runInNewContext } from "node:vm";
import { expect, it, vi } from "vitest";

it("loads the production preload in a sandbox with Electron as its only module", () => {
  const output = mkdtempSync(path.join(os.tmpdir(), "memhub-preload-"));
  try {
    execFileSync(
      path.join(process.cwd(), "node_modules", ".bin", "tsup"),
      ["--config", "tsup.config.ts", "--out-dir", output],
      { cwd: process.cwd(), stdio: "pipe" },
    );
    const source = readFileSync(path.join(output, "preload.js"), "utf8");
    const required: string[] = [];
    let exposed: Record<string, unknown> | null = null;
    const ipcRenderer = {
      invoke: vi.fn(async () => undefined),
      on: vi.fn(),
      removeListener: vi.fn(),
    };

    runInNewContext(
      source,
      {
        module: { exports: {} },
        exports: {},
        console,
        window: {
          addEventListener: vi.fn(),
          removeEventListener: vi.fn(),
        },
        require(name: string) {
          required.push(name);
          if (name === "electron") {
            return {
              ipcRenderer,
              contextBridge: {
                exposeInMainWorld(
                  bridgeName: string,
                  value: Record<string, unknown>,
                ) {
                  expect(bridgeName).toBe("oneMemory");
                  exposed = value;
                },
              },
            };
          }
          throw new Error(`sandbox module not found: ${name}`);
        },
      },
      { filename: "dist/preload.js" },
    );

    expect(required).toEqual(["electron"]);
    expect(exposed).not.toBeNull();
    const bridge = exposed as unknown as Record<string, unknown>;
    expect(bridge.getApiBaseUrl).toBeTypeOf("function");
    expect(bridge.getApiToken).toBeTypeOf("function");
    expect(bridge.getLanguagePreference).toBeTypeOf("function");
    expect(bridge.setLanguagePreference).toBeTypeOf("function");
    expect(bridge.onLanguagePreferenceChanged).toBeTypeOf("function");
  } finally {
    rmSync(output, { recursive: true, force: true });
  }
});
