import {
  lstat,
  mkdir,
  mkdtemp,
  readdir,
  readFile,
  rm,
  symlink,
  utimes,
  writeFile,
} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { strFromU8, unzipSync } from "fflate";
import { afterEach, describe, expect, it } from "vitest";

import {
  DiagnosticLogger,
  diagnosticError,
  diagnosticEvent,
  diagnosticLogFiles,
  exportDiagnosticBundle,
  exportDiagnosticZip,
  pruneDiagnosticLogs,
  readDiagnosticEvents,
  writeDiagnosticEventSync,
} from "./diagnostic-log.js";

const roots: string[] = [];
async function root() {
  const value = await mkdtemp(path.join(os.tmpdir(), "one-memory-log-"));
  roots.push(value);
  return value;
}
afterEach(async () =>
  Promise.all(roots.splice(0).map((item) => rm(item, { recursive: true }))),
);

describe("diagnostic log", () => {
  it("persists an early fatal event before the process can exit", async () => {
    const dataDir = await root();
    writeDiagnosticEventSync(
      {
        dataDir,
        service: "desktop",
        sessionId: "55555555-5555-4555-8555-555555555555",
      },
      {
        level: "error",
        component: "desktop.lifecycle",
        event: "desktop.early_start_failed",
        outcome: "failed",
        error_code: "desktop_test_real_home_mismatch",
        error_type: "Error",
        safe_stack: ["apps/desktop/src/main.ts:80:1"],
        details: { reason: "desktop_test_real_home_mismatch" },
      },
    );

    const result = await readDiagnosticEvents(dataDir);
    expect(result.events).toHaveLength(1);
    expect(result.events[0]).toMatchObject({
      event: "desktop.early_start_failed",
      error_code: "desktop_test_real_home_mismatch",
      details: { reason: "desktop_test_real_home_mismatch" },
    });
  });

  it("writes structured, private events and reads them by correlation identity", async () => {
    const dataDir = await root();
    const session = "11111111-1111-4111-8111-111111111111";
    const trace = "22222222-2222-4222-8222-222222222222";
    const logger = new DiagnosticLogger({
      dataDir,
      service: "runtime",
      sessionId: session,
    });
    await logger.log({
      level: "info",
      component: "runtime.service",
      event: "runtime.ready",
      outcome: "succeeded",
      trace_id: trace,
      operation_id: "operation-1",
      duration_ms: 12,
      details: {
        phase: "startup",
        count: 2,
        open_file_count: 7,
        memory_bytes: 4096,
        thread_count: 4,
      },
    });
    const result = await readDiagnosticEvents(dataDir, { traceId: trace });
    expect(result.invalid_lines).toBe(0);
    expect(result.events).toHaveLength(1);
    expect(result.events[0]).toMatchObject({
      schema_version: 1,
      session_id: session,
      trace_id: trace,
      event: "runtime.ready",
      details: expect.objectContaining({
        open_file_count: 7,
      }),
    });
    const file = (await diagnosticLogFiles(dataDir))[0];
    if (process.platform !== "win32")
      expect((await lstat(file)).mode & 0o777).toBe(0o600);
  });

  it("stamps the package identity from the environment onto every event", async () => {
    const dataDir = await root();
    const session = "33333333-3333-4333-8333-333333333333";
    const previous = {
      build: process.env.ONE_MEMORY_BUILD_ID,
      pkg: process.env.ONE_MEMORY_PACKAGE_ID,
      round: process.env.ONE_MEMORY_ROUND,
    };
    process.env.ONE_MEMORY_BUILD_ID = "One-Memory-H88-4760f30-candidate";
    process.env.ONE_MEMORY_PACKAGE_ID = "One-Memory-H88-4760f30-candidate";
    process.env.ONE_MEMORY_ROUND = "88";
    try {
      const logger = new DiagnosticLogger({
        dataDir,
        service: "desktop",
        sessionId: session,
      });
      await logger.log({
        level: "info",
        component: "desktop.lifecycle",
        event: "ui.action",
        outcome: "started",
      });
      const result = await readDiagnosticEvents(dataDir);
      expect(result.events[0]).toMatchObject({
        event: "ui.action",
        build_id: "One-Memory-H88-4760f30-candidate",
        package_id: "One-Memory-H88-4760f30-candidate",
        round: "88",
      });
    } finally {
      if (previous.build === undefined) delete process.env.ONE_MEMORY_BUILD_ID;
      else process.env.ONE_MEMORY_BUILD_ID = previous.build;
      if (previous.pkg === undefined) delete process.env.ONE_MEMORY_PACKAGE_ID;
      else process.env.ONE_MEMORY_PACKAGE_ID = previous.pkg;
      if (previous.round === undefined) delete process.env.ONE_MEMORY_ROUND;
      else process.env.ONE_MEMORY_ROUND = previous.round;
    }
  });

  it("writes no identity for a source run instead of fabricating one", async () => {
    const dataDir = await root();
    const previous = {
      build: process.env.ONE_MEMORY_BUILD_ID,
      pkg: process.env.ONE_MEMORY_PACKAGE_ID,
      round: process.env.ONE_MEMORY_ROUND,
    };
    delete process.env.ONE_MEMORY_BUILD_ID;
    delete process.env.ONE_MEMORY_PACKAGE_ID;
    delete process.env.ONE_MEMORY_ROUND;
    try {
      const logger = new DiagnosticLogger({
        dataDir,
        service: "desktop",
        sessionId: "44444444-4444-4444-8444-444444444444",
      });
      await logger.log({
        level: "info",
        component: "desktop.lifecycle",
        event: "ui.action",
        outcome: "started",
      });
      const result = await readDiagnosticEvents(dataDir);
      expect(result.events[0].build_id).toBeUndefined();
      expect(result.events[0].package_id).toBeUndefined();
      expect(result.events[0].round).toBeUndefined();
    } finally {
      if (previous.build !== undefined)
        process.env.ONE_MEMORY_BUILD_ID = previous.build;
      if (previous.pkg !== undefined)
        process.env.ONE_MEMORY_PACKAGE_ID = previous.pkg;
      if (previous.round !== undefined)
        process.env.ONE_MEMORY_ROUND = previous.round;
    }
  });

  it("rejects arbitrary detail fields and absolute stack paths", () => {
    const base = {
      timestamp: new Date().toISOString(),
      level: "error" as const,
      service: "core" as const,
      component: "hy.bridge",
      event: "hy.failed",
      outcome: "failed",
      session_id: "11111111-1111-4111-8111-111111111111",
    };
    expect(() =>
      diagnosticEvent({ ...base, details: { prompt: "private" } }),
    ).toThrow("diagnostic_detail_key_invalid");
    expect(() =>
      diagnosticEvent({ ...base, safe_stack: ["/Users/me/private.py:1"] }),
    ).toThrow("diagnostic_stack_invalid");
  });

  it("drops malformed logger events without interrupting later writes", async () => {
    const dataDir = await root();
    const logger = new DiagnosticLogger({
      dataDir,
      service: "desktop",
      sessionId: "11111111-1111-4111-8111-111111111111",
    });

    await expect(
      logger.log({
        level: "info",
        component: "desktop.sdk_migration",
        event: "sdk_migration.startup",
        outcome: "succeeded",
        details: { state: "cutover" },
      }),
    ).resolves.toBeUndefined();
    await logger.log({
      level: "info",
      component: "desktop.sdk_migration",
      event: "sdk_migration.startup",
      outcome: "succeeded",
      details: { runtime_state: "cutover" },
    });

    const result = await readDiagnosticEvents(dataDir);
    expect(result.invalid_lines).toBe(0);
    expect(result.events).toHaveLength(1);
    expect(result.events[0].details).toEqual({ runtime_state: "cutover" });
  });

  it("keeps code locations but strips exception messages and absolute prefixes", () => {
    const secret = "secret-token-918273";
    const error = new Error(secret);
    error.stack = `Error: ${secret}\n    at run (/Users/me/repo/packages/runtime-control/src/example.ts:42:7)`;
    const safe = diagnosticError(error);
    expect(safe).toEqual({
      error_type: "Error",
      safe_stack: ["packages/runtime-control/src/example.ts:42:7"],
    });
    expect(JSON.stringify(safe)).not.toContain(secret);
    expect(JSON.stringify(safe)).not.toContain("/Users/me");
  });

  it("marks incidents and exports only validated events", async () => {
    const dataDir = await root();
    const logger = new DiagnosticLogger({ dataDir, service: "desktop" });
    const incident = await logger.markIncident({
      component: "desktop.diagnostics",
      status: "degraded",
    });
    await writeFile(
      path.join(dataDir, "logs", "diagnostic-core.jsonl"),
      "not-json\n",
      { mode: 0o600 },
    );
    const target = path.join(await root(), "bundle.json");
    const result = await exportDiagnosticBundle({
      dataDir,
      target,
      productVersion: "1.2.3",
      status: { status: "degraded" },
    });
    expect(result).toEqual({ event_count: 1, invalid_lines: 1 });
    const payload = JSON.parse(await readFile(target, "utf8"));
    expect(payload.events[0].incident_id).toBe(incident.incident_id);
    expect(JSON.stringify(payload)).not.toContain(dataDir);
  });

  it("preserves microsecond ordering across service log files", async () => {
    const dataDir = await root();
    await mkdir(path.join(dataDir, "logs"), { recursive: true });
    const base = {
      schema_version: 1,
      level: "info",
      outcome: "started",
      session_id: "11111111-1111-4111-8111-111111111111",
      trace_id: "22222222-2222-4222-8222-222222222222",
    };
    await writeFile(
      path.join(dataDir, "logs", "diagnostic-desktop.jsonl"),
      `${JSON.stringify({
        ...base,
        timestamp: "2026-10-14T00:00:00.123Z",
        service: "desktop",
        component: "renderer.ui",
        event: "ui.action",
      })}\n`,
    );
    await writeFile(
      path.join(dataDir, "logs", "diagnostic-core.jsonl"),
      `${JSON.stringify({
        ...base,
        timestamp: "2026-10-14T00:00:00.123999Z",
        service: "core",
        component: "core.api",
        event: "memory.request.started",
      })}\n`,
    );

    const result = await readDiagnosticEvents(dataDir);
    expect(result.invalid_lines).toBe(0);
    expect(result.events.map((event) => event.event)).toEqual([
      "ui.action",
      "memory.request.started",
    ]);
    expect(result.events[1].timestamp).toBe("2026-10-14T00:00:00.123999Z");
  });

  it("exports a private seven-day zip with summary, logs, crashes and markers", async () => {
    const dataDir = await root();
    const outputDir = await root();
    const crashDir = await root();
    const secret = "sk-fake-secret-token";
    const conversation = "我喜欢吃绝密蓝莓";
    const home = "/Users/private-person";
    await mkdir(path.join(dataDir, "logs"), { recursive: true });
    await writeFile(
      path.join(dataDir, "logs", "diagnostic-core.jsonl"),
      `${JSON.stringify({
        schema_version: 1,
        timestamp: "2026-10-05T01:00:00.000Z",
        level: "error",
        service: "core",
        component: "memory.task",
        event: "memory.failed",
        outcome: "failed",
        session_id: "11111111-1111-4111-8111-111111111111",
        task_id: "task-1",
        error_code: "model_failed",
        details: { content: conversation, token: secret, count: 1 },
      })}\n`,
    );
    await writeFile(
      path.join(dataDir, "logs", "core.log"),
      `home=${home} build=/Users/build-agent/repo authorization=Bearer ${secret} content=${conversation}\n`,
    );
    await writeFile(
      path.join(crashDir, "MemHub-2026-10-05.ips"),
      `Path: ${home}/MemHub\napi_key=${secret}\n`,
    );
    const target = path.join(outputDir, "MemHub.zip");

    const result = await exportDiagnosticZip({
      dataDir,
      target,
      productVersion: "3800",
      commit: "abcdef12",
      coreSha256: "0".repeat(64),
      home,
      crashReportsDir: crashDir,
      now: new Date("2026-10-05T02:00:00.000Z"),
      markers: [
        {
          timestamp: "2026-10-05T01:30:00.000Z",
          description: "蓝莓没出来",
        },
      ],
      summary: {
        sources: [{ source_id: "workbuddy", imported_messages: 12 }],
        memory_tasks: { unknown: 1, last_error_codes: { model_failed: 1 } },
      },
    });

    expect(result.filename).toBe("MemHub.zip");
    const files = unzipSync(await readFile(target));
    expect(Object.keys(files)).toEqual(
      expect.arrayContaining([
        "README.txt",
        "summary.json",
        "logs/diagnostic-core.jsonl",
        "logs/core.log",
        "crashes/MemHub-2026-10-05.ips",
      ]),
    );
    const summary = files["summary.json"];
    const readme = files["README.txt"];
    if (!summary || !readme) throw new Error("missing support bundle metadata");
    expect(strFromU8(summary)).toContain("model_failed");
    expect(strFromU8(readme)).toContain("蓝莓没出来");
    const all = Object.values(files)
      .map((value) => strFromU8(value))
      .join("\n");
    expect(all).not.toContain(secret);
    expect(all).not.toContain(conversation);
    expect(all).not.toContain(home);
    expect(all).not.toContain("/Users/");
    expect(all).toContain("~/MemHub");
  });

  it("an incident retrieves its surrounding session rather than only its marker", async () => {
    const dataDir = await root();
    const times = [
      new Date("2026-09-24T01:00:00Z"),
      new Date("2026-09-24T01:01:00Z"),
      new Date("2026-09-24T01:02:00Z"),
    ];
    const logger = new DiagnosticLogger({
      dataDir,
      service: "desktop",
      sessionId: "11111111-1111-4111-8111-111111111111",
      now: () => times.shift() ?? new Date("2026-09-24T01:02:00Z"),
    });
    await logger.log({
      level: "info",
      component: "desktop.connection",
      event: "connection.started",
      outcome: "started",
    });
    const marker = await logger.markIncident({
      component: "desktop.diagnostics",
      instanceId: "instance-1",
    });
    await logger.log({
      level: "error",
      component: "desktop.connection",
      event: "connection.failed",
      outcome: "failed",
      error_code: "connection_failed",
    });
    const runtime = new DiagnosticLogger({
      dataDir,
      service: "runtime",
      sessionId: "33333333-3333-4333-8333-333333333333",
      now: () => new Date("2026-09-24T01:01:30Z"),
    });
    await runtime.log({
      level: "error",
      component: "runtime.core",
      event: "core.failed",
      outcome: "failed",
      instance_id: "instance-1",
    });
    const result = await readDiagnosticEvents(dataDir, {
      incidentId: marker.incident_id,
    });
    expect(result.events.map((item) => item.event)).toEqual([
      "connection.started",
      "incident.marked",
      "core.failed",
      "connection.failed",
    ]);
  });

  it("ignores symlinks and prunes files older than fourteen days", async () => {
    const dataDir = await root();
    const logs = path.join(dataDir, "logs");
    await mkdir(logs, { recursive: true, mode: 0o700 });
    const old = path.join(logs, "diagnostic-core.jsonl");
    await writeFile(old, "{}\n", { mode: 0o600 });
    const outside = path.join(await root(), "outside");
    await writeFile(outside, "private");
    await symlink(outside, path.join(logs, "diagnostic-runtime.jsonl"));
    const now = Date.now();
    await utimes(
      old,
      new Date(now - 15 * 86400_000),
      new Date(now - 15 * 86400_000),
    );
    await pruneDiagnosticLogs(dataDir, now);
    expect(await diagnosticLogFiles(dataDir)).toEqual([]);
  });

  it("deletes the oldest known log when the total budget is exceeded", async () => {
    const dataDir = await root();
    const logs = path.join(dataDir, "logs");
    await mkdir(logs, { recursive: true, mode: 0o700 });
    const old = path.join(logs, "diagnostic-core.jsonl");
    const recent = path.join(logs, "diagnostic-runtime.jsonl");
    await writeFile(old, "12345678", { mode: 0o600 });
    await writeFile(recent, "abcdefgh", { mode: 0o600 });
    await utimes(old, new Date(1_000), new Date(1_000));
    await utimes(recent, new Date(2_000), new Date(2_000));
    await pruneDiagnosticLogs(dataDir, 3_000, 8);
    expect(
      (await diagnosticLogFiles(dataDir)).map((file) => path.basename(file)),
    ).toEqual(["diagnostic-runtime.jsonl"]);
  });

  it("serializes concurrent writes and rotates bounded files", async () => {
    const dataDir = await root();
    const logger = new DiagnosticLogger({
      dataDir,
      service: "runtime",
      maxFileBytes: 512,
    });
    await Promise.all(
      Array.from({ length: 40 }, (_, index) =>
        logger.log({
          level: "info",
          component: "runtime.concurrent",
          event: "write.completed",
          outcome: "succeeded",
          details: { count: index },
        }),
      ),
    );
    expect((await diagnosticLogFiles(dataDir)).length).toBeGreaterThan(1);
    const result = await readDiagnosticEvents(dataDir, { tail: 5_000 });
    expect(result.invalid_lines).toBe(0);
    expect(result.events.length).toBeGreaterThan(0);
  });

  it("never follows a logs-directory symlink", async () => {
    const dataDir = await root();
    const outside = await root();
    await symlink(outside, path.join(dataDir, "logs"));
    const logger = new DiagnosticLogger({ dataDir, service: "runtime" });
    await logger.log({
      level: "error",
      component: "runtime.core",
      event: "core.failed",
      outcome: "failed",
    });
    expect(await readdir(outside)).toEqual([]);
  });
});
