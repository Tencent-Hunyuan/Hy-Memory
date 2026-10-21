import { randomBytes } from "node:crypto";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { RecallBrokerOptions } from "@one-memory/host-bridge";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  expectTypeOf,
  it,
  vi,
} from "vitest";
import type {
  RevokeRecallPermissionInput as WebRevokeInput,
  SetRecallPermissionInput as WebSetInput,
  RecallPermissionStatus as WebStatus,
} from "../../web/src/global";
import type {
  RecallPermissionStatus,
  RevokeRecallPermissionInput,
  SetRecallPermissionInput,
} from "./host-permissions";

const fake = vi.hoisted(() => ({
  ready: undefined as (() => Promise<void>) | undefined,
  handlers: new Map<string, (...args: unknown[]) => unknown>(),
  dataDir: "",
  home: "",
  token: "",
  brokerOptions: [] as RecallBrokerOptions[],
  startCore: vi.fn(async () => {}),
  startBroker: vi.fn(async () => {}),
  quit: vi.fn(),
  error: vi.fn(),
  presence: vi.fn(async () => true),
  expose: vi.fn(),
  invoke: vi.fn(async () => undefined),
}));

vi.mock("./background-tray", () => ({
  LOGIN_SERVICE_NAME: "one-memory-background.plist",
  installBackgroundTray: () => vi.fn(),
}));
vi.mock("electron", () => ({
  app: {
    requestSingleInstanceLock: () => true,
    on: vi.fn(),
    whenReady: () => ({
      // biome-ignore lint/suspicious/noThenProperty: Capture readiness without starting the real Electron lifecycle.
      then: (callback: () => Promise<void>) => {
        fake.ready = callback;
        // Electron hands back a chainable promise; the callback is run by the
        // test, so this only has to be one.
        return Promise.resolve();
      },
    }),
    isPackaged: false,
    getName: () => "Isolated One Memory Test",
    getPath: () => fake.home,
    getAppPath: () => fake.home,
    quit: fake.quit,
    exit: vi.fn(),
  },
  BrowserWindow: class {
    static getAllWindows() {
      return [];
    }
    webContents = { on: vi.fn(), setWindowOpenHandler: vi.fn() };
    once = vi.fn();
    show = vi.fn();
    loadURL = vi.fn(async () => {});
  },
  protocol: { registerSchemesAsPrivileged: vi.fn() },
  ipcMain: {
    handle: (name: string, callback: (...args: unknown[]) => unknown) =>
      fake.handlers.set(name, callback),
  },
  ipcRenderer: { invoke: fake.invoke },
  contextBridge: { exposeInMainWorld: fake.expose },
  clipboard: { writeText: vi.fn() },
  dialog: { showErrorBox: fake.error },
  Menu: { buildFromTemplate: vi.fn(() => ({})), setApplicationMenu: vi.fn() },
  screen: {
    getDisplayNearestPoint: () => ({
      workArea: { x: 0, y: 0, width: 1600, height: 1000 },
    }),
    getCursorScreenPoint: () => ({ x: 0, y: 0 }),
  },
  session: { defaultSession: {} },
  shell: { openExternal: vi.fn() },
  nativeTheme: {
    prefersReducedTransparency: false,
    on: vi.fn(),
    removeListener: vi.fn(),
  },
  systemPreferences: {
    getAnimationSettings: () => ({ prefersReducedMotion: false }),
  },
}));

