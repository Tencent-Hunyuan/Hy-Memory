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
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { HealthPanel } from "./HealthPanel";
import type { ImportSourcesController } from "./use-import-sources";

const getMemoryProcessing = vi.fn();
const getDetailedLogging = vi.fn(async () => false);
const setDetailedLogging = vi.fn(async (enabled: boolean) => enabled);

vi.mock("./api", () => ({
  getMemoryProcessing: (...args: unknown[]) => getMemoryProcessing(...args),
  getDetailedLogging: () => getDetailedLogging(),
  setDetailedLogging: (enabled: boolean) => setDetailedLogging(enabled),
}));

function source(
  sourceId: string,
  overrides: Partial<SourceResponse> = {},
): SourceResponse {
  return {
    source_id: sourceId,
    display_name: sourceId === "cursor" ? "Cursor" : "Claude Code",
    storage_kinds: ["jsonl"],
    available: true,
    authorized: true,
    detected: true,
    root_label: null,
    candidate_path: `~/.${sourceId}`,
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
    sync_enabled: false,
    sync_completeness: "additions_only",
    ...overrides,
  };
}

function imports(
  overrides: Partial<ImportSourcesController> = {},
): ImportSourcesController {
  return {
    health: {
      status: "ok",
      service: "one-memory-core",
      version: "1.2.3",
      instance_id: "instance-1",
    },
    sources: [],
    jobs: [],
    loading: false,
    busyKey: null,
    error: null,
    lastConnectResult: null,
    latestJobBySource: new Map(),
    refresh: vi.fn(),
    connectForOnboarding: vi.fn(),
    connectSelected: vi.fn(),
    syncAll: vi.fn(),
    authorize: vi.fn(),
    detect: vi.fn(),
    startImport: vi.fn(),
    changeJob: vi.fn(),
    revoke: vi.fn(),
    ...overrides,
  } as ImportSourcesController;
}

function bridge(hosts: unknown[]) {
  window.oneMemory = {
    getApiToken: vi.fn(),
    getApiBaseUrl: vi.fn(),
    getRecallHosts: vi.fn().mockResolvedValue(hosts),
    previewRecallInstall: vi.fn(),
    probeRecallHost: vi.fn(),
    previewSkillSync: vi.fn(),
    applySkillSync: vi.fn(),
    installRecall: vi.fn(),
    previewRecallUninstall: vi.fn(),
    uninstallRecall: vi.fn(),
  } as unknown as NonNullable<Window["oneMemory"]>;
  return window.oneMemory;
}

