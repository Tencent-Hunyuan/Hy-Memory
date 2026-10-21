import { expect, it, vi } from "vitest";
import {
  type RuntimeMaintenanceDependencies,
  withRuntimeMaintenanceFence,
} from "./runtime-maintenance.js";

function dependencies(enabled: boolean) {
  const events: string[] = [];
  const value: RuntimeMaintenanceDependencies = {
    serviceEnabled: vi.fn(async () => enabled),
    disableService: vi.fn(async () => void events.push("disable")),
    enableService: vi.fn(async () => void events.push("enable")),
    stopRuntime: vi.fn(async (): Promise<"stopped"> => {
      events.push("stop");
      return "stopped";
    }),
  };
  return { events, value };
}

it("fences managed restart before stopping Runtime and restores it afterwards", async () => {
  const { events, value } = dependencies(true);

  const result = await withRuntimeMaintenanceFence(
    "/data",
    async () => {
      events.push("migrate");
      return "done";
    },
    value,
  );

  expect(result).toBe("done");
  expect(events).toEqual(["disable", "stop", "migrate", "enable"]);
});

it("restores the prior enable marker when offline maintenance fails", async () => {
  const { events, value } = dependencies(true);

  await expect(
    withRuntimeMaintenanceFence(
      "/data",
      async () => {
        events.push("migrate");
        throw new Error("migration failed");
      },
      value,
    ),
  ).rejects.toThrow("migration failed");

  expect(events).toEqual(["disable", "stop", "migrate", "enable"]);
});

it("does not invent an enable marker for a manually stopped Runtime", async () => {
  const { events, value } = dependencies(false);

  await withRuntimeMaintenanceFence(
    "/data",
    async () => void events.push("migrate"),
    value,
  );

  expect(events).toEqual(["stop", "migrate"]);
});
