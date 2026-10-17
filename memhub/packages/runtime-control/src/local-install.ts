import { createHash, randomUUID } from "node:crypto";
import { createReadStream } from "node:fs";
import {
  chmod,
  cp,
  lstat,
  mkdir,
  readdir,
  readFile,
  readlink,
  realpath,
  rename,
  rm,
  symlink,
} from "node:fs/promises";
import path from "node:path";
import {
  CLI_PROTOCOL,
  CLI_SUPPORTED_RUNTIME_PROTOCOL,
  type ProtocolRange,
  protocolIncludes,
  protocolRange,
} from "./release-contract.js";

export interface DistributionManifest {
  schema_version: 1;
  product: "one-memory-runtime";
  version: string;
  /** Monotonic ordering authority (docs/contracts/c0-anti-downgrade-versioning.md).
   * Optional while packages built before that contract still exist: when either
   * side of a transition omits it, ordering falls back to the pre-contract
   * exact-version behavior rather than inventing a number. */
  build_number?: number;
  git_commit?: string | null;
  runtime_protocol?: number;
  supported_cli_protocol?: ProtocolRange;
  platform: "darwin" | "linux" | "win32";
  arch: string;
  files: Record<string, string>;
}

export interface LocalInstallPlan {
  schema_version: 1;
  source: string;
  install_root: string;
  version_directory: string;
  current_link: string;
  version: string;
  build_number: number | null;
  /** Recorded so `applyLocalInstall` re-verifies with the same opt-in the plan
   * was created under; it does not itself authorize anything. */
  allow_downgrade: boolean;
  /** True when this plan replaces an equal `build_number` whose bytes differ.
   * The incoming build gets its own version directory and the superseded tree
   * is left in place, so the transition is still reversible. */
  same_build_replace: boolean;
  require_protocol_compatibility: boolean;
  platform: DistributionManifest["platform"];
  arch: string;
  files: number;
}

export interface LocalInstallReceipt extends LocalInstallPlan {
  previous_current_target: string | null;
  created_version: boolean;
}

export async function planLocalInstall(options: {
  source: string;
  installRoot: string;
  platform: NodeJS.Platform;
  arch: string;
  /** Explicit, per-invocation opt-in required to install a distribution whose
   * `build_number` is lower than the selected one. Default is refusal. */
  allowDowngrade?: boolean;
  /** Explicit, per-invocation opt-in required to replace an equal
   * `build_number` whose bytes differ. Default is refusal
   * (`installed_version_conflict`); see `inspectBuildNumberTransition`. */
  allowSameBuildReplace?: boolean;
  requireProtocolCompatibility?: boolean;
}): Promise<LocalInstallPlan> {
  if (!isPlatform(options.platform))
    throw new Error("distribution_platform_unsupported");
  const source = await realpath(options.source);
  const sourceInfo = await lstat(source);
  if (!sourceInfo.isDirectory() || sourceInfo.isSymbolicLink()) {
    throw new Error("distribution_source_invalid");
  }
  const manifest = await verifyDistribution(
    source,
    options.platform,
    options.arch,
  );
  if (options.requireProtocolCompatibility === true) {
    assertDistributionProtocolCompatibility(manifest);
  }
  const transition = await inspectBuildNumberTransition({
    installRoot: options.installRoot,
    incoming: manifest,
    incomingSource: source,
    allowDowngrade: options.allowDowngrade === true,
    allowSameBuildReplace: options.allowSameBuildReplace === true,
  });
  const files = Object.keys(manifest.files).sort();
  const installRoot = path.resolve(options.installRoot);
  if (overlaps(source, installRoot))
    throw new Error("distribution_install_overlap");
  // A numbered build must have its own version directory. Otherwise every
  // hourly package with the same displayed `version` (for example `0.0.0`)
  // lands in one path, and the equal-version same-tree check rejects a higher
  // build with `installed_version_conflict` before `current` can move.
  // Unnumbered pre-contract packages retain the legacy path.
  const id =
    manifest.build_number === undefined
      ? `${manifest.version}-${manifest.platform}-${manifest.arch}`
      : `${manifest.version}-b${manifest.build_number}-${manifest.platform}-${manifest.arch}`;
  return Object.freeze({
    schema_version: 1,
    source,
    install_root: installRoot,
    version_directory: path.join(
      installRoot,
      "versions",
      transition.sameBuildReplace
        ? `${id}-r${payloadFingerprint(manifest)}`
        : id,
    ),
    current_link: path.join(installRoot, "current"),
    version: manifest.version,
    build_number: manifest.build_number ?? null,
    allow_downgrade: options.allowDowngrade === true,
    same_build_replace: transition.sameBuildReplace,
    require_protocol_compatibility:
      options.requireProtocolCompatibility === true,
    platform: manifest.platform,
    arch: manifest.arch,
    files: files.length,
  });
}