vi.mock("./permissions", () => ({ installDenyAllPermissionPolicy: vi.fn() }));
vi.mock("./app-protocol", async (importOriginal) => ({
  ...(await importOriginal()),
  installAppProtocol: vi.fn(),
}));
vi.mock("./core-supervisor", () => ({
  CoreSupervisor: class {
    port = 8765;
    apiToken = fake.token;
    instanceId = "isolated-desktop-test";
    start = fake.startCore;
    stop = vi.fn(async () => {});
  },
  resolveCoreDataDir: () => fake.dataDir,
  resolveCoreLaunch: vi.fn(),
  resolvePackagedRuntimeLaunch: vi.fn(() => ({ command: "runtime", args: [] })),
  ensureRuntimeProcess: vi.fn(async () => "started"),
  findAvailablePort: vi.fn(async () => 8765),
}));
vi.mock("./recall-hosts", async () => {
  const { cursorLayout: layout } = await import(
    "@one-memory/host-bridge/installer"
  );
  return {
    recallHosts: async () => [
      {
        hostId: "cursor",
        label: "Cursor",
        layout: layout(path.join(fake.home, "cursor")),
        presencePath: fake.home,
      },
    ],
    isHostPresent: fake.presence,
  };
});
vi.mock("@one-memory/host-bridge", async (importOriginal) => {
  const original =
    await importOriginal<typeof import("@one-memory/host-bridge")>();
  return {
    ...original,
    RecallBroker: class {
      constructor(options: RecallBrokerOptions) {
        fake.brokerOptions.push(options);
      }
      start = fake.startBroker;
      stop = vi.fn(async () => {});
    },
  };
});
vi.mock("../../../packages/host-bridge/src/recall-probe.js", () => ({
  probeRecallInstall: vi.fn(async () => ({
    state: "reachable_but_blind",
    detail: "isolated IPC fixture",
    probedAt: new Date(0).toISOString(),
  })),
}));

const roots: string[] = [];
beforeEach(async () => {
  vi.resetModules();
  vi.clearAllMocks();
  vi.stubEnv("ONE_MEMORY_WEB_URL", "http://127.0.0.1:5173");
  vi.stubEnv("ONE_MEMORY_SMOKE_QUIT_MS", "0");
  fake.handlers.clear();
  fake.brokerOptions.length = 0;
  fake.ready = undefined;
  fake.home = await mkdtemp(path.join(os.tmpdir(), "om-desktop-ipc-"));
  roots.push(fake.home);
  fake.dataDir = path.join(fake.home, ".one-memory");
  fake.token = randomBytes(32).toString("base64url");
  await mkdir(fake.dataDir, { mode: 0o700 });
  await mkdir(path.join(fake.home, "cursor"));
});
afterEach(async () => {
  vi.unstubAllEnvs();
  await Promise.all(
    roots.splice(0).map((root) => rm(root, { recursive: true, force: true })),
  );
});

async function startup() {
  await import("./main.js");
  if (!fake.ready) throw new Error("missing desktop startup");
  await fake.ready();
}

async function invoke(name: string, ...args: unknown[]): Promise<unknown> {
  const handler = fake.handlers.get(`one-memory:${name}`);
  if (!handler) throw new Error("missing IPC handler");
  return handler(
    { senderFrame: { url: "one-memory://app/index.html" } },
    ...args,
  );
}

