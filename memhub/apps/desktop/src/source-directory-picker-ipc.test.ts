import { EventEmitter } from "node:events";
import type { OpenDialogOptions, OpenDialogReturnValue } from "electron";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const fake = vi.hoisted(() => ({
  ready: undefined as (() => Promise<void>) | undefined,
  handlers: new Map<string, (...args: unknown[]) => unknown>(),
  windows: [] as FakeWindow[],
  app: undefined as unknown as EventEmitter,
  theme: undefined as unknown as EventEmitter,
  renderer: undefined as unknown as EventEmitter,
  packaged: true,
  expose: vi.fn(),
  invoke: vi.fn(),
  ensureRuntime: vi.fn(async () => "started"),
  showDialog:
    vi.fn<
      (
        window: FakeWindow,
        options: OpenDialogOptions,
      ) => Promise<OpenDialogReturnValue>
    >(),
  error: vi.fn(),
}));
const resources = Object.getOwnPropertyDescriptor(process, "resourcesPath");

class FakeWindow extends EventEmitter {
  destroyed = false;
  frame = {
    url: "one-memory://app/index.html",
    parent: null as object | null,
    detached: false,
    isDestroyed: vi.fn(() => false),
    send: vi.fn(),
  };
  webContents = Object.assign(new EventEmitter(), {
    mainFrame: this.frame,
    isDestroyed: vi.fn(() => false),
    setWindowOpenHandler: vi.fn(),
  });
  constructor() {
    super();
    fake.windows.push(this);
  }
  static getAllWindows() {
    return fake.windows;
  }
  isDestroyed() {
    return this.destroyed;
  }
  isMinimized() {
    return false;
  }
  restore() {}
  focus() {}
  show() {}
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
        // biome-ignore lint/suspicious/noThenProperty: Capture app setup without an Electron process.
        then(callback: () => Promise<void>) {
          fake.ready = callback;
          // Electron hands back a chainable promise; the callback is run by
          // the test, so this only has to be one.
          return Promise.resolve();
        },
      }),
      isPackaged: fake.packaged,
      getName: () => "One Memory Preview",
      getPath: () => "/unused/picker-test",
      getAppPath: () => "/unused/picker-test",
      quit: vi.fn(),
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
    handle: (channel: string, handler: (...args: unknown[]) => unknown) =>
      fake.handlers.set(channel, handler),
    removeHandler: (channel: string) => fake.handlers.delete(channel),
  },
  get ipcRenderer() {
    return Object.assign(fake.renderer, { invoke: fake.invoke });
  },
  contextBridge: { exposeInMainWorld: fake.expose },
  protocol: { registerSchemesAsPrivileged: vi.fn() },
  screen: {
    getDisplayNearestPoint: () => ({ workArea: { width: 1500, height: 1000 } }),
    getCursorScreenPoint: () => ({ x: 0, y: 0 }),
  },
  session: { defaultSession: {} },
  clipboard: { writeText: vi.fn() },
  dialog: { showErrorBox: fake.error, showOpenDialog: fake.showDialog },
  Menu: { buildFromTemplate: vi.fn(() => ({})), setApplicationMenu: vi.fn() },
  shell: { openExternal: vi.fn() },
}));
vi.mock("@one-memory/host-bridge", () => ({
  RecallBroker: class {
    async start() {}
    async stop() {}
  },
}));
vi.mock("@one-memory/host-bridge/installer", () => ({}));
vi.mock("@one-memory/runtime-control", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@one-memory/runtime-control")>()),
  DiagnosticLogger: class {
    sessionId = "synthetic-picker-test-session";
    async log() {}
  },
  RecallBrokerSupervisor: class {
    async start() {}
    async stop() {}
  },
}));
vi.mock("./host-permissions", () => ({
  HostPermissions: { open: async () => ({ hostGrantStore: {} }) },
}));
vi.mock("./recall-hosts", () => ({
  recallHosts: async () => [],
  isHostPresent: vi.fn(),
}));
vi.mock("./permissions", () => ({ installDenyAllPermissionPolicy: vi.fn() }));
vi.mock("./app-protocol", async (importOriginal) => ({
  ...(await importOriginal()),
  installAppProtocol: vi.fn(),
}));
vi.mock("./core-supervisor", () => ({
  CoreSupervisor: class {
    port = 9999;
    apiToken = "synthetic-picker-test-token";
    instanceId = "synthetic-picker-test-instance";
    async start() {}
    async stop() {}
  },
  resolveCoreDataDir: () => "/unused/picker-test/data",
  resolveCoreLaunch: () => ({}),
  resolvePackagedRuntimeLaunch: () => ({ command: "runtime", args: [] }),
  resolveRunningPackagedHostPaths: async () => undefined,
  resolveVerifiedHostRuntime: async () => undefined,
  stableRecallPaths: () => null,
  ensureRuntimeProcess: fake.ensureRuntime,
  findAvailablePort: async () => 9999,
}));

