import type {
  ConnectionSetupCapability,
  ConnectionSetupExecute,
  ConnectionSetupJournal,
  ConnectionSetupPreview,
  ConnectionSetupResult,
  ConnectionSetupSelection,
} from "@one-memory/contracts";
import type { DebugLogSnapshot } from "./debug-log-types.js";

export type RecallPermissionTool =
  | "om_recall"
  | "om_ask"
  | "om_conversations"
  | "om_matters"
  | "om_diagnostic_trace";

/** Display-only local receipt. Never an authorization token. */
export interface RecallPermissionReceipt {
  readonly installationId: string;
  readonly hostId: string;
  readonly revision: number;
  readonly status: "install-only" | "approved" | "revoked";
  readonly allowedTools: readonly RecallPermissionTool[];
  readonly allowedScopes: readonly string[];
}

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
  receipt: RecallPermissionReceipt | null;
}

export interface SyncHookStatus {
  hostId: string;
  state:
    | "unsupported"
    | "connection-required"
    | "not-installed"
    | "installed"
    | "modified"
    | "unavailable";
  reason: string | null;
}

export interface SyncHookPreview {
  hostId: string;
  operation: "install" | "uninstall";
  installationId: string;
  revision: number;
  fingerprint: string;
  files: Array<{
    path: string;
    action: "create" | "update" | "remove";
    owner: "host" | "one-memory";
  }>;
}

export interface ApplySyncHookInput {
  operation: "install" | "uninstall";
  installationId: string;
  revision: number;
  fingerprint: string;
  userConfirmed: true;
}

export interface SetRecallPermissionInput {
  installationId: string;
  expectedRevision: number;
  allowedTools: readonly RecallPermissionTool[];
  allowedScopes: readonly string[];
  userConfirmed: true;
}

export interface RevokeRecallPermissionInput {
  installationId: string;
  expectedRevision: number;
  userConfirmed: true;
}

export interface RecallHostStatus {
  hostId: string;
  label: string;
  /** Whether this host is actually installed on the machine. */
  present: boolean;
  /** Whether our files are there and unaltered. Not whether recall works.
   *
   * The two came apart once: three hosts were installed against a plausible
   * but wrong discovery path, every file intact, and recalled nothing. So the
   * link's own verdict is carried separately.
   */
  installed: boolean;
  /** Optional only for renderers attached to an older desktop shell. */
  permission?: RecallPermissionStatus;
  /** Launch request support, not a verified input-box prefill receipt. */
  handoff?: "open" | "copy";
  /** What the last self-check said, recorded at install time.
   *
   * Recorded rather than live, because probing starts a process per host and
   * this list is refreshed often.
   */
  selfCheck: { state: string; detail: string; probed_at: string } | null;
  /** Whether the call-rule skill on disk still matches what we ship (R-33). */
  skillDrift: {
    state: "in_sync" | "stale" | "modified";
    installedVersion: number | null;
    currentVersion: number;
  } | null;
}

/** A fresh self-check of one host, and whether it can be repaired. */
export interface RecallLinkCheck {
  hostId: string;
  probe: {
    state: "wired" | "unreachable" | "reachable_but_blind";
    detail: string;
    discoveryPath: string | null;
    discoveryPresent: boolean;
    probedAt: string;
  };
  skill: {
    state: "in_sync" | "stale" | "modified";
    installedVersion: number | null;
    currentVersion: number;
    detail: string;
  } | null;
  /** Non-null only when the host points somewhere other than where the app
   * writes. Null when the app is merely not running: the entry is already
   * correct then, and offering to repair it would send the user to fix the
   * wrong thing. */
  repoint: { from: string | null; to: string } | null;
}

/** The lines one install would change, and a little of the text around them. */
export interface RecallPreviewHunk {
  /** 1-based line number in the file as it stands now. */
  startLine: number;
  removed: string[];
  added: string[];
  contextBefore: string[];
  contextAfter: string[];
}

export interface RecallPreviewFile {
  path: string;
  /** `host` is the other program's own settings file; `one-memory` is ours. */
  owner: "host" | "one-memory";
  existed: boolean;
  /** Null when this file is not changed at all. */
  hunk: RecallPreviewHunk | null;
}

export interface RecallInstallPreview {
  hostId: string;
  hostLabel: string;
  files: RecallPreviewFile[];
  /** Identifies the on-disk state this preview describes. */
  fingerprint: string;
}

export interface SkillSyncPreview extends RecallInstallPreview {
  upToDate: boolean;
  conflict: boolean;
  requiresConfirmation?: boolean;
  drift?: {
    state: "in_sync" | "stale" | "modified";
    installedVersion: number | null;
    currentVersion: number;
  };
}

export interface RecallUninstallFile {
  path: string;
  owner: "host" | "one-memory";
  /** Null when this file is not changed at all, or when it is removed. */
  hunk: RecallPreviewHunk | null;
  /** The whole file goes, rather than a region of it changing. */
  deleted: boolean;
}

/** A place that may still hold part of the connection, which the desktop
 * could not settle; a person has to look at it. */
export interface RecallRemovalFinding {
  path: string;
  where: string;
}

