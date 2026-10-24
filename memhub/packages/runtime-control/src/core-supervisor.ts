import type { ChildProcess, SpawnOptions } from "node:child_process";
import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { createServer } from "node:net";
import path from "node:path";
import type { HealthResponse } from "@one-memory/contracts";
import axios from "axios";
import type { DiagnosticSink } from "./diagnostic-log.js";
import {
  discoverRuntimeAttachment,
  RuntimeDiscoveryError,
} from "./discovery.js";

export interface CoreLaunch {
  command: string;
  args: string[];
  cwd?: string;
}

export interface CoreSupervisorOptions {
  launch: CoreLaunch;
  dataDir: string;
  port?: number;
  /** When true, a read-only Reader child is started after Core becomes healthy.
   *  The Reader gets a dynamic port which is published in the Runtime receipt
   *  as `reader_port`. Default: false (no Reader). */
  readerEnabled?: boolean;
  readerPort?: number | null;
  instanceId?: string;
  apiToken?: string;
  webDirectory?: string;
  attempts?: number;
  delayMs?: number;
  shutdownTimeoutMs?: number;
  spawnProcess?: (
    command: string,
    args: readonly string[],
    options: SpawnOptions,
  ) => ChildProcess;
  probeHealth?: (port: number, apiToken?: string) => Promise<HealthResponse>;
  diagnostics?: DiagnosticSink;
  parentSessionId?: string;
  /** Receives the Core's stdout/stderr. The Runtime captures these into its own
   *  bounded rotating logs instead of letting launchd hold the file, because a
   *  launchd-held descriptor cannot be rotated. */
  onOutput?: (chunk: string, stream: "stdout" | "stderr") => void;
  /** True package/build identity of this runtime, forwarded to Core so the
   *  kernel half of the diagnostic chain carries the same ids as the ui and
   *  runtime halves. Absent fields are not injected. */
  buildIdentity?: {
    version?: string;
    buildId?: string;
    packageId?: string;
    round?: string;
    buildNumber?: string;
    commit?: string;
  };
  /** Bounded, throttled relaunch of an owned Core that exits after startup.
   * Zero disables it (used by tests that drive startup failures). The contract
   * requires bounded recovery with throttling, not an extra daemon. */
  restartAttempts?: number;
  restartBaseDelayMs?: number;
  restartMaxDelayMs?: number;
  /** Maximum total wait for Core readiness. `attempts * delayMs` is the
   *  existing per-attempt contract; the default is bounded but long enough for
   *  launchd cold starts measured at 11-20s under normal system load. */
  readinessBudgetMs?: number;
}

export const CORE_READINESS_DEFAULTS = Object.freeze({
  attempts: 300,
  delayMs: 200,
  readinessBudgetMs: 60_000,
});

/** Map package/build identity onto the env names the Core records. Only the
 *  fields that are actually known are emitted, so a source run without a build
 *  identity does not fabricate one. */
function identityEnv(
  identity: CoreSupervisorOptions["buildIdentity"],
): Record<string, string> {
  if (!identity) return {};
  const env: Record<string, string> = {};
  if (identity.version) env.ONE_MEMORY_VERSION = identity.version;
  if (identity.buildId) env.ONE_MEMORY_BUILD_ID = identity.buildId;
  if (identity.packageId) env.ONE_MEMORY_PACKAGE_ID = identity.packageId;
  if (identity.round) env.ONE_MEMORY_ROUND = identity.round;
  if (identity.buildNumber) env.ONE_MEMORY_BUILD_NUMBER = identity.buildNumber;
  if (identity.commit) env.ONE_MEMORY_COMMIT = identity.commit;
  return env;
}

export function isOwnedHealth(
  health: HealthResponse,
  expectedInstanceId: string,
): boolean {
  return (
    health.status === "ok" &&
    health.service === "one-memory-core" &&
    health.instance_id === expectedInstanceId
  );
}

