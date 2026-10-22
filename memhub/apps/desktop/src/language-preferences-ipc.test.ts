import { describe, expect, it, vi } from "vitest";
import { LanguagePreferences } from "./language-preferences";
import { installLanguagePreferencesIpc } from "./language-preferences-ipc";

describe("language preference IPC", () => {
  it("guards reads/writes, validates payloads, broadcasts commits and disposes", async () => {
    const handlers = new Map<
      string,
      (event: string, ...args: unknown[]) => Promise<unknown>
    >();
    const save = vi.fn(async () => {});
    const preferences = await LanguagePreferences.open(
      "/synthetic/profile",
      "es",
      { read: async () => "{}", save },
    );
    const broadcast = vi.fn();
    const dispose = installLanguagePreferencesIpc({
      ipc: {
        handle: (name, handler) => handlers.set(name, handler),
        removeHandler: (name) => {
          handlers.delete(name);
        },
      },
      preferences,
      requireTrustedWindow: (event: string) => {
        if (event !== "trusted") throw Error("untrusted");
      },
      broadcast,
    });
    const read = handlers.get("one-memory:get-language-preference");
    const write = handlers.get("one-memory:set-language-preference");
    if (!read || !write) throw Error("Missing language handlers");
    await expect(read("foreign")).rejects.toThrow("untrusted");
    await expect(write("iframe", "es")).rejects.toThrow("untrusted");
    await expect(write("trusted", "fr")).rejects.toThrow();
    await expect(write("trusted", "es", "extra")).rejects.toThrow();
    expect(save).not.toHaveBeenCalled();
    expect(await read("trusted")).toEqual({
      preference: "system",
      locale: "es",
      revision: 0,
    });
    await write("trusted", "en");
    expect(broadcast).toHaveBeenCalledExactlyOnceWith({
      preference: "en",
      locale: "en",
      revision: 1,
    });
    dispose();
    dispose();
    expect(handlers.size).toBe(0);
    await expect(write("trusted", "es")).rejects.toThrow();
  });
});
