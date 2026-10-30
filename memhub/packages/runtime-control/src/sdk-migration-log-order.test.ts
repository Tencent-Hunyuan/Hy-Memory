import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";
import {
  sdkMigrationLogPath,
  writeSdkMigrationLog,
} from "./sdk-migration-log.js";

async function events(dataDir: string): Promise<string[]> {
  return (await readFile(sdkMigrationLogPath(dataDir), "utf8"))
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line).event);
}

describe("SDK migration log ordering", () => {
  it("preserves invocation order for concurrent progress and completion writes", async () => {
    const dataDir = await mkdtemp(
      path.join(os.tmpdir(), "migration-log-order-"),
    );
    try {
      const expected = Array.from(
        { length: 100 },
        (_, i) => `migration.progress.${i}`,
      );
      expected.push("migration.finished");
      await Promise.all(
        expected.map((event) =>
          writeSdkMigrationLog(dataDir, {
            writer: "desktop",
            event,
            outcome: "started",
          }),
        ),
      );
      expect(await events(dataDir)).toEqual(expected);
    } finally {
      await rm(dataDir, { recursive: true, force: true });
    }
  });

  it("awaiting the terminal event also drains preceding progress writes", async () => {
    const dataDir = await mkdtemp(
      path.join(os.tmpdir(), "migration-log-drain-"),
    );
    const pending: Promise<void>[] = [];
    try {
      const expected = Array.from(
        { length: 50 },
        (_, i) => `migration.progress.${i}`,
      );
      for (const event of expected) {
        pending.push(
          writeSdkMigrationLog(dataDir, {
            writer: "desktop",
            event,
            outcome: "started",
          }),
        );
      }
      await writeSdkMigrationLog(dataDir, {
        writer: "desktop",
        event: "migration.finished",
        outcome: "succeeded",
      });
      expect(await events(dataDir)).toEqual([
        ...expected,
        "migration.finished",
      ]);
    } finally {
      await Promise.all(pending);
      await rm(dataDir, { recursive: true, force: true });
    }
  });

  it("does not let a failed append block subsequent writes to the same home", async () => {
    const dataDir = await mkdtemp(
      path.join(os.tmpdir(), "migration-log-recovery-"),
    );
    try {
      await writeFile(path.join(dataDir, "logs"), "not a directory");
      await expect(
        writeSdkMigrationLog(dataDir, {
          writer: "desktop",
          event: "migration.progress",
          outcome: "started",
        }),
      ).resolves.toBeUndefined();
      await rm(path.join(dataDir, "logs"));
      await writeSdkMigrationLog(dataDir, {
        writer: "desktop",
        event: "migration.finished",
        outcome: "succeeded",
      });
      expect(await events(dataDir)).toEqual(["migration.finished"]);
    } finally {
      await rm(dataDir, { recursive: true, force: true });
    }
  });
});
