import type { ChildProcess, SpawnOptions } from "node:child_process";
import { spawn } from "node:child_process";
import { access, readFile } from "node:fs/promises";
import path from "node:path";
import type { HealthResponse } from "@one-memory/contracts";
import axios from "axios";
import { processIsAlive } from "./core-supervisor.js";
import {
  discoverRuntimeAttachment,
  type RuntimeAttachment,
  RuntimeDiscoveryError,
} from "./discovery.js";
import { readSdkMigrationBlock } from "./sdk-migration-log.js";

export interface RuntimeLaunch {
  command: string;
  args: string[];
  cwd?: string;
  env?: NodeJS.ProcessEnv;
}

export interface EnsureRuntimeOptions {
  dataDir: string;
  launch: RuntimeLaunch;
  attempts?: number;
  delayMs?: number;
  spawnProcess?: (
    command: string,
    args: readonly string[],
    options: SpawnOptions,
  ) => ChildProcess;
  processIsAlive?: (pid: number) => boolean;
  probeIdentity?: (port: number, apiToken: string) => Promise<HealthResponse>;
}

export interface StopRuntimeOptions {
  dataDir: string;
  timeoutMs?: number;
  processIsAlive?: (pid: number) => boolean;
  signalProcess?: (pid: number, signal: NodeJS.Signals) => void;
  probeIdentity?: (port: number, apiToken: string) => Promise<HealthResponse>;
}

export interface WaitForRuntimeOptions {
  dataDir: string;
  attempts?: number;
  delayMs?: number;
  processIsAlive?: (pid: number) => boolean;
  probeIdentity?: (port: number, apiToken: string) => Promise<HealthResponse>;
}

export async function ensureRuntimeProcess(
  options: EnsureRuntimeOptions,
): Promise<"attached" | "started"> {
  const existing = await discover(options);
  if (existing) return "attached";

  const spawnProcess = options.spawnProcess ?? spawn;
  const child = spawnProcess(options.launch.command, options.launch.args, {
    cwd: options.launch.cwd,
    env: { ...process.env, ...options.launch.env },
    detached: true,
    stdio: "ignore",
    windowsHide: true,
  });
  const childState: {
    exit: { code: number | null; signal: NodeJS.Signals | null } | null;
    error: Error | null;
  } = { exit: null, error: null };
  child.once("exit", (code, signal) => {
    childState.exit = { code, signal };
  });
  child.once("error", (error) => {
    childState.error = error;
  });
  child.unref();

  const attempts = options.attempts ?? 120;
  const delayMs = options.delayMs ?? 250;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      if (await discover(options)) return "started";
    } catch (error) {
      // Our Runtime writes its service receipt after its Core's receipt, so a
      // receipt left by the previous Runtime is expected until it catches up.
      if (
        !(error instanceof RuntimeDiscoveryError) ||
        (error.code !== "runtime_service_not_ready" &&
          error.code !== "runtime_service_receipt_invalid")
      ) {
        throw error;
      }
    }
    const spawnError = childState.error;
    if (spawnError) {
      throw new Error(`MemHub Runtime could not start: ${spawnError.message}`, {
        cause: spawnError,
      });
    }
    const exit = childState.exit;
    if (exit) {
      throw new Error(
        `MemHub Runtime exited before becoming ready (${exit.code ?? exit.signal ?? "unknown"})`,
      );
    }
    await delay(delayMs);
  }
  throw new Error("Timed out waiting for MemHub Runtime");
}

