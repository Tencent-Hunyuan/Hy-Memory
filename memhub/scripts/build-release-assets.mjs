#!/usr/bin/env node

import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import {
  copyFile,
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
import * as tar from "tar";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(import.meta.url);
const { readReleaseContract } = require("./release-contract.cjs");
const releaseContract = readReleaseContract(root);
const SEMVER =
  /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/;
const SHA256 = /^[a-f0-9]{64}$/;
const COMMIT = /^[0-9a-f]{40}$/;

export async function buildReleaseAssets(options) {
  const output = path.resolve(options.output);
  const version = options.version;
  const channel = options.channel;
  const baseUrl = assetBaseUrl(options.assetBaseUrl);
  if (!SEMVER.test(version)) throw new Error("release_version_invalid");
  if (channel !== "stable" && channel !== "prerelease") {
    throw new Error("release_channel_invalid");
  }
  if (channel === "stable" && version.includes("-")) {
    throw new Error("release_channel_version_mismatch");
  }
  if (channel === "prerelease" && !version.includes("-")) {
    throw new Error("release_channel_version_mismatch");
  }
  if (
    !Array.isArray(options.distributions) ||
    options.distributions.length === 0
  ) {
    throw new Error("release_distribution_required");
  }
  const desktops = options.desktops ?? [];
  if (!Array.isArray(desktops)) throw new Error("release_desktop_invalid");

  const stage = `${output}.stage`;
  await rm(stage, { recursive: true, force: true });
  await mkdir(stage, { recursive: true, mode: 0o700 });
  try {
    const artifacts = [];
    const checksums = [];
    const identities = new Set();
    let buildNumber = null;
    let gitCommit = null;
    for (const sourceValue of options.distributions) {
      const source = await realpath(sourceValue);
      const sourceInfo = await lstat(source);
      if (!sourceInfo.isDirectory() || sourceInfo.isSymbolicLink()) {
        throw new Error("release_distribution_invalid");
      }
      const manifest = await verifyDistribution(source);
      if (manifest.version !== version) {
        throw new Error("release_distribution_version_mismatch");
      }
      if (buildNumber === null) buildNumber = manifest.build_number;
      else if (buildNumber !== manifest.build_number) {
        throw new Error("release_distribution_build_mismatch");
      }
      if (gitCommit === null) gitCommit = manifest.git_commit;
      else if (gitCommit !== manifest.git_commit) {
        throw new Error("release_distribution_commit_mismatch");
      }
      const identity = `runtime/${manifest.platform}/${manifest.arch}`;
      if (identities.has(identity))
        throw new Error("release_distribution_duplicate");
      identities.add(identity);

      const filename = `memhub-runtime-v${version}-${manifest.platform}-${manifest.arch}.tar.gz`;
      const archive = path.join(stage, filename);
      const entries = (await readdir(source)).sort();
      if (entries.length === 0) throw new Error("release_distribution_invalid");
      await tar.c(
        {
          cwd: source,
          file: archive,
          gzip: true,
          portable: true,
          noMtime: true,
        },
        entries,
      );
      const archiveInfo = await stat(archive);
      const digest = await sha256File(archive);
      const unpackedBytes = await distributionBytes(source);
      artifacts.push({
        kind: "runtime",
        platform: manifest.platform,
        arch: manifest.arch,
        format: "tar.gz",
        filename,
        url: new URL(filename, baseUrl).href,
        bytes: archiveInfo.size,
        unpacked_bytes: unpackedBytes,
        sha256: digest,
        runtime_manifest_schema: manifest.schema_version,
      });
      checksums.push(`${digest}  ${filename}`);
    }
    for (const desktop of desktops) {
      if (
        !desktop ||
        (desktop.arch !== "x64" && desktop.arch !== "arm64") ||
        typeof desktop.file !== "string" ||
        desktop.file.length === 0 ||
        typeof desktop.minimumSystemVersion !== "string" ||
        !/^\d+\.\d+$/.test(desktop.minimumSystemVersion)
      ) {
        throw new Error("release_desktop_invalid");
      }
      const identity = `desktop/darwin/${desktop.arch}`;
      if (identities.has(identity))
        throw new Error("release_distribution_duplicate");
      identities.add(identity);
      const source = await realpath(desktop.file);
      const sourceInfo = await lstat(source);
      if (
        !sourceInfo.isFile() ||
        sourceInfo.isSymbolicLink() ||
        sourceInfo.size <= 0
      ) {
        throw new Error("release_desktop_invalid");
      }
      const filename = `MemHub-v${version}-macos-${desktop.arch}.dmg`;
      const target = path.join(stage, filename);
      await copyFile(source, target);
      const digest = await sha256File(target);
      artifacts.push({
        kind: "desktop",
        platform: "darwin",
        arch: desktop.arch,
        format: "dmg",
        filename,
        url: new URL(filename, baseUrl).href,
        bytes: sourceInfo.size,
        sha256: digest,
        minimum_system_version: desktop.minimumSystemVersion,
      });
      checksums.push(`${digest}  ${filename}`);
    }
    for (const metadata of [
      { role: "sbom", format: "spdx-json", filename: "SBOM.spdx.json" },
      {
        role: "notices",
        format: "markdown",
        filename: "THIRD_PARTY_NOTICES.md",
      },
    ]) {
      const source = path.join(root, metadata.filename);
      const info = await lstat(source).catch(() => null);
      if (!info?.isFile() || info.isSymbolicLink() || info.size <= 0) {
        throw new Error("release_metadata_invalid");
      }
      const target = path.join(stage, metadata.filename);
      await copyFile(source, target);
      const digest = await sha256File(target);
      artifacts.push({
        kind: "metadata",
        role: metadata.role,
        format: metadata.format,
        filename: metadata.filename,
        url: new URL(metadata.filename, baseUrl).href,
        bytes: info.size,
        sha256: digest,
      });
      checksums.push(`${digest}  ${metadata.filename}`);
    }
    artifacts.sort((left, right) =>
      artifactIdentity(left).localeCompare(artifactIdentity(right)),
    );
    checksums.sort();
    const publishedAt = options.publishedAt ?? new Date().toISOString();
    if (
      !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/.test(publishedAt) ||
      Number.isNaN(Date.parse(publishedAt))
    ) {
      throw new Error("release_published_at_invalid");
    }
    const releaseManifest = {
      schema_version: 1,
      product: "memhub-release",
      version,
      build_number: buildNumber,
      git_commit: gitCommit,
      channel,
      cli_protocol: releaseContract.cliProtocol,
      supported_runtime_protocol: releaseContract.cliSupportedRuntimeProtocol,
      runtime_protocol: releaseContract.runtimeProtocol,
      supported_cli_protocol: releaseContract.runtimeSupportedCliProtocol,
      published_at: publishedAt,
      artifacts,
    };
    await writeFile(
      path.join(stage, "release-manifest.json"),
      `${JSON.stringify(releaseManifest, null, 2)}\n`,
      { mode: 0o600 },
    );
    await writeFile(
      path.join(stage, "checksums.txt"),
      `${checksums.join("\n")}\n`,
      { mode: 0o600 },
    );
    await rm(output, { recursive: true, force: true });
    await import("node:fs/promises").then(({ rename }) =>
      rename(stage, output),
    );
    return releaseManifest;
  } catch (error) {
    await rm(stage, { recursive: true, force: true });
    throw error;
  }
}

async function verifyDistribution(source) {
  let value;
  try {
    value = JSON.parse(
      await readFile(path.join(source, "manifest.json"), "utf8"),
    );
  } catch {
    throw new Error("release_distribution_manifest_invalid");
  }
  if (
    value?.schema_version !== 1 ||
    value?.product !== "one-memory-runtime" ||
    typeof value.version !== "string" ||
    !SEMVER.test(value.version) ||
    !Number.isSafeInteger(value.build_number) ||
    value.build_number < 0 ||
    typeof value.git_commit !== "string" ||
    !COMMIT.test(value.git_commit) ||
    value.runtime_protocol !== releaseContract.runtimeProtocol ||
    value.supported_cli_protocol?.min !==
      releaseContract.runtimeSupportedCliProtocol.min ||
    value.supported_cli_protocol?.max !==
      releaseContract.runtimeSupportedCliProtocol.max ||
    !["darwin", "linux"].includes(value.platform) ||
    !["x64", "arm64"].includes(value.arch) ||
    value.files === null ||
    typeof value.files !== "object" ||
    Array.isArray(value.files)
  ) {
    throw new Error("release_distribution_manifest_invalid");
  }
  const declared = Object.keys(value.files).sort();
  if (
    declared.length === 0 ||
    Object.values(value.files).some(
      (digest) => typeof digest !== "string" || !SHA256.test(digest),
    )
  ) {
    throw new Error("release_distribution_manifest_invalid");
  }
  const actual = (await treeFiles(source)).sort();
  const expected = [...declared, "manifest.json"].sort();
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error("release_distribution_manifest_incomplete");
  }
  for (const relative of declared) {
    if (
      (await sha256File(path.join(source, relative))) !== value.files[relative]
    ) {
      throw new Error("release_distribution_hash_mismatch");
    }
  }
  return value;
}

