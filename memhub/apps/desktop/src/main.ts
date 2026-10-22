import { createHash, randomUUID } from "node:crypto";
import { createReadStream, existsSync, mkdirSync } from "node:fs";
import path from "node:path";
import { RecallBroker } from "@one-memory/host-bridge";
import {
  checkSkillDrift,
  planRecallRepoint,
  readInstallSelfCheck,
  renderCustomHarnessSkill,
  verifyRecallInstall,
} from "@one-memory/host-bridge/installer";
import { type GuiLocale, translate } from "@one-memory/i18n";
import {
  claimRuntimeServiceForApp,
  claimRuntimeServiceForCli,
  createConnectionClient,
  createHostPermissionClient,
  DiagnosticLogger,
  defaultManagedInstallRoot,
  diagnosticError,
  disableRuntimeService,
  domainCodeOf,
  type HostPermissionClient,
  installPackagedRuntime,
  installUserService,
  RecallBrokerSupervisor,
  RuntimeSupervisor,
  readPublishedCorePort,
  readRuntimeServiceOwner,
  removeRuntimeServiceOwner,
  runtimeInstallLayout,
  runtimeServiceEnableMarkerPath,
  stopRuntimeProcess,
  waitForRuntimeProcess,
  writeRuntimeServiceOwner,
} from "@one-memory/runtime-control";
import type { IpcMainInvokeEvent } from "electron";
import {
  app,
  BrowserWindow,
  clipboard,
  dialog,
  ipcMain,
  Menu,
  nativeImage,
  nativeTheme,
  protocol,
  screen,
  session,
  shell,
  systemPreferences,
  Tray,
} from "electron";
import { APP_ORIGIN, installAppProtocol } from "./app-protocol";
import { installApplicationMenu } from "./application-menu";
import { installBackgroundHealthIpc } from "./background-health-ipc";
import { installBackgroundTray } from "./background-tray";
import { installConnectionSetupIpc } from "./connection-setup-ipc";
import {
  CoreSupervisor,
  ensureRuntimeProcess,
  findAvailablePort,
  resolveCoreDataDir,
  resolveCoreLaunch,
  resolveCoreUserDataDir,
  resolvePackagedRuntimeLaunch,
  resolveRunningPackagedHostPaths,
  resolveVerifiedHostRuntime,
  stableRecallPaths,
} from "./core-supervisor";
import { startDesktopLifecycle } from "./desktop-lifecycle";
import { desktopVersionOutput } from "./desktop-version";
import {
  developmentBrokerPort,
  developmentCorePort,
} from "./development-core-port";
import {
  captureDebugLogCursor,
  exportDesktopDiagnostics,
  installDiagnosticsIpc,
  resolveDiagnosticBuildIdentity,
  uiTraceDiagnosticInput,
} from "./diagnostics-ipc";
import { DisplayPreferences } from "./display-preferences";
import { installEarlyStartupErrorHandler } from "./early-startup-errors";
import { canHandOff, handoffUrl } from "./handoff";
import { HostPermissions } from "./host-permissions";
import {
  isCanonicalDailyDataDir,
  isIsolatedHourlyDesktop,
  resolveDesktopTestIsolation,
  resolvePackagedBuildNumber,
  resolvePackagedCommit,
} from "./isolated-runtime";
import {
  LanguagePreferences,
  preferredSystemLanguage,
} from "./language-preferences";
import { installLanguagePreferencesIpc } from "./language-preferences-ipc";
import {
  disableLegacyLoginItem,
  wasOpenedByLegacyLoginItem,
} from "./login-item";
import { readLoginPreference, setLoginPreference } from "./login-preference";
import { isAllowedNavigation } from "./navigation-policy";
import { installDenyAllPermissionPolicy } from "./permissions";
import { isHostPresent, recallHosts } from "./recall-hosts";
import { shutdownRuntime } from "./runtime-shutdown";
import { createSourceChangeHandler } from "./source-change-handler";
import { SourceDirectoryPicker } from "./source-directory-picker";
import {
  installBundledCliIfNeeded,
  installServiceWithDisabledFallback,
  isInstalledApplicationsApp,
  stableRuntimeErrorCode,
  startStableRuntimeRepair,
} from "./stable-runtime";
import { STARTUP_SCREEN_URL, startupScreenHtml } from "./startup-screen";
import {
  requireCompletedStartupSdkMigration,
  runStartupSdkMigration,
} from "./startup-sdk-migration";
import { resolveTrialProfile } from "./trial-profile";
import { createWindowOptions } from "./window-options";

const developmentUrl =
  process.env.ONE_MEMORY_WEB_URL ?? "http://127.0.0.1:5173";
const previewProductName = (name: string) =>
  name === "MemHub Preview" || name === "One Memory Preview";
let coreSupervisor: CoreSupervisor | null = null;
let runtimeSupervisor: RuntimeSupervisor | null = null;
// Where the Runtime keeps its receipt. Stopping it on a full quit needs this,
// and `stop` runs outside `initialize`, where the directory is chosen.
let runtimeDataDir: string | null = null;
let preserveManagedRuntimeOnQuit = false;
let disposeTray: (() => void) | null = null;
let disposeApplicationMenu: (() => void) | null = null;
let languagePreferences: LanguagePreferences | null = null;
let disposeLanguagePreferences: (() => void) | null = null;
const displayWindows = new Set<BrowserWindow>();
let disposeDisplayPreferences: (() => void) | null = null;
let disposeSourceDirectoryPicker: (() => void) | null = null;
let connectionSetup: ReturnType<typeof installConnectionSetupIpc> | null = null;
let disposeBackgroundHealth: (() => void) | null = null;
let stableRuntimeRepair: Promise<void> | null = null;
let disposeDiagnostics: (() => void) | null = null;
let desktopDiagnostics: DiagnosticLogger | null = null;
let exportDiagnosticsFromMenu: (() => Promise<void>) | null = null;
const desktopStartupStartedAt = Date.now();

declare const __ONE_MEMORY_TRIAL_PROFILE__: string | null;
declare const __ONE_MEMORY_VERSION__: string;
declare const __ONE_MEMORY_BUILD_NUMBER__: string;
declare const __ONE_MEMORY_COMMIT__: string;
installEarlyStartupErrorHandler({
  processEvents: process,
  dataDir:
    process.env.ONE_MEMORY_DATA_DIR?.trim() ||
    path.join(app.getPath("home"), ".one-memory"),
  stderr: process.stderr,
  readLocale: () => languagePreferences?.read().locale ?? "zh-CN",
  showError: (title, message) => dialog.showErrorBox(title, message),
  exit: (code) => app.exit(code),
});
const desktopTestIsolation = resolveDesktopTestIsolation(
  process.env,
  app.getPath("home"),
);
if (desktopTestIsolation) {
  mkdirSync(desktopTestIsolation.home, { recursive: true, mode: 0o700 });
  mkdirSync(desktopTestIsolation.userData, { recursive: true, mode: 0o700 });
  app.setPath("home", desktopTestIsolation.home);
  app.setPath("userData", desktopTestIsolation.userData);
}
const trialBuild =
  typeof __ONE_MEMORY_TRIAL_PROFILE__ === "undefined"
    ? null
    : __ONE_MEMORY_TRIAL_PROFILE__;
const trialProfile =
  trialBuild === null
    ? null
    : resolveTrialProfile(
        trialBuild,
        app.getPath("home"),
        app.getPath("appData"),
        process.env.ONE_MEMORY_TRIAL_INSTANCE,
      );
const isolatedHourly =
  trialProfile === null &&
  isIsolatedHourlyDesktop(
    process.env.ONE_MEMORY_HOURLY_INSTANCE_ID,
    process.env.ONE_MEMORY_HOURLY_PACKAGE_ID,
  );
