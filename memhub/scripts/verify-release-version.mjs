#!/usr/bin/env node

import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const {
  assertSemver,
  isPrerelease,
  pythonPackageVersion,
  readReleaseBuildNumber,
  readReleaseVersion,
  resolveBuildNumber,
  resolveBuildVersion,
} = require("./release-version.cjs");

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const packageFiles = [
  "package.json",
  "apps/cli/package.json",
  "apps/desktop/package.json",
  "apps/site/package.json",
  "apps/web/package.json",
  "packages/contracts/package.json",
  "packages/host-bridge/package.json",
  "packages/i18n/package.json",
  "packages/operations/package.json",
  "packages/runtime-control/package.json",
];

function parseArgs(arguments_) {
  const options = { channel: "development" };
  for (let index = 0; index < arguments_.length; index += 1) {
    const flag = arguments_[index];
    if (!["--channel", "--tag", "--version", "--build-number"].includes(flag)) {
      throw new Error(`unknown argument: ${flag}`);
    }
    const value = arguments_[++index];
    if (value === undefined || value === "")
      throw new Error(`missing value for ${flag}`);
    options[flag.slice(2).replace("-", "_")] = value;
  }
  if (!["development", "stable", "prerelease"].includes(options.channel)) {
    throw new Error(`invalid release channel: ${options.channel}`);
  }
  return options;
}

async function packageVersions() {
  const versions = {};
  for (const relative of packageFiles) {
    const document = JSON.parse(
      await readFile(path.join(root, relative), "utf8"),
    );
    versions[relative] = assertSemver(document.version, `${relative} version`);
  }
  const pyproject = await readFile(
    path.join(root, "apps/core/pyproject.toml"),
    "utf8",
  );
  const match = /^version\s*=\s*"([^"]+)"\s*$/m.exec(pyproject);
  if (!match)
    throw new Error("apps/core/pyproject.toml project version missing");
  versions["apps/core/pyproject.toml"] = match[1];
  const lock = await readFile(path.join(root, "apps/core/uv.lock"), "utf8");
  const lockMatch =
    /\[\[package\]\]\s*\nname = "one-memory"\s*\nversion = "([^"]+)"/m.exec(
      lock,
    );
  if (!lockMatch)
    throw new Error("apps/core/uv.lock one-memory version missing");
  versions["apps/core/uv.lock"] = lockMatch[1];
  return versions;
}

export async function verifyReleaseVersion(
  arguments_,
  environment = process.env,
) {
  const options = parseArgs(arguments_);
  const version = readReleaseVersion(root);
  const canonicalBuildNumber = readReleaseBuildNumber(root);
  const suppliedVersion =
    options.version ?? environment.ONE_MEMORY_VERSION?.trim();
  if (suppliedVersion !== undefined && suppliedVersion !== "") {
    assertSemver(suppliedVersion, "requested release version");
    if (suppliedVersion !== version) {
      throw new Error(
        `requested version ${suppliedVersion} does not match root ${version}`,
      );
    }
  }
  resolveBuildVersion(
    suppliedVersion === undefined || suppliedVersion === ""
      ? environment
      : { ...environment, ONE_MEMORY_VERSION: suppliedVersion },
    root,
  );
  const versions = await packageVersions();
  const pythonVersion = pythonPackageVersion(version);
  const mismatches = Object.entries(versions).filter(([file, value]) =>
    file.startsWith("apps/core/") ? value !== pythonVersion : value !== version,
  );
  if (mismatches.length > 0) {
    throw new Error(
      `release version mismatch: ${mismatches.map(([file, value]) => `${file}=${value}`).join(", ")}; root=${version}`,
    );
  }

  const tag = options.tag ?? environment.GITHUB_REF_NAME?.trim();
  const buildNumberRaw =
    options.build_number ?? environment.ONE_MEMORY_BUILD_NUMBER?.trim();
  const buildNumber =
    buildNumberRaw === undefined || buildNumberRaw === ""
      ? canonicalBuildNumber
      : Number(buildNumberRaw);
  if (!Number.isSafeInteger(buildNumber) || buildNumber < 0) {
    throw new Error(`invalid build number: ${buildNumberRaw}`);
  }
  resolveBuildNumber(
    { ...environment, ONE_MEMORY_BUILD_NUMBER: String(buildNumber) },
    root,
  );

  if (options.channel !== "development") {
    if (version === "0.0.0") throw new Error("0.0.0 cannot be published");
    if (tag !== `v${version}`) {
      throw new Error(
        `release tag mismatch: expected v${version}, got ${tag ?? "<missing>"}`,
      );
    }
  }
  if (options.channel === "stable" && isPrerelease(version)) {
    throw new Error(
      `stable channel cannot publish prerelease version ${version}`,
    );
  }
  if (options.channel === "prerelease" && !isPrerelease(version)) {
    throw new Error(
      `prerelease channel requires a SemVer prerelease: ${version}`,
    );
  }

  return {
    schema_version: 1,
    product: "memhub-release-version",
    channel: options.channel,
    version,
    python_version: pythonVersion,
    build_number: buildNumber,
    tag: tag ?? null,
    files: versions,
  };
}

async function main() {
  const receipt = await verifyReleaseVersion(process.argv.slice(2));
  process.stdout.write(`${JSON.stringify(receipt, null, 2)}\n`);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : error}\n`);
    process.exitCode = 1;
  });
}
