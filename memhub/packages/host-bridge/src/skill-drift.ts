import { createHash } from "node:crypto";
import path from "node:path";
import { captureFilePath, namesCapture } from "./capture-hooks.js";
import { PLUGIN_HOOKS } from "./codebuddy-plugin.js";
import { MCP_TOOLSET_KEY, MCP_TOOLSET_VERSION } from "./mcp-toolset.js";
import {
  asInstalled,
  expectedPluginHooks,
  planRecallInstall,
  type RecallHostLayout,
  type RecallInstallPlan,
  type RecallRuntime,
  readInstalledRecallCommand,
  renderSkillContent,
  SKILL_VERSION,
} from "./recall-installer.js";
import { probeRecallInstall, type RecallProbe } from "./recall-probe.js";

/** Whether the call-rule skill on disk still matches what we ship.
 *
 * Three states rather than a boolean, because a boolean cannot separate the
 * two failures that matter: the template moved and the bytes are still ours
 * (`stale`, safe to rewrite), against the user edited the skill
 * (`modified`, their asset — rewrite only with an explicit confirmation).
 */
export type SkillDriftState = "in_sync" | "stale" | "modified";

export interface SkillDrift {
  hostId: string;
  state: SkillDriftState;
  /** Version embedded in the disk skill, when the marker is ours. */
  installedVersion: number | null;
  currentVersion: number;
  skillPath: string | null;
  detail: string;
}

export interface SkillSyncPlan {
  drift: SkillDrift;
  plan: RecallInstallPlan;
  /** True when applying would overwrite bytes the user wrote by hand. */
  requiresConfirmation: boolean;
}

export interface HostHealth {
  hostId: string;
  hostLabel: string;
  /** R-24: whether the recall link reaches the app. */
  link: RecallProbe;
  /** R-33: whether the call-rule skill is current. Null when this host has none. */
  skill: SkillDrift | null;
}

/** Read the skill the host will load, wherever this layout keeps it. */
export function skillFilePath(layout: RecallHostLayout): string | null {
  if (layout.skillPath !== null) return layout.skillPath;
  if (layout.plugin !== undefined) {
    return path.join(
      layout.plugin.installRoot,
      "skills",
      layout.plugin.skillName,
      "SKILL.md",
    );
  }
  return null;
}

/** Where the skill a host has now actually sits.
 *
 * Differs from `skillFilePath` only on a host last installed before the
 * MemHub rename, whose skill is still under the old name. Drift has to read
 * that one, or a stale install reads as none and never gets synced.
 */
async function installedSkillFile(
  layout: RecallHostLayout,
): Promise<string | null> {
  const current = skillFilePath(layout);
  if (current === null || (await readOptional(current)) !== null) {
    return current;
  }
  if (layout.plugin !== undefined) {
    const legacy = path.join(
      layout.plugin.installRoot,
      "skills",
      layout.plugin.pluginName,
      "SKILL.md",
    );
    return (await readOptional(legacy)) === null ? current : legacy;
  }
  return (await asInstalled(layout)).skillPath;
}

/** Compare the skill, and a plugin's hook file, to what we would write today.
 *
 * A plugin's hooks change without its skill changing — the recall injection
 * hook arrived that way — so the hook file is judged by the same rule: still
 * the bytes we journaled is stale, anything else is the user's edit.
 */
export async function checkSkillDrift(
  layout: RecallHostLayout,
  runtime: RecallRuntime,
): Promise<SkillDrift | null> {
  const skill = await checkSkillFileDrift(layout, runtime);
  if (skill === null) {
    // A host with no skill mechanism still has an entry whose tool set can
    // age. One whose skill is merely missing is left alone: syncing would put
    // back a file somebody removed.
    if (skillFilePath(layout) !== null) return null;
    return (await serverToolsetDrift(layout)) === "stale"
      ? {
          hostId: layout.hostId,
          state: "stale",
          installedVersion: null,
          currentVersion: SKILL_VERSION,
          skillPath: null,
          detail: TOOLSET_STALE_DETAIL,
        }
      : null;
  }
  if (skill.state === "modified") return skill;
  const hooks = await pluginHooksDrift(layout, runtime);
  if (hooks === "modified") {
    return {
      ...skill,
      state: "modified",
      detail: "the plugin hook file was edited after install",
    };
  }
  if (skill.state === "stale") return skill;
  if (hooks === "stale") {
    return {
      ...skill,
      state: "stale",
      detail: "plugin hooks are still ours, but have moved since install",
    };
  }
  if ((await serverToolsetDrift(layout)) === "stale") {
    return { ...skill, state: "stale", detail: TOOLSET_STALE_DETAIL };
  }
  const capture = await captureHooksDrift(layout);
  if (capture === "stale") {
    return {
      ...skill,
      state: "stale",
      detail: "more than one managed capture command is installed",
    };
  }
  return skill;
}

