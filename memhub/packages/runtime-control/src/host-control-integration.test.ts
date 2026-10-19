import {
  chmod,
  lstat,
  mkdir,
  mkdtemp,
  readdir,
  readFile,
  realpath,
  rm,
  unlink,
  writeFile,
} from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import path from "node:path";
import {
  HostGrantStore,
  RecallBroker,
  readHostCredentialFile,
} from "@one-memory/host-bridge";
import {
  codexLayout,
  piLayout,
  type RecallInstallPreview,
  renderConnectionMaterial,
} from "@one-memory/host-bridge/installer";
import axios from "axios";
import { afterEach, expect, it, vi } from "vitest";
import { createConnectionClient } from "./connection-client.js";
import { createConnectionControlService } from "./connection-control-service.js";
import { createHostPermissionClient } from "./host-control-client.js";
import { HostPermissions } from "./host-permissions.js";

const INSTANCE = "11111111-1111-4111-8111-111111111111";
const TOKEN = "fixture-management-token";
const cleanups: Array<() => Promise<void>> = [];
afterEach(async () => {
  const errors: unknown[] = [];
  for (const cleanup of cleanups.splice(0).reverse()) {
    try {
      await cleanup();
    } catch (error) {
      errors.push(error);
    }
  }
  vi.restoreAllMocks();
  expect(errors).toEqual([]);
});

// Only Core's identity endpoint is synthetic. Every control layer and host file write is real.
async function fixture() {
  const root = await realpath(
    await mkdtemp(path.join(tmpdir(), "d06-host-wire-")),
  );
  cleanups.push(() => rm(root, { recursive: true, force: true }));
  const dataDir = path.join(root, "data");
  await mkdir(dataDir, { mode: 0o700 });
  await chmod(dataDir, 0o700);
  const layout = codexLayout(path.join(root, "codex"));
  await mkdir(layout.containmentRoot, { mode: 0o700 });
  if (!layout.configPath) throw Error("fixture missing config path");
  const piHost = piLayout(path.join(root, "pi", "agent"));
  await mkdir(piHost.containmentRoot, { recursive: true, mode: 0o700 });
  const original = 'model = "fixture-model"\n';
  await writeFile(layout.configPath, original, { mode: 0o600 });
  const unexpected: string[] = [];
  let identities = 0;
  const core = createServer((request, response) => {
    if (
      request.url !== "/api/v1/runtime/identity" ||
      request.method !== "GET" ||
      request.headers.authorization !== `Bearer ${TOKEN}`
    ) {
      unexpected.push(`${request.method} ${request.url}`);
      response.writeHead(403);
      response.end();
      return;
    }
    identities++;
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(
      JSON.stringify({
        status: "ok",
        service: "one-memory-core",
        version: "fixture",
        instance_id: INSTANCE,
      }),
    );
  });
  await new Promise<void>((resolve) => core.listen(0, "127.0.0.1", resolve));
  cleanups.push(async () => {
    core.closeAllConnections();
    await new Promise<void>((resolve, reject) =>
      core.close((error) => (error ? reject(error) : resolve())),
    );
    expect(unexpected).toEqual([]);
  });
  const address = core.address();
  if (!address || typeof address === "string")
    throw Error("fixture missing port");
  const coreBaseUrl = `http://127.0.0.1:${address.port}`;
  for (const [name, body] of Object.entries({
    "runtime.json": {
      schema_version: 1,
      service: "one-memory-core",
      instance_id: INSTANCE,
      pid: process.pid,
      parent_pid: process.pid,
      host: "127.0.0.1",
      port: address.port,
      started_at: new Date().toISOString(),
    },
    "runtime-service.json": {
      schema_version: 1,
      service: "one-memory-runtime",
      instance_id: INSTANCE,
      pid: process.pid,
      status: "running",
      core_port: address.port,
    },
  }))
    await writeFile(path.join(dataDir, name), JSON.stringify(body), {
      mode: 0o600,
    });
  await writeFile(path.join(dataDir, "runtime-token"), TOKEN, { mode: 0o600 });
  const discoveryPath = path.join(
    dataDir,
    "integrations",
    "cursor-broker.json",
  );
  const runtime = {
    runtimeCommand: path.join(root, "missing-runtime"),
    mcpEntryPath: path.join(root, "missing-entry.cjs"),
    recallCliPath: path.join(root, "missing-recall-cli.cjs"),
    discoveryPath,
  };
  const owner = await HostPermissions.open(dataDir, runtime);
  // Observe, never replace, the factory: client calls must not open another owner locally.
  const open = vi.spyOn(HostPermissions, "open");
  const options = {
    dataDir,
    permissions: owner,
    connection: { instanceId: INSTANCE, coreApiToken: TOKEN, coreBaseUrl },
    layouts: new Map([
      ["codex", layout],
      ["pi", piHost],
    ]),
    hosts: [
      {
        hostId: "codex",
        label: "Codex",
        layout,
        presencePath: layout.containmentRoot,
      },
      {
        hostId: "pi",
        label: "Pi",
        layout: piHost,
        presencePath: piHost.containmentRoot,
      },
    ],
  };
  let service = createConnectionControlService(options);
  const dispatched: string[] = [];
  const broker = new RecallBroker({
    coreBaseUrl,
    coreApiToken: TOKEN,
    discoveryPath,
    instanceId: INSTANCE,
    preferredPort: 0,
    hostGrantStore: owner.hostGrantStore,
    connectionControl(request) {
      dispatched.push(
        `${request.action}:${String(request.input.operation ?? "")}`,
      );
      return service.handle(request);
    },
  });
  await broker.start();
  cleanups.push(async () => {
    await service.stop();
    await broker.stop();
  });
  const client = createHostPermissionClient({ dataDir });
  const control = createConnectionClient({ dataDir });
  return {
    root,
    dataDir,
    layout,
    piHost,
    original,
    owner,
    client,
    control,
    broker,
    open,
    dispatched,
    identityReads: () => identities,
    async reopen() {
      await service.stop();
      service = createConnectionControlService(options);
      return createHostPermissionClient({ dataDir });
    },
  };
}