async function defaultProbeHealth(
  port: number,
  apiToken?: string,
): Promise<HealthResponse> {
  const response = await axios.get<HealthResponse>(
    `http://127.0.0.1:${port}${apiToken ? "/api/v1/runtime/identity" : "/api/v1/health"}`,
    {
      timeout: 500,
      ...(apiToken ? { headers: { Authorization: `Bearer ${apiToken}` } } : {}),
    },
  );
  return response.data;
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function describeExit(
  code: number | null,
  signal: NodeJS.Signals | null,
): string {
  if (code !== null) return `exit code ${code}`;
  if (signal !== null) return `signal ${signal}`;
  return "exit status unavailable";
}

/** What a running Core writes about itself next to the lock it holds. */
interface CoreReceipt {
  pid: number;
  parent_pid: number | null;
  started_at: string;
}

async function readCoreReceipt(dataDir: string): Promise<CoreReceipt | null> {
  let raw: string;
  try {
    raw = await readFile(path.join(dataDir, "runtime.json"), "utf8");
  } catch {
    return null;
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof parsed !== "object" || parsed === null) return null;
  const record = parsed as Record<string, unknown>;
  if (typeof record.pid !== "number") return null;
  return {
    pid: record.pid,
    parent_pid:
      typeof record.parent_pid === "number" ? record.parent_pid : null,
    started_at:
      typeof record.started_at === "string" ? record.started_at : "未知时间",
  };
}

export function processIsAlive(pid: number): boolean {
  if (!Number.isInteger(pid) || pid <= 1) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    // A live process we are not allowed to signal still answers the question.
    return (error as NodeJS.ErrnoException).code === "EPERM";
  }
}

async function terminateProcess(pid: number, timeoutMs: number): Promise<void> {
  try {
    process.kill(pid, "SIGTERM");
  } catch {
    return;
  }
  // Waited for rather than assumed: the lock is released as the old Core
  // unwinds, so starting again too early would fail the same way.
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (!processIsAlive(pid)) return;
    await delay(100);
  }
  try {
    process.kill(pid, "SIGKILL");
  } catch {
    return;
  }
  const hardDeadline = Date.now() + 1_000;
  while (Date.now() < hardDeadline && processIsAlive(pid)) {
    await delay(100);
  }
}

export async function findAvailablePort(host = "127.0.0.1"): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, host, () => {
      const address = server.address();
      if (!address || typeof address === "string") {
        server.close();
        reject(new Error("Could not allocate a local Core port"));
        return;
      }
      const { port } = address;
      server.close((error) => {
        if (error) reject(error);
        else resolve(port);
      });
    });
  });
}

export class CoreSupervisor {
  private readonly ownedInstanceId: string;
  private readonly ownedApiToken: string;
  private readonly ownedPort: number;
  private activeInstanceId: string;
  private activeApiToken: string;
  private activePort: number;
  private readonly options: Required<
    Pick<
      CoreSupervisorOptions,
      | "attempts"
      | "delayMs"
      | "port"
      | "shutdownTimeoutMs"
      | "restartAttempts"
      | "restartBaseDelayMs"
      | "restartMaxDelayMs"
      | "readinessBudgetMs"
    >
  > &
    CoreSupervisorOptions;
  private child: ChildProcess | null = null;
  private attached = false;
  private exited = false;
  private spawnError: Error | null = null;
  private exitCode: number | null = null;
  private exitSignal: NodeJS.Signals | null = null;
  private stderrTail = "";
  private startFailureLogged = false;
  private exitPromise: Promise<void> | null = null;
  private coreSessionId: string | null = null;
  private ownedChild = false;
  private startedOk = false;
  private stopping = false;
  private restartPromise: Promise<void> | null = null;
  // Backoff has to survive a finished restart round. Every crash used to start
  // a fresh round at attempt 1 with the base delay, because `restartPromise` is
  // cleared in `finally`: measured on the 3924c loop, all 780
  // `core.restart_scheduled` events read `{attempt: 1, delay_ms: 1000}` across
  // 2h23m, about one relaunch every 11s. Each of those relaunched the Core, and
  // the crash stack is the kuzu teardown, so the loop handed out one more heap
  // corruption chance every 11 seconds and turned an occasional crash into a
  // near-certain one.
  private restartStreak = 0;
  private restartCircuitOpen = false;
  private lastProbeError: string | null = null;
  private readerChild: ChildProcess | null = null;
  private activeReaderPort: number | null = null;

