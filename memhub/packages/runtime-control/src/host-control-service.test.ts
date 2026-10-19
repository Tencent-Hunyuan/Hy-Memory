import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import type { RecallHostLayout } from "@one-memory/host-bridge/installer";
import { afterEach, expect, it, vi } from "vitest";
import { createHostControlService } from "./host-control-service.js";
import type { HostPermissions } from "./host-permissions.js";

const reads = vi.hoisted(() => ({
  verify: vi.fn(),
  self: vi.fn(),
  drift: vi.fn(),
  repoint: vi.fn(),
}));
vi.mock("@one-memory/host-bridge/installer", async (load) => ({
  ...(await load<object>()),
  verifyRecallInstall: reads.verify,
  readInstallSelfCheck: reads.self,
  checkSkillDrift: reads.drift,
  planRecallRepoint: reads.repoint,
}));
const roots: string[] = [];
afterEach(async () => {
  vi.resetAllMocks();
  await Promise.all(
    roots.splice(0).map((root) => rm(root, { recursive: true, force: true })),
  );
});
const fingerprint = "a".repeat(64);
const permission = {
  installationId: "11111111-1111-4111-8111-111111111111",
  expectedRevision: 1,
  userConfirmed: true,
  allowedTools: ["om_recall"],
  allowedScopes: ["all-apps", "source:cursor"],
};
const hook = {
  operation: "install",
  installationId: permission.installationId,
  revision: 1,
  fingerprint,
  userConfirmed: true,
};
const operations = {
  getPermission: [],
  getSyncHookStatus: [],
  previewSyncHook: ["install"],
  applySyncHook: [hook],
  setPermission: [permission],
  revokePermission: [
    {
      installationId: permission.installationId,
      expectedRevision: 1,
      userConfirmed: true,
    },
  ],
  previewSkillSync: [],
  applySkillSync: [fingerprint, false],
  previewInstall: [],
  repairInstalledCommand: [],
  install: [fingerprint],
  previewUninstall: [],
  uninstall: [fingerprint],
};
async function fixture() {
  const root = await mkdtemp(path.join(tmpdir(), "host-control-"));
  roots.push(root);
  const layout: RecallHostLayout = {
    hostId: "cursor",
    hostLabel: "Cursor",
    configPath: path.join(root, "config.json"),
    journalPath: path.join(root, "journal.json"),
    containmentRoot: root,
    format: "json",
    channel: "mcp",
    skillPath: null,
  };
  const effects: string[] = [];
  const methods = Object.fromEntries(
    Object.keys(operations).map((name) => [
      name,
      vi.fn(async (..._args: unknown[]): Promise<unknown> => {
        effects.push(name);
        return { hostId: "cursor", operation: name };
      }),
    ]),
  );
  const runtime = {
    runtimeCommand: "fixture-node",
    mcpEntryPath: "fixture-entry",
    discoveryPath: "fixture-discovery",
    credentialFile: "PRIVATE-RUNTIME-ONLY",
  };
  const permissions = {
    ...methods,
    prepareGenericLocalConnection: vi.fn(async () => ({
      credentialFile: path.join(root, "private", "credential"),
    })),
    runtimeFor: vi.fn(() => runtime),
    recordLinkCheck: vi.fn(async () => ({
      link: { state: "wired" },
      skill: { state: "in_sync" },
    })),
    withHostLock: vi.fn(
      async (_layout: unknown, action: () => Promise<unknown>) => {
        effects.push("locked");
        const result = await action();
        effects.push("released");
        return result;
      },
    ),
  };
  reads.verify.mockResolvedValue(undefined);
  reads.self.mockResolvedValue({
    state: "wired",
    detail: "fixture",
    probed_at: "2026-09-20",
  });
  reads.drift.mockResolvedValue({ state: "in_sync" });
  reads.repoint.mockResolvedValue(null);
  const hosts = [
    { hostId: "cursor", label: "Cursor", layout, presencePath: root },
  ];
  return {
    root,
    hosts,
    layout,
    effects,
    methods,
    permissions,
    runtime,
    service: createHostControlService({
      permissions: permissions as unknown as HostPermissions,
      hosts,
    }),
  };
}
it("permits only the reserved no-argument generic prepare operation", async () => {
  const f = await fixture();
  const request = {
    hostId: "generic-local-mcp",
    operation: "prepareGenericLocalConnection",
    args: [],
  };
  expect(await f.service.handle(request)).toEqual({
    credentialFile: path.join(f.root, "private", "credential"),
  });
  expect(
    f.permissions.prepareGenericLocalConnection,
  ).toHaveBeenCalledExactlyOnceWith();
  for (const invalid of [
    { ...request, args: [true] },
    { ...request, operation: "getPermission" },
    { ...request, hostId: "cursor" },
    { ...request, path: "/untrusted" },
  ]) {
    await expect(f.service.handle(invalid)).rejects.toThrow(
      "host_control_invalid_input",
    );
  }
  expect(f.permissions.prepareGenericLocalConnection).toHaveBeenCalledTimes(1);
});
it.each(Object.entries(operations))(
  "dispatches %s exactly once with only the mapped layout and approved arguments",
  async (operation, args) => {
    const f = await fixture();
    expect(
      await f.service.handle({ hostId: "cursor", operation, args }),
    ).toEqual({ hostId: "cursor", operation });
    expect(f.methods[operation]).toHaveBeenCalledExactlyOnceWith(
      f.layout,
      ...args,
    );
    expect(f.effects).toEqual([operation]);
  },
);
it.each(Object.entries(operations))(
  "rejects wrong arity for %s without any dispatch",
  async (operation, args) => {
    const f = await fixture();
    await expect(
      f.service.handle({ hostId: "cursor", operation, args: [...args, null] }),
    ).rejects.toThrow("host_control_invalid_input");
    if (args.length)
      await expect(
        f.service.handle({ hostId: "cursor", operation, args: [] }),
      ).rejects.toThrow("host_control_invalid_input");
    expect(f.effects).toEqual([]);
  },
);
it.each([
  null,
  [],
  {},
  { hostId: "openclaw", operation: "getPermission", args: [] },
  { hostId: "unknown", operation: "getPermission", args: [] },
  { hostId: "cursor", operation: "runtimeFor", args: [] },
  { hostId: "cursor", operation: "constructor", args: [] },
  { hostId: "cursor", operation: "getPermission", args: [], layout: {} },
  { hostId: "cursor", operation: "install", args: ["not-a-fingerprint"] },
  {
    hostId: "cursor",
    operation: "setPermission",
    args: [{ ...permission, path: "/incoming" }],
  },
  {
    hostId: "cursor",
    operation: "setPermission",
    args: [{ ...permission, userConfirmed: false }],
  },
  {
    hostId: "cursor",
    operation: "applySkillSync",
    args: [fingerprint, "true"],
  },
  { hostId: "cursor", operation: "previewSyncHook", args: ["other"] },
])(
  "malformed request %# does not reach the permission owner",
  async (input) => {
    const f = await fixture();
    await expect(f.service.handle(input)).rejects.toThrow();
    expect(f.effects).toEqual([]);
  },
);
it("clones nested arguments and trusted layout before an asynchronous owner observes them", async () => {
  const f = await fixture();
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const args = [structuredClone(permission)];
  f.methods.setPermission.mockImplementation(async (...values: unknown[]) => {
    const [layout, input] = values as [RecallHostLayout, typeof permission];
    await gate;
    expect(input.allowedScopes).toEqual(permission.allowedScopes);
    expect(layout.configPath).toBe(path.join(f.root, "config.json"));
    return { ok: true };
  });
  const pending = f.service.handle({
    hostId: "cursor",
    operation: "setPermission",
    args,
  });
  args[0].allowedScopes.push("source:other");
  f.layout.configPath = "/changed";
  release();
  await pending;
});
it("inspect uses recorded self check, file verification, real presence and drift without live probing", async () => {
  const f = await fixture();
  const result = await f.service.handle({
    hostId: "cursor",
    operation: "inspect",
    args: [],
  });
  expect(result).toEqual({
    hostId: "cursor",
    label: "Cursor",
    present: true,
    installed: true,
    permission: { hostId: "cursor", operation: "getPermission" },
    selfCheck: await reads.self.mock.results[0].value,
    skillDrift: { state: "in_sync" },
  });
  expect(f.permissions.recordLinkCheck).not.toHaveBeenCalled();
  expect(reads.repoint).not.toHaveBeenCalled();
  expect(JSON.stringify(result)).not.toContain("PRIVATE-RUNTIME-ONLY");
});
it("uninstalled and absent inspect does not claim installed or request skill drift", async () => {
  const f = await fixture();
  await rm(f.root, { recursive: true });
  reads.verify.mockRejectedValueOnce(Error("missing"));
  reads.self.mockResolvedValue(null);
  expect(
    await f.service.handle({
      hostId: "cursor",
      operation: "inspect",
      args: [],
    }),
  ).toMatchObject({
    present: false,
    installed: false,
    skillDrift: null,
    selfCheck: null,
  });
  expect(reads.drift).not.toHaveBeenCalled();
});
it("probe records under the existing host lock and returns repoint endpoints, never its install plan", async () => {
  const f = await fixture();
  reads.repoint.mockImplementation(async () => {
    expect(f.effects).toEqual(["locked"]);
    expect(f.permissions.recordLinkCheck).toHaveBeenCalledOnce();
    return {
      from: "old",
      to: "new",
      plan: { private: "PRIVATE-RUNTIME-ONLY" },
    };
  });
  expect(
    await f.service.handle({ hostId: "cursor", operation: "probe", args: [] }),
  ).toEqual({
    hostId: "cursor",
    probe: { state: "wired" },
    skill: { state: "in_sync" },
    repoint: { from: "old", to: "new" },
  });
  expect(f.effects).toEqual(["locked", "released"]);
  expect(reads.repoint).toHaveBeenCalledExactlyOnceWith(f.layout, f.runtime);
});
it("does not evaluate accessors or dispatch malformed clone payloads", async () => {
  const f = await fixture();
  const getter = vi.fn(() => []);
  await expect(
    f.service.handle({
      hostId: "cursor",
      operation: "inspect",
      get args() {
        return getter();
      },
    }),
  ).rejects.toThrow();
  expect(getter).not.toHaveBeenCalled();
  expect(f.effects).toEqual([]);
});

