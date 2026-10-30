import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { readFileSync, type Stats } from "node:fs";
import {
  chmod,
  lstat,
  mkdir,
  readdir,
  readFile,
  rename,
  rm,
  writeFile,
} from "node:fs/promises";
import path from "node:path";

import { processIsAlive } from "./core-supervisor.js";
import { type DiagnosticSink, diagnosticError } from "./diagnostic-log.js";
import type { RuntimeConnection } from "./runtime-supervisor.js";

export interface ManagedRuntime {
  readonly connection: RuntimeConnection;
  /** Sidecar (Broker) health as the supervisor last observed it. Optional so a
   * purely Core-based embedder keeps working; when present it is published into
   * the outer service receipt so a *separate* process (`status`) can report
   * `degraded` without sharing memory with the Runtime. */
  readonly sidecarState?: "running" | "failed" | "stopped";
  start(): Promise<unknown>;
  stop(): Promise<void>;
}

export interface RuntimeServiceOptions {
  dataDir: string;
  /** Build the supervisor. `onSidecarFault` must be wired into it so a captured
   * post-start sidecar fault reaches the durable receipt this service writes. */
  createSupervisor(
    onSidecarFault: () => void,
  ): ManagedRuntime | Promise<ManagedRuntime>;
  pid?: number;
  processIsAlive?: (pid: number) => boolean;
  /** Identity of the current boot (`kern.bootsessionuuid` on macOS,
   * `/proc/sys/kernel/random/boot_id` on Linux, `null` when unknown).
   * Injectable so tests can simulate a reboot without one. */
  bootId?: () => string | null;
  /** Wall-clock epoch (ms) the kernel reports for the current boot, or `null`
   * when unknown. Consulted only for a legacy lock directory without
   * owner.json; see removeUnownedLease. Injectable for tests. */
  legacyBootTime?: () => number | null;
  diagnostics?: DiagnosticSink;
}

interface LeaseIdentity {
  dev: number;
  ino: number;
}

/** Publishes one outer Runtime identity around the Core+sidecar supervisor. */
export class RuntimeService {
  private readonly options: RuntimeServiceOptions;
  private lease: LeaseIdentity | null = null;
  private supervisor: ManagedRuntime | null = null;
  private started = false;
  private stopping: Promise<void> | null = null;
  private receiptConnection: Pick<
    RuntimeConnection,
    "instanceId" | "coreBaseUrl"
  > | null = null;
  private sidecarFailed = false;

  constructor(options: RuntimeServiceOptions) {
    this.options = options;
  }

  async start(): Promise<void> {
    if (this.lease || this.started) {
      throw new Error("MemHub Runtime service already started");
    }
    await this.options.diagnostics?.log({
      level: "info",
      component: "runtime.service",
      event: "service.starting",
      outcome: "started",
    });
    await this.acquireLease();
    try {
      const supervisor = await this.options.createSupervisor(() => {
        void this.noteSidecarFault();
      });
      this.supervisor = supervisor;
      await supervisor.start();
      const connection = supervisor.connection;
      this.receiptConnection = {
        instanceId: connection.instanceId,
        coreBaseUrl: connection.coreBaseUrl,
      };
      await this.writeReceipt(
        "running",
        connection.instanceId,
        connection.coreBaseUrl,
      );
      this.started = true;
      await this.options.diagnostics?.log({
        level: "info",
        component: "runtime.service",
        event: "service.ready",
        outcome: "succeeded",
        instance_id: connection.instanceId,
      });
    } catch (error) {
      await this.options.diagnostics?.log({
        level: "error",
        component: "runtime.service",
        event: "service.start_failed",
        outcome: "failed",
        error_code: "runtime_service_start_failed",
        ...diagnosticError(error),
      });
      // Failed cleanup must retain the live owner for a subsequent stop attempt.
      await this.stop();
      throw error;
    }
  }

  stop(): Promise<void> {
    if (this.stopping) return this.stopping;
    if (!this.lease) return Promise.resolve();
    const work = Promise.resolve().then(() => this.drain());
    const stopping = work.finally(() => {
      if (this.stopping === stopping) this.stopping = null;
    });
    this.stopping = stopping;
    return stopping;
  }

  /** Record that a sidecar (Broker) faulted after startup.
   *
   * The Runtime keeps running; only the receipt is upgraded to carry
   * `sidecar: "failed"` so a *separate* observer process (`status`, the
   * settings page) reports `degraded` instead of `healthy`. Best-effort and
   * idempotent: a failure to write must never take the daemon down, and the
   * first fault is what matters. */
  async noteSidecarFault(): Promise<void> {
    if (this.sidecarFailed || !this.receiptConnection) return;
    this.sidecarFailed = true;
    await this.writeReceipt(
      "running",
      this.receiptConnection.instanceId,
      this.receiptConnection.coreBaseUrl,
      "failed",
    ).catch(() => undefined);
  }

