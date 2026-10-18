/** Register `capture` on the hosts whose one-click install can own a hook file.
 *
 * The command is the packaged core binary. It reads the hook's JSON on stdin
 * and writes visible text into Evidence. Stdout stays empty, so the host does
 * not treat the capture as another instruction.
 *
 * WorkBuddy's command lives in its plugin tree. Hermes and OpenClaw deliver
 * the turn through a single memory-provider slot; those are not written here,
 * because taking the slot replaces whatever plugin is already there. ZCode
 * merges its hooks into the same config file as its MCP entry, and its
 * UserPromptSubmit is the recall injection entry (which hands the prompt to
 * capture itself), so only Stop carries a capture command there.
 */

import path from "node:path";

import type { RecallHostLayout, RecallRuntime } from "./recall-installer.js";
import { sameShellCommand, shellWords } from "./shell-words.js";

/** A place on the host that may still hold something of ours, which nothing
 * here can settle: a person has to look. */
export interface RemovalFinding {
  path: string;
  where: string;
}

export interface CaptureFile {
  path: string;
  before: string | null;
  after: string;
  /** The exact command string written into the host file, used to remove it. */
  command: string;
  kind: "merge" | "owned";
}

const COMMAND_HOSTS: Record<string, readonly string[]> = {
  cursor: ["beforeSubmitPrompt", "afterAgentResponse"],
  "claude-code": ["UserPromptSubmit", "Stop", "SubagentStop"],
  codex: ["UserPromptSubmit", "Stop"],
};

export function resolveCaptureCommand(runtime: RecallRuntime): string {
  if (runtime.captureCommand !== undefined && runtime.captureCommand !== "") {
    return runtime.captureCommand;
  }
  const installRoot = path.resolve(path.dirname(runtime.mcpEntryPath), "..");
  const executable =
    process.platform === "win32" ? "one-memory.exe" : "one-memory";
  return path.join(installRoot, "core", executable);
}

