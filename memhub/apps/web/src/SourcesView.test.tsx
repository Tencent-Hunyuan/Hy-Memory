// @vitest-environment jsdom

import type { ConnectSourcesResponse, ImportJob } from "@one-memory/contracts";
import {
  act,
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

afterEach(cleanup);

describe("SourcesView", () => {
  afterEach(() => {
    cleanup();
    delete window.oneMemory;
  });

  it("does not mark a completed scan with parsing errors as a healthy connection", () => {
    const source = {
      source_id: "openclaw",
      display_name: "OpenClaw",
      available: true,
      authorized: true,
      candidate_kind: "default",
      candidate_path: "~/.openclaw",
      imported_messages: 0,
      imported_conversations: 0,
      capabilities: {
        verification: "fixture_only",
        live_sync: "not_implemented",
      },
    };
    const job = {
      source_id: "openclaw",
      job_id: "partial",
      status: "completed",
      stored: 0,
      merged: 0,
      skipped: 0,
      errors: 2,
    };
    const imports = {
      sources: [source],
      jobs: [job],
      latestJobBySource: new Map([["openclaw", job]]),
      loading: false,
      busyKey: null,
      error: null,
      lastConnectResult: null,
      connectSelected: vi.fn(),
      syncAll: vi.fn(),
      startImport: vi.fn(),
      changeJob: vi.fn(),
    } as unknown as ReturnType<typeof useImportSources>;
    render(<SourcesView imports={imports} />);
    expect(screen.getByText("导入有异常")).toBeTruthy();
    expect(screen.getByText(/2 条异常/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "重新扫描" })).toBeTruthy();
  });

  it("turns one source switch into one explicit authorization and import", async () => {
    const user = userEvent.setup();
    const connectSelected = vi.fn().mockResolvedValue({
      accepted_source_ids: ["workbuddy"],
      failed_sources: {},
      jobs: [],
    });
    const imports = {
      health: null,
      healthStale: false,
      sources: [
        {
          source_id: "workbuddy",
          display_name: "WorkBuddy",
          storage_kinds: ["jsonl"],
          available: true,
          authorized: false,
          detected: null,
          root_label: null,
          candidate_path: "~/.workbuddy/projects",
          candidate_kind: "default",
          imported_messages: 0,
          imported_conversations: 0,
          capabilities: {
            historical_import: "implemented",
            incremental_import: "implemented",
            live_sync: "not_implemented",
            recall: "not_implemented",
            installer: "not_implemented",
            verification: "real_replayed",
          },
          sync_completeness: "additions_only",
        },
      ],
      jobs: [],
      loading: false,
      busyKey: null,
      error: null,
      lastConnectResult: null,
      latestJobBySource: new Map(),
      refresh: vi.fn(),
      connectSelected,
      syncAll: vi.fn(),
      detect: vi.fn(),
      startImport: vi.fn(),
      changeJob: vi.fn(),
    } as unknown as ReturnType<typeof useImportSources>;

    render(<SourcesView imports={imports} />);
    await user.click(screen.getByRole("checkbox", { name: /WorkBuddy/ }));
    await waitFor(() =>
      expect(connectSelected).toHaveBeenCalledWith(
        ["workbuddy"],
        [
          expect.objectContaining({
            source_id: "workbuddy",
            candidate_path: "~/.workbuddy/projects",
          }),
        ],
        // The interaction's trace id, so the connect request carries the kernel
        // legs that join the renderer's ui.action / ui.result pair.
        { traceId: expect.any(String) },
      ),
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("一次超时不写成「失败」：结果未确认，且不提示重试", async () => {
    // A timeout means Core may or may not have applied the switch. Reporting it
    // as 未能开启读取 would assert something the client cannot know, and
    // "请核对后重试" invites a retry of a write that may already have landed.
    // The trace must say `unknown` for the same reason.
    const emitted: Array<Record<string, unknown>> = [];
    (
      window as unknown as {
        oneMemory: { logUiTrace: (input: Record<string, unknown>) => void };
      }
    ).oneMemory = { logUiTrace: (input) => emitted.push(input) };
    const user = userEvent.setup();
    const timeout = Object.assign(new Error("timeout of 30000ms exceeded"), {
      code: "ECONNABORTED",
    });
    const imports = {
      health: null,
      healthStale: false,
      sources: [
        {
          source_id: "workbuddy",
          display_name: "WorkBuddy",
          storage_kinds: ["jsonl"],
          available: true,
          authorized: true,
          detected: true,
          root_label: null,
          candidate_path: "~/.workbuddy/projects",
          candidate_kind: "default",
          imported_messages: 0,
          imported_conversations: 0,
          grant_revision: 4,
          sync_enabled: true,
          capabilities: {
            historical_import: "implemented",
            incremental_import: "implemented",
            live_sync: "not_implemented",
            recall: "not_implemented",
            installer: "not_implemented",
            verification: "replayed",
          },
          sync_completeness: "additions_only",
        },
      ],
      jobs: [],
      loading: false,
      busyKey: null,
      error: null,
      lastConnectResult: null,
      latestJobBySource: new Map(),
      refresh: vi.fn(),
      connectSelected: vi.fn(),
      syncAll: vi.fn(),
      setSync: vi.fn().mockRejectedValue(timeout),
      detect: vi.fn(),
      startImport: vi.fn(),
      changeJob: vi.fn(),
    } as unknown as ReturnType<typeof useImportSources>;
    try {
      render(<SourcesView imports={imports} />);
      await user.click(screen.getByRole("checkbox", { name: /WorkBuddy/ }));
      const result = await waitFor(() => {
        const found = emitted.find((item) => item.phase === "result");
        expect(found).toBeTruthy();
        return found;
      });
      expect(result?.status).toBe("unknown");
      // The copy must not claim failure, and must not invite a retry.
      expect(screen.queryByText("未能关闭读取，请核对后重试。")).toBeNull();
      expect(
        screen.getByText(/结果尚未确认，请刷新核对；不会自动重试/),
      ).toBeTruthy();
    } finally {
      delete (window as unknown as { oneMemory?: unknown }).oneMemory;
    }
  });

  it("keeps the environment override confirmation after the source switch", async () => {
    const user = userEvent.setup();
    const connectSelected = vi.fn().mockResolvedValue({
      accepted_source_ids: ["cursor"],
      failed_sources: {},
      jobs: [],
    });
    const imports = {
      health: null,
      healthStale: false,
      sources: [
        {
          source_id: "cursor",
          display_name: "Cursor",
          storage_kinds: ["sqlite"],
          available: true,
          authorized: false,
          detected: true,
          root_label: null,
          candidate_path: "/Volumes/custom/cursor",
          candidate_kind: "environment_override",
          imported_messages: 0,
          imported_conversations: 0,
          capabilities: {
            historical_import: "implemented",
            incremental_import: "implemented",
            live_sync: "not_implemented",
            recall: "not_implemented",
            installer: "not_implemented",
            verification: "real_replayed",
          },
          sync_completeness: "additions_only",
        },
      ],
      jobs: [],
      loading: false,
      busyKey: null,
      error: null,
      lastConnectResult: null,
      latestJobBySource: new Map(),
      connectSelected,
      syncAll: vi.fn(),
      changeJob: vi.fn(),
    } as unknown as ReturnType<typeof useImportSources>;

    render(<SourcesView imports={imports} />);
    expect(screen.getByText(/环境变量覆盖路径/)).toBeTruthy();
    await user.click(screen.getByRole("checkbox", { name: /Cursor/ }));

    expect(connectSelected).not.toHaveBeenCalled();
    const dialog = screen.getByRole("dialog", {
      name: "确认读取这些自定义路径？",
    });
    expect(within(dialog).getByText("/Volumes/custom/cursor")).toBeTruthy();

    await user.click(
      screen.getByRole("button", { name: "确认路径并授权导入" }),
    );
    expect(connectSelected).toHaveBeenCalledWith(
      ["cursor"],
      [expect.objectContaining({ source_id: "cursor" })],
      { traceId: expect.any(String) },
    );
  });

  it("hides unavailable ungranted sources and shows per-source batch results", () => {
    const imports = {
      health: null,
      healthStale: false,
      sources: [
        {
          source_id: "ok",
          display_name: "Connected AI",
          storage_kinds: ["jsonl"],
          available: true,
          authorized: true,
          detected: true,
          root_label: "default",
          candidate_path: "~/.connected",
          candidate_kind: "default",
          imported_messages: 8,
          imported_conversations: 2,
          capabilities: {
            historical_import: "implemented",
            incremental_import: "implemented",
            live_sync: "not_implemented",
            recall: "not_implemented",
            installer: "not_implemented",
            verification: "real_replayed",
          },
          sync_completeness: "additions_only",
        },
        {
          source_id: "missing",
          display_name: "Missing AI",
          storage_kinds: ["jsonl"],
          available: false,
          authorized: false,
          detected: false,
          root_label: null,
          candidate_path: "~/.missing",
          candidate_kind: "default",
          imported_messages: 0,
          imported_conversations: 0,
          capabilities: {
            historical_import: "implemented",
            incremental_import: "implemented",
            live_sync: "not_implemented",
            recall: "not_implemented",
            installer: "not_implemented",
            verification: "fixture_only",
          },
          sync_completeness: "additions_only",
        },
      ],
      jobs: [],
      loading: false,
      busyKey: null,
      error: null,
      lastConnectResult: {
        accepted_source_ids: ["ok"],
        failed_sources: { cursor: "grant_not_available" },
        jobs: [],
      },
      latestJobBySource: new Map(),
      syncAll: vi.fn(),
      changeJob: vi.fn(),
    } as unknown as ReturnType<typeof useImportSources>;

    render(<SourcesView imports={imports} />);

    expect(document.querySelector(".source-table")?.textContent).not.toContain(
      "Missing AI",
    );
    expect(
      document.querySelector(".application-coverage")?.textContent,
    ).toContain("Missing AI");
    expect(screen.getAllByText("Connected AI")).toHaveLength(3);
    expect(screen.getByText("已授权，导入任务已开始")).toBeTruthy();
    expect(screen.getByText("未能授权：授权记录未建立")).toBeTruthy();
  });

  it("shows the edit and installs only once it is approved", async () => {
    const user = userEvent.setup();
    const installedPermission = {
      hostId: "claude-code",
      state: "install-only" as const,
      reason: null,
      receipt: {
        installationId: "24361b44-5a15-4f87-ad09-05571dc281c3",
        hostId: "claude-code",
        revision: 1,
        status: "install-only" as const,
        allowedTools: [],
        allowedScopes: [],
      },
    };
    const approvedPermission = {
      ...installedPermission,
      state: "approved" as const,
      receipt: {
        ...installedPermission.receipt,
        revision: 2,
        status: "approved" as const,
        allowedTools: ["om_recall", "om_ask"] as const,
        allowedScopes: ["all-apps"],
      },
    };
    const installRecall = vi.fn().mockResolvedValue({
      hostId: "claude-code",
      installed: true,
      permission: {
        hostId: "claude-code",
        state: "install-only",
        reason: null,
        receipt: {
          installationId: "24361b44-5a15-4f87-ad09-05571dc281c3",
          hostId: "claude-code",
          revision: 1,
          status: "install-only",
          allowedTools: [],
          allowedScopes: [],
        },
      },
    });
    const previewRecallInstall = vi.fn().mockResolvedValue({
      hostId: "claude-code",
      hostLabel: "Claude Code",
      fingerprint: "fp-1",
      files: [
        {
          path: "/Users/example/.claude.json",
          owner: "host",
          existed: true,
          hunk: {
            startLine: 4,
            removed: ['  "mcpServers": {}'],
            added: ['  "mcpServers": {', '    "one-memory": {}', "  }"],
            contextBefore: ["{"],
            contextAfter: ["}"],
          },
        },
      ],
    });
    const getRecallHosts = vi
      .fn()
      .mockResolvedValueOnce([
        { hostId: "cursor", label: "Cursor", present: true, installed: true },
        {
          hostId: "claude-code",
          label: "Claude Code",
          present: true,
          installed: false,
        },
      ])
      .mockResolvedValue([
        { hostId: "cursor", label: "Cursor", present: true, installed: true },
        {
          hostId: "claude-code",
          label: "Claude Code",
          present: true,
          installed: true,
          permission: approvedPermission,
        },
      ]);
    const getRecallPermission = vi.fn().mockResolvedValue(installedPermission);
    const setRecallPermission = vi.fn().mockResolvedValue(approvedPermission);
    window.oneMemory = {
      getApiToken: vi.fn(),
      getApiBaseUrl: vi.fn(),
      openHandoff: vi.fn(),
      probeRecallHost: vi.fn(),
      previewSkillSync: vi.fn(),
      applySkillSync: vi.fn(),
      getRecallHosts,
      previewRecallInstall,
      installRecall,
      previewRecallUninstall: vi.fn(),
      uninstallRecall: vi.fn(),
      getRecallPermission,
      setRecallPermission,
      revokeRecallPermission: vi.fn(),
    };
    const imports = emptyImports();

    render(<SourcesView imports={imports} />);

    // Cursor is already installed, so only Claude Code offers an install.
    const button = await screen.findByRole("button", {
      name: "一键安装到 Claude Code",
    });
    await user.click(button);

    // The click asks what would change; it does not change anything. This is
    // someone else's settings file, so nothing is written unseen.
    expect(previewRecallInstall).toHaveBeenCalledWith("claude-code");
    expect(installRecall).not.toHaveBeenCalled();
    const dialog = await screen.findByRole("dialog");
    expect(dialog.textContent).toContain("/Users/example/.claude.json");
    expect(dialog.textContent).toContain('+     "one-memory": {}');
    expect(dialog.textContent).toContain('-   "mcpServers": {}');

    expect(dialog.textContent).toContain(
      "确认后会一并按默认范围允许 Claude Code",
    );
    expect(setRecallPermission).not.toHaveBeenCalled();
    await user.click(
      screen.getByRole("button", { name: "连接并允许使用记忆" }),
    );

    // The approval carries the fingerprint of the state that was shown, so it
    // cannot be spent on a file that changed in between.
    expect(installRecall).toHaveBeenCalledWith("claude-code", "fp-1");
    expect(installRecall).toHaveBeenCalledTimes(1);
    await waitFor(() =>
      expect(
        within(
          screen.getByRole("region", { name: "Claude Code 调用权限" }),
        ).getByRole("button", { name: "调整调用权限" }),
      ).toHaveProperty("disabled", false),
    );
    expect(
      await screen.findAllByRole("button", { name: "已安装 · 点击断开" }),
    ).toHaveLength(2);
    expect(getRecallPermission).toHaveBeenCalledExactlyOnceWith("claude-code");
    expect(getRecallHosts).toHaveBeenCalledTimes(2);
    // No source is offered: grant governed-memory tools, never raw-conversation access.
    expect(setRecallPermission).toHaveBeenCalledExactlyOnceWith("claude-code", {
      installationId: "24361b44-5a15-4f87-ad09-05571dc281c3",
      expectedRevision: 1,
      allowedTools: ["om_recall", "om_ask", "om_diagnostic_trace"],
      allowedScopes: ["all-apps"],
      userConfirmed: true,
    });
    expect(installRecall.mock.invocationCallOrder[0]).toBeLessThan(
      getRecallPermission.mock.invocationCallOrder[0],
    );
    expect(getRecallPermission.mock.invocationCallOrder[0]).toBeLessThan(
      setRecallPermission.mock.invocationCallOrder[0],
    );
  });

  it("writes nothing when the edit is declined", async () => {
    const user = userEvent.setup();
    const installRecall = vi.fn();
    window.oneMemory = {
      getApiToken: vi.fn(),
      getApiBaseUrl: vi.fn(),
      openHandoff: vi.fn(),
      probeRecallHost: vi.fn(),
      previewSkillSync: vi.fn(),
      applySkillSync: vi.fn(),
      getRecallHosts: vi.fn().mockResolvedValue([
        {
          hostId: "claude-code",
          label: "Claude Code",
          present: true,
          installed: false,
        },
      ]),
      previewRecallInstall: vi.fn().mockResolvedValue({
        hostId: "claude-code",
        hostLabel: "Claude Code",
        fingerprint: "fp-1",
        files: [
          {
            path: "/Users/example/.claude.json",
            owner: "host",
            existed: true,
            hunk: {
              startLine: 2,
              removed: [],
              added: ['  "one-memory": {}'],
              contextBefore: ["{"],
              contextAfter: ["}"],
            },
          },
        ],
      }),
      installRecall,
      previewRecallUninstall: vi.fn(),
      uninstallRecall: vi.fn(),
    };

    render(<SourcesView imports={emptyImports()} />);

    await user.click(
      await screen.findByRole("button", { name: "一键安装到 Claude Code" }),
    );
    await user.click(screen.getByRole("button", { name: "取消" }));

    expect(installRecall).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(
      screen.getByRole("button", { name: "一键安装到 Claude Code" }),
    ).toBeTruthy();
  });

  it("shows the edit before disconnecting, too", async () => {
    const user = userEvent.setup();
    const uninstallRecall = vi
      .fn()
      .mockResolvedValue({ hostId: "cursor", installed: false });
    const previewRecallUninstall = vi.fn().mockResolvedValue({
      hostId: "cursor",
      hostLabel: "Cursor",
      fingerprint: "fp-out",
      files: [
        {
          path: "/Users/example/.cursor/mcp.json",
          owner: "host",
          deleted: false,
          hunk: {
            startLine: 2,
            removed: ['    "one-memory": {}'],
            added: [],
            contextBefore: ['  "mcpServers": {'],
            contextAfter: ["  }"],
          },
        },
        {
          path: "/Users/example/.cursor/skills/one-memory-recall/SKILL.md",
          owner: "one-memory",
          deleted: true,
          hunk: null,
        },
      ],
    });
    window.oneMemory = {
      getApiToken: vi.fn(),
      getApiBaseUrl: vi.fn(),
      openHandoff: vi.fn(),
      getRecallHosts: vi
        .fn()
        .mockResolvedValue([
          { hostId: "cursor", label: "Cursor", present: true, installed: true },
        ]),
      probeRecallHost: vi.fn(),
      previewSkillSync: vi.fn(),
      applySkillSync: vi.fn(),
      previewRecallInstall: vi.fn(),
      installRecall: vi.fn(),
      previewRecallUninstall,
      uninstallRecall,
    };

    render(<SourcesView imports={emptyImports()} />);

    await user.click(
      await screen.findByRole("button", { name: "已安装 · 点击断开" }),
    );

    // Disconnecting rewrites the host's own file just as installing did, so it
    // asks first rather than acting on the click.
    expect(previewRecallUninstall).toHaveBeenCalledWith("cursor");
    expect(uninstallRecall).not.toHaveBeenCalled();
    const dialog = await screen.findByRole("dialog");
    expect(dialog.textContent).toContain('-     "one-memory": {}');
    // A file that goes away is said to go away, not rendered as a diff.
    expect(dialog.textContent).toContain("整个文件会被移除");

    await user.click(screen.getByRole("button", { name: "确认断开" }));

    expect(uninstallRecall).toHaveBeenCalledWith("cursor", "fp-out");
    expect(
      await screen.findByRole("button", { name: "一键安装到 Cursor" }),
    ).toBeTruthy();
  });

  it("restores nothing when disconnecting is declined", async () => {
    const user = userEvent.setup();
    const uninstallRecall = vi.fn();
    window.oneMemory = {
      getApiToken: vi.fn(),
      getApiBaseUrl: vi.fn(),
      openHandoff: vi.fn(),
      getRecallHosts: vi
        .fn()
        .mockResolvedValue([
          { hostId: "cursor", label: "Cursor", present: true, installed: true },
        ]),
      probeRecallHost: vi.fn(),
      previewSkillSync: vi.fn(),
      applySkillSync: vi.fn(),
      previewRecallInstall: vi.fn(),
      installRecall: vi.fn(),
      previewRecallUninstall: vi.fn().mockResolvedValue({
        hostId: "cursor",
        hostLabel: "Cursor",
        fingerprint: "fp-out",
        files: [
          {
            path: "/Users/example/.cursor/mcp.json",
            owner: "host",
            deleted: true,
            hunk: null,
          },
        ],
      }),
      uninstallRecall,
    };

    render(<SourcesView imports={emptyImports()} />);

    await user.click(
      await screen.findByRole("button", { name: "已安装 · 点击断开" }),
    );
    await user.click(screen.getByRole("button", { name: "取消" }));

    expect(uninstallRecall).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeNull();
    // Still installed, because declining changed nothing.
    expect(
      screen.getByRole("button", { name: "已安装 · 点击断开" }),
    ).toBeTruthy();
  });

  it("does not offer hosts that are not installed on this machine", async () => {
    window.oneMemory = {
      getApiToken: vi.fn(),
      getApiBaseUrl: vi.fn(),
      openHandoff: vi.fn(),
      probeRecallHost: vi.fn(),
      previewSkillSync: vi.fn(),
      applySkillSync: vi.fn(),
      getRecallHosts: vi.fn().mockResolvedValue([
        { hostId: "cursor", label: "Cursor", present: true, installed: false },
        {
          hostId: "claude-code",
          label: "Claude Code",
          present: false,
          installed: false,
        },
      ]),
      previewRecallInstall: vi.fn(),
      installRecall: vi.fn(),
      previewRecallUninstall: vi.fn(),
      uninstallRecall: vi.fn(),
    };

    render(<SourcesView imports={emptyImports()} />);

    expect(
      await screen.findByRole("button", { name: "一键安装到 Cursor" }),
    ).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: "一键安装到 Claude Code" }),
    ).toBeNull();
  });

  it("says the link is not through, not merely that it is installed", async () => {
    // "已安装" is the word that hid the recorded failure: three hosts were
    // installed, and installed was all the list said, while recall returned
    // nothing. So a host whose last self-check did not go through must not
    // read as working.
    window.oneMemory = {
      getApiToken: vi.fn(),
      getApiBaseUrl: vi.fn(),
      openHandoff: vi.fn(),
      probeRecallHost: vi.fn().mockResolvedValue({
        hostId: "cursor",
        probe: {
          state: "unreachable",
          detail: "MemHub is not running (broker discovery is unavailable)",
          discoveryPath: "/tmp/wrong.json",
          discoveryPresent: false,
          probedAt: new Date().toISOString(),
        },
        skill: null,
        repoint: { from: "/tmp/wrong.json", to: "/right/broker.json" },
      }),
      previewSkillSync: vi.fn(),
      applySkillSync: vi.fn(),
      getRecallHosts: vi.fn().mockResolvedValue([
        {
          hostId: "cursor",
          label: "Cursor",
          present: true,
          installed: true,
          selfCheck: {
            state: "unreachable",
            detail: "broker discovery is unavailable",
            probed_at: new Date().toISOString(),
          },
        },
      ]),
      previewRecallInstall: vi.fn().mockResolvedValue({
        hostId: "cursor",
        hostLabel: "Cursor",
        files: [],
        supersedes: null,
        fingerprint: "f",
      }),
      installRecall: vi.fn(),
      previewRecallUninstall: vi.fn(),
      uninstallRecall: vi.fn(),
    };

    render(<SourcesView imports={emptyImports()} />);

    expect(await screen.findByText(/未接通/)).toBeTruthy();

    // Diagnosing offers the repair, and the repair goes through the ordinary
    // preview rather than writing to their file unannounced.
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "诊断" }));
    await user.click(await screen.findByRole("button", { name: "重新指向" }));

    expect(window.oneMemory?.previewRecallInstall).toHaveBeenCalledWith(
      "cursor",
    );
  });

  it("offers no repair when the app is merely not running", async () => {
    // The entry is already right, so repointing would change nothing and
    // saying "repoint" would send the user to fix a correct config while the
    // actual fix is to open the app. The probe reports this by offering no
    // repoint, and the view must respect that rather than keying off the
    // verdict.
    window.oneMemory = {
      getApiToken: vi.fn(),
      getApiBaseUrl: vi.fn(),
      openHandoff: vi.fn(),
      probeRecallHost: vi.fn().mockResolvedValue({
        hostId: "cursor",
        probe: {
          state: "reachable_but_blind",
          detail: "MemHub is not running or its recall broker is unavailable",
          discoveryPath: "/right/broker.json",
          discoveryPresent: true,
          probedAt: new Date().toISOString(),
        },
        skill: null,
        repoint: null,
      }),
      previewSkillSync: vi.fn(),
      applySkillSync: vi.fn(),
      getRecallHosts: vi.fn().mockResolvedValue([
        {
          hostId: "cursor",
          label: "Cursor",
          present: true,
          installed: true,
          selfCheck: {
            state: "reachable_but_blind",
            detail: "broker unavailable",
            probed_at: new Date().toISOString(),
          },
        },
      ]),
      previewRecallInstall: vi.fn(),
      installRecall: vi.fn(),
      previewRecallUninstall: vi.fn(),
      uninstallRecall: vi.fn(),
    };

    render(<SourcesView imports={emptyImports()} />);

    expect(await screen.findByText(/已安装，但取不到记忆/)).toBeTruthy();
    await userEvent
      .setup()
      .click(await screen.findByRole("button", { name: "诊断" }));

    expect(
      await screen.findByText(/not running or its recall broker/),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "重新指向" })).toBeNull();
  });

  it("still lists a host whose journal predates self-checks", async () => {
    // Installs made before self-checks existed have no verdict at all, and
    // reading one off a host that has none took the whole row's markup down
    // with it — the install button included. Found by the existing tests,
    // pinned here so it cannot come back.
    window.oneMemory = {
      getApiToken: vi.fn(),
      getApiBaseUrl: vi.fn(),
      openHandoff: vi.fn(),
      probeRecallHost: vi.fn(),
      previewSkillSync: vi.fn(),
      applySkillSync: vi.fn(),
      getRecallHosts: vi
        .fn()
        .mockResolvedValue([
          { hostId: "cursor", label: "Cursor", present: true, installed: true },
        ]),
      previewRecallInstall: vi.fn(),
      installRecall: vi.fn(),
      previewRecallUninstall: vi.fn(),
      uninstallRecall: vi.fn(),
    };

    render(<SourcesView imports={emptyImports()} />);

    expect(
      await screen.findByRole("button", { name: "已安装 · 点击断开" }),
    ).toBeTruthy();
  });

  it("shows call-rule state beside the link state", async () => {
    // R-33 + R-24 step 5: two different failure modes, side by side. A host
    // can be wired and still be running on yesterday's skill.
    window.oneMemory = {
      getApiToken: vi.fn(),
      getApiBaseUrl: vi.fn(),
      openHandoff: vi.fn(),
      probeRecallHost: vi.fn(),
      previewSkillSync: vi.fn(),
      applySkillSync: vi.fn(),
      getRecallHosts: vi.fn().mockResolvedValue([
        {
          hostId: "cursor",
          label: "Cursor",
          present: true,
          installed: true,
          selfCheck: {
            state: "wired",
            detail: "recalled 0 memories for the self-check",
            probed_at: new Date().toISOString(),
          },
          skillDrift: {
            state: "stale",
            installedVersion: 1,
            currentVersion: 2,
          },
        },
      ]),
      previewRecallInstall: vi.fn(),
      installRecall: vi.fn(),
      previewRecallUninstall: vi.fn(),
      uninstallRecall: vi.fn(),
    };

    render(<SourcesView imports={emptyImports()} />);

    expect(await screen.findByText(/已接通/)).toBeTruthy();
    expect(await screen.findByText(/需更新/)).toBeTruthy();
  });

  it("warns before overwriting a hand-edited call rule", async () => {
    window.oneMemory = {
      getApiToken: vi.fn(),
      getApiBaseUrl: vi.fn(),
      openHandoff: vi.fn(),
      probeRecallHost: vi.fn(),
      applySkillSync: vi.fn(),
      getRecallHosts: vi.fn().mockResolvedValue([
        {
          hostId: "cursor",
          label: "Cursor",
          present: true,
          installed: true,
          selfCheck: {
            state: "wired",
            detail: "ok",
            probed_at: new Date().toISOString(),
          },
          skillDrift: {
            state: "modified",
            installedVersion: 2,
            currentVersion: 2,
          },
        },
      ]),
      previewRecallInstall: vi.fn(),
      installRecall: vi.fn(),
      previewRecallUninstall: vi.fn(),
      uninstallRecall: vi.fn(),
      previewSkillSync: vi.fn().mockResolvedValue({
        hostId: "cursor",
        hostLabel: "Cursor",
        files: [],
        fingerprint: "f",
        upToDate: false,
        conflict: true,
      }),
    };

    render(<SourcesView imports={emptyImports()} />);

    expect(await screen.findByText(/你手动改过/)).toBeTruthy();
    await userEvent
      .setup()
      .click(await screen.findByRole("button", { name: "更新调用规则" }));
    expect(await screen.findByText(/会覆盖你的修改/)).toBeTruthy();
  });
});
function emptyImports(): ReturnType<typeof useImportSources> {
  return {
    sources: [],
    jobs: [],
    loading: false,
    busyKey: null,
    error: null,
    lastConnectResult: null,
    latestJobBySource: new Map(),
  } as unknown as ReturnType<typeof useImportSources>;
}