  private async drain(): Promise<void> {
    if (!this.receiptConnection && this.supervisor) {
      try {
        const connection = this.supervisor.connection;
        this.receiptConnection = {
          instanceId: connection.instanceId,
          coreBaseUrl: connection.coreBaseUrl,
        };
      } catch {
        // Partial startup may have no public identity. Keep the lease, not a fabricated receipt.
      }
    }
    if (this.receiptConnection) {
      await this.writeReceipt(
        "stopping",
        this.receiptConnection.instanceId,
        this.receiptConnection.coreBaseUrl,
      ).catch(() => undefined);
    }
    await this.supervisor?.stop();
    this.started = false;
    this.supervisor = null;
    await this.removeReceipt();
    await this.releaseLease();
    this.receiptConnection = null;
    await this.options.diagnostics?.log({
      level: "info",
      component: "runtime.service",
      event: "service.stopped",
      outcome: "succeeded",
    });
  }

  private async acquireLease(): Promise<void> {
    await mkdir(this.options.dataDir, { recursive: true, mode: 0o700 });
    // mkdir's mode does not change an already-existing directory, so a data dir
    // that was pre-created with default perms (e.g. 0755 by an isolated test
    // root) would keep those perms and later trip the connection-control
    // 0700+uid guard with an opaque 503. Normalize the Runtime-owned data dir
    // itself; never recurse, never follow a symlink.
    const info = await lstat(this.options.dataDir);
    if (info.isSymbolicLink() || !info.isDirectory())
      throw new Error("runtime_service_data_directory_unsafe");
    await chmod(this.options.dataDir, 0o700);
    const lock = this.lockPath();
    for (let attempt = 0; attempt < 2; attempt += 1) {
      try {
        await this.publishLease(lock);
        break;
      } catch (error) {
        if (!leaseTaken(error) || attempt > 0) {
          throw new Error("runtime_service_lock_unavailable", { cause: error });
        }
        await this.removeStaleLease(lock);
      }
    }
    await this.sweepLeaseDebris();
  }

  /** Publish the lease in one step: the lock directory becomes visible with
   * owner.json (pid, created_at, boot_id) already inside it, so a power cut
   * can leave a staging directory behind but never a lock without an owner.
   * The old mkdir -> write sequence had exactly that window (issue #184). */
  private async publishLease(lock: string): Promise<void> {
    const staging = `${lock}${STAGING_INFIX}${randomUUID()}`;
    await mkdir(staging, { mode: 0o700 });
    try {
      await privateWrite(
        path.join(staging, "owner.json"),
        JSON.stringify({
          pid: this.pid(),
          created_at: new Date().toISOString(),
          boot_id: this.currentBootId(),
        }),
      );
      // POSIX rename() silently REPLACES an existing *empty* directory, so on
      // its own it is not an exclusive create. A non-empty lock (every lease
      // this version publishes) makes it fail with EEXIST/ENOTEMPTY. An empty
      // lock can only come from an older version's two-step mkdir -> write:
      // either power-cut debris, or a live acquire in flight. The latter must
      // not be overwritten, and the former has to go through
      // removeStaleLease's legacy rule rather than be replaced blindly, so an
      // existing lock of any kind is reported as taken here. The residual race
      // (an old binary mkdir-ing between this check and the rename) needs an
      // old and a new Runtime starting on the same home within microseconds;
      // this version never creates an empty lock, since releaseLease also
      // removes it with a single rename.
      if (await pathExists(lock)) throw leaseTakenError();
      await rename(staging, lock);
    } catch (error) {
      await rm(staging, { recursive: true, force: true }).catch(
        () => undefined,
      );
      throw error;
    }
    // rename() keeps the inode, so this is the published directory's identity;
    // releaseLease compares it before touching the lock.
    const identity = await lstat(lock);
    this.lease = { dev: identity.dev, ino: identity.ino };
  }

