import os from "node:os";
import path from "node:path";

import {
  applyRecallInstall,
  claudeCodeLayout,
  codexLayout,
  cursorLayout,
  deepseekHarnessLayout,
  hermesLayout,
  openclawLayout,
  opencodeLayout,
  piLayout,
  planRecallInstall,
  previewRecallInstall,
  qwenCodeLayout,
  type RecallHostLayout,
  refuseDisabledHostOperation,
  UnsafeManagedPathError,
  verifyRecallInstall,
  workbuddyLayout,
  workbuddyScatteredLayout,
  zcodeLayout,
} from "./recall-installer.js";
import { probeRecallInstall } from "./recall-probe.js";
import { planRecallRepoint } from "./recall-repoint.js";

const HOSTS = [
  "cursor",
  "claude-code",
  "codex",
  "workbuddy",
  "workbuddy-scattered",
  "opencode",
  "qwen-code",
  "openclaw",
  "hermes",
  "deepseek-harness",
  "pi",
  "zcode",
] as const;
type HostId = (typeof HOSTS)[number];
/** Arguments that stand alone rather than taking a value. */
const FLAGS = new Set(["takeover", "allow-default-config"]);

async function main(): Promise<void> {
  const [command, ...args] = process.argv.slice(2);
  const values = parseArgs(args);
  const layout = resolveLayout(values);

  if (command === "plan" || command === "apply" || command === "preview") {
    const plan = await planRecallInstall(
      layout,
      {
        runtimeCommand: required(values, "runtime-command"),
        mcpEntryPath: required(values, "mcp-entry"),
        discoveryPath: required(values, "discovery"),
        // Only CLI-channel hosts need it, and the installer refuses those
        // without it, so it is optional here rather than a fourth required
        // flag every other host would have to pass for nothing.
        ...(values.has("recall-cli")
          ? { recallCliPath: values.get("recall-cli") }
          : {}),
        ...(values.has("credential-file")
          ? { credentialFile: values.get("credential-file") }
          : {}),
        ...(values.has("capture-command")
          ? { captureCommand: values.get("capture-command") }
          : {}),
        ...(values.has("data-dir") ? { dataDir: values.get("data-dir") } : {}),
      },
      { takeover: values.has("takeover") },
    );
    if (command === "preview") {
      // Same call the desktop makes before it asks for approval, so the diff
      // can be reviewed without a window — and checked against what `apply`
      // then writes.
      print({ ok: true, command, ...(await previewRecallInstall(plan)) });
      return;
    }
    if (command === "apply") await applyRecallInstall(plan);
    print({
      ok: true,
      command,
      host: layout.hostId,
      actions: plan.actions,
      targets: plan.paths,
    });
    return;
  }
  if (command === "repoint") {
    // The repair R-24 asks for, offered only when the host really points
    // somewhere else. Goes through the same plan / preview / apply an install
    // does, so the SHA-256 of what lands can be checked against the preview.
    const repoint = await planRecallRepoint(layout, {
      runtimeCommand: required(values, "runtime-command"),
      mcpEntryPath: required(values, "mcp-entry"),
      discoveryPath: required(values, "discovery"),
      ...(values.has("recall-cli")
        ? { recallCliPath: values.get("recall-cli") }
        : {}),
      ...(values.has("credential-file")
        ? { credentialFile: values.get("credential-file") }
        : {}),
      ...(values.has("capture-command")
        ? { captureCommand: values.get("capture-command") }
        : {}),
      ...(values.has("data-dir") ? { dataDir: values.get("data-dir") } : {}),
    });
    if (repoint === null) {
      print({ ok: true, command, host: layout.hostId, repoint: null });
      return;
    }
    const preview = await previewRecallInstall(repoint.plan);
    await applyRecallInstall(repoint.plan, preview.fingerprint);
    print({
      ok: true,
      command,
      host: layout.hostId,
      from: repoint.from,
      to: repoint.to,
      fingerprint: preview.fingerprint,
    });
    return;
  }

  if (command === "probe") {
    // The batch self-check R-24 asks for. It has to work with the host shut,
    // which it does because the probe starts the host's own server itself,
    // so a release can be checked across every host without opening eleven
    // applications.
    const probe = await probeRecallInstall(layout);
    print({ ok: true, command, ...probe });
    return;
  }

  if (command === "verify") {
    const verification = await verifyRecallInstall(layout);
    print({
      ok: true,
      command,
      host: layout.hostId,
      // Surfaced because a wrong path here passes every other check and then
      // returns nothing when the host actually calls recall.
      discovery: verification.discoveryPath,
      discovery_present: verification.discoveryPresent,
    });
    return;
  }
  // The same review the desktop app offers before disconnecting, for callers
  // that have no window: `preview-rollback` reports the restore without doing
  // it, and `rollback` carries it out.
  if (command === "preview-rollback") {
    // Disabled in the local package (product scope): refuse before touching
    // any filesystem state.
    refuseDisabledHostOperation("preview-rollback");
  }
  if (command === "rollback") {
    refuseDisabledHostOperation("rollback");
  }
  throw new Error(
    "Usage: installer-cli <plan|preview|apply|verify|preview-rollback|rollback> " +
      "[--host cursor|claude-code|codex] --host-dir <dir> [--config <path>] " +
      "[--allow-default-config] " +
      "[--runtime-command <path> --mcp-entry <path> --discovery <path>] " +
      "[--recall-cli <path>] [--credential-file <path>] " +
      "[--capture-command <path>] [--data-dir <path>] " +
      "[--profile <name>] [--takeover]",
  );
}