describe("SourcesView complementary sync Hook", () => {
  afterEach(() => {
    cleanup();
    delete window.oneMemory;
  });
  function hookHostApi() {
    const host = {
      hostId: "claude-code",
      label: "Claude Code",
      present: true,
      installed: true,
      selfCheck: null,
      skillDrift: null,
    };
    const api = {
      getApiToken: vi.fn(),
      getApiBaseUrl: vi.fn(),
      openHandoff: vi.fn(),
      getRecallHosts: vi
        .fn()
        .mockResolvedValue([
          host,
          { ...host, hostId: "cursor", label: "Cursor" },
        ]),
      probeRecallHost: vi.fn(),
      previewSkillSync: vi.fn(),
      applySkillSync: vi.fn(),
      previewRecallInstall: vi.fn(),
      installRecall: vi.fn(),
      previewRecallUninstall: vi.fn().mockResolvedValue({
        hostId: "claude-code",
        hostLabel: "Claude Code",
        fingerprint: "fp",
        files: [],
      }),
      uninstallRecall: vi.fn(),
      getSyncHookStatus: vi.fn().mockResolvedValue({
        hostId: "claude-code",
        state: "installed",
        reason: null,
      }),
      previewSyncHook: vi.fn(),
      applySyncHook: vi.fn(),
    };
    window.oneMemory = api;
    return api;
  }
  it("mounts only Claude Hook control alongside existing read permissions", async () => {
    const api = hookHostApi();
    render(<SourcesView imports={emptyImports()} />);
    expect(
      await screen.findByRole("region", { name: "Claude Code 同步 Hook" }),
    ).toBeTruthy();
    expect(
      screen.getByRole("region", { name: "Claude Code 调用权限" }),
    ).toBeTruthy();
    expect(
      screen.queryByRole("region", { name: "Cursor 同步 Hook" }),
    ).toBeNull();
    await waitFor(() =>
      expect(api.getSyncHookStatus).toHaveBeenCalledExactlyOnceWith(
        "claude-code",
      ),
    );
    expect(api.applySyncHook).not.toHaveBeenCalled();
  });
  it.each(["preview", "apply"])(
    "retains the host list and actionable remove-first message on %s failure",
    async (stage) => {
      const api = hookHostApi();
      const error = new Error(
        "Error invoking remote method: sync_hook_remove_first",
      );
      if (stage === "preview")
        api.previewRecallUninstall.mockRejectedValueOnce(error);
      else api.uninstallRecall.mockRejectedValueOnce(error);
      render(<SourcesView imports={emptyImports()} />);
      await screen.findByText("同步 Hook 已安装");
      const claude = screen
        .getByRole("region", { name: "Claude Code 同步 Hook" })
        .closest("li");
      if (!claude) throw new Error("missing Claude host row");
      await userEvent.click(
        within(claude).getByRole("button", { name: "已安装 · 点击断开" }),
      );
      if (stage === "apply")
        await userEvent.click(
          await screen.findByRole("button", { name: "确认断开" }),
        );
      expect(
        await screen.findByText("请先移除同步 Hook，再断开应用连接。"),
      ).toBeTruthy();
      expect(screen.queryByText("无法读取安装状态。")).toBeNull();
      expect(
        screen.getByRole("region", { name: "Claude Code 同步 Hook" }),
      ).toBeTruthy();
      expect(
        screen.getByRole("button", { name: "卸载同步 Hook" }),
      ).toBeTruthy();
      expect(api.applySyncHook).not.toHaveBeenCalled();
    },
  );
});

