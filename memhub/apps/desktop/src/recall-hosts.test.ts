import { mkdir, mkdtemp, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { isHostPresent, recallHosts } from "./recall-hosts";

/** No `$DSH_HOME`, so the Harness rows stay out of the fixed-path assertions. */
const withoutHarness = { DSH_HOME: path.join(os.tmpdir(), "om-no-dsh") };

describe("recall hosts", () => {
  it.each(["", "relative", "/tmp/a\0b", "/tmp/a\nb"])(
    "refuses invalid explicit host home %j",
    async (home) => {
      await expect(
        recallHosts("/daily-home", {
          ...withoutHarness,
          ONE_MEMORY_HOST_HOME: home,
        }),
      ).rejects.toThrow("runtime_service_host_home_invalid");
    },
  );
  it("keeps whitespace in an explicitly selected host home", async () => {
    const hosts = await recallHosts("/daily-home", {
      ...withoutHarness,
      ONE_MEMORY_HOST_HOME: "/isolated-home ",
    });
    expect(hosts[0].presencePath).toBe("/isolated-home /.cursor");
  });
  it("keeps package acceptance installs in an explicitly isolated host home", async () => {
    const hosts = await recallHosts("/real-home", {
      ...withoutHarness,
      ONE_MEMORY_HOST_HOME: "/acceptance-home",
    });
    expect(
      hosts.find((host) => host.hostId === "codex")?.layout.configPath,
    ).toBe("/acceptance-home/.codex/config.toml");
    expect(
      hosts.every((host) => !host.presencePath.startsWith("/real-home")),
    ).toBe(true);
  });

  it("points each host at its own real config location", async () => {
    const home = "/Users/example";
    const hosts = await recallHosts(home, withoutHarness);

    expect(hosts.map((host) => host.hostId)).toEqual([
      "cursor",
      "claude-code",
      "codex",
      "workbuddy",
      "opencode",
      "qwen-code",
      "openclaw",
      "hermes",
      "pi",
      "zcode",
    ]);
    const [
      cursor,
      claude,
      codex,
      workbuddy,
      opencode,
      qwen,
      openclaw,
      hermes,
      pi,
      zcode,
    ] = hosts;
    expect(cursor.layout.configPath).toBe("/Users/example/.cursor/mcp.json");
    // Codex stores servers in TOML, but it does load Skills.
    expect(codex.layout.configPath).toBe("/Users/example/.codex/config.toml");
    expect(codex.layout.format).toBe("toml");
    expect(codex.layout.skillPath).toBe(
      "/Users/example/.codex/skills/memhub-recall/SKILL.md",
    );
    // WorkBuddy installs as a plugin, so it edits no config of the host's and
    // carries its own skill. The cache path is not ours to choose: the host
    // resolves every level of it and rejects anything else.
    expect(workbuddy.layout.configPath).toBeNull();
    expect(workbuddy.layout.skillPath).toBeNull();
    expect(workbuddy.layout.format).toBe("codebuddy-plugin");
    expect(workbuddy.layout.plugin?.installRoot).toBe(
      "/Users/example/.workbuddy/plugins/cache/one-memory/one-memory-recall/1.0.0",
    );
    expect(
      workbuddy.layout.plugin !== undefined &&
        "settingsPath" in workbuddy.layout.plugin
        ? workbuddy.layout.plugin.settingsPath
        : null,
    ).toBe("/Users/example/.workbuddy/settings.json");
    // The three added on 2026-09-05, each dialect read off the installed host
    // rather than its docs. OpenCode puts servers under `mcp` and has no Skill
    // mechanism at all; OpenClaw nests them one deeper under `mcp.servers`;
    // Qwen Code is Cursor-shaped but keeps them in the file holding every one
    // of its settings.
    expect(opencode.layout.configPath).toBe(
      "/Users/example/.config/opencode/opencode.json",
    );
    expect(opencode.layout.format).toBe("opencode");
    expect(opencode.layout.skillPath).toBeNull();
    expect(qwen.layout.configPath).toBe("/Users/example/.qwen/settings.json");
    expect(qwen.layout.skillPath).toBe(
      "/Users/example/.qwen/skills/memhub-recall/SKILL.md",
    );
    expect(openclaw.layout.configPath).toBe(
      "/Users/example/.openclaw/openclaw.json",
    );
    expect(openclaw.layout.format).toBe("openclaw");
    // Not an oversight: OpenClaw's personal skill source is the shared
    // ~/.agents/skills, outside this host's directory and in front of every
    // other agent that reads it.
    expect(openclaw.layout.skillPath).toBeNull();
    // Hermes spells the key like Codex but carries it in YAML, and unlike
    // OpenClaw its skills directory sits inside its own root.
    expect(hermes.layout.configPath).toBe("/Users/example/.hermes/config.yaml");
    expect(hermes.layout.format).toBe("yaml");
    expect(hermes.layout.skillPath).toBe(
      "/Users/example/.hermes/skills/memhub-recall/SKILL.md",
    );
    // Pi has no MCP config at all — a skill running a command is its only
    // extension point — so the skill is the entire install and the layout has
    // to say so rather than name a file Pi never reads.
    expect(pi.layout.configPath).toBeNull();
    expect(pi.layout.format).toBe("none");
    expect(pi.layout.channel).toBe("cli");
    expect(pi.layout.skillPath).toBe(
      "/Users/example/.pi/agent/skills/memhub-recall/SKILL.md",
    );
    // ZCode installs as a plugin, like WorkBuddy but one directory deeper and
    // under the host's own plugins cache: no config file is edited for the
    // server or the skill, and the one key added to the user's config is the
    // plugin's own enable flag.
    expect(zcode.layout.configPath).toBeNull();
    expect(zcode.layout.format).toBe("zcode-plugin");
    expect(zcode.layout.skillPath).toBeNull();
    expect(zcode.layout.plugin?.installRoot).toBe(
      "/Users/example/.zcode/cli/plugins/cache/one-memory/one-memory-recall/1.0.0",
    );
    // Every other host speaks MCP, and mislabelling one would put wording in
    // its skill for a channel it does not have.
    expect(
      hosts
        .filter((host) => host.layout.channel === "cli")
        .map((h) => h.hostId),
    ).toEqual(["pi"]);
    // Claude Code keeps mcpServers beside the host directory, not inside it.
    expect(claude.layout.configPath).toBe("/Users/example/.claude.json");
    expect(claude.layout.skillPath).toBe(
      "/Users/example/.claude/skills/memhub-recall/SKILL.md",
    );
  });

  it("only reports a host that exists on this machine", async () => {
    const home = await mkdtemp(path.join(os.tmpdir(), "om-hosts-"));
    await mkdir(path.join(home, ".cursor"));
    const [cursor, claude, codex, workbuddy] = await recallHosts(
      home,
      withoutHarness,
    );

    expect(await isHostPresent(cursor)).toBe(true);
    // Offering an install for an absent host would fail at apply time.
    expect(await isHostPresent(claude)).toBe(false);
    expect(await isHostPresent(codex)).toBe(false);
    expect(await isHostPresent(workbuddy)).toBe(false);
  });

  // Harness is the one host with no host-wide config: it is configured per
  // profile. Until this, the desktop app offered no way to install into it at
  // all, while the installer CLI had supported it for days.
  it("offers DeepSeek Harness once per profile it finds", async () => {
    const home = await mkdtemp(path.join(os.tmpdir(), "om-hosts-"));
    const dshHome = path.join(home, ".dsh");
    for (const profile of ["web", "headless"]) {
      await mkdir(path.join(dshHome, "profiles", profile), { recursive: true });
      await writeFile(
        path.join(dshHome, "profiles", profile, "cordis.yml"),
        "plugins: {}\n",
      );
    }
    // Sits among the profiles on a real install and is not one.
    await mkdir(path.join(dshHome, "profiles", "node_modules"), {
      recursive: true,
    });

    const harness = (await recallHosts(home, { DSH_HOME: dshHome })).filter(
      (host) => host.hostId.startsWith("deepseek-harness"),
    );

    expect(harness.map((host) => host.hostId)).toEqual([
      "deepseek-harness:headless",
      "deepseek-harness:web",
    ]);
    expect(harness.map((host) => host.label)).toEqual([
      "DeepSeek Harness · headless",
      "DeepSeek Harness · web",
    ]);
    // Each profile edits its own patch file and keeps its own journal, so an
    // uninstall of one cannot roll back the other.
    expect(harness[0]?.layout.configPath).toBe(
      path.join(dshHome, "profiles", "headless", "cordis.patch.yml"),
    );
    expect(harness[0]?.layout.journalPath).not.toBe(
      harness[1]?.layout.journalPath,
    );
    expect(await isHostPresent(harness[0] as never)).toBe(true);
  });

  it("says nothing about Harness when it is not installed here", async () => {
    const home = await mkdtemp(path.join(os.tmpdir(), "om-hosts-"));

    const hosts = await recallHosts(home, {
      DSH_HOME: path.join(home, ".dsh"),
    });

    expect(hosts.some((host) => host.hostId.includes("deepseek"))).toBe(false);
  });

  it("follows $DSH_HOME rather than assuming ~/.dsh", async () => {
    const home = await mkdtemp(path.join(os.tmpdir(), "om-hosts-"));
    const elsewhere = path.join(home, "somewhere-else");
    await mkdir(path.join(elsewhere, "profiles", "only"), { recursive: true });
    await writeFile(
      path.join(elsewhere, "profiles", "only", "cordis.yml"),
      "plugins: {}\n",
    );

    const hosts = await recallHosts(home, { DSH_HOME: elsewhere });

    expect(
      hosts
        .filter((host) => host.hostId.startsWith("deepseek-harness"))
        .map((host) => host.layout.configPath),
    ).toEqual([path.join(elsewhere, "profiles", "only", "cordis.patch.yml")]);
  });
});
