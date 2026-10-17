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
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApplicationDetail } from "./ApplicationDetail";
import { ApplicationsHome } from "./ApplicationsHome";
import { saveConnectionPreferences } from "./connection-preferences";
import { resetConnectionReconcile } from "./connection-reconcile";
import type { RecallHostStatus } from "./global";

const { summary, processing, history, liveSync, recent } = vi.hoisted(() => ({
  summary: vi.fn(),
  processing: vi.fn(),
  history: vi.fn(),
  liveSync: vi.fn(),
  recent: vi.fn(),
}));
vi.mock("./call-history-client", async (original) => ({
  ...(await original<typeof import("./call-history-client")>()),
  getCallHistory: history,
}));
vi.mock("./api", () => ({
  getHomeSummary: summary,
  getLiveSyncHealth: liveSync,
  getMemoryProcessing: processing,
  getRecentConversations: recent,
  getSources: vi.fn().mockResolvedValue([]),
  getModelProfiles: vi.fn().mockResolvedValue([]),
  setSourceSync: vi.fn(),
}));
vi.mock("./ModelSettings", () => ({ ModelSettings: () => null }));

const cursor = {
  source_id: "cursor",
  display_name: "Cursor",
  authorized: true,
  sync_enabled: false,
  available: true,
  detected: true,
  grant_revision: 1,
  capabilities: {
    historical_import: "implemented",
    incremental_import: "implemented",
    live_sync: "implemented",
    recall: "implemented",
    installer: "implemented",
    verification: "real_replayed",
  },
} as SourceResponse;

const receipt = (revision: number, status: string) => ({
  hostId: "cursor",
  installationId: "install-1",
  revision,
  status,
  allowedTools: status === "approved" ? ["om_recall"] : [],
  allowedScopes: status === "approved" ? ["all-apps"] : [],
});
const approvedPermission = {
  hostId: "cursor",
  state: "approved",
  reason: null,
  receipt: receipt(2, "approved"),
};
const installOnly = {
  hostId: "cursor",
  state: "install-only",
  reason: null,
  receipt: receipt(1, "install-only"),
};
function cursorHost(installed: boolean, permission = approvedPermission) {
  return {
    hostId: "cursor",
    label: "Cursor",
    present: true,
    installed,
    permission,
    selfCheck: null,
  } as unknown as RecallHostStatus;
}

function importsWith(sources: SourceResponse[]) {
  return {
    sources,
    jobs: [],
    loading: false,
    health: { status: "ok" },
    sourceSnapshot: { stale: false },
    refresh: vi.fn(),
  } as unknown as Parameters<typeof ApplicationsHome>[0]["imports"];
}

function Home({ imports }: { imports: ReturnType<typeof importsWith> }) {
  return (
    <ApplicationsHome
      imports={imports}
      onManage={() => {}}
      onMemories={() => {}}
      onSettings={() => {}}
    />
  );
}

function Detail() {
  return (
    <ApplicationDetail
      source={cursor}
      sources={[cursor]}
      stale={false}
      setSync={vi.fn()}
      refresh={vi.fn().mockResolvedValue({ sources: [cursor] })}
      onBack={() => {}}
    />
  );
}

function access() {
  return screen.getByText("应用访问").closest("div")?.textContent ?? "";
}

let visibility: DocumentVisibilityState = "visible";

beforeEach(() => {
  visibility = "visible";
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    get: () => visibility,
  });
  Object.defineProperty(document, "hidden", {
    configurable: true,
    get: () => visibility === "hidden",
  });
  history.mockResolvedValue({
    available: true,
    recording_error: false,
    items: [],
  });
  summary.mockResolvedValue({
    memory_count: 3,
    latest: null,
    observed_at: "2026-09-25T00:00:00Z",
  });
  processing.mockResolvedValue({ running: 0, pending: 0 });
  liveSync.mockResolvedValue([]);
  recent.mockResolvedValue({ items: [], total: 0, offset: 0, limit: 1 });
  window.localStorage.clear();
  resetConnectionReconcile();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  window.oneMemory = undefined;
  window.localStorage.clear();
  resetConnectionReconcile();
  vi.clearAllMocks();
});

