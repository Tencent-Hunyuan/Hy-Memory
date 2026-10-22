import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import {
  mkdir,
  mkdtemp,
  readdir,
  readFile,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { buildReleaseAssets } from "./build-release-assets.mjs";

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

async function distribution(root, platform, arch, buildNumber = 7) {
  const target = path.join(root, `${platform}-${arch}`);
  await mkdir(path.join(target, "bin"), { recursive: true });
  const launcher = "#!/bin/sh\nexit 0\n";
  await writeFile(path.join(target, "bin", "memhub"), launcher, {
    mode: 0o700,
  });
  const sbom = await readFile(new URL("../SBOM.spdx.json", import.meta.url));
  const notices = await readFile(
    new URL("../THIRD_PARTY_NOTICES.md", import.meta.url),
  );
  await writeFile(path.join(target, "SBOM.spdx.json"), sbom);
  await writeFile(path.join(target, "THIRD_PARTY_NOTICES.md"), notices);
  const files = {
    "bin/memhub": sha256(launcher),
    "SBOM.spdx.json": sha256(sbom),
    "THIRD_PARTY_NOTICES.md": sha256(notices),
  };
  await writeFile(
    path.join(target, "manifest.json"),
    `${JSON.stringify({ schema_version: 1, product: "one-memory-runtime", version: "0.1.0-internal.1", build_number: buildNumber, git_commit: "a".repeat(40), runtime_protocol: 1, supported_cli_protocol: { min: 1, max: 1 }, platform, arch, files }, null, 2)}\n`,
  );
  return target;
}

test("builds versioned Runtime archives, checksums and a downloader-compatible manifest", async (t) => {
  const root = await mkdtemp(path.join(os.tmpdir(), "memhub-release-assets-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const linux = await distribution(root, "linux", "x64");
  const darwin = await distribution(root, "darwin", "arm64");
  const armDmg = path.join(root, "MemHub-arm64.dmg");
  const x64Dmg = path.join(root, "MemHub-x64.dmg");
  await writeFile(armDmg, "signed arm64 dmg");
  await writeFile(x64Dmg, "signed x64 dmg");
  const output = path.join(root, "release");

  const manifest = await buildReleaseAssets({
    output,
    version: "0.1.0-internal.1",
    channel: "prerelease",
    assetBaseUrl: "https://git.example.test/release/downloads/",
    publishedAt: "2026-10-13T00:00:00Z",
    distributions: [linux, darwin],
    desktops: [
      { arch: "arm64", file: armDmg, minimumSystemVersion: "13.0" },
      { arch: "x64", file: x64Dmg, minimumSystemVersion: "13.0" },
    ],
  });

  assert.equal(manifest.build_number, 7);
  assert.equal(manifest.git_commit, "a".repeat(40));
  assert.equal(manifest.cli_protocol, 1);
  assert.deepEqual(manifest.supported_runtime_protocol, { min: 1, max: 1 });
  assert.equal(manifest.runtime_protocol, 1);
  assert.deepEqual(manifest.supported_cli_protocol, { min: 1, max: 1 });
  assert.deepEqual(
    manifest.artifacts.map((artifact) =>
      artifact.kind === "metadata"
        ? `${artifact.kind}/${artifact.role}`
        : `${artifact.kind}/${artifact.platform}/${artifact.arch}`,
    ),
    [
      "desktop/darwin/arm64",
      "desktop/darwin/x64",
      "metadata/notices",
      "metadata/sbom",
      "runtime/darwin/arm64",
      "runtime/linux/x64",
    ],
  );
  const names = (await readdir(output)).sort();
  assert.deepEqual(
    names,
    [
      "checksums.txt",
      "MemHub-v0.1.0-internal.1-macos-arm64.dmg",
      "MemHub-v0.1.0-internal.1-macos-x64.dmg",
      "SBOM.spdx.json",
      "THIRD_PARTY_NOTICES.md",
      "memhub-runtime-v0.1.0-internal.1-darwin-arm64.tar.gz",
      "memhub-runtime-v0.1.0-internal.1-linux-x64.tar.gz",
      "release-manifest.json",
    ].sort(),
  );
  const saved = JSON.parse(
    await readFile(path.join(output, "release-manifest.json"), "utf8"),
  );
  assert.deepEqual(saved, manifest);
  const checksums = await readFile(path.join(output, "checksums.txt"), "utf8");
  for (const artifact of manifest.artifacts) {
    assert.match(
      checksums,
      new RegExp(`${artifact.sha256}  ${artifact.filename}`),
    );
    assert.equal(
      artifact.url,
      `https://git.example.test/release/downloads/${artifact.filename}`,
    );
    assert.ok(artifact.bytes > 0);
    if (artifact.kind === "runtime") assert.ok(artifact.unpacked_bytes > 0);
  }
});

test("verifies Runtime symlinks with the same identity rule as the installer", async (t) => {
  const root = await mkdtemp(path.join(os.tmpdir(), "memhub-release-assets-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const source = await distribution(root, "linux", "x64");
  const link = "memhub";
  await symlink(link, path.join(source, "bin", "node"));
  const manifestPath = path.join(source, "manifest.json");
  const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
  manifest.files["bin/node"] = sha256(`symlink\0${link}`);
  await writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);

  const release = await buildReleaseAssets({
    output: path.join(root, "release"),
    version: "0.1.0-internal.1",
    channel: "prerelease",
    assetBaseUrl: "https://git.example.test/release/downloads/",
    distributions: [source],
  });

  assert.ok(
    release.artifacts.some(
      (artifact) =>
        artifact.kind === "runtime" &&
        artifact.platform === "linux" &&
        artifact.arch === "x64",
    ),
  );
});

test("refuses version, build and duplicate target drift", async (t) => {
  const root = await mkdtemp(path.join(os.tmpdir(), "memhub-release-assets-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const first = await distribution(root, "linux", "x64", 7);
  const second = await distribution(root, "linux", "arm64", 8);
  await assert.rejects(
    buildReleaseAssets({
      output: path.join(root, "release"),
      version: "0.1.0-internal.1",
      channel: "prerelease",
      assetBaseUrl: "https://git.example.test/release/",
      distributions: [first, second],
    }),
    /release_distribution_build_mismatch/,
  );
  await assert.rejects(
    buildReleaseAssets({
      output: path.join(root, "release"),
      version: "0.1.0-internal.2",
      channel: "prerelease",
      assetBaseUrl: "https://git.example.test/release/",
      distributions: [first],
    }),
    /release_distribution_version_mismatch/,
  );
  await assert.rejects(
    buildReleaseAssets({
      output: path.join(root, "release"),
      version: "0.1.0-internal.1",
      channel: "prerelease",
      assetBaseUrl: "https://git.example.test/release/",
      distributions: [first, first],
    }),
    /release_distribution_duplicate/,
  );
});

test("refuses Runtime commit drift across platforms", async (t) => {
  const root = await mkdtemp(path.join(os.tmpdir(), "memhub-release-assets-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const first = await distribution(root, "linux", "x64", 7);
  const second = await distribution(root, "linux", "arm64", 7);
  const secondManifestPath = path.join(second, "manifest.json");
  const secondManifest = JSON.parse(await readFile(secondManifestPath, "utf8"));
  secondManifest.git_commit = "b".repeat(40);
  await writeFile(
    secondManifestPath,
    `${JSON.stringify(secondManifest, null, 2)}\n`,
  );
  await assert.rejects(
    buildReleaseAssets({
      output: path.join(root, "release"),
      version: "0.1.0-internal.1",
      channel: "prerelease",
      assetBaseUrl: "https://git.example.test/release/",
      distributions: [first, second],
    }),
    /release_distribution_commit_mismatch/,
  );
});

test("refuses a changed byte and insecure asset base URL", async (t) => {
  const root = await mkdtemp(path.join(os.tmpdir(), "memhub-release-assets-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const source = await distribution(root, "linux", "x64");
  await writeFile(path.join(source, "bin", "memhub"), "changed\n");
  await assert.rejects(
    buildReleaseAssets({
      output: path.join(root, "release"),
      version: "0.1.0-internal.1",
      channel: "prerelease",
      assetBaseUrl: "https://git.example.test/release/",
      distributions: [source],
    }),
    /release_distribution_hash_mismatch/,
  );
  await assert.rejects(
    buildReleaseAssets({
      output: path.join(root, "release"),
      version: "0.1.0-internal.1",
      channel: "prerelease",
      assetBaseUrl: "http://git.example.test/release/",
      distributions: [source],
    }),
    /release_asset_base_url_invalid/,
  );
});

test("refuses duplicate Desktop architecture and unusable DMG inputs", async (t) => {
  const root = await mkdtemp(path.join(os.tmpdir(), "memhub-release-assets-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const source = await distribution(root, "linux", "x64");
  const dmg = path.join(root, "MemHub.dmg");
  await writeFile(dmg, "dmg");
  await assert.rejects(
    buildReleaseAssets({
      output: path.join(root, "release"),
      version: "0.1.0-internal.1",
      channel: "prerelease",
      assetBaseUrl: "https://git.example.test/release/",
      distributions: [source],
      desktops: [
        { arch: "arm64", file: dmg, minimumSystemVersion: "13.0" },
        { arch: "arm64", file: dmg, minimumSystemVersion: "13.0" },
      ],
    }),
    /release_distribution_duplicate/,
  );
  await assert.rejects(
    buildReleaseAssets({
      output: path.join(root, "release-two"),
      version: "0.1.0-internal.1",
      channel: "prerelease",
      assetBaseUrl: "https://git.example.test/release/",
      distributions: [source],
      desktops: [
        {
          arch: "x64",
          file: path.join(root, "missing.dmg"),
          minimumSystemVersion: "13.0",
        },
      ],
    }),
    /ENOENT|release_desktop_invalid/,
  );
});