it("prepares a real generic grant on the first explicit control request", async () => {
  const f = await fixture();
  const installations = path.join(
    f.dataDir,
    "host-permissions",
    "installations",
  );
  expect(await f.owner.hostGrantStore.listInstallations()).toEqual([]);
  expect(await readdir(installations)).toEqual([]);

  const first = await f.client.prepareGenericLocalConnection();
  expect(first.credentialFile).toMatch(
    /\/host-permissions\/installations\/[a-f0-9]{64}\/credential$/,
  );
  expect(await readdir(installations)).toEqual([
    path.basename(path.dirname(first.credentialFile)),
  ]);
  expect((await lstat(first.credentialFile)).mode & 0o777).toBe(0o600);
  expect((await lstat(path.dirname(first.credentialFile))).mode & 0o777).toBe(
    0o700,
  );

  const persisted = await HostGrantStore.open(
    path.join(f.dataDir, "host-permissions", "grants"),
  );
  const receipts = await persisted.listInstallations();
  expect(receipts).toHaveLength(1);
  expect(receipts[0]).toMatchObject({
    hostId: "generic-local-mcp",
    revision: 2,
    status: "approved",
    allowedTools: ["om_recall", "om_conversations"],
    allowedScopes: ["all-apps", "raw-imported-all"],
  });
  expect(await f.owner.hostGrantStore.listInstallations()).toEqual(receipts);
  const secret = await readHostCredentialFile(first.credentialFile);
  expect(JSON.stringify(first)).not.toContain(secret);
  await expect(persisted.authenticate(secret)).resolves.toMatchObject(
    receipts[0],
  );
  await expect(
    persisted.authorize(secret, {
      tool: "om_recall",
      scopes: ["all-apps"],
    }),
  ).resolves.toMatchObject(receipts[0]);
  await expect(
    persisted.authorize(secret, {
      tool: "om_conversations",
      scopes: ["all-apps", "raw-imported-all"],
    }),
  ).resolves.toMatchObject(receipts[0]);
  await expect(
    persisted.authorize(secret, { tool: "om_ask", scopes: ["all-apps"] }),
  ).rejects.toMatchObject({ code: "host_grant_tool_denied" });
  expect(f.open).not.toHaveBeenCalled();
  expect(f.dispatched).toContain("host:prepareGenericLocalConnection");
});

