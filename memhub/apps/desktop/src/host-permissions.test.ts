import { createHash } from "node:crypto";
import {
  chmod,
  lstat,
  mkdir,
  mkdtemp,
  readdir,
  readFile,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import {
  HostGrantStore,
  readHostCredentialFile,
} from "@one-memory/host-bridge";
import {
  applyRecallInstall,
  claudeCodeLayout,
  codexLayout,
  cursorLayout,
  mcpConfigPath,
  piLayout,
  planRecallInstall,
  type RecallRuntime,
  readInstalledRecallCommand,
  workbuddyLayout,
  workbuddyScatteredLayout,
} from "@one-memory/host-bridge/installer";
import { afterEach, describe, expect, it, vi } from "vitest";
import { probeRecallInstall } from "../../../packages/host-bridge/src/recall-probe.js";
import { skillFilePath } from "../../../packages/host-bridge/src/skill-drift.js";
import { HostPermissions } from "./host-permissions";

vi.mock("../../../packages/host-bridge/src/recall-probe.js", () => ({
  probeRecallInstall: vi.fn(async () => ({
    state: "reachable_but_blind",
    detail: "isolated install-only fixture",
    probedAt: new Date(0).toISOString(),
  })),
}));

const roots: string[] = [];
afterEach(async () => {
  vi.restoreAllMocks();
  await Promise.all(
    roots.splice(0).map((root) => rm(root, { recursive: true, force: true })),
  );
});

async function fixture() {
  const root = await mkdtemp(path.join(os.tmpdir(), "om-desktop-permissions-"));
  roots.push(root);
  const dataDir = path.join(root, "data");
  await mkdir(dataDir, { mode: 0o700 });
  const runtime: RecallRuntime = {
    runtimeCommand: path.join(root, "mock-runtime"),
    mcpEntryPath: path.join(root, "mock-mcp.cjs"),
    discoveryPath: path.join(dataDir, "broker.json"),
    recallCliPath: path.join(root, "mock-cli.cjs"),
  };
  await Promise.all([
    writeFile(runtime.runtimeCommand!, "#!/bin/sh\nexit 0\n", { mode: 0o700 }),
    writeFile(runtime.mcpEntryPath, "export {};\n"),
    writeFile(runtime.recallCliPath!, "export {};\n"),
  ]);
  const layout = cursorLayout(path.join(root, "cursor"));
  await mkdir(layout.containmentRoot);
  await writeFile(
    mcpConfigPath(layout),
    JSON.stringify({ mcpServers: { neighbor: { command: "keep" } } }),
  );
  const permissions = await HostPermissions.open(dataDir, runtime);
  return { root, dataDir, runtime, layout, permissions };
}

async function install(context: Awaited<ReturnType<typeof fixture>>) {
  const preview = await context.permissions.previewInstall(context.layout);
  return context.permissions.install(context.layout, preview.fingerprint);
}

function approval(receipt: { installationId: string; revision: number }) {
  return {
    installationId: receipt.installationId,
    expectedRevision: receipt.revision,
    allowedTools: ["om_recall"] as const,
    allowedScopes: ["all-apps"],
    userConfirmed: true as const,
  };
}

function required<T>(value: T | null | undefined): T {
  if (value === null || value === undefined)
    throw new Error("missing fixture value");
  return value;
}

describe.skipIf(process.platform === "win32")(
  "desktop host permissions",
  () => {
    it("previews a stable credential path without issuing or writing anything", async () => {
      const context = await fixture();
      const before = await readFile(mcpConfigPath(context.layout), "utf8");
      const directories = await readdir(
        path.join(context.dataDir, "host-permissions"),
      );
      const preview = await context.permissions.previewInstall(context.layout);
      expect(await context.permissions.previewInstall(context.layout)).toEqual(
        preview,
      );
      expect(JSON.stringify(preview)).toContain(
        "ONE_MEMORY_HOST_CREDENTIAL_FILE",
      );
      expect(JSON.stringify(preview)).not.toContain("omhg_v1_");
      expect(
        await context.permissions.hostGrantStore.listInstallations(),
      ).toEqual([]);
      expect(await readFile(mcpConfigPath(context.layout), "utf8")).toBe(
        before,
      );
      expect(
        await readdir(path.join(context.dataDir, "host-permissions")),
      ).toEqual(directories);
      expect(
        await context.permissions.getPermission(context.layout),
      ).toMatchObject({ state: "not-installed", receipt: null });
    });

    it("requires a current preview before creating any installation identity", async () => {
      const context = await fixture();
      await expect(
        context.permissions.install(context.layout, undefined),
      ).rejects.toThrow("host_install_confirmation_required");
      const preview = await context.permissions.previewInstall(context.layout);
      await writeFile(mcpConfigPath(context.layout), '{"userChanged": true}');
      await expect(
        context.permissions.install(context.layout, preview.fingerprint),
      ).rejects.toThrow("host_install_preview_stale");
      expect(
        await context.permissions.hostGrantStore.listInstallations(),
      ).toEqual([]);
      expect(await readFile(mcpConfigPath(context.layout), "utf8")).toBe(
        '{"userChanged": true}',
      );
    });

    it("installs privately with no read grant and returns only an allowlisted receipt", async () => {
      const context = await fixture();
      const result = await install(context);
      expect(result).toMatchObject({
        installed: true,
        permission: {
          state: "install-only",
          receipt: {
            status: "install-only",
            allowedTools: [],
            allowedScopes: [],
          },
        },
      });
      const command = await readInstalledRecallCommand(context.layout);
      const file = required(command).env.ONE_MEMORY_HOST_CREDENTIAL_FILE;
      expect(file.startsWith(`${context.dataDir}${path.sep}`)).toBe(true);
      expect((await lstat(file)).mode & 0o7777).toBe(0o600);
      expect((await lstat(path.dirname(file))).mode & 0o7777).toBe(0o700);
      const secret = await readHostCredentialFile(file);
      await expect(
        context.permissions.hostGrantStore.authenticate(secret),
      ).rejects.toMatchObject({ code: "host_grant_not_approved" });
      const safe = JSON.stringify(result);
      expect(safe).not.toContain(secret);
      expect(safe).not.toContain(
        createHash("sha256").update(secret).digest("hex"),
      );
      expect(safe).not.toContain(file);
      expect(Object.keys(required(result.permission.receipt)).sort()).toEqual([
        "allowedScopes",
        "allowedTools",
        "hostId",
        "installationId",
        "revision",
        "status",
      ]);
      const reopened = await HostPermissions.open(
        context.dataDir,
        context.runtime,
      );
      expect(await reopened.getPermission(context.layout)).toEqual(
        result.permission,
      );
    });

    it("requires explicit tools/scopes and confirmation, then enforces CAS and host binding", async () => {
      const context = await fixture();
      const result = await install(context);
      const request = approval(required(result.permission.receipt));
      await expect(
        context.permissions.setPermission(context.layout, {
          ...request,
          userConfirmed: false,
        }),
      ).rejects.toThrow("host_permission_confirmation_required");
      await expect(
        context.permissions.setPermission(context.layout, {
          ...request,
          secret: "not-an-input",
        }),
      ).rejects.toThrow("host_permission_invalid_input");
      await expect(
        context.permissions.setPermission(context.layout, {
          ...request,
          allowedScopes: [],
        }),
      ).rejects.toMatchObject({ code: "host_grant_invalid_input" });
      const scoped = {
        ...request,
        allowedScopes: [`workspace:v1:${"a".repeat(64)}`],
      };
      const granted = await context.permissions.setPermission(
        context.layout,
        scoped,
      );
      expect(granted).toMatchObject({
        state: "approved",
        receipt: {
          allowedScopes: scoped.allowedScopes,
          allowedTools: ["om_recall"],
        },
      });
      const command = await readInstalledRecallCommand(context.layout);
      const secret = await readHostCredentialFile(
        required(command).env.ONE_MEMORY_HOST_CREDENTIAL_FILE,
      );
      await expect(
        context.permissions.hostGrantStore.authorize(secret, {
          tool: "om_recall",
          scopes: scoped.allowedScopes,
        }),
      ).resolves.toMatchObject({ hostId: "cursor" });
      await expect(
        context.permissions.hostGrantStore.authorize(secret, {
          tool: "om_recall",
          scopes: ["all-apps"],
        }),
      ).rejects.toMatchObject({ code: "host_grant_scope_denied" });
      await expect(
        context.permissions.setPermission(context.layout, request),
      ).rejects.toMatchObject({ code: "host_grant_revision_conflict" });
      const other = codexLayout(path.join(context.root, "codex"));
      await expect(
        context.permissions.setPermission(
          other,
          approval(required(granted.receipt)),
        ),
      ).rejects.toThrow("host_permission_installation_mismatch");
      await expect(
        context.permissions.revokePermission(context.layout, {
          installationId: request.installationId,
          expectedRevision: request.expectedRevision,
          userConfirmed: true,
        }),
      ).rejects.toMatchObject({ code: "host_grant_revision_conflict" });
      const revoked = await context.permissions.revokePermission(
        context.layout,
        {
          installationId: request.installationId,
          expectedRevision: required(granted.receipt).revision,
          userConfirmed: true,
        },
      );
      expect(revoked.state).toBe("revoked");
      await expect(
        context.permissions.hostGrantStore.authenticate(secret),
      ).rejects.toMatchObject({ code: "host_grant_authentication_failed" });

      const reapproved = await context.permissions.setPermission(
        context.layout,
        approval(required(revoked.receipt)),
      );
      expect(reapproved).toMatchObject({
        state: "approved",
        receipt: {
          hostId: context.layout.hostId,
          revision: 2,
          status: "approved",
        },
      });
      expect(reapproved.receipt?.installationId).not.toBe(
        revoked.receipt?.installationId,
      );
      await expect(
        context.permissions.hostGrantStore.authenticate(secret),
      ).rejects.toMatchObject({ code: "host_grant_authentication_failed" });
      const replacement = await readHostCredentialFile(
        required(await readInstalledRecallCommand(context.layout)).env
          .ONE_MEMORY_HOST_CREDENTIAL_FILE,
      );
      expect(replacement).not.toBe(secret);
      await expect(
        context.permissions.hostGrantStore.authenticate(replacement),
      ).resolves.toMatchObject({
        installationId: reapproved.receipt?.installationId,
        status: "approved",
      });
    });

    it("serializes competing same-host approvals, without locking other hosts", async () => {
      const context = await fixture();
      const result = await install(context);
      const request = approval(required(result.permission.receipt));
      const outcomes = await Promise.allSettled([
        context.permissions.setPermission(context.layout, request),
        context.permissions.setPermission(context.layout, request),
      ]);
      expect(
        outcomes.filter((item) => item.status === "fulfilled"),
      ).toHaveLength(1);
      expect(
        outcomes.filter((item) => item.status === "rejected"),
      ).toHaveLength(1);
      let release!: () => void;
      let entered!: () => void;
      const active = new Promise<void>((resolve) => {
        entered = resolve;
      });
      const blocked = new Promise<void>((resolve) => {
        release = resolve;
      });
      const pending = context.permissions.withHostLock(
        context.layout,
        async () => {
          entered();
          await blocked;
        },
      );
      await active;
      try {
        await expect(
          context.permissions.previewInstall(
            codexLayout(path.join(context.root, "codex")),
          ),
        ).resolves.toMatchObject({ hostId: "codex" });
      } finally {
        release();
        await pending;
      }
    });

    it.each(["approve", "revoke"] as const)(
      "returns the committed %s receipt even when the subsequent diagnostic throws",
      async (operation) => {
        const context = await fixture();
        const installed = await install(context);
        const initial = required(installed.permission.receipt);
        const current =
          operation === "revoke"
            ? required(
                (
                  await context.permissions.setPermission(
                    context.layout,
                    approval(initial),
                  )
                ).receipt,
              )
            : initial;
        const diagnosticError = new Error(
          "synthetic private diagnostic must not escape",
        );
        const journalBefore = await readFile(
          context.layout.journalPath,
          "utf8",
        );
        const diagnosticLog = vi
          .spyOn(console, "warn")
          .mockImplementation(() => {});
        vi.mocked(probeRecallInstall).mockClear();
        vi.mocked(probeRecallInstall).mockRejectedValueOnce(diagnosticError);
        const result = await (operation === "approve"
          ? context.permissions.setPermission(context.layout, approval(current))
          : context.permissions.revokePermission(context.layout, {
              installationId: current.installationId,
              expectedRevision: current.revision,
              userConfirmed: true,
            })
        )
          .then((value) => ({ value, error: null }))
          .catch((error: unknown) => ({ value: null, error }));
        const persisted = required(
          (await context.permissions.hostGrantStore.listInstallations()).find(
            (item) => item.installationId === current.installationId,
          ),
        );
        const expected = operation === "approve" ? "approved" : "revoked";
        expect(persisted).toMatchObject({
          status: expected,
          revision: current.revision + 1,
        });
        expect(
          await context.permissions.getPermission(context.layout),
        ).toMatchObject({
          state: expected,
          receipt: persisted,
        });
        expect(probeRecallInstall).toHaveBeenCalledExactlyOnceWith(
          context.layout,
        );
        expect(result.error).toBeNull();
        expect(result.value).toMatchObject({
          state: expected,
          receipt: persisted,
        });
        expect(JSON.stringify(result.value)).not.toContain(
          diagnosticError.message,
        );
        expect(await readFile(context.layout.journalPath, "utf8")).toBe(
          journalBefore,
        );
        expect(diagnosticLog).toHaveBeenCalledExactlyOnceWith(
          "host_permission_self_check_unavailable",
        );
      },
    );

    it("still refreshes the recorded diagnostic after successful approval and revocation", async () => {
      const context = await fixture();
      const installed = await install(context);
      const initial = required(installed.permission.receipt);
      const before = JSON.parse(
        await readFile(context.layout.journalPath, "utf8"),
      );
      const diagnosticLog = vi
        .spyOn(console, "warn")
        .mockImplementation(() => {});
      const probe = {
        hostId: context.layout.hostId,
        state: "wired" as const,
        discoveryPath: context.runtime.discoveryPath,
        discoveryPresent: true,
        detail: "recalled 0 memories for the self-check",
        probedAt: "2026-09-15T02:00:00Z",
      };
      vi.mocked(probeRecallInstall).mockClear();
      vi.mocked(probeRecallInstall).mockResolvedValueOnce(probe);
      const approved = await context.permissions.setPermission(
        context.layout,
        approval(initial),
      );
      expect(approved).toMatchObject({
        state: "approved",
        receipt: { revision: 2 },
      });
      const afterApproval = JSON.parse(
        await readFile(context.layout.journalPath, "utf8"),
      );
      expect(afterApproval).toEqual({
        ...before,
        self_check: {
          state: probe.state,
          detail: probe.detail,
          probed_at: probe.probedAt,
        },
      });
      vi.mocked(probeRecallInstall).mockResolvedValueOnce({
        ...probe,
        state: "reachable_but_blind",
        detail: "host_authorization_required",
        probedAt: "2026-09-15T02:01:00Z",
      });
      const revoked = await context.permissions.revokePermission(
        context.layout,
        {
          installationId: initial.installationId,
          expectedRevision: 2,
          userConfirmed: true,
        },
      );
      expect(revoked).toMatchObject({
        state: "revoked",
        receipt: { revision: 3 },
      });
      const afterRevoke = JSON.parse(
        await readFile(context.layout.journalPath, "utf8"),
      );
      expect(afterRevoke).toEqual({
        ...before,
        self_check: {
          state: "reachable_but_blind",
          detail: "host_authorization_required",
          probed_at: "2026-09-15T02:01:00Z",
        },
      });
      expect(probeRecallInstall).toHaveBeenCalledTimes(2);
      expect(diagnosticLog).not.toHaveBeenCalled();
    });

    it.each(["codex", "claude-code", "workbuddy"] as const)(
      "keeps %s install-only until exact all-apps and selected-source approval",
      async (hostId) => {
        const context = await fixture();
        const hostDir = path.join(context.root, hostId);
        const layout =
          hostId === "codex"
            ? codexLayout(hostDir)
            : hostId === "workbuddy"
              ? workbuddyLayout(hostDir)
              : claudeCodeLayout({
                  claudeDir: hostDir,
                  configPath: path.join(context.root, "claude.json"),
                });
        const preview = await context.permissions.previewInstall(layout);
        const installation = await context.permissions.install(
          layout,
          preview.fingerprint,
        );
        expect(installation.permission.state).toBe("install-only");
        const command = required(await readInstalledRecallCommand(layout));
        const secret = await readHostCredentialFile(
          command.env.ONE_MEMORY_HOST_CREDENTIAL_FILE,
        );
        await expect(
          context.permissions.hostGrantStore.authenticate(secret),
        ).rejects.toMatchObject({ code: "host_grant_not_approved" });
        const tools = ["om_recall", "om_conversations"] as const;
        const scopes = ["all-apps", "source:selected-source"];
        const granted = await context.permissions.setPermission(layout, {
          ...approval(required(installation.permission.receipt)),
          allowedTools: tools,
          allowedScopes: scopes,
        });
        expect(granted.receipt?.allowedTools).toEqual(tools);
        expect(granted.receipt?.allowedScopes).toEqual(scopes);
        await expect(
          context.permissions.hostGrantStore.authorize(secret, {
            tool: "om_conversations",
            scopes,
          }),
        ).resolves.toMatchObject({ hostId });
        await expect(
          context.permissions.hostGrantStore.authorize(secret, {
            tool: "om_conversations",
            scopes: ["all-apps", "source:unselected-source"],
          }),
        ).rejects.toMatchObject({ code: "host_grant_scope_denied" });
        const narrowed = await context.permissions.setPermission(layout, {
          ...approval(required(granted.receipt)),
          allowedScopes: ["all-apps"],
        });
        expect(narrowed.receipt?.allowedScopes).toEqual(["all-apps"]);
        expect(narrowed.receipt?.allowedTools).toEqual(["om_recall"]);
        await expect(
          context.permissions.hostGrantStore.authorize(secret, {
            tool: "om_conversations",
            scopes,
          }),
        ).rejects.toMatchObject({ code: "host_grant_tool_denied" });
      },
    );

    it("keeps failed installation explicitly install-only and refuses permission issuance", async () => {
      const context = await fixture();
      await mkdir(path.dirname(required(context.layout.skillPath)), {
        recursive: true,
      });
      await mkdir(context.layout.journalPath, { recursive: true });
      const preview = await context.permissions.previewInstall(context.layout);
      await expect(
        context.permissions.install(context.layout, preview.fingerprint),
      ).rejects.toThrow("host_install_failed");
      const permission = await context.permissions.getPermission(
        context.layout,
      );
      expect(permission).toMatchObject({
        state: "install-only",
        reason: "installation_incomplete",
        receipt: {
          status: "install-only",
          allowedScopes: [],
          allowedTools: [],
        },
      });
      await expect(
        context.permissions.setPermission(
          context.layout,
          approval(required(permission.receipt)),
        ),
      ).rejects.toThrow("host_permission_installation_incomplete");
    });

    it("marks legacy installations for explicit re-link without modifying them", async () => {
      const context = await fixture();
      await applyRecallInstall(
        await planRecallInstall(context.layout, context.runtime),
      );
      const before = await readFile(mcpConfigPath(context.layout), "utf8");
      expect(
        await context.permissions.getPermission(context.layout),
      ).toMatchObject({ state: "relink-required", receipt: null });
      await context.permissions.previewInstall(context.layout);
      await expect(
        context.permissions.previewSkillSync(context.layout),
      ).rejects.toThrow("host_permission_relink_required");
      expect(await readFile(mcpConfigPath(context.layout), "utf8")).toBe(
        before,
      );
      expect(
        await context.permissions.hostGrantStore.listInstallations(),
      ).toEqual([]);
      expect((await install(context)).permission.state).toBe("install-only");
    });

    it.each(["missing", "corrupt", "unsafe", "symlink"] as const)(
      "never resets a %s existing grant store",
      async (kind) => {
        const context = await fixture();
        const store = path.join(context.dataDir, "host-permissions", "grants");
        const file = path.join(store, "host-grants.json");
        const original = await readFile(file, "utf8");
        if (kind === "missing") await rm(file);
        if (kind === "corrupt") await writeFile(file, "corrupt store");
        if (kind === "unsafe") await chmod(file, 0o644);
        if (kind === "symlink") {
          const elsewhere = path.join(context.root, "elsewhere.json");
          await writeFile(elsewhere, original, { mode: 0o600 });
          await rm(file);
          await symlink(elsewhere, file);
        }
        await expect(
          HostPermissions.open(context.dataDir, context.runtime),
        ).rejects.toThrow();
        if (kind === "missing")
          await expect(lstat(file)).rejects.toMatchObject({ code: "ENOENT" });
        if (kind === "corrupt")
          expect(await readFile(file, "utf8")).toBe("corrupt store");
        if (kind === "unsafe")
          expect((await lstat(file)).mode & 0o777).toBe(0o644);
        if (kind === "symlink")
          expect((await lstat(file)).isSymbolicLink()).toBe(true);
      },
    );

    it("rejects a symlink or public permission root, without repairing it", async () => {
      const context = await fixture();
      const directory = path.join(context.dataDir, "host-permissions");
      await chmod(directory, 0o755);
      await expect(
        HostPermissions.open(context.dataDir, context.runtime),
      ).rejects.toThrow("host_permission_storage_unsafe");
      expect((await lstat(directory)).mode & 0o777).toBe(0o755);
    });

    it("rejects writable data-root ancestors and symlink permission directories", async () => {
      const context = await fixture();
      const writableParent = path.join(context.root, "shared-parent");
      await mkdir(path.join(writableParent, "data"), {
        recursive: true,
        mode: 0o700,
      });
      await chmod(writableParent, 0o777);
      await expect(
        HostPermissions.open(
          path.join(writableParent, "data"),
          context.runtime,
        ),
      ).rejects.toThrow("host_permission_storage_unsafe");
      const aliasData = path.join(context.root, "alias-data");
      await mkdir(aliasData, { mode: 0o700 });
      await symlink(
        path.join(context.dataDir, "host-permissions"),
        path.join(aliasData, "host-permissions"),
      );
      await expect(
        HostPermissions.open(aliasData, context.runtime),
      ).rejects.toThrow("host_permission_storage_unsafe");
      expect(
        (
          await lstat(path.join(aliasData, "host-permissions"))
        ).isSymbolicLink(),
      ).toBe(true);
    });

    it("reports live store corruption as unavailable and never repairs it on preview", async () => {
      const context = await fixture();
      await install(context);
      const file = path.join(
        context.dataDir,
        "host-permissions",
        "grants",
        "host-grants.json",
      );
      await writeFile(file, "corrupt store sentinel");
      expect(await context.permissions.getPermission(context.layout)).toEqual({
        hostId: "cursor",
        state: "unavailable",
        reason: "permission_store_unavailable",
        receipt: null,
      });
      await expect(
        context.permissions.previewInstall(context.layout),
      ).rejects.toMatchObject({ code: "host_grant_store_corrupt" });
      expect(await readFile(file, "utf8")).toBe("corrupt store sentinel");
    });

    it("does not approve a missing, changed or foreign credential file", async () => {
      const context = await fixture();
      const installed = await install(context);
      const command = await readInstalledRecallCommand(context.layout);
      const file = required(command).env.ONE_MEMORY_HOST_CREDENTIAL_FILE;
      const other = await context.permissions.hostGrantStore.createInstallation(
        { hostId: "codex" },
      );
      await writeFile(file, other.secret);
      expect(
        (await context.permissions.getPermission(context.layout)).state,
      ).toBe("relink-required");
      await expect(
        context.permissions.setPermission(
          context.layout,
          approval(required(installed.permission.receipt)),
        ),
      ).rejects.toThrow("host_permission_relink_required");
      expect(
        (await context.permissions.hostGrantStore.listInstallations()).every(
          (receipt) => receipt.status === "install-only",
        ),
      ).toBe(true);
    });

    it("re-link creates a new install-only identity at the same path and retires old rights", async () => {
      const context = await fixture();
      const first = await install(context);
      await context.permissions.setPermission(
        context.layout,
        approval(required(first.permission.receipt)),
      );
      const command = await readInstalledRecallCommand(context.layout);
      const file = required(command).env.ONE_MEMORY_HOST_CREDENTIAL_FILE;
      const oldSecret = await readHostCredentialFile(file);
      const second = await install(context);
      expect(required(second.permission.receipt).installationId).not.toBe(
        required(first.permission.receipt).installationId,
      );
      expect(second.permission.state).toBe("install-only");
      expect(
        (await readInstalledRecallCommand(context.layout))?.env
          .ONE_MEMORY_HOST_CREDENTIAL_FILE,
      ).toBe(file);
      await expect(
        context.permissions.hostGrantStore.authenticate(oldSecret),
      ).rejects.toThrow();
    });

    it("revokes only the managed identity on uninstall and leaves neighbors and source history", async () => {
      const context = await fixture();
      const installed = await install(context);
      await context.permissions.setPermission(
        context.layout,
        approval(required(installed.permission.receipt)),
      );
      const independent =
        await context.permissions.hostGrantStore.createInstallation({
          hostId: "cursor",
        });
      const history = path.join(context.dataDir, "source-history.fixture");
      await writeFile(history, "isolated history sentinel");
      const config = JSON.parse(
        await readFile(mcpConfigPath(context.layout), "utf8"),
      );
      config.newUserSetting = true;
      await writeFile(mcpConfigPath(context.layout), JSON.stringify(config));
      const preview = await context.permissions.previewUninstall(
        context.layout,
      );
      const result = await context.permissions.uninstall(
        context.layout,
        preview.fingerprint,
      );
      expect(result).toMatchObject({
        installed: false,
        permission: { state: "revoked" },
      });
      expect(
        JSON.parse(await readFile(mcpConfigPath(context.layout), "utf8")),
      ).toEqual({
        mcpServers: { neighbor: { command: "keep" } },
        newUserSetting: true,
      });
      expect(await readFile(history, "utf8")).toBe("isolated history sentinel");
      expect(
        (await context.permissions.hostGrantStore.listInstallations()).find(
          (entry) =>
            entry.installationId === independent.receipt.installationId,
        )?.status,
      ).toBe("install-only");
    });

    it("preserves a changed config when the uninstall fingerprint is stale", async () => {
      const context = await fixture();
      const result = await install(context);
      const granted = await context.permissions.setPermission(
        context.layout,
        approval(required(result.permission.receipt)),
      );
      const preview = await context.permissions.previewUninstall(
        context.layout,
      );
      const config = JSON.parse(
        await readFile(mcpConfigPath(context.layout), "utf8"),
      );
      config.afterPreview = "keep";
      const raw = JSON.stringify(config);
      await writeFile(mcpConfigPath(context.layout), raw);
      await expect(
        context.permissions.uninstall(context.layout, preview.fingerprint),
      ).rejects.toThrow("host_uninstall_preview_stale");
      expect(await readFile(mcpConfigPath(context.layout), "utf8")).toBe(raw);
      expect(await context.permissions.getPermission(context.layout)).toEqual(
        granted,
      );
    });

    it("revokes a confirmed uninstall before applying the native removal", async () => {
      const context = await fixture();
      const installed = await install(context);
      const granted = await context.permissions.setPermission(
        context.layout,
        approval(required(installed.permission.receipt)),
      );
      const preview = await context.permissions.previewUninstall(
        context.layout,
      );
      const revoke = context.permissions.hostGrantStore.revoke.bind(
        context.permissions.hostGrantStore,
      );
      vi.spyOn(context.permissions.hostGrantStore, "revoke").mockImplementation(
        async (request) => {
          const receipt = await revoke(request);
          const config = JSON.parse(
            await readFile(mcpConfigPath(context.layout), "utf8"),
          );
          config.racedAfterRevocation = "preserve";
          await writeFile(
            mcpConfigPath(context.layout),
            JSON.stringify(config),
          );
          return receipt;
        },
      );
      await expect(
        context.permissions.uninstall(context.layout, preview.fingerprint),
      ).rejects.toThrow("host_uninstall_preview_stale");
      expect(
        (await context.permissions.getPermission(context.layout)).state,
      ).toBe("revoked");
      expect(
        JSON.parse(await readFile(mcpConfigPath(context.layout), "utf8"))
          .racedAfterRevocation,
      ).toBe("preserve");
      expect(context.permissions.hostGrantStore.revoke).toHaveBeenCalledWith({
        installationId: required(granted.receipt).installationId,
        expectedRevision: required(granted.receipt).revision,
      });
    });

    it("does not delete newly added settings in a config originally created by installation", async () => {
      const context = await fixture();
      await rm(mcpConfigPath(context.layout));
      await install(context);
      const config = JSON.parse(
        await readFile(mcpConfigPath(context.layout), "utf8"),
      );
      config.newUserSetting = true;
      const changed = JSON.stringify(config);
      await writeFile(mcpConfigPath(context.layout), changed);
      await expect(
        context.permissions.previewUninstall(context.layout),
      ).rejects.toThrow("host_uninstall_modified_files");
      expect(await readFile(mcpConfigPath(context.layout), "utf8")).toBe(
        changed,
      );
    });

    it("does not adopt added user settings as deletable ownership during re-link", async () => {
      const context = await fixture();
      await rm(mcpConfigPath(context.layout));
      await install(context);
      const config = JSON.parse(
        await readFile(mcpConfigPath(context.layout), "utf8"),
      );
      config.newUserSetting = true;
      await writeFile(mcpConfigPath(context.layout), JSON.stringify(config));
      await install(context);
      await expect(
        context.permissions.previewUninstall(context.layout),
      ).rejects.toThrow("host_uninstall_modified_files");
      expect(
        JSON.parse(await readFile(mcpConfigPath(context.layout), "utf8"))
          .newUserSetting,
      ).toBe(true);
    });

    it("allows separately confirmed modified skill sync without changing credentials or grants", async () => {
      const context = await fixture();
      const result = await install(context);
      const granted = await context.permissions.setPermission(
        context.layout,
        approval(required(result.permission.receipt)),
      );
      const command = await readInstalledRecallCommand(context.layout);
      const credentialFile =
        required(command).env.ONE_MEMORY_HOST_CREDENTIAL_FILE;
      const secret = await readHostCredentialFile(credentialFile);
      const skill = `${await readFile(required(context.layout.skillPath), "utf8")}\nUser addition\n`;
      await writeFile(required(context.layout.skillPath), skill);
      const preview = await context.permissions.previewSkillSync(
        context.layout,
      );
      if (preview.upToDate) throw new Error("expected skill drift");
      expect(preview.requiresConfirmation).toBe(true);
      await expect(
        context.permissions.applySkillSync(
          context.layout,
          preview.fingerprint,
          false,
        ),
      ).rejects.toThrow("confirmation required");
      expect(await readFile(required(context.layout.skillPath), "utf8")).toBe(
        skill,
      );
      await context.permissions.applySkillSync(
        context.layout,
        preview.fingerprint,
        true,
      );
      expect(await context.permissions.getPermission(context.layout)).toEqual(
        granted,
      );
      expect(await readHostCredentialFile(credentialFile)).toBe(secret);
    });

    it("does not delete new files inside a managed plugin tree", async () => {
      const context = await fixture();
      const layout = workbuddyLayout(path.join(context.root, "workbuddy"));
      const preview = await context.permissions.previewInstall(layout);
      await context.permissions.install(layout, preview.fingerprint);
      const note = path.join(
        required(layout.plugin).cacheRoot,
        "user-note.txt",
      );
      await writeFile(note, "keep this added file");
      await expect(
        context.permissions.previewUninstall(layout),
      ).rejects.toThrow("host_uninstall_modified_files");
      await expect(context.permissions.previewInstall(layout)).rejects.toThrow(
        "host_install_modified_files",
      );
      expect(await readFile(note, "utf8")).toBe("keep this added file");
    });

    it("keeps installing while the host holds its own cache bookkeeping", async () => {
      const context = await fixture();
      const layout = workbuddyLayout(path.join(context.root, "workbuddy"));
      const plugin = required(layout.plugin);
      const preview = await context.permissions.previewInstall(layout);
      await context.permissions.install(layout, preview.fingerprint);
      // WorkBuddy marks a cached plugin version as in use by writing one file
      // per live process beside the manifest. No plan of ours can list those,
      // so counting them as unmanaged edits refused every re-link while the
      // host was open.
      const markers = path.join(plugin.installRoot, ".in_use");
      await mkdir(markers, { recursive: true });
      await writeFile(path.join(markers, "4242"), "");
      const relink = await context.permissions.previewInstall(layout);
      await expect(
        context.permissions.install(layout, relink.fingerprint),
      ).resolves.toMatchObject({ hostId: "workbuddy", installed: true });
      await expect(
        context.permissions.previewUninstall(layout),
      ).resolves.toMatchObject({ hostId: "workbuddy" });
      // A file a person put there is still an unmanaged edit.
      await writeFile(path.join(plugin.cacheRoot, "user-note.txt"), "keep");
      await expect(context.permissions.previewInstall(layout)).rejects.toThrow(
        "host_install_modified_files",
      );
    });

    it("reports a modified managed registry entry instead of overwriting it", async () => {
      const context = await fixture();
      const layout = workbuddyLayout(path.join(context.root, "workbuddy"));
      const plugin = required(layout.plugin);
      await mkdir(path.dirname(plugin.knownMarketplacesPath), {
        recursive: true,
      });
      await writeFile(
        plugin.knownMarketplacesPath,
        JSON.stringify({ neighbor: { installLocation: "keep" } }),
      );
      const preview = await context.permissions.previewInstall(layout);
      await context.permissions.install(layout, preview.fingerprint);
      const config = JSON.parse(
        await readFile(plugin.knownMarketplacesPath, "utf8"),
      );
      config["one-memory"].installLocation = "changed-by-user";
      const changed = JSON.stringify(config);
      await writeFile(plugin.knownMarketplacesPath, changed);
      // The value at our own key changed. Overwriting it would be a guess, so
      // uninstall schedules nothing, names the file in a review finding, and
      // refuses when the preview is applied. The bytes stay as the host left
      // them either way.
      const removal = await context.permissions.previewUninstall(layout);
      expect(removal.files).toEqual([]);
      expect((removal.review ?? []).map((finding) => finding.path)).toContain(
        plugin.knownMarketplacesPath,
      );
      await expect(
        context.permissions.uninstall(layout, removal.fingerprint),
      ).rejects.toThrow("host_uninstall_needs_review");
      expect(await readFile(plugin.knownMarketplacesPath, "utf8")).toBe(
        changed,
      );
    });

    it("refuses legacy migration that would delete an untracked host settings file", async () => {
      const context = await fixture();
      const layout = workbuddyLayout(path.join(context.root, "workbuddy"));
      const legacy = workbuddyScatteredLayout(layout.containmentRoot);
      await applyRecallInstall(
        await planRecallInstall(legacy, context.runtime),
      );
      expect(await context.permissions.getPermission(layout)).toMatchObject({
        state: "relink-required",
        reason: "legacy_installation",
        receipt: null,
      });
      const config = JSON.parse(await readFile(mcpConfigPath(legacy), "utf8"));
      config.mcpServers.neighbor = { command: "keep" };
      const changed = JSON.stringify(config);
      await writeFile(mcpConfigPath(legacy), changed);
      await expect(context.permissions.previewInstall(layout)).rejects.toThrow(
        "host_install_modified_files",
      );
      expect(await readFile(mcpConfigPath(legacy), "utf8")).toBe(changed);
      expect(
        await context.permissions.hostGrantStore.listInstallations(),
      ).toEqual([]);
    });

    it("links CLI-only hosts with a credential their recall command carries", async () => {
      const context = await fixture();
      const layout = piLayout(path.join(context.root, "pi"));
      await mkdir(layout.containmentRoot, { recursive: true, mode: 0o700 });
      expect((await context.permissions.getPermission(layout)).state).toBe(
        "not-installed",
      );
      const preview = await context.permissions.previewInstall(layout);
      expect(
        await context.permissions.hostGrantStore.listInstallations(),
      ).toEqual([]);
      const installed = await context.permissions.install(
        layout,
        preview.fingerprint,
      );
      expect(installed.permission.state).toBe("install-only");
      expect(await readFile(layout.skillPath as string, "utf8")).toContain(
        context.permissions.runtimeFor(layout).credentialFile,
      );
    });

    it("turns off a host that is already off as a no-op success", async () => {
      const context = await fixture();
      await install(context);
      const first = await context.permissions.previewUninstall(context.layout);
      await context.permissions.uninstall(context.layout, first.fingerprint);
      const config = await readFile(mcpConfigPath(context.layout), "utf8");
      const installations =
        await context.permissions.hostGrantStore.listInstallations();

      const again = await context.permissions.previewUninstall(context.layout);
      expect(again.files).toEqual([]);
      const result = await context.permissions.uninstall(
        context.layout,
        again.fingerprint,
      );
      expect(result).toMatchObject({ installed: false });
      expect(result.permission.state).not.toBe("approved");
      expect(await readFile(mcpConfigPath(context.layout), "utf8")).toBe(
        config,
      );
      expect(
        await context.permissions.hostGrantStore.listInstallations(),
      ).toEqual(installations);
    });

    it("turns off a never-installed host even when the user keeps hooks of their own", async () => {
      const context = await fixture();
      const hooks = path.join(context.layout.containmentRoot, "hooks.json");
      const own = JSON.stringify({
        version: 1,
        hooks: { stop: [{ command: "echo mine" }] },
      });
      await writeFile(hooks, own);
      const preview = await context.permissions.previewUninstall(
        context.layout,
      );
      expect(preview.files).toEqual([]);
      expect(await readFile(hooks, "utf8")).toBe(own);
    });

    it("turns off a host that was never installed without writing anything", async () => {
      const context = await fixture();
      const config = await readFile(mcpConfigPath(context.layout), "utf8");
      const preview = await context.permissions.previewUninstall(
        context.layout,
      );
      expect(preview.files).toEqual([]);
      await expect(
        context.permissions.uninstall(context.layout, preview.fingerprint),
      ).resolves.toMatchObject({
        installed: false,
        permission: { state: "not-installed" },
      });
      expect(await readFile(mcpConfigPath(context.layout), "utf8")).toBe(
        config,
      );
      expect(
        await context.permissions.hostGrantStore.listInstallations(),
      ).toEqual([]);
    });

    it("revokes a leftover grant when turning off a host whose files are gone", async () => {
      const context = await fixture();
      const original = await readFile(mcpConfigPath(context.layout), "utf8");
      const installed = await install(context);
      await context.permissions.setPermission(
        context.layout,
        approval(required(installed.permission.receipt)),
      );
      await writeFile(mcpConfigPath(context.layout), original);
      if (context.layout.skillPath !== null)
        await rm(context.layout.skillPath, { force: true });
      await rm(path.join(context.layout.containmentRoot, "hooks.json"), {
        force: true,
      });
      await rm(context.layout.journalPath, { force: true });
      const preview = await context.permissions.previewUninstall(
        context.layout,
      );
      expect(preview.files).toEqual([]);
      const result = await context.permissions.uninstall(
        context.layout,
        preview.fingerprint,
      );
      expect(result.installed).toBe(false);
      expect(result.permission.state).toBe("revoked");
      expect(await readFile(mcpConfigPath(context.layout), "utf8")).toBe(
        original,
      );
    });

    it.each([
      "entry",
      "skill",
      "capture hook",
      "JSON-escaped capture hook",
      "capture hook spelled with other whitespace",
    ] as const)(
      "does not call a host off while its %s is still there without a journal",
      async (left) => {
        const context = await fixture();
        const original = await readFile(mcpConfigPath(context.layout), "utf8");
        await install(context);
        const skill = required(context.layout.skillPath);
        const hooks = path.join(context.layout.containmentRoot, "hooks.json");
        expect(await readFile(hooks, "utf8")).toContain("capture cursor");
        if (left !== "entry")
          await writeFile(mcpConfigPath(context.layout), original);
        if (left !== "skill") await rm(skill, { force: true });
        if (left === "capture hook spelled with other whitespace") {
          const raw = await readFile(hooks, "utf8");
          await writeFile(
            hooks,
            raw.replaceAll("capture cursor", "capture  cursor"),
          );
          expect(await readFile(hooks, "utf8")).not.toContain("capture cursor");
        } else if (left === "JSON-escaped capture hook") {
          const raw = await readFile(hooks, "utf8");
          await writeFile(
            hooks,
            raw.replaceAll("capture cursor", "capture\\u0020cursor"),
          );
          expect(await readFile(hooks, "utf8")).not.toContain("capture cursor");
        } else if (left !== "capture hook") await rm(hooks, { force: true });
        await rm(context.layout.journalPath, { force: true });
        const config = await readFile(mcpConfigPath(context.layout), "utf8");
        await expect(
          context.permissions.previewUninstall(context.layout),
        ).rejects.toThrow();
        expect(await readFile(mcpConfigPath(context.layout), "utf8")).toBe(
          config,
        );
      },
    );

    it("asks for a person instead of calling a host off when a capture command it cannot place is there", async () => {
      const context = await fixture();
      const hooks = path.join(context.layout.containmentRoot, "hooks.json");
      const foreign = JSON.stringify({
        version: 1,
        hooks: {
          stop: [
            { command: "echo mine" },
            { command: "sh -c '/elsewhere/one-memory capture cursor'" },
          ],
        },
      });
      await writeFile(hooks, foreign);
      const preview = await context.permissions.previewUninstall(
        context.layout,
      );
      expect(preview.files).toEqual([]);
      expect(preview.review).toEqual([
        { path: hooks, where: "hooks.stop[1].command" },
      ]);
      await expect(
        context.permissions.uninstall(context.layout, preview.fingerprint),
      ).rejects.toThrow("host_uninstall_needs_review");
      expect(await readFile(hooks, "utf8")).toBe(foreign);
    });

    it("asks for a person when a hook file it has to read cannot be parsed", async () => {
      const context = await fixture();
      const hooks = path.join(context.layout.containmentRoot, "hooks.json");
      await writeFile(hooks, "{ not json");
      const preview = await context.permissions.previewUninstall(
        context.layout,
      );
      expect(preview.files).toEqual([]);
      expect(preview.review).toEqual([
        { path: hooks, where: expect.stringContaining("JSON") },
      ]);
    });

    it("looks inside Claude Code's nested hook groups", async () => {
      const context = await fixture();
      const claude = path.join(context.root, "claude");
      await mkdir(claude);
      const layout = claudeCodeLayout({
        claudeDir: claude,
        configPath: path.join(context.root, "claude.json"),
      });
      const settings = path.join(claude, "settings.json");
      await writeFile(
        settings,
        JSON.stringify({
          hooks: {
            Stop: [
              {
                matcher: "",
                hooks: [
                  {
                    type: "command",
                    command: "/elsewhere/one-memory capture claude-code",
                  },
                ],
              },
            ],
          },
        }),
      );
      const preview = await context.permissions.previewUninstall(layout);
      expect(preview.review).toEqual([
        { path: settings, where: "hooks.Stop[0].hooks[0].command" },
      ]);
    });

    it("does not call WorkBuddy off while only its registry entries are left", async () => {
      const context = await fixture();
      const layout = workbuddyLayout(path.join(context.root, "workbuddy"));
      const preview = await context.permissions.previewInstall(layout);
      await context.permissions.install(layout, preview.fingerprint);
      const plugin = required(layout.plugin);
      const registries = [
        plugin.knownMarketplacesPath,
        plugin.installedPluginsPath,
      ];
      for (const file of registries)
        expect(await readFile(file, "utf8")).toContain(plugin.marketplaceName);
      await rm(plugin.marketplaceRoot, { recursive: true, force: true });
      await rm(plugin.cacheRoot, { recursive: true, force: true });
      await rm(layout.journalPath, { force: true });
      const kept = await Promise.all(
        registries.map((file) => readFile(file, "utf8")),
      );
      await expect(
        context.permissions.previewUninstall(layout),
      ).rejects.toThrow();
      expect(
        await Promise.all(registries.map((file) => readFile(file, "utf8"))),
      ).toEqual(kept);
    });

    it("asks for a person when all that is left of WorkBuddy is the enablement flag", async () => {
      const context = await fixture();
      const layout = workbuddyLayout(path.join(context.root, "workbuddy"));
      const plugin = required(layout.plugin);
      const id = `${plugin.pluginName}@${plugin.marketplaceName}`;
      await mkdir(path.dirname(plugin.settingsPath), { recursive: true });
      await writeFile(
        plugin.settingsPath,
        JSON.stringify({ enabledPlugins: { [id]: true } }),
      );
      const preview = await context.permissions.previewUninstall(layout);
      expect(preview.files).toEqual([]);
      expect(preview.review).toEqual([
        { path: plugin.settingsPath, where: `enabledPlugins.${id}` },
      ]);
    });

    it("opens the very same trusted store supplied to the broker", async () => {
      const context = await fixture();
      await install(context);
      const stored = await HostGrantStore.open(
        path.join(context.dataDir, "host-permissions", "grants"),
      );
      expect(await stored.listInstallations()).toEqual(
        await context.permissions.hostGrantStore.listInstallations(),
      );
    });
  },
);

describe("upgrading installs left by an earlier release", () => {
  async function ageSkill(layout: ReturnType<typeof workbuddyLayout>) {
    const skillPath = required(skillFilePath(layout));
    const aged = (await readFile(skillPath, "utf8")).replace(
      /skill-version=\d+/,
      "skill-version=12",
    );
    await writeFile(skillPath, aged);
    const journal = JSON.parse(await readFile(layout.journalPath, "utf8"));
    journal.installed_skill_hash = createHash("sha256")
      .update(aged)
      .digest("hex");
    await writeFile(layout.journalPath, JSON.stringify(journal));
    return aged;
  }

  it("rewrites call rules that are still exactly what an earlier release wrote", async () => {
    const context = await fixture();
    const layout = workbuddyLayout(path.join(context.root, "workbuddy"));
    const preview = await context.permissions.previewInstall(layout);
    const result = await context.permissions.install(
      layout,
      preview.fingerprint,
    );
    const granted = await context.permissions.setPermission(
      layout,
      approval(required(result.permission.receipt)),
    );
    const current = await readFile(required(skillFilePath(layout)), "utf8");
    await ageSkill(layout);

    expect(await context.permissions.syncStaleInstalls([layout])).toEqual([
      layout.hostId,
    ]);
    expect(await readFile(required(skillFilePath(layout)), "utf8")).toBe(
      current,
    );
    expect(await context.permissions.getPermission(layout)).toEqual(granted);
  });

  it("leaves call rules the user edited for the detail page to confirm", async () => {
    const context = await fixture();
    const layout = workbuddyLayout(path.join(context.root, "workbuddy"));
    const preview = await context.permissions.previewInstall(layout);
    await context.permissions.install(layout, preview.fingerprint);
    const edited = `${await ageSkill(layout)}\nUser addition\n`;
    await writeFile(required(skillFilePath(layout)), edited);

    expect(await context.permissions.syncStaleInstalls([layout])).toEqual([]);
    expect(await readFile(required(skillFilePath(layout)), "utf8")).toBe(
      edited,
    );
  });
});
