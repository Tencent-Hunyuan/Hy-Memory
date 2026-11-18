#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { createReadStream, writeFileSync } from "node:fs";
import {
  copyFile,
  cp,
  lstat,
  mkdir,
  readdir,
  readFile,
  readlink,
  realpath,
  rm,
  stat,
  writeFile,
} from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { sameOriginRuntimeChecks } from "./service-lifecycle.mjs";

const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../..",
);
const require = createRequire(import.meta.url);
const {
  resolveBuildNumber,
  resolveBuildVersion,
} = require("../../release-version.cjs");

export async function buildMacosRelease(options) {
  if (process.platform !== "darwin") {
    throw new Error("macos_release_build_requires_darwin");
  }
  if (
    !["arm64", "x64"].includes(options.arch) ||
    process.arch !== options.arch
  ) {
    throw new Error("macos_release_build_requires_native_arch");
  }
  if (options.mode !== "internal" && options.mode !== "public") {
    throw new Error("macos_release_signing_mode_invalid");
  }
  const output = path.resolve(options.output);
  await assertAbsent(output, "macos_release_output_exists");
  await mkdir(output, { recursive: true, mode: 0o700 });

  const rootPackage = JSON.parse(
    await readFile(path.join(root, "package.json"), "utf8"),
  );
  const version = resolveBuildVersion(process.env, root);
  const buildNumber = resolveBuildNumber(process.env, root);
  const minimumSystemVersion = rootPackage.release?.macos_min_version;
  const appUpdate = rootPackage.release?.app_update;
  if (minimumSystemVersion !== "13.0" || appUpdate !== "manual") {
    throw new Error("macos_release_contract_invalid");
  }
  const publicBundleId = process.env.MEMHUB_PUBLIC_BUNDLE_ID?.trim();
  if (
    options.mode === "public" &&
    (!publicBundleId ||
      !/^[A-Za-z0-9][A-Za-z0-9.-]+[A-Za-z0-9]$/.test(publicBundleId) ||
      !publicBundleId.includes("."))
  ) {
    throw new Error("macos_public_bundle_id_required");
  }
  const signingIdentity = process.env.CSC_NAME?.trim();
  if (options.mode === "public" && !signingIdentity) {
    throw new Error("macos_developer_id_identity_required");
  }
  const electronBuilderIdentity = signingIdentity?.replace(
    /^Developer ID Application:\s*/,
    "",
  );
  const keychain = process.env.CSC_KEYCHAIN?.trim();
  if (options.mode === "public" && !keychain) {
    throw new Error("macos_signing_keychain_required");
  }

  const environment = {
    ...process.env,
    ONE_MEMORY_VERSION: version,
    ONE_MEMORY_BUILD_NUMBER: String(buildNumber),
    ONE_MEMORY_COMMIT: gitCommit(),
    MACOSX_DEPLOYMENT_TARGET: minimumSystemVersion,
    MEMHUB_MAC_SIGNING_MODE: options.mode,
  };
  const runtime = path.join(output, `memhub-runtime-darwin-${options.arch}`);
  const electronOutput = path.join(output, "electron");
  const app = path.join(output, "MemHub.app");
  const evidence = path.join(output, "evidence");
  await mkdir(evidence, { recursive: true, mode: 0o700 });

  run(
    "runtime-control-build",
    "pnpm",
    ["--filter", "@one-memory/runtime-control", "build"],
    environment,
    evidence,
  );
  run(
    "host-bridge-build",
    "pnpm",
    ["--filter", "@one-memory/host-bridge", "build"],
    environment,
    evidence,
  );
  run(
    "web-build",
    "pnpm",
    ["--filter", "@one-memory/web", "build"],
    environment,
    evidence,
  );
  run(
    "cli-build",
    "pnpm",
    ["--filter", "@one-memory/cli", "build"],
    environment,
    evidence,
  );
  run(
    "core-sync",
    "uv",
    ["sync", "--directory", "apps/core", "--all-groups", "--locked"],
    environment,
    evidence,
    10 * 60_000,
  );
  run("sbom", "pnpm", ["run", "sbom"], environment, evidence, 10 * 60_000);
  run(
    "embedding-assets",
    "pnpm",
    ["fetch:embedding-model"],
    environment,
    evidence,
    10 * 60_000,
  );
  run(
    "core-build",
    "uv",
    [
      "run",
      "--directory",
      "apps/core",
      "pyinstaller",
      "--noconfirm",
      "one-memory.spec",
    ],
    { ...environment, ONE_MEMORY_CORE_LAYOUT: "folder" },
    evidence,
    20 * 60_000,
  );
  run(
    "bm25-assets-check",
    path.join("apps", "core", "dist", "one-memory-runtime", "one-memory"),
    ["--bm25-assets-check"],
    environment,
    evidence,
    2 * 60_000,
  );
  run(
    "runtime-distribution",
    process.execPath,
    [
      "scripts/build_runtime_distribution.cjs",
      runtime,
      "darwin",
      options.arch,
      version,
      String(buildNumber),
    ],
    environment,
    evidence,
    10 * 60_000,
  );
  run(
    "desktop-build",
    "pnpm",
    ["--filter", "@one-memory/desktop", "build"],
    environment,
    evidence,
    10 * 60_000,
  );

  const builderArguments = [
    "--filter",
    "@one-memory/desktop",
    "exec",
    "electron-builder",
    "--mac",
    "dir",
    `--${options.arch}`,
    `-c.directories.output=${electronOutput}`,
    `-c.extraMetadata.version=${version}`,
    `-c.buildVersion=${buildNumber}`,
    `-c.mac.minimumSystemVersion=${minimumSystemVersion}`,
    ...(options.mode === "internal"
      ? ["-c.mac.identity=null"]
      : [
          `-c.appId=${publicBundleId}`,
          `-c.mac.identity=${electronBuilderIdentity}`,
          "-c.forceCodeSigning=true",
        ]),
  ];
  run(
    "desktop-package",
    "pnpm",
    builderArguments,
    environment,
    evidence,
    20 * 60_000,
  );
  const packagedApp = await findSingleApp(electronOutput);
  run(
    "copy-app",
    "/usr/bin/ditto",
    [packagedApp, app],
    environment,
    evidence,
    5 * 60_000,
  );

  const appIdentity = readAppIdentity(app);
  if (
    appIdentity.version !== version ||
    appIdentity.buildNumber !== String(buildNumber) ||
    canonicalMacosVersion(appIdentity.minimumSystemVersion) !==
      canonicalMacosVersion(minimumSystemVersion) ||
    appIdentity.bundleId !==
      (options.mode === "public" ? publicBundleId : "com.tencent.onememory")
  ) {
    throw new Error("macos_release_app_identity_mismatch");
  }
  assertMachOArchitecture(
    path.join(app, "Contents", "MacOS", "MemHub"),
    options.arch,
  );
  await synchronizeSignedAppRuntime({ app, runtime });
  await signStandaloneRuntimeMachOs({
    runtime,
    mode: options.mode,
    identity: signingIdentity,
    keychain,
    environment,
    evidence,
  });
  await rewriteDistributionManifest(runtime);
  await synchronizeRuntimeBackToApp({ app, runtime });
  resignPackagedApp({
    app,
    mode: options.mode,
    identity: signingIdentity,
    keychain,
    environment,
    evidence,
  });
  assertMachOArchitecture(path.join(runtime, "bin", "node"), options.arch);
  assertMachOArchitecture(
    path.join(runtime, "core", "one-memory"),
    options.arch,
  );
  const appTeamId = codesignTeamIdentifier(app);
  const runtimeTeamIds = await verifyRuntimeMachOSignatures(
    runtime,
    environment,
    evidence,
  );
  if (
    options.mode === "public" &&
    (!appTeamId ||
      runtimeTeamIds.length === 0 ||
      runtimeTeamIds.some((teamId) => teamId !== appTeamId))
  ) {
    throw new Error("macos_release_team_id_mismatch");
  }
  const sameOrigin = await sameOriginRuntimeChecks(runtime, app);
  if (sameOrigin.some((check) => check.result !== "pass")) {
    throw new Error("macos_app_runtime_origin_mismatch");
  }
  run(
    "codesign-verify",
    "/usr/bin/codesign",
    ["--verify", "--deep", "--strict", app],
    environment,
    evidence,
  );

  const receipt = {
    schema_version: 1,
    product: "memhub-macos-native-build",
    version,
    build_number: buildNumber,
    platform: "darwin",
    arch: options.arch,
    minimum_system_version: minimumSystemVersion,
    app_update: appUpdate,
    signing_mode: options.mode,
    bundle_id:
      options.mode === "public" ? publicBundleId : "com.tencent.onememory",
    team_id: appTeamId,
    runtime_team_ids: runtimeTeamIds,
    runtime,
    app,
    same_origin_checks: sameOrigin,
    result: "passed",
  };
  await writeFile(
    path.join(output, "macos-build-receipt.json"),
    `${JSON.stringify(receipt, null, 2)}\n`,
  );
  return receipt;
}

