import { createHash } from "node:crypto";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { applyCursorInstall, planCursorInstall } from "./cursor-installer.js";
import { MCP_TOOLSET_KEY, MCP_TOOLSET_VERSION } from "./mcp-toolset.js";
import {
  applyRecallInstall,
  applyRecallUninstall,
  codexLayout,
  cursorLayout,
  piLayout,
  planRecallInstall,
  planRecallUninstall,
  SKILL_MARKER,
  SKILL_VERSION,
  verifyRecallInstall,
} from "./recall-installer.js";
import {
  checkSkillDrift,
  hostHealthCheck,
  isManagedSkill,
  planSkillSync,
  readSkillVersion,
} from "./skill-drift.js";

const legacySkills = [
  { marker: "<!-- one-memory-managed:v1 -->", version: 1 },
  { marker: "<!-- one-memory-managed skill-version=6 -->", version: 6 },
  { marker: "<!-- one-memory-managed skill-version=9 -->", version: 9 },
  { marker: "<!-- one-memory-managed skill-version=10 -->", version: 10 },
];

describe.each(legacySkills)(
  "legacy Skill version $version",
  ({ marker, version }) => {
    it.each(["before", "after"])(
      "recognizes its marker %s frontmatter",
      (position) => {
        const frontmatter =
          "---\nname: memhub-recall\ndescription: Fixture.\n---\n";
        const text =
          position === "before"
            ? `${marker}\n${frontmatter}`
            : `${frontmatter}${marker}\n`;
        expect(isManagedSkill(text)).toBe(true);
        expect(readSkillVersion(text)).toBe(version);
      },
    );

    it.each([
      { channel: "mcp", layout: codexLayout },
      { channel: "cli", layout: piLayout },
    ])(
      "syncs a journaled $channel install and preserves uninstall lineage",
      async ({ layout }) => {
        const root = await mkdtemp(path.join(os.tmpdir(), "om-legacy-skill-"));
        try {
          const host = layout(path.join(root, "host"));
          const skillPath = host.skillPath as string;
          const fixtureRuntime = {
            runtimeCommand: path.join(root, "not-installed-runtime"),
            mcpEntryPath: path.join(root, "mcp.cjs"),
            discoveryPath: path.join(root, "discovery.json"),
            recallCliPath: path.join(root, "recall-cli.cjs"),
          };
          await applyRecallInstall(
            await planRecallInstall(host, fixtureRuntime),
          );
          const installed = await readFile(skillPath, "utf8");
          const withoutMarker = installed.replace(`${SKILL_MARKER}\n`, "");
          expect(withoutMarker).not.toBe(installed);
          const old = `${marker}\n${withoutMarker}`;
          await writeFile(skillPath, old);
          const journal = JSON.parse(await readFile(host.journalPath, "utf8"));
          journal.installed_skill_hash = createHash("sha256")
            .update(old)
            .digest("hex");
          await writeFile(host.journalPath, `${JSON.stringify(journal)}\n`);

          await expect(
            checkSkillDrift(host, fixtureRuntime),
          ).resolves.toMatchObject({
            state: "stale",
            installedVersion: version,
          });
          // Planning without takeover must still recognize both old marker forms.
          await expect(
            planRecallInstall(host, fixtureRuntime),
          ).resolves.toMatchObject({
            previous: { skill: null },
          });
          // A direct uninstall remains valid before upgrading the old file.
          const uninstall = await planRecallUninstall(host);
          expect(uninstall.files).toContainEqual({
            path: skillPath,
            owner: "one-memory",
            before: old,
            after: null,
          });
          const sync = await planSkillSync(host, fixtureRuntime);
          expect(sync?.requiresConfirmation).toBe(false);
          if (sync === null) throw new Error("expected a stale Skill sync");
          await applyRecallInstall(sync.plan);
          const updated = await readFile(skillPath, "utf8");
          expect(
            updated.startsWith("---\nname: memhub-recall\ndescription: "),
          ).toBe(true);
          expect(updated.match(/<!-- one-memory-managed[^\n]*-->/g)).toEqual([
            SKILL_MARKER,
          ]);
          await expect(
            checkSkillDrift(host, fixtureRuntime),
          ).resolves.toMatchObject({
            state: "in_sync",
            installedVersion: SKILL_VERSION,
          });
          await expect(planSkillSync(host, fixtureRuntime)).resolves.toBeNull();
          expect(
            JSON.parse(await readFile(host.journalPath, "utf8")).previous_skill,
          ).toBeNull();
          await applyRecallUninstall(await planRecallUninstall(host));
          await expect(readFile(skillPath, "utf8")).rejects.toMatchObject({
            code: "ENOENT",
          });
        } finally {
          await rm(root, { recursive: true, force: true });
        }
      },
    );
  },
);

