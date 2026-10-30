/** The `.codebuddy-plugin` install shape, as WorkBuddy actually loads it.
 *
 * Every other host this installer supports takes one entry in one config file.
 * A plugin is different in kind: it is a self-contained directory the host
 * copies, plus one key in each of three registries the host owns. That is why
 * this dialect exists as its own module — the unit of install is a tree, not a
 * line.
 *
 * The shape below was read out of WorkBuddy 5.5.0's own bundle
 * (`/Applications/WorkBuddy.app/Contents/Resources/app.asar`, whose comments
 * ship unminified) rather than from documentation, because two of the three
 * registries are not mentioned in any docs and one of them is the difference
 * between "installed" and "loaded":
 *
 * - `plugins/known_marketplaces.json` → `[name].installLocation` is the
 *   authoritative marketplace root (`resolveMarketplaceRoot`).
 * - `<marketplaceRoot>/.codebuddy-plugin/marketplace.json` → the entry whose
 *   `name` matches, with the plugin directory at `marketplaceRoot + source`
 *   (`resolvePluginSourceFromManifest`). This copy is what skills load from.
 * - `plugins/installed_plugins.json` → the cache snapshot MCP servers load
 *   from. `computePluginMcpConfigs` says so in as many words: "marketplace
 *   source is not used as a fallback".
 * - `settings.json` → `enabledPlugins["<plugin>@<marketplace>"]` must be
 *   `true`. Without it the plugin is installed and never projected: the host
 *   builds its server set from the enabled ids, and ours is not one of the
 *   five in `DEFAULT_ENABLED_PLUGIN_IDS`. This is the file that makes the
 *   difference between an install that looks healthy and one that works.
 */

import { createHash } from "node:crypto";
import { failOpenHookCommand, quoteCaptureCommand } from "./capture-hooks.js";

const MARKETPLACE_MANIFEST = ".codebuddy-plugin/marketplace.json";
const PLUGIN_MANIFEST = ".codebuddy-plugin/plugin.json";
export const PLUGIN_HOOKS = "hooks/hooks.json";

/** Where a plugin-format host keeps the trees and registries recall touches. */
export interface CodebuddyPluginTarget {
  marketplaceName: string;
  pluginName: string;
  /** The skill directory hosts list by name. Kept apart from `pluginName`,
   * which registries key installs by and so cannot change under them. */
  skillName: string;
  /** Named in `installed_plugins.json` and used as the cache directory name.
   *
   * Fixed rather than derived from content: the host re-reads both copies off
   * disk, so a new directory per build would only leave old ones to collect.
   */
  version: string;
  /** `<hostDir>/plugins/marketplaces/<marketplaceName>` — ours outright. */
  marketplaceRoot: string;
  /** `<hostDir>/plugins/cache/<marketplaceName>` — ours outright, and the
   * highest directory of the cache copy that belongs to us, so it is what
   * uninstall removes. */
  cacheRoot: string;
  /** `<cacheRoot>/<pluginName>/<version>` — the snapshot MCP loads from. */
  installRoot: string;
  knownMarketplacesPath: string;
  installedPluginsPath: string;
  settingsPath: string;
}

/** `<plugin>@<marketplace>`, the id both registries key on. */
export function pluginId(target: CodebuddyPluginTarget): string {
  return `${target.pluginName}@${target.marketplaceName}`;
}

/** One shared file the host owns, and the single key in it that is ours.
 *
 * `seed` is only used when the file has to be created: `installed_plugins.json`
 * carries a schema version beside its map, and a file holding just our key
 * would be a file the host cannot read.
 */
export interface PluginRegistry {
  path: string;
  keyPath: string[];
  seed: Record<string, unknown>;
}

export function pluginRegistries(
  target: CodebuddyPluginTarget,
): PluginRegistry[] {
  return [
    {
      path: target.knownMarketplacesPath,
      keyPath: [target.marketplaceName],
      seed: {},
    },
    {
      path: target.installedPluginsPath,
      keyPath: ["plugins", pluginId(target)],
      seed: { version: 2, plugins: {} },
    },
    {
      path: target.settingsPath,
      keyPath: ["enabledPlugins", pluginId(target)],
      seed: {},
    },
  ];
}

