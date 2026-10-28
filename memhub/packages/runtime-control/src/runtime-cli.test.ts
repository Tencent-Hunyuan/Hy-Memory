import { createHash } from "node:crypto";
import { existsSync, mkdtempSync, readFileSync } from "node:fs";
import {
  chmod,
  copyFile,
  mkdir,
  mkdtemp,
  readFile,
  realpath,
  symlink,
  unlink,
  writeFile,
} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";
import type { HostPermissions } from "./host-permissions.js";
import {
  cleanupRemovedAppOwnership,
  DEFAULT_CORE_PORT,
  guardedPermissions,
  isIsolatedHourlyRuntime,
  resolveCorePort,
  resolveRuntimeCliConfig,
  revalidateHostRuntime,
  runRuntimeCli,
  waitForAppRemoval,
} from "./runtime-cli.js";
import {
  claimRuntimeServiceForApp,
  claimRuntimeServiceForCli,
  enableRuntimeService,
  runtimeServiceEnabled,
  runtimeServiceEnableMarkerPath,
} from "./service-ownership.js";
import {
  defaultManagedInstallRoot,
  planUserService,
  type RuntimePlatform,
  runtimeInstallLayout,
} from "./service-plan.js";

describe("package build identity", () => {
  it("carries the launcher's build/package/round identity into the config", () => {
    const config = resolveRuntimeCliConfig({
      ONE_MEMORY_HOST_HOME: "/tmp/host",
      ONE_MEMORY_VERSION: "0.1.0-internal.1",
      ONE_MEMORY_BUILD_ID: "One-Memory-H85-6929cbd-candidate",
      ONE_MEMORY_PACKAGE_ID: "One-Memory-H85-6929cbd-candidate",
      ONE_MEMORY_ROUND: "85",
      ONE_MEMORY_BUILD_NUMBER: "3904",
      ONE_MEMORY_COMMIT: "388fd0e9",
    });
    expect(config.buildIdentity).toEqual({
      version: "0.1.0-internal.1",
      buildId: "One-Memory-H85-6929cbd-candidate",
      packageId: "One-Memory-H85-6929cbd-candidate",
      round: "85",
      buildNumber: "3904",
      commit: "388fd0e9",
    });
  });

  it("leaves the identity undefined for a source run instead of inventing one", () => {
    const config = resolveRuntimeCliConfig({
      ONE_MEMORY_HOST_HOME: "/tmp/host",
    });
    expect(config.buildIdentity).toBeUndefined();
  });
});

describe("App removal guard", () => {
  it("disables an App-owned service before Runtime startup", async () => {
    const dataDir = await mkdtemp(path.join(os.tmpdir(), "memhub-app-guard-"));
    await claimRuntimeServiceForApp({
      dataDir,
      appBundle: "/Applications/MemHub.app",
      appExecutable: "/Applications/MemHub.app/Contents/MacOS/MemHub",
    });
    await enableRuntimeService(dataDir);

    await expect(
      cleanupRemovedAppOwnership({
        dataDir,
        shouldStop: async () => true,
      }),
    ).resolves.toBe(true);
    await expect(runtimeServiceEnabled(dataDir)).resolves.toBe(false);
  });

  it("does not stop a CLI-owned service when the App is absent", async () => {
    const dataDir = await mkdtemp(path.join(os.tmpdir(), "memhub-app-guard-"));
    await claimRuntimeServiceForCli(dataDir);
    await enableRuntimeService(dataDir);

    await expect(
      cleanupRemovedAppOwnership({
        dataDir,
        shouldStop: async () => true,
      }),
    ).resolves.toBe(false);
    await expect(runtimeServiceEnabled(dataDir)).resolves.toBe(true);
    expect(runtimeServiceEnableMarkerPath(dataDir)).toContain(dataDir);
  });

  it("cancels the App-removal poll when another shutdown trigger wins", async () => {
    const dataDir = await mkdtemp(path.join(os.tmpdir(), "memhub-app-guard-"));
    const shouldStop = vi.fn(async () => false);
    const controller = new AbortController();
    const waiting = waitForAppRemoval(
      { dataDir, shouldStop, pollIntervalMs: 60_000 },
      controller.signal,
    );

    controller.abort();

    await expect(waiting).resolves.toBeUndefined();
    expect(shouldStop).not.toHaveBeenCalled();
  });
});