export async function applyLocalInstall(
  plan: LocalInstallPlan,
): Promise<LocalInstallReceipt> {
  if (process.platform === "win32")
    throw new Error("windows_install_not_verified");
  const installRoot = path.resolve(plan.install_root);
  const versions = path.join(installRoot, "versions");
  const versionDirectory = path.resolve(plan.version_directory);
  const expectedParent = path.resolve(versions);
  const verified = await planLocalInstall({
    source: plan.source,
    installRoot,
    platform: plan.platform,
    arch: plan.arch,
    allowDowngrade: plan.allow_downgrade,
    allowSameBuildReplace: plan.same_build_replace,
    requireProtocolCompatibility: plan.require_protocol_compatibility,
  });
  if (
    path.dirname(versionDirectory) !== expectedParent ||
    versionDirectory !== verified.version_directory ||
    path.resolve(plan.current_link) !== verified.current_link ||
    plan.version !== verified.version ||
    plan.build_number !== verified.build_number ||
    plan.same_build_replace !== verified.same_build_replace ||
    plan.require_protocol_compatibility !==
      verified.require_protocol_compatibility ||
    plan.files !== verified.files
  ) {
    throw new Error("install_plan_invalid");
  }
  const installRootExisted = await lstat(installRoot).catch(() => null);
  const versionsExisted = await lstat(versions).catch(() => null);
  await assertExistingDirectoryAncestrySafe(installRoot);
  await mkdir(versions, { recursive: true, mode: 0o700 });
  if (!installRootExisted) await chmod(installRoot, 0o700);
  if (!versionsExisted) await chmod(versions, 0o700);
  await assertPrivateDirectory(installRoot);
  await assertPrivateDirectory(versions);
  await assertExistingDirectoryAncestrySafe(installRoot);
  const current = await lstat(plan.current_link).catch(() => null);
  let previousCurrentTarget: string | null = null;
  if (current) {
    if (!current.isSymbolicLink()) throw new Error("install_current_unmanaged");
    previousCurrentTarget = await readlink(plan.current_link);
    const target = path.resolve(installRoot, previousCurrentTarget);
    const relative = path.relative(versions, target);
    if (relative.startsWith("..") || path.isAbsolute(relative)) {
      throw new Error("install_current_unmanaged");
    }
  }
  const stage = `${versionDirectory}.stage.${randomUUID()}`;
  const temporaryLink = path.join(installRoot, `.current.${randomUUID()}`);
  let createdVersion = false;
  try {
    await cp(plan.source, stage, {
      recursive: true,
      dereference: false,
      verbatimSymlinks: true,
      errorOnExist: true,
      force: false,
    });
    await verifyDistribution(stage, plan.platform, plan.arch);
    const existing = await lstat(versionDirectory).catch(() => null);
    createdVersion = existing === null;
    if (existing) {
      if (!existing.isDirectory() || existing.isSymbolicLink()) {
        throw new Error("installed_version_unsafe");
      }
      await verifySameTree(stage, versionDirectory);
      await rm(stage, { recursive: true });
    } else {
      await rename(stage, versionDirectory);
    }
    await symlink(
      path.relative(installRoot, versionDirectory),
      temporaryLink,
      "dir",
    );
    await rename(temporaryLink, plan.current_link);
    const receipt = Object.freeze({
      ...plan,
      previous_current_target: previousCurrentTarget,
      created_version: createdVersion,
    });
    return receipt;
  } catch (error) {
    await rm(stage, { recursive: true, force: true });
    await rm(temporaryLink, { force: true });
    if (createdVersion) {
      await rm(versionDirectory, { recursive: true, force: true });
    }
    throw error;
  }
}

