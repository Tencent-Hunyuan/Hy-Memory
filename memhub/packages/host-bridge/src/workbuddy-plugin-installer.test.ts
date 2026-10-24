import { createHash } from "node:crypto";
import {
  mkdir,
  mkdtemp,
  readdir,
  readFile,
  rm,
  stat,
  writeFile,
} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { failOpenHookCommand } from "./capture-hooks.js";
import {
  applyRecallInstall,
  applyRecallUninstall,
  mcpConfigPath,
  planRecallInstall,
  planRecallUninstall,
  planSkillSync,
  previewRecallInstall,
  previewRecallUninstall,
  renderSkillContent,
  rollbackRecallInstall,
  SKILL_VERSION,
  verifyRecallInstall,
  workbuddyLayout,
  workbuddyScatteredLayout,
} from "./recall-installer.js";
import { checkSkillDrift } from "./skill-drift.js";

describe("WorkBuddy plugin recall installer", () => {
  it("ships approved visible receipts in both WorkBuddy Skill copies", async () => {
    const { layout, plugin } = await fixture();
    const plan = await planRecallInstall(layout, runtime());
    await previewRecallInstall(plan);
    expect(await missing(plugin.installRoot)).toBe(true);
    expect(await missing(plugin.marketplaceRoot)).toBe(true);
    await applyRecallInstall(plan);
    await verifyRecallInstall(layout);
    for (const root of [
      plugin.installRoot,
      path.join(plugin.marketplaceRoot, "plugins", plugin.pluginName),
    ]) {
      const skill = await readFile(
        path.join(root, "skills", plugin.skillName, "SKILL.md"),
        "utf8",
      );
      expect(skill).toBe(renderSkillContent("mcp", runtime()));
      expect(skill).toContain(`skill-version=${SKILL_VERSION}`);
      expect(skill).toContain("成功使用 MemHub 回忆到相关记忆。");
      expect(skill).toContain("已完成MemHub的记忆查询。");
      expect(skill).toContain(
        "已从 MemHub 获取部分相关记忆，以下将补充这些信息继续。",
      );
      expect(skill).toContain("MemHub 暂时不可用，我将先依据当前对话继续。");
      expect(skill).toContain(
        "Never describe an error, denial, partial or unknown result as a completed successful lookup",
      );
      expect(skill).not.toContain("say this query returned no matches");
    }
  });

  it.each([7, 8])(
    "upgrades both journaled v%s Skill copies without changing WorkBuddy MCP configuration",
    async (version) => {
      const { layout, plugin, hostDir } = await fixture({ populated: true });
      await applyRecallInstall(await planRecallInstall(layout, runtime()));
      const registries = await readRegistries(hostDir);
      const manifestPath = path.join(
        plugin.installRoot,
        ".codebuddy-plugin",
        "plugin.json",
      );
      const manifest = await readFile(manifestPath, "utf8");
      const journal = JSON.parse(await readFile(layout.journalPath, "utf8"));
      // Simulate an intact journaled older install, not a user-edited Skill.
      let changed = 0;
      for (const file of journal.plugin_files as Array<{
        path: string;
        hash: string;
      }>) {
        if (!file.path.endsWith("SKILL.md")) continue;
        const current = await readFile(file.path, "utf8");
        const old = current
          .replace(`skill-version=${SKILL_VERSION}`, `skill-version=${version}`)
          .replace(
            /## Result presentation[\s\S]*?(?=\n- Leave the scope alone)/,
            "",
          );
        expect(old).not.toBe(current);
        await writeFile(file.path, old);
        file.hash = createHash("sha256").update(old).digest("hex");
        journal.installed_skill_hash = file.hash;
        changed++;
      }
      expect(changed).toBe(2);
      await writeFile(layout.journalPath, JSON.stringify(journal));
      await expect(checkSkillDrift(layout, runtime())).resolves.toMatchObject({
        state: "stale",
        installedVersion: version,
      });
      const sync = await planSkillSync(layout, runtime());
      if (!sync) throw new Error("expected WorkBuddy Skill upgrade");
      await applyRecallInstall(sync.plan);
      await verifyRecallInstall(layout);
      await expect(checkSkillDrift(layout, runtime())).resolves.toMatchObject({
        state: "in_sync",
        installedVersion: SKILL_VERSION,
      });
      for (const root of [
        plugin.installRoot,
        path.join(plugin.marketplaceRoot, "plugins", plugin.pluginName),
      ]) {
        expect(
          await readFile(
            path.join(root, "skills", plugin.skillName, "SKILL.md"),
            "utf8",
          ),
        ).toBe(renderSkillContent("mcp", runtime()));
      }
      expect(await readFile(manifestPath, "utf8")).toBe(manifest);
      expect(await readRegistries(hostDir)).toEqual(registries);
    },
  );

  it("moves a pre-rename plugin's skill to memhub-recall on sync", async () => {
    const { layout, plugin } = await fixture({ populated: true });
    await applyRecallInstall(await planRecallInstall(layout, runtime()));
    const journal = JSON.parse(await readFile(layout.journalPath, "utf8"));
    const digest = (text: string) =>
      createHash("sha256").update(text).digest("hex");
    // Rebuild what the previous release left: the same tree, with the skill
    // directory named after the plugin and an older template inside it.
    for (const file of journal.plugin_files as Array<{
      path: string;
      hash: string;
    }>) {
      const current = await readFile(file.path, "utf8");
      if (file.path.endsWith("SKILL.md")) {
        const old = current.replace(
          `skill-version=${SKILL_VERSION}`,
          "skill-version=14",
        );
        const legacy = file.path.replace(
          `${path.sep}memhub-recall${path.sep}`,
          `${path.sep}one-memory-recall${path.sep}`,
        );
        await rm(path.dirname(file.path), { recursive: true });
        await mkdir(path.dirname(legacy), { recursive: true });
        await writeFile(legacy, old);
        file.path = legacy;
        file.hash = digest(old);
        journal.installed_skill_hash = file.hash;
      } else if (file.path.endsWith("plugin.json")) {
        const old = current.replace(
          "./skills/memhub-recall",
          "./skills/one-memory-recall",
        );
        await writeFile(file.path, old);
        file.hash = digest(old);
      }
    }
    await writeFile(layout.journalPath, JSON.stringify(journal));

    await expect(checkSkillDrift(layout, runtime())).resolves.toMatchObject({
      state: "stale",
      installedVersion: 14,
    });
    const sync = await planSkillSync(layout, runtime());
    if (!sync) throw new Error("expected the pre-rename skill to sync");
    await applyRecallInstall(sync.plan);
    await verifyRecallInstall(layout);

    for (const root of [
      plugin.installRoot,
      path.join(plugin.marketplaceRoot, "plugins", plugin.pluginName),
    ]) {
      expect(
        await readFile(
          path.join(root, "skills", "memhub-recall", "SKILL.md"),
          "utf8",
        ),
      ).toMatch(/^---\nname: memhub-recall\n/);
      expect(
        await missing(path.join(root, "skills", "one-memory-recall")),
      ).toBe(true);
    }
  });

  it("writes a self-contained plugin and registers it in all three host files", async () => {
    const { layout, plugin, hostDir } = await fixture();

    const plan = await planRecallInstall(layout, runtime());
    await applyRecallInstall(plan);
    await verifyRecallInstall(layout);

    expect(plan.actions).toEqual(["create-plugin"]);
    // The marketplace copy is what skills load from and the cache copy is what
    // MCP servers load from, so both have to be there for the install to mean
    // anything. Neither is a copy of the other by accident.
    const manifest = JSON.parse(
      await readFile(
        path.join(plugin.installRoot, ".codebuddy-plugin", "plugin.json"),
        "utf8",
      ),
    );
    expect(manifest.mcpServers["one-memory"].command).toBe(
      runtime().runtimeCommand,
    );
    expect(manifest.skills).toEqual(["./skills/memhub-recall"]);
    expect(manifest.hooks).toBe("./hooks/hooks.json");
    const hooks = JSON.parse(
      await readFile(
        path.join(plugin.installRoot, "hooks", "hooks.json"),
        "utf8",
      ),
    );
    expect(Object.keys(hooks.hooks).sort()).toEqual([
      "SessionStart",
      "Stop",
      "UserPromptSubmit",
    ]);
    const core = "/opt/one-memory/core/one-memory";
    expect(hooks.hooks.SessionStart[0].hooks[0].command).toBe(
      failOpenHookCommand(core, "boundary", "context"),
    );
    expect(hooks.hooks.UserPromptSubmit[0].hooks[0].command).toBe(
      failOpenHookCommand(core, "capture workbuddy", "silent"),
    );
    expect(hooks.hooks.UserPromptSubmit[1].hooks[0].command).toBe(
      failOpenHookCommand(core, "boundary", "context"),
    );
    expect(hooks.hooks.Stop[0].hooks[0].command).toBe(
      failOpenHookCommand(core, "capture workbuddy", "silent"),
    );
    expect(JSON.stringify(hooks)).not.toContain(runtime().runtimeCommand);
    expect(JSON.stringify(hooks)).not.toContain("continueRequest");
    expect(
      await readFile(
        path.join(
          plugin.marketplaceRoot,
          "plugins",
          "one-memory-recall",
          "skills",
          "memhub-recall",
          "SKILL.md",
        ),
        "utf8",
      ),
    ).toContain("MemHub_recall");
    expect(
      JSON.parse(
        await readFile(
          path.join(
            plugin.marketplaceRoot,
            ".codebuddy-plugin",
            "marketplace.json",
          ),
          "utf8",
        ),
      ).plugins,
    ).toEqual([
      {
        name: "one-memory-recall",
        source: "./plugins/one-memory-recall",
        // `codebuddy plugin validate` warns without this.
        version: "1.0.0",
        description: expect.any(String),
      },
    ]);
    const registries = await readRegistries(hostDir);
    expect(registries.marketplaces["one-memory"].installLocation).toBe(
      plugin.marketplaceRoot,
    );
    expect(
      registries.installed.plugins["one-memory-recall@one-memory"],
    ).toEqual([
      {
        scope: "user",
        installPath: plugin.installRoot,
        version: "1.0.0",
      },
    ]);
    // The gate that decides whether any of the above is read: WorkBuddy builds
    // its server set from the enabled ids, and ours is not one of the five it
    // enables by default.
    expect(
      registries.settings.enabledPlugins["one-memory-recall@one-memory"],
    ).toBe(true);
  });

  it("adds one key to registries that already describe the user's own plugins", async () => {
    const { layout, hostDir } = await fixture({ populated: true });
    const before = await readRegistries(hostDir);

    await applyRecallInstall(await planRecallInstall(layout, runtime()));

    const after = await readRegistries(hostDir);
    expect(Object.keys(after.installed.plugins)).toHaveLength(
      Object.keys(before.installed.plugins).length + 1,
    );
    expect(Object.keys(after.marketplaces)).toHaveLength(
      Object.keys(before.marketplaces).length + 1,
    );
    // Every key of the user's survives with the value it had, including the
    // schema version beside the plugin map and the settings we do not read.
    for (const [id, records] of Object.entries(before.installed.plugins)) {
      expect(after.installed.plugins[id]).toEqual(records);
    }
    expect(after.installed.version).toBe(2);
    expect(after.marketplaces.experts).toEqual(before.marketplaces.experts);
    expect(after.settings.sandbox).toEqual(before.settings.sandbox);
    expect(
      Object.keys(after.settings.enabledPlugins as Record<string, boolean>),
    ).toHaveLength(
      Object.keys(before.settings.enabledPlugins as Record<string, boolean>)
        .length + 1,
    );
  });

  it("gives the registries back byte for byte when uninstalled", async () => {
    const { layout, hostDir } = await fixture({ populated: true });
    const paths = registryPaths(hostDir);
    const before = await Promise.all(
      paths.map((target) => readFile(target, "utf8")),
    );

    await applyRecallInstall(await planRecallInstall(layout, runtime()));
    await rollbackRecallInstall(layout);

    // Byte for byte, not merely equivalent: WorkBuddy 5.5.0 writes these three
    // files with two-space indentation and no closing newline, and one of them
    // holds every plugin the user has. Re-serialising it in our own style would
    // show up as a whole-file change in their next diff.
    expect(
      await Promise.all(paths.map((target) => readFile(target, "utf8"))),
    ).toEqual(before);
  });

  it("preserves minified registry bytes across reinstall and rollback", async () => {
    const { layout, hostDir } = await fixture({ populated: true });
    const paths = registryPaths(hostDir);
    for (const target of paths) {
      await writeFile(
        target,
        JSON.stringify(JSON.parse(await readFile(target, "utf8"))),
      );
    }
    const before = await Promise.all(
      paths.map((target) => readFile(target, "utf8")),
    );

    await applyRecallInstall(await planRecallInstall(layout, runtime()));
    await applyRecallInstall(await planRecallInstall(layout, runtime()));
    await rollbackRecallInstall(layout);

    expect(
      await Promise.all(paths.map((target) => readFile(target, "utf8"))),
    ).toEqual(before);
  });

  it("removes its own registry key while keeping a change the host made after", async () => {
    const { layout, hostDir, plugin } = await fixture({ populated: true });
    const settingsPath = path.join(hostDir, "settings.json");
    await applyRecallInstall(await planRecallInstall(layout, runtime()));
    const current = JSON.parse(await readFile(settingsPath, "utf8"));
    current.userChangeAfterInstall = true;
    await writeFile(settingsPath, JSON.stringify(current));

    // The whole file moving is not the key we own moving. Uninstall used to
    // refuse here, which is how a host that rewrites its own registries left
    // an install that could not be taken off at all (issue #29).
    await applyRecallUninstall(await planRecallUninstall(layout));

    const after = JSON.parse(await readFile(settingsPath, "utf8"));
    expect(after).toMatchObject({ userChangeAfterInstall: true });
    expect(after.enabledPlugins).not.toHaveProperty(
      `${plugin.pluginName}@${plugin.marketplaceName}`,
    );
  });

  it("uninstalls after the host re-formats a registry it shares", async () => {
    const { layout, hostDir } = await fixture({ populated: true });
    await applyRecallInstall(await planRecallInstall(layout, runtime()));
    for (const target of registryPaths(hostDir)) {
      const current = JSON.parse(await readFile(target, "utf8"));
      await writeFile(target, `${JSON.stringify(current)}\n`);
    }

    await applyRecallUninstall(await planRecallUninstall(layout));

    for (const target of registryPaths(hostDir)) {
      expect(JSON.parse(await readFile(target, "utf8"))).not.toBeNull();
    }
  });

  it("asks for review rather than removing a managed entry somebody changed", async () => {
    const { layout, hostDir, plugin } = await fixture({ populated: true });
    const settingsPath = path.join(hostDir, "settings.json");
    await applyRecallInstall(await planRecallInstall(layout, runtime()));
    const current = JSON.parse(await readFile(settingsPath, "utf8"));
    current.enabledPlugins[`${plugin.pluginName}@${plugin.marketplaceName}`] =
      false;
    await writeFile(settingsPath, JSON.stringify(current));

    const plan = await planRecallUninstall(layout);
    expect(plan.review?.map((finding) => finding.path)).toContain(settingsPath);
    await expect(applyRecallUninstall(plan)).rejects.toThrow(
      "host_uninstall_needs_review",
    );
    expect(JSON.parse(await readFile(settingsPath, "utf8"))).toMatchObject({
      enabledPlugins: {
        [`${plugin.pluginName}@${plugin.marketplaceName}`]: false,
      },
    });
  });

  it("migrates a v1 journal whose whole-file hash no longer matches", async () => {
    const { layout, hostDir } = await fixture({ populated: true });
    await applyRecallInstall(await planRecallInstall(layout, runtime()));
    // A journal from the build that compared whole files: no managed digest,
    // and a hash that stopped matching the moment the host rewrote anything.
    const journal = JSON.parse(await readFile(layout.journalPath, "utf8"));
    journal.plugin_registries = journal.plugin_registries.map(
      (entry: {
        installed_hash: string;
        installed_managed?: string | null;
      }) => {
        const { installed_managed: _dropped, ...legacy } = entry;
        return { ...legacy, installed_hash: "not-the-file-any-more" };
      },
    );
    await writeFile(layout.journalPath, JSON.stringify(journal));

    await applyRecallUninstall(await planRecallUninstall(layout));

    const settings = JSON.parse(
      await readFile(path.join(hostDir, "settings.json"), "utf8"),
    );
    expect(settings.enabledPlugins).not.toHaveProperty(
      "one-memory-recall@one-memory",
    );
  });

  it("keeps a registry it created once the host has put something else in it", async () => {
    const { layout, hostDir } = await fixture();
    const settingsPath = path.join(hostDir, "settings.json");
    await applyRecallInstall(await planRecallInstall(layout, runtime()));
    const current = JSON.parse(await readFile(settingsPath, "utf8"));
    current.sandbox = { extraAllowWrite: ["~/.tencent-cloudq"] };
    await writeFile(settingsPath, JSON.stringify(current));

    await applyRecallUninstall(await planRecallUninstall(layout));

    // The file did not exist before we installed, but it is no longer only
    // ours, so the key goes and the file stays.
    const after = JSON.parse(await readFile(settingsPath, "utf8"));
    expect(after.sandbox).toEqual({ extraAllowWrite: ["~/.tencent-cloudq"] });
    expect(after.enabledPlugins).toEqual({});
  });

  it("installs over a registry change made after the plan was shown", async () => {
    const { layout, hostDir, plugin } = await fixture({ populated: true });
    const settingsPath = path.join(hostDir, "settings.json");
    const plan = await planRecallInstall(layout, runtime());
    const current = JSON.parse(await readFile(settingsPath, "utf8"));
    current.anotherPluginEnabled = true;
    await writeFile(settingsPath, JSON.stringify(current));

    await applyRecallInstall(plan);

    const after = JSON.parse(await readFile(settingsPath, "utf8"));
    expect(after.anotherPluginEnabled).toBe(true);
    expect(after.enabledPlugins).toHaveProperty(
      `${plugin.pluginName}@${plugin.marketplaceName}`,
      true,
    );
  });

  it("leaves no directory of its own behind, empty or otherwise", async () => {
    const { layout, hostDir, plugin } = await fixture({ populated: true });
    const marketplaces = path.dirname(plugin.marketplaceRoot);
    const cache = path.dirname(plugin.cacheRoot);
    await mkdir(path.join(marketplaces, "experts"), { recursive: true });
    await mkdir(path.join(cache, "codebuddy-plugins-official"), {
      recursive: true,
    });

    await applyRecallInstall(await planRecallInstall(layout, runtime()));
    await rollbackRecallInstall(layout);

    // An empty `marketplaces/one-memory` would still be MemHub listed in
    // the host's plugin sources, so "uninstalled" has to mean the directory is
    // gone rather than emptied. The user's neighbours are untouched.
    expect(await readdir(marketplaces)).toEqual(["experts"]);
    expect(await readdir(cache)).toEqual(["codebuddy-plugins-official"]);
    expect(await missing(path.join(hostDir, ".one-memory"))).toBe(true);
  });

  it("refuses a marketplace of the same name that it did not write", async () => {
    const { layout, hostDir, plugin } = await fixture({ populated: true });
    const marketplacesPath = path.join(
      hostDir,
      "plugins",
      "known_marketplaces.json",
    );
    const theirs = {
      type: "zip",
      source: { source: "zip", path: "https://example.invalid/one-memory.zip" },
      installLocation: "/somewhere/else",
    };
    await writeFile(
      marketplacesPath,
      JSON.stringify({ "one-memory": theirs }, null, 2),
    );

    // The message has to name the marketplace key, not the plugin id: this
    // file keys ours by marketplace, and naming the plugin would send the user
    // looking for a key the file does not contain.
    await expect(planRecallInstall(layout, runtime())).rejects.toThrow(
      /entry at "one-memory" in known_marketplaces\.json that MemHub did not write/,
    );
    // Refused means nothing was touched, including the tree we would have
    // written first.
    expect(JSON.parse(await readFile(marketplacesPath, "utf8"))).toEqual({
      "one-memory": theirs,
    });
    expect(await missing(plugin.marketplaceRoot)).toBe(true);
  });

  it("takes over that entry only when told to, and puts it back afterwards", async () => {
    const { layout, hostDir } = await fixture();
    const marketplacesPath = path.join(
      hostDir,
      "plugins",
      "known_marketplaces.json",
    );
    const theirs = { type: "zip", installLocation: "/somewhere/else" };
    const original = JSON.stringify({ "one-memory": theirs }, null, 2);
    await mkdir(path.dirname(marketplacesPath), { recursive: true });
    await writeFile(marketplacesPath, original);

    const plan = await planRecallInstall(layout, runtime(), {
      takeover: true,
    });
    await applyRecallInstall(plan);

    expect(plan.actions).toContain("takeover-plugin");
    await rollbackRecallInstall(layout);
    // Their entry is the only copy of itself once we have overwritten it, so
    // the journal has to carry it — a takeover that cannot be undone is not a
    // takeover, it is a deletion.
    expect(await readFile(marketplacesPath, "utf8")).toBe(original);
  });

  it("keeps describing the host as it was before us when installed twice", async () => {
    const { layout, hostDir } = await fixture();
    const settingsPath = path.join(hostDir, "settings.json");
    // The user had turned our plugin off by hand before we ever installed.
    await writeFile(
      settingsPath,
      JSON.stringify(
        { enabledPlugins: { "one-memory-recall@one-memory": false } },
        null,
        2,
      ),
    );

    await applyRecallInstall(await planRecallInstall(layout, runtime()));
    const second = await planRecallInstall(layout, runtime());
    await applyRecallInstall(second);
    await rollbackRecallInstall(layout);

    expect(second.actions).toContain("update-plugin");
    // Reinstalling must not make MemHub its own previous state: uninstall
    // would then restore the enabled flag we set rather than the user's choice.
    expect(
      JSON.parse(await readFile(settingsPath, "utf8")).enabledPlugins,
    ).toEqual({ "one-memory-recall@one-memory": false });
  });

  it("shows every file it would write, and writes none of them", async () => {
    const { layout, hostDir, plugin } = await fixture({ populated: true });
    const installedPath = path.join(
      hostDir,
      "plugins",
      "installed_plugins.json",
    );
    const before = await readFile(installedPath, "utf8");

    const preview = await previewRecallInstall(
      await planRecallInstall(layout, runtime()),
    );

    expect(await readFile(installedPath, "utf8")).toBe(before);
    expect(await missing(plugin.marketplaceRoot)).toBe(true);
    const paths = preview.files.map((file) => file.path);
    expect(paths).toContain(installedPath);
    expect(paths).toContain(
      path.join(plugin.installRoot, ".codebuddy-plugin", "plugin.json"),
    );
    // A registry holding 49 other plugins is the reason previews exist: only the
    // added lines are shown, not the whole document.
    const registry = preview.files.find((file) => file.path === installedPath);
    expect(registry?.owner).toBe("host");
    expect(registry?.hunk?.added.join("\n")).toContain("one-memory-recall");
    expect(registry?.hunk?.added.length).toBeLessThan(20);
    expect(preview.supersedes).toBeNull();
  });

  it("reports the plugin tree as deleted rather than diffed against nothing", async () => {
    const { layout, hostDir, plugin } = await fixture({ populated: true });
    await applyRecallInstall(await planRecallInstall(layout, runtime()));

    const plan = await planRecallUninstall(layout);
    const preview = previewRecallUninstall(plan);

    const manifest = preview.files.find(
      (file) =>
        file.path ===
        path.join(plugin.installRoot, ".codebuddy-plugin", "plugin.json"),
    );
    expect(manifest?.deleted).toBe(true);
    expect(manifest?.hunk).toBeNull();
    // The registries are edited, not deleted: they were the host's before we
    // touched them and they stay the host's.
    const registry = preview.files.find(
      (file) =>
        file.path === path.join(hostDir, "plugins", "installed_plugins.json"),
    );
    expect(registry?.deleted).toBe(false);
    expect(registry?.hunk?.removed.join("\n")).toContain("one-memory-recall");
    await applyRecallUninstall(plan, preview.fingerprint);
  });

  it("fails verification when the host is no longer told the plugin is enabled", async () => {
    const { layout, hostDir } = await fixture();
    await applyRecallInstall(await planRecallInstall(layout, runtime()));
    const settingsPath = path.join(hostDir, "settings.json");

    await writeFile(
      settingsPath,
      JSON.stringify({ enabledPlugins: {} }, null, 2),
    );

    // Every file of the install is still perfect here. Without this check the
    // installer would report success on a plugin the host will never load,
    // which is the failure mode that has already cost this project a day.
    await expect(verifyRecallInstall(layout)).rejects.toThrow(
      /not registered in settings\.json/,
    );
  });

  it("fails verification when a file of the plugin has been edited", async () => {
    const { layout, plugin } = await fixture();
    await applyRecallInstall(await planRecallInstall(layout, runtime()));

    await writeFile(
      path.join(plugin.installRoot, "skills", "memhub-recall", "SKILL.md"),
      "someone else's instructions\n",
    );

    await expect(verifyRecallInstall(layout)).rejects.toThrow(
      /verification failed/,
    );
  });

  it("refuses to delete a plugin tree that has been edited since installing", async () => {
    const { layout, plugin } = await fixture();
    await applyRecallInstall(await planRecallInstall(layout, runtime()));
    const skillPath = path.join(
      plugin.marketplaceRoot,
      "plugins",
      "one-memory-recall",
      "skills",
      "memhub-recall",
      "SKILL.md",
    );
    await writeFile(skillPath, "notes the user added\n");

    // Uninstall removes these directories whole, so an edit inside one has to
    // stop it rather than be swept up by it.
    await expect(planRecallUninstall(layout)).rejects.toThrow(
      /changed after installation/,
    );
    expect(await readFile(skillPath, "utf8")).toBe("notes the user added\n");
  });

  it("takes down an install left by the scattered build it replaces", async () => {
    const { layout, hostDir } = await fixture({ populated: true });
    const scattered = workbuddyScatteredLayout(hostDir);
    const originalMcp = `${JSON.stringify({ mcpServers: {} }, null, 2)}\n`;
    await writeFile(mcpConfigPath(scattered), originalMcp);
    await applyRecallInstall(await planRecallInstall(scattered, runtime()));

    const plan = await planRecallInstall(layout, runtime());
    const preview = await previewRecallInstall(plan);
    await applyRecallInstall(plan, preview.fingerprint);
    await verifyRecallInstall(layout);

    expect(plan.actions).toContain("remove-superseded");
    // Leaving it would give the host the same server twice, from two places,
    // and only one of them would ever be removed again.
    expect(preview.supersedes?.files.map((file) => file.path)).toContain(
      mcpConfigPath(scattered),
    );
    expect(await readFile(mcpConfigPath(scattered), "utf8")).toBe(originalMcp);
    expect(await missing(String(scattered.skillPath))).toBe(true);
    expect(await missing(scattered.journalPath)).toBe(true);
  });

  it("installs the same MCP entry the other hosts get", async () => {
    const { layout, plugin } = await fixture();
    const scattered = workbuddyScatteredLayout(
      await mkdtemp(path.join(os.tmpdir(), "om-workbuddy-scattered-")),
    );
    await applyRecallInstall(await planRecallInstall(scattered, runtime()));

    await applyRecallInstall(await planRecallInstall(layout, runtime()));

    // Changing the plugin's carrier must not quietly change what recall is. The
    // manifest also accepts `${CODEBUDDY_PLUGIN_ROOT}`, which is not used: the
    // MCP entry point ships in the MemHub bundle, not in the plugin.
    const fromPlugin = JSON.parse(
      await readFile(
        path.join(plugin.installRoot, ".codebuddy-plugin", "plugin.json"),
        "utf8",
      ),
    ).mcpServers["one-memory"];
    const fromConfig = JSON.parse(
      await readFile(mcpConfigPath(scattered), "utf8"),
    ).mcpServers["one-memory"];
    expect(fromPlugin).toEqual(fromConfig);
  });
});

