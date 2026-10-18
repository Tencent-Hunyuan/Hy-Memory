#!/usr/bin/env node
/**
 * Build an installable publish manifest for the CLI.
 *
 * Why this exists: `apps/cli/package.json` declares its two workspace packages as
 * `workspace:*`, which npm refuses to install outside the monorepo
 * (`EUNSUPPORTEDPROTOCOL`). The built bundle is already self-contained — tsup
 * inlines `@one-memory/runtime-control` and `axios`, and nothing requires a
 * `@one-memory/*` package at runtime — so those entries are development-time
 * only. Publishing therefore needs a manifest without them.
 *
 * It is generated rather than hand-written so the two cannot drift: this reads
 * the real package.json and only strips what is provably not needed at runtime.
 */
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "..");
const require = createRequire(import.meta.url);
const { assertSemver } = require("../../../scripts/release-version.cjs");
const pkgPath = resolve(root, "package.json");
const pkg = JSON.parse(readFileSync(pkgPath, "utf8"));
const publishVersion = assertSemver(
  process.env.MEMHUB_NPM_VERSION?.trim() || pkg.version,
  "MEMHUB_NPM_VERSION",
);
const publishName = process.env.MEMHUB_NPM_PACKAGE?.trim();
if (!publishName || !/^(?:@[a-z0-9._-]+\/)?[a-z0-9._-]+$/.test(publishName)) {
  throw new Error(
    "MEMHUB_NPM_PACKAGE must name the explicit npm publish identity",
  );
}

const bundle = resolve(root, "dist/one-memory.cjs");
const source = readFileSync(bundle, "utf8");
const runtimeRequires = [
  ...source.matchAll(/require\(["']([^"']+)["']\)/g),
].map((m) => m[1]);
const workspaceLeaked = runtimeRequires.filter((name) =>
  name.startsWith("@one-memory/"),
);
if (workspaceLeaked.length > 0) {
  throw new Error(
    `bundle still requires workspace packages at runtime: ${workspaceLeaked.join(", ")}. ` +
      "Add them to tsup noExternal before publishing.",
  );
}

const outDir = resolve(root, "dist-publish");
mkdirSync(outDir, { recursive: true });

const published = { ...pkg };
published.name = publishName;
published.version = publishVersion;
for (const section of ["dependencies", "devDependencies", "peerDependencies"]) {
  if (!published[section]) continue;
  published[section] = Object.fromEntries(
    Object.entries(published[section]).filter(([name]) =>
      // Anything the bundle actually requires at runtime must stay; everything
      // else is build-time only. The bundle is inlined (tsup noExternal), so
      // in practice only Node builtins remain — but compute it rather than
      // assume, so a future non-inlined dep is not silently dropped.
      runtimeRequires.some((req) => req === name || req.startsWith(`${name}/`)),
    ),
  );
  if (Object.keys(published[section]).length === 0) delete published[section];
}
published.private = false;
published.files = ["dist", "scripts", "package.json"];

// `files` alone is NOT enough: npm silently applies the repo's root
// `.gitignore`, which ignores `dist/` — so the built bundle was dropped from the
// tarball and the published package had no executable at all. An `.npmignore`
// in the staging dir overrides that and makes the manifest authoritative.
writeFileSync(
  resolve(outDir, ".npmignore"),
  [
    "# Ship the built bundle: the repo root .gitignore excludes dist/.",
    "!dist/",
    "",
  ].join("\n"),
);

writeFileSync(
  resolve(outDir, "package.json"),
  `${JSON.stringify(published, null, 2)}\n`,
);
console.log("wrote", resolve(outDir, "package.json"));
