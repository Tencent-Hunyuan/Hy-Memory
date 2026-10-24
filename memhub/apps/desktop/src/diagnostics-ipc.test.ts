import { execFileSync } from "node:child_process";
import {
  appendFile,
  mkdir,
  mkdtemp,
  readdir,
  readFile,
  rename,
  rm,
  writeFile,
} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { DiagnosticLogger } from "@one-memory/runtime-control";
import { afterEach, expect, it, vi } from "vitest";

import {
  captureDebugLogCursor,
  installDiagnosticsIpc,
  readDebugLogSnapshot,
  resolveDiagnosticBuildIdentity,
  uiTraceDiagnosticInput,
} from "./diagnostics-ipc";

const roots: string[] = [];
afterEach(async () =>
  Promise.all(roots.splice(0).map((root) => rm(root, { recursive: true }))),
);

it("uses embedded package identity when Finder supplies no environment", () => {
  expect(
    resolveDiagnosticBuildIdentity({
      embeddedBuild: "3812",
      embeddedCommit: "5eb1230463de8de662b51fb180367402d37682d3",
      productVersion: "0.0.0",
    }),
  ).toEqual({
    buildNumber: "3812",
    commit: "5eb1230463de8de662b51fb180367402d37682d3",
  });
});

it("prefers runtime identity and ignores unknown build placeholders", () => {
  expect(
    resolveDiagnosticBuildIdentity({
      environmentBuild: "3813",
      environmentCommit: "runtime-commit",
      embeddedBuild: "unknown",
      embeddedCommit: "unknown",
      productVersion: "0.0.0",
    }),
  ).toEqual({ buildNumber: "3813", commit: "runtime-commit" });
});

async function fixture(trusted = true) {
  const dataDir = await mkdtemp(
    path.join(os.tmpdir(), "om-desktop-diagnostics-"),
  );
  roots.push(dataDir);
  const handlers = new Map<string, (...args: unknown[]) => Promise<unknown>>();
  const window = {} as never;
  const logger = new DiagnosticLogger({ dataDir, service: "desktop" });
  const startedAt = Date.now();
  const debugLogCursor = await captureDebugLogCursor(dataDir);
  const revealItem = vi.fn();
  const dispose = installDiagnosticsIpc({
    ipc: {
      handle: (name, handler) => handlers.set(name, handler as never),
      removeHandler: (name) => void handlers.delete(name),
    },
    dataDir,
    productVersion: "1.2.3",
    buildNumber: "3800",
    home: path.dirname(dataDir),
    downloadsDirectory: dataDir,
    crashReportsDirectory: path.join(dataDir, "crashes"),
    logger,
    startedAt,
    debugLogCursor,
    logUiTrace: (input) => logger.log(uiTraceDiagnosticInput(input)),
    requireTrustedWindow: vi.fn(() => {
      if (!trusted) throw new Error("untrusted_window");
      return window;
    }),
    revealItem,
  });
  return { dataDir, handlers, dispose, revealItem };
}

it("returns only diagnostic events written during this desktop launch", async () => {
  const dataDir = await mkdtemp(path.join(os.tmpdir(), "om-debug-logs-"));
  roots.push(dataDir);
  const logs = path.join(dataDir, "logs");
  await mkdir(logs, { recursive: true });
  const target = path.join(logs, "diagnostic-core.jsonl");
  const old = {
    schema_version: 1,
    timestamp: "2026-10-14T09:00:00.000Z",
    level: "info",
    service: "core",
    component: "core.startup",
    event: "core.old",
    outcome: "succeeded",
    session_id: "11111111-1111-4111-8111-111111111111",
  };
  await writeFile(target, `${JSON.stringify(old)}\n`);
  const cursor = await captureDebugLogCursor(dataDir);
  const startedAt = Date.parse("2026-10-14T10:00:00.000Z");
  const current = {
    ...old,
    timestamp: "2026-10-14T10:01:00.000Z",
    event: "core.current",
    level: "warn",
    details: {
      count: 1,
      "custom.private": "/Users/private/query.txt",
      credential_hint: "ghp_private_token",
    },
  };
  await appendFile(target, `${JSON.stringify(current)}\nnot-json\n`);

  const snapshot = await readDebugLogSnapshot(dataDir, startedAt, cursor);

  expect(snapshot.entries).toHaveLength(1);
  expect(snapshot.entries[0]).toMatchObject({
    event: "core.current",
    level: "warn",
    details: { count: 1 },
    source: "diagnostic",
  });
  expect(JSON.stringify(snapshot)).not.toContain("/Users/private");
  expect(JSON.stringify(snapshot)).not.toContain("ghp_private_token");
});