describe("WorkBuddy recall injection hooks", () => {
  it("refuses to install an injection hook whose runtime entry does not exist", async () => {
    const { layout, plugin } = await fixture();
    const runtime = await injectingRuntime();
    const missingRuntime = {
      ...runtime,
      runtimeCommand:
        "/opt/one-memory/missing/managed-runtime/current/bin/node",
    };

    await expect(planRecallInstall(layout, missingRuntime)).rejects.toThrow(
      /runtimeCommand does not exist/,
    );
    expect(await missing(plugin.marketplaceRoot)).toBe(true);
    expect(await missing(plugin.installRoot)).toBe(true);
  });

  it("refuses to install an injection hook whose recall command does not exist", async () => {
    const { layout, plugin } = await fixture();
    const runtime = await injectingRuntime();
    const missingRecall = {
      ...runtime,
      recallCliPath: "/opt/one-memory/missing/host-bridge/recall-cli.cjs",
    };

    await expect(planRecallInstall(layout, missingRecall)).rejects.toThrow(
      /recallCliPath does not exist/,
    );
    expect(await missing(plugin.marketplaceRoot)).toBe(true);
    expect(await missing(plugin.installRoot)).toBe(true);
  });

  it("gives each context event exactly one hook that speaks, carrying both memory and notices", async () => {
    const { layout, plugin } = await fixture({ populated: true });
    const runtime = await injectingRuntime();

    await applyRecallInstall(await planRecallInstall(layout, runtime));
    await verifyRecallInstall(layout);

    const hooks = JSON.parse(
      await readFile(
        path.join(plugin.installRoot, "hooks", "hooks.json"),
        "utf8",
      ),
    );
    const core = "/opt/one-memory/core/one-memory";
    const dataDir = runtime.dataDir;
    const capture = failOpenHookCommand(
      core,
      "capture workbuddy",
      "silent",
      dataDir,
    );
    for (const event of ["SessionStart", "UserPromptSubmit"]) {
      const speaking = speakingHooks(hooks.hooks[event], capture);
      expect(speaking, event).toHaveLength(1);
      expect(speaking[0].command).toContain("--inject workbuddy");
      expect(speaking[0].command).toContain(
        `ONE_MEMORY_BOUNDARY_COMMAND='\\''${core}'\\''`,
      );
      expect(speaking[0].timeout).toBe(50);
    }
    // The host runs every hook on an event in parallel and blocks the send
    // when any of them outlives its timeout, so capture rides inside the one
    // hook that already keeps its own deadline instead of being a second one.
    const prompt = (hooks.hooks.UserPromptSubmit as HookEntry[]).flatMap(
      (entry) => entry.hooks,
    );
    expect(prompt).toHaveLength(1);
    expect(prompt[0].command).toContain(
      `ONE_MEMORY_CAPTURE_COMMAND='\\''${core}'\\''`,
    );
    // The entry's own stdout is the hook's: nothing holds it back to reprint.
    expect(prompt[0].command).not.toContain("$(");
    expect(
      (hooks.hooks.Stop as HookEntry[]).flatMap((entry) => entry.hooks),
    ).toEqual([expect.objectContaining({ command: capture })]);
    // Every SessionStart source reaches it: the notice is due on all of them,
    // recall only after a compaction, and that choice is the hook's to make.
    expect(
      hooks.hooks.SessionStart.every(
        (entry: { matcher?: string }) => entry.matcher === undefined,
      ),
    ).toBe(true);
    const commands = (Object.values(hooks.hooks) as HookEntry[][])
      .flat()
      .flatMap((entry) => entry.hooks)
      .map((hook) => hook.command);
    expect(
      commands.filter((command) => command.includes('{"continue": true}')),
    ).toEqual([]);
    expect(hooks.hooks.Stop).toEqual([
      { hooks: [{ type: "command", command: capture, timeout: 10 }] },
    ]);
    // Both copies the host reads carry the same hook.
    expect(
      await readFile(
        path.join(
          plugin.marketplaceRoot,
          "plugins",
          plugin.pluginName,
          "hooks",
          "hooks.json",
        ),
        "utf8",
      ),
    ).toBe(
      await readFile(
        path.join(plugin.installRoot, "hooks", "hooks.json"),
        "utf8",
      ),
    );
  });

  it("leaves no injection hook behind when uninstalled", async () => {
    const { layout, hostDir, plugin } = await fixture({ populated: true });
    const runtime = await injectingRuntime();
    const paths = registryPaths(hostDir);
    const before = await Promise.all(
      paths.map((target) => readFile(target, "utf8")),
    );
    await applyRecallInstall(await planRecallInstall(layout, runtime));

    const plan = await planRecallUninstall(layout);
    await applyRecallUninstall(plan, previewRecallUninstall(plan).fingerprint);

    expect(await missing(plugin.installRoot)).toBe(true);
    expect(await missing(plugin.marketplaceRoot)).toBe(true);
    expect(
      await Promise.all(paths.map((target) => readFile(target, "utf8"))),
    ).toEqual(before);
  });

  it("brings an install from before injection up to date through the stale sync", async () => {
    const { layout, plugin } = await fixture({ populated: true });
    const runtime = await injectingRuntime();
    const { recallCliPath: _cli, ...older } = runtime;
    await applyRecallInstall(await planRecallInstall(layout, older));
    const hooksPath = path.join(plugin.installRoot, "hooks", "hooks.json");
    expect(await readFile(hooksPath, "utf8")).not.toContain("--inject");

    await expect(checkSkillDrift(layout, runtime)).resolves.toMatchObject({
      state: "stale",
    });
    const sync = await planSkillSync(layout, runtime);
    if (!sync) throw new Error("expected the hooks to sync");
    expect(sync.requiresConfirmation).toBe(false);
    await applyRecallInstall(sync.plan);
    await verifyRecallInstall(layout);

    expect(await readFile(hooksPath, "utf8")).toContain("--inject workbuddy");
    await expect(checkSkillDrift(layout, runtime)).resolves.toMatchObject({
      state: "in_sync",
    });
  });

  it("keeps one speaking boundary hook, silent on failure, when the runtime ships no recall command", async () => {
    const { layout, plugin } = await fixture({ populated: true });
    const runtime = await injectingRuntime();
    const { recallCliPath: _cli, ...older } = runtime;
    await applyRecallInstall(await planRecallInstall(layout, older));

    const hooks = JSON.parse(
      await readFile(
        path.join(plugin.installRoot, "hooks", "hooks.json"),
        "utf8",
      ),
    );
    const capture = failOpenHookCommand(
      "/opt/one-memory/core/one-memory",
      "capture workbuddy",
      "silent",
      older.dataDir,
    );
    for (const event of ["SessionStart", "UserPromptSubmit"]) {
      const speaking = speakingHooks(hooks.hooks[event], capture);
      expect(speaking, event).toHaveLength(1);
      expect(speaking[0].command).toContain(" boundary");
      expect(speaking[0].command).not.toContain('{"continue": true}');
    }
  });

  it("moves an install with two speaking hooks per event onto the single one", async () => {
    const { layout, plugin } = await fixture({ populated: true });
    const runtime = await injectingRuntime();
    await applyRecallInstall(await planRecallInstall(layout, runtime));
    // The bytes 66d8f88 wrote, journaled as ours, as an install from then has.
    const legacy = legacyHooksFile(runtime);
    const copies = [
      path.join(plugin.installRoot, "hooks", "hooks.json"),
      path.join(
        plugin.marketplaceRoot,
        "plugins",
        plugin.pluginName,
        "hooks",
        "hooks.json",
      ),
    ];
    for (const copy of copies) await writeFile(copy, legacy);
    const journal = JSON.parse(await readFile(layout.journalPath, "utf8"));
    for (const file of journal.plugin_files) {
      if (copies.includes(file.path))
        file.hash = createHash("sha256").update(legacy).digest("hex");
    }
    await writeFile(layout.journalPath, JSON.stringify(journal));

    await expect(checkSkillDrift(layout, runtime)).resolves.toMatchObject({
      state: "stale",
    });
    const sync = await planSkillSync(layout, runtime);
    if (!sync) throw new Error("expected the hooks to sync");
    expect(sync.requiresConfirmation).toBe(false);
    await applyRecallInstall(sync.plan);
    await verifyRecallInstall(layout);

    const hooks = JSON.parse(await readFile(copies[0], "utf8"));
    const capture = failOpenHookCommand(
      "/opt/one-memory/core/one-memory",
      "capture workbuddy",
      "silent",
      runtime.dataDir,
    );
    for (const event of ["SessionStart", "UserPromptSubmit"])
      expect(speakingHooks(hooks.hooks[event], capture)).toHaveLength(1);
    await expect(checkSkillDrift(layout, runtime)).resolves.toMatchObject({
      state: "in_sync",
    });
  });

  it("does not silently overwrite a hooks file the user edited", async () => {
    const { layout, plugin } = await fixture({ populated: true });
    const runtime = await injectingRuntime();
    const { recallCliPath: _cli, ...older } = runtime;
    await applyRecallInstall(await planRecallInstall(layout, older));
    await writeFile(
      path.join(plugin.installRoot, "hooks", "hooks.json"),
      `${JSON.stringify({ hooks: {} }, null, 2)}\n`,
    );

    const sync = await planSkillSync(layout, runtime).catch(() => null);
    await expect(checkSkillDrift(layout, runtime)).resolves.toMatchObject({
      state: "modified",
    });
    expect(sync?.requiresConfirmation ?? true).toBe(true);
  });

  it("recognizes a journal-less legacy hook as stale, not a user edit", async () => {
    const { layout, plugin } = await fixture({ populated: true });
    const runtime = await injectingRuntime();
    await applyRecallInstall(await planRecallInstall(layout, runtime));
    const hooksPath = path.join(plugin.installRoot, "hooks", "hooks.json");
    const legacy = legacyHooksFile(runtime);
    await writeFile(hooksPath, legacy);
    await rm(layout.journalPath, { force: true });

    await expect(checkSkillDrift(layout, runtime)).resolves.toMatchObject({
      state: "stale",
      detail: "plugin hooks are still ours, but have moved since install",
    });
  });
});