function selectableImports(): ReturnType<typeof useImportSources> {
  return {
    ...emptyImports(),
    sources: [
      {
        source_id: "workbuddy",
        display_name: "WorkBuddy",
        available: true,
        authorized: false,
        candidate_path: "/isolated/workbuddy/projects",
        candidate_kind: "default",
        imported_messages: 0,
        imported_conversations: 0,
        storage_kinds: ["jsonl"],
        capabilities: {
          verification: "fixture_only",
          live_sync: "implemented",
        },
      },
    ],
    connectSelected: vi.fn().mockResolvedValue({
      accepted_source_ids: ["workbuddy"],
      failed_sources: {},
      jobs: [],
    }),
  } as unknown as ReturnType<typeof useImportSources>;
}

it("S1.3 COLD-19: application entry leaves every ungranted source unselected", () => {
  const imports = selectableImports();
  imports.sources = [
    imports.sources[0],
    {
      ...imports.sources[0],
      source_id: "cursor",
      display_name: "Cursor",
      candidate_path: "/isolated/cursor",
    },
  ];
  render(<SourcesView imports={imports} />);
  for (const name of [/WorkBuddy/, /Cursor/]) {
    expect(
      screen.getByRole<HTMLInputElement>("checkbox", { name }).checked,
    ).toBe(false);
  }
  expect(
    screen.getByRole<HTMLButtonElement>("button", {
      name: "逐个开启来源授权",
    }).disabled,
  ).toBe(true);
  expect(imports.connectSelected).not.toHaveBeenCalled();
});