export async function waitForRuntimeProcess(
  options: WaitForRuntimeOptions,
): Promise<RuntimeAttachment> {
  const attempts = options.attempts ?? 120;
  const delayMs = options.delayMs ?? 250;
  const waitStarted = Date.now();
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      const attachment = await discoverRuntimeAttachment({
        dataDir: options.dataDir,
        processIsAlive: options.processIsAlive ?? processIsAlive,
        probeIdentity: options.probeIdentity ?? defaultProbeIdentity,
        requireServiceReceipt: true,
      });
      if (attachment !== null) return attachment;
    } catch (error) {
      if (
        !(error instanceof RuntimeDiscoveryError) ||
        (error.code !== "runtime_service_not_ready" &&
          error.code !== "runtime_service_receipt_invalid" &&
          error.code !== "runtime_identity_unavailable")
      ) {
        throw error;
      }
    }
    // The service refused to spawn Core because its migration was deferred
    // during this wait: it will never become ready, so say why now instead of
    // after the whole budget. An older note may predate a retry that is still
    // running, so it only counts once the wait has timed out.
    const refused = await readSdkMigrationBlock(options.dataDir);
    if (refused !== null && Date.parse(refused.at) >= waitStarted) {
      throw new Error(refused.code);
    }
    await delay(delayMs);
  }
  const blocked = await readSdkMigrationBlock(options.dataDir);
  if (blocked !== null) throw new Error(blocked.code);
  throw new Error("Timed out waiting for managed MemHub Runtime");
}

export async function stopRuntimeProcess(
  options: StopRuntimeOptions,
): Promise<"stopped" | "not-running"> {
  const alive = options.processIsAlive ?? processIsAlive;
  const attachment = await discoverRuntimeAttachment({
    dataDir: options.dataDir,
    processIsAlive: alive,
    probeIdentity: options.probeIdentity ?? defaultProbeIdentity,
    requireServiceReceipt: true,
  });
  if (attachment === null) return "not-running";
  if (attachment.servicePid === null) {
    throw new RuntimeDiscoveryError("runtime_service_receipt_invalid");
  }
  await terminateVerifiedRuntime(options, attachment.servicePid);
  return "stopped";
}

export interface StopAllRuntimesOptions extends StopRuntimeOptions {
  /** How long a Runtime caught starting or stopping may take to become
   * verifiable before the stop gives up on it. */
  settleMs?: number;
  /** Upper bound on Runtimes stopped in one call. The lease admits one at a
   * time, so more than a few means something keeps starting new ones. */
  maxRuntimes?: number;
}

export interface StopAllRuntimesResult {
  /** Verified Runtime processes this call stopped. */
  stopped: number[];
  /** A live pid the Runtime lease names that could not be verified as a
   * MemHub Runtime of this data dir. Never signalled: a recycled pid belongs
   * to someone else. */
  unverified: number[];
}

/** Stop every Runtime serving `dataDir`, whoever started it.
 *
 * The Runtime lease (`runtime-service.lock`) admits one Runtime per data dir,
 * so "every" is the current leaseholder plus any successor that takes the
 * lease while this runs: the App's own transient child, a `memhub start`, a
 * leftover from an earlier session. Each is found through its receipt and
 * signalled only once the authenticated identity endpoint has proved that the
 * receipt's pid is the Runtime serving this data dir. A live lease owner that
 * cannot be proved that way is reported, never signalled.
 */
export async function stopAllRuntimeProcesses(
  options: StopAllRuntimesOptions,
): Promise<StopAllRuntimesResult> {
  const alive = options.processIsAlive ?? processIsAlive;
  const stopped: number[] = [];
  const settleDeadline = Date.now() + (options.settleMs ?? 2_000);
  const maxRuntimes = options.maxRuntimes ?? 3;
  while (stopped.length < maxRuntimes) {
    let attachment: RuntimeAttachment | null;
    try {
      attachment = await discoverRuntimeAttachment({
        dataDir: options.dataDir,
        processIsAlive: alive,
        probeIdentity: options.probeIdentity ?? defaultProbeIdentity,
        requireServiceReceipt: true,
      });
    } catch (error) {
      if (settling(error) && Date.now() < settleDeadline) {
        await delay(100);
        continue;
      }
      throw error;
    }
    if (attachment === null) break;
    if (attachment.servicePid === null) {
      throw new RuntimeDiscoveryError("runtime_service_receipt_invalid");
    }
    await terminateVerifiedRuntime(options, attachment.servicePid);
    stopped.push(attachment.servicePid);
  }
  const leasePid = await readRuntimeLeaseOwnerPid(options.dataDir);
  const unverified =
    leasePid !== null &&
    leasePid !== process.pid &&
    !stopped.includes(leasePid) &&
    alive(leasePid)
      ? [leasePid]
      : [];
  return { stopped, unverified };
}

