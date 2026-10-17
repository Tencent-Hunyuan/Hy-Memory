// @vitest-environment jsdom

import type { SourceResponse } from "@one-memory/contracts";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ApplicationDetail } from "./ApplicationDetail";
import { saveConnectionPreferences } from "./connection-preferences";
import { resetConnectionReconcile } from "./connection-reconcile";
import { commitSources } from "./source-state";

vi.mock("./RecallPermissionControl", () => ({
  RecallPermissionControl: ({ host }: { host: { hostId: string } }) => (
    <span>授权控件 {host.hostId}</span>
  ),
}));
const source = {
  source_id: "cursor",
  display_name: "Cursor",
  authorized: true,
  sync_enabled: true,
  capabilities: {
    historical_import: "implemented",
    incremental_import: "implemented",
    live_sync: "implemented",
    recall: "implemented",
    installer: "implemented",
    verification: "real_replayed",
  },
} as SourceResponse;
const props = {
  source,
  sources: [source],
  stale: false,
  setSync: vi.fn(),
  refresh: vi.fn(),
  onBack: vi.fn(),
};
function statusControl() {
  return screen.getByRole("button", {
    name: /检测中|已接通|未接通|未开启关联|已安装，但取不到记忆/,
  });
}
afterEach(() => {
  cleanup();
  window.oneMemory = undefined;
  window.localStorage.clear();
  resetConnectionReconcile();
  vi.clearAllMocks();
});
beforeEach(() => {
  window.localStorage.clear();
  resetConnectionReconcile();
});
it("returns to the application home immediately", () => {
  render(<ApplicationDetail {...props} />);
  fireEvent.click(screen.getByRole("button", { name: "返回应用" }));
  expect(props.onBack).toHaveBeenCalledOnce();
});
it("matches only an exact host identity and never installs on entry", async () => {
  const install = vi.fn();
  window.oneMemory = {
    getRecallHosts: vi
      .fn()
      .mockResolvedValue([
        { hostId: "different-cursor", label: "Cursor", installed: true },
      ]),
    installRecall: install,
  } as unknown as NonNullable<typeof window.oneMemory>;
  render(<ApplicationDetail {...props} />);
  await screen.findByText("未开启关联");
  expect(screen.queryByText(/授权控件/)).toBeNull();
  expect(install).not.toHaveBeenCalled();
});
it("does not report host read errors as missing installation", async () => {
  window.oneMemory = {
    getRecallHosts: vi.fn().mockRejectedValue(new Error("offline")),
  } as unknown as NonNullable<typeof window.oneMemory>;
  render(<ApplicationDetail {...props} />);
  await screen.findByText("检测中");
  expect(screen.queryByText("未开启关联")).toBeNull();
});

it("does not leave a failed host read looking like an endless probe", async () => {
  window.oneMemory = {
    getRecallHosts: vi.fn().mockRejectedValue(new Error("offline")),
  } as unknown as NonNullable<typeof window.oneMemory>;
  render(
    <ApplicationDetail
      {...props}
      refresh={vi.fn().mockResolvedValue({ sources: [source] })}
    />,
  );

  expect(await screen.findByText("状态待核对")).toBeTruthy();
  expect(screen.queryByText("检测中")).toBeNull();
  expect(screen.getByText(/暂时无法读取连接状态/)).toBeTruthy();
});

