import {
  chmod,
  mkdir,
  mkdtemp,
  readFile,
  rm,
  stat,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RuntimeService } from "./runtime-service.js";
import { RuntimeSupervisor } from "./runtime-supervisor.js";

const roots: string[] = [];
async function directory(prefix: string) {
  const root = await mkdtemp(path.join(tmpdir(), prefix));
  roots.push(root);
  return root;
}
afterEach(async () => {
  await Promise.all(
    roots.splice(0).map((root) => rm(root, { recursive: true, force: true })),
  );
});

function supervisor() {
  return {
    connection: {
      coreBaseUrl: "http://127.0.0.1:8765",
      coreApiToken: "secret",
      instanceId: "runtime-instance",
    },
    start: vi.fn(async () => undefined),
    stop: vi.fn(async (): Promise<void> => {}),
  };
}

describe("RuntimeService", () => {
  it("publishes and removes an outer Runtime receipt without secrets", async () => {
    const dataDir = await directory("runtime-service-");
    const runtime = supervisor();
    const service = new RuntimeService({
      dataDir,
      createSupervisor: () => runtime,
      pid: 4321,
    });

    await service.start();
    const receipt = JSON.parse(
      await readFile(path.join(dataDir, "runtime-service.json"), "utf8"),
    );
    expect(receipt).toMatchObject({
      schema_version: 1,
      service: "one-memory-runtime",
      status: "running",
      instance_id: "runtime-instance",
      pid: 4321,
      core_port: 8765,
    });
    expect(JSON.stringify(receipt)).not.toContain("secret");

    await service.stop();
    await expect(
      readFile(path.join(dataDir, "runtime-service.json")),
    ).rejects.toThrow();
    expect(runtime.stop).toHaveBeenCalledOnce();
  });

  it("refuses a live owner of the same Runtime home", async () => {
    const dataDir = await directory("runtime-service-live-");
    const lock = path.join(dataDir, "runtime-service.lock");
    await import("node:fs/promises").then(({ mkdir }) =>
      mkdir(lock, { mode: 0o700 }),
    );
    await writeFile(
      path.join(lock, "owner.json"),
      JSON.stringify({ pid: 777 }),
      {
        mode: 0o600,
      },
    );
    await chmod(lock, 0o700);
    const service = new RuntimeService({
      dataDir,
      createSupervisor: supervisor,
      processIsAlive: (pid) => pid === 777,
    });

    await expect(service.start()).rejects.toThrow(
      "runtime_service_already_running",
    );
  });

  it("reclaims a lease older than the current boot even when a recycled PID answers the liveness probe", async () => {
    // Issue #184: a forced power-off leaves runtime-service.lock behind;
    // after the reboot the owner's PID is another process, so the old
    // liveness-only check reports "already running" forever and launchd's
    // KeepAlive loops the wrapper. A lease from a previous boot can never
    // have a living owner, regardless of what the PID table says.
    const dataDir = await directory("runtime-service-prevboot-");
    const lock = path.join(dataDir, "runtime-service.lock");
    await import("node:fs/promises").then(({ mkdir }) =>
      mkdir(lock, { mode: 0o700 }),
    );
    await writeFile(
      path.join(lock, "owner.json"),
      JSON.stringify({ pid: 777 }),
      {
        mode: 0o600,
      },
    );
    await chmod(lock, 0o700);
    const leaseTime = Date.now();
    const service = new RuntimeService({
      dataDir,
      createSupervisor: supervisor,
      // The recycled PID answers the probe: the liveness check alone
      // would refuse forever.
      processIsAlive: () => true,
      // This boot began minutes after the lease was taken.
      bootTime: () => leaseTime + 10 * 60_000,
    });

    await service.start();
    await service.stop();
  });

  it("reclaims a lease whose owner.json was never written once the machine has rebooted", async () => {
    // Issue #184's hard case: a power cut inside the mkdir → privateWrite
    // window leaves the lock directory with no owner.json at all. That used
    // to be runtime_service_lock_invalid forever; after a reboot it is
    // provably from a previous boot and is cleared.
    const dataDir = await directory("runtime-service-prevboot- corrupt-");
    const lock = path.join(dataDir, "runtime-service.lock");
    await import("node:fs/promises").then(({ mkdir }) =>
      mkdir(lock, { mode: 0o700 }),
    );
    await chmod(lock, 0o700);
    const leaseTime = Date.now();
    const service = new RuntimeService({
      dataDir,
      createSupervisor: supervisor,
      processIsAlive: () => true,
      bootTime: () => leaseTime + 10 * 60_000,
    });

    await service.start();
    await service.stop();
  });

  it("still refuses a corrupt lease from the current boot", async () => {
    // Same-boot corruption stays refused: the boot-time shortcut only
    // applies when the lease provably predates the boot.
    const dataDir = await directory("runtime-service-corrupt-sameboot-");
    const lock = path.join(dataDir, "runtime-service.lock");
    await import("node:fs/promises").then(({ mkdir }) =>
      mkdir(lock, { mode: 0o700 }),
    );
    await chmod(lock, 0o700);
    const service = new RuntimeService({
      dataDir,
      createSupervisor: supervisor,
      bootTime: () => Date.now() - 60 * 60_000, // booted an hour ago
    });

    await expect(service.start()).rejects.toThrow(
      "runtime_service_lock_invalid",
    );
  });

  it("reclaims a stale validated lease", async () => {
    const dataDir = await directory("runtime-service-stale-");
    const lock = path.join(dataDir, "runtime-service.lock");
    await import("node:fs/promises").then(({ mkdir }) =>
      mkdir(lock, { mode: 0o700 }),
    );
    await writeFile(
      path.join(lock, "owner.json"),
      JSON.stringify({ pid: 777 }),
      {
        mode: 0o600,
      },
    );
    await chmod(lock, 0o700);
    const service = new RuntimeService({
      dataDir,
      createSupervisor: supervisor,
      processIsAlive: () => false,
    });

    await service.start();
    await service.stop();
  });

  it("releases its lease when supervisor construction fails", async () => {
    const dataDir = await directory("runtime-service-create-");
    const service = new RuntimeService({
      dataDir,
      createSupervisor: async () => {
        throw new Error("cannot prepare runtime");
      },
    });

    await expect(service.start()).rejects.toThrow("cannot prepare runtime");
    await expect(
      readFile(path.join(dataDir, "runtime-service.json")),
    ).rejects.toThrow();
    await expect(
      readFile(path.join(dataDir, "runtime-service.lock", "owner.json")),
    ).rejects.toThrow();
  });

  it("retains stopping receipt and live lease after drain failure, then retries the real supervisor", async () => {
    const dataDir = await directory("runtime-service-drain-");
    const sidecar = {
      start: vi.fn(async () => {}),
      stop: vi.fn(async () => {}),
    };
    sidecar.stop.mockRejectedValueOnce(Error("drain failed"));
    const core = {
      instanceId: "runtime-instance",
      apiToken: "secret",
      port: 8765,
      start: vi.fn(async () => ({
        status: "ok" as const,
        service: "one-memory-core" as const,
        version: "fixture",
        instance_id: "runtime-instance",
      })),
      stop: vi.fn(async () => {}),
    };
    const runtime = new RuntimeSupervisor({
      core,
      createSidecar: () => sidecar,
    });
    const service = new RuntimeService({
      dataDir,
      createSupervisor: () => runtime,
      pid: 4321,
    });
    await service.start();
    const owner = await readFile(
      path.join(dataDir, "runtime-service.lock", "owner.json"),
      "utf8",
    );
    await expect(service.stop()).rejects.toThrow("drain failed");
    expect(core.stop).not.toHaveBeenCalled();
    expect(() => runtime.connection).toThrow();
    expect(
      await readFile(
        path.join(dataDir, "runtime-service.lock", "owner.json"),
        "utf8",
      ),
    ).toBe(owner);
    expect(
      JSON.parse(
        await readFile(path.join(dataDir, "runtime-service.json"), "utf8"),
      ),
    ).toMatchObject({ status: "stopping", instance_id: "runtime-instance" });
    await expect(service.start()).rejects.toThrow("already started");
    const competitor = new RuntimeService({
      dataDir,
      createSupervisor: supervisor,
      processIsAlive: () => true,
    });
    await expect(competitor.start()).rejects.toThrow(
      "runtime_service_already_running",
    );
    await service.stop();
    expect(sidecar.stop).toHaveBeenCalledTimes(2);
    expect(core.stop).toHaveBeenCalledOnce();
    await expect(
      readFile(path.join(dataDir, "runtime-service.lock", "owner.json")),
    ).rejects.toThrow();
    await expect(
      readFile(path.join(dataDir, "runtime-service.json")),
    ).rejects.toThrow();
  });

  it("shares concurrent stop without reading an invalidated connection or releasing ownership early", async () => {
    const dataDir = await directory("runtime-service-concurrent-");
    let release!: () => void;
    let entered!: () => void;
    const admission = new Promise<void>((resolve) => {
      entered = resolve;
    });
    let running = true;
    const connection = supervisor().connection;
    const runtime = {
      get connection() {
        if (!running) throw Error("not running");
        return connection;
      },
      start: vi.fn(async () => {}),
      stop: vi.fn(async () => {
        running = false;
        entered();
        await new Promise<void>((resolve) => {
          release = resolve;
        });
      }),
    };
    const service = new RuntimeService({
      dataDir,
      createSupervisor: () => runtime,
    });
    await service.start();
    const first = service.stop();
    await admission;
    let secondSettled = false;
    const second = service.stop().then(
      () => {
        secondSettled = true;
      },
      () => {
        secondSettled = true;
      },
    );
    try {
      await new Promise((resolve) => setTimeout(resolve, 20));
      expect(secondSettled).toBe(false);
      expect(runtime.stop).toHaveBeenCalledOnce();
      expect(
        JSON.parse(
          await readFile(path.join(dataDir, "runtime-service.json"), "utf8"),
        ),
      ).toHaveProperty("status", "stopping");
      expect(
        await readFile(
          path.join(dataDir, "runtime-service.lock", "owner.json"),
          "utf8",
        ),
      ).toBeTruthy();
    } finally {
      release();
      await Promise.all([first, second]);
    }
  });

  it("failed start cleanup retains the supervisor and lease when stop fails, even without connection", async () => {
    const dataDir = await directory("runtime-service-start-drain-");
    const runtime = {
      get connection(): never {
        throw Error("not started");
      },
      start: vi.fn(async () => {
        throw Error("start failed");
      }),
      stop: vi.fn(async () => {}),
    };
    runtime.stop.mockRejectedValueOnce(Error("cleanup failed"));
    const service = new RuntimeService({
      dataDir,
      createSupervisor: () => runtime,
    });
    await expect(service.start()).rejects.toThrow("cleanup failed");
    expect(
      await readFile(
        path.join(dataDir, "runtime-service.lock", "owner.json"),
        "utf8",
      ),
    ).toBeTruthy();
    await expect(service.start()).rejects.toThrow("already started");
    await service.stop();
    expect(runtime.stop).toHaveBeenCalledTimes(2);
    await expect(
      readFile(path.join(dataDir, "runtime-service.lock", "owner.json")),
    ).rejects.toThrow();
  });

  it("concurrent failed stops share one rejection and a later call retries cleanup", async () => {
    const dataDir = await directory("runtime-service-shared-failure-");
    const runtime = supervisor();
    let reject!: (error: Error) => void;
    let entered!: () => void;
    const admitted = new Promise<void>((resolve) => {
      entered = resolve;
    });
    runtime.stop.mockImplementationOnce(
      () =>
        new Promise<void>((_resolve, fail) => {
          reject = fail;
          entered();
        }),
    );
    const service = new RuntimeService({
      dataDir,
      createSupervisor: () => runtime,
    });
    await service.start();
    const first = service.stop();
    await admitted;
    const second = service.stop();
    expect(second).toBe(first);
    const results = Promise.allSettled([first, second]);
    reject(Error("not drained"));
    expect((await results).map((result) => result.status)).toEqual([
      "rejected",
      "rejected",
    ]);
    expect(runtime.stop).toHaveBeenCalledOnce();
    expect(
      JSON.parse(
        await readFile(path.join(dataDir, "runtime-service.json"), "utf8"),
      ),
    ).toHaveProperty("status", "stopping");
    expect(
      await readFile(
        path.join(dataDir, "runtime-service.lock", "owner.json"),
        "utf8",
      ),
    ).toBeTruthy();
    await service.stop();
    expect(runtime.stop).toHaveBeenCalledTimes(2);
    await expect(
      readFile(path.join(dataDir, "runtime-service.lock", "owner.json")),
    ).rejects.toThrow();
  });

  it("successful cleanup after failed startup releases the owner and preserves the startup error", async () => {
    const dataDir = await directory("runtime-service-start-failure-");
    const runtime = supervisor();
    runtime.start.mockRejectedValueOnce(Error("start failed"));
    const service = new RuntimeService({
      dataDir,
      createSupervisor: () => runtime,
    });
    await expect(service.start()).rejects.toThrow("start failed");
    expect(runtime.stop).toHaveBeenCalledOnce();
    await expect(
      readFile(path.join(dataDir, "runtime-service.json")),
    ).rejects.toThrow();
    await expect(
      readFile(path.join(dataDir, "runtime-service.lock", "owner.json")),
    ).rejects.toThrow();
    await service.start();
    await service.stop();
    expect(runtime.start).toHaveBeenCalledTimes(2);
  });

  it("normalizes a pre-existing 0755 data dir to 0700 on start", async () => {
    // A bare mkdir leaves 0755; the Runtime must still end up with an
    // owner-only data dir so the connection-control 0700 guard accepts it.
    const parent = await directory("runtime-service-mode-");
    const dataDir = path.join(parent, "data");
    await mkdir(dataDir, { mode: 0o755 });
    expect(((await stat(dataDir)).mode & 0o777).toString(8)).toBe("755");

    const service = new RuntimeService({
      dataDir,
      createSupervisor: () => supervisor(),
    });
    await service.start();
    expect(((await stat(dataDir)).mode & 0o777).toString(8)).toBe("700");
    await service.stop();
  });

  it("fails closed when the data dir is a symlink instead of normalizing it", async () => {
    const parent = await directory("runtime-service-symlink-");
    const real = path.join(parent, "real");
    const link = path.join(parent, "data");
    await mkdir(real, { mode: 0o755 });
    await import("node:fs/promises").then(({ symlink }) => symlink(real, link));
    const service = new RuntimeService({
      dataDir: link,
      createSupervisor: () => supervisor(),
    });
    await expect(service.start()).rejects.toThrow(
      "runtime_service_data_directory_unsafe",
    );
  });

  it("fails closed when the data dir path is not a directory", async () => {
    const parent = await directory("runtime-service-notdir-");
    const dataDir = path.join(parent, "data");
    await writeFile(dataDir, "not a directory");
    const service = new RuntimeService({
      dataDir,
      createSupervisor: () => supervisor(),
    });
    await expect(service.start()).rejects.toThrow();
  });
});