describe("host runtime attestation", () => {
  const attestation = {
    schemaVersion: 1,
    managedInstallRoot: "/opt/one-memory",
    selectedVersionRoot: "/opt/one-memory/versions/0.0.0-b3758-darwin-arm64",
    selectedRelativeTarget: "versions/0.0.0-b3758-darwin-arm64",
    appMarker: "One-Memory-HAPP1R5.20-f154da45-c7-isolated",
    manifestPackageId: "One-Memory-APP1R5.20-f154da45-candidate",
    buildNumber: 3758,
    platform: "darwin",
    arch: "arm64",
    manifestSha256: "a".repeat(64),
    componentDigests: Object.fromEntries(
      [
        "core/one-memory",
        "runtime/runtime.cjs",
        "host-bridge/mcp.cjs",
        "host-bridge/recall-cli.cjs",
        "bin/one-memory.cjs",
        "web/index.html",
        "host-bridge/sync-hook/runtime.mjs",
      ].map((key) => [key, "b".repeat(64)]),
    ),
    runtimeCommand:
      "/opt/one-memory/versions/0.0.0-b3758-darwin-arm64/bin/node",
    mcpEntryPath:
      "/opt/one-memory/versions/0.0.0-b3758-darwin-arm64/host-bridge/mcp.cjs",
    recallCliPath:
      "/opt/one-memory/versions/0.0.0-b3758-darwin-arm64/host-bridge/recall-cli.cjs",
    captureCommand:
      "/opt/one-memory/versions/0.0.0-b3758-darwin-arm64/core/one-memory",
    cliEntryPath:
      "/opt/one-memory/versions/0.0.0-b3758-darwin-arm64/bin/one-memory.cjs",
    webDirectory: "/opt/one-memory/versions/0.0.0-b3758-darwin-arm64/web",
    syncHookAssetsDirectory:
      "/opt/one-memory/versions/0.0.0-b3758-darwin-arm64/host-bridge/sync-hook",
  };

  it("parses only the canonical bounded attestation", () => {
    expect(
      resolveRuntimeCliConfig({
        ONE_MEMORY_HOST_HOME: "/tmp/host",
        ONE_MEMORY_HOST_RUNTIME_ATTESTATION: JSON.stringify(attestation),
      }).hostRuntime,
    ).toEqual(attestation);
    expect(() =>
      resolveRuntimeCliConfig({
        ONE_MEMORY_HOST_HOME: "/tmp/host",
        ONE_MEMORY_HOST_RUNTIME_ATTESTATION: JSON.stringify({
          ...attestation,
          extra: true,
        }),
      }),
    ).toThrow("host_runtime_attestation_invalid");
    expect(() =>
      resolveRuntimeCliConfig({
        ONE_MEMORY_HOST_HOME: "/tmp/host",
        ONE_MEMORY_HOST_RUNTIME_ATTESTATION: ` ${JSON.stringify(attestation)}`,
      }),
    ).toThrow("host_runtime_attestation_invalid");
    for (const managedInstallRoot of [
      "relative",
      "/opt/one-memory/../foreign",
      "/opt/one-memory\n",
    ]) {
      expect(() =>
        resolveRuntimeCliConfig({
          ONE_MEMORY_HOST_HOME: "/tmp/host",
          ONE_MEMORY_HOST_RUNTIME_ATTESTATION: JSON.stringify({
            ...attestation,
            managedInstallRoot,
          }),
        }),
      ).toThrow("host_runtime_attestation_invalid");
    }
  });

  it("keeps isolated host config separate from managed runtime authority", () => {
    const config = resolveRuntimeCliConfig({
      ONE_MEMORY_HOST_HOME: "/tmp/isolated-host",
      ONE_MEMORY_HOST_RUNTIME_ATTESTATION: JSON.stringify(attestation),
    });
    expect(config.hostHome).toBe("/tmp/isolated-host");
    expect(config.hostRuntime?.managedInstallRoot).toBe("/opt/one-memory");
    expect(config.hostRuntime?.selectedVersionRoot).toBe(
      "/opt/one-memory/versions/0.0.0-b3758-darwin-arm64",
    );
  });

  it("uses the attested capture command for host hook generation", () => {
    const source = readFileSync(
      new URL("./runtime-cli.ts", import.meta.url),
      "utf8",
    );
    const start = source.indexOf("const recallRuntime = {");
    const end = source.indexOf("\n      };", start);
    const body = source.slice(start, end);
    expect(body).toMatch(
      /captureCommand:\s+config\.hostRuntime\?\.captureCommand \?\? config\.coreCommand/,
    );
    expect(body).not.toContain("captureCommand: config.coreCommand,");
  });

  it.each([
    "repairInstalledCommand",
    "repairInstalledCommands",
    "syncStaleInstalls",
    "refreshInstalledHosts",
    "previewInstall",
    "install",
    "setPermission",
    "revokePermission",
    "previewSkillSync",
    "applySkillSync",
    "previewSyncHook",
    "applySyncHook",
  ])("guards %s before dispatch", async (method) => {
    const effect = vi.fn();
    const owner = { [method]: effect } as unknown as HostPermissions;
    const guarded = guardedPermissions(
      owner,
      async () => {
        throw new Error("host_runtime_current_changed");
      },
      "a".repeat(64),
    ) as unknown as Record<string, (...args: unknown[]) => Promise<unknown>>;
    await expect(guarded[method]?.({}, "a".repeat(64))).rejects.toThrow(
      "host_runtime_current_changed",
    );
    expect(effect).not.toHaveBeenCalled();
  });

  async function managedRuntimeFixture() {
    const platform: RuntimePlatform =
      process.platform === "darwin"
        ? "darwin"
        : process.platform === "win32"
          ? "win32"
          : "linux";
    const versionDirectory = `0.0.0-b3758-${platform}-${process.arch}`;
    const managedHome = await mkdtemp(
      path.join(os.tmpdir(), "om-managed-authority-"),
    );
    const hostHome = await mkdtemp(path.join(os.tmpdir(), "om-host-config-"));
    const managedInstallRoot = path.join(
      managedHome,
      "Library/Application Support/One Memory",
    );
    const selected = path.join(
      managedInstallRoot,
      `versions/${versionDirectory}`,
    );
    const files: Record<string, string> = {};
    for (const relative of [
      "core/one-memory",
      "runtime/runtime.cjs",
      "host-bridge/mcp.cjs",
      "host-bridge/recall-cli.cjs",
      "bin/one-memory.cjs",
      "web/index.html",
      "host-bridge/sync-hook/runtime.mjs",
      "bin/node",
    ]) {
      const target = path.join(selected, relative);
      await mkdir(path.dirname(target), { recursive: true, mode: 0o700 });
      if (relative === "core/one-memory") {
        await copyFile("/usr/bin/true", target);
      } else if (relative === "bin/node") {
        await copyFile(process.execPath, target);
      } else {
        await writeFile(target, `fixture:${relative}`, { mode: 0o600 });
      }
      if (relative === "core/one-memory" || relative === "bin/node")
        await chmod(target, 0o700);
      const body = await readFile(target);
      files[relative] = createHash("sha256").update(body).digest("hex");
    }
    const manifest = {
      schema_version: 1,
      product: "one-memory-runtime",
      version: "0.0.0",
      build_number: 3758,
      package_id: "One-Memory-APP1R5.20-f154da45-candidate",
      platform,
      arch: process.arch,
      files,
    };
    const manifestBody = `${JSON.stringify(manifest)}\n`;
    await writeFile(path.join(selected, "manifest.json"), manifestBody, {
      mode: 0o600,
    });
    await symlink(
      `versions/${versionDirectory}`,
      path.join(managedInstallRoot, "current"),
    );
    const selectedVersionRoot = await realpath(selected);
    const componentDigests = Object.fromEntries(
      Object.entries(files).filter(([relative]) => relative !== "bin/node"),
    );
    return {
      hostHome,
      managedInstallRoot: await realpath(managedInstallRoot),
      selectedVersionRoot,
      attestation: {
        schemaVersion: 1 as const,
        managedInstallRoot: await realpath(managedInstallRoot),
        selectedVersionRoot,
        selectedRelativeTarget: `versions/${versionDirectory}`,
        appMarker: "One-Memory-HAPP1R5.20-f154da45-c7-isolated",
        manifestPackageId: "One-Memory-APP1R5.20-f154da45-candidate",
        buildNumber: 3758,
        platform,
        arch: process.arch,
        manifestSha256: createHash("sha256").update(manifestBody).digest("hex"),
        componentDigests,
        runtimeCommand: path.join(selectedVersionRoot, "bin/node"),
        mcpEntryPath: path.join(selectedVersionRoot, "host-bridge/mcp.cjs"),
        recallCliPath: path.join(
          selectedVersionRoot,
          "host-bridge/recall-cli.cjs",
        ),
        captureCommand: path.join(selectedVersionRoot, "core/one-memory"),
        cliEntryPath: path.join(selectedVersionRoot, "bin/one-memory.cjs"),
        webDirectory: path.join(selectedVersionRoot, "web"),
        syncHookAssetsDirectory: path.join(
          selectedVersionRoot,
          "host-bridge/sync-hook",
        ),
      },
    };
  }

  it("revalidates a real managed root independently of isolated hostHome", async () => {
    const fixture = await managedRuntimeFixture();
    const config = resolveRuntimeCliConfig({
      ONE_MEMORY_HOST_HOME: fixture.hostHome,
      ONE_MEMORY_HOST_RUNTIME_ATTESTATION: JSON.stringify(fixture.attestation),
    });
    await expect(revalidateHostRuntime(config)).resolves.toBeUndefined();

    const previewInstall = vi
      .fn()
      .mockResolvedValue({ fingerprint: "c".repeat(64), files: [] });
    const install = vi.fn().mockResolvedValue({ installed: true });
    const guarded = guardedPermissions(
      { previewInstall, install } as unknown as HostPermissions,
      () => revalidateHostRuntime(config),
      fixture.attestation.manifestSha256,
    );
    const preview = await guarded.previewInstall({} as never);
    expect(preview).toMatchObject({
      files: [],
    });
    await expect(
      guarded.install({} as never, preview.fingerprint),
    ).resolves.toEqual({ installed: true });
    expect(previewInstall).toHaveBeenCalledTimes(2);
    expect(install).toHaveBeenCalledOnce();
  });

  it("refuses a current flip before invoking the guarded owner", async () => {
    const fixture = await managedRuntimeFixture();
    const config = resolveRuntimeCliConfig({
      ONE_MEMORY_HOST_HOME: fixture.hostHome,
      ONE_MEMORY_HOST_RUNTIME_ATTESTATION: JSON.stringify(fixture.attestation),
    });
    await unlink(path.join(fixture.managedInstallRoot, "current"));
    await symlink(
      "versions/foreign",
      path.join(fixture.managedInstallRoot, "current"),
    );
    const previewInstall = vi.fn();
    const guarded = guardedPermissions(
      { previewInstall } as unknown as HostPermissions,
      () => revalidateHostRuntime(config),
      fixture.attestation.manifestSha256,
    );
    await expect(guarded.previewInstall({} as never)).rejects.toThrow(
      "host_runtime_current_changed",
    );
    expect(previewInstall).not.toHaveBeenCalled();
  });

  it("refuses a selected version outside the attested managed root", async () => {
    const fixture = await managedRuntimeFixture();
    const foreignRoot = await mkdtemp(path.join(os.tmpdir(), "om-foreign-"));
    const config = resolveRuntimeCliConfig({
      ONE_MEMORY_HOST_HOME: fixture.hostHome,
      ONE_MEMORY_HOST_RUNTIME_ATTESTATION: JSON.stringify({
        ...fixture.attestation,
        managedInstallRoot: foreignRoot,
      }),
    });
    const previewInstall = vi.fn();
    const guarded = guardedPermissions(
      { previewInstall } as unknown as HostPermissions,
      () => revalidateHostRuntime(config),
      fixture.attestation.manifestSha256,
    );
    await expect(guarded.previewInstall({} as never)).rejects.toThrow(
      "host_runtime_current_unsafe",
    );
    expect(previewInstall).not.toHaveBeenCalled();
  });
});

