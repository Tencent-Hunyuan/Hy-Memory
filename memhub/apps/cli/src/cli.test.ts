import { mkdtemp, readdir, readFile, realpath, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import {
  claimRuntimeServiceForApp,
  readRuntimeServiceOwner,
  resolveRuntimeCliConfig,
} from "@one-memory/runtime-control";
import { describe, expect, it, vi } from "vitest";

import {
  defaultInstallRoot,
  managedRuntimeServiceId,
  packagedAppCliPath,
  run as productRun,
  runWithManagedInstallForTest as run,
  runtimeIsReady,
} from "./cli.js";
import { PRODUCT } from "./version.js";

const ROOT_VERSION = JSON.parse(
  await readFile(new URL("../../../package.json", import.meta.url), "utf8"),
).version as string;

const runtimeMocks = vi.hoisted(() => ({
  applyLocalInstall: vi.fn(
    async (plan: {
      schema_version: 1;
      source: string;
      install_root: string;
      version_directory: string;
      current_link: string;
      version: string;
      platform: "linux";
      arch: string;
      files: number;
    }) => ({
      ...plan,
      previous_current_target: null,
      created_version: true,
    }),
  ),
  installUserService: vi.fn(async (_layout, options) => ({
    schema_version: 1,
    platform: process.platform === "darwin" ? "darwin" : "linux",
    scope: "user",
    service_id: "one-memory.service",
    status: "ready",
    blocked_reason: null,
    service_file: {
      path: "/tmp/one-memory.service",
      mode: 0o600,
      content: "unit",
    },
    commands: { install: [], start: [], stop: [], uninstall: [] },
    options,
  })),
  uninstallUserService: vi.fn(async (..._args: unknown[]) => "removed"),
  restartUserService: vi.fn(async () => undefined),
  runSdkMigration: vi.fn(
    async (): Promise<{ state: string; code?: string }> => ({
      state: "not_required",
    }),
  ),
  rollbackLocalInstall: vi.fn(async () => undefined),
  planCliLink: vi.fn(async ({ home, installRoot }) => ({
    schema_version: 1,
    platform: "linux",
    home,
    install_root: installRoot,
    bin_directory: path.join(home, ".local", "bin"),
    status: "create",
    blocked_reason: null,
    link_path: path.join(home, ".local", "bin", "one-memory"),
    target_path: path.join(installRoot, "current", "bin", "one-memory"),
  })),
  installCliLink: vi.fn(async (plan) => ({ ...plan, created: true })),
  rollbackCliLink: vi.fn(async () => undefined),
  planLocalInstall: vi.fn(async ({ source, installRoot }) => ({
    schema_version: 1,
    source,
    install_root: installRoot,
    version_directory: path.join(installRoot, "versions", "1.0.0-linux-x64"),
    current_link: path.join(installRoot, "current"),
    version: "1.0.0",
    platform: "linux",
    arch: "x64",
    files: 9,
  })),
  planLocalUninstall: vi.fn(
    async ({ installRoot, home, dataDir, cliLinkPath, stopsService }) => ({
      schema_version: 1,
      install_root: installRoot,
      home,
      data_dir: dataDir,
      cli_link_path: cliLinkPath,
      stops_service: stopsService,
      status: "ready",
      blocked_reason: null,
    }),
  ),
  applyLocalUninstall: vi.fn(async () => ({ removed: true })),
  inspectRuntimeStatus: vi.fn(async () => ({
    state: "stopped",
    code: "runtime_not_running",
  })),
}));

const aliasMocks = vi.hoisted(() => ({
  planManagedCliLink: vi.fn(async (plan, name: string) => ({
    ...plan,
    status: "create",
    blocked_reason: null,
    link_path: `${plan.bin_directory}/${name}`,
  })),
  installManagedCliLink: vi.fn(async (plan) => ({ ...plan, created: true })),
  rollbackManagedCliLink: vi.fn(async () => undefined),
  removeManagedCliLink: vi.fn(async () => "removed" as const),
}));

vi.mock("@one-memory/runtime-control", async (importOriginal) => {
  const original =
    await importOriginal<typeof import("@one-memory/runtime-control")>();
  return {
    ...original,
    planLocalInstall: runtimeMocks.planLocalInstall,
    planLocalUninstall: runtimeMocks.planLocalUninstall,
    applyLocalUninstall: runtimeMocks.applyLocalUninstall,
    inspectRuntimeStatus: runtimeMocks.inspectRuntimeStatus,
    applyLocalInstall: runtimeMocks.applyLocalInstall,
    installUserService: runtimeMocks.installUserService,
    uninstallUserService: runtimeMocks.uninstallUserService,
    restartUserService: runtimeMocks.restartUserService,
    runSdkMigration: runtimeMocks.runSdkMigration,
    rollbackLocalInstall: runtimeMocks.rollbackLocalInstall,
    planCliLink: runtimeMocks.planCliLink,
    installCliLink: runtimeMocks.installCliLink,
    rollbackCliLink: runtimeMocks.rollbackCliLink,
  };
});

vi.mock("./managed-cli-link.js", () => aliasMocks);

const shellPathMocks = vi.hoisted(() => ({
  planShellPath: vi.fn(async (options: { binDirectory: string }) => ({
    schema_version: 1 as const,
    status: "present" as const,
    blocked_reason: null,
    bin_directory: options.binDirectory,
    profile_path: "/tmp/home/.zprofile",
    block: null,
  })),
  applyShellPath: vi.fn(async (plan) => ({ ...plan, created: false })),
  removeShellPath: vi.fn(async () => "not-present" as const),
}));

const lifecycleMocks = vi.hoisted(() => ({
  prepareServiceInstall: vi.fn(async (layout: unknown, options: unknown) => ({
    layout,
    options,
    mode: "direct" as const,
    previouslyEnabled: false,
  })),
  applyPreparedServiceInstall: vi.fn(
    async (prepared: { layout: unknown; options: unknown }) =>
      runtimeMocks.installUserService(
        prepared.layout as never,
        prepared.options as never,
      ),
  ),
  uninstallServicePreservingData: vi.fn(
    async (layout: unknown, options: unknown) =>
      runtimeMocks.uninstallUserService(layout as never, options as never),
  ),
  rollbackPreparedServiceInstall: vi.fn(
    async (_prepared: unknown, rollback: () => Promise<void>) => rollback(),
  ),
}));

vi.mock("./shell-path.js", () => shellPathMocks);

// The macOS service lifecycle probes the real global LaunchAgent label, which
// unit tests must neither depend on nor fight over (a peer probe may hold it).
// Keep the real data-dir wiring by delegating to runtime-control's mocked
// install/uninstall, and skip only the launchd preflight. The real preflight is
// covered in service-lifecycle.test.ts.
vi.mock("./service-lifecycle.js", async () => lifecycleMocks);

describe("public One Memory CLI", () => {
  it("derives a scoped service id from a custom Runtime root", () => {
    const config = {
      ...resolveRuntimeCliConfig(
        {
          HOME: "/tmp/custom-home",
          ONE_MEMORY_DATA_DIR: "/tmp/custom-data",
          ONE_MEMORY_HOST_HOME: "/tmp/custom-home",
          ONE_MEMORY_RUNTIME_ENTRY:
            "/tmp/custom-install/current/runtime/runtime.cjs",
        },
        "/tmp/custom-install/current/bin/one-memory.cjs",
      ),
    };

    expect(
      managedRuntimeServiceId(
        config,
        { ONE_MEMORY_INSTALL_ROOT: "/tmp/custom-install" },
        501,
      ),
    ).toMatch(/^com\.tencent\.onememory\.runtime\.[a-f0-9]{16}$/);
  });

  describe("local-experience install surface", () => {
    it("recognizes the explicit packaged App fixture on non-macOS test hosts", () => {
      const target =
        "/Applications/MemHub.app/Contents/Resources/cli/one-memory.cjs";
      expect(
        packagedAppCliPath(undefined, {
          ONE_MEMORY_TEST_ISOLATED: "1",
          ONE_MEMORY_APP_BUNDLE_CLI: target,
        }),
      ).toBe(target);
    });
    it("installs only stable CLI links when invoked from the packaged Mac app", async () => {
      vi.clearAllMocks();
      const home = "/tmp/app-cli-home";
      const bin = "/tmp/app-cli-bin";
      const target =
        "/Applications/MemHub.app/Contents/Resources/cli/one-memory.cjs";
      const output = captureStdout();
      let code: number;
      try {
        code = await productRun(
          ["install", "--home", home, "--bin-dir", bin, "--yes"],
          {
            HOME: home,
            ONE_MEMORY_TEST_ISOLATED: "1",
            ONE_MEMORY_APP_BUNDLE_CLI: target,
          },
        );
      } finally {
        output.restore();
      }
      expect(code!).toBe(0);
      expect(
        aliasMocks.planManagedCliLink.mock.calls.map((call) => call[1]),
      ).toEqual(["memhub", "memhub-cli", "one-memory"]);
      expect(aliasMocks.installManagedCliLink).toHaveBeenCalledTimes(3);
      expect(runtimeMocks.planLocalInstall).not.toHaveBeenCalled();
      expect(runtimeMocks.installUserService).not.toHaveBeenCalled();
      expect(JSON.parse(output.value())).toMatchObject({
        apply: true,
        cli: [
          { link_path: `${bin}/memhub`, target_path: target },
          { link_path: `${bin}/memhub-cli`, target_path: target },
          { link_path: `${bin}/one-memory`, target_path: target },
        ],
      });
    });

    it("does not register a second service from the packaged Mac app", async () => {
      vi.clearAllMocks();
      const output = captureStdout();
      let code: number;
      try {
        code = await productRun(
          [
            "service-install",
            "--install-root",
            "/Applications/MemHub.app",
            "--yes",
            "--json",
          ],
          {
            HOME: "/tmp/app-cli-home",
            ONE_MEMORY_TEST_ISOLATED: "1",
            ONE_MEMORY_APP_BUNDLE_CLI:
              "/Applications/MemHub.app/Contents/Resources/cli/one-memory.cjs",
          },
        );
      } finally {
        output.restore();
      }
      expect(code!).toBe(0);
      expect(lifecycleMocks.prepareServiceInstall).not.toHaveBeenCalled();
      expect(JSON.parse(output.value())).toMatchObject({
        command: "service-install",
        state: "managed_by_app",
        apply: false,
      });
    });

    it("removes only stable CLI links when uninstall runs from the packaged Mac app", async () => {
      vi.clearAllMocks();
      const bin = "/tmp/app-cli-bin";
      const output = captureStdout();
      let code: number;
      try {
        code = await productRun(
          [
            "uninstall",
            "--home",
            "/tmp/app-cli-home",
            "--bin-dir",
            bin,
            "--yes",
          ],
          {
            HOME: "/tmp/app-cli-home",
            ONE_MEMORY_TEST_ISOLATED: "1",
            ONE_MEMORY_APP_BUNDLE_CLI:
              "/Applications/MemHub.app/Contents/Resources/cli/one-memory.cjs",
          },
        );
      } finally {
        output.restore();
      }
      expect(code!).toBe(0);
      expect(aliasMocks.removeManagedCliLink).toHaveBeenCalledTimes(3);
      expect(
        (
          aliasMocks.removeManagedCliLink.mock.calls as unknown as Array<
            [{ link_path: string }]
          >
        ).map((call) => path.basename(call[0].link_path)),
      ).toEqual(["memhub", "memhub-cli", "one-memory"]);
      expect(runtimeMocks.applyLocalUninstall).not.toHaveBeenCalled();
      expect(
        lifecycleMocks.uninstallServicePreservingData,
      ).not.toHaveBeenCalled();
      expect(JSON.parse(output.value())).toMatchObject({
        command: "uninstall",
        state: "cli_links_removed",
        data_preserved: true,
      });
    });

    it.each([
      [
        "service-install",
        [
          "--platform",
          "linux",
          "--home",
          "/tmp/home",
          "--install-root",
          "/tmp/install",
        ],
      ],
      [
        "uninstall",
        [
          "--source",
          "/tmp/dist",
          "--home",
          "/tmp/home",
          "--install-root",
          "/tmp/install",
        ],
      ],
      [
        "install",
        [
          "--source",
          "/tmp/dist",
          "--home",
          "/tmp/home",
          "--install-root",
          "/tmp/custom",
        ],
      ],
    ] as const)(
      "exposes %s through the production CLI",
      async (command, args) => {
        vi.clearAllMocks();
        const streams = captureStreams();
        let code: number;
        try {
          code = await productRun([command, ...args, "--json"], {});
        } finally {
          streams.restore();
        }
        expect(code!).not.toBe(2);
        expect(`${streams.stdout()}${streams.stderr()}`).not.toContain(
          "local_experience_operation_unavailable",
        );
      },
    );

    it("runs the complete standalone install at the platform default root", async () => {
      vi.clearAllMocks();
      const home = "/tmp/local-experience-home";
      const installRoot = defaultInstallRoot(process.platform, home);
      const output = captureStdout();
      let code: number;
      try {
        code = await productRun(
          ["install", "--source", "/tmp/dist", "--home", home, "--yes"],
          {},
          { waitForRuntimeReady: async () => true },
        );
      } finally {
        output.restore();
      }
      expect(code!).toBe(0);
      expect(runtimeMocks.planLocalInstall).toHaveBeenCalledWith(
        expect.objectContaining({ installRoot }),
      );
      expect(runtimeMocks.applyLocalInstall).toHaveBeenCalledOnce();
      expect(runtimeMocks.installCliLink).toHaveBeenCalledOnce();
      expect(lifecycleMocks.applyPreparedServiceInstall).toHaveBeenCalledOnce();
      expect(JSON.parse(output.value())).toMatchObject({ apply: true });
    });

    it("keeps normal upgrade and its compensation path enabled", async () => {
      vi.clearAllMocks();
      runtimeMocks.planCliLink.mockResolvedValueOnce({
        schema_version: 1,
        platform: "linux",
        home: "/tmp/home",
        install_root: "/tmp/install",
        bin_directory: "/tmp/bin",
        status: "present",
        blocked_reason: null,
        link_path: "/tmp/bin/one-memory",
        target_path: "/tmp/install/current/bin/one-memory",
      });
      const output = captureStdout();
      let code: number;
      try {
        code = await productRun(
          [
            "upgrade",
            "--source",
            "/tmp/dist",
            "--home",
            "/tmp/home",
            "--install-root",
            "/tmp/install",
            "--yes",
          ],
          {},
          { waitForRuntimeReady: async () => true },
        );
      } finally {
        output.restore();
      }
      expect(code!).toBe(0);
      expect(runtimeMocks.applyLocalInstall).toHaveBeenCalledOnce();
      expect(runtimeMocks.restartUserService).toHaveBeenCalledOnce();
      expect(runtimeMocks.rollbackLocalInstall).not.toHaveBeenCalled();
    });
  });

  describe("explicit service data identity", () => {
    const serviceArgs = [
      "--platform",
      "linux",
      "--home",
      "/tmp/selected-home",
      "--install-root",
      "/tmp/install",
    ];

    it.each(["service-plan", "service-install", "service-uninstall"])(
      "%s keeps env data and lets an explicit flag override it",
      async (command) => {
        for (const flag of [false, true]) {
          const output = captureStdout();
          try {
            expect(
              await run(
                [
                  command,
                  ...serviceArgs,
                  ...(flag ? ["--data-dir", "/tmp/flag data"] : []),
                ],
                { ONE_MEMORY_DATA_DIR: "/tmp/env data" },
              ),
            ).toBe(0);
            expect(JSON.parse(output.value()).service_file.content).toContain(
              `ONE_MEMORY_DATA_DIR=${flag ? "/tmp/flag data" : "/tmp/env data"}`,
            );
          } finally {
            output.restore();
          }
        }
      },
    );

    it.each(["service-install", "service-uninstall"])(
      "%s applies the same explicitly selected data directory",
      async (command) => {
        vi.clearAllMocks();
        const output = captureStdout();
        try {
          expect(
            await run(
              [
                command,
                ...serviceArgs,
                "--data-dir",
                "/tmp/flag data",
                "--yes",
              ],
              { ONE_MEMORY_DATA_DIR: "/tmp/env data" },
              command === "service-install"
                ? { waitForRuntimeReady: async () => true }
                : {},
            ),
          ).toBe(0);
          const mutation =
            command === "service-install"
              ? runtimeMocks.installUserService
              : runtimeMocks.uninstallUserService;
          expect(mutation).toHaveBeenCalledWith(
            expect.objectContaining({
              dataDir: "/tmp/flag data",
              installRoot: "/tmp/install/current",
            }),
            expect.any(Object),
          );
        } finally {
          output.restore();
        }
      },
    );

    it.each(["install", "upgrade"])(
      "%s preview retains env/flag/default identity",
      async (command) => {
        for (const data of [undefined, "/tmp/env data", "/tmp/flag data"]) {
          runtimeMocks.planCliLink.mockResolvedValueOnce({
            schema_version: 1,
            platform: "linux",
            home: "/tmp/selected-home",
            install_root: "/tmp/install",
            bin_directory: "/tmp/bin",
            status: "present",
            blocked_reason: null,
            link_path: "/tmp/bin/one-memory",
            target_path: "/tmp/install/current/bin/one-memory",
          });
          const output = captureStdout();
          try {
            expect(
              await run(
                [
                  command,
                  "--source",
                  "/tmp/dist",
                  "--home",
                  "/tmp/selected-home",
                  "--install-root",
                  "/tmp/install",
                  ...(data === "/tmp/flag data" ? ["--data-dir", data] : []),
                ],
                data === undefined
                  ? {}
                  : { ONE_MEMORY_DATA_DIR: "/tmp/env data" },
              ),
            ).toBe(0);
            expect(
              JSON.parse(output.value()).service.service_file.content,
            ).toContain(
              `ONE_MEMORY_DATA_DIR=${data ?? "/tmp/selected-home/.one-memory"}`,
            );
          } finally {
            output.restore();
          }
        }
      },
    );

    it.each(["install", "upgrade"])(
      "%s passes allowDowngrade only when explicitly requested",
      async (command) => {
        for (const allowed of [false, true]) {
          vi.clearAllMocks();
          runtimeMocks.planCliLink.mockResolvedValueOnce({
            schema_version: 1,
            platform: "linux",
            home: "/tmp/selected-home",
            install_root: "/tmp/install",
            bin_directory: "/tmp/bin",
            status: "present",
            blocked_reason: null,
            link_path: "/tmp/bin/one-memory",
            target_path: "/tmp/install/current/bin/one-memory",
          });
          const output = captureStdout();
          try {
            expect(
              await run(
                [
                  command,
                  "--source",
                  "/tmp/dist",
                  "--home",
                  "/tmp/selected-home",
                  "--install-root",
                  "/tmp/install",
                  ...(allowed ? ["--allow-downgrade"] : []),
                ],
                {},
              ),
            ).toBe(0);
            expect(runtimeMocks.planLocalInstall).toHaveBeenCalledWith(
              expect.objectContaining({
                source: "/tmp/dist",
                installRoot: "/tmp/install",
                allowDowngrade: allowed,
              }),
            );
          } finally {
            output.restore();
          }
        }
      },
    );

    it("install apply carries env data without changing independent authorization", async () => {
      vi.clearAllMocks();
      const output = captureStdout();
      try {
        expect(
          await run(
            [
              "install",
              "--source",
              "/tmp/dist",
              "--home",
              "/tmp/home",
              "--install-root",
              "/tmp/install",
              "--yes",
            ],
            { ONE_MEMORY_DATA_DIR: "/tmp/env data" },
            { waitForRuntimeReady: async () => true },
          ),
        ).toBe(0);
        expect(runtimeMocks.installUserService).toHaveBeenCalledWith(
          expect.objectContaining({ dataDir: "/tmp/env data" }),
          expect.any(Object),
        );
        expect(runtimeMocks.applyLocalInstall).toHaveBeenCalledOnce();
      } finally {
        output.restore();
      }
    });

    // C8 74a7c9e: an install whose HOME is an isolated temp dir must not add
    // any entry to that HOME (no `.one-memory` receipt, no `.zprofile`). The
    // receipt is only for a real account-home install, where a later zero-env
    // short command has no other way to learn the data dir.
    it("isolated install writes nothing into the caller's HOME", async () => {
      vi.clearAllMocks();
      const home = await mkdtemp(path.join(tmpdir(), "memhub-receipt-home-"));
      try {
        const output = captureStdout();
        try {
          expect(
            await run(
              [
                "install",
                "--source",
                "/tmp/dist",
                "--home",
                home,
                "--install-root",
                path.join(home, "install"),
                "--data-dir",
                path.join(home, "custom data"),
                "--yes",
              ],
              { HOME: home },
              { waitForRuntimeReady: async () => true },
            ),
          ).toBe(0);
        } finally {
          output.restore();
        }
        const entries = await readdir(home);
        expect(entries).not.toContain(".one-memory");
        expect(entries).not.toContain(".zprofile");
        expect(entries).not.toContain(".profile");
      } finally {
        await rm(home, { recursive: true, force: true });
      }
    });

    it("uses the managed current layout without appending current twice", async () => {
      for (const installRoot of ["/tmp/install", "/tmp/install/current"]) {
        const output = captureStdout();
        try {
          expect(
            await run([
              "service-plan",
              "--platform",
              "linux",
              "--home",
              "/tmp/home",
              "--install-root",
              installRoot,
            ]),
          ).toBe(0);
          expect(JSON.parse(output.value()).service_file.content).toContain(
            "/tmp/install/current/runtime/runtime.cjs",
          );
        } finally {
          output.restore();
        }
      }
    });

    it("uses one service identity for macOS lexical and physical tmp paths", async () => {
      if (process.platform !== "darwin") return;
      const root = await mkdtemp("/tmp/one-memory-service-root-");
      try {
        const installRoot = await mkdtemp(path.join(root, "install-"));
        const physicalRoot = await realpath(installRoot);
        const physicalParent = await realpath(root);
        const serviceIds = async (candidates: string[]) => {
          const ids: string[] = [];
          for (const candidate of candidates) {
            const output = captureStdout();
            try {
              expect(
                await run([
                  "service-plan",
                  "--platform",
                  "darwin",
                  "--home",
                  path.join(root, "home"),
                  "--install-root",
                  candidate,
                  "--data-dir",
                  path.join(root, "data"),
                ]),
              ).toBe(0);
              ids.push(
                (JSON.parse(output.value()) as { service_id: string })
                  .service_id,
              );
            } finally {
              output.restore();
            }
          }
          return ids;
        };
        expect(
          new Set(
            await serviceIds([
              installRoot,
              path.join(installRoot, "current"),
              physicalRoot,
              path.join(physicalRoot, "current"),
            ]),
          ),
        ).toHaveProperty("size", 1);
        expect(
          new Set(
            await serviceIds([
              path.join(root, "missing", "install"),
              path.join(root, "missing", "install", "current"),
              path.join(physicalParent, "missing", "install"),
              path.join(physicalParent, "missing", "install", "current"),
            ]),
          ),
        ).toHaveProperty("size", 1);
      } finally {
        await rm(root, { recursive: true, force: true });
      }
    });

    it("explicit data flag overrides an invalid environment without trimming", async () => {
      const output = captureStdout();
      try {
        expect(
          await run(
            ["service-plan", ...serviceArgs, "--data-dir", "/tmp/data "],
            { ONE_MEMORY_DATA_DIR: "relative" },
          ),
        ).toBe(0);
        expect(JSON.parse(output.value()).service_file.content).toContain(
          'ONE_MEMORY_DATA_DIR=/tmp/data "',
        );
      } finally {
        output.restore();
      }
    });

    it("upgrade failure restarts the restored artifact with the same data identity", async () => {
      vi.clearAllMocks();
      runtimeMocks.planCliLink.mockResolvedValueOnce({
        schema_version: 1,
        platform: "linux",
        home: "/tmp/home",
        install_root: "/tmp/install",
        bin_directory: "/tmp/bin",
        status: "present",
        blocked_reason: null,
        link_path: "/tmp/bin/one-memory",
        target_path: "/tmp/install/current/bin/one-memory",
      });
      runtimeMocks.restartUserService.mockRejectedValueOnce(
        new Error("restart failed"),
      );
      const streams = captureStreams();
      let code: number;
      try {
        code = await run(
          [
            "upgrade",
            "--source",
            "/tmp/dist",
            "--home",
            "/tmp/home",
            "--install-root",
            "/tmp/install",
            "--data-dir",
            "/tmp/custom data",
            "--yes",
          ],
          { ONE_MEMORY_DATA_DIR: "/tmp/other" },
        );
      } finally {
        streams.restore();
      }
      expect(code!).toBe(1);
      expect(JSON.parse(streams.stderr())).toMatchObject({
        command: "upgrade",
        code: "restart failed",
      });
      expect(runtimeMocks.rollbackLocalInstall).toHaveBeenCalledOnce();
      // The CLI manages the objective aliases the package does not own
      // (`memhub` + `memhub-cli`); both are rolled back.
      expect(aliasMocks.rollbackManagedCliLink).toHaveBeenCalledTimes(2);
      expect(runtimeMocks.restartUserService).toHaveBeenCalledTimes(2);
      for (const call of runtimeMocks.restartUserService.mock
        .calls as unknown[][]) {
        expect(call[0]).toMatchObject({
          dataDir: "/tmp/custom data",
          installRoot: "/tmp/install/current",
        });
      }
    });

    // O-22 (2026-09-29 C8 H17): `upgrade --yes` on a manifest-valid but
    // crash-looping version reported exit 0 and left `current` on the bad
    // version. A restart that merely returns is not proof the new Runtime came
    // up, so the CLI now verifies health and rolls back if it never does.
    it("rolls back and fails when the upgraded Runtime never becomes healthy", async () => {
      vi.clearAllMocks();
      runtimeMocks.planCliLink.mockResolvedValueOnce({
        schema_version: 1,
        platform: "linux",
        home: "/tmp/home",
        install_root: "/tmp/install",
        bin_directory: "/tmp/bin",
        status: "present",
        blocked_reason: null,
        link_path: "/tmp/bin/one-memory",
        target_path: "/tmp/install/current/bin/one-memory",
      });
      const streams = captureStreams();
      let code: number;
      try {
        code = await run(
          [
            "upgrade",
            "--source",
            "/tmp/dist",
            "--home",
            "/tmp/home",
            "--install-root",
            "/tmp/install",
            "--data-dir",
            "/tmp/custom data",
            "--yes",
          ],
          {},
          { waitForRuntimeReady: async () => false },
        );
      } finally {
        streams.restore();
      }
      expect(code!).toBe(1);
      expect(JSON.parse(streams.stderr())).toMatchObject({
        command: "upgrade",
        code: "upgrade_health_check_failed",
      });
      // Rolled back to the previous version and restarted it.
      expect(runtimeMocks.rollbackLocalInstall).toHaveBeenCalledOnce();
      expect(runtimeMocks.restartUserService).toHaveBeenCalledTimes(2);
    });

    const upgradeArguments = [
      "upgrade",
      "--source",
      "/tmp/dist",
      "--home",
      "/tmp/home",
      "--install-root",
      "/tmp/install",
      "--data-dir",
      "/tmp/custom data",
      "--yes",
    ];
    const presentCliLink = {
      schema_version: 1 as const,
      platform: "linux" as const,
      home: "/tmp/home",
      install_root: "/tmp/install",
      bin_directory: "/tmp/bin",
      status: "present" as const,
      blocked_reason: null,
      link_path: "/tmp/bin/one-memory",
      target_path: "/tmp/install/current/bin/one-memory",
    };

    it("migrates the home with the new Core before restarting the Runtime", async () => {
      vi.clearAllMocks();
      runtimeMocks.planCliLink.mockResolvedValueOnce(presentCliLink);
      const order: string[] = [];
      runtimeMocks.runSdkMigration.mockImplementationOnce(async () => {
        order.push("migrate");
        return { state: "cutover" };
      });
      runtimeMocks.restartUserService.mockImplementationOnce(async () => {
        order.push("restart");
      });
      const output = captureStdout();
      let code: number;
      try {
        code = await run(
          upgradeArguments,
          {},
          { waitForRuntimeReady: async () => true },
        );
      } finally {
        output.restore();
      }
      expect(code!).toBe(0);
      expect(order).toEqual(["migrate", "restart"]);
      expect(runtimeMocks.runSdkMigration).toHaveBeenCalledWith(
        expect.objectContaining({ dataDir: "/tmp/custom data" }),
      );
    });

    it("rolls back to the previous version when the migration does not finish", async () => {
      vi.clearAllMocks();
      runtimeMocks.planCliLink.mockResolvedValueOnce(presentCliLink);
      runtimeMocks.runSdkMigration.mockResolvedValueOnce({
        state: "deferred",
        code: "sdk_migration_disk_space_insufficient",
      });
      const streams = captureStreams();
      let code: number;
      try {
        code = await run(
          upgradeArguments,
          {},
          { waitForRuntimeReady: async () => true },
        );
      } finally {
        streams.restore();
      }
      expect(code!).toBe(1);
      expect(JSON.parse(streams.stderr())).toMatchObject({
        command: "upgrade",
        code: "sdk_migration_disk_space_insufficient",
      });
      expect(runtimeMocks.rollbackLocalInstall).toHaveBeenCalledOnce();
    });

    it("keeps the new version when a migrated home is slow to come up", async () => {
      vi.clearAllMocks();
      runtimeMocks.planCliLink.mockResolvedValueOnce(presentCliLink);
      runtimeMocks.runSdkMigration.mockResolvedValueOnce({ state: "cutover" });
      const streams = captureStreams();
      let code: number;
      try {
        code = await run(
          upgradeArguments,
          {},
          { waitForRuntimeReady: async () => false },
        );
      } finally {
        streams.restore();
      }
      expect(code!).toBe(1);
      expect(JSON.parse(streams.stderr())).toMatchObject({
        command: "upgrade",
        code: "runtime_not_ready_after_sdk_migration",
      });
      expect(runtimeMocks.rollbackLocalInstall).not.toHaveBeenCalled();
    });

    it("reports success once the upgraded Runtime is healthy", async () => {
      vi.clearAllMocks();
      runtimeMocks.planCliLink.mockResolvedValueOnce({
        schema_version: 1,
        platform: "linux",
        home: "/tmp/home",
        install_root: "/tmp/install",
        bin_directory: "/tmp/bin",
        status: "present",
        blocked_reason: null,
        link_path: "/tmp/bin/one-memory",
        target_path: "/tmp/install/current/bin/one-memory",
      });
      const output = captureStdout();
      let code: number;
      try {
        code = await run(
          [
            "upgrade",
            "--source",
            "/tmp/dist",
            "--home",
            "/tmp/home",
            "--install-root",
            "/tmp/install",
            "--data-dir",
            "/tmp/custom data",
            "--yes",
          ],
          {},
          { waitForRuntimeReady: async () => true },
        );
      } finally {
        output.restore();
      }
      expect(code!).toBe(0);
      expect(runtimeMocks.rollbackLocalInstall).not.toHaveBeenCalled();
    });

    it.each([
      "service-plan",
      "service-install",
      "service-uninstall",
      "install",
      "upgrade",
    ])(
      "%s rejects invalid and duplicate data dirs before planning or mutations",
      async (command) => {
        for (const invalid of [
          "",
          "relative",
          "/tmp/a\nnext",
          "/tmp/a\rnext",
          "/tmp/a\0next",
        ]) {
          vi.clearAllMocks();
          const base = command.startsWith("service-")
            ? serviceArgs
            : ["--source", "/tmp/dist"];
          expect(await run([command, ...base, "--data-dir", invalid], {})).toBe(
            2,
          );
          expect(
            await run([command, ...base], { ONE_MEMORY_DATA_DIR: invalid }),
          ).toBe(2);
          expect(runtimeMocks.planLocalInstall).not.toHaveBeenCalled();
          expect(runtimeMocks.installUserService).not.toHaveBeenCalled();
          expect(runtimeMocks.uninstallUserService).not.toHaveBeenCalled();
        }
        expect(
          await run(
            [
              command,
              ...(command.startsWith("service-") ? serviceArgs : []),
              "--data-dir",
              "/tmp/a",
              "--data-dir",
              "/tmp/b",
            ],
            {},
          ),
        ).toBe(2);
      },
    );
  });

  it("rejects unsupported commands without changing the system", async () => {
    expect(await run(["unknown"])).toBe(2);
    expect(
      await run([
        "service-plan",
        "--platform",
        "linux",
        "--home",
        "/home/example",
        "--install-root",
        "/opt/one-memory",
        "--typo",
        "true",
      ]),
    ).toBe(2);
  });

  it("uses platform-native default install roots", () => {
    expect(defaultInstallRoot("darwin", "/Users/example")).toBe(
      "/Users/example/Library/Application Support/One Memory",
    );
    expect(defaultInstallRoot("linux", "/home/example")).toBe(
      "/home/example/.local/share/one-memory",
    );
    expect(defaultInstallRoot("win32", "C:\\Users\\Example")).toBe(
      "C:\\Users\\Example\\AppData\\Local\\One Memory",
    );
    expect(
      defaultInstallRoot("win32", "C:\\Users\\Example", "D:\\Local Data"),
    ).toBe("D:\\Local Data\\One Memory");
  });

  it("gives a custom install root its own Core port but leaves the daily root alone", async () => {
    const planFor = async (home: string, installRoot: string) => {
      const output = captureStdout();
      try {
        expect(
          await run(
            [
              "service-plan",
              "--platform",
              "darwin",
              "--home",
              home,
              "--install-root",
              installRoot,
              "--data-dir",
              `${home}/data`,
              "--uid",
              "501",
            ],
            {},
          ),
        ).toBe(0);
        return JSON.parse(output.value()).service_file.content as string;
      } finally {
        output.restore();
      }
    };
    const home = "/Users/example";
    // Two custom roots must not both fall back to the fixed 8765: that is the
    // `uninstall_refuses_when_data_lives_inside_the_removed_subtree` failure
    // (the second root's Core died with EADDRINUSE).
    const a = await planFor(home, "/Users/example/custom-A");
    const b = await planFor(home, "/Users/example/custom-B");
    const port = (plan: string) =>
      plan.match(/ONE_MEMORY_CORE_PORT<\/key><string>(\d+)<\/string>/)?.[1] ??
      null;
    expect(port(a)).not.toBeNull();
    expect(port(b)).not.toBeNull();
    expect(port(a)).not.toBe(port(b));

    // The daily install must keep the fixed default with no override key.
    const daily = await planFor(
      home,
      `${home}/Library/Application Support/One Memory`,
    );
    expect(port(daily)).toBeNull();
  });

  it("prints a blocked Windows service plan instead of pretending support", async () => {
    let output = "";
    const original = process.stdout.write;
    process.stdout.write = ((value: string) => {
      output += value;
      return true;
    }) as typeof process.stdout.write;
    try {
      expect(
        await run([
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

  it("previews a local install unless --yes is explicit", async () => {
    runtimeMocks.applyLocalInstall.mockClear();
    runtimeMocks.installUserService.mockClear();
    runtimeMocks.installCliLink.mockClear();
    const output = captureStdout();
    const installRoot = await mkdtemp(path.join(tmpdir(), "om-cli-install-"));
    try {
      expect(
        await run([
          "install",
          "--source",
          "/tmp/distribution",
          "--install-root",
          installRoot,
        ]),
      ).toBe(0);
      expect(JSON.parse(output.value()).apply).toBe(false);
      expect(runtimeMocks.applyLocalInstall).not.toHaveBeenCalled();
      expect(runtimeMocks.installUserService).not.toHaveBeenCalled();
      expect(runtimeMocks.installCliLink).not.toHaveBeenCalled();
    } finally {
      output.restore();
    }
  });

  it.each(["install", "upgrade"])(
    "%s previews the matching remote Runtime without downloading the artifact",
    async (command) => {
      const resolveRelease = vi.fn(async () => ({
        release: {
          schema_version: 1 as const,
          product: "memhub-release" as const,
          version: "0.1.0-internal.1",
          build_number: 1,
          git_commit: "a".repeat(40),
          channel: "prerelease" as const,
          cli_protocol: 1,
          supported_runtime_protocol: { min: 1, max: 1 },
          runtime_protocol: 1,
          supported_cli_protocol: { min: 1, max: 1 },
          published_at: "2026-10-13T06:00:00Z",
          artifacts: [],
        },
        artifact: {
          kind: "runtime" as const,
          platform: (process.platform === "darwin" ? "darwin" : "linux") as
            | "darwin"
            | "linux",
          arch: (process.arch === "arm64" ? "arm64" : "x64") as "arm64" | "x64",
          format: "tar.gz" as const,
          filename: "memhub-runtime.tar.gz",
          url: "https://downloads.example.test/runtime.tar.gz",
          bytes: 300_000_000,
          unpacked_bytes: 700_000_000,
          sha256: "a".repeat(64),
          runtime_manifest_schema: 1 as const,
        },
        manifest_url: "https://downloads.example.test/release-manifest.json",
        allowed_hosts: ["downloads.example.test"],
      }));
      const acquireRelease = vi.fn(async () => {
        throw new Error("preview_downloaded_runtime");
      });
      const output = captureStdout();
      try {
        expect(
          await productRun(
            [
              command,
              "--home",
              "/tmp/remote-home",
              "--install-root",
              "/tmp/remote-install",
              "--release-manifest-url",
              "https://downloads.example.test/release-manifest.json",
              "--json",
            ],
            {},
            {
              resolveRuntimeRelease: resolveRelease as never,
              acquireRuntimeDistribution: acquireRelease as never,
            },
          ),
        ).toBe(0);
      } finally {
        output.restore();
      }
      expect(resolveRelease).toHaveBeenCalledOnce();
      expect(acquireRelease).not.toHaveBeenCalled();
      expect(JSON.parse(output.value())).toMatchObject({
        release: {
          version: "0.1.0-internal.1",
          channel: "prerelease",
          git_commit: "a".repeat(40),
          cli_protocol: 1,
          runtime_protocol: 1,
          download_bytes: 300_000_000,
          install_bytes: 700_000_000,
        },
        install_root: "/tmp/remote-install",
        data_preserved: true,
        apply: false,
      });
    },
  );

  it("fails closed when a source build has no configured Release URL", async () => {
    const streams = captureStreams();
    try {
      expect(
        await productRun(
          [
            "install",
            "--home",
            "/tmp/no-release-home",
            "--install-root",
            "/tmp/no-release-install",
            "--json",
          ],
          {},
        ),
      ).toBe(1);
      expect(streams.stdout()).toBe("");
      expect(JSON.parse(streams.stderr())).toMatchObject({
        status: "error",
        code: "release_manifest_url_unavailable",
      });
    } finally {
      streams.restore();
    }
  });

  it("uses a runtime-configured same-version Release template", async () => {
    const resolveRelease = vi.fn(async (options: { manifestUrl: string }) => {
      throw new Error(`observed:${options.manifestUrl}`);
    });
    const output = captureStdout();
    try {
      expect(
        await productRun(
          [
            "install",
            "--home",
            "/tmp/template-home",
            "--install-root",
            "/tmp/template-install",
            "--json",
          ],
          {
            MEMHUB_RELEASE_MANIFEST_URL_TEMPLATE:
              "https://releases.example.test/v{version}/release-manifest.json",
          },
          { resolveRuntimeRelease: resolveRelease as never },
        ),
      ).toBe(1);
    } finally {
      output.restore();
    }
    expect(resolveRelease).toHaveBeenCalledWith(
      expect.objectContaining({
        manifestUrl: `https://releases.example.test/v${ROOT_VERSION}/release-manifest.json`,
      }),
    );
  });

  it("downloads a remote Runtime only for --yes and cleans its temporary directory", async () => {
    const cleanup = vi.fn(async () => undefined);
    const acquireRelease = vi.fn(async () => ({
      release: {
        schema_version: 1 as const,
        product: "memhub-release" as const,
        version: "0.1.0-internal.1",
        build_number: 1,
        git_commit: "a".repeat(40),
        channel: "prerelease" as const,
        cli_protocol: 1,
        supported_runtime_protocol: { min: 1, max: 1 },
        runtime_protocol: 1,
        supported_cli_protocol: { min: 1, max: 1 },
        published_at: "2026-10-13T06:00:00Z",
        artifacts: [],
      },
      artifact: {
        kind: "runtime" as const,
        platform: "linux" as const,
        arch: "x64" as const,
        format: "tar.gz" as const,
        filename: "runtime.tar.gz",
        url: "https://downloads.example.test/runtime.tar.gz",
        bytes: 1,
        unpacked_bytes: 1,
        sha256: "a".repeat(64),
        runtime_manifest_schema: 1 as const,
      },
      temporary_root: "/tmp/download-root",
      distribution: "/tmp/download-root/distribution",
      cleanup,
      [Symbol.asyncDispose]: cleanup,
    }));
    const output = captureStdout();
    try {
      expect(
        await productRun(
          [
            "install",
            "--home",
            "/tmp/remote-home",
            "--install-root",
            "/tmp/remote-install",
            "--release-manifest-url",
            "https://downloads.example.test/release-manifest.json",
            "--yes",
            "--json",
          ],
          {},
          {
            acquireRuntimeDistribution: acquireRelease as never,
            waitForRuntimeReady: async () => true,
          },
        ),
      ).toBe(0);
    } finally {
      output.restore();
    }
    expect(acquireRelease).toHaveBeenCalledOnce();
    expect(runtimeMocks.planLocalInstall).toHaveBeenCalledWith(
      expect.objectContaining({ source: "/tmp/download-root/distribution" }),
    );
    expect(runtimeMocks.applyLocalInstall).toHaveBeenCalledOnce();
    expect(cleanup).toHaveBeenCalledOnce();
  });

  it("keeps --source fully offline", async () => {
    const resolveRelease = vi.fn(async () => {
      throw new Error("local_source_used_network");
    });
    const acquireRelease = vi.fn(async () => {
      throw new Error("local_source_used_network");
    });
    const output = captureStdout();
    try {
      expect(
        await run(
          [
            "install",
            "--source",
            "/tmp/offline-distribution",
            "--install-root",
            "/tmp/offline-install",
          ],
          {},
          {
            resolveRuntimeRelease: resolveRelease as never,
            acquireRuntimeDistribution: acquireRelease as never,
          },
        ),
      ).toBe(0);
    } finally {
      output.restore();
    }
    expect(resolveRelease).not.toHaveBeenCalled();
    expect(acquireRelease).not.toHaveBeenCalled();
    expect(runtimeMocks.planLocalInstall).toHaveBeenCalledWith(
      expect.objectContaining({ source: "/tmp/offline-distribution" }),
    );
  });

  it("applies a verified local install only with --yes", async () => {
    runtimeMocks.applyLocalInstall.mockClear();
    runtimeMocks.installUserService.mockClear();
    runtimeMocks.installCliLink.mockClear();
    aliasMocks.installManagedCliLink.mockClear();
    const output = captureStdout();
    try {
      expect(
        await run(
          [
            "install",
            "--source",
            "/tmp/distribution",
            "--install-root",
            "/tmp/one-memory",
            "--yes",
          ],
          {},
          { waitForRuntimeReady: async () => true },
        ),
      ).toBe(0);
    } finally {
      output.restore();
    }
    expect(runtimeMocks.applyLocalInstall).toHaveBeenCalledOnce();
    expect(runtimeMocks.installCliLink).toHaveBeenCalledOnce();
    expect(runtimeMocks.installUserService).toHaveBeenCalledOnce();
    expect(aliasMocks.installManagedCliLink).toHaveBeenCalledWith(
      expect.objectContaining({
        link_path: expect.stringMatching(/\/memhub$/),
      }),
    );
    expect(JSON.parse(output.value()).apply).toBe(true);
  });

  it("rolls back the distribution when service installation fails", async () => {
    runtimeMocks.applyLocalInstall.mockResolvedValueOnce({
      schema_version: 1,
      source: "/tmp/distribution",
      install_root: "/tmp/one-memory",
      version_directory: "/tmp/one-memory/versions/1.0.0-linux-x64",
      current_link: "/tmp/one-memory/current",
      version: "1.0.0",
      platform: "linux",
      arch: "x64",
      files: 9,
      previous_current_target: null,
      created_version: true,
    });
    runtimeMocks.installUserService.mockRejectedValueOnce(
      new Error("service failed"),
    );
    runtimeMocks.rollbackLocalInstall.mockClear();
    runtimeMocks.rollbackCliLink.mockClear();
    aliasMocks.rollbackManagedCliLink.mockClear();

    const streams = captureStreams();
    let code: number;
    try {
      code = await run([
        "install",
        "--source",
        "/tmp/distribution",
        "--install-root",
        "/tmp/one-memory",
        "--yes",
      ]);
    } finally {
      streams.restore();
    }
    // A failed install now reports a structured error instead of throwing.
    expect(code!).toBe(1);
    expect(JSON.parse(streams.stderr())).toMatchObject({
      command: "install",
      status: "error",
      code: "service failed",
    });
    expect(runtimeMocks.rollbackLocalInstall).toHaveBeenCalledOnce();
    expect(runtimeMocks.rollbackCliLink).toHaveBeenCalledOnce();
    expect(aliasMocks.rollbackManagedCliLink).toHaveBeenCalledTimes(2);
  });

  it("upgrades only an existing managed CLI and rolls back a failed restart", async () => {
    runtimeMocks.planCliLink.mockResolvedValueOnce({
      schema_version: 1,
      platform: "linux",
      home: "/tmp/home",
      install_root: "/tmp/one-memory",
      bin_directory: "/tmp/home/.local/bin",
      status: "present",
      blocked_reason: null,
      link_path: "/tmp/home/.local/bin/one-memory",
      target_path: "/tmp/one-memory/current/bin/one-memory",
    });
    runtimeMocks.restartUserService.mockRejectedValueOnce(
      new Error("restart failed"),
    );
    runtimeMocks.rollbackLocalInstall.mockClear();
    runtimeMocks.installCliLink.mockClear();

    const streams = captureStreams();
    let code: number;
    try {
      code = await run([
        "upgrade",
        "--source",
        "/tmp/distribution",
        "--install-root",
        "/tmp/one-memory",
        "--home",
        "/tmp/home",
        "--yes",
      ]);
    } finally {
      streams.restore();
    }
    expect(code!).toBe(1);
    expect(JSON.parse(streams.stderr())).toMatchObject({
      command: "upgrade",
      status: "error",
      code: "restart failed",
    });
    expect(runtimeMocks.rollbackLocalInstall).toHaveBeenCalledOnce();
    expect(runtimeMocks.installCliLink).not.toHaveBeenCalled();
  });

  it("previews service installation and applies only with --yes", async () => {
    runtimeMocks.installUserService.mockClear();
    const output = captureStdout();
    const platform = process.platform === "darwin" ? "darwin" : "linux";
    try {
      expect(
        await run([
          "service-install",
          "--platform",
          platform,
          "--home",
          "/tmp/home",
          "--install-root",
          "/tmp/install",
        ]),
      ).toBe(0);
      expect(runtimeMocks.installUserService).not.toHaveBeenCalled();
    } finally {
      output.restore();
    }

    const applied = captureStdout();
    try {
      expect(
        await run(
          [
            "service-install",
            "--platform",
            platform,
            "--home",
            "/tmp/home",
            "--install-root",
            "/tmp/install",
            "--yes",
          ],
          {},
          { waitForRuntimeReady: async () => true },
        ),
      ).toBe(0);
    } finally {
      applied.restore();
    }
    expect(runtimeMocks.installUserService).toHaveBeenCalledOnce();
  });

  it("previews service removal and applies only with --yes", async () => {
    runtimeMocks.uninstallUserService.mockClear();
    const platform = process.platform === "darwin" ? "darwin" : "linux";
    const preview = captureStdout();
    try {
      expect(
        await run([
          "service-uninstall",
          "--platform",
          platform,
          "--home",
          "/tmp/home",
          "--install-root",
          "/tmp/install",
        ]),
      ).toBe(0);
      expect(runtimeMocks.uninstallUserService).not.toHaveBeenCalled();
    } finally {
      preview.restore();
    }

    const applied = captureStdout();
    try {
      expect(
        await run([
          "service-uninstall",
          "--platform",
          platform,
          "--home",
          "/tmp/home",
          "--install-root",
          "/tmp/install",
          "--yes",
        ]),
      ).toBe(0);
    } finally {
      applied.restore();
    }
    expect(runtimeMocks.uninstallUserService).toHaveBeenCalledOnce();
  });

  it("product uninstall removes the managed memhub alias", async () => {
    vi.clearAllMocks();
    const output = captureStdout();
    try {
      expect(
        await run([
          "uninstall",
          "--source",
          "/tmp/distribution",
          "--home",
          "/tmp/home",
          "--install-root",
          "/tmp/install",
          "--yes",
        ]),
      ).toBe(0);
    } finally {
      output.restore();
    }
    expect(aliasMocks.removeManagedCliLink).toHaveBeenCalledWith(
      expect.objectContaining({ link_path: "/tmp/home/.local/bin/memhub" }),
    );
  });

  // H15 P0 regression: integ's runtime-control now makes `<bin>/memhub` its
  // primary link and creates the `one-memory` compat itself (it declares
  // `compat_link_path`). The CLI must not re-plan those two — doing so raised
  // `cli_alias_plan_stale`. It still owns the objective's `memhub-cli` name,
  // which the package does not create.
  it("defers to runtime-control for its links but still adds memhub-cli", async () => {
    vi.clearAllMocks();
    runtimeMocks.planCliLink.mockResolvedValueOnce({
      schema_version: 1,
      platform: "linux",
      home: "/tmp/home",
      install_root: "/tmp/install",
      bin_directory: "/tmp/home/.local/bin",
      status: "create",
      blocked_reason: null,
      link_path: "/tmp/home/.local/bin/memhub",
      target_path: "/tmp/install/current/bin/one-memory",
      // The fields integ's `01eedd8` added; the CLI must tolerate them.
      compat_link_path: "/tmp/home/.local/bin/one-memory",
      compat_target_path: "/tmp/install/current/bin/one-memory",
    } as never);
    const output = captureStdout();
    try {
      expect(
        await run(
          [
            "install",
            "--source",
            "/tmp/distribution",
            "--home",
            "/tmp/home",
            "--install-root",
            "/tmp/install",
            "--yes",
          ],
          {},
          { waitForRuntimeReady: async () => true },
        ),
      ).toBe(0);
    } finally {
      output.restore();
    }
    // Runtime-control owns `memhub` + `one-memory`; the CLI plans only the
    // remaining objective alias `memhub-cli`.
    const planned = aliasMocks.planManagedCliLink.mock.calls.map((call) =>
      path.basename((call[1] as string) ?? ""),
    );
    expect(planned).toEqual(["memhub-cli"]);
    // The CLI still installed the distribution and the PATH block.
    expect(runtimeMocks.applyLocalInstall).toHaveBeenCalledOnce();
    expect(shellPathMocks.applyShellPath).toHaveBeenCalled();
  });

  // The objective names `memhub` and `memhub-cli` as the primary entries, and
  // the npm tarball ships all three names. The managed installer must create
  // the same set, otherwise "装完即短指令可用" is only true for the names the
  // installer happened to create.
  it("creates the memhub-cli primary alias during install", async () => {
    vi.clearAllMocks();
    const output = captureStdout();
    try {
      expect(
        await run(
          [
            "install",
            "--source",
            "/tmp/distribution",
            "--home",
            "/tmp/home",
            "--install-root",
            "/tmp/install",
            "--yes",
          ],
          {},
          { waitForRuntimeReady: async () => true },
        ),
      ).toBe(0);
    } finally {
      output.restore();
    }
    const planned = aliasMocks.planManagedCliLink.mock.calls.map((call) =>
      path.basename((call[1] as string) ?? ""),
    );
    // The older link plan owns only `one-memory`, so the CLI must create both
    // objective aliases: `memhub` (primary) and `memhub-cli`.
    expect(new Set(planned)).toEqual(new Set(["memhub", "memhub-cli"]));
  });

  it("removes the CLI-owned memhub-cli alias on uninstall", async () => {
    vi.clearAllMocks();
    runtimeMocks.planCliLink.mockResolvedValueOnce({
      schema_version: 1,
      platform: "linux",
      home: "/tmp/home",
      install_root: "/tmp/install",
      bin_directory: "/tmp/home/.local/bin",
      status: "present",
      blocked_reason: null,
      link_path: "/tmp/home/.local/bin/memhub",
      target_path: "/tmp/install/current/bin/one-memory",
      compat_link_path: "/tmp/home/.local/bin/one-memory",
      compat_target_path: "/tmp/install/current/bin/one-memory",
    } as never);
    const output = captureStdout();
    try {
      expect(
        await run([
          "uninstall",
          "--source",
          "/tmp/distribution",
          "--home",
          "/tmp/home",
          "--install-root",
          "/tmp/install",
          "--yes",
        ]),
      ).toBe(0);
    } finally {
      output.restore();
    }
    // runtime-control removes `memhub` + `one-memory`; the CLI removes the one
    // objective alias it created and the package does not own (`memhub-cli`).
    expect(aliasMocks.removeManagedCliLink).toHaveBeenCalledWith(
      expect.objectContaining({
        link_path: "/tmp/home/.local/bin/memhub-cli",
      }),
    );
  });

  // C8 P1 (2026-09-29): runtime-control's `applyLocalUninstall` only removes
  // `plan.cli_link`; it never removes the `compat_link_path` it created. So the
  // CLI must NOT treat the compat name as "owned by the package" on uninstall —
  // otherwise nobody removes it and a dangling `one-memory` symlink survives
  // (its target `current/` is deleted first).
  it("also removes runtime-control's compat link so uninstall leaves no dangling entry", async () => {
    vi.clearAllMocks();
    runtimeMocks.planCliLink.mockResolvedValueOnce({
      schema_version: 1,
      platform: "linux",
      home: "/tmp/home",
      install_root: "/tmp/install",
      bin_directory: "/tmp/home/.local/bin",
      status: "present",
      blocked_reason: null,
      link_path: "/tmp/home/.local/bin/memhub",
      target_path: "/tmp/install/current/bin/one-memory",
      compat_link_path: "/tmp/home/.local/bin/one-memory",
      compat_target_path: "/tmp/install/current/bin/one-memory",
    } as never);
    const output = captureStdout();
    try {
      expect(
        await run([
          "uninstall",
          "--source",
          "/tmp/distribution",
          "--home",
          "/tmp/home",
          "--install-root",
          "/tmp/install",
          "--yes",
        ]),
      ).toBe(0);
    } finally {
      output.restore();
    }
    const removed = (
      aliasMocks.removeManagedCliLink.mock.calls as unknown as Array<
        [{ link_path: string }]
      >
    ).map((call) => call[0].link_path);
    expect(removed).toContain("/tmp/home/.local/bin/one-memory");
    expect(removed).toContain("/tmp/home/.local/bin/memhub-cli");
  });
});

function captureStdout() {
  let value = "";
  const original = process.stdout.write;
  process.stdout.write = ((chunk: string) => {
    value += chunk;
    return true;
  }) as typeof process.stdout.write;
  return {
    value: () => value,
    restore: () => (process.stdout.write = original),
  };
}

// A stopped Runtime is not an argument error. These commands attach directly
// (rather than going through the read-command path) and used to fold
// "runtime not running" into `usage_error`/exit 2, so a script could not tell
// a typo from a missing Runtime. They must answer exit 3 + `runtime_not_running`.
describe("stopped Runtime is exit 3, not a usage error", () => {
  it.each([
    ["path", "list", "--json"],
    ["adapter", "list", "--json"],
    ["task", "--id", "nope", "--json"],
    [
      "write",
      "submit",
      "--session",
      "s",
      "--messages-file",
      "/tmp/messages.json",
      "--json",
    ],
    ["backup", "create", "--target", "/tmp/x", "--yes", "--json"],
  ])("%s ... --json exits 3 with runtime_not_running", async (...argv) => {
    const streams = captureStreams();
    let code: number;
    try {
      code = await run(argv as string[], {});
    } finally {
      streams.restore();
    }
    expect(code!).toBe(3);
    const document = JSON.parse(streams.stderr());
    expect(document).toMatchObject({
      code: "runtime_not_running",
      exit_code: 3,
    });
    // Never the usage-error shape.
    expect(document.status).not.toBe("usage_error");
  });

  it.each(["pause", "resume"] as const)(
    "parses sync %s as the direct sync-set command",
    async (action) => {
      const streams = captureStreams();
      try {
        expect(
          await run(["sync", action, "--source", "codex", "--json"], {}),
        ).toBe(3);
      } finally {
        streams.restore();
      }
      expect(JSON.parse(streams.stdout())).toMatchObject({
        state: "unknown",
        code: "runtime_not_running",
      });
      expect(streams.stderr()).toBe("");
    },
  );
});

// The objective requires "稳定 JSON/exit code" for every declared command.
// `start`/`stop`/`restart` accepted only the bare verb, so `--json` came back
// as a usage error (exit 2) — a script could not drive lifecycle in JSON.
describe("lifecycle commands accept --json", () => {
  it.each(["start", "stop", "restart"])(
    "%s --json is not a usage error and emits a JSON document",
    async (command) => {
      const output = captureStdout();
      let code: number;
      try {
        code = await run([command, "--json"], {});
      } finally {
        output.restore();
      }
      // No Runtime is running here, so it stops at the runtime/service check.
      // What matters: it is not rejected as an argument error, and stdout is a
      // single JSON object a script can parse.
      expect(code!).not.toBe(2);
      const text = output.value().trim();
      expect(text.length).toBeGreaterThan(0);
      const document = JSON.parse(text);
      expect(document).toMatchObject({
        schema_version: 1,
        product: PRODUCT,
        command,
      });
      expect(typeof document.state).toBe("string");
    },
  );

  it("restart uses the platform service path or the direct-process fallback", async () => {
    const restartManagedRuntime = vi.fn(async () => "absent" as const);
    const stopRuntime = vi.fn(async () => "stopped" as const);
    const ensureRuntime = vi.fn(async () => "started" as const);
    const output = captureStdout();
    let code: number;
    try {
      code = await run(
        ["restart", "--json"],
        { ONE_MEMORY_DATA_DIR: "/tmp/restart-data" },
        { restartManagedRuntime, stopRuntime, ensureRuntime },
      );
    } finally {
      output.restore();
    }

    expect(code!).toBe(0);
    if (process.platform === "darwin") {
      expect(restartManagedRuntime).toHaveBeenCalledOnce();
      expect(stopRuntime).toHaveBeenCalledOnce();
      expect(ensureRuntime).toHaveBeenCalledOnce();
    } else {
      expect(restartManagedRuntime).not.toHaveBeenCalled();
      expect(stopRuntime).toHaveBeenCalledOnce();
      expect(ensureRuntime).toHaveBeenCalledOnce();
    }
    expect(JSON.parse(output.value())).toMatchObject({
      command: "restart",
      state: "restarted",
      code: "runtime_restarted",
    });
  });
});

// The objective names `memhub host install <host>` (host as a positional). Both
// that and the `--host <id>` flag form must reach the same command; naming both
// at once is ambiguous and refused rather than guessed.
describe("host short form accepts a positional host id", () => {
  it("parses `host install <id>` without a usage error", async () => {
    const streams = captureStreams();
    let code: number;
    try {
      // No Runtime is running in this test, so the command stops at the
      // runtime check. What matters is that it is *not* a usage error (2).
      code = await run(["host", "install", "codex"], {});
    } finally {
      streams.restore();
    }
    expect(code!).not.toBe(2);
  });

  it("parses `host status <id> --json` and reports the host id", async () => {
    const streams = captureStreams();
    try {
      await run(["host", "status", "codex", "--json"], {});
    } finally {
      streams.restore();
    }
    const out = streams.stdout();
    if (out.trim() !== "") {
      expect(JSON.parse(out)).toMatchObject({ host_id: "codex" });
    }
  });

  it("refuses naming the host both positionally and with --host", async () => {
    const streams = captureStreams();
    let code: number;
    try {
      code = await run(["host", "install", "codex", "--host", "cursor"], {});
    } finally {
      streams.restore();
    }
    expect(code!).toBe(2);
  });
});

// A8 (C8 H18r3): the readiness predicate must require `runtime_verified` with an
// C8 H18r4 root-cause (ee07013): `install` registered the service and returned
// 0 without ever calling `waitForRuntimeReady`, unlike start/restart/upgrade.
// On a host where Core cold-start stalls, install therefore reported success
// while `status` was still `runtime_service_running` (not ready).
describe("install gates on Runtime readiness", () => {
  // `install`/`upgrade` plan-stage failures (unmanaged current, missing source)
  // used to escape `planLocalInstall` as a bare thrown string, so `--json` got
  // plain text on stderr instead of the structured envelope every other failure
  // has. A script must get a parseable document.
  it.each(["install", "upgrade"])(
    "%s --json reports a plan-stage failure as structured JSON, not bare text",
    async (command) => {
      vi.clearAllMocks();
      runtimeMocks.planLocalInstall.mockRejectedValueOnce(
        new Error("install_current_unmanaged"),
      );
      const streams = captureStreams();
      let code: number;
      try {
        code = await run(
          [
            command,
            "--source",
            "/tmp/dist",
            "--home",
            "/tmp/home",
            "--install-root",
            "/tmp/install",
            "--bin-dir",
            "/tmp/bin",
            "--data-dir",
            "/tmp/data",
            "--yes",
            "--json",
          ],
          {},
        );
      } finally {
        streams.restore();
      }
      expect(code!).toBe(1);
      const err = streams.stderr().trim();
      expect(err.startsWith("{")).toBe(true);
      const document = JSON.parse(err);
      expect(document).toMatchObject({
        command,
        status: "error",
        code: "install_current_unmanaged",
      });
    },
  );

  function stubInstallPlan() {
    runtimeMocks.planCliLink.mockResolvedValueOnce({
      schema_version: 1,
      platform: "linux",
      home: "/tmp/home",
      install_root: "/tmp/install",
      bin_directory: "/tmp/bin",
      status: "create",
      blocked_reason: null,
      link_path: "/tmp/bin/one-memory",
      target_path: "/tmp/install/current/bin/one-memory",
    });
  }

  it("fails and rolls back when the installed Runtime never becomes ready", async () => {
    vi.clearAllMocks();
    stubInstallPlan();
    const streams = captureStreams();
    let code: number;
    try {
      code = await run(
        [
          "install",
          "--source",
          "/tmp/dist",
          "--home",
          "/tmp/home",
          "--install-root",
          "/tmp/install",
          "--bin-dir",
          "/tmp/bin",
          "--data-dir",
          "/tmp/data",
          "--yes",
        ],
        {},
        { waitForRuntimeReady: async () => false },
      );
    } finally {
      streams.restore();
    }
    expect(code!).toBe(1);
    expect(streams.stderr()).toContain("install_runtime_not_ready");
    expect(runtimeMocks.rollbackLocalInstall).toHaveBeenCalledOnce();
    expect(
      lifecycleMocks.rollbackPreparedServiceInstall,
    ).toHaveBeenCalledOnce();
  });

  it("takes CLI ownership after a successful install over an App-owned Runtime", async () => {
    vi.clearAllMocks();
    stubInstallPlan();
    const root = await mkdtemp(path.join(tmpdir(), "memhub-cli-owner-"));
    const dataDir = path.join(root, "data");
    await claimRuntimeServiceForApp({
      dataDir,
      appBundle: "/Applications/MemHub.app",
      appExecutable: "/Applications/MemHub.app/Contents/MacOS/MemHub",
    });
    const output = captureStdout();
    try {
      expect(
        await run(
          [
            "install",
            "--source",
            "/tmp/dist",
            "--home",
            path.join(root, "home"),
            "--install-root",
            path.join(root, "install"),
            "--bin-dir",
            path.join(root, "bin"),
            "--data-dir",
            dataDir,
            "--yes",
          ],
          {},
          { waitForRuntimeReady: async () => true },
        ),
      ).toBe(0);
      await expect(readRuntimeServiceOwner(dataDir)).resolves.toEqual({
        schema_version: 1,
        owner: "cli",
      });
    } finally {
      output.restore();
      await rm(root, { recursive: true, force: true });
    }
  });

  it("restores App ownership before relaunching the old Runtime after install failure", async () => {
    vi.clearAllMocks();
    stubInstallPlan();
    const root = await mkdtemp(path.join(tmpdir(), "memhub-cli-owner-"));
    const dataDir = path.join(root, "data");
    await claimRuntimeServiceForApp({
      dataDir,
      appBundle: "/Applications/MemHub.app",
      appExecutable: "/Applications/MemHub.app/Contents/MacOS/MemHub",
    });
    lifecycleMocks.rollbackPreparedServiceInstall.mockImplementationOnce(
      async (_prepared: unknown, rollback: () => Promise<void>) => {
        await rollback();
        await expect(readRuntimeServiceOwner(dataDir)).resolves.toMatchObject({
          owner: "app",
        });
      },
    );
    const streams = captureStreams();
    try {
      expect(
        await run(
          [
            "install",
            "--source",
            "/tmp/dist",
            "--home",
            path.join(root, "home"),
            "--install-root",
            path.join(root, "install"),
            "--bin-dir",
            path.join(root, "bin"),
            "--data-dir",
            dataDir,
            "--yes",
          ],
          {},
          { waitForRuntimeReady: async () => false },
        ),
      ).toBe(1);
      await expect(readRuntimeServiceOwner(dataDir)).resolves.toMatchObject({
        owner: "app",
      });
    } finally {
      streams.restore();
      await rm(root, { recursive: true, force: true });
    }
  });

  it("succeeds when the installed Runtime becomes ready", async () => {
    vi.clearAllMocks();
    stubInstallPlan();
    const output = captureStdout();
    let code: number;
    try {
      code = await run(
        [
          "install",
          "--source",
          "/tmp/dist",
          "--home",
          "/tmp/home",
          "--install-root",
          "/tmp/install",
          "--bin-dir",
          "/tmp/bin",
          "--data-dir",
          "/tmp/data",
          "--yes",
        ],
        {},
        { waitForRuntimeReady: async () => true },
      );
    } finally {
      output.restore();
    }
    expect(code!).toBe(0);
    expect(runtimeMocks.rollbackLocalInstall).not.toHaveBeenCalled();
  });
});

// address, not just "a process exists". A launchd pid status
// (`runtime_service_running`, no host/port) once made `start` return in 0s
// while Core was still ~12s from answering.
describe("runtime readiness predicate", () => {
  it("treats a verified runtime with an address as ready", () => {
    expect(
      runtimeIsReady({
        state: "running",
        code: "runtime_verified",
        host: "127.0.0.1",
        port: 8765,
      }),
    ).toBe(true);
  });

  it("does not treat a launchd-pid-only status as ready", () => {
    expect(
      runtimeIsReady({
        state: "running",
        code: "runtime_service_running",
      }),
    ).toBe(false);
  });

  it("does not treat stopped/unknown/null as ready", () => {
    expect(
      runtimeIsReady({ state: "stopped", code: "runtime_not_running" }),
    ).toBe(false);
    expect(runtimeIsReady(null)).toBe(false);
  });
});

// `ui` is in the objective's declared command list, but Core does not yet serve
// same-origin web assets or exchange a one-time code for an HttpOnly session.
// The command must be declared and honest — blocked with a stable code — and
// must never build a URL that carries the bare Core token.
describe("ui wires the Core session contract without token leakage", () => {
  // With no Runtime attached (the mocked default), `ui` cannot start a session
  // and must say so honestly instead of inventing a URL.
  it("reports a stable blocked document and exit code in JSON", async () => {
    const output = captureStdout();
    let code: number;
    try {
      code = await run(["ui", "--json"], {});
    } finally {
      output.restore();
    }
    expect(code!).toBe(4);
    const document = JSON.parse(output.value());
    expect(document).toMatchObject({
      schema_version: 1,
      product: PRODUCT,
      command: "ui",
      state: "blocked",
      code: "runtime_not_running",
    });
    // A bare token in a URL is what the contract forbids, so no token may
    // appear anywhere in the output.
    expect(output.value()).not.toMatch(/token=[A-Za-z0-9]/i);
  });

  it("is documented in help with its blocked status", async () => {
    const output = captureStdout();
    try {
      expect(await run(["help", "ui"], {})).toBe(0);
    } finally {
      output.restore();
    }
    expect(output.value()).toContain("blocked");
  });
});

// `backup restore` replaces live data, so it must be honest at every gate:
// refuse while the Runtime owns the data dir, stop+call Core from a
// maintenance process, and never report success from a malformed response.
describe("backup restore drives the Core managed-restore contract", () => {
  it("rejects while the Runtime is running, with a distinct exit code", async () => {
    runtimeMocks.inspectRuntimeStatus.mockResolvedValueOnce({
      state: "running",
      code: "runtime_verified",
    });
    const output = captureStdout();
    let code: number;
    try {
      code = await run(
        [
          "backup",
          "restore",
          "--source",
          "/tmp/x",
          "--yes",
          "--confirm",
          "/tmp/x",
          "--json",
        ],
        {},
      );
    } finally {
      output.restore();
    }
    expect(code!).toBe(1);
    expect(JSON.parse(output.value())).toMatchObject({
      product: PRODUCT,
      command: "backup",
      verb: "restore",
      state: "rejected",
      code: "backup_restore_runtime_running",
    });
  });

  it("reports the managed restore result after Core performs the swap", async () => {
    const maintenance = vi.fn(async () => ({
      state: "restored" as const,
      result: {
        state: "restored" as const,
        restoredBytes: 4096,
        previousBackupPath: "/home/.one-memory/backups/pre-restore.sqlite3",
        sourceSchemaVersion: 34,
      },
    }));
    const output = captureStdout();
    let code: number;
    try {
      code = await run(
        [
          "backup",
          "restore",
          "--source",
          "/home/.one-memory/backups/a.sqlite3",
          "--yes",
          "--confirm",
          "/home/.one-memory/backups/a.sqlite3",
          "--json",
        ],
        {},
        { runMaintenanceRestore: maintenance },
      );
    } finally {
      output.restore();
    }
    expect(code!).toBe(0);
    expect(maintenance).toHaveBeenCalledWith({
      coreCommand: expect.any(String),
      dataDir: expect.any(String),
      source: "/home/.one-memory/backups/a.sqlite3",
    });
    expect(JSON.parse(output.value())).toMatchObject({
      schema_version: 1,
      product: PRODUCT,
      command: "backup",
      verb: "restore",
      state: "restored",
      restored_bytes: 4096,
      previous_backup_path: "/home/.one-memory/backups/pre-restore.sqlite3",
      source_schema_version: 34,
    });
  });

  it("surfaces Core's refusal code and exits 1", async () => {
    const maintenance = vi.fn(async () => ({
      state: "rejected" as const,
      code: "backup_restore_source_invalid",
    }));
    const output = captureStdout();
    let code: number;
    try {
      code = await run(
        [
          "backup",
          "restore",
          "--source",
          "/tmp/x",
          "--yes",
          "--confirm",
          "/tmp/x",
          "--json",
        ],
        {},
        { runMaintenanceRestore: maintenance },
      );
    } finally {
      output.restore();
    }
    expect(code!).toBe(1);
    expect(JSON.parse(output.value())).toMatchObject({
      command: "backup",
      verb: "restore",
      state: "rejected",
      code: "backup_restore_source_invalid",
    });
  });

  it("requires --confirm to repeat the exact source path", async () => {
    const maintenance = vi.fn();
    const output = captureStdout();
    let code: number;
    try {
      code = await run(
        [
          "backup",
          "restore",
          "--source",
          "/home/.one-memory/backups/a.sqlite3",
          "--yes",
          "--confirm",
          "/home/.one-memory/backups/b.sqlite3",
          "--json",
        ],
        {},
        { runMaintenanceRestore: maintenance },
      );
    } finally {
      output.restore();
    }
    expect(code!).toBe(1);
    expect(maintenance).not.toHaveBeenCalled();
    expect(JSON.parse(output.value())).toMatchObject({
      command: "backup",
      verb: "restore",
      state: "rejected",
      code: "backup_restore_confirmation_required",
    });
  });

  it("fails closed when a live service lease survives a missing receipt", async () => {
    const home = await mkdtemp(path.join(tmpdir(), "memhub-restore-lease-"));
    const dataDir = path.join(home, "data");
    await import("node:fs/promises").then(({ mkdir, writeFile }) =>
      mkdir(path.join(dataDir, "runtime-service.lock"), {
        recursive: true,
        mode: 0o700,
      }).then(() =>
        writeFile(
          path.join(dataDir, "runtime-service.lock", "owner.json"),
          JSON.stringify({ pid: process.pid }),
          { mode: 0o600 },
        ),
      ),
    );
    const maintenance = vi.fn();
    const output = captureStdout();
    let code: number;
    try {
      code = await run(
        [
          "backup",
          "restore",
          "--source",
          "/tmp/backup.sqlite3",
          "--yes",
          "--confirm",
          "/tmp/backup.sqlite3",
          "--json",
        ],
        { ONE_MEMORY_DATA_DIR: dataDir },
        { runMaintenanceRestore: maintenance },
      );
    } finally {
      output.restore();
      await rm(home, { recursive: true, force: true });
    }
    expect(code!).toBe(1);
    expect(maintenance).not.toHaveBeenCalled();
    expect(JSON.parse(output.value())).toMatchObject({
      command: "backup",
      verb: "restore",
      state: "rejected",
      code: "backup_restore_runtime_state_unknown",
    });
  });
});

// A typo'd flag must be rejected, not silently dropped. `path list`/`adapter
// list` matched on `arguments_.includes("list")` without validating the rest,
// so `path list --bogus`/`adapter list --bogus` ran anyway (and here returned
// the runtime-not-running code instead of a usage error).
// `--json` is documented under "Global options", so `memhub --json <cmd>` must
// work like `memhub <cmd> --json`. It used to be treated as the command name
// and fail with "未知命令".
describe("leading --json is accepted as a global option", () => {
  it("accepts --json before the command and still emits JSON", async () => {
    const output = captureStreams();
    let code: number;
    try {
      code = await run(["--json", "status"], {});
    } finally {
      output.restore();
    }
    expect(code!).not.toBe(2);
    expect(output.stderr()).not.toContain("未知命令");
    // The success document comes back as a single JSON object on stdout.
    expect(JSON.parse(output.stdout())).toMatchObject({
      schema_version: 1,
      product: PRODUCT,
    });
  });

  it("keeps a trailing --json working too", async () => {
    const output = captureStreams();
    try {
      await run(["status", "--json"], {});
    } finally {
      output.restore();
    }
    expect(JSON.parse(output.stdout()).product).toBe(PRODUCT);
  });
});

describe("path/adapter list reject unknown flags", () => {
  it.each([
    ["path", "list", "--bogus"],
    ["adapter", "list", "--bogus"],
    ["path", "list", "--json", "--bogus"],
  ])("%s %s %s exits 2", async (...argv) => {
    expect(await run(argv as string[], {})).toBe(2);
  });

  it("adapter probe rejects a stray flag or extra positional", async () => {
    // Only `adapter probe --source <id> [--json]` is valid.
    expect(
      await run(["adapter", "probe", "--source", "codex", "--bogus"], {}),
    ).toBe(2);
    expect(
      await run(["adapter", "probe", "--source", "codex", "junk"], {}),
    ).toBe(2);
  });

  it("adapter probe accepts the valid form (positive control)", async () => {
    // No Runtime is running here, so it stops at the runtime check — NOT a
    // usage error. This is the guard that the strict check above did not
    // over-tighten and reject the legitimate invocation.
    expect(
      await run(["adapter", "probe", "--source", "codex", "--json"], {}),
    ).not.toBe(2);
  });
});

describe("init onboarding", () => {
  it("refuses a repeated --json rather than guessing which one counted", async () => {
    expect(await run(["init", "--json", "--json"], {})).toBe(2);
  });

  it.each([
    ["path", "list"],
    ["adapter", "list"],
  ])("refuses a repeated --json on %s %s", async (...command) => {
    expect(await run([...command, "--json", "--json"], {})).toBe(2);
  });

  it("refuses --source with no value", async () => {
    expect(await run(["init", "--source"], {})).toBe(2);
  });

  it("refuses --source whose value is another flag", async () => {
    // Otherwise "--source --import" would connect a source literally named
    // "--import".
    expect(await run(["init", "--source", "--import"], {})).toBe(2);
  });

  it("refuses an unknown flag", async () => {
    expect(await run(["init", "--bogus"], {})).toBe(2);
  });

  it("refuses the same source twice", async () => {
    // A duplicate would connect one source while reporting two.
    expect(
      await run(["init", "--source", "codex", "--source", "codex"], {}),
    ).toBe(2);
  });

  // Boolean flags were idempotent, but accepting them twice hid a duplicated
  // argv fragment that could also have duplicated a value-bearing flag.
  it.each(["--import", "--full-rescan", "--confirm-overrides", "--dry-run"])(
    "refuses a repeated init %s flag",
    async (flag) => {
      expect(await run(["init", "--source", "codex", flag, flag], {})).toBe(2);
    },
  );

  it("refuses a repeated host approve fingerprint", async () => {
    const fingerprintA = "a".repeat(64);
    const fingerprintB = "b".repeat(64);
    expect(
      await run(
        [
          "host",
          "install",
          "--host",
          "codex",
          "--approve",
          fingerprintA,
          "--approve",
          fingerprintB,
        ],
        {},
      ),
    ).toBe(2);
  });

  it("refuses a repeated host sync overwrite flag", async () => {
    expect(
      await run(
        [
          "host",
          "sync",
          "skill",
          "--host",
          "codex",
          "--overwrite-modified",
          "--overwrite-modified",
        ],
        {},
      ),
    ).toBe(2);
  });

  // A duplicate paging flag silently took the last value. Scripts that build
  // their argv from several sources could then page with a value they never
  // intended, and the CLI would not even reach the argument error.
  it.each([
    ["--limit", "1", "2"],
    ["--offset", "0", "1"],
  ])(
    "refuses a repeated %s memory paging flag",
    async (flag, first, second) => {
      const streams = captureStreams();
      try {
        expect(
          await run(
            ["memories", "list", flag, first, flag, second, "--json"],
            {},
          ),
        ).toBe(2);
      } finally {
        streams.restore();
      }
      const payload = JSON.parse(streams.stderr());
      expect(payload).toMatchObject({
        status: "usage_error",
        command: "memories",
        exit_code: 2,
      });
    },
  );

  it("accepts --dry-run together with --import", async () => {
    // Parsing must accept the pair; it stops at the Runtime check, which is
    // not a usage failure.
    expect(
      await run(["init", "--source", "codex", "--import", "--dry-run"], {}),
    ).not.toBe(2);
  });
});

// A1/A9 (2026-09-29 acceptance): a failed `service-install --yes` surfaced as
// a bare thrown string, so the caller saw only "runtime_service_command_failed"
// with no machine-readable envelope or next step. And uninstall has to be
// provably program-only: it must name the preserved data directory and must not
// ever remove it.
describe("service lifecycle failure and data-safety contract", () => {
  const serviceArgs = [
    "--platform",
    "linux",
    "--home",
    "/tmp/selected-home",
    "--install-root",
    "/tmp/install",
  ];

  it("reports a service command failure as a structured error, not a throw", async () => {
    runtimeMocks.installUserService.mockRejectedValueOnce(
      new Error("runtime_service_command_failed"),
    );
    const streams = captureStreams();
    let code: number;
    try {
      code = await run(
        [..."service-install".split(" "), ...serviceArgs, "--yes"],
        {},
      );
    } finally {
      streams.restore();
    }
    expect(code!).toBe(1);
    expect(streams.stdout()).toBe("");
    const error = JSON.parse(streams.stderr());
    expect(error).toMatchObject({
      schema_version: 1,
      product: PRODUCT,
      command: "service-install",
      status: "error",
      code: "runtime_service_command_failed",
      exit_code: 1,
    });
    expect(error.next_step.length).toBeGreaterThan(0);
  });

  // C0 `4dc3f0d` / C8 `C0-service-install-readiness-gap-20260930.md`:
  // `service-install --yes` used to return 0 as soon as the OS accepted the
  // registration, even when the Runtime never became `runtime_verified`.
  // require the same readiness predicate as start/restart/install/upgrade and
  // fail with a stable structured code when the budget expires.
  it("fails closed when service registration never becomes ready", async () => {
    runtimeMocks.installUserService.mockResolvedValueOnce({
      schema_version: 1,
      platform: "linux",
      scope: "user",
      service_id: "one-memory.service",
      status: "ready",
      blocked_reason: null,
      service_file: {
        path: "/tmp/one-memory.service",
        mode: 0o600,
        content: "unit",
      },
      commands: { install: [], start: [], stop: [], uninstall: [] },
      options: {},
    });
    const streams = captureStreams();
    let code: number;
    try {
      code = await run(
        ["service-install", ...serviceArgs, "--yes", "--data-dir", "/tmp/data"],
        {},
        { waitForRuntimeReady: async () => false },
      );
    } finally {
      streams.restore();
    }
    expect(code!).toBe(1);
    expect(streams.stdout()).toBe("");
    expect(JSON.parse(streams.stderr())).toMatchObject({
      schema_version: 1,
      product: PRODUCT,
      command: "service-install",
      status: "error",
      code: "runtime_service_activation_unverified",
      exit_code: 1,
    });
  });

  it("reports success only after service registration is verified ready", async () => {
    runtimeMocks.installUserService.mockResolvedValueOnce({
      schema_version: 1,
      platform: "linux",
      scope: "user",
      service_id: "one-memory.service",
      status: "ready",
      blocked_reason: null,
      service_file: {
        path: "/tmp/one-memory.service",
        mode: 0o600,
        content: "unit",
      },
      commands: { install: [], start: [], stop: [], uninstall: [] },
      options: {},
    });
    const output = captureStdout();
    let code: number;
    try {
      code = await run(
        ["service-install", ...serviceArgs, "--yes", "--data-dir", "/tmp/data"],
        {},
        { waitForRuntimeReady: async () => true },
      );
    } finally {
      output.restore();
    }
    expect(code!).toBe(0);
    expect(JSON.parse(output.value())).toMatchObject({ apply: true });
  });

  it("keeps the preserved data directory and never deletes data on service-uninstall", async () => {
    runtimeMocks.uninstallUserService.mockClear();
    runtimeMocks.uninstallUserService.mockResolvedValueOnce("removed" as never);
    const streams = captureStreams();
    try {
      expect(
        await run(
          [
            "service-uninstall",
            ...serviceArgs,
            "--data-dir",
            "/tmp/keep data",
            "--yes",
          ],
          {},
        ),
      ).toBe(0);
    } finally {
      streams.restore();
    }
    const payload = JSON.parse(streams.stdout());
    // The uninstall receipt must name the data directory it left untouched;
    // nothing in this command is allowed to delete it.
    expect(payload.service_file.content).toContain(
      "ONE_MEMORY_DATA_DIR=/tmp/keep data",
    );
    expect(runtimeMocks.uninstallUserService).toHaveBeenCalledOnce();
  });

  // O-13 follow-up: `service-uninstall --yes` used to let the underlying
  // error escape as a bare throw, while install/upgrade already report a
  // structured envelope. A caller could not tell runtime_service_file_modified
  // from a crash, and got no next step.
  it("reports a refused service-uninstall as a structured error, not a throw", async () => {
    runtimeMocks.uninstallUserService.mockReset();
    runtimeMocks.uninstallUserService.mockRejectedValueOnce(
      new Error("runtime_service_file_modified"),
    );
    const streams = captureStreams();
    let code: number;
    try {
      code = await run(
        [..."service-uninstall".split(" "), ...serviceArgs, "--yes"],
        {},
      );
    } finally {
      streams.restore();
    }
    expect(code!).toBe(1);
    expect(streams.stdout()).toBe("");
    const error = JSON.parse(streams.stderr());
    expect(error).toMatchObject({
      schema_version: 1,
      product: PRODUCT,
      command: "service-uninstall",
      status: "error",
      code: "runtime_service_file_modified",
      exit_code: 1,
    });
    expect(error.next_step.length).toBeGreaterThan(0);
  });

  // P2 (2026-09-29 H13 acceptance): install/service-install/service-uninstall
  // always print JSON, yet passing --json was rejected as a usage error. A
  // caller that adds --json uniformly across commands must not get exit 2.
  it("accepts --json on commands that always emit JSON", async () => {
    // `install` needs an explicit source here because the test runner's
    // argv[1] cannot be used to infer a distribution; the point of this test is
    // that `--json` is not itself a usage error.
    for (const [command, extra] of [
      [
        "install",
        [
          "--source",
          "/tmp/distribution",
          "--home",
          "/tmp/selected-home",
          "--install-root",
          "/tmp/install",
        ],
      ],
      ["service-install", [...serviceArgs]],
      ["service-uninstall", [...serviceArgs]],
    ] as const) {
      const streams = captureStreams();
      let code: number;
      try {
        code = await run([command, ...extra, "--json"], {
          ONE_MEMORY_DATA_DIR: "/tmp/data",
        });
      } finally {
        streams.restore();
      }
      // These are previews (no --yes): exit 0 when the plan is ready.
      expect(code!, `${command} --json`).not.toBe(2);
      expect(() => JSON.parse(streams.stdout())).not.toThrow();
    }
  });

  it.each(["service-plan", "service-install", "service-uninstall"])(
    "rejects a repeated --json on %s",
    async (command) => {
      const streams = captureStreams();
      try {
        expect(
          await run(
            [command, "--install-root", "/tmp/install", "--json", "--json"],
            {},
          ),
        ).toBe(2);
      } finally {
        streams.restore();
      }
      expect(streams.stdout()).toBe("");
      expect(JSON.parse(streams.stderr())).toMatchObject({
        status: "usage_error",
        command,
        exit_code: 2,
      });
    },
  );

  it.each(["install", "upgrade", "uninstall"])(
    "rejects a repeated --json on %s",
    async (command) => {
      const streams = captureStreams();
      try {
        expect(
          await run(
            [
              command,
              "--source",
              "/tmp/distribution",
              "--install-root",
              "/tmp/install",
              "--home",
              "/tmp/selected-home",
              "--json",
              "--json",
            ],
            {},
          ),
        ).toBe(2);
      } finally {
        streams.restore();
      }
      expect(streams.stdout()).toBe("");
      expect(JSON.parse(streams.stderr())).toMatchObject({
        status: "usage_error",
        command,
        exit_code: 2,
      });
    },
  );
});

function captureStreams() {
  let out = "";
  let error = "";
  const originalOut = process.stdout.write;
  const originalError = process.stderr.write;
  process.stdout.write = ((chunk: string) => {
    out += chunk;
    return true;
  }) as typeof process.stdout.write;
  process.stderr.write = ((chunk: string) => {
    error += chunk;
    return true;
  }) as typeof process.stderr.write;
  return {
    stdout: () => out,
    stderr: () => error,
    restore: () => {
      process.stdout.write = originalOut;
      process.stderr.write = originalError;
    },
  };
}
