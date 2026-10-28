#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import {
  existsSync,
  mkdirSync,
  readFileSync,
  statSync,
  writeFileSync,
} from "node:fs";
import os from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";

function fail(message, receipt) {
  if (receipt) process.stdout.write(`${JSON.stringify(receipt, null, 2)}\n`);
  process.stderr.write(`${message}\n`);
  process.exit(1);
}

if (process.platform !== "linux") {
  fail("verify-linux-lifecycle.mjs must run on a Linux host");
}
if (!["x64", "arm64"].includes(process.arch)) {
  fail(`unsupported Linux architecture: ${process.arch}`);
}

const distribution = path.resolve(
  process.argv[2] ??
    path.join(process.cwd(), "dist-runtime-linux", process.arch),
);
if (!existsSync(path.join(distribution, "manifest.json"))) {
  fail("linux runtime artifact missing; run build-linux-runtime.mjs first");
}
const distributionManifest = JSON.parse(
  readFileSync(path.join(distribution, "manifest.json"), "utf8"),
);
if (
  typeof distributionManifest.version !== "string" ||
  !Number.isSafeInteger(distributionManifest.build_number)
) {
  fail("linux runtime manifest identity is invalid");
}

const verifyRoot = path.resolve(
  process.env.ONE_MEMORY_LINUX_VERIFY_ROOT ??
    path.join(os.tmpdir(), `one-memory-linux-lifecycle-${Date.now()}`),
);
const home = path.join(verifyRoot, "home");
const dataDir = path.join(verifyRoot, "data with spaces");
const installRoot = path.join(verifyRoot, "install");
const binDir = path.join(verifyRoot, "bin");
const evidenceDir = path.join(verifyRoot, "evidence");
const sourceDir = path.join(
  home,
  ".claude",
  "projects",
  "-tmp-one-memory-linux",
);
for (const directory of [home, dataDir, binDir, evidenceDir, sourceDir]) {
  mkdirSync(directory, { recursive: true });
}

const corePort = Number(process.env.ONE_MEMORY_CORE_PORT ?? 28785);
const runtimePort = Number(process.env.ONE_MEMORY_RUNTIME_PORT ?? 28786);
if (
  home === path.resolve(os.homedir()) ||
  [corePort, runtimePort].some((port) => port === 8765 || port === 18765)
) {
  fail(
    "Linux lifecycle verification requires an isolated HOME and non-default ports",
  );
}

const env = {
  ...process.env,
  HOME: home,
  XDG_CONFIG_HOME: path.join(home, ".config"),
  XDG_DATA_HOME: path.join(home, ".local", "share"),
  ONE_MEMORY_DATA_DIR: dataDir,
  ONE_MEMORY_CORE_PORT: String(corePort),
  ONE_MEMORY_RUNTIME_PORT: String(runtimePort),
  ONE_MEMORY_PORT: String(corePort),
  ONE_MEMORY_TEST_ISOLATED: "1",
  NO_COLOR: "1",
};
let cli = path.join(distribution, "bin", "one-memory");
const serviceId = "one-memory.service";
const status = {};
const observations = {};

function run(name, args, accepted = [0], timeout = 120_000) {
  const result = spawnSync(cli, args, { env, encoding: "utf8", timeout });
  writeFileSync(
    path.join(evidenceDir, `${name}.json`),
    `${JSON.stringify({ args, exit_code: result.status, signal: result.signal, stdout: result.stdout, stderr: result.stderr, error: result.error?.message ?? null }, null, 2)}\n`,
  );
  if (!accepted.includes(result.status)) {
    throw new Error(
      `${name} failed with exit ${result.status}: ${result.stderr || result.stdout}`,
    );
  }
  return result;
}

