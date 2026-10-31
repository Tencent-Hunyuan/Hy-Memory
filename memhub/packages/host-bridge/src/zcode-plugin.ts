/** The `.zcode-plugin` install shape, as ZCode actually loads it.
 *
 * ZCode is the second plugin-format host after WorkBuddy, and the two
 * dialects differ in more than the manifest directory:
 *
 * - One copy, not two. WorkBuddy loads skills from a marketplace copy and
 *   MCP servers from a cache snapshot; ZCode loads everything from the cache
 *   copy alone, and no marketplace registration is needed. Verified on a real
 *   3.16.2 client (2026-10-21) by hand-installing a plugin under
 *   `plugins/cache/` with only a registry record and an enable flag: hooks
 *   fired, the MCP server connected, and the skill was listed.
 * - An array registry. `installed_plugins.json` is `{version: 1, plugins:
 *   [records]}` keyed by each record's `id` field, where WorkBuddy's is a
 *   version-2 map of arrays keyed by `<plugin>@<marketplace>`.
 * - The enable flag lives in the user's own `config.json`, under
 *   `plugins.enabledPlugins["<id>"]`. Without it a plugin installs disabled —
 *   the "enabled by default" the docs promise holds only for marketplace
 *   installs. That entry is the one key this install adds to a file the user
 *   owns, and it names only our plugin.
 *
 * The global `hooks.enabled` switch is never written. ZCode runs plugin hooks
 * independently of it, and a live check showed a plugin's presence does not
 * wake the user's dormant config-file hooks either. Not touching a switch
 * that governs every hook the user has is the reason this dialect exists
 * (PR #64 review, point 2).
 */

import {
  canonicalJson,
  type PluginFile,
  type PluginRegistry,
} from "./codebuddy-plugin.js";

export const ZCODE_PLUGIN_MANIFEST = ".zcode-plugin/plugin.json";
export const ZCODE_PLUGIN_HOOKS = "hooks/hooks.json";

/** Where a ZCode plugin install keeps its tree and registries. */
export interface ZcodePluginTarget {
  /** Names the id (`<plugin>@<marketplace>`) and the cache directory segment.
   *
   * No marketplace is registered under it — the name exists because the id
   * format requires one and the cache layout mirrors it. */
  marketplaceName: string;
  pluginName: string;
  /** The skill directory hosts list by name, distinct from the plugin name. */
  skillName: string;
  /** Cache directory name; fixed rather than derived, so old ones are not
   * left to collect by a rebuild that changed nothing but a timestamp. */
  version: string;
  /** `<~/.zcode>/cli/plugins/cache/<marketplaceName>` — ours outright, and the
   * highest directory of the install that belongs to us, so it is what
   * uninstall removes. */
  cacheRoot: string;
  /** `<cacheRoot>/<pluginName>/<version>` — the copy the host loads from. */
  installRoot: string;
  /** `plugins/installed_plugins.json` — the host's plugin registry. */
  installedPluginsPath: string;
  /** `cli/config.json` — the user's own config, hosting the one key that is
   * ours: `plugins.enabledPlugins["<id>"]`. */
  configPath: string;
}

export function zcodePluginId(target: ZcodePluginTarget): string {
  return `${target.pluginName}@${target.marketplaceName}`;
}

/** What the recall skill and the MCP entry are built from at plan time. */
export interface ZcodePluginContents {
  skill: string;
  /** The same managed server entry a config-file host would merge in, now
   * inlined into the manifest: ZCode's server schema is strict, so the entry
   * is exactly the one shape every other host already installs. */
  server: unknown;
  /** `capture zcode`, fail-open wrapped; always present — capture degrades
   * alone when the runtime ships no recall command. */
  captureCommand: string;
  /** `recall-cli --inject zcode`, carrying the capture handoff; null when the
   * runtime ships no recall command, and then no injection events ship. */
  injectionCommand: string | null;
}

/** The registry entry `installed_plugins.json` carries for us.
 *
 * `installedAt`/`updatedAt` are required here, unlike the WorkBuddy
 * registries: a real 3.16.2 client refused to list a record without them, and
 * with the listing went the plugin's skill and its Settings entry, while the
 * MCP loader still connected the server — an install that works and cannot be
 * seen. `stamp` is minted once per plan and carried on it, so the preview and
 * the apply of one install write the same bytes. Two plans of the same
 * not-yet-recorded install a moment apart differ only in these fields, which
 * is why an approval has to name one plan (HostPermissions reuses the
 * previewed one) rather than a re-plan. */
