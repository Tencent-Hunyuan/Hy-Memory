import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import {
  chmod,
  cp,
  lstat,
  mkdir,
  mkdtemp,
  readFile,
  readlink,
  realpath,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { applyLocalInstall, planLocalInstall } from "./local-install.js";
import {
  describeBundlePayload,
  installPackagedRuntime,
} from "./packaged-runtime-install.js";

const roots: string[] = [];
afterEach(async () => {
  await Promise.all(
    roots.splice(0).map((root) => rm(root, { recursive: true, force: true })),
  );
});

async function fixture(): Promise<{
  root: string;
  resources: string;
  executable: string;
  installRoot: string;
}> {
  const root = await mkdtemp(
    path.join(tmpdir(), "one-memory-packaged-runtime-"),
  );
  roots.push(root);
  const resources = path.join(root, "resources");
  const executable = path.join(root, "MemHub");
  const installRoot = path.join(root, "One Memory");
  await mkdir(path.join(resources, "host-bridge"), { recursive: true });
  await mkdir(path.join(resources, "core"), { recursive: true });
  await mkdir(path.join(resources, "runtime"), { recursive: true });
  await mkdir(path.join(resources, "web"), { recursive: true });
  await mkdir(path.join(resources, "cli"), { recursive: true });
  await writeFile(path.join(resources, "host-bridge", "mcp.cjs"), "mcp-body");
  await writeFile(
    path.join(resources, "host-bridge", "recall-cli.cjs"),
    "recall-body",
  );
  await writeFile(
    path.join(resources, "runtime", "runtime.cjs"),
    "runtime-body",
  );
  await writeFile(path.join(resources, "web", "index.html"), "web-body");
  await writeFile(path.join(resources, "cli", "one-memory.cjs"), "cli-body");
  // `core/one-memory` must be a real Mach-O slice matching the manifest arch;
  // `bin/node` stays a shell launcher in this layout and is skipped by design.
  const coreHeader = Buffer.alloc(8);
  coreHeader.writeUInt32LE(0xfeedfacf, 0); // MH_MAGIC_64
  coreHeader.writeUInt32LE(0x0100000c, 4); // CPU_TYPE_ARM64
  await writeFile(path.join(resources, "core", "one-memory"), coreHeader);
  await chmod(path.join(resources, "core", "one-memory"), 0o755);
  await writeFile(
    executable,
    "#!/bin/sh\nprintf '%s\\n' \"$ELECTRON_RUN_AS_NODE\"\nprintf '%s\\n' \"$1\"\n",
  );
  await chmod(executable, 0o755);
  return { root, resources, executable, installRoot };
}

function run(command: string, args: string[]): Promise<string> {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) resolve(stdout);
      else reject(new Error(stderr || `exit ${code}`));
    });
  });
}

function sha256(value: Buffer): string {
  return createHash("sha256").update(value).digest("hex");
}

async function installFormalCurrent(options: {
  root: string;
  resources: string;
  executable: string;
  installRoot: string;
  buildNumber?: number;
  gitCommit?: string;
}): Promise<{ current: string; selected: string }> {
  const buildNumber = options.buildNumber ?? 3757;
  const appLayout = await installPackagedRuntime({
    resourcesPath: options.resources,
    executablePath: options.executable,
    installRoot: path.join(options.root, `app-layout-${buildNumber}`),
    platform: "darwin",
    arch: "arm64",
    buildNumber,
    gitCommit: options.gitCommit ?? "a".repeat(40),
  });
  const formalSource = path.join(options.root, `formal-dist-${buildNumber}`);
  await cp(appLayout.version_directory, formalSource, { recursive: true });
  const nativeNode = Buffer.alloc(8);
  nativeNode.writeUInt32LE(0xfeedfacf, 0);
  nativeNode.writeUInt32LE(0x0100000c, 4); // CPU_TYPE_ARM64
  await writeFile(path.join(formalSource, "bin", "node"), nativeNode);
  await chmod(path.join(formalSource, "bin", "node"), 0o755);
  const manifestPath = path.join(formalSource, "manifest.json");
  const manifest = JSON.parse(await readFile(manifestPath, "utf8")) as {
    files: Record<string, string>;
  };
  manifest.files["bin/node"] = sha256(nativeNode);
  await writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);
  await applyLocalInstall(
    await planLocalInstall({
      source: formalSource,
      installRoot: options.installRoot,
      platform: "darwin",
      arch: "arm64",
    }),
  );
  const current = path.join(options.installRoot, "current");
  return {
    current,
    selected: path.resolve(options.installRoot, await readlink(current)),
  };
}

