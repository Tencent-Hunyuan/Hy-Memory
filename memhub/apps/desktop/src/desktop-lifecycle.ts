import type { EventEmitter } from "node:events";

interface DesktopWindow {
  isDestroyed(): boolean;
  isMinimized(): boolean;
  restore(): void;
  show(): void;
  focus(): void;
}

interface LifecycleOptions {
  app: Pick<EventEmitter, "on" | "removeListener"> & {
    requestSingleInstanceLock(): boolean;
    whenReady(): Promise<void>;
    quit(): void;
    exit(code: number): void;
  };
  platform: NodeJS.Platform;
  background: boolean;
  prepare(): void;
  beforeNativeUi?(): Promise<void>;
  initialize(isQuitting: () => boolean): Promise<void>;
  stop(): Promise<void>;
  dispose(): void;
  createWindow(isQuitting: () => boolean): Promise<void>;
  getWindows(): DesktopWindow[];
  installTray(open: () => void): void;
  onError(error: unknown): void;
}

export function startDesktopLifecycle(options: LifecycleOptions): void {
  const { app } = options;
  if (!app.requestSingleInstanceLock()) {
    app.exit(0);
    return;
  }
  let quitting = false;
  let stopped = false;
  let disposed = false;
  let ready = false;
  let wantsWindow = !options.background;
  let opening: Promise<void> | null = null;
  let startup: Promise<void> = Promise.resolve();
  let shutdown: Promise<void> | null = null;

  // A failure that lands after a quit was asked for is the quit happening, not
  // something to interrupt it with: onError is a modal dialog, and the startup
  // path has already recorded the error before rejecting.
  const report = (error: unknown) => {
    if (!quitting) options.onError(error);
  };
  const fail = (error: unknown) => {
    try {
      report(error);
    } finally {
      app.quit();
    }
  };
  // A window failing is not the services failing. On macOS the tray keeps the
  // app reachable, so it stays resident and can be asked again; without one
  // there is no way back to it, and staying would only hide the process.
  const failOpen = (error: unknown) => {
    if (options.platform === "darwin") report(error);
    else fail(error);
  };
  const open = () => {
    if (quitting) return;
    wantsWindow = true;
    if (!ready || opening) return;
    // A destroyed window can still be listed: reusing it throws rather than
    // showing anything, so it does not count as a window to show.
    const window = options
      .getWindows()
      .find((candidate) => !candidate.isDestroyed());
    if (window) {
      if (window.isMinimized()) window.restore();
      window.show();
      window.focus();
      return;
    }
    opening = Promise.resolve()
      .then(() => {
        if (!quitting) return options.createWindow(() => quitting);
      })
      .catch(failOpen)
      .finally(() => {
        opening = null;
      });
  };
  const secondInstance = (_event: unknown, argv: string[]) => {
    if (!argv.includes("--background")) open();
  };
  const activate = () => {
    // Login activation can precede readiness. Only explicit reopen requests
    // override a background launch until initialization has settled.
    if (!ready && options.background) return;
    open();
  };
  const allClosed = () => {
    if (options.platform !== "darwin") app.quit();
  };
  const beforeQuit = (event: { preventDefault(): void }) => {
    quitting = true;
    if (stopped) return;
    event.preventDefault();
    if (shutdown) return;
    // Join startup before teardown so an in-flight start cannot resurrect a
    // service after stop. initialize checks quitting between async boundaries.
    shutdown = startup
      .catch(() => {})
      .then(options.stop)
      // Quit-time failures are logged by stop itself. A second modal here can
      // only obscure the startup error that initiated this shutdown.
      .catch(report)
      .finally(() => {
        stopped = true;
        app.quit();
      });
  };
  const willQuit = () => {
    quitting = true;
    if (disposed) return;
    disposed = true;
    options.dispose();
    app.removeListener("second-instance", secondInstance);
    app.removeListener("activate", activate);
    app.removeListener("window-all-closed", allClosed);
    app.removeListener("before-quit", beforeQuit);
    app.removeListener("will-quit", willQuit);
  };
  app.on("second-instance", secondInstance);
  app.on("activate", activate);
  app.on("window-all-closed", allClosed);
  app.on("before-quit", beforeQuit);
  app.on("will-quit", willQuit);
  try {
    options.prepare();
  } catch (error) {
    // Registering the app scheme has to happen before readiness, so there is
    // nothing left to start; quit visibly instead of throwing at the caller.
    fail(error);
    return;
  }
  app
    .whenReady()
    .then(async () => {
      if (quitting) return;
      startup = Promise.resolve().then(async () => {
        if (quitting) return;
        await options.beforeNativeUi?.();
        if (quitting) return;
        options.installTray(open);
        await options.initialize(() => quitting);
        if (quitting) return;
        ready = true;
        if (wantsWindow) open();
        await opening;
      });
      try {
        await startup;
      } catch (error) {
        fail(error);
      }
    })
    // Readiness itself can fail. Reporting it beats an unhandled rejection in
    // the main process, which leaves the app up with nothing running.
    .catch(fail);
}