beforeEach(() => {
  vi.resetModules();
  vi.resetAllMocks();
  fake.app = new EventEmitter();
  fake.theme = new EventEmitter();
  fake.renderer = new EventEmitter();
  fake.windows.length = 0;
  fake.handlers.clear();
  fake.ready = undefined;
  fake.packaged = true;
  fake.showDialog.mockResolvedValue({
    canceled: false,
    filePaths: ["/unused/selected history"],
  });
  Object.defineProperty(process, "resourcesPath", {
    configurable: true,
    value: "/unused/resources",
  });
  vi.stubGlobal("window", new EventTarget());
  vi.stubEnv("ONE_MEMORY_WEB_URL", "http://127.0.0.1:5173");
  vi.stubEnv("ONE_MEMORY_SMOKE_QUIT_MS", "0");
});
afterEach(() => {
  fake.app.emit("will-quit");
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  if (resources) Object.defineProperty(process, "resourcesPath", resources);
  else Reflect.deleteProperty(process, "resourcesPath");
});

async function startup() {
  await import("./main.js");
  if (!fake.ready) throw new Error("No startup callback");
  await fake.ready();
  expect(fake.error).not.toHaveBeenCalled();
  expect(fake.windows.at(-1)?.frame.url).toBe(
    fake.packaged ? "one-memory://app/index.html" : "http://127.0.0.1:5173",
  );
  if (!fake.windows[0]) throw new Error("Missing startup window");
  return fake.windows[0];
}
const eventFor = (window: FakeWindow) => ({
  sender: window.webContents,
  senderFrame: window.frame,
});
async function pick(
  event: unknown,
  source: unknown = "cursor",
  ...extra: unknown[]
) {
  const handler = fake.handlers.get("one-memory:pick-source-directory");
  if (!handler) throw new Error("Missing source picker IPC handler");
  return handler(event, source, ...extra);
}

