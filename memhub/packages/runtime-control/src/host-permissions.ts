import { createHash, randomUUID } from "node:crypto";
import { constants, type Stats } from "node:fs";
import {
  lstat,
  mkdir,
  open,
  readdir,
  realpath,
  rename,
  unlink,
} from "node:fs/promises";
import path from "node:path";
import { isHostId } from "@one-memory/contracts";
import {
  createClaudeCodeRemovalTarget,
  createClaudeCodeTarget,
  HostGrantError,
  type HostGrantReceipt,
  HostGrantStore,
  type HostPairingReceipt,
  type HostReadTool,
  isPluginCacheBookkeeping,
  readHostCredentialFile,
} from "@one-memory/host-bridge";
import {
  applyRecallInstall,
  applyRecallUninstall,
  hostHealthCheck,
  inspectRemnants,
  planRecallInstall,
  planRecallUninstall,
  planSkillSync,
  previewRecallInstall,
  previewRecallUninstall,
  type RecallHostLayout,
  type RecallInstallPlan,
  type RecallInstallPreview,
  type RecallRuntime,
  type RecallUninstallPlan,
  readInstalledRecallCommand,
  recordInstallSelfCheck,
  repairInstalledRecallCommands,
  verifyRecallInstall,
} from "@one-memory/host-bridge/installer";
import type { DiagnosticInput, DiagnosticSink } from "./diagnostic-log.js";
import {
  hasSyncHookArtifacts,
  loadSyncHookAssets,
  orphanSyncHookIdentity,
  type SyncHookStatus,
  syncHookInput,
  syncHookPreview,
} from "./sync-hook.js";

export interface RecallPermissionStatus {
  hostId: string;
  state:
    | "not-installed"
    | "install-only"
    | "approved"
    | "revoked"
    | "relink-required"
    | "unavailable"
    | "unsupported";
  reason:
    | "installation_incomplete"
    | "credential_missing_or_changed"
    | "legacy_installation"
    | "managed_config_changed"
    | "permission_store_unavailable"
    | "unsupported_transport"
    | null;
  receipt: HostGrantReceipt | null;
}

export interface SetRecallPermissionInput {
  installationId: string;
  expectedRevision: number;
  allowedTools: readonly HostReadTool[];
  allowedScopes: readonly string[];
  userConfirmed: true;
}

export interface RevokeRecallPermissionInput {
  installationId: string;
  expectedRevision: number;
  userConfirmed: true;
}

interface InstallationBinding {
  schemaVersion: 1;
  hostId: string;
  installationId: string;
  credentialDigest: string;
  complete: boolean;
  /** Files originally created by us may only be deleted if still identical. */
  deletions: Array<{ path: string; digest: string }>;
}

const CREDENTIAL_ENV = "ONE_MEMORY_HOST_CREDENTIAL_FILE";
/** The build whose host refresh last completed, beside the grants. */
const HOST_REFRESH_FILE = "host-refresh.json";
const locks = new Map<string, Promise<void>>();

/** What one pass of `refreshInstalledHosts` did, by host id. */
export interface HostRefreshReport {
  /** `already_refreshed` when this build had already completed a pass. */
  state: "refreshed" | "already_refreshed";
  checked: number;
  /** Hosts whose installed command or discovery path was moved. */
  repointed: string[];
  /** Hosts whose stale skill, hooks, plugin files or MCP entry were rewritten. */
  synced: string[];
  failed: string[];
}

export const LOCAL_EXPERIENCE_UNAVAILABLE = "体验版暂不支持";

/** Trusted local Runtime control plane. Never exposed through MCP or the Core API.
 * POSIX local storage, with a trusted owner-controlled data root; same-user
 * adversaries and broader ACLs require OS-level isolation, not this store.
 */
export class HostPermissions {
  private constructor(
    private readonly dataDir: string,
    private readonly directory: string,
    private readonly directoryIdentity: Stats,
    readonly hostGrantStore: HostGrantStore,
    private readonly runtime: RecallRuntime,
    private readonly syncHookAssetsDirectory?: string,
  ) {}

  /** One plan per host, the last one previewed, for install to approve by
   * fingerprint. See `approvedPlan`.
   *
   * Lives for the length of one settings-dialog session: a preview and its
   * confirm happen in the same process, and a restart re-previews rather
   * than approving an old fingerprint. Note the constraint this exists
   * under — previewing a plugin host twice is not byte-stable (each plan
   * mints its own record timestamps), so callers approve the one preview
   * they showed, never re-preview in between. */
  private readonly previewedPlans = new Map<string, RecallInstallPlan>();

  static async open(
    dataDir: string,
    runtime: RecallRuntime,
    options: { syncHookAssetsDirectory?: string } = {},
  ): Promise<HostPermissions> {
    if (process.platform === "win32" || typeof process.getuid !== "function") {
      throw new Error("host_permission_platform_unsupported");
    }
    dataDir = path.resolve(dataDir);
    return storageGuard(async () => {
      await checkDataRoot(dataDir);
      const directory = path.join(dataDir, "host-permissions");
      const created = await createPrivateDirectory(directory);
      // An existing but incomplete root is recovery work, never first setup.
      const grants = path.join(directory, "grants");
      const store = created
        ? await HostGrantStore.initialize(grants)
        : await HostGrantStore.open(grants);
      const installations = path.join(directory, "installations");
      if (created) await createPrivateDirectory(installations);
      checkPrivate(await lstat(installations), true);
      return new HostPermissions(
        dataDir,
        directory,
        await lstat(directory),
        store,
        { ...runtime },
        options.syncHookAssetsDirectory,
      );
    });
  }

  runtimeFor(
    layout: RecallHostLayout,
  ): RecallRuntime & { credentialFile: string } {
    return {
      ...this.runtime,
      credentialFile: path.join(this.hostDirectory(layout), "credential"),
    };
  }