  constructor(options: CoreSupervisorOptions) {
    this.ownedInstanceId = options.instanceId ?? randomUUID();
    this.ownedApiToken = options.apiToken ?? randomUUID();
    this.options = {
      ...options,
      // The readiness loop is bounded by both `attempts * delayMs` and the
      // wall-clock budget. Keep the attempt ceiling large enough that the
      // default 30s budget is actually reachable on a slow launchd cold start.
      attempts: options.attempts ?? CORE_READINESS_DEFAULTS.attempts,
      delayMs: options.delayMs ?? CORE_READINESS_DEFAULTS.delayMs,
      readinessBudgetMs:
        options.readinessBudgetMs ?? CORE_READINESS_DEFAULTS.readinessBudgetMs,
      port: options.port ?? 8765,
      shutdownTimeoutMs: options.shutdownTimeoutMs ?? 5_000,
      restartAttempts: options.restartAttempts ?? 5,
      restartBaseDelayMs: options.restartBaseDelayMs ?? 1_000,
      restartMaxDelayMs: options.restartMaxDelayMs ?? 30_000,
    };
    this.ownedPort = this.options.port;
    this.activeInstanceId = this.ownedInstanceId;
    this.activeApiToken = this.ownedApiToken;
    this.activePort = this.ownedPort;
  }

  get instanceId(): string {
    return this.activeInstanceId;
  }

  get apiToken(): string {
    return this.activeApiToken;
  }

  get port(): number {
    return this.activePort;
  }

  get readerPort(): number | null {
    return this.activeReaderPort;
  }

  async start(): Promise<HealthResponse> {
    if (this.child || this.attached) {
      throw new Error("MemHub Core supervisor already started");
    }
    this.stopping = false;
    // A deliberate start is the recovery path from an open circuit: it is a
    // fresh intent (new session, product restart), so the crash streak and the
    // circuit are cleared. Leaving either set would mean a Core that can never
    // come back without restarting the whole app.
    this.restartStreak = 0;
    this.restartCircuitOpen = false;
    let attached: HealthResponse | null;
    try {
      attached = await this.attachExisting();
    } catch (error) {
      await this.logStartFailure(error);
      throw error;
    }
    if (attached !== null) {
      await this.options.diagnostics?.log({
        level: "info",
        component: "runtime.core",
        event: "core.attached",
        outcome: "succeeded",
        instance_id: this.activeInstanceId,
        details: { spawned: false },
      });
      if (this.options.readerEnabled) {
        await this.startReader();
      }
      return attached;
    }
    this.useOwnedIdentity();
    try {
      const health = await this.attempt();
      if (this.options.readerEnabled) {
        await this.startReader();
      }
      return health;
    } catch (error) {
      // A standalone Runtime may have won the race after our initial
      // discovery read but before our child acquired the data lock. Verify it
      // now instead of presenting a false conflict or starting another writer.
      let raced: HealthResponse | null = null;
      try {
        raced = await this.attachExisting();
        if (raced !== null) {
          if (this.options.readerEnabled) {
            await this.startReader();
          }
          return raced;
        }
      } catch (attachError) {
        // A stale receipt can fail identity validation after the owned Core
        // exited. Treat that as a cleanup candidate, not as a new startup
        // failure, so an otherwise fresh Runtime is allowed to recover.
        await this.options.diagnostics?.log({
          level: "warn",
          component: "runtime.core",
          event: "core.attach_rejected",
          outcome: "failed",
          instance_id: this.activeInstanceId,
          error_code: "core_attachment_rejected",
          error_type: attachError instanceof Error ? attachError.name : "Error",
          // `details` only accepts the shared allowlist, which deliberately
          // excludes free text so no prompt or path can leak into the log.
          // `reason` is allowlisted and carries the short, non-identifying code.
          details: {
            reason: "core_attachment_rejected",
          },
        });
      }
      // The usual reason a launch fails outright is that a Core from a
      // previous run of this app is still holding the data directory. It is
      // invisible to the person using the app, so failing here would mean the
      // app can never start again after a crash.
      const cleared = await this.clearStaleCore();
      if (cleared === null) {
        await this.logStartFailure(error);
        throw error;
      }
      if (cleared !== "cleared") {
        const conflict = new Error(cleared, { cause: error });
        await this.logStartFailure(conflict);
        throw conflict;
      }
      this.exited = false;
      this.exitCode = null;
      this.exitSignal = null;
      this.spawnError = null;
      try {
        const health = await this.attempt();
        if (this.options.readerEnabled) {
          await this.startReader();
        }
        return health;
      } catch (retryError) {
        await this.logStartFailure(retryError);
        throw retryError;
      }
    }
  }

