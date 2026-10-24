import {
  chmod,
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
  projectDiagnosticEventForSafeOutput,
  pruneDiagnosticLogs,
  readDiagnosticEvents,
  recordDiagnosticMarker,
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

  it("keeps the synchronous writer fail-open for invalid input", async () => {
    const dataDir = await root();
    expect(() =>
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
          details: { nested: ["private"] } as never,
        },
      ),
    ).not.toThrow();
    expect(await readDiagnosticEvents(dataDir)).toEqual({
      events: [],
      invalid_lines: 0,
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
        "cache.hit-rate": 0.75,
        custom_label: "warm",
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
        "cache.hit-rate": 0.75,
        custom_label: "warm",
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

  it("strictly rejects complex detail values, dangerous keys and absolute stacks", () => {
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
      diagnosticEvent({
        ...base,
        details: { nested: { secret: "private" } } as never,
      }),
    ).toThrow("diagnostic_detail_value_invalid");
    expect(() =>
      diagnosticEvent({ ...base, details: { constructor: "private" } }),
    ).toThrow("diagnostic_detail_key_invalid");
    expect(() =>
      diagnosticEvent({ ...base, details: { "9invalid": "private" } }),
    ).toThrow("diagnostic_detail_key_invalid");
    expect(() =>
      diagnosticEvent({
        ...base,
        details: Object.fromEntries(
          Array.from({ length: 33 }, (_, index) => [`key_${index}`, index]),
        ),
      }),
    ).toThrow("diagnostic_detail_count_invalid");
    expect(() =>
      diagnosticEvent({ ...base, details: { message: "line1\nline2" } }),
    ).toThrow("diagnostic_text_invalid");
    expect(() =>
      diagnosticEvent({ ...base, details: { metric: Number.NaN } }),
    ).toThrow("diagnostic_detail_value_invalid");
    expect(() =>
      diagnosticEvent({
        ...base,
        details: { count: Number.MAX_SAFE_INTEGER + 1 },
      }),
    ).toThrow("diagnostic_detail_value_invalid");
    expect(() =>
      diagnosticEvent({ ...base, details: { list: ["private"] } as never }),
    ).toThrow("diagnostic_detail_value_invalid");
    expect(() =>
      diagnosticEvent({ ...base, safe_stack: ["/Users/me/private.py:1"] }),
    ).toThrow("diagnostic_stack_invalid");
    expect(() =>
      diagnosticEvent({
        ...base,
        safe_stack: ["packages/runtime/sk-live-secret-token.ts:1:1"],
      }),
    ).toThrow("diagnostic_stack_invalid");
    for (const line of [
      "arbitrary stack text",
      "thread=worker|threading.py:12:wait",
      "FILE:///Users/me/private.py:1:call",
      "one_memory/worker.py:9:call /Users/private",
      "one_memory/GOCSPX-privateCredential.py:9:call",
      "packages/ASIA1234567890ABCDEF/file.ts:1:1",
      "packages/Aa1Bb2Cc3Dd4Ee5Ff6Gg7Hh8/file.ts:1:1",
    ]) {
      expect(() => diagnosticEvent({ ...base, safe_stack: [line] })).toThrow(
        "diagnostic_stack_invalid",
      );
    }
    expect(() =>
      diagnosticEvent({
        ...base,
        safe_stack: Array.from(
          { length: 17 },
          (_, index) => `one_memory/worker.py:${index + 1}:call`,
        ),
      }),
    ).toThrow("diagnostic_stack_invalid");

    const accepted = diagnosticEvent({
      ...base,
      safe_stack: [
        "apps/desktop/src/main.ts:80:1",
        "one_memory/diagnostic_log.py:895:_safe_stack_line",
        "threading.py:1045:_bootstrap_inner",
        "thread|threading.py:1045:_bootstrap_inner > one_memory/worker.py:9:call",
      ],
    });
    expect(accepted.safe_stack).toHaveLength(4);

    const projected = projectDiagnosticEventForSafeOutput({
      ...accepted,
      safe_stack: [
        "free text",
        "thread=worker|one_memory/worker.py:9:call",
        "packages/runtime-control/src/diagnostic-log.ts:42:7",
        "thread|threading.py:1045:_bootstrap_inner > one_memory/worker.py:9:call",
      ],
    });
    expect(projected.safe_stack).toEqual([
      "packages/runtime-control/src/diagnostic-log.ts:42:7",
      "thread|threading.py:1045:_bootstrap_inner > one_memory/worker.py:9:call",
    ]);
    const conservative = projectDiagnosticEventForSafeOutput(
      diagnosticEvent({
        ...base,
        details: {
          source_id: "cursor",
          reason: "abcdef1234567890abcdef1234567890abcdef12",
          status: "11111111-1111-4111-8111-111111111111",
        },
      }),
    );
    expect(conservative.details).toEqual({
      source_id: "cursor",
      reason: "abcdef1234567890abcdef1234567890abcdef12",
      status: "11111111-1111-4111-8111-111111111111",
    });
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
        details: { nested: { secret: "private" } } as never,
      }),
    ).resolves.toBeUndefined();
    await logger.log({
      level: "info",
      component: "desktop.sdk_migration",
      event: "sdk_migration.startup",
      outcome: "succeeded",
      details: { arbitrary_state: "cutover" },
    });

    const result = await readDiagnosticEvents(dataDir);
    expect(result.invalid_lines).toBe(0);
    expect(result.events).toHaveLength(1);
    expect(result.events[0].details).toEqual({ arbitrary_state: "cutover" });
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

  it("exports events through an independent detail whitelist", async () => {
    const dataDir = await root();
    const logger = new DiagnosticLogger({ dataDir, service: "desktop" });
    const incident = await logger.markIncident({
      component: "desktop.diagnostics",
      status: "degraded",
    });
    await logger.log({
      level: "info",
      component: "desktop.diagnostics",
      event: "diagnostics.checked",
      outcome: "succeeded",
      details: {
        count: 1,
        reason: "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.signaturevalue",
        status: "ASIA1234567890ABCDEF",
        source_id: "GOCSPX-clientSecret_A1",
        mode: "Aa1Bb2Cc3Dd4Ee5Ff6Gg7Hh8",
        action: "AIzaSyExampleCredential1234567890",
        platform: "x_ASIA1234567890ABCDEF",
        phase: "x_GOCSPX-clientSecret_A1",
        local_only_secret: "must-not-export",
      },
    });
    await logger.log({
      level: "error",
      component: "credential.private",
      event: "token.failed",
      outcome: "ghp_private_token",
      trace_id: "ghp_private_token",
      instance_id: "ASIA1234567890ABCDEF",
      request_id: "GOCSPX-clientSecret_A1",
      task_id: "Aa1Bb2Cc3Dd4Ee5Ff6Gg7Hh8",
      error_code: "secret.token",
    });
    const target = path.join(await root(), "bundle.json");
    const result = await exportDiagnosticBundle({
      dataDir,
      target,
      productVersion: "1.2.3",
      status: { status: "degraded", local_only_secret: "must-not-export" },
    });
    expect(result).toEqual({ event_count: 3, invalid_lines: 0 });
    const payload = JSON.parse(await readFile(target, "utf8"));
    expect(payload.events[0].incident_id).toBe(incident.incident_id);
    expect(payload.events[1].details).toEqual({ count: 1 });
    expect(payload.events[2]).toMatchObject({
      component: "diagnostic.redacted",
      event: "diagnostic.redacted",
      outcome: "redacted",
    });
    expect(payload.events[2].trace_id).toBeUndefined();
    expect(payload.events[2].instance_id).toBeUndefined();
    expect(payload.events[2].request_id).toBeUndefined();
    expect(payload.events[2].task_id).toBeUndefined();
    expect(payload.events[2].error_code).toBeUndefined();
    expect(JSON.stringify(payload)).not.toContain("local_only_secret");
    expect(JSON.stringify(payload)).not.toContain("must-not-export");
    expect(JSON.stringify(payload)).not.toContain("eyJhbGciOiJIUzI1NiJ9");
    expect(JSON.stringify(payload)).not.toContain("ASIA1234567890ABCDEF");
    expect(JSON.stringify(payload)).not.toContain("GOCSPX-clientSecret_A1");
    expect(JSON.stringify(payload)).not.toContain("x_ASIA1234567890ABCDEF");
    expect(JSON.stringify(payload)).not.toContain("x_GOCSPX-clientSecret_A1");
    expect(JSON.stringify(payload)).not.toContain("Aa1Bb2Cc3Dd4Ee5Ff6Gg7Hh8");
    expect(JSON.stringify(payload)).not.toContain(
      "AIzaSyExampleCredential1234567890",
    );
    expect(JSON.stringify(payload)).not.toContain("ghp_private_token");
  });

  it("fails closed when exported build identity has a credential shape", async () => {
    const dataDir = await root();
    const outputDir = await root();
    await expect(
      exportDiagnosticBundle({
        dataDir,
        target: path.join(outputDir, "bundle.json"),
        productVersion: "xoxb-123456-abcdef",
      }),
    ).rejects.toThrow("diagnostic_product_version_invalid");
    await expect(
      exportDiagnosticZip({
        dataDir,
        target: path.join(outputDir, "commit.zip"),
        productVersion: "1.2.3",
        commit: "ghp_private_token",
      }),
    ).rejects.toThrow("diagnostic_commit_invalid");
    await expect(
      exportDiagnosticZip({
        dataDir,
        target: path.join(outputDir, "sha.zip"),
        productVersion: "1.2.3",
        coreSha256: "AKIDExampleSecretId1234567890",
      }),
    ).rejects.toThrow("diagnostic_sha256_invalid");
    expect(await readdir(outputDir)).toEqual([]);
  });

  it("fails closed without publishing a JSON bundle when any line is invalid", async () => {
    const dataDir = await root();
    await mkdir(path.join(dataDir, "logs"), { recursive: true });
    await writeFile(
      path.join(dataDir, "logs", "diagnostic-core.jsonl"),
      "not-json\n",
      { mode: 0o600 },
    );
    const outputDir = await root();
    const target = path.join(outputDir, "bundle.json");
    await expect(
      exportDiagnosticBundle({
        dataDir,
        target,
        productVersion: "1.2.3",
      }),
    ).rejects.toThrow("diagnostic_export_invalid_lines");
    expect(await readdir(outputDir)).toEqual([]);
  });

  it("fails closed when the diagnostic directory cannot be read", async () => {
    if (process.platform === "win32") return;
    const dataDir = await root();
    const logs = path.join(dataDir, "logs");
    await mkdir(logs, { recursive: true, mode: 0o700 });
    await writeFile(path.join(logs, "diagnostic-core.jsonl"), "{}\n");
    const outputDir = await root();
    const target = path.join(outputDir, "bundle.json");
    await chmod(logs, 0o000);
    try {
      await expect(
        exportDiagnosticBundle({
          dataDir,
          target,
          productVersion: "1.2.3",
        }),
      ).rejects.toMatchObject({ code: "EACCES" });
    } finally {
      await chmod(logs, 0o700);
    }
    expect(await readdir(outputDir)).toEqual([]);
  });

  it("never overwrites an existing export target or leaves temporary files", async () => {
    const dataDir = await root();
    const outputDir = await root();
    const target = path.join(outputDir, "bundle.json");
    await writeFile(target, "existing-artifact", { mode: 0o600 });
    await expect(
      exportDiagnosticBundle({
        dataDir,
        target,
        productVersion: "1.2.3",
      }),
    ).rejects.toMatchObject({ code: "EEXIST" });
    expect(await readFile(target, "utf8")).toBe("existing-artifact");
    expect(await readdir(outputDir)).toEqual(["bundle.json"]);
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

  it("exports only reprojected events, a strict summary and README", async () => {
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
          description:
            "蓝莓没出来 /Users/private/query.txt ASIA1234567890ABCDEF GOCSPX-clientSecret_A1 Aa1Bb2Cc3Dd4Ee5Ff6Gg7Hh8 token=ghp_private_token xoxb-123456-abcdef AKIDExampleSecretId1234567890 AIzaSyExampleCredential1234567890 eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.signaturevalue",
        },
      ],
      summary: {
        sources: [{ source_id: "workbuddy", imported_messages: 12 }],
        summary_secret: conversation,
        background: {
          status: "exported",
          runtime_state: "ready",
          core_state: "healthy",
          broker_state: "ready",
          instance_id: "instance-1",
          secret: conversation,
        },
      },
    });

    expect(result).toEqual({ filename: "MemHub.zip", file_count: 3 });
    const files = unzipSync(await readFile(target));
    expect(Object.keys(files).sort()).toEqual([
      "README.txt",
      "diagnostics/events.jsonl",
      "summary.json",
    ]);
    const eventFile = files["diagnostics/events.jsonl"];
    const summaryFile = files["summary.json"];
    const readme = files["README.txt"];
    if (!eventFile || !summaryFile || !readme)
      throw new Error("missing support bundle metadata");
    const exportedEvent = JSON.parse(strFromU8(eventFile).trim());
    expect(exportedEvent.details).toEqual({ count: 1 });
    const summary = JSON.parse(strFromU8(summaryFile));
    expect(summary.background).toEqual({
      status: "exported",
      runtime_state: "ready",
      core_state: "healthy",
      broker_state: "ready",
      instance_id: "instance-1",
    });
    expect(summary.sources).toBeUndefined();
    expect(summary.summary_secret).toBeUndefined();
    expect(summary.problem_markers).toEqual([
      {
        timestamp: "2026-10-05T01:30:00.000Z",
        description: "user_marked_incident",
      },
    ]);
    expect(strFromU8(readme)).toContain("user_marked_incident");
    expect(strFromU8(readme)).not.toContain("蓝莓没出来");
    const all = Object.values(files)
      .map((value) => strFromU8(value))
      .join("\n");
    expect(all).not.toContain(secret);
    expect(all).not.toContain("ghp_private_token");
    expect(all).not.toContain("xoxb-123456-abcdef");
    expect(all).not.toContain("AKIDExampleSecretId1234567890");
    expect(all).not.toContain("ASIA1234567890ABCDEF");
    expect(all).not.toContain("GOCSPX-clientSecret_A1");
    expect(all).not.toContain("Aa1Bb2Cc3Dd4Ee5Ff6Gg7Hh8");
    expect(all).not.toContain("AIzaSyExampleCredential1234567890");
    expect(all).not.toContain("eyJhbGciOiJIUzI1NiJ9");
    expect(all).not.toContain(conversation);
    expect(all).not.toContain(home);
    expect(all).not.toContain("/Users/");
  });

  it("fails closed without publishing a ZIP when a diagnostic line is invalid", async () => {
    const dataDir = await root();
    await mkdir(path.join(dataDir, "logs"), { recursive: true });
    await writeFile(
      path.join(dataDir, "logs", "diagnostic-core.jsonl"),
      "not-json\n",
    );
    const outputDir = await root();
    const target = path.join(outputDir, "MemHub.zip");
    await expect(
      exportDiagnosticZip({
        dataDir,
        target,
        productVersion: "3800",
      }),
    ).rejects.toThrow("diagnostic_export_invalid_lines");
    expect(await readdir(outputDir)).toEqual([]);
  });

  it("stores and exports only the fixed marker description", async () => {
    const dataDir = await root();
    const markerFile = path.join(dataDir, "logs", "problem-markers.jsonl");
    await recordDiagnosticMarker(
      dataDir,
      {
        timestamp: "2026-10-05T01:00:00.000Z",
        description:
          "蓝莓 /Users/private/query.txt ASIA1234567890ABCDEF GOCSPX-clientSecret_A1 Aa1Bb2Cc3Dd4Ee5Ff6Gg7Hh8",
      },
      "/Users/private",
    );
    expect(await readFile(markerFile, "utf8")).toBe(
      `${JSON.stringify({ timestamp: "2026-10-05T01:00:00.000Z", description: "user_marked_incident" })}\n`,
    );
    await writeFile(
      markerFile,
      `${await readFile(markerFile, "utf8")}${JSON.stringify({ timestamp: "2026-10-05T01:30:00.000Z", description: "legacy arbitrary text" })}\n`,
      { mode: 0o600 },
    );

    const target = path.join(await root(), "markers.zip");
    await exportDiagnosticZip({
      dataDir,
      target,
      productVersion: "3800",
      now: new Date("2026-10-05T02:00:00.000Z"),
    });
    const files = unzipSync(await readFile(target));
    const summaryFile = files["summary.json"];
    if (!summaryFile) throw new Error("missing summary.json");
    const payload = JSON.stringify(JSON.parse(strFromU8(summaryFile)));
    expect(payload.match(/user_marked_incident/g)).toHaveLength(2);
    expect(payload).not.toContain("legacy arbitrary text");
    expect(payload).not.toContain("蓝莓");
    expect(payload).not.toContain("/Users/");
  });

  it("never follows a problem marker symlink for reads or writes", async () => {
    const dataDir = await root();
    const logs = path.join(dataDir, "logs");
    await mkdir(logs, { recursive: true });
    const outside = path.join(await root(), "outside-marker");
    await writeFile(outside, "private");
    await symlink(outside, path.join(logs, "problem-markers.jsonl"));

    await expect(
      recordDiagnosticMarker(dataDir, {
        timestamp: "2026-10-05T01:00:00.000Z",
        description: "anything",
      }),
    ).rejects.toThrow("diagnostic_marker_file_unsafe");
    await expect(
      exportDiagnosticZip({
        dataDir,
        target: path.join(await root(), "markers.zip"),
        productVersion: "3800",
      }),
    ).rejects.toThrow("diagnostic_marker_file_unsafe");
    expect(await readFile(outside, "utf8")).toBe("private");
  });

  it("rejects invalid explicit and persisted markers without publishing a ZIP", async () => {
    const dataDir = await root();
    const logs = path.join(dataDir, "logs");
    await mkdir(logs, { recursive: true });
    const outputDir = await root();
    const explicitTarget = path.join(outputDir, "explicit.zip");
    await expect(
      exportDiagnosticZip({
        dataDir,
        target: explicitTarget,
        productVersion: "3800",
        markers: [{ timestamp: "invalid", description: "marker" }],
      }),
    ).rejects.toThrow("diagnostic_marker_invalid");
    await writeFile(path.join(logs, "problem-markers.jsonl"), "not-json\n");
    const persistedTarget = path.join(outputDir, "persisted.zip");
    await expect(
      exportDiagnosticZip({
        dataDir,
        target: persistedTarget,
        productVersion: "3800",
      }),
    ).rejects.toThrow("diagnostic_marker_invalid");
    expect(await readdir(outputDir)).toEqual([]);
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