// The package id discriminates one hourly install/data root from another; it is
// only meaningful once the two-marker check above passed, so it stays undefined
// for the daily build and every non-isolated launch.
const isolatedHourlyPackageId = isolatedHourly
  ? process.env.ONE_MEMORY_HOURLY_PACKAGE_ID
  : undefined;
// The monotonic build number of this package, baked into `LSEnvironment` at
// pack time. `installPackagedRuntime` uses it as the anti-downgrade ordering
// authority; without it the bundle defaults to 0 and every package looks equal.
const packagedBuildNumber = resolvePackagedBuildNumber(
  process.env.ONE_MEMORY_BUILD_NUMBER,
  typeof __ONE_MEMORY_BUILD_NUMBER__ === "string"
    ? __ONE_MEMORY_BUILD_NUMBER__
    : undefined,
);
const packagedVersion =
  process.env.ONE_MEMORY_VERSION?.trim() ||
  (typeof __ONE_MEMORY_VERSION__ === "string"
    ? __ONE_MEMORY_VERSION__.trim()
    : "");
if (packagedVersion) process.env.ONE_MEMORY_VERSION = packagedVersion;
const packagedCommit = resolvePackagedCommit(
  process.env.ONE_MEMORY_COMMIT,
  typeof __ONE_MEMORY_COMMIT__ === "string" ? __ONE_MEMORY_COMMIT__ : undefined,
);
const legacyLoginLaunch = wasOpenedByLegacyLoginItem(app, process.platform);
// Set this before the single-instance lock, session creation and Core launch.
if (trialProfile) {
  mkdirSync(trialProfile.userDataDir, { recursive: true, mode: 0o700 });
  app.setPath("userData", trialProfile.userDataDir);
} else if (isolatedHourly && isolatedHourlyPackageId !== undefined) {
  // An isolated hourly package is a different product instance on the same
  // machine, so it must not share Electron's userData with the daily App. Left
  // shared, `requestSingleInstanceLock()` finds the daily instance's
  // `SingletonLock`, returns false, and this App silently exits with no window
  // whenever the daily App is running. Key the userData directory by the
  // package id (the same discriminator the data/install roots use) so the
  // singleton lock and caches are per-package.
  const hourlyUserDataDir = resolveCoreUserDataDir(
    app.getName(),
    app.getPath("appData"),
    isolatedHourlyPackageId,
  );
  mkdirSync(hourlyUserDataDir, { recursive: true, mode: 0o700 });
  app.setPath("userData", hourlyUserDataDir);
}

function prepareProtocol(): void {
  protocol.registerSchemesAsPrivileged([
    {
      scheme: "one-memory",
      privileges: {
        standard: true,
        secure: true,
        supportFetchAPI: true,
        corsEnabled: true,
      },
    },
  ]);
}

function attachWindowGuards(window: BrowserWindow): void {
  window.webContents.on("will-navigate", (event, targetUrl) => {
    if (
      !isAllowedNavigation(targetUrl, {
        packaged: app.isPackaged,
        developmentUrl,
      })
    ) {
      event.preventDefault();
    }
  });
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
}

function newWindow(): BrowserWindow {
  const window = new BrowserWindow(
    createWindowOptions(
      path.join(__dirname, "preload.js"),
      screen.getDisplayNearestPoint(screen.getCursorScreenPoint()).workArea,
    ),
  );
  displayWindows.add(window);
  window.once("closed", () => displayWindows.delete(window));
  attachWindowGuards(window);
  return window;
}

let startupWindow: BrowserWindow | null = null;
let appProtocolInstalled = false;

/** Serve the packaged web bundle. Also the first thing the startup window
 *  needs, so it is idempotent: the protocol may only be claimed once. */
function installPackagedWebProtocol(): void {
  if (!app.isPackaged || appProtocolInstalled) return;
  installAppProtocol(path.join(process.resourcesPath, "web"), {
    // Preferences are open by now: beforeNativeUi runs before this window.
    startupScreen: () => {
      let locale: GuiLocale = "zh-CN";
      try {
        locale = languagePreferences?.read().locale ?? "zh-CN";
      } catch {
        // An unreadable preference keeps the Chinese placeholder.
      }
      return startupScreenHtml(locale);
    },
  });
  appProtocolInstalled = true;
}

/** Put a window on screen before the services exist.
 *
 * `initialize` installs a runtime and waits for Core, which is tens of seconds
 * on a cold launch. Showing the placeholder first turns that wait from a dead
 * screen into a visible app. The page it loads performs no IPC, so it cannot
 * race the handlers `initialize` registers.
 */
async function createStartupWindow(isQuitting: () => boolean): Promise<void> {
  if (!app.isPackaged) return;
  installPackagedWebProtocol();
  const window = newWindow();
  startupWindow = window;
  window.once("ready-to-show", () => {
    if (!isQuitting() && !window.isDestroyed()) window.show();
  });
  try {
    await window.loadURL(STARTUP_SCREEN_URL);
  } catch (error) {
    if (!window.isDestroyed()) window.destroy();
    startupWindow = null;
    throw error;
  }
}

/** Swap the placeholder onto the real app once the services are up. */
async function revealStartupWindow(isQuitting: () => boolean): Promise<void> {
  const window = startupWindow;
  startupWindow = null;
  if (!window || window.isDestroyed() || isQuitting()) return;
  window.once("ready-to-show", () => {
    if (!isQuitting() && !window.isDestroyed()) window.show();
  });
  try {
    await window.loadURL(
      app.isPackaged ? `${APP_ORIGIN}/index.html` : developmentUrl,
    );
  } catch (error) {
    if (!window.isDestroyed()) window.destroy();
    throw error;
  }
}

async function createWindow(isQuitting: () => boolean): Promise<void> {
  void desktopDiagnostics?.log({
    level: "info",
    component: "desktop.window",
    event: "window.requested",
    outcome: "started",
    ...(desktopStartupStartedAt
      ? { duration_ms: Date.now() - desktopStartupStartedAt }
      : {}),
  });
  const window = newWindow();
  window.once("ready-to-show", () => {
    if (!isQuitting() && !window.isDestroyed()) {
      window.show();
      void desktopDiagnostics?.log({
        level: "info",
        component: "desktop.window",
        event: "window.ready",
        outcome: "succeeded",
        ...(desktopStartupStartedAt
          ? { duration_ms: Date.now() - desktopStartupStartedAt }
          : {}),
      });
    }
  });

  try {
    await window.loadURL(
      app.isPackaged ? `${APP_ORIGIN}/index.html` : developmentUrl,
    );
  } catch (error) {
    // A failed hidden window must not block a later explicit reopen attempt.
    if (!window.isDestroyed()) window.destroy();
    throw error;
  }
}