function systemctl(name, args, accepted = [0]) {
  const result = spawnSync("systemctl", ["--user", ...args], {
    env,
    encoding: "utf8",
    timeout: 30_000,
  });
  writeFileSync(
    path.join(evidenceDir, `${name}.json`),
    `${JSON.stringify({ args: ["--user", ...args], exit_code: result.status, stdout: result.stdout, stderr: result.stderr, error: result.error?.message ?? null }, null, 2)}\n`,
  );
  if (!accepted.includes(result.status)) {
    throw new Error(
      `${name} failed with exit ${result.status}: ${result.stderr || result.stdout}`,
    );
  }
  return result;
}

function parseJson(result, name) {
  try {
    return JSON.parse(result.stdout);
  } catch {
    throw new Error(`${name} did not return JSON`);
  }
}

function findKey(value, key) {
  if (!value || typeof value !== "object") return null;
  if (Object.hasOwn(value, key)) return value[key];
  for (const nested of Object.values(value)) {
    const found = findKey(nested, key);
    if (found !== null && found !== undefined) return found;
  }
  return null;
}

function doctorCheck(document, id) {
  const check = document?.checks?.find?.((candidate) => candidate?.id === id);
  if (!check || typeof check !== "object") {
    throw new Error(`doctor did not report ${id}`);
  }
  return check;
}

/**
 * Whether an imported phrase reached Core's durable write queue.
 *
 * Recall cannot prove an import here: this runner has no generation model,
 * and with SDK authority nothing reaches the SDK (not even its raw layer)
 * until a model is active. What the lifecycle can prove is that the import
 * was read and queued for the SDK, durably, in the Runtime's own database.
 */
async function waitForQueued(phrase) {
  const deadline = Date.now() + 90_000;
  const database = path.join(dataDir, "one-memory.sqlite3");
  while (Date.now() < deadline) {
    if (existsSync(database)) {
      let connection;
      try {
        connection = new DatabaseSync(database, { readOnly: true });
        const row = connection
          .prepare(
            "SELECT COUNT(*) AS count FROM memory_write_jobs " +
              "WHERE instr(payload_json, ?) > 0",
          )
          .get(phrase);
        if (Number(row?.count) > 0) return true;
      } catch {
        // The queue table appears once Core has migrated its schema.
      } finally {
        connection?.close();
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 2_000));
  }
  return false;
}