async function assertExistingDirectoryAncestrySafe(
  target: string,
): Promise<void> {
  const resolved = path.resolve(target);
  const root = path.parse(resolved).root;
  const parts = resolved.slice(root.length).split(path.sep).filter(Boolean);
  let current = root;
  for (const part of parts) {
    current = path.join(current, part);
    let info: Awaited<ReturnType<typeof lstat>>;
    try {
      info = await lstat(current);
    } catch (error) {
      if (
        error instanceof Error &&
        "code" in error &&
        error.code === "ENOENT"
      ) {
        return;
      }
      throw error;
    }
    if (
      info.isSymbolicLink() &&
      process.platform === "darwin" &&
      ["/tmp", "/var", "/etc"].includes(current)
    ) {
      current = await realpath(current);
      continue;
    }
    if (info.isSymbolicLink() || !info.isDirectory()) {
      throw new Error("install_root_unsafe");
    }
  }
}

export async function rollbackLocalInstall(
  receipt: LocalInstallReceipt,
): Promise<void> {
  if (
    !receipt ||
    receipt.schema_version !== 1 ||
    typeof receipt.install_root !== "string" ||
    typeof receipt.version_directory !== "string" ||
    typeof receipt.current_link !== "string" ||
    (receipt.previous_current_target !== null &&
      typeof receipt.previous_current_target !== "string") ||
    typeof receipt.created_version !== "boolean"
  ) {
    throw new Error("install_receipt_invalid");
  }
  if (process.platform === "win32")
    throw new Error("windows_install_not_verified");
  const installRoot = path.resolve(receipt.install_root);
  const versions = path.join(installRoot, "versions");
  const versionDirectory = path.resolve(receipt.version_directory);
  if (
    path.dirname(versionDirectory) !== versions ||
    path.resolve(receipt.current_link) !== path.join(installRoot, "current")
  ) {
    throw new Error("install_receipt_invalid");
  }
  const current = await lstat(receipt.current_link).catch(() => null);
  if (!current?.isSymbolicLink()) throw new Error("install_current_changed");
  const target = path.resolve(
    installRoot,
    await readlink(receipt.current_link),
  );
  if (target !== versionDirectory) throw new Error("install_current_changed");

  if (receipt.previous_current_target === null) {
    await rm(receipt.current_link);
  } else {
    const previous = path.resolve(installRoot, receipt.previous_current_target);
    const relative = path.relative(versions, previous);
    if (relative.startsWith("..") || path.isAbsolute(relative)) {
      throw new Error("install_receipt_invalid");
    }
    const temporaryLink = path.join(
      installRoot,
      `.current.rollback.${randomUUID()}`,
    );
    await symlink(receipt.previous_current_target, temporaryLink, "dir");
    await rename(temporaryLink, receipt.current_link);
  }
  if (receipt.created_version) {
    await rm(versionDirectory, { recursive: true });
  }
}

/**
 * Enforce the release anti-downgrade rule before any write.
 *
 * Ordering authority is `build_number` alone (see
 * `docs/contracts/c0-anti-downgrade-versioning.md`); version strings and
 * directory names are never compared. A lower incoming build is refused unless
 * the caller explicitly opted in. An equal build is only allowed when the
 * selected version directory already contains byte-identical content —
 * otherwise the install would silently swap an equal-build package, which the
 * contract classifies as `installed_version_conflict`.
 *
 * `allowSameBuildReplace` is an explicit, per-invocation escape from that last
 * rule, and it is off by default: a silent equal-build swap is precisely what
 * the refusal protects against. It exists for one caller shape — a runtime
 * shipped inside an app bundle that the OS has already signed and notarized.
 * Such a payload is trusted by construction, and refusing it leaves an affected
 * machine with an app that cannot start at all after a same-build re-release.
 * The replacement never edits the selected tree in place: the incoming build
 * lands in its own version directory and `current` moves atomically, so the
 * superseded tree stays available as a rollback.
 */
