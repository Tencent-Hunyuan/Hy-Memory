import { createHash } from "node:crypto";
import {
  cp,
  lstat,
  mkdir,
  mkdtemp,
  readdir,
  readFile,
  readlink,
  realpath,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  applyLocalInstall,
  planLocalInstall,
  rollbackLocalInstall,
  verifyInstalledDistribution,
} from "./local-install.js";

vi.mock("node:fs/promises", async (original) => {
  const actual = await original<typeof import("node:fs/promises")>();
  return { ...actual, cp: vi.fn(actual.cp) };
});

const roots: string[] = [];
beforeEach(async () => {
  const actual =
    await vi.importActual<typeof import("node:fs/promises")>(
      "node:fs/promises",
    );
  vi.mocked(cp).mockReset().mockImplementation(actual.cp);
});
afterEach(async () => {
  vi.restoreAllMocks();
  await Promise.all(
    roots.splice(0).map((root) => rm(root, { recursive: true, force: true })),
  );
});

/** Minimal but real Mach-O magic + cputype header, enough for the product's
 * architecture probe. `arch: "x64"` in a manifest must be backed by an actual
 * x86_64 slice, so the fixtures must carry headers, not text. */
function macho(cpuArch: string): Buffer {
  const cputype =
    cpuArch === "x86_64"
      ? 0x01000007
      : cpuArch === "arm64"
        ? 0x0100000c
        : 0x01000007;
  const buffer = Buffer.alloc(8);
  buffer.writeUInt32LE(0xfeedfacf, 0); // MH_MAGIC_64
  buffer.writeUInt32LE(cputype, 4);
  return buffer;
}

async function distribution(
  version = "1.0.0",
  options: { buildNumber?: number; arch?: string; nativeArch?: string } = {},
) {
  const root = await mkdtemp(path.join(tmpdir(), "one-memory-local-install-"));
  roots.push(root);
  const source = path.join(root, "source");
  const platform = process.platform === "darwin" ? "darwin" : "linux";
  const arch =
    options.arch ??
    (process.platform === "darwin" ? process.arch : process.arch);
  const nativeArch = options.nativeArch ?? arch;
  const files: Record<string, string | Buffer> = {
    "bin/node": macho(nativeArch),
    "bin/one-memory": "launcher",
    "bin/one-memory.cjs": "cli",
    "core/one-memory": macho(nativeArch),
    "runtime/runtime.cjs": "runtime",
    "host-bridge/mcp.cjs": "mcp",
    "host-bridge/recall-cli.cjs": "recall",
    "host-bridge/sync-hook/manifest.json": "{}",
    "web/index.html": "web",
  };
  for (const [name, content] of Object.entries(files)) {
    await mkdir(path.dirname(path.join(source, name)), { recursive: true });
    await writeFile(path.join(source, name), content, {
      mode:
        name.startsWith("bin/") || name === "core/one-memory" ? 0o700 : 0o600,
    });
  }
  const digests = Object.fromEntries(
    Object.entries(files).map(([name, content]) => [
      name,
      createHash("sha256").update(content).digest("hex"),
    ]),
  );
  await writeFile(
    path.join(source, "manifest.json"),
    JSON.stringify({
      schema_version: 1,
      product: "one-memory-runtime",
      version,
      build_number:
        options.buildNumber ??
        // Derive a monotonic default from the fixture version so an "upgrade"
        // to 2.0.0 outranks 1.0.0 unless a test overrides it explicitly.
        (Number.parseInt(version.split(".")[0] ?? "1", 10) || 1),
      platform,
      arch,
      files: digests,
    }),
  );
  return { root, source, installRoot: path.join(root, "installed") };
}

