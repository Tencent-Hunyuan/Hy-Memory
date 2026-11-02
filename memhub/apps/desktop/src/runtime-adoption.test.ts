import type { RuntimeServiceState } from "@one-memory/runtime-control";
import { describe, expect, it, vi } from "vitest";

import {
  compareRuntimeBuild,
  decideRuntimeAdoption,
  type RunningRuntime,
  reconcileRunningRuntime,
} from "./runtime-adoption";

const app = { buildNumber: 38, commit: "abcdef1234567" };
const enabled = (jobPid: number | null): RuntimeServiceState => ({
  state: "enabled",
  reason: "service-enabled",
  jobPid,
});
const disabled: RuntimeServiceState = {
  state: "disabled",
  reason: "service-disabled-by-launchctl",
  jobPid: null,
};
const unknown: RuntimeServiceState = {
  state: "unknown",
  reason: "service-state-timeout",
  jobPid: null,
};
const running = (
  build: RunningRuntime["build"],
  pid = 4321,
): RunningRuntime => ({ pid, build });

function reconcile(options: {
  running: RunningRuntime | null;
  service: RuntimeServiceState;
  installedChanged?: boolean;
}) {
  const stopRuntime = vi.fn(async () => "stopped");
  const queryServiceState = vi.fn(async () => options.service);
  const result = reconcileRunningRuntime({
    app,
    installedChanged: options.installedChanged ?? false,
    findRunning: async () => options.running,
    queryServiceState,
    stopRuntime,
  });
  return { result, stopRuntime, queryServiceState };
}

describe("comparing the running Runtime's build with the App's", () => {
  it("compares build numbers, then commits both sides know", () => {
    expect(compareRuntimeBuild(app, { buildNumber: 38, commit: null })).toBe(
      "same",
    );
    expect(
      compareRuntimeBuild(app, { buildNumber: 38, commit: "abcdef1" }),
    ).toBe("same");
    expect(
      compareRuntimeBuild(app, { buildNumber: 38, commit: "0123456" }),
    ).toBe("app-newer");
    expect(compareRuntimeBuild(app, { buildNumber: 37, commit: null })).toBe(
      "app-newer",
    );
    expect(compareRuntimeBuild(app, { buildNumber: 39, commit: null })).toBe(
      "runtime-newer",
    );
  });

  it("reads a receipt without build fields as a Runtime older than this App", () => {
    expect(compareRuntimeBuild(app, undefined)).toBe("app-newer");
  });

  it("is unknown when either side cannot say", () => {
    expect(compareRuntimeBuild(app, { buildNumber: null, commit: null })).toBe(
      "unknown",
    );
    expect(compareRuntimeBuild({}, { buildNumber: 38, commit: null })).toBe(
      "unknown",
    );
  });
});