async function inspectBuildNumberTransition(options: {
  installRoot: string;
  incoming: DistributionManifest;
  incomingSource: string;
  allowDowngrade: boolean;
  allowSameBuildReplace: boolean;
}): Promise<{ sameBuildReplace: boolean }> {
  const installRoot = path.resolve(options.installRoot);
  const current = await lstat(path.join(installRoot, "current")).catch(
    () => null,
  );
  if (!current) return { sameBuildReplace: false };
  if (!current.isSymbolicLink()) throw new Error("install_current_unmanaged");
  const target = path.resolve(
    installRoot,
    await readlink(path.join(installRoot, "current")),
  );
  const versions = path.join(installRoot, "versions");
  const relative = path.relative(versions, target);
  if (relative.startsWith("..") || path.isAbsolute(relative)) {
    throw new Error("install_current_unmanaged");
  }
  const selected = await readManifest(target).catch(() => null);
  if (selected === null) return { sameBuildReplace: false };
  // Pre-contract packages have no `build_number`. Comparing them against an
  // invented ordinal would either break the hourly chain or manufacture a
  // downgrade; fall back to the prior exact-version behavior until both sides
  // are numbered.
  if (options.incoming.build_number === undefined)
    return { sameBuildReplace: false };
  if (selected.build_number === undefined) return { sameBuildReplace: false };
  if (options.incoming.build_number > selected.build_number)
    return { sameBuildReplace: false };
  if (options.incoming.build_number === selected.build_number) {
    // Same build: an idempotent reinstall is fine only if the bytes match. The
    // incoming source is verified, so compare it against the selected tree.
    try {
      await verifySameTree(options.incomingSource, target);
    } catch {
      if (!options.allowSameBuildReplace) {
        throw new Error("installed_version_conflict");
      }
      return { sameBuildReplace: true };
    }
    return { sameBuildReplace: false };
  }
  if (!options.allowDowngrade) {
    throw new Error("distribution_downgrade_refused");
  }
  return { sameBuildReplace: false };
}

/** Short, stable digest of the payload a manifest describes.
 *
 * Two packages of the same `build_number` whose bytes differ must not share a
 * version directory: `applyLocalInstall` compares the staged tree against the
 * directory it is about to occupy and would refuse the replacement before
 * `current` could move. Suffixing the directory with a payload fingerprint
 * keeps the equal-build replacement a plain "install then switch" operation. */
function payloadFingerprint(manifest: DistributionManifest): string {
  const digest = createHash("sha256");
  digest.update(`${manifest.version}\0${manifest.build_number ?? ""}\0`);
  for (const relative of Object.keys(manifest.files).sort()) {
    digest.update(`${relative}\0${manifest.files[relative]}\0`);
  }
  return digest.digest("hex").slice(0, 12);
}

async function assertPrivateDirectory(target: string): Promise<void> {
  const info = await lstat(target);
  if (
    !info.isDirectory() ||
    info.isSymbolicLink() ||
    (info.mode & 0o022) !== 0 ||
    (typeof process.getuid === "function" && info.uid !== process.getuid())
  ) {
    throw new Error("install_root_unsafe");
  }
}

async function readManifest(source: string): Promise<DistributionManifest> {
  let value: unknown;
  try {
    value = JSON.parse(
      await readFile(path.join(source, "manifest.json"), "utf8"),
    );
  } catch {
    throw new Error("distribution_manifest_invalid");
  }
  if (!value || typeof value !== "object")
    throw new Error("distribution_manifest_invalid");
  const manifest = value as Partial<DistributionManifest>;
  if (
    manifest.schema_version !== 1 ||
    manifest.product !== "one-memory-runtime" ||
    typeof manifest.version !== "string" ||
    !manifest.version ||
    (manifest.build_number !== undefined &&
      !isBuildNumber(manifest.build_number)) ||
    (manifest.git_commit !== undefined &&
      manifest.git_commit !== null &&
      (typeof manifest.git_commit !== "string" ||
        !/^[0-9a-f]{40}$/.test(manifest.git_commit))) ||
    (manifest.runtime_protocol !== undefined &&
      (!Number.isSafeInteger(manifest.runtime_protocol) ||
        manifest.runtime_protocol < 1)) ||
    (manifest.supported_cli_protocol !== undefined &&
      !protocolRange(manifest.supported_cli_protocol)) ||
    !isPlatform(manifest.platform) ||
    typeof manifest.arch !== "string" ||
    !manifest.arch ||
    !manifest.files ||
    typeof manifest.files !== "object" ||
    Array.isArray(manifest.files) ||
    Object.values(manifest.files).some(
      (digest) => typeof digest !== "string" || !/^[a-f0-9]{64}$/.test(digest),
    )
  ) {
    throw new Error("distribution_manifest_invalid");
  }
  return manifest as DistributionManifest;
}

