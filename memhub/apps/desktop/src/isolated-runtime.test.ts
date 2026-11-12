import { createHash } from "node:crypto";
import { mkdirSync, mkdtempSync } from "node:fs";
import { mkdir, mkdtemp, realpath, symlink, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";
import {
  resolveCoreDataDir,
  resolveCoreUserDataDir,
  resolvePackagedRuntimeLaunch,
  resolveRunningPackagedHostPaths,
  resolveVerifiedHostRuntime,
  stableRecallPaths,
} from "./core-supervisor";
import {
  isCanonicalDailyDataDir,
  isIsolatedHourlyDesktop,
  resolveDesktopTestIsolation,
  resolvePackagedBuildNumber,
  resolvePackagedCommit,
} from "./isolated-runtime";

describe("formal package test isolation", () => {
  const base = {
    ONE_MEMORY_TEST_ISOLATED: "1",
    ONE_MEMORY_DESKTOP_TEST_HOME: "/tmp/formal/home",
    ONE_MEMORY_TEST_REAL_HOME: "/Users/real",
    HOME: "/tmp/formal/home",
    ONE_MEMORY_DATA_DIR: "/tmp/formal/data",
    ONE_MEMORY_HOST_HOME: "/tmp/formal/hosts",
    ONE_MEMORY_CORE_PORT: "25411",
    ONE_MEMORY_RUNTIME_PORT: "25411",
    ONE_MEMORY_BROKER_PORT: "35411",
  };

  it("allows host integration only for the canonical daily data directory", () => {
    expect(
      isCanonicalDailyDataDir("/Users/real", "/Users/real/.one-memory"),
    ).toBe(true);
    expect(
      isCanonicalDailyDataDir(
        "/Users/real",
        "/Users/real/Desktop/coldstart/data",
      ),
    ).toBe(false);
  });

  it("redirects Electron home and userData before formal startup", () => {
    expect(resolveDesktopTestIsolation(base, "/Users/real")).toEqual({
      home: "/tmp/formal/home",
      userData: "/tmp/formal/home/Library/Application Support/MemHub Test",
    });
    expect(resolveDesktopTestIsolation(base, "/tmp/formal/home")).toEqual({
      home: "/tmp/formal/home",
      userData: "/tmp/formal/home/Library/Application Support/MemHub Test",
    });
    expect(
      resolveDesktopTestIsolation(
        { ONE_MEMORY_TEST_ISOLATED: "1" },
        "/Users/real",
      ),
    ).toBeNull();
  });

  it.skipIf(process.platform !== "darwin")(
    "accepts the macOS /tmp alias for an existing /private/tmp test home",
    () => {
      const root = mkdtempSync("/private/tmp/one-memory-desktop-alias.");
      const home = path.join(root, "home");
      const dataDir = path.join(root, "data");
      const hostHome = path.join(root, "host-home");
      mkdirSync(home, { recursive: true });
      mkdirSync(dataDir, { recursive: true });
      mkdirSync(hostHome, { recursive: true });

      expect(
        resolveDesktopTestIsolation(
          {
            ONE_MEMORY_TEST_ISOLATED: "1",
            ONE_MEMORY_TEST_REAL_HOME: "/Users/example",
            ONE_MEMORY_DESKTOP_TEST_HOME: home,
            ONE_MEMORY_DATA_DIR: dataDir,
            ONE_MEMORY_HOST_HOME: hostHome,
            ONE_MEMORY_CORE_PORT: "27791",
            ONE_MEMORY_RUNTIME_PORT: "27791",
            ONE_MEMORY_BROKER_PORT: "37791",
          },
          home.replace(/^\/private\/tmp\//, "/tmp/"),
        ),
      ).toMatchObject({ home });
    },
  );

  it("refuses the real home, real-home data, and daily ports", () => {
    expect(() =>
      resolveDesktopTestIsolation(
        { ...base, ONE_MEMORY_DESKTOP_TEST_HOME: "/Users/real" },
        "/Users/real",
      ),
    ).toThrow("desktop_test_real_home_refused");
    expect(() =>
      resolveDesktopTestIsolation(
        { ...base, ONE_MEMORY_DESKTOP_TEST_HOME: "/Users/real/test" },
        "/Users/real",
      ),
    ).toThrow("desktop_test_real_home_refused");
    expect(() =>
      resolveDesktopTestIsolation(
        { ...base, ONE_MEMORY_DATA_DIR: "/Users/real/.one-memory" },
        "/Users/real",
      ),
    ).toThrow("desktop_test_real_home_refused");
    expect(() =>
      resolveDesktopTestIsolation(
        { ...base, ONE_MEMORY_CORE_PORT: "8765" },
        "/Users/real",
      ),
    ).toThrow("desktop_test_port_refused");
    expect(() =>
      resolveDesktopTestIsolation(base, "/Users/someone-else"),
    ).toThrow("desktop_test_real_home_mismatch");
  });
});

async function hostRuntimeFixture() {
  const home = await mkdtemp(path.join(os.tmpdir(), "om-host-runtime-"));
  const resources = path.join(home, "app-resources");
  const selected = path.join(
    home,
    "Library/Application Support/One Memory/versions/0.0.0-b3758-darwin-arm64",
  );
  const mappings = [
    ["core/one-memory", "core/one-memory"],
    ["runtime/runtime.cjs", "runtime/runtime.cjs"],
    ["host-bridge/mcp.cjs", "host-bridge/mcp.cjs"],
    ["host-bridge/recall-cli.cjs", "host-bridge/recall-cli.cjs"],
    ["cli/one-memory.cjs", "bin/one-memory.cjs"],
    ["web/index.html", "web/index.html"],
    ["host-bridge/sync-hook/runtime.mjs", "host-bridge/sync-hook/runtime.mjs"],
  ] as const;
  const files: Record<string, string> = {};
  for (const [from, to] of mappings) {
    await mkdir(path.dirname(path.join(resources, from)), {
      recursive: true,
      mode: 0o700,
    });
    await mkdir(path.dirname(path.join(selected, to)), {
      recursive: true,
      mode: 0o700,
    });
    const body = Buffer.from(`fixture:${to}`);
    await writeFile(path.join(resources, from), body, {
      mode: to === "core/one-memory" ? 0o700 : 0o600,
    });
    await writeFile(path.join(selected, to), body, { mode: 0o600 });
    files[to] = createHash("sha256").update(body).digest("hex");
  }
  await writeFile(path.join(selected, "bin/node"), "node", { mode: 0o700 });
  files["bin/node"] = createHash("sha256").update("node").digest("hex");
  await writeFile(
    path.join(selected, "manifest.json"),
    JSON.stringify({
      schema_version: 1,
      product: "one-memory-runtime",
      version: "0.0.0",
      build_number: 3758,
      package_id: "One-Memory-APP1R5.20-f154da45-candidate",
      platform: "darwin",
      arch: process.arch,
      files,
    }),
  );
  await symlink(
    "versions/0.0.0-b3758-darwin-arm64",
    path.join(home, "Library/Application Support/One Memory/current"),
  );
  return { home, resources, selected };
}

describe("hourly isolated desktop launch", () => {
  it("binds the real HAPP1 marker to one verified APP1 managed version", async () => {
    const fixture = await hostRuntimeFixture();
    const result = await resolveVerifiedHostRuntime({
      resourcesPath: fixture.resources,
      appMarker: "One-Memory-HAPP1R5.20-f154da45-c7-isolated",
      buildNumber: 3758,
      home: fixture.home,
      platform: "darwin",
      arch: process.arch,
    });
    const selected = await realpath(fixture.selected);
    expect(result).toMatchObject({
      state: "verified",
      value: {
        managedInstallRoot: path.join(
          fixture.home,
          "Library/Application Support/One Memory",
        ),
        selectedVersionRoot: selected,
        runtimeCommand: path.join(selected, "bin/node"),
      },
    });
    await writeFile(path.join(fixture.resources, "web/index.html"), "mismatch");
    await expect(
      resolveVerifiedHostRuntime({
        resourcesPath: fixture.resources,
        appMarker: "One-Memory-HAPP1R5.20-f154da45-c7-isolated",
        buildNumber: 3758,
        home: fixture.home,
        platform: "darwin",
        arch: process.arch,
      }),
    ).resolves.toEqual({
      state: "blocked",
      code: "host_runtime_component_mismatch",
    });
  });

  it("rejects a mismatched HAPP1/APP1 family", async () => {
    const fixture = await hostRuntimeFixture();
    await expect(
      resolveVerifiedHostRuntime({
        resourcesPath: fixture.resources,
        appMarker: "One-Memory-HAPP1R5.21-deadbeef-c7-isolated",
        buildNumber: 3758,
        home: fixture.home,
        platform: "darwin",
        arch: process.arch,
      }),
    ).resolves.toEqual({
      state: "blocked",
      code: "host_runtime_identity_mismatch",
    });
  });

  it("keeps the hourly Core port while exporting verified host paths", async () => {
    const fixture = await hostRuntimeFixture();
    const selection = await resolveVerifiedHostRuntime({
      resourcesPath: fixture.resources,
      appMarker: "One-Memory-HAPP1R5.20-f154da45-c7-isolated",
      buildNumber: 3758,
      home: fixture.home,
      platform: "darwin",
      arch: process.arch,
    });
    const launch = resolvePackagedRuntimeLaunch({
      resourcesPath: fixture.resources,
      dataDir: path.join(fixture.home, ".one-memory-hourly/example"),
      runtimeCommand: "/apps/MemHub",
      home: fixture.home,
      platform: "darwin",
      isolated: true,
      hourlyPackageId: "One-Memory-HAPP1R5.20-f154da45-c7-isolated",
      hostRuntimeSelection: selection,
      buildNumber: 3758,
      commit: "f154da45",
    });
    expect(launch.env?.ONE_MEMORY_NODE_COMMAND).toBe(
      path.join(await realpath(fixture.selected), "bin/node"),
    );
    expect(launch.env?.ONE_MEMORY_CORE_PORT).not.toBe("8765");
    expect(launch.env?.ONE_MEMORY_BUILD_NUMBER).toBe("3758");
    expect(launch.env?.ONE_MEMORY_COMMIT).toBe("f154da45");
    expect(launch.env?.ONE_MEMORY_HOST_RUNTIME_ATTESTATION).toBeTypeOf(
      "string",
    );
    expect(
      JSON.parse(launch.env?.ONE_MEMORY_HOST_RUNTIME_ATTESTATION ?? "{}"),
    ).toMatchObject({
      managedInstallRoot: path.join(
        fixture.home,
        "Library/Application Support/One Memory",
      ),
      selectedVersionRoot: await realpath(fixture.selected),
    });
  });

  it("is enabled only by the controlled isolated marker", () => {
    expect(isIsolatedHourlyDesktop("1", undefined)).toBe(false);
    expect(isIsolatedHourlyDesktop("1", "random-value")).toBe(false);
    expect(isIsolatedHourlyDesktop(undefined, "random-value")).toBe(false);
    expect(
      isIsolatedHourlyDesktop(
        "1d3b0c1c-59d1-44c3-9e3b-24e5ecfa1b19",
        "One-Memory-H08-6e116c4-c7-isolated",
      ),
    ).toBe(true);
  });

  it("keeps hourly data out of the default home directory", () => {
    const home = "/Users/example";
    expect(resolveCoreDataDir("MemHub", home, undefined, true)).not.toBe(
      `${home}/.one-memory`,
    );
    expect(resolveCoreDataDir("MemHub", home, undefined, false)).toBe(
      `${home}/.one-memory`,
    );
  });

  it("launches from the app bundle but points installed hosts at a stable hourly root", () => {
    const launch = resolvePackagedRuntimeLaunch({
      resourcesPath: "/apps/MemHub.app/Contents/Resources",
      dataDir: "/managed/hourly/H08/data",
      runtimeCommand: "/apps/MemHub.app/Contents/MacOS/MemHub",
      home: "/Users/example",
      platform: "darwin",
      localAppData: undefined,
      isolated: true,
    });
    expect(launch.env?.ONE_MEMORY_DESKTOP_RUNTIME_ISOLATED).toBe("1");
    expect(launch.env?.ONE_MEMORY_CORE_COMMAND).toBe(
      "/apps/MemHub.app/Contents/Resources/core/one-memory",
    );
    expect(launch.env?.ONE_MEMORY_MCP_ENTRY).toBe(
      "/Users/example/Library/Application Support/One Memory Hourly/managed-runtime/current/host-bridge/mcp.cjs",
    );

    const stable = stableRecallPaths({
      home: "/Users/example",
      platform: "darwin",
      dataDir: "/managed/hourly/H08/data",
      isolated: true,
    });
    expect(stable.captureCommand).toBe(
      "/Users/example/Library/Application Support/One Memory Hourly/managed-runtime/current/core/one-memory",
    );
  });

  it("forces the isolated Runtime onto a non-8765 port (red-first for the R2 8765 defect)", () => {
    // The App spawns the Runtime as a detached child; the child resolves its
    // Core port itself from ONE_MEMORY_CORE_PORT. If the launch env omits it,
    // the child falls back to 8765 even though the markers are baked, so a
    // Finder double-clicked isolated package would collide with the daily Core.
    const launch = resolvePackagedRuntimeLaunch({
      resourcesPath: "/apps/MemHub.app/Contents/Resources",
      dataDir:
        "/Users/example/.one-memory-hourly/One-Memory-HAPP1-1ef6801-c7-isolated",
      runtimeCommand: "/apps/MemHub.app/Contents/MacOS/MemHub",
      home: "/Users/example",
      platform: "darwin",
      localAppData: undefined,
      isolated: true,
      hourlyPackageId: "One-Memory-HAPP1-1ef6801-c7-isolated",
    });
    const port = launch.env?.ONE_MEMORY_CORE_PORT;
    expect(port, "isolated launch must set ONE_MEMORY_CORE_PORT").toBeDefined();
    expect(Number(port)).toBeGreaterThanOrEqual(9200);
    expect(Number(port)).toBeLessThanOrEqual(9899);
    expect(Number(port)).not.toBe(8765);
  });

  it("forwards the Desktop-selected port for an explicit data directory", () => {
    const launch = resolvePackagedRuntimeLaunch({
      resourcesPath: "/apps/MemHub.app/Contents/Resources",
      dataDir: "/tmp/isolated/data",
      runtimeCommand: "/apps/MemHub.app/Contents/MacOS/MemHub",
      home: "/tmp/isolated/home",
      corePortOverride: 53754,
    });

    expect(launch.env?.ONE_MEMORY_CORE_PORT).toBe("53754");
  });

  it("keeps the daily launch on 8765 (no override without isolation)", () => {
    const launch = resolvePackagedRuntimeLaunch({
      resourcesPath: "/apps/MemHub.app/Contents/Resources",
      dataDir: "/Users/example/.one-memory",
      runtimeCommand: "/apps/MemHub.app/Contents/MacOS/MemHub",
      home: "/Users/example",
      localAppData: undefined,
      isolated: false,
    });
    expect(launch.env?.ONE_MEMORY_CORE_PORT).toBeUndefined();
  });

  it("advertises only an app inside the Applications root to hosts", async () => {
    const appRoot = await mkdtemp(path.join(os.tmpdir(), "MemHub-running-"));
    const applicationsRoot = path.join(appRoot, "Applications");
    const bundle = path.join(applicationsRoot, "MemHub.app");
    const resources = path.join(bundle, "Contents/Resources");
    const executable = path.join(bundle, "Contents/MacOS/MemHub");
    await mkdir(path.dirname(executable), { recursive: true });
    await mkdir(resources, { recursive: true });
    await writeFile(executable, "fixture", { mode: 0o700 });
    const resolvedBundle = await realpath(bundle);
    const resolvedResources = path.join(resolvedBundle, "Contents/Resources");
    const packagedHostPaths = {
      runtimeCommand: path.join(resolvedBundle, "Contents/MacOS/MemHub"),
      mcpEntryPath: path.join(resolvedResources, "host-bridge/mcp.cjs"),
      recallCliPath: path.join(resolvedResources, "host-bridge/recall-cli.cjs"),
      captureCommand: path.join(resolvedResources, "core/one-memory"),
      syncHookAssetsDirectory: path.join(
        resolvedResources,
        "host-bridge/sync-hook",
      ),
    };
    await expect(
      resolveRunningPackagedHostPaths({
        runtimeCommand: executable,
        resourcesPath: resources,
        platform: "darwin",
        applicationsRoot,
      }),
    ).resolves.toEqual({ state: "verified", value: packagedHostPaths });
    const launch = resolvePackagedRuntimeLaunch({
      resourcesPath: "/private/tmp/build/MemHub.app/Contents/Resources",
      dataDir: "/tmp/isolated/data",
      runtimeCommand: "/private/tmp/build/MemHub.app/Contents/MacOS/MemHub",
      home: "/tmp/isolated/home",
      corePortOverride: 53754,
      packagedHostPaths,
    });

    expect(launch.command).toContain("/private/tmp/build/MemHub.app");
    expect(launch.env?.ONE_MEMORY_NODE_COMMAND).toBe(
      packagedHostPaths.runtimeCommand,
    );
    expect(launch.env?.ONE_MEMORY_MCP_ENTRY).toBe(
      packagedHostPaths.mcpEntryPath,
    );
    expect(launch.env?.ONE_MEMORY_RECALL_CLI).toBe(
      packagedHostPaths.recallCliPath,
    );
  });

  it("refuses a temporary app as a durable host target", async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), "MemHub-temporary-"));
    const bundle = path.join(root, "MemHub.app");
    const resources = path.join(bundle, "Contents/Resources");
    const executable = path.join(bundle, "Contents/MacOS/MemHub");
    await mkdir(path.dirname(executable), { recursive: true });
    await mkdir(resources, { recursive: true });
    await writeFile(executable, "fixture", { mode: 0o700 });

    await expect(
      resolveRunningPackagedHostPaths({
        runtimeCommand: executable,
        resourcesPath: resources,
        platform: "darwin",
        applicationsRoot: path.join(root, "Applications"),
      }),
    ).resolves.toEqual({
      state: "blocked",
      code: "host_runtime_app_location_unsafe",
    });
  });

  it("refuses a disk image or translocated app as a durable host target", async () => {
    for (const root of [
      "/Volumes/MemHub/MemHub.app",
      "/private/var/folders/x/AppTranslocation/y/MemHub.app",
    ]) {
      await expect(
        resolveRunningPackagedHostPaths({
          runtimeCommand: path.join(root, "Contents/MacOS/MemHub"),
          resourcesPath: path.join(root, "Contents/Resources"),
          platform: "darwin",
        }),
      ).resolves.toEqual({
        state: "blocked",
        code: "host_runtime_app_location_unsafe",
      });
    }
  });

  it("keys the isolated Electron userData by package so the single-instance lock is not shared with daily", () => {
    // macOS places `requestSingleInstanceLock()`'s SingletonLock under
    // `userData`. If an isolated package reused the daily App's userData, it
    // would see the daily instance's lock, exit 0 with no window, and never
    // reach its own Core. The userData directory must therefore be partitioned
    // by the same package id that splits the data/install roots.
    const appData = "/Users/example/Library/Application Support";
    const isolated = resolveCoreUserDataDir(
      "MemHub",
      appData,
      "One-Memory-HAPP1R5-5915c5b-c7-isolated",
    );
    const daily = resolveCoreUserDataDir("MemHub", appData, "daily");
    expect(isolated).not.toBe(daily);
    expect(isolated.startsWith(appData)).toBe(true);
    expect(isolated).toContain("One-Memory-HAPP1R5-5915c5b-c7-isolated");
  });

  it("accepts the real packaged app.getName() (scoped npm name) without throwing", () => {
    // EXEC-1914-R5.1-USERDATA-NAME-REJECTED: the packaged bundle has no
    // `productName`, so `app.getName()` returns the package.json name
    // `@one-memory/desktop`. The strict package guard rejected the `/` and
    // threw `hourly_package_id_invalid`, so `app.setPath("userData", ...)`
    // never ran and the isolated App kept the daily single-instance lock and
    // exited with no window. The display prefix must be sanitized, not rejected.
    const appData = "/Users/example/Library/Application Support";
    let result = "";
    expect(() => {
      result = resolveCoreUserDataDir(
        "@one-memory/desktop",
        appData,
        "One-Memory-HAPP1R5-5915c5b-c7-isolated",
      );
    }).not.toThrow();
    expect(result.startsWith(appData)).toBe(true);
    expect(result).toContain("One-Memory-HAPP1R5-5915c5b-c7-isolated");
    expect(result).not.toBe(resolveCoreUserDataDir("MemHub", appData, "daily"));
    // The scoped name's `/` must not survive into the path as a separator.
    expect(result.slice(appData.length)).not.toContain("/desktop ");
  });

  it("still rejects an escaping package id while sanitizing the display name", () => {
    expect(() =>
      resolveCoreUserDataDir(
        "@one-memory/desktop",
        "/Users/example/Library/Application Support",
        "../escape",
      ),
    ).toThrow("hourly_package_id_invalid");
  });

  it("reads a monotonic build number from the baked env and never invents one", () => {
    // Anti-downgrade compares `build_number` alone, so a package that omits it
    // defaults to 0 and R->R.1 is refused as a downgrade. The value is baked at
    // pack time; a source run must stay undefined rather than fabricate order.
    expect(resolvePackagedBuildNumber("3653")).toBe(3653);
    expect(resolvePackagedBuildNumber(" 42 ")).toBe(42);
    expect(resolvePackagedBuildNumber(undefined)).toBeUndefined();
    expect(resolvePackagedBuildNumber("")).toBeUndefined();
    expect(resolvePackagedBuildNumber("v3653")).toBeUndefined();
    expect(resolvePackagedBuildNumber("-1")).toBeUndefined();
    expect(resolvePackagedBuildNumber(undefined, "3907")).toBe(3907);
    expect(resolvePackagedBuildNumber("3908", "3907")).toBe(3908);
  });

  it("resolves only a full 40-character commit and lowercases it", () => {
    // Every downstream check (packaged-runtime install, local install, release
    // download, release scripts) requires 40 lowercase hex characters.
    const embedded = "388fd0e9b3c41a2d5e6f7a8b9c0d1e2f3a4b5c6d";
    expect(resolvePackagedCommit(undefined, embedded)).toBe(embedded);
    expect(resolvePackagedCommit(embedded.toUpperCase())).toBe(embedded);
    expect(
      resolvePackagedCommit(` ${embedded.toUpperCase()} `, undefined),
    ).toBe(embedded);
    expect(resolvePackagedCommit("388fd0e9", embedded)).toBe(embedded);
    expect(resolvePackagedCommit("388fd0e9")).toBeUndefined();
    expect(resolvePackagedCommit("not-a-sha", embedded)).toBe(embedded);
    expect(resolvePackagedCommit(`${embedded}0`, embedded)).toBe(embedded);
    expect(resolvePackagedCommit("g".repeat(40), embedded)).toBe(embedded);
    expect(resolvePackagedCommit(undefined, "unknown")).toBeUndefined();
  });
});