describe("one host state shared by the home and detail pages", () => {
  it("shows a host read made on the detail page on the home page without its own read", async () => {
    let hosts = [cursorHost(true)];
    const getRecallHosts = vi.fn(async () => hosts);
    window.oneMemory = {
      getRecallHosts,
      probeRecallHost: vi.fn(() => new Promise(() => {})),
    } as unknown as NonNullable<typeof window.oneMemory>;
    render(
      <>
        <Home imports={importsWith([cursor])} />
        <Detail />
      </>,
    );
    await waitFor(() => expect(access()).toContain("1 个应用已接入"));
    const insertion = screen.getByRole("switch", { name: "一键插入应用" });
    await waitFor(() =>
      expect(insertion.getAttribute("aria-checked")).toBe("true"),
    );
    hosts = [cursorHost(false, installOnly)];
    const before = getRecallHosts.mock.calls.length;
    fireEvent.click(
      screen.getByRole("button", { name: /检测中|已接通|未接通|未开启关联/ }),
    );
    await waitFor(() =>
      expect(insertion.getAttribute("aria-checked")).toBe("false"),
    );
    // Only the detail page's re-read went out; the home page must follow it.
    expect(getRecallHosts.mock.calls.length).toBe(before + 1);
    await waitFor(() => expect(access()).toContain("0 个应用已接入"), {
      timeout: 1_000,
    });
  });

  it("shows insertion off on the home page's first frame after the detail page turned it off", async () => {
    let installed = true;
    const getRecallHosts = vi.fn(async () => [
      cursorHost(installed, installed ? approvedPermission : installOnly),
    ]);
    window.oneMemory = {
      getRecallHosts,
      probeRecallHost: vi.fn(() => new Promise(() => {})),
      previewRecallInstall: vi.fn(),
      previewRecallUninstall: vi.fn(async () => ({
        hostId: "cursor",
        fingerprint: "fp",
      })),
      uninstallRecall: vi.fn(async () => {
        installed = false;
        return { hostId: "cursor", installed: false };
      }),
    } as unknown as NonNullable<typeof window.oneMemory>;
    const imports = importsWith([cursor]);
    const home = render(<Home imports={imports} />);
    await waitFor(() => expect(access()).toContain("1 个应用已接入"));
    home.unmount();
    const detail = render(<Detail />);
    const insertion = await screen.findByRole("switch", {
      name: "一键插入应用",
    });
    await waitFor(() =>
      expect(insertion.getAttribute("aria-checked")).toBe("true"),
    );
    fireEvent.click(insertion);
    await waitFor(() =>
      expect(window.oneMemory?.uninstallRecall).toHaveBeenCalled(),
    );
    await waitFor(() =>
      expect(insertion.getAttribute("aria-checked")).toBe("false"),
    );
    detail.unmount();
    // The home page's own read never lands: only shared state can answer.
    getRecallHosts.mockImplementation(() => new Promise(() => {}));
    render(<Home imports={imports} />);
    expect(access()).toContain("0 个应用已接入");
  });
});