async function synchronizeSignedAppRuntime(options) {
  const resources = path.join(options.app, "Contents", "Resources");
  for (const component of ["core", "runtime", "host-bridge"]) {
    const source = path.join(resources, component);
    const target = path.join(options.runtime, component);
    await rm(target, { recursive: true, force: true });
    await cp(source, target, {
      recursive: true,
      dereference: false,
      verbatimSymlinks: true,
    });
  }
  await copyFile(
    path.join(resources, "cli", "one-memory.cjs"),
    path.join(options.runtime, "bin", "one-memory.cjs"),
  );
}

async function synchronizeRuntimeBackToApp(options) {
  const resources = path.join(options.app, "Contents", "Resources");
  for (const component of ["core", "runtime", "host-bridge"]) {
    const source = path.join(options.runtime, component);
    const target = path.join(resources, component);
    await rm(target, { recursive: true, force: true });
    await cp(source, target, {
      recursive: true,
      dereference: false,
      verbatimSymlinks: true,
    });
  }
  await copyFile(
    path.join(options.runtime, "bin", "one-memory.cjs"),
    path.join(resources, "cli", "one-memory.cjs"),
  );
}

function resignPackagedApp(options) {
  const arguments_ = ["--force"];
  if (options.mode === "public") {
    arguments_.push(
      "--options",
      "runtime",
      "--timestamp",
      "--entitlements",
      path.join(root, "apps/desktop/entitlements/entitlements.mac.plist"),
      "--keychain",
      options.keychain,
    );
  }
  arguments_.push(
    "--sign",
    options.mode === "public" ? options.identity : "-",
    options.app,
  );
  run(
    "resign-packaged-app",
    "/usr/bin/codesign",
    arguments_,
    options.environment,
    options.evidence,
  );
}

