// A6: the product commands Runtime exposes on the CLI.
//
// These are the six commands the objective names: path, adapter, scan, write,
// task, backup. Two rules run through all of them:
//
// - **Mutating commands are two-phase.** Without `--yes` they print a plan and
//   change nothing. This is the convention the existing install/service
//   commands already follow, and it is why `scan` and `backup` default to a
//   plan rather than quietly doing work.
// - **Output is machine-readable.** `--json` emits one document on stdout so an
//   agent can consume it; the human form is text. Never both, so a caller that
//   parses stdout is not surprised by prose.
//
// The commands talk to the Runtime's HTTP API, not to the database and not to
// HY. That is the A6 rule: CLI and Electron are clients of the same Runtime.

/** What a mutating command would do, before it is allowed to do it. */
export interface Plan {
  readonly action: string;
  readonly target: string;
  readonly detail?: string;
  /** Which form of the command this plan is for, when one name has several.
   *
   * `backup` has `create` and `restore`, and applying the wrong one is not a
   * harmless mistake — so the verb travels with the plan the user confirmed.
   */
  readonly verb?: string;
  /** The source the Runtime endpoint addresses, when the plan names one.
   *
   * Carried on the plan rather than re-parsed at apply time so the plan the
   * user confirmed and the request actually sent cannot disagree.
   */
  readonly sourceId?: string;
  /** `scan --all`: every source Runtime actually found, not a named one. */
  readonly all?: boolean;
}

export function planAddRoot(
  root: string,
  source: string | undefined,
  recursive: boolean,
): Plan {
  return {
    action: "add scan root",
    target: root,
    detail: `source=${source ?? "auto"} recursive=${recursive}`,
  };
}

export function planRemoveRoot(root: string): Plan {
  return { action: "remove scan root", target: root };
}

export function planScan(root: string | undefined, full: boolean): Plan {
  return {
    action: "scan",
    target: root ?? "all registered roots",
    detail: `full=${full}`,
  };
}

export function planScanSource(sourceId: string, full: boolean): Plan {
  return {
    action: "scan",
    target: `source ${sourceId}`,
    detail: `full=${full}`,
    sourceId,
  };
}

export function planScanAll(full: boolean): Plan {
  return {
    action: "scan",
    target: "all detected sources",
    detail: `full=${full}`,
    all: true,
  };
}

export function planBackup(target: string): Plan {
  return { action: "create backup", target, verb: "create" };
}

export function planRestore(source: string): Plan {
  return { action: "restore backup", target: source, verb: "restore" };
}

export interface ProductCommand {
  readonly plan: Plan;
  readonly confirmed: boolean;
  /** For destructive verbs with a named artifact, the exact value repeated by
   * the user. `backup restore` requires this to match `--source`. */
  readonly confirmPath?: string;
}

/** Parse a mutating product command.
 *
 * Unknown flags are rejected rather than ignored: a typo'd `--yes` that is
 * silently dropped would turn a real apply into a plan, and the user would
 * believe the change had been made.
 */
