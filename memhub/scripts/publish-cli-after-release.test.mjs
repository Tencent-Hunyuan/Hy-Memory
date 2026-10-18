import assert from "node:assert/strict";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { publishCliAfterRelease } from "./publish-cli-after-release.mjs";

const CLI_VERSION = "0.1.1";
const RUNTIME_VERSION = "0.1.0-internal.1";

async function fixture(t) {
  const root = await mkdtemp(path.join(os.tmpdir(), "memhub-publish-gate-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const publish = path.join(root, "apps", "cli", "dist-publish");
  const output = path.join(root, "apps", "cli", "dist-cli");
  await mkdir(publish, { recursive: true });
  await mkdir(output, { recursive: true });
  await writeFile(
    path.join(publish, "package.json"),
    `${JSON.stringify({ name: "@example/memhub-cli", version: CLI_VERSION, private: false })}\n`,
  );
  await writeFile(
    path.join(output, `example-memhub-cli-${CLI_VERSION}.tgz`),
    "tarball",
  );
  return root;
}

const options = {
  manifestUrl: "https://releases.example.test/release-manifest.json",
  version: CLI_VERSION,
  runtimeVersion: RUNTIME_VERSION,
  gitCommit: "a".repeat(40),
  channel: "prerelease",
  tag: `v${RUNTIME_VERSION}`,
  buildNumber: 1,
  platform: "linux",
  arch: "x64",
  registry: "https://registry.example.test/npm/",
  npmTag: "internal",
  access: "public",
  provenance: true,
  dryRun: true,
  environment: { MEMHUB_NPM_PACKAGE: "@example/memhub-cli" },
};

test("verifies the Runtime before building and publishing the CLI", async (t) => {
  const root = await fixture(t);
  const events = [];
  const receipt = await publishCliAfterRelease(
    { ...options, root },
    {
      verifyVersion: async () => {
        events.push("version");
        return {
          version: options.runtimeVersion,
          build_number: 1,
          tag: options.tag,
        };
      },
      verifyRelease: async (verifyOptions) => {
        events.push("release");
        assert.equal(verifyOptions.gitCommit, options.gitCommit);
        assert.equal(verifyOptions.version, options.runtimeVersion);
        return { version: options.runtimeVersion, sha256: "a".repeat(64) };
      },
      runCommand: (command, arguments_, commandOptions) => {
        assert.equal(commandOptions.env.MEMHUB_NPM_VERSION, options.version);
        assert.equal(
          commandOptions.env.MEMHUB_DEFAULT_RUNTIME_VERSION,
          options.runtimeVersion,
        );
        assert.equal(
          commandOptions.env.ONE_MEMORY_BUILD_NUMBER,
          String(options.buildNumber),
        );
        assert.equal(commandOptions.env.ONE_MEMORY_COMMIT, options.gitCommit);
        events.push(`${command} ${arguments_.join(" ")}`);
      },
    },
  );
  assert.deepEqual(events.slice(0, 2), ["version", "release"]);
  assert.match(events[2], /^pnpm /);
  assert.match(events[3], /^npm publish /);
  assert.match(events[3], /--dry-run/);
  assert.match(events[3], /--access public/);
  assert.match(events[3], /--provenance/);
  assert.equal(receipt.npm_package, "@example/memhub-cli");
  assert.equal(receipt.version, CLI_VERSION);
  assert.equal(receipt.runtime_version, RUNTIME_VERSION);
  assert.equal(receipt.dry_run, true);
  assert.equal(receipt.npm_access, "public");
  assert.equal(receipt.provenance, true);
});

test("never builds or invokes npm when the Runtime release is unavailable", async (t) => {
  const root = await fixture(t);
  let commands = 0;
  await assert.rejects(
    publishCliAfterRelease(
      { ...options, root },
      {
        verifyVersion: async () => ({
          version: options.runtimeVersion,
          build_number: 1,
          tag: options.tag,
        }),
        verifyRelease: async () => {
          throw new Error("release_http_status_404");
        },
        runCommand: () => {
          commands += 1;
        },
      },
    ),
    /release_http_status_404/,
  );
  assert.equal(commands, 0);
});

test("rejects a package identity that differs from the configured publish name", async (t) => {
  const root = await fixture(t);
  await assert.rejects(
    publishCliAfterRelease(
      {
        ...options,
        root,
        environment: { MEMHUB_NPM_PACKAGE: "@public/memhub" },
      },
      {
        verifyVersion: async () => ({
          version: options.runtimeVersion,
          build_number: 1,
          tag: options.tag,
        }),
        verifyRelease: async () => ({ version: options.runtimeVersion }),
        runCommand: () => undefined,
      },
    ),
    /npm_publish_package_mismatch/,
  );
});
