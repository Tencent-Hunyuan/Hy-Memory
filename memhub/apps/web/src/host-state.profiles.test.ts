// @vitest-environment jsdom

/**
 * DeepSeek Harness is configured per profile: every `deepseek-harness:<profile>`
 * is its own host with its own patch, credential and grant. The app page has
 * one switch for all of them (#230). These pin what that switch does: on
 * installs and grants every detected profile, off removes and revokes every
 * linked one, a failing profile is named without undoing the others, and a
 * profile that appears later is picked up by the remembered switch.
 */

import { afterEach, describe, expect, it } from "vitest";
import { saveConnectionPreferences } from "./connection-preferences";
import {
  autoReconcile,
  resetConnectionReconcile,
} from "./connection-reconcile";
import { harnessBridge } from "./harness-profiles.test-fixture";
import {
  disconnectProfiles,
  enableHostConnection,
  hostWriteReason,
  ProfileWriteError,
  profileHosts,
  refreshRecallHosts,
  sourceConnectionApproved,
  sourceConnectionIncomplete,
} from "./host-state";

afterEach(() => {
  window.oneMemory = undefined;
  window.localStorage.clear();
  resetConnectionReconcile();
});

describe("DeepSeek Harness profile hosts", () => {
  it("lists only the profiles of the per-profile source", async () => {
    harnessBridge({ desktop: "not-installed", web: "approved" });
    const hosts = await refreshRecallHosts();
    expect(
      profileHosts(hosts, "deepseek-harness").map((host) => host.hostId),
    ).toEqual(["deepseek-harness:desktop", "deepseek-harness:web"]);
    expect(profileHosts(hosts, "cursor")).toEqual([]);
    // One approved profile reads as on; the other is still to do.
    expect(sourceConnectionApproved(hosts, "deepseek-harness")).toBe(true);
    expect(sourceConnectionIncomplete(hosts, "deepseek-harness")).toBe(true);
  });
});

describe("turning the DeepSeek Harness switch on", () => {
  it("installs and grants every profile that is not approved yet", async () => {
    const { api, state } = harnessBridge({
      desktop: "not-installed",
      headless: "revoked",
      web: "approved",
    });
    await refreshRecallHosts();
    await enableHostConnection("deepseek-harness");
    expect(api.installRecall.mock.calls.map(([hostId]) => hostId)).toEqual([
      "deepseek-harness:desktop",
      "deepseek-harness:headless",
    ]);
    expect(api.setRecallPermission).toHaveBeenCalledWith(
      "deepseek-harness:desktop",
      expect.objectContaining({
        allowedTools: ["om_recall", "om_conversations", "om_diagnostic_trace"],
        allowedScopes: ["all-apps", "source:deepseek-harness"],
        userConfirmed: true,
      }),
    );
    expect([...state.values()]).toEqual(["approved", "approved", "approved"]);
  });

  it("keeps the profiles that worked and names the one that failed", async () => {
    const { api, state } = harnessBridge(
      { desktop: "not-installed", web: "not-installed" },
      { failInstall: ["web"] },
    );
    await refreshRecallHosts();
    const error = await enableHostConnection("deepseek-harness").catch(
      (reason: unknown) => reason,
    );
    expect(error).toBeInstanceOf(ProfileWriteError);
    expect((error as ProfileWriteError).profiles).toEqual(["web"]);
    expect((error as Error).message).toContain("host_control_unavailable");
    expect(state.get("deepseek-harness:desktop")).toBe("approved");
    expect(state.get("deepseek-harness:web")).toBe("not-installed");
    expect(api.setRecallPermission).toHaveBeenCalledTimes(1);
    // The failure re-read lands the real state of every profile.
    const hosts = await refreshRecallHosts();
    expect(sourceConnectionApproved(hosts, "deepseek-harness")).toBe(true);
  });
});

