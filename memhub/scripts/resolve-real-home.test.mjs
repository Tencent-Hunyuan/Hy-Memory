import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);

// The harness resolves the real home through bash + python3, so probe it the
// same way instead of spawning an interpreter Node may not be able to exec on
// Windows (where `python3` has no extension on PATH).
function harnessRealHome(extraEnv) {
  const result = spawnSync(
    "bash",
    [
      "scripts/run-tests-isolated.sh",
      "node",
      "-e",
      "process.stdout.write(process.env.ONE_MEMORY_TEST_REAL_HOME ?? '<unset>')",
    ],
    {
      cwd: repoRoot,
      encoding: "utf8",
      env: { ...process.env, ...extraEnv },
    },
  );
  assert.equal(result.error, undefined, "bash must be available");
  assert.equal(result.status, 0, result.stderr);
  return result.stdout.trim().split(/\r?\n/).pop();
}

describe("resolve-real-home", () => {
  it("gives the harness a path Node resolves on this platform", () => {
    const home = harnessRealHome({});
    assert.notEqual(home, "<unset>");
    if (process.platform === "win32") {
      assert.match(home, /^[A-Za-z]:[\\/]/, "must be a native Windows path");
    } else {
      assert.ok(home.startsWith("/"), "must be an absolute POSIX path");
    }
  });

  // The isolation guard compares USERPROFILE against this value, so the value
  // must not itself come from USERPROFILE/HOME; otherwise a spoofed profile
  // makes the comparison self-attesting and the guard silently passes.
  it("ignores a spoofed HOME and USERPROFILE", () => {
    assert.equal(
      harnessRealHome({
        HOME: "/tmp/not-the-real-home",
        USERPROFILE: "D:\\not-the-real-home",
      }),
      harnessRealHome({}),
    );
  });
});