describe("remembered insertion is applied once, not on every source change", () => {
  function unconfirmedInstallBridge(options: { previewFails: boolean }) {
    const previewRecallInstall = vi.fn(async () => {
      if (options.previewFails)
        throw Error(
          "Error invoking remote method 'one-memory:preview-recall-install': Error: connection_client_unavailable",
        );
      return { hostId: "cursor", fingerprint: "fp", files: [] };
    });
    window.oneMemory = {
      // Reads keep saying "not connected" even after the install went through.
      getRecallHosts: vi.fn(async () => [cursorHost(false, installOnly)]),
      previewRecallInstall,
      installRecall: vi.fn(async () => ({
        hostId: "cursor",
        installed: true,
        permission: installOnly,
      })),
      getRecallPermission: vi.fn(async () => installOnly),
      setRecallPermission: vi.fn(async () => approvedPermission),
      repairRecallCommands: vi.fn(async () => []),
    } as unknown as NonNullable<typeof window.oneMemory>;
    return previewRecallInstall;
  }

  async function changeSourcesFiveTimes(view: ReturnType<typeof render>) {
    for (let revision = 2; revision <= 6; revision += 1) {
      view.rerender(
        <Home
          imports={importsWith([{ ...cursor, grant_revision: revision }])}
        />,
      );
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 20));
      });
    }
  }

  it("starts at most one insertion while the source list changes five times", async () => {
    saveConnectionPreferences({ syncSourceIds: [], hostSourceIds: ["cursor"] });
    const preview = unconfirmedInstallBridge({ previewFails: false });
    const view = render(<Home imports={importsWith([cursor])} />);
    await waitFor(() => expect(preview).toHaveBeenCalled());
    await changeSourcesFiveTimes(view);
    expect(preview.mock.calls.length).toBeLessThanOrEqual(1);
  });

  it("does not retry a failed insertion on its own and tells the user why", async () => {
    saveConnectionPreferences({ syncSourceIds: [], hostSourceIds: ["cursor"] });
    const preview = unconfirmedInstallBridge({ previewFails: true });
    const view = render(<Home imports={importsWith([cursor])} />);
    await waitFor(() => expect(preview).toHaveBeenCalledOnce());
    await changeSourcesFiveTimes(view);
    expect(preview).toHaveBeenCalledOnce();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Cursor");
    expect(alert.textContent).toContain("连接组件");
  });
});

describe("home host polling", () => {
  it("polls while visible and stops reading while the page is hidden", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const getRecallHosts = vi.fn(async () => [cursorHost(true)]);
    window.oneMemory = { getRecallHosts } as unknown as NonNullable<
      typeof window.oneMemory
    >;
    render(<Home imports={importsWith([cursor])} />);
    await waitFor(() => expect(access()).toContain("1 个应用已接入"));
    const settled = getRecallHosts.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_100);
    });
    // The measurement moves: a visible page does poll.
    expect(getRecallHosts.mock.calls.length).toBeGreaterThan(settled);
    visibility = "hidden";
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      window.dispatchEvent(new Event("visibilitychange"));
    });
    const hidden = getRecallHosts.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(getRecallHosts.mock.calls.length).toBe(hidden);
  });
});