export interface RecallUninstallPreview {
  hostId: string;
  hostLabel: string;
  files: RecallUninstallFile[];
  /** Non-empty when the app cannot be called off without a person looking;
   * nothing is removed then. Absent from older desktops. */
  review?: RecallRemovalFinding[];
  /** Identifies the on-disk state this preview describes. */
  fingerprint: string;
}

declare global {
  interface Window {
    oneMemory?: {
      getBuildInfo?(): {
        version: string;
        buildNumber: string;
        commit: string;
      };
      getDebugLogs?(): Promise<DebugLogSnapshot>;
      /** Renderer end of the four-step UI/kernel trace. */
      logUiTrace?(input: {
        action: string;
        page: string;
        phase: "action" | "result";
        status?: string;
        reason?: string;
        count?: number;
        duration_ms?: number;
        trace_id?: string;
      }): Promise<{ state: "logged" }>;
      markDiagnosticIncident?(description?: string): Promise<{
        incident_id: string;
        session_id: string;
        timestamp: string;
      }>;
      exportDiagnostics?(): Promise<{
        state: "exported";
        filename?: string;
        path?: string;
        file_count?: number;
      }>;
      getBackgroundHealth?(): Promise<{
        state: "healthy" | "stopped" | "degraded" | "untrusted" | "unknown";
        code: string;
        core: "running" | "stopped" | "unhealthy" | "untrusted" | "unknown";
        broker:
          | "running"
          | "stopped"
          | "unhealthy"
          | "untrusted"
          | "not_checked";
        instanceId?: string;
      }>;
      restartBackground?(): Promise<{ state: "restarted" }>;
      getConnectionSetupCapability?(): Promise<ConnectionSetupCapability>;
      previewConnectionSetup?(
        input: ConnectionSetupSelection,
      ): Promise<ConnectionSetupPreview>;
      executeConnectionSetup?(
        input: ConnectionSetupExecute,
      ): Promise<ConnectionSetupResult>;
      getConnectionSetupReceipt?(
        operationId: string,
      ): Promise<ConnectionSetupJournal | null>;
      getLoginPreference?(): Promise<{
        supported: boolean;
        enabled: boolean;
        status: string;
      }>;
      setLoginPreference?(input: {
        openAtLogin: boolean;
        userConfirmed: true;
      }): Promise<{ supported: boolean; enabled: boolean; status: string }>;
      requestQuit?(confirmed: true): Promise<void>;
      pickSourceDirectory?(sourceId: string): Promise<{
        cancelled: boolean;
        path: string | null;
      }>;
      getDisplayPreferences?(): Promise<{
        reduceMotion: boolean;
        reduceTransparency: boolean;
        revision: number;
      }>;
      onDisplayPreferencesChanged?(
        listener: (preferences: {
          reduceMotion: boolean;
          reduceTransparency: boolean;
          revision: number;
        }) => void,
      ): () => void;
      getApiToken(): Promise<string>;
      getApiBaseUrl(): Promise<string>;
      /** Optional while a dev renderer is attached to an older desktop shell. */
      getConnectionMaterial?(): Promise<{
        mcp: string;
        shell: string | null;
        prompt: string;
      }>;
      copyText?(text: string): Promise<void>;
      getRecallHosts(): Promise<RecallHostStatus[]>;
      getSyncHookStatus?(hostId: string): Promise<SyncHookStatus>;
      previewSyncHook?(
        hostId: string,
        operation: "install" | "uninstall",
      ): Promise<SyncHookPreview>;
      applySyncHook?(
        hostId: string,
        input: ApplySyncHookInput,
      ): Promise<SyncHookStatus>;
      /** Absent in older shells; never infer approval from installation. */
      getRecallPermission?(hostId: string): Promise<RecallPermissionStatus>;
      setRecallPermission?(
        hostId: string,
        input: SetRecallPermissionInput,
      ): Promise<RecallPermissionStatus>;
      revokeRecallPermission?(
        hostId: string,
        input: RevokeRecallPermissionInput,
      ): Promise<RecallPermissionStatus>;
      previewRecallInstall(hostId: string): Promise<RecallInstallPreview>;
      probeRecallHost(hostId: string): Promise<RecallLinkCheck>;
      previewSkillSync(hostId: string): Promise<SkillSyncPreview>;
      applySkillSync(
        hostId: string,
        approved?: string,
        overwriteModified?: boolean,
      ): Promise<{ hostId: string; synced: boolean }>;
      /** Repoint installed hosts at the running app without reissuing grants. */
      repairRecallCommands?(): Promise<string[]>;
      installRecall(
        hostId: string,
        approved?: string,
      ): Promise<{
        hostId: string;
        installed: boolean;
        permission?: RecallPermissionStatus;
      }>;
      /** Start a chat in another app with these memories already typed in.
       * `opened: false` means that app takes no starting prompt. */
      openHandoff(
        hostId: string,
        prompt: string,
      ): Promise<{ opened: boolean; reason: string | null }>;
      previewRecallUninstall(hostId: string): Promise<RecallUninstallPreview>;
      uninstallRecall(
        hostId: string,
        approved?: string,
      ): Promise<{
        hostId: string;
        installed: boolean;
        permission?: RecallPermissionStatus;
      }>;
    };
  }
}
