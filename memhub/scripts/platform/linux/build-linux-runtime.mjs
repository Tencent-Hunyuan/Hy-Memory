#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../..",
);
const require = createRequire(import.meta.url);
const {
  resolveBuildNumber,
  resolveBuildVersion,
} = require("../../release-version.cjs");

function fail(message) {
  process.stderr.write(`${message}\n`);
  process.exit(1);
}

function supportedArch() {
  switch (process.arch) {
    case "x64":
      return "x64";
    case "arm64":
      return "arm64";
    default:
      fail(`unsupported Linux architecture: ${process.arch}`);
  }
}

if (process.platform !== "linux") {
  fail(
    "build-linux-runtime.mjs must run on a Linux host; cross builds are not accepted",
  );
}

const arch = supportedArch();
const version = resolveBuildVersion(process.env, repoRoot);
const buildNumber = resolveBuildNumber(process.env, repoRoot);
const commitResult = spawnSync("git", ["rev-parse", "HEAD"], {
  cwd: repoRoot,
  encoding: "utf8",
});
const gitCommit = commitResult.stdout?.trim();
if (commitResult.status !== 0 || !/^[0-9a-f]{40}$/.test(gitCommit)) {
  fail("release git commit unavailable");
}
const releaseEnvironment = {
  ONE_MEMORY_VERSION: version,
  ONE_MEMORY_BUILD_NUMBER: String(buildNumber),
  ONE_MEMORY_COMMIT: gitCommit,
};
const output = path.resolve(
  process.argv[2] ?? path.join(repoRoot, "dist-runtime-linux", arch),
);

function run(command, args, env = {}) {
  const result = spawnSync(command, args, {
    cwd: repoRoot,
    stdio: "inherit",
    env: { ...process.env, ...env },
  });
  if (result.status !== 0) {
    fail(`${command} ${args.join(" ")} failed`);
  }
}

run(
  "pnpm",
  ["--filter", "@one-memory/runtime-control", "build"],
  releaseEnvironment,
);
run(
  "pnpm",
  ["--filter", "@one-memory/host-bridge", "build"],
  releaseEnvironment,
);
run("pnpm", ["--filter", "@one-memory/web", "build"], releaseEnvironment);
run("pnpm", ["--filter", "@one-memory/cli", "build"], releaseEnvironment);
run("uv", ["sync", "--directory", "apps/core", "--all-groups", "--locked"]);
run("pnpm", ["run", "sbom"]);
run("pnpm", ["fetch:embedding-model"]);
run(
  "uv",
  [
    "run",
    "--directory",
    "apps/core",
    "pyinstaller",
    "--noconfirm",
    "one-memory.spec",
  ],
  { ...releaseEnvironment, ONE_MEMORY_CORE_LAYOUT: "folder" },
);
run(
  path.join("apps", "core", "dist", "one-memory-runtime", "one-memory"),
  ["--bm25-assets-check"],
  releaseEnvironment,
);
run(
  "pnpm",
  [
    "--filter",
    "@one-memory/cli",
    "exec",
    "node",
    "../../scripts/build_runtime_distribution.cjs",
    output,
    "linux",
    arch,
    version,
    String(buildNumber),
  ],
  releaseEnvironment,
);

const distributionManifestPath = path.join(output, "manifest.json");
if (!existsSync(distributionManifestPath)) {
  fail("distribution manifest missing");
}

const distributionManifest = JSON.parse(
  await import("node:fs/promises").then(({ readFile }) =>
    readFile(distributionManifestPath, "utf8"),
  ),
);
const componentHashes = {};
for (const [component, prefix] of Object.entries({
  core: "core/",
  runtime: "runtime/",
  "host-bridge": "host-bridge/",
  web: "web/",
  cli: "bin/",
})) {
  const files = Object.fromEntries(
    Object.entries(distributionManifest.files).filter(([file]) =>
      file.startsWith(prefix),
    ),
  );
  componentHashes[component] = createHash("sha256")
    .update(JSON.stringify(files))
    .digest("hex");
}

const buildReceipt = {
  schema_version: 1,
  product: "one-memory-linux-runtime",
  platform: "linux",
  arch,
  version,
  build_number: buildNumber,
  git_commit: gitCommit,
  distribution_manifest: distributionManifest,
  component_hashes: componentHashes,
};

writeFileSync(
  `${output}.build-receipt.json`,
  `${JSON.stringify(buildReceipt, null, 2)}\n`,
);
process.stdout.write(`${JSON.stringify(buildReceipt, null, 2)}\n`);