/** Pick the host layout, keeping `--cursor-dir` working for existing callers. */
function resolveLayout(values: Map<string, string>): RecallHostLayout {
  const host = (values.get("host") ?? "cursor") as HostId;
  if (!HOSTS.includes(host)) {
    throw new Error(`Unsupported host: ${host}`);
  }
  const hostDir = values.get("host-dir") ?? values.get("cursor-dir");
  if (!hostDir) {
    throw new Error("Missing --host-dir");
  }
  if (host === "cursor") {
    return cursorLayout(hostDir);
  }
  if (host === "codex") {
    return codexLayout(hostDir);
  }
  if (host === "workbuddy") {
    return workbuddyLayout(hostDir);
  }
  // Only reachable for uninstall: nothing installs the scattered shape any
  // more, but a machine that still has it needs a way to name it.
  if (host === "workbuddy-scattered") {
    return workbuddyScatteredLayout(hostDir);
  }
  if (host === "opencode") {
    return opencodeLayout(hostDir);
  }
  if (host === "qwen-code") {
    return qwenCodeLayout(hostDir);
  }
  if (host === "zcode") {
    return zcodeLayout(hostDir);
  }
  if (host === "openclaw") {
    return openclawLayout(hostDir);
  }
  if (host === "hermes") {
    return hermesLayout(hostDir);
  }
  if (host === "pi") {
    return piLayout(hostDir);
  }
  if (host === "deepseek-harness") {
    // Per profile, not per host: `--profile` names which one.
    return deepseekHarnessLayout({
      dshHome: hostDir,
      profile: values.get("profile") ?? "web",
    });
  }
  if (host === "claude-code") {
    const explicitConfig = values.get("config");
    const allowDefaultConfig = values.has("allow-default-config");
    const defaultHostDir = path.join(os.homedir(), ".claude");
    const usesDefaultHostDir =
      path.resolve(hostDir) === path.resolve(defaultHostDir);
    if (
      explicitConfig === undefined &&
      !(allowDefaultConfig && usesDefaultHostDir)
    ) {
      // Claude Code stores mcpServers beside the host directory, not inside
      // it. An isolated run must pass --config; a default-path run must
      // explicitly opt in with --allow-default-config. Either way the CLI
      // never silently writes the real ~/.claude.json.
      throw new Error("isolated_claude_config_required");
    }
    return claudeCodeLayout({
      claudeDir: hostDir,
      // Claude Code keeps mcpServers beside the host directory, not inside it.
      configPath: explicitConfig ?? path.join(os.homedir(), ".claude.json"),
    });
  }
  return claudeCodeLayout({
    claudeDir: hostDir,
    // Claude Code keeps mcpServers beside the host directory, not inside it.
    configPath: values.get("config") ?? path.join(os.homedir(), ".claude.json"),
  });
}

function parseArgs(args: string[]): Map<string, string> {
  const values = new Map<string, string>();
  let index = 0;
  while (index < args.length) {
    const key = args[index];
    if (!key?.startsWith("--")) {
      throw new Error(`Invalid installer argument: ${key ?? ""}`);
    }
    const name = key.slice(2);
    if (FLAGS.has(name)) {
      values.set(name, "true");
      index += 1;
      continue;
    }
    const value = args[index + 1];
    if (!value || value.startsWith("--")) {
      throw new Error(`Missing value for ${key}`);
    }
    values.set(name, value);
    index += 2;
  }
  return values;
}

function required(values: Map<string, string>, key: string): string {
  const value = values.get(key);
  if (!value) throw new Error(`Missing --${key}`);
  return value;
}

function print(value: unknown): void {
  process.stdout.write(`${JSON.stringify(value, null, 2)}\n`);
}

main().catch((error) => {
  // Path-safety refusals get a stable machine-readable code so callers do not
  // have to match on a message substring.
  if (error instanceof UnsafeManagedPathError) {
    process.stderr.write(
      `${JSON.stringify({
        ok: false,
        code: error.code,
        operation: error.operation,
        target: error.lexicalTarget,
        ancestor: error.canonicalAncestor,
        reason: error.reason,
      })}\n`,
    );
  } else {
    process.stderr.write(
      `${error instanceof Error ? error.message : "Recall installer failed"}\n`,
    );
  }
  process.exitCode = 1;
});
