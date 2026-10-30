#!/usr/bin/env node

import os from "node:os";
import path from "node:path";

import {
  DiagnosticLogger,
  diagnosticError,
  resolveRuntimeCliConfig,
} from "@one-memory/runtime-control";
import { run } from "./cli.js";
import { resolveEntryDiagnostics } from "./entry-diagnostics.js";
import { resolveDiagnosticsDataDir } from "./install-receipt.js";

function accountHomeDirectory(): string | undefined {
  try {
    return os.userInfo().homedir;
  } catch {
    return os.homedir();
  }
}

async function main(): Promise<number> {
  const arguments_ = process.argv.slice(2);
  const command = arguments_[0];
  // A preview (`--yes` absent) must not create or write any diagnostic file:
  // the contract is that it only emits a plan. Diagnostics for these commands
  // are initialized only once the caller has confirmed the mutation.
  const confirmed = arguments_.includes("--yes");
  const installLike = [
    "install",
    "upgrade",
    "service-install",
    "service-uninstall",
  ].includes(command ?? "");
  const isPreview = installLike && !confirmed;
  const mutating = [
    "start",
    "stop",
    "restart",
    "install",
    "upgrade",
    "service-install",
    "service-uninstall",
  ].includes(command ?? "");
  let diagnostics: DiagnosticLogger | null = null;
  if (mutating && !isPreview) {
    try {
      // Apply the managed install receipt before resolving the config, so
      // `memhub start/stop/...` on a custom data dir logs to the same place the
      // command itself will act on instead of the default ~/.one-memory.
      const diagnosticsDataDir = await resolveDiagnosticsDataDir(
        arguments_,
        process.env,
      );
      // An explicit but unusable --data-dir must not silently log elsewhere;
      // skip diagnostics and let `run()` report the usage error.
      if (arguments_.includes("--data-dir") && diagnosticsDataDir === null) {
        throw new Error("cli_diagnostics_data_dir_invalid");
      }
      // An isolated HOME with no explicit data dir resolves to
      // `<HOME>/.one-memory` only because that is the account-home default.
      // Writing CLI diagnostics there would add `.one-memory/logs/...` to a
      // directory the caller meant as an isolation root, not a user home
      // (C8 P1 isolated-cli-session-writes-home). Skip entry diagnostics in
      // that case; the command still runs and reports its own structured
      // output.
      const entryDecision = resolveEntryDiagnostics({
        arguments_,
        home: process.env.HOME,
        accountHome: accountHomeDirectory(),
        environmentDataDir: process.env.ONE_MEMORY_DATA_DIR,
      });
      if (entryDecision.kind === "skip") {
        throw new Error(`cli_diagnostics_${entryDecision.reason}`);
      }
      const config = resolveRuntimeCliConfig(
        diagnosticsDataDir === null
          ? process.env
          : { ...process.env, ONE_MEMORY_DATA_DIR: diagnosticsDataDir },
        process.argv[1],
      );
      diagnostics = new DiagnosticLogger({
        dataDir: config.dataDir,
        service: "cli",
      });
      await diagnostics.log({
        level: "info",
        component: "cli.command",
        event: "command.started",
        outcome: "started",
        details: { action: command ?? "unknown" },
      });
    } catch {
      diagnostics = null;
    }
  }
  try {
    const code = await run(arguments_);
    await diagnostics?.log({
      level: code === 0 ? "info" : "warn",
      component: "cli.command",
      event: "command.completed",
      outcome: code === 0 ? "succeeded" : "failed",
      details: { action: command ?? "unknown", status: String(code) },
    });
    return code;
  } catch (error) {
    await diagnostics?.log({
      level: "error",
      component: "cli.command",
      event: "command.failed",
      outcome: "failed",
      error_code: "cli_command_failed",
      ...diagnosticError(error),
      details: { action: command ?? "unknown" },
    });
    process.stderr.write(
      `${error instanceof Error ? error.message : "MemHub CLI failed"}\n`,
    );
    return 1;
  }
}

main().then((code) => {
  process.exitCode = code;
});