  createCustomPairing(displayName: string): Promise<HostPairingReceipt> {
    return this.hostGrantStore.createPairing({ displayName });
  }

  /** Whether the host's entry was installed from this data directory.
   *
   * Several data directories can share one set of hosts (the daily app, a
   * trial build, a development checkout). An entry naming another directory's
   * credential belongs to that installation, so nothing done here without the
   * user confirming a preview may write it: that would move the host here onto
   * a credential this directory never issued. Moving it is an explicit install.
   */
  private async ownsInstalledEntry(layout: RecallHostLayout): Promise<boolean> {
    const installed = await readInstalledRecallCommand(layout);
    const credential = installed?.env[CREDENTIAL_ENV];
    return (
      credential !== undefined &&
      path.resolve(credential) ===
        path.resolve(this.runtimeFor(layout).credentialFile)
    );
  }

  /** Point hosts this directory installed that still name an old command at the runtime we install now. */
  async repairInstalledCommands(
    layouts: RecallHostLayout[],
  ): Promise<string[]> {
    const repaired: string[] = [];
    for (const layout of layouts) {
      await this.withHostLock(layout, async () => {
        if (!(await this.ownsInstalledEntry(layout))) return;
        repaired.push(
          ...(await repairInstalledRecallCommands([layout], (owned) =>
            this.runtimeFor(owned),
          )),
        );
      });
    }
    return repaired;
  }

  /** Bring linked hosts whose call rules are still exactly ours up to this release.
   *
   * Rewrites skill, hooks and plugin files through the install plan without
   * touching authorization. A skill the user edited is left for the detail page.
   */
  async syncStaleInstalls(layouts: RecallHostLayout[]): Promise<string[]> {
    return (await this.syncStale(layouts)).synced;
  }

  private async syncStale(
    layouts: RecallHostLayout[],
  ): Promise<{ synced: string[]; failed: string[] }> {
    const synced: string[] = [];
    const failed: string[] = [];
    for (const layout of layouts) {
      try {
        await this.withHostLock(layout, async () => {
          // Not linked from this data directory is not ours to sync, and not
          // a failure either.
          if (!(await this.isLinked(layout))) return;
          const sync = await planSkillSync(layout, this.runtimeFor(layout));
          if (sync === null || sync.requiresConfirmation) return;
          const preview = await previewRecallInstall(sync.plan);
          await this.checkInstallTrees(layout, preview);
          await applyRecallInstall(sync.plan, preview.fingerprint);
          synced.push(layout.hostId);
        });
      } catch {
        // One host that cannot be synced must not stop the runtime starting.
        failed.push(layout.hostId);
      }
    }
    return { synced, failed };
  }

  /** Refresh every host this directory installed, once per build.
   *
   * An upgrade changes the call rules, hooks and MCP entry we would write, but
   * nothing rewrote the copies hosts already had, so they kept telling the
   * model to call tools that were gone. This runs the same repoint and stale
   * sync the detail page offers, without asking, for files that are still
   * exactly ours; an edited skill stays for the user to confirm.
   *
   * The build it last completed for is recorded beside the grants, so later
   * launches of the same build cost one file read. A run in which any host
   * failed is not recorded, so the next launch tries again. With no build to
   * name (a source checkout) it runs every time and records nothing.
   */
  async refreshInstalledHosts(
    layouts: RecallHostLayout[],
    options: { build?: string; diagnostics?: DiagnosticSink } = {},
  ): Promise<HostRefreshReport> {
    const log = (input: DiagnosticInput) =>
      options.diagnostics?.log(input).catch(() => undefined);
    const build = options.build?.trim() || undefined;
    if (build !== undefined && (await this.readRefreshedBuild()) === build) {
      await log({
        level: "info",
        component: "host.refresh",
        event: "host_refresh.skipped",
        outcome: "skipped",
        details: { reason: "already_refreshed", count: layouts.length },
      });
      return {
        state: "already_refreshed",
        checked: layouts.length,
        repointed: [],
        synced: [],
        failed: [],
      };
    }
    const started = Date.now();
    const repointed: string[] = [];
    const failed: string[] = [];
    for (const layout of layouts) {
      try {
        repointed.push(...(await this.repairInstalledCommands([layout])));
      } catch {
        failed.push(layout.hostId);
      }
    }
    const stale = await this.syncStale(layouts);
    failed.push(...stale.failed.filter((hostId) => !failed.includes(hostId)));
    for (const hostId of new Set([...repointed, ...stale.synced])) {
      await log({
        level: "info",
        component: "host.refresh",
        event: "host_refresh.host_updated",
        outcome: "succeeded",
        details: { source_id: hostId },
      });
    }
    for (const hostId of failed) {
      await log({
        level: "warn",
        component: "host.refresh",
        event: "host_refresh.host_failed",
        outcome: "failed",
        error_code: "host_refresh_failed",
        details: { source_id: hostId },
      });
    }
    if (build !== undefined && failed.length === 0) {
      await this.writeRefreshedBuild(build).catch(() => undefined);
    }
    await log({
      level: failed.length === 0 ? "info" : "warn",
      component: "host.refresh",
      event: "host_refresh.completed",
      outcome: failed.length === 0 ? "succeeded" : "degraded",
      duration_ms: Date.now() - started,
      details: {
        count: layouts.length,
        updated_count: new Set([...repointed, ...stale.synced]).size,
        error_count: failed.length,
      },
    });
    return {
      state: "refreshed",
      checked: layouts.length,
      repointed,
      synced: stale.synced,
      failed,
    };
  }

  private async readRefreshedBuild(): Promise<string | null> {
    try {
      return await storageGuard(async () => {
        const file = path.join(this.directory, HOST_REFRESH_FILE);
        if ((await optionalStat(file)) === null) return null;
        const value: unknown = JSON.parse(await privateRead(file));
        return isObject(value) &&
          value.schemaVersion === 1 &&
          typeof value.build === "string"
          ? value.build
          : null;
      });
    } catch {
      // Unreadable reads as never refreshed: the refresh is idempotent.
      return null;
    }
  }