it("S1.3 COLD-10: stale source state is qualified and refresh cannot start a connection", async () => {
  const imports = selectableImports();
  imports.sourceSnapshot = {
    confirmedAt: "2026-09-14T04:00:00.000Z",
    stale: true,
  };
  imports.refresh = vi.fn().mockRejectedValue(new Error("offline"));
  render(<SourcesView imports={imports} />);
  expect(screen.getByText("状态待核对")).toBeTruthy();
  expect(screen.getByText(/上次确认/).querySelector("time")?.dateTime).toBe(
    imports.sourceSnapshot.confirmedAt,
  );
  expect(document.querySelector(".coverage-line")?.textContent).toContain(
    "个待核对的活动任务",
  );
  await userEvent.click(screen.getByRole("button", { name: "刷新来源状态" }));
  expect(imports.refresh).toHaveBeenCalledOnce();
  expect(imports.connectSelected).not.toHaveBeenCalled();
  expect(screen.getByRole("alert").textContent).toContain("尚未取得新状态");
});

it("does not turn an unconfirmed source read into zero coverage", () => {
  const imports = selectableImports();
  imports.sourceSnapshot = { confirmedAt: null, stale: true };
  imports.jobs = [];
  render(<SourcesView imports={imports} />);
  const coverage = document.querySelector(".coverage-line")?.textContent ?? "";
  expect(coverage).toContain("来源状态尚未核对");
  expect(coverage).not.toContain("0 个已连接");
  expect(coverage).not.toContain("0 个可连接");
  expect(coverage).not.toContain("0 个待核对");
});