describe("host writes land in the shared state", () => {
  function installBridge(options: { grantFails?: boolean } = {}) {
    let approved = false;
    const getRecallHosts = vi.fn(async () => [
      cursorHost(approved, approved ? approvedPermission : installOnly),
    ]);
    window.oneMemory = {
      getRecallHosts,
      probeRecallHost: vi.fn(() => new Promise(() => {})),
      previewRecallInstall: vi.fn(async () => ({
        hostId: "cursor",
        fingerprint: "fp",
        files: [],
      })),
      installRecall: vi.fn(async () => ({
        hostId: "cursor",
        installed: true,
        permission: installOnly,
      })),
      getRecallPermission: vi.fn(async () => installOnly),
      setRecallPermission: vi.fn(async () => {
        if (options.grantFails) {
          // The grant went through on the desktop side, the reply did not.
          approved = true;
          throw Error("host_control_unavailable");
        }
        return approvedPermission;
      }),
      repairRecallCommands: vi.fn(async () => []),
    } as unknown as NonNullable<typeof window.oneMemory>;
    return getRecallHosts;
  }

  it("shows a turn-on receipt from the detail page on the home page without a read", async () => {
    const getRecallHosts = installBridge();
    const detail = render(<Detail />);
    const insertion = await screen.findByRole("switch", {
      name: "一键插入应用",
    });
    await waitFor(() =>
      expect(insertion.getAttribute("aria-checked")).toBe("false"),
    );
    // Reads keep answering "not approved"; only the receipt says otherwise.
    getRecallHosts.mockImplementation(async () => [
      cursorHost(false, installOnly),
    ]);
    fireEvent.click(insertion);
    await waitFor(() =>
      expect(window.oneMemory?.setRecallPermission).toHaveBeenCalled(),
    );
    await waitFor(() => expect(insertion.getAttribute("aria-busy")).toBeNull());
    detail.unmount();
    getRecallHosts.mockImplementation(() => new Promise(() => {}));
    render(<Home imports={importsWith([cursor])} />);
    expect(access()).toContain("1 个应用已接入");
  });

  it("re-reads once after a failed turn-on and shows what is really there", async () => {
    const getRecallHosts = installBridge({ grantFails: true });
    render(
      <>
        <Home imports={importsWith([cursor])} />
        <Detail />
      </>,
    );
    const insertion = await screen.findByRole("switch", {
      name: "一键插入应用",
    });
    await waitFor(() => expect(access()).toContain("0 个应用已接入"));
    const before = getRecallHosts.mock.calls.length;
    fireEvent.click(insertion);
    expect(
      await screen.findByText(
        "此应用的一键接入未完成，其他应用不受影响。原因：应用控制通道暂不可用。",
      ),
    ).toBeTruthy();
    await waitFor(() => expect(access()).toContain("1 个应用已接入"));
    // One read before the install, one after the failure; no polling in between.
    expect(getRecallHosts.mock.calls.length).toBe(before + 2);
  });

  it("re-reads once after a failed turn-off and shows the removal that did happen", async () => {
    let installed = true;
    const getRecallHosts = vi.fn(async () => [
      cursorHost(installed, installed ? approvedPermission : installOnly),
    ]);
    window.oneMemory = {
      getRecallHosts,
      probeRecallHost: vi.fn(() => new Promise(() => {})),
      previewRecallInstall: vi.fn(),
      previewRecallUninstall: vi.fn(async () => ({
        hostId: "cursor",
        fingerprint: "fp",
      })),
      uninstallRecall: vi.fn(async () => {
        installed = false;
        throw Error("host_control_invalid_response");
      }),
      getRecallPermission: vi.fn(async () => {
        throw Error("host_control_unavailable");
      }),
      revokeRecallPermission: vi.fn(),
    } as unknown as NonNullable<typeof window.oneMemory>;
    render(<Detail />);
    const insertion = await screen.findByRole("switch", {
      name: "一键插入应用",
    });
    await waitFor(() =>
      expect(insertion.getAttribute("aria-checked")).toBe("true"),
    );
    const before = getRecallHosts.mock.calls.length;
    fireEvent.click(insertion);
    expect(
      await screen.findByText(
        "连接未能移除，记忆权限也未能撤销，Cursor 仍可使用记忆，请重试。原因：应用控制通道暂不可用。",
      ),
    ).toBeTruthy();
    await waitFor(() =>
      expect(insertion.getAttribute("aria-checked")).toBe("false"),
    );
    expect(getRecallHosts.mock.calls.length).toBe(before + 1);
  });
});

