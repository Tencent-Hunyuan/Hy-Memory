import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { builtinModules } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

/** The app root (one level above this test's `src/`). */
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const bundle = path.join(root, "dist", "one-memory.cjs");

// The published npm package must be self-contained. The bundle inlines
// `@one-memory/runtime-control` and `axios` (tsup `noExternal`), so a published
// manifest that still lists `axios` makes `npm install` pull a transitive tree
// for nothing — the earlier generator only stripped `@one-memory/*` and left
// `axios`, which installed 28 extra packages.
describe("publish manifest is production-safe", () => {
  it("declares no npm runtime dependency once dependencies are inlined", () => {
    // `test` builds with tsup first, so a missing bundle is a real failure.
    expect(
      existsSync(bundle),
      "run `pnpm --filter @one-memory/cli build`",
    ).toBe(true);
    const source = readFileSync(bundle, "utf8");
    const external = [
      ...new Set(
        [...source.matchAll(/require\(["']([^"']+)["']\)/g)]
          .map((match) => match[1] as string)
          .filter(
            (name) =>
              !name.startsWith("node:") &&
              !name.startsWith(".") &&
              !name.startsWith("/"),
          ),
      ),
    ];
    // Every one of these must be a Node builtin, i.e. no npm package.
    const npmDeps = external.filter((name) => !builtinModules.includes(name));
    expect(npmDeps).toEqual([]);
  });

  it("strip-out rule keeps only what the bundle requires", () => {
    expect(
      existsSync(bundle),
      "run `pnpm --filter @one-memory/cli build`",
    ).toBe(true);
    // Run the real generator, then inspect its output manifest.
    execFileSync("node", ["scripts/prepare-publish-manifest.mjs"], {
      cwd: root,
      stdio: "pipe",
      env: {
        ...process.env,
        MEMHUB_NPM_PACKAGE: "@example/memhub-cli",
        MEMHUB_NPM_VERSION: "1.0.5",
      },
    });
    const published = JSON.parse(
      readFileSync(path.join(root, "dist-publish", "package.json"), "utf8"),
    ) as Record<string, unknown>;
    expect(published.dependencies).toBeUndefined();
    expect(published.private).toBe(false);
    expect(published.name).toBe("@example/memhub-cli");
    expect(published.version).toBe("1.0.5");
    expect(published.bin).toMatchObject({
      memhub: "dist/one-memory.cjs",
      "memhub-cli": "dist/one-memory.cjs",
      "one-memory": "dist/one-memory.cjs",
    });
  });

  it("refuses to invent a private or public npm identity", () => {
    expect(() =>
      execFileSync("node", ["scripts/prepare-publish-manifest.mjs"], {
        cwd: root,
        stdio: "pipe",
        env: Object.fromEntries(
          Object.entries(process.env).filter(
            ([name]) => name !== "MEMHUB_NPM_PACKAGE",
          ),
        ),
      }),
    ).toThrow();
  });

  it("copies every CLI script into the publish staging directory", () => {
    const packageJson = JSON.parse(
      readFileSync(path.join(root, "package.json"), "utf8"),
    ) as { scripts: Record<string, string> };
    expect(packageJson.scripts["pack:publish"]).toContain(
      "cp scripts/*.mjs dist-publish/scripts/",
    );
  });
});