/** The values our three registry keys hold when recall is installed.
 *
 * No `installedAt` or `lastUpdated`, though the host writes both on its own
 * installs. They are optional everywhere they are read — `installLocation` is
 * the only field `resolveMarketplaceRoot` looks at, and
 * `selectDaemonPluginCacheRecord` requires `installPath`, `version` and
 * `scope`, using the timestamps only to order several records of one plugin.
 * A timestamp made up at plan time would also make two plans of the same
 * install differ, which would spend the user's approval on nothing: the
 * preview fingerprint covers the exact bytes. When we installed is recorded in
 * our own journal, where it is true and where nobody else has to parse it.
 */
export function registryEntry(
  target: CodebuddyPluginTarget,
  registry: PluginRegistry,
): unknown {
  if (registry.path === target.knownMarketplacesPath) {
    return {
      type: "directory",
      source: { source: "directory", path: target.marketplaceRoot },
      installLocation: target.marketplaceRoot,
      description: `Marketplace from ${target.marketplaceRoot}`,
      autoUpdate: false,
    };
  }
  if (registry.path === target.installedPluginsPath) {
    // An array of one: the host stores several records per plugin so a managed
    // and a user install can coexist, and reads the highest-priority one.
    return [
      {
        scope: "user",
        installPath: target.installRoot,
        version: target.version,
      },
    ];
  }
  return true;
}

/** Whether a registry key holds an entry this installer wrote.
 *
 * Recognition is by the paths it points at rather than by a marker key of ours:
 * these are the host's own documents and an unknown field in them is a change
 * we would have to defend. Anything claiming our marketplace name while
 * pointing somewhere else is somebody else's, and refused.
 */
export function isManagedRegistryEntry(
  target: CodebuddyPluginTarget,
  registry: PluginRegistry,
  value: unknown,
): boolean {
  if (registry.path === target.knownMarketplacesPath) {
    if (!isRecord(value)) return false;
    return (
      value.type === "directory" &&
      value.installLocation === target.marketplaceRoot
    );
  }
  if (registry.path === target.installedPluginsPath) {
    const records = Array.isArray(value) ? value : [value];
    return (
      records.length > 0 &&
      records.every(
        (record) =>
          isRecord(record) && record.installPath === target.installRoot,
      )
    );
  }
  // Enablement is a bare boolean, so there is nothing in it to own. Either
  // value is the user's to have set, and both are restored verbatim.
  return typeof value === "boolean";
}

/** The files of the plugin tree, relative to the root they are written under.
 *
 * The marketplace copy and the cache copy differ by one file: only the
 * marketplace has the manifest listing its plugins. Everything else is the same
 * bytes in both, because the host reads skills from the marketplace copy and
 * MCP servers from the cache copy, and the two disagreeing would mean the model
 * is told about a tool that is configured differently.
 */
export interface PluginFile {
  relativePath: string;
  content: string;
}

/** What the recall injection hook needs to reach the broker as this install. */
export interface InjectionHookRuntime {
  runtimeCommand: string;
  recallCliPath: string;
  discoveryPath: string;
  credentialFile?: string;
  dataDir?: string;
  /** Core binary handed the prompt on UserPromptSubmit, so capture needs no
   * hook of its own. */
  captureCommand?: string;
}

export interface PluginContents {
  skill: string;
  server: unknown;
  cliCommand?: string;
  dataDir?: string;
  /** Absent when the runtime ships no recall command; then nothing is injected. */
  injection?: InjectionHookRuntime;
}

export function marketplaceTree(
  target: CodebuddyPluginTarget,
  contents: PluginContents,
): PluginFile[] {
  return [
    {
      relativePath: MARKETPLACE_MANIFEST,
      content: json({
        name: target.marketplaceName,
        description: "MemHub recall",
        plugins: [
          {
            name: target.pluginName,
            source: `./plugins/${target.pluginName}`,
            // `codebuddy plugin validate` warns when this is missing, even
            // though the plugin's own manifest carries it too. Its absence is
            // the only thing the host's own validator objected to.
            version: target.version,
            description: PLUGIN_DESCRIPTION,
          },
        ],
      }),
    },
    ...pluginTree(target, contents).map((file) => ({
      relativePath: `plugins/${target.pluginName}/${file.relativePath}`,
      content: file.content,
    })),
  ];
}

const PLUGIN_DESCRIPTION =
  "Recall confirmed memories from the local MemHub app";

function hostCli(explicit?: string): string {
  if (explicit !== undefined && explicit !== "") return explicit;
  throw new Error("workbuddy_hook_command_missing");
}

