#!/usr/bin/env node

import { readFile, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { assertSemver, pythonPackageVersion } = require("./release-version.cjs");
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

export async function setReleaseVersion(
  version,
  repositoryRoot = root,
  buildNumber = undefined,
) {
  assertSemver(version, "requested release version");
  if (
    buildNumber !== undefined &&
    (!Number.isSafeInteger(buildNumber) || buildNumber < 0)
  ) {
    throw new Error(`invalid release build number: ${String(buildNumber)}`);
  }
  const packageDocuments = new Map();
  for (const relative of packageFiles) {
    const target = path.join(repositoryRoot, relative);
    const document = JSON.parse(await readFile(target, "utf8"));
    packageDocuments.set(relative, document);
  }

  const pyprojectRelative = "apps/core/pyproject.toml";
  const pythonVersion = pythonPackageVersion(version);
  const pyprojectPath = path.join(repositoryRoot, pyprojectRelative);
  const pyproject = await readFile(pyprojectPath, "utf8");
  const pyprojectPattern =
    /(^\[project\][\s\S]*?^version\s*=\s*")[^"]+("\s*$)/m;
  if (!pyprojectPattern.test(pyproject)) {
    throw new Error("apps/core/pyproject.toml project version missing");
  }
  const nextPyproject = pyproject.replace(
    pyprojectPattern,
    `$1${pythonVersion}$2`,
  );

  const lockRelative = "apps/core/uv.lock";
  const lockPath = path.join(repositoryRoot, lockRelative);
  const lock = await readFile(lockPath, "utf8");
  const lockPattern =
    /(\[\[package\]\]\s*\nname = "one-memory"\s*\nversion = ")[^"]+("\s*$)/gm;
  const matches = [...lock.matchAll(lockPattern)];
  if (matches.length !== 1) {
    throw new Error(
      "apps/core/uv.lock one-memory version must appear exactly once",
    );
  }
  const nextLock = lock.replace(lockPattern, `$1${pythonVersion}$2`);

  const rootPackage = packageDocuments.get("package.json");
  if (!rootPackage) throw new Error("root package manifest missing");
  const currentBuildNumber = rootPackage.release?.build_number;
  const nextBuildNumber = buildNumber ?? currentBuildNumber;
  if (!Number.isSafeInteger(nextBuildNumber) || nextBuildNumber < 0) {
    throw new Error("release build number is required");
  }
  rootPackage.release = {
    ...(rootPackage.release ?? {}),
    build_number: nextBuildNumber,
  };

  const changed = [];
  for (const relative of packageFiles) {
    const target = path.join(repositoryRoot, relative);
    const document = packageDocuments.get(relative);
    if (!document) throw new Error(`package manifest missing: ${relative}`);
    const currentVersion = document.version;
    document.version = version;
    if (
      currentVersion === version &&
      (relative !== "package.json" || currentBuildNumber === nextBuildNumber)
    ) {
      continue;
    }
    await writeFile(target, `${JSON.stringify(document, null, 2)}\n`);
    changed.push(relative);
  }
  if (nextPyproject !== pyproject) {
    await writeFile(pyprojectPath, nextPyproject);
    changed.push(pyprojectRelative);
  }
  if (nextLock !== lock) {
    await writeFile(lockPath, nextLock);
    changed.push(lockRelative);
  }
  if (
    currentBuildNumber !== nextBuildNumber &&
    !changed.includes("package.json")
  ) {
    // Kept as a defensive invariant if package handling changes later.
    rootPackage.release = {
      ...(rootPackage.release ?? {}),
      build_number: nextBuildNumber,
    };
    await writeFile(
      path.join(repositoryRoot, "package.json"),
      `${JSON.stringify(rootPackage, null, 2)}\n`,
    );
    changed.push("package.json");
  }
  return {
    version,
    python_version: pythonVersion,
    build_number: nextBuildNumber,
    changed,
  };
}

async function main() {
  const [version, ...arguments_] = process.argv.slice(2);
  let buildNumber;
  if (arguments_.length > 0) {
    if (arguments_.length !== 2 || arguments_[0] !== "--build-number") {
      throw new Error(
        "usage: set-release-version.mjs <SemVer> [--build-number <integer>]",
      );
    }
    buildNumber = Number(arguments_[1]);
  }
  if (version === undefined) {
    throw new Error(
      "usage: set-release-version.mjs <SemVer> [--build-number <integer>]",
    );
  }
  const receipt = await setReleaseVersion(version, root, buildNumber);
  process.stdout.write(`${JSON.stringify(receipt, null, 2)}\n`);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : error}\n`);
    process.exitCode = 1;
  });
}
