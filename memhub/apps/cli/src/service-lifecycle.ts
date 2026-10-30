import { spawn } from "node:child_process";
import { lstat, readFile, realpath } from "node:fs/promises";
import path from "node:path";

import {
  disableRuntimeService,
  enableRuntimeService,
  installUserService,
  planUserService,
  type RuntimeInstallLayout,
  type RuntimeServicePlan,
  restartUserService,
  runtimeServiceEnabled,
  type ServiceCommandRunner,
  uninstallUserService,
} from "@one-memory/runtime-control";

type ServiceOptions = {
  uid?: number;
  xdgConfigHome?: string;
  /** Injected OS-service command runner. `uninstallUserService` treats any
   * non-zero exit as `runtime_service_command_failed`; callers that already
   * booted the label out (e.g. `memhub stop`) pass a tolerant runner so the
   * plist still gets removed. */
  runCommand?: ServiceCommandRunner;
};

type ManagedSnapshot = {
  kind: "managed";
  plistPath: string;
  content: string;
};

type Registration =
  | { kind: "absent" }
  | ManagedSnapshot
  | { kind: "unmanaged"; plistPath: string | null };

type ServiceFile =
  | { kind: "absent" }
  | { kind: "managed"; content: string }
  | { kind: "unmanaged" };

export type CommandResult = {
  code: number;
  stdout: string;
  stderr: string;
};

export interface ServiceLifecycleDependencies {
  inspectRegistration: (
    plan: RuntimeServicePlan,
    options: ServiceOptions,
  ) => Promise<Registration>;
  inspectServiceFile: (plan: RuntimeServicePlan) => Promise<ServiceFile>;
  install: typeof installUserService;
  restart: typeof restartUserService;
  uninstall: typeof uninstallUserService;
  runCommand: ServiceCommandRunner;
  verifyRegistrationSnapshot: (snapshot: ManagedSnapshot) => Promise<void>;
  /** Whether two absolute paths denote the same file (post-realpath). */
  sameFile: (left: string, right: string) => Promise<boolean>;
  serviceEnabled: (dataDir: string) => Promise<boolean>;
  enableService: (dataDir: string) => Promise<void>;
  disableService: (dataDir: string) => Promise<void>;
}

export type PreparedServiceInstall = {
  layout: RuntimeInstallLayout;
  options: ServiceOptions;
  plan: RuntimeServicePlan;
  mode: "direct" | "bootstrap-existing" | "restart" | "replace";
  previous?: ManagedSnapshot;
  previouslyEnabled: boolean;
  /**
   * Re-bootstrap the previous (possibly older) LaunchAgent when the new one
   * fails to bootstrap. `install` turns this off once the home has been handed
   * to this version's SDK migration: an older Runtime has no migration gate and
   * would serve a home that is not on SDK authority.
   */
  restorePreviousOnFailure?: boolean;
};

const defaultDependencies: ServiceLifecycleDependencies = {
  inspectRegistration: inspectLaunchAgentRegistration,
  inspectServiceFile,
  install: installUserService,
  restart: restartUserService,
  uninstall: uninstallUserService,
  runCommand: runServiceCommand,
  verifyRegistrationSnapshot,
  sameFile: pathsIdentifySameFile,
  serviceEnabled: runtimeServiceEnabled,
  enableService: enableRuntimeService,
  disableService: disableRuntimeService,
};