  private async writeRefreshedBuild(build: string): Promise<void> {
    await storageGuard(() =>
      privateWrite(
        path.join(this.directory, HOST_REFRESH_FILE),
        `${JSON.stringify({ schemaVersion: 1, build })}\n`,
      ),
    );
  }

  /** Same repair for one host. Does not revoke or reissue its authorization. */
  repairInstalledCommand(layout: RecallHostLayout): Promise<string[]> {
    return this.repairInstalledCommands([layout]);
  }

  async withHostLock<T>(
    layout: RecallHostLayout,
    operation: () => Promise<T>,
  ): Promise<T> {
    const key = this.hostDirectory(layout);
    const previous = locks.get(key) ?? Promise.resolve();
    let release!: () => void;
    const current = new Promise<void>((resolve) => {
      release = resolve;
    });
    const tail = previous.then(() => current);
    locks.set(key, tail);
    await previous;
    try {
      return await operation();
    } finally {
      release();
      if (locks.get(key) === tail) locks.delete(key);
    }
  }

  async getPermission(
    layout: RecallHostLayout,
  ): Promise<RecallPermissionStatus> {
    return this.withHostLock(layout, async () => {
      if (!supported(layout))
        return status(layout, "unsupported", null, "unsupported_transport");
      try {
        return await this.inspect(layout);
      } catch {
        return status(
          layout,
          "unavailable",
          null,
          "permission_store_unavailable",
        );
      }
    });
  }

  async previewInstall(layout: RecallHostLayout) {
    return this.withHostLock(layout, async () => {
      assertSupported(layout);
      await this.checkStorage();
      await this.requireNoSyncHook(layout);
      const plan = await planRecallInstall(layout, this.runtimeFor(layout));
      const preview = await previewRecallInstall(plan);
      this.previewedPlans.set(layout.hostId, plan);
      await this.checkInstallTrees(layout, preview);
      return preview;
    });
  }

  /** The plan this approval names. The preview's own plan is reused when the
   * fingerprint matches it: a plugin record carries the timestamp its plan
   * minted, so on a machine with no prior record a fresh plan could never
   * reproduce a preview's fingerprint, and every first install would refuse
   * as stale. Any other approval falls through to a fresh plan, and the
   * caller's fingerprint check still decides; apply's own drift guards hold
   * the disk still in place either way. */
  private async approvedPlan(
    layout: RecallHostLayout,
    approved: string,
  ): Promise<{ plan: RecallInstallPlan; preview: RecallInstallPreview }> {
    const previewed = this.previewedPlans.get(layout.hostId);
    if (previewed !== undefined) {
      const preview = await previewRecallInstall(previewed);
      if (preview.fingerprint === approved) return { plan: previewed, preview };
    }
    const plan = await planRecallInstall(layout, this.runtimeFor(layout));
    return { plan, preview: await previewRecallInstall(plan) };
  }

  async install(layout: RecallHostLayout, approved: unknown) {
    return this.withHostLock(layout, async () => {
      assertSupported(layout);
      requireFingerprint(approved);
      await this.checkStorage();
      await this.requireNoSyncHook(layout);
      await this.reclaimOrphanSyncHook(layout);
      const { plan, preview } = await this.approvedPlan(layout, approved);
      if (preview.fingerprint !== approved)
        throw new Error("host_install_preview_stale");
      await this.checkInstallTrees(layout, preview);
      const previous = await this.readBinding(layout);
      const beforeHashes = new Map([
        [plan.paths.mcp, plan.expected.mcp],
        ...plan.expected.registries.map(
          (file) => [file.path, file.hash] as const,
        ),
      ]);
      const deletable = new Set([
        ...preview.files
          .filter((file) => !file.existed)
          .map((file) => file.path),
        ...(previous?.deletions ?? [])
          .filter((file) => beforeHashes.get(file.path) === file.digest)
          .map((file) => file.path),
      ]);
      // Every confirmed re-link starts with no rights, including a repair of an
      // approved install. A failed rewrite must not retain prior authorization.
      await this.revokeBinding(layout, previous);
      if (preview.supersedes !== null && layout.supersedes !== undefined) {
        await this.revokeBinding(
          layout.supersedes,
          await this.readBinding(layout.supersedes),
        );
      }
      await storageGuard(async () => {
        await createPrivateDirectory(this.hostDirectory(layout));
      });
      const issued = await this.hostGrantStore.createInstallation({
        hostId: layout.hostId,
      });
      const binding: InstallationBinding = {
        schemaVersion: 1,
        hostId: layout.hostId,
        installationId: issued.receipt.installationId,
        credentialDigest: digest(issued.secret),
        complete: false,
        deletions: previous?.deletions ?? [],
      };
      try {
        await this.writeBinding(layout, binding);
        await privateWrite(
          this.runtimeFor(layout).credentialFile,
          `${issued.secret}\n`,
        );
        await applyRecallInstall(plan, approved);
        await verifyRecallInstall(layout);
        const removal = await planRecallUninstall(layout);
        const recorded = new Map(
          binding.deletions.map((file) => [file.path, file.digest]),
        );
        for (const file of removal.files) {
          if (
            file.owner === "host" &&
            file.after === null &&
            file.before !== null &&
            deletable.has(file.path)
          )
            recorded.set(file.path, digest(file.before));
        }
        binding.deletions = [...recorded].map(([file, hash]) => ({
          path: file,
          digest: hash,
        }));
        binding.complete = true;
        await this.writeBinding(layout, binding);
      } catch {
        // The only issued capability remains install-only, even if a native
        // installer wrote some files before failing. Never return a raw cause.
        throw new Error("host_install_failed");
      }
      return {
        hostId: layout.hostId,
        installed: true,
        permission: await this.inspect(layout),
      };
    });
  }