it("does not fall back to a previously wired self-check after a failed new probe", async () => {
  window.oneMemory = {
    getRecallHosts: vi.fn().mockResolvedValue([
      {
        hostId: "cursor",
        label: "Cursor",
        present: true,
        installed: true,
        selfCheck: {
          state: "wired",
          detail: "previous success",
          probed_at: "2026-09-01T00:00:00Z",
        },
      },
    ]),
    probeRecallHost: vi.fn().mockRejectedValue(new Error("offline")),
  } as unknown as NonNullable<typeof window.oneMemory>;
  render(
    <ApplicationDetail
      {...props}
      refresh={vi.fn().mockResolvedValue({ sources: [source] })}
    />,
  );

  expect(await screen.findByText("状态待核对")).toBeTruthy();
  expect(screen.queryByText("已接通")).toBeNull();
  expect(screen.getByText(/本次连接探测失败/)).toBeTruthy();
});
it("installs and grants one-click insertion directly from app detail", async () => {
  const permission = {
    hostId: "cursor",
    state: "install-only",
    reason: null,
    receipt: {
      hostId: "cursor",
      installationId: "install-1",
      revision: 1,
      status: "install-only",
      allowedTools: [],
      allowedScopes: [],
    },
  };
  const approved = {
    ...permission,
    state: "approved",
    receipt: { ...permission.receipt, revision: 2, status: "approved" },
  };
  let installed = false;
  const getRecallHosts = vi.fn(async () => [
    {
      hostId: "cursor",
      label: "Cursor",
      present: true,
      installed,
      permission: installed ? approved : permission,
    },
  ]);
  const installRecall = vi.fn().mockImplementation(async () => {
    installed = true;
    return {
      hostId: "cursor",
      installed: true,
      permission,
    };
  });
  const setRecallPermission = vi.fn().mockResolvedValue(approved);
  window.oneMemory = {
    getRecallHosts,
    previewRecallInstall: vi.fn().mockResolvedValue({
      hostId: "cursor",
      fingerprint: "fingerprint",
      files: [],
    }),
    installRecall,
    getRecallPermission: vi.fn().mockResolvedValue(permission),
    setRecallPermission,
  } as unknown as NonNullable<typeof window.oneMemory>;
  render(
    <ApplicationDetail
      {...props}
      refresh={vi.fn().mockResolvedValue({ sources: [source] })}
    />,
  );
  const insertion = await screen.findByRole("switch", { name: "一键插入应用" });
  await waitFor(() =>
    expect(insertion.getAttribute("aria-checked")).toBe("false"),
  );
  fireEvent.click(insertion);
  await waitFor(() =>
    expect(installRecall).toHaveBeenCalledWith("cursor", "fingerprint"),
  );
  expect(setRecallPermission).toHaveBeenCalledWith(
    "cursor",
    expect.objectContaining({
      installationId: "install-1",
      expectedRevision: 1,
      userConfirmed: true,
    }),
  );
});
it("disables every mutation while a stale snapshot refreshes", () => {
  render(<ApplicationDetail {...props} stale />);
  for (const name of ["同步开关", "一键插入应用"]) {
    expect(screen.getByRole("switch", { name })).toHaveProperty(
      "disabled",
      true,
    );
  }
  expect(screen.getByRole("switch", { name: "同步开关" })).toBeTruthy();
  expect(statusControl()).toHaveProperty("disabled", false);
  expect(screen.queryByRole("button", { name: /已保存原文/ })).toBeNull();
});

it("does not write sync or host permission from a stale detail", () => {
  const setSync = vi.fn();
  const permission = vi.fn();
  window.oneMemory = {
    getRecallHosts: vi.fn().mockResolvedValue([]),
    setRecallPermission: permission,
  } as unknown as NonNullable<typeof window.oneMemory>;
  render(<ApplicationDetail {...props} stale setSync={setSync} />);
  fireEvent.click(screen.getByRole("switch", { name: "同步开关" }));
  expect(setSync).not.toHaveBeenCalled();
  expect(permission).not.toHaveBeenCalled();
});

it("lets a confirmed sync write continue after its follow-up read turns the snapshot stale", async () => {
  const setSync = vi.fn().mockResolvedValue(undefined);
  window.oneMemory = {
    getRecallHosts: vi.fn().mockResolvedValue([]),
  } as unknown as NonNullable<typeof window.oneMemory>;
  const view = render(
    <ApplicationDetail {...props} stale syncWriteConfirmed setSync={setSync} />,
  );
  const sync = screen.getByRole("switch", { name: "同步开关" });
  expect(sync).toHaveProperty("disabled", false);
  fireEvent.click(sync);
  await waitFor(() =>
    expect(setSync).toHaveBeenCalledWith(
      expect.objectContaining({ source_id: "cursor" }),
      false,
    ),
  );
  view.rerender(<ApplicationDetail {...props} stale setSync={setSync} />);
  expect(screen.getByRole("switch", { name: "同步开关" })).toHaveProperty(
    "disabled",
    true,
  );
});

it("refreshes the source snapshot on entry without granting permissions", async () => {
  const refresh = vi.fn().mockResolvedValue({ sources: [source] });
  window.oneMemory = {
    getRecallHosts: vi.fn().mockResolvedValue([]),
  } as unknown as NonNullable<typeof window.oneMemory>;
  render(<ApplicationDetail {...props} refresh={refresh} />);
  await waitFor(() => expect(refresh).toHaveBeenCalledOnce());
  expect(props.setSync).not.toHaveBeenCalled();
});

it("retries the source snapshot and host read without granting permissions", async () => {
  const hosts = vi.fn().mockResolvedValue([]);
  window.oneMemory = { getRecallHosts: hosts } as unknown as NonNullable<
    typeof window.oneMemory
  >;
  const refreshed = vi.fn();
  function Parent() {
    const [stale, setStale] = useState(true);
    return (
      <ApplicationDetail
        {...props}
        stale={stale}
        refresh={async () => {
          refreshed();
          setStale(false);
          return { sources: [source] };
        }}
      />
    );
  }
  render(<Parent />);
  await waitFor(() => expect(hosts).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(refreshed).toHaveBeenCalledOnce());
  await act(async () => {
    fireEvent.click(statusControl());
  });
  expect(refreshed).toHaveBeenCalledTimes(2);
  expect(hosts).toHaveBeenCalledTimes(2);
  expect(props.setSync).not.toHaveBeenCalled();
});

