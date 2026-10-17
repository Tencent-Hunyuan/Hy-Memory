import { EventEmitter } from "node:events";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

interface FakeFrame {
  url: string;
  parent: object | null;
  detached: boolean;
  isDestroyed: ReturnType<typeof vi.fn>;
  send: ReturnType<typeof vi.fn>;
}

const fake = vi.hoisted(() => ({
  ready: undefined as (() => Promise<void>) | undefined,
  handlers: new Map<string, (event: unknown) => unknown>(),
  windows: [] as FakeWindow[],
  app: undefined as unknown as EventEmitter,
  theme: undefined as unknown as EventEmitter,
  renderer: undefined as unknown as EventEmitter,
  unload: undefined as unknown as EventTarget,
  packaged: true,
  loadFailure: false,
  reduceMotion: false as unknown,
  reduceTransparency: false as unknown,
  motionRead: vi.fn(),
  transparencyRead: vi.fn(),
  expose: vi.fn(),
  invoke: vi.fn(),
  removeHandler: vi.fn(),
  backgroundInspect: vi.fn(),
  error: vi.fn(),
}));
const resourcesDescriptor = Object.getOwnPropertyDescriptor(
  process,
  "resourcesPath",
);

class FakeWindow extends EventEmitter {
  destroyed = false;
  frame: FakeFrame = {
    url: "one-memory://app/index.html",
    parent: null,
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
  show() {}
  destroy() {
    this.destroyed = true;
    this.emit("closed");
  }
  async loadURL(url: string) {
    if (fake.loadFailure) throw new Error("page_load_failed");
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
        // biome-ignore lint/suspicious/noThenProperty: Capture startup without running Electron.
        then(callback: () => Promise<void>) {
          fake.ready = callback;
          // Electron hands back a chainable promise; the callback is run by
          // the test, so this only has to be one.
          return Promise.resolve();
        },
      }),
      isPackaged: fake.packaged,
      getName: () => "One Memory Preview",
      getPath: () => "/unused/display-test",
      getAppPath: () => "/unused/display-test",
      quit: vi.fn(),
      exit: vi.fn(),
    });
  },
  get nativeTheme() {
    return Object.defineProperty(fake.theme, "prefersReducedTransparency", {
      configurable: true,
      get: () => {
        fake.transparencyRead();
        return fake.reduceTransparency;
      },
    });
  },
  systemPreferences: {
    getAnimationSettings: () => {
      fake.motionRead();
      return {
        prefersReducedMotion: fake.reduceMotion,
        shouldRenderRichAnimation: true,
      };
    },
  },
  BrowserWindow: FakeWindow,
  ipcMain: {
    handle: (channel: string, handler: (event: unknown) => unknown) =>
      fake.handlers.set(channel, handler),
    removeHandler: (channel: string) => {
      fake.removeHandler(channel);
      fake.handlers.delete(channel);
    },
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
  dialog: { showErrorBox: fake.error },
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
  inspectBackgroundHealth: fake.backgroundInspect,
  DiagnosticLogger: class {
    sessionId = "synthetic-display-test-session";
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
    apiToken = "synthetic-display-test-token";
    instanceId = "synthetic-display-test-instance";
    async start() {}
    async stop() {}
  },
  resolveCoreDataDir: () => "/unused/display-test/data",
  resolveCoreLaunch: () => ({}),
  resolvePackagedRuntimeLaunch: () => ({ command: "runtime", args: [] }),
  resolveRunningPackagedHostPaths: async () => undefined,
  resolveVerifiedHostRuntime: async () => undefined,
  stableRecallPaths: () => null,
  ensureRuntimeProcess: async () => "started",
  findAvailablePort: async () => 9999,
}));