async function waitForRunningStatus(prefix, previousInstanceId = null) {
  const deadline = Date.now() + 30_000;
  let attempt = 0;
  while (Date.now() < deadline) {
    attempt += 1;
    const result = run(
      `${prefix}-${attempt}`,
      ["status", "--json"],
      // Exit 1 is a valid transient receipt: systemd has started the service,
      // but Runtime/Core/Broker identity is not ready yet. Keep polling until
      // status reaches 0 instead of aborting the lifecycle on a cold start.
      [0, 1, 3],
      10_000,
    );
    if (result.status === 0) {
      const statusReceipt = parseJson(result, prefix);
      const instanceId = findKey(statusReceipt, "instance_id");
      if (
        typeof instanceId === "string" &&
        instanceId &&
        (previousInstanceId === null || instanceId !== previousInstanceId)
      ) {
        return statusReceipt;
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`${prefix} did not reach a new running Runtime`);
}

const conversationFile = path.join(
  sourceDir,
  "11111111-2222-3333-4444-555555555555.jsonl",
);
function row(type, uuid, content, minute) {
  return JSON.stringify({
    type,
    sessionId: "11111111-2222-3333-4444-555555555555",
    uuid,
    timestamp: `2026-10-04T10:${minute}:00+08:00`,
    cwd: "/tmp/one-memory-linux",
    message: {
      role: type,
      content:
        type === "assistant" ? [{ type: "text", text: content }] : content,
    },
  });
}
const initialRows = `${row("user", "u1", "Linux lifecycle initial phrase", "01")}\n${row("assistant", "a1", "Initial phrase received", "02")}\n`;
writeFileSync(conversationFile, initialRows);

const receipt = {
  schema_version: 1,
  product: "one-memory-linux-lifecycle",
  platform: "linux",
  arch: process.arch,
  distribution,
  verify_root: verifyRoot,
  capabilities: { systemd_user: "unverified" },
  status,
  observations,
};

try {
  systemctl("systemd-probe", ["show-environment"]);
  receipt.capabilities.systemd_user = "available";
  systemctl("preflight-disabled", ["is-enabled", serviceId], [1, 4]);
  systemctl("preflight-inactive", ["is-active", serviceId], [3, 4]);
  const installReceipt = parseJson(
    run("install", [
      "install",
      "--source",
      distribution,
      "--install-root",
      installRoot,
      "--home",
      home,
      "--bin-dir",
      binDir,
      "--data-dir",
      dataDir,
      "--xdg-config-home",
      env.XDG_CONFIG_HOME,
      "--no-shell-path",
      "--yes",
      "--json",
    ]),
    "install",
  );
  const installedCorePort = findKey(installReceipt, "core_port");
  if (
    !Number.isSafeInteger(installedCorePort) ||
    installedCorePort < 1024 ||
    installedCorePort > 65535 ||
    installedCorePort === 8765 ||
    installedCorePort === 18765
  ) {
    throw new Error("install did not select a safe non-default Core port");
  }
  env.ONE_MEMORY_CORE_PORT = String(installedCorePort);
  env.ONE_MEMORY_PORT = String(installedCorePort);
  observations.installed_core_port = installedCorePort;
  status.install = "passed";
  cli = path.join(binDir, "memhub");
  systemctl("installed-enabled", ["is-enabled", serviceId]);
  systemctl("installed-active", ["is-active", serviceId]);

  systemctl("stop-before-start", ["stop", serviceId]);
  systemctl("stopped-inactive", ["is-active", serviceId], [3]);
  run("status-stopped", ["status", "--json"], [3]);
  systemctl("start", ["start", serviceId]);
  systemctl("started-active", ["is-active", serviceId]);
  status.start = "passed";
  const before = await waitForRunningStatus("status-before-restart");
  status.status = "passed";
  const doctor = parseJson(
    run("doctor", ["doctor", "--json"], [0, 1]),
    "doctor",
  );
  const coreCheck = doctorCheck(doctor, "core");
  const brokerCheck = doctorCheck(doctor, "broker");
  if (
    coreCheck.state !== "pass" ||
    coreCheck.code !== "runtime_verified" ||
    coreCheck.details?.instance_verified !== true ||
    coreCheck.details?.version !== distributionManifest.version
  ) {
    throw new Error("doctor did not verify the packaged Core identity");
  }
  if (
    brokerCheck.state !== "pass" ||
    brokerCheck.code !== "background_verified"
  ) {
    throw new Error("doctor did not verify the background broker");
  }
  observations.doctor_state = doctor.state;
  observations.doctor_core_version = coreCheck.details.version;
  status.doctor = "passed";
  const beforeId = findKey(before, "instance_id");
  systemctl("restart", ["restart", serviceId]);
  systemctl("restarted-active", ["is-active", serviceId]);
  const after = await waitForRunningStatus("status-after-restart", beforeId);
  const afterId = findKey(after, "instance_id");
  observations.instance_id_changed = Boolean(
    beforeId && afterId && beforeId !== afterId,
  );
  if (!observations.instance_id_changed)
    throw new Error("restart did not change instance_id");
  status.restart = "passed";

  run("initial-import", [
    "init",
    "--source",
    "claude-code",
    "--import",
    "--json",
  ]);
  observations.initial_queued = await waitForQueued(
    "Linux lifecycle initial phrase",
  );
  if (!observations.initial_queued)
    throw new Error("initial import was not queued for the SDK");
  status.import = "passed";

  writeFileSync(
    conversationFile,
    `${initialRows}${row("user", "u2", "Linux lifecycle appended phrase", "03")}\n${row("assistant", "a2", "Appended phrase received", "04")}\n`,
  );
  run(
    "append-scan",
    ["scan", "--source", "claude-code", "--yes", "--json"],
    [0],
    30_000,
  );
  observations.append_queued = await waitForQueued(
    "Linux lifecycle appended phrase",
  );
  if (!observations.append_queued)
    throw new Error("appended turn was not queued for the SDK");
  status.append_sync = "passed";

  const beforeUpgrade = await waitForRunningStatus("status-before-upgrade");
  const beforeUpgradeId = findKey(beforeUpgrade, "instance_id");
  run("upgrade-idempotent", [
    "upgrade",
    "--source",
    distribution,
    "--install-root",
    installRoot,
    "--home",
    home,
    "--bin-dir",
    binDir,
    "--data-dir",
    dataDir,
    "--xdg-config-home",
    env.XDG_CONFIG_HOME,
    "--no-shell-path",
    "--yes",
    "--json",
  ]);
  systemctl("upgraded-active", ["is-active", serviceId]);
  const afterUpgrade = await waitForRunningStatus(
    "status-after-upgrade",
    beforeUpgradeId,
  );
  const afterUpgradeId = findKey(afterUpgrade, "instance_id");
  observations.idempotent_upgrade_instance_id_changed = Boolean(
    beforeUpgradeId && afterUpgradeId && beforeUpgradeId !== afterUpgradeId,
  );
  if (!observations.idempotent_upgrade_instance_id_changed) {
    throw new Error("idempotent upgrade did not restart the Runtime");
  }
  observations.post_upgrade_queued = await waitForQueued(
    "Linux lifecycle appended phrase",
  );
  if (!observations.post_upgrade_queued) {
    throw new Error("idempotent upgrade lost previously queued data");
  }
  status.upgrade_idempotent = "passed";

  systemctl("stop", ["stop", serviceId]);
  systemctl("stopped-final", ["is-active", serviceId], [3]);
  run("status-after-stop", ["status", "--json"], [3]);
  status.stop = "passed";
  const databasePath = path.join(dataDir, "one-memory.sqlite3");
  if (!existsSync(databasePath))
    throw new Error("data database missing before uninstall");
  const sizeBefore = statSync(databasePath).size;
  run("uninstall", [
    "uninstall",
    "--source",
    distribution,
    "--install-root",
    installRoot,
    "--home",
    home,
    "--bin-dir",
    binDir,
    "--data-dir",
    dataDir,
    "--xdg-config-home",
    env.XDG_CONFIG_HOME,
    "--no-shell-path",
    "--yes",
    "--json",
  ]);
  systemctl("uninstalled-disabled", ["is-enabled", serviceId], [1, 4]);
  systemctl("uninstalled-inactive", ["is-active", serviceId], [3, 4]);
  observations.uninstall_preserved_data =
    existsSync(databasePath) && statSync(databasePath).size === sizeBefore;
  if (!observations.uninstall_preserved_data)
    throw new Error("uninstall changed or removed data");
  status.uninstall_preserve_data = "passed";
  receipt.result = "passed";
} catch (error) {
  receipt.result = "failed";
  receipt.error = error instanceof Error ? error.message : String(error);
  try {
    systemctl("cleanup-stop", ["stop", serviceId], [0, 5]);
  } catch {}
  try {
    cli = path.join(distribution, "bin", "one-memory");
    run("cleanup-uninstall", [
      "uninstall",
      "--source",
      distribution,
      "--install-root",
      installRoot,
      "--home",
      home,
      "--bin-dir",
      binDir,
      "--data-dir",
      dataDir,
      "--xdg-config-home",
      env.XDG_CONFIG_HOME,
      "--no-shell-path",
      "--yes",
      "--json",
    ]);
  } catch {}
  writeFileSync(
    path.join(evidenceDir, "linux-lifecycle-receipt.json"),
    `${JSON.stringify(receipt, null, 2)}\n`,
  );
  fail(receipt.error, receipt);
}

writeFileSync(
  path.join(evidenceDir, "linux-lifecycle-receipt.json"),
  `${JSON.stringify(receipt, null, 2)}\n`,
);
process.stdout.write(`${JSON.stringify(receipt, null, 2)}\n`);
