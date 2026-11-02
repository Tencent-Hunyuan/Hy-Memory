import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const root = fileURLToPath(new URL(".", import.meta.url));

function sourceFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return sourceFiles(path);
    if (!/\.tsx?$/.test(entry.name) || /\.test\.tsx?$/.test(entry.name))
      return [];
    return [relative(root, path)];
  });
}

const files = sourceFiles(root).map((path) => ({
  path,
  text: readFileSync(join(root, path), "utf8"),
}));

// Desktop bridge members that read or change a host's connection.
const HOST_MEMBERS = [
  "getRecallHosts",
  "previewRecallInstall",
  "installRecall",
  "previewRecallUninstall",
  "uninstallRecall",
  "getRecallPermission",
  "setRecallPermission",
  "revokeRecallPermission",
  "repairRecallCommands",
  "previewConnectionSetup",
  "executeConnectionSetup",
  "getConnectionSetupReceipt",
  "probeRecallHost",
  "previewSkillSync",
  "applySkillSync",
];

// Core calls whose result is, or changes, a source record.
const SOURCE_CALLS = [
  "getSources",
  "setSourceSync",
  "detectSource",
  "connectSources",
  "saveSourceDirectory",
];

function hostUses(text: string): string[] {
  return HOST_MEMBERS.filter((name) => new RegExp(`\\b${name}\\b`).test(text));
}

function sourceUses(text: string): string[] {
  const found = new Set<string>();
  for (const match of text.matchAll(
    /import\s*(?:type\s*)?\{([^}]*)\}\s*from\s*["']\.\/api["']/g,
  )) {
    for (const part of match[1].split(",")) {
      const name = part.trim().split(/\s+as\s+/)[0];
      if (SOURCE_CALLS.includes(name)) found.add(name);
    }
  }
  for (const name of SOURCE_CALLS)
    if (new RegExp(`\\.${name}\\s*\\(`).test(text)) found.add(name);
  return [...found].sort();
}

function offenders(
  use: (text: string) => string[],
  owners: string[],
): string[] {
  return files
    .filter((file) => !owners.includes(file.path))
    .map((file) => ({ path: file.path, names: use(file.text) }))
    .filter((file) => file.names.length)
    .map((file) => `${file.path}: ${file.names.join(", ")}`);
}

describe("single state layer", () => {
  it("scans the real tree, so an empty result is not a vacuous pass", () => {
    const host = files.find((file) => file.path === "host-state.ts");
    const source = files.find((file) => file.path === "source-state.ts");
    expect(host && hostUses(host.text)).toContain("getRecallHosts");
    expect(source && sourceUses(source.text)).toContain("getSources");
    expect(hostUses("x.installRecall(a)")).toEqual(["installRecall"]);
    expect(
      sourceUses('import { getSources as g, getHealth } from "./api";'),
    ).toEqual(["getSources"]);
  });

  it("reads and writes host connections only through host-state", () => {
    expect(
      offenders(hostUses, [
        "host-state.ts",
        "global.d.ts",
        // Test support: it is the fake bridge, and only tests import it.
        "harness-profiles.test-fixture.ts",
      ]),
    ).toEqual([]);
  });

  it("reads and writes sources only through source-state", () => {
    expect(offenders(sourceUses, ["source-state.ts", "api.ts"])).toEqual([]);
  });
});