beforeEach(() => {
  vi.resetModules();
  vi.resetAllMocks();
  fake.app = new EventEmitter();
  fake.theme = new EventEmitter();
  fake.renderer = new EventEmitter();
  fake.unload = new EventTarget();
  fake.handlers.clear();
  fake.windows.length = 0;
  fake.ready = undefined;
  fake.packaged = true;
  fake.loadFailure = false;
  fake.reduceMotion = false;
  fake.reduceTransparency = false;
  Object.defineProperty(process, "resourcesPath", {
    configurable: true,
    value: "/unused/resources",
  });
  vi.stubGlobal("window", fake.unload);
  vi.stubEnv("ONE_MEMORY_WEB_URL", "http://127.0.0.1:5173");
  vi.stubEnv("ONE_MEMORY_SMOKE_QUIT_MS", "0");
});
afterEach(() => {
  fake.app.emit("will-quit");
  fake.unload.dispatchEvent(new Event("unload"));
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  if (resourcesDescriptor)
    Object.defineProperty(process, "resourcesPath", resourcesDescriptor);
  else Reflect.deleteProperty(process, "resourcesPath");
});

async function startup() {
  await import("./main.js");
  if (!fake.ready) throw new Error("Missing app ready callback");
  await fake.ready();
  if (fake.windows.length === 0 && fake.error.mock.calls.length > 0) {
    throw new Error(String(fake.error.mock.calls.at(-1)?.[1]));
  }
  return fake.windows[0];
}

function handler() {
  const get = fake.handlers.get("one-memory:get-display-preferences");
  if (!get) throw new Error("Missing display-preference IPC handler");
  return get;
}
const eventFor = (window: FakeWindow) => ({
  sender: window.webContents,
  senderFrame: window.frame,
});