describe("the remembered apply stands aside", () => {
  it("does not start while another host write is running", async () => {
    saveConnectionPreferences({ syncSourceIds: [], hostSourceIds: ["cursor"] });
    const previewRecallInstall = vi.fn(() => new Promise(() => {}));
    window.oneMemory = {
      getRecallHosts: vi.fn(async () => [cursorHost(false, installOnly)]),
      previewRecallInstall,
      installRecall: vi.fn(),
      getRecallPermission: vi.fn(),
      setRecallPermission: vi.fn(),
    } as unknown as NonNullable<typeof window.oneMemory>;
    const { enableHostConnection } = await import("./host-state");
    // Onboarding installs through the same write without touching the gate.
    void enableHostConnection("cursor").catch(() => {});
    await waitFor(() => expect(previewRecallInstall).toHaveBeenCalledOnce());
    render(<Home imports={importsWith([cursor])} />);
    await waitFor(() => expect(access()).not.toContain("0 个应用已接入"));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(previewRecallInstall).toHaveBeenCalledOnce();
  });

  it("does not retry on its own after the user's own turn-on failed", async () => {
    const previewRecallInstall = vi.fn(async () => {
      throw Error("connection_client_unavailable");
    });
    window.oneMemory = {
      getRecallHosts: vi.fn(async () => [cursorHost(false, installOnly)]),
      probeRecallHost: vi.fn(() => new Promise(() => {})),
      previewRecallInstall,
      installRecall: vi.fn(),
      getRecallPermission: vi.fn(),
      setRecallPermission: vi.fn(),
    } as unknown as NonNullable<typeof window.oneMemory>;
    const detail = render(<Detail />);
    const insertion = await screen.findByRole("switch", {
      name: "一键插入应用",
    });
    await waitFor(() =>
      expect(insertion.getAttribute("aria-checked")).toBe("false"),
    );
    fireEvent.click(insertion);
    await screen.findByText(/此应用的一键接入未完成/);
    expect(previewRecallInstall).toHaveBeenCalledOnce();
    detail.unmount();
    render(<Home imports={importsWith([cursor])} />);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(previewRecallInstall).toHaveBeenCalledOnce();
  });

  it("drops a remembered host that cannot be inserted without asking the bridge", async () => {
    saveConnectionPreferences({ syncSourceIds: [], hostSourceIds: ["pi"] });
    const previewRecallInstall = vi.fn();
    window.oneMemory = {
      getRecallHosts: vi.fn(async () => [
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
          selfCheck: null,
        },
      ]),
      previewRecallInstall,
      installRecall: vi.fn(),
      getRecallPermission: vi.fn(),
      setRecallPermission: vi.fn(),
    } as unknown as NonNullable<typeof window.oneMemory>;
    const { readConnectionPreferences } = await import(
      "./connection-preferences"
    );
    render(<Home imports={importsWith([cursor])} />);
    await waitFor(() =>
      expect(readConnectionPreferences().hostSourceIds).toEqual([]),
    );
    await waitFor(() => expect(access()).toContain("0 个应用已接入"));
    expect(previewRecallInstall).not.toHaveBeenCalled();
  });
});

it("shows a turn-off as off while the removal is still running", async () => {
  window.oneMemory = {
    getRecallHosts: vi.fn(async () => [cursorHost(true)]),
    probeRecallHost: vi.fn(() => new Promise(() => {})),
    previewRecallInstall: vi.fn(),
    previewRecallUninstall: vi.fn(() => new Promise(() => {})),
    uninstallRecall: vi.fn(),
  } as unknown as NonNullable<typeof window.oneMemory>;
  render(<Detail />);
  const insertion = await screen.findByRole("switch", {
    name: "一键插入应用",
  });
  await waitFor(() =>
    expect(insertion.getAttribute("aria-checked")).toBe("true"),
  );
  fireEvent.click(insertion);
  await waitFor(() =>
    expect(window.oneMemory?.previewRecallUninstall).toHaveBeenCalled(),
  );
  expect(insertion.getAttribute("aria-checked")).toBe("false");
});

it("keeps the last host state on the home page and says so when a poll fails", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const getRecallHosts = vi
    .fn()
    .mockResolvedValueOnce([cursorHost(true)])
    .mockRejectedValue(Error("ipc gone"));
  window.oneMemory = { getRecallHosts } as unknown as NonNullable<
    typeof window.oneMemory
  >;
  render(<Home imports={importsWith([cursor])} />);
  await waitFor(() => expect(access()).toContain("1 个应用已接入"));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3_100);
  });
  await waitFor(() => expect(getRecallHosts.mock.calls.length).toBe(2));
  expect(access()).toContain("1 个应用已接入");
  expect(
    (await screen.findByRole("status", { name: "应用接入状态" })).textContent,
  ).toContain("当前为上次状态");
});