  async setPermission(
    layout: RecallHostLayout,
    input: unknown,
  ): Promise<RecallPermissionStatus> {
    return this.withHostLock(layout, async () => {
      const mutation = permissionInput(input, true);
      const binding = await this.requireBinding(
        layout,
        mutation.installationId,
      );
      const current = await this.inspect(layout);
      if (current.state === "relink-required")
        throw new Error("host_permission_relink_required");
      if (!binding.complete || current.reason === "installation_incomplete")
        throw new Error("host_permission_installation_incomplete");
      if (
        current.state !== "approved" &&
        current.state !== "install-only" &&
        current.state !== "revoked"
      )
        throw new Error("host_permission_unavailable");
      if (current.state === "revoked") {
        const receipt = current.receipt;
        if (
          !receipt ||
          receipt.installationId !== mutation.installationId ||
          receipt.revision !== mutation.expectedRevision
        ) {
          throw new HostGrantError("host_grant_revision_conflict");
        }
        const installed = await readInstalledRecallCommand(layout);
        if (
          installed?.env[CREDENTIAL_ENV] !==
          this.runtimeFor(layout).credentialFile
        ) {
          throw new Error("host_permission_relink_required");
        }
        try {
          await verifyRecallInstall(layout);
        } catch {
          throw new Error("host_permission_relink_required");
        }
        const issued = await this.hostGrantStore.createInstallation({
          hostId: layout.hostId,
        });
        const replacement: InstallationBinding = {
          ...binding,
          installationId: issued.receipt.installationId,
          credentialDigest: digest(issued.secret),
          complete: true,
        };
        await privateWrite(
          this.runtimeFor(layout).credentialFile,
          `${issued.secret}\n`,
        );
        await this.writeBinding(layout, replacement);
        const approved = await this.hostGrantStore.approveGrants({
          installationId: issued.receipt.installationId,
          expectedRevision: issued.receipt.revision,
          allowedTools: mutation.allowedTools,
          allowedScopes: mutation.allowedScopes,
        });
        try {
          await this.recordLinkCheck(layout);
        } catch {
          console.warn("host_permission_self_check_unavailable");
        }
        return status(layout, approved.status, approved);
      }
      const receipt = await this.hostGrantStore.approveGrants({
        installationId: mutation.installationId,
        expectedRevision: mutation.expectedRevision,
        allowedTools: mutation.allowedTools,
        allowedScopes: mutation.allowedScopes,
      });
      // Install probed before this grant existed. Refresh so the list can
      // stop saying the link is blind after the user has allowed it.
      try {
        await this.recordLinkCheck(layout);
      } catch {
        // Diagnosis cannot undo the committed grant or expose its raw error.
        console.warn("host_permission_self_check_unavailable");
      }
      return status(layout, receipt.status, receipt);
    });
  }

  async revokePermission(
    layout: RecallHostLayout,
    input: unknown,
  ): Promise<RecallPermissionStatus> {
    return this.withHostLock(layout, async () => {
      const mutation = permissionInput(input, false);
      await this.requireBinding(layout, mutation.installationId);
      const receipt = await this.hostGrantStore.revoke({
        installationId: mutation.installationId,
        expectedRevision: mutation.expectedRevision,
      });
      try {
        await this.recordLinkCheck(layout);
      } catch {
        // Preserve the last check for later explicit diagnosis, not a fake wired result.
        console.warn("host_permission_self_check_unavailable");
      }
      return status(layout, "revoked", receipt);
    });
  }

  async previewSkillSync(layout: RecallHostLayout) {
    return this.withHostLock(layout, async () => {
      await this.requireLinked(layout);
      const sync = await planSkillSync(layout, this.runtimeFor(layout));
      if (sync === null)
        return { hostId: layout.hostId, upToDate: true as const };
      const preview = await previewRecallInstall(sync.plan);
      await this.checkInstallTrees(layout, preview);
      return {
        upToDate: false as const,
        drift: sync.drift,
        requiresConfirmation: sync.requiresConfirmation,
        conflict: sync.requiresConfirmation,
        ...preview,
      };
    });
  }

  async applySkillSync(
    layout: RecallHostLayout,
    approved: unknown,
    overwriteModified: unknown,
  ) {
    return this.withHostLock(layout, async () => {
      await this.requireLinked(layout);
      const sync = await planSkillSync(layout, this.runtimeFor(layout));
      if (sync !== null) {
        requireFingerprint(approved);
        await this.checkInstallTrees(
          layout,
          await previewRecallInstall(sync.plan),
        );
        await applyRecallInstall(sync.plan, approved, {
          overwriteModifiedSkill: overwriteModified === true,
        });
      }
      return { hostId: layout.hostId, synced: true };
    });
  }

  async recordLinkCheck(layout: RecallHostLayout) {
    const health = await hostHealthCheck(layout, this.runtimeFor(layout));
    if (await this.ownsInstalledEntry(layout))
      await recordInstallSelfCheck(layout, health.link);
    return health;
  }

  async previewUninstall(layout: RecallHostLayout) {
    return this.withHostLock(layout, async () => {
      await this.checkStorage();
      await this.requireNoSyncHook(layout);
      return previewRecallUninstall(await this.planRemoval(layout));
    });
  }

  async uninstall(layout: RecallHostLayout, approved: unknown) {
    return this.withHostLock(layout, async () => {
      requireFingerprint(approved);
      await this.checkStorage();
      await this.requireNoSyncHook(layout);
      const plan = await this.planRemoval(layout);
      if (previewRecallUninstall(plan).fingerprint !== approved)
        throw new Error("host_uninstall_preview_stale");
      if (plan.review?.length) throw new Error("host_uninstall_needs_review");
      const binding = await this.readBinding(layout);
      // Revoke before removing files. A partial removal must never leave a
      // still-authorized credential in a host process that is already running.
      await this.revokeBinding(layout, binding);
      const refreshed = await this.planRemoval(layout);
      if (previewRecallUninstall(refreshed).fingerprint !== approved)
        throw new Error("host_uninstall_preview_stale");
      if (refreshed.files.length || refreshed.removedRoots.length)
        await applyRecallUninstall(refreshed, approved);
      return {
        hostId: layout.hostId,
        installed: false,
        permission: await this.inspect(layout),
      };
    });
  }

