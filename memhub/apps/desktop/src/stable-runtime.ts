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

const DISABLED_SERVICE_INSTALL_ERROR = "runtime_service_disabled_by_system";

/** Whether `installUserService` refused because the service is disabled. */
export function isDisabledServiceInstallError(error: unknown): boolean {
  return (
    error instanceof Error && error.message === DISABLED_SERVICE_INSTALL_ERROR
  );
}
