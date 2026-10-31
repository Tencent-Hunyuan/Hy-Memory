import { mkdir, mkdtemp, readdir, readFile, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";

import {
  applyRecallInstall,
  applyRecallUninstall,
  planRecallInstall,
  planRecallUninstall,
  previewRecallInstall,
  rollbackRecallInstall,
  verifyRecallInstall,
  zcodeLayout,
} from "./recall-installer.js";
import type { ZcodePluginTarget } from "./zcode-plugin.js";

/** What a ZCode client leaves behind: the config sits one level down at
 * `cli/config.json`, nests its servers under `mcp.servers`, and keeps hooks
 * state in the same file. Written by the host, not by us, which is the point:
 * a plugin install may add its one enable key and may change nothing else. */
const HOST_WRITTEN_CONFIG = JSON.stringify({
  mcp: {
    servers: {
      "fixture-existing-server": { command: "keep-me" },
    },
  },
  plugins: {
    enabledPlugins: { "github@zcode-plugins-official": true },
  },
  hooks: {
    enabled: false,
    events: {
      SessionStart: [
        {
          matcher: "startup",
          hooks: [{ type: "command", command: "user-own" }],
        },
      ],
    },
  },
});

/** The registry as the host itself writes it: a versioned array of records,
 * ours beside whatever the user installed from a marketplace. */
const HOST_WRITTEN_REGISTRY = JSON.stringify({
  version: 1,
  plugins: [
    {
      id: "github@zcode-plugins-official",
      name: "github",
      marketplace: "zcode-plugins-official",
      version: "0.1.3",
      installPath:
        "/Users/someone/.zcode/cli/plugins/cache/zcode-plugins-official/github/0.1.3",
      scope: "user",
    },
  ],
});

describe("ZCode recall installer", () => {
  it("installs a plugin tree, not a config edit", async () => {
    const { layout, plugin } = await fixture();
    await writeHostState(plugin);

    const plan = await planRecallInstall(layout, runtimeWithHooks());
    await applyRecallInstall(plan);
    await verifyRecallInstall(layout);

    expect(plan.actions).toEqual(["create-plugin"]);
    const manifest = JSON.parse(
      await readFile(
        path.join(plugin.installRoot, ".zcode-plugin", "plugin.json"),
        "utf8",
      ),
    );
    expect(manifest.name).toBe("one-memory-recall");
    expect(manifest.skills).toBe("skills");
    expect(manifest.hooks).toBe("hooks");
    // The listing reads description_i18n by the host UI's language; both the
    // fallback and the two official keys have to be there for a bilingual
    // listing, and every key must be one the official plugins carry, because
    // the host drops entries with keys it does not know.
    expect(manifest.description_i18n.en).toEqual(manifest.description);
    expect(manifest.description_i18n["zh-CN"]).toContain("MemHub");
    expect(manifest.author.name).toBe("MemHub");
    // The server entry lives inline in the manifest now, and ZCode drops an
    // entry carrying a key it does not know, so it stays exactly the stdio
    // triple.
    expect(manifest.mcpServers["one-memory"].command).toBe(
      "/opt/one-memory/bin/one-memory",
    );
    expect(Object.keys(manifest.mcpServers["one-memory"]).sort()).toEqual([
      "args",
      "command",
      "env",
    ]);
    // The skill ships inside the tree rather than loose in ~/.zcode/skills.
    expect(
      await readFile(
        path.join(plugin.installRoot, "skills", "memhub-recall", "SKILL.md"),
        "utf8",
      ),
    ).toContain("MemHub_recall");
  });

  it("adds exactly one key to the user's config and flips no hook switch", async () => {
    const { layout, configPath } = await fixture();
    await writeHostState(await fixtureTarget(layout));
    const before = await readFile(configPath, "utf8");

    await applyRecallInstall(
      await planRecallInstall(layout, runtimeWithHooks()),
    );

    const after = JSON.parse(await readFile(configPath, "utf8"));
    const beforeParsed = JSON.parse(before);
    // The one write into a file the user owns is the plugin's own enable key.
    // The global `hooks.enabled` switch — which governs every config-file
    // hook the user has — stays exactly as the host left it, and so does the
    // user's own hook and every server of theirs. That is the review point
    // this plugin shape exists to answer.
    expect(after.hooks).toEqual(beforeParsed.hooks);
    expect(after.mcp).toEqual(beforeParsed.mcp);
    expect(after.plugins.enabledPlugins).toEqual({
      "github@zcode-plugins-official": true,
      "one-memory-recall@one-memory": true,
    });
    expect(Object.keys(after).sort()).toEqual(Object.keys(beforeParsed).sort());
  });

  it("registers beside the user's own plugins, not over them", async () => {
    const { layout, plugin } = await fixture();
    await writeHostState(plugin);

    await applyRecallInstall(
      await planRecallInstall(layout, runtimeWithHooks()),
    );

    const registry = JSON.parse(
      await readFile(plugin.installedPluginsPath, "utf8"),
    );
    expect(registry.version).toBe(1);
    expect(registry.plugins.map((record: { id: string }) => record.id)).toEqual(
      ["github@zcode-plugins-official", "one-memory-recall@one-memory"],
    );
    expect(registry.plugins[1].installPath).toBe(plugin.installRoot);
    // The host's plugin list refuses a record without these, and a plugin it
    // cannot list is a plugin whose skill never loads (found on a live
    // client: the MCP server still connected, the Settings entry did not).
    expect(registry.plugins[1].installedAt).toMatch(
      /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/,
    );
    expect(registry.plugins[1].updatedAt).toBe(registry.plugins[1].installedAt);
  });

  it("carries the injection and capture hooks in the plugin tree", async () => {
    const { layout, plugin } = await fixture();

    await applyRecallInstall(
      await planRecallInstall(layout, runtimeWithHooks()),
    );

    const hooks = JSON.parse(
      await readFile(
        path.join(plugin.installRoot, "hooks", "hooks.json"),
        "utf8",
      ),
    );
    const commands = (event: string) =>
      hooks.hooks[event].flatMap(
        (group: { hooks: Array<{ command: string }> }) =>
          group.hooks.map((hook) => hook.command),
      );
    expect(commands("SessionStart").join(" ")).toContain("--inject zcode");
    expect(commands("UserPromptSubmit").join(" ")).toContain("--inject zcode");
    expect(commands("Stop").join(" ")).toContain("capture zcode");
    // One hook per event speaks: the injection entry hands the prompt to
    // capture itself, so no separate UserPromptSubmit capture exists.
    expect(commands("UserPromptSubmit")).toHaveLength(1);
  });

  it("keeps capturing the prompt when the runtime ships no recall command", async () => {
    const { layout, plugin } = await fixture();

    await applyRecallInstall(await planRecallInstall(layout, runtime()));

    const hooks = JSON.parse(
      await readFile(
        path.join(plugin.installRoot, "hooks", "hooks.json"),
        "utf8",
      ),
    );
    const commands = (event: string) =>
      hooks.hooks[event].flatMap(
        (group: { hooks: Array<{ command: string }> }) =>
          group.hooks.map((hook) => hook.command),
      );
    expect(Object.keys(hooks.hooks)).toEqual(["UserPromptSubmit", "Stop"]);
    expect(commands("UserPromptSubmit")).toHaveLength(1);
    expect(commands("UserPromptSubmit").join(" ")).toContain("capture zcode");
    expect(commands("UserPromptSubmit").join(" ")).not.toContain("--inject");
    expect(commands("Stop").join(" ")).toContain("capture zcode");
  });

  it("creates the registry files a fresh host never wrote, and takes them back", async () => {
    const { layout, plugin, configPath } = await fixture();

    await applyRecallInstall(
      await planRecallInstall(layout, runtimeWithHooks()),
    );
    // Neither shared file existed; both were created holding only our entry.
    expect(JSON.parse(await readFile(configPath, "utf8"))).toEqual({
      plugins: { enabledPlugins: { "one-memory-recall@one-memory": true } },
    });
    const registry = JSON.parse(
      await readFile(plugin.installedPluginsPath, "utf8"),
    );
    expect(registry.plugins).toHaveLength(1);

    await applyRecallUninstall(await planRecallUninstall(layout));

    // A file we created and that holds nothing but our entry goes entirely;
    // the plugin cache directory goes with it.
    await expect(readFile(configPath, "utf8")).rejects.toThrow(/ENOENT/);
    await expect(readFile(plugin.installedPluginsPath, "utf8")).rejects.toThrow(
      /ENOENT/,
    );
    expect(
      (await readdir(path.dirname(plugin.cacheRoot))).sort(),
    ).not.toContain(plugin.marketplaceName);
  });

  it("re-plans an installed record byte-stably, reusing its timestamps", async () => {
    const { layout, plugin } = await fixture();
    await writeHostState(plugin);

    await applyRecallInstall(
      await planRecallInstall(layout, runtimeWithHooks()),
    );
    // An install already on disk carries the timestamps that make it list;
    // a repair plan must reproduce them rather than mint new ones, or its
    // preview and the eventual apply could never agree.
    const first = await previewRecallInstall(
      await planRecallInstall(layout, runtimeWithHooks()),
    );
    const second = await previewRecallInstall(
      await planRecallInstall(layout, runtimeWithHooks()),
    );
    expect(second.fingerprint).toBe(first.fingerprint);
  });

  it("uninstalls our entries and leaves the user's plugins and hooks alone", async () => {
    const { layout, plugin, configPath } = await fixture();
    await writeHostState(plugin);
    const before = await readFile(configPath, "utf8");

    await applyRecallInstall(
      await planRecallInstall(layout, runtimeWithHooks()),
    );
    await applyRecallUninstall(await planRecallUninstall(layout));

    // Nothing else moved, so the config comes back byte for byte — hooks
    // section included, exactly as the host left it.
    expect(await readFile(configPath, "utf8")).toBe(before);
    const registry = JSON.parse(
      await readFile(plugin.installedPluginsPath, "utf8"),
    );
    expect(registry.plugins).toHaveLength(1);
    expect(registry.plugins[0].id).toBe("github@zcode-plugins-official");
    expect(
      (await readdir(path.dirname(plugin.cacheRoot))).sort(),
    ).not.toContain(plugin.marketplaceName);
  });

  it("rolls a failed install back to files that never knew us", async () => {
    const { layout, plugin, configPath } = await fixture();
    await writeHostState(plugin);
    const before = await readFile(configPath, "utf8");

    await applyRecallInstall(
      await planRecallInstall(layout, runtimeWithHooks()),
    );
    await rollbackRecallInstall(layout);

    expect(await readFile(configPath, "utf8")).toBe(before);
    expect(
      (await readdir(path.dirname(plugin.cacheRoot))).sort(),
    ).not.toContain(plugin.marketplaceName);
  });

  it("refuses a foreign record under our plugin id", async () => {
    const { layout, plugin } = await fixture();
    await writeHostState(plugin);
    const registry = JSON.parse(
      await readFile(plugin.installedPluginsPath, "utf8"),
    );
    registry.plugins.push({
      id: "one-memory-recall@one-memory",
      installPath: "/Users/someone/elsewhere",
      scope: "user",
    });
    await writeFile(
      plugin.installedPluginsPath,
      JSON.stringify(registry, null, 2),
    );

    await expect(planRecallInstall(layout, runtimeWithHooks())).rejects.toThrow(
      /already has an entry at "plugins.one-memory-recall@one-memory"/,
    );
  });
});

async function fixture() {
  const zcodeDir = await mkdtemp(path.join(os.tmpdir(), "om-zcode-"));
  const layout = zcodeLayout(zcodeDir);
  const plugin = await fixtureTarget(layout);
  return { layout, plugin, zcodeDir, configPath: plugin.configPath };
}

/** The plugin target, or the layout is not the shape these tests install. */
async function fixtureTarget(
  layout: ReturnType<typeof zcodeLayout>,
): Promise<ZcodePluginTarget> {
  const plugin = layout.plugin;
  if (plugin === undefined || "marketplaceRoot" in plugin)
    throw new Error("ZCode installs as a ZCode-shaped plugin");
  return plugin;
}

/** A host that has been used: config, registry, neither knowing us. */
async function writeHostState(plugin: ZcodePluginTarget) {
  await mkdir(path.dirname(plugin.configPath), { recursive: true });
  await writeFile(plugin.configPath, HOST_WRITTEN_CONFIG);
  await mkdir(path.dirname(plugin.installedPluginsPath), { recursive: true });
  await writeFile(plugin.installedPluginsPath, HOST_WRITTEN_REGISTRY);
}

function runtime() {
  return {
    runtimeCommand: "/opt/one-memory/bin/one-memory",
    mcpEntryPath: "/opt/one-memory/mcp/entry.mjs",
    discoveryPath: "/opt/one-memory/runtime.json",
  };
}

/** The runtime as the desktop ships it once recall-cli is packed: capture
 * binary, data directory, and the recall command the injection hook runs. */
function runtimeWithHooks() {
  return {
    ...runtime(),
    captureCommand: "/opt/one-memory/core/one-memory",
    dataDir: "/Users/someone/Library/Application Support/one-memory",
    recallCliPath: "/opt/one-memory/recall/recall-cli.cjs",
    credentialFile: "/opt/one-memory/credentials/zcode.json",
  };
}