it("reuses one real grant and credential across concurrent local and broker requests", async () => {
  const f = await fixture();
  const [first, second, third] = await Promise.all([
    f.client.prepareGenericLocalConnection(),
    f.owner.prepareGenericLocalConnection(),
    f.client.prepareGenericLocalConnection(),
  ]);
  expect(third).toEqual(first);
  expect(second).toEqual(first);
  expect((await lstat(first.credentialFile)).mode & 0o777).toBe(0o600);
  expect((await lstat(path.dirname(first.credentialFile))).mode & 0o777).toBe(
    0o700,
  );
  expect(first.credentialFile).toMatch(
    /\/host-permissions\/installations\/[a-f0-9]{64}\/credential$/,
  );
  expect(JSON.stringify(first)).not.toContain("omhg_v1_");
  const material = renderConnectionMaterial({
    runtimeCommand: "/synthetic/installed/runtime",
    mcpEntryPath: "/synthetic/installed/mcp.cjs",
    recallCliPath: "/synthetic/installed/recall-cli.cjs",
    discoveryPath: path.join(f.dataDir, "integrations", "cursor-broker.json"),
    credentialFile: first.credentialFile,
  });
  const config = JSON.parse(material.mcp) as {
    mcpServers: Record<string, { env: Record<string, string> }>;
  };
  expect(
    config.mcpServers["one-memory"].env.ONE_MEMORY_HOST_CREDENTIAL_FILE,
  ).toBe(first.credentialFile);
  expect(material.shell).toContain(first.credentialFile);
  expect(JSON.stringify(material)).not.toContain("omhg_v1_");
  const remote = await f.client.prepareGenericLocalConnection();
  expect(remote).toEqual(first);
  expect(await (await f.reopen()).prepareGenericLocalConnection()).toEqual(
    first,
  );
  const stored = await HostGrantStore.open(
    path.join(f.dataDir, "host-permissions", "grants"),
  );
  const receipts = await stored.listInstallations();
  expect(receipts).toHaveLength(1);
  expect(receipts[0]).toMatchObject({
    hostId: "generic-local-mcp",
    status: "approved",
    allowedTools: ["om_recall", "om_conversations"],
    allowedScopes: ["all-apps", "raw-imported-all"],
  });
  expect(await f.owner.hostGrantStore.listInstallations()).toEqual(receipts);
  const secret = await readHostCredentialFile(first.credentialFile);
  await expect(stored.authenticate(secret)).resolves.toMatchObject(receipts[0]);
  expect(JSON.stringify(material)).not.toContain(secret);
  expect(await readHostCredentialFile(first.credentialFile)).toBe(secret);
  expect(f.open).not.toHaveBeenCalled();
});