  async getSyncHookStatus(layout: RecallHostLayout): Promise<SyncHookStatus> {
    return this.withHostLock(layout, () => this.inspectSyncHook(layout));
  }

  async previewSyncHook(layout: RecallHostLayout, operation: unknown) {
    return this.withHostLock(layout, async () => {
      const prepared = await this.prepareSyncHook(layout, operation);
      return prepared.preview;
    });
  }

  async applySyncHook(
    layout: RecallHostLayout,
    input: unknown,
  ): Promise<SyncHookStatus> {
    return this.withHostLock(layout, async () => {
      const request = syncHookInput(input);
      const prepared = await this.prepareSyncHook(layout, request.operation);
      if (
        prepared.preview.fingerprint !== request.fingerprint ||
        prepared.preview.installationId !== request.installationId ||
        prepared.preview.revision !== request.revision
      )
        throw new Error("sync_hook_preview_stale");
      try {
        if (prepared.operation === "install") {
          await prepared.target.applyInstall(
            prepared.plan,
            prepared.plan.fingerprint,
          );
          const current = await this.inspect(layout);
          const receipt = current.receipt;
          if (
            (current.state !== "approved" &&
              current.state !== "install-only") ||
            current.reason !== null ||
            receipt?.installationId !== request.installationId ||
            receipt?.revision !== request.revision
          )
            throw new Error("sync_hook_connection_changed");
        } else {
          await prepared.target.applyUninstall(
            prepared.plan,
            prepared.plan.fingerprint,
          );
        }
      } catch {
        throw new Error("sync_hook_apply_failed");
      }
      return this.inspectSyncHook(layout);
    });
  }

  private async prepareSyncHook(layout: RecallHostLayout, operation: unknown) {
    if (layout.hostId !== "claude-code" || !supported(layout))
      throw new Error("sync_hook_unsupported");
    if (operation !== "install" && operation !== "uninstall")
      throw new Error("sync_hook_confirmation_required");
    await this.checkStorage();
    const binding = await this.readBinding(layout);
    // Removal has to stay possible when the installation record is already
    // gone: that is exactly the state the artifacts strand a host in. The
    // preview then borrows a directory-derived identity, because the files it
    // removes are the only thing left to identify the install by.
    if (operation === "uninstall") {
      const identity = binding
        ? await (async () => {
            const receipt = await this.receiptFor(layout, binding);
            return {
              installationId: receipt.installationId,
              revision: receipt.revision,
              credentialDigest: binding.credentialDigest,
            };
          })()
        : orphanSyncHookIdentity(layout.containmentRoot);
      const target = createClaudeCodeRemovalTarget(layout.containmentRoot);
      const plan = await target.planUninstall();
      return {
        operation: "uninstall" as const,
        target,
        plan,
        preview: syncHookPreview(layout.containmentRoot, plan, identity),
      };
    }
    if (!binding) throw new Error("sync_hook_connection_required");
    const receipt = await this.receiptFor(layout, binding);
    const identity = {
      installationId: receipt.installationId,
      revision: receipt.revision,
      credentialDigest: binding.credentialDigest,
    };
    const linked = await this.inspect(layout);
    if (
      !binding.complete ||
      (linked.state !== "approved" && linked.state !== "install-only") ||
      linked.reason !== null
    )
      throw new Error("sync_hook_connection_required");
    if (!this.syncHookAssetsDirectory)
      throw new Error("sync_hook_assets_unavailable");
    const runtime = this.runtimeFor(layout);
    const target = createClaudeCodeTarget({
      claudeDirectory: layout.containmentRoot,
      runtimeKind: "electron",
      nodeExecutable: runtime.runtimeCommand,
      assets: await loadSyncHookAssets(this.syncHookAssetsDirectory),
      config: {
        schemaVersion: 1,
        mode: "sync-only",
        installationId: receipt.installationId,
        discoveryPath: runtime.discoveryPath,
        credentialFile: runtime.credentialFile,
        stateDirectory: path.join(
          layout.containmentRoot,
          ".one-memory/mh-c01/state",
        ),
      },
    });
    const plan = await target.planInstall();
    return {
      operation: "install" as const,
      target,
      plan,
      preview: syncHookPreview(layout.containmentRoot, plan, identity),
    };
  }

  private async inspectSyncHook(
    layout: RecallHostLayout,
  ): Promise<SyncHookStatus> {
    const result = (
      state: SyncHookStatus["state"],
      reason: string | null = null,
    ): SyncHookStatus => ({ hostId: layout.hostId, state, reason });
    if (layout.hostId !== "claude-code" || !supported(layout))
      return result("unsupported");
    try {
      await this.checkStorage();
      const linked = await this.inspect(layout);
      if (await hasSyncHookArtifacts(layout.containmentRoot)) {
        try {
          await createClaudeCodeRemovalTarget(layout.containmentRoot).verify();
          return result(
            "installed",
            linked.state === "revoked"
              ? "connection-revoked"
              : linked.state === "approved" || linked.state === "install-only"
                ? null
                : "connection-unavailable",
          );
        } catch {
          return result("modified", "sync_hook_files_changed");
        }
      }
      if (
        (linked.state !== "approved" && linked.state !== "install-only") ||
        linked.reason !== null
      )
        return result("connection-required");
      if (!this.syncHookAssetsDirectory)
        return result("unavailable", "sync_hook_assets_unavailable");
      try {
        await loadSyncHookAssets(this.syncHookAssetsDirectory);
      } catch {
        return result("unavailable", "sync_hook_assets_unavailable");
      }
      return result("not-installed");
    } catch {
      return result("unavailable", "sync_hook_status_unavailable");
    }
  }

