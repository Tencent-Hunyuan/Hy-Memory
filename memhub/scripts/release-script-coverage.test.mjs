import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);

function platformTestFiles(dir, found = []) {
  for (const entry of readdirSync(path.join(repoRoot, dir), {
    withFileTypes: true,
  })) {
    const relative = path.posix.join(dir, entry.name);
    if (entry.isDirectory()) platformTestFiles(relative, found);
    else if (entry.name.endsWith(".test.mjs")) found.push(relative);
  }
  return found;
}

// Files `node --test` cannot execute at all, so listing them would only turn
// the suite red. Each entry has to keep earning its place: the second test
// below re-runs the file and fails once it becomes runnable.
const UNRUNNABLE = {
  "scripts/platform/macos/custom-root-port-isolation.test.mjs":
    "imports packages/runtime-control/src/service-plan.ts, which imports ./service-ownership.js",
};

function childEnv() {
  // The test runner injects NODE_* variables that change how a nested
  // `node --test` reports, so probe the file with a clean environment.
  const env = { ...process.env };
  for (const key of Object.keys(env)) {
    if (key.startsWith("NODE_")) delete env[key];
  }
  return env;
}

describe("test:release-scripts enumeration coverage", () => {
  // The command lists files by hand, so a new platform test is silently never
  // run. Enumerate the denominator from disk instead of trusting the list.
  it("runs every runnable platform test file", () => {
    const command = JSON.parse(
      readFileSync(path.join(repoRoot, "package.json"), "utf8"),
    ).scripts["test:release-scripts"];
    const missing = platformTestFiles("scripts/platform").filter(
      (relative) => !command.includes(relative) && !(relative in UNRUNNABLE),
    );
    assert.deepEqual(missing, []);
    for (const relative of Object.keys(UNRUNNABLE)) {
      assert.ok(
        !command.includes(relative),
        `${relative} is both listed and excluded; drop one`,
      );
    }
  });

  it("keeps each exclusion still unrunnable", () => {
    for (const [relative, reason] of Object.entries(UNRUNNABLE)) {
      assert.ok(
        existsSync(path.join(repoRoot, relative)),
        `${relative} is gone; drop its exclusion (${reason})`,
      );
      const result = spawnSync(
        process.execPath,
        ["--test", path.join(repoRoot, relative)],
        { encoding: "utf8", env: childEnv() },
      );
      assert.notEqual(
        result.status,
        0,
        `${relative} now runs; move it into test:release-scripts (${reason})`,
      );
    }
  });
});