describe.skipIf(process.platform === "win32")(
  "desktop host permission IPC",
  () => {
    it("keeps the Web receipt and IPC request types identical to the desktop types", () => {
      expectTypeOf<RecallPermissionStatus>().toEqualTypeOf<WebStatus>();
      expectTypeOf<SetRecallPermissionInput>().toEqualTypeOf<WebSetInput>();
      expectTypeOf<RevokeRecallPermissionInput>().toEqualTypeOf<WebRevokeInput>();
    });
    it("always supplies the trusted store to the runtime broker and keeps install separate from grants", async () => {
      await startup();
      expect(fake.startCore).toHaveBeenCalledTimes(1);
      expect(fake.startBroker).toHaveBeenCalledTimes(1);
      const store = fake.brokerOptions[0]?.hostGrantStore;
      expect(store).toBeDefined();
      const preview = (await invoke("preview-recall-install", "cursor")) as {
        fingerprint: string;
      };
      expect(await store?.listInstallations()).toEqual([]);
      const installation = (await invoke(
        "install-recall",
        "cursor",
        preview.fingerprint,
      )) as { permission: RecallPermissionStatus };
      const receipt = installation.permission.receipt;
      if (!receipt) throw new Error("missing safe installation receipt");
      expect(receipt.status).toBe("install-only");
      expect(await invoke("get-recall-permission", "cursor")).toEqual(
        installation.permission,
      );
      const hosts = (await invoke("recall-hosts")) as Array<{
        permission: RecallPermissionStatus;
      }>;
      expect(hosts[0].permission).toEqual(installation.permission);
      const approved = (await invoke("set-recall-permission", "cursor", {
        installationId: receipt.installationId,
        expectedRevision: receipt.revision,
        allowedTools: ["om_recall"],
        allowedScopes: ["all-apps"],
        userConfirmed: true,
      })) as RecallPermissionStatus;
      expect(approved.state).toBe("approved");
      expect((await store?.listInstallations())?.[0]).toEqual(approved.receipt);
      expect(JSON.stringify(approved)).not.toContain("secret");
      expect(JSON.stringify(approved)).not.toContain("hash");
      const revoked = (await invoke("revoke-recall-permission", "cursor", {
        installationId: receipt.installationId,
        expectedRevision: approved.receipt?.revision,
        userConfirmed: true,
      })) as RecallPermissionStatus;
      expect(revoked.state).toBe("revoked");
    });

    it("rejects foreign origins and unknown hosts before reading or mutating grants", async () => {
      await startup();
      for (const method of [
        "get-recall-permission",
        "set-recall-permission",
        "revoke-recall-permission",
        "get-sync-hook-status",
        "preview-sync-hook",
        "apply-sync-hook",
      ]) {
        const handler = fake.handlers.get(`one-memory:${method}`);
        if (!handler) throw new Error("missing handler");
        expect(() =>
          handler(
            { senderFrame: { url: "https://example.invalid" } },
            "cursor",
            {},
          ),
        ).toThrow("Renderer origin is not allowed");
        await expect(invoke(method, "unknown", {})).rejects.toThrow(
          "Unknown recall host",
        );
      }
      expect(
        await fake.brokerOptions[0]?.hostGrantStore?.listInstallations(),
      ).toEqual([]);
      expect(fake.presence).not.toHaveBeenCalled();
    });

    it("never starts Core or an anonymous broker when persisted permission state is broken", async () => {
      await mkdir(path.join(fake.dataDir, "host-permissions"), { mode: 0o700 });
      await writeFile(
        path.join(fake.dataDir, "host-permissions", "recovery-note"),
        "must survive failed startup",
      );
      await startup();
      expect(fake.startCore).not.toHaveBeenCalled();
      expect(fake.startBroker).not.toHaveBeenCalled();
      expect(fake.brokerOptions).toEqual([]);
      expect(fake.error).toHaveBeenCalledWith(
        "MemHub 无法启动",
        expect.stringContaining("MemHub 启动失败"),
      );
      expect(fake.error.mock.calls[0]?.[1]).not.toContain(
        "host_permission_store_unavailable",
      );
      expect(
        await readFile(
          path.join(fake.dataDir, "logs", "diagnostic-desktop.jsonl"),
          "utf8",
        ),
      ).toContain('"error_code":"host_permission_store_unavailable"');
      expect(fake.quit).toHaveBeenCalledTimes(1);
      expect(
        await readFile(
          path.join(fake.dataDir, "host-permissions", "recovery-note"),
          "utf8",
        ),
      ).toBe("must survive failed startup");
      expect(fake.handlers.size).toBe(0);
    });

    it("forwards only the explicit permission IPC methods from the preload", async () => {
      await import("./preload.js");
      expect(fake.expose).toHaveBeenCalledTimes(1);
      const exposed = fake.expose.mock.calls[0]?.[1] as {
        getRecallPermission(hostId: string): Promise<unknown>;
        setRecallPermission(hostId: string, value: unknown): Promise<unknown>;
        revokeRecallPermission(
          hostId: string,
          value: unknown,
        ): Promise<unknown>;
      };
      await exposed.getRecallPermission("cursor");
      const input = {
        installationId: "receipt-id",
        expectedRevision: 1,
        allowedTools: ["om_recall"],
        allowedScopes: ["all-apps"],
        userConfirmed: true,
      };
      await exposed.setRecallPermission("cursor", input);
      await exposed.revokeRecallPermission("cursor", {
        installationId: "receipt-id",
        expectedRevision: 2,
        userConfirmed: true,
      });
      expect(fake.invoke.mock.calls).toEqual([
        ["one-memory:get-recall-permission", "cursor"],
        ["one-memory:set-recall-permission", "cursor", input],
        [
          "one-memory:revoke-recall-permission",
          "cursor",
          {
            installationId: "receipt-id",
            expectedRevision: 2,
            userConfirmed: true,
          },
        ],
      ]);
      expect(
        Object.keys(exposed).some((name) =>
          /credential|secret|store/i.test(name),
        ),
      ).toBe(false);
    });

    it("forwards host uninstall through the explicit preload transport", async () => {
      await import("./preload.js");
      const exposed = fake.expose.mock.calls[0]?.[1] as {
        previewRecallUninstall(hostId: string): Promise<unknown>;
        uninstallRecall(hostId: string, fingerprint: string): Promise<unknown>;
      };
      await exposed.previewRecallUninstall("cursor");
      await exposed.uninstallRecall("cursor", "a".repeat(64));
      expect(fake.invoke).toHaveBeenCalledWith(
        "one-memory:preview-recall-uninstall",
        "cursor",
      );
      expect(fake.invoke).toHaveBeenCalledWith(
        "one-memory:uninstall-recall",
        "cursor",
        "a".repeat(64),
      );
    });

    it("keeps Node-only host permissions out of the sandboxed preload", async () => {
      const source = await readFile(
        path.join(process.cwd(), "src", "preload.ts"),
        "utf8",
      );
      expect(source).not.toMatch(
        /import\s*\{[^}]*LOCAL_EXPERIENCE_UNAVAILABLE[^}]*\}\s*from\s*["']\.\/host-permissions["']/s,
      );
      expect(source).toContain("import type {\n  RecallPermissionStatus,");

      await import("./preload.js");
      const exposed = fake.expose.mock.calls[0]?.[1] as {
        getApiBaseUrl(): Promise<string>;
        getApiToken(): Promise<string>;
      };
      expect(exposed.getApiBaseUrl).toBeTypeOf("function");
      expect(exposed.getApiToken).toBeTypeOf("function");
    });

    it("forwards supplementary Hook actions without accepting renderer paths or exposing secrets", async () => {
      await import("./preload.js");
      const exposed = fake.expose.mock.calls[0]?.[1] as {
        getSyncHookStatus(hostId: string): Promise<unknown>;
        previewSyncHook(hostId: string, operation: string): Promise<unknown>;
        applySyncHook(hostId: string, input: unknown): Promise<unknown>;
      };
      const input = {
        operation: "install",
        installationId: "identity",
        revision: 1,
        fingerprint: "a".repeat(64),
        userConfirmed: true,
      };
      await exposed.getSyncHookStatus("claude-code");
      await exposed.previewSyncHook("claude-code", "install");
      await exposed.applySyncHook("claude-code", input);
      expect(fake.invoke.mock.calls).toEqual([
        ["one-memory:get-sync-hook-status", "claude-code"],
        ["one-memory:preview-sync-hook", "claude-code", "install"],
        ["one-memory:apply-sync-hook", "claude-code", input],
      ]);
      await startup();
      expect(await invoke("get-sync-hook-status", "cursor")).toEqual({
        hostId: "cursor",
        state: "unsupported",
        reason: null,
      });
      await expect(
        invoke("apply-sync-hook", "cursor", { ...input, path: "/untrusted" }),
      ).rejects.toThrow();
      expect(
        await fake.brokerOptions[0].hostGrantStore?.listInstallations(),
      ).toEqual([]);
    });
  },
);