const TOOLSET_STALE_DETAIL =
  "the MCP entry predates the tool set this MemHub serves";

/** Whether our MCP entry names the tool set this release serves.
 *
 * The entry is otherwise identical across upgrades, so a host that caches a
 * server's tool list under a fingerprint of its config (WorkBuddy does) keeps
 * listing tools that are gone until the entry itself changes. Only an entry
 * marked as ours is read; a CLI-channel host has no entry at all.
 */
async function serverToolsetDrift(
  layout: RecallHostLayout,
): Promise<"in_sync" | "stale" | null> {
  if (layout.channel !== "mcp") return null;
  // A config the host cannot parse either is not ours to judge here; the
  // install and link checks report it.
  const installed = await readInstalledRecallCommand(layout).catch(() => null);
  if (installed === null) return null;
  return installed.env[MCP_TOOLSET_KEY] === MCP_TOOLSET_VERSION
    ? "in_sync"
    : "stale";
}

/** Detect duplicated command hooks left by earlier installs.
 *
 * `checkSkillDrift` historically compared only the Skill and plugin hooks;
 * Cursor/Claude/Codex capture files could accumulate several One Memory
 * commands while still reporting `in_sync`. A normal authorized sync repairs
 * them through the merge path, but only if drift sees them first.
 */
async function captureHooksDrift(
  layout: RecallHostLayout,
): Promise<"in_sync" | "stale" | null> {
  if (!["cursor", "claude-code", "codex"].includes(layout.hostId)) return null;
  const raw = await readOptional(layout.journalPath);
  let journal: {
    capture_hooks?: Array<{ path?: unknown }>;
  } = {};
  if (raw !== null) {
    try {
      journal = JSON.parse(raw) as typeof journal;
    } catch {
      journal = {};
    }
  }
  const paths = new Set(
    (journal.capture_hooks ?? [])
      .map((entry) => entry.path)
      .filter((value): value is string => typeof value === "string"),
  );
  const standard = captureFilePath(layout);
  if (standard !== null) paths.add(standard);
  for (const filePath of paths) {
    const content = await readOptional(filePath);
    if (content === null) continue;
    let document: unknown;
    try {
      document = JSON.parse(content);
    } catch {
      continue;
    }
    const hooks = (document as { hooks?: unknown }).hooks;
    if (hooks && typeof hooks === "object" && !Array.isArray(hooks)) {
      for (const entries of Object.values(hooks as Record<string, unknown>)) {
        if (
          commandValues(entries).filter((value) =>
            namesCapture(value, layout.hostId),
          ).length > 1
        ) {
          return "stale";
        }
      }
    }
  }
  return "in_sync";
}

function commandValues(value: unknown, found: string[] = []): string[] {
  if (Array.isArray(value)) {
    for (const item of value) commandValues(item, found);
  } else if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    if (typeof record.command === "string") found.push(record.command);
    for (const item of Object.values(record)) commandValues(item, found);
  }
  return found;
}

async function pluginHooksDrift(
  layout: RecallHostLayout,
  runtime: RecallRuntime,
): Promise<"in_sync" | "stale" | "modified" | null> {
  const expected = expectedPluginHooks(layout, runtime);
  if (expected === null || layout.plugin === undefined) return null;
  const hooksPath = path.join(layout.plugin.installRoot, PLUGIN_HOOKS);
  const onDisk = await readOptional(hooksPath);
  if (onDisk === null || onDisk === expected) return "in_sync";
  const journaled = await readJournaledPluginHash(layout, hooksPath);
  if (journaled !== null && journaled === hash(onDisk)) return "stale";
  return isRecognizableWorkbuddyHookFile(onDisk) ? "stale" : "modified";
}

/** A plugin hook file old enough that the journal may be absent.
 *
 * A missing journal must not turn our own previous hook into a user edit.
 * Recognize only the structure and the One Memory commands this installer
 * wrote; any foreign or edited structure remains `modified`.
 */
function isRecognizableWorkbuddyHookFile(raw: string): boolean {
  let document: unknown;
  try {
    document = JSON.parse(raw);
  } catch {
    return false;
  }
  if (!document || typeof document !== "object" || Array.isArray(document)) {
    return false;
  }
  const hooks = (document as { hooks?: unknown }).hooks;
  if (!hooks || typeof hooks !== "object" || Array.isArray(hooks)) return false;
  const record = hooks as Record<string, unknown>;
  const commands = (event: string): string[] =>
    Array.isArray(record[event])
      ? (record[event] as unknown[]).flatMap((entry) => commandValues(entry))
      : [];
  const start = commands("SessionStart");
  const prompt = commands("UserPromptSubmit");
  const stop = commands("Stop");
  const has = (values: string[], marker: string): boolean =>
    values.some((value) => value.includes(marker));
  return (
    has(start, "--inject workbuddy") &&
    (has(prompt, "--inject workbuddy") || has(prompt, "capture workbuddy")) &&
    has(stop, "capture workbuddy")
  );
}

