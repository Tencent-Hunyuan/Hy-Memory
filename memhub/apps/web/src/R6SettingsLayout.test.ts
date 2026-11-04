import { readFileSync } from "node:fs";
import { expect, it } from "vitest";

function declaration(file: string, selector: string): string {
  const css = readFileSync(new URL(`./${file}`, import.meta.url), "utf8");
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return css.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`))?.[1] ?? "";
}

it("fills settings controls without widening shared fallback controls", () => {
  for (const selector of [
    ".r6-memory-mode-settings .memory-mode-choice-list",
    ".r6-settings .connection-fallback .custom-harness-name",
  ]) {
    const rule = declaration("r6-settings.css", selector);
    expect(rule, selector).toMatch(/(?:^|[\s;])width:\s*100%\s*;/);
    expect(rule, selector).toMatch(/(?:^|[\s;])max-width:\s*none\s*;/);
  }
  expect(declaration("core-visual.css", ".custom-harness-name")).toMatch(
    /(?:^|[\s;])max-width:\s*420px\s*;/,
  );
});