it("retains failed source protection despite a successful host read and permits retry", async () => {
  const refresh = vi.fn().mockRejectedValue(new Error("private details"));
  window.oneMemory = {
    getRecallHosts: vi.fn().mockResolvedValue([]),
  } as unknown as NonNullable<typeof window.oneMemory>;
  render(<ApplicationDetail {...props} stale refresh={refresh} />);
  await waitFor(() => expect(refresh).toHaveBeenCalledOnce());
  expect(screen.getByRole("alert").textContent).not.toContain(
    "private details",
  );
  await act(async () => {
    fireEvent.click(statusControl());
  });
  expect(refresh).toHaveBeenCalledTimes(2);
  expect(screen.getByRole("alert").textContent).not.toContain(
    "private details",
  );
  await act(async () => {
    fireEvent.click(statusControl());
  });
  expect(refresh).toHaveBeenCalledTimes(3);
});

it("coalesces source retries and ignores their late failure after switching applications", async () => {
  const fails: Array<(reason: Error) => void> = [];
  const refresh = vi.fn(() => {
    if (fails.length === 0) {
      return new Promise<{ sources: SourceResponse[] }>((_, fail) => {
        fails.push(fail);
      });
    }
    return Promise.resolve({ sources: [source] });
  });
  const view = render(<ApplicationDetail {...props} stale refresh={refresh} />);
  await waitFor(() => expect(refresh).toHaveBeenCalledOnce());
  await act(async () => {
    fireEvent.click(statusControl());
    fireEvent.click(statusControl());
  });
  expect(refresh).toHaveBeenCalledOnce();
  view.rerender(
    <ApplicationDetail
      {...props}
      source={{ ...source, source_id: "codex", display_name: "Codex" }}
      refresh={refresh}
    />,
  );
  await waitFor(() => expect(refresh).toHaveBeenCalledTimes(2));
  await act(async () => {
    fails[0](new Error("old failure"));
  });
  expect(screen.queryByRole("alert")).toBeNull();
  expect(statusControl()).toHaveProperty("disabled", false);
});

it("lets insertion turn off and on again without getting stuck on stale host state", async () => {
  const permission = {
    hostId: "cursor",
    state: "approved",
    reason: null,
    receipt: {
      hostId: "cursor",
      installationId: "install-1",
      revision: 2,
      status: "approved",
      allowedTools: ["om_recall"],
      allowedScopes: ["all-apps"],
    },
  };
  const getRecallHosts = vi.fn().mockResolvedValue([
    {
      hostId: "cursor",
      label: "Cursor",
      present: true,
      installed: true,
      permission,
    },
  ]);
  const previewRecallUninstall = vi.fn().mockResolvedValue({
    hostId: "cursor",
    fingerprint: "fingerprint",
  });
  const uninstallRecall = vi.fn().mockResolvedValue({
    hostId: "cursor",
    installed: false,
  });
  const previewRecallInstall = vi.fn().mockResolvedValue({
    hostId: "cursor",
    fingerprint: "fingerprint",
    files: [],
  });
  const installRecall = vi.fn().mockResolvedValue({
    hostId: "cursor",
    installed: true,
    permission,
  });
  window.oneMemory = {
    getRecallHosts,
    previewRecallUninstall,
    uninstallRecall,
    previewRecallInstall,
    installRecall,
    getRecallPermission: vi.fn().mockResolvedValue(permission),
    setRecallPermission: vi.fn().mockResolvedValue(permission),
    revokeRecallPermission: vi.fn(),
  } as unknown as NonNullable<typeof window.oneMemory>;
  render(
    <ApplicationDetail
      {...props}
      refresh={vi.fn().mockResolvedValue({ sources: [source] })}
    />,
  );
  const insertion = await screen.findByRole("switch", { name: "一键插入应用" });
  await waitFor(() =>
    expect(insertion.getAttribute("aria-checked")).toBe("true"),
  );
  fireEvent.click(insertion);
  await waitFor(() =>
    expect(uninstallRecall).toHaveBeenCalledWith("cursor", "fingerprint"),
  );
  expect(insertion.getAttribute("aria-checked")).toBe("false");
  getRecallHosts.mockResolvedValue([
    {
      hostId: "cursor",
      label: "Cursor",
      present: true,
      installed: false,
    },
  ]);
  fireEvent.click(insertion);
  await waitFor(() =>
    expect(installRecall).toHaveBeenCalledWith("cursor", "fingerprint"),
  );
});