it("fails closed on missing, changed, or public generic credentials without reissuing", async () => {
  const f = await fixture();
  const first = await f.client.prepareGenericLocalConnection();
  const store = await HostGrantStore.open(
    path.join(f.dataDir, "host-permissions", "grants"),
  );
  const receipts = await store.listInstallations();
  const secret = await readHostCredentialFile(first.credentialFile);

  await chmod(first.credentialFile, 0o644);
  await expect(f.client.prepareGenericLocalConnection()).rejects.toThrow(
    "host_permission_relink_required",
  );
  expect(await store.listInstallations()).toEqual(receipts);
  await chmod(first.credentialFile, 0o600);

  await unlink(first.credentialFile);
  await expect(f.client.prepareGenericLocalConnection()).rejects.toThrow(
    "host_permission_relink_required",
  );
  expect(await store.listInstallations()).toEqual(receipts);
  await writeFile(first.credentialFile, `${secret}\n`, { mode: 0o600 });

  await writeFile(
    first.credentialFile,
    `${secret.slice(0, -1)}${secret.endsWith("a") ? "b" : "a"}\n`,
  );
  await expect(f.client.prepareGenericLocalConnection()).rejects.toThrow(
    "host_permission_relink_required",
  );
  expect(await store.listInstallations()).toEqual(receipts);
  await writeFile(first.credentialFile, `${secret}\n`);

  await writeFile(
    path.join(path.dirname(first.credentialFile), "installation.json"),
    "{invalid",
  );
  await expect(f.client.prepareGenericLocalConnection()).rejects.toThrow(
    "host_permission_storage_unsafe",
  );
  expect(await store.listInstallations()).toEqual(receipts);
  expect(f.open).not.toHaveBeenCalled();
});

it("remote preview is read-only, exact approval installs one real Codex identity without usable-link claims", async () => {
  const f = await fixture();
  expect(await f.client.getPermission(f.layout)).toMatchObject({
    state: "not-installed",
    receipt: null,
  });
  const preview = await f.client.previewInstall(f.layout);
  expect(preview.hostId).toBe("codex");
  expect(preview.fingerprint).toMatch(/^[a-f0-9]{64}$/);
  expect(await f.owner.hostGrantStore.listInstallations()).toEqual([]);
  expect(await readFile(f.layout.configPath as string, "utf8")).toBe(
    f.original,
  );
  expect(JSON.stringify(preview)).not.toContain("omhg_v1_");
  await expect(f.client.install(f.layout, "b".repeat(64))).rejects.toThrow();
  expect(await f.owner.hostGrantStore.listInstallations()).toEqual([]);
  const result = await f.client.install(f.layout, preview.fingerprint);
  expect(result).toMatchObject({
    installed: true,
    permission: {
      state: "install-only",
      receipt: { status: "install-only", allowedTools: [], allowedScopes: [] },
    },
  });
  expect(await readFile(f.layout.configPath as string, "utf8")).toContain(
    "fixture-model",
  );
  expect(await readFile(f.layout.configPath as string, "utf8")).toContain(
    "ONE_MEMORY_HOST_CREDENTIAL_FILE",
  );
  const secret = await readHostCredentialFile(
    f.owner.runtimeFor(f.layout).credentialFile,
  );
  expect(JSON.stringify(result)).not.toContain(secret);
  expect(JSON.stringify(result)).not.toContain(TOKEN);
  await expect(
    f.owner.hostGrantStore.authenticate(secret),
  ).rejects.toMatchObject({ code: "host_grant_not_approved" });
  if (!f.control.host) throw Error("host control missing");
  const inspect = await f.control.host({
    hostId: "codex",
    operation: "inspect",
    args: [],
  });
  expect(inspect).toMatchObject({
    hostId: "codex",
    present: true,
    installed: true,
    permission: { state: "install-only" },
    selfCheck: { state: "unreachable" },
  });
  expect(await f.owner.hostGrantStore.listInstallations()).toHaveLength(1);
  expect(await (await f.reopen()).getPermission(f.layout)).toEqual(
    result.permission,
  );
  expect(await readdir(f.dataDir)).not.toContain("connection-batches");
  expect(f.open).not.toHaveBeenCalled();
  expect(f.identityReads()).toBeGreaterThan(0);
  expect(f.dispatched).toContain("host:install");
});