function captureHook(
  cliCommand?: string,
  dataDir?: string,
): {
  hooks: Array<{ type: "command"; command: string; timeout: number }>;
} {
  return {
    hooks: [
      {
        type: "command",
        command: failOpenHookCommand(
          hostCli(cliCommand),
          "capture workbuddy",
          "silent",
          dataDir,
        ),
        timeout: 10,
      },
    ],
  };
}

/** Run the recall command's injection mode the way the MCP entry is run.
 *
 * Same runtime, same `ELECTRON_RUN_AS_NODE`, same discovery and credential
 * file as the plugin's MCP server, so turning one-click insert off revokes
 * both at once. A failure prints nothing: `{"continue": true}` would be read
 * back into the turn as text, because the host keeps raw stdout.
 *
 * The entry writes straight to the hook's stdout. It prints one complete
 * reply or nothing, so there is nothing to hold back and reprint.
 */
export function injectionHookCommand(runtime: InjectionHookRuntime): string {
  const environment = [
    'ELECTRON_RUN_AS_NODE="1"',
    `ONE_MEMORY_BROKER_DISCOVERY=${quoteCaptureCommand(runtime.discoveryPath)}`,
    ...(runtime.credentialFile
      ? [
          `ONE_MEMORY_HOST_CREDENTIAL_FILE=${quoteCaptureCommand(runtime.credentialFile)}`,
        ]
      : []),
    ...(runtime.dataDir
      ? [`ONE_MEMORY_DATA_DIR=${quoteCaptureCommand(runtime.dataDir)}`]
      : []),
    ...(runtime.captureCommand
      ? [
          `ONE_MEMORY_CAPTURE_COMMAND=${quoteCaptureCommand(runtime.captureCommand)}`,
        ]
      : []),
  ].join(" ");
  const invocation = `${environment} ${quoteCaptureCommand(runtime.runtimeCommand)} ${quoteCaptureCommand(runtime.recallCliPath)} --inject workbuddy`;
  // stderr is silent unless the operator explicitly asks for delivery
  // diagnosis. The entry only writes a broker error code under that switch.
  const script = `if [ "\${ONE_MEMORY_DEBUG_DELIVERY:-}" = "1" ]; then ${invocation}; else ${invocation} 2>/dev/null; fi; exit 0`;
  return `sh -c ${quoteCaptureCommand(script)}`;
}

/** The host treats running past this as a blocked send. The entry enforces a
 * single process-start deadline below four seconds, including cold start. */
function injectionHook(runtime: InjectionHookRuntime): {
  hooks: Array<{ type: "command"; command: string; timeout: number }>;
} {
  return {
    hooks: [
      {
        type: "command",
        command: injectionHookCommand(runtime),
        timeout: 50,
      },
    ],
  };
}

/** The hook file both plugin copies carry.
 *
 * One hook per event speaks. WorkBuddy shallow-merges the `hookSpecificOutput`
 * of every hook on an event, later ones overwriting earlier ones, so two of
 * ours each carrying `additionalContext` would lose one of them. The
 * injection hook runs on SessionStart too, where it recalls only after a
 * compaction.
 *
 * The host runs every hook on an event at once and blocks the send when any
 * outlives its timeout, so UserPromptSubmit capture is handed to the same
 * entry, which starts it and does not wait for it.
 */
export function pluginHooksFile(
  contents: Pick<PluginContents, "cliCommand" | "dataDir" | "injection">,
): string {
  const capture = captureHook(contents.cliCommand, contents.dataDir);
  if (!contents.injection) {
    return json({
      hooks: { UserPromptSubmit: [capture], Stop: [capture] },
    });
  }
  const context = injectionHook({
    ...contents.injection,
    captureCommand: hostCli(contents.cliCommand),
  });
  return json({
    hooks: {
      SessionStart: [context],
      UserPromptSubmit: [context],
      Stop: [capture],
    },
  });
}

