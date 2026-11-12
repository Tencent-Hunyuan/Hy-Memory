/** A fake desktop bridge holding DeepSeek Harness profile hosts, for tests. */

import { vi } from "vitest";
import type { RecallHostStatus, RecallPermissionStatus } from "./global";

type Bridge = NonNullable<typeof window.oneMemory>;
type Grant =
  | "not-installed"
  | "install-only"
  | "approved"
  | "revoked"
  | "unsupported";

function permission(hostId: string, state: Grant): RecallPermissionStatus {
  return {
    hostId,
    state,
    reason: null,
    receipt:
      state === "not-installed" || state === "unsupported"
        ? null
        : {
            hostId,
            installationId: `install-${hostId}`,
            revision: 1,
            status: state,
            allowedTools: state === "approved" ? ["om_recall"] : [],
            allowedScopes: state === "approved" ? ["all-apps"] : [],
          },
  };
}

/** A desktop that knows a set of Harness profiles and keeps their state. */
export function harnessBridge(
  initial: Record<string, Grant>,
  options: { failInstall?: string[]; failUninstall?: string[] } = {},
) {
  const state = new Map(
    Object.entries(initial).map(([profile, grant]) => [
      `deepseek-harness:${profile}`,
      grant,
    ]),
  );
  const hosts = (): RecallHostStatus[] =>
    [...state].map(([hostId, grant]) => ({
      hostId,
      label: `DeepSeek Harness · ${hostId.split(":")[1]}`,
      present: true,
      installed: grant === "install-only" || grant === "approved",
      permission: permission(hostId, grant),
      // Recorded by the desktop when the grant lands, as on a real install.
      selfCheck:
        grant === "approved"
          ? { state: "wired", detail: "", probed_at: "2026-11-02" }
          : null,
      skillDrift: null,
    }));
  const failing = (hostId: string, list: string[] = []) =>
    list.some((profile) => hostId === `deepseek-harness:${profile}`);
  const api = {
    getRecallHosts: vi.fn(async () => hosts()),
    getRecallPermission: vi.fn(async (hostId: string) =>
      permission(hostId, state.get(hostId) ?? "not-installed"),
    ),
    previewRecallInstall: vi.fn(async (hostId: string) => ({
      hostId,
      hostLabel: hostId,
      files: [],
      fingerprint: `fp-${hostId}`,
    })),
    installRecall: vi.fn(async (hostId: string, _approved?: string) => {
      if (failing(hostId, options.failInstall))
        throw Error(
          "Error invoking remote method 'one-memory:install-recall': Error: host_control_unavailable",
        );
      state.set(hostId, "install-only");
      return {
        hostId,
        installed: true,
        permission: permission(hostId, "install-only"),
      };
    }),
    setRecallPermission: vi.fn(async (hostId: string, _input: unknown) => {
      state.set(hostId, "approved");
      return permission(hostId, "approved");
    }),
    revokeRecallPermission: vi.fn(async (hostId: string, _input: unknown) => {
      state.set(hostId, "revoked");
      return permission(hostId, "revoked");
    }),
    previewRecallUninstall: vi.fn(async (hostId: string) => ({
      hostId,
      hostLabel: hostId,
      files: [],
      review: [],
      fingerprint: `fp-${hostId}`,
    })),
    uninstallRecall: vi.fn(async (hostId: string, _approved?: string) => {
      if (failing(hostId, options.failUninstall))
        throw Error(
          "Error invoking remote method 'one-memory:uninstall-recall': Error: host_control_unavailable",
        );
      state.set(hostId, "not-installed");
      return {
        hostId,
        installed: false,
        permission: permission(hostId, "not-installed"),
      };
    }),
    repairRecallCommands: vi.fn(async () => []),
  };
  window.oneMemory = api as unknown as Bridge;
  return {
    api,
    state,
    addProfile(profile: string) {
      state.set(`deepseek-harness:${profile}`, "not-installed");
    },
  };
}