describe("standalone CLI must not default an hourly package to the daily root", () => {
  const HOURLY = {
    ONE_MEMORY_HOST_HOME: "/tmp/host",
    ONE_MEMORY_HOURLY_INSTANCE_ID: "1d3b0c1c-59d1-44c3-9e3b-24e5ecfa1b19",
    ONE_MEMORY_HOURLY_PACKAGE_ID: "One-Memory-HAPP1R5-5915c5b-c7-isolated",
  };

  it("resolves the hourly data root from the markers when ONE_MEMORY_DATA_DIR is absent", () => {
    // EXEC-1914-CLI-STANDALONE-DAILY-DEFAULT: a packaged CLI carries the hourly
    // markers but not the plist's ONE_MEMORY_DATA_DIR. Reading `~/.one-memory`
    // would silently manage the user's daily database.
    const config = resolveRuntimeCliConfig(
      { ...HOURLY },
      "/managed/runtime/runtime.cjs",
    );
    expect(config.dataDir).toBe(
      path.join(
        os.homedir(),
        ".one-memory-hourly",
        "One-Memory-HAPP1R5-5915c5b-c7-isolated",
      ),
    );
    expect(config.dataDir).not.toBe(path.join(os.homedir(), ".one-memory"));
  });

  it("still resolves the daily root when the hourly markers are absent", () => {
    const config = resolveRuntimeCliConfig(
      { ONE_MEMORY_HOST_HOME: "/tmp/host" },
      "/managed/runtime/runtime.cjs",
    );
    expect(config.dataDir).toBe(path.join(os.homedir(), ".one-memory"));
  });

  it("lets an explicit ONE_MEMORY_DATA_DIR override the markers", () => {
    const config = resolveRuntimeCliConfig(
      { ...HOURLY, ONE_MEMORY_DATA_DIR: "/tmp/exact" },
      "/managed/runtime/runtime.cjs",
    );
    expect(config.dataDir).toBe("/tmp/exact");
  });

  it("refuses a partial marker pair instead of silently using daily", () => {
    const config = resolveRuntimeCliConfig(
      {
        ONE_MEMORY_HOST_HOME: "/tmp/host",
        // instance id only: not a valid isolated pair
        ONE_MEMORY_HOURLY_INSTANCE_ID: "1d3b0c1c-59d1-44c3-9e3b-24e5ecfa1b19",
      },
      "/managed/runtime/runtime.cjs",
    );
    expect(config.dataDir).toBe(path.join(os.homedir(), ".one-memory"));
  });
});

