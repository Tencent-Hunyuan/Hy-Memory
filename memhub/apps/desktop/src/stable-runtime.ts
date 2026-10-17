import {
  chmod,
  lstat,
  mkdir,
  readFile,
  rename,
  rm,
  writeFile,
} from "node:fs/promises";
import path from "node:path";
import {
  installCliLink,
  isRecoverableInstallConflict,
  type InstallQuarantineReceipt,
  planCliLink,
  quarantineSelectedRuntime,
} from "@one-memory/runtime-control";

const CLI_PATH_BEGIN = "# >>> memhub PATH >>>";
const CLI_PATH_END = "# <<< memhub PATH <<<";

export function isInstalledApplicationsApp(
  executablePath: string,
  platform: NodeJS.Platform = process.platform,
): boolean {
  if (platform !== "darwin") return true;
  const resolved = path.resolve(executablePath);
  return /^\/Applications\/[^/]+\.app\/Contents\/MacOS\/[^/]+$/.test(resolved);
}

export async function installBundledCliIfNeeded(options: {
  home: string;
  installRoot: string;
  platform?: NodeJS.Platform;
}): Promise<void> {
  const platform = options.platform ?? process.platform;
  if (platform === "win32") return;
  const plan = await planCliLink({
    home: options.home,
    installRoot: options.installRoot,
    platform,
    includeCompatibilityAlias: false,
  });
  await installCliLink(plan);
  await ensureCliPath(options.home, plan.bin_directory, platform);
}

async function ensureCliPath(
  home: string,
  binDirectory: string,
  platform: NodeJS.Platform,
): Promise<void> {
  const profile = path.join(
    home,
    platform === "darwin" ? ".zprofile" : ".profile",
  );
  const existingInfo = await lstat(profile).catch(() => null);
  if (
    existingInfo?.isSymbolicLink() ||
    (existingInfo && !existingInfo.isFile())
  ) {
    throw new Error("shell_profile_unmanaged");
  }
  const existing = existingInfo ? await readFile(profile, "utf8") : "";
  if (
    existing.includes(CLI_PATH_BEGIN) ||
    existing.split("\n").some((line) => line.includes(binDirectory))
  ) {
    return;
  }
  await mkdir(home, { recursive: true, mode: 0o700 });
  const separator = existing === "" || existing.endsWith("\n") ? "" : "\n";
  const block = `${CLI_PATH_BEGIN}\nexport PATH="$HOME/.local/bin:$PATH"\n${CLI_PATH_END}\n`;
  const temporary = `${profile}.memhub-${process.pid}`;
  try {
    await writeFile(temporary, `${existing}${separator}${block}`, {
      encoding: "utf8",
      mode: 0o600,
    });
    await chmod(temporary, existingInfo ? existingInfo.mode & 0o777 : 0o600);
    await rename(temporary, profile);
  } finally {
    await rm(temporary, { force: true });
  }
}

/** Install the bundle into the stable directory before rewriting host configs.
 *
 * Repair points every already-installed host at `current`. Doing that first
 * leaves those hosts calling a program that is not on disk.
 */
export async function ensureStableRuntimeThenRepair(options: {
  packaged: boolean;
  preview: boolean;
  install: () => Promise<unknown>;
  repair: () => Promise<unknown>;
}): Promise<void> {
  if (!options.packaged) return;
  if (!options.preview) await options.install();
  await options.repair();
}

export function startStableRuntimeRepair(options: {
  packaged: boolean;
  preview: boolean;
  install: () => Promise<unknown>;
  repair: () => Promise<unknown>;
  onStage?: (
    stage: "install" | "repair",
    outcome: "started" | "succeeded",
  ) => void | Promise<void>;
  onError?: (error: unknown) => void | Promise<void>;
}): Promise<void> {
  return (async () => {
    try {
      if (!options.packaged) return;
      if (!options.preview) {
        await options.onStage?.("install", "started");
        await options.install();
        await options.onStage?.("install", "succeeded");
      }
      await options.onStage?.("repair", "started");
      await options.repair();
      await options.onStage?.("repair", "succeeded");
    } catch (error) {
      await options.onError?.(error);
    }
  })();
}