async function initialize(isQuitting: () => boolean): Promise<void> {
  installDenyAllPermissionPolicy(session.defaultSession);
  const corePort = app.isPackaged
    ? await findAvailablePort()
    : developmentCorePort();
  const brokerPort = app.isPackaged ? undefined : developmentBrokerPort();
  if (isQuitting()) return;
  const dataDir =
    trialProfile?.dataDir ??
    (isolatedHourly
      ? resolveCoreDataDir(
          app.getName(),
          app.getPath("home"),
          undefined,
          true,
          isolatedHourlyPackageId,
        )
      : undefined) ??
    resolveCoreDataDir(
      app.getName(),
      app.getPath("home"),
      process.env.ONE_MEMORY_DATA_DIR,
    );
  mkdirSync(dataDir, { recursive: true, mode: 0o700 });
  runtimeDataDir = dataDir;
  const hostIntegrationEnabled =
    process.env.ONE_MEMORY_DATA_DIR === undefined ||
    isCanonicalDailyDataDir(app.getPath("home"), dataDir);
  const diagnostics = new DiagnosticLogger({ dataDir, service: "desktop" });
  const runtimeDiagnostics = new DiagnosticLogger({
    dataDir,
    service: "runtime",
  });
  const hostDiagnostics = new DiagnosticLogger({
    dataDir,
    service: "host-bridge",
  });
  let readDiagnosticSummary = async (): Promise<
    Record<string, unknown>
  > => ({});
  desktopDiagnostics = diagnostics;
  const debugLogCursor = await captureDebugLogCursor(dataDir);
  const diagnosticIdentity = resolveDiagnosticBuildIdentity({
    environmentBuild: process.env.ONE_MEMORY_BUILD_NUMBER,
    environmentCommit: process.env.ONE_MEMORY_COMMIT,
    embeddedBuild:
      typeof __ONE_MEMORY_BUILD_NUMBER__ === "string"
        ? __ONE_MEMORY_BUILD_NUMBER__
        : undefined,
    embeddedCommit:
      typeof __ONE_MEMORY_COMMIT__ === "string"
        ? __ONE_MEMORY_COMMIT__
        : undefined,
    productVersion:
      typeof app.getVersion === "function" ? app.getVersion() : "0.0.0",
  });
  const diagnosticExportOptions = {
    dataDir,
    productVersion:
      typeof app.getVersion === "function" ? app.getVersion() : "0.0.0",
    ...diagnosticIdentity,
    ...(process.env.ONE_MEMORY_CORE_SHA256 === undefined
      ? {}
      : { coreSha256: process.env.ONE_MEMORY_CORE_SHA256 }),
    home: app.getPath("home"),
    downloadsDirectory: app.getPath("downloads"),
    crashReportsDirectory: path.join(
      app.getPath("home"),
      "Library",
      "Logs",
      "DiagnosticReports",
    ),
    logger: diagnostics,
    revealItem: (target: string) => shell.showItemInFolder(target),
    getSummary: () => readDiagnosticSummary(),
  };
  exportDiagnosticsFromMenu = async () => {
    await exportDesktopDiagnostics(diagnosticExportOptions);
  };
  await diagnostics.log({
    level: "info",
    component: "desktop.lifecycle",
    event: "desktop.initializing",
    outcome: "started",
    details: { mode: app.isPackaged ? "packaged" : "development" },
  });
  const hostBridgeDir = app.isPackaged
    ? path.join(process.resourcesPath, "host-bridge")
    : path.resolve(__dirname, "../../../packages/host-bridge/dist");
  const appLocationBlocked =
    app.isPackaged &&
    process.platform === "darwin" &&
    !previewProductName(app.getName()) &&
    !isolatedHourly &&
    !isInstalledApplicationsApp(process.execPath, process.platform);
  const productName = app.getName();
  const preview = previewProductName(productName);
  const installedApplication = isInstalledApplicationsApp(
    process.execPath,
    process.platform,
  );
  const appManagedRuntime =
    app.isPackaged &&
    process.platform === "darwin" &&
    trialProfile === null &&
    !isolatedHourly &&
    !preview &&
    (hostIntegrationEnabled || desktopTestIsolation !== null) &&
    installedApplication;
  const runningPackagedHost =
    app.isPackaged && !isolatedHourly
      ? await resolveRunningPackagedHostPaths({
          runtimeCommand: process.execPath,
          resourcesPath: process.resourcesPath,
        })
      : undefined;
  const hostRuntimeSelection =
    app.isPackaged && isolatedHourly
      ? await resolveVerifiedHostRuntime({
          resourcesPath: process.resourcesPath,
          appMarker: isolatedHourlyPackageId,
          buildNumber: packagedBuildNumber,
          home: app.getPath("home"),
          platform: process.platform,
          arch: process.arch,
          localAppData: process.env.LOCALAPPDATA,
        })
      : runningPackagedHost?.state === "blocked"
        ? runningPackagedHost
        : undefined;
  const packagedHostPaths =
    runningPackagedHost?.state === "verified"
      ? runningPackagedHost.value
      : undefined;
  const stable = app.isPackaged
    ? hostRuntimeSelection?.state === "verified"
      ? hostRuntimeSelection.value
      : isolatedHourly
        ? stableRecallPaths({
            home: app.getPath("home"),
            dataDir,
            localAppData: process.env.LOCALAPPDATA,
            isolated: true,
            hourlyPackageId: isolatedHourlyPackageId,
          })
        : (packagedHostPaths ??
          stableRecallPaths({
            home: app.getPath("home"),
            dataDir,
            localAppData: process.env.LOCALAPPDATA,
          }))
    : null;
  const recallRuntime = {
    runtimeCommand: stable?.runtimeCommand ?? process.execPath,
    mcpEntryPath: stable?.mcpEntryPath ?? path.join(hostBridgeDir, "mcp.cjs"),
    discoveryPath: path.join(dataDir, "integrations", "cursor-broker.json"),
    // Ships beside the MCP entry, and is the whole install for a host that
    // cannot speak MCP. Packaged installs name the copy under `current`,
    // which stays put when the app bundle is replaced.
    recallCliPath:
      stable?.recallCliPath ?? path.join(hostBridgeDir, "recall-cli.cjs"),
    captureCommand:
      stable?.captureCommand ??
      path.join(
        path.resolve(app.getAppPath(), "../.."),
        "apps/core/.venv/bin",
        process.platform === "win32" ? "one-memory.exe" : "one-memory",
      ),
    hermesProviderSource: path.join(
      path.dirname(stable?.mcpEntryPath ?? path.join(hostBridgeDir, "mcp.cjs")),
      "hermes-provider",
      "hy_memory",
    ),
    dataDir,
  };
  const standaloneRuntime =
    app.isPackaged &&
    process.env.ONE_MEMORY_DESKTOP_RUNTIME_MODE !== "embedded";
  const connectionClient = standaloneRuntime
    ? createConnectionClient({ dataDir })
    : null;
  let localPermissions: HostPermissions | null = null;
  let hostPermissions: HostPermissionClient;
  try {
    if (!hostIntegrationEnabled) {
      hostPermissions = blockedHostPermissions(
        "noncanonical_data_dir_host_integration_disabled",
      );
    } else if (standaloneRuntime) {
      hostPermissions = createHostPermissionClient({ dataDir });
    } else if (hostRuntimeSelection?.state === "blocked") {
      hostPermissions = blockedHostPermissions(hostRuntimeSelection.code);
    } else {
      localPermissions = await HostPermissions.open(dataDir, recallRuntime, {
        syncHookAssetsDirectory:
          stable?.syncHookAssetsDirectory ??
          path.join(hostBridgeDir, "sync-hook"),
      });
      hostPermissions = localPermissions;
    }
  } catch {
    // Never fall back to an anonymous broker or reset an existing broken store.
    throw new Error("host_permission_store_unavailable");
  }
  if (isQuitting()) return;
  const hosts = (await recallHosts(app.getPath("home"))).filter(
    (host) => host.hostId !== "openclaw",
  );
  readDiagnosticSummary = async () => {
    const core = coreSupervisor;
    if (!core) return {};
    const read = async (pathname: string): Promise<unknown> => {
      const response = await fetch(`http://127.0.0.1:${core.port}${pathname}`, {
        headers: { Authorization: `Bearer ${core.apiToken}` },
        signal: AbortSignal.timeout(2_000),
      });
      if (!response.ok) throw new Error("diagnostic_summary_unavailable");
      return response.json();
    };
    const [sources, processing, models, hostStates] = await Promise.all([
      read("/api/v1/sources").catch(() => null),
      read("/api/v1/memory-processing").catch(() => null),
      read("/api/v1/model-profiles").catch(() => null),
      Promise.all(
        hosts.map(async (host) => ({
          host_id: host.hostId,
          label: host.label,
          permission: await hostPermissions
            .getPermission(host.layout)
            .catch(() => ({ state: "unavailable" })),
        })),
      ),
    ]);
    const coreSha256 = await sha256File(
      path.join(
        process.resourcesPath,
        "core",
        process.platform === "win32" ? "one-memory.exe" : "one-memory",
      ),
    ).catch(() => null);
    return {
      core_sha256: coreSha256,
      sources,
      memory_processing: processing,
      hosts: hostStates,
      models,
    };
  };
  if (isQuitting()) return;
  const runtimeLaunch = () =>
    resolvePackagedRuntimeLaunch({
      resourcesPath: process.resourcesPath,
      dataDir,
      runtimeCommand: process.execPath,
      home: app.getPath("home"),
      localAppData: process.env.LOCALAPPDATA,
      isolated: isolatedHourly,
      hourlyPackageId: isolatedHourlyPackageId,
      hostRuntimeSelection,
      packagedHostPaths: !isolatedHourly ? packagedHostPaths : undefined,
      buildNumber: packagedBuildNumber,
      commit: packagedCommit,
      corePortOverride:
        process.env.ONE_MEMORY_DATA_DIR !== undefined ? corePort : undefined,
    });
  const repairInstalledHostCommands = async (): Promise<string[]> => {
    if (hostRuntimeSelection?.state === "blocked") return [];
    const repair = async () => {
      const repaired: string[] = [];
      for (const host of hosts) {
        const result = await hostPermissions.repairInstalledCommand(
          host.layout,
        );
        if (Array.isArray(result)) repaired.push(...result);
      }
      return repaired;
    };
    try {
      return await repair();
    } catch (error) {
      const message = error instanceof Error ? error.message : "";
      if (message.includes("connection_client_capability_unavailable"))
        return [];
      // An already-running Runtime from before this operation existed still
      // has the old command baked in. Replace it once; the new process
      // repoints installed hosts as it starts, without reissuing grants.
      if (
        !standaloneRuntime ||
        (!message.includes("host_control_invalid_input") &&
          !message.includes("connection_client_operation_unknown"))
      )
        throw error;
      await stopRuntimeProcess({ dataDir });
      await ensureRuntimeProcess({
        dataDir,
        launch: runtimeLaunch(),
        attempts: app.isPackaged ? 360 : 120,
      });
      return [];
    }
  };
  const requireHost = (hostId: unknown) => {
    const host = hosts.find((candidate) => candidate.hostId === hostId);
    if (!host) throw new Error("Unknown recall host");
    return host;
  };
  const observeDesktopAction = async <T>(
    event: string,
    action: () => Promise<T> | T,
    details: Record<string, string | number | boolean | null> = {},
    fallbackCode = "desktop_action_failed",
  ): Promise<T> => {
    const traceId = randomUUID();
    const started = Date.now();
    await diagnostics.log({
      level: "info",
      component: "desktop.action",
      event: `${event}.started`,
      outcome: "started",
      trace_id: traceId,
      details,
    });
    try {
      const result = await action();
      await diagnostics.log({
        level: "info",
        component: "desktop.action",
        event: `${event}.completed`,
        outcome: "succeeded",
        trace_id: traceId,
        duration_ms: Date.now() - started,
        details,
      });
      return result;
    } catch (error) {
      // A thrown DomainError already says what failed; the caller's code is
      // only the fallback for the failures nobody has classified yet. Logging
      // the fallback over a real code is what made issue #29 undiagnosable:
      // every refusal looked like the same generic action failure.
      await diagnostics.log({
        level: "error",
        component: "desktop.action",
        event: `${event}.failed`,
        outcome: "failed",
        trace_id: traceId,
        duration_ms: Date.now() - started,
        error_code: domainCodeOf(error) ?? fallbackCode,
        ...diagnosticError(error),
        details,
      });
      throw error;
    }
  };
  coreSupervisor = new CoreSupervisor({
    launch: resolveCoreLaunch({
      packaged: app.isPackaged,
      appPath: app.getAppPath(),
      resourcesPath: process.resourcesPath,
      platform: process.platform,
    }),
    readerEnabled: process.platform !== "win32",
    dataDir,
    attempts: app.isPackaged ? 300 : 50,
    delayMs: app.isPackaged ? 250 : 200,
    port: corePort,
    diagnostics: runtimeDiagnostics,
    parentSessionId: runtimeDiagnostics.sessionId,
    buildIdentity: {
      ...(packagedBuildNumber === undefined
        ? {}
        : { buildNumber: String(packagedBuildNumber) }),
      ...(packagedCommit === undefined ? {} : { commit: packagedCommit }),
    },
  });
  ipcMain.handle("one-memory:get-api-token", (event) => {
    const senderUrl = event.senderFrame?.url;
    if (!senderUrl) {
      throw new Error("Renderer frame is unavailable");
    }
    const sender = new URL(senderUrl);
    const isAppRenderer =
      sender.protocol === "one-memory:" && sender.host === "app";
    if (!isAppRenderer && sender.origin !== new URL(developmentUrl).origin) {
      throw new Error("Renderer origin is not allowed");
    }
    return coreSupervisor?.apiToken;
  });
  ipcMain.handle("one-memory:get-api-base-url", async (event) => {
    const senderUrl = event.senderFrame?.url;
    if (!senderUrl) {
      throw new Error("Renderer frame is unavailable");
    }
    const sender = new URL(senderUrl);
    const isAppRenderer =
      sender.protocol === "one-memory:" && sender.host === "app";
    if (!isAppRenderer && sender.origin !== new URL(developmentUrl).origin) {
      throw new Error("Renderer origin is not allowed");
    }
    if (!coreSupervisor) {
      throw new Error("Core supervisor is unavailable");
    }
    // `runtime-service.json.core_port` is written only after the Core actually
    // bound, and the Runtime publishes the URL it reached there. Prefer it over
    // the supervisor's requested port, so the renderer talks to the real
    // listener even when the managed Runtime (not this process) owns the Core
    // and chose a different port. Fall back to the supervisor during the
    // startup window before the receipt exists.
    const publishedPort = await readPublishedCorePort(dataDir).catch(
      () => null,
    );
    return `http://127.0.0.1:${publishedPort ?? coreSupervisor.port}`;
  });
  ipcMain.handle("one-memory:restart-background", async (event) => {
    assertAllowedRenderer(event.senderFrame?.url);
    if (!runtimeSupervisor) throw new Error("runtime_supervisor_unavailable");
    await runtimeSupervisor.restart();
    return { state: "restarted" as const };
  });
  /** Start a chat in another app with the memories already typed in.
   *
   * Takes a host id and text, never a URL: the link is built in `handoff.ts`
   * from a fixed table, so the renderer cannot reach a scheme that is not on
   * it. An unknown host is refused rather than opened. */
  ipcMain.handle(
    "one-memory:open-handoff",
    async (event, hostId: string, prompt: string) => {
      assertAllowedRenderer(event.senderFrame?.url);
      if (typeof hostId !== "string" || typeof prompt !== "string") {
        throw new Error("Invalid handoff input");
      }
      const url = handoffUrl(hostId, prompt, app.getPath("home"));
      if (!url) return { opened: false as const, reason: "unsupported_host" };
      await shell.openExternal(url);
      return { opened: true as const, reason: null };
    },
  );
  ipcMain.handle(
    "one-memory:create-custom-harness-skill",
    async (event, displayName: unknown) => {
      assertAllowedRenderer(event.senderFrame?.url);
      if (hostRuntimeSelection?.state === "blocked")
        throw new Error(hostRuntimeSelection.code);
      if (
        typeof displayName !== "string" ||
        displayName !== displayName.trim() ||
        displayName.length < 1 ||
        displayName.length > 80 ||
        /[\0\r\n]/.test(displayName)
      )
        throw new Error("custom_harness_name_invalid");
      const pairing = connectionClient?.createPairing
        ? await connectionClient.createPairing(displayName)
        : await localPermissions?.createCustomPairing(displayName);
      if (!pairing) throw new Error("host_pairing_unavailable");
      return {
        displayName: pairing.displayName,
        expiresAt: pairing.expiresAt,
        skill: renderCustomHarnessSkill(recallRuntime, pairing),
      };
    },
  );
  ipcMain.handle("one-memory:get-login-preference", (event) => {
    assertAllowedRenderer(event.senderFrame?.url);
    return readLoginPreference(dataDir, process.platform, appManagedRuntime);
  });
  ipcMain.handle("one-memory:set-login-preference", (event, input: unknown) => {
    assertAllowedRenderer(event.senderFrame?.url);
    return observeDesktopAction(
      "login_preference",
      () =>
        setLoginPreference(dataDir, process.platform, input, appManagedRuntime),
      { action: "set" },
    );
  });
  ipcMain.handle("one-memory:request-quit", (event, confirmed: unknown) => {
    assertAllowedRenderer(event.senderFrame?.url);
    if (confirmed !== true) throw new Error("quit_confirmation_required");
    app.quit();
  });
  ipcMain.handle("one-memory:copy-text", (event, text: unknown) => {
    assertAllowedRenderer(event.senderFrame?.url);
    if (typeof text !== "string" || !text || text.length > 1_000_000) {
      throw new Error("Invalid clipboard text");
    }
    clipboard.writeText(text);
  });
  ipcMain.handle("one-memory:recall-hosts", async (event) => {
    assertAllowedRenderer(event.senderFrame?.url);
    if (connectionClient?.host) {
      const inspector = createConnectionClient({ dataDir });
      const rows: unknown[] = [];
      for (const host of hosts) {
        const inspected = await inspector.host?.({
          hostId: host.hostId,
          operation: "inspect",
          args: [],
        });
        if (
          !inspected ||
          typeof inspected !== "object" ||
          Array.isArray(inspected)
        )
          throw Error("host_control_invalid_response");
        rows.push({
          ...inspected,
          handoff: canHandOff(host.hostId) ? "open" : "copy",
        });
      }
      return rows;
    }
    const permissions = localPermissions;
    if (!permissions) throw Error("host_permission_store_unavailable");
    return Promise.all(
      hosts.map(async (host) => {
        const permission = await hostPermissions.getPermission(host.layout);
        let installed = false;
        try {
          await verifyRecallInstall(host.layout);
          installed = true;
        } catch {
          installed = false;
        }
        return {
          hostId: host.hostId,
          label: host.label,
          present: await isHostPresent(host),
          installed,
          permission,
          handoff: canHandOff(host.hostId) ? "open" : "copy",
          // The recorded verdict, not a fresh one. `installed` only says our
          // files are intact, which is exactly what was true of the three
          // hosts that were installed against a wrong path and recalled
          // nothing — so the list needs to be able to say more than that.
          selfCheck: await readInstallSelfCheck(host.layout),
          // Call-rule drift is a file read, cheap enough for the list. The
          // link probe is not, which is why it stays on demand below.
          skillDrift: installed
            ? await checkSkillDrift(
                host.layout,
                permissions.runtimeFor(host.layout),
              )
            : null,
        };
      }),
    );
  });
  ipcMain.handle("one-memory:get-recall-permission", (event, hostId) => {
    assertAllowedRenderer(event.senderFrame?.url);
    return hostPermissions.getPermission(requireHost(hostId).layout);
  });
  ipcMain.handle("one-memory:get-sync-hook-status", (event, hostId) => {
    assertAllowedRenderer(event.senderFrame?.url);
    return hostPermissions.getSyncHookStatus(requireHost(hostId).layout);
  });
  ipcMain.handle("one-memory:preview-sync-hook", (event, hostId, operation) => {
    assertAllowedRenderer(event.senderFrame?.url);
    return hostPermissions.previewSyncHook(
      requireHost(hostId).layout,
      operation,
    );
  });
  ipcMain.handle("one-memory:apply-sync-hook", (event, hostId, input) => {
    assertAllowedRenderer(event.senderFrame?.url);
    const host = requireHost(hostId);
    return observeDesktopAction(
      "sync_hook",
      () => hostPermissions.applySyncHook(host.layout, input),
      { action: "apply", source_id: host.hostId },
    );
  });
  ipcMain.handle("one-memory:set-recall-permission", (event, hostId, input) => {
    assertAllowedRenderer(event.senderFrame?.url);
    const host = requireHost(hostId);
    return observeDesktopAction(
      "recall_permission",
      () => hostPermissions.setPermission(host.layout, input),
      { action: "set", source_id: host.hostId },
    );
  });
  ipcMain.handle(
    "one-memory:revoke-recall-permission",
    (event, hostId, input) => {
      assertAllowedRenderer(event.senderFrame?.url);
      const host = requireHost(hostId);
      return observeDesktopAction(
        "recall_permission",
        () => hostPermissions.revokePermission(host.layout, input),
        { action: "revoke", source_id: host.hostId },
      );
    },
  );
  // A fresh self-check, on demand. Separate from the host list because it
  // costs a Node process per host and the list is refreshed far too often to
  // carry that.
  ipcMain.handle("one-memory:probe-recall-host", async (event, hostId) => {
    assertAllowedRenderer(event.senderFrame?.url);
    const host = requireHost(hostId);
    if (connectionClient?.host) {
      return createConnectionClient({ dataDir }).host?.({
        hostId: host.hostId,
        operation: "probe",
        args: [],
      });
    }
    const permissions = localPermissions;
    if (!permissions) throw Error("host_permission_store_unavailable");
    // One round trip for both failure modes the detail page shows side by
    // side: whether the link reaches the app (R-24), and whether the
    // call-rule skill is still the one we ship (R-33).
    return permissions.withHostLock(host.layout, async () => {
      const runtime = permissions.runtimeFor(host.layout);
      const health = await permissions.recordLinkCheck(host.layout);
      const repoint = await planRecallRepoint(host.layout, runtime);
      return {
        hostId: host.hostId,
        probe: health.link,
        skill: health.skill,
        repoint:
          repoint === null ? null : { from: repoint.from, to: repoint.to },
      };
    });
  });
  ipcMain.handle("one-memory:preview-skill-sync", async (event, hostId) => {
    assertAllowedRenderer(event.senderFrame?.url);
    const host = requireHost(hostId);
    return hostPermissions.previewSkillSync(host.layout);
  });
  ipcMain.handle(
    "one-memory:apply-skill-sync",
    async (event, hostId, approved, overwriteModified) => {
      assertAllowedRenderer(event.senderFrame?.url);
      const host = requireHost(hostId);
      return hostPermissions.applySkillSync(
        host.layout,
        approved,
        overwriteModified,
      );
    },
  );
  // Installing rewrites a file belonging to another program, so the renderer
  // can ask what the edit would be before asking for it to happen.
  ipcMain.handle("one-memory:repair-recall-commands", async (event) => {
    assertAllowedRenderer(event.senderFrame?.url);
    return repairInstalledHostCommands();
  });
  ipcMain.handle("one-memory:preview-recall-install", async (event, hostId) => {
    assertAllowedRenderer(event.senderFrame?.url);
    const host = requireHost(hostId);
    return observeDesktopAction(
      "recall_install_preview",
      () => hostPermissions.previewInstall(host.layout),
      { action: "preview", source_id: host.hostId },
    );
  });
  ipcMain.handle(
    "one-memory:install-recall",
    async (event, hostId, approved) => {
      assertAllowedRenderer(event.senderFrame?.url);
      const host = requireHost(hostId);
      return observeDesktopAction(
        "recall_install",
        () => hostPermissions.install(host.layout, approved),
        { action: "install", source_id: host.hostId },
      );
    },
  );
  ipcMain.handle(
    "one-memory:preview-recall-uninstall",
    async (event, hostId) => {
      assertAllowedRenderer(event.senderFrame?.url);
      const host = requireHost(hostId);
      return observeDesktopAction(
        "recall_uninstall_preview",
        () => hostPermissions.previewUninstall(host.layout),
        { action: "preview", source_id: host.hostId },
      );
    },
  );
  ipcMain.handle(
    "one-memory:uninstall-recall",
    async (event, hostId, approved) => {
      assertAllowedRenderer(event.senderFrame?.url);
      const host = requireHost(hostId);
      return observeDesktopAction(
        "recall_uninstall",
        () => hostPermissions.uninstall(host.layout, approved),
        { action: "uninstall", source_id: host.hostId },
      );
    },
  );
  installPackagedWebProtocol();

  const migrateAtStartup = async (core: {
    command: string;
    args: string[];
    cwd?: string;
  }) => {
    const migration = await runStartupSdkMigration({
      dataDir,
      core,
      runtimeBuild:
        packagedBuildNumber === undefined
          ? typeof app.getVersion === "function"
            ? app.getVersion()
            : "0.0.0"
          : String(packagedBuildNumber),
    });
    await diagnostics.log({
      level: migration.state === "deferred" ? "warn" : "info",
      component: "desktop.sdk_migration",
      event: "sdk_migration.startup",
      outcome:
        migration.state === "cutover"
          ? "succeeded"
          : migration.state === "deferred"
            ? "blocked"
            : "skipped",
      ...(migration.code === undefined ? {} : { error_code: migration.code }),
      details: { state: migration.state },
    });
    requireCompletedStartupSdkMigration(migration);
  };

  try {
    if (languagePreferences) {
      disposeLanguagePreferences = installLanguagePreferencesIpc({
        ipc: ipcMain,
        preferences: languagePreferences,
        requireTrustedWindow,
        broadcast: (snapshot) => {
          for (const window of displayWindows) {
            try {
              assertTrustedWindow(window);
              window.webContents.mainFrame.send(
                "one-memory:language-preference-changed",
                snapshot,
              );
            } catch {
              // Navigation/destruction races must never leak to another document.
            }
          }
        },
      });
    }
    disposeDisplayPreferences = installDisplayPreferences();
    disposeSourceDirectoryPicker = installSourceDirectoryPicker();
    if (appManagedRuntime) {
      if (!desktopTestIsolation) disableLegacyLoginItem(app, process.platform);
      const installRoot = desktopTestIsolation
        ? path.join(app.getPath("home"), "managed-runtime-test")
        : defaultManagedInstallRoot("darwin", app.getPath("home"));
      const layout = runtimeInstallLayout({
        platform: "darwin",
        home: app.getPath("home"),
        installRoot,
        dataDir,
        ...(desktopTestIsolation
          ? {
              corePort: Number(process.env.ONE_MEMORY_CORE_PORT),
              brokerPort: Number(process.env.ONE_MEMORY_BROKER_PORT),
            }
          : {}),
      });
      let owner = await readRuntimeServiceOwner(dataDir);
      const serviceFile = path.join(
        app.getPath("home"),
        "Library",
        "LaunchAgents",
        "com.tencent.onememory.runtime.plist",
      );
      if (owner === null && existsSync(serviceFile)) {
        owner = await claimRuntimeServiceForCli(dataDir);
      }
      const startAtLogin = owner?.owner === "app" ? owner.start_at_login : true;
      const previousOwner = owner;
      let runtimeChanged = false;
      let transientRuntime = false;
      if (owner?.owner !== "cli") {
        const installed = await installPackagedRuntime({
          resourcesPath: process.resourcesPath,
          executablePath: process.execPath,
          installRoot,
          platform: "darwin",
          arch: process.arch,
          ...(packagedBuildNumber === undefined
            ? {}
            : { buildNumber: packagedBuildNumber }),
          ...(packagedCommit === undefined
            ? {}
            : { gitCommit: packagedCommit }),
          // Only the packaged Desktop path opts into a valid same-build,
          // different-commit takeover. CLI installs remain strict.
          replaceSameBuild: true,
          serviceEnableMarker: runtimeServiceEnableMarkerPath(dataDir),
        });
        runtimeChanged = !installed.reused_current;
        await installBundledCliIfNeeded({
          home: app.getPath("home"),
          installRoot,
          platform: "darwin",
        });
      }
      if (
        !desktopBackgroundLaunch &&
        !desktopTestIsolation &&
        process.env.NODE_ENV !== "test"
      ) {
        await migrateAtStartup({
          command: stable?.captureCommand ?? layout.coreCommand,
          args: [],
        });
      }
      const alreadyRunning = runtimeChanged
        ? false
        : await waitForRuntimeProcess({ dataDir, attempts: 4 }).then(
            () => true,
            () => false,
          );
      if (owner?.owner !== "cli") {
        const appBundle = path.resolve(path.dirname(process.execPath), "../..");
        owner = await claimRuntimeServiceForApp({
          dataDir,
          appBundle,
          appExecutable: process.execPath,
          startAtLogin,
        });
      }
      if (!alreadyRunning) {
        // A durable `launchctl disable` override is a state to report, not a
        // failure to undo: `installUserService` never re-enables a service the
        // user or the system disabled. An explicit App launch may still own a
        // transient Runtime for this UI session. Shutdown stops that process.
        const serviceMode = await installServiceWithDisabledFallback({
          install: async () => {
            await installUserService(layout, {
              uid: process.getuid?.(),
              platform: "darwin",
            });
            await waitForRuntimeProcess({ dataDir, attempts: 360 });
          },
          restoreOwner: async () => {
            if (owner?.owner === "app") {
              if (previousOwner === null)
                await removeRuntimeServiceOwner(dataDir);
              else await writeRuntimeServiceOwner(dataDir, previousOwner);
            }
            owner = previousOwner;
          },
          reportDisabled: () =>
            diagnostics.log({
              level: "warn",
              component: "desktop.stable_runtime",
              event: "stable_runtime.service_disabled",
              outcome: "degraded",
              error_code: "runtime_service_disabled_by_system",
            }),
          startTransient: async () => {
            await ensureRuntimeProcess({
              dataDir,
              launch: runtimeLaunch(),
              attempts: app.isPackaged ? 360 : 120,
            });
          },
        });
        transientRuntime = serviceMode === "transient";
      }
      if (owner?.owner === "app" && !startAtLogin) {
        await disableRuntimeService(dataDir);
      }
      preserveManagedRuntimeOnQuit = !transientRuntime;
    } else if (standaloneRuntime) {
      if (
        !desktopBackgroundLaunch &&
        !desktopTestIsolation &&
        process.env.NODE_ENV !== "test"
      ) {
        await migrateAtStartup(
          resolveCoreLaunch({
            packaged: app.isPackaged,
            appPath: app.getAppPath(),
            resourcesPath: process.resourcesPath,
            platform: process.platform,
          }),
        );
      }
      await ensureRuntimeProcess({
        dataDir,
        launch: runtimeLaunch(),
        attempts: app.isPackaged ? 360 : 120,
      });
    }
    if (isQuitting()) return;
    runtimeSupervisor = new RuntimeSupervisor({
      core: coreSupervisor,
      diagnostics: runtimeDiagnostics,
      createSidecar(connection) {
        if (!hostIntegrationEnabled) {
          return {
            async start() {
              await hostDiagnostics.log({
                level: "info",
                component: "host.broker",
                event: "broker.disabled",
                outcome: "blocked",
                instance_id: connection.instanceId,
                details: { reason: "noncanonical_data_dir" },
              });
            },
            async stop() {},
          };
        }
        const discoveryPath = path.join(
          dataDir,
          "integrations",
          "cursor-broker.json",
        );
        return new RecallBrokerSupervisor({
          discoveryPath,
          instanceId: connection.instanceId,
          diagnostics: hostDiagnostics,
          createBroker: () => {
            if (!localPermissions) throw Error("runtime_broker_unavailable");
            return new RecallBroker({
              coreBaseUrl: connection.coreBaseUrl,
              coreApiToken: connection.coreApiToken,
              discoveryPath,
              instanceId: connection.instanceId,
              hostGrantStore: localPermissions.hostGrantStore,
              ...(brokerPort === undefined
                ? {}
                : { preferredPort: brokerPort }),
              diagnostics: (event) => hostDiagnostics.log(event),
              sourceNotificationHandler: createSourceChangeHandler({
                coreBaseUrl: connection.coreBaseUrl,
                coreApiToken: connection.coreApiToken,
                instanceId: connection.instanceId,
              }),
            });
          },
        });
      },
    });
    await runtimeSupervisor.start();
    if (isQuitting()) return;
    if (
      app.isPackaged &&
      process.platform === "darwin" &&
      !preview &&
      hostIntegrationEnabled &&
      !installedApplication
    ) {
      diagnostics.log({
        level: "warn",
        component: "desktop.stable_runtime",
        event: "stable_runtime.install_skipped",
        outcome: "blocked",
        error_code: "app_not_in_applications",
      });
      void dialog.showMessageBox({
        type: "warning",
        message: translate(
          languagePreferences?.read().locale ?? "zh-CN",
          "nativeMoveTitle",
        ),
        detail: translate(
          languagePreferences?.read().locale ?? "zh-CN",
          "nativeMoveDetail",
        ),
        buttons: [
          translate(
            languagePreferences?.read().locale ?? "zh-CN",
            "nativeAcknowledge",
          ),
        ],
      });
    } else if (
      trialProfile === null &&
      !isolatedHourly &&
      hostIntegrationEnabled &&
      !appManagedRuntime
    ) {
      stableRuntimeRepair = startStableRuntimeRepair({
        packaged: app.isPackaged,
        preview,
        install: async () => {
          if (process.platform !== "darwin" && process.platform !== "linux") {
            throw new Error("packaged_runtime_platform_unsupported");
          }
          // Captured here, not at the call site: the install callback below is
          // a nested function, and TypeScript drops property narrowing there.
          const hostPlatform = process.platform;
          const installRoot = defaultManagedInstallRoot(
            process.platform,
            app.getPath("home"),
            process.env.LOCALAPPDATA,
          );
          await installPackagedRuntime({
            resourcesPath: process.resourcesPath,
            executablePath: process.execPath,
            installRoot,
            platform: hostPlatform,
            arch: process.arch,
            ...(packagedBuildNumber === undefined
              ? {}
              : { buildNumber: packagedBuildNumber }),
            ...(packagedCommit === undefined
              ? {}
              : { gitCommit: packagedCommit }),
            replaceSameBuild: true,
            serviceEnableMarker: runtimeServiceEnableMarkerPath(dataDir),
          });
          await installBundledCliIfNeeded({
            home: app.getPath("home"),
            installRoot,
            platform: process.platform,
          });
        },
        repair: repairInstalledHostCommands,
        onStage: (stage, outcome) =>
          diagnostics.log({
            level: "info",
            component: "desktop.stable_runtime",
            event: `stable_runtime.${stage}`,
            outcome,
          }),
        onError: (error) =>
          diagnostics.log({
            level: "error",
            component: "desktop.stable_runtime",
            event: "stable_runtime.repair_failed",
            outcome: "failed",
            error_code: stableRuntimeErrorCode(error),
            ...diagnosticError(error),
          }),
      });
    }
    disposeBackgroundHealth = installBackgroundHealthIpc({
      ipc: ipcMain,
      dataDir,
      requireTrustedWindow,
    });
    disposeDiagnostics = installDiagnosticsIpc({
      ipc: ipcMain,
      ...diagnosticExportOptions,
      startedAt: desktopStartupStartedAt,
      debugLogCursor,
      requireTrustedWindow,
      logUiTrace: (input) => diagnostics.log(uiTraceDiagnosticInput(input)),
      revealItem: (target) => shell.showItemInFolder(target),
    });
    connectionSetup = installConnectionSetupIpc({
      ipc: ipcMain,
      dataDir,
      hostPermissions,
      hosts,
      ...(connectionClient ? { remote: connectionClient } : {}),
      ...(!hostIntegrationEnabled
        ? { disabledReason: "noncanonical_data_dir" as const }
        : appLocationBlocked
          ? {
              disabledReason: "app_not_in_applications" as const,
            }
          : {}),
      diagnostics,
      requireTrustedWindow,
      getCore: () => {
        if (!coreSupervisor)
          throw new Error("connection_setup_runtime_unavailable");
        return {
          port: coreSupervisor.port,
          apiToken: coreSupervisor.apiToken,
          instanceId: coreSupervisor.instanceId,
        };
      },
    });
    await diagnostics.log({
      level: "info",
      component: "desktop.lifecycle",
      event: "desktop.ready",
      outcome: "succeeded",
      instance_id: coreSupervisor.instanceId,
    });
    const smokeQuitMilliseconds = Number(
      process.env.ONE_MEMORY_SMOKE_QUIT_MS ?? "0",
    );
    if (
      !app.isPackaged &&
      Number.isFinite(smokeQuitMilliseconds) &&
      smokeQuitMilliseconds > 0
    ) {
      setTimeout(() => app.quit(), smokeQuitMilliseconds);
    }
  } catch (error) {
    console.error(
      "MemHub startup failed",
      error instanceof Error ? error.name : "UnknownError",
    );
    await desktopDiagnostics?.log({
      level: "error",
      component: "desktop.lifecycle",
      event: "desktop.start_failed",
      outcome: "failed",
      error_code: stableRuntimeErrorCode(error),
      ...diagnosticError(error),
    });
    throw error;
  }
}

