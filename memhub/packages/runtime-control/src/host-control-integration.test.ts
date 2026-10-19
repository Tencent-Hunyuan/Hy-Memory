import {
  chmod,
  mkdir,
  mkdtemp,
  readdir,
  readFile,
  realpath,
  rm,
  writeFile,
} from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import path from "node:path";
import { RecallBroker, readHostCredentialFile } from "@one-memory/host-bridge";
import {
  codexLayout,
  deepseekHarnessLayout,
  piLayout,
  type RecallInstallPreview,
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

it("accepts a profile-scoped DeepSeek Harness host id instead of calling the transport unsupported", async () => {
  // A Harness profile is configured by one `cordis.patch.yml`, so the host ID
  // carries the profile (`deepseek-harness:<profile>`). The installation
  // directory is a digest of that ID, so the colon is a label, not a path, and
  // the permission layer has to read it as one. Before this, `supported()`
  // rejected every Harness profile and `host install` told the caller its
  // transport was unsupported.
  const f = await fixture();
  const dshHome = path.join(f.root, ".dsh");
  const profileDirectory = path.join(dshHome, "profiles", "desktop");
  await mkdir(profileDirectory, { recursive: true, mode: 0o700 });
  await chmod(dshHome, 0o700);
  await writeFile(path.join(profileDirectory, "cordis.yml"), "[]\n", {
    mode: 0o600,
  });
  await writeFile(path.join(profileDirectory, "cordis.patch.yml"), "[]\n", {
    mode: 0o600,
  });
  const layout = {
    ...deepseekHarnessLayout({ dshHome, profile: "desktop" }),
    hostId: "deepseek-harness:desktop",
    hostLabel: "DeepSeek Harness · desktop",
  };

  expect(await f.owner.getPermission(layout)).toMatchObject({
    state: "not-installed",
    receipt: null,
  });
  const preview: RecallInstallPreview = await f.owner.previewInstall(layout);
  expect(preview.hostId).toBe("deepseek-harness:desktop");
  expect(preview.fingerprint).toMatch(/^[a-f0-9]{64}$/);
  const patchFile = preview.files[0];
  if (!patchFile?.hunk) throw Error("preview missing patch hunk");
  const added = patchFile.hunk.added.join("\n");
  expect(added).toContain("one-memory-recall");
  expect(added).toContain("'@deepseek-ai/dsh-mcp-client'");
  if (layout.configPath === null) throw Error("layout missing config path");
  expect(await readFile(layout.configPath, "utf8")).toBe("[]\n");
  expect(await f.owner.hostGrantStore.listInstallations()).toEqual([]);
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
  await f.client.install(redirected, preview.fingerprint);
  await expect(readFile(redirected.configPath)).rejects.toMatchObject({
    code: "ENOENT",
  });
  const credential = await readHostCredentialFile(
    f.owner.runtimeFor(f.layout).credentialFile,
  );
  const before = [...f.dispatched];
  const response = await axios.post(
    `http://127.0.0.1:${f.broker.port}/control/connections`,
    {
      schema_version: 1,
      instance_id: INSTANCE,
      action: "host",
      input: { hostId: "codex", operation: "getPermission", args: [] },
    },
    {
      headers: { Authorization: `Bearer ${credential}` },
      proxy: false,
      maxRedirects: 0,
      timeout: 2000,
      validateStatus: () => true,
    },
  );
  expect(response.status).toBe(403);
  expect(f.dispatched).toEqual(before);
  expect(JSON.stringify(response.data)).not.toContain(credential);
  expect(await f.owner.hostGrantStore.listInstallations()).toHaveLength(1);
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
