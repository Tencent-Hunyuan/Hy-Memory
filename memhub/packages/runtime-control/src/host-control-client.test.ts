import path from "node:path";
import { codexLayout } from "@one-memory/host-bridge/installer";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ConnectionClient } from "./connection-client.js";
import {
  createHostPermissionClient,
  type HostPermissionClient,
} from "./host-control-client.js";

const fake = vi.hoisted(() => ({
  host: vi.fn<NonNullable<ConnectionClient["host"]>>(),
  create: vi.fn(),
  available: true,
}));

// Transport discovery/authentication have their own tests. This suite isolates
// adapter projection and scheduling without reading a home or opening a socket.
vi.mock("./connection-client.js", () => ({
  createConnectionClient: (options: unknown) => {
    fake.create(options);
    return fake.available ? { host: fake.host } : {};
  },
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

const permission = {
  installationId: "synthetic-installation",
  expectedRevision: 3,
  allowedTools: ["om_recall"],
  allowedScopes: ["source:codex"],
  userConfirmed: true,
};
const hook = {
  operation: "install",
  installationId: "synthetic-installation",
  revision: 3,
  fingerprint: "b".repeat(64),
  userConfirmed: true,
};
const cases: Array<[keyof HostPermissionClient, unknown[]]> = [
  ["getPermission", []],
  ["getSyncHookStatus", []],
  ["previewSyncHook", ["uninstall"]],
  ["applySyncHook", [hook]],
  ["setPermission", [permission]],
  [
    "revokePermission",
    [
      {
        installationId: "synthetic-installation",
        expectedRevision: 4,
        userConfirmed: true,
      },
    ],
  ],
  ["previewSkillSync", []],
  ["applySkillSync", ["c".repeat(64), false]],
  ["previewInstall", []],
  ["repairInstalledCommand", []],
  ["install", ["d".repeat(64)]],
  ["previewUninstall", []],
  ["uninstall", ["e".repeat(64)]],
];

beforeEach(() => {
  vi.resetAllMocks();
  fake.available = true;
});

describe("host permission remote adapter", () => {
  it("prepares the shared identity with fixed zero-argument input and returns only its private path", async () => {
    const credentialFile = path.join(
      "/synthetic/data/host-permissions/installations",
      "a".repeat(64),
      "credential",
    );
    fake.host.mockResolvedValue({ credentialFile });
    const client = createHostPermissionClient({ dataDir: "/synthetic/data" });
    await expect(client.prepareGenericLocalConnection()).resolves.toEqual({
      credentialFile,
    });
    expect(fake.host).toHaveBeenCalledExactlyOnceWith({
      hostId: "generic-local-mcp",
      operation: "prepareGenericLocalConnection",
      args: [],
    });
  });

  it.each([
    { credentialFile: "/synthetic/data/credential" },
    {
      credentialFile:
        "/synthetic/data/host-permissions/installations/other/credential",
    },
    {
      credentialFile: `/elsewhere/installations/${"a".repeat(64)}/credential`,
    },
    {
      credentialFile: `/synthetic/data/host-permissions/installations/${"a".repeat(64)}/credential`,
      secret: "unexpected",
    },
  ])("rejects an invalid shared credential response: %j", async (response) => {
    fake.host.mockResolvedValue(response);
    const client = createHostPermissionClient({ dataDir: "/synthetic/data" });
    await expect(client.prepareGenericLocalConnection()).rejects.toThrow(
      "connection_client_invalid_response",
    );
  });

  it("never retries the prepare write after an uncertain transport failure", async () => {
    fake.host.mockRejectedValue(Error("connection_client_unavailable"));
    const client = createHostPermissionClient({ dataDir: "/synthetic/data" });
    await expect(client.prepareGenericLocalConnection()).rejects.toThrow(
      "connection_client_unavailable",
    );
    expect(fake.host).toHaveBeenCalledTimes(1);
  });

  it.each(cases)(
    "forwards %s with exact args and only the layout host ID",
    async (operation, args) => {
      const layout = codexLayout("/synthetic/not-a-real-home/codex");
      const response = { syntheticResult: operation };
      fake.host.mockResolvedValue(response);
      const client = createHostPermissionClient({ dataDir: "/synthetic/data" });
      expect(fake.create).toHaveBeenCalledExactlyOnceWith({
        dataDir: "/synthetic/data",
      });
      expect(fake.host).not.toHaveBeenCalled();

      expect(
        await Reflect.apply(client[operation], client, [layout, ...args]),
      ).toBe(response);
      expect(fake.host).toHaveBeenCalledExactlyOnceWith({
        hostId: "codex",
        operation,
        args,
      });
      expect(JSON.stringify(fake.host.mock.calls)).not.toContain("/synthetic/");
      expect(Object.keys(client).sort()).toEqual(
        [
          ...cases.map(([name]) => name),
          "prepareGenericLocalConnection",
        ].sort(),
      );
    },
  );

  it("serializes cross-host mutations and reads behind the active mutation", async () => {
    const first = deferred<unknown>();
    const entered = deferred<void>();
    fake.host
      .mockImplementationOnce(() => {
        entered.resolve();
        return first.promise;
      })
      .mockResolvedValue({ done: true });
    const client = createHostPermissionClient({ dataDir: "/synthetic/data" });
    const layout = codexLayout("/synthetic/codex");
    const install = client.install(layout, "a".repeat(64));
    const revoke = client.revokePermission(
      { ...layout, hostId: "other-host" },
      {
        installationId: "synthetic-installation",
        expectedRevision: 4,
        userConfirmed: true,
      },
    );
    const read = client.getPermission(layout);
    await entered.promise;
    expect(fake.host.mock.calls).toEqual([
      [{ hostId: "codex", operation: "install", args: ["a".repeat(64)] }],
    ]);
    first.resolve({ installed: true });
    await Promise.all([install, revoke, read]);
    expect(
      fake.host.mock.calls.map(([request]) => [
        request.hostId,
        request.operation,
      ]),
    ).toEqual([
      ["codex", "install"],
      ["other-host", "revokePermission"],
      ["codex", "getPermission"],
    ]);
  });

  it("snapshots nested args and host ID before a queued request can dispatch", async () => {
    const gate = deferred<unknown>();
    fake.host.mockReturnValueOnce(gate.promise).mockResolvedValue(null);
    const client = createHostPermissionClient({ dataDir: "/synthetic/data" });
    const layout = codexLayout("/synthetic/codex");
    const blocker = client.install(layout, "a".repeat(64));
    const input = structuredClone(permission);
    const queued = client.setPermission(layout, input);
    input.allowedScopes.push("all-apps");
    input.expectedRevision = 99;
    layout.hostId = "changed-after-enqueue";
    gate.resolve(null);
    await Promise.all([blocker, queued]);
    expect(fake.host.mock.calls[1]).toEqual([
      { hostId: "codex", operation: "setPermission", args: [permission] },
    ]);
  });

  it.each(["rejection", "synchronous throw"])(
    "does not replay a mutation after %s and lets queued work continue",
    async (kind) => {
      const error = Error("connection_client_operation_unknown");
      if (kind === "rejection") fake.host.mockRejectedValueOnce(error);
      else
        fake.host.mockImplementationOnce(() => {
          throw error;
        });
      fake.host.mockResolvedValue({ state: "revoked" });
      const client = createHostPermissionClient({ dataDir: "/synthetic/data" });
      const layout = codexLayout("/synthetic/codex");
      const failed = client.install(layout, "a".repeat(64));
      const rejected = expect(failed).rejects.toBe(error);
      const next = client.revokePermission(layout, {
        installationId: "synthetic",
        expectedRevision: 2,
        userConfirmed: true,
      });
      await rejected;
      await expect(next).resolves.toEqual({ state: "revoked" });
      await client.getPermission(layout);
      expect(
        fake.host.mock.calls.map(([request]) => request.operation),
      ).toEqual(["install", "revokePermission", "getPermission"]);
    },
  );

  it("fails closed when the remote host capability is absent", async () => {
    fake.available = false;
    const client = createHostPermissionClient({ dataDir: "/synthetic/data" });
    const layout = codexLayout("/synthetic/codex");
    await expect(client.install(layout, "a".repeat(64))).rejects.toThrow(
      "connection_client_capability_unavailable",
    );
    await expect(client.getPermission(layout)).rejects.toThrow(
      "connection_client_capability_unavailable",
    );
    expect(fake.host).not.toHaveBeenCalled();
    expect(fake.create).toHaveBeenCalledTimes(1);
  });

  it("retries one transient read-only preview without replaying mutations", async () => {
    const transient = Error("connection_client_unavailable");
    fake.host
      .mockRejectedValueOnce(transient)
      .mockResolvedValueOnce({ fingerprint: "a" });
    const client = createHostPermissionClient({ dataDir: "/synthetic/data" });
    const layout = codexLayout("/synthetic/codex");
    await expect(client.previewInstall(layout)).resolves.toEqual({
      fingerprint: "a",
    });
    expect(fake.host).toHaveBeenCalledTimes(2);

    fake.host.mockRejectedValue(transient);
    await expect(client.install(layout, "b".repeat(64))).rejects.toBe(
      transient,
    );
    expect(fake.host).toHaveBeenCalledTimes(3);
  });
});
