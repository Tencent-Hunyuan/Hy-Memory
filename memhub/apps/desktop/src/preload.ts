import type {
  ConnectionSetupCapability,
  ConnectionSetupExecute,
  ConnectionSetupJournal,
  ConnectionSetupPreview,
  ConnectionSetupResult,
  ConnectionSetupSelection,
} from "@one-memory/contracts";
import {
  isLanguagePreference,
  isLanguageSnapshot,
  type LanguagePreference,
  type LanguageSnapshot,
} from "@one-memory/i18n";
import type {
  RecallInstallPreview,
  RecallUninstallPreview,
} from "@one-memory/host-bridge/installer";
import type { BackgroundHealth } from "@one-memory/runtime-control";
import { contextBridge, ipcRenderer } from "electron";
import type { DebugLogSnapshot } from "./diagnostics-ipc";
import {
  type DisplayPreferenceSnapshot,
  displayPreferencePayload,
} from "./display-preferences";
import type {
  RecallPermissionStatus,
  RevokeRecallPermissionInput,
  SetRecallPermissionInput,
} from "./host-permissions";
import type { LoginPreference } from "./login-preference";
import type { SourceDirectorySelection } from "./source-directory-picker";
import type {
  ApplySyncHookInput,
  SyncHookPreview,
  SyncHookStatus,
} from "./sync-hook";

declare const __ONE_MEMORY_VERSION__: string;
declare const __ONE_MEMORY_BUILD_NUMBER__: string;
declare const __ONE_MEMORY_COMMIT__: string;

function languageSnapshot(payload: unknown): LanguageSnapshot {
  if (!isLanguageSnapshot(payload))
    throw Error("language_preferences_unavailable");
  return {
    preference: payload.preference,
    locale: payload.locale,
    revision: payload.revision,
  };
}

