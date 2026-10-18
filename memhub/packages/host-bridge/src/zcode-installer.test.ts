import { mkdir, mkdtemp, readdir, readFile, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";

import {
  applyRecallInstall,
  mcpConfigPath,
  planRecallInstall,
  rollbackRecallInstall,
  verifyRecallInstall,
  zcodeLayout,
} from "./recall-installer.js";

/** What a ZCode client leaves behind: the config sits one level down at
 * `cli/config.json`, nests its servers under `mcp.servers`, and keeps hooks
 * state in the same file. Written by the host, not by us, which is the point. */
const HOST_WRITTEN_CONFIG = JSON.stringify({
  mcp: {
    servers: {
      "fixture-existing-server": { command: "keep-me" },
    },
  },
  hooks: {
    enabled: true,
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

describe("ZCode recall installer", () => {
  it("merges into cli/config.json and installs the Skill", async () => {
    const { layout, configPath } = await fixture();
    await writeHostConfig(layout);

    const plan = await planRecallInstall(layout, runtime());
    await applyRecallInstall(plan);
    await verifyRecallInstall(layout);

    expect(plan.actions).toEqual(["merge-mcp", "create-skill"]);
    const merged = JSON.parse(await readFile(configPath, "utf8"));
    expect(merged.mcp.servers["one-memory"].command).toBe(
      "/opt/one-memory/bin/one-memory",
    );
    // ZCode drops a server entry that carries a key it does not know, so the
    // entry has to be exactly the stdio triple — no marker keys smuggled in
    // alongside the ones the host understands.
    expect(Object.keys(merged.mcp.servers["one-memory"]).sort()).toEqual([
      "args",
      "command",
      "env",
    ]);
    // The server map is nested under `mcp.servers` beside the user's own
    // servers, and the file holds the host's hooks state too. A merge that
    // rebuilt either container from our entry alone would take the user's
    // other server and hook configuration with it.
    expect(merged.mcp.servers["fixture-existing-server"]).toEqual({
      command: "keep-me",
    });
    // Without a recall command the install degrades to capture only, so the
    // user's SessionStart entry stands alone and our single addition is the
    // Stop group.
    expect(merged.hooks.events.SessionStart).toEqual([
      {
        matcher: "startup",
        hooks: [{ type: "command", command: "user-own" }],
      },
    ]);
    expect(merged.hooks.events.Stop).toHaveLength(1);
    expect(merged.hooks.events.Stop[0].hooks[0].command).toContain(
      "capture zcode",
    );
    expect(merged.hooks.enabled).toBe(true);
    expect(await readFile(String(layout.skillPath), "utf8")).toContain(
      "MemHub_recall",
    );
  });

  it("gives back the same config, though not the same bytes", async () => {
    const { layout, configPath } = await fixture();
    await writeHostConfig(layout);

    await applyRecallInstall(await planRecallInstall(layout, runtime()));
    await rollbackRecallInstall(layout);

    const restored = await readFile(configPath, "utf8");
    // The guarantee for a JSON host is the value, not the layout: the file is
    // re-serialised on the way out, so the host's own formatting comes back
    // in our style. Restoring the whole file verbatim would be worse than
    // this: it would also revert settings the user changed after installing,
    // which we never owned.
    expect(JSON.parse(restored)).toEqual(JSON.parse(HOST_WRITTEN_CONFIG));
    expect(restored).not.toBe(HOST_WRITTEN_CONFIG);
  });

  it("creates the cli directory when the host has never written a config", async () => {
    const { layout, configPath } = await fixture();

    const plan = await planRecallInstall(layout, runtime());
    await applyRecallInstall(plan);
    await verifyRecallInstall(layout);

    expect(plan.actions).toEqual(["create-mcp", "create-skill"]);
    const created = JSON.parse(await readFile(configPath, "utf8"));
    expect(created.mcp.servers["one-memory"].command).toBe(
      "/opt/one-memory/bin/one-memory",
    );
  });

  it("installs prefetch and capture hooks beside the server in one file", async () => {
    const { layout, configPath } = await fixture();
    await writeHostConfig(layout);

    await applyRecallInstall(
      await planRecallInstall(layout, runtimeWithHooks()),
    );
    await verifyRecallInstall(layout);

    const merged = JSON.parse(await readFile(configPath, "utf8"));
    // The one invariant this host adds: hooks and the MCP entry share
    // cli/config.json, and apply writes the file twice — server first, hooks
    // second. A hooks plan built from the pre-install text would have dropped
    // the server the first write had just landed.
    expect(merged.mcp.servers["one-memory"].command).toBe(
      "/opt/one-memory/bin/one-memory",
    );
    expect(merged.mcp.servers["fixture-existing-server"]).toEqual({
      command: "keep-me",
    });
    const commands = (event: string) =>
      merged.hooks.events[event].flatMap(
        (group: { hooks: Array<{ command: string }> }) =>
          group.hooks.map((hook) => hook.command),
      );
    // UserPromptSubmit and SessionStart run the recall injection entry, which
    // hands the prompt to capture itself; Stop carries the capture command.
    // SessionStart also keeps the user's own entry from the fixture.
    expect(commands("UserPromptSubmit")).toHaveLength(1);
    expect(commands("UserPromptSubmit")[0]).toContain("--inject zcode");
    expect(commands("SessionStart")).toEqual([
      "user-own",
      expect.stringContaining("--inject zcode"),
    ]);
    expect(commands("Stop")).toHaveLength(1);
    expect(commands("Stop")[0]).toContain("capture zcode");
    expect(commands("Stop")[0]).not.toContain("--inject zcode");
  });

  it("gives back the same config with hooks and server removed together", async () => {
    const { layout, configPath } = await fixture();
    await writeHostConfig(layout);

    await applyRecallInstall(
      await planRecallInstall(layout, runtimeWithHooks()),
    );
    await rollbackRecallInstall(layout);

    const restored = JSON.parse(await readFile(configPath, "utf8"));
    // The two removals are one edit, because each rebuilds the same file from
    // the same starting text: applied separately, the second write would
    // resurrect what the first removed.
    expect(restored).toEqual(JSON.parse(HOST_WRITTEN_CONFIG));
  });

  it("enables config-file hooks, which ZCode requires for ours to run", async () => {
    const { layout, configPath } = await fixture();
    await writeHostConfig(layout);
    const disabled = JSON.parse(await readFile(configPath, "utf8"));
    disabled.hooks.enabled = false;
    await writeFile(configPath, JSON.stringify(disabled, null, 2));

    await applyRecallInstall(await planRecallInstall(layout, runtime()));

    // A named side effect rather than an accident: flipping the flag starts
    // the user's own config-file hooks too, and the alternative — hooks the
    // host never executes — silently captures nothing.
    expect(JSON.parse(await readFile(configPath, "utf8")).hooks.enabled).toBe(
      true,
    );
  });

  it("degrades to a Stop capture hook when the runtime ships no recall command", async () => {
    const { layout, configPath } = await fixture();
    await writeHostConfig(layout);

    await applyRecallInstall(await planRecallInstall(layout, runtime()));

    const merged = JSON.parse(await readFile(configPath, "utf8"));
    expect(Object.keys(merged.hooks.events).sort()).toEqual([
      "SessionStart",
      "Stop",
    ]);
    expect(merged.hooks.events.UserPromptSubmit).toBeUndefined();
  });

  it("takes its skills directory with it when it created one", async () => {
    const { layout, zcodeDir } = await fixture();
    await writeHostConfig(layout);

    await applyRecallInstall(await planRecallInstall(layout, runtime()));
    await rollbackRecallInstall(layout);

    // ZCode ships no `skills` directory, so the one holding our Skill is ours
    // two levels deep; removing only the inner level would leave an empty
    // `skills/` behind.
    expect((await readdir(zcodeDir)).sort()).toEqual(["cli"]);
  });

  it("keeps a skills directory that holds someone else's skill", async () => {
    const { layout, zcodeDir } = await fixture();
    await writeHostConfig(layout);
    const theirs = path.join(zcodeDir, "skills", "their-skill");
    await mkdir(theirs, { recursive: true });
    await writeFile(path.join(theirs, "SKILL.md"), "theirs\n");

    await applyRecallInstall(await planRecallInstall(layout, runtime()));
    await rollbackRecallInstall(layout);

    // The cleanup walks up two levels, so it has to stop at anything occupied.
    expect(await readdir(path.join(zcodeDir, "skills"))).toEqual([
      "their-skill",
    ]);
  });
});

async function fixture() {
  const zcodeDir = await mkdtemp(path.join(os.tmpdir(), "om-zcode-"));
  const layout = zcodeLayout(zcodeDir);
  return { layout, zcodeDir, configPath: mcpConfigPath(layout) };
}

async function writeHostConfig(layout: ReturnType<typeof zcodeLayout>) {
  const configPath = mcpConfigPath(layout);
  await mkdir(path.dirname(configPath), { recursive: true });
  await writeFile(configPath, HOST_WRITTEN_CONFIG);
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