export function assertDistributionProtocolCompatibility(
  manifest: DistributionManifest,
): void {
  if (
    manifest.runtime_protocol === undefined ||
    manifest.supported_cli_protocol === undefined
  ) {
    throw new Error("distribution_protocol_missing");
  }
  if (
    !protocolIncludes(
      CLI_SUPPORTED_RUNTIME_PROTOCOL,
      manifest.runtime_protocol,
    ) ||
    !protocolIncludes(manifest.supported_cli_protocol, CLI_PROTOCOL)
  ) {
    throw new Error("distribution_protocol_incompatible");
  }
}

/** A monotonically increasing, JS-safe non-negative integer. Not a boolean. */
function isBuildNumber(value: unknown): value is number {
  return (
    typeof value === "number" &&
    Number.isSafeInteger(value) &&
    value >= 0 &&
    !Number.isNaN(value)
  );
}

/** Verify an existing distribution without installation or repair. */
export async function verifyInstalledDistribution(
  directory: string,
  platform: NodeJS.Platform,
  arch: string,
): Promise<DistributionManifest> {
  return verifyDistribution(directory, platform, arch);
}

async function verifyDistribution(
  source: string,
  platform: NodeJS.Platform,
  arch: string,
): Promise<DistributionManifest> {
  const manifest = await readManifest(source);
  if (manifest.platform !== platform || manifest.arch !== arch) {
    throw new Error("distribution_target_mismatch");
  }
  await verifyPayloadArchitecture(source, manifest);
  const files = Object.keys(manifest.files).sort();
  if (
    !/^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$/.test(manifest.version) ||
    !/^[0-9A-Za-z][0-9A-Za-z._-]{0,31}$/.test(manifest.arch) ||
    files.length === 0 ||
    files.some((file) => !safeRelative(file))
  ) {
    throw new Error("distribution_manifest_invalid");
  }
  const actual = await treeFiles(source);
  const expected = [...files, "manifest.json"].sort();
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error("distribution_manifest_incomplete");
  }
  for (const file of files) {
    if ((await sha256(path.join(source, file))) !== manifest.files[file]) {
      throw new Error("distribution_hash_mismatch");
    }
  }
  return manifest;
}

/** Native payloads whose Mach-O architecture must agree with the manifest.
 * `bin/node` is the bundled interpreter and `core/one-memory` is the packaged
 * Core; both are platform binaries. A forged `manifest.arch` must not be able
 * to smuggle an arm64 payload into an "x64" install. */
const NATIVE_PAYLOAD_FILES: Record<string, string[]> = {
  darwin: ["bin/node", "core/one-memory"],
};

/**
 * Refuse a distribution whose declared `arch` is not actually present in its
 * native binaries. Only reported on darwin today: other platforms keep their
 * existing manifest/hash validations until a real binary-format probe is
 * verified for them.
 */
async function verifyPayloadArchitecture(
  source: string,
  manifest: DistributionManifest,
): Promise<void> {
  const targets = NATIVE_PAYLOAD_FILES[manifest.platform];
  if (!targets) return;
  let verifiedSlices = 0;
  for (const relative of targets) {
    const file = path.join(source, relative);
    const info = await lstat(file).catch(() => null);
    if (!info) continue;
    const architectures = await machoArchitectures(file);
    // Some legitimate layouts replace a native payload with a launcher script
    // (the packaged-App installer writes `bin/node` as a shell launcher that
    // execs Electron). Those carry no architecture of their own, so they are
    // skipped -- but at least one real Mach-O slice must still be verified, and
    // any Mach-O that IS present must contain the declared architecture.
    if (architectures === null) continue;
    if (!architectures.includes(manifest.arch)) {
      throw new Error("distribution_arch_mismatch");
    }
    verifiedSlices += 1;
  }
  if (targets.length > 0 && verifiedSlices === 0) {
    // A darwin distribution with no inspectable native payload cannot prove its
    // declared architecture; fail closed rather than pass vacuously.
    throw new Error("distribution_arch_mismatch");
  }
}