const SAFE_STABLE_RUNTIME_ERROR_CODES = new Set([
  "installed_version_conflict",
  "distribution_downgrade_refused",
  "install_current_unmanaged",
]);

export function stableRuntimeErrorCode(error: unknown): string {
  if (
    error instanceof Error &&
    SAFE_STABLE_RUNTIME_ERROR_CODES.has(error.message)
  ) {
    return error.message;
  }
  return "desktop_start_failed";
}

/**
 * `installUserService` reports a durable `launchctl disable` override with this
 * exact code. It is a state to surface, not a failure to undo: the installer
 * never re-enables a service the user or the system disabled, and the macOS
 * platform checks assert that refusal
 * (`install_refuses_to_re_enable_a_disabled_service`).
 *
 * The override outlives a re-install, so treating it as fatal leaves an
 * affected machine with an app that exits on every launch. Desktop degrades
 * instead, owning the Runtime as a child process, and reports the durable
 * state rather than clearing it.
 *
 * Only this exact code degrades. Every other install failure keeps failing
 * closed, so a real misconfiguration still surfaces instead of hiding behind a
 * child process that may not serve hosts either.
 */
export function isDisabledServiceInstallError(error: unknown): boolean {
  return (
    error instanceof Error &&
    error.message === "runtime_service_disabled_by_system"
  );
}

export async function installServiceWithDisabledFallback(options: {
  install(): Promise<void>;
  restoreOwner(): Promise<void>;
  reportDisabled(): Promise<void>;
  startTransient(): Promise<void>;
}): Promise<"managed" | "transient"> {
  try {
    await options.install();
    return "managed";
  } catch (error) {
    await options.restoreOwner();
    if (!isDisabledServiceInstallError(error)) throw error;
    await options.reportDisabled();
    await options.startTransient();
    return "transient";
  }
}

/** Install once, and on a runtime conflict move the selected tree aside and
 * install again.
 *
 * The ordinary upgrade paths already succeed without this: the bundled runtime
 * takes over from a same-build tree whose bytes differ. What is left is the
 * residue of an interrupted or hand-edited install, where the selected tree
 * cannot be reused and nothing else will fix it either. Quarantining costs
 * nothing a reinstall cannot rebuild, and the memory data lives elsewhere, so
 * it is safe to do without asking. A second failure is still reported: two
 * attempts are a repair, an endless loop would be a cover-up.
 */
export async function withInstallConflictRecovery<T>(options: {
  install: () => Promise<T>;
  installRoot: string;
  onRecovered?: (receipt: InstallQuarantineReceipt) => void | Promise<void>;
}): Promise<T> {
  try {
    return await options.install();
  } catch (error) {
    if (!isRecoverableInstallConflict(error)) throw error;
    const receipt = await quarantineSelectedRuntime({
      installRoot: options.installRoot,
    });
    await options.onRecovered?.(receipt);
    return await options.install();
  }
}

/** What the user is told, instead of an internal error code.
 *
 * The codes are contractual and stay in the logs; this is the layer that turns
 * them into something a person can act on. Every string says what happens to
 * their memories, because that is the first thing anyone asks. */
export const STABLE_RUNTIME_USER_MESSAGES: Record<string, string> = {
  installed_version_conflict:
    "运行环境与这个安装包不一致，已尝试自动修复但没有成功。请重新启动 MemHub 再试一次；如果仍然打不开，重新安装即可，你的记忆数据不会受到影响。",
  distribution_downgrade_refused:
    "已安装的运行环境比这个安装包更新，已保留较新的版本，无需处理。",
  install_current_unmanaged:
    "运行环境的安装目录被改动过，无法安全接管。请重新安装 MemHub，你的记忆数据不会受到影响。",
  desktop_start_failed:
    "MemHub 启动失败。请重新启动；如果仍然打不开，请重新安装，你的记忆数据不会受到影响。",
};

export function stableRuntimeUserMessage(error: unknown): string {
  const code = stableRuntimeErrorCode(error);
  return (
    STABLE_RUNTIME_USER_MESSAGES[code] ??
    STABLE_RUNTIME_USER_MESSAGES.desktop_start_failed ??
    ""
  );
}