function captured(): { text: () => string; restore: () => void } {
  let text = "";
  const original = process.stdout.write;
  process.stdout.write = ((value: string) => {
    text += value;
    return true;
  }) as typeof original;
  return {
    text: () => text,
    restore: () => {
      process.stdout.write = original;
    },
  };
}

it("pins the packaged Runtime to the renderer's fixed Core port", () => {
  const source = readFileSync(
    new URL("./runtime-cli.ts", import.meta.url),
    "utf8",
  );
  const start = source.indexOf("export async function createRuntimeService");
  const end = source.indexOf("\n}", start);
  const body = source.slice(start, end);
  // The everyday path must keep the renderer's fixed port as the default and
  // must not silently start probing free ports (that would desync the renderer,
  // which hardcodes 8765). The exact default now comes from `resolveCorePort`,
  // which returns DEFAULT_CORE_PORT unless an ISOLATED hour opts in.
  expect(body).toContain("resolveCorePort(");
  expect(body).not.toContain("findAvailablePort");
  expect(DEFAULT_CORE_PORT).toBe(8765);
});

describe("isolated-hour Core port override", () => {
  const isolatedEnv = {
    ONE_MEMORY_HOURLY_INSTANCE_ID: "19a77cd4-a6ca-4c1b-b503-a1c4f97c3cb4",
    ONE_MEMORY_HOURLY_PACKAGE_ID: "One-Memory-H08-10e78f9-c7-isolated",
  };

  it("uses the requested port for a non-default install root", () => {
    const resolved = resolveCorePort({
      ...isolatedEnv,
      ONE_MEMORY_INSTALL_ROOT: "/Users/example/custom-root/current",
      ONE_MEMORY_CORE_PORT: "9311",
    });
    expect(resolved).toEqual({
      port: 9311,
      overrideApplied: true,
      ignoredReason: null,
    });
  });

  it("ignores the override when the launch names the everyday root", () => {
    // The daily install must keep 8765; a stray variable cannot move it.
    const platform: RuntimePlatform =
      process.platform === "darwin"
        ? "darwin"
        : process.platform === "win32"
          ? "win32"
          : "linux";
    const home = "/home/example";
    const resolved = resolveCorePort({
      ONE_MEMORY_INSTALL_ROOT: path.join(
        defaultManagedInstallRoot(platform, home),
        "current",
      ),
      ONE_MEMORY_HOST_HOME: home,
      ONE_MEMORY_CORE_PORT: "9311",
    });
    expect(resolved.port).toBe(DEFAULT_CORE_PORT);
    expect(resolved.overrideApplied).toBe(false);
    expect(resolved.ignoredReason).toBe(
      "core_port_override_ignored_daily_root",
    );
  });

  it("honors the override when the root is not declared to be the daily root", () => {
    // C7's arbitration: the collision is two ordinary custom roots, which carry
    // no hourly marker at all. They must not both fall back to 8765.
    const resolved = resolveCorePort({ ONE_MEMORY_CORE_PORT: "9311" });
    expect(resolved.port).toBe(9311);
    expect(resolved.overrideApplied).toBe(true);
    expect(resolved.ignoredReason).toBeNull();
  });

  it("does not let a package that merely looks isolated switch ports", () => {
    // Right prefix/suffix but a bogus instance id is not a genuine isolated
    // hour, so it must not force the isolated gate open.
    expect(
      isIsolatedHourlyRuntime({
        ONE_MEMORY_HOURLY_INSTANCE_ID: "not-a-uuid",
        ONE_MEMORY_HOURLY_PACKAGE_ID: "One-Memory-H08-x-c7-isolated",
      }),
    ).toBe(false);
  });

  it.each(["0", "-1", "70000", "abc", "8765.5", ""])(
    "falls back to the default for an unusable port value: %j",
    (value) => {
      const resolved = resolveCorePort({
        ...isolatedEnv,
        ONE_MEMORY_CORE_PORT: value,
      });
      expect(resolved.port).toBe(DEFAULT_CORE_PORT);
      expect(resolved.overrideApplied).toBe(false);
    },
  );

  it("defaults to 8765 when the variable is absent", () => {
    expect(resolveCorePort({}).port).toBe(DEFAULT_CORE_PORT);
  });
});