  private async logStartFailure(error: unknown): Promise<void> {
    if (this.startFailureLogged) return;
    this.startFailureLogged = true;
    await this.options.diagnostics?.log({
      level: "error",
      component: "runtime.core",
      event: "core.start_failed",
      outcome: "failed",
      instance_id: this.activeInstanceId,
      error_code: "core_start_failed",
      error_type: error instanceof Error ? error.name : "Error",
      // Same privacy rule as above: no free-text `message` in `details`, which
      // threw `diagnostic_detail_key_invalid` and masked the real start error.
      details: {
        reason: "core_start_failed",
      },
    });
  }

  private useOwnedIdentity(): void {
    this.activeInstanceId = this.ownedInstanceId;
    this.activeApiToken = this.ownedApiToken;
    this.activePort = this.ownedPort;
  }

  /** Attach to a standalone/CLI Core before trying to create a second writer. */
  private async attachExisting(): Promise<HealthResponse | null> {
    try {
      const probeHealth = this.options.probeHealth ?? defaultProbeHealth;
      const attachment = await discoverRuntimeAttachment({
        dataDir: this.options.dataDir,
        processIsAlive,
        probeIdentity: async (port, apiToken) => probeHealth(port, apiToken),
      });
      if (attachment === null) return null;
      this.activeInstanceId = attachment.instanceId;
      this.activeApiToken = attachment.apiToken;
      this.activePort = attachment.port;
      this.attached = true;
      return attachment.health;
    } catch (error) {
      if (!(error instanceof RuntimeDiscoveryError)) throw error;
      throw new Error(
        "无法验证正在运行的 MemHub Core。请先用 one-memory doctor 检查它。",
        { cause: error },
      );
    }
  }

  /** Deals with a Core that already owns the data directory.
   *
   * Returns `"cleared"` when the way is now free, a message to show the
   * person when another Core legitimately holds it, or `null` when this is
   * not what went wrong and the original failure should stand.
   */
  private async clearStaleCore(): Promise<"cleared" | string | null> {
    const receipt = await readCoreReceipt(this.options.dataDir);
    if (receipt === null) return null;

    if (!processIsAlive(receipt.pid)) {
      // The receipt outlived its Core, so the lock is already free and the
      // failure was a race with a shutdown. Worth one more attempt.
      return "cleared";
    }
    // Only a Core whose launcher is provably gone is an orphan. A Core with no
    // recorded launcher was started by hand — a developer's terminal — and
    // killing that would be taking something we were not given.
    if (receipt.parent_pid === null || processIsAlive(receipt.parent_pid)) {
      return (
        `另一个 MemHub Core 正在使用同一份数据（进程 ${receipt.pid}，` +
        `启动于 ${receipt.started_at}）。请先退出它，再打开 MemHub。`
      );
    }
    await terminateProcess(receipt.pid, this.options.shutdownTimeoutMs);
    return processIsAlive(receipt.pid)
      ? `无法结束上一次运行残留的 MemHub Core（进程 ${receipt.pid}）。` +
          "请手动结束该进程后重试。"
      : "cleared";
  }

  private async attempt(): Promise<HealthResponse> {
    try {
      return await this.attemptOnce();
    } catch (error) {
      await this.logStartFailure(error);
      throw error;
    }
  }