describe("skill drift (R-33)", () => {
  it("reports in_sync when the disk skill is exactly what we write today", async () => {
    const cursorDir = await fixtureDir();
    const discovery = path.join(cursorDir, "broker.json");
    await writeFile(discovery, "{}\n");
    await applyCursorInstall(
      await planCursorInstall(options(cursorDir, discovery)),
    );

    const drift = await checkSkillDrift(
      cursorLayout(cursorDir),
      runtime(discovery),
    );

    expect(drift?.state).toBe("in_sync");
    expect(drift?.currentVersion).toBe(SKILL_VERSION);
    expect(drift?.installedVersion).toBe(SKILL_VERSION);
  }, 30_000);

  it("reports stale when the disk skill is what we wrote, but the template moved", async () => {
    // The journal still fingerprints the bytes on disk: we installed them, the
    // user did not touch them, and the template is now different. That is
    // stale, not modified — offering "you changed this" would be a lie, and
    // syncing without asking is fine because the bytes are still ours.
    const cursorDir = await fixtureDir();
    const discovery = path.join(cursorDir, "broker.json");
    await writeFile(discovery, "{}\n");
    await applyCursorInstall(
      await planCursorInstall(options(cursorDir, discovery)),
    );

    const skillPath = path.join(
      cursorDir,
      "skills",
      "memhub-recall",
      "SKILL.md",
    );
    const installed = await readFile(skillPath, "utf8");
    // An older body we would have written: still marked as ours, still
    // matching the journal, just not today's template.
    const older = installed.replace(
      /# MemHub Recall[\s\S]*$/,
      "# MemHub Recall\n\nUse the tool only when the user explicitly asks for workspace scope.\n",
    );
    await writeFile(skillPath, older);
    // Keep the journal claiming these bytes are what we installed, so the
    // detector can tell "ours, but old" from "someone edited".
    const journalPath = path.join(
      cursorDir,
      ".one-memory",
      "recall-install.json",
    );
    const journal = JSON.parse(await readFile(journalPath, "utf8"));
    const { createHash } = await import("node:crypto");
    journal.installed_skill_hash = createHash("sha256")
      .update(older)
      .digest("hex");
    await writeFile(journalPath, `${JSON.stringify(journal, null, 2)}\n`);

    const drift = await checkSkillDrift(
      cursorLayout(cursorDir),
      runtime(discovery),
    );

    expect(drift?.state).toBe("stale");
  }, 30_000);

  it("reports modified when the user edited the skill after install", async () => {
    // Hand-edited content is the user's asset. Sync must not overwrite it
    // silently; the third state exists so the UI can say so and ask.
    const cursorDir = await fixtureDir();
    const discovery = path.join(cursorDir, "broker.json");
    await writeFile(discovery, "{}\n");
    await applyCursorInstall(
      await planCursorInstall(options(cursorDir, discovery)),
    );

    const skillPath = path.join(
      cursorDir,
      "skills",
      "memhub-recall",
      "SKILL.md",
    );
    const installed = await readFile(skillPath, "utf8");
    await writeFile(
      skillPath,
      `${installed}\n\n## My own rule\nOnly recall on Mondays.\n`,
    );

    const drift = await checkSkillDrift(
      cursorLayout(cursorDir),
      runtime(discovery),
    );

    expect(drift?.state).toBe("modified");
  }, 30_000);

  it("embeds a version in the skill that moves with the template, from one source", async () => {
    // Two copies of the skill text — one with a version, one without — would
    // drift the way preview-vs-apply once could. The version lives in the
    // same function that writes the body.
    const cursorDir = await fixtureDir();
    const discovery = path.join(cursorDir, "broker.json");
    await writeFile(discovery, "{}\n");
    await applyCursorInstall(
      await planCursorInstall(options(cursorDir, discovery)),
    );

    const skill = await readFile(
      path.join(cursorDir, "skills", "memhub-recall", "SKILL.md"),
      "utf8",
    );

    expect(skill).toMatch(
      new RegExp(`one-memory-managed[^\\n]*skill-version=${SKILL_VERSION}`),
    );
    // The old marker must not remain as a second spelling of "ours".
    expect(skill).not.toContain("<!-- one-memory-managed:v1 -->");
  }, 30_000);

  it("detects drift by content hash, not by the version numeral alone", async () => {
    // Mutation target from the requirements: change the template body without
    // bumping SKILL_VERSION, and the host must still read as not in_sync.
    // A detector that only compared the version string would go on saying
    // in_sync, which is the failure this pins.
    const cursorDir = await fixtureDir();
    const discovery = path.join(cursorDir, "broker.json");
    await writeFile(discovery, "{}\n");
    await applyCursorInstall(
      await planCursorInstall(options(cursorDir, discovery)),
    );

    const skillPath = path.join(
      cursorDir,
      "skills",
      "memhub-recall",
      "SKILL.md",
    );
    const installed = await readFile(skillPath, "utf8");
    // Same version numeral in the marker, different body — as if the template
    // was edited without a version bump.
    const sameVersionDifferentBody = installed.replace(
      "you must call `MemHub_recall` before answering",
      "you must call `MemHub_recall` before answering (edited without bumping the version)",
    );
    expect(sameVersionDifferentBody).not.toBe(installed);
    expect(sameVersionDifferentBody).toContain(
      `skill-version=${SKILL_VERSION}`,
    );
    await writeFile(skillPath, sameVersionDifferentBody);
    const journalPath = path.join(
      cursorDir,
      ".one-memory",
      "recall-install.json",
    );
    const journal = JSON.parse(await readFile(journalPath, "utf8"));
    const { createHash } = await import("node:crypto");
    journal.installed_skill_hash = createHash("sha256")
      .update(sameVersionDifferentBody)
      .digest("hex");
    await writeFile(journalPath, `${JSON.stringify(journal, null, 2)}\n`);

    const drift = await checkSkillDrift(
      cursorLayout(cursorDir),
      runtime(discovery),
    );

    expect(drift?.state).not.toBe("in_sync");
    expect(drift?.state).toBe("stale");
  }, 30_000);

  it("refuses to overwrite a modified skill without an explicit approval", async () => {
    // Sync is user-initiated and goes through preview / apply. A silent
    // rewrite of a hand-edited skill is the class of behaviour that gets an
    // app uninstalled on sight.
    const cursorDir = await fixtureDir();
    const discovery = path.join(cursorDir, "broker.json");
    await writeFile(discovery, "{}\n");
    await applyCursorInstall(
      await planCursorInstall(options(cursorDir, discovery)),
    );
    const skillPath = path.join(
      cursorDir,
      "skills",
      "memhub-recall",
      "SKILL.md",
    );
    const before = `${await readFile(skillPath, "utf8")}\n\n## keep me\n`;
    await writeFile(skillPath, before);

    const { planSkillSync } = await import("./skill-drift.js");
    const sync = await planSkillSync(
      cursorLayout(cursorDir),
      runtime(discovery),
    );
    expect(sync?.drift.state).toBe("modified");
    expect(sync?.requiresConfirmation).toBe(true);

    // Applying without the conflict acknowledgement must leave the file alone.
    const { applyRecallInstall } = await import("./recall-installer.js");
    if (sync === null) throw new Error("expected a sync plan");
    await expect(applyRecallInstall(sync.plan)).rejects.toThrow(
      /modified|确认|confirmation|conflict/i,
    );
    expect(await readFile(skillPath, "utf8")).toBe(before);
  }, 30_000);

  it("health check reports link and skill side by side", async () => {
    // Different failure modes: the link can be fine while the call rules are
    // stale, and the reverse. One combined check so the detail page does not
    // have to ask twice.
    const cursorDir = await fixtureDir();
    const discovery = path.join(cursorDir, "broker.json");
    await writeFile(discovery, "{}\n");
    await applyCursorInstall(
      await planCursorInstall(options(cursorDir, discovery)),
    );

    const health = await hostHealthCheck(
      cursorLayout(cursorDir),
      runtime(discovery),
    );

    expect(health.link).toBeDefined();
    expect(health.skill?.state).toBe("in_sync");
    // The two are siblings, not nested under "installed".
    expect(Object.keys(health).sort()).toEqual(
      expect.arrayContaining(["link", "skill"]),
    );
  }, 30_000);

  it("marks stale when a second managed capture command is accumulated", async () => {
    const cursorDir = await fixtureDir();
    const discovery = path.join(cursorDir, "broker.json");
    await writeFile(discovery, "{}\n");
    await applyCursorInstall(
      await planCursorInstall(options(cursorDir, discovery)),
    );
    const hooksPath = path.join(cursorDir, "hooks.json");
    const hooks = JSON.parse(await readFile(hooksPath, "utf8")) as {
      hooks: { beforeSubmitPrompt: Array<{ command: string }> };
    };
    hooks.hooks.beforeSubmitPrompt.push({
      command:
        "sh -c 'ONE_MEMORY_DATA_DIR='\\''/old-data'\\'' '\\''/old/core/one-memory'\\'' capture cursor >/dev/null 2>&1; exit 0'",
    });
    await writeFile(hooksPath, `${JSON.stringify(hooks, null, 2)}\n`);

    await expect(
      checkSkillDrift(cursorLayout(cursorDir), runtime(discovery)),
    ).resolves.toMatchObject({
      state: "stale",
      detail: "more than one managed capture command is installed",
    });
  }, 30_000);

  it("marks stale an MCP entry written before the tool set was stamped, and sync stamps it", async () => {
    // Our entry is otherwise identical across upgrades, so hosts that cache a
    // tool list by config fingerprint keep the removed tools until it changes.
    const cursorDir = await fixtureDir();
    const discovery = path.join(cursorDir, "broker.json");
    await writeFile(discovery, "{}\n");
    await applyCursorInstall(
      await planCursorInstall(options(cursorDir, discovery)),
    );
    const layout = cursorLayout(cursorDir);
    const configPath = layout.configPath as string;
    const config = JSON.parse(await readFile(configPath, "utf8")) as {
      mcpServers: Record<string, { env: Record<string, string> }>;
    };
    expect(config.mcpServers["one-memory"].env[MCP_TOOLSET_KEY]).toBe(
      MCP_TOOLSET_VERSION,
    );
    // What an earlier release wrote and journaled: the same entry, no stamp.
    delete config.mcpServers["one-memory"].env[MCP_TOOLSET_KEY];
    await writeFile(configPath, `${JSON.stringify(config, null, 2)}\n`);
    const journal = JSON.parse(await readFile(layout.journalPath, "utf8"));
    journal.installed_server_hash = createHash("sha256")
      .update(JSON.stringify(config.mcpServers["one-memory"]))
      .digest("hex");
    await writeFile(layout.journalPath, `${JSON.stringify(journal)}\n`);

    // Still intact as installed, so the link is not reported broken...
    await expect(verifyRecallInstall(layout)).resolves.toMatchObject({
      hostId: "cursor",
    });
    // ...but stale against this release, and safe to sync without asking.
    await expect(
      checkSkillDrift(layout, runtime(discovery)),
    ).resolves.toMatchObject({
      state: "stale",
      detail: "the MCP entry predates the tool set this MemHub serves",
    });
    const sync = await planSkillSync(layout, runtime(discovery));
    expect(sync?.requiresConfirmation).toBe(false);
    if (sync === null) throw new Error("expected a stale entry sync");
    await applyRecallInstall(sync.plan);

    const synced = JSON.parse(await readFile(configPath, "utf8")) as {
      mcpServers: Record<string, { env: Record<string, string> }>;
    };
    expect(synced.mcpServers["one-memory"].env[MCP_TOOLSET_KEY]).toBe(
      MCP_TOOLSET_VERSION,
    );
    await expect(
      checkSkillDrift(layout, runtime(discovery)),
    ).resolves.toMatchObject({ state: "in_sync" });
  }, 30_000);

  it("requires recall for dependent answers, not on every unrelated turn", async () => {
    const cursorDir = await fixtureDir();
    const discovery = path.join(cursorDir, "broker.json");
    await writeFile(discovery, "{}\n");
    await applyCursorInstall(
      await planCursorInstall(options(cursorDir, discovery)),
    );

    const skill = await readFile(
      path.join(cursorDir, "skills", "memhub-recall", "SKILL.md"),
      "utf8",
    );

    expect(skill).toMatch(/必须先调|must (?:first )?call|must recall/i);
    expect(skill).toContain("When the answer would depend on");
    expect(skill).toContain("Skip recall when prior context is irrelevant");
  }, 30_000);
});

async function fixtureDir(): Promise<string> {
  const root = await mkdtemp(path.join(os.tmpdir(), "om-skill-drift-"));
  const cursorDir = path.join(root, ".cursor");
  await mkdir(cursorDir);
  await writeFile(
    path.join(cursorDir, "mcp.json"),
    `${JSON.stringify({ mcpServers: {} }, null, 2)}\n`,
  );
  return cursorDir;
}

function runtime(discoveryPath: string) {
  return {
    runtimeCommand: process.execPath,
    mcpEntryPath: path.join(process.cwd(), "dist", "mcp.cjs"),
    discoveryPath,
  };
}

function options(cursorDir: string, discoveryPath: string) {
  return { cursorDir, ...runtime(discoveryPath) };
}
