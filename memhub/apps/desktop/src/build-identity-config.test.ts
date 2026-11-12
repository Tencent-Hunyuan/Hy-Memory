import { readFile } from "node:fs/promises";
import path from "node:path";
import { expect, it } from "vitest";

it("bakes fallback release identity when the packaging pipeline omits it", async () => {
  const config = await readFile(
    path.resolve(__dirname, "../tsup.config.ts"),
    "utf8",
  );

  expect(config).toContain("resolveBuildNumber");
  expect(config).toContain(
    "const releaseBuildNumber = resolveBuildNumber(process.env)",
  );
  expect(config).toMatch(
    /__ONE_MEMORY_BUILD_NUMBER__: JSON\.stringify\(\s*String\(releaseBuildNumber\)/,
  );
  expect(config).toContain(
    "const releaseCommit = resolveReleaseCommit(process.env.ONE_MEMORY_COMMIT)",
  );
  expect(config).toMatch(/if \(!trimmed\) return readSourceCommit\(\);/);
  // A supplied commit must be the full 40-character form the runtime accepts.
  expect(config).toContain("/^[0-9a-f]{40}$/i.test(trimmed)");
  expect(config).toContain("return trimmed.toLowerCase();");
  expect(config).toMatch(
    /execFileSync\("git", \["rev-parse", "HEAD"\],[\s\S]*?encoding: "utf8"/,
  );
  expect(config).toContain(
    "__ONE_MEMORY_COMMIT__: JSON.stringify(releaseCommit)",
  );
});
