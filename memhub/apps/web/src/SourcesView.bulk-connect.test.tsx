// @vitest-environment jsdom
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SourcesView } from "./SourcesView";
import type { useImportSources } from "./use-import-sources";

afterEach(() => {
  cleanup();
  delete window.oneMemory;
});

function source(id = "workbuddy") {
  return {
    source_id: id,
    display_name: "WorkBuddy",
    authorized: true,
    available: true,
    detected: true,
    storage_kinds: ["jsonl"],
    root_label: `/isolated/${id}`,
    candidate_path: `/isolated/${id}`,
    candidate_kind: "default",
    root_id: `isolated-${id}`,
    grant_revision: 1,
    imported_messages: 1,
    imported_conversations: 1,
    sync_completeness: "additions_only",
    capabilities: {
      historical_import: "implemented",
      incremental_import: "implemented",
      live_sync: "implemented",
      recall: "not_implemented",
      installer: "not_implemented",
      verification: "fixture_only",
    },
  };
}

/** Two hosts: one installed but not allowed, one not installed at all. */
const defaultHosts = [
  {
    hostId: "cursor",
    label: "Cursor",
    present: true,
    installed: true,
    selfCheck: null,
    skillDrift: null,
    permission: {
      hostId: "cursor",
      state: "install-only",
      reason: null,
      receipt: {
        hostId: "cursor",
        installationId: "cursor-installation",
        revision: 1,
        status: "install-only",
        allowedTools: [],
        allowedScopes: [],
      },
    },
  },
  {
    hostId: "claude",
    label: "Claude",
    present: true,
    installed: false,
    selfCheck: null,
    skillDrift: null,
    permission: {
      hostId: "claude",
      state: "not-installed",
      reason: null,
      receipt: null,
    },
  },
];

function setup(hosts: unknown[] = defaultHosts) {
  const api = {
    getApiBaseUrl: vi.fn(),
    getApiToken: vi.fn(),
    getRecallHosts: vi.fn().mockResolvedValue(hosts),
    previewRecallInstall: vi.fn().mockResolvedValue({
      hostId: "claude",
      hostLabel: "Claude",
      fingerprint: "claude-fingerprint",
      files: [
        {
          path: "/synthetic/.claude.json",
          owner: "host",
          existed: true,
          hunk: {
            startLine: 1,
            removed: [],
            added: ["one-memory"],
            contextBefore: [],
            contextAfter: [],
          },
        },
      ],
    }),
    installRecall: vi.fn().mockImplementation(async (hostId: string) => ({
      hostId,
      installed: true,
      permission: null,
    })),
    previewRecallUninstall: vi.fn(),
    uninstallRecall: vi.fn(),
    getRecallPermission: vi.fn().mockImplementation(async (hostId: string) => ({
      hostId,
      state: "install-only",
      reason: null,
      receipt: {
        hostId,
        installationId: `${hostId}-installation`,
        revision: 1,
        status: "install-only",
        allowedTools: [],
        allowedScopes: [],
      },
    })),
    setRecallPermission: vi
      .fn()
      .mockImplementation(async (hostId: string, input) => ({
        hostId,
        state: "approved",
        reason: null,
        receipt: {
          hostId,
          installationId: input.installationId,
          revision: input.expectedRevision + 1,
          status: "approved",
          allowedTools: [...input.allowedTools],
          allowedScopes: [...input.allowedScopes],
        },
      })),
    revokeRecallPermission: vi.fn(),
    probeRecallHost: vi.fn(),
    previewSkillSync: vi.fn(),
    applySkillSync: vi.fn(),
    openHandoff: vi.fn(),
  };
  window.oneMemory = api;
  const imports = {
    sources: [source()],
    jobs: [],
    latestJobBySource: new Map(),
    loading: false,
    busyKey: null,
    error: null,
    lastConnectResult: null,
    refresh: vi.fn(),
    connectSelected: vi.fn(),
    syncAll: vi.fn(),
    startImport: vi.fn(),
    changeJob: vi.fn(),
  } as unknown as ReturnType<typeof useImportSources>;
  render(<SourcesView imports={imports} />);
  return api;
}

const fullGrant = {
  allowedTools: ["om_recall", "om_conversations", "om_diagnostic_trace"],
  allowedScopes: ["all-apps", "source:workbuddy"],
  userConfirmed: true,
};