it("remote explicit revoke changes the original owner receipt and rejects stale or false confirmation", async () => {
  const f = await fixture();
  const preview = await f.client.previewInstall(f.layout);
  const installed = await f.client.install(f.layout, preview.fingerprint);
  const receipt = installed.permission.receipt;
  if (!receipt) throw Error("fixture missing receipt");
  const request = {
    installationId: receipt.installationId,
    expectedRevision: receipt.revision,
    userConfirmed: true as const,
  };
  await expect(
    f.client.revokePermission(f.layout, { ...request, userConfirmed: false }),
  ).rejects.toThrow();
  expect((await f.owner.hostGrantStore.listInstallations())[0]).toHaveProperty(
    "revision",
    receipt.revision,
  );
  const revoked = await f.client.revokePermission(f.layout, request);
  expect(revoked).toMatchObject({
    state: "revoked",
    receipt: {
      installationId: receipt.installationId,
      revision: receipt.revision + 1,
      status: "revoked",
    },
  });
  expect((await f.owner.hostGrantStore.listInstallations())[0]).toEqual(
    revoked.receipt,
  );
  await expect(f.client.revokePermission(f.layout, request)).rejects.toThrow();
  expect(await (await f.reopen()).getPermission(f.layout)).toEqual(revoked);
  expect(f.open).not.toHaveBeenCalled();
});

it("a host credential cannot enter management transport and caller layout cannot redirect file writes", async () => {
  const f = await fixture();
  const redirected = {
    ...f.layout,
    configPath: path.join(f.root, "incoming.json"),
    containmentRoot: f.root,
  };
  const preview: RecallInstallPreview =
    await f.client.previewInstall(redirected);
  expect(preview.files.some((file) => file.path === f.layout.configPath)).toBe(
    true,
  );
  expect(
    preview.files.some((file) => file.path === redirected.configPath),
  ).toBe(false);
  const installed = await f.client.install(redirected, preview.fingerprint);
  await expect(readFile(redirected.configPath)).rejects.toMatchObject({
    code: "ENOENT",
  });
  const receipt = installed.permission.receipt;
  if (!receipt) throw Error("fixture missing receipt");
  await f.client.setPermission(f.layout, {
    installationId: receipt.installationId,
    expectedRevision: receipt.revision,
    allowedTools: ["om_recall"],
    allowedScopes: ["all-apps"],
    userConfirmed: true,
  });
  const credential = await readHostCredentialFile(
    f.owner.runtimeFor(f.layout).credentialFile,
  );
  await expect(
    f.owner.hostGrantStore.authenticate(credential),
  ).resolves.toMatchObject({ hostId: "codex", status: "approved" });
  const receipts = await f.owner.hostGrantStore.listInstallations();
  const before = [...f.dispatched];
  for (const input of [
    { hostId: "codex", operation: "getPermission", args: [] },
    {
      hostId: "generic-local-mcp",
      operation: "prepareGenericLocalConnection",
      args: [],
    },
  ]) {
    const response = await axios.post(
      `http://127.0.0.1:${f.broker.port}/control/connections`,
      { schema_version: 1, instance_id: INSTANCE, action: "host", input },
      {
        headers: { Authorization: `Bearer ${credential}` },
        proxy: false,
        maxRedirects: 0,
        timeout: 2000,
        validateStatus: () => true,
      },
    );
    expect(response.status).toBe(403);
    expect(JSON.stringify(response.data)).not.toContain(credential);
    expect(f.dispatched).toEqual(before);
  }
  expect(await f.owner.hostGrantStore.listInstallations()).toEqual(receipts);
  expect(receipts).toMatchObject([{ hostId: "codex" }]);
  expect(f.open).not.toHaveBeenCalled();
});

it("remote install refuses a preview made stale by an unrelated real Codex config edit", async () => {
  const f = await fixture();
  const preview = await f.client.previewInstall(f.layout);
  const changed = `${f.original}approval_policy = "untrusted"\n`;
  await writeFile(f.layout.configPath as string, changed, { mode: 0o600 });
  await expect(
    f.client.install(f.layout, preview.fingerprint),
  ).rejects.toThrow();
  expect(await readFile(f.layout.configPath as string, "utf8")).toBe(changed);
  expect(await f.owner.hostGrantStore.listInstallations()).toEqual([]);
  expect(await f.client.getPermission(f.layout)).toHaveProperty(
    "state",
    "not-installed",
  );
  expect(f.open).not.toHaveBeenCalled();
});