describe("App start adopts or replaces a running Runtime", () => {
  it("starts one when nothing runs, without asking the service manager", async () => {
    const { result, stopRuntime, queryServiceState } = reconcile({
      running: null,
      service: enabled(null),
    });
    await expect(result).resolves.toMatchObject({
      decision: { action: "start" },
      adopted: false,
    });
    expect(queryServiceState).not.toHaveBeenCalled();
    expect(stopRuntime).not.toHaveBeenCalled();
  });

  it("adopts the same build instead of starting a second Runtime", async () => {
    const { result, stopRuntime } = reconcile({
      running: running({ buildNumber: 38, commit: null }),
      service: disabled,
    });
    await expect(result).resolves.toEqual({
      decision: { action: "adopt" },
      adopted: true,
      preserveReason: null,
    });
    expect(stopRuntime).not.toHaveBeenCalled();
  });

  it("adopts the service's own job when the service is enabled", async () => {
    const { result, stopRuntime } = reconcile({
      running: running({ buildNumber: 38, commit: null }, 4321),
      service: enabled(4321),
    });
    await expect(result).resolves.toMatchObject({
      decision: { action: "adopt" },
      adopted: true,
    });
    expect(stopRuntime).not.toHaveBeenCalled();
  });

  it("ends with one Runtime when a stray holds the lease beside an enabled service", async () => {
    const { result, stopRuntime } = reconcile({
      running: running({ buildNumber: 38, commit: null }, 777),
      service: enabled(null),
    });
    await expect(result).resolves.toMatchObject({
      decision: { action: "stop-then-start", reason: "stray-runtime" },
      adopted: false,
    });
    expect(stopRuntime).toHaveBeenCalledOnce();
  });

  it("does not treat a Runtime as a stray when the service state is unknown", async () => {
    const { result, stopRuntime } = reconcile({
      running: running({ buildNumber: 38, commit: null }, 777),
      service: unknown,
    });
    await expect(result).resolves.toMatchObject({
      decision: { action: "adopt" },
    });
    expect(stopRuntime).not.toHaveBeenCalled();
  });

  it("replaces an older Runtime that is the service's job through the service manager", async () => {
    const { result, stopRuntime } = reconcile({
      running: running({ buildNumber: 37, commit: null }, 4321),
      service: enabled(4321),
    });
    await expect(result).resolves.toMatchObject({
      decision: { action: "replace-via-service" },
      adopted: false,
      preserveReason: null,
    });
    // No plain kill: launchd would answer it with a respawn of the old job.
    expect(stopRuntime).not.toHaveBeenCalled();
  });

  it("replaces a legacy service job whose receipt has no build through launchd", async () => {
    const { result, stopRuntime } = reconcile({
      running: running(undefined, 4321),
      service: enabled(4321),
    });
    await expect(result).resolves.toMatchObject({
      decision: { action: "replace-via-service" },
    });
    expect(stopRuntime).not.toHaveBeenCalled();
  });

  it("stops an older transient or CLI-started Runtime, then starts the right one", async () => {
    const { result, stopRuntime } = reconcile({
      running: running({ buildNumber: 36, commit: null }, 999),
      service: disabled,
    });
    await expect(result).resolves.toMatchObject({
      decision: { action: "stop-then-start", reason: "app-newer" },
      adopted: false,
    });
    expect(stopRuntime).toHaveBeenCalledOnce();
  });

  it("replaces a same-build Runtime when this launch moved `current`", () => {
    expect(
      decideRuntimeAdoption({
        running: running({ buildNumber: 38, commit: null }, 4321),
        app,
        service: enabled(4321),
        installedChanged: true,
      }),
    ).toEqual({ action: "replace-via-service" });
  });

  it("adopts a newer Runtime silently and leaves quit to the service state", async () => {
    const { result, stopRuntime } = reconcile({
      running: running({ buildNumber: 40, commit: null }, 999),
      service: disabled,
    });
    // No special quit pass: with the service disabled, quit stops it like any
    // adopted Runtime; with it enabled, quit preserves it.
    await expect(result).resolves.toEqual({
      decision: { action: "adopt-newer" },
      adopted: true,
      preserveReason: null,
    });
    expect(stopRuntime).not.toHaveBeenCalled();
  });

  it("never stops or downgrades a newer Runtime at startup, even beside an enabled service", async () => {
    const { result, stopRuntime } = reconcile({
      running: running({ buildNumber: 40, commit: null }, 999),
      service: enabled(4321),
      installedChanged: true,
    });
    await expect(result).resolves.toMatchObject({
      decision: { action: "adopt-newer" },
      adopted: true,
    });
    expect(stopRuntime).not.toHaveBeenCalled();
  });

  it("never kills a Runtime whose build cannot be read", async () => {
    const { result, stopRuntime } = reconcile({
      running: running({ buildNumber: null, commit: null }, 999),
      service: disabled,
      installedChanged: true,
    });
    await expect(result).resolves.toEqual({
      decision: { action: "adopt-unverified" },
      adopted: true,
      preserveReason: "runtime-build-unknown",
    });
    expect(stopRuntime).not.toHaveBeenCalled();
  });
});