function editedHostBridge(options: { revokeFails: boolean }) {
  let status = "approved";
  const receipt = () => ({
    hostId: "opencode",
    installationId: "opencode-install",
    revision: 4,
    status,
    allowedTools: ["om_recall"],
    allowedScopes: ["all-apps"],
  });
  const permission = () => ({
    hostId: "opencode",
    state: status,
    reason: null,
    receipt: receipt(),
  });
  const revokeRecallPermission = vi.fn(async () => {
    if (options.revokeFails) throw Error("host_control_unavailable");
    status = "revoked";
    return permission();
  });
  const uninstallRecall = vi.fn();
  window.oneMemory = {
    getRecallHosts: vi.fn(async () => [
      {
        hostId: "opencode",
        label: "OpenCode",
        present: true,
        installed: true,
        permission: permission(),
      },
    ]),
    previewRecallUninstall: vi.fn(async () => {
      throw Error(
        "Error invoking remote method 'one-memory:preview-recall-uninstall': Error: host_uninstall_modified_files",
      );
    }),
    uninstallRecall,
    previewRecallInstall: vi.fn(),
    installRecall: vi.fn(),
    getRecallPermission: vi.fn(async () => permission()),
    setRecallPermission: vi.fn(),
    revokeRecallPermission,
  } as unknown as NonNullable<typeof window.oneMemory>;
  return { revokeRecallPermission, uninstallRecall };
}
const opencode = {
  ...source,
  source_id: "opencode",
  display_name: "OpenCode",
} as SourceResponse;

it("names the file and place to check when turning an app off cannot be confirmed", async () => {
  const bridge = editedHostBridge({ revokeFails: false });
  const oneMemory = window.oneMemory as unknown as Record<string, unknown>;
  oneMemory.previewRecallUninstall = vi.fn(async () => ({
    hostId: "opencode",
    hostLabel: "OpenCode",
    files: [],
    review: [
      {
        path: "/home/me/.config/opencode/plugin/one-memory-capture.js",
        where: "文件内容与安装时不同",
      },
    ],
    fingerprint: "fp",
  }));
  render(
    <ApplicationDetail
      {...props}
      source={opencode}
      sources={[opencode]}
      refresh={vi.fn().mockResolvedValue({ sources: [opencode] })}
    />,
  );
  const insertion = await screen.findByRole("switch", { name: "一键插入应用" });
  await waitFor(() =>
    expect(insertion.getAttribute("aria-checked")).toBe("true"),
  );
  fireEvent.click(insertion);
  expect(
    await screen.findByText(
      "已撤销记忆权限，OpenCode 已查不到记忆。但无法确认连接已全部移除，需要你手动核对：/home/me/.config/opencode/plugin/one-memory-capture.js（文件内容与安装时不同）。",
    ),
  ).toBeTruthy();
  expect(bridge.uninstallRecall).not.toHaveBeenCalled();
});

it("revokes an app whose config was edited when its connection cannot be removed", async () => {
  const bridge = editedHostBridge({ revokeFails: false });
  render(
    <ApplicationDetail
      {...props}
      source={opencode}
      sources={[opencode]}
      refresh={vi.fn().mockResolvedValue({ sources: [opencode] })}
    />,
  );
  const insertion = await screen.findByRole("switch", { name: "一键插入应用" });
  await waitFor(() =>
    expect(insertion.getAttribute("aria-checked")).toBe("true"),
  );
  fireEvent.click(insertion);
  await waitFor(() =>
    expect(bridge.revokeRecallPermission).toHaveBeenCalledWith("opencode", {
      installationId: "opencode-install",
      expectedRevision: 4,
      userConfirmed: true,
    }),
  );
  expect(bridge.uninstallRecall).not.toHaveBeenCalled();
  expect(
    await screen.findByText(
      "已撤销记忆权限，OpenCode 已查不到记忆。配置文件被改过，MemHub 没有自动删除；请手动移除其中的 MemHub 连接。",
    ),
  ).toBeTruthy();
  await waitFor(() =>
    expect(insertion.getAttribute("aria-checked")).toBe("false"),
  );
});

it("shows insertion still on when neither removal nor revocation succeeded", async () => {
  editedHostBridge({ revokeFails: true });
  render(
    <ApplicationDetail
      {...props}
      source={opencode}
      sources={[opencode]}
      refresh={vi.fn().mockResolvedValue({ sources: [opencode] })}
    />,
  );
  const insertion = await screen.findByRole("switch", { name: "一键插入应用" });
  await waitFor(() =>
    expect(insertion.getAttribute("aria-checked")).toBe("true"),
  );
  fireEvent.click(insertion);
  expect(
    await screen.findByText(
      "连接未能移除，记忆权限也未能撤销，OpenCode 仍可使用记忆，请重试。原因：应用控制通道暂不可用。",
    ),
  ).toBeTruthy();
  await waitFor(() =>
    expect(insertion.getAttribute("aria-checked")).toBe("true"),
  );
});