async function signStandaloneRuntimeMachOs(options) {
  let signed = 0;
  for (const relative of await treeFiles(options.runtime)) {
    const target = path.join(options.runtime, relative);
    const info = await lstat(target);
    if (!info.isFile() || info.isSymbolicLink()) continue;
    const kind = spawnSync("/usr/bin/file", ["-b", target], {
      encoding: "utf8",
    });
    if (kind.status !== 0 || !kind.stdout.includes("Mach-O")) continue;
    const arguments_ = ["--force"];
    if (options.mode === "public") {
      arguments_.push(
        "--options",
        "runtime",
        "--timestamp",
        "--entitlements",
        path.join(
          root,
          "apps/desktop/entitlements/entitlements.mac.inherit.plist",
        ),
        "--keychain",
        options.keychain,
      );
    }
    arguments_.push(
      "--sign",
      options.mode === "public" ? options.identity : "-",
      target,
    );
    run(
      `sign-runtime-macho-${signed}`,
      "/usr/bin/codesign",
      arguments_,
      options.environment,
      options.evidence,
    );
    signed += 1;
  }
  if (signed === 0) throw new Error("macos_release_no_runtime_macho_signed");
  let signedBundles = 0;
  for (const bundle of await treeCodeBundles(options.runtime)) {
    const arguments_ = ["--force"];
    if (options.mode === "public") {
      arguments_.push(
        "--options",
        "runtime",
        "--timestamp",
        "--entitlements",
        path.join(
          root,
          "apps/desktop/entitlements/entitlements.mac.inherit.plist",
        ),
        "--keychain",
        options.keychain,
      );
    }
    arguments_.push(
      "--sign",
      options.mode === "public" ? options.identity : "-",
      bundle,
    );
    run(
      `sign-runtime-bundle-${signedBundles}`,
      "/usr/bin/codesign",
      arguments_,
      options.environment,
      options.evidence,
    );
    signedBundles += 1;
  }
}

