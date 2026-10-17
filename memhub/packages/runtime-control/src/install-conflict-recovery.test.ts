import {
  lstat,
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  readlink,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import {
  isRecoverableInstallConflict,
  quarantineSelectedRuntime,
} from "./install-conflict-recovery.js";

const roots: string[] = [];
afterEach(async () => {
  await Promise.all(
    roots.splice(0).map((root) => rm(root, { recursive: true, force: true })),
  );
});

/** Minimal managed install root: a `versions/` tree with `current` pointing
 * into it. Quarantine never inspects the payload, so no manifest is needed. */
async function fixture(): Promise<{
  installRoot: string;
  selected: string;
}> {
  const root = await mkdtemp(
    path.join(tmpdir(), "one-memory-install-recovery-"),
  );
  roots.push(root);
  const installRoot = path.join(root, "One Memory");
  const selected = path.join(
    installRoot,
    "versions",
    "1.0.0-b6-darwin-arm64",
  );
  await mkdir(selected, { recursive: true });
  await writeFile(path.join(selected, "manifest.json"), "{}");
  await symlink(
    path.relative(installRoot, selected),
    path.join(installRoot, "current"),
    "dir",
  );
  return { installRoot, selected };
}

describe("install conflict recovery", () => {
  it("moves the selected runtime aside and clears the current link", async () => {
    const { installRoot, selected } = await fixture();
    const receipt = await quarantineSelectedRuntime({ installRoot });
    expect(receipt.moved).toBe(true);
    expect(receipt.from).toBe(selected);
    // Gone from `versions/`, so a fresh install cannot collide with it.
    expect(await lstat(selected).catch(() => null)).toBeNull();
    expect(await lstat(path.join(installRoot, "current")).catch(() => null))
      .toBeNull();
    // Kept, not deleted: the tree is still readable where it landed.
    expect(receipt.to).not.toBeNull();
    expect(path.basename(path.dirname(receipt.to ?? ""))).toMatch(
      /^quarantine-[0-9]+$/,
    );
    const moved = path.join(receipt.to ?? "", "manifest.json");
    expect(await readFile(moved, "utf8")).toBe("{}");
    const quarantined = (await readdir(installRoot)).filter((entry) =>
      entry.startsWith("quarantine-"),
    );
    expect(quarantined).toHaveLength(1);
  });

  it("reports nothing to move when no runtime is selected yet", async () => {
    const root = await mkdtemp(
      path.join(tmpdir(), "one-memory-install-recovery-"),
    );
    roots.push(root);
    const installRoot = path.join(root, "One Memory");
    await mkdir(path.join(installRoot, "versions"), { recursive: true });
    await expect(
      quarantineSelectedRuntime({ installRoot }),
    ).resolves.toEqual({ moved: false, from: null, to: null });
  });

  it("refuses to move a current link that is not a managed version", async () => {
    const root = await mkdtemp(
      path.join(tmpdir(), "one-memory-install-recovery-"),
    );
    roots.push(root);
    const installRoot = path.join(root, "One Memory");
    const outside = path.join(root, "somewhere-else");
    await mkdir(outside, { recursive: true });
    await mkdir(path.join(installRoot, "versions"), { recursive: true });
    await symlink(outside, path.join(installRoot, "current"), "dir");
    await expect(
      quarantineSelectedRuntime({ installRoot }),
    ).rejects.toThrow("install_current_unmanaged");
    // Nothing outside the managed root was touched.
    expect(await lstat(outside)).toBeTruthy();
  });

  it("recognises only the failure it can repair", async () => {
    expect(
      isRecoverableInstallConflict(new Error("installed_version_conflict")),
    ).toBe(true);
    // A higher build already installed is a decision to keep, not to undo.
    expect(
      isRecoverableInstallConflict(
        new Error("distribution_downgrade_refused"),
      ),
    ).toBe(false);
    expect(isRecoverableInstallConflict(new Error("install_root_unsafe"))).toBe(
      false,
    );
    expect(isRecoverableInstallConflict("installed_version_conflict")).toBe(
      false,
    );
    // Nothing selected yet: the caller may retry, there is nothing to move.
    const { installRoot } = await fixture();
    await rm(path.join(installRoot, "current"));
    await expect(
      quarantineSelectedRuntime({ installRoot }),
    ).resolves.toMatchObject({ moved: false });
    expect(await readdir(path.join(installRoot, "versions"))).toHaveLength(1);
  });
});
