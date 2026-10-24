#!/usr/bin/env node
/**
 * Read-only package CLI contract probe.
 *
 * It validates the machine-readable surface that package acceptance consumes:
 * version, help JSON, capability aliases, stopped-runtime exit/JSON, and no-TTY
 * behavior. Lifecycle mutations remain in the package overlay receipts and are
 * intentionally not performed by this probe.
 *
 * Usage:
 *   node apps/cli/scripts/verify-package-matrix.mjs <runtime-dist> [--json]
 */

import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { access, chmod, mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

const REQUIRED_ALIASES = {
  daemon: ["start", "stop", "restart", "status"],
  "startup-service": ["service-plan", "service-install", "service-uninstall"],
};

const REQUIRED_COMMAND_SURFACE = [
  "install",
  "init",
  "sources",
  "sync",
  "memories",
  "profile",
  "calls",
  "notes",
  "graph",
  "models",
  "host",
  "doctor",
  "logs",
  "ui",
  "start",
  "stop",
  "restart",
  "status",
  "service-plan",
  "service-install",
  "service-uninstall",
  "backup",
  "upgrade",
  "uninstall",
  "delete-data",
  "recall",
  "setup",
  "scan",
  "progress",
  "jobs",
  "path",
  "adapter",
  "write",
  "task",
  "serve",
];

const REQUIRED_MARKERS = [
  "backup_restore_confirmation_required",
  "backup_restore_runtime_state_unknown",
  "backup_restore_maintenance_identity_invalid",
  "runtime_service_activation_unverified",
  "ui_session_contract_unavailable",
  "ui_session_exchange_failed",
  "/api/v1/backup/restore",
  "/api/v1/ui/session",
];

/**
 * Sub-commands the UI-write parity work added. A package can declare the
 * parent command (`models`) while still missing the new verb, so
 * checking command names alone would pass a stale bundle. The marker is the
 * exact literal the catalog usage string contains.
 */
const REQUIRED_SUBCOMMANDS = [
  { command: "calls", marker: "memhub calls clear" },
  { command: "notes", marker: "memhub notes add" },
  { command: "notes", marker: "memhub notes process" },
  { command: "memories", marker: "memhub memories update" },
  { command: "memories", marker: "memhub memories delete" },
  { command: "write", marker: "memhub write submit" },
  { command: "write", marker: "memhub write status" },
  { command: "graph", marker: "memhub graph width" },
  { command: "models", marker: "memhub models edit" },
  { command: "models", marker: "memhub models activate" },
  { command: "jobs", marker: "memhub jobs start" },
  { command: "jobs", marker: "memhub jobs <pause|resume|cancel>" },
];

/**
 * Every destructive command the UI-write surface added must fail closed when
 * no Runtime is running: a structured JSON `unknown` envelope and exit 3, with
 * no partial write. Without this the closure condition ("destructive
 * counterexamples on the packaged Runtime") had no machine-checkable probe.
 * These run without `--lifecycle`, so they are safe against any package.
 */
const STOPPED_DESTRUCTIVE_PROBES = [
  {
    name: "calls_clear_preview",
    args: ["calls", "clear", "--event", "11111111-1111-4111-8111-111111111111"],
  },
  {
    name: "calls_clear_apply",
    args: [
      "calls",
      "clear",
      "--event",
      "11111111-1111-4111-8111-111111111111",
      "--confirm",
    ],
  },
  {
    name: "calls_clear_all_apply",
    args: ["calls", "clear", "--all", "--confirm"],
  },
  {
    name: "notes_process_apply",
    args: [
      "notes",
      "process",
      "33333333-3333-4333-8333-333333333333",
      "--confirm",
    ],
  },
  {
    name: "graph_width_apply",
    args: ["graph", "width", "--decision", "keep_existing", "--confirm"],
  },
  {
    name: "models_activate_apply",
    args: [
      "models",
      "activate",
      "44444444-4444-4444-8444-444444444444",
      "--confirm",
    ],
  },
  {
    name: "models_edit_apply",
    args: [
      "models",
      "edit",
      "44444444-4444-4444-8444-444444444444",
      "--config-file",
      "/nonexistent/patch.json",
      "--confirm",
    ],
  },
];

export function validateCatalog(document) {
  const failures = [];
  if (document?.schema_version !== 1) {
    failures.push("schema_version must be 1");
  }
  if (document?.product !== "memhub") failures.push("product must be memhub");
  const names = (document?.commands ?? []).map((command) => command.name);
  for (const name of REQUIRED_COMMAND_SURFACE) {
    if (!names.includes(name)) {
      failures.push(`required command is not declared: ${name}`);
    }
  }
  for (const [capability, targets] of Object.entries(REQUIRED_ALIASES)) {
    const actual = document?.capability_aliases?.[capability];
    if (JSON.stringify(actual) !== JSON.stringify(targets)) {
      failures.push(`${capability} capability alias mismatch`);
    }
    for (const target of targets) {
      if (!names.includes(target)) {
        failures.push(`${capability} maps to missing command ${target}`);
      }
    }
  }
  const byName = new Map(
    (document?.commands ?? []).map((command) => [command.name, command]),
  );
  for (const { command, marker } of REQUIRED_SUBCOMMANDS) {
    const declared = byName.get(command);
    if (!declared) continue; // already reported as a missing command above
    const usage = [declared.usage, declared.alternative_usage ?? ""].join("\n");
    if (!usage.includes(marker)) {
      failures.push(`command "${command}" is missing usage "${marker}"`);
    }
  }
  return failures;
}

async function run(executable, args, options = {}) {
  return new Promise((resolve) => {
    const child = spawn(executable, args, {
      ...options,
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
    child.on("error", (error) => {
      resolve({ code: null, stdout, stderr: `${stderr}${error.message}` });
    });
    child.on("close", (code) => resolve({ code, stdout, stderr }));
  });
}

export async function runPackageMatrix(runtimeDist, options = {}) {
  if (runtimeDist === undefined || runtimeDist.length === 0) {
    throw new Error("usage: verify-package-matrix.mjs <runtime-dist>");
  }
  const node = options.node ?? process.execPath;
  const cli = path.join(runtimeDist, "bin", "one-memory.cjs");
  await chmod(cli, 0o755);
  const bundle = await readFile(cli);
  const manifest = JSON.parse(
    await readFile(path.join(runtimeDist, "manifest.json"), "utf8"),
  );
  const expectedVersion = manifest.version;
  const bundleSha = createHash("sha256").update(bundle).digest("hex");
  const markers = REQUIRED_MARKERS.filter(
    (marker) => !bundle.toString("utf8").includes(marker),
  );
  const isolatedRoot = await mkdtemp(
    path.join(tmpdir(), "memhub-package-matrix-"),
  );
  const home = path.join(isolatedRoot, "home");
  const dataDir = path.join(isolatedRoot, "data");
  const hostHome = path.join(isolatedRoot, "host-home");
  await Promise.all([
    import("node:fs/promises").then(({ mkdir }) =>
      mkdir(home, { recursive: true, mode: 0o700 }),
    ),
    import("node:fs/promises").then(({ mkdir }) =>
      mkdir(dataDir, { recursive: true, mode: 0o700 }),
    ),
    import("node:fs/promises").then(({ mkdir }) =>
      mkdir(hostHome, { recursive: true, mode: 0o700 }),
    ),
  ]);
  const environment = {
    ...process.env,
    HOME: home,
    ONE_MEMORY_DATA_DIR: dataDir,
    ONE_MEMORY_HOST_HOME: hostHome,
  };

  const version = await run(node, [cli, "--version"], { env: environment });
  const help = await run(node, [cli, "help", "--json"], { env: environment });
  const status = await run(node, [cli, "status", "--json"], {
    env: environment,
  });
  const runtime = path.join(runtimeDist, "runtime", "runtime.cjs");
  const stoppedDestructive =
    options.stoppedCommands === true
      ? await runStoppedDestructive(node, cli, environment)
      : null;
  const lifecycle =
    options.lifecycle === true
      ? await runLifecycle(node, cli, runtime, environment)
      : null;

  const failures = [];
  if (markers.length > 0) {
    failures.push(`bundle missing contract markers: ${markers.join(", ")}`);
  }
  if (
    options.expectSha !== undefined &&
    bundleSha !== options.expectSha.toLowerCase()
  ) {
    failures.push(
      `bundle SHA ${bundleSha} does not match expected ${options.expectSha}`,
    );
  }
  if (
    version.code !== 0 ||
    version.stdout.trim() !== `memhub ${expectedVersion}`
  ) {
    failures.push(
      `--version did not return memhub ${expectedVersion} with exit 0`,
    );
  }
  let catalog;
  try {
    catalog = JSON.parse(help.stdout);
  } catch {
    failures.push("help --json did not emit a JSON document");
  }
  failures.push(...validateCatalog(catalog));
  if (status.code !== 3) {
    failures.push(`stopped status exit code ${status.code}, expected 3`);
  }
  let statusDocument;
  try {
    statusDocument = JSON.parse(status.stdout);
  } catch {
    failures.push("status --json did not emit a JSON document");
  }
  if (statusDocument?.runtime?.code !== "runtime_not_running") {
    failures.push("stopped status code is not runtime_not_running");
  }
  if (statusDocument?.data_dir !== dataDir) {
    failures.push("status data_dir does not match the isolated data directory");
  }
  if (status.stderr !== "") {
    failures.push("status --json wrote diagnostics to stderr without a TTY");
  }
  if (lifecycle !== null) {
    failures.push(...lifecycle.failures);
  }
  if (stoppedDestructive !== null) {
    failures.push(...stoppedDestructive.failures);
  }

  return {
    schema_version: 1,
    state: failures.length === 0 ? "verified" : "failed",
    runtime_dist: runtimeDist,
    bundle: {
      path: cli,
      sha256: bundleSha,
      bytes: bundle.length,
      missing_contract_markers: markers,
    },
    isolated_root: isolatedRoot,
    commands: {
      version: { exit: version.code, stdout: version.stdout.trim() },
      help_json: {
        exit: help.code,
        command_count: catalog?.commands?.length ?? null,
        capability_aliases: catalog?.capability_aliases ?? null,
      },
      stopped_status: {
        exit: status.code,
        runtime_code: statusDocument?.runtime?.code ?? null,
        data_dir: statusDocument?.data_dir ?? null,
      },
      lifecycle,
      stopped_destructive: stoppedDestructive,
    },
    failures,
  };
}

async function runStoppedDestructive(node, cli, environment) {
  const results = {};
  const failures = [];
  for (const probe of STOPPED_DESTRUCTIVE_PROBES) {
    const result = await run(node, [cli, ...probe.args, "--json"], {
      env: environment,
    });
    let document;
    try {
      document = JSON.parse(result.stdout);
    } catch {
      document = null;
    }
    results[probe.name] = {
      exit: result.code,
      state: document?.state ?? null,
      code: document?.code ?? null,
    };
    if (result.code !== 3) {
      failures.push(
        `${probe.name} exit ${result.code}, expected 3 while Runtime is stopped`,
      );
    }
    if (
      document?.state !== "unknown" ||
      document?.code !== "runtime_not_running"
    ) {
      failures.push(
        `${probe.name} did not fail closed with unknown/runtime_not_running`,
      );
    }
    if (result.stderr !== "") {
      failures.push(`${probe.name} wrote diagnostics to stderr without a TTY`);
    }
  }
  return { results, failures };
}

async function runLifecycle(node, cli, runtime, environment) {
  const failures = [];
  try {
    await access(runtime);
  } catch {
    return {
      started: { exit: null },
      restart: { exit: null },
      stop: { exit: null },
      residual_process: false,
      failures: [`packaged runtime entry not found: ${runtime}`],
    };
  }
  const server = spawn(node, [runtime, "serve"], {
    env: environment,
    stdio: ["ignore", "pipe", "pipe"],
  });
  let serverError = "";
  let serverStderr = "";
  server.on("error", (error) => {
    serverError = error.message;
  });
  server.stderr.on("data", (chunk) => {
    serverStderr += chunk;
  });

  let started;
  for (let attempt = 0; attempt < 20; attempt += 1) {
    if (serverError !== "") break;
    started = await run(node, [cli, "status", "--json"], {
      env: environment,
    });
    if (started.code === 0) break;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  if (started?.code !== 0) {
    failures.push(
      `packaged Runtime did not become ready: ${
        serverError || serverStderr.slice(-400) || "no status response"
      }`,
    );
  }

  const restarted = await run(node, [cli, "restart", "--json"], {
    env: environment,
  });
  if (restarted.code !== 0) {
    failures.push(`restart failed with exit ${restarted.code}`);
  }

  const stopped = await run(node, [cli, "stop", "--json"], {
    env: environment,
  });
  if (stopped.code !== 0) {
    failures.push(`stop failed with exit ${stopped.code}`);
  }
  await new Promise((resolve) => setTimeout(resolve, 1500));
  if (server.exitCode === null) {
    server.kill("SIGTERM");
    failures.push("stop left the packaged Runtime process alive");
  }

  return {
    started: { exit: started?.code ?? null },
    restart: { exit: restarted.code },
    stop: { exit: stopped.code },
    residual_process: server.exitCode === null,
    failures,
  };
}

async function main() {
  const [runtimeDist, ...rest] = process.argv.slice(2);
  if (runtimeDist === "--help" || runtimeDist === "-h") {
    process.stdout.write(
      [
        "Usage: verify-package-matrix.mjs <runtime-dist> [--expect-sha <sha256>] [--stopped-commands] [--lifecycle] [--json]",
        "",
        "Default is read-only. --lifecycle starts the packaged Runtime and",
        "checks status, restart, stop, and residual-process state.",
        "--stopped-commands runs every destructive UI-write command with no",
        "Runtime and asserts the structured unknown/runtime_not_running exit 3.",
        "",
      ].join("\n"),
    );
    return;
  }
  if (runtimeDist === undefined) {
    process.stderr.write(
      "usage: verify-package-matrix.mjs <runtime-dist> [--expect-sha <sha256>] [--lifecycle] [--json]\n",
    );
    process.exitCode = 2;
    return;
  }
  const asJson = rest.includes("--json");
  const expectShaIndex = rest.indexOf("--expect-sha");
  const expectSha = expectShaIndex >= 0 ? rest[expectShaIndex + 1] : undefined;
  if (expectSha !== undefined && !/^[0-9a-fA-F]{64}$/.test(expectSha)) {
    process.stderr.write("--expect-sha must be a 64-character SHA-256\n");
    process.exitCode = 2;
    return;
  }
  const result = await runPackageMatrix(runtimeDist, {
    lifecycle: rest.includes("--lifecycle"),
    stoppedCommands: rest.includes("--stopped-commands"),
    expectSha,
  });
  if (asJson) {
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
  } else if (result.state === "verified") {
    process.stdout.write("package CLI matrix verified\n");
  } else {
    process.stderr.write(`${result.failures.join("\n")}\n`);
  }
  process.exitCode = result.state === "verified" ? 0 : 1;
}

if (
  process.argv[1] !== undefined &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  main().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : error}\n`);
    process.exitCode = 1;
  });
}
