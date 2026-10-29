import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";

const script = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  "verify-linux-support.mjs",
);

describe("Linux support matrix", () => {
  it("reports explicit blocked status until a Linux runtime is available", () => {
    const result = spawnSync(process.execPath, [script], { encoding: "utf8" });
    assert.equal(result.error, undefined);
    const matrix = JSON.parse(result.stdout);
    for (const [key, status] of Object.entries({
      x64_build: "blocked",
      arm64_build: "blocked",
      x64_systemd_user: "blocked",
      arm64_systemd_user: "blocked",
      no_systemd_serve: "blocked",
    })) {
      assert.equal(matrix.status[key], status, `status.${key}`);
    }
    assert.ok("systemd_user" in matrix.capabilities);
    assert.ok(
      matrix.conditions_to_unblock.includes(
        "linux_x64_arm64_runtime_or_runner",
      ),
    );
  });
});
