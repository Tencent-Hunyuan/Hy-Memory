import { randomUUID } from "node:crypto";
import {
  chmod,
  lstat,
  mkdir,
  readFile,
  rename,
  rm,
  rmdir,
  writeFile,
} from "node:fs/promises";
import os from "node:os";
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
  /** Wall-clock epoch (ms) of the current boot. The default is derived from
   * `os.uptime()`; injectable so tests can put a lease before or after the
   * boot without moving the machine's clock. */
  bootTime?: () => number;
  diagnostics?: DiagnosticSink;
}

interface LeaseIdentity {
  dev: number;
  ino: number;
}

/** Publishes one outer Runtime identity around the Core+sidecar supervisor. */
export class RuntimeService {
  /** See removeStaleLease: absorbs a forward clock step since boot. */
  private static readonly BOOT_SLACK_MS = 2 * 60_000;

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
        await mkdir(lock, { mode: 0o700 });
        const identity = await lstat(lock);
        this.lease = { dev: identity.dev, ino: identity.ino };
        try {
          await privateWrite(
            path.join(lock, "owner.json"),
            JSON.stringify({
              pid: this.pid(),
              created_at: new Date().toISOString(),
            }),
          );
        } catch (error) {
          await this.releaseLease();
          throw error;
        }
        return;
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== "EEXIST" || attempt > 0) {
          throw new Error("runtime_service_lock_unavailable", { cause: error });
        }
        await this.removeStaleLease(lock);
      }
    }
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
    // A lease created before the current boot cannot have a living owner:
    // every process from that boot is gone, and a PID that answers the
    // liveness probe now is a different process that merely reused the
    // number. Before this check, a forced power-off (issue #184) left
    // runtime-service.lock behind, the reboot recycled the owner's PID, and
    // launchd's KeepAlive restarted the wrapper into
    // runtime_service_already_running without end.
    //
    // The slack absorbs a forward clock step since boot: an NTP correction
    // would otherwise make a lease taken moments after boot look older than
    // the boot itself. With the slack, only a jump larger than two minutes
    // within that window could mistake a live lease for a stale one.
    const bootTime = this.options.bootTime ?? defaultBootTime;
    if (info.mtimeMs < bootTime() - RuntimeService.BOOT_SLACK_MS) {
      await this.clearLeaseDir(lock);
      return;
    }
    let owner: unknown;
    try {
      owner = JSON.parse(await readFile(path.join(lock, "owner.json"), "utf8"));
    } catch {
      throw new Error("runtime_service_lock_invalid");
    }
    const pid =
      owner && typeof owner === "object"
        ? (owner as { pid?: unknown }).pid
        : undefined;
    if (typeof pid !== "number" || !Number.isInteger(pid) || pid <= 1) {
      throw new Error("runtime_service_lock_invalid");
    }
    if ((this.options.processIsAlive ?? processIsAlive)(pid)) {
      throw new Error("runtime_service_already_running");
    }
    await this.clearLeaseDir(lock);
  }

  private async clearLeaseDir(lock: string): Promise<void> {
    const stale = `${lock}.stale.${randomUUID()}`;
    await rename(lock, stale);
    await rm(stale, { recursive: true });
  }

  private async releaseLease(): Promise<void> {
    const lease = this.lease;
    this.lease = null;
    if (!lease) return;
    const lock = this.lockPath();
    const current = await lstat(lock).catch(() => null);
    if (!current || current.dev !== lease.dev || current.ino !== lease.ino)
      return;
    await rm(path.join(lock, "owner.json"), { force: true });
    await rmdir(lock).catch(() => undefined);
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

function defaultBootTime(): number {
  // Wall-clock epoch of the current boot, as far as the clock admits today.
  // os.uptime() is boot-based (includes suspend) on every supported platform.
  return Date.now() - os.uptime() * 1000;
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
