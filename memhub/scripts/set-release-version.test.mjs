import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { setReleaseVersion } from "./set-release-version.mjs";

test("updates every release-bearing manifest in one transaction-sized command", async (t) => {
  const root = await mkdtemp(path.join(os.tmpdir(), "memhub-version-sync-"));
  t.after(() => rm(root, { recursive: true, force: true }));
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
  for (const relative of packageFiles) {
    const target = path.join(root, relative);
    await mkdir(path.dirname(target), { recursive: true });
    await writeFile(target, '{"name":"fixture","version":"0.0.0"}\n');
  }
  const rootPackage = JSON.parse(
    await readFile(path.join(root, "package.json"), "utf8"),
  );
  rootPackage.release = { build_number: 6 };
  await writeFile(
    path.join(root, "package.json"),
    `${JSON.stringify(rootPackage)}\n`,
  );
  await mkdir(path.join(root, "apps/core"), { recursive: true });
  await writeFile(
    path.join(root, "apps/core/pyproject.toml"),
    '[project]\nname = "fixture"\nversion = "0.0.0"\n',
  );
  await writeFile(
    path.join(root, "apps/core/uv.lock"),
    'version = 1\n\n[[package]]\nname = "one-memory"\nversion = "0.0.0"\n',
  );

  const receipt = await setReleaseVersion("1.2.3-rc.1", root, 7);

  assert.equal(receipt.changed.length, 12);
  assert.equal(receipt.build_number, 7);
  for (const relative of packageFiles) {
    const document = JSON.parse(
      await readFile(path.join(root, relative), "utf8"),
    );
    assert.equal(document.version, "1.2.3-rc.1");
  }
  assert.match(
    await readFile(path.join(root, "apps/core/pyproject.toml"), "utf8"),
    /^version = "1\.2\.3rc1"$/m,
  );
  assert.match(
    await readFile(path.join(root, "apps/core/uv.lock"), "utf8"),
    /name = "one-memory"\nversion = "1\.2\.3rc1"/,
  );
  assert.equal(
    JSON.parse(await readFile(path.join(root, "package.json"), "utf8")).release
      .build_number,
    7,
  );
});

test("maps an internal SemVer prerelease to deterministic PEP 440 metadata", async (t) => {
  const root = await mkdtemp(
    path.join(os.tmpdir(), "memhub-version-internal-"),
  );
  t.after(() => rm(root, { recursive: true, force: true }));
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
  for (const relative of packageFiles) {
    const target = path.join(root, relative);
    await mkdir(path.dirname(target), { recursive: true });
    await writeFile(target, '{"name":"fixture","version":"0.0.0"}\n');
  }
  const rootPackage = JSON.parse(
    await readFile(path.join(root, "package.json"), "utf8"),
  );
  rootPackage.release = { build_number: 1 };
  await writeFile(
    path.join(root, "package.json"),
    `${JSON.stringify(rootPackage)}\n`,
  );
  await mkdir(path.join(root, "apps/core"), { recursive: true });
  await writeFile(
    path.join(root, "apps/core/pyproject.toml"),
    '[project]\nname = "fixture"\nversion = "0.0.0"\n',
  );
  await writeFile(
    path.join(root, "apps/core/uv.lock"),
    'version = 1\n\n[[package]]\nname = "one-memory"\nversion = "0.0.0"\n',
  );

  const receipt = await setReleaseVersion("0.1.0-internal.1", root);

  assert.equal(receipt.python_version, "0.1.0.dev1+internal");
  assert.equal(receipt.build_number, 1);
  assert.match(
    await readFile(path.join(root, "apps/core/pyproject.toml"), "utf8"),
    /^version = "0\.1\.0\.dev1\+internal"$/m,
  );
  assert.match(
    await readFile(path.join(root, "apps/core/uv.lock"), "utf8"),
    /name = "one-memory"\nversion = "0\.1\.0\.dev1\+internal"/,
  );
});

test("refuses an ambiguous Core lockfile instead of changing an arbitrary package", async (t) => {
  const root = await mkdtemp(path.join(os.tmpdir(), "memhub-version-lock-"));
  t.after(() => rm(root, { recursive: true, force: true }));
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
  for (const relative of packageFiles) {
    const target = path.join(root, relative);
    await mkdir(path.dirname(target), { recursive: true });
    await writeFile(
      target,
      `${JSON.stringify({ name: "fixture", version: "0.1.0", ...(relative === "package.json" ? { release: { build_number: 1 } } : {}) })}\n`,
    );
  }
  await mkdir(path.join(root, "apps/core"), { recursive: true });
  await writeFile(
    path.join(root, "apps/core/pyproject.toml"),
    '[project]\nname = "fixture"\nversion = "0.1.0"\n',
  );
  await writeFile(
    path.join(root, "apps/core/uv.lock"),
    'version = 1\n\n[[package]]\nname = "other"\nversion = "0.1.0"\n',
  );

  await assert.rejects(
    setReleaseVersion("0.2.0", root, 2),
    /uv\.lock one-memory version must appear exactly once/,
  );
  assert.equal(
    JSON.parse(await readFile(path.join(root, "package.json"), "utf8")).version,
    "0.1.0",
  );
  assert.match(
    await readFile(path.join(root, "apps/core/pyproject.toml"), "utf8"),
    /^version = "0\.1\.0"$/m,
  );
});

test("rejects a non-SemVer version before changing files", async () => {
  await assert.rejects(
    setReleaseVersion("release-one", "/nonexistent"),
    /valid SemVer/,
  );
});