async function rewriteDistributionManifest(runtime) {
  const manifestPath = path.join(runtime, "manifest.json");
  const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
  const actual = (await treeFiles(runtime)).filter(
    (relative) => relative !== "manifest.json",
  );
  const declared = Object.keys(manifest.files ?? {}).sort();
  const actualSet = new Set(actual);
  const declaredSet = new Set(declared);
  const missing = declared.filter((relative) => !actualSet.has(relative));
  const added = actual.filter((relative) => !declaredSet.has(relative));
  if (
    missing.length > 0 ||
    added.some((relative) => !/(?:^|\/)_CodeSignature\//.test(relative))
  ) {
    throw new Error("macos_release_distribution_file_set_changed");
  }
  const files = {};
  for (const relative of actual) {
    files[relative] = await sha256File(path.join(runtime, relative));
  }
  manifest.files = files;
  await writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, {
    mode: 0o600,
  });
}

async function verifyRuntimeMachOSignatures(runtime, environment, evidence) {
  let verified = 0;
  const teamIds = [];
  for (const relative of await treeFiles(runtime)) {
    if (relative === "manifest.json") continue;
    const file = path.join(runtime, relative);
    const info = await lstat(file);
    if (!info.isFile() || info.isSymbolicLink()) continue;
    const kind = spawnSync("/usr/bin/file", ["-b", file], {
      encoding: "utf8",
    });
    if (kind.status !== 0 || !kind.stdout.includes("Mach-O")) continue;
    run(
      `verify-runtime-macho-${verified}`,
      "/usr/bin/codesign",
      ["--verify", "--strict", file],
      environment,
      evidence,
    );
    const teamId = codesignTeamIdentifier(file);
    if (teamId) teamIds.push(teamId);
    verified += 1;
  }
  if (verified === 0) throw new Error("macos_release_no_macho_verified");
  return teamIds;
}

async function treeFiles(rootDirectory, current = rootDirectory) {
  const entries = await readdir(current, { withFileTypes: true });
  entries.sort((left, right) => left.name.localeCompare(right.name));
  const files = [];
  for (const entry of entries) {
    const target = path.join(current, entry.name);
    const relative = path.relative(rootDirectory, target);
    if (entry.isDirectory())
      files.push(...(await treeFiles(rootDirectory, target)));
    else if (entry.isFile() || entry.isSymbolicLink()) files.push(relative);
    else throw new Error("macos_release_distribution_entry_invalid");
  }
  return files.sort();
}

async function treeCodeBundles(rootDirectory, current = rootDirectory) {
  const entries = await readdir(current, { withFileTypes: true });
  entries.sort((left, right) => left.name.localeCompare(right.name));
  const bundles = [];
  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    const target = path.join(current, entry.name);
    bundles.push(...(await treeCodeBundles(rootDirectory, target)));
    if (/\.(?:app|framework|xpc)$/.test(entry.name)) bundles.push(target);
  }
  return bundles;
}

