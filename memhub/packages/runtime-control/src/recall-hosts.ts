import type { Dirent } from "node:fs";
import { access, readdir } from "node:fs/promises";
import path from "node:path";
import {
  claudeCodeLayout,
  codexLayout,
  cursorLayout,
  deepseekHarnessLayout,
  hermesLayout,
  openclawLayout,
  opencodeLayout,
  piLayout,
  qwenCodeLayout,
  type RecallHostLayout,
  workbuddyLayout,
} from "@one-memory/host-bridge/installer";

export interface RecallHostDescriptor {
  hostId: string;
  label: string;
  layout: RecallHostLayout;
  /** Directory whose presence means this host is actually installed here. */
  presencePath: string;
}

/** Hosts One Memory can install recall into, in the order the UI lists them.
 *
 * A host is only offered when its own directory exists, so the list reflects
 * the machine rather than an aspirational roadmap.
 *
 * Async because one host cannot be named without reading the disk: DeepSeek
 * Harness is configured per profile, and which profiles exist is only knowable
 * by looking. Every other host is a fixed path.
 */
export async function recallHosts(
  home: string,
  environment: NodeJS.ProcessEnv = process.env,
): Promise<RecallHostDescriptor[]> {
  if (environment.ONE_MEMORY_HOST_HOME !== undefined) {
    const selected = environment.ONE_MEMORY_HOST_HOME;
    if (!path.isAbsolute(selected) || /[\0\r\n]/.test(selected)) {
      throw Error("runtime_service_host_home_invalid");
    }
    home = path.normalize(selected);
  }
  const cursorDir = path.join(home, ".cursor");
  const claudeDir = path.join(home, ".claude");
  const codexDir = path.join(home, ".codex");
  const workbuddyDir = path.join(home, ".workbuddy");
  // OpenCode splits config from data: the config lives under XDG, so its
  // presence is what says the host is set up here.
  const opencodeDir = path.join(home, ".config", "opencode");
  const qwenDir = path.join(home, ".qwen");
  const openclawDir = path.join(home, ".openclaw");
  // Hermes keeps one `state.db` per profile, so `~/.hermes` is only right for
  // the default profile. `HERMES_HOME` is Hermes' own override for that and
  // must move the host install with it, or the installer writes the provider,
  // MCP block and skill into the default profile while imports read another.
  const hermesHome = environment.HERMES_HOME?.trim();
  const hermesDir = hermesHome ? path.resolve(hermesHome) : path.join(home, ".hermes");
  // Pi keeps everything a level down, under `agent`, and that is also where
  // its skills live — so it is the directory that says the agent is set up,
  // not `~/.pi` itself.
  const piDir = path.join(home, ".pi", "agent");
  const dshHome = environment.DSH_HOME?.trim()
    ? path.resolve(environment.DSH_HOME.trim())
    : path.join(home, ".dsh");
  return [
    {
      hostId: "cursor",
      label: "Cursor",
      layout: cursorLayout(cursorDir),
      presencePath: cursorDir,
    },
    {
      hostId: "claude-code",
      label: "Claude Code",
      layout: claudeCodeLayout({
        claudeDir,
        configPath: path.join(home, ".claude.json"),
      }),
      presencePath: claudeDir,
    },
    {
      hostId: "codex",
      label: "Codex",
      layout: codexLayout(codexDir),
      presencePath: codexDir,
    },
    {
      hostId: "workbuddy",
      // The only host that installs as a plugin rather than as an entry in a
      // config file. Its directory is still what says it is set up here: the
      // `plugins` tree underneath is partly ours, so keying presence off that
      // would report the host as present because we installed into it.
      label: "WorkBuddy",
      layout: workbuddyLayout(workbuddyDir),
      presencePath: workbuddyDir,
    },
    {
      hostId: "opencode",
      label: "OpenCode",
      layout: opencodeLayout(opencodeDir),
      presencePath: opencodeDir,
    },
    {
      hostId: "qwen-code",
      label: "Qwen Code",
      layout: qwenCodeLayout(qwenDir),
      presencePath: qwenDir,
    },
    {
      hostId: "openclaw",
      label: "OpenClaw",
      layout: openclawLayout(openclawDir),
      presencePath: openclawDir,
    },
    {
      hostId: "hermes",
      label: "Hermes",
      layout: hermesLayout(hermesDir),
      presencePath: hermesDir,
    },
    {
      hostId: "pi",
      label: "Pi",
      layout: piLayout(piDir),
      presencePath: piDir,
    },
    ...(await deepseekHarnessProfiles(dshHome)),
  ];
}

/** One row per Harness profile, because that is the unit it configures.
 *
 * Harness has no host-wide config: each `$DSH_HOME/profiles/<name>` carries
 * its own `cordis.patch.yml`, and this machine has two (`web`, `headless`).
 * Listing the profiles rather than the host keeps the install honest — a
 * single "DeepSeek Harness" button would either reach one profile without
 * saying which, or silently write to all of them.
 */
async function deepseekHarnessProfiles(
  dshHome: string,
): Promise<RecallHostDescriptor[]> {
  const profilesDir = path.join(dshHome, "profiles");
  let entries: Dirent[];
  try {
    entries = await readdir(profilesDir, { withFileTypes: true });
  } catch {
    return [];
  }
  const profiles: string[] = [];
  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    // `cordis.yml` is Harness's own file, which is what separates a profile
    // from the `node_modules` directory sitting among them. Keying off our own
    // `cordis.patch.yml` instead would make an uninstalled profile invisible.
    try {
      await access(path.join(profilesDir, entry.name, "cordis.yml"));
    } catch {
      continue;
    }
    profiles.push(entry.name);
  }
  return profiles.sort().map((profile) => {
    const hostId = `deepseek-harness:${profile}`;
    const label = `DeepSeek Harness · ${profile}`;
    return {
      hostId,
      label,
      layout: {
        ...deepseekHarnessLayout({ dshHome, profile }),
        hostId,
        hostLabel: label,
      },
      presencePath: path.join(profilesDir, profile, "cordis.yml"),
    };
  });
}

export async function isHostPresent(
  descriptor: RecallHostDescriptor,
): Promise<boolean> {
  try {
    await access(descriptor.presencePath);
    return true;
  } catch {
    return false;
  }
}