describe("source directory picker main IPC", () => {
  it("starts the packaged shared Runtime before attaching the desktop", async () => {
    await startup();

    expect(fake.ensureRuntime).toHaveBeenCalledOnce();
    expect(fake.ensureRuntime).toHaveBeenCalledWith({
      dataDir: "/unused/picker-test/data",
      launch: { command: "runtime", args: [] },
      attempts: 360,
    });
  });

  it("attaches to the owned parent, ignores arbitrary options and keeps display IPC", async () => {
    const window = await startup();
    expect(
      await pick(eventFor(window), "cursor", {
        properties: ["openFile", "multiSelections"],
      }),
    ).toEqual({ cancelled: false, path: "/unused/selected history" });
    expect(fake.showDialog).toHaveBeenCalledWith(
      window,
      expect.objectContaining({ properties: ["openDirectory"] }),
    );
    expect(fake.handlers.has("one-memory:get-display-preferences")).toBe(true);
    expect(fake.theme.listenerCount("updated")).toBe(1);
  });

  it.each([
    "https://example.invalid/",
    "http://127.0.0.1:5173/",
    "one-memory://other/index.html",
    "one-memory://app.evil/index.html",
    "one-memory://user@app/index.html",
    "file:///tmp/index.html",
    "not a url",
  ])("rejects untrusted packaged sender URL %s", async (url) => {
    const window = await startup();
    window.frame.url = url;
    await expect(pick(eventFor(window))).rejects.toThrow();
    expect(fake.showDialog).not.toHaveBeenCalled();
  });

  it("rejects a same-origin iframe, missing frame and fake owned parent", async () => {
    const window = await startup();
    await expect(
      pick({
        sender: window.webContents,
        senderFrame: { ...window.frame, parent: window.frame },
      }),
    ).rejects.toThrow();
    await expect(
      pick({ sender: window.webContents, senderFrame: null }),
    ).rejects.toThrow();
    await expect(pick(eventFor(new FakeWindow()))).rejects.toThrow();
    expect(fake.showDialog).not.toHaveBeenCalled();
  });

  it.each(["closed", "contents", "detached", "frame", "parent"])(
    "rejects unavailable %s",
    async (kind) => {
      const window = await startup();
      if (kind === "closed") window.destroyed = true;
      else if (kind === "contents")
        window.webContents.isDestroyed.mockReturnValue(true);
      else if (kind === "detached") window.frame.detached = true;
      else if (kind === "frame") window.frame.isDestroyed.mockReturnValue(true);
      else window.frame.parent = {};
      await expect(pick(eventFor(window))).rejects.toThrow();
      expect(fake.showDialog).not.toHaveBeenCalled();
    },
  );

  it("uses only the configured development origin", async () => {
    fake.packaged = false;
    const window = await startup();
    expect(await pick(eventFor(window))).toMatchObject({ cancelled: false });
    fake.showDialog.mockClear();
    window.frame.url = "one-memory://app/index.html";
    await expect(pick(eventFor(window))).rejects.toThrow();
    window.frame.url = "http://localhost:5173/";
    await expect(pick(eventFor(window))).rejects.toThrow();
    expect(fake.showDialog).not.toHaveBeenCalled();
  });

  it("rejects unknown host before opening and maps cancel to a null path", async () => {
    const window = await startup();
    await expect(pick(eventFor(window), "openclaw")).rejects.toThrow(
      "source_directory_picker_source_unsupported",
    );
    expect(fake.showDialog).not.toHaveBeenCalled();
    fake.showDialog.mockResolvedValue({ canceled: true, filePaths: [] });
    expect(await pick(eventFor(window), "workbuddy")).toEqual({
      cancelled: true,
      path: null,
    });
  });

  it("rejects a pending result after navigation and removes both IPC handlers on quit", async () => {
    const window = await startup();
    let complete!: (value: OpenDialogReturnValue) => void;
    fake.showDialog.mockReturnValue(
      new Promise((resolve) => {
        complete = resolve;
      }),
    );
    const pending = pick(eventFor(window));
    const rejected = expect(pending).rejects.toThrow(
      "source_directory_picker_context_changed",
    );
    window.webContents.emit("did-start-navigation", { isMainFrame: true });
    await rejected;
    complete({ canceled: false, filePaths: ["/unused/late"] });
    fake.app.emit("will-quit");
    expect(fake.handlers.has("one-memory:pick-source-directory")).toBe(false);
    expect(fake.handlers.has("one-memory:get-display-preferences")).toBe(false);
  });
});

describe("source directory picker preload", () => {
  it("passes only the source ID and never exposes arbitrary dialog or IPC objects", async () => {
    await import("./preload.js");
    const api = fake.expose.mock.calls[0]?.[1] as {
      pickSourceDirectory(
        source: string,
        ...extra: unknown[]
      ): Promise<unknown>;
    };
    fake.invoke.mockResolvedValue({
      cancelled: false,
      path: "/unused/history",
    });
    expect(
      await api.pickSourceDirectory("codex", { properties: ["openFile"] }),
    ).toEqual({ cancelled: false, path: "/unused/history" });
    expect(fake.invoke).toHaveBeenCalledExactlyOnceWith(
      "one-memory:pick-source-directory",
      "codex",
    );
    expect(Object.keys(api)).not.toContain("showOpenDialog");
    expect(Object.keys(api)).not.toContain("ipcRenderer");
  });
});