async function injectingRuntime() {
  const root = await mkdtemp(path.join(os.tmpdir(), "om-workbuddy-runtime-"));
  const runtimeCommand = path.join(root, "bin", "node");
  const recallCliPath = path.join(root, "host-bridge", "recall-cli.cjs");
  const discoveryPath = path.join(root, "integrations", "broker.json");
  for (const [target, contents] of [
    [runtimeCommand, "#!/bin/sh\nexit 0\n"],
    [recallCliPath, "// fixture recall cli\n"],
    [discoveryPath, '{"status":"running"}\n'],
  ] as const) {
    await mkdir(path.dirname(target), { recursive: true });
    await writeFile(target, contents, {
      mode: target === runtimeCommand ? 0o755 : 0o644,
    });
  }
  return {
    ...runtime(),
    runtimeCommand,
    recallCliPath,
    discoveryPath,
    credentialFile: "/opt/one-memory-data/integrations/workbuddy/credential",
    dataDir: root,
  };
}

interface HookEntry {
  matcher?: string;
  hooks: Array<{ type: string; command: string; timeout: number }>;
}

/** Hooks on an event whose stdout the host reads; capture prints nothing. */
function speakingHooks(entries: HookEntry[], capture: string) {
  return entries
    .flatMap((entry) => entry.hooks)
    .filter((hook) => hook.command !== capture);
}