describe("connecting every app at once", () => {
  it("reads what it would write before writing any of it", async () => {
    const api = setup();
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("button", { name: "一键接入全部 2 个应用" }),
    );
    const dialog = await screen.findByRole("dialog");
    // Only the app that is not connected yet has a file to write.
    expect(api.previewRecallInstall).toHaveBeenCalledExactlyOnceWith("claude");
    expect(api.installRecall).not.toHaveBeenCalled();
    expect(api.setRecallPermission).not.toHaveBeenCalled();
    expect(dialog.textContent).toContain("安装连接，并允许使用记忆");
    expect(dialog.textContent).toContain("已安装，允许使用记忆");
    expect(dialog.textContent).toContain("/synthetic/.claude.json");
  });

  it("installs and allows every prepared app, then re-reads the list", async () => {
    const api = setup();
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("button", { name: "一键接入全部 2 个应用" }),
    );
    await user.click(
      await screen.findByRole("button", { name: "确认接入 2 个应用" }),
    );
    await waitFor(() => expect(api.installRecall).toHaveBeenCalledTimes(1));
    expect(api.installRecall).toHaveBeenCalledWith(
      "claude",
      "claude-fingerprint",
    );
    await waitFor(() =>
      expect(api.setRecallPermission).toHaveBeenCalledTimes(2),
    );
    expect(api.setRecallPermission.mock.calls.map((call) => call[0])).toEqual([
      "cursor",
      "claude",
    ]);
    expect(api.setRecallPermission.mock.calls[0][1]).toEqual({
      installationId: "cursor-installation",
      expectedRevision: 1,
      ...fullGrant,
    });
    expect(api.setRecallPermission.mock.calls[1][1]).toEqual({
      installationId: "claude-installation",
      expectedRevision: 1,
      ...fullGrant,
    });
    // Mount plus the re-read after the run: the rows come from the desktop,
    // not from what this run thinks it did.
    expect(api.getRecallHosts).toHaveBeenCalledTimes(2);
    expect(
      (await within(screen.getByRole("dialog")).findByRole("status"))
        .textContent,
    ).toBe("2 个应用已接入。");
  });

  it("names every app that did not finish, with the reason, next to the count", async () => {
    const api = setup();
    api.installRecall.mockRejectedValueOnce(
      new Error("controlled write failure"),
    );
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("button", { name: "一键接入全部 2 个应用" }),
    );
    await user.click(
      await screen.findByRole("button", { name: "确认接入 2 个应用" }),
    );
    expect(
      (await within(screen.getByRole("dialog")).findByRole("status"))
        .textContent,
    ).toBe(
      "1 个应用已接入，1 个未完成：Claude（未能完成）。可在下方逐个重试。",
    );
  });

  it("skips an app it cannot prepare and still does the rest", async () => {
    const api = setup();
    api.previewRecallInstall.mockRejectedValueOnce(
      new Error("controlled preview failure"),
    );
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("button", { name: "一键接入全部 2 个应用" }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(dialog.textContent).toContain(
      "跳过：未能读取这个应用的连接变更预览",
    );
    expect(dialog.textContent).toContain("有 1 个应用无法准备");
    await user.click(screen.getByRole("button", { name: "确认接入 1 个应用" }));
    await waitFor(() =>
      expect(api.setRecallPermission).toHaveBeenCalledTimes(1),
    );
    expect(api.installRecall).not.toHaveBeenCalled();
    expect(api.setRecallPermission).toHaveBeenCalledWith("cursor", {
      installationId: "cursor-installation",
      expectedRevision: 1,
      ...fullGrant,
    });
    // Skipped before the run is still unfinished: the count cannot leave it out.
    expect(
      (await within(screen.getByRole("dialog")).findByRole("status"))
        .textContent,
    ).toBe(
      "1 个应用已接入，1 个未完成：Claude（未能读取这个应用的连接变更预览）。可在下方逐个重试。",
    );
  });

  it("offers nothing once every app is connected and allowed", async () => {
    setup([
      {
        hostId: "cursor",
        label: "Cursor",
        present: true,
        installed: true,
        selfCheck: null,
        skillDrift: null,
        permission: {
          hostId: "cursor",
          state: "approved",
          reason: null,
          receipt: {
            hostId: "cursor",
            installationId: "cursor-installation",
            revision: 2,
            status: "approved",
            allowedTools: ["om_recall"],
            allowedScopes: ["all-apps"],
          },
        },
      },
    ]);
    await screen.findByRole("heading", { name: "使用记忆" });
    expect(screen.queryByRole("button", { name: /一键接入全部/ })).toBeNull();
  });
});