/** The pid the Runtime lease names, or null without a readable lease. */
export async function readRuntimeLeaseOwnerPid(
  dataDir: string,
): Promise<number | null> {
  try {
    const owner = JSON.parse(
      await readFile(
        path.join(dataDir, "runtime-service.lock", "owner.json"),
        "utf8",
      ),
    ) as { pid?: unknown };
    return typeof owner.pid === "number" &&
      Number.isInteger(owner.pid) &&
      owner.pid > 1
      ? owner.pid
      : null;
  } catch {
    return null;
  }
}

/** A Runtime between states: starting, stopping, or swapping receipts. */
function settling(error: unknown): boolean {
  return (
    error instanceof RuntimeDiscoveryError &&
    (error.code === "runtime_service_not_ready" ||
      error.code === "runtime_service_receipt_invalid" ||
      error.code === "runtime_identity_unavailable")
  );
}

async function terminateVerifiedRuntime(
  options: StopRuntimeOptions,
  pid: number,
): Promise<void> {
  const alive = options.processIsAlive ?? processIsAlive;
  const signal = options.signalProcess ?? process.kill;
  signal(pid, "SIGTERM");
  const timeoutMs = options.timeoutMs ?? 15_000;
  const deadline = Date.now() + timeoutMs;
  // The process exiting is the authoritative stop signal. The receipt files
  // are cleanup artifacts: a Runtime that is killed hard can leave one behind,
  // and requiring all three to disappear reported a real stop as a timeout.
  const receiptGraceMs = Math.min(RECEIPT_CLEANUP_GRACE_MS, timeoutMs);
  let exitedAt: number | null = null;
  while (Date.now() < deadline) {
    if (!alive(pid)) {
      exitedAt ??= Date.now();
      if (await receiptsAreGone(options.dataDir)) return;
      if (Date.now() - exitedAt >= receiptGraceMs) return;
    } else {
      exitedAt = null;
    }
    await delay(100);
  }
  throw new Error("Timed out waiting for MemHub Runtime to stop");
}

/** How long a stopped process may take to clean up its own receipts. */
const RECEIPT_CLEANUP_GRACE_MS = 500;

async function receiptsAreGone(dataDir: string): Promise<boolean> {
  return (
    !(await exists(path.join(dataDir, "runtime-service.json"))) &&
    !(await exists(path.join(dataDir, "runtime.json"))) &&
    !(await exists(path.join(dataDir, "runtime-token")))
  );
}

async function exists(target: string): Promise<boolean> {
  try {
    await access(target);
    return true;
  } catch {
    return false;
  }
}

async function discover(options: EnsureRuntimeOptions) {
  return discoverRuntimeAttachment({
    dataDir: options.dataDir,
    processIsAlive: options.processIsAlive ?? processIsAlive,
    probeIdentity: options.probeIdentity ?? defaultProbeIdentity,
    requireServiceReceipt: true,
  });
}

async function defaultProbeIdentity(
  port: number,
  apiToken: string,
): Promise<HealthResponse> {
  return (
    await axios.get<HealthResponse>(
      `http://127.0.0.1:${port}/api/v1/runtime/identity`,
      {
        headers: { Authorization: `Bearer ${apiToken}` },
        timeout: 500,
        proxy: false,
        maxRedirects: 0,
      },
    )
  ).data;
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}