/** hooks.json exactly as 66d8f88 wrote it: inject, then boundary, per event. */
function legacyHooksFile(
  runtime: Awaited<ReturnType<typeof injectingRuntime>>,
) {
  const quote = (text: string) => `'${text.replaceAll("'", `'\\''`)}'`;
  const core = "/opt/one-memory/core/one-memory";
  const data = `ONE_MEMORY_DATA_DIR=${quote(runtime.dataDir)} `;
  const capture = {
    hooks: [
      {
        type: "command",
        command: `sh -c ${quote(`${data}${quote(core)} capture workbuddy >/dev/null 2>&1; exit 0`)}`,
        timeout: 10,
      },
    ],
  };
  const boundary = {
    hooks: [
      {
        type: "command",
        command: `sh -c ${quote(`out=$(${data}${quote(core)} boundary 2>/dev/null); status=$?; if [ "$status" -eq 0 ]; then printf '%s\\n' "$out"; else printf '%s\\n' '{"continue": true}'; fi`)}`,
        timeout: 10,
      },
    ],
  };
  const environment = [
    'ELECTRON_RUN_AS_NODE="1"',
    `ONE_MEMORY_BROKER_DISCOVERY=${quote(runtime.discoveryPath)}`,
    `ONE_MEMORY_HOST_CREDENTIAL_FILE=${quote(runtime.credentialFile)}`,
    `ONE_MEMORY_DATA_DIR=${quote(runtime.dataDir)}`,
  ].join(" ");
  const invocation = `${environment} ${quote(runtime.runtimeCommand)} ${quote(runtime.recallCliPath)} --inject workbuddy`;
  const inject = {
    type: "command",
    command: `sh -c ${quote(`out=$(${invocation} 2>/dev/null); status=$?; if [ "$status" -eq 0 ] && [ -n "$out" ]; then printf '%s\\n' "$out"; fi; exit 0`)}`,
    timeout: 5,
  };
  return JSON.stringify(
    {
      hooks: {
        SessionStart: [{ matcher: "compact", hooks: [inject] }, boundary],
        UserPromptSubmit: [capture, { hooks: [inject] }, boundary],
        Stop: [capture],
      },
    },
    null,
    2,
  );
}

/** A WorkBuddy directory, optionally with the plugin state a real one carries.
 *
 * The populated variant is shaped after this machine's own install: registries
 * written with two-space indentation and no closing newline, `installed_plugins`
 * carrying its schema version beside the map, and `settings.json` holding keys
 * we have no business understanding.
 */
async function fixture(options: { populated?: boolean } = {}) {
  const hostDir = await mkdtemp(path.join(os.tmpdir(), "om-workbuddy-plugin-"));
  if (options.populated) {
    await mkdir(path.join(hostDir, "plugins"), { recursive: true });
    await writeFile(
      path.join(hostDir, "plugins", "known_marketplaces.json"),
      host({
        "codebuddy-plugins-official": {
          manifestName: "codebuddy-plugins-official",
          type: "zip",
          installLocation: `${hostDir}/plugins/marketplaces/codebuddy-plugins-official`,
          autoUpdate: true,
          isBuiltIn: true,
        },
        experts: {
          type: "directory",
          source: {
            source: "directory",
            path: `${hostDir}/plugins/marketplaces/experts`,
          },
          installLocation: `${hostDir}/plugins/marketplaces/experts`,
          autoUpdate: false,
        },
      }),
    );
    await writeFile(
      path.join(hostDir, "plugins", "installed_plugins.json"),
      host({
        version: 2,
        plugins: Object.fromEntries(
          Array.from({ length: 49 }, (_, index) => [
            `plugin-${index}@codebuddy-plugins-official`,
            [
              {
                scope: "user",
                installPath: `${hostDir}/plugins/cache/codebuddy-plugins-official/plugin-${index}/1.0.0`,
                version: "1.0.0",
                installedAt: "2026-08-30T16:03:56.607Z",
                lastUpdated: "2026-09-01T12:01:09.267Z",
              },
            ],
          ]),
        ),
      }),
    );
    await writeFile(
      path.join(hostDir, "settings.json"),
      host({
        enabledPlugins: { "find-skills@codebuddy-plugins-official": true },
        sandbox: { extraAllowWrite: ["~/.tencent-cloudq"] },
      }),
    );
  }
  const layout = workbuddyLayout(hostDir);
  const plugin = layout.plugin;
  if (plugin === undefined || !("marketplaceRoot" in plugin))
    throw new Error("WorkBuddy installs as a WorkBuddy-shaped plugin");
  return { layout, hostDir, plugin };
}

/** Serialized the way WorkBuddy 5.5.0 writes these files: no closing newline. */
function host(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function registryPaths(hostDir: string): string[] {
  return [
    path.join(hostDir, "plugins", "known_marketplaces.json"),
    path.join(hostDir, "plugins", "installed_plugins.json"),
    path.join(hostDir, "settings.json"),
  ];
}

async function readRegistries(hostDir: string) {
  const [marketplaces, installed, settings] = await Promise.all(
    registryPaths(hostDir).map(async (target) =>
      JSON.parse(await readFile(target, "utf8")),
    ),
  );
  return {
    marketplaces: marketplaces as Record<string, { installLocation: string }>,
    installed: installed as {
      version: number;
      plugins: Record<string, unknown>;
    },
    settings: settings as Record<string, unknown> & {
      enabledPlugins: Record<string, boolean>;
    },
  };
}

async function missing(target: string): Promise<boolean> {
  try {
    await stat(target);
    return false;
  } catch {
    return true;
  }
}

function runtime() {
  return {
    runtimeCommand: "/opt/one-memory/bin/one-memory",
    mcpEntryPath: "/opt/one-memory/mcp/entry.mjs",
    discoveryPath: "/opt/one-memory/runtime.json",
  };
}
