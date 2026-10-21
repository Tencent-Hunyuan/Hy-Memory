import { EventEmitter } from "node:events";
import { describe, expect, it, vi } from "vitest";
import { startDesktopLifecycle } from "./desktop-lifecycle";

function deferred() {
  let resolve!: () => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<void>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

interface FakeWindow {
  isDestroyed: () => boolean;
  isMinimized: () => boolean;
  restore: ReturnType<typeof vi.fn<() => void>>;
  show: ReturnType<typeof vi.fn<() => void>>;
  focus: ReturnType<typeof vi.fn<() => void>>;
}

function liveWindow(): FakeWindow {
  return {
    isDestroyed: () => false,
    isMinimized: () => true,
    restore: vi.fn(),
    show: vi.fn(),
    focus: vi.fn(),
  };
}

/** A window Electron has torn down: every accessor throws, like the real one. */
function destroyedWindow(): FakeWindow {
  const gone = () => {
    throw new Error("Object has been destroyed");
  };
  return {
    isDestroyed: () => true,
    isMinimized: gone,
    restore: vi.fn(gone),
    show: vi.fn(gone),
    focus: vi.fn(gone),
  };
}

interface Overrides {
  platform?: NodeJS.Platform;
  prepareError?: Error;
  whenReady?: Promise<void>;
  windowGate?: Promise<void>;
  windowError?: Error;
  beforeNativeUi?: () => Promise<void>;
  /** Give the lifecycle a startup placeholder, as the packaged App does. */
  startupWindow?: boolean;
  startupWindowError?: Error;
  revealError?: Error;
}

function fixture(background = false, lock = true, overrides: Overrides = {}) {
  const ready = deferred();
  const starting = deferred();
  const stopping = deferred();
  const windows: FakeWindow[] = [];
  const app = Object.assign(new EventEmitter(), {
    requestSingleInstanceLock: () => lock,
    whenReady: () => overrides.whenReady ?? ready.promise,
    quit: vi.fn(),
    exit: vi.fn(),
  });
  const initialize = vi.fn(() => starting.promise);
  const stop = vi.fn(() => stopping.promise);
  const dispose = vi.fn();
  const createWindow = vi.fn(async () => {
    if (overrides.windowGate) await overrides.windowGate;
    if (overrides.windowError) throw overrides.windowError;
    windows.push(liveWindow());
  });
  // The startup placeholder is a window the lifecycle raises before the
  // services exist, then hands back to be swapped onto the real app.
  const showStartupWindow = vi.fn(async () => {
    if (overrides.startupWindowError) throw overrides.startupWindowError;
    windows.push(liveWindow());
  });
  const revealStartupWindow = vi.fn(async () => {
    if (overrides.revealError) throw overrides.revealError;
  });
  const prepare = vi.fn(() => {
    if (overrides.prepareError) throw overrides.prepareError;
  });
  const installTray = vi.fn<(open: () => void) => void>();
  const onError = vi.fn();
  startDesktopLifecycle({
    app,
    platform: overrides.platform ?? "darwin",
    background,
    prepare,
    beforeNativeUi: overrides.beforeNativeUi,
    initialize,
    stop,
    dispose,
    ...(overrides.startupWindow
      ? { showStartupWindow, revealStartupWindow }
      : {}),
    createWindow,
    getWindows: () => windows,
    installTray,
    onError,
  });
  const flush = async () => {
    for (let i = 0; i < 20; i++) await Promise.resolve();
  };
  const boot = async () => {
    ready.resolve();
    starting.resolve();
    await flush();
  };
  const quit = () => {
    const event = { preventDefault: vi.fn() };
    app.emit("before-quit", event);
    return event;
  };
  // The menu bar entry the person actually clicks, not a synthetic event.
  const trayOpen = () => {
    const open = installTray.mock.calls.at(-1)?.[0];
    if (!open) throw new Error("tray was never installed");
    open();
  };
  return {
    app,
    ready,
    starting,
    stopping,
    initialize,
    stop,
    dispose,
    windows,
    createWindow,
    showStartupWindow,
    revealStartupWindow,
    startupWindow: overrides.startupWindow === true,
    prepare,
    installTray,
    onError,
    flush,
    boot,
    quit,
    trayOpen,
  };
}

describe("desktop background lifecycle with injected Electron", () => {
  it("awaits the post-readiness language hook before menus, tray and runtime", async () => {
    const gate = deferred();
    const beforeNativeUi = vi.fn(() => gate.promise);
    const f = fixture(false, true, { beforeNativeUi });
    expect(beforeNativeUi).not.toHaveBeenCalled();
    f.ready.resolve();
    await f.flush();
    expect(beforeNativeUi).toHaveBeenCalledTimes(1);
    expect(f.installTray).not.toHaveBeenCalled();
    expect(f.initialize).not.toHaveBeenCalled();
    gate.resolve();
    f.starting.resolve();
    await f.flush();
    expect(f.installTray).toHaveBeenCalledTimes(1);
    expect(f.windows).toHaveLength(1);
  });
  it("does not install native UI after quitting during the language hook", async () => {
    const gate = deferred();
    const f = fixture(false, true, { beforeNativeUi: () => gate.promise });
    f.ready.resolve();
    await f.flush();
    f.quit();
    gate.resolve();
    await f.flush();
    expect(f.installTray).not.toHaveBeenCalled();
    expect(f.initialize).not.toHaveBeenCalled();
    f.stopping.resolve();
    await f.flush();
  });
  it("starts services and tray without a window in background mode", async () => {
    const f = fixture(true);
    await f.boot();
    expect(f.initialize).toHaveBeenCalledTimes(1);
    expect(f.installTray).toHaveBeenCalledTimes(1);
    expect(f.createWindow).not.toHaveBeenCalled();
  });
  it("manual launch opens one window and close keeps services alive", async () => {
    const f = fixture();
    await f.boot();
    expect(f.windows).toHaveLength(1);
    f.windows.length = 0;
    f.app.emit("window-all-closed");
    expect(f.stop).not.toHaveBeenCalled();
    expect(f.app.quit).not.toHaveBeenCalled();
    f.app.emit("second-instance", {}, ["One Memory"]);
    await f.flush();
    expect(f.windows).toHaveLength(1);
    f.app.emit("activate");
    await f.flush();
    expect(f.windows).toHaveLength(1);
    expect(f.windows[0].restore).toHaveBeenCalled();
    expect(f.windows[0].show).toHaveBeenCalled();
    expect(f.windows[0].focus).toHaveBeenCalled();
  });
  it("coalesces early manual launches and activation until services are ready", async () => {
    const f = fixture(true);
    f.app.emit("second-instance", {}, ["One Memory"]);
    f.app.emit("activate");
    f.ready.resolve();
    await f.flush();
    expect(f.createWindow).not.toHaveBeenCalled();
    f.starting.resolve();
    await f.flush();
    expect(f.windows).toHaveLength(1);
  });
  it("ignores background second instances and startup activation for a login launch", async () => {
    const f = fixture(true);
    f.app.emit("activate");
    f.app.emit("second-instance", {}, ["One Memory", "--background"]);
    await f.boot();
    expect(f.windows).toHaveLength(0);
    f.app.emit("activate");
    await f.flush();
    expect(f.windows).toHaveLength(1);
  });
  it("a lock loser registers nothing and never starts services", async () => {
    const f = fixture(false, false);
    await f.boot();
    expect(f.app.exit).toHaveBeenCalledWith(0);
    expect(f.prepare).not.toHaveBeenCalled();
    expect(f.initialize).not.toHaveBeenCalled();
    expect(f.app.eventNames()).toEqual([]);
  });
  it("quit before ready prevents every startup side effect", async () => {
    const f = fixture();
    f.quit();
    await f.boot();
    f.stopping.resolve();
    await f.flush();
    expect(f.initialize).not.toHaveBeenCalled();
    expect(f.createWindow).not.toHaveBeenCalled();
  });
  it("quit during startup waits for settlement, stops once, and never opens late windows", async () => {
    const f = fixture();
    f.ready.resolve();
    await f.flush();
    expect(f.initialize).toHaveBeenCalledTimes(1);
    expect(f.quit().preventDefault).toHaveBeenCalled();
    f.quit();
    f.app.emit("activate");
    f.app.emit("second-instance", {}, ["One Memory"]);
    expect(f.stop).not.toHaveBeenCalled();
    f.starting.resolve();
    await f.flush();
    expect(f.stop).toHaveBeenCalledTimes(1);
    expect(f.createWindow).not.toHaveBeenCalled();
    expect(f.app.quit).not.toHaveBeenCalled();
    f.stopping.resolve();
    await f.flush();
    expect(f.app.quit).toHaveBeenCalledTimes(1);
    expect(f.quit().preventDefault).not.toHaveBeenCalled();
    f.app.emit("will-quit");
    f.app.emit("will-quit");
    expect(f.dispose).toHaveBeenCalledTimes(1);
    expect(f.app.listenerCount("activate")).toBe(0);
  });
});

describe("desktop lifecycle startup window", () => {
  it("raises the placeholder before initialization and swaps it after", async () => {
    const f = fixture(false, true, { startupWindow: true });
    f.ready.resolve();
    await f.flush();
    // The whole point: a window exists while `initialize` is still pending,
    // because that is the part that takes tens of seconds.
    expect(f.initialize).toHaveBeenCalledTimes(1);
    expect(f.showStartupWindow).toHaveBeenCalledTimes(1);
    expect(f.windows).toHaveLength(1);
    expect(f.revealStartupWindow).not.toHaveBeenCalled();
    expect(f.createWindow).not.toHaveBeenCalled();
    f.starting.resolve();
    await f.flush();
    expect(f.revealStartupWindow).toHaveBeenCalledTimes(1);
    // The placeholder is reused, not replaced: two windows would flash.
    expect(f.createWindow).not.toHaveBeenCalled();
    expect(f.windows).toHaveLength(1);
    expect(f.windows[0].show).toHaveBeenCalled();
  });
  it("keeps no placeholder for a background launch", async () => {
    const f = fixture(true, true, { startupWindow: true });
    await f.boot();
    expect(f.initialize).toHaveBeenCalledTimes(1);
    expect(f.showStartupWindow).not.toHaveBeenCalled();
    expect(f.revealStartupWindow).not.toHaveBeenCalled();
    expect(f.windows).toHaveLength(0);
  });
  it("opens the real app when the placeholder was closed during startup", async () => {
    const f = fixture(false, true, { startupWindow: true });
    f.ready.resolve();
    await f.flush();
    expect(f.windows).toHaveLength(1);
    f.windows.length = 0;
    f.starting.resolve();
    await f.flush();
    expect(f.revealStartupWindow).toHaveBeenCalledTimes(1);
    expect(f.createWindow).toHaveBeenCalledTimes(1);
    expect(f.windows).toHaveLength(1);
  });
  it("quitting during initialization leaves the placeholder unrevealed", async () => {
    const f = fixture(false, true, { startupWindow: true });
    f.ready.resolve();
    await f.flush();
    expect(f.showStartupWindow).toHaveBeenCalledTimes(1);
    f.quit();
    f.starting.resolve();
    await f.flush();
    expect(f.revealStartupWindow).not.toHaveBeenCalled();
    expect(f.createWindow).not.toHaveBeenCalled();
  });
  it("still opens the real app when the placeholder cannot be raised", async () => {
    const f = fixture(false, true, {
      startupWindow: true,
      startupWindowError: new Error("startup_window_unavailable"),
    });
    f.ready.resolve();
    await f.flush();
    // The placeholder is a courtesy, not a dependency: a launch that cannot
    // draw it must still bring up the services and the real window.
    expect(f.initialize).toHaveBeenCalledTimes(1);
    expect(f.onError).not.toHaveBeenCalled();
    f.starting.resolve();
    await f.flush();
    expect(f.revealStartupWindow).not.toHaveBeenCalled();
    expect(f.createWindow).toHaveBeenCalledTimes(1);
    expect(f.windows).toHaveLength(1);
  });
});

describe("desktop lifecycle failure paths", () => {
  it("a failed start is reported once and opens no window on any request", async () => {
    const f = fixture();
    const error = new Error("core_unavailable");
    f.ready.resolve();
    await f.flush();
    f.starting.reject(error);
    await f.flush();
    expect(f.onError).toHaveBeenCalledExactlyOnceWith(error);
    expect(f.app.quit).toHaveBeenCalledTimes(1);
    f.trayOpen();
    f.app.emit("activate");
    f.app.emit("second-instance", {}, ["One Memory"]);
    await f.flush();
    expect(f.createWindow).not.toHaveBeenCalled();
    expect(f.quit().preventDefault).toHaveBeenCalled();
    // Teardown can fail after the startup error (for example, while writing a
    // diagnostic). It must not replace the useful error with a second dialog.
    f.stopping.reject(new Error("diagnostic_detail_key_invalid"));
    await f.flush();
    // Whatever did come up before the failure still has to be torn down.
    expect(f.stop).toHaveBeenCalledTimes(1);
    expect(f.onError).toHaveBeenCalledTimes(1);
  });
  it("a start that fails after a quit request stops services without an error box", async () => {
    const f = fixture();
    f.ready.resolve();
    await f.flush();
    f.quit();
    f.starting.reject(new Error("core_unavailable"));
    await f.flush();
    f.stopping.resolve();
    await f.flush();
    expect(f.stop).toHaveBeenCalledTimes(1);
    expect(f.onError).not.toHaveBeenCalled();
    expect(f.createWindow).not.toHaveBeenCalled();
  });
  it("an Electron readiness failure is reported instead of left unhandled", async () => {
    const error = new Error("electron_never_ready");
    const f = fixture(false, true, { whenReady: Promise.reject(error) });
    await f.flush();
    expect(f.prepare).toHaveBeenCalledTimes(1);
    expect(f.onError).toHaveBeenCalledExactlyOnceWith(error);
    expect(f.app.quit).toHaveBeenCalledTimes(1);
    expect(f.initialize).not.toHaveBeenCalled();
    expect(f.installTray).not.toHaveBeenCalled();
  });
  it("a failed protocol registration is reported instead of thrown at the caller", async () => {
    const error = new Error("scheme_registration_failed");
    const f = fixture(false, true, { prepareError: error });
    await f.boot();
    expect(f.onError).toHaveBeenCalledExactlyOnceWith(error);
    expect(f.app.quit).toHaveBeenCalledTimes(1);
    expect(f.initialize).not.toHaveBeenCalled();
    expect(f.installTray).not.toHaveBeenCalled();
    expect(f.quit().preventDefault).toHaveBeenCalled();
    f.stopping.resolve();
    await f.flush();
    expect(f.stop).toHaveBeenCalledTimes(1);
  });
  it("replaces a destroyed window instead of throwing at the tray", async () => {
    const f = fixture();
    await f.boot();
    expect(f.windows).toHaveLength(1);
    f.windows[0] = destroyedWindow();
    expect(() => f.trayOpen()).not.toThrow();
    await f.flush();
    expect(f.createWindow).toHaveBeenCalledTimes(2);
    expect(f.windows.filter((window) => !window.isDestroyed())).toHaveLength(1);
    expect(f.onError).not.toHaveBeenCalled();
    expect(f.stop).not.toHaveBeenCalled();
  });
  it("a window that fails to open leaves the resident services running", async () => {
    const error = new Error("window_unavailable");
    const overrides: Overrides = { windowError: error };
    const f = fixture(true, true, overrides);
    await f.boot();
    expect(f.windows).toHaveLength(0);
    f.trayOpen();
    await f.flush();
    expect(f.onError).toHaveBeenCalledExactlyOnceWith(error);
    expect(f.stop).not.toHaveBeenCalled();
    expect(f.app.quit).not.toHaveBeenCalled();
    overrides.windowError = undefined;
    f.trayOpen();
    await f.flush();
    expect(f.windows).toHaveLength(1);
  });
  it("without a tray a failed window ends the process instead of hiding it", async () => {
    const error = new Error("window_unavailable");
    const f = fixture(false, true, { platform: "win32", windowError: error });
    await f.boot();
    expect(f.windows).toHaveLength(0);
    expect(f.onError).toHaveBeenCalledExactlyOnceWith(error);
    expect(f.app.quit).toHaveBeenCalledTimes(1);
  });
  it("a long start collapses every queued open into one Core and one window", async () => {
    const gate = deferred();
    const f = fixture(false, true, { windowGate: gate.promise });
    f.ready.resolve();
    await f.flush();
    f.trayOpen();
    f.trayOpen();
    f.app.emit("activate");
    f.app.emit("second-instance", {}, ["One Memory"]);
    await f.flush();
    expect(f.initialize).toHaveBeenCalledTimes(1);
    expect(f.createWindow).not.toHaveBeenCalled();
    f.starting.resolve();
    await f.flush();
    expect(f.createWindow).toHaveBeenCalledTimes(1);
    // Impatient clicks while the first window is still being built must not
    // build a second one, nor a second Core behind it.
    f.trayOpen();
    f.trayOpen();
    f.app.emit("activate");
    await f.flush();
    expect(f.createWindow).toHaveBeenCalledTimes(1);
    gate.resolve();
    await f.flush();
    expect(f.windows).toHaveLength(1);
    expect(f.initialize).toHaveBeenCalledTimes(1);
  });
});
