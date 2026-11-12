// @vitest-environment jsdom

import type { SourceResponse } from "@one-memory/contracts";
import { isMessageKey, SUPPORTED_LOCALES, translate } from "@one-memory/i18n";
import { afterEach, expect, it, vi } from "vitest";
import {
  readConnectionPreferences,
  saveConnectionPreferences,
} from "./connection-preferences";
import {
  associationStatus,
  autoReconcile,
  reconcileConnectionPreferences,
  resetConnectionReconcile,
} from "./connection-reconcile";
import {
  enableHostConnection,
  hostConnectionApproved,
  hostWriteReason,
  refreshRecallHosts,
} from "./host-state";
import { connectSourceSet } from "./source-state";

vi.mock("./api", async (original) => ({
  ...(await original<typeof import("./api")>()),
  connectSources: vi.fn(() => new Promise(() => {})),
}));

afterEach(() => {
  window.localStorage.clear();
  resetConnectionReconcile();
});

it("names the three association states used by app detail", () => {
  expect(associationStatus({ probing: true, approved: false })).toBe("检测中");
  expect(associationStatus({ probing: false, approved: true })).toBe(
    "成功关联",
  );
  expect(associationStatus({ probing: false, approved: false })).toBe(
    "未开启关联",
  );
  expect(
    associationStatus({
      probing: false,
      approved: false,
      intended: true,
    }),
  ).toBe("检测中");
});

it("writes a remembered WorkBuddy sync when the server flag is still off", async () => {
  saveConnectionPreferences({
    syncSourceIds: ["workbuddy"],
    hostSourceIds: [],
  });
  let enabled = false;
  const source = {
    source_id: "workbuddy",
    display_name: "WorkBuddy",
    authorized: true,
    sync_enabled: false,
  } as SourceResponse;
  const setSync = vi.fn(async () => {
    enabled = true;
  });
  const refresh = vi.fn(async () => ({
    sources: [{ ...source, sync_enabled: enabled }],
  }));
  await reconcileConnectionPreferences({
    sources: [source],
    setSync,
    refresh,
  });
  expect(setSync).toHaveBeenCalledWith(
    expect.objectContaining({ source_id: "workbuddy", sync_enabled: false }),
    true,
    expect.any(String),
  );
});

it("repoints an already approved host without installing again", async () => {
  saveConnectionPreferences({
    syncSourceIds: [],
    hostSourceIds: ["cursor"],
  });
  const repairRecallCommands = vi.fn(async () => ["cursor"]);
  const installRecall = vi.fn();
  window.oneMemory = {
    getRecallHosts: vi.fn(async () => [
      {
        hostId: "cursor",
        label: "Cursor",
        present: true,
        installed: true,
        permission: { state: "approved" },
      },
    ]),
    previewRecallInstall: vi.fn(),
    installRecall,
    getRecallPermission: vi.fn(),
    setRecallPermission: vi.fn(),
    repairRecallCommands,
  } as unknown as NonNullable<typeof window.oneMemory>;
  await reconcileConnectionPreferences({ sources: [] });
  expect(repairRecallCommands).toHaveBeenCalledTimes(1);
  expect(installRecall).not.toHaveBeenCalled();
});

/** A bridge whose host reports this permission state. The state is what the
 * write path acts on; the host id is only a fixture. */
function stateBridge(state: string) {
  const previewRecallInstall = vi.fn(async () => {
    throw Error("connection_client_unavailable");
  });
  window.oneMemory = {
    getRecallHosts: vi.fn(async () => [
      {
        hostId: "pi",
        label: "Pi",
        present: true,
        installed: false,
        permission: { hostId: "pi", state, reason: null, receipt: null },
      },
    ]),
    previewRecallInstall,
    installRecall: vi.fn(),
    getRecallPermission: vi.fn(),
    setRecallPermission: vi.fn(),
  } as unknown as NonNullable<typeof window.oneMemory>;
  return previewRecallInstall;
}

it("reports a host without a revocable connection as unsupported instead of previewing it", async () => {
  const previewRecallInstall = stateBridge("unsupported");
  await expect(enableHostConnection("pi")).rejects.toThrow("host_unsupported");
  expect(previewRecallInstall).not.toHaveBeenCalled();
});

it("stops retrying an unsupported host on later refreshes", async () => {
  saveConnectionPreferences({ syncSourceIds: [], hostSourceIds: ["pi"] });
  const previewRecallInstall = stateBridge("unsupported");
  await reconcileConnectionPreferences({ sources: [] });
  await reconcileConnectionPreferences({ sources: [] });
  expect(previewRecallInstall).not.toHaveBeenCalled();
  expect(readConnectionPreferences().hostSourceIds).toEqual([]);
});

it("keeps retrying a supported host whose preview failed", async () => {
  saveConnectionPreferences({ syncSourceIds: [], hostSourceIds: ["pi"] });
  const previewRecallInstall = stateBridge("not-installed");
  await reconcileConnectionPreferences({ sources: [] });
  await reconcileConnectionPreferences({ sources: [] });
  expect(previewRecallInstall).toHaveBeenCalledTimes(2);
  expect(readConnectionPreferences().hostSourceIds).toEqual(["pi"]);
});