  private async attemptOnce(): Promise<HealthResponse> {
    const spawnProcess = this.options.spawnProcess ?? spawn;
    this.coreSessionId = randomUUID();
    await this.options.diagnostics?.log({
      level: "info",
      component: "runtime.core",
      event: "core.spawn_started",
      outcome: "started",
      instance_id: this.activeInstanceId,
      details: { spawned: true },
    });
    this.child = spawnProcess(
      this.options.launch.command,
      this.options.launch.args,
      {
        cwd: this.options.launch.cwd,
        env: {
          ...process.env,
          ONE_MEMORY_DATA_DIR: this.options.dataDir,
          ONE_MEMORY_HOST: "127.0.0.1",
          ONE_MEMORY_PORT: String(this.activePort),
          ONE_MEMORY_INSTANCE_ID: this.activeInstanceId,
          ONE_MEMORY_API_TOKEN: this.activeApiToken,
          // The four-level chain (ui.action -> kernel-call -> kernel-return ->
          // ui.result) is only complete when Core writes its half. Core's
          // diagnostic log is opt-in, so the shared spawn point is the one
          // place that must turn it on and stamp the identity every caller
          // (Desktop, CLI, LaunchAgent) shares.
          ONE_MEMORY_DIAGNOSTICS: "1",
          ...identityEnv(this.options.buildIdentity),
          // Core ties its own lifetime to us. Without this a Force Quit or a
          // crash leaves Core holding the data directory's lock, and the next
          // launch cannot start at all.
          ONE_MEMORY_PARENT_PID: String(process.pid),
          ONE_MEMORY_CORE_SESSION_ID: this.coreSessionId,
          ...(this.options.webDirectory
            ? { ONE_MEMORY_WEB_DIR: this.options.webDirectory }
            : {}),
          ...(this.options.parentSessionId
            ? { ONE_MEMORY_PARENT_SESSION_ID: this.options.parentSessionId }
            : {}),
        },
        stdio: this.options.onOutput
          ? ["ignore", "pipe", "pipe"]
          : ["ignore", "inherit", "inherit"],
        windowsHide: true,
      },
    );
    // The Runtime captures the Core's output itself so it can bound and rotate
    // it; launchd cannot rotate a descriptor it holds.
    this.child.stdout?.on("data", (chunk: Buffer) => {
      this.options.onOutput?.(chunk.toString(), "stdout");
    });
    this.child.stderr?.on("data", (chunk: Buffer) => {
      const text = chunk.toString();
      this.stderrTail = `${this.stderrTail}${text}`.slice(-4000);
      this.options.onOutput?.(text, "stderr");
    });
    this.ownedChild = true;
    this.exitPromise = new Promise((resolve) => {
      this.child?.once("exit", (code, signal) => {
        this.exited = true;
        this.exitCode = code;
        this.exitSignal = signal;
        resolve();
        // Startup failures are handled (and surfaced) by the attempt loop
        // below. Once the Core has come up, an unexpected exit is a runtime
        // fault we must recover from or report honestly.
        if (this.startedOk && !this.stopping) void this.scheduleRestart();
      });
      this.child?.once("error", (error) => {
        this.spawnError = error;
        this.exited = true;
        resolve();
      });
    });

    const probeHealth = this.options.probeHealth ?? defaultProbeHealth;
    const deadline = Date.now() + this.options.readinessBudgetMs;
    for (
      let attempt = 0;
      attempt < this.options.attempts && Date.now() < deadline;
      attempt += 1
    ) {
      if (this.exited) {
        const failure = this.spawnError
          ? `MemHub Core could not start: ${this.spawnError.message}`
          : `MemHub Core exited before becoming ready (${describeExit(this.exitCode, this.exitSignal)})`;
        await this.options.diagnostics?.log({
          level: "error",
          component: "runtime.core",
          event: "core.start_failed",
          outcome: "failed",
          instance_id: this.activeInstanceId,
          error_code: this.spawnError
            ? "core_spawn_failed"
            : "core_exited_before_ready",
          error_type: this.spawnError?.name ?? "Error",
          details: {
            ...(this.exitCode === null ? {} : { exit_code: this.exitCode }),
            ...(this.exitSignal === null ? {} : { signal: this.exitSignal }),
            // `stderr_tail` is the Core's own captured output: free text that
            // can carry private data, so it is deliberately NOT a diagnostic
            // detail key. Report only that the tail was non-empty; the bounded
            // text itself already lives in the rotating service log.
            ...(this.stderrTail.trim() ? { spawned: true } : {}),
          },
        });
        this.startFailureLogged = true;
        await this.stop();
        if (this.spawnError) {
          throw new Error(failure, { cause: this.spawnError });
        }
        throw new Error(failure);
      }
      try {
        const health = await probeHealth(this.activePort);
        this.lastProbeError = null;
        if (isOwnedHealth(health, this.activeInstanceId)) {
          this.startedOk = true;
          await this.options.diagnostics?.log({
            level: "info",
            component: "runtime.core",
            event: "core.ready",
            outcome: "succeeded",
            instance_id: this.activeInstanceId,
            details: { attempt: attempt + 1 },
          });
          return health;
        }
      } catch (error) {
        this.lastProbeError =
          error instanceof Error ? error.name : "probe_failed";
        // The local Core may still be starting.
      }
      await delay(this.options.delayMs);
    }

    await this.stop();
    await this.options.diagnostics?.log({
      level: "error",
      component: "runtime.core",
      event: "core.start_failed",
      outcome: "failed",
      instance_id: this.activeInstanceId,
      error_code: "core_start_timeout",
      error_type: "TimeoutError",
      details: {
        attempts: this.options.attempts,
        delay_ms: this.options.delayMs,
        budget_ms: this.options.readinessBudgetMs,
        last_probe: this.lastProbeError ?? "not_probed",
      },
    });
    this.startFailureLogged = true;
    throw new Error(
      `Timed out waiting for the owned MemHub Core (${this.options.readinessBudgetMs}ms budget, last probe: ${this.lastProbeError ?? "not_probed"})`,
    );
  }