it.each(["getPermission", "install", "probe"])(
  "%s failures do not serialize arbitrary native secrets",
  async (operation) => {
    const f = await fixture();
    const method =
      operation === "probe"
        ? f.permissions.recordLinkCheck
        : f.methods[operation];
    method.mockRejectedValueOnce(
      Error("PRIVATE-TOKEN /private/host unexpected failure"),
    );
    await expect(
      f.service.handle({
        hostId: "cursor",
        operation,
        args: operation === "install" ? [fingerprint] : [],
      }),
    ).rejects.toThrow(new Error("host_control_unavailable"));
  },
);

it("preserves stable host-runtime refusal codes", async () => {
  const f = await fixture();
  f.methods.previewInstall.mockRejectedValueOnce(
    Error("host_runtime_current_changed"),
  );
  await expect(
    f.service.handle({
      hostId: "cursor",
      operation: "previewInstall",
      args: [],
    }),
  ).rejects.toThrow("host_runtime_current_changed");
});

it("preserves the actionable fixed hook-removal code without automatically uninstalling", async () => {
  const f = await fixture();
  f.methods.install.mockRejectedValueOnce(Error("sync_hook_remove_first"));
  await expect(
    f.service.handle({
      hostId: "cursor",
      operation: "install",
      args: [fingerprint],
    }),
  ).rejects.toThrow("sync_hook_remove_first");
  expect(f.methods.uninstall).not.toHaveBeenCalled();
  expect(f.methods.applySyncHook).not.toHaveBeenCalled();
});
