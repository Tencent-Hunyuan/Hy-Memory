/** Host-bridge installer containment: symlinked ancestors must not be written
 * through (C5 host-bridge containment fix, design Rev5).
 *
 * The reproduced defect: a `<host-dir>/skills` (or `plugin`/`plugins`/
 * `extensions`) component replaced by an out-of-root symlink made `apply`
 * exit 0 and write outside `containmentRoot`. These tests are the red/green
 * gate: every layout refuses before the first mutation and leaves the outside
 * directory untouched.
 */

import {
  lstat,
  mkdir,
  mkdtemp,
  readdir,
  readFile,
  realpath,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  applyRecallInstall,
  claudeCodeLayout,
  codexLayout,
  cursorLayout,
  deepseekHarnessLayout,
  hermesLayout,
  LOCAL_OPERATION_DISABLED_MESSAGE,
  opencodeLayout,
  piLayout,
  planRecallInstall,
  previewRecallInstall,
  qwenCodeLayout,
  type RecallHostLayout,
  type RecallRuntime,
  refuseDisabledHostOperation,
  verifyRecallInstall,
  workbuddyLayout,
  zcodeLayout,
} from "./recall-installer.js";

let base: string;
let outside: string;

function runtime(): RecallRuntime {
  return {
    runtimeCommand: process.execPath,
    mcpEntryPath: path.join(base, "runtime", "mcp.cjs"),
    discoveryPath: path.join(base, "data", "cursor-broker.json"),
    dataDir: path.join(base, "data"),
    // Needed by CLI-channel hosts (pi).
    recallCliPath: path.join(base, "runtime", "recall-cli.cjs"),
  };
}

async function outsideEntries(): Promise<string[]> {
  return readdir(outside).catch(() => [] as string[]);
}

beforeEach(async () => {
  base = await mkdtemp(path.join(tmpdir(), "om-containment-"));
  outside = path.join(base, "outside");
  await mkdir(outside, { recursive: true });
  await mkdir(path.join(base, "runtime"), { recursive: true });
  await writeFile(path.join(base, "runtime", "mcp.cjs"), "// stub\n");
  await writeFile(path.join(base, "runtime", "recall-cli.cjs"), "// stub\n");
});

afterEach(async () => {
  await rm(base, { recursive: true, force: true });
});

describe("symlinked ancestor containment", () => {
  it("refuses a symlinked skills directory instead of writing outside the root", async () => {
    const hostDir = path.join(base, "cursor");
    await mkdir(hostDir, { recursive: true });
    // The attack: skills -> outside, so SKILL.md lands at outside/...
    await symlink(outside, path.join(hostDir, "skills"));
    const layout = cursorLayout(hostDir);
    const plan = await planRecallInstall(layout, runtime());

    await expect(applyRecallInstall(plan)).rejects.toThrow(
      /containment_refused|escapes/,
    );
    // Nothing was written through the link.
    expect(await outsideEntries()).toEqual([]);
  });

  it("refuses a symlinked skills leaf for codex too", async () => {
    const hostDir = path.join(base, "codex");
    await mkdir(hostDir, { recursive: true });
    await symlink(outside, path.join(hostDir, "skills"));
    const layout = codexLayout(hostDir);
    const plan = await planRecallInstall(layout, runtime());

    await expect(applyRecallInstall(plan)).rejects.toThrow(
      /containment_refused|escapes/,
    );
    expect(await outsideEntries()).toEqual([]);
  });

  it("still installs normally when the skills tree is a real directory", async () => {
    const hostDir = path.join(base, "cursor-ok");
    await mkdir(hostDir, { recursive: true });
    const layout = cursorLayout(hostDir);
    const plan = await planRecallInstall(layout, runtime());
    const preview = await previewRecallInstall(plan);

    await applyRecallInstall(plan, preview.fingerprint);
    const skill = await readFile(
      path.join(hostDir, "skills", "memhub-recall", "SKILL.md"),
      "utf8",
    );
    expect(skill.length).toBeGreaterThan(0);
    expect(await outsideEntries()).toEqual([]);
  });

  it("binds a canonical root that is itself a symlink", async () => {
    const realRoot = path.join(base, "real-cursor");
    await mkdir(realRoot, { recursive: true });
    const linkedRoot = path.join(base, "linked-cursor");
    await symlink(realRoot, linkedRoot);
    const layout = cursorLayout(linkedRoot);
    const plan = await planRecallInstall(layout, runtime());
    const preview = await previewRecallInstall(plan);

    await applyRecallInstall(plan, preview.fingerprint);
    const canonical = await realpath(realRoot);
    const skill = await readFile(
      path.join(canonical, "skills", "memhub-recall", "SKILL.md"),
      "utf8",
    );
    expect(skill.length).toBeGreaterThan(0);
  });

  it("refuses when the root is repointed between preview and apply", async () => {
    const realRoot = path.join(base, "root-a");
    const otherRoot = path.join(base, "root-b");
    await mkdir(realRoot, { recursive: true });
    await mkdir(otherRoot, { recursive: true });
    const linkedRoot = path.join(base, "root-link");
    await symlink(realRoot, linkedRoot);
    const layout: RecallHostLayout = cursorLayout(linkedRoot);
    const plan = await planRecallInstall(layout, runtime());

    // Swap the link to a different directory after planning.
    await rm(linkedRoot);
    await symlink(otherRoot, linkedRoot);

    await expect(applyRecallInstall(plan)).rejects.toThrow(
      /containment_refused/,
    );
  });
});

