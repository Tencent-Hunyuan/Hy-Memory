import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";

const script = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  "verify-windows-support.mjs",
);

function runMatrix() {
  const result = spawnSync(process.execPath, [script], { encoding: "utf8" });
  assert.equal(result.error, undefined);
  return { exitCode: result.status, matrix: JSON.parse(result.stdout) };
}

describe("Windows support matrix", () => {
  it("reports explicit blocked status until a Windows 11 x64 target is available", () => {
    const { matrix } = runMatrix();
    assert.deepEqual(matrix.release_gate, {
      os: "Windows 11",
      arch: "x64",
      status: "blocked",
      blocked_reason: "windows_target_machine_required",
    });
    assert.deepEqual(matrix.explicit_matrix, {
      windows_11_x64: "blocked",
      windows_10_x64: "blocked",
      windows_arm64: "blocked",
    });
    assert.equal(matrix.capabilities.app_cli_shared_runtime, "implemented");
    assert.equal(matrix.capabilities.current_user_sid_dacl, "blocked");
    assert.ok(
      matrix.conditions_to_unblock.includes(
        "windows_11_x64_target_machine_or_runner",
      ),
    );
  });

  // Fail-closed gate: the exit code must be derived from the matrix in both
  // directions. Dropping `process.exitCode = ...` from the script goes red, and
  // so does hardcoding it to 1 while the gate reads "accepted".
  it("derives its exit code from the release gate status", () => {
    const { exitCode, matrix } = runMatrix();
    assert.equal(
      exitCode,
      matrix.release_gate.status === "accepted" ? 0 : 1,
      "exit code must agree with release_gate.status",
    );
    assert.equal(matrix.release_gate.status, "blocked");
    assert.notEqual(exitCode, 0);
  });
});