it("tracks diagnostic JSONL across rotation without rereading the old prefix", async () => {
  const dataDir = await mkdtemp(path.join(os.tmpdir(), "om-debug-jsonl-"));
  roots.push(dataDir);
  const logs = path.join(dataDir, "logs");
  await mkdir(logs, { recursive: true });
  const target = path.join(logs, "diagnostic-core.jsonl");
  const event = (name: string, timestamp: string) =>
    JSON.stringify({
      schema_version: 1,
      timestamp,
      level: "info",
      service: "core",
      component: "core.test",
      event: name,
      outcome: "succeeded",
      session_id: "22222222-2222-4222-8222-222222222222",
    });
  await writeFile(target, `${event("before", "2026-10-14T09:00:00.000Z")}\n`);
  const cursor = await captureDebugLogCursor(dataDir);
  await appendFile(
    target,
    `${event("current-a", "2026-10-14T10:01:00.000Z")}\n`,
  );
  await rename(target, `${target}.1`);
  await writeFile(
    target,
    `${event("current-b", "2026-10-14T10:02:00.000Z")}\n`,
  );

  const snapshot = await readDebugLogSnapshot(
    dataDir,
    Date.parse("2026-10-14T10:00:00.000Z"),
    cursor,
  );

  expect(snapshot.entries.map((entry) => entry.event)).toEqual([
    "current-a",
    "current-b",
  ]);
});

it("keeps only actionable raw service output across rotation", async () => {
  const dataDir = await mkdtemp(path.join(os.tmpdir(), "om-debug-service-"));
  roots.push(dataDir);
  const logs = path.join(dataDir, "logs");
  await mkdir(logs, { recursive: true });
  const target = path.join(logs, "service.log");
  await writeFile(target, "before launch\n");
  const cursor = await captureDebugLogCursor(dataDir);
  const startedAt = Date.now();
  await appendFile(target, "INFO current request\n");
  await rename(target, `${target}.1`);
  await writeFile(target, "WARNING after rotation\nERROR failed request\n");

  const snapshot = await readDebugLogSnapshot(dataDir, startedAt, cursor);

  expect(snapshot.entries.map((entry) => entry.message)).toEqual([
    "WARNING after rotation",
    "ERROR failed request",
  ]);
  expect(snapshot.entries.map((entry) => entry.level)).toEqual([
    "warn",
    "error",
  ]);
});

it("filters raw Core INFO and DEBUG while preserving warnings and errors", async () => {
  const dataDir = await mkdtemp(path.join(os.tmpdir(), "om-debug-core-log-"));
  roots.push(dataDir);
  const logs = path.join(dataDir, "logs");
  await mkdir(logs, { recursive: true });
  const target = path.join(logs, "core.log");
  await writeFile(target, "2026-10-14 10:00:00 INFO old line\n");
  const cursor = await captureDebugLogCursor(dataDir);
  const startedAt = Date.parse("2026-10-14T10:30:00+08:00");
  await appendFile(
    target,
    "2026-10-14 10:30:30 DEBUG hy pipeline queued\n" +
      '2026-10-14 10:30:45 INFO: 127.0.0.1:54000 - "GET /health HTTP/1.1" 200 OK\n' +
      "2026-10-14 10:31:00 WARNING database wait\n" +
      "2026-10-14 10:32:00 ERROR worker failed\n",
  );

  const snapshot = await readDebugLogSnapshot(dataDir, startedAt, cursor);

  expect(snapshot.entries.map((entry) => entry.message)).toEqual([
    "2026-10-14 10:31:00 WARNING database wait",
    "2026-10-14 10:32:00 ERROR worker failed",
  ]);
  expect(snapshot.entries.map((entry) => entry.level)).toEqual([
    "warn",
    "error",
  ]);
  expect(
    snapshot.entries.every((entry) => entry.component === "core.file"),
  ).toBe(true);
});

it("shows HY pipeline metrics without exposing model content", async () => {
  const dataDir = await mkdtemp(
    path.join(os.tmpdir(), "om-debug-hy-pipeline-"),
  );
  roots.push(dataDir);
  const pipelineDir = path.join(
    dataDir,
    "hy-memory",
    "governed-v2",
    "logs",
    "default",
  );
  await mkdir(pipelineDir, { recursive: true });
  const target = path.join(pipelineDir, "2026-10-14.log");
  await writeFile(target, "");
  const cursor = await captureDebugLogCursor(dataDir);
  await appendFile(
    target,
    `${JSON.stringify({
      request_id: "request-safe-1",
      user_id: "private-user",
      agent_id: "private-agent",
      session_id: "private-session",
      step: "EXTRACT",
      prompt: "private prompt",
      content: "private response",
      parsed: "private parsed output",
      tokens: 42,
      elapsed_ms: 123.5,
      timestamp: "2026-10-14T10:01:00.000",
    })}\n`,
  );

  const snapshot = await readDebugLogSnapshot(
    dataDir,
    Date.parse("2026-10-14T10:00:00.000Z"),
    cursor,
  );

  expect(snapshot.entries).toEqual([
    expect.objectContaining({
      service: "hy-memory",
      component: "hy.pipeline",
      event: "hy.pipeline.extract",
      message: "EXTRACT · 42 tokens · 124 ms",
      duration_ms: 123.5,
      trace_id: "request-safe-1",
      details: { phase: "EXTRACT", count: 42 },
      source: "pipeline",
    }),
  ]);
  const serialized = JSON.stringify(snapshot);
  for (const secret of [
    "private prompt",
    "private response",
    "private parsed output",
    "private-user",
    "private-agent",
    "private-session",
  ]) {
    expect(serialized).not.toContain(secret);
  }
});