it("missing Runtime discovery entry rejects remote installation before dispatch", async () => {
  const f = await fixture();
  const preview = await f.client.previewInstall(f.layout);
  const before = [...f.dispatched];
  await rm(path.join(f.dataDir, "runtime-service.json"));
  await expect(f.client.install(f.layout, preview.fingerprint)).rejects.toThrow(
    "connection_client_runtime_unavailable",
  );
  expect(f.dispatched).toEqual(before);
  expect(await readFile(f.layout.configPath as string, "utf8")).toBe(
    f.original,
  );
  expect(await f.owner.hostGrantStore.listInstallations()).toEqual([]);
  expect(f.open).not.toHaveBeenCalled();
});

it("tells the caller a host that cannot hold a revocable credential is unsupported instead of reporting the transport down", async () => {
  const f = await fixture();
  vi.spyOn(f.owner, "previewInstall").mockRejectedValueOnce(
    Error("host_permission_transport_unsupported"),
  );
  await expect(f.client.previewInstall(f.layout)).rejects.toThrow(
    "host_permission_transport_unsupported",
  );
  expect(f.dispatched).toContain("host:previewInstall");
  expect(await f.owner.hostGrantStore.listInstallations()).toEqual([]);
});

it("an approved host refuses uninstall when user-edited files would be removed", async () => {
  const f = await fixture();
  await rm(f.layout.configPath as string);
  const preview = await f.client.previewInstall(f.layout);
  const installed = await f.client.install(f.layout, preview.fingerprint);
  const receipt = installed.permission.receipt;
  if (!receipt) throw Error("fixture missing receipt");
  const approved = await f.client.setPermission(f.layout, {
    installationId: receipt.installationId,
    expectedRevision: receipt.revision,
    allowedTools: ["om_recall"],
    allowedScopes: ["all-apps"],
    userConfirmed: true,
  });
  const secret = await readHostCredentialFile(
    f.owner.runtimeFor(f.layout).credentialFile,
  );
  await expect(
    f.owner.hostGrantStore.authenticate(secret),
  ).resolves.toMatchObject({ status: "approved" });
  const config = f.layout.configPath as string;
  await writeFile(config, `${await readFile(config, "utf8")}\n# user edit\n`);
  await expect(f.client.previewUninstall(f.layout)).rejects.toThrow(
    "host_uninstall_modified_files",
  );
  if (!approved.receipt) throw Error("fixture missing approval");
  const revoked = await f.client.revokePermission(f.layout, {
    installationId: approved.receipt.installationId,
    expectedRevision: approved.receipt.revision,
    userConfirmed: true,
  });
  expect(revoked.state).toBe("revoked");
  await expect(
    f.owner.hostGrantStore.authenticate(secret),
  ).rejects.toMatchObject({ code: "host_grant_authentication_failed" });
  expect(await readFile(config, "utf8")).toContain("# user edit");
});

it("preserves the Hook removal recovery instruction across the authenticated transport", async () => {
  const f = await fixture();
  expect(await f.client.getPermission(f.layout)).toHaveProperty(
    "state",
    "not-installed",
  );
  // The existing native guard's fixed failure is injected; all transport layers are real.
  vi.spyOn(f.owner, "previewInstall").mockRejectedValueOnce(
    Error("sync_hook_remove_first"),
  );
  await expect(f.client.previewInstall(f.layout)).rejects.toThrow(
    "sync_hook_remove_first",
  );
  expect(f.dispatched).toContain("host:previewInstall");
  expect(await f.owner.hostGrantStore.listInstallations()).toEqual([]);
  expect(await readFile(f.layout.configPath as string, "utf8")).toBe(
    f.original,
  );
});

