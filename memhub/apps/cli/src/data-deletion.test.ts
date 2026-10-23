import { mkdir, mkdtemp, readFile, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { applyDataDeletion, planDataDeletion } from "./data-deletion.js";

async function homeWith(
  layout: Record<string, string> = { "one-memory.sqlite3": "db" },
): Promise<{ home: string; dataDir: string }> {
  const home = await mkdtemp(path.join(tmpdir(), "memhub-del-"));
  const dataDir = path.join(home, ".one-memory");
  await mkdir(dataDir, { recursive: true });
  for (const [name, content] of Object.entries(layout)) {
    await writeFile(path.join(dataDir, name), content);
  }
  return { home, dataDir };
}

describe("explicit data deletion", () => {
  it("previews an owned One Memory Home with its entries", async () => {
    const { home, dataDir } = await homeWith({
      "one-memory.sqlite3": "db",
      "runtime-service.json": "{}",
    });
    const plan = await planDataDeletion({
      dataDir,
      home,
      runtimeRunning: false,
    });
    expect(plan.state).toBe("preview");
    expect(plan.entries).toContain(path.join(dataDir, "one-memory.sqlite3"));
  });

  it("refuses while the Runtime is still running", async () => {
    const { home, dataDir } = await homeWith();
    const plan = await planDataDeletion({
      dataDir,
      home,
      runtimeRunning: true,
    });
    expect(plan).toMatchObject({
      state: "blocked",
      code: "runtime_still_running",
    });
    expect(plan.next_step).toContain("memhub stop");
  });

  it("refuses a directory that is not a One Memory Home", async () => {
    const home = await mkdtemp(path.join(tmpdir(), "memhub-del-"));
    const victim = path.join(home, "random-folder");
    await mkdir(victim, { recursive: true });
    await writeFile(path.join(victim, "notes.txt"), "not ours");
    const plan = await planDataDeletion({
      dataDir: victim,
      home,
      runtimeRunning: false,
    });
    expect(plan).toMatchObject({
      state: "blocked",
      code: "not_a_one_memory_home",
    });
    // Nothing was touched.
    await expect(
      readFile(path.join(victim, "notes.txt"), "utf8"),
    ).resolves.toBe("not ours");
  });

  it("refuses the home directory and the filesystem root", async () => {
    const home = await mkdtemp(path.join(tmpdir(), "memhub-del-"));
    for (const dataDir of [home, "/"]) {
      const plan = await planDataDeletion({
        dataDir,
        home,
        runtimeRunning: false,
      });
      expect(plan).toMatchObject({
        state: "blocked",
        code: "data_dir_refused",
      });
    }
  });

  it("requires the second confirmation to name the exact directory", async () => {
    const { home, dataDir } = await homeWith();
    const plan = await planDataDeletion({
      dataDir,
      home,
      runtimeRunning: false,
    });
    await expect(
      applyDataDeletion(plan, path.join(home, "other")),
    ).rejects.toThrow("data_deletion_confirmation_mismatch");
    // The data survived a mismatched confirmation.
    await expect(
      stat(path.join(dataDir, "one-memory.sqlite3")),
    ).resolves.toBeTruthy();
  });

  it("deletes an owned Home only when both confirmations match", async () => {
    const { home, dataDir } = await homeWith({ "one-memory.sqlite3": "db" });
    const plan = await planDataDeletion({
      dataDir,
      home,
      runtimeRunning: false,
    });
    const receipt = await applyDataDeletion(plan, dataDir);
    expect(receipt.state).toBe("deleted");
    await expect(stat(dataDir)).rejects.toMatchObject({ code: "ENOENT" });
  });

  it("refuses a directory whose only marker is a generic logs directory", async () => {
    const home = await mkdtemp(path.join(tmpdir(), "memhub-del-"));
    const project = path.join(home, "my-project");
    await mkdir(path.join(project, "logs"), { recursive: true });
    await writeFile(path.join(project, "main.js"), "project code");

    const plan = await planDataDeletion({
      dataDir: project,
      home,
      runtimeRunning: false,
      runtimeLock: "clear",
    });
    expect(plan).toMatchObject({
      state: "blocked",
      code: "not_a_one_memory_home",
    });
    await expect(readFile(path.join(project, "main.js"), "utf8")).resolves.toBe(
      "project code",
    );
  });

  it("refuses while the service lease is live even without a runtime receipt", async () => {
    const { home, dataDir } = await homeWith();
    const plan = await planDataDeletion({
      dataDir,
      home,
      runtimeRunning: false,
      runtimeLock: "live",
    });
    expect(plan).toMatchObject({
      state: "blocked",
      code: "runtime_still_running",
    });
  });

  it("refuses when the service lease cannot be inspected", async () => {
    const { home, dataDir } = await homeWith();
    const plan = await planDataDeletion({
      dataDir,
      home,
      runtimeRunning: false,
      runtimeLock: "unknown",
    });
    expect(plan).toMatchObject({
      state: "blocked",
      code: "runtime_state_unknown",
    });
  });
});