it("S1.3 COLD-19: late discovery and returning to applications do not infer selection", async () => {
  const imports = selectableImports();
  const view = render(<SourcesView imports={{ ...imports, sources: [] }} />);
  view.rerender(<SourcesView imports={imports} />);
  const source = screen.getByRole<HTMLInputElement>("checkbox", {
    name: /WorkBuddy/,
  });
  expect(source.checked).toBe(false);
  expect(source.disabled).toBe(false);
  view.unmount();
  render(<SourcesView imports={imports} />);
  expect(
    screen.getByRole<HTMLInputElement>("checkbox", { name: /WorkBuddy/ })
      .checked,
  ).toBe(false);
  expect(imports.connectSelected).not.toHaveBeenCalled();
});

it("IMP-013: a failed switch keeps the same source available for retry", async () => {
  const imports = selectableImports();
  vi.mocked(imports.connectSelected).mockRejectedValueOnce(
    new Error("connection failed"),
  );
  render(<SourcesView imports={imports} />);
  await userEvent.click(screen.getByRole("checkbox", { name: /WorkBuddy/ }));
  expect(await screen.findByRole("alert")).toBeTruthy();
  expect(screen.getByText("/isolated/workbuddy/projects")).toBeTruthy();
  expect(
    screen.getByRole<HTMLInputElement>("checkbox", { name: /WorkBuddy/ })
      .checked,
  ).toBe(false);
  await userEvent.click(screen.getByRole("checkbox", { name: /WorkBuddy/ }));
  expect(imports.connectSelected).toHaveBeenCalledTimes(2);
  cleanup();
});

