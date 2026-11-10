import { readFileSync } from "node:fs";
import { expect, it } from "vitest";

function declaration(file: string, selector: string): string {
  const css = readFileSync(new URL(`./${file}`, import.meta.url), "utf8");
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return css.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`))?.[1] ?? "";
}

it("fills settings controls without widening shared fallback controls", () => {
  const modeMenu = declaration(
    "r6-settings.css",
    ".r6-settings .r6-mode-option-menu",
  );
  expect(modeMenu).toMatch(/(?:^|[\s;])width:\s*100%\s*;/);
  expect(modeMenu).toMatch(/(?:^|[\s;])max-width:\s*none\s*;/);

  const fallback = declaration(
    "r6-settings.css",
    ".r6-settings .connection-fallback .custom-harness-name",
  );
  expect(fallback).toMatch(/(?:^|[\s;])width:\s*100%\s*;/);
  expect(fallback).toMatch(/(?:^|[\s;])max-width:\s*none\s*;/);
  expect(declaration("core-visual.css", ".custom-harness-name")).toMatch(
    /(?:^|[\s;])max-width:\s*420px\s*;/,
  );
});
