// Parsers for the commands that attach to the Runtime directly:
// `scan-roots` (alias `path`), `scan`, `backup`, `write`.
//
// Rules shared by all of them:
//
// - **Only irreversible operations ask for `--yes`.** `backup restore`
//   overwrites the database, so without `--yes` it previews and changes
//   nothing. Registering a scan root, scanning a source, creating a backup and
//   submitting a write are direct. A legacy `--yes` on a direct command is
//   accepted and ignored so older scripts keep working.
// - **Unknown or repeated flags are rejected**, never ignored: a dropped typo
//   would silently change what the command does.
// - **Output is machine-readable.** `--json` emits one document on stdout.
//
// The commands talk to the Runtime's HTTP API, not to the database and not to
// the SDK: CLI and desktop are clients of the same Runtime.

export type ScanRootsCommand =
  | {
      readonly kind: "add";
      readonly root: string;
      readonly source?: string;
      readonly recursive: boolean;
    }
  | { readonly kind: "remove"; readonly root: string };

export type ScanCommand =
  | { readonly kind: "source"; readonly sourceId: string }
  /** Hidden alias of `sources connect --all-detected --import`. */
  | { readonly kind: "all" };

export type BackupCommand =
  | { readonly verb: "create"; readonly target: string }
  | {
      readonly verb: "restore";
      readonly source: string;
      readonly confirmed: boolean;
      /** Legacy `--confirm <path>`: accepted, and must repeat `--source`. */
      readonly confirmPath?: string;
    };

interface ParsedFlags {
  readonly values: Map<string, string>;
  readonly switches: Set<string>;
}

/** Read `--flag value` pairs and bare switches; null on anything unknown,
 * repeated or missing its value. */
function parseFlags(
  args: readonly string[],
  valueFlags: readonly string[],
  switchFlags: readonly string[],
): ParsedFlags | null {
  const values = new Map<string, string>();
  const switches = new Set<string>();
  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    if (switchFlags.includes(arg)) {
      if (switches.has(arg)) return null;
      switches.add(arg);
      continue;
    }
    if (!valueFlags.includes(arg) || values.has(arg)) return null;
    const value = args[index + 1];
    if (value === undefined || value.startsWith("--")) return null;
    values.set(arg, value);
    index += 1;
  }
  return { values, switches };
}

const SOURCE_ID = /^[a-z0-9][a-z0-9-]{0,127}$/;

/** `scan-roots add --root <path> [--source <id>] [--no-recursive]` and
 * `scan-roots remove --root <path>` (`list` is answered separately). */
export function parseScanRootsCommand(
  args: readonly string[],
): ScanRootsCommand | null {
  const verb = args[0];
  if (verb === "add") {
    const flags = parseFlags(
      args.slice(1),
      ["--root", "--source"],
      ["--no-recursive", "--yes", "--json"],
    );
    const root = flags?.values.get("--root");
    if (!flags || root === undefined) return null;
    const source = flags.values.get("--source");
    if (source !== undefined && !SOURCE_ID.test(source)) return null;
    return {
      kind: "add",
      root,
      ...(source === undefined ? {} : { source }),
      recursive: !flags.switches.has("--no-recursive"),
    };
  }
  if (verb === "remove") {
    const flags = parseFlags(args.slice(1), ["--root"], ["--yes", "--json"]);
    const root = flags?.values.get("--root");
    return flags && root !== undefined ? { kind: "remove", root } : null;
  }
  return null;
}

/** `scan --source <id>`. A scan names a source: the Runtime has no endpoint
 * that scans a bare path, so a missing `--source` is a usage error rather
 * than a preview of something that can never run. */
export function parseScanCommand(args: readonly string[]): ScanCommand | null {
  const flags = parseFlags(args, ["--source"], ["--all", "--yes", "--json"]);
  if (!flags) return null;
  const source = flags.values.get("--source");
  if (flags.switches.has("--all")) {
    return source === undefined ? { kind: "all" } : null;
  }
  return source !== undefined && SOURCE_ID.test(source)
    ? { kind: "source", sourceId: source }
    : null;
}