it("IMP-009: polling cannot turn a pending switch into an authorized source", async () => {
  const imports = selectableImports();
  const view = render(<SourcesView imports={imports} />);
  view.rerender(
    <SourcesView
      imports={{
        ...imports,
        sources: imports.sources.map((item) => ({ ...item })),
      }}
    />,
  );
  expect(
    (screen.getByRole("checkbox", { name: /WorkBuddy/ }) as HTMLInputElement)
      .checked,
  ).toBe(false);
  expect(imports.connectSelected).not.toHaveBeenCalled();
  cleanup();
});

it("IMP-015: a source path change after starting is surfaced before retry", async () => {
  const imports = selectableImports();
  let first = true;
  vi.mocked(imports.connectSelected).mockImplementation(
    async (): Promise<ConnectSourcesResponse> => {
      if (!first)
        return {
          accepted_source_ids: ["workbuddy"],
          failed_sources: {},
          jobs: [],
        };
      first = false;
      return {
        accepted_source_ids: [],
        failed_sources: { workbuddy: "source_identity_changed" },
        jobs: [],
      };
    },
  );
  const view = render(<SourcesView imports={imports} />);
  await userEvent.click(screen.getByRole("checkbox", { name: /WorkBuddy/ }));
  expect(imports.connectSelected).toHaveBeenCalledTimes(1);
  expect(await screen.findByRole("alert")).toBeTruthy();
  view.rerender(
    <SourcesView
      imports={{
        ...imports,
        sources: imports.sources.map((item) => ({
          ...item,
          candidate_path: "/changed/private",
        })),
      }}
    />,
  );
  expect(screen.getByText("/changed/private")).toBeTruthy();
  await userEvent.click(screen.getByRole("checkbox", { name: /WorkBuddy/ }));
  expect(imports.connectSelected).toHaveBeenCalledTimes(2);
  expect(vi.mocked(imports.connectSelected).mock.calls[1][1]).toEqual([
    expect.objectContaining({ candidate_path: "/changed/private" }),
  ]);
  cleanup();
});