export async function waitForStableRuntimeRepair(): Promise<void> {
  await stableRuntimeRepair;
}

function blockedHostPermissions(code: string): HostPermissionClient {
  return new Proxy({} as HostPermissionClient, {
    get() {
      return async () => {
        throw new Error(code);
      };
    },
  });
}

async function sha256File(target: string): Promise<string> {
  const digest = createHash("sha256");
  for await (const chunk of createReadStream(target)) digest.update(chunk);
  return digest.digest("hex");
}

const desktopVersion = desktopVersionOutput(process.argv, packagedVersion);
const desktopBackgroundLaunch =
  legacyLoginLaunch || process.argv.includes("--background");
if (desktopVersion !== null) {
  process.stdout.write(desktopVersion, () => app.exit(0));
} else
  startDesktopLifecycle({
    app,
    platform: process.platform,
    background: desktopBackgroundLaunch,
    prepare: prepareProtocol,
    beforeNativeUi: async () => {
      languagePreferences = await LanguagePreferences.open(
        app.getPath("userData"),
        preferredSystemLanguage(app),
      );
    },
    initialize,
    createWindow,
    showStartupWindow: createStartupWindow,
    revealStartupWindow,
    getWindows: () => [...displayWindows],
    installTray: (open) => {
      disposeApplicationMenu = installApplicationMenu({
        productName: app.getName(),
        language: languagePreferences ?? undefined,
        buildMenu: (template) => Menu.buildFromTemplate(template),
        setMenu: (menu) => Menu.setApplicationMenu(menu),
        exportLogs: async () => {
          if (!exportDiagnosticsFromMenu)
            throw new Error("diagnostic_export_not_ready");
          await exportDiagnosticsFromMenu();
        },
        reportError: (message) => dialog.showErrorBox("MemHub", message),
      });
      disposeTray = installBackgroundTray({
        app,
        language: languagePreferences ?? undefined,
        platform: process.platform,
        resourcesPath: process.resourcesPath,
        appPath: app.getAppPath(),
        createImage: (file) => nativeImage.createFromPath(file),
        createTray: (image) => new Tray(image),
        buildMenu: (template) => Menu.buildFromTemplate(template),
        open,
        reportError: (message) => dialog.showErrorBox("MemHub", message),
        readLoginPreference: () =>
          runtimeDataDir
            ? readLoginPreference(
                runtimeDataDir,
                process.platform,
                preserveManagedRuntimeOnQuit,
              )
            : Promise.resolve({
                supported: false,
                enabled: false,
                status: "not-ready",
              }),
        setLoginPreference: (enabled) => {
          if (!runtimeDataDir) throw new Error("runtime_not_ready");
          return setLoginPreference(
            runtimeDataDir,
            process.platform,
            { openAtLogin: enabled, userConfirmed: true },
            preserveManagedRuntimeOnQuit,
          );
        },
        ...(trialProfile
          ? { allowLoginStartup: false, productName: app.getName() }
          : {}),
      });
    },
    stop: async () => {
      const started = Date.now();
      await desktopDiagnostics?.log({
        level: "info",
        component: "desktop.lifecycle",
        event: "desktop.stopping",
        outcome: "started",
        ...(coreSupervisor ? { instance_id: coreSupervisor.instanceId } : {}),
      });
      // An admitted native effect must settle and journal before Runtime detach.
      disposeLanguagePreferences?.();
      disposeLanguagePreferences = null;
      await languagePreferences?.drain();
      await connectionSetup?.drain();
      // BUG-12: stopping the supervisor only releases this process's handles. In
      // a packaged build the Runtime is its own process and was left running
      // after the app exited, still named by its receipt, so the next launch
      // thought it was already up and attached to a process that never answered.
      const runtime = await shutdownRuntime({
        // Only read on the standalone path, which is gated on the directory
        // being known; a quit before startup got that far has no Runtime to
        // stop and says so by taking the embedded path instead.
        dataDir: runtimeDataDir ?? "",
        standalone: app.isPackaged && runtimeDataDir !== null,
        preserveManagedRuntime: preserveManagedRuntimeOnQuit,
        stopSupervisor: async () => {
          const results = await Promise.allSettled([
            runtimeSupervisor?.stop() ?? coreSupervisor?.stop(),
          ]);
          for (const result of results) {
            if (result.status === "rejected")
              console.error(
                "MemHub shutdown failed",
                result.reason instanceof Error
                  ? result.reason.name
                  : "UnknownError",
              );
          }
        },
        onDiagnostic: async (event) => {
          await desktopDiagnostics?.log({
            level: event.outcome === "degraded" ? "warn" : "info",
            component: "desktop.lifecycle",
            event: "desktop.runtime_stop",
            outcome: event.outcome,
            details: { runtime_state: event.detail },
          });
        },
      });
      if (runtime.runtime === "failed") {
        console.error("MemHub shutdown failed", runtime.detail);
      }
      await desktopDiagnostics?.log({
        level: "info",
        component: "desktop.lifecycle",
        event: "desktop.stopped",
        outcome: "succeeded",
        duration_ms: Date.now() - started,
      });
    },
    dispose: () => {
      disposeLanguagePreferences?.();
      disposeLanguagePreferences = null;
      disposeApplicationMenu?.();
      disposeApplicationMenu = null;
      connectionSetup?.dispose();
      disposeDiagnostics?.();
      disposeDiagnostics = null;
      exportDiagnosticsFromMenu = null;
      disposeBackgroundHealth?.();
      disposeBackgroundHealth = null;
      connectionSetup = null;
      disposeTray?.();
      disposeTray = null;
      disposeSourceDirectoryPicker?.();
      disposeSourceDirectoryPicker = null;
      disposeDisplayPreferences?.();
      disposeDisplayPreferences = null;
      displayWindows.clear();
    },
    onError: (error) => {
      console.error(
        "MemHub desktop failed",
        error instanceof Error ? error.name : "UnknownError",
      );
      dialog.showErrorBox(
        translate(
          languagePreferences?.read().locale ?? "zh-CN",
          "nativeErrorTitle",
        ),
        error instanceof Error ? error.message : "desktop_unavailable",
      );
    },
  });