async function treeFiles(source, current = source) {
  const entries = await readdir(current, { withFileTypes: true });
  entries.sort((left, right) => left.name.localeCompare(right.name));
  const files = [];
  for (const entry of entries) {
    const target = path.join(current, entry.name);
    const relative = path.relative(source, target);
    if (!safeRelative(relative))
      throw new Error("release_distribution_path_invalid");
    if (entry.isSymbolicLink()) {
      const resolved = await realpath(target);
      const escaped = path.relative(source, resolved);
      if (escaped.startsWith("..") || path.isAbsolute(escaped)) {
        throw new Error("release_distribution_link_escapes");
      }
      files.push(relative);
    } else if (entry.isDirectory()) {
      files.push(...(await treeFiles(source, target)));
    } else if (entry.isFile()) files.push(relative);
    else throw new Error("release_distribution_path_invalid");
  }
  return files;
}

async function distributionBytes(source) {
  let total = 0;
  for (const relative of await treeFiles(source)) {
    total += (await lstat(path.join(source, relative))).size;
  }
  if (!Number.isSafeInteger(total) || total <= 0) {
    throw new Error("release_distribution_size_invalid");
  }
  return total;
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

function assetBaseUrl(raw) {
  let url;
  try {
    url = new URL(raw.endsWith("/") ? raw : `${raw}/`);
  } catch {
    throw new Error("release_asset_base_url_invalid");
  }
  if (
    url.protocol !== "https:" ||
    url.username ||
    url.password ||
    url.search ||
    url.hash
  ) {
    throw new Error("release_asset_base_url_invalid");
  }
  return url;
}

function safeRelative(value) {
  return (
    value.length > 0 &&
    !path.isAbsolute(value) &&
    value !== ".." &&
    !value.startsWith(`..${path.sep}`) &&
    !value.includes("\0")
  );
}

function artifactIdentity(artifact) {
  return artifact.kind === "metadata"
    ? `metadata/${artifact.role}`
    : `${artifact.kind}/${artifact.platform}/${artifact.arch}`;
}

function parseArgs(arguments_) {
  const options = { distributions: [], desktops: [] };
  for (let index = 0; index < arguments_.length; index += 1) {
    const flag = arguments_[index];
    const value = arguments_[++index];
    if (value === undefined) throw new Error(`missing value for ${flag}`);
    if (flag === "--output" && options.output === undefined)
      options.output = value;
    else if (flag === "--version" && options.version === undefined)
      options.version = value;
    else if (flag === "--channel" && options.channel === undefined)
      options.channel = value;
    else if (flag === "--asset-base-url" && options.assetBaseUrl === undefined)
      options.assetBaseUrl = value;
    else if (flag === "--published-at" && options.publishedAt === undefined)
      options.publishedAt = value;
    else if (flag === "--distribution") options.distributions.push(value);
    else if (flag === "--desktop") {
      const match = /^(arm64|x64)=(.+)$/.exec(value);
      if (!match) throw new Error("release_desktop_invalid");
      options.desktops.push({
        arch: match[1],
        file: match[2],
        minimumSystemVersion: "13.0",
      });
    } else throw new Error(`invalid argument: ${flag}`);
  }
  if (
    !options.output ||
    !options.version ||
    !options.channel ||
    !options.assetBaseUrl
  ) {
    throw new Error(
      "usage: build-release-assets.mjs --output <dir> --version <SemVer> --channel <stable|prerelease> --asset-base-url <https-url> --distribution <dir> [...] [--desktop <arm64|x64>=<dmg>]",
    );
  }
  return options;
}

async function main() {
  const result = await buildReleaseAssets(parseArgs(process.argv.slice(2)));
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : error}\n`);
    process.exitCode = 1;
  });
}
