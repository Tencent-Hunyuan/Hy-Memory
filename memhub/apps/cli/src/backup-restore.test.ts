import { createServer, type Server } from "node:http";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import {
  callRestore,
  normalizeRestoreResult,
  restoreExitCode,
  restoreFailure,
  verifyMaintenanceCoreIdentity,
} from "./backup-restore.js";

// Loopback HTTP tests are milliseconds in isolation; the generous timeout
// prevents a busy CI host from turning scheduler contention into a false red.
/** What a restore of an old backup (or an old Core's answer) reads as. */
const OLD_BACKUP = {
  backupSchemaVersion: 1,
  memoriesIncluded: false,
  restoredFiles: null,
  verification: null,
  graphRebuildScheduled: false,
  note: null,
} as const;

describe("managed restore over HTTP", { timeout: 15_000 }, () => {
  let server: Server;
  let port: number;

  beforeAll(async () => {
    server = createServer((request, response) => {
      response.setHeader("content-type", "application/json");
      if (request.url !== "/api/v1/backup/restore") {
        response.statusCode = 404;
        response.end("{}");
        return;
      }
      if (request.headers.authorization === "Bearer r401") {
        response.statusCode = 401;
        response.end(JSON.stringify({ detail: "unauthorized" }));
        return;
      }
      if (request.headers.authorization === "Bearer r409") {
        response.statusCode = 409;
        response.end(
          JSON.stringify({ detail: "backup_restore_runtime_running" }),
        );
        return;
      }
      if (request.headers.authorization === "Bearer r422") {
        response.statusCode = 422;
        response.end(
          JSON.stringify({ detail: "backup_restore_source_outside_home" }),
        );
        return;
      }
      response.end(
        JSON.stringify({
          schema_version: 1,
          state: "restored",
          restored_bytes: 8192,
          previous_backup_path: "/home/backups/pre-restore.sqlite3",
          source_schema_version: 34,
        }),
      );
    });
    await new Promise<void>((resolve) =>
      server.listen(0, "127.0.0.1", resolve),
    );
    const address = server.address();
    if (address === null || typeof address === "string")
      throw new Error("no port");
    port = address.port;
  });

  afterAll(async () => {
    await new Promise<void>((resolve) => server.close(() => resolve()));
  });

  it("sends the bearer and parses a restored response", async () => {
    const outcome = await callRestore(
      { host: "127.0.0.1", port, apiToken: "r200" },
      "/home/.one-memory/backups/a.sqlite3",
    );
    expect(outcome).toEqual({
      state: "restored",
      result: {
        state: "restored",
        restoredBytes: 8192,
        previousBackupPath: "/home/backups/pre-restore.sqlite3",
        sourceSchemaVersion: 34,
        ...OLD_BACKUP,
      },
    });
  });

  it("maps the runtime-running refusal to its stable code", async () => {
    await expect(
      callRestore(
        { host: "127.0.0.1", port, apiToken: "r409" },
        "/home/.one-memory/backups/a.sqlite3",
      ),
    ).resolves.toEqual({
      state: "rejected",
      code: "backup_restore_runtime_running",
    });
  });

  it("maps Core's 422 detail unchanged", async () => {
    await expect(
      callRestore(
        { host: "127.0.0.1", port, apiToken: "r422" },
        "/home/.one-memory/backups/a.sqlite3",
      ),
    ).resolves.toEqual({
      state: "rejected",
      code: "backup_restore_source_outside_home",
    });
  });

  it("does not report success on a 401", async () => {
    await expect(
      callRestore(
        { host: "127.0.0.1", port, apiToken: "r401" },
        "/home/.one-memory/backups/a.sqlite3",
      ),
    ).resolves.toMatchObject({ state: "rejected" });
  });
});