it("keeps a remembered insertion for the desktop app when this shell has no bridge", async () => {
  saveConnectionPreferences({ syncSourceIds: [], hostSourceIds: ["pi"] });
  window.oneMemory = undefined;
  // A browser Console reads hosts as empty: nothing here can be granted.
  await refreshRecallHosts();
  const failures = await autoReconcile({ sources: [] });
  expect(failures).toEqual([]);
  expect(readConnectionPreferences().hostSourceIds).toEqual(["pi"]);
});

it("names the page, not the host, when there is no bridge to write through", async () => {
  window.oneMemory = undefined;
  await expect(enableHostConnection("pi")).rejects.toThrow(
    "desktop_bridge_unavailable",
  );
  const reason = hostWriteReason(Error("desktop_bridge_unavailable"));
  expect(reason).toBe("apps.desktopBridgeUnavailable");
  if (!reason || !isMessageKey(reason)) throw Error("reason is not a key");
  const source = "一键插入需要在 MemHub 桌面 App 中操作";
  expect(translate("zh-Hans", reason)).toBe(source);
  for (const locale of SUPPORTED_LOCALES) {
    expect(translate(locale, reason), locale).not.toBe(reason);
    if (locale !== "zh-Hans")
      expect(translate(locale, reason), locale).not.toBe(source);
  }
});

it("does not treat a relink-required host as admitted", () => {
  expect(
    hostConnectionApproved({
      installed: true,
      permission: { state: "relink-required" },
    } as never),
  ).toBe(false);
});

it("submits a remembered sync once and leaves a failure for the user instead of resubmitting", async () => {
  saveConnectionPreferences({
    syncSourceIds: ["workbuddy"],
    hostSourceIds: [],
  });
  const source = {
    source_id: "workbuddy",
    display_name: "WorkBuddy",
    authorized: true,
    sync_enabled: false,
  } as SourceResponse;
  const setSync = vi.fn(async () => {
    throw Error("此应用的持续同步设置未能确认，其他应用不受影响。");
  });
  const refresh = vi.fn(async () => ({ sources: [source] }));
  const failures = await autoReconcile({ sources: [source], setSync, refresh });
  expect(setSync).toHaveBeenCalledTimes(1);
  // One read before the write; the write layer does its own single re-read.
  expect(refresh).toHaveBeenCalledTimes(1);
  expect(failures).toEqual([
    {
      kind: "sync",
      sourceId: "workbuddy",
      reason: "此应用的持续同步设置未能确认，其他应用不受影响。",
    },
  ]);
  await autoReconcile({ sources: [source], setSync, refresh });
  expect(setSync).toHaveBeenCalledTimes(1);
});

it("does not start a remembered insertion while a source write is running", async () => {
  saveConnectionPreferences({ syncSourceIds: [], hostSourceIds: ["cursor"] });
  const previewRecallInstall = vi.fn(async () => {
    throw Error("connection_client_unavailable");
  });
  window.oneMemory = {
    getRecallHosts: vi.fn(async () => [
      {
        hostId: "cursor",
        label: "Cursor",
        present: true,
        installed: false,
        permission: {
          hostId: "cursor",
          state: "not-installed",
          reason: null,
          receipt: null,
        },
        selfCheck: null,
        skillDrift: null,
      },
    ]),
    previewRecallInstall,
    installRecall: vi.fn(),
    getRecallPermission: vi.fn(),
    setRecallPermission: vi.fn(),
  } as unknown as NonNullable<typeof window.oneMemory>;
  await refreshRecallHosts();
  void connectSourceSet([
    ["cursor"],
    true,
    {
      requestId: "00000000-0000-4000-8000-000000000001",
      sourceRoots: { cursor: "root" },
    },
  ]);
  expect(await autoReconcile({ sources: [] })).toBeNull();
  expect(previewRecallInstall).not.toHaveBeenCalled();
  window.oneMemory = undefined;
});

it("does not queue a remembered insertion behind a host write that is still running", async () => {
  saveConnectionPreferences({ syncSourceIds: [], hostSourceIds: ["cursor"] });
  const previewRecallInstall = vi.fn(() => new Promise(() => {}));
  window.oneMemory = {
    getRecallHosts: vi.fn(async () => [
      {
        hostId: "cursor",
        label: "Cursor",
        present: true,
        installed: false,
        permission: {
          hostId: "cursor",
          state: "not-installed",
          reason: null,
          receipt: null,
        },
        selfCheck: null,
        skillDrift: null,
      },
    ]),
    previewRecallInstall,
    installRecall: vi.fn(),
    getRecallPermission: vi.fn(),
    setRecallPermission: vi.fn(),
  } as unknown as NonNullable<typeof window.oneMemory>;
  void enableHostConnection("cursor").catch(() => {});
  await vi.waitFor(() => expect(previewRecallInstall).toHaveBeenCalledOnce());
  const started = await Promise.race([
    autoReconcile({ sources: [] }),
    new Promise<"queued">((done) => setTimeout(() => done("queued"), 100)),
  ]);
  expect(started).toBeNull();
  window.oneMemory = undefined;
});
