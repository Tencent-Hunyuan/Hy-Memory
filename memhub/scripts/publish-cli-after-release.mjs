#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import { readFile, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { verifyPublishedRelease } from "./verify-published-release.mjs";
import { verifyReleaseVersion } from "./verify-release-version.mjs";

const repositoryRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);

export async function publishCliAfterRelease(options, dependencies = {}) {
  const root = path.resolve(options.root ?? repositoryRoot);
  const environment = options.environment ?? process.env;
  const verifyVersion = dependencies.verifyVersion ?? verifyReleaseVersion;
  const verifyRelease = dependencies.verifyRelease ?? verifyPublishedRelease;
  const runCommand = dependencies.runCommand ?? defaultRunCommand;
  const readFileImpl = dependencies.readFileImpl ?? readFile;
  const statImpl = dependencies.statImpl ?? stat;
  const buildEnvironment = {
    ...environment,
    MEMHUB_NPM_VERSION: options.version,
    MEMHUB_DEFAULT_RUNTIME_VERSION: options.runtimeVersion,
    ONE_MEMORY_BUILD_NUMBER: String(options.buildNumber),
    ONE_MEMORY_COMMIT: options.gitCommit,
  };

  const versionReceipt = await verifyVersion(
    [
      "--version",
      options.runtimeVersion,
      "--channel",
      options.channel,
      "--tag",
      options.tag,
      "--build-number",
      String(options.buildNumber),
    ],
    environment,
  );
  const releaseReceipt = await verifyRelease({
    manifestUrl: options.manifestUrl,
    version: options.runtimeVersion,
    gitCommit: options.gitCommit,
    channel: options.channel,
    platform: options.platform,
    arch: options.arch,
    allowedHosts: options.allowedHosts,
  });

  runCommand("pnpm", ["--filter", "@one-memory/cli", "pack:publish"], {
    cwd: root,
    env: buildEnvironment,
  });
  const publishManifestPath = path.join(
    root,
    "apps",
    "cli",
    "dist-publish",
    "package.json",
  );
  const publishManifest = JSON.parse(
    await readFileImpl(publishManifestPath, "utf8"),
  );
  if (
    typeof publishManifest.name !== "string" ||
    publishManifest.name.length === 0 ||
    publishManifest.version !== options.version ||
    publishManifest.private === true
  ) {
    throw new Error("npm_publish_manifest_invalid");
  }
  const expectedPackage = environment.MEMHUB_NPM_PACKAGE?.trim();
  if (expectedPackage && publishManifest.name !== expectedPackage) {
    throw new Error("npm_publish_package_mismatch");
  }
  const tarball = path.join(
    root,
    "apps",
    "cli",
    "dist-cli",
    npmTarballName(publishManifest.name, publishManifest.version),
  );
  const tarballInfo = await statImpl(tarball).catch(() => null);
  if (!tarballInfo?.isFile() || tarballInfo.size <= 0) {
    throw new Error("npm_publish_tarball_missing");
  }

  const publishArguments = [
    "publish",
    tarball,
    "--registry",
    trustedRegistry(options.registry),
    "--tag",
    npmTag(options.npmTag),
  ];
  if (options.access)
    publishArguments.push("--access", npmAccess(options.access));
  if (options.provenance) publishArguments.push("--provenance");
  if (options.dryRun) publishArguments.push("--dry-run");
  runCommand("npm", publishArguments, { cwd: root, env: buildEnvironment });

  return {
    schema_version: 1,
    product: "memhub-cli-publication",
    version: options.version,
    runtime_version: releaseReceipt.version,
    build_number: versionReceipt.build_number,
    tag: versionReceipt.tag,
    release: releaseReceipt,
    npm_package: publishManifest.name,
    npm_registry: trustedRegistry(options.registry),
    npm_tag: npmTag(options.npmTag),
    npm_access: options.access ? npmAccess(options.access) : null,
    provenance: Boolean(options.provenance),
    tarball,
    tarball_bytes: tarballInfo.size,
    dry_run: Boolean(options.dryRun),
  };
}

