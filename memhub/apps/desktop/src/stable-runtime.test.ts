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
  installServiceWithDisabledFallback,
  isDisabledServiceInstallError,
  isInstalledApplicationsApp,
  requireCurrentRuntimeAtLeast,
  stableRuntimeErrorCode,
  stableRuntimeFailureDetail,
  stableRuntimeUserMessage,
  startStableRuntimeRepair,
  startupFailureCause,
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
  it("recognises the durable disabled-service state", () => {
    expect(
      isDisabledServiceInstallError(
        new Error("runtime_service_disabled_by_system"),
      ),
    ).toBe(true);
  });

  it("keeps every other startup failure fatal", () => {
    for (const other of [
      new Error("runtime_service_label_conflict"),
      new Error("runtime_service_install_root_not_ready"),
      new Error("runtime_service_command_failed"),
      "runtime_service_disabled_by_system",
      null,
      undefined,
    ]) {
      expect(isDisabledServiceInstallError(other)).toBe(false);
    }
  });
});

describe("installServiceWithDisabledFallback", () => {
  it("keeps a successfully installed service managed", async () => {
    const restoreOwner = vi.fn(async () => undefined);
    const reportDisabled = vi.fn(async () => undefined);
    const startTransient = vi.fn(async () => undefined);

    await expect(
      installServiceWithDisabledFallback({
        install: async () => undefined,
        restoreOwner,
        reportDisabled,
        startTransient,
      }),
    ).resolves.toBe("managed");
    expect(restoreOwner).not.toHaveBeenCalled();
    expect(reportDisabled).not.toHaveBeenCalled();
    expect(startTransient).not.toHaveBeenCalled();
  });

  it("uses an app-lifetime Runtime when launchd disabled the service", async () => {
    const order: string[] = [];

    await expect(
      installServiceWithDisabledFallback({
        install: async () => {
          order.push("install");
          throw new Error("runtime_service_disabled_by_system");
        },
        restoreOwner: async () => {
          order.push("restore-owner");
        },
        reportDisabled: async () => {
          order.push("report-start");
          await Promise.resolve();
          order.push("report-end");
        },
        startTransient: async () => {
          order.push("start-transient");
        },
      }),
    ).resolves.toBe("transient");
    expect(order).toEqual([
      "install",
      "restore-owner",
      "report-start",
      "report-end",
      "start-transient",
    ]);
  });

  it("restores ownership and preserves unrelated startup failures", async () => {
    const restoreOwner = vi.fn(async () => undefined);
    const reportDisabled = vi.fn(async () => undefined);
    const startTransient = vi.fn(async () => undefined);

    await expect(
      installServiceWithDisabledFallback({
        install: async () => {
          throw new Error("runtime_service_command_failed");
        },
        restoreOwner,
        reportDisabled,
        startTransient,
      }),
    ).rejects.toThrow("runtime_service_command_failed");
    expect(restoreOwner).toHaveBeenCalledOnce();
    expect(reportDisabled).not.toHaveBeenCalled();
    expect(startTransient).not.toHaveBeenCalled();
  });
});

describe("startup install errors", () => {
  it("explains the failure to the user instead of showing an error code", () => {
    const conflict = stableRuntimeUserMessage(
      new Error("installed_version_conflict"),
    );
    expect(conflict).toContain("记忆数据");
    expect(conflict).not.toContain("installed_version_conflict");
    expect(
      stableRuntimeUserMessage(new Error("distribution_downgrade_refused")),
    ).toContain("更新");
    // An unrecognised failure still gets a sentence, never a raw code.
    expect(
      stableRuntimeUserMessage(new Error("something_unexpected")),
    ).toContain("MemHub 启动失败");
    expect(stableRuntimeUserMessage("not an error")).toContain(
      "MemHub 启动失败",
    );
  });

  it("says why a data upgrade failed, and what the person can do", () => {
    const noSpace = stableRuntimeUserMessage(
      new Error("sdk_migration_disk_space_insufficient"),
    );
    // The only failure here the person fixes themselves: name the action.
    expect(noSpace).toContain("磁盘空间不足");
    expect(noSpace).toContain("重新启动");
    expect(noSpace).toContain("不会丢失");
    expect(noSpace).not.toContain("sdk_migration_disk_space_insufficient");

    const unreadable = stableRuntimeUserMessage(
      new Error("sdk_migration_memory_unreadable"),
    );
    expect(unreadable).toContain("没有切换数据源");
    expect(unreadable).toContain("不会丢失");
  });
});

describe("startup dialog body", () => {
  it("keeps a code we have no copy for, so support can see which failure fired", () => {
    expect(stableRuntimeFailureDetail(new Error("page_load_failed"))).toBe(
      "page_load_failed",
    );
    expect(
      stableRuntimeFailureDetail(new Error("runtime_broker_unavailable")),
    ).toBe("runtime_broker_unavailable");
  });

  it("shows the sentence we wrote for a migration failure", () => {
    const detail = stableRuntimeFailureDetail(
      new Error("sdk_migration_disk_space_insufficient"),
    );
    expect(detail).toContain("磁盘空间不足");
    expect(detail).not.toContain("sdk_migration_disk_space_insufficient");
  });

  it("turns runtime_service_not_ready into a user-facing sentence", () => {
    const error = new Error("runtime_service_not_ready");
    expect(stableRuntimeErrorCode(error)).toBe("runtime_service_not_ready");
    expect(stableRuntimeFailureDetail(error)).toContain("首次启动");
    expect(stableRuntimeFailureDetail(error)).not.toContain(
      "runtime_service_not_ready",
    );
  });
});

describe("packaged runtime build after an App upgrade", () => {
  it("refuses to run on a Runtime older than the App, or none", async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), "memhub-current-"));
    await expect(requireCurrentRuntimeAtLeast(root, 21)).rejects.toThrow(
      "packaged_runtime_not_current",
    );
    await mkdir(path.join(root, "current"));
    const manifest = path.join(root, "current", "manifest.json");
    await writeFile(manifest, JSON.stringify({ build_number: 19 }));
    await expect(requireCurrentRuntimeAtLeast(root, 21)).rejects.toThrow(
      "packaged_runtime_not_current",
    );
    await writeFile(manifest, JSON.stringify({ build_number: 21 }));
    await expect(
      requireCurrentRuntimeAtLeast(root, 21),
    ).resolves.toBeUndefined();
    await writeFile(manifest, JSON.stringify({ build_number: 22 }));
    await expect(
      requireCurrentRuntimeAtLeast(root, 21),
    ).resolves.toBeUndefined();
    await expect(
      requireCurrentRuntimeAtLeast(root, undefined),
    ).resolves.toBeUndefined();
    expect(
      stableRuntimeUserMessage(new Error("packaged_runtime_not_current")),
    ).toContain("重新安装");
  });

  it("keeps the cause of a startup failure, without the home path", () => {
    const cause = startupFailureCause(
      new Error("ENOENT: no such file /Users/someone/Library/x\nmore"),
      "/Users/someone",
    );
    expect(cause).toBe("Error: ENOENT: no such file ~/Library/x more");
    expect(startupFailureCause(new Error("x".repeat(500)), "/h").length).toBe(
      300,
    );
  });
});