export function quoteCaptureCommand(command: string): string {
  return `'${command.replaceAll("'", `'\\''`)}'`;
}

/** Run a hook without letting its failure become the host's error.
 *
 * Cursor and WorkBuddy treat a non-zero hook as a failed send and show the
 * program's own text. The installed core can reject `capture` and `boundary`,
 * and the MCP launcher is not a CLI at all. Either one used to stop the turn.
 * A capture stays silent. A boundary that cannot answer prints nothing: a host
 * that finds no `hookSpecificOutput` reads raw stdout as context, so even
 * `{"continue": true}` would reach the model as text.
 */
export function failOpenHookCommand(
  binary: string,
  args: string,
  mode: "silent" | "context",
  dataDir?: string,
): string {
  const quoted = quoteCaptureCommand(binary);
  const prefix =
    dataDir !== undefined && dataDir !== ""
      ? `ONE_MEMORY_DATA_DIR=${quoteCaptureCommand(dataDir)} `
      : "";
  const invocation = `${prefix}${quoted} ${args}`;
  const script =
    mode === "silent"
      ? `${invocation} >/dev/null 2>&1; exit 0`
      : `out=$(${invocation} 2>/dev/null); status=$?; if [ "$status" -eq 0 ] && [ -n "$out" ]; then printf '%s\\n' "$out"; fi; exit 0`;
  return `sh -c ${quoteCaptureCommand(script)}`;
}

export async function planCaptureFiles(
  layout: RecallHostLayout,
  runtime: RecallRuntime,
  readFile: (target: string) => Promise<string | null>,
): Promise<CaptureFile[]> {
  const sourceId = layout.hostId;
  const command = captureCommandFor(layout, runtime);
  const filePath = captureFilePath(layout);
  if (filePath !== null && sourceId in COMMAND_HOSTS) {
    const before = await readFile(filePath);
    return [
      {
        path: filePath,
        before,
        after: mergeCommandHooks(
          before,
          sourceId,
          command,
          COMMAND_HOSTS[sourceId],
        ),
        command,
        kind: "merge",
      },
    ];
  }
  if (filePath !== null && sourceId === "pi") {
    const command = resolveCaptureCommand(runtime);
    return [
      {
        path: filePath,
        before: await readFile(filePath),
        after: piExtension(command, runtime.dataDir),
        command,
        kind: "owned",
      },
    ];
  }
  if (filePath !== null && sourceId === "opencode") {
    const command = resolveCaptureCommand(runtime);
    return [
      {
        path: filePath,
        before: await readFile(filePath),
        after: openCodePlugin(command, runtime.dataDir),
        command,
        kind: "owned",
      },
    ];
  }
  if (filePath !== null && sourceId === "zcode") {
    const capture = captureCommandFor(layout, runtime);
    const recallCli = runtime.recallCliPath;
    const injection =
      recallCli === undefined
        ? null
        : zcodeInjectionCommand(runtime, recallCli);
    const before = await readFile(filePath);
    return [
      {
        path: filePath,
        before,
        after: mergeZcodeHooks(before, capture, injection),
        command: capture,
        kind: "merge",
      },
    ];
  }
  return [];
}

export function captureFilePath(layout: RecallHostLayout): string | null {
  const root = layout.containmentRoot;
  if (layout.hostId === "claude-code") return path.join(root, "settings.json");
  if (layout.hostId in COMMAND_HOSTS) return path.join(root, "hooks.json");
  if (layout.hostId === "pi")
    return path.join(root, "extensions", "one-memory-capture.js");
  if (layout.hostId === "opencode")
    return path.join(root, "plugin", "one-memory-capture.js");
  if (layout.hostId === "zcode") return layout.configPath;
  return null;
}

/** The hook command an install from this runtime writes for this host. */
export function captureCommandFor(
  layout: RecallHostLayout,
  runtime: RecallRuntime,
): string {
  return failOpenHookCommand(
    resolveCaptureCommand(runtime),
    `capture ${layout.hostId}`,
    "silent",
    runtime.dataDir,
  );
}

export const UNPARSEABLE_HOOKS = "不是合法的 JSON，无法确认其中的采集命令";
export const UNREADABLE = "无法读取";

/** What a host's capture file says about us, read the way the host reads it.
 * `ours` when it runs `expected` however spelled. A command that names our
 * capture but is not `expected`, or a file that cannot be read, is a finding:
 * it is neither proof of absence nor something safe to remove. */
export async function inspectCapture(
  layout: RecallHostLayout,
  expected: string,
  readFile: (target: string) => Promise<string | null>,
): Promise<{ ours: boolean; findings: RemovalFinding[] }> {
  const filePath = captureFilePath(layout);
  if (filePath === null) return { ours: false, findings: [] };
  let raw: string | null;
  try {
    raw = await readFile(filePath);
  } catch {
    return { ours: false, findings: [{ path: filePath, where: UNREADABLE }] };
  }
  if (raw === null) return { ours: false, findings: [] };
  if (!(layout.hostId in COMMAND_HOSTS) && layout.hostId !== "zcode") {
    return { ours: true, findings: [] };
  }
  let document: unknown;
  try {
    document = JSON.parse(raw);
  } catch {
    return {
      ours: false,
      findings: [{ path: filePath, where: UNPARSEABLE_HOOKS }],
    };
  }
  let ours = false;
  const findings: RemovalFinding[] = [];
  for (const { where, command } of hookCommands(document, layout.hostId)) {
    if (layout.hostId === "zcode") {
      // Both of ZCode's hook commands are ours by spelling — the capture
      // entry and the `--inject zcode` entry — and `expected` names only the
      // capture one, so the inject spelling cannot be told from a stale
      // variant without the runtime that wrote it.
      if (sameShellCommand(command, expected) || namesZcodeHook(command)) {
        ours = true;
      }
      continue;
    }
    if (sameShellCommand(command, expected)) ours = true;
    else if (namesCapture(command, layout.hostId))
      findings.push({ path: filePath, where });
  }
  return { ours, findings };
}

/** Commands still naming our capture in a hook file uninstall has already
 * stripped of the recorded one. */
export function captureLeftovers(
  raw: string,
  hostId: string,
  filePath: string,
): RemovalFinding[] {
  return hookCommands(JSON.parse(raw) as unknown)
    .filter(({ command }) => namesCapture(command, hostId))
    .map(({ where }) => ({ path: filePath, where }));
}

export function namesCapture(command: string, hostId: string): boolean {
  const words = shellWords(command);
  if (words === null)
    return command.includes("capture") && command.includes(hostId);
  return words.some(
    (word, index) => word === "capture" && words[index + 1] === hostId,
  );
}

function hookCommands(
  document: unknown,
  hostId?: string,
): Array<{ where: string; command: string }> {
  if (!document || typeof document !== "object") return [];
  const hooks = (document as { hooks?: unknown }).hooks;
  if (!hooks || typeof hooks !== "object") return [];
  // ZCode nests its event lists under `hooks.events`; every other group-shaped
  // host hangs them directly off `hooks`.
  const container =
    hostId === "zcode" ? (hooks as { events?: unknown }).events : hooks;
  if (!container || typeof container !== "object" || Array.isArray(container)) {
    return [];
  }
  const commands: Array<{ where: string; command: string }> = [];
  const visit = (entry: unknown, where: string): void => {
    if (!entry || typeof entry !== "object") return;
    const record = entry as Record<string, unknown>;
    if (typeof record.command === "string")
      commands.push({ where: `${where}.command`, command: record.command });
    if (Array.isArray(record.hooks))
      for (const [index, hook] of record.hooks.entries())
        visit(hook, `${where}.hooks[${index}]`);
  };
  for (const [event, entries] of Object.entries(container))
    if (Array.isArray(entries))
      for (const [index, entry] of entries.entries())
        visit(entry, `hooks.${event}[${index}]`);
  return commands;
}

/** True when stripping our command left no hook entries. */
export function strippedCaptureIsEmpty(raw: string): boolean {
  const parsed: unknown = JSON.parse(raw);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    return false;
  }
  const hooks = (parsed as { hooks?: unknown }).hooks;
  if (hooks === undefined) return true;
  return (
    typeof hooks === "object" &&
    hooks !== null &&
    !Array.isArray(hooks) &&
    Object.keys(hooks).length === 0
  );
}

export function stripCaptureCommand(raw: string, command: string): string {
  const parsed: unknown = JSON.parse(raw);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    return raw;
  }
  const document = parsed as Record<string, unknown>;
  const hooks = document.hooks;
  if (!hooks || typeof hooks !== "object" || Array.isArray(hooks)) return raw;
  for (const [event, entries] of Object.entries(
    hooks as Record<string, unknown>,
  )) {
    if (!Array.isArray(entries)) continue;
    const kept = entries.flatMap((entry) => stripEntry(entry, command));
    if (kept.length === 0) delete (hooks as Record<string, unknown>)[event];
    else (hooks as Record<string, unknown>)[event] = kept;
  }
  if (Object.keys(hooks as object).length === 0) delete document.hooks;
  return `${JSON.stringify(document, null, 2)}\n`;
}

function stripEntry(entry: unknown, command: string): unknown[] {
  if (!entry || typeof entry !== "object") return [entry];
  const record = entry as Record<string, unknown>;
  if (
    typeof record.command === "string" &&
    sameShellCommand(record.command, command)
  )
    return [];
  if (!Array.isArray(record.hooks)) return [entry];
  const hooks = record.hooks.filter(
    (hook) =>
      !hook ||
      typeof hook !== "object" ||
      !isCommand((hook as { command?: unknown }).command, command),
  );
  if (hooks.length === 0) return [];
  return [{ ...record, hooks }];
}

function isCommand(value: unknown, command: string): boolean {
  return typeof value === "string" && sameShellCommand(value, command);
}

function mergeCommandHooks(
  raw: string | null,
  sourceId: string,
  command: string,
  events: readonly string[],
): string {
  let document: Record<string, unknown> = {};
  if (raw !== null && raw.trim() !== "") {
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error(
        `${sourceId} hook configuration is not a JSON object; no files were changed`,
      );
    }
    document = parsed as Record<string, unknown>;
  }
  const hooks =
    document.hooks &&
    typeof document.hooks === "object" &&
    !Array.isArray(document.hooks)
      ? (document.hooks as Record<string, unknown>)
      : {};
  for (const event of events) {
    const entries = Array.isArray(hooks[event]) ? [...hooks[event]] : [];
    if (sourceId === "cursor") {
      // Replace the previous One Memory command for this event instead of
      // stacking one per install. A user can have several historical paths
      // from trial/current builds; leaving them all would make one prompt
      // emit several contexts.
      for (let index = entries.length - 1; index >= 0; index -= 1) {
        const entry = entries[index];
        if (
          entry &&
          typeof entry === "object" &&
          typeof (entry as { command?: unknown }).command === "string" &&
          namesCapture((entry as { command: string }).command, sourceId)
        ) {
          entries.splice(index, 1);
        }
      }
      if (
        !entries.some(
          (entry) =>
            entry &&
            typeof entry === "object" &&
            isCommand((entry as { command?: unknown }).command, command),
        )
      ) {
        entries.push({ command });
      }
    } else {
      // Claude Code, Codex and the other group-shaped hosts have the same
      // accumulation failure: a different historical binary/data root is not
      // byte-equal, so it is appended instead of replacing the old entry.
      // Drop only groups whose commands are recognizably ours for this host;
      // a mixed group keeps its foreign commands.
      for (let index = entries.length - 1; index >= 0; index -= 1) {
        const entry = entries[index];
        if (!entry || typeof entry !== "object") continue;
        const group = entry as { hooks?: unknown };
        if (!Array.isArray(group.hooks)) continue;
        const kept = group.hooks.filter((hook) => {
          if (!hook || typeof hook !== "object") return true;
          const value = (hook as { command?: unknown }).command;
          return !(typeof value === "string" && namesCapture(value, sourceId));
        });
        if (kept.length === 0) entries.splice(index, 1);
        else if (kept.length !== group.hooks.length)
          entries[index] = {
            ...(entry as Record<string, unknown>),
            hooks: kept,
          };
      }
      if (
        !entries.some(
          (entry) =>
            entry &&
            typeof entry === "object" &&
            Array.isArray((entry as { hooks?: unknown }).hooks) &&
            (entry as { hooks: Array<{ command?: unknown }> }).hooks.some(
              (hook) => isCommand(hook.command, command),
            ),
        )
      ) {
        entries.push({
          matcher: "",
          hooks: [{ type: "command", command, timeout: 10 }],
        });
      }
    }
    hooks[event] = entries;
  }
  document.hooks = hooks;
  if (sourceId === "cursor" && document.version === undefined) {
    document.version = 1;
  }
  return `${JSON.stringify(document, null, 2)}\n`;
}

function captureEnvironment(dataDir?: string): string {
  if (dataDir === undefined || dataDir === "") return "process.env";
  return `Object.assign({}, process.env, { ONE_MEMORY_DATA_DIR: ${JSON.stringify(dataDir)} })`;
}

function piExtension(command: string, dataDir?: string): string {
  return `// MemHub capture. Tool results are not sent.
import { spawnSync } from "node:child_process";

const command = ${JSON.stringify(command)};

function sessionId(ctx) {
  const file = ctx?.sessionManager?.getSessionFile?.();
  if (typeof file === "string" && file.trim()) return file.trim();
  return "pi";
}

function send(payload) {
  try {
    spawnSync(command, ["capture", "pi"], {
      input: JSON.stringify(payload),
      timeout: 3000,
      stdio: ["pipe", "ignore", "ignore"],
      env: ${captureEnvironment(dataDir)},
    });
  } catch {
    // A capture failure must not stop the turn.
  }
}

export default function oneMemoryCapture(pi) {
  pi.on("before_agent_start", (event, ctx) => {
    send({
      hook_event_name: "before_agent_start",
      session_id: sessionId(ctx),
      prompt: event?.prompt ?? "",
      cwd: event?.systemPromptOptions?.cwd,
    });
  });
  pi.on("turn_end", (event, ctx) => {
    send({
      hook_event_name: "turn_end",
      session_id: sessionId(ctx),
      message: event?.message,
    });
  });
}
`;
}

function openCodePlugin(command: string, dataDir?: string): string {
  return `// MemHub capture. Only text parts are sent.
import { spawnSync } from "node:child_process";

const command = ${JSON.stringify(command)};
const pending = new Map();

function remember(sessionId, role, text) {
  if (!sessionId || !text) return;
  const turn = pending.get(sessionId) ?? [];
  turn.push({ role, parts: [{ type: "text", text }] });
  pending.set(sessionId, turn);
}

function send(sessionId) {
  const messages = pending.get(sessionId);
  pending.delete(sessionId);
  if (!messages || messages.length === 0) return;
  try {
    spawnSync(command, ["capture", "opencode"], {
      input: JSON.stringify({
        hook_event_name: "session.status",
        status: "idle",
        session_id: sessionId,
        messages,
      }),
      timeout: 3000,
      stdio: ["pipe", "ignore", "ignore"],
      env: ${captureEnvironment(dataDir)},
    });
  } catch {
    // A capture failure must not stop the turn.
  }
}

export default async function oneMemoryCapture() {
  return {
    event: async ({ event }) => {
      try {
        const type = event?.type;
        const props = event?.properties ?? {};
        const sessionId = props.sessionID ?? props.sessionId ?? event?.sessionID;
        if (type === "message.part.updated" || type === "message.part.delta") {
          const part = props.part ?? props;
          if (part?.type === "text" && typeof part.text === "string") {
            remember(sessionId, props.role ?? "assistant", part.text);
          }
        }
        if (type === "session.idle" || (type === "session.status" && props.status === "idle")) {
          send(sessionId);
        }
      } catch {
        // Ignore a shape this OpenCode build does not use.
      }
    },
  };
}
`;
}

/** The recall injection entry ZCode's UserPromptSubmit and SessionStart run.
 *
 * Same shape as the WorkBuddy plugin's hook (codebuddy-plugin.ts), built here
 * because a config-file host names the command in the user's own config
 * rather than in a plugin file we own. The entry recalls with the prompt's
 * words and answers `additionalContext`; it also hands the prompt to
 * `<core> capture zcode` without waiting, which is why ZCode gets no separate
 * UserPromptSubmit capture hook — one hook per event speaks, and this is that
 * one. Absent when the runtime ships no recall command; a Stop capture hook
 * is still installed, so capture degrades alone.
 */
function zcodeInjectionCommand(
  runtime: RecallRuntime,
  recallCliPath: string,
): string {
  const environment = [
    'ELECTRON_RUN_AS_NODE="1"',
    'ONE_MEMORY_BOUNDARY_VIA_BROKER="1"',
    `ONE_MEMORY_BROKER_DISCOVERY=${quoteCaptureCommand(runtime.discoveryPath)}`,
    ...(runtime.credentialFile === undefined
      ? []
      : [
          `ONE_MEMORY_HOST_CREDENTIAL_FILE=${quoteCaptureCommand(runtime.credentialFile)}`,
        ]),
    ...(runtime.dataDir === undefined
      ? []
      : [`ONE_MEMORY_DATA_DIR=${quoteCaptureCommand(runtime.dataDir)}`]),
    `ONE_MEMORY_CAPTURE_COMMAND=${quoteCaptureCommand(resolveCaptureCommand(runtime))}`,
  ].join(" ");
  const invocation = `${environment} ${quoteCaptureCommand(runtime.runtimeCommand)} ${quoteCaptureCommand(recallCliPath)} --inject zcode`;
  // stderr is silent unless the operator explicitly asks for delivery
  // diagnosis; a failure prints nothing, because ZCode keeps raw stdout and
  // would read an error reply back into the turn as text.
  const script = `if [ "\${ONE_MEMORY_DEBUG_DELIVERY:-}" = "1" ]; then ${invocation}; else ${invocation} 2>/dev/null; fi; exit 0`;
  return `sh -c ${quoteCaptureCommand(script)}`;
}

/** Post-strip residue check for ZCode's shared config file.
 *
 * Runs where the strip itself is composed with the server removal by the
 * caller, so this reports only what a person has to look at: a file that
 * cannot be parsed, or a command that still names us after everything
 * recorded was stripped. */
export function zcodeCaptureReview(
  current: string,
  filePath: string,
): RemovalFinding[] {
  let document: unknown;
  try {
    document = JSON.parse(stripZcodeManagedHooks(current));
  } catch {
    return [{ path: filePath, where: UNPARSEABLE_HOOKS }];
  }
  return hookCommands(document, "zcode")
    .filter(({ command }) => namesZcodeHook(command))
    .map(({ where }) => ({ path: filePath, where }));
}

/** True for either hook command this installer writes for ZCode. */
export function namesZcodeHook(command: string): boolean {
  return command.includes("--inject zcode") || namesCapture(command, "zcode");
}

/** Merge this install's hooks into ZCode's `hooks.events`.
 *
 * ZCode nests config-file hooks one level deeper than the other group-shaped
 * hosts (`hooks.events.<Event>`, not `hooks.<Event>`) and gates all of them on
 * `hooks.enabled` — both from the client's own configuration guide. Setting
 * that flag true when the user had it false is a side effect worth naming:
 * their own config-file hooks start running too. The alternative — installing
 * hooks the host never executes — silently captures nothing, which is worse.
 *
 * Old installs of ours are replaced rather than stacked, as for Claude Code:
 * a different historical binary path is not byte-equal and would otherwise
 * accumulate one group per install.
 */
function mergeZcodeHooks(
  raw: string | null,
  capture: string,
  injection: string | null,
): string {
  let document: Record<string, unknown> = {};
  if (raw !== null && raw.trim() !== "") {
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error(
        "zcode hook configuration is not a JSON object; no files were changed",
      );
    }
    document = parsed as Record<string, unknown>;
  }
  const hooks =
    document.hooks &&
    typeof document.hooks === "object" &&
    !Array.isArray(document.hooks)
      ? (document.hooks as Record<string, unknown>)
      : {};
  const events =
    hooks.events &&
    typeof hooks.events === "object" &&
    !Array.isArray(hooks.events)
      ? (hooks.events as Record<string, unknown>)
      : {};
  const planned = injection
    ? [
        { event: "SessionStart", command: injection, timeoutMs: 8000 },
        { event: "UserPromptSubmit", command: injection, timeoutMs: 8000 },
        { event: "Stop", command: capture, timeoutMs: 5000 },
      ]
    : [{ event: "Stop", command: capture, timeoutMs: 5000 }];
  for (const { event, command, timeoutMs } of planned) {
    const entries = Array.isArray(events[event]) ? [...events[event]] : [];
    for (let index = entries.length - 1; index >= 0; index -= 1) {
      const entry = entries[index];
      if (!entry || typeof entry !== "object") continue;
      const group = entry as { hooks?: unknown };
      if (!Array.isArray(group.hooks)) continue;
      const kept = group.hooks.filter((hook) => {
        if (!hook || typeof hook !== "object") return true;
        const value = (hook as { command?: unknown }).command;
        return !(typeof value === "string" && namesZcodeHook(value));
      });
      if (kept.length === 0) entries.splice(index, 1);
      else if (kept.length !== group.hooks.length)
        entries[index] = {
          ...(entry as Record<string, unknown>),
          hooks: kept,
        };
    }
    if (
      !entries.some(
        (entry) =>
          entry &&
          typeof entry === "object" &&
          Array.isArray((entry as { hooks?: unknown }).hooks) &&
          (entry as { hooks: Array<{ command?: unknown }> }).hooks.some(
            (hook) => isCommand(hook.command, command),
          ),
      )
    ) {
      entries.push({ hooks: [{ type: "command", command, timeoutMs }] });
    }
    events[event] = entries;
  }
  hooks.events = events;
  hooks.enabled = true;
  document.hooks = hooks;
  return `${JSON.stringify(document, null, 2)}\n`;
}

/** Config text with every hook this installer wrote for ZCode gone.
 *
 * Empty event lists are pruned and an emptied `events` container is removed,
 * like the flat hosts' strip. `hooks.enabled` stays: it is one flag for every
 * hook the user has, not ours alone, and we may have been the reason it was
 * on.
 */
export function stripZcodeManagedHooks(raw: string): string {
  const parsed: unknown = JSON.parse(raw);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    return raw;
  }
  const document = parsed as Record<string, unknown>;
  const hooks = document.hooks;
  if (!hooks || typeof hooks !== "object" || Array.isArray(hooks)) return raw;
  const events = (hooks as Record<string, unknown>).events;
  if (!events || typeof events !== "object" || Array.isArray(events)) {
    return raw;
  }
  const container = events as Record<string, unknown>;
  for (const [event, entries] of Object.entries(container)) {
    if (!Array.isArray(entries)) continue;
    const kept = entries.flatMap((entry) => {
      if (!entry || typeof entry !== "object") return [entry];
      const group = entry as { hooks?: unknown };
      if (!Array.isArray(group.hooks)) return [entry];
      const hooks = group.hooks.filter((hook) => {
        if (!hook || typeof hook !== "object") return true;
        const value = (hook as { command?: unknown }).command;
        return !(typeof value === "string" && namesZcodeHook(value));
      });
      if (hooks.length === 0) return [];
      if (hooks.length === group.hooks.length) return [entry];
      return [{ ...(entry as Record<string, unknown>), hooks }];
    });
    if (kept.length === 0) delete container[event];
    else container[event] = kept;
  }
  if (Object.keys(container).length === 0) {
    delete (hooks as Record<string, unknown>).events;
  }
  return `${JSON.stringify(document, null, 2)}\n`;
}
