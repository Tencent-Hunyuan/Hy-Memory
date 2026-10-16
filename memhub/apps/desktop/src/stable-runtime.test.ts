import {
  mkdir,
  mkdtemp,
  readFile,
  readlink,
  writeFile,
} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";

import {
  ensureStableRuntimeThenRepair,
  installBundledCliIfNeeded,
  isDisabledServiceInstallError,
  isInstalledApplicationsApp,
  stableRuntimeErrorCode,
  startStableRuntimeRepair,
} from "./stable-runtime";

it("only treats a packaged macOS executable under Applications as installed", () => {
  expect(
    isInstalledApplicationsApp(
      "/Applications/MemHub.app/Contents/MacOS/MemHub",
      "darwin",
    ),
  ).toBe(true);
  expect(
    isInstalledApplicationsApp(
      "/Volumes/MemHub/MemHub.app/Contents/MacOS/MemHub",
      "darwin",
    ),
  ).toBe(false);
  expect(
    isInstalledApplicationsApp(
      "/private/tmp/MemHub.app/Contents/MacOS/MemHub",
      "darwin",
    ),
  ).toBe(false);
});

it("installs only memhub and adds its directory to a new shell", async () => {
  if (process.platform === "win32") return;
  const home = await mkdtemp(path.join(os.tmpdir(), "memhub-auto-cli-"));
  const installRoot = path.join(
    home,
    "Library",
    "Application Support",
    "One Memory",
  );
  const target = path.join(installRoot, "current", "bin", "one-memory");
  await mkdir(path.dirname(target), { recursive: true });
  await writeFile(target, "#!/bin/sh\n", { mode: 0o700 });

  await installBundledCliIfNeeded({ home, installRoot, platform: "darwin" });
  await installBundledCliIfNeeded({ home, installRoot, platform: "darwin" });

  expect(await readlink(path.join(home, ".local", "bin", "memhub"))).toBe(
    target,
  );
  const profile = await readFile(path.join(home, ".zprofile"), "utf8");
  expect(profile.match(/# >>> memhub PATH >>>/g)).toHaveLength(1);
  await expect(
    readFile(path.join(home, ".local", "bin", "one-memory")),
  ).rejects.toThrow();
});

describe("ensureStableRuntimeThenRepair", () => {
  it("does not hold first-window startup while the stable install is blocked", async () => {
    let releaseInstall!: () => void;
    const installBlocked = new Promise<void>((resolve) => {
      releaseInstall = resolve;
    });
    const repair = vi.fn(async () => undefined);

    let settled = false;
    const background = startStableRuntimeRepair({
      packaged: true,
      preview: false,
      install: () => installBlocked,
      repair,
    }).then(() => {
      settled = true;
    });

    await Promise.resolve();
    expect(settled).toBe(false);
    expect(repair).not.toHaveBeenCalled();
    releaseInstall();
    await background;
    expect(repair).toHaveBeenCalledOnce();
  });

  it("installs the stable runtime before rewriting hosts", async () => {
    const order: string[] = [];
    await ensureStableRuntimeThenRepair({
      packaged: true,
      preview: false,
      install: async () => {
        order.push("install");
      },
      repair: async () => {
        order.push("repair");
      },
    });
    expect(order).toEqual(["install", "repair"]);
  });

  it("does not rewrite hosts when the stable install fails", async () => {
    const repair = vi.fn(async () => undefined);
    await expect(
      ensureStableRuntimeThenRepair({
        packaged: true,
        preview: false,
        install: async () => {
          throw new Error("packaged_runtime_incomplete");
        },
        repair,
      }),
    ).rejects.toThrow("packaged_runtime_incomplete");
    expect(repair).not.toHaveBeenCalled();
  });

  it("leaves development hosts on the paths they already have", async () => {
    const install = vi.fn(async () => undefined);
    const repair = vi.fn(async () => undefined);
    await ensureStableRuntimeThenRepair({
      packaged: false,
      preview: false,
      install,
      repair,
    });
    expect(install).not.toHaveBeenCalled();
    expect(repair).not.toHaveBeenCalled();
  });

  it("does not replace the daily install when this bundle is a preview", async () => {
    const install = vi.fn(async () => undefined);
    const repair = vi.fn(async () => undefined);
    await ensureStableRuntimeThenRepair({
      packaged: true,
      preview: true,
      install,
      repair,
    });
    expect(install).not.toHaveBeenCalled();
    expect(repair).toHaveBeenCalledOnce();
  });

  it("reports only allowlisted stable installer codes", () => {
    expect(
      stableRuntimeErrorCode(new Error("installed_version_conflict")),
    ).toBe("installed_version_conflict");
    expect(
      stableRuntimeErrorCode(new Error("secret=/Users/person/private-token")),
    ).toBe("desktop_start_failed");
    expect(stableRuntimeErrorCode("installed_version_conflict")).toBe(
      "desktop_start_failed",
    );
  });
});

describe("isDisabledServiceInstallError", () => {
  it("recognises the durable disabled-service refusal", () => {
    expect(
      isDisabledServiceInstallError(
        new Error("runtime_service_disabled_by_system"),
      ),
    ).toBe(true);
  });

  it("keeps every other install failure fatal", () => {
    for (const other of [
      new Error("runtime_service_label_conflict"),
      new Error("runtime_service_install_root_not_ready"),
      new Error("runtime_service_command_failed"),
      new Error("runtime_service_disabled_by_system" + " "),
      "runtime_service_disabled_by_system",
      null,
      undefined,
    ]) {
      expect(isDisabledServiceInstallError(other)).toBe(false);
    }
  });
});
