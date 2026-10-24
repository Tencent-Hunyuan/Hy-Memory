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
import { installCliLink, planCliLink } from "@one-memory/runtime-control";

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
  // Migration runs before the app window exists, so these are the only names
  // a first launch can be given. They stay codes here and become sentences in
  // STABLE_RUNTIME_USER_MESSAGES: a person reading the dialog never sees them.
  "sdk_migration_preflight_failed",
  "sdk_migration_disk_space_insufficient",
  "sdk_migration_cutover_failed",
  "sdk_migration_bundle_copy_failed",
  "sdk_migration_response_invalid",
  "sdk_migration_status_unavailable",
  "sdk_migration_incomplete",
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

/** What the user is told, instead of an internal error code.
 *
 * The codes are contractual and stay in the logs; this is the layer that turns
 * them into something a person can act on. Every string says what happens to
 * their memories, because that is the first thing anyone asks. */
export const STABLE_RUNTIME_USER_MESSAGES: Record<string, string> = {
  installed_version_conflict:
    "运行环境与这个安装包不一致，无法安全接管。请安装更新版本的 MemHub；你的记忆数据不会受到影响。",
  distribution_downgrade_refused:
    "已安装的运行环境比这个安装包更新，无法用旧安装包启动。请安装最新版本的 MemHub；你的记忆数据不会受到影响。",
  install_current_unmanaged:
    "运行环境的安装目录被改动过，无法安全接管。请重新安装 MemHub，你的记忆数据不会受到影响。",
  sdk_migration_disk_space_insufficient:
    "磁盘空间不足，升级记忆数据格式时中断了。请至少腾出与记忆数据同等大小的可用空间（可在「关于本机 → 储存空间」查看），然后重新启动 MemHub；升级会自动继续，原有记忆数据不会丢失。",
  sdk_migration_bundle_copy_failed:
    "升级前备份没有写完，通常是磁盘空间不足。请清理磁盘空间后重新启动；原有记忆数据不会丢失。",
  sdk_migration_cutover_failed:
    "切换数据源时失败，原有记忆数据仍然完整可用。请重新启动重试；如果仍然失败，请把这个提示反馈给我们。",
  sdk_migration_preflight_failed:
    "升级前的检查没有通过，升级已停止。请重新启动重试；如果仍然失败，请把这个提示反馈给我们，原有记忆数据不会丢失。",
  sdk_migration_status_unavailable:
    "读不到升级状态，无法判断是否需要升级。请重新启动 MemHub；如果仍然失败，请重新安装，你的记忆数据不会受到影响。",
  sdk_migration_response_invalid:
    "升级过程返回了无法识别的内容，已停止。原有记忆数据不会丢失；请重新启动重试，如果仍然失败，请把这个提示反馈给我们。",
  sdk_migration_incomplete:
    "记忆数据格式升级没有完成，MemHub 暂时无法打开。请重新启动重试；如果仍然失败，请把这个提示反馈给我们，原有记忆数据不会丢失。",
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

/** What the startup dialog body says.
 *
 * A code we wrote copy for becomes a sentence. Every other code is shown as it
 * is: this dialog is the last thing a person sees before the app closes, and an
 * unexpected failure is more useful as the code support can look up than as a
 * reassuring sentence that erases which failure actually fired. */
export function stableRuntimeFailureDetail(error: unknown): string {
  if (!(error instanceof Error)) {
    return STABLE_RUNTIME_USER_MESSAGES.desktop_start_failed ?? "";
  }
  return STABLE_RUNTIME_USER_MESSAGES[error.message] ?? error.message;
}
