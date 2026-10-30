import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import {
  installUserService,
  planUserService,
  runtimeInstallLayout,
} from "@one-memory/runtime-control";
import { describe, expect, it, vi } from "vitest";
import {
  applyPreparedServiceInstall,
  inspectLaunchAgentRegistration,
  prepareServiceInstall,
  rollbackPreparedServiceInstall,
  type ServiceLifecycleDependencies,
  uninstallServicePreservingData,
} from "./service-lifecycle.js";
import { createTolerantLaunchctlRunner } from "./service-session.js";

function fixture() {
  const home = "/Users/example";
  const layout = runtimeInstallLayout({
    platform: "darwin",
    home,
    installRoot: "/Applications/MemHub/current",
    dataDir: "/Users/example/.one-memory",
  });
  const options = { uid: 501 };
  const plan = planUserService(layout, options);
  if (plan.service_file === null) throw new Error("expected ready plan");
  return { layout, options, plan };
}

function dependencies(
  overrides: Partial<ServiceLifecycleDependencies> = {},
): ServiceLifecycleDependencies {
  return {
    inspectRegistration: vi.fn(async () => ({ kind: "absent" as const })),
    inspectServiceFile: vi.fn(async () => ({ kind: "absent" as const })),
    install: vi.fn(async (_layout, options) => {
      for (const command of fixture().plan.commands.install) {
        await options.runCommand?.(command);
      }
      return fixture().plan;
    }),
    restart: vi.fn(async () => fixture().plan),
    uninstall: vi.fn(async () => "removed" as const),
    runCommand: vi.fn(async () => undefined),
    verifyRegistrationSnapshot: vi.fn(async () => undefined),
    // Default: paths are only "same" when literally equal, which matches the
    // old behaviour for positive controls and is overridden where the realpath
    // case is under test.
    sameFile: vi.fn(async (left: string, right: string) => left === right),
    serviceEnabled: vi.fn(async () => false),
    enableService: vi.fn(async () => undefined),
    disableService: vi.fn(async () => undefined),
    ...overrides,
  };
}