async function rewriteManifestDigest(
  root: string,
  relative: string,
): Promise<void> {
  const manifestPath = path.join(root, "manifest.json");
  const manifest = JSON.parse(await readFile(manifestPath, "utf8")) as {
    files: Record<string, string>;
  };
  manifest.files[relative] = sha256(await readFile(path.join(root, relative)));
  await writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);
}

describe("installPackagedRuntime", () => {
  it("puts a launchable node on the stable current link and leaves the bundle path out of that name", async () => {
    const { resources, executable, installRoot } = await fixture();
    const receipt = await installPackagedRuntime({
      resourcesPath: resources,
      executablePath: executable,
      installRoot,
      platform: "darwin",
      arch: "arm64",
    });

    const current = path.join(installRoot, "current");
    const link = await lstat(current);
    expect(link.isSymbolicLink()).toBe(true);
    const target = path.resolve(installRoot, await readlink(current));
    expect(target).toBe(receipt.version_directory);
    expect(
      path
        .relative(path.join(installRoot, "versions"), target)
        .startsWith(".."),
    ).toBe(false);

    const node = path.join(current, "bin", "node");
    expect((await lstat(node)).isSymbolicLink()).toBe(false);
    expect(await readFile(node, "utf8")).toContain(executable);
    expect(node).not.toContain(".app");
    expect(
      await readFile(path.join(current, "host-bridge", "mcp.cjs"), "utf8"),
    ).toBe("mcp-body");
    // `core/one-memory` is a real Mach-O slice (its bytes are an architecture
    // header), so compare the copied size instead of decoding it as text.
    expect((await lstat(path.join(current, "core", "one-memory"))).size).toBe(
      8,
    );
    await expect(run(node, ["mcp.cjs"])).resolves.toBe("1\nmcp.cjs\n");

    const again = await installPackagedRuntime({
      resourcesPath: resources,
      executablePath: executable,
      installRoot,
      platform: "darwin",
      arch: "arm64",
    });
    expect(again.version_directory).toBe(receipt.version_directory);
    expect(again.created_version).toBe(false);
    await expect(run(node, ["mcp.cjs"])).resolves.toBe("1\nmcp.cjs\n");
  });

  it("installs packaged file and directory symlinks with manifest-safe hashes", async () => {
    const { resources, executable, installRoot } = await fixture();
    await mkdir(path.join(resources, "core", "assets"));
    await writeFile(
      path.join(resources, "core", "assets", "model.bin"),
      "model",
    );
    await symlink(
      "assets/model.bin",
      path.join(resources, "core", "model-link"),
    );
    await symlink("assets", path.join(resources, "core", "assets-link"));

    const receipt = await installPackagedRuntime({
      resourcesPath: resources,
      executablePath: executable,
      installRoot,
      platform: "darwin",
      arch: "arm64",
    });

    expect(
      await readlink(path.join(receipt.current_link, "core", "model-link")),
    ).toBe("assets/model.bin");
    expect(
      await readlink(path.join(receipt.current_link, "core", "assets-link")),
    ).toBe("assets");
  });

  it("removes the service marker and exits cleanly after the App is deleted", async () => {
    const { root, resources, executable, installRoot } = await fixture();
    const marker = path.join(root, "runtime-service.enabled");
    await writeFile(marker, "enabled\n", { mode: 0o600 });
    const receipt = await installPackagedRuntime({
      resourcesPath: resources,
      executablePath: executable,
      installRoot,
      platform: "darwin",
      arch: "arm64",
      serviceEnableMarker: marker,
    });
    const launcher = path.join(receipt.current_link, "bin", "node");

    await rm(executable);
    await expect(run(launcher, ["runtime.cjs", "serve"])).resolves.toBe("");
    await expect(lstat(marker)).rejects.toMatchObject({ code: "ENOENT" });
  });

  it("reuses an already-selected formal distribution from the same App build", async () => {
    const { root, resources, executable, installRoot } = await fixture();
    const { current } = await installFormalCurrent({
      root,
      resources,
      executable,
      installRoot,
    });
    const selectedBefore = await readlink(current);
    const inodeBefore = (await lstat(current)).ino;
    await writeFile(path.join(current, ".DS_Store"), "finder metadata");
    await expect(
      installPackagedRuntime({
        resourcesPath: resources,
        executablePath: executable,
        installRoot,
        platform: "darwin",
        arch: "arm64",
        buildNumber: 3757,
      }),
    ).resolves.toMatchObject({
      build_number: 3757,
      created_version: false,
      reused_current: true,
    });
    expect(await readlink(current)).toBe(selectedBefore);
    expect((await lstat(current)).ino).toBe(inodeBefore);
  });

  it("repairs a same-build packaged launcher to the current App executable", async () => {
    const { root, resources, executable, installRoot } = await fixture();
    const first = await installPackagedRuntime({
      resourcesPath: resources,
      executablePath: executable,
      installRoot,
      platform: "darwin",
      arch: "arm64",
      buildNumber: 3757,
    });
    const movedExecutable = path.join(
      root,
      "Applications",
      "MemHub.app",
      "Contents",
      "MacOS",
      "MemHub",
    );
    await mkdir(path.dirname(movedExecutable), { recursive: true });
    await writeFile(movedExecutable, "#!/bin/sh\nprintf 'moved\\n'\n");
    await chmod(movedExecutable, 0o755);

    const reused = await installPackagedRuntime({
      resourcesPath: resources,
      executablePath: movedExecutable,
      installRoot,
      platform: "darwin",
      arch: "arm64",
      buildNumber: 3757,
    });

    expect(reused.reused_current).toBe(true);
    const launcher = await readFile(
      path.join(first.current_link, "bin", "node"),
      "utf8",
    );
    expect(launcher).toContain(movedExecutable);
    expect(launcher).not.toContain(executable);
    await expect(
      run(path.join(first.current_link, "bin", "node"), []),
    ).resolves.toBe("moved\n");
  });

  it.each([
    "core/one-memory",
    "runtime/runtime.cjs",
    "host-bridge/mcp.cjs",
    "web/index.html",
    "bin/one-memory.cjs",
    "bin/one-memory",
  ])("refuses a same-build current with a changed %s", async (relative) => {
    const { root, resources, executable, installRoot } = await fixture();
    const { current, selected } = await installFormalCurrent({
      root,
      resources,
      executable,
      installRoot,
    });
    const inodeBefore = (await lstat(current)).ino;
    const selectedFile = path.join(selected, relative);
    await writeFile(
      selectedFile,
      Buffer.concat([await readFile(selectedFile), Buffer.from("changed")]),
    );
    await expect(
      installPackagedRuntime({
        resourcesPath: resources,
        executablePath: executable,
        installRoot,
        platform: "darwin",
        arch: "arm64",
        buildNumber: 3757,
      }),
    ).rejects.toThrow("distribution_hash_mismatch");
    expect((await lstat(current)).ino).toBe(inodeBefore);
  });

  it("refuses a manifest-blessed same-build component mismatch", async () => {
    const { root, resources, executable, installRoot } = await fixture();
    const { current, selected } = await installFormalCurrent({
      root,
      resources,
      executable,
      installRoot,
    });
    const inodeBefore = (await lstat(current)).ino;
    await writeFile(path.join(selected, "web", "index.html"), "blessed-change");
    await rewriteManifestDigest(selected, "web/index.html");
    await expect(
      installPackagedRuntime({
        resourcesPath: resources,
        executablePath: executable,
        installRoot,
        platform: "darwin",
        arch: "arm64",
        buildNumber: 3757,
      }),
    ).rejects.toThrow("installed_version_conflict");
    expect((await lstat(current)).ino).toBe(inodeBefore);
  });

  it("takes over from a same-build runtime whose bytes differ when the bundle asks it to", async () => {
    const { root, resources, executable, installRoot } = await fixture();
    const { current, selected } = await installFormalCurrent({
      root,
      resources,
      executable,
      installRoot,
    });
    // A re-cut package: same build number, manifest still self-consistent,
    // but not the bytes this bundle carries.
    await writeFile(path.join(selected, "web", "index.html"), "rebuilt-body");
    await rewriteManifestDigest(selected, "web/index.html");
    const receipt = await installPackagedRuntime({
      resourcesPath: resources,
      executablePath: executable,
      installRoot,
      platform: "darwin",
      arch: "arm64",
      buildNumber: 3757,
      gitCommit: "b".repeat(40),
      replaceSameBuild: true,
    });
    expect(receipt.reused_current).toBe(false);
    expect(receipt.version_directory).not.toBe(selected);
    // `current` serves the payload this bundle brought.
    expect(await readlink(current)).toBe(
      path.relative(installRoot, receipt.version_directory),
    );
    expect(
      await readFile(
        path.join(receipt.version_directory, "web", "index.html"),
        "utf8",
      ),
    ).toBe("web-body");
    // The superseded tree is left on disk, not edited: it is the rollback.
    expect(
      await readFile(path.join(selected, "web", "index.html"), "utf8"),
    ).toBe("rebuilt-body");
  });

  it("repairs same-commit drift when the trusted bundle opts in", async () => {
    const { root, resources, executable, installRoot } = await fixture();
    const { selected } = await installFormalCurrent({
      root,
      resources,
      executable,
      installRoot,
      gitCommit: "a".repeat(40),
    });
    await writeFile(path.join(selected, "web", "index.html"), "unexpected");
    await rewriteManifestDigest(selected, "web/index.html");

    const receipt = await installPackagedRuntime({
      resourcesPath: resources,
      executablePath: executable,
      installRoot,
      platform: "darwin",
      arch: "arm64",
      buildNumber: 3757,
      gitCommit: "a".repeat(40),
      replaceSameBuild: true,
    });
    expect(receipt.reused_current).toBe(false);
    expect(receipt.version_directory).not.toBe(selected);
  });

  it("repairs an extra file left in the selected runtime tree", async () => {
    const { root, resources, executable, installRoot } = await fixture();
    const { selected } = await installFormalCurrent({
      root,
      resources,
      executable,
      installRoot,
      gitCommit: "a".repeat(40),
    });
    await writeFile(path.join(selected, "left-by-ditto"), "dirty");

    const receipt = await installPackagedRuntime({
      resourcesPath: resources,
      executablePath: executable,
      installRoot,
      platform: "darwin",
      arch: "arm64",
      buildNumber: 3757,
      gitCommit: "a".repeat(40),
      replaceSameBuild: true,
    });
    expect(receipt.reused_current).toBe(false);
    expect(receipt.version_directory).not.toBe(selected);
  });

  it.each(["missing"])(
    "refuses a same-build current whose bin/node is %s",
    async (state) => {
      const { root, resources, executable, installRoot } = await fixture();
      const { selected } = await installFormalCurrent({
        root,
        resources,
        executable,
        installRoot,
      });
      const node = path.join(selected, "bin", "node");
      const manifestPath = path.join(selected, "manifest.json");
      if (state === "missing") {
        await rm(node);
        const manifest = JSON.parse(await readFile(manifestPath, "utf8")) as {
          files: Record<string, string>;
        };
        delete manifest.files["bin/node"];
        await writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);
      } else {
        await chmod(node, 0o600);
      }
      await expect(
        installPackagedRuntime({
          resourcesPath: resources,
          executablePath: executable,
          installRoot,
          platform: "darwin",
          arch: "arm64",
          buildNumber: 3757,
        }),
      ).rejects.toThrow("installed_version_conflict");
    },
  );

  it("refuses to repair a selected tree whose manifest is unreadable", async () => {
    const { root, resources, executable, installRoot } = await fixture();
    const { selected } = await installFormalCurrent({
      root,
      resources,
      executable,
      installRoot,
      buildNumber: 3758,
    });
    await writeFile(path.join(selected, "manifest.json"), "{");

    await expect(
      installPackagedRuntime({
        resourcesPath: resources,
        executablePath: executable,
        installRoot,
        platform: "darwin",
        arch: "arm64",
        buildNumber: 3757,
        gitCommit: "a".repeat(40),
        replaceSameBuild: true,
      }),
    ).rejects.toThrow("distribution_manifest_invalid");
  });

  it("preserves downgrade refusal when selected current is newer", async () => {
    const { root, resources, executable, installRoot } = await fixture();
    await installFormalCurrent({
      root,
      resources,
      executable,
      installRoot,
      buildNumber: 3758,
    });
    await expect(
      installPackagedRuntime({
        resourcesPath: resources,
        executablePath: executable,
        installRoot,
        platform: "darwin",
        arch: "arm64",
        buildNumber: 3757,
      }),
    ).rejects.toThrow("distribution_downgrade_refused");
  });

  it("installs formal build 3762 over hourly-wrapped current 3761 without a downgrade override", async () => {
    const { root, resources, executable, installRoot } = await fixture();
    await installFormalCurrent({
      root,
      resources,
      executable,
      installRoot,
      buildNumber: 3761,
    });
    await expect(
      installPackagedRuntime({
        resourcesPath: resources,
        executablePath: executable,
        installRoot,
        platform: "darwin",
        arch: "arm64",
        buildNumber: 3762,
      }),
    ).resolves.toMatchObject({
      build_number: 3762,
      allow_downgrade: false,
      created_version: true,
      reused_current: false,
    });
  });

  it("refuses an escaping current link before same-build reuse", async () => {
    const { root, resources, executable, installRoot } = await fixture();
    const foreignRoot = path.join(root, "foreign");
    await installFormalCurrent({
      root,
      resources,
      executable,
      installRoot: foreignRoot,
    });
    await mkdir(installRoot, { recursive: true });
    await writeFile(path.join(installRoot, "placeholder"), "owned");
    await symlink(
      path.join(foreignRoot, "current"),
      path.join(installRoot, "current"),
    );
    await expect(
      installPackagedRuntime({
        resourcesPath: resources,
        executablePath: executable,
        installRoot,
        platform: "darwin",
        arch: "arm64",
        buildNumber: 3757,
      }),
    ).rejects.toThrow("install_current_unmanaged");
  });

  it("refuses a partial selected current before same-build reuse", async () => {
    const { resources, executable, installRoot } = await fixture();
    const partial = path.join(installRoot, "versions", "partial");
    await mkdir(partial, { recursive: true });
    await symlink("versions/partial", path.join(installRoot, "current"));
    await expect(
      installPackagedRuntime({
        resourcesPath: resources,
        executablePath: executable,
        installRoot,
        platform: "darwin",
        arch: "arm64",
        buildNumber: 3757,
      }),
    ).rejects.toThrow("distribution_manifest_invalid");
  });

  it("refuses a selected current not owned by the current user", async () => {
    const { root, resources, executable, installRoot } = await fixture();
    const { selected } = await installFormalCurrent({
      root,
      resources,
      executable,
      installRoot,
    });
    await chmod(selected, 0o777);
    await expect(
      installPackagedRuntime({
        resourcesPath: resources,
        executablePath: executable,
        installRoot,
        platform: "darwin",
        arch: "arm64",
        buildNumber: 3757,
      }),
    ).rejects.toThrow("install_current_unmanaged");
  });

  it("does not create current when the bundle has no host program", async () => {
    const { resources, executable, installRoot } = await fixture();
    await rm(path.join(resources, "host-bridge", "mcp.cjs"));
    await expect(
      installPackagedRuntime({
        resourcesPath: resources,
        executablePath: executable,
        installRoot,
        platform: "darwin",
        arch: "arm64",
      }),
    ).rejects.toThrow("packaged_runtime_incomplete");
    await expect(lstat(installRoot)).rejects.toThrow();
  });

  it("describes the bundle payload exactly as the staged copy would, without staging it", async () => {
    const { resources, executable, installRoot } = await fixture();
    await mkdir(path.join(resources, "core", "assets"));
    await writeFile(
      path.join(resources, "core", "assets", "model.bin"),
      "model",
    );
    await symlink(
      "assets/model.bin",
      path.join(resources, "core", "model-link"),
    );

    // The reuse decision reads this instead of a staged copy. It has to agree
    // with the manifest the staged copy produces, file for file, or moving the
    // decision ahead of the copy would decide something different. The paths
    // are resolved because the generated launchers embed them verbatim.
    const payload = await describeBundlePayload({
      resources: await realpath(resources),
      executable: await realpath(executable),
    });
    const receipt = await installPackagedRuntime({
      resourcesPath: resources,
      executablePath: executable,
      installRoot,
      platform: "darwin",
      arch: "arm64",
    });
    const manifest = JSON.parse(
      await readFile(
        path.join(receipt.version_directory, "manifest.json"),
        "utf8",
      ),
    ) as { version: string; files: Record<string, string> };

    expect(manifest.version).toBe(payload.version);
    expect(manifest.files).toEqual(payload.files);
    expect(Object.keys(payload.files)).toContain("bin/node");
  });

  it("refuses to replace a current that is not a managed version link", async () => {
    const { resources, executable, installRoot } = await fixture();
    await mkdir(path.join(installRoot, "current", "bin"), { recursive: true });
    await writeFile(
      path.join(installRoot, "current", "bin", "node"),
      "hand-made",
    );
    await expect(
      installPackagedRuntime({
        resourcesPath: resources,
        executablePath: executable,
        installRoot,
        platform: "darwin",
        arch: "arm64",
      }),
    ).rejects.toThrow("install_current_unmanaged");
    expect(
      await readFile(path.join(installRoot, "current", "bin", "node"), "utf8"),
    ).toBe("hand-made");
  });
});
