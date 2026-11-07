import type { Stats } from "node:fs";

/** The subset of `fs.Stats` the credential-file boundary relies on. */
export type CredentialNodeStats = Pick<
  Stats,
  "isDirectory" | "isFile" | "isSymbolicLink" | "nlink" | "uid" | "mode"
>;

/** Raised when a credential node is not private enough to read a secret from. */
export class UnsafeCredentialNodeError extends Error {
  constructor() {
    super("unsafe credential node");
    this.name = "UnsafeCredentialNodeError";
  }
}

/**
 * Validate a node on the installer-owned credential-file path.
 *
 * POSIX expresses "only this OS account can read it" with uid + mode bits.
 * Windows has no POSIX uid/mode: `fs.Stats` reports `uid: 0` and a mode
 * without permission bits, and `process.getuid` does not exist, so those
 * comparisons can never pass there (#384). On Windows we keep the checks that
 * are meaningful on NTFS -- a regular file with no hard links and no reparse
 * point -- and rely on the strict 51/52-byte token format in
 * `readHostCredentialFile` plus the user-profile ACL of the installer-owned
 * tree. Inspecting the DACL itself is a separate follow-up.
 *
 * The platform branch is deliberately inside this pure helper so it can be
 * regression-tested without axios, the broker, or a real credential file.
 */
export function assertPrivateCredentialNode(
  info: CredentialNodeStats,
  directory: boolean,
  platform: NodeJS.Platform = process.platform,
): void {
  if (directory ? !info.isDirectory() : !info.isFile() || info.nlink !== 1) {
    throw new UnsafeCredentialNodeError();
  }
  if (platform === "win32") {
    if (info.isSymbolicLink()) throw new UnsafeCredentialNodeError();
    return;
  }
  if (
    typeof process.getuid !== "function" ||
    info.uid !== process.getuid() ||
    (info.mode & 0o7777) !== (directory ? 0o700 : 0o600)
  ) {
    throw new UnsafeCredentialNodeError();
  }
}