export function pluginTree(
  target: CodebuddyPluginTarget,
  contents: PluginContents,
): PluginFile[] {
  return [
    {
      relativePath: PLUGIN_MANIFEST,
      content: json({
        name: target.pluginName,
        version: target.version,
        description: PLUGIN_DESCRIPTION,
        // Declared explicitly although the host also finds `skills/` on its
        // own (`find-skills` ships no such field and its skill still loads).
        // Being explicit is what keeps the tree readable to a person deciding
        // whether to trust it.
        skills: [`./skills/${target.skillName}`],
        // Absolute paths, not `${CODEBUDDY_PLUGIN_ROOT}`. The host does expand
        // that variable, but our MCP entry ships in the One Memory bundle
        // rather than in the plugin, so there is nothing under the plugin root
        // to point at. This is the same entry the other nine hosts get.
        mcpServers: { "one-memory": contents.server },
        // WorkBuddy 5.5.6 consumes additionalContext on SessionStart and
        // UserPromptSubmit. Stop only runs capture: it prints nothing and does
        // not return continueRequest, so it does not start another turn.
        hooks: "./hooks/hooks.json",
      }),
    },
    {
      relativePath: PLUGIN_HOOKS,
      content: pluginHooksFile(contents),
    },
    {
      relativePath: `skills/${target.skillName}/SKILL.md`,
      content: contents.skill,
    },
  ];
}

/** Host bookkeeping inside the cache copy that no plan of ours can ever list.
 *
 * WorkBuddy marks a cached plugin version as in use by creating one
 * `.in_use/<pid>` file beside the manifest for every live process, and sweeps
 * the expired ones on its own schedule (`.last_inuse_sweep` at the plugins
 * root). Those files belong to the host and are written after we installed, so
 * they are absent from every plan by construction — including the re-plan an
 * install does against the file as it stands now.
 *
 * Measured on a real machine: three `.in_use/<pid>` files under
 * `plugins/cache/one-memory/one-memory-recall/1.0.0/`, one per running
 * WorkBuddy process. Because the install and uninstall guards both require
 * every file under a tree to be planned, WorkBuddy could not be re-linked or
 * installed at all while WorkBuddy was open: the desktop reported only its
 * generic "could not read the connection change preview".
 *
 * A leading dot is how the host names bookkeeping it owns: `.in_use/` here,
 * and `.staging-<pid>-<ts>-<rand>/` while it writes a cache entry. Neither is
 * a name a person gives a skill. Nothing else inside the cache copy is
 * excused, so an edited SKILL.md still stops an install.
 */
export function isPluginCacheBookkeeping(
  /** Structural: only the cache root is consulted, and every plugin dialect
   * has one. */
  target: { cacheRoot: string },
  filePath: string,
): boolean {
  const prefix = target.cacheRoot.endsWith("/")
    ? target.cacheRoot
    : `${target.cacheRoot}/`;
  if (!filePath.startsWith(prefix)) return false;
  return filePath
    .slice(prefix.length)
    .split("/")
    .some(
      (segment) =>
        segment.startsWith(".") && segment !== "." && segment !== "..",
    );
}

/** Read the one entry that is ours, without disturbing the rest.
 *
 * `undefined` means the key is absent, which is different from a key holding
 * `null`: the first is a file we have never touched, the second is a value we
 * would have to put back.
 */
export function readRegistryEntry(
  raw: string | null,
  registry: PluginRegistry,
  label: string,
): unknown {
  if (raw === null) return undefined;
  let node: unknown = parseRegistry(raw, registry.path, label);
  for (const key of registry.keyPath) {
    if (!isRecord(node)) return undefined;
    node = node[key];
  }
  return node;
}

export function putRegistryEntry(
  raw: string | null,
  registry: PluginRegistry,
  label: string,
  value: unknown,
): string {
  const document =
    raw === null
      ? structuredClone(registry.seed)
      : parseRegistry(raw, registry.path, label);
  assignAtPath(document, registry.keyPath, value, registry.path, label);
  return serializeRegistry(document, raw);
}

/** The registry with our key gone, or holding what it held before us.
 *
 * Whether the file should exist at all is not decided here but by whether it
 * existed before we installed, which the journal records — the same rule the
 * other dialects follow, and for the same reason: a host that ships an empty
 * registry is entitled to get an empty registry back.
 */
export function removeRegistryEntry(
  raw: string | null,
  registry: PluginRegistry,
  label: string,
  reinstate: unknown,
): string {
  const document =
    raw === null
      ? structuredClone(registry.seed)
      : parseRegistry(raw, registry.path, label);
  if (reinstate !== undefined) {
    assignAtPath(document, registry.keyPath, reinstate, registry.path, label);
    return serializeRegistry(document, raw);
  }
  let node: unknown = document;
  for (const key of registry.keyPath.slice(0, -1)) {
    if (!isRecord(node)) return serializeRegistry(document, raw);
    node = node[key];
  }
  if (isRecord(node))
    delete node[registry.keyPath[registry.keyPath.length - 1]];
  return serializeRegistry(document, raw);
}