describe("disabled local operations", () => {
  it("refuses with the exact visible copy", () => {
    expect(() => refuseDisabledHostOperation("rollback")).toThrow(
      LOCAL_OPERATION_DISABLED_MESSAGE,
    );
    expect(() => refuseDisabledHostOperation("preview-rollback")).toThrow(
      /体验版暂不支持/,
    );
  });

  it("does not mutate the host directory when refusing", async () => {
    const hostDir = path.join(base, "disabled-host");
    await mkdir(hostDir, { recursive: true });
    await writeFile(path.join(hostDir, "mcp.json"), "{}\n");
    const before = await lstat(path.join(hostDir, "mcp.json"));

    expect(() => refuseDisabledHostOperation("rollback")).toThrow();
    const after = await lstat(path.join(hostDir, "mcp.json"));
    expect(after.mtimeMs).toBe(before.mtimeMs);
    expect(await readdir(hostDir)).toEqual(["mcp.json"]);
  });
});

describe("all writing layouts refuse a symlinked ancestor", () => {
  const cases: Array<{
    name: string;
    link: string;
    layout: (dir: string) => RecallHostLayout;
  }> = [
    { name: "cursor skills", link: "skills", layout: cursorLayout },
    { name: "codex skills", link: "skills", layout: codexLayout },
    {
      name: "claude-code skills",
      link: "skills",
      layout: (dir) =>
        claudeCodeLayout({
          claudeDir: dir,
          configPath: path.join(dir, ".claude.json"),
        }),
    },
    { name: "qwen-code skills", link: "skills", layout: qwenCodeLayout },
    { name: "hermes skills", link: "skills", layout: hermesLayout },
    { name: "opencode plugin", link: "plugin", layout: opencodeLayout },
    { name: "pi extensions", link: "extensions", layout: piLayout },
    {
      name: "deepseek profiles/headless",
      link: path.join("profiles", "headless"),
      layout: (dir) =>
        deepseekHarnessLayout({ dshHome: dir, profile: "headless" }),
    },
    { name: "workbuddy plugins", link: "plugins", layout: workbuddyLayout },
    {
      name: "zcode plugins",
      link: path.join("cli", "plugins"),
      layout: zcodeLayout,
    },
  ];

  for (const testCase of cases) {
    it(`${testCase.name}`, async () => {
      const hostDir = path.join(
        base,
        testCase.name.replaceAll(/[^a-z0-9]+/gi, "-"),
      );
      await mkdir(hostDir, { recursive: true });
      const linkPath = path.join(hostDir, testCase.link);
      await mkdir(path.dirname(linkPath), { recursive: true });
      await symlink(outside, linkPath);
      const layout = testCase.layout(hostDir);
      const plan = await planRecallInstall(layout, runtime());
      await expect(applyRecallInstall(plan)).rejects.toThrow(
        /containment_refused|escapes/,
      );
      expect(await outsideEntries()).toEqual([]);
    });
  }
});