// Mach-O file magic numbers. `*_CIGAM*` are the byte-swapped variants, which is
// how a little-endian host tells that a big-endian image follows.
const MH_MAGIC = 0xfeedface;
const MH_MAGIC_64 = 0xfeedfacf;
const MH_CIGAM = 0xcefaedfe;
const MH_CIGAM_64 = 0xcffaedfe;
const FAT_MAGIC = 0xcafebabe;
const FAT_CIGAM = 0xbebafeca;
const FAT_MAGIC_64 = 0xcafebabf;
const FAT_CIGAM_64 = 0xbfbafeca;

const CPU_ARCH_ABI64 = 0x01000000;
const CPU_TYPE_X86 = 7;
const CPU_TYPE_ARM = 12;
const CPU_TYPE_ARM64_32 = 12 | 0x02000000;

/** Map a Mach-O `cputype` to the architecture token used in manifests. */
function archName(cputype: number): string | null {
  switch (cputype) {
    case CPU_TYPE_X86:
      return "i386";
    case CPU_TYPE_X86 | CPU_ARCH_ABI64:
      return "x86_64";
    case CPU_TYPE_ARM:
      return "arm";
    case CPU_TYPE_ARM | CPU_ARCH_ABI64:
      return "arm64";
    case CPU_TYPE_ARM64_32:
      return "arm64_32";
    default:
      return null;
  }
}

/** Read the architecture list of a thin or fat Mach-O directly from its header.
 *
 * Deliberately does not shell out to `lipo`: the header is the authoritative
 * statement of the slice architecture, and parsing it keeps this check usable
 * on stripped build artifacts and in unit tests. Returns null when the file is
 * not a Mach-O this parser understands. */
async function machoArchitectures(file: string): Promise<string[] | null> {
  const bytes = await readFile(file).catch(() => null);
  if (bytes === null || bytes.length < 8) return null;
  const magic = bytes.readUInt32BE(0);
  if (
    magic === FAT_MAGIC ||
    magic === FAT_CIGAM ||
    magic === FAT_MAGIC_64 ||
    magic === FAT_CIGAM_64
  ) {
    // Fat headers store the byte order in the magic; read the count with the
    // matching endianness, then walk the fat_arch entries.
    const little = magic === FAT_CIGAM || magic === FAT_CIGAM_64;
    const read = (offset: number) =>
      little ? bytes.readUInt32LE(offset) : bytes.readUInt32BE(offset);
    const entrySize =
      magic === FAT_MAGIC_64 || magic === FAT_CIGAM_64 ? 32 : 20;
    const count = read(4);
    const architectures: string[] = [];
    for (let index = 0; index < count; index += 1) {
      const offset = 8 + index * entrySize;
      if (offset + 4 > bytes.length) return null;
      const name = archName(read(offset));
      if (name !== null) architectures.push(name);
    }
    return architectures.length > 0 ? architectures : null;
  }
  if (
    magic !== MH_MAGIC &&
    magic !== MH_MAGIC_64 &&
    magic !== MH_CIGAM &&
    magic !== MH_CIGAM_64
  ) {
    return null;
  }
  // A little-endian Mach-O stores `MH_MAGIC_64` (0xfeedfacf) as bytes
  // `cf fa ed fe`, which a big-endian read reports as `MH_CIGAM_64`. So the
  // byte-swapped magic is what marks a little-endian image on this host.
  const little = magic === MH_CIGAM || magic === MH_CIGAM_64;
  const cputype = little ? bytes.readUInt32LE(4) : bytes.readUInt32BE(4);
  const name = archName(cputype);
  return name === null ? null : [name];
}