export async function prepareServiceInstall(
  layout: RuntimeInstallLayout,
  options: ServiceOptions,
  dependencies: ServiceLifecycleDependencies = defaultDependencies,
): Promise<PreparedServiceInstall> {
  const plan = planUserService(layout, options);
  if (plan.status !== "ready" || plan.service_file === null) {
    throw new Error(plan.blocked_reason ?? "runtime_service_plan_blocked");
  }
  const previouslyEnabled = await dependencies.serviceEnabled(layout.dataDir);
  if (layout.platform !== "darwin") {
    return { layout, options, plan, mode: "direct", previouslyEnabled };
  }

  const [registration, target] = await Promise.all([
    dependencies.inspectRegistration(plan, options),
    dependencies.inspectServiceFile(plan),
  ]);
  if (registration.kind === "unmanaged") {
    throw new Error("runtime_service_conflict_unmanaged_label");
  }
  if (target.kind === "unmanaged") {
    throw new Error("runtime_service_conflict_unmanaged_file");
  }
  if (registration.kind === "absent") {
    return {
      layout,
      options,
      plan,
      mode: target.kind === "managed" ? "bootstrap-existing" : "direct",
      previouslyEnabled,
    };
  }
  // launchd reports the plist's realpath, so compare file identity rather than
  // the literal string: the same file can be reached via a different path.
  if (
    await dependencies.sameFile(registration.plistPath, plan.service_file.path)
  ) {
    if (target.kind !== "managed") {
      throw new Error("runtime_service_conflict_unmanaged_file");
    }
    return {
      layout,
      options,
      plan,
      mode: "restart",
      previous: registration,
      previouslyEnabled,
    };
  }
  if (target.kind !== "absent") {
    throw new Error("runtime_service_conflict_unmanaged_file");
  }
  return {
    layout,
    options,
    plan,
    mode: "replace",
    previous: registration,
    previouslyEnabled,
  };
}

export async function applyPreparedServiceInstall(
  prepared: PreparedServiceInstall,
  dependencies: ServiceLifecycleDependencies = defaultDependencies,
): Promise<RuntimeServicePlan> {
  const { layout, options, plan } = prepared;
  try {
    if (prepared.mode === "restart") {
      await dependencies.enableService(layout.dataDir);
      try {
        await verifyPreparedRegistration(prepared, dependencies);
        return await dependencies.restart(layout, options);
      } catch (error) {
        await restoreServiceEnableState(prepared, dependencies).catch(
          () => undefined,
        );
        throw error;
      }
    }
    if (prepared.mode === "bootstrap-existing") {
      await dependencies.enableService(layout.dataDir);
      try {
        for (const command of plan.commands.install) {
          await dependencies.runCommand(command);
        }
      } catch (error) {
        await restoreServiceEnableState(prepared, dependencies).catch(
          () => undefined,
        );
        throw error;
      }
      return plan;
    }
    if (prepared.mode === "direct") {
      return await dependencies.install(layout, options);
    }

    const previous = prepared.previous;
    if (!previous) throw new Error("runtime_service_upgrade_state_invalid");
    await verifyPreparedRegistration(prepared, dependencies);
    const domain = launchdDomain(options.uid);
    let replacementFailure: "upgrade" | "rollback" | "not-restored" | null =
      null;
    const replaceCommand: ServiceCommandRunner = async (command) => {
      if (command[0] !== "launchctl" || command[1] !== "bootstrap") {
        await dependencies.runCommand(command);
        return;
      }
      await dependencies.verifyRegistrationSnapshot(previous);
      await dependencies.runCommand([
        "launchctl",
        "bootout",
        domain,
        previous.plistPath,
      ]);
      try {
        await dependencies.runCommand(command);
      } catch {
        replacementFailure = "upgrade";
        if (prepared.restorePreviousOnFailure === false) {
          replacementFailure = "not-restored";
          throw new Error("runtime_service_replacement_failed");
        }
        try {
          await dependencies.verifyRegistrationSnapshot(previous);
          await dependencies.runCommand([
            "launchctl",
            "bootstrap",
            domain,
            previous.plistPath,
          ]);
        } catch {
          replacementFailure = "rollback";
        }
        throw new Error("runtime_service_managed_upgrade_failed");
      }
    };
    try {
      return await dependencies.install(layout, {
        ...options,
        runCommand: replaceCommand,
      });
    } catch (error) {
      if (replacementFailure === "rollback") {
        throw new Error("runtime_service_managed_upgrade_rollback_failed");
      }
      if (replacementFailure === "upgrade") {
        throw new Error("runtime_service_managed_upgrade_failed");
      }
      if (replacementFailure === "not-restored") {
        throw new Error("runtime_service_replacement_failed");
      }
      throw error;
    }
  } catch (error) {
    if (
      error instanceof Error &&
      error.message === "runtime_install_incomplete"
    ) {
      throw new Error(
        "runtime_install_incomplete: run memhub install --yes first",
      );
    }
    throw error;
  }
}

/** Restore the exact service-registration state observed before install.
 * The callback rolls back Runtime files and ownership while the failed service
 * is stopped; any process relaunched afterward sees the old Runtime and owner. */