it("Pi links through the recall command: the skill carries its own credential, approval lets it recall, turning it off makes the credential fail", async () => {
  const f = await fixture();
  expect(await f.client.getPermission(f.piHost)).toMatchObject({
    state: "not-installed",
    receipt: null,
  });
  const preview = await f.client.previewInstall(f.piHost);
  const installed = await f.client.install(f.piHost, preview.fingerprint);
  expect(installed.permission.state).toBe("install-only");
  const credentialFile = f.owner.runtimeFor(f.piHost).credentialFile;
  const skill = await readFile(f.piHost.skillPath as string, "utf8");
  expect(skill).toContain(
    `ONE_MEMORY_HOST_CREDENTIAL_FILE="${credentialFile}"`,
  );
  expect(skill).toContain("missing-recall-cli.cjs");
  const secret = await readHostCredentialFile(credentialFile);
  expect(skill).not.toContain(secret);
  await expect(
    f.owner.hostGrantStore.authenticate(secret),
  ).rejects.toMatchObject({ code: "host_grant_not_approved" });
  const receipt = installed.permission.receipt;
  if (!receipt) throw Error("fixture missing receipt");
  const approved = await f.client.setPermission(f.piHost, {
    installationId: receipt.installationId,
    expectedRevision: receipt.revision,
    allowedTools: ["om_recall"],
    allowedScopes: ["all-apps"],
    userConfirmed: true,
  });
  expect(approved.state).toBe("approved");
  await expect(
    f.owner.hostGrantStore.authenticate(secret),
  ).resolves.toMatchObject({ status: "approved" });
  if (!approved.receipt) throw Error("fixture missing approved receipt");
  await f.client.revokePermission(f.piHost, {
    installationId: approved.receipt.installationId,
    expectedRevision: approved.receipt.revision,
    userConfirmed: true,
  });
  await expect(
    f.owner.hostGrantStore.authenticate(secret),
  ).rejects.toMatchObject({ code: "host_grant_authentication_failed" });
  await expect(
    readFile(f.piHost.skillPath as string, "utf8"),
  ).resolves.toContain("missing-recall-cli.cjs");
  expect(await f.client.getPermission(f.piHost)).toHaveProperty(
    "state",
    "revoked",
  );
});

it("previews and applies host uninstall through the authenticated transport", async () => {
  const f = await fixture();
  const previewInstall = await f.client.previewInstall(f.piHost);
  await f.client.install(f.piHost, previewInstall.fingerprint);

  const previewUninstall = await f.client.previewUninstall(f.piHost);
  expect(previewUninstall.files.length).toBeGreaterThan(0);
  const removed = await f.client.uninstall(
    f.piHost,
    previewUninstall.fingerprint,
  );

  expect(removed).toMatchObject({ hostId: "pi", installed: false });
  await expect(
    readFile(f.piHost.skillPath as string, "utf8"),
  ).rejects.toThrow();
  expect(await f.client.getPermission(f.piHost)).toHaveProperty(
    "state",
    "revoked",
  );
  expect(f.dispatched).toContain("host:previewUninstall");
  expect(f.dispatched).toContain("host:uninstall");
});

it("revoking Pi without removing its skill stops the credential the skill carries", async () => {
  const f = await fixture();
  const preview = await f.client.previewInstall(f.piHost);
  const installed = await f.client.install(f.piHost, preview.fingerprint);
  const receipt = installed.permission.receipt;
  if (!receipt) throw Error("fixture missing receipt");
  const approved = await f.client.setPermission(f.piHost, {
    installationId: receipt.installationId,
    expectedRevision: receipt.revision,
    allowedTools: ["om_recall"],
    allowedScopes: ["all-apps"],
    userConfirmed: true,
  });
  const secret = await readHostCredentialFile(
    f.owner.runtimeFor(f.piHost).credentialFile,
  );
  await expect(
    f.owner.hostGrantStore.authenticate(secret),
  ).resolves.toMatchObject({ status: "approved" });
  if (!approved.receipt) throw Error("fixture missing approval");
  await f.client.revokePermission(f.piHost, {
    installationId: approved.receipt.installationId,
    expectedRevision: approved.receipt.revision,
    userConfirmed: true,
  });
  await expect(
    f.owner.hostGrantStore.authenticate(secret),
  ).rejects.toMatchObject({ code: "host_grant_authentication_failed" });
});