function defaultRunCommand(command, arguments_, options) {
  const result = spawnSync(command, arguments_, {
    cwd: options.cwd,
    env: options.env,
    stdio: "inherit",
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(`release_command_failed:${command}:${result.status}`);
  }
}

function npmTarballName(name, version) {
  const normalizedName = name.replace(/^@/, "").replaceAll("/", "-");
  if (!/^[0-9A-Za-z._-]+$/.test(normalizedName)) {
    throw new Error("npm_publish_manifest_invalid");
  }
  return `${normalizedName}-${version}.tgz`;
}

function trustedRegistry(raw) {
  let url;
  try {
    url = new URL(raw);
  } catch {
    throw new Error("npm_registry_invalid");
  }
  if (
    url.protocol !== "https:" ||
    url.username ||
    url.password ||
    url.search ||
    url.hash
  ) {
    throw new Error("npm_registry_invalid");
  }
  return url.href;
}

function npmTag(value) {
  if (typeof value !== "string" || !/^[a-z][a-z0-9._-]{0,63}$/.test(value)) {
    throw new Error("npm_tag_invalid");
  }
  return value;
}

function npmAccess(value) {
  if (value !== "public" && value !== "restricted") {
    throw new Error("npm_access_invalid");
  }
  return value;
}

function parseArgs(arguments_) {
  const options = {
    allowedHosts: [],
    dryRun: false,
    provenance: false,
  };
  for (let index = 0; index < arguments_.length; index += 1) {
    const flag = arguments_[index];
    if (flag === "--dry-run") {
      options.dryRun = true;
      continue;
    }
    if (flag === "--provenance") {
      options.provenance = true;
      continue;
    }
    const value = arguments_[++index];
    if (!value) throw new Error(`missing value for ${flag}`);
    if (flag === "--manifest-url" && options.manifestUrl === undefined)
      options.manifestUrl = value;
    else if (flag === "--version" && options.version === undefined)
      options.version = value;
    else if (
      flag === "--runtime-version" &&
      options.runtimeVersion === undefined
    )
      options.runtimeVersion = value;
    else if (flag === "--git-commit" && options.gitCommit === undefined)
      options.gitCommit = value;
    else if (flag === "--channel" && options.channel === undefined)
      options.channel = value;
    else if (flag === "--tag" && options.tag === undefined) options.tag = value;
    else if (flag === "--build-number" && options.buildNumber === undefined)
      options.buildNumber = Number(value);
    else if (flag === "--platform" && options.platform === undefined)
      options.platform = value;
    else if (flag === "--arch" && options.arch === undefined)
      options.arch = value;
    else if (flag === "--allowed-host") options.allowedHosts.push(value);
    else if (flag === "--registry" && options.registry === undefined)
      options.registry = value;
    else if (flag === "--npm-tag" && options.npmTag === undefined)
      options.npmTag = value;
    else if (flag === "--access" && options.access === undefined)
      options.access = value;
    else throw new Error(`invalid argument: ${flag}`);
  }
  if (
    !options.manifestUrl ||
    !options.version ||
    !options.runtimeVersion ||
    !options.gitCommit ||
    !options.channel ||
    !options.tag ||
    !Number.isSafeInteger(options.buildNumber) ||
    options.buildNumber < 0 ||
    !options.platform ||
    !options.arch ||
    !options.registry ||
    !options.npmTag
  ) {
    throw new Error(
      "usage: publish-cli-after-release.mjs --manifest-url <https-url> --version <CLI SemVer> --runtime-version <Runtime SemVer> --git-commit <sha> --channel <stable|prerelease> --tag <vSemVer> --build-number <integer> --platform <darwin|linux> --arch <x64|arm64> --registry <https-url> --npm-tag <tag> [--access <public|restricted>] [--provenance] [--allowed-host <hostname>] [--dry-run]",
    );
  }
  return options;
}

async function main() {
  const receipt = await publishCliAfterRelease(
    parseArgs(process.argv.slice(2)),
  );
  process.stdout.write(`${JSON.stringify(receipt, null, 2)}\n`);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : error}\n`);
    process.exitCode = 1;
  });
}