describe("Runtime CLI configuration", () => {
  it("uses a validated non-default Broker port from the service declaration", () => {
    expect(
      resolveRuntimeCliConfig({
        HOME: "/tmp/qa-home",
        ONE_MEMORY_TEST_ISOLATED: "1",
        ONE_MEMORY_BROKER_PORT: "33805",
      }).brokerPort,
    ).toBe(33805);
    expect(
      resolveRuntimeCliConfig({
        HOME: "/tmp/qa-home",
        ONE_MEMORY_TEST_ISOLATED: "1",
        ONE_MEMORY_BROKER_PORT: "18765",
      }).brokerPort,
    ).toBeUndefined();
    expect(
      resolveRuntimeCliConfig({
        HOME: "/tmp/qa-home",
        ONE_MEMORY_BROKER_PORT: "33805",
      }).brokerPort,
    ).toBe(33805);
  });
  it.each(["", "relative", "/tmp/a\0b", "/tmp/a\nb"])(
    "refuses invalid explicit host home instead of falling back to daily hosts: %j",
    (hostHome) => {
      expect(() =>
        resolveRuntimeCliConfig({ ONE_MEMORY_HOST_HOME: hostHome }),
      ).toThrow("runtime_service_host_home_invalid");
    },
  );
  it("preserves significant trailing spaces in isolated host home", () => {
    expect(
      resolveRuntimeCliConfig({ ONE_MEMORY_HOST_HOME: "/tmp/host " }).hostHome,
    ).toBe("/tmp/host ");
  });
  it("preserves significant trailing spaces in the same explicit data identity", () => {
    expect(
      resolveRuntimeCliConfig({ ONE_MEMORY_DATA_DIR: "/tmp/data " }).dataDir,
    ).toBe("/tmp/data ");
  });

  it.each(["", "relative", "/tmp/a\0b", "/tmp/a\nb", "/tmp/a\rb"])(
    "refuses invalid explicit startup data instead of switching to a default: %j",
    (dataDir) => {
      expect(() =>
        resolveRuntimeCliConfig({ ONE_MEMORY_DATA_DIR: dataDir }),
      ).toThrow("runtime_service_data_directory_invalid");
    },
  );

  it("uses explicit resource paths for an isolated installation", () => {
    const config = resolveRuntimeCliConfig(
      {
        ONE_MEMORY_DATA_DIR: "/tmp/one-memory-data",
        ONE_MEMORY_HOST_HOME: "/tmp/one-memory-host-home",
        ONE_MEMORY_RUNTIME_ENTRY: "/opt/one-memory/runtime/runtime.cjs",
        ONE_MEMORY_CORE_COMMAND: "/opt/one-memory/core/one-memory",
        ONE_MEMORY_MCP_ENTRY: "/opt/one-memory/host-bridge/mcp.cjs",
        ONE_MEMORY_RECALL_CLI: "/opt/one-memory/host-bridge/recall-cli.cjs",
        ONE_MEMORY_SYNC_HOOK_ASSETS: "/opt/one-memory/host-bridge/sync-hook",
        ONE_MEMORY_NODE_COMMAND: "/opt/one-memory/node",
      },
      "/opt/one-memory/runtime/runtime.cjs",
    );

    expect(config).toEqual({
      dataDir: "/tmp/one-memory-data",
      hostHome: "/tmp/one-memory-host-home",
      runtimeEntry: "/opt/one-memory/runtime/runtime.cjs",
      coreCommand: "/opt/one-memory/core/one-memory",
      coreArgs: ["serve"],
      mcpEntryPath: "/opt/one-memory/host-bridge/mcp.cjs",
      recallCliPath: "/opt/one-memory/host-bridge/recall-cli.cjs",
      syncHookAssetsDirectory: "/opt/one-memory/host-bridge/sync-hook",
      nodeCommand: "/opt/one-memory/node",
      webDirectory: "/opt/one-memory/web",
    });
  });

  it("derives the packaged layout beside the Runtime entry", () => {
    const config = resolveRuntimeCliConfig(
      {},
      "/opt/one-memory/runtime/runtime.cjs",
    );

    expect(config.coreCommand).toBe(
      path.join(
        "/opt/one-memory",
        "core",
        process.platform === "win32" ? "one-memory.exe" : "one-memory",
      ),
    );
    expect(config.runtimeEntry).toBe("/opt/one-memory/runtime/runtime.cjs");
    expect(config.mcpEntryPath).toBe("/opt/one-memory/host-bridge/mcp.cjs");
    expect(config.recallCliPath).toBe(
      "/opt/one-memory/host-bridge/recall-cli.cjs",
    );
  });

  // The service and the CLI are two descriptions of one installation. They stay
  // in step only because the unit file names every resource the CLI would
  // otherwise guess for itself. Read the environment back out of the literal
  // unit that gets installed, so a resource added to one side and forgotten on
  // the other is caught here rather than by a host pointing somewhere stale.
  const installRoot = "/home/example/.local/share/one-memory/current";
  const layout = runtimeInstallLayout({
    platform: "linux",
    home: "/home/example",
    installRoot,
  });

  function unitEnvironment(): Record<string, string> {
    const plan = planUserService(layout);
    const content = plan.service_file?.content ?? "";
    return Object.fromEntries(
      [...content.matchAll(/^Environment="([^=]+)=(.*)"$/gm)].map(
        ([, key, value]) => [key, value],
      ),
    );
  }

  it("hands the CLI the same installation the user service describes", () => {
    const environment = unitEnvironment();
    expect(Object.keys(environment).sort()).toEqual([
      "HOME",
      "ONE_MEMORY_CORE_COMMAND",
      "ONE_MEMORY_DATA_DIR",
      "ONE_MEMORY_HOST_HOME",
      "ONE_MEMORY_MCP_ENTRY",
      "ONE_MEMORY_NODE_COMMAND",
      "ONE_MEMORY_RECALL_CLI",
      "ONE_MEMORY_RUNTIME_ENTRY",
      "ONE_MEMORY_SERVICE_ENABLE_MARKER",
      "ONE_MEMORY_SERVICE_OWNER_FILE",
      "ONE_MEMORY_SYNC_HOOK_ASSETS",
    ]);

    const config = resolveRuntimeCliConfig(environment, layout.runtimeEntry);

    expect(config).toEqual({
      dataDir: layout.dataDir,
      hostHome: layout.home,
      runtimeEntry: layout.runtimeEntry,
      coreCommand: layout.coreCommand,
      coreArgs: ["serve"],
      mcpEntryPath: layout.mcpEntryPath,
      recallCliPath: layout.recallCliPath,
      syncHookAssetsDirectory: layout.syncHookAssetsDirectory,
      nodeCommand: layout.nodeCommand,
      webDirectory: path.join(installRoot, "web"),
      buildIdentity: undefined,
    });
  });

  it("notices a resource the unit stops naming", () => {
    const { ONE_MEMORY_NODE_COMMAND: _dropped, ...partial } = unitEnvironment();

    // Every host One Memory installs is told to run this command. Left
    // unnamed it becomes whichever node happened to launch the Runtime.
    expect(
      resolveRuntimeCliConfig(partial, layout.runtimeEntry).nodeCommand,
    ).not.toBe(layout.nodeCommand);
  });
});