  private async requireNoSyncHook(layout: RecallHostLayout): Promise<void> {
    if (layout.hostId !== "claude-code") return;
    if (!(await hasSyncHookArtifacts(layout.containmentRoot))) return;
    // Artifacts with no installation record behind them are not a managed hook:
    // nothing is authorized through them, and re-linking reclaims the
    // directory. Blocking on them deadlocked the host, because the removal
    // preview itself used to need the very record that was missing.
    if ((await this.readBinding(layout)) === null) return;
    throw new Error("sync_hook_remove_first");
  }

  /** Reclaims hook artifacts left behind by an install with no record left.
   *
   * Best effort on purpose: a stale hook must never block re-linking, and the
   * plan only ever touches files the removal flow already owns.
   */
  private async reclaimOrphanSyncHook(layout: RecallHostLayout): Promise<void> {
    if (layout.hostId !== "claude-code") return;
    if ((await this.readBinding(layout)) !== null) return;
    try {
      if (!(await hasSyncHookArtifacts(layout.containmentRoot))) return;
      const target = createClaudeCodeRemovalTarget(layout.containmentRoot);
      const plan = await target.planUninstall();
      await target.applyUninstall(plan, plan.fingerprint);
    } catch {
      // The artifacts stay, but the host is usable and the hook can still be
      // removed by hand now that removal no longer needs the missing record.
    }
  }

  private hostDirectory(layout: RecallHostLayout): string {
    // Fixed-width path components also cover profiles without interpreting host
    // IDs or caller data as filesystem paths. No renderer supplies this layout.
    const key = digest(
      JSON.stringify([
        layout.hostId,
        path.resolve(layout.containmentRoot),
        layout.configPath,
        layout.journalPath,
      ]),
    );
    return path.join(this.directory, "installations", key);
  }

  private async checkStorage(): Promise<void> {
    await storageGuard(async () => {
      await checkDataRoot(this.dataDir);
      const current = await lstat(this.directory);
      checkPrivate(current, true);
      if (
        current.dev !== this.directoryIdentity.dev ||
        current.ino !== this.directoryIdentity.ino
      )
        throw new Error("host_permission_storage_unsafe");
      checkPrivate(
        await lstat(path.join(this.directory, "installations")),
        true,
      );
    });
    await this.hostGrantStore.listInstallations();
  }

  private async readBinding(
    layout: RecallHostLayout,
  ): Promise<InstallationBinding | null> {
    return storageGuard(async () => {
      const directory = this.hostDirectory(layout);
      if ((await optionalStat(directory)) === null) return null;
      checkPrivate(await lstat(directory), true);
      const file = path.join(directory, "installation.json");
      if ((await optionalStat(file)) === null)
        throw new Error("host_permission_storage_unsafe");
      const value: unknown = JSON.parse(await privateRead(file));
      if (
        !isObject(value) ||
        value.schemaVersion !== 1 ||
        value.hostId !== layout.hostId ||
        typeof value.installationId !== "string" ||
        !/^[a-f0-9-]{36}$/.test(value.installationId) ||
        !isDigest(value.credentialDigest) ||
        typeof value.complete !== "boolean" ||
        !Array.isArray(value.deletions) ||
        !value.deletions.every(
          (entry) =>
            isObject(entry) &&
            typeof entry.path === "string" &&
            isDigest(entry.digest),
        )
      )
        throw new Error("host_permission_storage_unsafe");
      return value as unknown as InstallationBinding;
    });
  }

  private async writeBinding(
    layout: RecallHostLayout,
    binding: InstallationBinding,
  ): Promise<void> {
    await storageGuard(() =>
      privateWrite(
        path.join(this.hostDirectory(layout), "installation.json"),
        `${JSON.stringify(binding)}\n`,
      ),
    );
  }

  private async receiptFor(
    layout: RecallHostLayout,
    binding: InstallationBinding,
  ): Promise<HostGrantReceipt> {
    const receipt = (await this.hostGrantStore.listInstallations()).find(
      (item) =>
        item.installationId === binding.installationId &&
        item.hostId === layout.hostId,
    );
    if (!receipt) throw new Error("host_permission_installation_mismatch");
    return receipt;
  }

  private async requireBinding(
    layout: RecallHostLayout,
    installationId: string,
  ): Promise<InstallationBinding> {
    assertSupported(layout);
    await this.checkStorage();
    const binding = await this.readBinding(layout);
    if (!binding || binding.installationId !== installationId)
      throw new Error("host_permission_installation_mismatch");
    await this.receiptFor(layout, binding);
    return binding;
  }

  private async revokeBinding(
    layout: RecallHostLayout,
    binding: InstallationBinding | null,
  ): Promise<void> {
    if (binding === null) return;
    const receipt = await this.receiptFor(layout, binding);
    if (receipt.status !== "revoked")
      await this.hostGrantStore.revoke({
        installationId: receipt.installationId,
        expectedRevision: receipt.revision,
      });
  }

  private async inspect(
    layout: RecallHostLayout,
    verifyFiles = true,
  ): Promise<RecallPermissionStatus> {
    await this.checkStorage();
    const binding = await this.readBinding(layout);
    if (binding === null) {
      const installed =
        (await readInstalledRecallCommand(layout)) ??
        (layout.supersedes === undefined
          ? null
          : await readInstalledRecallCommand(layout.supersedes));
      return installed === null
        ? status(layout, "not-installed", null)
        : status(layout, "relink-required", null, "legacy_installation");
    }
    const receipt = await this.receiptFor(layout, binding);
    if (receipt.status === "revoked") return status(layout, "revoked", receipt);
    if (!binding.complete)
      return status(layout, "install-only", receipt, "installation_incomplete");
    try {
      const secret = await readHostCredentialFile(
        this.runtimeFor(layout).credentialFile,
      );
      if (digest(secret) !== binding.credentialDigest)
        return status(
          layout,
          "relink-required",
          receipt,
          "credential_missing_or_changed",
        );
    } catch {
      return status(
        layout,
        "relink-required",
        receipt,
        "credential_missing_or_changed",
      );
    }
    const installed = await readInstalledRecallCommand(layout);
    if (
      installed?.env[CREDENTIAL_ENV] !== this.runtimeFor(layout).credentialFile
    )
      return status(layout, "relink-required", receipt, "legacy_installation");
    try {
      if (verifyFiles) await verifyRecallInstall(layout);
    } catch {
      return status(
        layout,
        "relink-required",
        receipt,
        "managed_config_changed",
      );
    }
    return status(layout, receipt.status, receipt);
  }

