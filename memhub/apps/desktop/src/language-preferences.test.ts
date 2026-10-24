import { mkdtemp, readdir, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  LanguagePreferences,
  preferredSystemLanguage,
} from "./language-preferences";

const roots: string[] = [];
async function profile() {
  const root = await mkdtemp(path.join(os.tmpdir(), "memhub-language-"));
  roots.push(root);
  return root;
}
afterEach(async () => {
  await Promise.all(
    roots.splice(0).map((root) => rm(root, { recursive: true, force: true })),
  );
});

describe("GUI language preferences", () => {
  it("defaults to system, persists only the preference in the active profile and reloads", async () => {
    const root = await profile();
    const store = await LanguagePreferences.open(root, "es-MX");
    expect(store.read()).toEqual({
      preference: "system",
      locale: "es",
      revision: 0,
    });
    expect(await readdir(root)).toEqual([]);
    expect(await store.set("zh-CN")).toEqual({
      preference: "zh-CN",
      locale: "zh-CN",
      revision: 1,
    });
    expect(
      JSON.parse(await readFile(path.join(root, "gui-language.json"), "utf8")),
    ).toEqual({ preference: "zh-CN" });
    expect((await LanguagePreferences.open(root, "es")).read().preference).toBe(
      "zh-CN",
    );
    expect(
      (await LanguagePreferences.open(await profile(), "es")).read().preference,
    ).toBe("system");
    expect(await readdir(root)).toEqual(["gui-language.json"]);
  });
  it.each(["{broken", '{"preference":"fr"}', "null", "[]"])(
    "does not overwrite invalid input %s while loading",
    async (content) => {
      const root = await profile();
      const file = path.join(root, "gui-language.json");
      await writeFile(file, content);
      const store = await LanguagePreferences.open(root, "es");
      expect(store.read().preference).toBe("system");
      expect(await readFile(file, "utf8")).toBe(content);
    },
  );
  it("survives failed reads and preserves the snapshot on failed writes, then recovers", async () => {
    const save = vi
      .fn()
      .mockRejectedValueOnce(new Error("disk full"))
      .mockResolvedValue(undefined);
    const store = await LanguagePreferences.open("/synthetic/profile", "es", {
      read: async () => {
        throw new Error("unreadable");
      },
      save,
    });
    const listener = vi.fn();
    store.subscribe(listener);
    await expect(store.set("zh-CN")).rejects.toThrow(
      "language_preference_save_failed",
    );
    expect(store.read()).toEqual({
      preference: "system",
      locale: "es",
      revision: 0,
    });
    expect(listener).not.toHaveBeenCalled();
    await store.set("zh-CN");
    expect(store.read().revision).toBe(1);
    expect(listener).toHaveBeenCalledTimes(1);
  });
  it("serializes racing writes and emits only committed snapshots", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const save = vi
      .fn()
      .mockImplementationOnce(() => gate)
      .mockResolvedValue(undefined);
    const store = await LanguagePreferences.open("/synthetic/profile", "es", {
      read: async () => "{}",
      save,
    });
    const listener = vi.fn();
    const unsubscribe = store.subscribe(listener);
    const first = store.set("zh-CN");
    const second = store.set("es");
    await vi.waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(store.read().revision).toBe(0);
    release();
    expect((await first).revision).toBe(1);
    expect(await second).toEqual({
      preference: "es",
      locale: "es",
      revision: 2,
    });
    expect(listener.mock.calls.map(([value]) => value.revision)).toEqual([
      1, 2,
    ]);
    unsubscribe();
    await store.set("system");
    expect(listener).toHaveBeenCalledTimes(2);
    await expect(store.set("invalid" as "es")).rejects.toThrow();
  });
  it("uses only the primary preferred language, falling back to locale if unavailable", () => {
    expect(
      preferredSystemLanguage({
        getPreferredSystemLanguages: () => ["en", "es"],
        getLocale: () => "es",
      }),
    ).toBe("en");
    expect(
      preferredSystemLanguage({
        getPreferredSystemLanguages: () => [],
        getLocale: () => "es",
      }),
    ).toBe("es");
    expect(
      preferredSystemLanguage({
        getPreferredSystemLanguages: () => {
          throw Error();
        },
        getLocale: () => "es",
      }),
    ).toBe("es");
  });
});
