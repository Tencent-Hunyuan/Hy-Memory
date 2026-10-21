import {
  mkdtempSync,
  readFileSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { UiLanguagePreference } from "./ui-language-preference";

const roots: string[] = [];
function root() {
  const directory = mkdtempSync(path.join(tmpdir(), "ui-language-test-"));
  roots.push(directory);
  return directory;
}
afterEach(() => {
  for (const directory of roots.splice(0))
    rmSync(directory, { recursive: true, force: true });
});

describe("desktop language preference", () => {
  it("defaults to Chinese on first use and restores English before restart", () => {
    const directory = root();
    const first = new UiLanguagePreference(directory);
    expect(first.get()).toBe("zh-CN");
    first.set("en");
    expect(new UiLanguagePreference(directory).get()).toBe("en");
    expect(readFileSync(path.join(directory, "ui-language.json"), "utf8")).toBe(
      '"en"',
    );
  });

  it("rejects invalid IPC input without changing the persisted preference", () => {
    const directory = root();
    const preference = new UiLanguagePreference(directory);
    preference.set("en");
    for (const input of ["fr", "../../profile", {}, null]) {
      expect(() => preference.set(input)).toThrow("invalid_ui_language");
    }
    expect(new UiLanguagePreference(directory).get()).toBe("en");
  });

  it("ignores malformed or symlinked preference files", () => {
    const directory = root();
    const filename = path.join(directory, "ui-language.json");
    writeFileSync(filename, '"en-US"');
    expect(new UiLanguagePreference(directory).get()).toBe("zh-CN");
    rmSync(filename);
    const outside = path.join(directory, "other.json");
    writeFileSync(outside, '"zh-CN"');
    symlinkSync(outside, filename);
    expect(new UiLanguagePreference(directory).get()).toBe("zh-CN");
    new UiLanguagePreference(directory).set("en");
    expect(readFileSync(outside, "utf8")).toBe('"zh-CN"');
  });
});