  private async requireLinked(layout: RecallHostLayout): Promise<void> {
    assertSupported(layout);
    // A modified skill is exactly what this confirmed repair is for. The
    // credential binding is still required; plan/apply owns the file diff.
    const current = await this.inspect(layout, false);
    if (current.state !== "approved" && current.state !== "install-only")
      throw new Error("host_permission_relink_required");
    if (current.reason !== null)
      throw new Error("host_permission_installation_incomplete");
  }

  /** `requireLinked` as a question, for sweeps that skip hosts not ours. */
  private async isLinked(layout: RecallHostLayout): Promise<boolean> {
    if (!supported(layout)) return false;
    const current = await this.inspect(layout, false);
    return (
      (current.state === "approved" || current.state === "install-only") &&
      current.reason === null
    );
  }

  /** Turning off a host with nothing of ours on it is an empty removal, not a
   * failure; one whose state cannot be settled is an empty removal carrying
   * what to look at, which uninstall refuses. Only what is ours for certain
   * goes on to the journal planner. */
  private async planRemoval(
    layout: RecallHostLayout,
  ): Promise<RecallUninstallPlan> {
    const remnants = await inspectRemnants(layout, this.runtimeFor(layout));
    if (remnants.state !== "present")
      return {
        layout,
        files: [],
        removedRoots: [],
        review: remnants.state === "review" ? remnants.findings : [],
      };
    const plan = await planRecallUninstall(layout);
    if (plan.review?.length) return plan;
    await this.checkRemoval(layout, plan);
    return plan;
  }

  private async checkRemoval(
    layout: RecallHostLayout,
    plan: RecallUninstallPlan,
  ): Promise<void> {
    if (layout.plugin !== undefined) {
      try {
        await verifyRecallInstall(layout);
      } catch {
        throw new Error("host_uninstall_modified_files");
      }
    }
    const binding = await this.readBinding(layout);
    const known = new Map(
      binding?.deletions.map((file) => [file.path, file.digest]) ?? [],
    );
    for (const file of plan.files) {
      // The old installer deletes a whole newly created settings file even if
      // the user added unrelated settings later. Refuse, never erase those.
      if (
        file.owner === "host" &&
        file.after === null &&
        file.before !== null &&
        known.get(file.path) !== digest(file.before)
      )
        throw new Error("host_uninstall_modified_files");
    }
    const planned = new Set(plan.files.map((file) => file.path));
    for (const root of plan.removedRoots) {
      for (const file of await treeFiles(root)) {
        // The host's own bookkeeping is not a file the user put there, and it
        // is written while the host runs, so it can never be planned. Refusing
        // on it made uninstall impossible on any machine with the host open.
        if (
          layout.plugin !== undefined &&
          isPluginCacheBookkeeping(layout.plugin, file)
        )
          continue;
        if (!planned.has(file))
          throw new Error("host_uninstall_modified_files");
      }
    }
  }

  private async checkInstallTrees(
    layout: RecallHostLayout,
    preview: RecallInstallPreview,
  ): Promise<void> {
    if (preview.supersedes !== null && layout.supersedes !== undefined) {
      try {
        await this.checkRemoval(
          layout.supersedes,
          await planRecallUninstall(layout.supersedes),
        );
      } catch {
        throw new Error("host_install_modified_files");
      }
    }
    if (!layout.plugin) return;
    // WorkBuddy has a marketplace copy beside its cache copy; a ZCode plugin
    // is the cache copy alone. Both roots are ours, and both are checked.
    const plugin = layout.plugin;
    const roots =
      "marketplaceRoot" in plugin
        ? [plugin.marketplaceRoot, plugin.cacheRoot]
        : [plugin.cacheRoot];
    const planned = new Set(preview.files.map((file) => file.path));
    for (const root of roots) {
      for (const file of await treeFiles(root)) {
        // WorkBuddy writes `.in_use/<pid>` beside the cached manifest for every
        // live process. Those files are the host's, not the user's, and they
        // cannot appear in a plan, so they must not read as an unmanaged edit.
        if (isPluginCacheBookkeeping(layout.plugin, file)) continue;
        if (!planned.has(file)) throw new Error("host_install_modified_files");
      }
    }
  }
}

function status(
  layout: RecallHostLayout,
  state: RecallPermissionStatus["state"],
  receipt: HostGrantReceipt | null,
  reason: RecallPermissionStatus["reason"] = null,
): RecallPermissionStatus {
  return {
    hostId: layout.hostId,
    state,
    reason,
    receipt:
      receipt === null
        ? null
        : {
            hostId: receipt.hostId,
            installationId: receipt.installationId,
            revision: receipt.revision,
            status: receipt.status,
            allowedTools: [...receipt.allowedTools],
            allowedScopes: [...receipt.allowedScopes],
          },
  };
}

/** Whether a layout can hold a revocable credential.
 *
 * The id grammar lives in `@one-memory/contracts` so that this layer, the
 * grant store and the CLI cannot drift: a host id `host list` prints has to be
 * one `host install` accepts. Profile scoping is what DeepSeek Harness needs —
 * it configures recall per profile (`deepseek-harness:desktop`) — and the
 * installation directory is a digest of the whole id, so the colon never
 * becomes a path component.
 */