  /** Relaunch an owned Core that exited after startup, with bounded backoff.
   *
   * Throttled and capped on purpose: the contract requires "the system-managed
   * process recovers, repeated crashes are throttled and diagnosable", and
   * explicitly forbids adding a second unlimited polling daemon. Each attempt
   * is spaced by exponential backoff, the count is finite, and every failure is
   * logged. When the budget is exhausted the Core is left down so the product
   * honestly reports `degraded` instead of silently pretending to be healthy.
   */
  private scheduleRestart(): Promise<void> {
    if (this.restartPromise) return this.restartPromise;
    this.restartPromise = (async () => {
      // Once the circuit is open, relaunching is what keeps corrupting the
      // heap. Report and wait for a caller that can act (a new session, a
      // restart from the product) instead of retrying forever.
      if (this.restartCircuitOpen) {
        await this.options.diagnostics?.log({
          level: "error",
          component: "runtime.core",
          event: "core.restart_skipped_circuit_open",
          outcome: "failed",
          instance_id: this.activeInstanceId,
          error_code: "core_restart_circuit_open",
          details: { restart_streak: this.restartStreak },
        });
        return;
      }
      try {
        const budget = this.options.restartAttempts;
        for (
          let attempt = 0;
          attempt < budget && !this.stopping;
          attempt += 1
        ) {
          // Carry the streak across rounds so the delay actually grows: 1s,
          // 2s, 4s ... capped, rather than restarting from 1s every crash.
          const delayMs = Math.min(
            this.options.restartBaseDelayMs *
              2 ** (this.restartStreak + attempt),
            this.options.restartMaxDelayMs,
          );
          await this.options.diagnostics?.log({
            level: "warn",
            component: "runtime.core",
            event: "core.restart_scheduled",
            outcome: "started",
            instance_id: this.activeInstanceId,
            details: { attempt: attempt + 1, delay_ms: delayMs },
          });
          await delay(delayMs);
          if (this.stopping) return;
          try {
            // The old Reader pointed at the Core that just died. Stop it so
            // the relaunched Core gets a Reader with the current token/port.
            await this.stopReader();
            // Keep the configured port stable across relaunches. The packaged
            // renderer and discovery receipts point at this port, so moving to
            // an ephemeral port would make a recovered Core unreachable.
            // A conflicting listener is therefore a fail-clear condition, not
            // a reason to silently change ports.
            this.activePort = this.ownedPort;
            this.child = null;
            this.exited = false;
            this.spawnError = null;
            const health = await this.attempt();
            if (this.options.readerEnabled) {
              await this.startReader();
            }
            await this.options.diagnostics?.log({
              level: "info",
              component: "runtime.core",
              event: "core.restart_succeeded",
              outcome: "succeeded",
              instance_id: this.activeInstanceId,
              details: { attempt: attempt + 1, port: this.activePort },
            });
            void health;
            // A Core that came back and stayed up is no longer in a crash
            // streak; the next failure starts from the base delay again.
            this.restartStreak = 0;
            this.restartCircuitOpen = false;
            return;
          } catch (error) {
            this.restartStreak += 1;
            await this.options.diagnostics?.log({
              level: "error",
              component: "runtime.core",
              event: "core.restart_failed",
              outcome: "failed",
              instance_id: this.activeInstanceId,
              error_code: "core_restart_failed",
              error_type: error instanceof Error ? error.name : "Error",
              details: { attempt: attempt + 1 },
            });
          }
        }
        if (!this.stopping) {
          // Repeated relaunch is what wears the heap down; stop feeding it and
          // report honestly. The product shows `degraded` instead of a green
          // dot over a dead Core.
          this.restartCircuitOpen = true;
          await this.options.diagnostics?.log({
            level: "error",
            component: "runtime.core",
            event: "core.restart_exhausted",
            outcome: "failed",
            instance_id: this.activeInstanceId,
            error_code: "core_restart_exhausted",
            details: { attempts: budget, restart_streak: this.restartStreak },
          });
          await this.options.diagnostics?.log({
            level: "error",
            component: "runtime.core",
            event: "core.restart_circuit_opened",
            outcome: "failed",
            instance_id: this.activeInstanceId,
            error_code: "core_restart_circuit_open",
            details: { restart_streak: this.restartStreak },
          });
        }
      } finally {
        this.restartPromise = null;
      }
    })();
    return this.restartPromise;
  }