  /** Remove staging and cleared-lease directories left behind by a crash.
   * Runs only while this process holds the lease, so a staging directory is
   * either debris or belongs to a competitor that has already lost (its
   * rename cannot succeed over our non-empty lock). Best-effort. */
  private async sweepLeaseDebris(): Promise<void> {
    const base = path.basename(this.lockPath());
    const names = await readdir(this.options.dataDir).catch((): string[] => []);
    await Promise.all(
      names
        .filter(
          (name) =>
            name.startsWith(`${base}${STAGING_INFIX}`) ||
            name.startsWith(`${base}${STALE_INFIX}`),
        )
        .map((name) =>
          rm(path.join(this.options.dataDir, name), {
            recursive: true,
            force: true,
          }).catch(() => undefined),
        ),
    );
  }

  private async removeStaleLease(lock: string): Promise<void> {
    const info = await lstat(lock);
    if (
      !info.isDirectory() ||
      info.isSymbolicLink() ||
      (process.platform !== "win32" &&
        ((info.mode & 0o077) !== 0 ||
          (typeof process.getuid === "function" &&
            info.uid !== process.getuid())))
    ) {
      throw new Error("runtime_service_lock_unsafe");
    }
    let raw: string;
    try {
      raw = await readFile(path.join(lock, "owner.json"), "utf8");
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") {
        await this.removeUnownedLease(lock, info);
        return;
      }
      throw new Error("runtime_service_lock_invalid", { cause: error });
    }
    let owner: unknown;
    try {
      owner = JSON.parse(raw);
    } catch {
      throw new Error("runtime_service_lock_invalid");
    }
    if (!owner || typeof owner !== "object" || Array.isArray(owner)) {
      throw new Error("runtime_service_lock_invalid");
    }
    // A lease written during another boot cannot have a living owner: every
    // process of that boot is gone, and a PID that answers the liveness probe
    // now merely reused the number. Without this, a forced power-off left the
    // lock behind, the reboot recycled the owner's PID, and launchd's
    // KeepAlive restarted the wrapper into runtime_service_already_running
    // without end (issue #184). The kernel's boot identity is compared, not
    // the wall clock: the clock can jump forward while a lease is held (no
    // RTC, VM resume, NTP or manual correction) and make a live lease look
    // older than the boot. When either side is unknown (a lease from an older
    // version, an unsupported platform) the PID probe below decides, as before.
    const ownerBoot = (owner as { boot_id?: unknown }).boot_id;
    const currentBoot = this.currentBootId();
    if (
      typeof ownerBoot === "string" &&
      ownerBoot !== "" &&
      currentBoot !== null &&
      ownerBoot !== currentBoot
    ) {
      await this.clearLeaseDir(lock);
      return;
    }
    const pid = (owner as { pid?: unknown }).pid;
    if (typeof pid !== "number" || !Number.isInteger(pid) || pid <= 1) {
      throw new Error("runtime_service_lock_invalid");
    }
    if ((this.options.processIsAlive ?? processIsAlive)(pid)) {
      throw new Error("runtime_service_already_running");
    }
    await this.clearLeaseDir(lock);
  }

  /** A lock directory without owner.json. This version publishes owner.json
   * together with the lock and releases it with one rename, so such a
   * directory was written by an older version (mkdir -> write, where a power
   * cut in between leaves it empty) or by hand. It names no PID and no boot,
   * so the only usable evidence is "created before the current boot": clear
   * it then, and otherwise keep the previous refusal
   * (runtime_service_lock_invalid), including when the boot time is unknown.
   *
   * The kernel boot time and the directory mtime are both wall-clock values,
   * which is why lease ownership itself no longer depends on them. Here a
   * forward clock jump can only hurt if an older binary is live inside its
   * mkdir -> write window at that very moment; a backward jump only keeps the
   * old refusal. */
  private async removeUnownedLease(lock: string, info: Stats): Promise<void> {
    const bootTime = (this.options.legacyBootTime ?? currentBootTimeMs)();
    if (bootTime === null || !(info.mtimeMs < bootTime)) {
      throw new Error("runtime_service_lock_invalid");
    }
    await this.clearLeaseDir(lock);
  }

  private async clearLeaseDir(lock: string): Promise<void> {
    const stale = `${lock}${STALE_INFIX}${randomUUID()}`;
    await rename(lock, stale);
    await rm(stale, { recursive: true, force: true });
  }

  private async releaseLease(): Promise<void> {
    const lease = this.lease;
    this.lease = null;
    if (!lease) return;
    const lock = this.lockPath();
    const current = await lstat(lock).catch(() => null);
    if (!current || current.dev !== lease.dev || current.ino !== lease.ino)
      return;
    // Take the lock away with one rename so no observer ever sees it without
    // owner.json: an owner-less lock is left to mean legacy debris.
    const released = `${lock}${STALE_INFIX}${randomUUID()}`;
    try {
      await rename(lock, released);
    } catch {
      return;
    }
    await rm(released, { recursive: true, force: true }).catch(() => undefined);
  }

  private currentBootId(): string | null {
    return (this.options.bootId ?? currentBootId)();
  }

  private async writeReceipt(
    status: "running" | "stopping",
    instanceId: string,
    coreBaseUrl: string,
    sidecar?: "running" | "failed",
  ): Promise<void> {
    const url = new URL(coreBaseUrl);
    if (url.hostname !== "127.0.0.1" || !url.port) {
      throw new Error("runtime_service_core_invalid");
    }
    await privateWrite(
      this.receiptPath(),
      JSON.stringify(
        {
          schema_version: 1,
          service: "one-memory-runtime",
          status,
          instance_id: instanceId,
          pid: this.pid(),
          core_port: Number(url.port),
          ...(sidecar === undefined ? {} : { sidecar }),
          started_at: new Date().toISOString(),
        },
        null,
        2,
      ),
    );
  }

  private async removeReceipt(): Promise<void> {
    await rm(this.receiptPath(), { force: true });
  }

  private receiptPath(): string {
    return path.join(this.options.dataDir, "runtime-service.json");
  }

  private lockPath(): string {
    return path.join(this.options.dataDir, "runtime-service.lock");
  }

  private pid(): number {
    return this.options.pid ?? process.pid;
  }
}