async function checkSkillFileDrift(
  layout: RecallHostLayout,
  runtime: RecallRuntime,
): Promise<SkillDrift | null> {
  const skillPath = await installedSkillFile(layout);
  if (skillPath === null) return null;

  const onDisk = await readOptional(skillPath);
  if (onDisk === null) return null;
  if (!isManagedSkill(onDisk)) {
    // Present but not ours. Treated as modified so sync asks before touching.
    return {
      hostId: layout.hostId,
      state: "modified",
      installedVersion: null,
      currentVersion: SKILL_VERSION,
      skillPath,
      detail: "this skill is not marked as managed by MemHub",
    };
  }

  const expected = renderSkillContent(layout.channel, runtime);
  const installedVersion = readSkillVersion(onDisk);
  if (onDisk === expected) {
    return {
      hostId: layout.hostId,
      state: "in_sync",
      installedVersion,
      currentVersion: SKILL_VERSION,
      skillPath,
      detail: "call rules match the template shipping with this app",
    };
  }

  // Content differs. Stale means the bytes are still exactly what we last
  // wrote (journal fingerprint matches); anything else is the user's edit.
  const journalHash = await readInstalledSkillHash(layout);
  const onDiskHash = hash(onDisk);
  if (journalHash !== null && onDiskHash === journalHash) {
    return {
      hostId: layout.hostId,
      state: "stale",
      installedVersion,
      currentVersion: SKILL_VERSION,
      skillPath,
      detail:
        "call rules are still ours, but the template has moved since install",
    };
  }
  return {
    hostId: layout.hostId,
    state: "modified",
    installedVersion,
    currentVersion: SKILL_VERSION,
    skillPath,
    detail: "the call-rule skill was edited after install",
  };
}

/** Plan a sync of the call-rule skill through the ordinary install path.
 *
 * Returns a plan rather than writing, so preview and apply stay one path, and
 * so a modified skill can be shown as a conflict before anything is touched.
 */
export async function planSkillSync(
  layout: RecallHostLayout,
  runtime: RecallRuntime,
): Promise<SkillSyncPlan | null> {
  const drift = await checkSkillDrift(layout, runtime);
  if (drift === null || drift.state === "in_sync") return null;
  // Takeover: the entry being replaced is our own (stale) or was ours and is
  // now edited (modified). Without it the planner would refuse to touch a
  // skill it no longer recognises as matching the journal.
  const plan = await planRecallInstall(layout, runtime, { takeover: true });
  return {
    drift,
    plan,
    requiresConfirmation: drift.state === "modified",
  };
}

/** Link state and call-rule state in one round trip for the detail page. */
export async function hostHealthCheck(
  layout: RecallHostLayout,
  runtime: RecallRuntime,
): Promise<HostHealth> {
  const [link, skill] = await Promise.all([
    probeRecallInstall(layout),
    checkSkillDrift(layout, runtime),
  ]);
  return {
    hostId: layout.hostId,
    hostLabel: layout.hostLabel,
    link,
    skill,
  };
}

const MANAGED_PATTERN =
  /<!--\s*one-memory-managed(?:\s+skill-version=(\d+))?\s*-->/;

export function isManagedSkill(text: string): boolean {
  return MANAGED_PATTERN.test(text) || text.includes("one-memory-managed:v1");
}

export function readSkillVersion(text: string): number | null {
  const match = text.match(MANAGED_PATTERN);
  if (match?.[1] !== undefined) return Number(match[1]);
  // Legacy marker from before versioned skills.
  if (text.includes("<!-- one-memory-managed:v1 -->")) return 1;
  return null;
}

async function readInstalledSkillHash(
  layout: RecallHostLayout,
): Promise<string | null> {
  const raw = await readOptional(layout.journalPath);
  if (raw === null) return null;
  try {
    const journal = JSON.parse(raw) as { installed_skill_hash?: string | null };
    return typeof journal.installed_skill_hash === "string"
      ? journal.installed_skill_hash
      : null;
  } catch {
    return null;
  }
}

async function readJournaledPluginHash(
  layout: RecallHostLayout,
  filePath: string,
): Promise<string | null> {
  const raw = await readOptional(layout.journalPath);
  if (raw === null) return null;
  try {
    const journal = JSON.parse(raw) as {
      plugin_files?: Array<{ path?: unknown; hash?: unknown }>;
    };
    const entry = journal.plugin_files?.find((file) => file.path === filePath);
    return typeof entry?.hash === "string" ? entry.hash : null;
  } catch {
    return null;
  }
}

async function readOptional(targetPath: string): Promise<string | null> {
  try {
    const { readFile } = await import("node:fs/promises");
    return await readFile(targetPath, "utf8");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return null;
    throw error;
  }
}

function hash(value: string): string {
  return createHash("sha256").update(value).digest("hex");
}