/** `backup create --target <path>` and
 * `backup restore --source <path> [--yes]`. */
export function parseBackupCommand(
  args: readonly string[],
): BackupCommand | null {
  const verb = args[0];
  if (verb === "create") {
    const flags = parseFlags(args.slice(1), ["--target"], ["--yes", "--json"]);
    const target = flags?.values.get("--target");
    return flags && target !== undefined ? { verb: "create", target } : null;
  }
  if (verb === "restore") {
    const flags = parseFlags(
      args.slice(1),
      ["--source", "--confirm"],
      ["--yes", "--json"],
    );
    const source = flags?.values.get("--source");
    if (!flags || source === undefined) return null;
    const confirmPath = flags.values.get("--confirm");
    return {
      verb: "restore",
      source,
      confirmed: flags.switches.has("--yes"),
      ...(confirmPath === undefined ? {} : { confirmPath }),
    };
  }
  return null;
}

/** Whether a legacy `--confirm <path>` names the same restore source.
 *
 * The comparison is lexical on purpose: the CLI must not resolve symlinks or
 * touch the filesystem before Core has validated the managed source. A
 * different string is a different confirmation.
 */
export function restoreConfirmationMatches(
  source: string,
  confirmPath: string | undefined,
): boolean {
  return confirmPath === undefined || confirmPath === source;
}

export interface WriteSubmitCommand {
  readonly kind: "submit";
  readonly session: string;
  readonly source: string;
  readonly messages_file: string;
  readonly request_id?: string;
  readonly memory_at?: string;
  readonly priority?: "live" | "normal";
}

export interface WriteStatusCommand {
  readonly kind: "status";
  readonly id: string;
}

export type WriteCommand = WriteSubmitCommand | WriteStatusCommand;

/** Parse `write submit ...` and `write status <request-id>` (or `--id`). */
export function parseWriteCommand(
  args: readonly string[],
): WriteCommand | null {
  if (args.filter((item) => item === "--json").length > 1) return null;
  const meaningful = args.filter((item) => item !== "--json");
  if (meaningful[0] === "status") {
    const rest = meaningful.slice(1);
    if (rest.length === 1 && !rest[0].startsWith("--")) {
      return { kind: "status", id: rest[0] };
    }
    const status = parseTaskCommand(rest);
    return status === null ? null : { kind: "status", id: status.id };
  }
  // `write --session ...` without `submit` stays accepted for older scripts.
  const input = meaningful[0] === "submit" ? meaningful.slice(1) : meaningful;
  const flags = parseFlags(
    input,
    ["--session", "--source", "--messages-file", "--request-id", "--memory-at"],
    ["--live"],
  );
  if (!flags) return null;
  const session = flags.values.get("--session");
  const source = flags.values.get("--source") ?? "memhub-cli";
  const messagesFile = flags.values.get("--messages-file");
  const requestId = flags.values.get("--request-id");
  const memoryAt = flags.values.get("--memory-at");
  if (session === undefined || messagesFile === undefined || !source)
    return null;
  if (
    memoryAt !== undefined &&
    (!Number.isFinite(Date.parse(memoryAt)) ||
      !/(?:Z|[+-]\d\d:\d\d)$/.test(memoryAt))
  )
    return null;
  const command: WriteSubmitCommand = {
    kind: "submit",
    session,
    source,
    messages_file: messagesFile,
    ...(requestId === undefined ? {} : { request_id: requestId }),
    ...(memoryAt === undefined ? {} : { memory_at: memoryAt }),
  };
  return flags.switches.has("--live")
    ? { ...command, priority: "live" }
    : command;
}

export interface TaskCommand {
  readonly id: string;
}

/** `--id <request-id>`: the legacy `task` spelling of `write status`. */
export function parseTaskCommand(args: readonly string[]): TaskCommand | null {
  if (args.filter((item) => item === "--json").length > 1) return null;
  const flags = parseFlags(
    args.filter((item) => item !== "--json"),
    ["--id"],
    [],
  );
  const id = flags?.values.get("--id");
  return id === undefined ? null : { id };
}