export async function rollbackPreparedServiceInstall(
  prepared: PreparedServiceInstall,
  rollbackFilesAndOwner: () => Promise<void>,
  dependencies: ServiceLifecycleDependencies = defaultDependencies,
): Promise<void> {
  const { layout, options, plan } = prepared;
  if (prepared.mode === "direct") {
    await dependencies.uninstall(layout, options);
    await rollbackFilesAndOwner();
    await restoreServiceEnableState(prepared, dependencies);
    return;
  }

  if (prepared.mode === "bootstrap-existing") {
    for (const command of plan.commands.stop) {
      await dependencies.runCommand(command);
    }
    await rollbackFilesAndOwner();
    await restoreServiceEnableState(prepared, dependencies);
    return;
  }

  if (prepared.mode === "restart") {
    await dependencies.uninstall(layout, options);
    await rollbackFilesAndOwner();
    await restoreServiceEnableState(prepared, dependencies);
    await dependencies.install(layout, options);
    return;
  }

  const previous = prepared.previous;
  if (!previous) throw new Error("runtime_service_upgrade_state_invalid");
  await dependencies.verifyRegistrationSnapshot(previous);
  // `replace` published a new plist at this install's target. Remove that
  // declaration completely before restoring the old registration, otherwise
  // the stale target blocks the next install even though launchd is serving
  // the previous plist again.
  await dependencies.uninstall(layout, options);
  await rollbackFilesAndOwner();
  await restoreServiceEnableState(prepared, dependencies);
  await dependencies.runCommand([
    "launchctl",
    "bootstrap",
    launchdDomain(options.uid),
    previous.plistPath,
  ]);
}

async function restoreServiceEnableState(
  prepared: PreparedServiceInstall,
  dependencies: ServiceLifecycleDependencies,
): Promise<void> {
  if (prepared.previouslyEnabled) {
    await dependencies.enableService(prepared.layout.dataDir);
  } else {
    await dependencies.disableService(prepared.layout.dataDir);
  }
}

async function verifyPreparedRegistration(
  prepared: PreparedServiceInstall,
  dependencies: ServiceLifecycleDependencies,
): Promise<void> {
  const previous = prepared.previous;
  if (!previous) throw new Error("runtime_service_upgrade_state_invalid");
  const current = await dependencies.inspectRegistration(
    prepared.plan,
    prepared.options,
  );
  if (
    current.kind !== "managed" ||
    current.plistPath !== previous.plistPath ||
    current.content !== previous.content
  ) {
    throw new Error("runtime_service_registration_changed");
  }
}

export async function uninstallServicePreservingData(
  layout: RuntimeInstallLayout,
  options: ServiceOptions,
  dependencies: ServiceLifecycleDependencies = defaultDependencies,
): Promise<"removed" | "not-installed"> {
  return dependencies.uninstall(layout, options);
}

async function inspectServiceFile(
  plan: RuntimeServicePlan,
): Promise<ServiceFile> {
  if (plan.service_file === null) return { kind: "unmanaged" };
  try {
    const info = await lstat(plan.service_file.path);
    if (
      !info.isFile() ||
      info.isSymbolicLink() ||
      (info.mode & 0o7777) !== 0o600 ||
      (process.getuid && info.uid !== process.getuid())
    ) {
      return { kind: "unmanaged" };
    }
    const content = await readFile(plan.service_file.path, "utf8");
    return content === plan.service_file.content
      ? { kind: "managed", content }
      : { kind: "unmanaged" };
  } catch (error) {
    return (error as NodeJS.ErrnoException).code === "ENOENT"
      ? { kind: "absent" }
      : Promise.reject(error);
  }
}