const STAGING_INFIX = ".tmp-";
const STALE_INFIX = ".stale.";

function leaseTaken(error: unknown): boolean {
  const code = (error as NodeJS.ErrnoException | undefined)?.code;
  return code === "EEXIST" || code === "ENOTEMPTY";
}

function leaseTakenError(): NodeJS.ErrnoException {
  return Object.assign(new Error("runtime_service_lock_exists"), {
    code: "EEXIST",
  });
}

async function pathExists(target: string): Promise<boolean> {
  try {
    await lstat(target);
    return true;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return false;
    throw error;
  }
}

function sysctl(name: string): string {
  return execFileSync("/usr/sbin/sysctl", ["-n", name], {
    encoding: "utf8",
    timeout: 1000,
    maxBuffer: 4096,
    killSignal: "SIGKILL",
    shell: false,
    stdio: ["ignore", "pipe", "ignore"],
    env: { PATH: "/usr/bin:/bin:/usr/sbin:/sbin", LANG: "C", LC_ALL: "C" },
  });
}

let cachedBootId: string | null | undefined;

/** The kernel's identity for the current boot, cached per process (it cannot
 * change while the process lives). `null` on unsupported platforms or when
 * the source cannot be read; the lease logic then falls back to the PID
 * probe. */
function currentBootId(): string | null {
  if (cachedBootId === undefined) cachedBootId = readBootId();
  return cachedBootId;
}

function readBootId(): string | null {
  try {
    let raw: string;
    if (process.platform === "darwin") raw = sysctl("kern.bootsessionuuid");
    else if (process.platform === "linux")
      raw = readFileSync("/proc/sys/kernel/random/boot_id", "utf8");
    else return null;
    const id = raw.trim();
    return /^[0-9A-Za-z-]{8,64}$/.test(id) ? id : null;
  } catch {
    return null;
  }
}

/** Wall-clock epoch (ms) the kernel reports for the current boot, or `null`.
 * Used only for owner-less legacy lock directories. */
function currentBootTimeMs(): number | null {
  try {
    if (process.platform === "darwin") {
      const match = /\bsec\s*=\s*(\d+)\s*,\s*usec\s*=\s*(\d+)/.exec(
        sysctl("kern.boottime"),
      );
      return match
        ? Number(match[1]) * 1000 + Math.floor(Number(match[2]) / 1000)
        : null;
    }
    if (process.platform === "linux") {
      const match = /^btime\s+(\d+)\s*$/m.exec(
        readFileSync("/proc/stat", "utf8"),
      );
      return match ? Number(match[1]) * 1000 : null;
    }
  } catch {
    // Unknown boot time: the caller keeps refusing.
  }
  return null;
}

async function privateWrite(target: string, value: string): Promise<void> {
  const temporary = `${target}.${randomUUID()}.tmp`;
  try {
    await writeFile(temporary, `${value}\n`, {
      encoding: "utf8",
      mode: 0o600,
      flag: "wx",
    });
    await chmod(temporary, 0o600);
    await rename(temporary, target);
    await chmod(target, 0o600);
  } catch (error) {
    await rm(temporary, { force: true }).catch(() => undefined);
    throw error;
  }
}