describe("leaf-level attacks", () => {
  it("refuses a symlinked SKILL.md leaf", async () => {
    const hostDir = path.join(base, "leaf-symlink");
    const skillDir = path.join(hostDir, "skills", "memhub-recall");
    await mkdir(skillDir, { recursive: true });
    // The leaf itself is the symlink, not its parent.
    await writeFile(path.join(outside, "target.md"), "outside\n");
    await symlink(
      path.join(outside, "target.md"),
      path.join(skillDir, "SKILL.md"),
    );
    const layout = cursorLayout(hostDir);
    // A symlinked leaf is refused either at plan (unmanaged skill) or apply
    // (containment); both must decline to write through it.
    await expect(
      planRecallInstall(layout, runtime()).then((plan) =>
        applyRecallInstall(plan),
      ),
    ).rejects.toThrow(/containment_refused|escapes|unmanaged/);
    expect(await readFile(path.join(outside, "target.md"), "utf8")).toBe(
      "outside\n",
    );
  });

  it("refuses a regular file swapped in for the leaf after planning", async () => {
    const hostDir = path.join(base, "leaf-swap");
    await mkdir(hostDir, { recursive: true });
    const layout = cursorLayout(hostDir);
    const plan = await planRecallInstall(layout, runtime());
    // A user file appears at the exact leaf path after the plan was made.
    const skillDir = path.join(hostDir, "skills", "memhub-recall");
    await mkdir(skillDir, { recursive: true });
    await writeFile(path.join(skillDir, "SKILL.md"), "user data\n");
    await expect(applyRecallInstall(plan)).rejects.toThrow(
      /containment_refused|changed after plan/,
    );
    // The user's file is intact, not truncated.
    expect(await readFile(path.join(skillDir, "SKILL.md"), "utf8")).toBe(
      "user data\n",
    );
  });
});

describe("external config path", () => {
  it("refuses when the config's parent is a symlink outside the host", async () => {
    const hostDir = path.join(base, "claude");
    await mkdir(hostDir, { recursive: true });
    const outsideDir = path.join(outside, "cfg");
    await mkdir(outsideDir, { recursive: true });
    const linkedParent = path.join(base, "linked-parent");
    await symlink(outsideDir, linkedParent);
    const layout = claudeCodeLayout({
      claudeDir: hostDir,
      configPath: path.join(linkedParent, ".claude.json"),
    });
    const plan = await planRecallInstall(layout, runtime());
    await expect(applyRecallInstall(plan)).rejects.toThrow(
      /containment_refused/,
    );
    // Nothing was written through the linked parent.
    expect(await readdir(outsideDir)).toEqual([]);
  });
});

describe("verify re-runs containment", () => {
  it("refuses to verify a healthy install whose ancestor became a symlink", async () => {
    const hostDir = path.join(base, "verify-host");
    const outsideSkills = path.join(outside, "skills");
    await mkdir(hostDir, { recursive: true });
    const layout = cursorLayout(hostDir);
    const plan = await planRecallInstall(layout, runtime());
    const preview = await previewRecallInstall(plan);
    await applyRecallInstall(plan, preview.fingerprint);

    // The install is intact, but its ancestor is later swapped for a link.
    await rm(path.join(hostDir, "skills"), { recursive: true, force: true });
    await mkdir(path.join(outsideSkills, "memhub-recall"), { recursive: true });
    await writeFile(
      path.join(outsideSkills, "memhub-recall", "SKILL.md"),
      "moved",
    );
    await symlink(outsideSkills, path.join(hostDir, "skills"));

    await expect(verifyRecallInstall(layout)).rejects.toThrow(
      /containment_refused/,
    );
  });
});