export function zcodeInstalledRecord(
  target: ZcodePluginTarget,
  stamp: string,
): unknown {
  return {
    id: zcodePluginId(target),
    name: target.pluginName,
    marketplace: target.marketplaceName,
    version: target.version,
    installPath: target.installRoot,
    scope: "user",
    installedAt: stamp,
    updatedAt: stamp,
  };
}

/** The enable flag as a generic key-path registry, so the existing
 * object-navigation machinery reads, writes, and removes it like any other.
 *
 * `seed` is what a config.json that does not exist yet would get — a document
 * holding nothing but our key. ZCode reads every other setting from
 * defaults, so a config this small is one it can load. */
export function zcodeEnabledRegistry(
  target: ZcodePluginTarget,
): PluginRegistry {
  return {
    path: target.configPath,
    keyPath: ["plugins", "enabledPlugins", zcodePluginId(target)],
    seed: { plugins: { enabledPlugins: {} } },
  };
}

interface InstalledDocument {
  version: number;
  plugins: unknown[];
}

const INSTALLED_SEED: InstalledDocument = { version: 1, plugins: [] };

function parseInstalled(raw: string, label: string): InstalledDocument {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch (error) {
    throw new Error(
      `${label} installed_plugins.json is not valid JSON; no files were changed`,
      { cause: error },
    );
  }
  if (!isRecord(value) || !Array.isArray(value.plugins)) {
    throw new Error(
      `${label} installed_plugins.json must hold a plugins array`,
    );
  }
  return value as unknown as InstalledDocument;
}

function isOurs(target: ZcodePluginTarget, record: unknown): boolean {
  return isRecord(record) && record.id === zcodePluginId(target);
}

/** Our record, or `undefined` when no record carries our id. */
export function readZcodeInstalledRecord(
  raw: string | null,
  target: ZcodePluginTarget,
): unknown {
  if (raw === null) return undefined;
  return (
    parseInstalled(raw, "ZCode").plugins.find((record) =>
      isOurs(target, record),
    ) ?? undefined
  );
}

/** The registry with our record placed, others left exactly where they were.
 *
 * A record with our id pointing at a different install root is refused
 * rather than overwritten: same rule as the WorkBuddy registries, and for the
 * same reason — the id is ours, but the record saying it lives somewhere else
 * belongs to whoever put it there. */
export function putZcodeInstalledRecord(
  raw: string | null,
  target: ZcodePluginTarget,
  stamp: string,
): string {
  const existing =
    raw === null ? undefined : readZcodeInstalledRecord(raw, target);
  if (
    existing !== undefined &&
    !isManagedZcodeInstalledRecord(target, existing)
  ) {
    throw new Error(
      `ZCode installed_plugins.json already has a different install for ${zcodePluginId(target)}; no files were changed`,
    );
  }
  // A reinstall keeps the timestamps the record already carries. The host
  // requires their presence, but the clock is not what the user is approving:
  // reusing them keeps one install's preview and apply byte-identical even
  // when the two steps run as separate processes, each planning afresh.
  const stampToUse =
    isRecord(existing) && typeof existing.installedAt === "string"
      ? existing.installedAt
      : stamp;
  const document =
    raw === null
      ? structuredClone(INSTALLED_SEED)
      : parseInstalled(raw, "ZCode");
  const others = document.plugins.filter((record) => !isOurs(target, record));
  return serialize(
    {
      ...document,
      plugins: [...others, zcodeInstalledRecord(target, stampToUse)],
    },
    raw,
  );
}

/** Whether a record proves an install of ours: it must point at our install
 * root. */
export function isManagedZcodeInstalledRecord(
  target: ZcodePluginTarget,
  value: unknown,
): boolean {
  return isRecord(value) && value.installPath === target.installRoot;
}

/** The registry as canonical JSON with our record gone — the input to the
 * byte-exact rollback comparison, exactly like `registryWithoutOurs`. */
export function zcodeInstalledWithoutOurs(
  raw: string,
  target: ZcodePluginTarget,
): string {
  const document = parseInstalled(raw, "ZCode");
  return canonicalJson({
    ...document,
    plugins: document.plugins.filter((record) => !isOurs(target, record)),
  });
}