contextBridge.exposeInMainWorld("oneMemory", {
  getBuildInfo: () => ({
    version: __ONE_MEMORY_VERSION__,
    buildNumber: __ONE_MEMORY_BUILD_NUMBER__,
    commit: __ONE_MEMORY_COMMIT__,
  }),
  getLanguagePreference: async (): Promise<LanguageSnapshot> =>
    languageSnapshot(
      await ipcRenderer.invoke("one-memory:get-language-preference"),
    ),
  setLanguagePreference: async (
    preference: LanguagePreference,
  ): Promise<LanguageSnapshot> => {
    if (!isLanguagePreference(preference))
      throw Error("invalid_language_preference");
    return languageSnapshot(
      await ipcRenderer.invoke(
        "one-memory:set-language-preference",
        preference,
      ),
    );
  },
  onLanguagePreferenceChanged: (
    listener: (value: LanguageSnapshot) => void,
  ): (() => void) => {
    if (typeof listener !== "function")
      throw Error("invalid_language_preference_listener");
    const handler = (_event: unknown, payload: unknown) => {
      if (isLanguageSnapshot(payload)) listener(languageSnapshot(payload));
    };
    let subscribed = true;
    const unsubscribe = () => {
      if (!subscribed) return;
      subscribed = false;
      ipcRenderer.removeListener(
        "one-memory:language-preference-changed",
        handler,
      );
      window.removeEventListener("unload", unsubscribe);
    };
    ipcRenderer.on("one-memory:language-preference-changed", handler);
    window.addEventListener("unload", unsubscribe, { once: true });
    return unsubscribe;
  },
  getDebugLogs: (): Promise<DebugLogSnapshot> =>
    ipcRenderer.invoke("one-memory:get-debug-logs"),
  logUiTrace: (input: {
    action: string;
    page: string;
    phase: "action" | "result";
    status?: string;
    reason?: string;
    count?: number;
    duration_ms?: number;
    trace_id?: string;
  }): Promise<{ state: "logged" }> =>
    ipcRenderer.invoke("one-memory:log-ui-trace", input),
  markDiagnosticIncident: (
    description?: string,
  ): Promise<{
    incident_id: string;
    session_id: string;
    timestamp: string;
  }> => ipcRenderer.invoke("one-memory:mark-diagnostic-incident", description),
  exportDiagnostics: (): Promise<{
    state: "exported";
    filename?: string;
    path?: string;
    file_count?: number;
  }> => ipcRenderer.invoke("one-memory:export-diagnostics"),
  getBackgroundHealth: (): Promise<BackgroundHealth> =>
    ipcRenderer.invoke("one-memory:get-background-health"),
  restartBackground: (): Promise<{ state: "restarted" }> =>
    ipcRenderer.invoke("one-memory:restart-background"),
  getConnectionSetupCapability: (): Promise<ConnectionSetupCapability> =>
    ipcRenderer.invoke("one-memory:connection-setup-capability"),
  previewConnectionSetup: (
    input: ConnectionSetupSelection,
  ): Promise<ConnectionSetupPreview> =>
    ipcRenderer.invoke("one-memory:connection-setup-preview", input),
  executeConnectionSetup: (
    input: ConnectionSetupExecute,
  ): Promise<ConnectionSetupResult> =>
    ipcRenderer.invoke("one-memory:connection-setup-execute", input),
  getConnectionSetupReceipt: (
    operationId: string,
  ): Promise<ConnectionSetupJournal | null> =>
    ipcRenderer.invoke("one-memory:connection-setup-receipt", operationId),
  getLoginPreference: (): Promise<LoginPreference> =>
    ipcRenderer.invoke("one-memory:get-login-preference"),
  setLoginPreference: (input: {
    openAtLogin: boolean;
    userConfirmed: true;
  }): Promise<LoginPreference> =>
    ipcRenderer.invoke("one-memory:set-login-preference", input),
  requestQuit: (confirmed: true): Promise<void> =>
    ipcRenderer.invoke("one-memory:request-quit", confirmed),
  pickSourceDirectory: (sourceId: string): Promise<SourceDirectorySelection> =>
    ipcRenderer.invoke("one-memory:pick-source-directory", sourceId),
  getDisplayPreferences: async (): Promise<DisplayPreferenceSnapshot> => {
    const value = displayPreferencePayload(
      await ipcRenderer.invoke("one-memory:get-display-preferences"),
    );
    if (!value) throw new Error("display_preferences_unavailable");
    return value;
  },
  onDisplayPreferencesChanged: (
    listener: (value: DisplayPreferenceSnapshot) => void,
  ): (() => void) => {
    if (typeof listener !== "function")
      throw new Error("Invalid display preference listener");
    const handler = (_event: unknown, payload: unknown) => {
      const value = displayPreferencePayload(payload);
      if (value) listener(value);
    };
    let subscribed = true;
    const unsubscribe = () => {
      if (!subscribed) return;
      subscribed = false;
      ipcRenderer.removeListener(
        "one-memory:display-preferences-changed",
        handler,
      );
      window.removeEventListener("unload", unsubscribe);
    };
    ipcRenderer.on("one-memory:display-preferences-changed", handler);
    window.addEventListener("unload", unsubscribe, { once: true });
    return unsubscribe;
  },
  getApiToken: (): Promise<string> =>
    ipcRenderer.invoke("one-memory:get-api-token"),
  getApiBaseUrl: (): Promise<string> =>
    ipcRenderer.invoke("one-memory:get-api-base-url"),
  createCustomHarnessSkill: (
    displayName: string,
  ): Promise<{ displayName: string; expiresAt: string; skill: string }> =>
    ipcRenderer.invoke("one-memory:create-custom-harness-skill", displayName),
  copyText: (text: string): Promise<void> =>
    ipcRenderer.invoke("one-memory:copy-text", text),
  getRecallHosts: (): Promise<
    Array<{
      hostId: string;
      label: string;
      present: boolean;
      installed: boolean;
      permission: RecallPermissionStatus;
    }>
  > => ipcRenderer.invoke("one-memory:recall-hosts"),
  getRecallPermission: (hostId: string): Promise<RecallPermissionStatus> =>
    ipcRenderer.invoke("one-memory:get-recall-permission", hostId),
  getSyncHookStatus: (hostId: string): Promise<SyncHookStatus> =>
    ipcRenderer.invoke("one-memory:get-sync-hook-status", hostId),
  previewSyncHook: (
    hostId: string,
    operation: "install" | "uninstall",
  ): Promise<SyncHookPreview> =>
    ipcRenderer.invoke("one-memory:preview-sync-hook", hostId, operation),
  applySyncHook: (
    hostId: string,
    input: ApplySyncHookInput,
  ): Promise<SyncHookStatus> =>
    ipcRenderer.invoke("one-memory:apply-sync-hook", hostId, input),
  setRecallPermission: (
    hostId: string,
    input: SetRecallPermissionInput,
  ): Promise<RecallPermissionStatus> =>
    ipcRenderer.invoke("one-memory:set-recall-permission", hostId, input),
  revokeRecallPermission: (
    hostId: string,
    input: RevokeRecallPermissionInput,
  ): Promise<RecallPermissionStatus> =>
    ipcRenderer.invoke("one-memory:revoke-recall-permission", hostId, input),
  previewRecallInstall: (hostId: string): Promise<RecallInstallPreview> =>
    ipcRenderer.invoke("one-memory:preview-recall-install", hostId),
  /** Run the link self-check now, and say whether it can be repaired.
   *
   * `repoint` is non-null only when the host points somewhere other than
   * where the app writes. It is null when the app is merely not running,
   * because then the entry is already correct and there is nothing to repair.
   */
  probeRecallHost: (
    hostId: string,
  ): Promise<{
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
    repoint: { from: string | null; to: string } | null;
  }> => ipcRenderer.invoke("one-memory:probe-recall-host", hostId),
  previewSkillSync: (hostId: string) =>
    ipcRenderer.invoke("one-memory:preview-skill-sync", hostId),
  applySkillSync: (
    hostId: string,
    approved?: string,
    overwriteModified?: boolean,
  ) =>
    ipcRenderer.invoke(
      "one-memory:apply-skill-sync",
      hostId,
      approved,
      overwriteModified,
    ),
  repairRecallCommands: (): Promise<string[]> =>
    ipcRenderer.invoke("one-memory:repair-recall-commands"),
  installRecall: (
    hostId: string,
    /** The fingerprint from the preview the user approved, when there was one. */
    approved?: string,
  ): Promise<{
    hostId: string;
    installed: boolean;
    permission: RecallPermissionStatus;
  }> => ipcRenderer.invoke("one-memory:install-recall", hostId, approved),
  previewRecallUninstall: (hostId: string): Promise<RecallUninstallPreview> =>
    ipcRenderer.invoke("one-memory:preview-recall-uninstall", hostId),
  /** Open another AI app with these memories already in its input box.
   * Returns `opened: false` when that app cannot be started with a prompt,
   * so the caller can offer the text instead of opening an empty window. */
  openHandoff: (
    hostId: string,
    prompt: string,
  ): Promise<{ opened: boolean; reason: string | null }> =>
    ipcRenderer.invoke("one-memory:open-handoff", hostId, prompt),
  uninstallRecall: (
    hostId: string,
    /** The fingerprint from the preview the user approved, when there was one. */
    approved?: string,
  ): Promise<{
    hostId: string;
    installed: boolean;
    permission: RecallPermissionStatus;
  }> => ipcRenderer.invoke("one-memory:uninstall-recall", hostId, approved),
});
