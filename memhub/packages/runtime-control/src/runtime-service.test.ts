import {
  chmod,
  mkdir,
  mkdtemp,
  readdir,
  readFile,
  rm,
  stat,
  symlink,
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

const PREVIOUS_BOOT = "00000000-0000-4000-8000-00000000000a";
const CURRENT_BOOT = "00000000-0000-4000-8000-00000000000b";

/** Seed a lock as an existing owner would leave it; no owner = legacy debris. */
async function seedLock(dataDir: string, owner?: object): Promise<string> {
  const lock = path.join(dataDir, "runtime-service.lock");
  await mkdir(lock, { mode: 0o700 });
  if (owner) {
    await writeFile(path.join(lock, "owner.json"), JSON.stringify(owner), {
      mode: 0o600,
    });
  }
  await chmod(lock, 0o700);
  return lock;
}

async function ownerOf(dataDir: string): Promise<Record<string, unknown>> {
  return JSON.parse(
    await readFile(
      path.join(dataDir, "runtime-service.lock", "owner.json"),
      "utf8",
    ),
  );
}

/** Every lock, staging and cleared-lease entry in the data dir. */
async function lockEntries(dataDir: string): Promise<string[]> {
  return (await readdir(dataDir))
    .filter((name) => name.startsWith("runtime-service.lock"))
    .sort();
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
    await seedLock(dataDir, { pid: 777 });
    const service = new RuntimeService({
      dataDir,
      createSupervisor: supervisor,
      processIsAlive: (pid) => pid === 777,
    });

    await expect(service.start()).rejects.toThrow(
      "runtime_service_already_running",
    );
  });

  it("reclaims a previous-boot lease even when a recycled PID answers the liveness probe", async () => {
    // Issue #184: a forced power-off leaves runtime-service.lock behind;
    // after the reboot the owner's PID belongs to another process, so a
    // liveness-only check reports "already running" forever and launchd's
    // KeepAlive loops the wrapper. A lease from another boot can never have
    // a living owner, whatever the PID table says.
    const dataDir = await directory("runtime-service-prevboot-");
    await seedLock(dataDir, { pid: 777, boot_id: PREVIOUS_BOOT });
    const service = new RuntimeService({
      dataDir,
      createSupervisor: supervisor,
      pid: 4321,
      processIsAlive: () => true,
      bootId: () => CURRENT_BOOT,
    });

    await service.start();
    expect(await ownerOf(dataDir)).toMatchObject({
      pid: 4321,
      boot_id: CURRENT_BOOT,
    });
    await service.stop();
    expect(await lockEntries(dataDir)).toEqual([]);
  });

  it("refuses a live owner from the current boot", async () => {
    const dataDir = await directory("runtime-service-sameboot-live-");
    await seedLock(dataDir, { pid: 777, boot_id: CURRENT_BOOT });
    const service = new RuntimeService({
      dataDir,
      createSupervisor: supervisor,
      pid: 4321,
      processIsAlive: (pid) => pid === 777,
      bootId: () => CURRENT_BOOT,
    });

    await expect(service.start()).rejects.toThrow(
      "runtime_service_already_running",
    );
    expect(await ownerOf(dataDir)).toMatchObject({ pid: 777 });
  });

  it("reclaims a dead owner from the current boot", async () => {
    const dataDir = await directory("runtime-service-sameboot-dead-");
    await seedLock(dataDir, { pid: 777, boot_id: CURRENT_BOOT });
    const service = new RuntimeService({
      dataDir,
      createSupervisor: supervisor,
      pid: 4321,
      processIsAlive: () => false,
      bootId: () => CURRENT_BOOT,
    });

    await service.start();
    expect(await ownerOf(dataDir)).toMatchObject({ pid: 4321 });
    await service.stop();
  });

  it.each([
    ["the lease predates boot ids", { pid: 777 }, CURRENT_BOOT],
    ["the lease has an empty boot id", { pid: 777, boot_id: "" }, CURRENT_BOOT],
    [
      "the current boot id is unknown",
      { pid: 777, boot_id: PREVIOUS_BOOT },
      null,
    ],
  ])("falls back to the PID probe when %s", async (_case, owner, current) => {
    const dataDir = await directory("runtime-service-unknown-boot-");
    await seedLock(dataDir, owner);
    const live = new RuntimeService({
      dataDir,
      createSupervisor: supervisor,
      pid: 4321,
      processIsAlive: (pid) => pid === 777,
      bootId: () => current,
    });
    await expect(live.start()).rejects.toThrow(
      "runtime_service_already_running",
    );
    expect(await ownerOf(dataDir)).toMatchObject({ pid: 777 });

    const dead = new RuntimeService({
      dataDir,
      createSupervisor: supervisor,
      pid: 4321,
      processIsAlive: () => false,
      bootId: () => current,
    });
    await dead.start();
    expect(await ownerOf(dataDir)).toMatchObject({ pid: 4321 });
    await dead.stop();
  });

  it("records the platform boot id in owner.json", async () => {
    const dataDir = await directory("runtime-service-boot-id-");
    const service = new RuntimeService({
      dataDir,
      createSupervisor: supervisor,
    });

    await service.start();
    const owner = await ownerOf(dataDir);
    if (process.platform === "darwin" || process.platform === "linux") {
      expect(owner.boot_id).toMatch(/^[0-9A-Za-z-]{8,64}$/);
    } else {
      expect(owner.boot_id).toBeNull();
    }
    expect(owner.pid).toBe(process.pid);
    await service.stop();
  });

  it("leaves no lock behind when publishing fails before the rename", async () => {
    // The lock only appears through a rename of a fully written staging
    // directory, so a failure (or power cut) while staging cannot leave a
    // lock without owner.json.
    const dataDir = await directory("runtime-service-publish-failure-");
    const broken = new RuntimeService({
      dataDir,
      createSupervisor: supervisor,
      bootId: () => {
        throw new Error("simulated crash while staging");
      },
    });

    await expect(broken.start()).rejects.toThrow(
      "runtime_service_lock_unavailable",
    );
    expect(await lockEntries(dataDir)).toEqual([]);

    const next = new RuntimeService({
      dataDir,
      createSupervisor: supervisor,
      pid: 4321,
      bootId: () => CURRENT_BOOT,
    });
    await next.start();
    expect(await ownerOf(dataDir)).toMatchObject({ pid: 4321 });
    await next.stop();
  });

  it("clears an owner-less legacy lock created before the current boot", async () => {
    // Older versions created the lock with mkdir and wrote owner.json
    // afterwards; a power cut in between left an empty lock that used to
    // fail as runtime_service_lock_invalid forever.
    const dataDir = await directory("runtime-service-legacy-prevboot-");
    const lock = await seedLock(dataDir);
    const createdAt = (await stat(lock)).mtimeMs;
    const service = new RuntimeService({
      dataDir,
      createSupervisor: supervisor,
      pid: 4321,
      bootId: () => CURRENT_BOOT,
      legacyBootTime: () => createdAt + 60_000,
    });

    await service.start();
    expect(await ownerOf(dataDir)).toMatchObject({ pid: 4321 });
    await service.stop();
  });

  it.each([
    ["it may be from the current boot", (createdAt: number) => createdAt - 1],
    ["the boot time is unknown", () => null],
  ])(
    "keeps refusing an owner-less legacy lock when %s",
    async (_case, boot) => {
      const dataDir = await directory("runtime-service-legacy-sameboot-");
      const lock = await seedLock(dataDir);
      const createdAt = (await stat(lock)).mtimeMs;
      const service = new RuntimeService({
        dataDir,
        createSupervisor: supervisor,
        bootId: () => CURRENT_BOOT,
        legacyBootTime: () => boot(createdAt),
      });

      await expect(service.start()).rejects.toThrow(
        "runtime_service_lock_invalid",
      );
      // rename() may replace an empty directory; the publish step must not.
      expect(await readdir(lock)).toEqual([]);
      expect(await lockEntries(dataDir)).toEqual(["runtime-service.lock"]);
    },
  );

  it("removes staging and cleared-lease debris after acquiring", async () => {
    const dataDir = await directory("runtime-service-debris-");
    const staging = path.join(dataDir, "runtime-service.lock.tmp-crashed");
    await mkdir(staging, { mode: 0o700 });
    await writeFile(
      path.join(staging, "owner.json"),
      JSON.stringify({ pid: 9 }),
    );
    await mkdir(path.join(dataDir, "runtime-service.lock.stale.crashed"), {
      mode: 0o700,
    });
    const service = new RuntimeService({
      dataDir,
      createSupervisor: supervisor,
      pid: 4321,
      bootId: () => CURRENT_BOOT,
    });

    await service.start();
    expect(await lockEntries(dataDir)).toEqual(["runtime-service.lock"]);
    expect(await ownerOf(dataDir)).toMatchObject({ pid: 4321 });
    await service.stop();
    expect(await lockEntries(dataDir)).toEqual([]);
  });

  it("reclaims a stale validated lease", async () => {
    const dataDir = await directory("runtime-service-stale-");
    await seedLock(dataDir, { pid: 777 });
    const service = new RuntimeService({
      dataDir,
      createSupervisor: supervisor,
      pid: 4321,
      processIsAlive: () => false,
    });

    await service.start();
    expect(await ownerOf(dataDir)).toMatchObject({ pid: 4321 });
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
    await symlink(real, link);
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
