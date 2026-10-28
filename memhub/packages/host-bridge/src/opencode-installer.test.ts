import { mkdtemp, readdir, readFile, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";

import {
  applyRecallInstall,
  MCP_TOOLSET_VERSION,
  mcpConfigPath,
  opencodeLayout,
  planRecallInstall,
  rollbackRecallInstall,
  verifyRecallInstall,
} from "./recall-installer.js";

describe("OpenCode recall installer", () => {
  it("writes the dialect the installed host actually accepts", async () => {
    const { layout, configPath } = await fixture();

    const plan = await planRecallInstall(layout, runtime());
    await applyRecallInstall(plan);
    const verification = await verifyRecallInstall(layout);

    // Hash checks alone call this install healthy even when the entry points at
    // a broker file that will never exist, and so does the host handshake —
    // the stdio server starts without the app. Three hosts on this machine were
    // installed exactly that way and looked fine until recall returned nothing.
    expect(verification.discoveryPath).toBe("/opt/one-memory/runtime.json");
    expect(verification.discoveryPresent).toBe(false);

    // OpenCode has no Skill mechanism, so the MCP entry is the whole install.
    expect(plan.actions).toEqual(["create-mcp"]);
    const installed = JSON.parse(await readFile(configPath, "utf8"));
    // Three ways this differs from the `mcpServers` hosts, each verified
    // against opencode 1.18.29 rather than taken from its docs: the container
    // is `mcp`, the invocation is one array, and the env block is
    // `environment`. Writing the Cursor shape here parses fine and starts
    // nothing, which is the failure this pins.
    expect(installed.mcpServers).toBeUndefined();
    expect(installed.mcp["one-memory"]).toEqual({
      type: "local",
      command: [
        "/opt/one-memory/bin/one-memory",
        "/opt/one-memory/mcp/entry.mjs",
      ],
      environment: {
        ELECTRON_RUN_AS_NODE: "1",
        ONE_MEMORY_MANAGED_BY: "one-memory",
        ONE_MEMORY_BROKER_DISCOVERY: "/opt/one-memory/runtime.json",
        ONE_MEMORY_MCP_TOOLSET: MCP_TOOLSET_VERSION,
      },
      enabled: true,
    });
  });

  it("recognises its own entry instead of installing a second one", async () => {
    const { layout, configPath } = await fixture();
    await applyRecallInstall(await planRecallInstall(layout, runtime()));

    // Our entry is stored in OpenCode's spelling, so ownership has to be read
    // back through the same conversion. Failing to do so would see a foreign
    // entry and refuse to reinstall — or worse, record ours as the user's.
    const second = await planRecallInstall(layout, runtime());
    expect(second.actions).toEqual(["merge-mcp"]);
    await applyRecallInstall(second);
    await rollbackRecallInstall(layout);

    // OpenCode had no config before us, and installing twice must not change
    // that: the journal still describes the machine as it was the first time,
    // so uninstall takes the file away rather than leaving an empty shell.
    await expect(readFile(configPath, "utf8")).rejects.toThrow(/ENOENT/);
  });

  it("refuses a foreign entry of the same name unless told to take over", async () => {
    const { layout, configPath } = await fixture();
    const theirs = {
      $schema: "https://opencode.ai/config.json",
      mcp: {
        "one-memory": {
          type: "local",
          command: ["/somewhere/else/one-memory-tutti"],
          enabled: true,
        },
      },
    };
    const original = `${JSON.stringify(theirs, null, 2)}\n`;
    await writeFile(configPath, original);

    await expect(planRecallInstall(layout, runtime())).rejects.toThrow(
      /OpenCode/,
    );

    // Taking over must stay reversible, and what goes back is the host's own
    // spelling — not our normalised view of it.
    const plan = await planRecallInstall(layout, runtime(), { takeover: true });
    await applyRecallInstall(plan);
    expect(
      JSON.parse(await readFile(configPath, "utf8")).mcp["one-memory"].command,
    ).toHaveLength(2);
    await rollbackRecallInstall(layout);
    expect(await readFile(configPath, "utf8")).toBe(original);
  });

  it("leaves no trace of itself in the host directory after uninstall", async () => {
    const { layout, configDir } = await fixture();
    // OpenCode's own file, which must survive: the check is "nothing of ours
    // is left", not "the directory is empty".
    await writeFile(path.join(configDir, ".gitignore"), "auth.json\n");

    await applyRecallInstall(await planRecallInstall(layout, runtime()));
    await rollbackRecallInstall(layout);

    // Removing the journal but keeping the `.one-memory` directory that held
    // it left our name inside every host we had ever touched, which is not
    // what "uninstall" says. Found on the real machine, not in a fixture.
    expect((await readdir(configDir)).sort()).toEqual([".gitignore"]);
  });

  it("leaves unrelated servers and top-level settings untouched", async () => {
    const { layout, configPath } = await fixture();
    const original = {
      $schema: "https://opencode.ai/config.json",
      model: "anthropic/claude-sonnet-4",
      mcp: {
        weather: {
          type: "remote",
          url: "https://example.invalid",
          enabled: true,
        },
      },
    };
    await writeFile(configPath, `${JSON.stringify(original, null, 2)}\n`);

    await applyRecallInstall(await planRecallInstall(layout, runtime()));
    const merged = JSON.parse(await readFile(configPath, "utf8"));
    expect(merged.model).toBe(original.model);
    expect(merged.mcp.weather).toEqual(original.mcp.weather);

    await rollbackRecallInstall(layout);
    expect(JSON.parse(await readFile(configPath, "utf8"))).toEqual(original);
  });
});

async function fixture() {
  const configDir = await mkdtemp(path.join(os.tmpdir(), "om-opencode-"));
  const layout = opencodeLayout(configDir);
  return { layout, configDir, configPath: mcpConfigPath(layout) };
}

function runtime() {
  return {
    runtimeCommand: "/opt/one-memory/bin/one-memory",
    mcpEntryPath: "/opt/one-memory/mcp/entry.mjs",
    discoveryPath: "/opt/one-memory/runtime.json",
  };
}