describe("maintenance Core identity", { timeout: 15_000 }, () => {
  it("accepts only the exact Core instance and sends no bearer on health", async () => {
    const calls: Array<{ url: string; headers: Record<string, string> }> = [];
    await expect(
      verifyMaintenanceCoreIdentity(
        { host: "127.0.0.1", port: 8765, instanceId: "expected-instance" },
        async (url, headers) => {
          calls.push({ url, headers });
          return {
            status: 200,
            data: {
              status: "ok",
              service: "one-memory-core",
              instance_id: "expected-instance",
            },
          };
        },
      ),
    ).resolves.toBe(true);
    expect(calls).toEqual([
      {
        url: "http://127.0.0.1:8765/api/v1/health",
        headers: {},
      },
    ]);
  });

  it("rejects a squatter, wrong service, wrong instance, and non-200", async () => {
    const cases = [
      {
        status: 200,
        data: { status: "ok", service: "other", instance_id: "x" },
      },
      {
        status: 200,
        data: {
          status: "ok",
          service: "one-memory-core",
          instance_id: "wrong",
        },
      },
      { status: 503, data: {} },
      { status: 200, data: null },
    ];
    for (const response of cases) {
      await expect(
        verifyMaintenanceCoreIdentity(
          { host: "127.0.0.1", port: 8765, instanceId: "x" },
          async () => response,
        ),
      ).resolves.toBe(false);
    }
  });

  it("does not let a squatter receive the bearer", async () => {
    const seen: Array<string | undefined> = [];
    const squatter = createServer((request, response) => {
      seen.push(request.headers.authorization);
      response.setHeader("content-type", "application/json");
      response.end(
        JSON.stringify({
          status: "ok",
          service: "one-memory-core",
          instance_id: "squatter",
        }),
      );
    });
    await new Promise<void>((resolve) =>
      squatter.listen(0, "127.0.0.1", resolve),
    );
    const address = squatter.address();
    if (address === null || typeof address === "string") {
      throw new Error("no port");
    }
    try {
      await expect(
        verifyMaintenanceCoreIdentity({
          host: "127.0.0.1",
          port: address.port,
          instanceId: "expected",
        }),
      ).resolves.toBe(false);
      expect(seen).toEqual([undefined]);
    } finally {
      await new Promise<void>((resolve) => squatter.close(() => resolve()));
    }
  });
});

describe("backup restore result shape", () => {
  it("accepts the Core contract result and never echoes a token", () => {
    const result = normalizeRestoreResult({
      state: "restored",
      restored_bytes: 123456,
      previous_backup_path: "/home/backups/pre-restore-1.sqlite3",
      source_schema_version: 34,
    });
    expect(result).toEqual({
      state: "restored",
      restoredBytes: 123456,
      previousBackupPath: "/home/backups/pre-restore-1.sqlite3",
      sourceSchemaVersion: 34,
      ...OLD_BACKUP,
    });
  });

  it("reads a v2 restore: memories included, counts verified", () => {
    const verification = {
      vector_store: "verified",
      layers: "verified",
      layers_reason: null,
      graph: "quarantined",
      mismatches: [],
    };
    expect(
      normalizeRestoreResult({
        state: "restored",
        restored_bytes: 9,
        previous_backup_path: "/home/backups/pre-restore-1.memhub-previous",
        source_schema_version: 63,
        backup_schema_version: 2,
        memories_included: true,
        restored_files: 9,
        verification,
        graph_rebuild_scheduled: true,
        note: null,
      }),
    ).toMatchObject({
      backupSchemaVersion: 2,
      memoriesIncluded: true,
      restoredFiles: 9,
      verification,
      graphRebuildScheduled: true,
    });
    // `memories_included` from an answer that is not v2 is not believed.
    expect(
      normalizeRestoreResult({
        state: "restored",
        restored_bytes: 9,
        previous_backup_path: "/p",
        source_schema_version: 63,
        memories_included: true,
      }),
    ).toMatchObject({ backupSchemaVersion: 1, memoriesIncluded: false });
  });

  it("rejects a malformed result instead of reporting success", () => {
    for (const value of [
      null,
      {},
      { state: "restored" },
      {
        state: "restoring",
        restored_bytes: 1,
        previous_backup_path: "/x",
        source_schema_version: 1,
      },
      {
        state: "restored",
        restored_bytes: -1,
        previous_backup_path: "/x",
        source_schema_version: 1,
      },
    ]) {
      expect(normalizeRestoreResult(value)).toBeNull();
    }
  });

  it("maps Core failure codes to exit 1 and unknown codes conservatively", () => {
    for (const code of [
      "backup_restore_runtime_running",
      "backup_restore_source_outside_home",
      "backup_restore_source_invalid",
      "backup_restore_source_too_new",
      "backup_restore_protective_backup_failed",
      "backup_restore_replace_failed",
    ]) {
      expect(restoreFailure(code)).toEqual({ code });
      expect(restoreExitCode({ state: "rejected", code })).toBe(1);
    }
    // An unrecognized code is reported, not swallowed as success.
    expect(restoreFailure("something_else").code).toBe("backup_restore_failed");
    expect(restoreExitCode({ state: "restored" })).toBe(0);
  });
});