/** Whether our record is the only thing in the file beyond the seed.
 *
 * Only then may a file we created be deleted outright on uninstall. */
export function zcodeInstalledHoldsOnlyOurs(
  raw: string,
  target: ZcodePluginTarget,
): boolean {
  return (
    zcodeInstalledWithoutOurs(raw, target) === canonicalJson(INSTALLED_SEED)
  );
}

/** The registry with our record gone, or holding what it held before us.
 *
 * `reinstate` is the record the journal says sat at our id before we
 * installed, when one did. */
export function removeZcodeInstalledRecord(
  raw: string,
  target: ZcodePluginTarget,
  reinstate: unknown,
): string {
  const document = parseInstalled(raw, "ZCode");
  const others = document.plugins.filter((record) => !isOurs(target, record));
  return serialize(
    {
      ...document,
      plugins: reinstate === undefined ? others : [...others, reinstate],
    },
    raw,
  );
}

/** Re-serialise the way the host writes the file: two spaces, and a closing
 * newline only when one was there. */
function serialize(document: InstalledDocument, raw: string | null): string {
  const text = JSON.stringify(document, null, 2);
  return raw?.endsWith("\n") ? `${text}\n` : text;
}

/** The hooks file the plugin carries.
 *
 * ZCode's plugin hooks nest under a `hooks` wrapper with the event names
 * directly beneath it — one level shallower than its config-file hooks — and
 * each event holds `{matcher?, hooks: [...]}` groups. This shape is the one
 * the client's own diagnosing guide documents, and the one a live 3.16.2
 * session was verified to fire on all three events.
 *
 * One hook per event speaks. With a recall command, the injection entry
 * recalls with the prompt's words, answers `additionalContext`, and hands the
 * prompt to capture without waiting — which is why there is no separate
 * UserPromptSubmit capture hook. Without one, the capture command takes that
 * slot itself, so the user's prompt is still captured; Stop always captures
 * the assistant side.
 */
export function zcodePluginHooksFile(
  contents: Pick<ZcodePluginContents, "captureCommand" | "injectionCommand">,
): string {
  const entry = (command: string, timeoutMs: number) => [
    { hooks: [{ type: "command", command, timeoutMs }] },
  ];
  return json({
    hooks: {
      ...(contents.injectionCommand === null
        ? {
            UserPromptSubmit: entry(contents.captureCommand, 5000),
          }
        : {
            SessionStart: entry(contents.injectionCommand, 8000),
            UserPromptSubmit: entry(contents.injectionCommand, 8000),
          }),
      Stop: entry(contents.captureCommand, 5000),
    },
  });
}

/** The plugin tree: manifest, hooks, skill — every file ours outright. */
export function zcodePluginTree(
  target: ZcodePluginTarget,
  contents: ZcodePluginContents,
): PluginFile[] {
  return [
    {
      relativePath: ZCODE_PLUGIN_MANIFEST,
      content: json({
        name: target.pluginName,
        version: target.version,
        description:
          "Recall confirmed memories from the local MemHub app and capture ZCode conversations into it in real time",
        // The listing shows the language matching the host UI; both keys are
        // the ones the official plugins carry.
        description_i18n: {
          en: "Recall confirmed memories from the local MemHub app and capture ZCode conversations into it in real time",
          "zh-CN":
            "从本地 MemHub 应用召回已确认的记忆，并实时采集 ZCode 会话存入其中",
        },
        author: {
          name: "MemHub",
          url: "https://github.com/Tencent-Hunyuan/Hy-Memory",
        },
        keywords: ["memory", "recall", "memhub", "context"],
        license: "Apache-2.0",
        // Declared explicitly, in the directory form the official plugins
        // use: without this field the host loads the hooks and the servers
        // but lists no skills (verified on a live client).
        skills: "skills",
        hooks: "hooks",
        mcpServers: { "one-memory": contents.server },
      }),
    },
    {
      relativePath: ZCODE_PLUGIN_HOOKS,
      content: zcodePluginHooksFile(contents),
    },
    {
      relativePath: `skills/${target.skillName}/SKILL.md`,
      content: contents.skill,
    },
  ];
}

function json(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