it("explains why an app without a revocable connection cannot be inserted", async () => {
  const pi = {
    ...source,
    source_id: "pi",
    display_name: "Pi",
  } as SourceResponse;
  const previewRecallInstall = vi.fn();
  window.oneMemory = {
    getRecallHosts: vi.fn().mockResolvedValue([
      {
        hostId: "pi",
        label: "Pi",
        present: true,
        installed: false,
        permission: {
          hostId: "pi",
          state: "unsupported",
          reason: "unsupported_transport",
          receipt: null,
        },
      },
    ]),
    previewRecallInstall,
    installRecall: vi.fn(),
    getRecallPermission: vi.fn(),
    setRecallPermission: vi.fn(),
    revokeRecallPermission: vi.fn(),
  } as unknown as NonNullable<typeof window.oneMemory>;
  render(
    <ApplicationDetail
      {...props}
      source={pi}
      sources={[pi]}
      refresh={vi.fn().mockResolvedValue({ sources: [pi] })}
    />,
  );
  expect(
    await screen.findByText(
      "Pi 暂不支持一键插入：它没有 MCP 通道，无法发放可撤销的记忆凭证。",
    ),
  ).toBeTruthy();
  const insertion = screen.getByRole("switch", { name: "一键插入应用" });
  await waitFor(() => expect(insertion).toHaveProperty("disabled", true));
  expect(previewRecallInstall).not.toHaveBeenCalled();
});

it("waits for an explicit click before applying a cold-start insertion preference", async () => {
  saveConnectionPreferences({
    syncSourceIds: ["cursor"],
    hostSourceIds: ["cursor"],
  });
  const permission = {
    hostId: "cursor",
    state: "relink-required",
    reason: "credential_missing_or_changed",
    receipt: {
      hostId: "cursor",
      installationId: "install-1",
      revision: 1,
      status: "install-only",
      allowedTools: [],
      allowedScopes: [],
    },
  };
  const approved = {
    ...permission,
    state: "approved",
    receipt: { ...permission.receipt, revision: 2, status: "approved" },
  };
  let linked = false;
  const getRecallHosts = vi.fn(async () => [
    {
      hostId: "cursor",
      label: "Cursor",
      present: linked,
      installed: true,
      permission: linked ? approved : permission,
    },
  ]);
  const installRecall = vi.fn().mockImplementation(async () => {
    linked = true;
    return {
      hostId: "cursor",
      installed: true,
      permission,
    };
  });
  window.oneMemory = {
    getRecallHosts,
    previewRecallInstall: vi.fn().mockResolvedValue({
      hostId: "cursor",
      fingerprint: "fingerprint",
      files: [],
    }),
    installRecall,
    getRecallPermission: vi.fn().mockResolvedValue(permission),
    setRecallPermission: vi.fn().mockResolvedValue(approved),
    probeRecallHost: vi.fn().mockResolvedValue({
      hostId: "cursor",
      probe: {
        state: "wired",
        detail: "recalled 0 memories for the self-check",
        discoveryPath: null,
        discoveryPresent: true,
        probedAt: "2026-09-22T00:00:00.000Z",
      },
      skill: null,
      repoint: null,
    }),
  } as unknown as NonNullable<typeof window.oneMemory>;
  render(
    <ApplicationDetail
      {...props}
      refresh={vi.fn().mockResolvedValue({ sources: [source] })}
    />,
  );
  const insertion = await screen.findByRole("switch", {
    name: "一键插入应用",
  });
  expect(insertion.getAttribute("aria-checked")).toBe("false");
  expect(screen.queryByRole("button", { name: "检查连接" })).toBeNull();
  expect(installRecall).not.toHaveBeenCalled();
  fireEvent.click(insertion);
  await waitFor(() =>
    expect(installRecall).toHaveBeenCalledWith("cursor", "fingerprint"),
  );
  expect(await screen.findByText("已接通")).toBeTruthy();
});

it("does not call an approved install connected when the recall probe cannot start", async () => {
  saveConnectionPreferences({
    syncSourceIds: ["cursor"],
    hostSourceIds: ["cursor"],
  });
  window.oneMemory = {
    getRecallHosts: vi.fn().mockResolvedValue([
      {
        hostId: "cursor",
        label: "Cursor",
        present: true,
        installed: true,
        permission: {
          hostId: "cursor",
          state: "approved",
          reason: null,
          receipt: {
            hostId: "cursor",
            installationId: "install-1",
            revision: 2,
            status: "approved",
            allowedTools: ["om_recall"],
            allowedScopes: ["all-apps"],
          },
        },
        selfCheck: null,
      },
    ]),
    probeRecallHost: vi.fn().mockResolvedValue({
      hostId: "cursor",
      probe: {
        state: "unreachable",
        detail: "the host's recall server could not be started",
        discoveryPath: null,
        discoveryPresent: false,
        probedAt: "2026-09-22T00:00:00.000Z",
      },
      skill: null,
      repoint: null,
    }),
  } as unknown as NonNullable<typeof window.oneMemory>;
  render(
    <ApplicationDetail
      {...props}
      refresh={vi.fn().mockResolvedValue({ sources: [source] })}
    />,
  );
  expect(await screen.findByText("未接通")).toBeTruthy();
  expect(screen.queryByText("成功关联")).toBeNull();
  expect(
    screen.getByText(
      "记忆工具没有进到 Cursor 当前对话。完全退出 Cursor 后重新打开，新对话里才会出现。",
    ),
  ).toBeTruthy();
});