describe("local Runtime installation", () => {
  async function distributionWithLink() {
    const value = await distribution();
    const manifestPath = path.join(value.source, "manifest.json");
    const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
    await symlink("web/index.html", path.join(value.source, "web-link"));
    manifest.files["web-link"] = createHash("sha256")
      .update("symlink\0web/index.html")
      .digest("hex");
    await writeFile(manifestPath, JSON.stringify(manifest));
    return { ...value, manifest };
  }

  it("accepts a complete manifest containing web-link and web/index.html", async () => {
    const value = await distributionWithLink();
    await expect(
      verifyInstalledDistribution(value.source, process.platform, process.arch),
    ).resolves.toEqual(value.manifest);
  });

  it("hashes an internal directory symlink without reading it as a file", async () => {
    const value = await distribution();
    const manifestPath = path.join(value.source, "manifest.json");
    const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
    await symlink("web", path.join(value.source, "web-dir-link"));
    manifest.files["web-dir-link"] = createHash("sha256")
      .update("symlink\0web")
      .digest("hex");
    await writeFile(manifestPath, JSON.stringify(manifest));

    await expect(
      verifyInstalledDistribution(value.source, process.platform, process.arch),
    ).resolves.toEqual(manifest);
  });

  it("canonicalizes the entire flattened tree for manifest verification and same-version reuse", async () => {
    const value = await distributionWithLink();
    await expect(
      verifyInstalledDistribution(value.source, process.platform, process.arch),
    ).resolves.toEqual(value.manifest);
    const plan = await planLocalInstall({
      source: value.source,
      installRoot: value.installRoot,
      platform: process.platform,
      arch: process.arch,
    });
    expect(plan.files).toBe(10);
    const actual =
      await vi.importActual<typeof import("node:fs/promises")>(
        "node:fs/promises",
      );
    const stagedLinks: string[] = [];
    vi.mocked(cp).mockImplementation(async (...args) => {
      await actual.cp(...args);
      const stage = String(args[1]);
      const link = await readlink(path.join(stage, "web-link"));
      stagedLinks.push(link);
      expect(link).toBe("web/index.html");
      expect(await realpath(path.join(stage, "web-link"))).toBe(
        await realpath(path.join(stage, "web/index.html")),
      );
    });
    const first = await applyLocalInstall(plan);
    const repeated = await applyLocalInstall(plan);
    expect(stagedLinks).toEqual(["web/index.html", "web/index.html"]);
    expect(first.created_version).toBe(true);
    expect(repeated.created_version).toBe(false);
    for (const root of [value.source, plan.version_directory]) {
      expect(await readlink(path.join(root, "web-link"))).toBe(
        "web/index.html",
      );
      expect(await realpath(path.join(root, "web-link"))).toBe(
        await realpath(path.join(root, "web/index.html")),
      );
    }
    expect(
      await readFile(path.join(plan.version_directory, "web-link"), "utf8"),
    ).toBe("web");
  });

  it.each(["missing", "extra", "hash"])(
    "does not hide %s integrity failures in a canonicalized linked tree",
    async (change) => {
      const value = await distributionWithLink();
      if (change === "missing") await rm(path.join(value.source, "web-link"));
      if (change === "extra")
        await writeFile(path.join(value.source, "web-extra"), "unlisted");
      if (change === "hash")
        await writeFile(path.join(value.source, "web/index.html"), "changed");
      await expect(
        verifyInstalledDistribution(
          value.source,
          process.platform,
          process.arch,
        ),
      ).rejects.toThrow(
        change === "hash"
          ? "distribution_hash_mismatch"
          : "distribution_manifest_incomplete",
      );
    },
  );

  it("ignores Finder metadata without weakening payload verification", async () => {
    const value = await distribution();
    const plan = await planLocalInstall({
      source: value.source,
      installRoot: value.installRoot,
      platform: process.platform,
      arch: process.arch,
    });
    const first = await applyLocalInstall(plan);
    await writeFile(path.join(plan.version_directory, ".DS_Store"), "finder");

    await expect(
      verifyInstalledDistribution(
        plan.version_directory,
        process.platform,
        process.arch,
      ),
    ).resolves.toMatchObject({ version: plan.version });
    const repeated = await applyLocalInstall(plan);
    expect(first.created_version).toBe(true);
    expect(repeated.created_version).toBe(false);

    await writeFile(
      path.join(plan.version_directory, "unexpected-runtime-file"),
      "not metadata",
    );
    await expect(
      verifyInstalledDistribution(
        plan.version_directory,
        process.platform,
        process.arch,
      ),
    ).rejects.toThrow("distribution_manifest_incomplete");
  });

  it("does not let a non-file masquerade as Finder metadata", async () => {
    const value = await distribution();
    await symlink("../outside", path.join(value.source, ".DS_Store"));

    await expect(
      verifyInstalledDistribution(value.source, process.platform, process.arch),
    ).rejects.toThrow("distribution_path_invalid");
  });

  it.each(["extra", "hash"])(
    "same-version reuse still rejects %s changes to installed canonical trees",
    async (change) => {
      const value = await distributionWithLink();
      const plan = await planLocalInstall({
        source: value.source,
        installRoot: value.installRoot,
        platform: process.platform,
        arch: process.arch,
      });
      await applyLocalInstall(plan);
      if (change === "extra")
        await writeFile(
          path.join(plan.version_directory, "web-extra"),
          "unlisted",
        );
      else
        await writeFile(
          path.join(plan.version_directory, "web/index.html"),
          "changed",
        );
      await expect(applyLocalInstall(plan)).rejects.toThrow(
        "installed_version_conflict",
      );
      expect(await readlink(plan.current_link)).toBe(
        path.relative(value.installRoot, plan.version_directory),
      );
    },
  );

  it("rejects a relative escape introduced after planning without publishing current", async () => {
    const value = await distributionWithLink();
    const plan = await planLocalInstall({
      source: value.source,
      installRoot: value.installRoot,
      platform: process.platform,
      arch: process.arch,
    });
    const outside = path.join(value.root, "outside.html");
    await writeFile(outside, "web");
    await rm(path.join(value.source, "web-link"));
    await symlink("../outside.html", path.join(value.source, "web-link"));
    await expect(applyLocalInstall(plan)).rejects.toThrow(
      "distribution_link_escapes",
    );
    await expect(readlink(plan.current_link)).rejects.toMatchObject({
      code: "ENOENT",
    });
    expect(await readFile(outside, "utf8")).toBe("web");
  });

  it("rejects an escaping link introduced in stage even when its referent hash matches", async () => {
    const value = await distributionWithLink();
    const plan = await planLocalInstall({
      source: value.source,
      installRoot: value.installRoot,
      platform: process.platform,
      arch: process.arch,
    });
    const actual =
      await vi.importActual<typeof import("node:fs/promises")>(
        "node:fs/promises",
      );
    vi.mocked(cp).mockImplementationOnce(async (...args) => {
      await actual.cp(...args);
      const stagedLink = path.join(String(args[1]), "web-link");
      await rm(stagedLink);
      await symlink(path.join(value.source, "web/index.html"), stagedLink);
    });
    await expect(applyLocalInstall(plan)).rejects.toThrow(
      "distribution_link_escapes",
    );
    await expect(readlink(plan.current_link)).rejects.toMatchObject({
      code: "ENOENT",
    });
    expect(await readlink(path.join(value.source, "web-link"))).toBe(
      "web/index.html",
    );
  });

  it("verifies hashes before producing an install plan", async () => {
    const value = await distribution();
    const plan = await planLocalInstall({
      source: value.source,
      installRoot: value.installRoot,
      platform: process.platform,
      arch: process.arch,
    });

    expect(plan.files).toBe(9);
    expect(plan.version_directory).toContain("1.0.0-");
    expect(plan.current_link).toBe(path.join(value.installRoot, "current"));
  });

  it("installs a version and atomically points current at it", async () => {
    const value = await distribution();
    const plan = await planLocalInstall({
      source: value.source,
      installRoot: value.installRoot,
      platform: process.platform,
      arch: process.arch,
    });

    const receipt = await applyLocalInstall(plan);

    expect(await readlink(plan.current_link)).toBe(
      path.relative(value.installRoot, plan.version_directory),
    );
    expect(
      await readFile(
        path.join(plan.version_directory, "web/index.html"),
        "utf8",
      ),
    ).toBe("web");
    expect(receipt.created_version).toBe(true);
  });

  it("refuses an existing install-root ancestor symlink before writing outside", async () => {
    const value = await distribution();
    const outside = path.join(value.root, "outside");
    const alias = path.join(value.root, "alias");
    await mkdir(outside);
    await symlink(outside, alias, "dir");
    const installRoot = path.join(alias, "managed");
    const plan = await planLocalInstall({
      source: value.source,
      installRoot,
      platform: process.platform,
      arch: process.arch,
    });

    await expect(applyLocalInstall(plan)).rejects.toThrow(
      "install_root_unsafe",
    );
    await expect(lstat(path.join(outside, "managed"))).rejects.toMatchObject({
      code: "ENOENT",
    });
  });

  it("rejects a modified source without replacing current", async () => {
    const first = await distribution("1.0.0");
    const firstPlan = await planLocalInstall({
      source: first.source,
      installRoot: first.installRoot,
      platform: process.platform,
      arch: process.arch,
    });
    await applyLocalInstall(firstPlan);
    await writeFile(path.join(first.source, "web/index.html"), "tampered");

    await expect(
      planLocalInstall({
        source: first.source,
        installRoot: first.installRoot,
        platform: process.platform,
        arch: process.arch,
      }),
    ).rejects.toThrow("distribution_hash_mismatch");
    expect(await readlink(firstPlan.current_link)).toBe(
      path.relative(first.installRoot, firstPlan.version_directory),
    );
  });

  it("rejects a version that could escape the versions directory", async () => {
    const value = await distribution("../outside");

    await expect(
      planLocalInstall({
        source: value.source,
        installRoot: value.installRoot,
        platform: process.platform,
        arch: process.arch,
      }),
    ).rejects.toThrow("distribution_manifest_invalid");
  });

  it("does not replace an unmanaged current path", async () => {
    const value = await distribution();
    const plan = await planLocalInstall({
      source: value.source,
      installRoot: value.installRoot,
      platform: process.platform,
      arch: process.arch,
    });
    await mkdir(value.installRoot, { recursive: true });
    await writeFile(plan.current_link, "not a managed link");

    await expect(applyLocalInstall(plan)).rejects.toThrow(
      "install_current_unmanaged",
    );
    expect(await readFile(plan.current_link, "utf8")).toBe(
      "not a managed link",
    );
  });

  it("does not change the mode of an existing safe install root", async () => {
    if (process.platform === "win32") return;
    const value = await distribution();
    const { chmod, stat } = await import("node:fs/promises");
    await mkdir(value.installRoot, { mode: 0o755 });
    await chmod(value.installRoot, 0o755);
    const plan = await planLocalInstall({
      source: value.source,
      installRoot: value.installRoot,
      platform: process.platform,
      arch: process.arch,
    });

    await applyLocalInstall(plan);

    expect((await stat(value.installRoot)).mode & 0o777).toBe(0o755);
  });

  it("rejects a caller-modified install plan", async () => {
    const value = await distribution();
    const plan = await planLocalInstall({
      source: value.source,
      installRoot: value.installRoot,
      platform: process.platform,
      arch: process.arch,
    });

    await expect(
      applyLocalInstall({
        ...plan,
        version_directory: path.join(value.installRoot, "versions", "other"),
      }),
    ).rejects.toThrow("install_plan_invalid");
  });

  it("restores the previous current target and removes a newly added version", async () => {
    const first = await distribution("1.0.0");
    const firstPlan = await planLocalInstall({
      source: first.source,
      installRoot: first.installRoot,
      platform: process.platform,
      arch: process.arch,
    });
    await applyLocalInstall(firstPlan);
    const second = await distribution("2.0.0");
    const secondPlan = await planLocalInstall({
      source: second.source,
      installRoot: first.installRoot,
      platform: process.platform,
      arch: process.arch,
    });
    const receipt = await applyLocalInstall(secondPlan);

    await rollbackLocalInstall(structuredClone(receipt));

    expect(await readlink(firstPlan.current_link)).toBe(
      path.relative(first.installRoot, firstPlan.version_directory),
    );
    await expect(
      readFile(path.join(secondPlan.version_directory, "manifest.json")),
    ).rejects.toThrow();
  });

  // Anti-downgrade: ordering authority is `build_number` alone
  // (docs/contracts/c0-anti-downgrade-versioning.md).
  it("refuses an incoming build_number lower than the installed one and leaves current untouched", async () => {
    if (process.platform !== "darwin") return;
    const first = await distribution("1.0.0", { buildNumber: 5 });
    await applyLocalInstall(
      await planLocalInstall({
        source: first.source,
        installRoot: first.installRoot,
        platform: process.platform,
        arch: process.arch,
      }),
    );
    const before = await readlink(path.join(first.installRoot, "current"));
    const older = await distribution("0.9.0", { buildNumber: 4 });
    await expect(
      planLocalInstall({
        source: older.source,
        installRoot: first.installRoot,
        platform: process.platform,
        arch: process.arch,
      }),
    ).rejects.toThrow("distribution_downgrade_refused");
    // The refusal must be a no-op: current unchanged, no new version directory.
    expect(await readlink(path.join(first.installRoot, "current"))).toBe(
      before,
    );
    expect(
      (await readdir(path.join(first.installRoot, "versions"))).sort(),
    ).toHaveLength(1);
  });

  it("allows a downgrade only through the explicit allowDowngrade opt-in", async () => {
    if (process.platform !== "darwin") return;
    const first = await distribution("1.0.0", { buildNumber: 5 });
    await applyLocalInstall(
      await planLocalInstall({
        source: first.source,
        installRoot: first.installRoot,
        platform: process.platform,
        arch: process.arch,
      }),
    );
    const older = await distribution("0.9.0", { buildNumber: 4 });
    const plan = await planLocalInstall({
      source: older.source,
      installRoot: first.installRoot,
      platform: process.platform,
      arch: process.arch,
      allowDowngrade: true,
    });
    expect(plan.build_number).toBe(4);
    expect(plan.allow_downgrade).toBe(true);
  });

  it("refuses an equal build_number whose bytes differ", async () => {
    if (process.platform !== "darwin") return;
    const first = await distribution("1.0.0", { buildNumber: 5 });
    await applyLocalInstall(
      await planLocalInstall({
        source: first.source,
        installRoot: first.installRoot,
        platform: process.platform,
        arch: process.arch,
      }),
    );
    const sameBuildDifferentBytes = await distribution("1.0.0", {
      buildNumber: 5,
    });
    await writeFile(
      path.join(sameBuildDifferentBytes.source, "web/index.html"),
      "different web",
    );
    const files = JSON.parse(
      await readFile(
        path.join(sameBuildDifferentBytes.source, "manifest.json"),
        "utf8",
      ),
    ).files;
    files["web/index.html"] = createHash("sha256")
      .update("different web")
      .digest("hex");
    const manifestPath = path.join(
      sameBuildDifferentBytes.source,
      "manifest.json",
    );
    const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
    manifest.files = files;
    await writeFile(manifestPath, JSON.stringify(manifest));
    await expect(
      planLocalInstall({
        source: sameBuildDifferentBytes.source,
        installRoot: first.installRoot,
        platform: process.platform,
        arch: process.arch,
      }),
    ).rejects.toThrow("installed_version_conflict");
  });

  it("replaces an equal build_number whose bytes differ only when the caller opts in", async () => {
    if (process.platform !== "darwin") return;
    const first = await distribution("1.0.0", { buildNumber: 5 });
    const firstPlan = await planLocalInstall({
      source: first.source,
      installRoot: first.installRoot,
      platform: process.platform,
      arch: process.arch,
    });
    await applyLocalInstall(firstPlan);

    // The same release rebuilt from a new commit: identical build number,
    // different bytes. This is what a re-cut MemHub package looks like.
    const rebuilt = await distribution("1.0.0", { buildNumber: 5 });
    await writeFile(
      path.join(rebuilt.source, "web/index.html"),
      "rebuilt web",
    );
    const manifestPath = path.join(rebuilt.source, "manifest.json");
    const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
    manifest.files["web/index.html"] = createHash("sha256")
      .update("rebuilt web")
      .digest("hex");
    await writeFile(manifestPath, JSON.stringify(manifest));

    const plan = await planLocalInstall({
      source: rebuilt.source,
      installRoot: first.installRoot,
      platform: process.platform,
      arch: process.arch,
      allowSameBuildReplace: true,
    });
    // The replacement lands in its own directory, so the superseded tree is
    // left intact as a rollback instead of being edited under a running host.
    expect(plan.same_build_replace).toBe(true);
    expect(plan.version_directory).not.toBe(firstPlan.version_directory);

    await applyLocalInstall(plan);
    expect(await readlink(path.join(first.installRoot, "current"))).toBe(
      path.relative(first.installRoot, plan.version_directory),
    );
    expect(
      await readFile(
        path.join(plan.version_directory, "web/index.html"),
        "utf8",
      ),
    ).toBe("rebuilt web");
    expect(
      await readFile(
        path.join(firstPlan.version_directory, "web/index.html"),
        "utf8",
      ),
    ).toBe("web");
  });

  it("does not let the same-build opt-in weaken downgrade refusal", async () => {
    if (process.platform !== "darwin") return;
    const first = await distribution("1.0.0", { buildNumber: 5 });
    await applyLocalInstall(
      await planLocalInstall({
        source: first.source,
        installRoot: first.installRoot,
        platform: process.platform,
        arch: process.arch,
      }),
    );
    const older = await distribution("0.9.0", { buildNumber: 4 });
    await expect(
      planLocalInstall({
        source: older.source,
        installRoot: first.installRoot,
        platform: process.platform,
        arch: process.arch,
        allowSameBuildReplace: true,
      }),
    ).rejects.toThrow("distribution_downgrade_refused");
  });

  it("accepts an equal build_number when the bytes are identical (idempotent reinstall)", async () => {
    if (process.platform !== "darwin") return;
    const first = await distribution("1.0.0", { buildNumber: 5 });
    const plan = await planLocalInstall({
      source: first.source,
      installRoot: first.installRoot,
      platform: process.platform,
      arch: process.arch,
    });
    await applyLocalInstall(plan);
    await expect(
      planLocalInstall({
        source: first.source,
        installRoot: first.installRoot,
        platform: process.platform,
        arch: process.arch,
      }),
    ).resolves.toMatchObject({ build_number: 5 });
  });

  it("gives different build_numbers distinct version directories even when the displayed version is equal", async () => {
    if (process.platform !== "darwin") return;
    const first = await distribution("0.0.0", { buildNumber: 43 });
    const firstPlan = await planLocalInstall({
      source: first.source,
      installRoot: first.installRoot,
      platform: process.platform,
      arch: process.arch,
    });
    await applyLocalInstall(firstPlan);

    const second = await distribution("0.0.0", { buildNumber: 44 });
    await writeFile(
      path.join(second.source, "web/index.html"),
      "higher build web",
    );
    const manifestPath = path.join(second.source, "manifest.json");
    const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
    manifest.files["web/index.html"] = createHash("sha256")
      .update("higher build web")
      .digest("hex");
    await writeFile(manifestPath, JSON.stringify(manifest));

    const secondPlan = await planLocalInstall({
      source: second.source,
      installRoot: first.installRoot,
      platform: process.platform,
      arch: process.arch,
    });
    expect(secondPlan.build_number).toBe(44);
    expect(secondPlan.version_directory).not.toBe(firstPlan.version_directory);
    await applyLocalInstall(secondPlan);
    expect(await readlink(path.join(first.installRoot, "current"))).toBe(
      path.relative(first.installRoot, secondPlan.version_directory),
    );
    expect(
      (await readdir(path.join(first.installRoot, "versions"))).sort(),
    ).toHaveLength(2);
  });

  // Manifest/architecture integrity: the declared arch must be backed by the
  // actual Mach-O slice, not just the manifest string.
  it("refuses a manifest whose arch does not match the payload binaries", async () => {
    if (process.platform !== "darwin") return;
    const lying = await distribution("1.0.0", {
      arch: process.arch === "arm64" ? "x64" : "arm64",
      nativeArch: process.arch,
    });
    await expect(
      planLocalInstall({
        source: lying.source,
        installRoot: lying.installRoot,
        platform: process.platform,
        arch: process.arch === "arm64" ? "x64" : "arm64",
      }),
    ).rejects.toThrow("distribution_arch_mismatch");
  });

  it("accepts a manifest whose arch matches the payload binaries", async () => {
    if (process.platform !== "darwin") return;
    const honest = await distribution("1.0.0", {
      arch: process.arch,
      nativeArch: process.arch,
    });
    await expect(
      planLocalInstall({
        source: honest.source,
        installRoot: honest.installRoot,
        platform: process.platform,
        arch: process.arch,
      }),
    ).resolves.toMatchObject({ arch: process.arch });
  });

  it("keeps one hourly package's install root independent from another's", async () => {
    if (process.platform !== "darwin") return;
    // Two hourly packages carry the SAME build_number but different trees, the
    // shape that used to collide on one shared `managed-runtime/current`. Per
    // package id they now own separate roots, so neither can move the other's
    // `current` link.
    const first = await distribution("1.0.0", { buildNumber: 90 });
    const second = await distribution("1.0.0", { buildNumber: 90 });
    const rootA = path.join(first.root, "hourly", "pkg-A", "managed-runtime");
    const rootB = path.join(second.root, "hourly", "pkg-B", "managed-runtime");
    const planA = await planLocalInstall({
      source: first.source,
      installRoot: rootA,
      platform: process.platform,
      arch: process.arch,
    });
    await applyLocalInstall(planA);
    const aBefore = await readlink(planA.current_link);

    const planB = await planLocalInstall({
      source: second.source,
      installRoot: rootB,
      platform: process.platform,
      arch: process.arch,
    });
    await applyLocalInstall(planB);

    // B never shares A's root, so A's `current` is untouched.
    expect(planA.current_link).not.toBe(planB.current_link);
    expect(await readlink(planA.current_link)).toBe(aBefore);
  });
});

it("refuses rollback with a malformed persisted receipt", async () => {
  await expect(rollbackLocalInstall({} as never)).rejects.toThrow(
    "install_receipt_invalid",
  );
});