describe("Runtime CLI status", () => {
  it("accepts ONE_MEMORY_RUNTIME_PORT as the probe port contract", () => {
    // A CLI harness that spawns the Runtime itself cannot rely on the plist's
    // ONE_MEMORY_CORE_PORT. The standalone port contract must let it name a
    // non-canonical port so probes never attach to 8765.
    expect(
      resolveCorePort({
        ONE_MEMORY_RUNTIME_PORT: "9415",
        ONE_MEMORY_DATA_DIR: "/Users/example/.one-memory-hourly/H08",
      }),
    ).toEqual({ port: 9415, overrideApplied: true, ignoredReason: null });
    // The installed plist name wins when both are present.
    expect(
      resolveCorePort({
        ONE_MEMORY_CORE_PORT: "9415",
        ONE_MEMORY_RUNTIME_PORT: "9999",
        ONE_MEMORY_DATA_DIR: "/Users/example/.one-memory-hourly/H08",
      }).port,
    ).toBe(9415);
    expect(resolveCorePort({ ONE_MEMORY_RUNTIME_PORT: "80" })).toEqual({
      port: DEFAULT_CORE_PORT,
      overrideApplied: false,
      ignoredReason: "core_port_override_invalid",
    });
  });

  it("lets service-plan name an exact non-canonical port and reports it", async () => {
    let output = "";
    const original = process.stdout.write;
    process.stdout.write = ((value: string) => {
      output += value;
      return true;
    }) as typeof original;
    try {
      // Out-of-range ports are refused, not silently coerced.
      expect(
        await runRuntimeCli(
          [
            "service-plan",
            "--platform",
            "darwin",
            "--home",
            "/Users/example",
            "--install-root",
            "/Users/example/Library/Application Support/One Memory Hourly/H08/managed-runtime",
            "--uid",
            "501",
            "--port",
            "80",
          ],
          {},
        ),
      ).toBe(2);
      expect(
        await runRuntimeCli(
          [
            "service-plan",
            "--platform",
            "darwin",
            "--home",
            "/Users/example",
            "--install-root",
            "/Users/example/Library/Application Support/One Memory Hourly/H08/managed-runtime",
            "--uid",
            "501",
            "--port",
            "9415",
          ],
          {},
        ),
      ).toBe(0);
    } finally {
      process.stdout.write = original;
    }
    const plan = JSON.parse(output);
    expect(plan.core_port).toBe(9415);
    expect(plan.service_file.content).toContain(
      "<key>ONE_MEMORY_CORE_PORT</key><string>9415</string>",
    );
  });

  it("reports a stopped Runtime as a failure, not as silence", async () => {
    const dataDir = mkdtempSync(path.join(os.tmpdir(), "om-status-"));
    const output = captured();
    try {
      expect(await runRuntimeCli(["status", "--data-dir", dataDir], {})).toBe(
        4,
      );
    } finally {
      output.restore();
    }
    const status = JSON.parse(output.text());
    expect(status).toMatchObject({ data_dir: dataDir, state: "stopped" });
    expect(status.code).toBe("background_runtime_not_healthy");
  });

  it("observes without creating, starting or printing a credential", async () => {
    const dataDir = path.join(
      mkdtempSync(path.join(os.tmpdir(), "om-status-")),
      "absent",
    );
    const output = captured();
    try {
      expect(await runRuntimeCli(["status", "--data-dir", dataDir], {})).toBe(
        4,
      );
    } finally {
      output.restore();
    }
    expect(existsSync(dataDir)).toBe(false);
    expect(output.text()).not.toMatch(/token|secret|authorization/i);
  });

  it("reads the same data directory the service would use", async () => {
    const dataDir = mkdtempSync(path.join(os.tmpdir(), "om-status-env-"));
    const output = captured();
    try {
      expect(
        await runRuntimeCli(["status"], { ONE_MEMORY_DATA_DIR: dataDir }),
      ).toBe(4);
    } finally {
      output.restore();
    }
    expect(JSON.parse(output.text()).data_dir).toBe(dataDir);
  });

  it.each([
    ["relative", ["status", "--data-dir", "relative"]],
    ["unknown option", ["status", "--platform", "darwin"]],
    ["extra option", ["status", "--data-dir", "/tmp/a", "--uid", "5"]],
  ])("refuses %s rather than guessing a target", async (_name, arguments_) => {
    expect(await runRuntimeCli(arguments_, {})).toBe(2);
  });
});

