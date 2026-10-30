#!/usr/bin/env node
// Real-process evidence for CLI-20/21: runs the built CLI against a temporary
// local HTTP stand-in for Core over a real socket, in a throwaway data
// directory. Synthetic data only; no real user library, no Runtime, no model.
//
//   node apps/cli/scripts/real-read-probe.mjs
//
// It prints every request the stand-in received, so "the CLI only issued GET
// to the allowlist" is observed rather than asserted.

import { spawn } from "node:child_process";
import { mkdtemp, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const CLI = fileURLToPath(new URL("../dist/one-memory.cjs", import.meta.url));
const INSTANCE = "probe-instance";
const TOKEN = "probe-token-not-a-real-credential";

const MEMORY = {
  memory_id: "probe-1",
  content: "部署脚本要先跑 verify_merge_plan。",
  layer: "l2_fact",
  status: "active",
  is_latest: true,
  supersedes: [],
  superseded_by: [],
  tags: [],
  owner: null,
  source_raw_ids: ["probe-raw-1"],
  user_id: "local",
  agent_id: "codex",
  session_id: "probe-session",
  source: "codex",
  memory_at: 1758240000,
  gmt_created: 1758240000,
};

const ROUTES = new Map([
  [
    "/api/v1/runtime/identity",
    {
      status: "ok",
      service: "one-memory-core",
      version: "probe-1.0.0",
      instance_id: INSTANCE,
    },
  ],
  [
    "/api/v1/sources",
    [
      {
        source_id: "codex",
        display_name: "Codex",
        storage_kinds: ["history"],
        available: true,
        authorized: true,
        detected: true,
        root_label: "Codex history",
        candidate_path: "/synthetic/codex",
        candidate_kind: "default",
        imported_conversations: 3,
        imported_messages: 41,
        capabilities: {
          historical_import: "implemented",
          incremental_import: "implemented",
          live_sync: "implemented",
          recall: "implemented",
          installer: "not_implemented",
          verification: "fixture_only",
        },
        sync_completeness: "additions_only",
      },
    ],
  ],
  [
    "/api/v1/live-sync",
    [
      {
        source_id: "codex",
        mode: "polling",
        state: "running",
        last_job_id: null,
        last_attempt_at: null,
        last_success_at: null,
        last_error_code: null,
      },
    ],
  ],
  [
    "/api/v1/memory-processing",
    {
      pending: 1,
      fresh_pending: 1,
      backlog_pending: 0,
      outside_window_pending: 0,
      running: 0,
      waiting_for_model: 1,
      completed: 4,
      failed: 0,
      blocked_stages: [],
    },
  ],
  [
    "/api/v1/import-jobs",
    [
      {
        job_id: "probe-job",
        user_id: "local",
        source_id: "codex",
        operation: "history",
        status: "running",
        phase: "read",
        checkpoint: null,
        discovered: 3,
        discovered_targets: 1,
        emitted: 2,
        stored: 2,
        revised: 0,
        merged: 0,
        skipped: 0,
        errors: 0,
        created_at: "2026-09-19T00:00:00Z",
        updated_at: "2026-09-19T00:00:01Z",
      },
    ],
  ],
  [
    "/api/v1/model-profiles",
    [
      {
        profile_id: "p1",
        name: "probe",
        provider_kind: "openai_compatible",
        model: "probe-model",
        has_api_key: true,
        credential_state: "available",
        is_active: true,
        api_key: "sk-probe-must-never-be-printed",
      },
    ],
  ],
  [
    "/api/v2/home-summary",
    {
      schema_version: 2,
      memory_count: 1,
      latest: MEMORY,
      observed_at: "2026-09-19T00:00:01Z",
    },
  ],
  [
    "/api/v2/memory-library",
    { memories: [MEMORY], total: 1, offset: 0, limit: 100 },
  ],
  ["/api/v2/memories/probe-1", MEMORY],
]);

const seen = [];

const server = createServer((request, response) => {
  const url = new URL(request.url, "http://127.0.0.1");
  seen.push({
    method: request.method,
    path: url.pathname,
    query: url.search,
    authorized: request.headers.authorization === `Bearer ${TOKEN}`,
  });
  if (
    request.method !== "GET" ||
    request.headers.authorization !== `Bearer ${TOKEN}`
  ) {
    response.writeHead(403).end("{}");
    return;
  }
  const body = ROUTES.get(url.pathname);
  response.writeHead(body === undefined ? 404 : 200, {
    "content-type": "application/json",
  });
  response.end(JSON.stringify(body ?? { detail: "not_found" }));
});

const dataDir = await mkdtemp(path.join(tmpdir(), "om-cli-probe-"));
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const port = server.address().port;

await writeFile(
  path.join(dataDir, "runtime.json"),
  JSON.stringify({
    schema_version: 1,
    service: "one-memory-core",
    instance_id: INSTANCE,
    pid: process.pid,
    parent_pid: null,
    host: "127.0.0.1",
    port,
    started_at: new Date().toISOString(),
  }),
);
await writeFile(
  path.join(dataDir, "runtime-service.json"),
  JSON.stringify({
    schema_version: 1,
    service: "one-memory-runtime",
    instance_id: INSTANCE,
    pid: process.pid,
    status: "running",
    core_port: port,
  }),
);
await writeFile(path.join(dataDir, "runtime-token"), TOKEN, { mode: 0o600 });

console.log(`data dir: ${dataDir}`);
console.log(`core stand-in: http://127.0.0.1:${port}\n`);

for (const argv of [
  ["status"],
  ["status", "--json"],
  ["sources", "list", "--json"],
  ["sync", "status", "--json"],
  ["progress", "--json"],
  ["memories", "list"],
  ["memories", "show", "probe-1", "--json"],
]) {
  const before = seen.length;
  const result = await runCli(argv);
  console.log(`$ one-memory ${argv.join(" ")}`);
  console.log(`exit=${result.code}`);
  console.log(`--stdout--\n${result.stdout}--stderr--\n${result.stderr}`);
  console.log(
    `--requests--\n${seen
      .slice(before)
      .map(
        (item) =>
          `${item.method} ${item.path}${item.query} auth=${item.authorized}`,
      )
      .join("\n")}\n`,
  );
  if (result.stdout.includes("sk-probe-must-never-be-printed")) {
    console.log("LEAK: the server's api_key reached stdout");
    process.exitCode = 1;
  }
}

const watchBefore = seen.length;
const watch = await runWatchCli([
  "status",
  "--watch",
  "--interval",
  "1",
  "--json",
]);
console.log("$ one-memory status --watch --interval 1 --json");
console.log(
  `exit=${watch.code} events=${watch.stdout.trim().split("\n").length}`,
);
console.log(
  `--requests--\n${seen
    .slice(watchBefore)
    .map(
      (item) =>
        `${item.method} ${item.path}${item.query} auth=${item.authorized}`,
    )
    .join("\n")}\n`,
);

const methods = [...new Set(seen.map((item) => item.method))];
console.log(`methods observed: ${methods.join(", ")}`);
console.log(
  `unauthenticated requests: ${seen.filter((item) => !item.authorized).length}`,
);
server.close();

function runCli(argv) {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [CLI, ...argv], {
      env: {
        PATH: process.env.PATH,
        HOME: dataDir,
        ONE_MEMORY_DATA_DIR: dataDir,
      },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    child.on("close", (code) => resolve({ code, stdout, stderr }));
  });
}

function runWatchCli(argv) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [CLI, ...argv], {
      env: {
        PATH: process.env.PATH,
        HOME: dataDir,
        ONE_MEMORY_DATA_DIR: dataDir,
      },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    let signalled = false;
    const timeout = setTimeout(() => {
      child.kill("SIGKILL");
      reject(new Error("watch_probe_timeout"));
    }, 8_000);
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
      if (!signalled && stdout.trim().split("\n").length >= 2) {
        signalled = true;
        child.kill("SIGINT");
      }
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    child.on("error", reject);
    child.on("close", (code) => {
      clearTimeout(timeout);
      resolve({ code, stdout, stderr });
    });
  });
}