async function treeFiles(root: string, current = root): Promise<string[]> {
  const { readdir } = await import("node:fs/promises");
  const entries = await readdir(current, { withFileTypes: true });
  entries.sort((left, right) => left.name.localeCompare(right.name));
  const result: string[] = [];
  for (const entry of entries) {
    // Finder may add this metadata file after installation. It is not Runtime
    // payload and must not make an otherwise verified install unreusable.
    if (entry.name === ".DS_Store") {
      if (!entry.isFile()) throw new Error("distribution_path_invalid");
      continue;
    }
    const target = path.join(current, entry.name);
    const relative = path.relative(root, target);
    if (!safeRelative(relative)) throw new Error("distribution_path_invalid");
    if (entry.isSymbolicLink()) {
      const link = await readlink(target);
      const resolved = path.resolve(path.dirname(target), link);
      if (path.relative(root, resolved).startsWith("..")) {
        throw new Error("distribution_link_escapes");
      }
      result.push(relative);
    } else if (entry.isDirectory()) {
      result.push(...(await treeFiles(root, target)));
    } else if (entry.isFile()) {
      result.push(relative);
    } else {
      throw new Error("distribution_path_invalid");
    }
  }
  return result.sort();
}

async function verifySameTree(left: string, right: string): Promise<void> {
  const leftFiles = await treeFiles(left);
  const rightFiles = await treeFiles(right);
  if (JSON.stringify(leftFiles) !== JSON.stringify(rightFiles)) {
    throw new Error("installed_version_conflict");
  }
  for (const file of leftFiles) {
    if (
      (await sha256(path.join(left, file))) !==
      (await sha256(path.join(right, file)))
    ) {
      throw new Error("installed_version_conflict");
    }
  }
}

async function sha256(file: string): Promise<string> {
  const digest = createHash("sha256");
  const info = await lstat(file);
  if (info.isSymbolicLink()) {
    digest.update("symlink\0");
    digest.update(await readlink(file));
    return digest.digest("hex");
  }
  for await (const chunk of createReadStream(file)) digest.update(chunk);
  return digest.digest("hex");
}

function safeRelative(value: string): boolean {
  return (
    value.length > 0 &&
    !path.isAbsolute(value) &&
    value !== ".." &&
    !value.startsWith(`..${path.sep}`) &&
    !value.includes("\0")
  );
}

function overlaps(left: string, right: string): boolean {
  const fromLeft = path.relative(left, right);
  const fromRight = path.relative(right, left);
  return (
    fromLeft === "" ||
    (!fromLeft.startsWith("..") && !path.isAbsolute(fromLeft)) ||
    (!fromRight.startsWith("..") && !path.isAbsolute(fromRight))
  );
}

function isPlatform(value: unknown): value is DistributionManifest["platform"] {
  return value === "darwin" || value === "linux" || value === "win32";
}

export interface LocalUninstallPlan {
  schema_version: 1;
  install_root: string;
  current_link: string;
  version_directories: string[];
  cli_link: string | null;
  /** Never touched. Stated in the plan so a reader can see the boundary. */
  preserved_data_dir: string | null;
  stops_service: boolean;
}

export interface LocalUninstallReceipt extends LocalUninstallPlan {
  removed_current_link: boolean;
  removed_version_directories: string[];
  removed_cli_link: boolean;
}

/**
 * Plan removing the managed installation.
 *
 * The rule this exists to enforce: uninstall removes the *program*, never the
 * *data*. `dataDir` is passed so the plan can name it as preserved — a reader
 * should be able to see the boundary rather than infer it — and it is also
 * checked against the subtree being deleted. If the user pointed the data
 * directory inside `<install-root>/versions/`, the recursive removal would
 * destroy their data while the plan still advertised it as preserved; refusing
 * here is the only honest outcome, and the CLI calls this before it stops the
 * service or touches any file, so the refusal is a no-op on disk.
 */