describe("display preference main IPC", () => {
  it("registers background inspection against the fixed data directory and guards navigation", async () => {
    const window = await startup();
    const inspect = fake.handlers.get("one-memory:get-background-health");
    if (!inspect) throw Error("Missing actual background health handler");
    fake.backgroundInspect.mockResolvedValue({
      state: "degraded",
      code: "background_broker_not_healthy",
      core: "running",
      broker: "unhealthy",
    });
    expect(await inspect(eventFor(window))).toMatchObject({
      state: "degraded",
    });
    expect(fake.backgroundInspect).toHaveBeenCalledExactlyOnceWith({
      dataDir: "/unused/display-test/data",
    });
    window.frame.url = "https://example.invalid";
    await expect(inspect(eventFor(window))).rejects.toThrow();
    expect(fake.backgroundInspect).toHaveBeenCalledTimes(1);
    fake.app.emit("will-quit");
    expect(fake.handlers.has("one-memory:get-background-health")).toBe(false);
  });
  it("recreates the window after a failed page load without restarting the runtime", async () => {
    fake.loadFailure = true;
    const failed = await startup();
    expect(failed.destroyed).toBe(true);
    fake.loadFailure = false;
    fake.app.emit("activate");
    // The startup placeholder fails the same page load first, so count the
    // windows that survived rather than the ones that were ever created.
    await vi.waitFor(() =>
      expect(fake.windows.filter((window) => !window.destroyed)).toHaveLength(
        1,
      ),
    );
    expect(fake.windows.at(-1)?.frame.url).toBe("one-memory://app/index.html");
    expect(fake.app.listenerCount("before-quit")).toBe(1);
  });
  it("reads exact native dimensions at revision zero and sends only changes", async () => {
    fake.reduceMotion = true;
    const window = await startup();
    expect(await handler()(eventFor(window))).toEqual({
      reduceMotion: true,
      reduceTransparency: false,
      revision: 0,
    });
    window.frame.send.mockClear();
    fake.theme.emit("updated");
    fake.app.emit("browser-window-focus", {}, window);
    expect(window.frame.send).not.toHaveBeenCalled();
    fake.reduceTransparency = true;
    fake.theme.emit("updated");
    expect(window.frame.send).toHaveBeenCalledExactlyOnceWith(
      "one-memory:display-preferences-changed",
      { reduceMotion: true, reduceTransparency: true, revision: 1 },
    );
    expect(await handler()(eventFor(window))).toEqual({
      reduceMotion: true,
      reduceTransparency: true,
      revision: 1,
    });
    fake.reduceMotion = false;
    fake.app.emit("browser-window-focus", {}, window);
    expect(window.frame.send).toHaveBeenLastCalledWith(
      "one-memory:display-preferences-changed",
      { reduceMotion: false, reduceTransparency: true, revision: 2 },
    );
  });

  it.each([
    "https://example.invalid/",
    "http://127.0.0.1:5173/",
    "one-memory://attacker/index.html",
    "one-memory://app.evil/index.html",
    "file:///tmp/index.html",
    "not a url",
    "one-memory://user@app/index.html",
  ])(
    "rejects untrusted packaged URL %s before reading preferences",
    async (url) => {
      const window = await startup();
      const get = handler();
      window.frame.url = url;
      fake.motionRead.mockClear();
      fake.transparencyRead.mockClear();
      expect(() => get(eventFor(window))).toThrow();
      expect(fake.motionRead).not.toHaveBeenCalled();
      expect(fake.transparencyRead).not.toHaveBeenCalled();
    },
  );

  it("rejects same-origin iframe, missing frame, foreign window and dead senders", async () => {
    const window = await startup();
    const get = handler();
    const child = { ...window.frame, parent: window.frame };
    expect(() =>
      get({ sender: window.webContents, senderFrame: child }),
    ).toThrow();
    expect(() =>
      get({ sender: window.webContents, senderFrame: null }),
    ).toThrow();
    const foreign = new FakeWindow();
    expect(() => get(eventFor(foreign))).toThrow();
    window.frame.detached = true;
    expect(() => get(eventFor(window))).toThrow();
    window.frame.detached = false;
    window.frame.isDestroyed.mockReturnValue(true);
    expect(() => get(eventFor(window))).toThrow();
    window.frame.isDestroyed.mockReturnValue(false);
    window.webContents.isDestroyed.mockReturnValue(true);
    expect(() => get(eventFor(window))).toThrow();
  });

  it("limits development reads to its configured trusted top-level window", async () => {
    fake.packaged = false;
    const window = await startup();
    expect(window.frame.url).toBe("http://127.0.0.1:5173");
    expect(await handler()(eventFor(window))).toMatchObject({ revision: 0 });
    window.frame.url = "http://localhost:5173/";
    expect(() => handler()(eventFor(window))).toThrow();
    window.frame.url = "one-memory://app/index.html";
    expect(() => handler()(eventFor(window))).toThrow();
  });

  it("does not broadcast to unowned, navigated or destroyed windows", async () => {
    const window = await startup();
    const foreign = new FakeWindow();
    fake.reduceMotion = true;
    fake.theme.emit("updated");
    expect(window.frame.send).toHaveBeenCalledTimes(1);
    expect(foreign.frame.send).not.toHaveBeenCalled();
    window.frame.send.mockClear();
    window.frame.url = "https://example.invalid";
    fake.reduceTransparency = true;
    fake.theme.emit("updated");
    expect(window.frame.send).not.toHaveBeenCalled();
    window.frame.url = "one-memory://app/index.html";
    window.destroyed = true;
    window.emit("closed");
    fake.reduceMotion = false;
    fake.theme.emit("updated");
    expect(window.frame.send).not.toHaveBeenCalled();
    expect(() => handler()(eventFor(window))).toThrow();
  });

  it("tolerates send races and unregisters theme, focus and IPC on exit", async () => {
    const window = await startup();
    expect(fake.theme.listenerCount("updated")).toBe(1);
    expect(fake.app.listenerCount("browser-window-focus")).toBe(1);
    window.frame.send.mockImplementation(() => {
      throw new Error("Window destroyed during send");
    });
    fake.reduceMotion = true;
    expect(() => fake.theme.emit("updated")).not.toThrow();
    fake.app.emit("before-quit", { preventDefault: vi.fn() });
    fake.app.emit("will-quit");
    expect(fake.theme.listenerCount("updated")).toBe(0);
    expect(fake.app.listenerCount("browser-window-focus")).toBe(0);
    expect(fake.handlers.has("one-memory:get-display-preferences")).toBe(false);
    fake.motionRead.mockClear();
    fake.theme.emit("updated");
    fake.app.emit("browser-window-focus", {}, window);
    expect(fake.motionRead).not.toHaveBeenCalled();
  });
});

interface PreferenceAPI {
  getBackgroundHealth(): Promise<unknown>;
  restartBackground(): Promise<{ state: "restarted" }>;
  getDisplayPreferences(): Promise<{
    reduceMotion: boolean;
    reduceTransparency: boolean;
    revision: number;
  }>;
  onDisplayPreferencesChanged(listener: (value: unknown) => void): () => void;
}
async function preload() {
  await import("./preload.js");
  return fake.expose.mock.calls[0][1] as PreferenceAPI;
}

