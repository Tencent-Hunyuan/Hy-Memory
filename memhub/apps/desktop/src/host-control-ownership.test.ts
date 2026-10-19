import { EventEmitter } from "node:events";
import { mkdir, mkdtemp, readdir, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const fake = vi.hoisted(() => ({
  ready: undefined as (() => Promise<void>) | undefined,
  handlers: new Map<string, (...args: unknown[]) => unknown>(),
  windows: [] as FakeWindow[],
  app: undefined as unknown as EventEmitter,
  theme: undefined as unknown as EventEmitter,
  home: "",
  dataDir: "",
  missingBroker: false,
  hostCapability: true,
  open: vi.fn(),
  host: vi.fn<
    (input: {
      hostId: string;
      operation: string;
      args: unknown[];
    }) => Promise<unknown>
  >(),
  createClient: vi.fn(),
  brokerConstructed: vi.fn(),
  brokerStart: vi.fn(async () => {}),
  ensureRuntime: vi.fn(async () => "started"),
  verifyHostRuntime: vi.fn(),
  resolveRuntimeLaunch: vi.fn((_options: unknown) => ({
    command: "synthetic-runtime",
    args: [],
  })),
  coreStart: vi.fn(async () => {}),
  coreStop: vi.fn(async () => {}),
  coreOptions: undefined as Record<string, unknown> | undefined,
  probeBroker: vi.fn(async () => {}),
  error: vi.fn(),
  quit: vi.fn(),
  localRead: vi.fn(),
  waitForStableRuntimeRepair: undefined as (() => Promise<void>) | undefined,
}));
const resources = Object.getOwnPropertyDescriptor(process, "resourcesPath");

class FakeWindow extends EventEmitter {
  frame = {
    url: "one-memory://app/index.html",
    parent: null,
    detached: false,
    isDestroyed: () => false,
    send: vi.fn(),
  };
  webContents = Object.assign(new EventEmitter(), {
    mainFrame: this.frame,
    isDestroyed: () => false,
    setWindowOpenHandler: vi.fn(),
  });
  constructor() {
    super();
    fake.windows.push(this);
  }
  isDestroyed() {
    return false;
  }
  isMinimized() {
    return false;
  }
  restore() {}
  show() {}
  focus() {}
  async loadURL(url: string) {
    this.frame.url = url;
  }
}

vi.mock("node:fs", async (importOriginal) => ({
  ...(await importOriginal<typeof import("node:fs")>()),
  appendFileSync: vi.fn(),
  mkdirSync: vi.fn(),
}));
vi.mock("./background-tray", () => ({
  LOGIN_SERVICE_NAME: "one-memory-background.plist",
  installBackgroundTray: () => vi.fn(),
}));
vi.mock("electron", () => ({
  get app() {
    return Object.assign(fake.app, {
      requestSingleInstanceLock: () => true,
      whenReady: () => ({
        // biome-ignore lint/suspicious/noThenProperty: Manually drive the real initialize callback without Electron.
        then(callback: () => Promise<void>) {
          fake.ready = callback;
          return Promise.resolve();
        },
      }),
      isPackaged: true,
      getName: () => "One Memory Preview",
      getPath: () => fake.home,
      setPath: vi.fn(),
      getAppPath: () => fake.home,
      quit: fake.quit,
      exit: vi.fn(),
    });
  },
  get nativeTheme() {
    return Object.assign(fake.theme, { prefersReducedTransparency: false });
  },
  systemPreferences: {
    getAnimationSettings: () => ({ prefersReducedMotion: false }),
  },
  BrowserWindow: FakeWindow,
  ipcMain: {
    handle: (name: string, callback: (...args: unknown[]) => unknown) =>
      fake.handlers.set(name, callback),
    removeHandler: (name: string) => fake.handlers.delete(name),
  },
  protocol: { registerSchemesAsPrivileged: vi.fn() },
  screen: {
    getDisplayNearestPoint: () => ({ workArea: { width: 1500, height: 1000 } }),
    getCursorScreenPoint: () => ({ x: 0, y: 0 }),
  },
  session: { defaultSession: {} },
  clipboard: { writeText: vi.fn() },
  dialog: { showErrorBox: fake.error, showOpenDialog: vi.fn() },
  Menu: { buildFromTemplate: vi.fn(() => ({})), setApplicationMenu: vi.fn() },
  shell: { openExternal: vi.fn() },
}));
vi.mock("./permissions", () => ({ installDenyAllPermissionPolicy: vi.fn() }));
vi.mock("./app-protocol", async (importOriginal) => ({
  ...(await importOriginal()),
  installAppProtocol: vi.fn(),
}));
vi.mock("./core-supervisor", () => ({
  CoreSupervisor: class {
    port = 9999;
    apiToken = "synthetic-token";
    instanceId = "synthetic-instance";
    constructor(options: Record<string, unknown>) {
      fake.coreOptions = options;
    }
    start = fake.coreStart;
    stop = fake.coreStop;
  },
  resolveCoreDataDir: () => fake.dataDir,
  resolveCoreUserDataDir: () => path.join(fake.home, "hourly-user-data"),
  resolveCoreLaunch: () => ({}),
  resolvePackagedRuntimeLaunch: (options: unknown) =>
    fake.resolveRuntimeLaunch(options),
  resolveRunningPackagedHostPaths: async () => ({
    state: "verified",
    value: {
      runtimeCommand: "/synthetic/MemHub.app/Contents/MacOS/MemHub",
      mcpEntryPath:
        "/synthetic/MemHub.app/Contents/Resources/host-bridge/mcp.cjs",
      recallCliPath:
        "/synthetic/MemHub.app/Contents/Resources/host-bridge/recall-cli.cjs",
      captureCommand:
        "/synthetic/MemHub.app/Contents/Resources/core/one-memory",
      syncHookAssetsDirectory:
        "/synthetic/MemHub.app/Contents/Resources/host-bridge/sync-hook",
    },
  }),
  resolveVerifiedHostRuntime: (options: unknown) =>
    fake.verifyHostRuntime(options),
  stableRecallPaths: () => ({
    runtimeCommand: "/synthetic/current/bin/node",
    mcpEntryPath: "/synthetic/current/host-bridge/mcp.cjs",
    recallCliPath: "/synthetic/current/host-bridge/recall-cli.cjs",
    captureCommand: "/synthetic/current/core/one-memory",
  }),
  ensureRuntimeProcess: fake.ensureRuntime,
  findAvailablePort: async () => 9999,
}));
vi.mock("./host-permissions", () => ({
  HostPermissions: { open: fake.open },
  LOCAL_EXPERIENCE_UNAVAILABLE: "体验版暂不支持",
}));
vi.mock("./recall-hosts", () => ({
  recallHosts: async () => [
    {
      hostId: "codex",
      label: "Codex",
      layout: {
        hostId: "codex",
        configPath: path.join(fake.home, "never-read-config"),
      },
      presencePath: fake.home,
    },
  ],
  isHostPresent: fake.localRead,
}));
vi.mock("@one-memory/host-bridge/installer", () => ({
  checkSkillDrift: fake.localRead,
  planRecallRepoint: fake.localRead,
  readInstallSelfCheck: fake.localRead,
  renderConnectionMaterial: fake.localRead,
  verifyRecallInstall: fake.localRead,
}));
// Spread the real module instead of whitelisting it. Main imports domain
// helpers that runtime-control re-exports from host-bridge, so a partial mock
// silently hands back undefined for them and every error path that calls one
// fails with a TypeError instead of the code under test.
vi.mock("@one-memory/host-bridge", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@one-memory/host-bridge")>()),
  RecallBroker: class {
    constructor(options: unknown) {
      fake.brokerConstructed(options);
    }
    start = fake.brokerStart;
    async stop() {}
  },
}));
vi.mock("../../../packages/runtime-control/src/connection-client.js", () => ({
  createConnectionClient: (options: unknown) => {
    fake.createClient(options);
    return fake.hostCapability ? { host: fake.host } : {};
  },
}));
// Keep real supervisor attach/fallback logic. Replace only discovery, process
// liveness and health I/O, so a missing broker exercises Main's actual factory.
vi.mock("@one-memory/runtime-control", async (importOriginal) => {
  const original =
    await importOriginal<typeof import("@one-memory/runtime-control")>();
  return {
    ...original,
    RecallBrokerSupervisor: class extends original.RecallBrokerSupervisor {
      constructor(
        options: ConstructorParameters<
          typeof original.RecallBrokerSupervisor
        >[0],
      ) {
        super({
          ...options,
          processIsAlive: () => true,
          readBrokerDiscovery: async () => {
            if (fake.missingBroker)
              throw Error("runtime_broker_discovery_missing");
            return {
              schema_version: 1,
              service: "one-memory-recall-broker",
              instance_id: "synthetic-instance",
              pid: 12345,
              host: "127.0.0.1",
              port: 9998,
              status: "running",
              updated_at: new Date(0).toISOString(),
            };
          },
          probeBroker: fake.probeBroker,
        });
      }
    },
  };
});

