import { mkdtemp, rm, symlink } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { DiagnosticLogger } from "@one-memory/runtime-control";
import { afterEach, expect, it } from "vitest";

import {
  followLogs,
  parseLogQuery,
  readLogReport,
  redactDiagnosticEvent,
  renderLogText,
} from "./log-report.js";

const roots: string[] = [];
afterEach(async () =>
  Promise.all(roots.splice(0).map((root) => rm(root, { recursive: true }))),
);

it("parses bounded filters and refuses malformed identifiers", () => {
  expect(
    parseLogQuery(["--since", "15m", "--errors-only", "--tail", "25"]),
  ).toMatchObject({
    errorsOnly: true,
    tail: 25,
  });
  expect(parseLogQuery(["--tail", "5001"])).toBeNull();
  expect(parseLogQuery(["--trace", "not-a-uuid"])).toBeNull();
  expect(parseLogQuery(["--component", "../../private"])).toBeNull();
});

it("redacts unsafe details and absolute stack frames from the read surface", () => {
  const event = {
    schema_version: 1 as const,
    timestamp: "2026-09-24T02:00:00.000Z",
    level: "error" as const,
    service: "runtime" as const,
    component: "runtime.core",
    event: "core.failed",
    outcome: "failed",
    session_id: "11111111-1111-4111-8111-111111111111",
    details: {
      count: 1,
      reason: "/Users/me/private-query.txt",
      status: "sk-live-secret-token",
      source_id: "credential.private",
      prompt: "secret-token-123",
      authorization: "Bearer secret-token-123",
    } as unknown as Record<string, string | number | boolean | null>,
    safe_stack: [
      "packages/runtime-control/src/example.ts:42:7",
      "packages/runtime/sk-live-secret-token.ts:1:1",
      "/Users/me/private.py:1",
      "C:\\Users\\me\\private.py:1",
    ],
  };
  const redacted = redactDiagnosticEvent(event);
  expect(redacted.details).toEqual({ count: 1 });
  expect(redacted.safe_stack).toEqual([
    "packages/runtime-control/src/example.ts:42:7",
  ]);
  expect(JSON.stringify(redacted)).not.toContain("secret-token-123");
  expect(JSON.stringify(redacted)).not.toContain("sk-live-secret-token");
  expect(JSON.stringify(redacted)).not.toContain("credential.private");
  expect(JSON.stringify(redacted)).not.toContain("/Users/me");
  expect(JSON.stringify(redacted)).not.toContain("C:\\\\Users");
});

it("defaults to the newest session and renders correlation ids", async () => {
  const dataDir = await mkdtemp(path.join(os.tmpdir(), "om-cli-logs-"));
  roots.push(dataDir);
  const old = new DiagnosticLogger({
    dataDir,
    service: "runtime",
    sessionId: "11111111-1111-4111-8111-111111111111",
    now: () => new Date("2026-09-24T01:00:00Z"),
  });
  const recent = new DiagnosticLogger({
    dataDir,
    service: "desktop",
    sessionId: "22222222-2222-4222-8222-222222222222",
    now: () => new Date("2026-09-24T02:00:00Z"),
  });
  await old.log({
    level: "info",
    component: "runtime.service",
    event: "service.ready",
    outcome: "succeeded",
  });
  await recent.log({
    level: "error",
    component: "desktop.connection",
    event: "connection.failed",
    outcome: "failed",
    operation_id: "operation-1",
  });
  const query = parseLogQuery([]);
  expect(query).not.toBeNull();
  if (!query) throw new Error("query_missing");
  const report = await readLogReport(dataDir, query);
  expect(report.events).toHaveLength(1);
  expect(renderLogText(report.events, 0)).toContain("operation=operation-1");
});

it("follows a newly appended event without stopping Runtime", async () => {
  const dataDir = await mkdtemp(path.join(os.tmpdir(), "om-cli-follow-"));
  roots.push(dataDir);
  const logger = new DiagnosticLogger({ dataDir, service: "runtime" });
  await logger.log({
    level: "info",
    component: "runtime.service",
    event: "service.ready",
    outcome: "succeeded",
  });
  const seen: string[] = [];
  let waited = false;
  const query = parseLogQuery(["--follow"]);
  expect(query).not.toBeNull();
  if (!query) throw new Error("query_missing");
  await followLogs({
    dataDir,
    query,
    signal: new AbortController().signal,
    maxEvents: 2,
    emit: (event) => seen.push(event.event),
    wait: async () => {
      if (!waited) {
        waited = true;
        await logger.log({
          level: "error",
          component: "runtime.core",
          event: "core.failed",
          outcome: "failed",
          error_code: "core_failed",
        });
      }
    },
  });
  expect(seen).toEqual(["service.ready", "core.failed"]);
});

it("fails follow mode when the diagnostic directory is unsafe", async () => {
  const dataDir = await mkdtemp(
    path.join(os.tmpdir(), "om-cli-follow-unsafe-"),
  );
  const external = await mkdtemp(
    path.join(os.tmpdir(), "om-cli-follow-external-"),
  );
  roots.push(dataDir, external);
  await symlink(external, path.join(dataDir, "logs"));
  const query = parseLogQuery(["--follow"]);
  expect(query).not.toBeNull();
  if (!query) throw new Error("query_missing");
  await expect(
    followLogs({
      dataDir,
      query,
      signal: new AbortController().signal,
      maxEvents: 1,
      emit: () => undefined,
      wait: async () => undefined,
    }),
  ).rejects.toThrow("log_directory_unreadable");
});