describe("display preference preload", () => {
  it("preload exposes a no-argument background health read", async () => {
    const api = await preload();
    const status = {
      state: "unknown",
      code: "background_inspection_unavailable",
      core: "unknown",
      broker: "not_checked",
    };
    fake.invoke.mockResolvedValue(status);
    expect(await api.getBackgroundHealth()).toEqual(status);
    expect(fake.invoke).toHaveBeenCalledExactlyOnceWith(
      "one-memory:get-background-health",
    );
  });
  it("preload exposes the explicit background restart action", async () => {
    const api = await preload();
    fake.invoke.mockResolvedValue({ state: "restarted" });
    await expect(api.restartBackground()).resolves.toEqual({
      state: "restarted",
    });
    expect(fake.invoke).toHaveBeenCalledExactlyOnceWith(
      "one-memory:restart-background",
    );
  });
  it("exposes only snapshot payloads and an idempotent unsubscribe", async () => {
    const api = await preload();
    const snapshot = {
      reduceMotion: true,
      reduceTransparency: false,
      revision: 2,
    };
    fake.invoke.mockResolvedValue(snapshot);
    expect(await api.getDisplayPreferences()).toEqual(snapshot);
    expect(fake.invoke).toHaveBeenCalledExactlyOnceWith(
      "one-memory:get-display-preferences",
    );
    const listener = vi.fn();
    const unsubscribe = api.onDisplayPreferencesChanged(listener);
    fake.renderer.emit(
      "one-memory:display-preferences-changed",
      { sender: "do not expose", secret: "event" },
      snapshot,
    );
    expect(listener).toHaveBeenCalledExactlyOnceWith(snapshot);
    expect(listener.mock.calls[0][0]).not.toBe(snapshot);
    unsubscribe();
    unsubscribe();
    expect(
      fake.renderer.listenerCount("one-memory:display-preferences-changed"),
    ).toBe(0);
    fake.renderer.emit("one-memory:display-preferences-changed", {}, snapshot);
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("rejects invalid reads, ignores invalid events and strips extra fields", async () => {
    const api = await preload();
    const listener = vi.fn();
    const unsubscribe = api.onDisplayPreferencesChanged(listener);
    for (const payload of [
      null,
      {},
      { reduceMotion: 1, reduceTransparency: false, revision: 0 },
      { reduceMotion: true, reduceTransparency: false, revision: -1 },
      { reduceMotion: true, reduceTransparency: false, revision: 1.5 },
    ]) {
      fake.invoke.mockResolvedValue(payload);
      await expect(api.getDisplayPreferences()).rejects.toThrow();
      fake.renderer.emit("one-memory:display-preferences-changed", {}, payload);
    }
    expect(listener).not.toHaveBeenCalled();
    const value = {
      reduceMotion: false,
      reduceTransparency: true,
      revision: 0,
      privateField: "not exposed",
    };
    fake.invoke.mockResolvedValue(value);
    expect(await api.getDisplayPreferences()).toEqual({
      reduceMotion: false,
      reduceTransparency: true,
      revision: 0,
    });
    unsubscribe();
  });

  it("removes subscribed listeners on renderer unload without removing others", async () => {
    const api = await preload();
    const external = vi.fn();
    fake.renderer.on("one-memory:display-preferences-changed", external);
    const first = vi.fn(),
      second = vi.fn();
    api.onDisplayPreferencesChanged(first);
    const unsubscribe = api.onDisplayPreferencesChanged(second);
    expect(
      fake.renderer.listenerCount("one-memory:display-preferences-changed"),
    ).toBe(3);
    fake.unload.dispatchEvent(new Event("unload"));
    unsubscribe();
    expect(
      fake.renderer.listenerCount("one-memory:display-preferences-changed"),
    ).toBe(1);
    fake.renderer.emit(
      "one-memory:display-preferences-changed",
      {},
      { reduceMotion: false, reduceTransparency: false, revision: 0 },
    );
    expect(first).not.toHaveBeenCalled();
    expect(second).not.toHaveBeenCalled();
    expect(external).toHaveBeenCalledTimes(1);
  });
});