function supported(layout: RecallHostLayout): boolean {
  return (
    (layout.channel === "mcp" || layout.channel === "cli") &&
    isHostId(layout.hostId) &&
    layout.hostId !== "openclaw"
  );
}

function assertSupported(layout: RecallHostLayout): void {
  if (!supported(layout))
    throw new Error("host_permission_transport_unsupported");
}

function requireFingerprint(value: unknown): asserts value is string {
  if (!isDigest(value)) throw new Error("host_install_confirmation_required");
}

function permissionInput(
  input: unknown,
  approval: true,
): SetRecallPermissionInput;
function permissionInput(
  input: unknown,
  approval: false,
): RevokeRecallPermissionInput;
function permissionInput(
  input: unknown,
  approval: boolean,
): SetRecallPermissionInput | RevokeRecallPermissionInput {
  if (!isObject(input) || input.userConfirmed !== true)
    throw new Error("host_permission_confirmation_required");
  const keys = [
    "installationId",
    "expectedRevision",
    "userConfirmed",
    ...(approval ? ["allowedTools", "allowedScopes"] : []),
  ];
  if (
    Object.keys(input).length !== keys.length ||
    !keys.every((key) => Object.hasOwn(input, key)) ||
    typeof input.installationId !== "string" ||
    !Number.isSafeInteger(input.expectedRevision) ||
    (input.expectedRevision as number) < 1 ||
    (approval &&
      (!Array.isArray(input.allowedTools) ||
        !Array.isArray(input.allowedScopes)))
  )
    throw new Error("host_permission_invalid_input");
  return input as unknown as
    | SetRecallPermissionInput
    | RevokeRecallPermissionInput;
}

async function checkDataRoot(directory: string): Promise<void> {
  const info = await lstat(directory);
  if (
    !info.isDirectory() ||
    info.uid !== process.getuid?.() ||
    (info.mode & 0o022) !== 0
  )
    throw new Error("host_permission_storage_unsafe");
  // macOS /var may be a root-owned symlink. Check both its lexical parents
  // and the resolved path, allowing only trusted owners and root sticky dirs.
  for (const start of new Set([directory, await realpath(directory)])) {
    let current = start;
    for (;;) {
      const ancestor = await lstat(current);
      const trustedOwner =
        ancestor.uid === 0 || ancestor.uid === process.getuid?.();
      const stickyRoot = ancestor.uid === 0 && (ancestor.mode & 0o1000) !== 0;
      if (
        !trustedOwner ||
        (!ancestor.isSymbolicLink() &&
          (!ancestor.isDirectory() ||
            ((ancestor.mode & 0o022) !== 0 && !stickyRoot)))
      )
        throw new Error("host_permission_storage_unsafe");
      const parent = path.dirname(current);
      if (parent === current) break;
      current = parent;
    }
  }
}

function checkPrivate(info: Stats, directory: boolean): void {
  if (
    (directory ? !info.isDirectory() : !info.isFile() || info.nlink !== 1) ||
    info.uid !== process.getuid?.() ||
    (info.mode & 0o7777) !== (directory ? 0o700 : 0o600)
  )
    throw new Error("host_permission_storage_unsafe");
}

async function optionalStat(file: string): Promise<Stats | null> {
  try {
    return await lstat(file);
  } catch (error) {
    if (isObject(error) && error.code === "ENOENT") return null;
    throw error;
  }
}

async function createPrivateDirectory(directory: string): Promise<boolean> {
  try {
    await mkdir(directory, { mode: 0o700 });
    checkPrivate(await lstat(directory), true);
    return true;
  } catch (error) {
    if (!isObject(error) || error.code !== "EEXIST") throw error;
    checkPrivate(await lstat(directory), true);
    return false;
  }
}

async function privateRead(file: string): Promise<string> {
  checkPrivate(await lstat(path.dirname(file)), true);
  checkPrivate(await lstat(file), false);
  const handle = await open(
    file,
    constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK,
  );
  try {
    const info = await handle.stat();
    checkPrivate(info, false);
    if (info.size > 1024 * 1024)
      throw new Error("host_permission_storage_unsafe");
    return await handle.readFile("utf8");
  } finally {
    await handle.close();
  }
}

async function privateWrite(file: string, content: string): Promise<void> {
  checkPrivate(await lstat(path.dirname(file)), true);
  const current = await optionalStat(file);
  if (current !== null) checkPrivate(current, false);
  const temporary = `${file}.${randomUUID()}.tmp`;
  const handle = await open(
    temporary,
    constants.O_WRONLY |
      constants.O_CREAT |
      constants.O_EXCL |
      constants.O_NOFOLLOW,
    0o600,
  );
  try {
    await handle.chmod(0o600);
    await handle.writeFile(content, "utf8");
    await handle.sync();
    await handle.close();
    await rename(temporary, file);
    const directory = await open(
      path.dirname(file),
      constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW,
    );
    try {
      await directory.sync();
    } finally {
      await directory.close();
    }
  } finally {
    await handle.close();
    await unlink(temporary).catch(() => {});
  }
}

async function treeFiles(root: string): Promise<string[]> {
  const info = await optionalStat(root);
  if (info === null) return [];
  if (!info.isDirectory()) throw new Error("host_uninstall_modified_files");
  const result: string[] = [];
  for (const entry of await readdir(root, { withFileTypes: true })) {
    const file = path.join(root, entry.name);
    if (entry.isDirectory()) result.push(...(await treeFiles(file)));
    else if (entry.isFile()) result.push(file);
    else throw new Error("host_uninstall_modified_files");
  }
  return result;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isDigest(value: unknown): value is string {
  return typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
}

function digest(value: string): string {
  return createHash("sha256").update(value).digest("hex");
}

async function storageGuard<T>(operation: () => Promise<T>): Promise<T> {
  try {
    return await operation();
  } catch (error) {
    if (error instanceof HostGrantError) throw error;
    throw new Error("host_permission_storage_unsafe");
  }
}