it("names the open application when the recall tool is not in its current chat", async () => {
  const item = {
    ...source,
    source_id: "workbuddy",
    display_name: "WorkBuddy",
  };
  window.oneMemory = {
    getRecallHosts: vi.fn().mockResolvedValue([
      {
        hostId: "workbuddy",
        label: "WorkBuddy",
        present: true,
        installed: true,
        permission: {
          hostId: "workbuddy",
          state: "approved",
          reason: null,
          receipt: {
            hostId: "workbuddy",
            installationId: "install-1",
            revision: 2,
            status: "approved",
            allowedTools: ["om_recall"],
            allowedScopes: ["all-apps"],
          },
        },
        selfCheck: null,
      },
    ]),
    probeRecallHost: vi.fn().mockResolvedValue({
      hostId: "workbuddy",
      probe: {
        state: "unreachable",
        detail: "the host's recall server could not be started",
        discoveryPath: null,
        discoveryPresent: false,
        probedAt: "2026-09-22T00:00:00.000Z",
      },
      skill: null,
      repoint: null,
    }),
  } as unknown as NonNullable<typeof window.oneMemory>;
  render(
    <ApplicationDetail
      {...props}
      source={item}
      sources={[item]}
      refresh={vi.fn().mockResolvedValue({ sources: [item] })}
    />,
  );
  expect(
    await screen.findByText(
      "记忆工具没有进到 WorkBuddy 当前对话。完全退出 WorkBuddy 后重新打开，新对话里才会出现。",
    ),
  ).toBeTruthy();
  expect(screen.queryByText(/Cursor/)).toBeNull();
});

it("does not auto-install a cold-start remembered host on page entry", async () => {
  saveConnectionPreferences({
    syncSourceIds: ["claude"],
    hostSourceIds: ["claude"],
  });
  const item = {
    ...source,
    source_id: "claude-code",
    display_name: "Claude Code",
  };
  const permission = {
    hostId: "claude-code",
    state: "install-only",
    reason: null,
    receipt: {
      hostId: "claude-code",
      installationId: "install-1",
      revision: 1,
      status: "install-only",
      allowedTools: [],
      allowedScopes: [],
    },
  };
  const approved = {
    ...permission,
    state: "approved",
    receipt: { ...permission.receipt, revision: 2, status: "approved" },
  };
  let linked = false;
  const getRecallHosts = vi.fn(async () => [
    {
      hostId: "claude-code",
      label: "Claude Code",
      present: true,
      installed: linked,
      permission: linked ? approved : permission,
    },
  ]);
  const installRecall = vi.fn().mockImplementation(async () => {
    linked = true;
    return {
      hostId: "claude-code",
      installed: true,
      permission,
    };
  });
  window.oneMemory = {
    getRecallHosts,
    previewRecallInstall: vi.fn().mockResolvedValue({
      hostId: "claude-code",
      fingerprint: "fingerprint",
      files: [],
    }),
    installRecall,
    getRecallPermission: vi.fn().mockResolvedValue(permission),
    setRecallPermission: vi.fn().mockResolvedValue(approved),
  } as unknown as NonNullable<typeof window.oneMemory>;
  render(
    <ApplicationDetail
      {...props}
      source={item}
      sources={[item]}
      refresh={vi.fn().mockResolvedValue({ sources: [item] })}
    />,
  );
  const insertion = await screen.findByRole("switch", {
    name: "一键插入应用",
  });
  expect(insertion.getAttribute("aria-checked")).toBe("false");
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(installRecall).not.toHaveBeenCalled();
  fireEvent.click(insertion);
  await waitFor(() =>
    expect(installRecall).toHaveBeenCalledWith("claude-code", "fingerprint"),
  );
});

