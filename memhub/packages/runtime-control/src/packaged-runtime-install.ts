import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import {
  chmod,
  copyFile,
  cp,
  lstat,
  mkdir,
  mkdtemp,
  readdir,
  readFile,
  readlink,
  realpath,
  rename,
  rm,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import {
  applyLocalInstall,
  type DistributionManifest,
  type LocalInstallReceipt,
  planLocalInstall,
  verifyInstalledDistribution,
} from "./local-install.js";
import {
  RUNTIME_PROTOCOL,
  RUNTIME_SUPPORTED_CLI_PROTOCOL,
} from "./release-contract.js";

export interface PackagedRuntimeInstallReceipt extends LocalInstallReceipt {
  reused_current: boolean;
}

/** The directories of an App bundle that make up the managed runtime payload.
 *
 * `installPackagedRuntime` stages exactly these four, plus the two generated
 * launchers under `bin/`, so the same relative paths describe both the bundle
 * and the staged copy. */
const PAYLOAD_DIRECTORIES = ["core", "host-bridge", "runtime", "web"] as const;

/** How many payload files are hashed at once. Sequential hashing of the
 *  Mac bundle costs about two seconds of pure read; a small pool keeps the
 *  digests identical while overlapping the reads. */
const DIGEST_CONCURRENCY = 8;

/** The bundle payload as the staged copy would look, without copying a byte.
 *
 * Reuse is decided by comparing file digests, and nothing about that decision
 * needs the files to live in a staging directory: the staged tree is a byte
 * for byte copy of `resources/{core,host-bridge,runtime,web}` plus two
 * generated launchers, so the same relative paths and digests can be read in
 * place. Doing it this way keeps the 373MB copy off the reuse path entirely. */
export async function describeBundlePayload(options: {
  resources: string;
  executable: string;
  serviceEnableMarker?: string;
}): Promise<{ version: string; files: Record<string, string> }> {
  const files: Record<string, string> = {};
  // Walked with the bundle root as the walker's root, not each directory on
  // its own: a symlink is only legal while it stays inside the payload the
  // staging step would copy, and that boundary is the resources directory.
  const relatives: string[] = [];
  for (const name of PAYLOAD_DIRECTORIES) {
    const source = path.join(options.resources, name);
    const info = await lstat(source).catch(() => null);
    if (!info?.isDirectory() || info.isSymbolicLink()) continue;
    relatives.push(...(await treeFiles(options.resources, source)));
  }
  Object.assign(files, await digestRelatives(options.resources, relatives));
  const cli = path.join(options.resources, "cli", "one-memory.cjs");
  const cliInfo = await lstat(cli).catch(() => null);
  if (cliInfo?.isFile()) {
    files["bin/one-memory.cjs"] = await sha256(cli);
    files["bin/one-memory"] = sha256Text(cliLauncher());
  }
  // Generated from the App path, so it is known without writing the file. The
  // comparison below skips it anyway: it is the one file that legitimately
  // differs between two launches of the same build.
  files["bin/node"] = sha256Text(
    nodeLauncher(options.executable, options.serviceEnableMarker),
  );
  const ordered = Object.fromEntries(
    Object.keys(files)
      .sort()
      .map((relative) => [relative, files[relative]]),
  );
  return { version: versionOf(ordered), files: ordered };
}

function versionOf(files: Record<string, string>): string {
  const digest = createHash("sha256");
  for (const relative of Object.keys(files).sort()) {
    digest.update(relative);
    digest.update("\0");
    digest.update(files[relative]);
    digest.update("\0");
  }
  return digest.digest("hex");
}

/** Copy this app bundle into the managed install, then point `current` at it.
 *
 * Host configs name `<install root>/current/bin/node`. That file is a launcher
 * which starts the binary inside this bundle, so replacing the bundle does
 * not change the path other apps already have. `current` stays a link into
 * `versions/`, which is what a later CLI install is willing to replace.
 */
export async function installPackagedRuntime(options: {
  resourcesPath: string;
  executablePath: string;
  installRoot: string;
  platform: "darwin" | "linux";
  arch: string;
  /** Anti-downgrade ordering authority for the App-bundled runtime. Defaults to
   * 0: the bundle is not a published build, so it can never outrank a
   * CLI-installed distribution. Callers that do know the bundle's build number
   * must pass it (see `docs/contracts/c0-anti-downgrade-versioning.md`). */
  buildNumber?: number;
  /** Allow the bundle to take over from an already-selected runtime that
   * reports this same `build_number` but different bytes.
   *
   * Off by default, which preserves `installed_version_conflict`. Desktop turns
   * it on: the bundle it carries has passed notarization, so its payload is
   * already trusted, and the same build number is re-released whenever a
   * release is rebuilt from a new commit. Refusing that case leaves an affected
   * machine with an app that exits on every launch, which is a worse outcome
   * than replacing the runtime. The superseded tree is kept, not edited. */
  replaceSameBuild?: boolean;
  gitCommit?: string;
  /** App-owned services use this marker to stop launchd before the App binary
   * is invoked after the bundle has been removed. */
  serviceEnableMarker?: string;
}): Promise<PackagedRuntimeInstallReceipt> {
  if (options.platform !== "darwin" && options.platform !== "linux") {
    throw new Error("packaged_runtime_platform_unsupported");
  }
  if (!/^[0-9A-Za-z][0-9A-Za-z._-]{0,31}$/.test(options.arch)) {
    throw new Error("packaged_runtime_arch_invalid");
  }
  if (
    options.gitCommit !== undefined &&
    !/^[0-9a-f]{40}$/.test(options.gitCommit)
  ) {
    throw new Error("packaged_runtime_commit_invalid");
  }
  const resources = await realpath(options.resourcesPath);
  const executable = await realpath(options.executablePath);
  const executableInfo = await lstat(executable);
  if (!executableInfo.isFile() || /[\0\r\n]/.test(executable)) {
    throw new Error("packaged_runtime_executable_invalid");
  }
  for (const relative of [
    path.join("host-bridge", "mcp.cjs"),
    path.join("host-bridge", "recall-cli.cjs"),
    path.join("core", "one-memory"),
  ]) {
    const info = await lstat(path.join(resources, relative)).catch(() => null);
    if (!info?.isFile()) throw new Error("packaged_runtime_incomplete");
  }

  // Before writing anything: the overwhelmingly common case is a machine that
  // already runs this exact build, and staging it costs a 373MB copy plus two
  // full passes of hashing that the reuse decision then throws away. Reading
  // the bundle in place decides the same thing without any of that.
  const payload = await describeBundlePayload({
    resources,
    executable,
    ...(options.serviceEnableMarker === undefined
      ? {}
      : { serviceEnableMarker: options.serviceEnableMarker }),
  });
  const reused = await reuseSelectedSameBuild({
    payload,
    installRoot: options.installRoot,
    platform: options.platform,
    arch: options.arch,
    buildNumber: options.buildNumber,
    replaceSameBuild: options.replaceSameBuild === true,
    executablePath: executable,
    ...(options.serviceEnableMarker === undefined
      ? {}
      : { serviceEnableMarker: options.serviceEnableMarker }),
  });
  if (reused !== null) return reused;

  const stage = await mkdtemp(
    path.join(tmpdir(), "one-memory-packaged-runtime-"),
  );
  try {
    await mkdir(path.join(stage, "bin"), { recursive: true, mode: 0o700 });
    for (const name of ["core", "host-bridge", "runtime", "web"]) {
      const source = path.join(resources, name);
      const info = await lstat(source).catch(() => null);
      if (!info?.isDirectory() || info.isSymbolicLink()) continue;
      await cp(source, path.join(stage, name), {
        recursive: true,
        dereference: false,
        verbatimSymlinks: true,
        errorOnExist: true,
        force: false,
      });
    }
    const cli = path.join(resources, "cli", "one-memory.cjs");
    const cliInfo = await lstat(cli).catch(() => null);
    if (cliInfo?.isFile()) {
      await copyFile(cli, path.join(stage, "bin", "one-memory.cjs"));
      const launcher = path.join(stage, "bin", "one-memory");
      await writeFile(launcher, cliLauncher(), {
        encoding: "utf8",
        mode: 0o700,
      });
      await chmod(launcher, 0o700);
    }
    const node = path.join(stage, "bin", "node");
    await writeFile(
      node,
      nodeLauncher(executable, options.serviceEnableMarker),
      {
        encoding: "utf8",
        mode: 0o700,
      },
    );
    await chmod(node, 0o700);

    const version = await treeVersion(stage);
    const files = await fileDigests(stage);
    await writeFile(
      path.join(stage, "manifest.json"),
      `${JSON.stringify(
        {
          schema_version: 1,
          product: "one-memory-runtime",
          version,
          build_number: options.buildNumber ?? 0,
          git_commit: options.gitCommit ?? null,
          runtime_protocol: RUNTIME_PROTOCOL,
          supported_cli_protocol: RUNTIME_SUPPORTED_CLI_PROTOCOL,
          platform: options.platform,
          arch: options.arch,
          files,
        },
        null,
        2,
      )}\n`,
      { encoding: "utf8", mode: 0o600 },
    );
    const plan = await planLocalInstall({
      source: stage,
      installRoot: options.installRoot,
      platform: options.platform,
      arch: options.arch,
      allowSameBuildReplace: options.replaceSameBuild === true,
      requireProtocolCompatibility: true,
    });
    return Object.freeze({
      ...(await applyLocalInstall(plan)),
      reused_current: false,
    });
  } finally {
    await rm(stage, { recursive: true, force: true });
  }
}

async function reuseSelectedSameBuild(options: {
  payload: { version: string; files: Record<string, string> };
  installRoot: string;
  platform: "darwin" | "linux";
  arch: string;
  buildNumber: number | undefined;
  replaceSameBuild: boolean;
  executablePath: string;
  serviceEnableMarker?: string;
}): Promise<PackagedRuntimeInstallReceipt | null> {
  if (options.buildNumber === undefined) return null;
  const installRoot = path.resolve(options.installRoot);
  const versions = path.join(installRoot, "versions");
  const currentLink = path.join(installRoot, "current");
  const currentInfo = await lstat(currentLink).catch(() => null);
  if (currentInfo === null) return null;
  if (!currentInfo.isSymbolicLink())
    throw new Error("install_current_unmanaged");
  const selectedLink = await readlink(currentLink);
  if (path.isAbsolute(selectedLink))
    throw new Error("install_current_unmanaged");
  const selected = path.resolve(installRoot, selectedLink);
  if (path.dirname(selected) !== versions) {
    throw new Error("install_current_unmanaged");
  }
  for (const directory of [installRoot, versions, selected]) {
    const info = await lstat(directory).catch(() => null);
    if (
      !info?.isDirectory() ||
      info.isSymbolicLink() ||
      (info.mode & 0o022) !== 0 ||
      (typeof process.getuid === "function" && info.uid !== process.getuid())
    ) {
      throw new Error("install_current_unmanaged");
    }
  }
  const selectedReal = await realpath(selected);
  const versionsReal = await realpath(versions);
  if (
    path.dirname(selectedReal) !== versionsReal ||
    (await realpath(currentLink)) !== selectedReal
  ) {
    throw new Error("install_current_unmanaged");
  }
  const selectedManifest = await verifyInstalledDistribution(
    selected,
    options.platform,
    options.arch,
  );
  if (selectedManifest.build_number !== options.buildNumber) return null;
  // The bundle's own payload, read in place. It has to agree with the selected
  // tree file for file; there is no cheaper signal that is also exact, and a
  // wrong reuse here is what `installed_version_conflict` exists to prevent.
  for (const [relative, digest] of Object.entries(options.payload.files)) {
    if (relative === "bin/node") continue;
    if (relative === "bin/one-memory") {
      const selectedDigest = selectedManifest.files[relative];
      const selectedPackageId = (
        selectedManifest as DistributionManifest & { package_id?: unknown }
      ).package_id;
      const formalDigest =
        typeof selectedPackageId === "string" &&
        /^[0-9A-Za-z][0-9A-Za-z._-]{0,127}$/.test(selectedPackageId)
          ? sha256Text(
              formalCliLauncher(selectedPackageId, options.buildNumber),
            )
          : null;
      if (selectedDigest !== digest && selectedDigest !== formalDigest) {
        // Same build number, different bytes: not reusable. Hand the install
        // back to the normal path so the bundle lands in its own directory.
        if (options.replaceSameBuild) return null;
        throw new Error("installed_version_conflict");
      }
      continue;
    }
    if (selectedManifest.files[relative] !== digest) {
      if (options.replaceSameBuild) return null;
      throw new Error("installed_version_conflict");
    }
  }
  const selectedPackageId = (
    selectedManifest as DistributionManifest & { package_id?: unknown }
  ).package_id;
  if (typeof selectedPackageId !== "string") {
    await rewritePackagedNodeLauncher(
      selected,
      nodeLauncher(options.executablePath, options.serviceEnableMarker),
      selectedManifest,
    );
  }
  const selectedNode = await lstat(path.join(selected, "bin", "node")).catch(
    () => null,
  );
  if (
    !selectedNode?.isFile() ||
    selectedNode.isSymbolicLink() ||
    (selectedNode.mode & 0o111) === 0
  ) {
    throw new Error("installed_version_conflict");
  }
  return Object.freeze({
    schema_version: 1,
    source: selected,
    install_root: installRoot,
    version_directory: selected,
    current_link: currentLink,
    version: selectedManifest.version,
    build_number: selectedManifest.build_number ?? null,
    allow_downgrade: false,
    // Nothing was replaced: the selected tree already matched this bundle.
    same_build_replace: false,
    require_protocol_compatibility: true,
    platform: selectedManifest.platform,
    arch: selectedManifest.arch,
    files: Object.keys(selectedManifest.files).length,
    previous_current_target: selectedLink,
    created_version: false,
    reused_current: true,
  });
}

function nodeLauncher(
  executable: string,
  serviceEnableMarker?: string,
): string {
  const guard = serviceEnableMarker
    ? `if [ ! -x '${shellQuote(executable)}' ]; then
  rm -f -- '${shellQuote(serviceEnableMarker)}'
  exit 0
fi
`
    : "";
  return `#!/bin/sh
# MemHub managed App runtime launcher v2
${guard}export ELECTRON_RUN_AS_NODE=1
exec '${shellQuote(executable)}' "$@"
`;
}

function shellQuote(value: string): string {
  if (/\0|\r|\n/.test(value)) throw new Error("packaged_runtime_path_invalid");
  return value.replaceAll("'", `'\\''`);
}

async function rewritePackagedNodeLauncher(
  selected: string,
  expected: string,
  manifest: DistributionManifest,
): Promise<void> {
  const node = path.join(selected, "bin", "node");
  const current = await readFile(node, "utf8").catch(() => null);
  if (current === expected) return;
  if (current === null) throw new Error("installed_version_conflict");
  if (
    !current.startsWith("#!/bin/sh\nexport ELECTRON_RUN_AS_NODE=1\n") &&
    !current.startsWith("#!/bin/sh\n# MemHub managed App runtime launcher v2\n")
  ) {
    const info = await lstat(node).catch(() => null);
    if (info?.isFile() && (info.mode & 0o111) !== 0) return;
    throw new Error("installed_version_conflict");
  }
  const temporaryNode = `${node}.repair-${process.pid}`;
  const manifestPath = path.join(selected, "manifest.json");
  const temporaryManifest = `${manifestPath}.repair-${process.pid}`;
  const repaired = {
    ...manifest,
    files: { ...manifest.files, "bin/node": sha256Text(expected) },
  };
  try {
    await writeFile(temporaryNode, expected, { encoding: "utf8", mode: 0o700 });
    await chmod(temporaryNode, 0o700);
    await writeFile(
      temporaryManifest,
      `${JSON.stringify(repaired, null, 2)}\n`,
      {
        encoding: "utf8",
        mode: 0o600,
      },
    );
    await rename(temporaryNode, node);
    await rename(temporaryManifest, manifestPath);
  } finally {
    await rm(temporaryNode, { force: true });
    await rm(temporaryManifest, { force: true });
  }
}

function cliLauncher(): string {
  return `#!/bin/sh
SELF=$0
while [ -L "$SELF" ]; do
  DIR=$(CDPATH= cd -P -- "$(dirname -- "$SELF")" && pwd) || exit 1
  LINK=$(readlink "$SELF") || exit 1
  case "$LINK" in
    /*) SELF=$LINK ;;
    *) SELF=$DIR/$LINK ;;
  esac
done
DIR=$(CDPATH= cd -P -- "$(dirname -- "$SELF")" && pwd) || exit 1
exec "$DIR/node" "$DIR/one-memory.cjs" "$@"
`;
}

function formalCliLauncher(packageId: string, buildNumber: number): string {
  return `#!/bin/sh
# Package identity for the shared diagnostic chain.
ONE_MEMORY_BUILD_ID="\${ONE_MEMORY_BUILD_ID:-${packageId}}"
ONE_MEMORY_PACKAGE_ID="\${ONE_MEMORY_PACKAGE_ID:-${packageId}}"
ONE_MEMORY_ROUND="\${ONE_MEMORY_ROUND:-${buildNumber}}"
ONE_MEMORY_BUILD_NUMBER="\${ONE_MEMORY_BUILD_NUMBER:-${buildNumber}}"
ONE_MEMORY_DIAGNOSTICS=1
export ONE_MEMORY_BUILD_ID ONE_MEMORY_PACKAGE_ID ONE_MEMORY_ROUND ONE_MEMORY_BUILD_NUMBER ONE_MEMORY_DIAGNOSTICS
SELF=$0
while [ -L "$SELF" ]; do
  DIR=$(CDPATH= cd -P -- "$(dirname -- "$SELF")" && pwd) || exit 1
  LINK=$(readlink "$SELF") || exit 1
  case "$LINK" in
    /*) SELF=$LINK ;;
    *) SELF=$DIR/$LINK ;;
  esac
done
DIR=$(CDPATH= cd -P -- "$(dirname -- "$SELF")" && pwd) || exit 1
# Name this payload's root so the Runtime can tell a non-default install from
# the everyday one and pick its Core port accordingly. Managed versions live at
# install/versions/version; lifecycle commands must address the install root.
PAYLOAD_ROOT=$(CDPATH= cd -P -- "$DIR/.." && pwd) || exit 1
PAYLOAD_PARENT=$(CDPATH= cd -P -- "$PAYLOAD_ROOT/.." && pwd) || exit 1
case "$PAYLOAD_PARENT" in
  */versions) ONE_MEMORY_INSTALL_ROOT=$(CDPATH= cd -P -- "$PAYLOAD_PARENT/.." && pwd) || exit 1 ;;
  *) ONE_MEMORY_INSTALL_ROOT=$PAYLOAD_ROOT ;;
esac
export ONE_MEMORY_INSTALL_ROOT
exec "$DIR/node" "$DIR/one-memory.cjs" "$@"
`;
}

function sha256Text(value: string): string {
  return createHash("sha256").update(value).digest("hex");
}

async function treeVersion(root: string): Promise<string> {
  return versionOf(await fileDigests(root));
}

async function fileDigests(root: string): Promise<Record<string, string>> {
  return digestRelatives(root, await treeFiles(root));
}

/** Hash `relatives` under `root` with a small worker pool.
 *
 * The resulting map is keyed in sorted order, so a manifest built from it is
 * byte-identical to the one the sequential walk used to produce. */
async function digestRelatives(
  root: string,
  relatives: string[],
): Promise<Record<string, string>> {
  const digests = new Map<string, string>();
  let cursor = 0;
  const worker = async () => {
    while (cursor < relatives.length) {
      const relative = relatives[cursor];
      cursor += 1;
      digests.set(relative, await sha256(path.join(root, relative)));
    }
  };
  await Promise.all(
    Array.from({ length: Math.min(DIGEST_CONCURRENCY, relatives.length) }, () =>
      worker(),
    ),
  );
  const files: Record<string, string> = {};
  for (const relative of [...digests.keys()].sort()) {
    const digest = digests.get(relative);
    if (digest !== undefined) files[relative] = digest;
  }
  return files;
}

async function treeFiles(root: string, current = root): Promise<string[]> {
  const entries = await readdir(current, { withFileTypes: true });
  entries.sort((left, right) => left.name.localeCompare(right.name));
  const result: string[] = [];
  for (const entry of entries) {
    // Finder metadata is not part of the packaged Runtime identity.
    if (entry.name === ".DS_Store") {
      if (!entry.isFile()) throw new Error("distribution_path_invalid");
      continue;
    }
    const target = path.join(current, entry.name);
    const relative = path.relative(root, target);
    if (entry.isSymbolicLink()) {
      const link = await readlink(target);
      const resolved = path.resolve(path.dirname(target), link);
      const relativeTarget = path.relative(root, resolved);
      if (relativeTarget.startsWith("..") || path.isAbsolute(relativeTarget)) {
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