it("logs a renderer ui-action with action, page, phase and the shared trace id", async () => {
  const { handlers, dataDir } = await fixture();
  const trace = "trace-abc-123";
  await handlers.get("one-memory:log-ui-trace")?.(
    {},
    {
      action: "toggle",
      page: "sources",
      phase: "action",
      trace_id: trace,
    },
  );
  const lines = (
    await readFile(
      path.join(dataDir, "logs", "diagnostic-desktop.jsonl"),
      "utf8",
    )
  )
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line));
  const event = lines.at(-1) as Record<string, unknown>;
  expect(event.component).toBe("renderer.ui");
  expect(event.event).toBe("ui.action");
  expect(event.trace_id).toBe(trace);
  expect(event.details).toMatchObject({
    action: "toggle",
    mode: "sources",
    phase: "action",
  });
});

it("logs the ui-result line with the same trace id and an outcome", async () => {
  const { handlers, dataDir } = await fixture();
  const trace = "trace-abc-123";
  await handlers.get("one-memory:log-ui-trace")?.(
    {},
    {
      action: "toggle",
      page: "sources",
      phase: "result",
      status: "failed",
      reason: "read_off_failed",
      trace_id: trace,
    },
  );
  const lines = (
    await readFile(
      path.join(dataDir, "logs", "diagnostic-desktop.jsonl"),
      "utf8",
    )
  )
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line));
  const event = lines.at(-1) as Record<string, unknown>;
  expect(event.event).toBe("ui.result");
  expect(event.outcome).toBe("failed");
  expect(event.trace_id).toBe(trace);
  expect(event.details).toMatchObject({
    mode: "sources",
    status: "failed",
    reason: "read_off_failed",
  });
});

it("rejects a replayed page, an unknown action and extra positional arguments", async () => {
  const { handlers } = await fixture();
  const handler = handlers.get("one-memory:log-ui-trace");
  if (!handler) throw new Error("missing ui trace handler");
  await expect(
    handler({}, { action: "toggle", page: "nowhere", phase: "action" }),
  ).rejects.toThrow(/ui_trace_page_rejected/);
  await expect(
    handler({}, { action: "detonate", page: "sources", phase: "action" }),
  ).rejects.toThrow(/ui_trace_action_rejected/);
  await expect(
    handler(
      {},
      { action: "toggle", page: "sources", phase: "action" },
      "extra",
    ),
  ).rejects.toThrow(/ui_trace_arguments_rejected/);
  await expect(
    handler(
      {},
      {
        action: "toggle",
        page: "sources",
        phase: "action",
        trace_id: "bad id",
      },
    ),
  ).rejects.toThrow(/ui_trace_trace_id_rejected/);
});

it("refuses a ui trace from an untrusted window", async () => {
  const { handlers } = await fixture(false);
  await expect(
    handlers.get("one-memory:log-ui-trace")?.(
      {},
      { action: "toggle", page: "sources", phase: "action" },
    ),
  ).rejects.toThrow(/untrusted_window/);
});

it("marks a problem and exports a redacted local bundle", async () => {
  const value = await fixture();
  const mark = await value.handlers.get(
    "one-memory:mark-diagnostic-incident",
  )?.({}, "蓝莓没出来");
  expect(mark).toMatchObject({
    incident_id: expect.any(String),
    session_id: expect.any(String),
  });
  const exported = await value.handlers.get("one-memory:export-diagnostics")?.(
    {},
  );
  expect(exported).toMatchObject({
    state: "exported",
    file_count: expect.any(Number),
  });
  const zip = (await readdir(value.dataDir)).find((name) =>
    name.endsWith(".zip"),
  );
  expect(zip).toMatch(/^MemHub-日志-3800-/);
  if (!zip) throw new Error("missing diagnostic zip");
  expect(value.revealItem).toHaveBeenCalledWith(path.join(value.dataDir, zip));
  const summary = JSON.parse(
    execFileSync(
      "/usr/bin/unzip",
      ["-p", path.join(value.dataDir, zip), "summary.json"],
      {
        encoding: "utf8",
      },
    ),
  );
  expect(summary).toMatchObject({ build: "3800" });
  value.dispose();
});

it("rejects an untrusted renderer before writing an export", async () => {
  const value = await fixture(false);
  await expect(
    value.handlers.get("one-memory:mark-diagnostic-incident")?.({}),
  ).rejects.toThrow("untrusted_window");
  await expect(
    value.handlers.get("one-memory:export-diagnostics")?.({}),
  ).rejects.toThrow("untrusted_window");
  await expect(
    readFile(path.join(value.dataDir, "logs", "diagnostic-desktop.jsonl")),
  ).rejects.toMatchObject({ code: "ENOENT" });
});

it("preload exposes the read-only debug log snapshot channel", async () => {
  const source = await readFile(path.join(__dirname, "preload.ts"), "utf8");
  expect(source).toContain("getDebugLogs");
  expect(source).toContain('ipcRenderer.invoke("one-memory:get-debug-logs")');
});