export async function inspectLaunchAgentRegistration(
  plan: RuntimeServicePlan,
  options: ServiceOptions,
  runCaptured: (
    command: readonly string[],
  ) => Promise<CommandResult> = runServiceCommandCaptured,
): Promise<Registration> {
  if (plan.platform !== "darwin") return { kind: "absent" };
  const domain = launchdDomain(options.uid);
  const result = await runCaptured([
    "launchctl",
    "print",
    `${domain}/${plan.service_id}`,
  ]);
  if (result.code !== 0) {
    if (/could not find service|service not found/i.test(result.stderr)) {
      return { kind: "absent" };
    }
    throw new Error("runtime_service_inspection_failed");
  }
  const plistPath = parseLaunchdPath(result.stdout);
  if (plistPath === null) return { kind: "unmanaged", plistPath: null };
  const plist = await runCaptured([
    "/usr/bin/plutil",
    "-convert",
    "json",
    "-o",
    "-",
    plistPath,
  ]);
  if (plist.code !== 0) return { kind: "unmanaged", plistPath };
  try {
    const document = JSON.parse(plist.stdout) as Record<string, unknown>;
    const args = document.ProgramArguments;
    const environment = document.EnvironmentVariables;
    if (
      document.Label !== plan.service_id ||
      !Array.isArray(args) ||
      args.length !== 3 ||
      typeof args[0] !== "string" ||
      typeof args[1] !== "string" ||
      args[2] !== "serve" ||
      environment === null ||
      typeof environment !== "object" ||
      (environment as Record<string, unknown>).ONE_MEMORY_NODE_COMMAND !==
        args[0] ||
      (environment as Record<string, unknown>).ONE_MEMORY_RUNTIME_ENTRY !==
        args[1]
    ) {
      return { kind: "unmanaged", plistPath };
    }
    const content = await readManagedPlist(plistPath);
    return { kind: "managed", plistPath, content };
  } catch {
    return { kind: "unmanaged", plistPath };
  }
}

async function verifyRegistrationSnapshot(snapshot: ManagedSnapshot) {
  const content = await readManagedPlist(snapshot.plistPath);
  if (content !== snapshot.content) {
    throw new Error("runtime_service_registration_changed");
  }
}

/** Compare two absolute paths as files, resolving symlinks and macOS's
 * `/private` prefix. Falls back to a literal comparison only when neither path
 * can be resolved, so a genuinely different file is never called "same". */
async function pathsIdentifySameFile(
  left: string,
  right: string,
): Promise<boolean> {
  if (left === right) return true;
  const resolved = await Promise.all([
    realpath(left).catch(() => null),
    realpath(right).catch(() => null),
  ]);
  return resolved[0] !== null && resolved[0] === resolved[1];
}

async function readManagedPlist(plistPath: string): Promise<string> {
  if (!path.isAbsolute(plistPath)) {
    throw new Error("runtime_service_registration_unmanaged");
  }
  const info = await lstat(plistPath);
  if (
    !info.isFile() ||
    info.isSymbolicLink() ||
    (info.mode & 0o022) !== 0 ||
    (process.getuid && info.uid !== process.getuid())
  ) {
    throw new Error("runtime_service_registration_unmanaged");
  }
  return readFile(plistPath, "utf8");
}

function parseLaunchdPath(output: string): string | null {
  for (const line of output.split("\n")) {
    const match = /^\s*path\s*=\s*(.+?)\s*$/.exec(line);
    if (match?.[1] && path.isAbsolute(match[1])) return match[1];
  }
  return null;
}

function launchdDomain(uid: number | undefined): string {
  if (!Number.isSafeInteger(uid) || (uid ?? 0) < 1) {
    throw new Error("macos_user_id_required");
  }
  return `gui/${uid}`;
}

async function runServiceCommand(command: readonly string[]): Promise<void> {
  const result = await runServiceCommandCaptured(command);
  if (result.code !== 0) throw new Error("runtime_service_command_failed");
}

async function runServiceCommandCaptured(
  command: readonly string[],
): Promise<CommandResult> {
  const [program, ...args] = command;
  if (!program) throw new Error("runtime_service_command_invalid");
  return new Promise((resolve, reject) => {
    const child = spawn(program, args, {
      shell: false,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    const stdout: Buffer[] = [];
    const stderr: Buffer[] = [];
    child.stdout.on("data", (chunk: Buffer) => stdout.push(chunk));
    child.stderr.on("data", (chunk: Buffer) => stderr.push(chunk));
    child.once("error", () =>
      reject(new Error("runtime_service_command_failed")),
    );
    child.once("exit", (code) =>
      resolve({
        code: code ?? 1,
        stdout: Buffer.concat(stdout).toString("utf8"),
        stderr: Buffer.concat(stderr).toString("utf8"),
      }),
    );
  });
}