beforeEach(async () => {
  vi.resetModules();
  vi.resetAllMocks();
  fake.app = new EventEmitter();
  fake.theme = new EventEmitter();
  fake.handlers.clear();
  fake.windows.length = 0;
  fake.ready = undefined;
  fake.missingBroker = false;
  fake.hostCapability = true;
  fake.coreOptions = undefined;
  fake.verifyHostRuntime.mockResolvedValue({
    state: "blocked",
    code: "host_runtime_current_unavailable",
  });
  fake.home = await mkdtemp(path.join(os.tmpdir(), "om-host-ownership-"));
  fake.dataDir = path.join(fake.home, ".one-memory");
  await mkdir(fake.dataDir, { mode: 0o700 });
  fake.open.mockResolvedValue({
    hostGrantStore: {},
    repairInstalledCommand: vi.fn(async () => []),
  });
  fake.host.mockResolvedValue({ state: "synthetic-remote-result" });
  Object.defineProperty(process, "resourcesPath", {
    configurable: true,
    value: path.join(fake.home, "resources"),
  });
  vi.stubEnv("HOME", fake.home);
  vi.stubEnv("ONE_MEMORY_DESKTOP_RUNTIME_MODE", "standalone");
  vi.stubEnv("ONE_MEMORY_WEB_URL", "http://127.0.0.1:5173");
  vi.stubEnv("ONE_MEMORY_SMOKE_QUIT_MS", "0");
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(async () => {
  await fake.waitForStableRuntimeRepair?.();
  const quitCalls = fake.quit.mock.calls.length;
  fake.app.emit("before-quit", { preventDefault() {} });
  await vi.waitFor(() =>
    expect(fake.quit.mock.calls.length).toBeGreaterThan(quitCalls),
  );
  fake.app.emit("will-quit");
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  if (resources) Object.defineProperty(process, "resourcesPath", resources);
  else Reflect.deleteProperty(process, "resourcesPath");
  await rm(fake.home, { recursive: true, force: true });
});
async function startup() {
  const main = await import("./main.js");
  fake.waitForStableRuntimeRepair = main.waitForStableRuntimeRepair;
  if (!fake.ready) throw Error("Missing initialize callback");
  await fake.ready();
}
async function invoke(channel: string, ...args: unknown[]) {
  const handler = fake.handlers.get(`one-memory:${channel}`);
  if (!handler) throw Error(`Missing ${channel} handler`);
  return handler(
    { senderFrame: { url: "one-memory://app/index.html" } },
    ...args,
  );
}

describe("packaged Desktop host-control ownership", () => {
  it("enables the read-only Reader on non-Windows desktop launch", async () => {
    await startup();

    expect(fake.coreOptions).toMatchObject({
      readerEnabled: process.platform !== "win32",
    });
  });

  it("blocks every host write when an explicit noncanonical data directory is used", async () => {
    fake.dataDir = path.join(fake.home, "coldstart", "data");
    await mkdir(fake.dataDir, { recursive: true, mode: 0o700 });
    vi.stubEnv("ONE_MEMORY_DATA_DIR", fake.dataDir);

    await startup();

    expect(fake.host).not.toHaveBeenCalled();
    await expect(invoke("repair-recall-commands")).rejects.toThrow(
      "noncanonical_data_dir_host_integration_disabled",
    );
    await expect(
      invoke("install-recall", "codex", "a".repeat(64)),
    ).rejects.toThrow("noncanonical_data_dir_host_integration_disabled");
    expect(fake.verifyHostRuntime).not.toHaveBeenCalled();
  });

  it("keeps an hourly host path failure out of connection material", async () => {
    vi.stubEnv(
      "ONE_MEMORY_HOURLY_INSTANCE_ID",
      "1d3b0c1c-59d1-44c3-9e3b-24e5ecfa1b19",
    );
    vi.stubEnv(
      "ONE_MEMORY_HOURLY_PACKAGE_ID",
      "One-Memory-HAPP1R5.20-f154da45-c7-isolated",
    );
    vi.stubEnv("ONE_MEMORY_BUILD_NUMBER", "3758");
    await startup();
    expect(fake.verifyHostRuntime).toHaveBeenCalledOnce();
    expect(fake.resolveRuntimeLaunch).toHaveBeenCalledWith(
      expect.objectContaining({
        hostRuntimeSelection: {
          state: "blocked",
          code: "host_runtime_current_unavailable",
        },
      }),
    );
    await expect(invoke("prepare-connection-material")).rejects.toThrow(
      "host_runtime_current_unavailable",
    );
  });

  it("prepares only through standalone Runtime and hands the renderer a path, never a secret", async () => {
    await startup();
    fake.host.mockClear();
    const credentialFile = path.join(
      fake.dataDir,
      "host-permissions",
      "installations",
      "a".repeat(64),
      "credential",
    );
    fake.host.mockResolvedValueOnce({ credentialFile });
    fake.localRead.mockImplementationOnce(
      (runtime: { credentialFile: string }) => ({
        mcp: JSON.stringify({
          env: { ONE_MEMORY_HOST_CREDENTIAL_FILE: runtime.credentialFile },
        }),
        shell: null,
        prompt: "synthetic path only",
      }),
    );
    expect(fake.host).not.toHaveBeenCalled();
    const result = (await invoke("prepare-connection-material")) as {
      mcp: string;
      shell: string | null;
      prompt: string;
    };
    expect(fake.host).toHaveBeenCalledExactlyOnceWith({
      hostId: "generic-local-mcp",
      operation: "prepareGenericLocalConnection",
      args: [],
    });
    expect(result).toMatchObject({ shell: null });
    expect(result.prompt).toContain("MemHub_recall");
    expect(result.prompt).toContain("MemHub_context");
    expect(result.prompt).not.toContain("MemHub_conversations");
    expect(result.prompt).not.toContain("MemHub_ask");
    expect(JSON.stringify(result)).toContain(credentialFile);
    expect(JSON.stringify(result)).not.toContain("omhg_v1_");
    expect(fake.open).not.toHaveBeenCalled();
  });

  it("rejects malformed Runtime material instead of rendering an untrusted credential path", async () => {
    await startup();
    fake.host.mockClear();
    fake.host.mockResolvedValueOnce({ credentialFile: "/outside/credential" });
    await expect(invoke("prepare-connection-material")).rejects.toThrow(
      "connection_client_invalid_response",
    );
    expect(fake.localRead).not.toHaveBeenCalled();
    expect(fake.open).not.toHaveBeenCalled();
  });

  it("uses the embedded permission owner and never dispatches a standalone control call", async () => {
    vi.stubEnv("ONE_MEMORY_DESKTOP_RUNTIME_MODE", "embedded");
    fake.missingBroker = true;
    const credentialFile = path.join(
      fake.dataDir,
      "host-permissions",
      "installations",
      "a".repeat(64),
      "credential",
    );
    const prepare = vi.fn(async () => ({ credentialFile }));
    fake.open.mockResolvedValue({
      hostGrantStore: {},
      repairInstalledCommand: vi.fn(async () => []),
      prepareGenericLocalConnection: prepare,
    });
    await startup();
    fake.localRead.mockImplementationOnce(
      (runtime: { credentialFile: string }) => ({
        mcp: JSON.stringify({
          env: { ONE_MEMORY_HOST_CREDENTIAL_FILE: runtime.credentialFile },
        }),
        shell: null,
        prompt: "synthetic path only",
      }),
    );
    expect(await invoke("prepare-connection-material")).toMatchObject({
      shell: null,
      prompt: expect.stringContaining("MemHub_context"),
    });
    expect(prepare).toHaveBeenCalledExactlyOnceWith();
    expect(fake.host).not.toHaveBeenCalled();
  });

  it("does not open local HostPermissions for a blocked embedded hourly path", async () => {
    vi.stubEnv(
      "ONE_MEMORY_HOURLY_INSTANCE_ID",
      "1d3b0c1c-59d1-44c3-9e3b-24e5ecfa1b19",
    );
    vi.stubEnv(
      "ONE_MEMORY_HOURLY_PACKAGE_ID",
      "One-Memory-HAPP1R5.20-f154da45-c7-isolated",
    );
    vi.stubEnv("ONE_MEMORY_BUILD_NUMBER", "3758");
    vi.stubEnv("ONE_MEMORY_DESKTOP_RUNTIME_MODE", "embedded");
    await startup();
    expect(fake.verifyHostRuntime).toHaveBeenCalledOnce();
    expect(fake.open).not.toHaveBeenCalled();
    await expect(invoke("preview-recall-install", "codex")).rejects.toThrow(
      "host_runtime_current_unavailable",
    );
    expect(fake.host).not.toHaveBeenCalled();
  });

  it("keeps a trial build out of stable install and host repair", async () => {
    vi.stubGlobal("__ONE_MEMORY_TRIAL_PROFILE__", "diag27");
    fake.host.mockRejectedValue(Error("connection_client_unavailable"));
    await startup();
    expect(fake.error).not.toHaveBeenCalled();
    expect(fake.host).not.toHaveBeenCalled();
    const written = await readFile(
      path.join(
        fake.home,
        ".one-memory-trial-diag27",
        "logs",
        "diagnostic-desktop.jsonl",
      ),
      "utf8",
    );
    expect(written).toContain('"event":"desktop.ready"');
  });

  it("uninstalls a host through Runtime like install does", async () => {
    await startup();
    fake.host.mockClear();
    await expect(invoke("preview-recall-uninstall", "codex")).resolves.toEqual({
      state: "synthetic-remote-result",
    });
    expect(fake.host).toHaveBeenCalledExactlyOnceWith({
      hostId: "codex",
      operation: "previewUninstall",
      args: [],
    });
    fake.host.mockClear();
    await expect(
      invoke("uninstall-recall", "codex", "d".repeat(64)),
    ).resolves.toEqual({ state: "synthetic-remote-result" });
    expect(fake.host).toHaveBeenCalledExactlyOnceWith({
      hostId: "codex",
      operation: "uninstall",
      args: ["d".repeat(64)],
    });
  });
  it("attaches without opening local permissions and forwards host IPC to Runtime", async () => {
    await startup();
    expect(fake.error).not.toHaveBeenCalled();
    expect(fake.windows).toHaveLength(1);
    expect(fake.ensureRuntime).toHaveBeenCalledExactlyOnceWith(
      expect.objectContaining({
        dataDir: fake.dataDir,
        launch: { command: "synthetic-runtime", args: [] },
      }),
    );
    expect(fake.coreStart).toHaveBeenCalledTimes(1);
    expect(fake.probeBroker).toHaveBeenCalledTimes(1);
    expect(fake.open).not.toHaveBeenCalled();
    expect(fake.brokerConstructed).not.toHaveBeenCalled();
    const grant = {
      installationId: "synthetic",
      expectedRevision: 2,
      allowedTools: ["om_recall"],
      allowedScopes: ["source:codex"],
      userConfirmed: true,
    };
    const calls: Array<[string, string, unknown[]]> = [
      ["get-recall-permission", "getPermission", []],
      ["set-recall-permission", "setPermission", [grant]],
      [
        "revoke-recall-permission",
        "revokePermission",
        [
          {
            installationId: "synthetic",
            expectedRevision: 3,
            userConfirmed: true,
          },
        ],
      ],
      ["get-sync-hook-status", "getSyncHookStatus", []],
      ["preview-sync-hook", "previewSyncHook", ["install"]],
      [
        "apply-sync-hook",
        "applySyncHook",
        [{ operation: "install", fingerprint: "a".repeat(64) }],
      ],
      ["preview-skill-sync", "previewSkillSync", []],
      ["apply-skill-sync", "applySkillSync", ["b".repeat(64), true]],
      ["preview-recall-install", "previewInstall", []],
      ["install-recall", "install", ["c".repeat(64)]],
      ["preview-recall-uninstall", "previewUninstall", []],
      ["uninstall-recall", "uninstall", ["c".repeat(64)]],
    ];
    for (const [channel, operation, args] of calls) {
      fake.host.mockClear();
      expect(await invoke(channel, "codex", ...args)).toEqual({
        state: "synthetic-remote-result",
      });
      expect(fake.host).toHaveBeenCalledExactlyOnceWith({
        hostId: "codex",
        operation,
        args,
      });
      expect(JSON.stringify(fake.host.mock.calls)).not.toContain(fake.home);
    }
    fake.host.mockClear();
    await invoke("repair-recall-commands");
    expect(fake.host).toHaveBeenCalledExactlyOnceWith({
      hostId: "codex",
      operation: "repairInstalledCommand",
      args: [],
    });
    expect(fake.open).not.toHaveBeenCalled();
    expect(fake.localRead).not.toHaveBeenCalled();
    expect(await readdir(fake.dataDir)).toEqual(["logs"]);
  });

  it("uses remote inspection and probe without local host-file reads", async () => {
    await startup();
    fake.host.mockResolvedValueOnce({
      hostId: "codex",
      installed: true,
      present: true,
    });
    expect(await invoke("recall-hosts")).toEqual([
      {
        hostId: "codex",
        installed: true,
        present: true,
        handoff: expect.any(String),
      },
    ]);
    expect(fake.host).toHaveBeenLastCalledWith({
      hostId: "codex",
      operation: "inspect",
      args: [],
    });
    await invoke("probe-recall-host", "codex");
    expect(fake.host).toHaveBeenLastCalledWith({
      hostId: "codex",
      operation: "probe",
      args: [],
    });
    expect(fake.localRead).not.toHaveBeenCalled();
    expect(fake.open).not.toHaveBeenCalled();
  });

  it("never creates an embedded broker or permission store when standalone discovery is missing", async () => {
    fake.missingBroker = true;
    await startup();
    expect(fake.ensureRuntime).toHaveBeenCalledTimes(1);
    expect(fake.coreStart).toHaveBeenCalledTimes(1);
    expect(fake.error).toHaveBeenCalledWith(
      expect.any(String),
      "runtime_broker_unavailable",
    );
    expect(fake.quit).toHaveBeenCalledTimes(1);
    // A window exists, but it is the startup placeholder raised before the
    // services were tried. The app window itself was never opened.
    expect(fake.windows.map((window) => window.frame.url)).toEqual([
      "one-memory://app/__startup",
    ]);
    expect(fake.open).not.toHaveBeenCalled();
    expect(fake.brokerConstructed).not.toHaveBeenCalled();
    expect(fake.brokerStart).not.toHaveBeenCalled();
    expect(fake.host).not.toHaveBeenCalled();
    expect(await readdir(fake.dataDir)).toEqual(["logs"]);
  });

  it("propagates remote errors without replaying or falling back to a local store", async () => {
    await startup();
    fake.host.mockClear();
    fake.host.mockRejectedValueOnce(
      Error("connection_client_operation_unknown"),
    );
    await expect(
      invoke("install-recall", "codex", "a".repeat(64)),
    ).rejects.toThrow("connection_client_operation_unknown");
    await invoke("get-recall-permission", "codex");
    expect(fake.host.mock.calls.map(([request]) => request.operation)).toEqual([
      "install",
      "getPermission",
    ]);
    expect(fake.open).not.toHaveBeenCalled();
    expect(fake.brokerConstructed).not.toHaveBeenCalled();
  });

  it("does not fall back to local inspection if an older remote lacks host capability", async () => {
    fake.hostCapability = false;
    await startup();
    await expect(invoke("recall-hosts")).rejects.toThrow(
      "host_permission_store_unavailable",
    );
    await expect(invoke("probe-recall-host", "codex")).rejects.toThrow(
      "host_permission_store_unavailable",
    );
    await expect(invoke("get-recall-permission", "codex")).rejects.toThrow(
      "connection_client_capability_unavailable",
    );
    expect(fake.open).not.toHaveBeenCalled();
    expect(fake.localRead).not.toHaveBeenCalled();
    expect(fake.host).not.toHaveBeenCalled();
  });

  it("keeps explicit embedded ownership as a positive control", async () => {
    vi.stubEnv("ONE_MEMORY_DESKTOP_RUNTIME_MODE", "embedded");
    fake.missingBroker = true;
    await startup();
    expect(fake.error).not.toHaveBeenCalled();
    expect(fake.open).toHaveBeenCalledTimes(1);
    expect(fake.open.mock.calls[0]?.[0]).toBe(fake.dataDir);
    expect(fake.brokerConstructed).toHaveBeenCalledTimes(1);
    expect(fake.brokerStart).toHaveBeenCalledTimes(1);
    expect(fake.ensureRuntime).not.toHaveBeenCalled();
    expect(fake.createClient).not.toHaveBeenCalled();
    expect(fake.windows).toHaveLength(1);
  });
});