it("IMP-016: the source switch stays single-flight while authorization lands", async () => {
  const imports = selectableImports();
  let finish = () => {};
  vi.mocked(imports.connectSelected).mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = () =>
          resolve({
            accepted_source_ids: ["workbuddy"],
            failed_sources: {},
            jobs: [],
          });
      }),
  );
  render(<SourcesView imports={imports} />);
  await userEvent.click(screen.getByRole("checkbox", { name: /WorkBuddy/ }));
  expect(
    screen.getByRole<HTMLInputElement>("checkbox", { name: /WorkBuddy/ })
      .disabled,
  ).toBe(true);
  await userEvent.dblClick(screen.getByRole("checkbox", { name: /WorkBuddy/ }));
  expect(imports.connectSelected).toHaveBeenCalledTimes(1);
  await act(async () => finish());
  cleanup();
});

function syncImports(): ReturnType<typeof useImportSources> {
  const imports = selectableImports();
  return {
    ...imports,
    sources: imports.sources.map((source) => ({
      ...source,
      authorized: true,
      grant_revision: 1,
      root_id: "root-workbuddy",
      sync_enabled: false,
      sync_completeness: "additions_only",
    })),
    setSync: vi.fn().mockResolvedValue(undefined),
    syncAll: vi.fn().mockResolvedValue(undefined),
  };
}

