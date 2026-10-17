import { lstat, mkdir, readlink, rename, rm } from "node:fs/promises";
import path from "node:path";

/** Install failures that mean "the selected runtime and this package disagree",
 * not "this machine cannot host a runtime". Only these may be answered by
 * moving the selected tree aside and installing again. */
const RECOVERABLE_INSTALL_CODES = new Set(["installed_version_conflict"]);

export function isRecoverableInstallConflict(error: unknown): boolean {
  return error instanceof Error && RECOVERABLE_INSTALL_CODES.has(error.message);
}

export interface InstallQuarantineReceipt {
  moved: boolean;
  from: string | null;
  to: string | null;
}

/** Move the selected runtime aside so a fresh install can take its place.
 *
 * A package that cannot reuse the selected tree is not evidence of a broken
 * machine: re-cutting one release keeps its `build_number`, so a fully
 * up-to-date app can still meet a runtime it refuses to reuse. The tree is
 * moved, never deleted, and only inside the managed install root — the memory
 * data directory is a different path and stays untouched.
 *
 * Returns `moved: false` when there is nothing selected yet, which lets the
 * caller retry unconditionally after a first-attempt failure.
 */
export async function quarantineSelectedRuntime(options: {
  installRoot: string;
}): Promise<InstallQuarantineReceipt> {
  const installRoot = path.resolve(options.installRoot);
  const currentLink = path.join(installRoot, "current");
  const current = await lstat(currentLink).catch(() => null);
  if (current === null) return { moved: false, from: null, to: null };
  // Anything other than a managed link into `versions/` is not ours to move.
  if (!current.isSymbolicLink()) throw new Error("install_current_unmanaged");
  const target = await readlink(currentLink);
  if (path.isAbsolute(target)) throw new Error("install_current_unmanaged");
  const selected = path.resolve(installRoot, target);
  const versions = path.join(installRoot, "versions");
  if (path.dirname(selected) !== versions) {
    throw new Error("install_current_unmanaged");
  }
  const quarantine = path.join(installRoot, `quarantine-${Date.now()}`);
  await mkdir(quarantine, { recursive: true, mode: 0o700 });
  const destination = path.join(quarantine, path.basename(selected));
  await rename(selected, destination);
  await rm(currentLink);
  return { moved: true, from: selected, to: destination };
}