async function sha256File(file) {
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

function run(
  name,
  command,
  arguments_,
  environment,
  evidence,
  timeout = 120_000,
) {
  const result = spawnSync(command, arguments_, {
    cwd: root,
    env: environment,
    encoding: "utf8",
    timeout,
  });
  const document = {
    command,
    args: arguments_,
    exit_code: result.status,
    signal: result.signal,
    stdout: result.stdout,
    stderr: result.stderr,
    error: result.error?.message ?? null,
  };
  writeFileSync(
    path.join(evidence, `${name}.json`),
    `${JSON.stringify(document, null, 2)}\n`,
  );
  if (result.status !== 0) {
    throw new Error(
      `${name} failed: ${result.error?.message || result.stderr || result.stdout || "unknown error"}`,
    );
  }
  return document;
}

function gitCommit() {
  const result = spawnSync("git", ["rev-parse", "HEAD"], {
    cwd: root,
    encoding: "utf8",
  });
  if (result.status !== 0 || !/^[0-9a-f]{40}\n?$/.test(result.stdout)) {
    throw new Error("macos_release_commit_unavailable");
  }
  return result.stdout.trim();
}

function assertMachOArchitecture(file, arch) {
  const result = spawnSync("/usr/bin/lipo", ["-archs", file], {
    encoding: "utf8",
  });
  const architectures = result.stdout.trim().split(/\s+/).filter(Boolean);
  if (result.status !== 0 || !architectures.includes(arch)) {
    throw new Error(`macos_release_arch_mismatch:${file}`);
  }
}

function readAppIdentity(app) {
  const plist = path.join(app, "Contents", "Info.plist");
  const read = (key) => {
    const result = spawnSync(
      "/usr/libexec/PlistBuddy",
      ["-c", `Print :${key}`, plist],
      { encoding: "utf8" },
    );
    if (result.status !== 0)
      throw new Error(`macos_release_plist_missing:${key}`);
    return result.stdout.trim();
  };
  return {
    bundleId: read("CFBundleIdentifier"),
    version: read("CFBundleShortVersionString"),
    buildNumber: read("CFBundleVersion"),
    minimumSystemVersion: read("LSMinimumSystemVersion"),
  };
}

function canonicalMacosVersion(value) {
  const parts = String(value).split(".");
  if (parts.some((part) => !/^\d+$/.test(part))) {
    throw new Error("macos_release_minimum_system_version_invalid");
  }
  while (parts.length > 1 && parts.at(-1) === "0") parts.pop();
  return parts.map((part) => String(Number(part))).join(".");
}

function codesignTeamIdentifier(target) {
  const result = spawnSync("/usr/bin/codesign", ["-d", "--verbose=4", target], {
    encoding: "utf8",
  });
  const text = `${result.stdout}${result.stderr}`;
  const match = /^TeamIdentifier=(.+)$/m.exec(text);
  const value = match?.[1]?.trim();
  return value && value !== "not set" ? value : null;
}

async function findSingleApp(directory) {
  const apps = [];
  async function visit(current) {
    for (const entry of await readdir(current, { withFileTypes: true })) {
      const target = path.join(current, entry.name);
      if (entry.isDirectory() && entry.name.endsWith(".app")) apps.push(target);
      else if (entry.isDirectory()) await visit(target);
    }
  }
  await visit(directory);
  if (apps.length !== 1) throw new Error("macos_release_app_ambiguous");
  return realpath(apps[0]);
}

async function exists(target) {
  return stat(target).then(
    () => true,
    () => false,
  );
}

async function assertAbsent(target, code) {
  if (await exists(target)) throw new Error(code);
}

function parseArguments(arguments_) {
  const options = { mode: "internal" };
  for (let index = 0; index < arguments_.length; index += 1) {
    const flag = arguments_[index];
    const value = arguments_[++index];
    if (!value) throw new Error(`missing value for ${flag}`);
    if (flag === "--output") options.output = value;
    else if (flag === "--arch") options.arch = value;
    else if (flag === "--mode") options.mode = value;
    else throw new Error(`invalid argument: ${flag}`);
  }
  if (!options.output || !options.arch) {
    throw new Error(
      "usage: build-macos-release.mjs --output <dir> --arch <arm64|x64> [--mode <internal|public>]",
    );
  }
  return options;
}

async function main() {
  const receipt = await buildMacosRelease(
    parseArguments(process.argv.slice(2)),
  );
  process.stdout.write(`${JSON.stringify(receipt, null, 2)}\n`);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : error}\n`);
    process.exitCode = 1;
  });
}