function assertAllowedRenderer(senderUrl: string | undefined): void {
  if (!senderUrl) throw new Error("Renderer frame is unavailable");
  const sender = new URL(senderUrl);
  const isAppRenderer =
    sender.protocol === "one-memory:" && sender.host === "app";
  if (!isAppRenderer && sender.origin !== new URL(developmentUrl).origin) {
    throw new Error("Renderer origin is not allowed");
  }
}

function assertTrustedWindow(window: BrowserWindow): void {
  if (
    !displayWindows.has(window) ||
    window.isDestroyed() ||
    window.webContents.isDestroyed()
  )
    throw new Error("Display preference window is unavailable");
  const frame = window.webContents.mainFrame;
  if (frame.detached || frame.isDestroyed() || frame.parent !== null)
    throw new Error("Display preference frame is unavailable");
  assertAllowedRenderer(frame.url);
  const url = new URL(frame.url);
  if (
    url.username ||
    url.password ||
    !isAllowedNavigation(frame.url, {
      packaged: app.isPackaged,
      developmentUrl,
    })
  )
    throw new Error("Renderer origin is not allowed");
}

function installDisplayPreferences(): () => void {
  const preferences = new DisplayPreferences(
    () => systemPreferences.getAnimationSettings().prefersReducedMotion,
    () => nativeTheme.prefersReducedTransparency,
  );
  let latest = preferences.read();
  const refresh = () => {
    const next = preferences.read();
    if (!next || next.revision === latest?.revision) return next;
    latest = next;
    for (const window of displayWindows) {
      try {
        assertTrustedWindow(window);
        window.webContents.mainFrame.send(
          "one-memory:display-preferences-changed",
          next,
        );
      } catch {
        // Navigation or destruction can race an update; never send elsewhere.
      }
    }
    return next;
  };
  ipcMain.handle(
    "one-memory:get-display-preferences",
    (event: IpcMainInvokeEvent) => {
      requireTrustedWindow(event);
      const snapshot = refresh();
      if (!snapshot) throw new Error("display_preferences_unavailable");
      return snapshot;
    },
  );
  nativeTheme.on("updated", refresh);
  app.on("browser-window-focus", refresh);
  return () => {
    nativeTheme.removeListener("updated", refresh);
    app.removeListener("browser-window-focus", refresh);
    ipcMain.removeHandler("one-memory:get-display-preferences");
  };
}

function requireTrustedWindow(event: IpcMainInvokeEvent): BrowserWindow {
  const window = [...displayWindows].find(
    (candidate) => candidate.webContents === event.sender,
  );
  if (!window) throw new Error("Trusted renderer window is unavailable");
  assertTrustedWindow(window);
  if (event.senderFrame !== event.sender.mainFrame)
    throw new Error("This operation requires the main frame");
  return window;
}

function installSourceDirectoryPicker(): () => void {
  const picker = new SourceDirectoryPicker({
    requireWindow: requireTrustedWindow,
    showOpenDialog: (window, options) => dialog.showOpenDialog(window, options),
  });
  ipcMain.handle(
    "one-memory:pick-source-directory",
    (event: IpcMainInvokeEvent, sourceId: unknown) =>
      picker.pick(event, sourceId),
  );
  return () => {
    picker.dispose();
    ipcMain.removeHandler("one-memory:pick-source-directory");
  };
}