/** A stable digest of the one value that is ours.
 *
 * Ownership of a shared registry file is the value at our key, not the bytes
 * of the file the host and every other plugin also write to. Comparing the
 * whole file made uninstall refuse whenever anything else in it moved — a
 * plugin installed since, a key reordered, two spaces where there was one.
 * Issue #29 could not be uninstalled at all because of that.
 *
 * `null` means the key is absent, which is a different fact from a key holding
 * `null` and must stay distinguishable.
 */
export function registryManagedDigest(value: unknown): string | null {
  if (value === undefined) return null;
  return createHash("sha256")
    .update(canonicalJson(value), "utf8")
    .digest("hex");
}

/** JSON with object keys sorted, so formatting cannot change a digest. */
export function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== "object")
    return JSON.stringify(value) ?? "null";
  if (Array.isArray(value))
    return `[${value.map((item) => canonicalJson(item)).join(",")}]`;
  const record = value as Record<string, unknown>;
  return `{${Object.keys(record)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`)
    .join(",")}}`;
}

/** The document with our key removed, as canonical JSON.
 *
 * Comparing this against what the file held before we installed is how a
 * registry can be handed back byte for byte when nothing else has moved, and
 * merged rather than reverted when something has.
 */
export function registryWithoutOurs(
  raw: string,
  registry: PluginRegistry,
  label: string,
): string {
  const document = parseRegistry(raw, registry.path, label);
  const withoutOurs = structuredClone(document);
  let node: unknown = withoutOurs;
  for (const key of registry.keyPath.slice(0, -1)) {
    if (isRecord(node)) node = node[key];
  }
  if (isRecord(node))
    delete node[registry.keyPath[registry.keyPath.length - 1]];
  return canonicalJson(withoutOurs);
}

/** Whether our key is the only thing in the file beyond the seed.
 *
 * Only then may a file we created be deleted outright on uninstall. Anything
 * else in it — a key the user or another plugin added after we installed —
 * means removing our key and leaving the file, rather than deleting work that
 * was never ours.
 */
export function registryHoldsOnlyOurs(
  raw: string,
  registry: PluginRegistry,
  label: string,
): boolean {
  return (
    registryWithoutOurs(raw, registry, label) === canonicalJson(registry.seed)
  );
}

function parseRegistry(
  raw: string,
  registryPath: string,
  label: string,
): Record<string, unknown> {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch (error) {
    // The host reads `settings.json` with a JSONC parser, so comments in it
    // would be legal for it and unreadable to us. Refusing loses nothing;
    // parsing it as JSON5 and writing back plain JSON would silently delete
    // whatever the user wrote in those comments.
    throw new Error(
      `${label} ${basename(registryPath)} is not valid JSON; no files were changed`,
      { cause: error },
    );
  }
  if (!isRecord(value)) {
    throw new Error(
      `${label} ${basename(registryPath)} must contain a JSON object`,
    );
  }
  return value;
}

/** Re-serialise a registry the way the host writes it.
 *
 * Two spaces and no closing newline: that is what WorkBuddy 5.5.0 leaves on
 * disk for all three files, verified by re-serialising them and comparing
 * bytes. Matching it is what lets uninstall be byte-exact rather than merely
 * equivalent, which for a file holding 49 other plugins is worth having.
 */
function serializeRegistry(
  document: Record<string, unknown>,
  raw: string | null,
): string {
  const text = JSON.stringify(document, null, 2);
  return raw?.endsWith("\n") ? `${text}\n` : text;
}

function assignAtPath(
  document: Record<string, unknown>,
  keyPath: string[],
  value: unknown,
  registryPath: string,
  label: string,
): void {
  let node = document;
  for (const key of keyPath.slice(0, -1)) {
    const existing = node[key];
    // Anything here that is not an object belongs to the host and is not ours
    // to reinterpret; refusing beats silently replacing it.
    if (existing !== undefined && !isRecord(existing)) {
      throw new Error(
        `${label} ${basename(registryPath)} has a non-object at ${keyPath.join(".")}; no files were changed`,
      );
    }
    node[key] = { ...(existing ?? {}) };
    node = node[key] as Record<string, unknown>;
  }
  node[keyPath[keyPath.length - 1]] = value;
}

function json(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function basename(target: string): string {
  return target.slice(target.lastIndexOf("/") + 1);
}