describe("HealthPanel", () => {
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    delete window.oneMemory;
  });

  beforeEach(() => {
    vi.resetAllMocks();
    getDetailedLogging.mockResolvedValue(false);
    setDetailedLogging.mockImplementation(async (enabled: boolean) => enabled);
    getMemoryProcessing.mockResolvedValue({
      pending: 2,
      running: 1,
      fresh_pending: 2,
      backlog_pending: 0,
      recent_completions: [],
      waiting_for_model: 7,
      completed: 4,
      failed: 0,
      blocked_stages: [
        { kind: "extract", reason: "model_profile_not_active", count: 7 },
      ],
      outside_window_pending: 0,
      extraction_window_preset: "3m",
      manual_extract_batches_planned: 0,
      manual_extract_batches_current: 0,
      active_queue: 0,
      in_flight_count: 0,
      seconds_per_completion: 2,
      metrics: {
        raw_evidence: 100000,
        complete_turns: 50000,
        hy_calls: 120,
        completed: 100,
        unknown: 1,
        failed: 2,
        cancelled: 3,
        cancelled_reasons: { source_permission_changed: 3 },
        governed_memories: 80,
        workbuddy: { injected: 20, timed_out: 1, unavailable: 0 },
      },
    });
  });

  it("shows the complete import, HY and WorkBuddy counters", async () => {
    bridge([]);
    render(<HealthPanel imports={imports()} />);

    const details = await screen.findByLabelText("导入与整理详情");
    expect(details.textContent).toContain("100000 条原文 · 50000 个完整 turn");
    expect(details.textContent).toContain(
      "120 次调用 · 100 完成 · 1 结果不确定 · 2 失败 · 3 取消",
    );
    expect(details.textContent).toContain("80 条记忆");
    expect(details.textContent).toContain("20 次注入 · 1 次超时 · 0 次不可用");
  });

  it("keeps detailed pipeline logging off by default and changes the Core setting", async () => {
    bridge([]);
    render(<HealthPanel imports={imports()} />);
    const toggle = await screen.findByRole("switch", {
      name: "详细日志（排查用）",
    });
    expect(toggle.getAttribute("aria-checked")).toBe("false");
    fireEvent.click(toggle);
    await waitFor(() => expect(setDetailedLogging).toHaveBeenCalledWith(true));
    await waitFor(() =>
      expect(toggle.getAttribute("aria-checked")).toBe("true"),
    );
  });

  it("records a problem marker, copies its id and exports diagnostics", async () => {
    const desktop = bridge([]);
    desktop.markDiagnosticIncident = vi.fn().mockResolvedValue({
      incident_id: "11111111-1111-4111-8111-111111111111",
      session_id: "22222222-2222-4222-8222-222222222222",
      timestamp: "2026-09-24T01:00:00Z",
    });
    desktop.copyText = vi.fn().mockResolvedValue(undefined);
    desktop.exportDiagnostics = vi.fn().mockResolvedValue({
      state: "exported",
      filename: "MemHub-日志-3800-20261005-0300.zip",
      file_count: 12,
    });
    vi.spyOn(window, "prompt").mockReturnValue("蓝莓没出来");
    render(<HealthPanel imports={imports()} />);

    fireEvent.click(screen.getByRole("button", { name: "记录问题时间点" }));
    expect(await screen.findByText(/问题编号已复制/)).toBeTruthy();
    expect(desktop.markDiagnosticIncident).toHaveBeenCalledWith("蓝莓没出来");
    expect(desktop.copyText).toHaveBeenCalledWith(
      "11111111-1111-4111-8111-111111111111",
    );

    fireEvent.click(
      screen.getByRole("button", { name: "导出日志（发给开发排查）" }),
    );
    expect(await screen.findByText(/MemHub-日志-3800/)).toBeTruthy();
    expect(desktop.exportDiagnostics).toHaveBeenCalledOnce();
  });

  it("回报导出失败，而不是静默像成功", async () => {
    const desktop = bridge([]);
    desktop.exportDiagnostics = vi
      .fn()
      .mockRejectedValue(new Error("disk_full"));
    render(<HealthPanel imports={imports()} />);

    fireEvent.click(
      screen.getByRole("button", { name: "导出日志（发给开发排查）" }),
    );

    expect(await screen.findByText("诊断信息未能导出，请重试。")).toBeTruthy();
    expect(screen.queryByText(/已导出/)).toBeNull();
  });

  it("四类状态各说各的真实数字，不是四个占位", async () => {
    bridge([
      {
        hostId: "cursor",
        label: "Cursor",
        present: true,
        installed: true,
        selfCheck: { state: "wired", detail: "", probed_at: "" },
        skillDrift: null,
      },
    ]);

    render(
      <HealthPanel
        imports={imports({
          sources: [
            source("cursor", {
              imported_conversations: 6,
              imported_messages: 12,
            }),
          ],
        })}
      />,
    );

    expect(screen.getByLabelText("本地服务").textContent).toContain("1.2.3");

    const sources = screen.getByLabelText("各来源累计条数");
    expect(sources.textContent).toContain("Cursor");
    expect(sources.textContent).toContain("6");
    expect(sources.textContent).toContain("12");

    const pipeline = await screen.findByLabelText("记忆提炼");
    expect(pipeline.textContent).toContain("记忆抽取");
    expect(pipeline.textContent).toContain("还没有启用生成模型");

    expect((await screen.findByLabelText("AI 接通情况")).textContent).toContain(
      "已接通",
    );
  });

  it("三档措辞与来源页同一套，不新造词", async () => {
    bridge([
      {
        hostId: "cursor",
        label: "Cursor",
        present: true,
        installed: true,
        selfCheck: {
          state: "reachable_but_blind",
          detail: "",
          probed_at: "",
        },
        skillDrift: null,
      },
    ]);

    render(<HealthPanel imports={imports()} />);

    const hosts = await screen.findByLabelText("AI 接通情况");
    expect(hosts.textContent).toContain("已安装，但取不到记忆");
    expect(hosts.textContent).not.toContain("已接通");
  });

  it("桌面桥不在时说读不到，而不是谎报未接通", async () => {
    render(<HealthPanel imports={imports()} />);

    const hosts = await screen.findByLabelText("AI 接通情况");
    expect(hosts.textContent).toContain("在浏览器里读不到");
    expect(hosts.textContent).not.toContain("未接通");
  });

  it("诊断面板也不印流水线自己的错误码", async () => {
    getMemoryProcessing.mockResolvedValue({
      pending: 0,
      running: 0,
      fresh_pending: 0,
      backlog_pending: 0,
      recent_completions: [],
      waiting_for_model: 0,
      completed: 0,
      failed: 3,
      blocked_stages: [
        { kind: "extract", reason: "model_api_key_missing", count: 1 },
        {
          kind: "extract",
          reason: "hy_memory_write_verification_clause_incomplete",
          count: 2,
        },
      ],
      outside_window_pending: 0,
      extraction_window_preset: "3m",
      manual_extract_batches_planned: 0,
      manual_extract_batches_current: 0,
      active_queue: 0,
      in_flight_count: 0,
      seconds_per_completion: null,
    });
    bridge([]);

    const { container } = render(<HealthPanel imports={imports()} />);

    const block = await screen.findByLabelText("记忆提炼");
    expect(block.textContent).toContain(
      "记忆抽取卡在 缺少生成模型 API Key（1 条）",
    );
    expect(block.textContent).toContain("2 条这次没能整理成功，正在重试");
    expect(container.textContent).not.toContain("hy_memory_");
  });

  it("Core 没连上时不报正常", async () => {
    bridge([]);

    render(<HealthPanel imports={imports({ health: null })} />);

    const service = screen.getByLabelText("本地服务");
    expect(service.textContent).toContain("还没连上");
    expect(service.textContent).not.toContain("正常");
  });

  describe("共享后台健康", () => {
    const healthy = {
      state: "healthy",
      code: "background_verified",
      core: "running",
      broker: "running",
      instanceId: "instance-1",
    } as const;
    function background(value: unknown) {
      const desktop = bridge([]);
      desktop.getBackgroundHealth = vi.fn().mockResolvedValue(value);
      return vi.mocked(desktop.getBackgroundHealth);
    }

    it("刷新双击不重复检查，卸载后迟到结果不影响下一次打开", async () => {
      const read = background(healthy);
      const { unmount } = render(<HealthPanel imports={imports()} />);
      await waitFor(() =>
        expect(screen.getByLabelText("后台服务").textContent).toContain(
          "后台服务可用",
        ),
      );
      let resolve!: (value: typeof healthy) => void;
      read.mockReturnValueOnce(
        new Promise((done) => {
          resolve = done;
        }),
      );
      const button = screen.getByRole("button", { name: "重新读取状态" });
      fireEvent.click(button);
      fireEvent.click(button);
      expect(read).toHaveBeenCalledTimes(2);
      unmount();
      background({
        state: "stopped",
        code: "background_runtime_not_healthy",
        core: "stopped",
        broker: "not_checked",
      });
      render(<HealthPanel imports={imports()} />);
      await waitFor(() =>
        expect(screen.getByLabelText("后台服务").textContent).toContain(
          "后台服务已停止",
        ),
      );
      await act(async () => resolve(healthy));
      expect(screen.getByLabelText("后台服务").textContent).not.toContain(
        "后台服务可用",
      );
    });

    it("读取共享后台身份及桥接状态，不把状态检查当自动启动", async () => {
      const read = background(healthy);
      const state = imports();
      render(<HealthPanel imports={state} />);
      await waitFor(() =>
        expect(screen.getByLabelText("后台服务").textContent).toContain(
          "后台服务可用",
        ),
      );
      const text = screen.getByLabelText("后台服务").textContent;
      expect(text).toContain("宿主桥接：运行中");
      expect(text).toContain("Core：运行中");
      expect(text).toContain("上次成功读取");
      expect(read).toHaveBeenCalledExactlyOnceWith();
      expect(state.syncAll).not.toHaveBeenCalled();
      expect(window.oneMemory?.installRecall).not.toHaveBeenCalled();
      expect(window.oneMemory?.probeRecallHost).not.toHaveBeenCalled();
    });

    it.each([
      [
        {
          state: "degraded",
          code: "background_broker_not_healthy",
          core: "running",
          broker: "stopped",
        },
        "宿主桥接：已停止",
      ],
      [
        {
          state: "degraded",
          code: "background_broker_not_healthy",
          core: "running",
          broker: "unhealthy",
        },
        "宿主桥接：无法连接",
      ],
      [
        {
          state: "untrusted",
          code: "background_broker_identity_mismatch",
          core: "running",
          broker: "untrusted",
        },
        "宿主桥接：身份未通过核验",
      ],
      [
        {
          state: "stopped",
          code: "background_runtime_not_healthy",
          core: "stopped",
          broker: "not_checked",
        },
        "后台服务已停止",
      ],
      [
        {
          state: "unknown",
          code: "background_identity_changed",
          core: "running",
          broker: "not_checked",
        },
        "后台状态尚未确认",
      ],
    ])(
      "保留真实后台状态，不因Core正常而报全部可用 %#",
      async (value, expected) => {
        background(value);
        render(<HealthPanel imports={imports()} />);
        await waitFor(() =>
          expect(screen.getByLabelText("后台服务").textContent).toContain(
            expected,
          ),
        );
        expect(screen.getByLabelText("后台服务").textContent).not.toContain(
          "后台服务可用",
        );
      },
    );

    it.each([undefined, {}])(
      "缺桌面后台接口时明确未检查 %#",
      async (desktop) => {
        if (desktop) bridge([]);
        render(<HealthPanel imports={imports()} />);
        await waitFor(() =>
          expect(screen.getByLabelText("后台服务").textContent).toContain(
            "当前环境无法检查后台服务",
          ),
        );
        expect(screen.getByLabelText("后台服务").textContent).not.toContain(
          "后台服务可用",
        );
      },
    );

    it.each([
      null,
      { ...healthy, broker: "stopped" },
      { ...healthy, instanceId: undefined },
      { ...healthy, state: ["healthy"] },
      { ...healthy, core: "invented" },
    ])("拒绝不完整或矛盾回执 %#", async (value) => {
      background(value);
      render(<HealthPanel imports={imports()} />);
      await waitFor(() =>
        expect(screen.getByLabelText("后台服务").textContent).toContain(
          "暂时无法读取",
        ),
      );
      expect(screen.getByLabelText("后台服务").textContent).not.toContain(
        "后台服务可用",
      );
    });

    it("重新读取失败标记旧状态，恢复后显示新状态且不输出异常正文", async () => {
      const read = background(healthy);
      render(<HealthPanel imports={imports()} />);
      const block = screen.getByLabelText("后台服务");
      await waitFor(() => expect(block.textContent).toContain("后台服务可用"));
      read.mockRejectedValueOnce(Error("PRIVATE_BACKGROUND_ERROR"));
      fireEvent.click(screen.getByRole("button", { name: "重新读取状态" }));
      await waitFor(() => expect(block.textContent).toContain("数据已过期"));
      expect(block.textContent).toContain("上次读取：后台服务可用");
      expect(block.textContent).not.toContain("PRIVATE_BACKGROUND_ERROR");
      read.mockResolvedValue({
        state: "degraded",
        code: "background_broker_not_healthy",
        core: "running",
        broker: "stopped",
      });
      fireEvent.click(screen.getByRole("button", { name: "重新读取状态" }));
      await waitFor(() =>
        expect(block.textContent).toContain("宿主桥接：已停止"),
      );
      expect(block.textContent).not.toContain("数据已过期");
      expect(block.textContent).not.toContain("后台服务可用");
    });
  });

  describe("读取回执", () => {
    beforeEach(() => {
      vi.useFakeTimers();
      vi.setSystemTime(new Date("2026-09-19T02:10:00Z"));
    });

    async function settle() {
      await act(async () => {});
    }

    function deferred<T>() {
      let resolve!: (value: T) => void;
      let reject!: (reason: Error) => void;
      const promise = new Promise<T>((yes, no) => {
        resolve = yes;
        reject = no;
      });
      return { promise, resolve, reject };
    }

    it("宿主首次读取中不报浏览器不可用或空结果", async () => {
      const desktop = bridge([]);
      vi.mocked(desktop.getRecallHosts).mockReturnValue(new Promise(() => {}));
      render(<HealthPanel imports={imports()} />);
      await settle();
      const text = screen.getByLabelText("AI 接通情况").textContent;
      expect(text).toContain("正在读取");
      expect(text).not.toContain("浏览器");
      expect(text).not.toContain("没有检测到");
    });

    it("宿主读取拒绝不误报未检测到工具且不暴露异常正文", async () => {
      const desktop = bridge([]);
      vi.mocked(desktop.getRecallHosts).mockRejectedValue(
        new Error("private-transport-detail"),
      );
      render(<HealthPanel imports={imports()} />);
      await settle();
      const text = screen.getByLabelText("AI 接通情况").textContent;
      expect(text).toContain("暂时无法读取");
      expect(text).toContain("读取失败时间");
      expect(text).not.toContain("没有检测到");
      expect(text).not.toContain("private-transport-detail");
    });

    it("桌面桥同步抛错也进入可恢复读取失败", async () => {
      const desktop = bridge([]);
      vi.mocked(desktop.getRecallHosts).mockImplementation(() => {
        throw new Error("bridge failed");
      });
      render(<HealthPanel imports={imports()} />);
      await settle();
      expect(screen.getByLabelText("AI 接通情况").textContent).toContain(
        "暂时无法读取",
      );
    });

    it("成功读取空宿主列表才显示真正空态", async () => {
      bridge([{ hostId: "cursor", present: false }]);
      render(<HealthPanel imports={imports()} />);
      await settle();
      const text = screen.getByLabelText("AI 接通情况").textContent;
      expect(text).toContain("本机没有检测到可接入的 AI 工具");
      expect(text).toContain("上次成功读取");
    });

    it("提炼首次失败结束加载，不显示正常结论", async () => {
      getMemoryProcessing.mockRejectedValue(new Error("offline"));
      render(<HealthPanel imports={imports()} />);
      await settle();
      const text = screen.getByLabelText("记忆提炼").textContent;
      expect(text).toContain("暂时无法读取");
      expect(text).not.toContain("正在读取");
      expect(text).not.toContain("暂无需要处理的问题");
    });

    it("轮询失败保留数字并标旧数据及成功/失败时间，恢复后清除错误", async () => {
      const healthy = {
        pending: 2,
        running: 1,
        completed: 4,
        failed: 0,
        blocked_stages: [],
      };
      getMemoryProcessing.mockResolvedValue(healthy);
      render(<HealthPanel imports={imports()} />);
      await settle();
      const block = screen.getByLabelText("记忆提炼");
      expect(block.textContent).toContain("暂无需要处理的问题");
      getMemoryProcessing.mockRejectedValue(new Error("offline"));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5_000);
      });
      expect(block.textContent).toContain("数据已过期");
      expect(block.textContent).toContain("4 已完成");
      expect(block.textContent).not.toContain("暂无需要处理的问题");
      expect(
        block.querySelector('time[datetime="2026-09-19T02:10:00.000Z"]'),
      ).not.toBeNull();
      expect(
        block.querySelector('time[datetime="2026-09-19T02:10:05.000Z"]'),
      ).not.toBeNull();
      getMemoryProcessing.mockResolvedValue(healthy);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5_000);
      });
      expect(block.textContent).not.toContain("数据已过期");
      expect(block.textContent).not.toContain("读取失败时间");
      expect(block.textContent).toContain("暂无需要处理的问题");
    });

    it("sourceSnapshot 过期不继续报服务运行中或来源空", async () => {
      render(
        <HealthPanel
          imports={imports({
            sourceSnapshot: {
              stale: true,
              confirmedAt: "2026-09-19T02:06:00Z",
            },
            error: "offline",
          })}
        />,
      );
      await settle();
      const service = screen.getByLabelText("本地服务");
      expect(service.textContent).toContain("数据已过期");
      expect(service.textContent).toContain("1.2.3");
      expect(service.textContent).not.toContain("运行中");
      expect(service.querySelector("time")?.dateTime).toBe(
        "2026-09-19T02:06:00Z",
      );
      const sources = screen.getByLabelText("各来源累计条数");
      expect(sources.textContent).toContain("暂时无法读取");
      expect(sources.textContent).not.toContain("还没有已连接");
    });

    it("来源首次加载/失败与确认空态分开", async () => {
      const { rerender } = render(
        <HealthPanel
          imports={imports({
            health: null,
            healthStale: false,
            loading: true,
            sourceSnapshot: { stale: true, confirmedAt: null },
          })}
        />,
      );
      await settle();
      expect(screen.getByLabelText("各来源累计条数").textContent).toContain(
        "正在读取",
      );
      rerender(
        <HealthPanel
          imports={imports({
            health: null,
            healthStale: false,
            loading: false,
            error: "offline",
            sourceSnapshot: { stale: true, confirmedAt: null },
          })}
        />,
      );
      expect(screen.getByLabelText("各来源累计条数").textContent).toContain(
        "暂时无法读取",
      );
      rerender(
        <HealthPanel
          imports={imports({
            sourceSnapshot: {
              stale: false,
              confirmedAt: "2026-09-19T02:10:00Z",
            },
          })}
        />,
      );
      expect(screen.getByLabelText("各来源累计条数").textContent).toContain(
        "还没有已连接",
      );
    });

    it("重新读取只调已有只读接口并恢复宿主错误，不重复点击提交", async () => {
      const desktop = bridge([]);
      const readHosts = vi.mocked(desktop.getRecallHosts);
      readHosts.mockRejectedValueOnce(new Error("offline"));
      const pending = deferred<never[]>();
      const state = imports({
        refresh: vi.fn().mockReturnValue(pending.promise),
      });
      render(<HealthPanel imports={state} />);
      await settle();
      const button = screen.getByRole("button", { name: "重新读取状态" });
      fireEvent.click(button);
      fireEvent.click(button);
      await settle();
      expect(state.refresh).toHaveBeenCalledTimes(1);
      expect(readHosts).toHaveBeenCalledTimes(2);
      expect(getMemoryProcessing).toHaveBeenCalledTimes(2);
      expect(desktop.probeRecallHost).not.toHaveBeenCalled();
      expect(desktop.installRecall).not.toHaveBeenCalled();
      expect(state.detect).not.toHaveBeenCalled();
      expect(state.syncAll).not.toHaveBeenCalled();
      expect(screen.getByLabelText("AI 接通情况").textContent).not.toContain(
        "暂时无法读取",
      );
      pending.resolve([]);
      await settle();
      expect((button as HTMLButtonElement).disabled).toBe(false);
    });

    it("宿主成功后刷新失败保留旧列表而不报告当前已接通", async () => {
      const desktop = bridge([
        {
          hostId: "cursor",
          label: "Cursor",
          present: true,
          installed: true,
          selfCheck: { state: "wired" },
        },
      ]);
      render(<HealthPanel imports={imports()} />);
      await settle();
      vi.mocked(desktop.getRecallHosts).mockRejectedValue(new Error("offline"));
      fireEvent.click(screen.getByRole("button", { name: "重新读取状态" }));
      await settle();
      const text = screen.getByLabelText("AI 接通情况").textContent;
      expect(text).toContain("数据已过期");
      expect(text).toContain("Cursor");
      expect(text).toContain("上次读取：已接通");
      expect(text).not.toContain("没有检测到");
    });

    it.each(["resolve", "reject"] as const)(
      "StrictMode 旧生命周期迟到 %s 不覆盖新回执",
      async (outcome) => {
        const old = deferred<unknown>();
        getMemoryProcessing.mockReturnValueOnce(old.promise);
        const desktop = bridge([
          {
            hostId: "cursor",
            label: "Cursor",
            present: true,
            installed: true,
            selfCheck: { state: "wired" },
          },
        ]);
        const oldHosts = deferred<never[]>();
        vi.mocked(desktop.getRecallHosts).mockReturnValueOnce(oldHosts.promise);
        render(
          <StrictMode>
            <HealthPanel imports={imports()} />
          </StrictMode>,
        );
        await settle();
        if (outcome === "resolve") {
          old.resolve({
            pending: 99,
            running: 0,
            completed: 0,
            failed: 0,
            blocked_stages: [],
          });
          oldHosts.resolve([]);
        } else {
          old.reject(new Error("late"));
          oldHosts.reject(new Error("late"));
        }
        await settle();
        expect(screen.getByLabelText("记忆提炼").textContent).toContain(
          "4 已完成",
        );
        expect(screen.getByLabelText("记忆提炼").textContent).not.toContain(
          "数据已过期",
        );
        expect(screen.getByLabelText("AI 接通情况").textContent).not.toContain(
          "暂时无法读取",
        );
        expect(screen.getByLabelText("AI 接通情况").textContent).toContain(
          "Cursor",
        );
      },
    );

    it.each(["resolve", "reject"] as const)(
      "主动刷新后的新回执不被旧请求 %s 覆盖",
      async (outcome) => {
        const old = deferred<unknown>();
        const oldHosts = deferred<never[]>();
        getMemoryProcessing.mockReturnValueOnce(old.promise);
        const desktop = bridge([
          {
            hostId: "cursor",
            label: "Cursor",
            present: true,
            installed: true,
            selfCheck: { state: "wired" },
          },
        ]);
        vi.mocked(desktop.getRecallHosts).mockReturnValueOnce(oldHosts.promise);
        render(<HealthPanel imports={imports()} />);
        await settle();
        fireEvent.click(screen.getByRole("button", { name: "重新读取状态" }));
        await settle();
        if (outcome === "resolve") {
          old.resolve({
            pending: 99,
            running: 0,
            completed: 0,
            failed: 0,
            blocked_stages: [],
          });
          oldHosts.resolve([]);
        } else {
          old.reject(new Error("late"));
          oldHosts.reject(new Error("late"));
        }
        await settle();
        expect(screen.getByLabelText("记忆提炼").textContent).toContain(
          "4 已完成",
        );
        expect(screen.getByLabelText("记忆提炼").textContent).not.toContain(
          "数据已过期",
        );
        expect(screen.getByLabelText("AI 接通情况").textContent).toContain(
          "Cursor",
        );
        expect(screen.getByLabelText("AI 接通情况").textContent).not.toContain(
          "暂时无法读取",
        );
      },
    );

    it("来源刷新失败不隐藏其他成功回执，父快照恢复后清除旧错误", async () => {
      bridge([]);
      const state = imports({
        refresh: vi.fn().mockRejectedValue(new Error("offline")),
        sourceSnapshot: { stale: true, confirmedAt: "2026-09-19T02:06:00Z" },
      });
      const { rerender } = render(<HealthPanel imports={state} />);
      await settle();
      fireEvent.click(screen.getByRole("button", { name: "重新读取状态" }));
      await settle();
      expect(screen.getByLabelText("本地服务").textContent).toContain(
        "读取失败时间",
      );
      expect(screen.getByLabelText("记忆提炼").textContent).toContain(
        "4 已完成",
      );
      expect(
        (
          screen.getByRole("button", {
            name: "重新读取状态",
          }) as HTMLButtonElement
        ).disabled,
      ).toBe(false);
      rerender(
        <HealthPanel
          imports={imports({
            sourceSnapshot: {
              stale: false,
              confirmedAt: "2026-09-19T02:11:00Z",
            },
          })}
        />,
      );
      expect(screen.getByLabelText("本地服务").textContent).toContain("运行中");
      expect(screen.getByLabelText("本地服务").textContent).not.toContain(
        "读取失败时间",
      );
    });

    it("来源操作失败不覆盖已确认的新鲜读取快照", async () => {
      render(
        <HealthPanel
          imports={imports({
            error: "operation failed",
            sourceSnapshot: {
              stale: false,
              confirmedAt: "2026-09-19T02:10:00Z",
            },
          })}
        />,
      );
      await settle();
      expect(screen.getByLabelText("本地服务").textContent).toContain("运行中");
      expect(screen.getByLabelText("本地服务").textContent).not.toContain(
        "数据已过期",
      );
    });

    it("挂起的轮询不堆积，卸载后不再轮询且迟到失败被处理", async () => {
      const pending = deferred<unknown>();
      getMemoryProcessing.mockReturnValue(pending.promise);
      const { unmount } = render(<HealthPanel imports={imports()} />);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(15_000);
      });
      expect(getMemoryProcessing).toHaveBeenCalledTimes(1);
      unmount();
      pending.reject(new Error("late"));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10_000);
      });
      expect(getMemoryProcessing).toHaveBeenCalledTimes(1);
      expect(vi.getTimerCount()).toBe(0);
    });
  });
});