describe("Runtime CLI service plan", () => {
  it.each([
    [[], {}, "/home/selected/.one-memory"],
    [[], { ONE_MEMORY_DATA_DIR: "/tmp/env data" }, "/tmp/env data"],
    [[], { ONE_MEMORY_DATA_DIR: "/tmp/env data " }, "/tmp/env data "],
    [
      ["--data-dir", "/tmp/flag data"],
      { ONE_MEMORY_DATA_DIR: "/tmp/env data" },
      "/tmp/flag data",
    ],
  ] as const)(
    "preserves explicit data identity and selected home: %j",
    async (flags, environment, expected) => {
      let output = "";
      const original = process.stdout.write;
      process.stdout.write = ((value: string) => {
        output += value;
        return true;
      }) as typeof original;
      try {
        expect(
          await runRuntimeCli(
            [
              "service-plan",
              "--platform",
              "linux",
              "--home",
              "/home/selected",
              "--install-root",
              "/opt/one-memory",
              ...flags,
            ],
            environment,
          ),
        ).toBe(0);
      } finally {
        process.stdout.write = original;
      }
      expect(JSON.parse(output).service_file.content).toContain(
        `ONE_MEMORY_DATA_DIR=${expected}`,
      );
    },
  );

  it.each(["", "relative", "/tmp/a\0b", "/tmp/a\nb", "/tmp/a\rb"])(
    "rejects invalid flag and environment data directory %j",
    async (dataDir) => {
      const base = [
        "service-plan",
        "--platform",
        "linux",
        "--home",
        "/home/selected",
        "--install-root",
        "/opt/one-memory",
      ];
      expect(await runRuntimeCli([...base, "--data-dir", dataDir], {})).toBe(2);
      expect(await runRuntimeCli(base, { ONE_MEMORY_DATA_DIR: dataDir })).toBe(
        2,
      );
    },
  );

  it("rejects duplicate data flags rather than using the last one", async () => {
    expect(
      await runRuntimeCli(
        [
          "service-plan",
          "--platform",
          "linux",
          "--home",
          "/home/selected",
          "--install-root",
          "/opt/one-memory",
          "--data-dir",
          "/tmp/a",
          "--data-dir",
          "/tmp/b",
        ],
        {},
      ),
    ).toBe(2);
  });

  it("prints a Linux user-service plan without applying it", async () => {
    let output = "";
    const original = process.stdout.write;
    process.stdout.write = ((value: string) => {
      output += value;
      return true;
    }) as typeof process.stdout.write;
    try {
      expect(
        await runRuntimeCli([
          "service-plan",
          "--platform",
          "linux",
          "--home",
          "/home/example",
          "--install-root",
          "/opt/one-memory",
        ]),
      ).toBe(0);
    } finally {
      process.stdout.write = original;
    }
    const plan = JSON.parse(output);
    expect(plan.status).toBe("ready");
    expect(plan.service_file.path).toContain("one-memory.service");
  });

  it("reports Windows as blocked until ACL support is implemented", async () => {
    let output = "";
    const original = process.stdout.write;
    process.stdout.write = ((value: string) => {
      output += value;
      return true;
    }) as typeof process.stdout.write;
    try {
      expect(
        await runRuntimeCli([
          "service-plan",
          "--platform",
          "win32",
          "--home",
          "C:\\Users\\Example",
          "--install-root",
          "C:\\OneMemory",
        ]),
      ).toBe(4);
    } finally {
      process.stdout.write = original;
    }
    expect(JSON.parse(output).blocked_reason).toBe(
      "windows_acl_and_user_service_not_verified",
    );
  });
});