export function parseProductCommand(
  name: string,
  args: readonly string[],
): ProductCommand | null {
  let root: string | undefined;
  let source: string | undefined;
  let target: string | undefined;
  let recursive = true;
  let full = false;
  let all = false;
  let confirmed = false;
  let confirmPath: string | undefined;
  const seen = new Set<string>();
  const seenValues = new Set<string>();

  const flags = new Set([
    "--root",
    "--source",
    "--target",
    "--confirm",
    "--no-recursive",
    "--full",
    "--all",
    "--yes",
    "--json",
  ]);

  // The first token is the verb for commands that have one (`path add`,
  // `backup create`). It used to fall into the flag loop and be rejected as an
  // unknown flag, which made every one of these commands permanently
  // unparseable — `path add --root X` printed "参数无效" even though `help`
  // documented it. The tests missed this because they asserted
  // `parsed?.plan!` and null satisfies `?.` + `!`.
  const verb = args[0];
  const start = verb !== undefined && !verb.startsWith("--") ? 1 : 0;

  for (let index = start; index < args.length; index += 1) {
    const arg = args[index];
    if (!flags.has(arg)) return null;
    // A repeated flag is a malformed argv, not an idempotent spelling. The
    // other command parsers already apply this rule to --json as well.
    if (seen.has(arg)) return null;
    seen.add(arg);
    if (arg === "--yes") {
      confirmed = true;
      continue;
    }
    if (arg === "--confirm") {
      const value = args[index + 1];
      if (value === undefined || value.startsWith("--")) return null;
      if (confirmPath !== undefined) return null;
      confirmPath = value;
      index += 1;
      continue;
    }
    if (arg === "--json") continue;
    if (arg === "--no-recursive") {
      recursive = false;
      continue;
    }
    if (arg === "--full") {
      full = true;
      continue;
    }
    if (arg === "--all") {
      all = true;
      continue;
    }
    const value = args[index + 1];
    if (value === undefined || value.startsWith("--")) return null;
    if (seenValues.has(arg)) return null;
    seenValues.add(arg);
    index += 1;
    if (arg === "--root") root = value;
    else if (arg === "--source") source = value;
    else target = value;
  }

  if (name === "path") {
    if (confirmPath !== undefined) return null;
    if (target !== undefined) return null;
    if (root === undefined) return null;
    if (source === undefined && args.includes("--source")) return null;
    if (seen.has("--full") || seen.has("--all")) return null;
    if (args[0] !== "add" && args[0] !== "remove") return null;
    if (
      args[0] === "remove" &&
      (seen.has("--source") || seen.has("--no-recursive"))
    ) {
      return null;
    }
    return {
      plan:
        args[0] === "add"
          ? planAddRoot(root, source, recursive)
          : planRemoveRoot(root),
      confirmed,
    };
  }
  if (name === "scan") {
    if (confirmPath !== undefined) return null;
    if (target !== undefined) return null;
    if (seen.has("--no-recursive")) return null;
    if (args[0] !== undefined && !args[0].startsWith("--")) return null;
    // `--all` means "every source Runtime actually found", so naming one too
    // would make the command mean two different things.
    if (all && source !== undefined) return null;
    if (all) return { plan: planScanAll(full), confirmed };
    // A source is the thing the Runtime scan endpoint addresses. A bare root is
    // still accepted for planning (it means "all registered roots") but cannot
    // be applied, because there is no endpoint that scans a raw path — the CLI
    // must not pretend it can.
    if (source === undefined) return { plan: planScan(root, full), confirmed };
    return { plan: planScanSource(source, full), confirmed };
  }
  if (name === "backup") {
    const verb = args[0];
    if (verb !== "create" && verb !== "restore") return null;
    if (seen.has("--full") || seen.has("--all") || seen.has("--no-recursive")) {
      return null;
    }
    if (verb === "restore") {
      if (source === undefined) return null;
      if (target !== undefined) return null;
      return {
        plan: planRestore(source),
        confirmed,
        ...(confirmPath === undefined ? {} : { confirmPath }),
      };
    }
    if (confirmPath !== undefined) return null;
    if (source !== undefined) return null;
    if (target === undefined) return null;
    return {
      plan: planBackup(target),
      confirmed,
      ...(confirmPath === undefined ? {} : { confirmPath }),
    };
  }
  return null;
}

/** Whether `--confirm <path>` names the same restore source.
 *
 * The comparison is lexical on purpose: the CLI must not resolve symlinks or
 * touch the filesystem before Core has validated the managed source. A
 * different string is a different confirmation, so a typo or a path escape
 * can never be applied as a side effect of `--yes`.
 */
export function restoreConfirmationMatches(
  source: string,
  confirmPath: string | undefined,
): boolean {
  return confirmPath === source;
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

/** Parse SDK-native `write submit|status`. */
export function parseWriteCommand(
  args: readonly string[],
): WriteCommand | null {
  if (args.filter((item) => item === "--json").length > 1) return null;
  const meaningful = args.filter((item) => item !== "--json");
  if (meaningful[0] === "status") {
    const status = parseTaskCommand(meaningful.slice(1));
    return status === null ? null : { kind: "status", id: status.id };
  }
  const input = meaningful[0] === "submit" ? meaningful.slice(1) : meaningful;
  let session: string | undefined;
  let source = "memhub-cli";
  let messagesFile: string | undefined;
  let requestId: string | undefined;
  let memoryAt: string | undefined;
  let priority: "live" | "normal" | undefined;

  for (let index = 0; index < input.length; index += 1) {
    const arg = input[index];
    if (arg === "--live") {
      priority = "live";
      continue;
    }
    const value = input[index + 1];
    if (value === undefined || value.startsWith("--")) return null;
    index += 1;
    if (arg === "--session") session = value;
    else if (arg === "--source") source = value;
    else if (arg === "--messages-file") messagesFile = value;
    else if (arg === "--request-id") requestId = value;
    else if (arg === "--memory-at") memoryAt = value;
    else return null;
  }

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
  return priority === undefined ? command : { ...command, priority };
}

export interface TaskCommand {
  readonly id: string;
}

export function parseTaskCommand(args: readonly string[]): TaskCommand | null {
  if (args.filter((item) => item === "--json").length > 1) return null;
  let id: string | undefined;
  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    if (arg === "--json") continue;
    if (arg !== "--id") return null;
    const value = args[index + 1];
    if (value === undefined || value.startsWith("--")) return null;
    index += 1;
    id = value;
  }
  return id === undefined ? null : { id };
}

/** The human form. Kept separate from the JSON form on purpose. */
export function describe(plan: Plan): string {
  const line = `${plan.action}: ${plan.target}`;
  return plan.detail ? `${line} (${plan.detail})` : line;
}

/** The machine form: one document, nothing else on stdout. */
export function asPlanDocument(plan: Plan): {
  schema_version: 1;
  plan: Plan;
  applied: boolean;
} {
  return { schema_version: 1, plan, applied: false };
}