it.each([
  ["pending", "等待检查新增"],
  ["running", "正在检查新增"],
  ["paused", "新增同步已暂停"],
  ["interrupted", "新增同步已中断"],
  ["failed", "新增同步失败"],
  ["completed", "新增检查已完成"],
  ["cancelled", "新增同步已取消"],
] as const)(
  "sync %s preserves cumulative history and removes engineering verification wording",
  (status, label) => {
    const imports = syncImports();
    imports.sources = imports.sources.map((source) => ({
      ...source,
      imported_messages: 81614,
      imported_conversations: 1761,
    }));
    const current: ImportJob = {
      job_id: "sync-zero",
      source_id: "workbuddy",
      user_id: "local",
      operation: "sync",
      status,
      phase: "read",
      checkpoint: null,
      discovered: 0,
      discovered_targets: 0,
      emitted: 0,
      stored: 0,
      merged: 81614,
      revised: 0,
      skipped: 0,
      errors: 0,
      created_at: "2026-09-14T00:00:00Z",
      updated_at: "2026-09-14T00:00:01Z",
    };
    imports.jobs = [current];
    imports.latestJobBySource = new Map([["workbuddy", current]]);
    const { container } = render(<SourcesView imports={imports} />);
    expect(container.querySelector(".source-state")?.textContent).toBe(label);
    expect(container.querySelector(".source-counts")?.textContent).toContain(
      "81614 条消息",
    );
    expect(container.querySelector(".source-counts")?.textContent).toContain(
      "本次同步新增 0 条",
    );
    expect(container.textContent).not.toContain("已通过真实样本回放");
    expect(
      container.querySelector(".coverage-line")?.textContent,
    ).not.toContain("个正在导入");
    if (status === "running" || status === "pending")
      expect(container.querySelector(".coverage-line")?.textContent).toContain(
        "1 个正在检查新增",
      );
  },
);

it("S1.3 COLD-12: source rows expose real job controls without coupling cancellation to revocation", async () => {
  const imports = syncImports();
  const job = {
    job_id: "ui-import-job",
    user_id: "local",
    source_id: "workbuddy",
    status: "running" as const,
    phase: "read" as const,
    checkpoint: null,
    discovered: 10,
    discovered_targets: 1,
    emitted: 8,
    stored: 8,
    revised: 0,
    merged: 0,
    skipped: 0,
    errors: 0,
    created_at: "2026-09-14T00:00:00Z",
    updated_at: "2026-09-14T00:00:01Z",
    operation: "history" as const,
  };
  imports.jobs = [job];
  imports.latestJobBySource = new Map([["workbuddy", job]]);
  imports.changeJob = vi.fn().mockResolvedValue({
    ...job,
    status: "paused",
    updated_at: "2026-09-14T00:00:02Z",
  });
  render(<SourcesView imports={imports} />);
  const actions = screen.getByRole("group", { name: "WorkBuddy 导入任务操作" });
  await userEvent.click(
    within(actions).getByRole("button", { name: "暂停导入" }),
  );
  expect(imports.changeJob).toHaveBeenCalledWith(job.job_id, "pause");
  expect(
    await within(actions).findByRole("button", { name: "继续导入" }),
  ).toBeTruthy();
  await userEvent.click(
    within(actions).getByRole("button", { name: "取消本次导入" }),
  );
  const dialog = screen.getByRole("dialog", {
    name: "取消 WorkBuddy 的本次导入？",
  });
  expect(within(dialog).getByText(/本机副本会保留/)).toBeTruthy();
  expect(imports.changeJob).toHaveBeenCalledTimes(1);
  await userEvent.keyboard("{Escape}");
  await waitFor(() =>
    expect(
      screen.queryByRole("dialog", { name: "取消 WorkBuddy 的本次导入？" }),
    ).toBeNull(),
  );
  expect(imports.changeJob).toHaveBeenCalledTimes(1);
  expect(imports.setSync).not.toHaveBeenCalled();
});

it("Thaw and Freeze use the one Harness processing switch", async () => {
  const imports = syncImports();
  const view = render(<SourcesView imports={imports} />);
  const processing = screen.getByRole<HTMLInputElement>("checkbox", {
    name: "处理 WorkBuddy",
  });
  expect(processing.checked).toBe(false);
  expect(
    screen.getByRole<HTMLButtonElement>("button", { name: "同步来源" })
      .disabled,
  ).toBe(true);
  await userEvent.click(processing);
  expect(imports.setSync).toHaveBeenCalledWith(
    imports.sources[0],
    true,
    expect.any(String),
  );
  expect(imports.connectSelected).not.toHaveBeenCalled();
  view.rerender(
    <SourcesView
      imports={{
        ...imports,
        sources: imports.sources.map((source) => ({
          ...source,
          sync_enabled: true,
        })),
      }}
    />,
  );
  const thawed = screen.getByRole<HTMLInputElement>("checkbox", {
    name: "处理 WorkBuddy",
  });
  expect(thawed.checked).toBe(true);
  await userEvent.click(screen.getByRole("button", { name: "同步来源" }));
  expect(imports.syncAll).toHaveBeenCalledTimes(1);
  await userEvent.click(thawed);
  expect(imports.setSync).toHaveBeenLastCalledWith(
    expect.objectContaining({ source_id: "workbuddy", sync_enabled: true }),
    false,
    expect.any(String),
  );
});

it("S1.3: an incomplete permission receipt cannot activate global sync", () => {
  const imports = syncImports();
  render(
    <SourcesView
      imports={{
        ...imports,
        sources: imports.sources.map((source) => ({
          ...source,
          sync_enabled: true,
          root_id: null,
        })),
      }}
    />,
  );
  expect(
    screen.getByRole<HTMLInputElement>("checkbox", { name: "处理 WorkBuddy" })
      .checked,
  ).toBe(true);
  expect(
    screen.getByRole<HTMLButtonElement>("button", { name: "同步来源" })
      .disabled,
  ).toBe(true);
});