it("lets sync turn on and off again without leaving the page", async () => {
  let item = { ...source, sync_enabled: false };
  // The real setSync lands its receipt in the shared source cache.
  const setSync = vi.fn(async (_current: SourceResponse, enabled: boolean) => {
    item = { ...item, sync_enabled: enabled };
    commitSources([item]);
  });
  const refresh = vi.fn(async () => ({ sources: [item] }));
  render(
    <ApplicationDetail
      {...props}
      source={item}
      setSync={setSync}
      refresh={refresh}
    />,
  );
  const sync = await screen.findByRole("switch", { name: "同步开关" });
  await waitFor(() => expect(sync.getAttribute("aria-checked")).toBe("false"));
  fireEvent.click(sync);
  await waitFor(() =>
    expect(setSync).toHaveBeenCalledWith(
      expect.objectContaining({ source_id: "cursor" }),
      true,
    ),
  );
  await waitFor(() => expect(sync.getAttribute("aria-checked")).toBe("true"));
  fireEvent.click(sync);
  await waitFor(() =>
    expect(setSync).toHaveBeenLastCalledWith(
      expect.objectContaining({ source_id: "cursor" }),
      false,
    ),
  );
  await waitFor(() => expect(sync.getAttribute("aria-checked")).toBe("false"));
  fireEvent.click(sync);
  await waitFor(() =>
    expect(setSync).toHaveBeenLastCalledWith(
      expect.objectContaining({ source_id: "cursor" }),
      true,
    ),
  );
  await waitFor(() => expect(sync.getAttribute("aria-checked")).toBe("true"));
});

it("writes a remembered WorkBuddy sync and only then shows it as on", async () => {
  saveConnectionPreferences({
    syncSourceIds: ["workbuddy"],
    hostSourceIds: [],
  });
  let item = {
    ...source,
    source_id: "workbuddy",
    display_name: "WorkBuddy",
    authorized: true,
    sync_enabled: false,
  };
  // The real setSync lands its receipt in the shared source cache.
  const setSync = vi.fn(async (_current: SourceResponse, enabled: boolean) => {
    item = { ...item, sync_enabled: enabled };
    commitSources([item]);
  });
  const refresh = vi.fn(async () => ({ sources: [item] }));
  render(
    <ApplicationDetail
      {...props}
      source={item}
      sources={[item]}
      setSync={setSync}
      refresh={refresh}
    />,
  );
  expect(screen.getByText("已冻结")).toBeTruthy();
  await waitFor(() =>
    expect(setSync).toHaveBeenCalledWith(
      expect.objectContaining({ source_id: "workbuddy", sync_enabled: false }),
      true,
    ),
  );
  await waitFor(() => expect(screen.getByText("处理中")).toBeTruthy());
  expect(
    screen
      .getByRole("switch", { name: "同步开关" })
      .getAttribute("aria-checked"),
  ).toBe("true");
});