describe("macOS service lifecycle", () => {
  it("recognizes a registered MemHub plist through launchctl and plutil", async () => {
    const f = fixture();
    const root = await mkdtemp(path.join(tmpdir(), "memhub-launch-agent-"));
    const plistPath = path.join(root, "com.tencent.onememory.runtime.plist");
    await writeFile(plistPath, "managed plist", { mode: 0o600 });
    const capture = vi
      .fn()
      .mockResolvedValueOnce({
        code: 0,
        stdout: `gui/501/${f.plan.service_id} = {\n  path = ${plistPath}\n}`,
        stderr: "",
      })
      .mockResolvedValueOnce({
        code: 0,
        stdout: JSON.stringify({
          Label: f.plan.service_id,
          ProgramArguments: [
            f.layout.nodeCommand,
            f.layout.runtimeEntry,
            "serve",
          ],
          EnvironmentVariables: {
            ONE_MEMORY_NODE_COMMAND: f.layout.nodeCommand,
            ONE_MEMORY_RUNTIME_ENTRY: f.layout.runtimeEntry,
          },
        }),
        stderr: "",
      });

    await expect(
      inspectLaunchAgentRegistration(f.plan, f.options, capture),
    ).resolves.toMatchObject({
      kind: "managed",
      plistPath,
      content: "managed plist",
    });
    expect(capture).toHaveBeenNthCalledWith(2, [
      "/usr/bin/plutil",
      "-convert",
      "json",
      "-o",
      "-",
      plistPath,
    ]);
  });

  it("classifies a spoofed same-label plist as unmanaged", async () => {
    const f = fixture();
    const root = await mkdtemp(path.join(tmpdir(), "memhub-launch-agent-"));
    const plistPath = path.join(root, "com.tencent.onememory.runtime.plist");
    await writeFile(plistPath, "foreign plist", { mode: 0o600 });
    const capture = vi
      .fn()
      .mockResolvedValueOnce({
        code: 0,
        stdout: `path = ${plistPath}\n`,
        stderr: "",
      })
      .mockResolvedValueOnce({
        code: 0,
        stdout: JSON.stringify({
          Label: f.plan.service_id,
          ProgramArguments: ["/tmp/not-memhub", "--serve"],
        }),
        stderr: "",
      });

    await expect(
      inspectLaunchAgentRegistration(f.plan, f.options, capture),
    ).resolves.toEqual({ kind: "unmanaged", plistPath });
  });

  // P1 (2026-09-29 H13 real-machine acceptance): re-running `install --yes` on
  // an already-installed root failed with runtime_service_conflict_unmanaged_file
  // even though the on-disk plist was byte-identical. `launchctl print` reports
  // the plist's realpath (e.g. /private/tmp/... on macOS) while the plan path is
  // the unresolved one (/tmp/...), so the literal string comparison misclassified
  // a self-managed service as unknown. The comparison must be path-identity, not
  // string equality.
  it("treats launchd's resolved plist path as the same managed service", async () => {
    const f = fixture();
    const resolved =
      "/private/tmp/memhub-home/Library/LaunchAgents/com.tencent.onememory.runtime.plist";
    const unresolved =
      "/tmp/memhub-home/Library/LaunchAgents/com.tencent.onememory.runtime.plist";
    const plan = {
      ...f.plan,
      service_file: { ...f.plan.service_file!, path: unresolved },
    };
    const deps = dependencies({
      inspectRegistration: vi.fn(async () => ({
        kind: "managed" as const,
        plistPath: resolved,
        content: plan.service_file!.content,
      })),
      inspectServiceFile: vi.fn(async () => ({
        kind: "managed" as const,
        content: plan.service_file!.content,
      })),
      // The dead-simple stand-in for "these two paths point at the same file".
      sameFile: vi.fn(async () => true),
    });

    const prepared = await prepareServiceInstall(f.layout, f.options, deps);
    await applyPreparedServiceInstall(prepared, deps);

    expect(deps.restart).toHaveBeenCalledOnce();
    expect(deps.install).not.toHaveBeenCalled();
  });

  it("kickstarts an already registered managed service at the same plist", async () => {
    const f = fixture();
    const deps = dependencies({
      inspectRegistration: vi.fn(async () => ({
        kind: "managed" as const,
        plistPath: f.plan.service_file!.path,
        content: f.plan.service_file!.content,
      })),
      inspectServiceFile: vi.fn(async () => ({
        kind: "managed" as const,
        content: f.plan.service_file!.content,
      })),
    });

    const prepared = await prepareServiceInstall(f.layout, f.options, deps);
    await applyPreparedServiceInstall(prepared, deps);

    expect(deps.restart).toHaveBeenCalledOnce();
    expect(deps.install).not.toHaveBeenCalled();
  });

  it("restores an already registered App service after install health fails", async () => {
    const f = fixture();
    const deps = dependencies({
      inspectRegistration: vi.fn(async () => ({
        kind: "managed" as const,
        plistPath: f.plan.service_file!.path,
        content: f.plan.service_file!.content,
      })),
      inspectServiceFile: vi.fn(async () => ({
        kind: "managed" as const,
        content: f.plan.service_file!.content,
      })),
      serviceEnabled: vi.fn(async () => true),
    });

    const prepared = await prepareServiceInstall(f.layout, f.options, deps);
    await applyPreparedServiceInstall(prepared, deps);
    const rollback = vi.fn(async () => undefined);
    await rollbackPreparedServiceInstall(prepared, rollback, deps);

    expect(deps.restart).toHaveBeenCalledOnce();
    expect(deps.uninstall).toHaveBeenCalledWith(f.layout, f.options);
    expect(rollback).toHaveBeenCalledOnce();
    expect(deps.enableService).toHaveBeenCalledWith(f.layout.dataDir);
    expect(deps.install).toHaveBeenCalledOnce();
  });

  it("returns an unloaded existing plist to the unloaded state", async () => {
    const f = fixture();
    const deps = dependencies({
      inspectServiceFile: vi.fn(async () => ({
        kind: "managed" as const,
        content: f.plan.service_file!.content,
      })),
    });

    const prepared = await prepareServiceInstall(f.layout, f.options, deps);
    await applyPreparedServiceInstall(prepared, deps);
    const rollback = vi.fn(async () => undefined);
    await rollbackPreparedServiceInstall(prepared, rollback, deps);

    expect(deps.runCommand).toHaveBeenNthCalledWith(
      1,
      f.plan.commands.install[0],
    );
    expect(deps.runCommand).toHaveBeenNthCalledWith(2, f.plan.commands.stop[0]);
    expect(rollback).toHaveBeenCalledOnce();
    expect(deps.disableService).toHaveBeenCalledWith(f.layout.dataDir);
    expect(deps.uninstall).not.toHaveBeenCalled();
  });

  it("removes a first-time service when install health later fails", async () => {
    const f = fixture();
    const deps = dependencies();

    const prepared = await prepareServiceInstall(f.layout, f.options, deps);
    await applyPreparedServiceInstall(prepared, deps);
    const rollback = vi.fn(async () => undefined);
    await rollbackPreparedServiceInstall(prepared, rollback, deps);

    expect(deps.uninstall).toHaveBeenCalledOnce();
    expect(rollback).toHaveBeenCalledOnce();
    expect(deps.disableService).toHaveBeenCalledWith(f.layout.dataDir);
  });

  it("replaces a managed registration from another install root", async () => {
    const f = fixture();
    const oldPath = "/Users/example/Library/LaunchAgents/old-runtime.plist";
    const deps = dependencies({
      inspectRegistration: vi.fn(async () => ({
        kind: "managed" as const,
        plistPath: oldPath,
        content: "old managed plist",
      })),
    });

    const prepared = await prepareServiceInstall(f.layout, f.options, deps);
    await applyPreparedServiceInstall(prepared, deps);

    expect(deps.runCommand).toHaveBeenNthCalledWith(1, [
      "launchctl",
      "bootout",
      "gui/501",
      oldPath,
    ]);
    expect(deps.runCommand).toHaveBeenNthCalledWith(
      2,
      f.plan.commands.install[0],
    );
    expect(deps.verifyRegistrationSnapshot).toHaveBeenCalledWith(
      expect.objectContaining({ plistPath: oldPath }),
    );
  });

  it("restores the previous managed registration when bootstrap fails", async () => {
    const f = fixture();
    const oldPath = "/Users/example/Library/LaunchAgents/old-runtime.plist";
    const runCommand = vi
      .fn()
      .mockResolvedValueOnce(undefined)
      .mockRejectedValueOnce(new Error("bootstrap failed"))
      .mockResolvedValueOnce(undefined);
    const deps = dependencies({
      inspectRegistration: vi.fn(async () => ({
        kind: "managed" as const,
        plistPath: oldPath,
        content: "old managed plist",
      })),
      runCommand,
    });

    const prepared = await prepareServiceInstall(f.layout, f.options, deps);
    await expect(applyPreparedServiceInstall(prepared, deps)).rejects.toThrow(
      "runtime_service_managed_upgrade_failed",
    );

    expect(runCommand).toHaveBeenNthCalledWith(3, [
      "launchctl",
      "bootstrap",
      "gui/501",
      oldPath,
    ]);
    expect(deps.verifyRegistrationSnapshot).toHaveBeenCalledTimes(2);
  });

  it("does not re-bootstrap a previous registration that may serve a legacy home", async () => {
    const f = fixture();
    const oldPath = "/Users/example/Library/LaunchAgents/old-runtime.plist";
    const runCommand = vi
      .fn()
      .mockResolvedValueOnce(undefined)
      .mockRejectedValueOnce(new Error("bootstrap failed"));
    const deps = dependencies({
      inspectRegistration: vi.fn(async () => ({
        kind: "managed" as const,
        plistPath: oldPath,
        content: "old managed plist",
      })),
      runCommand,
    });

    const prepared = await prepareServiceInstall(f.layout, f.options, deps);
    await expect(
      applyPreparedServiceInstall(
        { ...prepared, restorePreviousOnFailure: false },
        deps,
      ),
    ).rejects.toThrow("runtime_service_replacement_failed");

    // bootout + the failed bootstrap only; the old plist is not bootstrapped.
    expect(runCommand).toHaveBeenCalledTimes(2);
    expect(runCommand).not.toHaveBeenCalledWith([
      "launchctl",
      "bootstrap",
      "gui/501",
      oldPath,
    ]);
  });

  it("removes the replacement plist and restores the previous registration", async () => {
    const f = fixture();
    const oldPath = "/Users/example/Library/LaunchAgents/old-runtime.plist";
    const deps = dependencies({
      inspectRegistration: vi.fn(async () => ({
        kind: "managed" as const,
        plistPath: oldPath,
        content: "old managed plist",
      })),
      serviceEnabled: vi.fn(async () => true),
    });

    const prepared = await prepareServiceInstall(f.layout, f.options, deps);
    await applyPreparedServiceInstall(prepared, deps);
    const rollback = vi.fn(async () => undefined);
    await rollbackPreparedServiceInstall(prepared, rollback, deps);

    expect(deps.uninstall).toHaveBeenCalledWith(f.layout, f.options);
    expect(rollback).toHaveBeenCalledOnce();
    expect(deps.enableService).toHaveBeenCalledWith(f.layout.dataDir);
    expect(deps.runCommand).toHaveBeenLastCalledWith([
      "launchctl",
      "bootstrap",
      "gui/501",
      oldPath,
    ]);
  });

  it("rejects an unknown same-label service before any install mutation", async () => {
    const f = fixture();
    const deps = dependencies({
      inspectRegistration: vi.fn(async () => ({
        kind: "unmanaged" as const,
        plistPath: "/Library/LaunchAgents/com.tencent.onememory.runtime.plist",
      })),
    });

    await expect(
      prepareServiceInstall(f.layout, f.options, deps),
    ).rejects.toThrow("runtime_service_conflict_unmanaged_label");
    expect(deps.install).not.toHaveBeenCalled();
    expect(deps.runCommand).not.toHaveBeenCalled();
  });

  it("turns an incomplete install into an actionable service-install error", async () => {
    const f = fixture();
    const deps = dependencies({
      install: vi.fn(async () => {
        throw new Error("runtime_install_incomplete");
      }),
    });

    const prepared = await prepareServiceInstall(f.layout, f.options, deps);
    await expect(applyPreparedServiceInstall(prepared, deps)).rejects.toThrow(
      "runtime_install_incomplete: run memhub install --yes first",
    );
  });

  it("removes only the service and preserves the selected data directory", async () => {
    const root = await mkdtemp(path.join(tmpdir(), "memhub-data-preserved-"));
    const marker = path.join(root, "must-survive.txt");
    await writeFile(marker, "user memory", "utf8");
    const layout = runtimeInstallLayout({
      platform: "darwin",
      home: "/Users/example",
      installRoot: "/Applications/MemHub/current",
      dataDir: root,
    });
    const deps = dependencies();

    await expect(
      uninstallServicePreservingData(layout, { uid: 501 }, deps),
    ).resolves.toBe("removed");
    expect(await readFile(marker, "utf8")).toBe("user memory");
  });

  // Regression (2026-09-29 H17 A8): `memhub stop` now boots the launchd label
  // out, but leaves the managed plist on disk. A later `memhub uninstall --yes`
  // re-runs `launchctl bootout`, which then exits non-zero ("Boot-out failed: 3:
  // No such process"), and `uninstallUserService` aborted before unlinking the
  // plist. The stale plist made every subsequent `install --yes` fail with
  // `runtime_service_file_exists`. The CLI injects a tolerant runner so an
  // already-unloaded label is not treated as a teardown failure.
  it("uninstalls after the service was already booted out and removes the plist", async () => {
    if (process.platform !== "darwin") return;
    const home = await mkdtemp(path.join(tmpdir(), "memhub-uninstall-"));
    const layout = runtimeInstallLayout({
      platform: "darwin",
      home,
      installRoot: path.join(home, "runtime"),
      dataDir: path.join(home, "data"),
    });
    const files = [
      layout.nodeCommand,
      layout.runtimeEntry,
      layout.coreCommand,
      layout.mcpEntryPath,
      layout.recallCliPath,
      path.join(layout.syncHookAssetsDirectory, "manifest.json"),
    ];
    for (const file of files) {
      const { mkdir } = await import("node:fs/promises");
      await mkdir(path.dirname(file), { recursive: true });
      await writeFile(file, "fixture");
    }
    const options = { uid: process.getuid?.() };
    const plan = await installUserService(layout, {
      ...options,
      runCommand: async () => undefined,
    });
    const plistPath = plan.service_file?.path as string;
    await expect(readFile(plistPath, "utf8")).resolves.toBe(
      plan.service_file?.content,
    );

    // C7 `9e10d1e` changed the uninstall semantics: a `bootout` failure is no
    // longer fatal on its own. The shared uninstaller re-probes with
    // `launchctl print` and tolerates the failure only when that probe reports
    // 113 (the label really is not loaded). The CLI injects the tolerant
    // runner, which is checked below; the tolerant runner alone (without the
    // print re-probe) is no longer the whole story.
    //
    // The reverse case must still fail: a job that is still loaded after a
    // failed bootout is a real teardown failure and must not be reported as a
    // successful uninstall.
    const withLaunchctlPrint = (code: number) => {
      // The shared uninstaller calls the real `launchctl print` through the
      // package's probe, not the injected runner, so the deterministic control
      // is a `launchctl` shim first on PATH. 113 = not loaded (tolerated);
      // 0 = still loaded (must surface the bootout failure).
      const shimDir = path.join(home, `shim-${code}`);
      const shim = path.join(shimDir, "launchctl");
      const previousPath = process.env.PATH;
      return {
        install: async () => {
          const { mkdir } = await import("node:fs/promises");
          await mkdir(shimDir, { recursive: true });
          await writeFile(
            shim,
            `#!/bin/sh\nif [ "$1" = "print" ]; then exit ${code}; fi\nexit 0\n`,
            { mode: 0o755 },
          );
          process.env.PATH = `${shimDir}:${previousPath ?? ""}`;
        },
        restore: () => {
          process.env.PATH = previousPath;
        },
      };
    };

    // 113 from `launchctl print` after a failed bootout: already stopped.
    //
    // This exact expectation only holds once C7 `9e10d1e`'s print re-probe is
    // present in `packages/runtime-control`. This branch alone has the older
    // shared package, where a plain bootout failure is still fatal, so the
    // directions are asserted where each is meaningful:
    //  - on this branch: the tolerant runner still tolerates the not-found
    //    case and refuses a genuinely unrecognized failure;
    //  - in the C7 combination: the print re-probe decides, so both the
    //    113 (tolerated) and still-loaded (fatal) cases are checked below.
    // The condition keeps the test honest in both trees instead of passing
    // for the wrong reason.
    const sharedInstallSource = await readFile(
      new URL(
        "../../../packages/runtime-control/src/service-install.ts",
        import.meta.url,
      ),
      "utf8",
    );
    const hasPrintReprobe = sharedInstallSource.includes("uninstallBootout");

    const notLoaded = withLaunchctlPrint(113);
    await notLoaded.install();
    try {
      await expect(
        uninstallServicePreservingData(layout, {
          ...options,
          runCommand: createTolerantLaunchctlRunner(async (command) => {
            if (command[1] === "bootout") {
              return {
                code: hasPrintReprobe ? 5 : 3,
                stdout: "",
                stderr: hasPrintReprobe
                  ? `Operation not permitted`
                  : `Boot-out failed: 3: No such process`,
              };
            }
            return { code: 0, stdout: "", stderr: "" };
          }),
        }),
      ).resolves.toBe("removed");
    } finally {
      notLoaded.restore();
    }
    await expect(readFile(plistPath, "utf8")).rejects.toThrow();

    // Re-create the managed plist so the reverse case starts from the same
    // state.
    const { mkdir } = await import("node:fs/promises");
    await mkdir(path.dirname(plistPath), { recursive: true, mode: 0o700 });
    await writeFile(plistPath, plan.service_file?.content as string, {
      mode: 0o600,
    });

    // `launchctl print` reports the job is still loaded (exit 0): the failed
    // bootout must surface and the plist must survive.
    const stillLoaded = withLaunchctlPrint(0);
    await stillLoaded.install();
    try {
      await expect(
        uninstallServicePreservingData(layout, {
          ...options,
          runCommand: createTolerantLaunchctlRunner(async (command) => {
            if (command[1] === "bootout") {
              return {
                code: 5,
                stdout: "",
                stderr: `Operation not permitted`,
              };
            }
            return { code: 0, stdout: "", stderr: "" };
          }),
        }),
      ).rejects.toThrow("runtime_service_command_failed");
    } finally {
      stillLoaded.restore();
    }
    await expect(readFile(plistPath, "utf8")).resolves.toBeDefined();
  });
});
