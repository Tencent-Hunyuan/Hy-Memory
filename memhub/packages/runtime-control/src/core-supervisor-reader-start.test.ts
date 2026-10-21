import type { ChildProcess, SpawnOptions } from "node:child_process";
import { EventEmitter } from "node:events";
import { describe, expect, it, vi } from "vitest";

import { CoreSupervisor } from "./core-supervisor.js";

function fakeChild(): ChildProcess {
  const child = new EventEmitter() as ChildProcess;
  child.stderr = new EventEmitter() as ChildProcess["stderr"];
  child.stdout = new EventEmitter() as ChildProcess["stdout"];
  child.kill = vi.fn(() => {
    setImmediate(() => child.emit("exit", 0, null));
    return true;
  });
  return child;
}

function fakeReaderChild(): ChildProcess {
  return fakeChild();
}

describe("CoreSupervisor reader startup", () => {
  it("starts the reader after spawning its own Core", async () => {
    const core = fakeChild();
    const reader = fakeReaderChild();
    const spawned: string[][] = [];
    const spawnProcess = vi.fn(
      (_command: string, args: readonly string[], _options: SpawnOptions) => {
        spawned.push([...args]);
        if (args.includes("--reader")) {
          // The Reader attaches its stderr listener synchronously after this
          // call returns, so publish the port on the next tick.
          setImmediate(() =>
            reader.stderr?.emit("data", Buffer.from("reader_port:43210\n")),
          );
          return reader;
        }
        return core;
      },
    );
    const supervisor = new CoreSupervisor({
      launch: { command: "one-memory", args: [] },
      dataDir: "/tmp/one-memory-reader-start-test",
      instanceId: "owned",
      port: 9600,
      attempts: 1,
      delayMs: 0,
      readerEnabled: true,
      spawnProcess,
      probeHealth: async () => ({
        status: "ok",
        service: "one-memory-core",
        version: "0.0.0",
        instance_id: "owned",
      }),
    });

    await supervisor.start();

    expect(spawnProcess).toHaveBeenCalledTimes(2);
    expect(spawned[1]).toContain("--reader");
    expect(supervisor.readerPort).toBe(43210);

    await supervisor.stop();
  });
});