it("does not keep WorkBuddy looking synced when the server write fails", async () => {
  saveConnectionPreferences({
    syncSourceIds: ["workbuddy"],
    hostSourceIds: [],
  });
  const item = {
    ...source,
    source_id: "workbuddy",
    display_name: "WorkBuddy",
    authorized: true,
    sync_enabled: false,
  };
  const setSync = vi.fn(async () => {
    throw Error("sync_not_enabled");
  });
  const refresh = vi.fn(async () => ({ sources: [item] }));
  render(
    <ApplicationDetail
      {...props}
      source={item}
      sources={[item]}
      setSync={setSync}
      refresh={refresh}
    />,
  );
  await waitFor(() => expect(setSync).toHaveBeenCalledOnce());
  await waitFor(() =>
    expect(
      screen.getByText("此应用的处理开关结果未能确认，其他应用不受影响。"),
    ).toBeTruthy(),
  );
  expect(screen.getByText("已冻结")).toBeTruthy();
  expect(
    screen
      .getByRole("switch", { name: "同步开关" })
      .getAttribute("aria-checked"),
  ).toBe("false");
  // 2026-10-12: 提示语必须留在开关左侧的说明栏里；开关恒居右，不许被挤动。
  const section = screen
    .getByRole("switch", { name: "同步开关" })
    .closest("section");
  const children = Array.from(section?.children ?? []);
  expect(children[children.length - 1]).toBe(
    screen.getByRole("switch", { name: "同步开关" }),
  );
  expect(
    screen
      .getByRole("alert")
      .compareDocumentPosition(
        screen.getByRole("switch", { name: "同步开关" }),
      ) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
});

it("disables connection and explains why outside Applications, and stays enabled when in Applications", async () => {
  window.oneMemory = {
    getConnectionSetupCapability: vi.fn().mockResolvedValue({
      available: false,
      reason: "app_not_in_applications",
    }),
  } as unknown as NonNullable<typeof window.oneMemory>;
  render(<ApplicationDetail {...props} />);
  await waitFor(() =>
    expect(screen.getByRole("switch", { name: "一键插入应用" })).toHaveProperty(
      "disabled",
      true,
    ),
  );
  expect(
    screen.getByText(
      "请先把 MemHub 拖到“应用程序”并从那里重新打开，才能接入应用。",
    ),
  ).toBeTruthy();

  cleanup();
  window.oneMemory = {
    getConnectionSetupCapability: vi
      .fn()
      .mockResolvedValue({ available: true, reason: null }),
    previewRecallInstall: vi.fn(),
  } as unknown as NonNullable<typeof window.oneMemory>;
  render(<ApplicationDetail {...props} />);
  await waitFor(() =>
    expect(screen.getByRole("switch", { name: "一键插入应用" })).toHaveProperty(
      "disabled",
      false,
    ),
  );
  expect(
    screen.queryByText(
      "请先把 MemHub 拖到“应用程序”并从那里重新打开，才能接入应用。",
    ),
  ).toBeNull();
});

it("hides the read-permission switch and states the frozen truth (D97)", () => {
  const off = { ...source, sync_enabled: false };
  render(
    <ApplicationDetail
      {...props}
      source={off}
      sources={[off]}
      setSync={vi.fn().mockResolvedValue(undefined)}
    />,
  );
  expect(screen.getByRole("switch", { name: "同步开关" })).toBeTruthy();
  // Kristin 14:14: nothing about recall stays printed under the switch.
  expect(
    screen.queryByText("关闭后已生成的记忆仍会参与召回，如需清除请删除。"),
  ).toBeNull();
  expect(screen.getByText("已冻结")).toBeTruthy();
});

it("promises a backfill when the switch is turned back on (D97)", async () => {
  const off = { ...source, sync_enabled: false };
  render(
    <ApplicationDetail
      {...props}
      source={off}
      sources={[off]}
      setSync={vi.fn().mockResolvedValue(undefined)}
    />,
  );
  expect(screen.queryByText(/将补充冻结期间的新对话/)).toBeNull();
  fireEvent.click(await screen.findByRole("switch", { name: "同步开关" }));
  await waitFor(() =>
    expect(screen.getByText(/将补充冻结期间的新对话/)).toBeTruthy(),
  );
});

it("says sync is being turned on while the write is in flight (D77)", async () => {
  let finish: () => void = () => {};
  const setSync = vi.fn(
    () =>
      new Promise<void>((resolve) => {
        finish = resolve;
      }),
  );
  const off = { ...source, sync_enabled: false };
  render(
    <ApplicationDetail
      {...props}
      source={off}
      sources={[off]}
      setSync={setSync}
    />,
  );
  const sync = await screen.findByRole("switch", { name: "同步开关" });
  fireEvent.click(sync);
  await waitFor(() => expect(screen.getByText("正在开启处理…")).toBeTruthy());
  expect(screen.getByRole("status").textContent).toContain("一两分钟");
  expect(screen.queryByText("处理中")).toBeNull();
  finish();
  await waitFor(() => expect(screen.queryByText("正在开启处理…")).toBeNull());
});

it("sends a click made while the remembered sync is still writing (D77)", async () => {
  saveConnectionPreferences({
    syncSourceIds: ["workbuddy"],
    hostSourceIds: [],
  });
  let item = {
    ...source,
    source_id: "workbuddy",
    display_name: "WorkBuddy",
    authorized: true,
    sync_enabled: false,
  };
  let finish: () => void = () => {};
  const setSync = vi.fn(async (_current: SourceResponse, enabled: boolean) => {
    if (setSync.mock.calls.length === 1)
      await new Promise<void>((resolve) => {
        finish = resolve;
      });
    item = { ...item, sync_enabled: enabled };
    commitSources([item]);
  });
  const refresh = vi.fn(async () => ({ sources: [item] }));
  render(
    <ApplicationDetail
      {...props}
      source={item}
      sources={[item]}
      setSync={setSync}
      refresh={refresh}
    />,
  );
  await waitFor(() => expect(setSync).toHaveBeenCalledTimes(1));
  // The person turns it off again while the remembered "on" is in flight.
  fireEvent.click(screen.getByRole("switch", { name: "同步开关" }));
  finish();
  await waitFor(() =>
    expect(setSync).toHaveBeenLastCalledWith(
      expect.objectContaining({ source_id: "workbuddy" }),
      false,
    ),
  );
  await waitFor(() => expect(screen.getByText("已冻结")).toBeTruthy());
});

it("asks for the source fix beside the sync switch, and never for a reinstall", () => {
  // `authorized=false` used to be all the UI knew, so the same words covered a
  // revoked grant and a broken plugin, and people reinstalled for a source
  // problem that reinstalling cannot touch (issue #29).
  const unauthorized = {
    ...source,
    authorized: false,
    sync_enabled: false,
    authorization_state: "revoked_grant",
  } as SourceResponse;
  render(
    <ApplicationDetail
      {...props}
      source={unauthorized}
      sources={[unauthorized]}
    />,
  );

  expect(screen.getByText("此应用的读取授权已撤销，请重新授权。")).toBeTruthy();
  expect(screen.queryByText(/重新插入|重装|卸载/)).toBeNull();
});

it("says nothing about the source beside the sync switch when one is not needed", () => {
  const healthy = { ...source, authorization_state: "ok" } as SourceResponse;
  render(<ApplicationDetail {...props} source={healthy} sources={[healthy]} />);

  expect(screen.queryByText(/读取授权/)).toBeNull();
});