  async stop(): Promise<void> {
    this.stopping = true;
    await this.stopReader();
    const child = this.child;
    const exitPromise = this.exitPromise;
    if (!child) {
      this.attached = false;
      this.useOwnedIdentity();
      return;
    }

    if (!this.exited) {
      child.kill("SIGTERM");
      const exitedGracefully =
        exitPromise &&
        (await Promise.race([
          exitPromise.then(() => true),
          delay(this.options.shutdownTimeoutMs).then(() => false),
        ]));
      if (!exitedGracefully && !this.exited) {
        child.kill("SIGKILL");
        if (exitPromise) {
          await Promise.race([exitPromise, delay(1_000)]);
        }
      }
    }
    this.child = null;
    this.exitPromise = null;
    this.attached = false;
    this.coreSessionId = null;
    this.useOwnedIdentity();
  }

  private async startReader(): Promise<void> {
    if (this.readerChild) return;
    const spawnProcess =
      this.options.spawnProcess ??
      ((command: string, args: readonly string[], opts: SpawnOptions) =>
        spawn(command, args, opts));
    // The Core CLI only accepts `serve --reader`. A bare `--reader` makes the
    // child print usage and exit 2, so the Reader never actually started.
    const args = [...this.options.launch.args, "serve", "--reader"];
    const environment: NodeJS.ProcessEnv = {
      ...process.env,
      // The Reader opens the same database the Core is using. Spreading
      // process.env alone can hand it a different directory than the one the
      // supervisor resolved (an isolated install keeps the database under a
      // scoped path), and then it exits with reader_database_missing.
      ONE_MEMORY_DATA_DIR: this.options.dataDir,
      ONE_MEMORY_CORE_PORT: String(this.activePort),
      ONE_MEMORY_CORE_API_TOKEN: this.activeApiToken,
      ONE_MEMORY_READER_PORT: "0",
    };
    const child = spawnProcess(this.options.launch.command, args, {
      stdio: "pipe",
      env: environment,
      cwd: this.options.launch.cwd,
    });
    this.readerChild = child;
    // A Reader exits when its Core goes away. Clear the handle so the restart
    // path can spawn a fresh one instead of returning early forever.
    child.on("exit", () => {
      if (this.readerChild === child) {
        this.readerChild = null;
        this.activeReaderPort = null;
      }
    });
    // Wait briefly for the reader to publish its port on stderr.
    this.activeReaderPort = await new Promise<number | null>((resolve) => {
      let settled = false;
      const finish = (value: number | null) => {
        if (settled) return;
        settled = true;
        resolve(value);
      };
      child.on("error", () => finish(null));
      child.on("exit", () => finish(null));
      let readerBuffer = "";
      if (child.stderr) {
        child.stderr.on("data", (chunk: string) => {
          readerBuffer += chunk;
          const match = /reader_port:(\d+)/.exec(readerBuffer);
          if (match) finish(Number(match[1]));
        });
      }
      setTimeout(() => finish(null), 10_000).unref();
    });
    await this.options.diagnostics?.log({
      level: "info",
      component: "runtime.core",
      event: "reader.started",
      outcome: "succeeded",
      instance_id: this.activeInstanceId,
      details: { reader_port: this.activeReaderPort },
    });
  }

  private async stopReader(): Promise<void> {
    const child = this.readerChild;
    this.readerChild = null;
    this.activeReaderPort = null;
    if (!child) return;
    child.kill("SIGTERM");
    await new Promise<void>((resolve) => {
      child.on("exit", resolve);
      setTimeout(resolve, 3_000).unref();
    });
  }
}