export async function planLocalUninstall(options: {
  installRoot: string;
  home: string;
  dataDir: string | null;
  cliLinkPath: string | null;
  stopsService: boolean;
  /** Explicit per-invocation override for the "data inside the removed
   * subtree" refusal. It does NOT make the data safe: it only changes which
   * directory the plan is asked to protect, so the caller must have moved the
   * data out first. Recorded in the plan so a reader can audit the choice. */
  preserveDataDir?: boolean;
}): Promise<LocalUninstallPlan> {
  const installRoot = path.resolve(options.installRoot);
  const versions = path.join(installRoot, "versions");
  // The data directory is out of scope for uninstall. If it happens to live
  // inside the subtree we delete recursively (e.g. the user pointed --data-dir
  // at <install-root>/versions/<something>), removing that subtree would
  // silently destroy their data while the plan still advertises it as
  // preserved. Refuse instead of lying: moving it out is a user decision.
  // `preserveDataDir` is the escape hatch: when the user really did point the
  // data directory inside the subtree we delete recursively, the only ways out
  // are (a) move the data out and retry, or (b) explicitly confirm that THIS
  // invocation must not consider that directory. Without (b) the service could
  // never be uninstalled at all -- the guard would strand it. With (b) the
  // caller has stated, per invocation, what it is protecting, and the plan
  // records that stated boundary instead of silently deleting it.
  if (
    !options.preserveDataDir &&
    options.dataDir !== null &&
    overlaps(path.resolve(options.dataDir), versions)
  ) {
    throw new Error("uninstall_data_inside_install_root");
  }
  const entries = await readdir(versions).catch(() => []);
  // Never list the preserved data directory for deletion, even when it lives
  // under versions/. `preserved_data_dir` has to mean it, otherwise the plan
  // advertises protection while the same plan deletes the directory as if it
  // were a managed version.
  const protectedDataDir =
    options.dataDir === null ? null : path.resolve(options.dataDir);
  const versionDirectories: string[] = [];
  for (const entry of entries) {
    const candidate = path.join(versions, entry);
    if (protectedDataDir !== null && overlaps(candidate, protectedDataDir)) {
      continue;
    }
    const info = await lstat(candidate).catch(() => null);
    if (info?.isDirectory() && !info.isSymbolicLink()) {
      versionDirectories.push(candidate);
    }
  }
  versionDirectories.sort();
  return {
    schema_version: 1,
    install_root: installRoot,
    current_link: path.join(installRoot, "current"),
    version_directories: versionDirectories,
    cli_link: options.cliLinkPath,
    preserved_data_dir: options.dataDir,
    stops_service: options.stopsService,
  };
}

export async function applyLocalUninstall(
  plan: LocalUninstallPlan,
): Promise<LocalUninstallReceipt> {
  return applyLocalUninstallInternal(plan);
}

/** Internal safety harness for the future managed release implementation. */
export async function applyLocalUninstallInternal(
  plan: LocalUninstallPlan,
): Promise<LocalUninstallReceipt> {
  if (process.platform === "win32")
    throw new Error("windows_install_not_verified");
  const installRoot = path.resolve(plan.install_root);
  const versions = path.join(installRoot, "versions");
  const removedVersionDirectories: string[] = [];
  // Only paths under the install root are touched, and each is re-checked: a
  // plan is data, and data can be stale by the time it is applied.
  for (const directory of plan.version_directories) {
    const resolved = path.resolve(directory);
    if (path.dirname(resolved) !== versions)
      throw new Error("uninstall_path_unsafe");
    await rm(resolved, { recursive: true });
    removedVersionDirectories.push(resolved);
  }
  const current = path.resolve(plan.current_link);
  if (current !== path.join(installRoot, "current")) {
    throw new Error("uninstall_path_unsafe");
  }
  const info = await lstat(current).catch(() => null);
  const removedCurrentLink = Boolean(info);
  if (info) {
    await rm(current);
  }
  let removedCliLink = false;
  if (plan.cli_link) {
    // Only remove the PATH entry this product created: a symlink pointing at
    // `<install_root>/current/bin/one-memory`. A regular file, a directory or a
    // symlink to something else belongs to the user (or another tool) and must
    // survive an uninstall. Verified defect: the previous version removed any
    // object here, deleting a foreign `one-memory` file the user had put there.
    const linkInfo = await lstat(plan.cli_link).catch(() => null);
    if (linkInfo?.isSymbolicLink()) {
      const target = path.resolve(
        path.dirname(plan.cli_link),
        await readlink(plan.cli_link),
      );
      const expected = path.join(installRoot, "current", "bin", "one-memory");
      if (target === expected) {
        await rm(plan.cli_link);
        removedCliLink = true;
      }
    }
  }
  return {
    ...plan,
    removed_current_link: removedCurrentLink,
    removed_version_directories: removedVersionDirectories,
    removed_cli_link: removedCliLink,
  };
}