describe("turning the DeepSeek Harness switch off", () => {
  it("removes and revokes every linked profile and leaves unlinked ones alone", async () => {
    const { api, state } = harnessBridge({
      desktop: "approved",
      headless: "not-installed",
      web: "approved",
    });
    await refreshRecallHosts();
    const results = await disconnectProfiles("deepseek-harness");
    expect(results.map((item) => [item.hostId, item.outcome.state])).toEqual([
      ["deepseek-harness:desktop", "removed"],
      ["deepseek-harness:web", "removed"],
    ]);
    expect(api.uninstallRecall).toHaveBeenCalledTimes(2);
    expect(state.get("deepseek-harness:desktop")).toBe("not-installed");
    expect(state.get("deepseek-harness:web")).toBe("not-installed");
  });

  it("revokes a profile whose files cannot be removed and still reaches the rest", async () => {
    const { api, state } = harnessBridge(
      { desktop: "approved", web: "approved" },
      { failUninstall: ["desktop"] },
    );
    await refreshRecallHosts();
    const results = await disconnectProfiles("deepseek-harness");
    expect(results.map((item) => item.outcome.state)).toEqual([
      "revoked",
      "removed",
    ]);
    expect(api.revokeRecallPermission).toHaveBeenCalledWith(
      "deepseek-harness:desktop",
      expect.objectContaining({ userConfirmed: true }),
    );
    expect(state.get("deepseek-harness:desktop")).toBe("revoked");
    expect(state.get("deepseek-harness:web")).toBe("not-installed");
  });
});

describe("a profile created after the switch was turned on", () => {
  it("is installed and granted by the remembered switch while the app is running", async () => {
    saveConnectionPreferences({
      syncSourceIds: [],
      hostSourceIds: ["deepseek-harness"],
    });
    const harness = harnessBridge({ desktop: "not-installed" });
    await refreshRecallHosts();
    expect(await autoReconcile({ sources: [] })).toEqual([]);
    expect(harness.api.installRecall).toHaveBeenCalledTimes(1);

    // One attempt was already spent on this source; the new profile gets its own.
    harness.addProfile("web");
    await refreshRecallHosts();
    expect(await autoReconcile({ sources: [] })).toEqual([]);
    expect(harness.api.installRecall.mock.calls.at(-1)).toEqual([
      "deepseek-harness:web",
      "fp-deepseek-harness:web",
    ]);
    expect(harness.state.get("deepseek-harness:web")).toBe("approved");
  });

  it("does not retry a failing profile on every poll", async () => {
    saveConnectionPreferences({
      syncSourceIds: [],
      hostSourceIds: ["deepseek-harness"],
    });
    const harness = harnessBridge(
      { desktop: "approved", web: "not-installed" },
      { failInstall: ["web"] },
    );
    await refreshRecallHosts();
    expect(await autoReconcile({ sources: [] })).toEqual([
      {
        kind: "host",
        sourceId: "deepseek-harness",
        reason: "应用控制通道暂不可用",
        profiles: ["web"],
      },
    ]);
    await refreshRecallHosts();
    expect(await autoReconcile({ sources: [] })).toBeNull();
    expect(harness.api.installRecall).toHaveBeenCalledTimes(1);
  });

  it("stays out while the switch is off", async () => {
    const harness = harnessBridge({ desktop: "not-installed" });
    await refreshRecallHosts();
    expect(await autoReconcile({ sources: [] })).toBeNull();
    harness.addProfile("web");
    await refreshRecallHosts();
    expect(await autoReconcile({ sources: [] })).toBeNull();
    expect(harness.api.installRecall).not.toHaveBeenCalled();
  });
});

describe("a profile whose name MemHub cannot grant", () => {
  it("is named with the real reason and does not hold up the others", async () => {
    const { state } = harnessBridge({
      My_Profile: "unsupported",
      web: "not-installed",
    });
    await refreshRecallHosts();
    const error = await enableHostConnection("deepseek-harness").catch(
      (reason: unknown) => reason,
    );
    expect((error as ProfileWriteError).profiles).toEqual(["My_Profile"]);
    expect(hostWriteReason(error)).toBe("appProfileNameUnsupported");
    expect(state.get("deepseek-harness:web")).toBe("approved");
    // Nothing is left for the remembered switch to keep trying.
    expect(
      sourceConnectionIncomplete(
        await refreshRecallHosts(),
        "deepseek-harness",
      ),
    ).toBe(false);
  });
});
